/* ExamForge front end — "New paper" wizard. */
(function () {
  'use strict';
  const { esc, $, $$, fm, api, toast, shell } = EF;

  const blank = () => ({
    step: 0, title: '', institution: '', subject: '', class_level: '', board: '', exam_name: '', date: '', language: 'English',
    pattern: null, patternId: '', docIds: [], topics: [], syllabus: '', difficulty: { easy: 30, medium: 50, hard: 20 },
    diagram_mode: 'auto', num_sets: 1, extra: '', avoid: true, reuse: false, writer: 'auto', duration: '', blueprint: null,
  });
  let wiz = blank();
  const STEPS = ['Subject & sources', 'Exam pattern', 'Syllabus & difficulty', 'Review & generate'];
  const SUBJECTS = ['Mathematics', 'Physics', 'Chemistry', 'Biology', 'Computer Science', 'English', 'History', 'Geography', 'Economics', 'Accountancy', 'Business Studies', 'Social Science', 'Science'];

  const clone = (o) => JSON.parse(JSON.stringify(o));
  const sectionLabel = (t) => (EF.state.meta.question_types.find((x) => x.id === t) || { label: t }).label;
  const totalMarks = (p) => (p ? p.sections.reduce((a, s) => a + Math.min(s.attempt || s.count, s.count) * s.marks_each, 0) : 0);

  function request() {
    const w = wiz;
    return {
      title: w.title, institution: w.institution, subject: w.subject, class_level: w.class_level, board: w.board, exam_name: w.exam_name,
      date: w.date, language: w.language, pattern: w.pattern, duration_minutes: w.duration ? +w.duration : null, document_ids: w.docIds,
      topics: w.topics.map((t) => ({ name: t.name, weight: +t.weight || 0, unit: t.unit || '' })), syllabus_text: w.topics.length ? '' : w.syllabus,
      difficulty: w.difficulty, diagram_mode: w.diagram_mode, extra_instructions: w.extra, num_sets: +w.num_sets || 1, avoid_repeats: w.avoid, reuse_bank: w.reuse, writer_mode: w.writer,
    };
  }

  function pick(id) {
    const p = EF.state.meta.patterns.find((x) => x.id === id);
    if (!p) return;
    wiz.patternId = id; wiz.pattern = clone({ id: p.custom ? '' : p.id, name: p.name, board: p.board, level: p.level, description: p.description, duration_minutes: p.duration_minutes, instructions: p.instructions || [], sections: p.sections });
    if (!wiz.board && p.board) wiz.board = p.board;
  }

  // ---------------------------------------------------------------- step 1
  function stepBasics(el, docs) {
    el.innerHTML = `<div class="card"><h2>About the exam</h2><div class="grid3">
      <label class="f">Subject *<input type="text" id="w-subject" list="subjects" value="${esc(wiz.subject)}" placeholder="e.g. Mathematics"><datalist id="subjects">${SUBJECTS.map((s) => `<option>${s}</option>`).join('')}</datalist></label>
      <label class="f">Class / level<input type="text" id="w-class" value="${esc(wiz.class_level)}" placeholder="e.g. 10, B.Sc Sem 2"></label>
      <label class="f">Board / university<input type="text" id="w-board" value="${esc(wiz.board)}" placeholder="e.g. CBSE, Anna University"></label>
      <label class="f">Institution name (printed on paper)<input type="text" id="w-inst" value="${esc(wiz.institution)}"></label>
      <label class="f">Exam name<input type="text" id="w-exam" value="${esc(wiz.exam_name)}" placeholder="e.g. Half-yearly Examination 2026"></label>
      <label class="f">Date<input type="text" id="w-date" value="${esc(wiz.date)}" placeholder="optional"></label></div>
      <div class="grid2" style="margin-top:.9rem"><label class="f">Paper title (optional)<input type="text" id="w-title" value="${esc(wiz.title)}"></label>
      <label class="f">Language of the paper<select id="w-lang">${langOptions()}</select></label></div>
      <div id="w-lang-note"></div></div>
      <div class="card"><div class="row"><h2 style="margin:0">Study material</h2><a class="right small" href="#/library">Manage library →</a></div>
      <p class="muted small">Choose the documents the questions should be based on. Leave empty to rely on the model's general curriculum knowledge${EF.state.meta.engine.offline ? ' (needs a configured language model, except for mathematics)' : ''}.</p>
      ${docs.length ? `<div class="docpick">${docs.map((d) => `<label><input type="checkbox" data-doc="${d.id}" ${wiz.docIds.includes(d.id) ? 'checked' : ''} ${d.status !== 'ready' ? 'disabled' : ''}>
        <span style="flex:1"><b>${esc(d.filename)}</b> <span class="muted small">${d.status === 'ready' ? (d.stats.words || 0).toLocaleString() + ' words' : ''}</span></span>${EF.statusBadge(d.status)}</label>`).join('')}</div>`
      : '<div class="empty">No documents yet. <a href="#/library">Upload some</a>.</div>'}</div>`;
    const bind = (id, key) => ($('#' + id, el).oninput = (e) => (wiz[key] = e.target.value));
    [['w-subject', 'subject'], ['w-class', 'class_level'], ['w-board', 'board'], ['w-inst', 'institution'], ['w-exam', 'exam_name'], ['w-date', 'date'], ['w-title', 'title']].forEach(([i, k]) => bind(i, k));
    const note = () => {
      $('#w-lang-note', el).innerHTML = languageNote(docs);
      const use = $('[data-setlang]', el);
      if (use) use.onclick = () => { wiz.language = use.dataset.setlang; $('#w-lang', el).value = wiz.language; note(); };
    };
    $('#w-lang', el).onchange = (e) => { wiz.language = e.target.value; note(); };
    $$('[data-doc]', el).forEach((c) => (c.onchange = () => { wiz.docIds = $$('[data-doc]:checked', el).map((x) => x.dataset.doc); note(); }));
    note();
  }

  function langOptions() {
    const list = EF.state.meta.languages || [{ name: 'English', native: 'English' }];
    const known = list.some((l) => l.name === wiz.language);
    return (known ? '' : `<option selected>${esc(wiz.language)}</option>`) + list.map((l) =>
      `<option value="${esc(l.name)}" ${l.name === wiz.language ? 'selected' : ''}>${esc(l.name)}${l.native && l.native !== l.name ? ' — ' + esc(l.native) : ''}</option>`).join('');
  }

  function languageNote(docs) {
    const list = EF.state.meta.languages || [];
    const cur = list.find((l) => l.name === wiz.language);
    const out = [];
    const chosen = docs.filter((d) => wiz.docIds.includes(d.id) && d.stats && d.stats.language_hint);
    const hint = chosen.length ? chosen[0].stats : null;
    if (hint && hint.language_hint !== wiz.language) {
      out.push(`<div class="banner info" style="margin:.7rem 0 0">Your selected material is in <b>${esc(hint.language_hint)}</b> (${esc(hint.script)} script).
        <button class="btn sm" type="button" data-setlang="${esc(hint.language_hint)}">Write the paper in ${esc(hint.language_hint)}</button></div>`);
    }
    if (cur && cur.script !== 'latin') {
      out.push(EF.state.meta.engine.offline
        ? `<div class="banner warn" style="margin:.7rem 0 0">No AI is connected, and the built-in writer can only write English. Add an AI key under <a href="#/settings">AI &amp; billing</a> to get a ${esc(cur.name)} paper.</div>`
        : `<div class="banner info" style="margin:.7rem 0 0">AI writes every question in ${esc(cur.name)}. ${esc(cur.name)} text uses roughly three times the AI tokens of English, and the question bank and built-in templates are English only. Headings print in ${esc(cur.name)}; diagram labels stay in Latin letters and digits.</div>`);
    }
    return out.join('');
  }

  // ---------------------------------------------------------------- step 2
  function stepPattern(el, docs) {
    const pats = EF.state.meta.patterns;
    const sel = wiz.patternId;
    el.innerHTML = `<div class="card"><h2>Choose the paper pattern</h2><p class="muted small">Presets follow commonly published structures — adjust anything below to match your board's current scheme.</p>
      <div class="grid3">${pats.map((p) => `<button class="card" data-pat="${p.id}" style="text-align:left;cursor:pointer;margin:0;${sel === p.id ? 'border-color:var(--brand-2);box-shadow:0 0 0 2px #cfe0ff' : ''}">
        <b>${esc(p.name)}</b><div class="muted small">${esc(p.board || '')}${p.level ? ' · ' + esc(p.level) : ''}</div>
        <div class="small" style="margin-top:.3rem">${p.total_marks || ''} marks · ${p.duration_minutes} min${p.custom ? ' · <span class="badge">mine</span>' : ''}</div></button>`).join('')}</div></div>
      <div id="pe"></div>`;
    $$('[data-pat]', el).forEach((b) => (b.onclick = () => { pick(b.dataset.pat); stepPattern(el, docs); }));
    patternEditor($('#pe', el), docs);
  }

  function patternEditor(el, docs) {
    const p = wiz.pattern;
    if (!p) { el.innerHTML = ''; return; }
    const types = EF.state.meta.question_types;
    const rows = p.sections.map((s, i) => `<tr data-i="${i}">
      <td><input type="text" data-k="id" value="${esc(s.id)}" style="width:56px"></td>
      <td><input type="text" data-k="title" value="${esc(s.title)}"></td>
      <td><select data-k="question_type">${types.map((t) => `<option value="${t.id}" ${t.id === s.question_type ? 'selected' : ''}>${esc(t.label)}</option>`).join('')}</select></td>
      <td><input type="number" data-k="count" min="1" max="100" value="${s.count}" style="width:64px"></td>
      <td><input type="number" data-k="attempt" min="1" value="${s.attempt || ''}" placeholder="all" style="width:64px"></td>
      <td><input type="number" data-k="marks_each" min="0.5" step="0.5" value="${s.marks_each}" style="width:70px"></td>
      <td><input type="number" data-k="internal_choice" min="0" value="${s.internal_choice || 0}" style="width:64px"></td>
      <td><input type="number" data-k="negative_marks" min="0" step="0.25" value="${s.negative_marks || 0}" style="width:64px"></td>
      <td><button class="btn sm danger" data-rm="${i}" ${p.sections.length < 2 ? 'disabled' : ''}>✕</button></td></tr>`).join('');
    el.innerHTML = `<div class="card"><div class="row"><h2 style="margin:0">${esc(p.name)}</h2><span class="badge ok" id="tm">${fm(totalMarks(p))} marks</span>
      <label class="f right" style="flex-direction:row;align-items:center;gap:.5rem">Duration (min)<input type="number" id="dur" value="${wiz.duration || p.duration_minutes}" style="width:80px"></label></div>
      <div style="overflow:auto"><table class="t" style="margin-top:.6rem"><thead><tr><th>Id</th><th>Heading</th><th>Question type</th><th>Questions</th><th>Attempt any</th><th>Marks each</th><th>With OR</th><th>Negative</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
      <div class="row" style="margin-top:.7rem"><button class="btn sm" id="addsec">＋ Add section</button><button class="btn sm" id="savepat">Save as my pattern</button>
      <span class="right row"><select id="inferdoc" style="width:200px"><option value="">Infer from a sample paper…</option>${docs.filter((d) => d.status === 'ready').map((d) => `<option value="${d.id}">${esc(d.filename)}</option>`).join('')}</select><button class="btn sm" id="infer">Read pattern</button></span></div>
      <details style="margin-top:.8rem"><summary class="small muted">General instructions printed on the paper (${(p.instructions || []).length})</summary>
      <textarea id="instr" style="margin-top:.4rem">${esc((p.instructions || []).join('\n'))}</textarea></details></div>`;
    const refresh = () => ($('#tm', el).textContent = fm(totalMarks(p)) + ' marks');
    $$('tr[data-i]', el).forEach((tr) => $$('[data-k]', tr).forEach((inp) => (inp.onchange = inp.oninput = () => {
      const s = p.sections[+tr.dataset.i], k = inp.dataset.k;
      if (['count', 'attempt', 'internal_choice'].includes(k)) s[k] = inp.value === '' ? (k === 'attempt' ? null : 0) : parseInt(inp.value, 10);
      else if (['marks_each', 'negative_marks'].includes(k)) s[k] = parseFloat(inp.value) || 0;
      else s[k] = inp.value;
      refresh();
    })));
    $$('[data-rm]', el).forEach((b) => (b.onclick = () => { p.sections.splice(+b.dataset.rm, 1); patternEditor(el, docs); }));
    $('#addsec', el).onclick = () => {
      const used = new Set(p.sections.map((s) => s.id));
      const id = 'ABCDEFGHIJKL'.split('').find((c) => !used.has(c)) || 'X' + p.sections.length;
      p.sections.push({ id, title: '', question_type: 'short', count: 5, attempt: null, marks_each: 2, internal_choice: 0, negative_marks: 0, instruction: '' });
      patternEditor(el, docs);
    };
    $('#dur', el).oninput = (e) => (wiz.duration = e.target.value);
    $('#instr', el).oninput = (e) => (p.instructions = e.target.value.split('\n').map((x) => x.trim()).filter(Boolean));
    $('#savepat', el).onclick = async () => {
      const name = await EF.prompt('Name for this pattern:', p.name);
      if (!name) return;
      try { await api('/patterns', { method: 'POST', json: Object.assign(clone(p), { name, id: '' }) }); EF.state.meta = await api('/meta'); toast('Pattern saved', 'ok'); } catch (e) { toast(e.message, 'bad'); }
    };
    $('#infer', el).onclick = async () => {
      const id = $('#inferdoc', el).value; if (!id) return toast('Choose a document first');
      $('#infer', el).disabled = true;
      try { const r = await api('/patterns/infer', { method: 'POST', json: { document_id: id } }); wiz.pattern = Object.assign(clone(r.pattern), { id: '' }); wiz.patternId = ''; toast('Pattern read from the sample paper — please review it', 'ok'); patternEditor(el, docs); }
      catch (e) { toast(e.message, 'bad'); $('#infer', el).disabled = false; }
    };
  }

  // ---------------------------------------------------------------- step 3
  async function loadChapters() {
    const titles = [];
    for (const id of wiz.docIds) {
      const d = await api('/documents/' + id);
      (d.chapters || []).forEach((c) => { if (!titles.includes(c.title)) titles.push(c.title); });
    }
    return titles;
  }

  function stepSyllabus(el) {
    const d = wiz.difficulty;
    el.innerHTML = `<div class="grid2" style="align-items:start"><div class="card"><h2>Syllabus coverage</h2>
      <p class="muted small">Weights decide how many marks each topic receives. Paste a syllabus outline, or load chapters from your sources.</p>
      <textarea id="syl" placeholder="Unit 1: Algebra&#10;- Linear equations&#10;- Quadratic equations&#10;Unit 2: Geometry&#10;- Triangles">${esc(wiz.syllabus)}</textarea>
      <div class="row" style="margin:.5rem 0"><button class="btn sm" id="parse">Turn outline into topics</button><button class="btn sm" id="chap" ${wiz.docIds.length ? '' : 'disabled'}>Load chapters from sources</button><button class="btn sm ghost" id="clr">Clear</button></div>
      <div id="topics"></div></div>
      <div class="col"><div class="card"><h2>Difficulty mix</h2>
        ${['easy', 'medium', 'hard'].map((k) => `<div class="range-row"><span class="badge ${k}">${k}</span><input type="range" min="0" max="100" step="5" data-d="${k}" value="${d[k]}"><b id="dv-${k}">${d[k]}%</b></div>`).join('')}
        <div class="muted small" id="dsum"></div></div>
      <div class="card"><h2>Options</h2><div class="grid2">
        <label class="f">Diagrams<select id="dg"><option value="auto">Where useful</option><option value="more">Frequent</option><option value="none">None</option></select></label>
        <label class="f">Parallel sets (A, B…)<select id="sets">${[1, 2, 3, 4].map((n) => `<option ${+wiz.num_sets === n ? 'selected' : ''}>${n}</option>`).join('')}</select></label></div>
        <label class="f" style="margin-top:.7rem">Who writes the questions
          <select id="wm"><option value="auto">Automatic (recommended)</option>
            <option value="code_only">Code only — no AI</option>
            <option value="ai_all">AI writes everything</option></select>
          <span class="muted small" style="font-weight:400">Automatic uses your question bank first, then code (exact maths, questions from your own text), and AI only where needed. Code-written questions use no AI tokens; you see the split before generating.</span></label>
        <label class="f" style="margin-top:.7rem">Extra instructions to the paper-setter<textarea id="extra" placeholder="e.g. Prefer real-life contexts; avoid questions on proofs; include one graph-based question">${esc(wiz.extra)}</textarea></label>
        <label class="check" style="margin-top:.6rem"><input type="checkbox" id="avoid" ${wiz.avoid ? 'checked' : ''}><span>Avoid repeating questions from my earlier papers</span></label>
        <label class="check" style="margin-top:.5rem"><input type="checkbox" id="reuse" ${wiz.reuse ? 'checked' : ''}><span>Reuse matching questions from my question bank first <span class="muted small">(no AI tokens for those)</span></span></label></div></div></div>`;
    $('#dg', el).value = wiz.diagram_mode;
    $('#wm', el).value = wiz.writer;
    const drawTopics = () => {
      const box = $('#topics', el);
      if (!wiz.topics.length) { box.innerHTML = '<div class="muted small">No topics listed — questions will spread across the selected material or the whole subject.</div>'; return; }
      box.innerHTML = `<table class="t"><thead><tr><th>Topic</th><th style="width:90px">Weight</th><th></th></tr></thead><tbody>${wiz.topics.map((t, i) => `<tr><td>${esc(t.name)}${t.unit && t.unit !== t.name ? `<div class="muted small">${esc(t.unit)}</div>` : ''}</td>
        <td><input type="number" min="0" max="100" value="${t.weight}" data-w="${i}"></td><td><button class="btn sm ghost" data-rm="${i}">✕</button></td></tr>`).join('')}</tbody></table>`;
      $$('[data-w]', box).forEach((inp) => (inp.oninput = () => (wiz.topics[+inp.dataset.w].weight = parseFloat(inp.value) || 0)));
      $$('[data-rm]', box).forEach((b) => (b.onclick = () => { wiz.topics.splice(+b.dataset.rm, 1); drawTopics(); }));
    };
    drawTopics();
    $('#syl', el).oninput = (e) => (wiz.syllabus = e.target.value);
    $('#parse', el).onclick = async () => {
      try { const r = await api('/syllabus/parse', { method: 'POST', json: { text: wiz.syllabus } }); wiz.topics = r.topics.map((t) => ({ name: t.name, unit: t.unit, weight: 5 })); drawTopics(); if (!r.topics.length) toast('No topics found in that text'); } catch (e) { toast(e.message, 'bad'); }
    };
    $('#chap', el).onclick = async () => {
      try { const t = await loadChapters(); wiz.topics = t.slice(0, 40).map((n) => ({ name: n, unit: '', weight: 5 })); drawTopics(); if (!t.length) toast('No chapter headings were detected in the selected sources'); } catch (e) { toast(e.message, 'bad'); }
    };
    $('#clr', el).onclick = () => { wiz.topics = []; wiz.syllabus = ''; $('#syl', el).value = ''; drawTopics(); };
    const sum = () => { const t = d.easy + d.medium + d.hard; $('#dsum', el).textContent = t === 100 ? 'Total 100%' : `Total ${t}% — will be scaled to 100%`; };
    $$('[data-d]', el).forEach((r) => (r.oninput = () => { d[r.dataset.d] = +r.value; $('#dv-' + r.dataset.d, el).textContent = r.value + '%'; sum(); }));
    sum();
    $('#dg', el).onchange = (e) => (wiz.diagram_mode = e.target.value);
    $('#sets', el).onchange = (e) => (wiz.num_sets = +e.target.value);
    $('#extra', el).oninput = (e) => (wiz.extra = e.target.value);
    $('#avoid', el).onchange = (e) => (wiz.avoid = e.target.checked);
    $('#reuse', el).onchange = (e) => (wiz.reuse = e.target.checked);
    $('#wm', el).onchange = (e) => (wiz.writer = e.target.value);
  }

  // ---------------------------------------------------------------- step 4
  function sourcesNote(src) {
    const miss = (src && src.unmatched_topics) || [];
    if (!miss.length) return '';
    return `<div class="banner warn">No passage in your chosen material matches ${miss.length === 1 ? 'this topic' : 'these topics'}:
      <b>${miss.slice(0, 5).map(esc).join(', ')}</b>${miss.length > 5 ? ` and ${miss.length - 5} more` : ''}. Questions on ${miss.length === 1 ? 'it' : 'them'} will not be based on your material.
      Topic names are matched against the document's own words, so write them in the document's language (or use its chapter titles).</div>`;
  }

  function writersCard(wr) {
    if (!wr) return '';
    const bill = { platform: 'AI tokens on the platform key are charged by usage.', byok: 'AI runs on your own key — billed by your AI provider, not by ExamForge.', none: '' }[wr.billing] || '';
    const chips = [`<span class="chip">🗃️ <b>${wr.bank}</b> from your bank</span>`, `<span class="chip">🧩 <b>${wr.code}</b> written by code (no tokens)</span>`,
      `<span class="chip">🤖 <b>${wr.ai}</b> written by AI</span>`].join('');
    let note = wr.uses_tokens ? bill : 'No AI tokens will be used for this paper — only the platform fee applies.';
    if (wr.unwritten) note = wr.ai_available
      ? `${wr.unwritten} question(s) need AI, but “Code only” is selected, so they will be left for you to write. Choose “Automatic” writing to let AI write them.`
      : `${wr.unwritten} question(s) need AI and no AI is connected, so they will be left for you to write. Add your own API key under <a href="#/settings">AI &amp; billing</a> to have them written.`;
    if (wr.language && wr.language.will_be_english) note = `The built-in writer cannot write ${esc(wr.language.name)}: whatever it can write (such as mathematics) will be in English, and the rest will be left for you to write. Add an AI key under <a href="#/settings">AI &amp; billing</a> to get a ${esc(wr.language.name)} paper.`;
    return `<div class="card"><h2>Who will write the questions</h2><div class="chips">${chips}</div>
      <div class="banner ${wr.unwritten || (wr.language && wr.language.will_be_english) ? 'warn' : 'info'}" style="margin:.7rem 0 0">${note}</div></div>`;
  }

  async function stepReview(el) {
    el.innerHTML = '<div class="card"><span class="spin"></span> Planning the paper…</div>';
    let bp;
    try { bp = await api('/papers/blueprint', { method: 'POST', json: request() }); } catch (e) { el.innerHTML = `<div class="banner bad">${esc(e.message)}</div>`; return; }
    wiz.blueprint = bp;
    const sm = bp.summary;
    el.innerHTML = `<div class="card"><h2>Blueprint</h2><div class="chips"><span class="chip"><b>${sm.questions}</b> questions</span><span class="chip"><b>${fm(bp.total_marks)}</b> marks</span>
      ${Object.entries(sm.difficulty_share).map(([k, v]) => `<span class="chip"><span class="badge ${k}">${k}</span> ${v}%</span>`).join('')}<span class="chip"><b>${sm.diagram_slots}</b> diagram question(s)</span></div>
      <p class="muted small" style="margin:.7rem 0 .4rem">Each slot is fixed before writing, so the paper hits your marks, difficulty split and topic coverage whichever model writes it.</p>
      <div style="overflow:auto;max-height:380px"><table class="t small"><thead><tr><th>#</th><th>Section</th><th>Type</th><th>Marks</th><th>Level</th><th>Bloom</th><th>Topic</th><th></th></tr></thead><tbody>${bp.slots.map((s, i) => `<tr>
      <td>${i + 1}</td><td>${esc(s.section_id)}</td><td>${esc(sectionLabel(s.question_type))}</td><td>${fm(s.marks)}</td><td><span class="badge ${s.difficulty}">${s.difficulty}</span></td><td>${esc(s.bloom)}</td><td>${esc(s.topic)}</td>
      <td>${s.wants_diagram ? '<span class="badge">figure</span> ' : ''}${s.internal_choice ? '<span class="badge">OR</span>' : ''}</td></tr>`).join('')}</tbody></table></div></div>
      ${sourcesNote(bp.sources)}${writersCard(bp.writers)}${EF.engineBanner()}<div class="row"><button class="btn primary" id="gen">Generate paper</button><span class="muted small">Writing takes a minute or two with a language model.</span></div>`;
    $('#gen', el).onclick = async () => {
      $('#gen', el).disabled = true;
      try { const r = await api('/papers', { method: 'POST', json: request() }); wiz = blank(); location.hash = '#/paper/' + r.id; }
      catch (e) { toast(e.message, 'bad'); $('#gen', el).disabled = false; }
    };
  }

  // ---------------------------------------------------------------- view
  EF.views.wizard = async function () {
    const main = shell('#/new', `<div class="pagehead"><div><h1>New question paper</h1><div class="muted">Four short steps. Nothing is generated until you confirm the blueprint.</div></div>
      <button class="btn sm right" id="reset">Start over</button></div><div class="stepper" id="stepper"></div><div id="body"><span class="spin"></span></div>
      <div class="row" style="margin-top:1rem"><button class="btn" id="back">← Back</button><button class="btn primary" id="next">Continue →</button></div>`);
    const { documents } = await api('/documents');
    if (!wiz.pattern) pick(EF.state.meta.patterns[0].id);
    const body = $('#body', main);

    function validate(step) {
      if (step === 0 && !wiz.subject.trim()) return 'Enter the subject.';
      if (step === 1) {
        const ids = wiz.pattern.sections.map((s) => s.id.trim());
        if (ids.some((i) => !i) || new Set(ids).size !== ids.length) return 'Each section needs a unique id.';
        if (wiz.pattern.sections.some((s) => !(s.count >= 1) || !(s.marks_each > 0))) return 'Every section needs a question count and marks.';
      }
      return '';
    }
    async function draw() {
      $('#stepper', main).innerHTML = STEPS.map((s, i) => `<button class="step ${i === wiz.step ? 'on' : ''} ${i < wiz.step ? 'done' : ''}" data-s="${i}"><b>${i + 1}</b>${s}</button>`).join('');
      $$('.step', main).forEach((b) => (b.onclick = () => { const t = +b.dataset.s; if (t <= wiz.step || !validate(wiz.step)) { wiz.step = t; draw(); } else toast(validate(wiz.step)); }));
      $('#back', main).style.visibility = wiz.step === 0 ? 'hidden' : 'visible';
      $('#next', main).style.display = wiz.step === STEPS.length - 1 ? 'none' : '';
      if (wiz.step === 0) stepBasics(body, documents);
      else if (wiz.step === 1) stepPattern(body, documents);
      else if (wiz.step === 2) stepSyllabus(body);
      else await stepReview(body);
    }
    $('#back', main).onclick = () => { wiz.step = Math.max(0, wiz.step - 1); draw(); };
    $('#next', main).onclick = () => { const err = validate(wiz.step); if (err) return toast(err); wiz.step += 1; draw(); };
    $('#reset', main).onclick = async () => { if (await EF.confirm('Discard everything you entered?', 'Discard')) { wiz = blank(); pick(EF.state.meta.patterns[0].id); draw(); } };
    draw();
  };
})();
