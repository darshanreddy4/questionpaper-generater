from __future__ import annotations

import io
import re
from urllib.parse import quote
import zipfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import (
    audit, get_for_change, get_state, get_visible, names_of, school_shared, scope_to_visible,
)
from app.diagrams import DiagramError, check_diagram, render_png, render_svg
from app.exam.blueprint import blueprint_summary, build_blueprint
from app.exam.generator import resolve_topics
from app.exam.patterns import get_preset
from app.exam.schema import ExamPattern, GenerationRequest, PaperContent, Question
from app.exam.verify import verify_question
from app.export import build_docx, build_pdf
from app.export.common import SET_LABELS
from app.export.variants import make_variant
from app.models import Document, Paper, Pattern, User
from app.security import current_user
from app.services.documents import document_chapters
from app.services.papers import estimate_writers, source_coverage, regenerate_question
from app.services.ai_settings import resolve_ai
from app.services.usage import enforce_budget

router = APIRouter(prefix="/api", tags=["papers"])

_MIME = {"pdf": "application/pdf",
         "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
_MODES = ("question", "answer", "combined")


# ------------------------------------------------------------------ helpers

def _resolve_pattern(state, user: User, req: GenerationRequest) -> ExamPattern:
    if req.pattern is not None:
        return req.pattern
    if not req.pattern_id:
        raise HTTPException(422, "Choose an exam pattern (pattern_id) or provide a custom pattern.")
    try:
        return get_preset(req.pattern_id)
    except KeyError:
        pass
    with state.db.session() as db:
        try:
            row = get_visible(db, Pattern, req.pattern_id, user, school_shared(state))
        except HTTPException:
            raise HTTPException(404, "Pattern not found.")
        spec = dict(row.spec)
        spec.setdefault("id", row.id)
        return ExamPattern.model_validate(spec)


def _check_documents(state, user: User, ids: List[str]) -> None:
    if not ids:
        return
    with state.db.session() as db:
        for doc_id in ids:
            doc = get_visible(db, Document, doc_id, user, school_shared(state))
            if doc.status != "ready":
                raise HTTPException(409, f"'{doc.filename}' is not ready yet (status: {doc.status}).")


def _brief(p: Paper, by: str = "", can_edit: bool = True) -> Dict[str, Any]:
    return {"created_by": by, "can_edit": can_edit, "id": p.id, "title": p.title or p.subject, "subject": p.subject, "status": p.status,
            "progress": p.progress, "message": p.message, "revision": p.revision,
            "score": (p.quality or {}).get("score"), "created_at": p.created_at.isoformat() + "Z",
            "updated_at": p.updated_at.isoformat() + "Z"}


def _full(p: Paper, by: str = "", can_edit: bool = True) -> Dict[str, Any]:
    out = _brief(p, by, can_edit)
    out.update(content=p.content or None, quality=p.quality or None, request=p.request)
    return out


def _recompute_total(content: PaperContent) -> float:
    total = 0.0
    for sec in content.sections:
        marks = sorted((q.marks for q in sec.questions), reverse=True)
        total += sum(marks[: sec.effective_attempt()])
    return total


def _validate_content(content: PaperContent) -> None:
    seen = set()
    for sec in content.sections:
        for q in sec.questions:
            if not re.fullmatch(r"q\d+", q.id) or q.id in seen:
                raise HTTPException(422, f"Invalid or duplicate question id '{q.id}'.")
            seen.add(q.id)
            specs = [("question", q.diagram), ("answer", q.answer.diagram)]
            if q.alternative:
                specs += [("alternative", q.alternative.diagram), ("alternative answer", q.alternative.answer.diagram)]
            for label, spec in specs:
                if spec:
                    try:
                        check_diagram(spec)
                    except DiagramError as exc:
                        raise HTTPException(422, f"{q.id} {label} diagram: {exc}")


def _reverify(content: PaperContent) -> None:
    """A teacher's edit can fix or break an answer key, so the machine check is run again on save."""
    for sec in content.sections:
        for q in sec.questions:
            if q.origin == "code":
                continue
            status = verify_question(q).status
            if status:
                q.verified = status
            elif q.verified in ("checked", "flagged"):
                q.verified = ""


_NAME_JUNK = re.compile(r"[^\w.\-\u0900-\u0D7F]+")  # keep letters of any script and Indic vowel signs


def _base_name(title: str) -> str:
    return _NAME_JUNK.sub("_", re.sub("[\u200c\u200d]", "", title)).strip("_")[:60] or "paper"


def _attachment(name: str) -> str:
    """Content-Disposition with an ASCII fallback and the real (possibly Indic) name in RFC 5987 form."""
    ascii_name = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9._-]+", "_", name)).strip("_-.") or "paper"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


def _filename(title: str, mode: str, set_label: str, ext: str) -> str:
    base = _base_name(title)
    suffix = {"question": "question_paper", "answer": "answer_key", "combined": "teachers_copy"}[mode]
    return f"{base}_{suffix}{'_set' + set_label if set_label != 'A' else ''}.{ext}"


def _render(content: PaperContent, fmt: str, mode: str, set_label: str, seed: int, size: float) -> bytes:
    variant = make_variant(content, set_label, seed)
    return build_pdf(variant, mode, size) if fmt == "pdf" else build_docx(variant, mode, size)


# ------------------------------------------------------------------ blueprint & creation

@router.post("/papers/blueprint")
def preview_blueprint(req: GenerationRequest, request: Request, user: User = Depends(current_user)):
    state = get_state(request)
    pattern = _resolve_pattern(state, user, req)
    _check_documents(state, user, req.document_ids)
    topics = resolve_topics(req, document_chapters(state, req.document_ids) if req.document_ids else [])
    slots = build_blueprint(req, pattern, topics)
    return {"pattern": pattern.model_dump(), "total_marks": pattern.total_marks,
            "topics": [t.model_dump() for t in topics],
            "slots": [s.model_dump() for s in slots], "summary": blueprint_summary(slots, pattern),
            "writers": estimate_writers(state, user.id, req, pattern, slots, topics),
            "sources": source_coverage(state, req, topics)}


@router.post("/papers", status_code=202)
def create_paper(req: GenerationRequest, request: Request, user: User = Depends(current_user)):
    state = get_state(request)
    pattern = _resolve_pattern(state, user, req)
    _check_documents(state, user, req.document_ids)
    enforce_budget(state, user.id)
    if req.writer_mode == "extract_source" and not req.document_ids:
        raise HTTPException(422, "Choose at least one source document to copy its exact questions from.")
    no_ai = resolve_ai(state, user.id).provider.is_offline or req.writer_mode == "code_only"
    if no_ai and req.writer_mode != "extract_source" and not req.document_ids and not re.search(
            r"math|algebra|calculus|geometry|trigonometry|statistics", req.subject, re.I):
        raise HTTPException(422, "Without AI, ExamForge can only write from uploaded study material (or maths "
                                 "templates). Upload at least one document, or add an AI key under AI & billing.")
    req = req.model_copy(update={"pattern": pattern})
    with state.db.session() as db:
        paper = Paper(owner_id=user.id, subject=req.subject, status="queued", progress=0,
                      title=req.title or f"{req.subject} - {req.exam_name or pattern.name}",
                      request=req.model_dump(mode="json"))
        db.add(paper)
        db.flush()
        paper_id = paper.id
    state.jobs.wake()
    audit(state, request, user, "paper.create", "paper", paper_id, req.subject)
    return {"id": paper_id, "status": "queued"}


# ------------------------------------------------------------------ CRUD

@router.get("/papers")
def list_papers(request: Request, user: User = Depends(current_user)):
    state = get_state(request)
    with state.db.session() as db:
        q = scope_to_visible(db.query(Paper), Paper.owner_id, db, user, school_shared(state))
        rows = q.order_by(Paper.created_at.desc()).limit(200).all()
        names = names_of(db, [p.owner_id for p in rows])
        return {"papers": [_brief(p, names.get(p.owner_id, ""), p.owner_id == user.id or user.role == "admin") for p in rows]}


@router.get("/papers/{paper_id}")
def get_paper(paper_id: str, request: Request, user: User = Depends(current_user)):
    state = get_state(request)
    with state.db.session() as db:
        p = get_visible(db, Paper, paper_id, user, school_shared(state))
        return _full(p, names_of(db, [p.owner_id]).get(p.owner_id, ""), p.owner_id == user.id or user.role == "admin")


@router.put("/papers/{paper_id}")
def update_paper(paper_id: str, content: PaperContent, request: Request, user: User = Depends(current_user)):
    state = get_state(request)
    _validate_content(content)
    _reverify(content)
    content.meta.total_marks = _recompute_total(content)
    with state.db.session() as db:
        paper = get_for_change(db, Paper, paper_id, user, school_shared(state))
        if paper.status != "ready":
            raise HTTPException(409, "The paper is not ready for editing.")
        paper.content = content.model_dump()
        paper.title = content.meta.title[:300]
        paper.revision += 1
        result = _full(paper)
    audit(state, request, user, "paper.update", "paper", paper_id)
    return result


@router.delete("/papers/{paper_id}", status_code=204)
def delete_paper(paper_id: str, request: Request, user: User = Depends(current_user)):
    state = get_state(request)
    with state.db.session() as db:
        db.delete(get_for_change(db, Paper, paper_id, user, school_shared(state)))
    audit(state, request, user, "paper.delete", "paper", paper_id)


class RegenerateBody(BaseModel):
    instruction: str = Field(default="", max_length=500)


@router.post("/papers/{paper_id}/questions/{qid}/regenerate")
def regenerate(paper_id: str, qid: str, body: RegenerateBody, request: Request, user: User = Depends(current_user)):
    state = get_state(request)
    enforce_budget(state, user.id)
    with state.db.session() as db:
        paper = get_for_change(db, Paper, paper_id, user, school_shared(state))
        if paper.status != "ready":
            raise HTTPException(409, "The paper is not ready for editing.")
        snapshot = Paper(id=paper.id, owner_id=paper.owner_id, request=paper.request, content=paper.content)
    try:
        new_q = regenerate_question(state, snapshot, qid, body.instruction)
    except KeyError:
        raise HTTPException(404, "Question not found.")
    except Exception as exc:
        raise HTTPException(502, f"Could not regenerate: {exc}")
    with state.db.session() as db:
        paper = get_for_change(db, Paper, paper_id, user, school_shared(state))
        content = PaperContent.model_validate(paper.content)
        for sec in content.sections:
            sec.questions = [Question.model_validate(new_q) if q.id == qid else q for q in sec.questions]
        paper.content = content.model_dump()
        paper.revision += 1
    audit(state, request, user, "paper.regenerate", "paper", paper_id, qid)
    return {"question": new_q}


# ------------------------------------------------------------------ export

@router.get("/papers/{paper_id}/export")
def export_paper(paper_id: str, request: Request, fmt: str = Query("pdf", pattern="^(pdf|docx)$", alias="format"),
                 mode: str = Query("question"), set_label: str = Query("A", alias="set"),
                 font_size: float = Query(11.0, ge=9, le=14), user: User = Depends(current_user)):
    state = get_state(request)
    if mode not in _MODES:
        raise HTTPException(422, f"mode must be one of {', '.join(_MODES)}.")
    if set_label not in SET_LABELS:
        raise HTTPException(422, "set must be A, B, C or D.")
    ok, retry = state.export_limiter.check(user.id)
    if not ok:
        raise HTTPException(429, f"Too many exports. Try again in {retry} seconds.")
    with state.db.session() as db:
        paper = get_visible(db, Paper, paper_id, user, school_shared(state))
        if paper.status != "ready":
            raise HTTPException(409, "The paper is not ready yet.")
        content = PaperContent.model_validate(paper.content)
        seed = (paper.request or {}).get("seed") or 0
    try:
        data = _render(content, fmt, mode, set_label, seed, font_size)
    except Exception as exc:
        raise HTTPException(500, f"Could not build the document: {exc.__class__.__name__}")
    audit(state, request, user, "paper.export", "paper", paper_id, f"{fmt}/{mode}/{set_label}")
    name = _filename(content.meta.title, mode, set_label, fmt)
    return Response(data, media_type=_MIME[fmt], headers={"Content-Disposition": _attachment(name)})


@router.get("/papers/{paper_id}/export-all")
def export_all(paper_id: str, request: Request, formats: str = Query("pdf,docx"),
               sets: int = Query(1, ge=1, le=4), modes: str = Query("question,answer"),
               user: User = Depends(current_user)):
    state = get_state(request)
    fmts = [f for f in formats.split(",") if f in _MIME]
    mode_list = [m for m in modes.split(",") if m in _MODES]
    if not fmts or not mode_list:
        raise HTTPException(422, "Choose at least one valid format and mode.")
    ok, retry = state.export_limiter.check(user.id)
    if not ok:
        raise HTTPException(429, f"Too many exports. Try again in {retry} seconds.")
    with state.db.session() as db:
        paper = get_visible(db, Paper, paper_id, user, school_shared(state))
        if paper.status != "ready":
            raise HTTPException(409, "The paper is not ready yet.")
        content = PaperContent.model_validate(paper.content)
        seed = (paper.request or {}).get("seed") or 0
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for label in SET_LABELS[:sets]:
            for mode in mode_list:
                for fmt in fmts:
                    zf.writestr(_filename(content.meta.title, mode, label, fmt),
                                _render(content, fmt, mode, label, seed, 11.0))
    audit(state, request, user, "paper.export_all", "paper", paper_id)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": _attachment(f"{_base_name(content.meta.title)}_all.zip")})


# ------------------------------------------------------------------ diagrams

class DiagramBody(BaseModel):
    spec: Dict[str, Any]
    format: str = Field(default="svg", pattern="^(svg|png)$")


@router.post("/diagrams/render")
def render_diagram(body: DiagramBody, request: Request, user: User = Depends(current_user)):
    state = get_state(request)
    ok, retry = state.render_limiter.check(user.id)
    if not ok:
        raise HTTPException(429, f"Too many requests. Try again in {retry} seconds.")
    try:
        if body.format == "png":
            return Response(render_png(body.spec, dpi=150).data, media_type="image/png")
        return Response(render_svg(body.spec), media_type="image/svg+xml")
    except DiagramError as exc:
        raise HTTPException(422, str(exc))
