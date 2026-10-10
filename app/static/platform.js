/* ExamForge front end — platform console: the operator's list of schools, their terms and this month's charges. */
(function () {
  'use strict';
  const { esc, $, $$, api, toast, shell, when } = EF;
  const money = (n, cur) => `${esc(cur)} ${Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
  const tok = (n) => Number(n || 0).toLocaleString();

  EF.views.platform = async function () {
    if (!EF.state.user.is_superadmin) { location.hash = '#/'; return; }
    const main = shell('#/platform', `<div class="pagehead"><div><h1>Platform</h1>
      <div class="muted">Schools on this service, their terms and what each owes this month. School content stays hidden unless a school's administrator allows a read-only support view.</div></div></div>
      <div id="ptot"></div><div class="card" id="plist"><span class="spin"></span></div>
      <div class="card"><h2>Onboard a school</h2>
        <div class="row" style="flex-wrap:wrap;align-items:flex-end">
          <label class="f" style="min-width:220px">School name<input type="text" id="pn"></label>
          <label class="f" style="min-width:220px">Administrator email <span class="muted small" style="font-weight:400">(optional)</span><input type="email" id="pe"></label>
          <button class="btn primary" id="pmake">Create school</button></div>
        <div id="pnew"></div></div>`);
    let month = '';
    const draw = async () => {
      const d = await api('/platform/orgs' + (month ? '?month=' + encodeURIComponent(month) : ''));
      month = d.month;
      const t = d.totals, cur = d.currency;
      $('#ptot', main).innerHTML = `<div class="card"><div class="row"><div><b>${t.active}</b> active of <b>${t.schools}</b> schools · <b>${tok(t.platform_tokens)}</b> platform tokens ·
        expected revenue <b>${money(t.revenue, cur)}</b></div>
        <label class="right row small">Month <input type="month" id="pm" value="${esc(month)}" style="width:150px"></label></div></div>`;
      $('#pm', main).onchange = (e) => { month = e.target.value; draw().catch((x) => toast(x.message, 'bad')); };
      $('#plist', main).innerHTML = `<table class="t"><thead><tr><th>School</th><th>Users</th><th>Papers</th><th>Terms</th><th>Tokens</th><th>Total</th><th>Support</th><th></th></tr></thead><tbody>${d.orgs.map((o) =>
        `<tr><td><b>${esc(o.name)}</b><div class="muted small">${o.is_active ? 'Active' : '<span class="badge bad">suspended</span>'} · since ${when(o.created_at)}</div></td>
        <td>${o.users}</td><td>${o.papers}</td>
        <td class="small">${money(o.terms.platform_fee_monthly, cur)} / month<br>${money(o.terms.price_input_per_million, cur)} in · ${money(o.terms.price_output_per_million, cur)} out per 1M${o.terms.custom ? ' <span class="badge warn">custom</span>' : ''}</td>
        <td>${tok(o.month.platform_tokens)}</td><td><b>${money(o.month.total, cur)}</b></td>
        <td class="small">${o.support && o.support.active ? `<span class="badge ok">allowed</span><div class="muted">until ${when(o.support.expires_at)}</div>${o.support.note ? `<div class="muted">“${esc(o.support.note)}”</div>` : ''}` : '<span class="muted">not allowed</span>'}</td>
        <td style="text-align:right;white-space:nowrap"><button class="btn sm" data-terms="${o.id}">Terms</button>
          <button class="btn sm" data-sup="${o.id}" ${o.support && o.support.active ? '' : 'disabled title="The school has not allowed support access"'}>Open as support</button>
          <button class="btn sm" data-inv="${o.id}">Admin invite</button>
          <button class="btn sm ${o.is_active ? 'danger' : ''}" data-sus="${o.id}" data-v="${!o.is_active}">${o.is_active ? 'Suspend' : 'Reactivate'}</button></td></tr>`).join('')}</tbody></table>`;
      const byId = (id) => d.orgs.find((o) => o.id === id);
      $$('[data-sus]', main).forEach((b) => (b.onclick = async () => {
        const o = byId(b.dataset.sus), on = b.dataset.v === 'true';
        if (!on && !(await EF.confirm(`Suspend ${o.name}? Nobody in that school will be able to sign in or use the service.`, 'Suspend'))) return;
        try { await api('/platform/orgs/' + o.id, { method: 'PATCH', json: { is_active: on } }); draw(); } catch (e) { toast(e.message, 'bad'); }
      }));
      $$('[data-sup]', main).forEach((b) => (b.onclick = async () => {
        try {
          const r = await api(`/platform/orgs/${b.dataset.sup}/support-session`, { method: 'POST' });
          sessionStorage.setItem('ef_operator_token', EF.state.token);
          EF.state.token = r.token; sessionStorage.setItem('ef_token', r.token);
          location.hash = '#/'; await EF.start();
        } catch (e) { toast(e.message, 'bad'); draw(); }
      }));
      $$('[data-inv]', main).forEach((b) => (b.onclick = async () => {
        const o = byId(b.dataset.inv);
        const r = await api(`/platform/orgs/${o.id}/admin-invite`, { method: 'POST', json: { email: '' } });
        $('#pnew', main).innerHTML = `<div class="banner info" style="margin-top:.8rem"><b>New administrator invitation for ${esc(o.name)} — shown once.</b>
          <div style="margin-top:.4rem">Code: <code>${esc(r.code)}</code></div></div>`;
      }));
      $$('[data-terms]', main).forEach((b) => (b.onclick = async () => {
        const o = byId(b.dataset.terms), t = o.terms, cur = (v) => (t.custom ? v : '');
        const r = await EF.dialog({ title: `Terms for ${o.name}`, message: 'Leave a box empty to use the platform default.', ok: 'Save terms', fields: [
          { label: 'Monthly platform fee', value: cur(t.platform_fee_monthly), type: 'number', step: 'any', min: 0 },
          { label: 'Price per 1M input tokens', value: cur(t.price_input_per_million), type: 'number', step: 'any', min: 0 },
          { label: 'Price per 1M output tokens', value: cur(t.price_output_per_million), type: 'number', step: 'any', min: 0 }] });
        if (!r) return;
        const num = (v) => (v.trim() === '' ? null : Number(v));
        const body = { platform_fee_monthly: num(r[0]), price_input_per_million: num(r[1]), price_output_per_million: num(r[2]) };
        if (Object.values(body).some((v) => v !== null && !(Number.isFinite(v) && v >= 0))) { toast('Enter numbers of zero or more.', 'bad'); return; }
        try { await api('/platform/orgs/' + o.id, { method: 'PATCH', json: body }); toast('Terms saved', 'ok'); draw(); } catch (e) { toast(e.message, 'bad'); }
      }));
    };
    $('#pmake', main).onclick = async () => {
      const name = $('#pn', main).value.trim();
      if (name.length < 2) { toast('Enter the school name.', 'bad'); return; }
      try {
        const r = await api('/platform/orgs', { method: 'POST', json: { name, admin_email: $('#pe', main).value.trim() } });
        $('#pnew', main).innerHTML = `<div class="banner info" style="margin-top:.8rem"><b>${esc(r.name)} created. Send this to its administrator — shown once.</b>
          <div style="margin-top:.4rem">Code: <code>${esc(r.admin_invite_code)}</code></div>
          <div class="small" style="margin-top:.2rem">Link: <code>${esc(location.origin + '/#/join/' + r.admin_invite_code)}</code></div></div>`;
        $('#pn', main).value = ''; $('#pe', main).value = ''; draw();
      } catch (e) { toast(e.message, 'bad'); }
    };
    draw().catch((e) => toast(e.message, 'bad'));
  };
})();
