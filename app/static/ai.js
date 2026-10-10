/* ExamForge front end — AI & billing: bring-your-own key, which AI is in use, and usage charges. */
(function () {
  'use strict';
  const { esc, $, $$, fm, api, toast, shell } = EF;
  const money = (n, cur) => `${esc(cur)} ${Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
  const SOURCES = { user: ['ok', 'Your own AI key'], workspace: ['ok', 'Your school’s AI key'], platform: ['warn', 'ExamForge platform AI — billed by usage'], offline: ['', 'No AI — built-in engine only'] };
  const tok = (n) => Number(n || 0).toLocaleString();

  function keyForm(scope, cfg, S) {
    const saved = cfg && cfg.provider ? cfg : null;
    const prov = saved ? saved.provider : 'openai';
    const opts = S.providers.map((p) => `<option value="${esc(p.id)}" ${p.id === prov ? 'selected' : ''}>${esc(p.label)}</option>`).join('');
    const def = (S.providers.find((p) => p.id === prov) || {}).default_model || '';
    return `<div class="col" data-scope="${scope}">
      <div class="grid2"><label class="f">Provider<select data-k="provider">${opts}</select></label>
        <label class="f">Model<input type="text" data-k="model" value="${esc(saved ? saved.model : '')}" placeholder="${esc(def || 'model name')}"></label></div>
      <label class="f" data-url ${(S.providers.find((p) => p.id === prov) || {}).needs_url ? '' : 'hidden'}>Endpoint URL
        <input type="url" data-k="base_url" value="${esc(saved ? saved.base_url : '')}" placeholder="https://your-endpoint.example.com/v1"></label>
      <label class="f">Cheaper model for reading pictures <span class="muted small" style="font-weight:400">(optional)</span>
        <input type="text" data-k="fast_model" value="${esc(saved ? saved.fast_model : '')}" placeholder="e.g. a smaller, cheaper model"></label>
      <label class="f">API key
        <input type="password" data-k="api_key" autocomplete="off" placeholder="${saved ? `•••• ${esc(saved.key_hint)} — leave blank to keep it` : 'Paste your key'}"></label>
      <div class="row"><button class="btn primary sm" data-a="save">${saved ? 'Update' : 'Save key'}</button>
        <button class="btn sm" data-a="test" ${saved ? '' : 'disabled'}>Test connection</button>
        <button class="btn sm danger" data-a="remove" ${saved ? '' : 'disabled'}>Remove</button><span class="small muted" data-out></span></div></div>`;
  }

  function billingCard(B, AB, month) {
    const cur = B.currency, p = B.platform, o = B.own_key;
    const configured = B.pricing.configured;
    const rows = `<tr><td>Platform AI tokens</td><td>${tok(p.tokens)}</td><td>${money(p.charge, cur)}</td></tr>
      <tr><td>Your own AI key</td><td>${tok(o.tokens)}</td><td class="muted">billed by your provider</td></tr>`;
    return `<div class="card"><div class="row"><h2 style="margin:0">Usage &amp; charges</h2><label class="right row small">Month <input type="month" id="bm" value="${esc(month)}" style="width:150px"></label></div>
      <table class="t" style="margin-top:.6rem"><thead><tr><th></th><th>Tokens</th><th>Charge</th></tr></thead><tbody>${rows}</tbody></table>
      <div class="muted small" style="margin-top:.5rem">${esc(B.summary)} ${B.tokens_saved_by_cache ? `Caching saved about ${tok(B.tokens_saved_by_cache)} tokens.` : ''}</div>
      ${configured ? '' : '<div class="banner info small" style="margin:.7rem 0 0">Rates have not been set by the operator yet, so charges show as 0.</div>'}
      ${AB ? `<h3 style="margin-top:1.2rem">School total (administrators)</h3>
        <table class="t"><tbody><tr><td>Platform fee</td><td style="text-align:right">${money(AB.summary.platform_fee, cur)}</td></tr>
        <tr><td>Platform AI tokens (${tok(AB.summary.platform.tokens)})</td><td style="text-align:right">${money(AB.summary.platform.charge, cur)}</td></tr>
        <tr><td><b>Estimated total</b></td><td style="text-align:right"><b>${money(AB.summary.total, cur)}</b></td></tr></tbody></table>
        ${AB.by_user.length ? `<table class="t small" style="margin-top:.6rem"><thead><tr><th>Teacher</th><th>Platform tokens</th><th>Own-key tokens</th><th>Charge</th></tr></thead><tbody>${AB.by_user.map((u) =>
          `<tr><td>${esc(u.email)}</td><td>${tok(u.platform_tokens)}</td><td>${tok(u.own_key_tokens)}</td><td>${money(u.charge, cur)}</td></tr>`).join('')}</tbody></table>` : ''}` : ''}
      <div class="muted small" style="margin-top:.6rem">Platform fee: ${money(B.pricing.platform_fee_monthly, cur)} per month. Input ${money(B.pricing.input_per_million, cur)} / output ${money(B.pricing.output_per_million, cur)} per million tokens.</div></div>`;
  }

  EF.views.settings = async function () {
    const main = shell('#/settings', `<div class="pagehead"><div><h1>AI &amp; billing</h1><div class="muted">Choose which AI writes your papers and see exactly what you are charged.</div></div></div><div id="ai"><span class="spin"></span></div>`);
    let month = '';
    async function draw() {
      const q = month ? '?month=' + encodeURIComponent(month) : '';
      const [S, B] = await Promise.all([api('/ai/settings'), api('/billing' + q)]);
      const AB = S.can_set_workspace ? await api('/admin/billing' + q) : null;
      month = B.month;
      const e = S.effective, src = SOURCES[e.source] || ['', e.source];
      const explain = { user: 'Questions and diagram reading use your key. You pay your AI provider directly; ExamForge charges only the platform fee.',
        workspace: 'Your school’s key is used. Your school pays its AI provider directly; ExamForge charges only the platform fee.',
        platform: 'No key of your own is set, so ExamForge’s AI is used and tokens are charged at the rates below. If a paper uses no AI tokens, only the platform fee applies.',
        offline: 'No AI is connected. You can still make papers from maths templates and your own material, and you pay only the platform fee.' }[e.source];
      $('#ai', main).innerHTML = `<div class="card"><h2>Which AI is used right now</h2>
        <div class="row"><span class="badge ${src[0]}" style="font-size:.9rem">${esc(src[1])}</span>${e.offline ? '' : `<span class="muted small">${esc(e.provider)} · ${esc(e.model)}</span>`}</div>
        <p class="muted" style="margin:.5rem 0 0">${esc(explain)}</p>
        <div class="muted small" style="margin-top:.5rem">AI is only ever used to write questions that need judgement, to read pictures or scans (only if you tick it when uploading), and to learn a pattern from a sample paper. Reading text from your PDFs and documents never uses AI.</div></div>
        <div class="grid2" style="align-items:start"><div class="card"><h2>Use your own AI key</h2>
          <p class="muted small">Your key is encrypted on our server, never shown again and never sent to your browser. It overrides the school key and the platform AI for your work only.</p>${keyForm('user', S.user, S)}</div>
        ${S.can_set_workspace ? `<div class="card"><h2>School-wide AI key <span class="badge">admin</span></h2>
          <p class="muted small">Used by every teacher who has not set their own key. Billed by your AI provider, not by ExamForge.</p>${keyForm('workspace', S.workspace, S)}</div>`
        : `<div class="card"><h2>School-wide AI key</h2><p class="muted">${S.workspace ? 'Your administrator has set a school key; it is used unless you add your own.' : 'Your administrator has not set a school key.'}</p></div>`}</div>
        ${billingCard(B, AB, month)}`;

      $$('[data-scope]', main).forEach((box) => {
        const scope = box.dataset.scope, val = (k) => $(`[data-k="${k}"]`, box).value.trim(), out = $('[data-out]', box);
        $('[data-k=provider]', box).onchange = (ev) => {
          const p = S.providers.find((x) => x.id === ev.target.value) || {};
          $('[data-url]', box).hidden = !p.needs_url;
          $('[data-k=model]', box).placeholder = p.default_model || 'model name';
        };
        $('[data-a=save]', box).onclick = async () => {
          try {
            await api('/ai/settings', { method: 'PUT', json: { scope, provider: val('provider'), model: val('model'), fast_model: val('fast_model'), base_url: val('base_url'), api_key: $('[data-k=api_key]', box).value.trim() } });
            toast('AI key saved', 'ok'); draw();
          } catch (err) { toast(err.message, 'bad'); }
        };
        $('[data-a=test]', box).onclick = async () => {
          out.textContent = 'Testing…';
          try { const r = await api('/ai/test', { method: 'POST', json: { scope } }); out.textContent = r.ok ? `Connected (${r.model}, ${r.latency_ms} ms)` : 'Failed: ' + r.error; out.style.color = r.ok ? 'var(--ok)' : 'var(--bad)'; }
          catch (err) { out.textContent = err.message; out.style.color = 'var(--bad)'; }
        };
        $('[data-a=remove]', box).onclick = async () => {
          if (!(await EF.confirm('Remove this API key? Papers will use the next available AI.', 'Remove'))) return;
          try { await api('/ai/settings?scope=' + scope, { method: 'DELETE' }); toast('Key removed', 'ok'); draw(); } catch (err) { toast(err.message, 'bad'); }
        };
      });
      const bm = $('#bm', main); if (bm) bm.onchange = () => { month = bm.value; draw(); };
      // keep the sidebar badge honest after a change
      try { EF.state.meta = await api('/meta'); } catch (e2) { /* ignore */ }
    }
    draw().catch((err) => toast(err.message, 'bad'));
  };
})();
