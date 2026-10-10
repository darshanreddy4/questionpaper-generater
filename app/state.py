from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.db import Database
from app.llm import LLMProvider, get_provider
from app.llm.usage import ResponseCache
from app.security import RateLimiter


@dataclass
class AppState:
    settings: Settings
    db: Database
    provider: LLMProvider
    ingest_pool: ThreadPoolExecutor
    generate_pool: ThreadPoolExecutor
    jobs: Any = None  # JobRunner, attached by create_app
    ai_providers: dict = field(default_factory=dict)  # (setting id, version) -> customer provider instance
    ai_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(12, 60))
    login_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(8, 300))
    render_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(90, 60))
    export_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(40, 60))


def build_state(settings: Settings) -> AppState:
    db = Database(settings.database_url, settings.db_pool_size, settings.db_max_overflow)
    provider = get_provider(settings)
    provider.cache = ResponseCache(db, settings.cache_ttl_days)
    provider.cache.prune()
    return AppState(
        settings=settings,
        db=db,
        provider=provider,
        ingest_pool=ThreadPoolExecutor(max_workers=2, thread_name_prefix="ingest"),
        generate_pool=ThreadPoolExecutor(max_workers=2, thread_name_prefix="generate"),
    )
