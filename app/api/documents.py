from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import audit, get_state, get_visible, names_of, school_shared, scope_to_visible
from app.ingestion.extract import ALLOWED_EXTENSIONS, safe_display_name
from app.models import Chunk, Document, User
from app.security import current_user
from app.services.documents import delete_document_files

router = APIRouter(prefix="/api/documents", tags=["documents"])

_CHUNK = 1024 * 1024


def _doc_json(d: Document, detail: bool = False, by: str = "", can_delete: bool = True) -> dict:
    out = {"id": d.id, "filename": d.filename, "format": d.format, "status": d.status, "message": d.message,
           "created_at": d.created_at.isoformat() + "Z", "uploaded_by": by, "can_delete": can_delete,
           "published": bool(d.published)}
    stats = d.stats or {}
    out["stats"] = {k: v for k, v in stats.items() if k != "chapters"}
    if detail:
        out["chapters"] = stats.get("chapters", [])
    return out


def _may_delete(d: Document, user: User, shared: bool) -> bool:
    """In a multi-school service source files are the school's shared library and only its administrator removes them."""
    return user.role == "admin" if shared else (user.role == "admin" or d.owner_id == user.id)


@router.post("", status_code=202)
async def upload(request: Request, files: List[UploadFile] = File(...), ai_vision: bool = Form(False),
                 user: User = Depends(current_user)):
    state = get_state(request)
    if len(files) > 10:
        raise HTTPException(413, "Upload at most 10 files at a time.")
    created = []
    for f in files:
        name = safe_display_name(f.filename or "document")
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(415, f"'{name}': unsupported file type. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")
        doc_id = uuid.uuid4().hex
        folder = state.settings.upload_dir / doc_id
        folder.mkdir(parents=True)
        target = folder / f"source{ext}"
        size = 0
        try:
            with open(target, "wb") as out:
                while True:
                    block = await f.read(_CHUNK)
                    if not block:
                        break
                    size += len(block)
                    if size > state.settings.max_upload_bytes:
                        raise HTTPException(413, f"'{name}' exceeds the {state.settings.max_upload_mb} MB limit.")
                    out.write(block)
        except HTTPException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        if size == 0:
            shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(422, f"'{name}' is empty.")
        with state.db.session() as db:
            doc = Document(id=doc_id, owner_id=user.id, filename=name, format=ext.lstrip("."), status="queued",
                           stored_path=str(target), stats={"size_bytes": size, "ai_vision": ai_vision})
            db.add(doc)
            db.flush()
            created.append(_doc_json(doc, by=user.name, can_delete=user.role == "admin" or not school_shared(state)))
        state.jobs.wake()
        audit(state, request, user, "document.upload", "document", doc_id, name)
    return {"documents": created}


@router.get("")
def list_documents(request: Request, user: User = Depends(current_user)):
    state = get_state(request)
    shared = school_shared(state)
    with state.db.session() as db:
        q = scope_to_visible(db.query(Document), Document.owner_id, db, user, shared)
        rows = q.order_by(Document.created_at.desc()).limit(200).all()
        names = names_of(db, [d.owner_id for d in rows])
        return {"documents": [_doc_json(d, by=names.get(d.owner_id, ""), can_delete=_may_delete(d, user, shared)) for d in rows]}


@router.get("/{doc_id}")
def get_document(doc_id: str, request: Request, user: User = Depends(current_user)):
    state = get_state(request)
    with state.db.session() as db:
        shared = school_shared(state)
        d = get_visible(db, Document, doc_id, user, shared)
        return _doc_json(d, detail=True, by=names_of(db, [d.owner_id]).get(d.owner_id, ""),
                         can_delete=_may_delete(d, user, shared))


@router.delete("/{doc_id}", status_code=204)
def delete_document(doc_id: str, request: Request, user: User = Depends(current_user)):
    state = get_state(request)
    shared = school_shared(state)
    with state.db.session() as db:
        doc = get_visible(db, Document, doc_id, user, shared)
        if not _may_delete(doc, user, shared):
            raise HTTPException(403, "Only your school administrator can delete source files.")
        stored = doc.stored_path
        db.delete(doc)
    root = state.settings.upload_dir.resolve()
    if stored and root in Path(stored).resolve().parents:
        delete_document_files(stored)
    audit(state, request, user, "document.delete", "document", doc_id)


@router.get("/{doc_id}/figures")
def list_figures(doc_id: str, request: Request, user: User = Depends(current_user)):
    state = get_state(request)
    with state.db.session() as db:
        get_visible(db, Document, doc_id, user, school_shared(state))
        rows = db.query(Chunk).filter(Chunk.document_id == doc_id, Chunk.kind == "figure").order_by(Chunk.idx).all()
        return {"figures": [{"id": c.id, "description": c.text, "page": (c.meta or {}).get("page"),
                             "has_image": bool((c.meta or {}).get("image_path")),
                             "has_diagram": bool((c.meta or {}).get("diagram"))} for c in rows]}


@router.get("/{doc_id}/figures/{chunk_id}/image")
def figure_image(doc_id: str, chunk_id: str, request: Request, user: User = Depends(current_user)):
    state = get_state(request)
    with state.db.session() as db:
        get_visible(db, Document, doc_id, user, school_shared(state))
        chunk = db.get(Chunk, chunk_id)
        path = (chunk.meta or {}).get("image_path") if chunk and chunk.document_id == doc_id else None
    root = state.settings.upload_dir.resolve()
    if not path or root not in Path(path).resolve().parents or not Path(path).is_file():
        raise HTTPException(404, "Not found.")
    return FileResponse(path, media_type="image/png")
