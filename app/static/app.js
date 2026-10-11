/* ExamForge front end — core: utilities, API client, shell, routing, simple views. No build step. */
(function () {
  'use strict';
  const EF = (window.EF = { views: {}, state: { token: sessionStorage.getItem('ef_token'), user: null, meta: null }, timers: [] });

  // ---------------------------------------------------------------- utilities
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = (sel, el) => (el || document).querySelector(sel);
  const $$ = (sel, el) => Array.from((el || document).querySelectorAll(sel));
  const fm = (n) => (Number.isInteger(+n) ? String(+n) : String(+(+n).toFixed(2)));
  const fmt = (text) => esc(text).replace(/\*\*(.+?)\*\*/gs, '<b>$1</b>').replace(/\n/g, '<br>');
  const when = (iso) => { try { return new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }); } catch (e) { return iso; } };
  Object.assign(EF, { esc, $, $$, fm, fmt, when });

  function toast(msg, kind) {
    const el = document.createElement('div');
    el.className = 'toast ' + (kind || '');
    el.textContent = msg;
    $('#toasts').appendChild(el);
    setTimeout(() => el.remove(), kind === 'bad' ? 7000 : 3500);
  }
  EF.toast = toast;

  // In-page dialogs: the browser's confirm()/prompt() are blocked or missing in embedded and some locked-down browsers.
  EF.dialog = function ({ title, message, fields, ok, danger }) {
    return new Promise((resolve) => {
      const back = document.createElement('div');
      back.className = 'dlg-back';
      back.innerHTML = `<form class="dlg" role="dialog" aria-modal="true"><h3>${esc(title || '')}</h3>
        ${message ? `<p class="muted" style="margin-top:0;white-space:pre-line">${esc(message)}</p>` : ''}
        ${(fields || []).map((f, i) => `<label class="f" style="margin-bottom:.6rem">${esc(f.label)}${f.hint ? ` <span class="muted small" style="font-weight:400">${esc(f.hint)}</span>` : ''}
          <input type="${f.type || 'text'}" name="f${i}" value="${esc(f.value == null ? '' : f.value)}" placeholder="${esc(f.placeholder || '')}" ${f.step ? `step="${f.step}"` : ''} ${f.min != null ? `min="${f.min}"` : ''}></label>`).join('')}
        <div class="row" style="justify-content:flex-end;margin-top:.8rem"><button type="button" class="btn" data-x>Cancel</button>
        <button type="submit" class="btn ${danger ? 'danger' : 'primary'}">${esc(ok || 'OK')}</button></div></form>`;
      const close = (v) => { back.remove(); document.removeEventListener('keydown', onKey); resolve(v); };
      const onKey = (e) => { if (e.key === 'Escape') close(null); };
      document.addEventListener('keydown', onKey);
      back.addEventListener('mousedown', (e) => { if (e.target === back) close(null); });
      back.querySelector('[data-x]').onclick = () => close(null);
      back.querySelector('form').onsubmit = (e) => { e.preventDefault(); close(Array.from(back.querySelectorAll('input')).map((x) => x.value)); };
      document.body.appendChild(back);
      const first = back.querySelector('input') || back.querySelector('button[type=submit]');
      first.focus();
    });
  };
  EF.confirm = async (message, ok, title) => (await EF.dialog({ title: title || 'Please confirm', message, ok: ok || 'Yes', danger: true })) !== null;
  EF.prompt = async (message, value) => {
    const r = await EF.dialog({ title: message, fields: [{ label: '', value }], ok: 'OK' });
    return r === null ? null : r[0];
  };

  function clearTimers() { EF.timers.forEach(clearTimeout); EF.timers = []; }
  EF.after = (fn, ms) => { const t = setTimeout(fn, ms); EF.timers.push(t); return t; };

  // ---------------------------------------------------------------- API
  async function api(path, opts) {
    opts = opts || {};
    const headers = {};
    if (EF.state.token) headers.Authorization = 'Bearer ' + EF.state.token;
    let body;
    if (opts.json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(opts.json); }
    else if (opts.form) body = opts.form;
    const res = await fetch('/api' + path, { method: opts.method || 'GET', headers, body });
    if (res.status === 401 && EF.state.token && !opts.quiet401) { logout(); throw new Error('Your session has expired. Please sign in again.'); }
    if (!res.ok) {
      let msg = res.statusText || 'Request failed';
      try {
        const j = await res.json();
        if (typeof j.detail === 'string') msg = j.detail;
        else if (Array.isArray(j.detail)) msg = j.detail.map((d) => ((d.loc || []).slice(1).join('.') + ': ' + d.msg).replace(/^: /, '')).join('; ');
      } catch (e) { /* non-JSON error */ }
      throw new Error(msg);
    }
    if (opts.raw) return res;
    if (res.status === 204) return null;
    return res.json();
  }
  EF.api = api;

  EF.download = async function (path, fallbackName) {
    const res = await api(path, { raw: true });
    const cd = res.headers.get('content-disposition') || '';
    const m = /filename="([^"]+)"/.exec(cd);
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement('a');
    a.href = url; a.download = m ? m[1] : fallbackName || 'download';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  };

  // Diagrams are drawn server-side; the browser only ever shows them as <img>, so spec content can never run as markup.
  const dgReg = [];
  const dgCache = new Map();
  EF.dgRegister = (spec) => { dgReg.push(spec); return dgReg.length - 1; };
  EF.dgReset = () => { dgReg.length = 0; };
  EF.loadDiagrams = function (root) {
    $$('img[data-dg]', root).forEach(async (img) => {
      const spec = dgReg[+img.dataset.dg];
      if (!spec) return;
      const key = JSON.stringify(spec);
      try {
        if (!dgCache.has(key)) {
          const res = await api('/diagrams/render', { method: 'POST', json: { spec: spec, format: 'svg' }, raw: true });
          dgCache.set(key, URL.createObjectURL(await res.blob()));
        }
        img.src = dgCache.get(key);
      } catch (e) { img.alt = 'Diagram could not be drawn: ' + e.message; img.title = e.message; img.classList.add('dg-bad'); }
    });
  };
  EF.renderMath = function (root) {
    if (!window.renderMathInElement) return;
    window.renderMathInElement(root, {
      delimiters: [{ left: '$$', right: '$$', display: true }, { left: '\\[', right: '\\]', display: true },
        { left: '$', right: '$', display: false }, { left: '\\(', right: '\\)', display: false }],
      throwOnError: false, strict: 'ignore',
    });
  };
  EF.enhance = function (root) { EF.renderMath(root); EF.loadDiagrams(root); };

  // ---------------------------------------------------------------- session
  function logout() {
    EF.state.token = null; EF.state.user = null; EF.state.meta = null;
    sessionStorage.removeItem('ef_token'); sessionStorage.removeItem('ef_operator_token');
    location.hash = '#/login';
    route();
  }
  EF.logout = logout;

  async function boot() {
    if (!EF.state.token) return false;
    try {
      EF.state.user = await api('/auth/me');
      EF.state.meta = await api('/meta');
      return true;
    } catch (e) { return false; }
  }

  // ---------------------------------------------------------------- shell
  const NAV = [['#/', 'Papers', '▤'], ['#/new', 'New paper', '＋'], ['#/library', 'Source library', '▥'], ['#/bank', 'Question bank', '❖'], ['#/settings', 'AI & billing', '◈']];

  function shell(active, html) {
    const u = EF.state.user, eng = EF.state.meta.engine;
    const nav = (u.support ? NAV.filter(([h]) => ['#/', '#/library', '#/bank'].includes(h)) : NAV).concat(u.role === 'admin' && !u.is_superadmin ? [['#/admin', 'Administration', '⚙']] : [])
      .concat(EF.state.meta.tenancy === 'multi' && !u.is_superadmin ? [['#/catalog', 'Platform library', '✦']] : [])
      .concat(u.is_superadmin ? [['#/platform', 'Platform', '◆']] : [])
      .map(([h, l, i]) => `<a href="${h}" class="${active === h ? 'active' : ''}"><span>${i}</span>${l}</a>`).join('');
    $('#app').innerHTML = `<div class="shell"><aside class="side">
      <div class="brand"><span class="logo">${'<svg width="16" height="16" viewBox="0 0 16 16"><path d="M2 3h12M2 8h12M2 13h7" stroke="#fff" stroke-width="2" stroke-linecap="round"/></svg>'}</span>ExamForge</div>
      <nav class="nav">${nav}</nav>
      <div class="foot"><div>${esc(u.name)}</div><div class="muted small">${esc(u.organization || u.email)}</div>
        <div style="margin-top:.5rem"><span class="badge ${eng.offline ? 'warn' : 'ok'}" title="${esc(eng.label || '')}">${eng.offline ? 'No AI connected' : esc((eng.label || eng.provider) + ' · ' + eng.model)}</span></div>
        <button class="btn sm ghost" id="logout" style="color:#cfd8ea;margin-top:.4rem;padding-left:0">Sign out</button></div></aside>
      <main class="main" id="main">${EF.supportBanner()}${html}</main></div>`;
    $('#logout').onclick = logout;
    EF.bindSupport($('#main'));
    return $('#main');
  }
  EF.shell = shell;

  // Shown at the top of every page while the operator is in a support session.
  EF.supportBanner = function () {
    const u = EF.state.user;
    return u && u.support ? `<div class="banner warn" style="position:sticky;top:0;z-index:5"><b>Support view of ${esc(u.support.school)}</b> — read-only; you can see papers, source files and the question bank, nothing else. Access ends ${when(u.support.expires_at)} or sooner if the school stops it. Every item you open is recorded in the school's audit log.
        <button class="btn sm right" id="exitsup">Exit support view</button></div>` : '';
  };
  EF.bindSupport = (main) => { const b = $('#exitsup', main); if (b) b.onclick = EF.exitSupport; };

  EF.engineBanner = function () {
    const eng = EF.state.meta.engine;
    if (!eng.offline) return '';
    return `<div class="banner warn"><b>No AI is connected.</b> ExamForge will use its built-in engine: exact maths templates and questions built from your
      uploaded material. For richer questions, add your own API key under <a href="#/settings">AI &amp; billing</a>, or ask your administrator to enable AI.</div>`;
  };

  // ---------------------------------------------------------------- auth view
  async function authView() {
    let cfg = { tenancy: 'single', allow_registration: true, allow_school_signup: true };
    try { cfg = await api('/auth/config', { quiet401: true }); } catch (e) { /* older server: keep defaults */ }
    const multi = cfg.tenancy === 'multi';
    const joinCode = (location.hash.match(/^#\/join\/([\w-]+)/) || [])[1] || '';
    const tabs = [['in', 'Sign in']];
    if (multi) {
      if (cfg.allow_individual_signup) tabs.push(['solo', 'Create account']);
      if (cfg.allow_school_signup) tabs.push(['school', 'Register a school']);
      tabs.push(['join', 'Join with code']);
    } else if (cfg.allow_registration) tabs.push(['up', 'Create account']);
    $('#app').innerHTML = `<div class="auth"><div class="card">
      <div class="brand" style="color:var(--brand);padding:0 0 1rem"><span class="logo"><svg width="16" height="16" viewBox="0 0 16 16"><path d="M2 3h12M2 8h12M2 13h7" stroke="#fff" stroke-width="2" stroke-linecap="round"/></svg></span>ExamForge</div>
      <p class="muted" style="margin-top:0">Generate board-pattern question papers and answer keys from your own study material.</p>
      <div class="tabs">${tabs.map(([k, l]) => `<button data-t="${k}">${l}</button>`).join('')}</div>
      <form id="af" class="col" autocomplete="on">
        <label class="f" data-m="join" hidden>Invitation code<input type="text" name="invite_code" autocomplete="off" value="${esc(joinCode)}"></label>
        <label class="f" data-m="up solo school join" hidden>Full name<input type="text" name="name" autocomplete="name"></label>
        <label class="f" data-m="up school" hidden>School / institution<input type="text" name="organization" autocomplete="organization"></label>
        <label class="f">Email<input type="email" name="email" required autocomplete="username"></label>
        <label class="f">Password<input type="password" name="password" required minlength="10" autocomplete="current-password"><span class="muted small" data-m="up solo school join" hidden>At least 10 characters.</span></label>
        <div class="muted small" data-m="solo" hidden>For individual teachers and tutors who are not part of a school. You get your own private workspace; nobody else can see your papers.</div>
        <div class="muted small" data-m="school" hidden>You will be the administrator of this school and can invite your teachers afterwards.</div>
        <div class="banner bad" id="aerr" hidden></div>
        <button class="btn primary" type="submit" id="asubmit">Sign in</button></form></div></div>`;
    let mode = 'in';
    const label = { in: 'Sign in', up: 'Create account', solo: 'Create account', school: 'Register school', join: 'Join school' };
    const setMode = (m) => {
      mode = m;
      $$('.tabs button').forEach((x) => x.classList.toggle('on', x.dataset.t === m));
      $$('[data-m]').forEach((el) => (el.hidden = !el.dataset.m.split(' ').includes(m)));
      $('#asubmit').textContent = label[m];
      $('#af [name=password]').autocomplete = m === 'in' ? 'current-password' : 'new-password';
    };
    $$('.tabs button').forEach((b) => (b.onclick = () => setMode(b.dataset.t)));
    setMode(joinCode && multi ? 'join' : 'in');
    $('#af').onsubmit = async (ev) => {
      ev.preventDefault();
      const f = Object.fromEntries(new FormData(ev.target).entries());
      const err = $('#aerr'); err.hidden = true;
      $('#asubmit').disabled = true;
      try {
        let body = { email: f.email, password: f.password };
        if (mode !== 'in') body = Object.assign(body, { name: f.name || f.email });
        if (mode === 'solo') body.individual = true;
        if (mode === 'up') body.organization = f.organization || '';
        if (mode === 'school') body.school_name = f.organization || '';
        if (mode === 'join') body.invite_code = (f.invite_code || '').trim();
        const res = await api('/auth/' + (mode === 'in' ? 'login' : 'register'), { method: 'POST', json: body, quiet401: true });
        EF.state.token = res.token; sessionStorage.setItem('ef_token', res.token);
        location.hash = '#/'; await start();
      } catch (e) { err.textContent = e.message; err.hidden = false; }
      const sb = $('#asubmit'); if (sb) sb.disabled = false;
    };
  }

  // ---------------------------------------------------------------- dashboard
  function statusBadge(s) {
    const cls = { ready: 'ok', failed: 'bad', processing: 'warn', generating: 'warn', queued: 'warn' }[s] || '';
    return `<span class="badge ${cls}">${esc(s)}</span>`;
  }
  EF.statusBadge = statusBadge;

  EF.views.dashboard = async function () {
    const main = shell('#/', `<div class="pagehead"><div><h1>Question papers</h1><div class="muted">Everything you have generated, ready to edit and download.</div></div>
      ${EF.state.user.support ? '' : '<a class="btn primary right" href="#/new">＋ New paper</a>'}</div>${EF.state.user.support ? '' : EF.engineBanner()}<div id="list" class="card"><span class="spin"></span></div>`);
    const draw = async () => {
      const { papers } = await api('/papers');
      const list = $('#list', main);
      if (!papers.length) { list.innerHTML = `<div class="empty"><h3>No papers yet</h3><p>Upload a textbook or syllabus, pick a board pattern and generate your first paper.</p><a class="btn primary" href="#/new">Create a paper</a></div>`; return; }
      list.innerHTML = `<table class="t"><thead><tr><th>Title</th><th>Subject</th>${papers.some((p) => p.created_by) ? '<th>By</th>' : ''}<th>Status</th><th>Quality</th><th>Created</th><th></th></tr></thead><tbody>${papers.map((p) =>
        `<tr><td><a href="#/paper/${p.id}"><b>${esc(p.title)}</b></a></td><td>${esc(p.subject)}</td>${papers.some((q) => q.created_by) ? `<td class="small">${esc(p.created_by)}</td>` : ''}
         <td>${statusBadge(p.status)}${p.status === 'generating' ? ` <span class="muted small">${p.progress}%</span>` : ''}</td>
         <td>${p.score == null ? '—' : `<span class="badge ${p.score >= 85 ? 'ok' : 'warn'}">${p.score}/100</span>`}</td>
         <td class="muted small">${when(p.created_at)}</td>
         <td style="text-align:right">${p.can_edit ? `<button class="btn sm danger" data-del="${p.id}">Delete</button>` : ''}</td></tr>`).join('')}</tbody></table>`;
      $$('[data-del]', list).forEach((b) => (b.onclick = async () => {
        if (!(await EF.confirm('Delete this paper permanently?', 'Delete'))) return;
        await api('/papers/' + b.dataset.del, { method: 'DELETE' }); draw();
      }));
      if (papers.some((p) => ['queued', 'generating'].includes(p.status))) EF.after(draw, 2000);
    };
    draw().catch((e) => toast(e.message, 'bad'));
  };

  // ---------------------------------------------------------------- library
  EF.views.library = async function () {
    const lim = EF.state.meta.limits;
    const main = shell('#/library', `<div class="pagehead"><div><h1>Source library</h1><div class="muted">Upload textbooks, notes, syllabi and past papers. ExamForge reads text, scanned pages and diagrams.</div></div></div>
      <div class="card"><div class="drop" id="drop" tabindex="0"><b>Drop files here or click to browse</b><div class="small">PDF, DOC/DOCX, PPTX, EPUB, RTF, HTML, TXT/MD, images · up to ${lim.max_upload_mb} MB each</div>
      <input type="file" id="fi" multiple hidden></div>
      <label class="check" style="margin-top:.7rem"><input type="checkbox" id="aiv"> <span><b>Read pictures, diagrams and scans with AI</b>
        <span class="muted small"> — optional, uses AI tokens (up to 20 images per file). Normal text in PDFs, Word and slides is always read free, without AI.</span></span></label></div><div id="docs" class="card"><span class="spin"></span></div>`);
    const upload = async (files) => {
      if (!files.length) return;
      const form = new FormData(); Array.from(files).forEach((f) => form.append('files', f)); form.append('ai_vision', $('#aiv', main).checked ? 'true' : 'false');
      try { await api('/documents', { method: 'POST', form }); toast('Upload started', 'ok'); draw(); } catch (e) { toast(e.message, 'bad'); }
    };
    const drop = $('#drop', main), fi = $('#fi', main);
    drop.onclick = () => fi.click(); drop.onkeydown = (e) => { if (e.key === 'Enter') fi.click(); };
    fi.onchange = () => upload(fi.files);
    ['dragover', 'dragenter'].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', (e) => upload(e.dataTransfer.files));
    const pub = EF.state.user.is_superadmin && EF.state.meta.tenancy === 'multi';
    async function draw() {
      const { documents } = await api('/documents');
      const el = $('#docs', main);
      if (!documents.length) { el.innerHTML = '<div class="empty">No documents yet.</div>'; return; }
      el.innerHTML = `<table class="t"><thead><tr><th>File</th><th>Status</th><th>Content</th><th>Added</th><th></th></tr></thead><tbody>${documents.map((d) =>
        `<tr><td><b>${esc(d.filename)}</b><div class="muted small">${esc(d.format.toUpperCase())}${d.uploaded_by ? ' · added by ' + esc(d.uploaded_by) : ''}${d.published ? ' · <span class="badge ok">Published to all schools</span>' : ''}</div></td>
        <td>${statusBadge(d.status)}${d.message ? `<div class="small muted" style="max-width:340px">${esc(d.message)}</div>` : ''}</td>
        <td class="small">${d.status === 'ready' ? `${(d.stats.words || 0).toLocaleString()} words · ${d.stats.chunks || 0} passages${d.stats.figures ? ` · ${d.stats.figures} figures` : ''}${d.stats.ai_vision ? ' · <span class="badge">AI-read</span>' : ''}` : '—'}</td>
        <td class="muted small">${when(d.created_at)}</td><td style="text-align:right">${pub && d.status === 'ready' ? `<button class="btn sm ${d.published ? '' : 'primary'}" data-pub="${d.id}" data-v="${!d.published}">${d.published ? 'Unpublish' : 'Publish'}</button> ` : ''}${d.can_delete ? `<button class="btn sm danger" data-del="${d.id}">Remove</button>` : '<span class="muted small">Administrator only</span>'}</td></tr>`).join('')}</tbody></table>${documents.some((d) => !d.can_delete) ? '<p class="muted small" style="margin:.7rem 0 0">Source files belong to your whole school. Everyone can use them; only your school administrator can remove them.</p>' : ''}`;
      $$('[data-pub]', el).forEach((b) => (b.onclick = async () => {
        try { await api('/catalog/publish', { method: 'POST', json: { kind: 'document', ids: [b.dataset.pub], published: b.dataset.v === 'true' } }); toast(b.dataset.v === 'true' ? 'Published to all schools' : 'Withdrawn from the platform library', 'ok'); draw(); }
        catch (e) { toast(e.message, 'bad'); }
      }));
      $$('[data-del]', el).forEach((b) => (b.onclick = async () => { if (!(await EF.confirm('Remove this document? It disappears for everyone in your school.', 'Remove'))) return; await api('/documents/' + b.dataset.del, { method: 'DELETE' }); draw(); }));
      if (documents.some((d) => ['queued', 'processing'].includes(d.status))) EF.after(draw, 1500);
    }
    draw().catch((e) => toast(e.message, 'bad'));
  };

  // ---------------------------------------------------------------- question bank
  EF.views.bank = async function () {
    const main = shell('#/bank', `<div class="pagehead"><div><h1>Question bank</h1><div class="muted">Every accepted question is kept here. New papers avoid repeating them.</div></div></div>
      <div class="card"><div class="row"><input type="text" id="bq" placeholder="Filter by topic" style="max-width:260px">
      <select id="bd" style="max-width:150px"><option value="">Any difficulty</option><option>easy</option><option>medium</option><option>hard</option></select>
      <select id="bt" style="max-width:200px"><option value="">Any type</option>${EF.state.meta.question_types.map((t) => `<option value="${t.id}">${esc(t.label)}</option>`).join('')}</select></div></div>
      <div id="bl" class="card" style="margin-top:1rem"><span class="spin"></span></div>`);
    const pubq = EF.state.user.is_superadmin && EF.state.meta.tenancy === 'multi';
    const setPub = async (ids, published) => {
      try { await api('/catalog/publish', { method: 'POST', json: { kind: 'question', ids, published } }); toast(published ? 'Published to all schools' : 'Withdrawn from the platform library', 'ok'); draw(); }
      catch (e) { toast(e.message, 'bad'); }
    };
    const draw = async () => {
      const p = new URLSearchParams({ limit: 100 });
      if ($('#bq').value) p.set('topic', $('#bq').value); if ($('#bd').value) p.set('difficulty', $('#bd').value); if ($('#bt').value) p.set('q_type', $('#bt').value);
      const { items, total } = await api('/bank?' + p);
      const el = $('#bl', main);
      if (!items.length) { el.innerHTML = '<div class="empty">No questions match.</div>'; return; }
      EF.dgReset();
      el.innerHTML = `<div class="row small" style="margin-bottom:.5rem"><span class="muted">${total} question(s)</span>${pubq ? `<button class="btn sm right" id="pubshown">${items.some((i) => !i.published) ? 'Publish' : 'Unpublish'} all shown (${items.length})</button>` : ''}</div>` + items.map((it) =>
        `<div style="padding:.7rem 0;border-bottom:1px solid var(--line)"><div class="row small"><span class="badge ${it.difficulty}">${it.difficulty}</span><span class="badge">${esc(it.type)}</span><span class="badge">${it.marks} marks</span>
        <span class="muted">${esc(it.subject)} · ${esc(it.topic)}${it.created_by ? ' · by ' + esc(it.created_by) : ''}</span>${it.published ? '<span class="badge ok">Published</span>' : ''}${pubq ? `<button class="btn sm ${it.published ? '' : 'primary'} right" data-pub="${it.id}" data-v="${!it.published}">${it.published ? 'Unpublish' : 'Publish'}</button>` : ''}${it.can_delete ? `<button class="btn sm danger right" data-del="${it.id}">Delete</button>` : ''}</div>
        <div style="margin-top:.35rem">${fmt(it.question.text)}</div></div>`).join('');
      $$('[data-pub]', el).forEach((b) => (b.onclick = async () => { await setPub([b.dataset.pub], b.dataset.v === 'true'); }));
      const pa = $('#pubshown', main);
      if (pa) pa.onclick = () => setPub(items.map((i) => i.id), items.some((i) => !i.published));
      $$('[data-del]', el).forEach((b) => (b.onclick = async () => { await api('/bank/' + b.dataset.del, { method: 'DELETE' }); draw(); }));
      EF.enhance(el);
    };
    ['bq', 'bd', 'bt'].forEach((id) => $('#' + id).addEventListener(id === 'bq' ? 'input' : 'change', () => { clearTimeout(EF._bt); EF._bt = setTimeout(draw, 250); }));
    draw().catch((e) => toast(e.message, 'bad'));
  };

  // ---------------------------------------------------------------- admin
  EF.views.admin = async function () {
    if (EF.state.user.role !== 'admin') { location.hash = '#/'; return; }
    if (EF.state.user.is_superadmin) { location.hash = '#/platform'; return; }  // the operator manages schools from Platform, not this page
    const multi = EF.state.user.org_id && (await api('/auth/config', { quiet401: true }).catch(() => ({}))).tenancy === 'multi';
    const main = shell('#/admin', `<div class="pagehead"><h1>Administration</h1></div>
      <div class="card" id="school"></div>
      <div class="card" id="users"><span class="spin"></span></div>
      ${multi ? '<div class="card" id="invites"></div><div class="card" id="support"></div>' : ''}
      <div class="card"><h2>Audit log</h2><div id="audit"></div></div>`);
    const draw = async () => {
      const calls = [api('/admin/users'), api('/admin/audit?limit=60'), api('/admin/school')];
      if (multi) calls.push(api('/admin/invites'), api('/admin/support'));
      const [{ users }, { events }, school, inv, sup] = await Promise.all(calls);
      $('#school', main).innerHTML = `<div class="row"><div><h2 style="margin:0">${esc(school.name)}</h2><div class="muted small">${school.users} user${school.users === 1 ? '' : 's'} in this school. Everything here is private to your school.</div></div>
        <button class="btn sm right" id="rename">Rename</button></div>`;
      $('#rename', main).onclick = async () => {
        const name = await EF.prompt('School name', school.name);
        if (!name || name.trim().length < 2) return;
        await api('/admin/school', { method: 'PATCH', json: { name: name.trim() } });
        EF.state.user = await api('/auth/me'); draw();
      };
      $('#users', main).innerHTML = `<h2>Users</h2><table class="t"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Papers</th><th>Status</th><th></th></tr></thead><tbody>${users.map((u) =>
        `<tr><td>${esc(u.name)}</td><td>${esc(u.email)}</td><td>${esc(u.role)}</td><td>${u.papers}</td><td>${u.is_active ? '<span class="badge ok">active</span>' : '<span class="badge bad">disabled</span>'}</td>
        <td style="text-align:right">${u.id === EF.state.user.id ? '' : `<button class="btn sm" data-act="${u.id}" data-v="${!u.is_active}">${u.is_active ? 'Disable' : 'Enable'}</button>
        <button class="btn sm" data-role="${u.id}" data-v="${u.role === 'admin' ? 'teacher' : 'admin'}">Make ${u.role === 'admin' ? 'teacher' : 'admin'}</button>`}</td></tr>`).join('')}</tbody></table>`;
      if (multi) {
        $('#invites', main).innerHTML = `<h2>Invite teachers</h2>
          <div class="row" style="flex-wrap:wrap;align-items:flex-end">
            <label class="f" style="min-width:230px">Email <span class="muted small" style="font-weight:400">(optional — locks the code to one address)</span><input type="email" id="ie"></label>
            <label class="f">Role<select id="ir"><option value="teacher">Teacher</option><option value="admin">Administrator</option></select></label>
            <button class="btn primary" id="imake">Create invitation</button></div>
          <div id="inew"></div>
          ${inv.invites.length ? `<table class="t" style="margin-top:.8rem"><thead><tr><th>Role</th><th>For</th><th>Expires</th><th>Status</th><th></th></tr></thead><tbody>${inv.invites.map((i) =>
            `<tr><td>${esc(i.role)}</td><td>${esc(i.email || 'anyone with the code')}</td><td class="muted small">${when(i.expires_at)}</td>
            <td><span class="badge ${i.status === 'pending' ? 'warn' : (i.status === 'used' ? 'ok' : 'bad')}">${i.status}</span></td>
            <td style="text-align:right">${i.status === 'pending' ? `<button class="btn sm danger" data-rev="${i.id}">Revoke</button>` : ''}</td></tr>`).join('')}</tbody></table>` : ''}`;
        $('#imake', main).onclick = async () => {
          const r = await api('/admin/invites', { method: 'POST', json: { email: $('#ie', main).value.trim(), role: $('#ir', main).value } });
          const link = location.origin + '/#/join/' + r.code;
          $('#inew', main).innerHTML = `<div class="banner info" style="margin-top:.8rem"><b>Copy this now — it is shown only once.</b>
            <div style="margin-top:.4rem">Code: <code>${esc(r.code)}</code></div>
            <div class="small" style="margin-top:.2rem">Or send this link: <code>${esc(link)}</code></div></div>`;
          const t = await api('/admin/invites'); inv.invites = t.invites;
        };
        $$('[data-rev]', main).forEach((b) => (b.onclick = async () => { await api('/admin/invites/' + b.dataset.rev, { method: 'DELETE' }); draw(); }));
      }
      if (multi) {
        $('#support', main).innerHTML = `<h2>Support access</h2>
          <p class="muted small" style="margin-top:0">ExamForge support cannot see anything in your school unless you allow it. If you do, it is read-only (papers, source files and the question bank — no people, keys or billing), it ends by itself, and every item support opens is recorded in the audit log below. You can stop it at any time.</p>
          ${sup.active ? `<div class="banner info" style="margin:.4rem 0 .8rem">Allowed until <b>${when(sup.expires_at)}</b>${sup.note ? ` — “${esc(sup.note)}”` : ''}. <button class="btn sm danger right" id="supstop">Stop support access now</button></div>`
            : '<div class="muted small" style="margin-bottom:.6rem">Support access is <b>off</b>.</div>'}
          <div class="row" style="flex-wrap:wrap;align-items:flex-end">
            <label class="f">${sup.active ? 'Replace with' : 'Allow for'}<select id="suph">${[1, 4, 24, 72].map((h) => `<option value="${h}" ${h === 24 ? 'selected' : ''}>${h === 1 ? '1 hour' : h === 24 ? '24 hours' : h === 72 ? '3 days' : h + ' hours'}</option>`).join('')}</select></label>
            <label class="f" style="min-width:260px">What do you need help with? <span class="muted small" style="font-weight:400">(optional)</span><input type="text" id="supn" maxlength="300"></label>
            <button class="btn primary" id="supgo">${sup.active ? 'Update' : 'Allow support access'}</button></div>`;
        $('#supgo', main).onclick = async () => { await api('/admin/support', { method: 'POST', json: { hours: +$('#suph', main).value, note: $('#supn', main).value } }); toast('Support access allowed', 'ok'); draw(); };
        const stop = $('#supstop', main);
        if (stop) stop.onclick = async () => { await api('/admin/support', { method: 'DELETE' }); toast('Support access stopped', 'ok'); draw(); };
      }
      $('#audit', main).innerHTML = `<table class="t small"><thead><tr><th>When</th><th>Action</th><th>Detail</th><th>IP</th></tr></thead><tbody>${events.map((e) =>
        `<tr><td class="muted">${when(e.at)}</td><td>${esc(e.action)}</td><td>${esc(e.detail || e.entity_id)}</td><td class="muted">${esc(e.ip)}</td></tr>`).join('')}</tbody></table>`;
      $$('[data-act]', main).forEach((b) => (b.onclick = async () => { await api('/admin/users/' + b.dataset.act, { method: 'PATCH', json: { is_active: b.dataset.v === 'true' } }); draw(); }));
      $$('[data-role]', main).forEach((b) => (b.onclick = async () => { await api('/admin/users/' + b.dataset.role, { method: 'PATCH', json: { role: b.dataset.v } }); draw(); }));
    };
    draw().catch((e) => toast(e.message, 'bad'));
  };

  // ---------------------------------------------------------------- router
  async function route() {
    clearTimers();
    const hash = location.hash || '#/';
    if (!EF.state.user) { authView(); return; }
    try {
      if (hash === '#/' || hash === '') return await EF.views.dashboard();
      if (hash === '#/new') return await EF.views.wizard();
      if (hash === '#/library') return await EF.views.library();
      if (hash === '#/bank') return await EF.views.bank();
      if (hash === '#/settings') return await EF.views.settings();
      if (hash === '#/admin') return await EF.views.admin();
      if (hash === '#/platform') return await EF.views.platform();
      if (hash === '#/catalog') return await EF.views.catalog();
      const m = /^#\/paper\/([0-9a-f]+)$/.exec(hash);
      if (m) return await EF.views.paper(m[1]);
      location.hash = '#/';
    } catch (e) { toast(e.message, 'bad'); }
  }
  EF.route = route;
  EF.exitSupport = async () => {
    const own = sessionStorage.getItem('ef_operator_token');
    sessionStorage.removeItem('ef_operator_token');
    if (!own) return logout();
    EF.state.token = own; sessionStorage.setItem('ef_token', own);
    location.hash = '#/platform'; await start();
  };
  EF.start = () => start();

  async function start() {
    if (await boot()) { route(); return; }
    if (sessionStorage.getItem('ef_operator_token')) { toast('Support access to that school has ended.', 'bad'); await EF.exitSupport(); return; }  // back to the operator's own session
    EF.state.token = null; sessionStorage.removeItem('ef_token'); authView();
  }
  window.addEventListener('hashchange', () => { if (EF.state.user) route(); else if (/^#\/join\//.test(location.hash)) authView(); });
  window.addEventListener('load', start);
})();
