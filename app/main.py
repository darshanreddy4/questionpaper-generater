from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import text

from app import __version__
from app.api import ai, auth, catalog, documents, library, papers, platform, support as support_api
from app.config import Settings, load_settings
from app.services.documents import process_document
from app.services import tenancy
from app.services.jobs import JobRunner
from app.services.papers import run_generation
from app.state import build_state

STATIC_DIR = Path(__file__).resolve().parent / "static"
_CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
        "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'")
MAX_JSON_BODY = 5 * 1024 * 1024


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        length = request.headers.get("content-length")
        if length and length.isdigit() and not path.startswith("/api/documents") and int(length) > MAX_JSON_BODY:
            return JSONResponse({"detail": "Request body too large."}, status_code=413)
        response = await call_next(request)
        response.headers.setdefault("X-Request-ID", request.headers.get("x-request-id", uuid.uuid4().hex)[:64])
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Content-Security-Policy", _CSP)
        if path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        else:  # static assets are revalidated (ETag) so a deploy is never masked by a stale cache
            response.headers.setdefault("Cache-Control", "no-cache")
        return response


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or load_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    state = build_state(settings)
    tenancy.ensure_default_org(state)
    state.jobs = JobRunner(state, {"document": process_document, "paper": run_generation})
    state.jobs.start()

    app = FastAPI(title="ExamForge", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url="/api/openapi.json")
    app.state.ctx = state
    app.add_middleware(SecurityMiddleware)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                           allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])
    for module in (auth, documents, papers, library, ai, platform, support_api, catalog):
        app.include_router(module.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok", "version": __version__}

    @app.get("/readyz", include_in_schema=False)
    def readyz():
        try:
            with state.db.session() as db:
                db.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready", "model": state.provider.model, "offline": state.provider.is_offline}

    @app.on_event("shutdown")
    def _shutdown() -> None:
        state.jobs.stop()
        state.ingest_pool.shutdown(wait=False, cancel_futures=True)
        state.generate_pool.shutdown(wait=False, cancel_futures=True)
        state.db.dispose()

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


def app_factory() -> FastAPI:  # for `uvicorn app.main:app_factory --factory`
    return create_app()
