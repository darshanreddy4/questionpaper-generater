/* ExamForge front end — platform library: content the platform publishes, which a school adds to its own library. */
(function () {
  'use strict';
  const { esc, $, $$, api, toast, shell, when } = EF;
  const fmt = (t) => esc(t).replace(/\*\*(.+?)\*\*/gs, '<b>$1</b>').replace(/\n/g, '<br>');

  EF.views.catalog = async function () {
    if (EF.state.meta.tenancy !== 'multi' || EF.state.user.is_superadmin) { location.hash = '#/'; return; }
    const canAdd = !EF.state.user.support;
    const main = shell('#/catalog', `<div class="pagehead"><div><h1>Platform library</h1>
      <div class="muted">Source files and questions published by ExamForge for every school. Add what you want to your own library; the copy is yours and stays even if it is later withdrawn.</div></div></div>
      <div class="card"><h2>Source files</h2><div id="cd"><span class="spin"></span></div></div>
      <div class="card" style="margin-top:1rem"><h2>Questions</h2>
        <div class="row" style="flex-wrap:wrap"><select id="cs" style="max-width:220px"><option value="">All subjects</option></select>
        <select id="cdf" style="max-width:150px"><option value="">Any difficulty</option><option>easy</option><option>medium</option><option>hard</option></select>
        <select id="ct" style="max-width:200px"><option value="">Any type</option>${EF.state.meta.question_types.map((t) => `<option value="${t.id}">${esc(t.label)}</option>`).join('')}</select>
        <input type="text" id="cq" placeholder="Filter by topic" style="max-width:220px">
        ${canAdd ? '<button class="btn primary right" id="caddall" hidden></button>' : ''}</div>
        <div id="cl" style="margin-top:.8rem"><span class="spin"></span></div></div>`);

    const drawDocs = async () => {
      const { documents } = await api('/catalog/documents');
      const el = $('#cd', main);
      if (!documents.length) { el.innerHTML = '<div class="empty">Nothing published yet.</div>'; return; }
      el.innerHTML = `<table class="t"><thead><tr><th>File</th><th>Content</th><th>Published</th><th></th></tr></thead><tbody>${documents.map((d) =>
        `<tr><td><b>${esc(d.filename)}</b><div class="muted small">${esc(d.format.toUpperCase())}</div>
        ${d.chapters.length ? `<details class="small muted"><summary>${d.chapters.length} chapter(s)</summary>${d.chapters.map(esc).join(' · ')}</details>` : ''}</td>
        <td class="small">${(d.words || 0).toLocaleString()} words${d.figures ? ` · ${d.figures} figures` : ''}</td>
        <td class="muted small">${when(d.published_at)}</td>
        <td style="text-align:right">${d.added ? '<span class="badge ok">In your library</span>' : canAdd ? `<button class="btn sm primary" data-add="${d.id}">Add to my library</button>` : ''}</td></tr>`).join('')}</tbody></table>`;
      $$('[data-add]', el).forEach((b) => (b.onclick = async () => {
        b.disabled = true;
        try { await api('/catalog/documents/' + b.dataset.add + '/add', { method: 'POST' }); toast('Added to your source library', 'ok'); drawDocs(); }
        catch (e) { toast(e.message, 'bad'); b.disabled = false; }
      }));
    };

    const filters = () => ({ subject: $('#cs', main).value, q_type: $('#ct', main).value, difficulty: $('#cdf', main).value, topic: $('#cq', main).value.trim() });
    let subjectsLoaded = false;
    const drawQs = async () => {
      const f = filters();
      const p = new URLSearchParams({ limit: 100 });
      Object.entries(f).forEach(([k, v]) => { if (v) p.set(k, v); });
      const { items, total, subjects } = await api('/catalog/questions?' + p);
      if (!subjectsLoaded) {
        $('#cs', main).insertAdjacentHTML('beforeend', subjects.map((s) => `<option value="${esc(s.subject)}">${esc(s.subject)} (${s.count})</option>`).join(''));
        subjectsLoaded = true;
      }
      const el = $('#cl', main), all = $('#caddall', main);
      const fresh = items.filter((i) => !i.added).length;
      if (all) {
        all.hidden = !(f.subject || f.q_type || f.difficulty || f.topic) || !total;
        all.textContent = `Add all ${total} matching`;
      }
      if (!items.length) { el.innerHTML = '<div class="empty">No published questions match.</div>'; return; }
      EF.dgReset();
      el.innerHTML = `<div class="muted small" style="margin-bottom:.5rem">${total} question(s)${fresh < items.length ? ` · ${items.length - fresh} already in your bank` : ''}</div>` + items.map((it) =>
        `<div style="padding:.7rem 0;border-bottom:1px solid var(--line)"><div class="row small"><span class="badge ${it.difficulty}">${it.difficulty}</span><span class="badge">${esc(it.type)}</span><span class="badge">${it.marks} marks</span>
        <span class="muted">${esc(it.subject)} · ${esc(it.topic)}</span>
        <span class="right">${it.added ? '<span class="badge ok">In your bank</span>' : canAdd ? `<button class="btn sm primary" data-q="${it.id}">Add</button>` : ''}</span></div>
        <div style="margin-top:.35rem">${fmt(it.question.text)}</div></div>`).join('');
      $$('[data-q]', el).forEach((b) => (b.onclick = async () => {
        b.disabled = true;
        try { await api('/catalog/questions/add', { method: 'POST', json: { ids: [b.dataset.q] } }); toast('Added to your question bank', 'ok'); drawQs(); }
        catch (e) { toast(e.message, 'bad'); b.disabled = false; }
      }));
      EF.enhance(el);
    };
    if ($('#caddall', main)) $('#caddall', main).onclick = async () => {
      const f = filters();
      if (!(await EF.confirm(`${$('#caddall', main).textContent.replace('Add all', 'Add')} to your school's question bank?`, 'Add'))) return;
      try {
        const r = await api('/catalog/questions/add', { method: 'POST', json: f });
        toast(`${r.added} added${r.skipped ? `, ${r.skipped} already in your bank` : ''}`, 'ok'); drawQs();
      } catch (e) { toast(e.message, 'bad'); }
    };
    ['cs', 'cdf', 'ct'].forEach((id) => $('#' + id, main).addEventListener('change', drawQs));
    $('#cq', main).addEventListener('input', () => { clearTimeout(EF._ct); EF._ct = setTimeout(drawQs, 250); });
    drawDocs().catch((e) => toast(e.message, 'bad'));
    drawQs().catch((e) => toast(e.message, 'bad'));
  };
})();
