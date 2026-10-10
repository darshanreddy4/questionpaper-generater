"""Token accounting and the persistent response cache."""
from __future__ import annotations

import datetime as dt
import hashlib
import threading
from typing import Dict, Optional, Sequence, Tuple

from app.db import Database
from app.languages import INDIC_CHAR
from app.models import LLMCacheEntry


def estimate_tokens(text: str) -> int:
    """~4 characters per token for Latin text and ~1.4 for Indic scripts; used only when a provider does not report usage."""
    indic = len(INDIC_CHAR.findall(text))
    return max(1, (len(text) - indic) // 4 + int(indic / 1.4))


class UsageMeter:
    """Thread-safe counters for one job (a paper, an upload) or for the whole process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cached_prompt_tokens = 0  # prompt tokens billed at the provider's discounted cache rate
        self.cache_hits = 0  # whole calls answered from our own response cache
        self.tokens_saved = 0  # tokens a cache hit avoided paying for
        self.estimated = False

    def add(self, prompt: int, completion: int, cached: int = 0, estimated: bool = False) -> None:
        with self._lock:
            self.calls += 1
            self.prompt_tokens += prompt
            self.completion_tokens += completion
            self.cached_prompt_tokens += cached
            self.estimated = self.estimated or estimated

    def add_cache_hit(self, saved_tokens: int) -> None:
        with self._lock:
            self.cache_hits += 1
            self.tokens_saved += saved_tokens

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> Dict[str, object]:
        return {
            "calls": self.calls, "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens, "cached_prompt_tokens": self.cached_prompt_tokens,
            "cache_hits": self.cache_hits, "tokens_saved_by_cache": self.tokens_saved, "estimated": self.estimated,
        }


def cache_key(model: str, system: str, user: str, images: Sequence[bytes], max_tokens: int, temperature: float) -> str:
    h = hashlib.sha256()
    for part in (model, system, user, str(max_tokens), f"{temperature:.2f}"):
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\x00")
    for img in images:
        h.update(hashlib.sha256(img).digest())
    return h.hexdigest()


class ResponseCache:
    """Stores validated model replies so identical requests (re-uploads, same-seed reruns) cost nothing."""

    def __init__(self, db: Database, ttl_days: int = 30):
        self.db = db
        self.ttl = dt.timedelta(days=ttl_days)

    def get(self, key: str) -> Optional[Tuple[str, int]]:
        with self.db.session() as s:
            row = s.get(LLMCacheEntry, key)
            if row is None:
                return None
            if dt.datetime.utcnow() - row.created_at > self.ttl:
                s.delete(row)
                return None
            row.hits += 1
            return row.response, row.tokens

    def put(self, key: str, response: str, model: str, tokens: int) -> None:
        with self.db.session() as s:
            if s.get(LLMCacheEntry, key) is None:
                s.add(LLMCacheEntry(key=key, model=model[:100], response=response, tokens=tokens))

    def prune(self) -> int:
        cutoff = dt.datetime.utcnow() - self.ttl
        with self.db.session() as s:
            return s.query(LLMCacheEntry).filter(LLMCacheEntry.created_at < cutoff).delete()
