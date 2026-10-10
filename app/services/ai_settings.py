"""Customer-supplied AI keys (bring your own key) and per-request provider resolution.

Resolution order for every job:  the teacher's own key  ->  the school (workspace) key  ->  the platform's key
-> built-in code engine. Jobs on a customer's key are billed by their AI provider, never by us; jobs on the
platform key are metered and charged. Keys are encrypted at rest and never returned to a browser.
"""
from __future__ import annotations

import base64
import copy
import datetime as dt
import hashlib
import ipaddress
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from app.config import Settings
from app.llm.base import LLMError, LLMProvider
from app.llm.providers import AnthropicProvider, OpenAICompatProvider
from app.models import AiSetting, User
from app.state import AppState

log = logging.getLogger("examforge.ai")

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "default_model": "gpt-4o", "needs_url": False},
    "anthropic": {"label": "Anthropic (Claude)", "base_url": "https://api.anthropic.com", "default_model": "claude-sonnet-4-5",
                  "needs_url": False},
    "openai_compatible": {"label": "OpenAI-compatible (Azure, Gemini, Ollama, vLLM…)", "base_url": "", "default_model": "",
                          "needs_url": True},
}


@dataclass
class ResolvedAI:
    provider: LLMProvider
    source: str  # user | workspace | platform | offline
    billing: str  # byok (customer's provider bills them) | platform (we bill tokens) | none (no AI used)

    @property
    def label(self) -> str:
        return {"user": "Your own AI key", "workspace": "Your school's AI key", "platform": "ExamForge platform AI",
                "offline": "Built-in engine (no AI)"}[self.source]


# ------------------------------------------------------------------ encryption

def _fernet(settings: Settings) -> Fernet:
    secret = os.environ.get("EXAMFORGE_ENCRYPTION_KEY") or settings.secret_key
    digest = hashlib.sha256(b"examforge/ai-keys/v1|" + secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_key(settings: Settings, plain: str) -> str:
    return _fernet(settings).encrypt(plain.encode()).decode()


def decrypt_key(settings: Settings, cipher: str) -> str:
    try:
        return _fernet(settings).decrypt(cipher.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("stored key cannot be decrypted (was the secret key changed?)") from exc


# ------------------------------------------------------------------ endpoint safety

def validate_base_url(url: str, allow_private: bool) -> str:
    """A customer-chosen URL must not be a way to reach our internal network (SSRF)."""
    url = (url or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise HTTPException(422, "Enter a full endpoint URL such as https://my-endpoint.example.com/v1.")
    if parsed.username or parsed.password:
        raise HTTPException(422, "The URL must not contain a username or password.")
    if parsed.scheme == "http" and not allow_private:
        raise HTTPException(422, "Only https endpoints are allowed.")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise HTTPException(422, "That host name could not be found.")
    if not allow_private:
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                raise HTTPException(422, "Endpoints on private or internal networks are not allowed.")
    return url


# ------------------------------------------------------------------ providers

_lock = threading.Lock()


def _build(state: AppState, row: AiSetting, key: str) -> LLMProvider:
    s = state.settings
    spec = PROVIDERS[row.provider]
    base = row.base_url or spec["base_url"]
    if row.provider == "anthropic":
        p: LLMProvider = AnthropicProvider(key, base, row.model, s.llm_timeout_seconds, s.llm_max_concurrency, row.fast_model)
    else:
        p = OpenAICompatProvider(key, base, row.model, s.llm_timeout_seconds, s.llm_max_concurrency, row.fast_model)
    p.cache = state.provider.cache
    p.cache_scope = f"{row.scope}:{row.owner_id}"
    return p


def _provider_for(state: AppState, row: AiSetting) -> Optional[LLMProvider]:
    token = (row.id, row.updated_at.isoformat() if row.updated_at else "")
    with _lock:
        cached = state.ai_providers.get(token)
        if cached is not None:
            return cached
    try:
        provider = _build(state, row, decrypt_key(state.settings, row.key_cipher))
    except ValueError as exc:
        log.error("AI setting %s unusable: %s", row.id, exc)
        return None
    with _lock:
        state.ai_providers = {k: v for k, v in state.ai_providers.items() if k[0] != row.id}
        state.ai_providers[token] = provider
    return provider


def _platform_for(state: AppState, org_id: str) -> LLMProvider:
    """The platform's provider, with a cache namespace per school so one school's cached replies never reach another."""
    base = state.provider
    if base.is_offline:
        return base
    token = ("platform", org_id, id(base))
    with _lock:
        scoped = state.ai_providers.get(token)
        if scoped is None:
            scoped = copy.copy(base)  # shares the HTTP client, rate gate and lifetime counters
            scoped.cache_scope = f"org:{org_id}"
            state.ai_providers[token] = scoped
    return scoped


def resolve_ai(state: AppState, user_id: str) -> ResolvedAI:
    with state.db.session() as db:
        me = db.get(User, user_id)
        org_id = me.org_id if me else ""
        rows = {r.scope: r for r in db.query(AiSetting).filter(
            ((AiSetting.scope == "user") & (AiSetting.owner_id == user_id))
            | ((AiSetting.scope == "workspace") & (AiSetting.owner_id == org_id))).all()}
    for scope in ("user", "workspace"):
        row = rows.get(scope)
        if row is not None:
            provider = _provider_for(state, row)
            if provider is not None:
                return ResolvedAI(provider, scope, "byok")
    platform = _platform_for(state, org_id)
    if platform.is_offline:
        return ResolvedAI(platform, "offline", "none")
    return ResolvedAI(platform, "platform", "platform")


# ------------------------------------------------------------------ views and CRUD

def row_view(row: Optional[AiSetting]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {"provider": row.provider, "model": row.model, "fast_model": row.fast_model, "base_url": row.base_url,
            "key_hint": row.key_hint, "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None}


def save_setting(state: AppState, scope: str, owner_id: str, actor_id: str, *, provider: str, model: str,
                 fast_model: str = "", base_url: str = "", api_key: str = "") -> None:
    if provider not in PROVIDERS:
        raise HTTPException(422, "Unknown AI provider.")
    model = model.strip() or PROVIDERS[provider]["default_model"]
    if not model:
        raise HTTPException(422, "Enter the model name to use.")
    url = ""
    if PROVIDERS[provider]["needs_url"]:
        url = validate_base_url(base_url, state.settings.allow_private_ai_urls)
    with state.db.session() as db:
        row = (db.query(AiSetting).filter(AiSetting.scope == scope, AiSetting.owner_id == owner_id).first())
        key = api_key.strip()
        if row is None:
            if len(key) < 8:
                raise HTTPException(422, "Paste your API key.")
            row = AiSetting(scope=scope, owner_id=owner_id)
            db.add(row)
        row.provider, row.model, row.fast_model, row.base_url, row.updated_by = provider, model, fast_model.strip(), url, actor_id
        if key:
            if len(key) < 8 or len(key) > 400 or any(c.isspace() for c in key):
                raise HTTPException(422, "That does not look like a valid API key.")
            row.key_cipher, row.key_hint = encrypt_key(state.settings, key), key[-4:]
        row.updated_at = dt.datetime.utcnow()


def delete_setting(state: AppState, scope: str, owner_id: str) -> bool:
    with state.db.session() as db:
        row = db.query(AiSetting).filter(AiSetting.scope == scope, AiSetting.owner_id == owner_id).first()
        if row is None:
            return False
        db.delete(row)
        return True


def test_setting(state: AppState, scope: str, owner_id: str) -> Dict[str, Any]:
    with state.db.session() as db:
        row = db.query(AiSetting).filter(AiSetting.scope == scope, AiSetting.owner_id == owner_id).first()
        if row is None:
            raise HTTPException(404, "No AI key is saved for this scope.")
        db.expunge(row)
    try:
        key = decrypt_key(state.settings, row.key_cipher)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    provider = _build(state, row, key)
    start = time.monotonic()
    try:
        reply = provider.complete("You are a connectivity test.", "Reply with the single word OK.", json_mode=False,
                                  max_tokens=16, temperature=0.0)
    except LLMError as exc:
        return {"ok": False, "error": str(exc).replace(key, "***")[:300]}
    return {"ok": True, "latency_ms": int((time.monotonic() - start) * 1000), "reply": reply.strip()[:40],
            "model": row.model}
