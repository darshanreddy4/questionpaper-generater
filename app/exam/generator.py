from __future__ import annotations

import logging
import random
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from app import languages
from app.exam.blueprint import build_blueprint, parse_syllabus_text
from app.exam.llm_engine import Engine, GenContext, SlotJob, Source
from app.exam.normalize import QuestionInvalid, VerificationFailed, normalise_question, question_text_blob
from app.exam.quality import analyse, jaccard, text_hash, text_tokens
from app.exam.verify import verify_question
from app.exam.schema import (
    Answer, ExamPattern, GenerationRequest, PaperContent, PaperMeta, PaperSection, Question, Slot, TopicSpec,
)
from app.ingestion.retrieval import Retriever, tokenize
from app.llm.base import LLMError
from app.llm.usage import UsageMeter

log = logging.getLogger("examforge.generator")

_BATCH_BY_TYPE = {"long": 4, "case_study": 3, "numerical": 5, "short": 6}


def _batch_size(qtype: str, diagrams: bool, indic: bool = False) -> int:
    base = _BATCH_BY_TYPE.get(qtype, 8)
    size = max(2, base - 2) if diagrams else base
    return max(2, size // 2) if indic else size  # Indic text costs several times the output tokens per question


def language_problem(q: Question, language: str) -> Optional[str]:
    """A question the model wrote in English when an Indian language was asked for."""
    lang = languages.resolve(language)
    if lang is None or lang.script == "latin":
        return None
    prose = re.sub(r"\$[^$]*\$", " ", question_text_blob(q))
    share = languages.indic_share(prose)
    if share is not None and share < 0.5:
        return (f"Write the question, options and answer in {lang.name} ({lang.native}) using its own script, "
                "not in English or in Latin transliteration.")
    return None


@dataclass
class BankItem:
    """An approved question from the user's bank that can fill a matching slot without any model call."""

    id: str
    topic: str
    q_type: str
    marks: float
    difficulty: str
    question: Question
    use_count: int = 0


def _topic_match(a: str, b: str) -> bool:
    a, b = a.lower().strip(), b.lower().strip()
    if a == b or (a and b and (a in b or b in a)):
        return True
    ta, tb = set(tokenize(a)), set(tokenize(b))
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.5


def find_bank_matches(slots: List[Slot], items: Sequence[BankItem], seed: int) -> Dict[int, BankItem]:
    """Which slots a bank could fill (same type, marks, difficulty and topic). Used for the cost preview."""
    rng = random.Random(seed)
    taken: Set[str] = set()
    out: Dict[int, BankItem] = {}
    for slot in slots:
        cands = [it for it in items
                 if it.id not in taken and it.q_type == slot.question_type and abs(it.marks - slot.marks) < 1e-6
                 and it.difficulty == slot.difficulty and _topic_match(it.topic, slot.topic)
                 and (it.question.alternative is not None or not slot.internal_choice)
                 and (it.question.diagram is not None or not slot.wants_diagram)]
        if cands:
            it = rng.choice(cands)
            taken.add(it.id)
            out[slot.index] = it
    return out


class PaperGenerator:
    def __init__(self, engine: Engine, request: GenerationRequest, pattern: ExamPattern,
                 retriever: Optional[Retriever] = None, bank_texts: Sequence[str] = (),
                 topics: Optional[List[TopicSpec]] = None, workers: int = 4,
                 progress: Callable[[int, str], None] = lambda p, m: None, meter: Optional[UsageMeter] = None,
                 bank_items: Sequence[BankItem] = ()):
        self.engine, self.request, self.pattern = engine, request, pattern
        self._bank_items = list(bank_items) if request.reuse_bank else []
        self.reused: List[str] = []  # ids of bank questions placed in this paper
        self.meter = meter
        self.retriever = retriever
        self.topics = topics if topics is not None else request.topics
        self.workers = workers
        self.progress = progress
        self.bank_texts = list(bank_texts)
        self.bank_hashes: Set[str] = {text_hash(t) for t in self.bank_texts}
        self.similarity_limit = getattr(engine, "similarity_limit", 0.72)
        self._bank_tokens = [text_tokens(t) for t in self.bank_texts[-400:]]
        self._lock = threading.Lock()
        lang = languages.resolve(request.language)
        self._indic = lang is not None and lang.script != "latin"
        self._used: Counter = Counter()
        self._accepted: Dict[int, Set[str]] = {}

    # ------------------------------------------------------------------ retrieval

    def _sources_for(self, slot: Slot) -> List[Source]:
        if not self.retriever:
            return []
        query = f"{slot.topic} {self.request.subject}"
        out: List[Source] = []
        with self._lock:
            hits = self.retriever.search(query, k=2, chapter=slot.topic, kinds=["text"], used=self._used)
            if slot.wants_diagram or self.request.diagram_mode != "none":
                hits += self.retriever.search(query, k=1, kinds=["figure"], used=self._used)
            for _, c in hits:
                self._used[c.id] += 1
                out.append(Source(id=c.id[:8], kind=c.kind, chapter=c.chapter, text=c.text,
                                  diagram=(c.meta or {}).get("diagram")))
        return out

    # ------------------------------------------------------------------ one batch

    def _check_and_accept(self, slot: Slot, q: Question, from_bank: bool = False) -> Optional[str]:
        """Atomically reject a near-duplicate, otherwise record the question as part of the paper."""
        toks = text_tokens(question_text_blob(q))
        # Templated wording is shared by design (only the numbers change), so code-written items get a looser bar.
        limit = 0.9 if q.origin == "code" else self.similarity_limit
        with self._lock:
            if not from_bank:
                if text_hash(question_text_blob(q)) in self.bank_hashes:
                    return "This question already appears in an earlier paper; write a different one."
            for idx, other in self._accepted.items():
                if idx != slot.index and jaccard(toks, other) >= limit:
                    return "Too similar to another question in this paper; write a different one."
            if not from_bank and any(jaccard(toks, b) >= limit + 0.1 for b in self._bank_tokens):
                return "Too similar to a question from an earlier paper; write a different one."
            self._accepted[slot.index] = toks
        return None

    def _write_jobs(self, jobs: List[SlotJob], instruction: str = "") -> Tuple[Dict[int, Question], Dict[int, str]]:
        # Replaying cached replies is only right for a fresh paper with a fixed seed, never for "regenerate".
        request = self.request
        if instruction:
            request = request.model_copy(update={
                "extra_instructions": (request.extra_instructions + "\n" + instruction).strip()})
        ctx = GenContext(request, self.pattern, avoid=self._avoid_list(), meter=self.meter,
                         cacheable=self.request.seed is not None and not instruction, prefer_model=bool(instruction))
        done: Dict[int, Question] = {}
        errors: Dict[int, str] = {}
        flagged: Dict[int, Question] = {}  # last candidate whose answer key failed a computer check
        pending = list(jobs)
        for attempt in range(self.engine.max_attempts):
            if not pending:
                break
            try:
                raw = self.engine.write(ctx, pending, dict(errors))
            except LLMError as exc:
                log.warning("engine error (attempt %d): %s", attempt + 1, exc)
                for j in pending:
                    errors[j.slot.index] = f"model call failed: {exc}"
                time.sleep(min(2 ** attempt, 8))
                continue
            except Exception as exc:  # never lose the whole paper to one bad batch
                log.exception("unexpected engine failure")
                for j in pending:
                    errors[j.slot.index] = f"unexpected error: {exc}"
                continue
            still: List[SlotJob] = []
            for job in pending:
                item = raw.get(job.slot.index)
                if item is None:
                    errors[job.slot.index] = getattr(self.engine, "missing_reason", "No question was returned for this slot.")
                    still.append(job)
                    continue
                try:
                    q = normalise_question(item, job.slot, f"q{job.slot.index + 1}")
                    q.origin = q.origin or "model"
                    if q.origin == "code":
                        q.verified = q.verified or "construction"
                    else:
                        check = verify_question(q)
                        q.verified = check.status
                        if check.issues:
                            flagged[job.slot.index] = q
                            raise VerificationFailed(
                                "A computer check contradicts your answer key: " + " ".join(check.issues[:3])
                                + " Recompute carefully and correct the question or its answer.")
                    if q.origin != "code" and getattr(self.engine, "multilingual", True):
                        wrong = language_problem(q, self.request.language)
                        if wrong:
                            raise QuestionInvalid(wrong)
                    reason = self._check_and_accept(job.slot, q)
                    if reason:
                        raise QuestionInvalid(reason)
                except QuestionInvalid as exc:
                    errors[job.slot.index] = str(exc)
                    still.append(job)
                    continue
                except Exception as exc:
                    errors[job.slot.index] = f"Malformed question: {exc}"
                    still.append(job)
                    continue
                done[job.slot.index] = q
                errors.pop(job.slot.index, None)
            pending = still
        # An answer key that still fails the check after every retry is kept, but flagged for the teacher.
        for job in list(pending):
            idx = job.slot.index
            if idx in flagged and errors.get(idx, "").startswith("A computer check contradicts"):
                if self._check_and_accept(job.slot, flagged[idx]) is None:
                    done[idx] = flagged[idx]
                    pending.remove(job)
        return done, {j.slot.index: errors.get(j.slot.index, "unknown error") for j in pending}

    def _avoid_list(self) -> List[str]:
        return self.bank_texts[-25:]

    # ------------------------------------------------------------------ whole paper

    def run(self) -> Tuple[PaperContent, Dict[str, Any], List[Slot]]:
        slots = build_blueprint(self.request, self.pattern, self.topics)
        reused = self._reuse_from_bank(slots)
        todo = [s for s in slots if s.index not in reused]
        jobs = {s.index: SlotJob(s, self._sources_for(s)) for s in todo}
        batches: List[List[SlotJob]] = []
        for sec in self.pattern.sections:
            sec_jobs = [jobs[s.index] for s in todo if s.section_id == sec.id]
            if not sec_jobs:
                continue
            size = _batch_size(sec.question_type, any(j.slot.wants_diagram for j in sec_jobs), self._indic)
            for i in range(0, len(sec_jobs), size):
                batches.append(sec_jobs[i:i + size])

        results: Dict[int, Question] = dict(reused)
        failed: Dict[int, str] = {}
        completed = 0
        self.progress(5, f"Writing {len(todo)} questions in {len(batches)} batch(es); {len(reused)} reused from the bank")
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = [pool.submit(self._write_jobs, b) for b in batches]
            for fut in futures:
                done, bad = fut.result()
                results.update(done)
                failed.update(bad)
                completed += 1
                self.progress(5 + int(85 * completed / max(len(batches), 1)), f"Wrote batch {completed} of {len(batches)}")

        content = self._assemble(slots, results, failed)
        self.progress(95, "Checking quality")
        quality = analyse(content, self.pattern, self.request, slots, failed, self.bank_hashes, self.similarity_limit)
        quality["reuse"] = {"enabled": self.request.reuse_bank, "from_bank": len(reused), "questions": len(slots)}
        self._language_report(quality)
        self._builtin_report(quality, slots)
        return content, quality, slots

    def _builtin_report(self, quality: Dict[str, Any], slots: List[Slot]) -> None:
        """Without AI the built-in writer cannot write maths problems about an uploaded chapter; say what happened."""
        if not getattr(self.engine, "is_builtin", False) or self.retriever is None:
            return
        left_out = len(quality.get("failed") or [])
        note = ("The built-in writer (no AI connected) builds questions from sentences in your uploaded material"
                " and cannot write new problems or worked solutions from it.")
        if left_out:
            note += f" {left_out} question(s) it could not base on your material are marked \"could not be generated\"."
        note += " Add an AI key under AI & billing for a full paper on this material."
        quality["warnings"].append(note)
        quality["status"] = "review"

    def _language_report(self, quality: Dict[str, Any]) -> None:
        lang = languages.resolve(self.request.language)
        if languages.is_english(self.request.language):
            return
        name = lang.name if lang else self.request.language
        quality["language"] = {"requested": name, "writer_can_write_it": bool(getattr(self.engine, "multilingual", True))}
        if not getattr(self.engine, "multilingual", True):
            quality["warnings"].append(
                f"The built-in writer cannot write {name}, so questions are in English. Connect an AI key under "
                f"AI & billing to get a {name} paper.")
            quality["status"] = "review"

    def _reuse_from_bank(self, slots: List[Slot]) -> Dict[int, Question]:
        """Fill slots from approved bank questions of the same type, marks, difficulty and topic."""
        if not self._bank_items:
            return {}
        rng = random.Random(self.request.seed if self.request.seed is not None else 7)
        taken: Set[str] = set()
        out: Dict[int, Question] = {}
        for slot in slots:
            cands = [it for it in self._bank_items
                     if it.id not in taken and it.q_type == slot.question_type and abs(it.marks - slot.marks) < 1e-6
                     and it.difficulty == slot.difficulty and _topic_match(it.topic, slot.topic)
                     and (it.question.alternative is not None or not slot.internal_choice)
                     and (it.question.diagram is not None or not slot.wants_diagram)]
            rng.shuffle(cands)
            cands.sort(key=lambda it: it.use_count)  # spread reuse so the same items are not always picked
            for it in cands:
                q = it.question.model_copy(deep=True)
                q.id, q.marks, q.difficulty, q.bloom, q.topic = f"q{slot.index + 1}", slot.marks, slot.difficulty, slot.bloom, slot.topic
                q.origin, q.origin_ref = "bank", it.id
                if not slot.internal_choice:
                    q.alternative = None
                if self._check_and_accept(slot, q, from_bank=True) is None:
                    out[slot.index] = q
                    taken.add(it.id)
                    self.reused.append(it.id)
                    break
        return out

    def _localised(self, instructions: List[str], pat: ExamPattern) -> Dict[str, Any]:
        """The pattern's own wording (instructions, section titles) is English; translate it once when the paper is not."""
        out: Dict[str, Any] = {"instructions": instructions, "titles": {}, "section_instructions": {},
                               "exam_name": pat.name}
        if languages.is_english(self.request.language):
            return out
        slots: List[Tuple[str, str, str]] = [("i", str(i), t) for i, t in enumerate(instructions)]
        for sec in pat.sections:
            slots += [("t", sec.id, sec.title), ("s", sec.id, sec.instruction)]
        if not self.request.exam_name:
            slots.append(("n", "", pat.name))
        todo = [s for s in slots if s[2].strip() and not languages.has_indic(s[2])]
        translate = getattr(self.engine, "localise", None)
        if not todo or translate is None:
            return out
        done = translate([t for _, _, t in todo], self.request.language, self.meter)
        if not done:
            return out
        for (kind, ident, _), text in zip(todo, done):
            if kind == "i":
                instructions[int(ident)] = text
            elif kind == "t":
                out["titles"][ident] = text
            elif kind == "s":
                out["section_instructions"][ident] = text
            else:
                out["exam_name"] = text
        return out

    def _assemble(self, slots: List[Slot], results: Dict[int, Question], failed: Dict[int, str]) -> PaperContent:
        req, pat = self.request, self.pattern
        instructions = list(pat.instructions)
        if any(s.negative_marks for s in pat.sections):
            instructions.append("Negative marking applies where stated in the section heading.")
        names = self._localised(instructions, pat)
        instructions = names["instructions"]
        meta = PaperMeta(
            institution=req.institution, title=req.title or f"{req.subject} - {req.exam_name or pat.name}",
            exam_name=req.exam_name or names["exam_name"], subject=req.subject, class_level=req.class_level,
            board=req.board or pat.board, date=req.date, language=req.language,
            duration_minutes=req.duration_minutes or pat.duration_minutes, total_marks=pat.total_marks,
            instructions=instructions)
        sections: List[PaperSection] = []
        for sec in pat.sections:
            qs: List[Question] = []
            for s in (x for x in slots if x.section_id == sec.id):
                q = results.get(s.index)
                if q is None:
                    q = Question(
                        id=f"q{s.index + 1}", type=s.question_type, marks=s.marks, difficulty=s.difficulty,
                        bloom=s.bloom, topic=s.topic,
                        text=f"[This question could not be generated: {failed.get(s.index, 'unknown error')} "
                             "Use Regenerate to try again.]",
                        answer=Answer())
                qs.append(q)
            sections.append(PaperSection(
                id=sec.id, title=names["titles"].get(sec.id, sec.title),
                instruction=names["section_instructions"].get(sec.id, sec.instruction), question_type=sec.question_type,
                marks_each=sec.marks_each, count=sec.count, attempt=sec.attempt, negative_marks=sec.negative_marks,
                questions=qs))
        return PaperContent(meta=meta, sections=sections)

    # ------------------------------------------------------------------ single question

    def regenerate(self, content: PaperContent, qid: str, instruction: str = "") -> Question:
        slot = self._slot_from_question(content, qid)
        for sec in content.sections:
            for q in sec.questions:
                if q.id != qid:
                    self._accepted[int(q.id[1:]) - 1] = text_tokens(question_text_blob(q))
        # Writing a replacement must not be blocked by the question it replaces.
        self._accepted.pop(slot.index, None)
        job = SlotJob(slot, self._sources_for(slot))
        done, bad = self._write_jobs([job], instruction)
        if slot.index not in done:
            raise LLMError(bad.get(slot.index, "Could not regenerate this question."))
        return done[slot.index]

    @staticmethod
    def _slot_from_question(content: PaperContent, qid: str) -> Slot:
        for sec in content.sections:
            for q in sec.questions:
                if q.id == qid:
                    return Slot(index=int(qid[1:]) - 1, section_id=sec.id, question_type=q.type, marks=q.marks,
                                difficulty=q.difficulty, bloom=q.bloom, topic=q.topic,
                                wants_diagram=bool(q.diagram), internal_choice=q.alternative is not None)
        raise KeyError(qid)


def resolve_topics(req: GenerationRequest, chapter_titles: Sequence[str]) -> List[TopicSpec]:
    """Topic list precedence: explicit topics, then the pasted syllabus, then the document's own chapters."""
    if req.topics:
        return list(req.topics)
    if req.syllabus_text.strip():
        parsed = parse_syllabus_text(req.syllabus_text)
        if parsed:
            return parsed
    return [TopicSpec(name=t) for t in list(dict.fromkeys(chapter_titles))[:30]]
