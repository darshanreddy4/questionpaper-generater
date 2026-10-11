"""Platform library: the operator publishes source files and questions; any school or teacher can copy them into
its own library. A copy is independent, so unpublishing or deleting the original never changes what a school added."""
from __future__ import annotations

import datetime as dt
import shutil
import uuid
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import audit, get_state, school_user_ids
from app.models import BankQuestion, Chunk, Document, User
from app.security import current_user, require_superadmin
from app.state import AppState

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

_MAX_BULK = 2000


def _live(state: AppState) -> None:
    if state.settings.tenancy != "multi":
        raise HTTPException(404, "Not found.")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class PublishBody(BaseModel):
    kind: Literal["document", "question"]
    ids: List[str] = Field(min_length=1, max_length=500)
    published: bool = True


@router.post("/publish")
def publish(body: PublishBody, request: Request, op: User = Depends(require_superadmin)):
    state = get_state(request)
    _live(state)
    model = Document if body.kind == "document" else BankQuestion
    ids = set(body.ids)
    with state.db.session() as db:
        # only the operator's own uploads can be published
        rows = db.query(model).filter(model.id.in_(ids), model.owner_id.in_(school_user_ids(db, op))).all()
        if len(rows) != len(ids):
            raise HTTPException(404, "Some of these items were not found in your library.")
        if body.published and body.kind == "document" and any(r.status != "ready" for r in rows):
            raise HTTPException(422, "Only files that have finished processing can be published.")
        for r in rows:
            if body.published and not r.published:
                r.published_at = _now()
            r.published = body.published
    audit(state, request, op, "catalog.publish" if body.published else "catalog.unpublish", body.kind, "", f"{len(ids)} item(s)")
    return {"updated": len(ids), "published": body.published}


# ------------------------------------------------------------------ source files

def _added_sources(db, user: User) -> set:
    rows = db.query(Document.stats).filter(Document.owner_id.in_(school_user_ids(db, user))).all()
    return {(s or {}).get("from_catalog") for (s,) in rows} - {None}


@router.get("/documents")
def list_documents(request: Request, user: User = Depends(current_user)):
    state = get_state(request)
    _live(state)
    with state.db.session() as db:
        have = _added_sources(db, user)
        rows = (db.query(Document).filter(Document.published.is_(True), Document.status == "ready")
                .order_by(Document.published_at.desc()).limit(500).all())
        return {"documents": [{
            "id": d.id, "filename": d.filename, "format": d.format,
            "words": (d.stats or {}).get("words", 0), "figures": (d.stats or {}).get("figures", 0),
            "chapters": [c if isinstance(c, str) else c.get("title", "") for c in (d.stats or {}).get("chapters", [])][:40],
            "published_at": (d.published_at or d.created_at).isoformat() + "Z", "added": d.id in have} for d in rows]}


@router.post("/documents/{doc_id}/add", status_code=201)
def add_document(doc_id: str, request: Request, user: User = Depends(current_user)):
    state = get_state(request)
    _live(state)
    new_id = uuid.uuid4().hex
    dst = state.settings.upload_dir / new_id
    try:
        with state.db.session() as db:
            src = db.get(Document, doc_id)
            if src is None or not src.published or src.status != "ready":
                raise HTTPException(404, "This file is no longer in the platform library.")
            if doc_id in _added_sources(db, user):
                raise HTTPException(409, "This file is already in your library.")
            src_dir = Path(src.stored_path).parent
            root = state.settings.upload_dir.resolve()
            if not src.stored_path or root not in src_dir.resolve().parents or not src_dir.is_dir():
                raise HTTPException(410, "The original file is no longer available.")
            shutil.copytree(src_dir, dst)
            stats = dict(src.stats or {}, from_catalog=src.id)
            copy = Document(id=new_id, owner_id=user.id, filename=src.filename, format=src.format, status="ready",
                            stats=stats, stored_path=str(dst / Path(src.stored_path).name))
            db.add(copy)
            for c in db.query(Chunk).filter(Chunk.document_id == src.id).order_by(Chunk.idx).all():
                meta = dict(c.meta or {})
                if meta.get("image_path"):
                    meta["image_path"] = str(dst / Path(meta["image_path"]).relative_to(src_dir))
                db.add(Chunk(document_id=new_id, idx=c.idx, kind=c.kind, chapter=c.chapter, text=c.text, meta=meta))
            name = src.filename
    except Exception:
        shutil.rmtree(dst, ignore_errors=True)
        raise
    audit(state, request, user, "catalog.add", "document", new_id, name)
    return {"id": new_id, "filename": name}


# ------------------------------------------------------------------ questions

def _published_questions(db, subject, q_type, difficulty, topic):
    q = db.query(BankQuestion).filter(BankQuestion.published.is_(True))
    if subject:
        q = q.filter(BankQuestion.subject == subject)
    if q_type:
        q = q.filter(BankQuestion.q_type == q_type)
    if difficulty:
        q = q.filter(BankQuestion.difficulty == difficulty)
    if topic:
        q = q.filter(BankQuestion.topic.ilike(f"%{topic}%"))
    return q


def _have_hashes(db, user: User) -> set:
    return {h for (h,) in db.query(BankQuestion.text_hash).filter(BankQuestion.owner_id.in_(school_user_ids(db, user))).all()}


@router.get("/questions")
def list_questions(request: Request, subject: Optional[str] = None, q_type: Optional[str] = None,
                   difficulty: Optional[str] = None, topic: Optional[str] = None,
                   limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                   user: User = Depends(current_user)):
    state = get_state(request)
    _live(state)
    with state.db.session() as db:
        from sqlalchemy import func
        subjects = [{"subject": s, "count": n} for s, n in
                    db.query(BankQuestion.subject, func.count(BankQuestion.id)).filter(BankQuestion.published.is_(True))
                    .group_by(BankQuestion.subject).order_by(BankQuestion.subject).all()]
        q = _published_questions(db, subject, q_type, difficulty, topic)
        total = q.count()
        have = _have_hashes(db, user)
        rows = q.order_by(BankQuestion.published_at.desc()).offset(offset).limit(limit).all()
        items = [{"id": r.id, "subject": r.subject, "topic": r.topic, "type": r.q_type, "difficulty": r.difficulty,
                  "marks": r.marks, "language": r.language,
                  "question": {k: v for k, v in r.content.items() if k != "text_blob"},
                  "added": r.text_hash in have} for r in rows]
        return {"total": total, "items": items, "subjects": subjects}


class AddQuestions(BaseModel):
    ids: List[str] = Field(default_factory=list, max_length=500)
    subject: str = ""
    q_type: str = ""
    difficulty: str = ""
    topic: str = ""


@router.post("/questions/add")
def add_questions(body: AddQuestions, request: Request, user: User = Depends(current_user)):
    """Copy the chosen published questions (or everything matching the filters) into the caller's school bank."""
    state = get_state(request)
    _live(state)
    with state.db.session() as db:
        q = _published_questions(db, body.subject, body.q_type, body.difficulty, body.topic)
        if body.ids:
            q = q.filter(BankQuestion.id.in_(body.ids))
        elif not (body.subject or body.q_type or body.difficulty or body.topic):
            raise HTTPException(422, "Choose questions, or filter by subject first.")
        have = _have_hashes(db, user)
        added = skipped = 0
        for r in q.limit(_MAX_BULK).all():
            if r.text_hash in have:
                skipped += 1
                continue
            have.add(r.text_hash)
            db.add(BankQuestion(owner_id=user.id, paper_id=None, subject=r.subject, topic=r.topic, q_type=r.q_type,
                                difficulty=r.difficulty, marks=r.marks, text_hash=r.text_hash, language=r.language,
                                content=dict(r.content), use_count=0))
            added += 1
    audit(state, request, user, "catalog.add", "question", "", f"{added} added, {skipped} already there")
    return {"added": added, "skipped": skipped}
