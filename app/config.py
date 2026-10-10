from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, default)))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    secret_key: str
    token_ttl_minutes: int
    allow_registration: bool
    max_upload_mb: int
    cors_origins: List[str]
    llm_provider: str  # auto | openai | anthropic | offline
    openai_api_key: Optional[str]
    openai_base_url: str
    openai_model: str
    openai_fast_model: str
    anthropic_api_key: Optional[str]
    anthropic_base_url: str
    anthropic_model: str
    anthropic_fast_model: str
    daily_token_budget: int  # per user; 0 disables the quota
    cache_ttl_days: int
    job_lease_seconds: int  # a running job whose heartbeat is older than this is reclaimed
    job_max_attempts: int
    db_pool_size: int
    tenancy: str  # single: one school per deployment | multi: many schools, sign-up by school or invite
    allow_school_signup: bool
    allow_individual_signup: bool  # multi: a lone teacher or tutor may open a personal workspace
    superadmin_emails: List[str]
    hybrid_writer: bool  # let code write what it can write exactly
    allow_private_ai_urls: bool  # permit customer AI endpoints on private networks (on-prem Ollama/vLLM)
    price_input_per_million: float  # what the platform charges per 1M prompt tokens on ITS key
    price_output_per_million: float
    platform_fee_monthly: float  # flat fee per workspace per month, owed even with zero AI tokens
    currency: str
    db_max_overflow: int
    llm_timeout_seconds: int
    llm_max_concurrency: int
    vision_max_images: int
    vision_ocr_max_pages: int

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def _resolve_secret(data_dir: Path) -> str:
    env = os.environ.get("EXAMFORGE_SECRET_KEY")
    if env:
        return env
    # Persisted so tokens survive restarts without forcing operators to set one.
    secret_file = data_dir / ".secret"
    if secret_file.is_file():
        return secret_file.read_text(encoding="utf-8").strip()
    secret = secrets.token_urlsafe(48)
    fd = os.open(str(secret_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(secret)
    return secret


def load_settings(load_env_file: bool = True) -> Settings:
    if load_env_file:
        _load_dotenv(ROOT / ".env")
    data_dir = Path(os.environ.get("EXAMFORGE_DATA_DIR", str(ROOT / "data"))).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "uploads").mkdir(exist_ok=True)
    origins = [o.strip() for o in os.environ.get("EXAMFORGE_CORS_ORIGINS", "").split(",") if o.strip()]
    return Settings(
        data_dir=data_dir,
        database_url=os.environ.get("EXAMFORGE_DATABASE_URL", f"sqlite:///{data_dir / 'examforge.db'}"),
        secret_key=_resolve_secret(data_dir),
        token_ttl_minutes=_int("EXAMFORGE_TOKEN_TTL_MINUTES", 720),
        allow_registration=_bool("EXAMFORGE_ALLOW_REGISTRATION", True),
        max_upload_mb=_int("EXAMFORGE_MAX_UPLOAD_MB", 50),
        cors_origins=origins,
        llm_provider=os.environ.get("EXAMFORGE_LLM_PROVIDER", "auto").lower(),
        openai_api_key=os.environ.get("OPENAI_API_KEY") or None,
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        openai_fast_model=os.environ.get("OPENAI_FAST_MODEL", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        anthropic_base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/"),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
        anthropic_fast_model=os.environ.get("ANTHROPIC_FAST_MODEL", ""),
        daily_token_budget=_int("EXAMFORGE_DAILY_TOKEN_BUDGET", 0),
        cache_ttl_days=_int("EXAMFORGE_CACHE_TTL_DAYS", 30),
        job_lease_seconds=max(30, _int("EXAMFORGE_JOB_LEASE_SECONDS", 90)),
        job_max_attempts=max(1, _int("EXAMFORGE_JOB_MAX_ATTEMPTS", 3)),
        db_pool_size=max(1, _int("EXAMFORGE_DB_POOL_SIZE", 10)),
        tenancy="multi" if os.environ.get("EXAMFORGE_TENANCY", "single").lower() == "multi" else "single",
        allow_school_signup=_bool("EXAMFORGE_ALLOW_SCHOOL_SIGNUP", True),
        allow_individual_signup=_bool("EXAMFORGE_ALLOW_INDIVIDUAL_SIGNUP", True),
        superadmin_emails=[e.strip().lower() for e in os.environ.get("EXAMFORGE_SUPERADMIN_EMAILS", "").split(",") if e.strip()],
        hybrid_writer=_bool("EXAMFORGE_HYBRID_WRITER", True),
        allow_private_ai_urls=_bool("EXAMFORGE_ALLOW_PRIVATE_AI_URLS", False),
        price_input_per_million=_float("EXAMFORGE_PRICE_INPUT_PER_M", 0.0),
        price_output_per_million=_float("EXAMFORGE_PRICE_OUTPUT_PER_M", 0.0),
        platform_fee_monthly=_float("EXAMFORGE_PLATFORM_FEE_MONTHLY", 0.0),
        currency=os.environ.get("EXAMFORGE_CURRENCY", "USD").upper()[:6],
        db_max_overflow=max(0, _int("EXAMFORGE_DB_MAX_OVERFLOW", 20)),
        llm_timeout_seconds=_int("EXAMFORGE_LLM_TIMEOUT_SECONDS", 180),
        llm_max_concurrency=max(1, _int("EXAMFORGE_LLM_MAX_CONCURRENCY", 4)),
        vision_max_images=_int("EXAMFORGE_VISION_MAX_IMAGES", 20),
        vision_ocr_max_pages=_int("EXAMFORGE_VISION_OCR_MAX_PAGES", 40),
    )
