/* ExamForge front end — paper viewer / editor, quality report and downloads. */
(function () {
  'use strict';
  const { esc, $, $$, fm, fmt, api, toast, shell } = EF;
  const letter = (i) => 'abcd'[i] || String(i + 1);
  const typeLabel = (t) => (EF.state.meta.question_types.find((x) => x.id === t) || { label: t }).label;
  const dgImg = (spec) => (spec ? `<figure><img class="dg" data-dg="${EF.dgRegister(spec)}" alt="Diagram">${spec.caption ? `<figcaption>${esc(spec.caption)}</figcaption>` : ''}</figure>` : '');

  function sectionMarksLine(s) {
    const att = Math.min(s.attempt || s.count, s.count);
    let l = att < s.count ? `Attempt any ${att} of ${s.count} questions · ${att} × ${fm(s.marks_each)} = ${fm(att * s.marks_each)} marks` : `${s.count} × ${fm(s.marks_each)} = ${fm(s.count * s.marks_each)} marks`;
    if (s.negative_marks) l += ` · −${fm(s.negative_marks)} for each incorrect answer`;
    return l;
  }
  const sectionName = (id) => (/^[A-Za-z]\d$/.test(id) ? id[0] : id);

  // ---------------------------------------------------------------- answer + question rendering
  function stepsTable(ans, total) {
    const steps = (ans.steps || []).filter((s) => s.text);
    if (!steps.length || (steps.length === 1 && steps[0].text.trim() === (ans.text || '').trim())) return '';
    return `<table><tr><th>Marking scheme</th><th>Marks</th></tr>${steps.map((s) => `<tr><td>${fmt(s.text)}</td><td>${fm(s.marks)}</td></tr>`).join('')}<tr><th>Total</th><th>${fm(total)}</th></tr></table>`;
  }
  function answerHtml(ans, options, total) {
    let t = '';
    if (ans.correct_option != null && options && options[ans.correct_option] != null) {
      t += `<div><b>Correct option: (${letter(ans.correct_option)})</b> ${fmt(options[ans.correct_option])}</div>`;
      if (ans.text && !/^\([a-dA-D]\)/.test(ans.text)) t += `<div>${fmt(ans.text)}</div>`;
    } else if (ans.text) t += `<div>${fmt(ans.text)}</div>`;
    return `<div class="ans"><div class="lbl">ANSWER</div>${t}${stepsTable(ans, total)}${dgImg(ans.diagram)}</div>`;
  }
  function optionsHtml(opts, correct, show) {
    if (!opts || !opts.length) return '';
    const long = opts.some((o) => o.length > 40);
    return `<div class="opts ${long ? 'one' : ''}">${opts.map((o, i) => `<div class="${show && correct === i ? 'right-opt' : ''}">(${letter(i)}) ${fmt(o)}${show && correct === i ? ' ✓' : ''}</div>`).join('')}</div>`;
  }
  function tableHtml(t) {
    if (!t || !t.rows || !t.rows.length) return '';
    return `<table class="dtable">${t.headers && t.headers.length ? `<tr>${t.headers.map((h) => `<th>${fmt(h)}</th>`).join('')}</tr>` : ''}${t.rows.map((r) => `<tr>${r.map((c) => `<td>${fmt(c)}</td>`).join('')}</tr>`).join('')}</table>`;
  }
  function matchHtml(m) {
    if (!m) return '';
    return `<table class="dtable"><tr><th>Column I</th><th>Column II</th></tr>${m.left.map((l, i) => `<tr><td>${i + 1}. ${fmt(l)}</td><td>(${letter(i)}) ${fmt(m.right[i])}</td></tr>`).join('')}</table>`;
  }
  function subsHtml(subs, show) {
    return (subs || []).map((s) => `<div style="margin:.35rem 0 .35rem 1rem"><div class="q-head"><div class="qtext">${fmt(s.label)} ${fmt(s.text)}</div><span class="marks">[${fm(s.marks)}]</span></div>
      ${optionsHtml(s.options, s.answer.correct_option, show)}${show ? answerHtml(s.answer, s.options, s.marks) : ''}</div>`).join('');
  }
  function bodyHtml(o, show, total) {
    return `<div class="qtext">${fmt(o.text)}</div>${dgImg(o.diagram)}${tableHtml(o.table)}${matchHtml(o.match)}${optionsHtml(o.options, o.answer && o.answer.correct_option, show)}${subsHtml(o.sub_questions, show)}
      ${show && !(o.sub_questions || []).length ? answerHtml(o.answer, o.options, total) : ''}`;
  }
  const ORIGIN = { code: 'Written by code', bank: 'From your bank', model: 'Written by AI', source: 'Copied from your document' };
  const VERIFIED = { construction: 'Exact answer', source: 'From your material', checked: 'Answer checked', flagged: 'Check the answer' };
  function provenanceBadge(q) {
    const parts = [];
    if (q.origin) parts.push(`<span class="badge" title="Who wrote this question">${ORIGIN[q.origin] || esc(q.origin)}</span>`);
    if (q.verified) parts.push(`<span class="badge ${q.verified === 'flagged' ? 'hard' : 'ok'}" title="${q.verified === 'flagged' ? 'A computer check contradicts this answer key' : 'Verified without AI'}">${VERIFIED[q.verified] || esc(q.verified)}</span>`);
    return parts.join('');
  }

  function questionHtml(q, num, P) {
    const failed = q.text.startsWith('[This question could not be generated');
    if (P.editing === q.id) return `<div class="q editing" data-qid="${q.id}"><span class="qnum">${num}</span>${editorHtml(q)}</div>`;
    return `<div class="q ${failed ? 'flag' : ''}" data-qid="${q.id}"><span class="qnum">${num}.</span>
      <div class="q-head"><div style="flex:1">${bodyHtml(q, P.answers, q.marks)}</div><span class="marks">[${fm(q.marks)}]</span></div>
      ${q.alternative ? `<div class="or">OR</div>${bodyHtml(q.alternative, P.answers, q.marks)}` : ''}
      <div class="tools"><span class="badge ${q.difficulty}">${q.difficulty}</span><span class="badge">${esc(typeLabel(q.type))}</span><span class="badge">${esc(q.bloom)}</span>${q.topic ? `<span class="badge">${esc(q.topic)}</span>` : ''}${provenanceBadge(q)}
        ${P.canEdit ? `<button class="btn sm" data-act="edit">Edit</button><button class="btn sm" data-act="regen">${failed ? 'Generate' : 'Regenerate'}</button><button class="btn sm danger" data-act="del">Remove</button>` : ''}</div></div>`;
  }

  function sheetHtml(P) {
    const c = P.content, m = c.meta;
    let n = 0;
    let h = `${m.institution ? `<div class="inst">${esc(m.institution)}</div>` : ''}<div class="exam">${esc(m.exam_name || m.title)}</div>
      <div class="sub">${esc([m.subject, m.class_level && 'Class/Level: ' + m.class_level, m.board].filter(Boolean).join(' · '))}${m.set_label ? ` · Set ${esc(m.set_label)}` : ''}</div>
      <div class="bar"><span>Time Allowed: ${Math.floor(m.duration_minutes / 60) ? Math.floor(m.duration_minutes / 60) + ' hour(s) ' : ''}${m.duration_minutes % 60 ? (m.duration_minutes % 60) + ' minutes' : ''}</span><span>Maximum Marks: ${fm(m.total_marks)}</span></div>`;
    if ((m.instructions || []).length) h += `<b style="font:700 .8rem -apple-system,sans-serif;letter-spacing:.05em">GENERAL INSTRUCTIONS</b><ol class="instr">${m.instructions.map((i) => `<li>${fmt(i)}</li>`).join('')}</ol>`;
    c.sections.forEach((s) => {
      if (!s.questions.length) return;
      h += `<div class="sec-head"><b>SECTION ${esc(sectionName(s.id))}${s.title ? ' — ' + esc(s.title) : ''}</b><div>${esc(sectionMarksLine(s))}</div></div>`;
      if (s.instruction) h += `<div class="muted" style="font-style:italic;margin-bottom:.4rem">${esc(s.instruction)}</div>`;
      s.questions.forEach((q) => { n += 1; h += questionHtml(q, n, P); });
    });
    return h;
  }

  // ---------------------------------------------------------------- inline editor
  function editorHtml(q) {
    const opt = (q.options || []).length && q.type === 'mcq';
    const steps = (q.answer.steps || []).map((s, i) => `<div class="step-row"><input type="text" data-step="${i}" value="${esc(s.text)}"><input type="number" step="0.5" min="0" data-stepm="${i}" value="${s.marks}"></div>`).join('');
    return `<div class="editor">
      <label class="f">Question<textarea data-f="text">${esc(q.text)}</textarea></label>
      <div class="grid3"><label class="f">Marks<input type="number" step="0.5" min="0.5" data-f="marks" value="${q.marks}"></label>
        <label class="f">Difficulty<select data-f="difficulty">${['easy', 'medium', 'hard'].map((d) => `<option ${d === q.difficulty ? 'selected' : ''}>${d}</option>`).join('')}</select></label></div>
      ${opt ? `<div class="f">Options (select the correct one)${q.options.map((o, i) => `<div class="opt-row"><input type="radio" name="co" value="${i}" ${q.answer.correct_option === i ? 'checked' : ''}><input type="text" data-opt="${i}" value="${esc(o)}"></div>`).join('')}</div>` : ''}
      ${q.type === 'assertion_reason' ? `<label class="f">Correct option<select data-f="co">${[0, 1, 2, 3].map((i) => `<option value="${i}" ${q.answer.correct_option === i ? 'selected' : ''}>(${letter(i)}) ${esc(q.options[i] || '')}</option>`).join('')}</select></label>` : ''}
      ${(q.sub_questions || []).map((s, i) => `<div class="f">Part ${esc(s.label)}<textarea data-sub="${i}">${esc(s.text)}</textarea><div class="grid2"><label class="f">Marks<input type="number" step="0.5" data-subm="${i}" value="${s.marks}"></label><label class="f">Answer<input type="text" data-suba="${i}" value="${esc(s.answer.text)}"></label></div></div>`).join('')}
      <label class="f">Model answer<textarea data-f="answer">${esc(q.answer.text)}</textarea></label>
      ${(q.answer.steps || []).length || !(q.sub_questions || []).length ? `<div class="f">Marking scheme (step · marks)${steps}<button class="btn sm" data-act="addstep" style="align-self:flex-start">＋ Step</button></div>` : ''}
      ${q.alternative ? `<label class="f">OR alternative<textarea data-f="alt">${esc(q.alternative.text)}</textarea></label><label class="f">Alternative answer<textarea data-f="altans">${esc(q.alternative.answer.text)}</textarea></label>` : ''}
      <label class="f">Diagram (JSON spec — leave empty for none)<textarea data-f="diagram" style="font-family:ui-monospace,monospace;font-size:.8rem;min-height:110px">${esc(q.diagram ? JSON.stringify(q.diagram, null, 1) : '')}</textarea></label>
      <div class="row"><button class="btn primary sm" data-act="apply">Apply</button><button class="btn sm" data-act="cancel">Cancel</button><button class="btn sm" data-act="prevdg">Preview diagram</button><img id="dgprev" alt="" style="max-height:140px"></div></div>`;
  }

  function readEditor(card, q) {
    const f = (k) => $(`[data-f="${k}"]`, card);
    const nq = JSON.parse(JSON.stringify(q));
    nq.text = f('text').value.trim();
    nq.marks = parseFloat(f('marks').value) || q.marks;
    nq.difficulty = f('difficulty').value;
    if ($('[data-opt]', card)) {
      nq.options = $$('[data-opt]', card).map((i) => i.value.trim());
      const r = $('input[name=co]:checked', card); if (r) nq.answer.correct_option = +r.value;
    }
    if (f('co')) nq.answer.correct_option = +f('co').value;
    nq.answer.text = f('answer').value.trim();
    const steps = $$('[data-step]', card).map((i) => ({ text: i.value.trim(), marks: parseFloat($(`[data-stepm="${i.dataset.step}"]`, card).value) || 0 })).filter((s) => s.text);
    if ($('[data-step]', card) || $('[data-act=addstep]', card)) nq.answer.steps = steps;
    $$('[data-sub]', card).forEach((t) => {
      const i = +t.dataset.sub, s = nq.sub_questions[i];
      s.text = t.value.trim(); s.marks = parseFloat($(`[data-subm="${i}"]`, card).value) || s.marks; s.answer.text = $(`[data-suba="${i}"]`, card).value.trim();
    });
    if (nq.alternative && f('alt')) { nq.alternative.text = f('alt').value.trim(); nq.alternative.answer.text = f('altans').value.trim(); }
    const raw = f('diagram').value.trim();
    if (raw) { try { nq.diagram = JSON.parse(raw); } catch (e) { throw new Error('The diagram is not valid JSON: ' + e.message); } } else nq.diagram = null;
    if (nq.text.length < 8) throw new Error('The question text is too short.');
    return nq;
  }

  // ---------------------------------------------------------------- side panels
  function economyHtml(Q) {
    const w = Q.writers || {}, u = Q.usage || {};
    const total = Object.values(w).reduce((a, b) => a + b, 0);
    if (!total) return '';
    const label = { code: 'code', bank: 'bank', model: 'AI' };
    const mix = Object.entries(w).map(([k, v]) => `${v} ${label[k] || k}`).join(' · ');
    const tokens = u.total_tokens ? `${u.total_tokens.toLocaleString()} tokens${u.tokens_saved_by_cache ? `, ${u.tokens_saved_by_cache.toLocaleString()} saved by cache` : ''}` : 'no AI tokens used';
    return `<h3 style="margin-top:1rem">Who wrote it</h3><div class="small">${mix}</div><div class="muted small">${tokens}</div>`;
  }

  function qualityHtml(Q) {
    if (!Q) return '';
    const col = Q.score >= 85 ? 'var(--ok)' : Q.score >= 60 ? '#d98200' : 'var(--bad)';
    const d = Q.difficulty;
    const bars = ['easy', 'medium', 'hard'].map((k) => `<div class="bar-row"><span class="badge ${k}">${k}</span><div class="track"><i style="width:${Math.min(d.actual[k] || 0, 100)}%"></i><u style="left:${d.target[k]}%"></u></div><span>${d.actual[k] || 0}% <span class="muted">/ ${d.target[k]}%</span></span></div>`).join('');
    const topics = Object.entries(Q.topics || {}).map(([t, v]) => `<tr><td>${esc(t)}</td><td>${v.questions}</td><td>${fm(v.marks)}</td></tr>`).join('');
    return `<div class="card"><h2>Quality check</h2><div class="row"><div class="ring" style="--p:${Q.score};--c:${col}"><span>${Q.score}</span></div>
      <div><span class="badge ${Q.status === 'pass' ? 'ok' : 'warn'}">${Q.status === 'pass' ? 'Ready to use' : 'Needs review'}</span>
      <div class="muted small" style="margin-top:.3rem">${Q.engine ? esc(Q.engine.offline ? 'Template engine' : Q.engine.provider + ' · ' + Q.engine.model) : ''}</div></div></div>
      ${(Q.warnings || []).map((w) => `<div class="banner warn small" style="margin:.6rem 0 0">${esc(w)}</div>`).join('')}
      <h3 style="margin-top:1rem">Difficulty (by marks)</h3><div class="bars">${bars}</div><div class="muted small">Red tick = your target</div>
      <h3 style="margin-top:1rem">Topic coverage</h3><table class="t small"><thead><tr><th>Topic</th><th>Qs</th><th>Marks</th></tr></thead><tbody>${topics}</tbody></table>
      <div class="small muted" style="margin-top:.6rem">Figures: ${Q.diagrams.delivered} of ${Q.diagrams.planned} planned</div>${economyHtml(Q)}</div>`;
  }

  function downloadsHtml(paper) {
    const sets = (paper.request && paper.request.num_sets) || 1;
    return `<div class="card"><h2>Download</h2><div class="col">
      <label class="f">Document<select id="dl-mode"><option value="question">Question paper (students)</option><option value="answer">Answer key &amp; marking scheme</option><option value="combined">Teacher's copy (questions + answers)</option></select></label>
      <div class="grid2"><label class="f">Format<select id="dl-fmt"><option value="pdf">PDF</option><option value="docx">Word (.docx)</option></select></label>
      <label class="f">Set<select id="dl-set">${['A', 'B', 'C', 'D'].map((s, i) => `<option ${i >= sets ? 'class="muted"' : ''}>${s}</option>`).join('')}</select></label></div>
      <button class="btn primary" id="dl-one">Download</button>
      <button class="btn" id="dl-all">All documents (.zip)</button>
      <div class="muted small">Sets re-order questions and shuffle MCQ options; the answer key follows the set you choose. Word files keep every equation's LaTeX as image alt-text.</div></div></div>`;
  }

  // ---------------------------------------------------------------- view
  EF.views.paper = async function (id) {
    const main = shell('#/', '<span class="spin"></span>');
    let paper = await api('/papers/' + id);

    if (paper.status !== 'ready') {
      const failed = paper.status === 'failed';
      main.innerHTML = `<div class="pagehead"><div><h1>${esc(paper.title || paper.subject)}</h1><div class="muted">${failed ? 'Generation failed' : 'Writing your paper…'}</div></div><a class="btn right" href="#/">← All papers</a></div>
        <div class="card">${failed ? `<div class="banner bad">${esc(paper.message)}</div><button class="btn primary" id="again">Generate again</button>`
        : `<div class="row" style="margin-bottom:.6rem"><span class="spin"></span><b>${esc(paper.message || 'Queued')}</b><span class="muted right">${paper.progress}%</span></div><div class="progress"><i style="width:${paper.progress}%"></i></div>
        <p class="muted small" style="margin-bottom:0">You can leave this page; the paper appears in your list when it is ready.</p>`}</div>`;
      if (failed) $('#again', main).onclick = async () => { try { const r = await api('/papers', { method: 'POST', json: paper.request }); location.hash = '#/paper/' + r.id; } catch (e) { toast(e.message, 'bad'); } };
      else EF.after(() => EF.views.paper(id), 1500);
      return;
    }

    const P = { content: paper.content, answers: false, editing: null, dirty: false, canEdit: paper.can_edit !== false };
    main.innerHTML = `${EF.supportBanner()}<div class="pagehead"><div><h1>${esc(paper.title)}</h1><div class="muted">${esc(paper.subject)} · revision ${paper.revision}${paper.created_by ? ' · by ' + esc(paper.created_by) : ''}</div></div>
      <div class="row right"><label class="row small"><input type="checkbox" id="showans"> Show answers</label>${paper.can_edit === false ? '' : '<button class="btn primary" id="save" disabled>Save changes</button>'}<a class="btn" href="#/">← All papers</a></div></div>
      <div id="qbanner"></div><div class="layout"><div class="sheet" id="sheet"></div><aside class="col sticky"><div id="dl">${EF.state.user.support ? '' : downloadsHtml(paper)}</div><div id="ql">${qualityHtml(paper.quality)}</div></aside></div>`;
    EF.bindSupport(main);
    const sheet = $('#sheet', main), saveBtn = $('#save', main) || { disabled: true };
    const setDirty = (v) => { P.dirty = v; saveBtn.disabled = !v; };
    const draw = () => { EF.dgReset(); sheet.innerHTML = sheetHtml(P); EF.enhance(sheet); };
    draw();
    if (paper.quality && paper.quality.engine && paper.quality.engine.offline) $('#qbanner', main).innerHTML = EF.engineBanner();

    const save = async () => {
      saveBtn.disabled = true;
      try { const r = await api('/papers/' + id, { method: 'PUT', json: P.content }); P.content = r.content; paper = r; setDirty(false); draw(); toast('Saved', 'ok'); return true; }
      catch (e) { toast(e.message, 'bad'); setDirty(true); return false; }
    };
    saveBtn.onclick = save;
    if (!P.canEdit) $('#qbanner', main).innerHTML = `<div class="banner info">This paper was made by <b>${esc(paper.created_by)}</b>. ${EF.state.user.support ? 'This is a read-only support view.' : `You can read it and download it; only ${esc(paper.created_by)} or your school administrator can change it.`}</div>`;
    $('#showans', main).onchange = (e) => { P.answers = e.target.checked; draw(); };
    window.onbeforeunload = () => (P.dirty ? 'You have unsaved changes.' : undefined);

    const find = (qid) => { for (const s of P.content.sections) { const i = s.questions.findIndex((q) => q.id === qid); if (i >= 0) return { s, i, q: s.questions[i] }; } return null; };

    sheet.addEventListener('click', async (ev) => {
      const btn = ev.target.closest('[data-act]'); if (!btn) return;
      const card = btn.closest('.q'); if (!card) return;
      const hit = find(card.dataset.qid); if (!hit) return;
      const act = btn.dataset.act;
      if (act === 'edit') { P.editing = hit.q.id; draw(); }
      else if (act === 'cancel') { P.editing = null; draw(); }
      else if (act === 'addstep') {
        const nq = (() => { try { return readEditor(card, hit.q); } catch (e) { return JSON.parse(JSON.stringify(hit.q)); } })();
        nq.answer.steps = (nq.answer.steps || []).concat([{ text: 'New step', marks: 0 }]); hit.s.questions[hit.i] = nq; draw();
      } else if (act === 'apply') {
        try { const nq = readEditor(card, hit.q); hit.s.questions[hit.i] = nq; P.editing = null; setDirty(true); draw(); } catch (e) { toast(e.message, 'bad'); }
      } else if (act === 'prevdg') {
        try {
          const raw = $('[data-f=diagram]', card).value.trim(); if (!raw) return;
          const res = await api('/diagrams/render', { method: 'POST', json: { spec: JSON.parse(raw), format: 'svg' }, raw: true });
          $('#dgprev', card).src = URL.createObjectURL(await res.blob());
        } catch (e) { toast('Diagram: ' + e.message, 'bad'); }
      } else if (act === 'del') {
        if (!(await EF.confirm('Remove this question from the paper?', 'Remove'))) return;
        hit.s.questions.splice(hit.i, 1); setDirty(true); draw();
      } else if (act === 'regen') {
        const instruction = await EF.prompt('Anything specific for the new question? (optional) e.g. "make it a word problem" or "easier"', '');
        if (instruction === null) return;
        btn.disabled = true; btn.textContent = 'Writing…';
        try {
          if (P.dirty && !(await save())) return;
          await api(`/papers/${id}/questions/${hit.q.id}/regenerate`, { method: 'POST', json: { instruction } });
          paper = await api('/papers/' + id); P.content = paper.content; draw(); toast('Question regenerated', 'ok');
        } catch (e) { toast(e.message, 'bad'); draw(); }
      }
    });

    const dl = $('#dl', main);
    if (EF.state.user.support) return;  // no downloads in a support view
    const ensureSaved = async () => (P.dirty ? save() : true);
    $('#dl-one', dl).onclick = async (ev) => {
      if (!(await ensureSaved())) return;
      const b = ev.target; b.disabled = true;
      const q = new URLSearchParams({ format: $('#dl-fmt', dl).value, mode: $('#dl-mode', dl).value, set: $('#dl-set', dl).value });
      try { await EF.download(`/papers/${id}/export?${q}`, 'paper'); } catch (e) { toast(e.message, 'bad'); }
      b.disabled = false;
    };
    $('#dl-all', dl).onclick = async (ev) => {
      if (!(await ensureSaved())) return;
      const b = ev.target; b.disabled = true;
      const q = new URLSearchParams({ formats: 'pdf,docx', modes: 'question,answer,combined', sets: (paper.request && paper.request.num_sets) || 1 });
      try { await EF.download(`/papers/${id}/export-all?${q}`, 'paper.zip'); } catch (e) { toast(e.message, 'bad'); }
      b.disabled = false;
    };
  };
})();
