from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from book_to_skill.exceptions import ExtractionError

from app.ingestion.chunking import chunk_text, summarise_chapters
from app.ingestion.extract import extract_source
from app.llm.usage import UsageMeter
from app.models import Chunk, Document
from app.services.ai_settings import resolve_ai
from app.services.usage import record_usage
from app.state import AppState

log = logging.getLogger("examforge.documents")


@dataclass
class ChunkView:
    id: str
    document_id: str
    chapter: str
    text: str
    kind: str
    meta: Dict[str, Any]


def load_chunks(state: AppState, document_ids: List[str]) -> List[ChunkView]:
    if not document_ids:
        return []
    with state.db.session() as db:
        rows = db.query(Chunk).filter(Chunk.document_id.in_(document_ids)).order_by(Chunk.document_id, Chunk.idx).all()
        return [ChunkView(r.id, r.document_id, r.chapter, r.text, r.kind, dict(r.meta or {})) for r in rows]


def document_chapters(state: AppState, document_ids: List[str]) -> List[str]:
    titles: List[str] = []
    with state.db.session() as db:
        rows = (db.query(Chunk.chapter).filter(Chunk.document_id.in_(document_ids), Chunk.kind == "text")
                .order_by(Chunk.document_id, Chunk.idx).all())
    for (title,) in rows:
        if title and title not in titles:
            titles.append(title)
    return titles


def _set_status(state: AppState, doc_id: str, status: str, message: str = "", stats: Dict[str, Any] = None) -> None:
    with state.db.session() as db:
        doc = db.get(Document, doc_id)
        if doc is None:
            return
        doc.status, doc.message = status, message[:1000]
        if stats is not None:
            doc.stats = stats


def process_document(state: AppState, doc_id: str) -> None:
    """Background job: extract text and figures, chunk, and index. Never raises."""
    try:
        with state.db.session() as db:
            doc = db.get(Document, doc_id)
            if doc is None:
                return
            path, owner_id = Path(doc.stored_path), doc.owner_id
            vision = bool((doc.stats or {}).get("ai_vision"))
        meter = UsageMeter()
        resolved = resolve_ai(state, owner_id)
        try:
            result = extract_source(path, resolved.provider, state.settings,
                                    lambda m: _set_status(state, doc_id, "processing", m), meter, vision)
        finally:
            record_usage(state, owner_id, "document", doc_id, meter, resolved)
        drafts = chunk_text(result.text)
        if not drafts and result.text.strip():
            from app.ingestion.chunking import ChunkDraft
            drafts = [ChunkDraft("", result.text.strip())]
        fig_dir = path.parent / "figures"
        with state.db.session() as db:
            db.query(Chunk).filter(Chunk.document_id == doc_id).delete()  # a retry starts clean
            idx = 0
            for d in drafts:
                db.add(Chunk(document_id=doc_id, idx=idx, kind="text", chapter=d.chapter[:300], text=d.text))
                idx += 1
            for n, fig in enumerate(result.figures):
                meta: Dict[str, Any] = {"page": fig.page, "kind": fig.kind, "diagram": fig.diagram}
                if fig.image:
                    fig_dir.mkdir(exist_ok=True)
                    img_path = fig_dir / f"{n}.png"
                    img_path.write_bytes(fig.image)
                    meta["image_path"] = str(img_path)
                db.add(Chunk(document_id=doc_id, idx=idx, kind="figure", chapter="", text=fig.description, meta=meta))
                idx += 1
        stats = dict(result.stats)
        stats["ai_vision"] = vision
        stats.update(method=result.method, chunks=len(drafts), figures=len(result.figures),
                     words=len(result.text.split()), chapters=summarise_chapters(drafts)[:200])
        _set_status(state, doc_id, "ready", "", stats)
    except ExtractionError as exc:
        _set_status(state, doc_id, "failed", str(exc))
    except Exception as exc:  # keep the worker alive; surface a safe message
        log.exception("document processing failed")
        _set_status(state, doc_id, "failed", f"Could not process this file ({exc.__class__.__name__}).")


def delete_document_files(path: str) -> None:
    if path:
        shutil.rmtree(Path(path).parent, ignore_errors=True)
