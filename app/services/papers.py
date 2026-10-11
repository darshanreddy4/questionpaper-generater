from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app import languages
from app.exam.extract_engine import ExtractEngine
from app.exam.generator import BankItem, PaperGenerator, resolve_topics
from app.exam.hybrid_engine import CodeOnlyEngine, HybridEngine
from app.exam.llm_engine import LLMEngine, SlotJob
from app.exam.normalize import question_text_blob
from app.exam.offline_engine import OfflineEngine
from app.exam.quality import text_hash
from app.exam.schema import ExamPattern, GenerationRequest, PaperContent, Question
from app.ingestion.question_extract import extract_from_chunks
from app.ingestion.retrieval import Retriever
from app.llm.usage import UsageMeter
from app.models import AuditLog, BankQuestion, Paper, User
from app.services.ai_settings import ResolvedAI, resolve_ai
from app.services.documents import document_chapters, load_chunks
from app.services.usage import record_usage
from app.state import AppState

log = logging.getLogger("examforge.papers")


def make_engine(state: AppState, seed: Optional[int], mode: str = "auto", resolved: Optional[ResolvedAI] = None,
                extracted_items: Optional[List[Dict[str, Any]]] = None):
    """Pick the writer: code only (no AI), AI for everything, the source document's own questions verbatim, or
    the automatic bank -> code -> AI routing."""
    if mode == "extract_source":
        return ExtractEngine(extracted_items or [])
    provider = resolved.provider if resolved else state.provider
    if provider.is_offline:
        return OfflineEngine(seed or 0)
    if mode == "code_only":
        return CodeOnlyEngine(OfflineEngine(seed or 0))
    model = LLMEngine(provider)
    if mode == "ai_all" or not state.settings.hybrid_writer:
        return model
    return HybridEngine(model, OfflineEngine(seed or 0))


def _bank_language(query, language: str):
    """A bank only serves papers in the language its questions were written in."""
    key = languages.key(language)
    return query.filter(BankQuestion.language.in_(["en", ""]) if key == "en" else BankQuestion.language == key)


def _bank_owner_ids(state: AppState, owner_id: str) -> List[str]:
    """Whose questions count as 'the bank' for this paper's owner: their whole school's when schools share work."""
    if state.settings.tenancy != "multi":
        return [owner_id]
    with state.db.session() as db:
        owner = db.get(User, owner_id)
        return [r[0] for r in db.query(User.id).filter(User.org_id == owner.org_id)] if owner else [owner_id]


def _bank_texts(state: AppState, owner_id: str, subject: str, language: str = "English", limit: int = 300) -> List[str]:
    with state.db.session() as db:
        rows = (_bank_language(db.query(BankQuestion).filter(BankQuestion.owner_id.in_(_bank_owner_ids(state, owner_id)), BankQuestion.subject == subject), language)
                .order_by(BankQuestion.created_at.desc()).limit(limit).all())
        return [r.content.get("text_blob", "") for r in reversed(rows) if r.content.get("text_blob")]


def _bank_items(state: AppState, owner_id: str, subject: str, language: str = "English", limit: int = 800) -> List[BankItem]:
    items: List[BankItem] = []
    with state.db.session() as db:
        rows = (_bank_language(db.query(BankQuestion).filter(BankQuestion.owner_id.in_(_bank_owner_ids(state, owner_id)), BankQuestion.subject == subject), language)
                .order_by(BankQuestion.created_at.desc()).limit(limit).all())
        for r in rows:
            try:
                q = Question.model_validate(r.content)
            except Exception:
                continue
            if q.verified == "flagged":
                continue
            items.append(BankItem(r.id, r.topic, r.q_type, float(r.marks), r.difficulty, q, r.use_count or 0))
    return items


def _update(state: AppState, paper_id: str, **fields: Any) -> None:
    with state.db.session() as db:
        paper = db.get(Paper, paper_id)
        if paper is not None:
            for k, v in fields.items():
                setattr(paper, k, v)


def _build_generator(state: AppState, paper_id: str, owner_id: str, req: GenerationRequest,
                     meter: Optional[UsageMeter] = None, resolved: Optional[ResolvedAI] = None) -> PaperGenerator:
    chunks = load_chunks(state, req.document_ids)
    retriever = Retriever(chunks) if chunks else None
    topics = resolve_topics(req, document_chapters(state, req.document_ids))
    bank = _bank_texts(state, owner_id, req.subject, req.language) if req.avoid_repeats else []
    items = _bank_items(state, owner_id, req.subject, req.language) if req.reuse_bank else []
    extracted = extract_from_chunks(chunks) if req.writer_mode == "extract_source" else None
    return PaperGenerator(
        make_engine(state, req.seed, req.writer_mode, resolved, extracted), req, req.pattern, retriever, bank, topics,
        workers=state.settings.llm_max_concurrency, meter=meter, bank_items=items,
        progress=lambda pct, msg: _update(state, paper_id, status="generating", progress=pct, message=msg))


def run_generation(state: AppState, paper_id: str) -> None:
    """Background job: write the paper. Never raises."""
    try:
        with state.db.session() as db:
            paper = db.get(Paper, paper_id)
            if paper is None:
                return
            req = GenerationRequest.model_validate(paper.request)
            owner_id = paper.owner_id
        _update(state, paper_id, status="generating", progress=2, message="Preparing source material")
        meter = UsageMeter()
        resolved = resolve_ai(state, owner_id)
        gen = _build_generator(state, paper_id, owner_id, req, meter, resolved)
        content, quality, slots = gen.run()
        quality["engine"] = {"provider": resolved.provider.name, "model": resolved.provider.model,
                             "offline": resolved.provider.is_offline, "source": resolved.source,
                             "billing": resolved.billing, "writer_mode": req.writer_mode}
        quality["usage"] = meter.to_dict()
        record_usage(state, owner_id, "paper", paper_id, meter, resolved)
        _update(state, paper_id, status="ready", progress=100, message="", content=content.model_dump(),
                quality=quality, title=content.meta.title[:300])
        _store_bank(state, owner_id, paper_id, req.subject, content, languages.key(req.language))
        _bump_reuse(state, gen.reused)
        with state.db.session() as db:
            db.add(AuditLog(user_id=owner_id, action="paper.generated", entity="paper", entity_id=paper_id,
                            detail=f"score={quality['score']}"))
    except Exception as exc:
        log.exception("paper generation failed")
        _update(state, paper_id, status="failed", message=f"Generation failed: {exc}"[:1000])


def _store_bank(state: AppState, owner_id: str, paper_id: str, subject: str, content: PaperContent,
                language: str = "en") -> None:
    with state.db.session() as db:
        for sec in content.sections:
            for q in sec.questions:
                if q.text.startswith("[This question could not be generated"):
                    continue
                if q.origin == "bank" or q.verified == "flagged":  # already stored, or not trustworthy enough to reuse
                    continue
                blob = question_text_blob(q)
                data = q.model_dump()
                data["text_blob"] = blob
                db.add(BankQuestion(owner_id=owner_id, paper_id=paper_id, subject=subject, topic=q.topic,
                                    q_type=q.type, difficulty=q.difficulty, marks=int(round(q.marks)),
                                    text_hash=text_hash(blob), language=language, content=data))


def _bump_reuse(state: AppState, ids: List[str]) -> None:
    if not ids:
        return
    with state.db.session() as db:
        db.query(BankQuestion).filter(BankQuestion.id.in_(ids)).update(
            {BankQuestion.use_count: BankQuestion.use_count + 1}, synchronize_session=False)


def regenerate_question(state: AppState, paper: Paper, qid: str, instruction: str) -> Dict[str, Any]:
    req = GenerationRequest.model_validate(paper.request)
    content = PaperContent.model_validate(paper.content)
    meter = UsageMeter()
    resolved = resolve_ai(state, paper.owner_id)
    gen = _build_generator(state, paper.id, paper.owner_id, req, meter, resolved)
    try:
        return gen.regenerate(content, qid, instruction).model_dump()
    finally:
        record_usage(state, paper.owner_id, "regenerate", paper.id, meter, resolved)


def _estimate_extracted(slots, items: List[Dict[str, Any]]) -> int:
    """How many slots ExtractEngine could actually fill, without touching the real pool."""
    jobs = [SlotJob(slot=s) for s in slots]
    return len(ExtractEngine(list(items)).write(None, jobs, {}))


def estimate_writers(state: AppState, user_id: str, req: GenerationRequest, pattern: ExamPattern, slots, topics) -> Dict[str, Any]:
    """Preview of who will write each question, so cost is visible before anything is generated."""
    from app.exam.blueprint import is_math_subject
    from app.exam.generator import find_bank_matches
    from app.exam.offline_engine import math_code_ok, text_code_ok

    resolved = resolve_ai(state, user_id)
    bank = {}
    if req.reuse_bank:
        bank = find_bank_matches(slots, _bank_items(state, user_id, req.subject, req.language), req.seed if req.seed is not None else 7)
    rest = [s for s in slots if s.index not in bank]
    if req.writer_mode == "extract_source":
        chunks = load_chunks(state, req.document_ids)
        extracted = _estimate_extracted(rest, extract_from_chunks(chunks))
        return {
            "mode": req.writer_mode, "questions": len(slots), "bank": len(bank), "code": 0, "ai": 0,
            "extracted": extracted, "unwritten": len(rest) - extracted,
            "ai_available": not resolved.provider.is_offline, "billing": "none", "source": resolved.source,
            "uses_tokens": False, "language": None,
        }
    code = 0
    if req.writer_mode != "ai_all" and languages.is_english(req.language):  # code writes English wording only
        if is_math_subject(req.subject):
            code = sum(1 for s in rest if math_code_ok(s))
            if resolved.provider.is_offline and req.document_ids:  # the rest come from the material's sentences
                chunks = load_chunks(state, req.document_ids)
                gen = PaperGenerator(OfflineEngine(0), req, pattern, Retriever(chunks) if chunks else None, (), topics)
                code += sum(1 for s in rest if not math_code_ok(s) and text_code_ok(s, gen._sources_for(s)))
        else:
            chunks = load_chunks(state, req.document_ids)
            gen = PaperGenerator(OfflineEngine(0), req, pattern, Retriever(chunks) if chunks else None, (), topics)
            code = sum(1 for s in rest if text_code_ok(s, gen._sources_for(s)))
    remaining = len(rest) - code
    ai_possible = not resolved.provider.is_offline and req.writer_mode != "code_only"
    return {
        "mode": req.writer_mode, "questions": len(slots), "bank": len(bank), "code": code,
        "ai": remaining if ai_possible else 0, "unwritten": 0 if ai_possible else remaining,
        "ai_available": not resolved.provider.is_offline, "billing": resolved.billing, "source": resolved.source,
        "uses_tokens": ai_possible and remaining > 0,
        "language": _language_note(req.language, resolved.provider.is_offline or req.writer_mode == "code_only"),
    }


def source_coverage(state: AppState, req: GenerationRequest, topics) -> Dict[str, Any]:
    """Topics that no passage in the chosen documents matches, usually because the topic is typed in a different
    language from the material (an English syllabus over Kannada notes)."""
    if not req.document_ids:
        return {"unmatched_topics": []}
    chunks = [c for c in load_chunks(state, req.document_ids) if c.kind == "text"]
    if not chunks:
        return {"unmatched_topics": []}
    retriever = Retriever(chunks)
    names = [t.name for t in topics]
    return {"unmatched_topics": [n for n in names if not retriever.search(n, k=1, chapter=n)]}


def _language_note(language: str, cannot_write: bool) -> Dict[str, Any]:
    lang = languages.resolve(language)
    return {"name": lang.name if lang else language, "english": languages.is_english(language),
            "needs_ai": not languages.is_english(language), "will_be_english": cannot_write and not languages.is_english(language)}
