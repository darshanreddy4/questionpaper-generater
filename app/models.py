from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class JobColumns:
    """Lease bookkeeping for rows that background workers claim (documents, papers)."""

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    heartbeat_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    worker: Mapped[str] = mapped_column(String(255), default="")


class Organization(Base):
    """A school or college. Everything a teacher creates belongs to exactly one organization."""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # a suspended school cannot sign in or generate
    kind: Mapped[str] = mapped_column(String(16), default="school")  # "individual" = one teacher's own workspace; blank on old rows means school
    # Per-school commercial terms; null means "use the platform default".
    platform_fee_monthly: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_input_per_million: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_output_per_million: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class SupportGrant(Base):
    """A school administrator's permission for platform support to look at the school's content, read-only, until a set time."""

    __tablename__ = "support_grants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    granted_by: Mapped[str] = mapped_column(String(32), default="")
    note: Mapped[str] = mapped_column(String(300), default="")
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    revoked_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class Invite(Base):
    """One-time invitation to join an organization. Only a hash of the code is stored."""

    __tablename__ = "invites"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), default="")  # when set, only this address may use the code
    role: Mapped[str] = mapped_column(String(20), default="teacher")
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    used_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    organization: Mapped[str] = mapped_column(String(200), default="")  # display copy of the school name
    org_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)  # the platform operator, not a school admin
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="teacher")  # admin | teacher
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class Document(JobColumns, Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    format: Mapped[str] = mapped_column(String(20), default="")
    status: Mapped[str] = mapped_column(String(20), default="processing")  # processing|ready|failed
    message: Mapped[str] = mapped_column(Text, default="")
    stats: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    stored_path: Mapped[str] = mapped_column(String(500), default="")
    published: Mapped[bool] = mapped_column(Boolean, default=False)  # platform operator only: offered to every school
    published_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    chunks: Mapped[List["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Chunk.idx"
    )


class Chunk(Base):
    """A retrievable unit of source material: a text passage or a figure."""

    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(10), default="text")  # text | figure
    chapter: Mapped[str] = mapped_column(String(300), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)  # figure: image_path, page, diagram

    document: Mapped[Document] = relationship(back_populates="chunks")


class Pattern(Base):
    """User-defined (or inferred) exam pattern."""

    __tablename__ = "patterns"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    spec: Mapped[Dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class Paper(JobColumns, Base):
    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    subject: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|generating|ready|failed
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    request: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    content: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    quality: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class BankQuestion(Base):
    """Every accepted question is kept so later papers can avoid repeating it."""

    __tablename__ = "question_bank"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    paper_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    subject: Mapped[str] = mapped_column(String(200), index=True)
    topic: Mapped[str] = mapped_column(String(300), default="")
    q_type: Mapped[str] = mapped_column(String(30), default="")
    difficulty: Mapped[str] = mapped_column(String(10), default="")
    marks: Mapped[int] = mapped_column(Integer, default=0)
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    language: Mapped[str] = mapped_column(String(40), default="en")  # "" on rows from before languages: English
    content: Mapped[Dict[str, Any]] = mapped_column(JSON)
    use_count: Mapped[int] = mapped_column(Integer, default=0)  # times reused in later papers
    published: Mapped[bool] = mapped_column(Boolean, default=False)  # platform operator only: offered to every school
    published_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class LLMCacheEntry(Base):
    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(100), default="")
    response: Mapped[str] = mapped_column(Text)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)


class UsageLog(Base):
    """One row per finished job, so quotas and chargeback never have to re-add individual calls."""

    __tablename__ = "usage_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # paper | regenerate | document
    ref_id: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(100), default="")
    calls: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_hits: Mapped[int] = mapped_column(Integer, default=0)
    tokens_saved: Mapped[int] = mapped_column(Integer, default=0)
    org_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    billing: Mapped[str] = mapped_column(String(10), default="platform")  # platform | byok | none
    charge_micros: Mapped[int] = mapped_column(BigInteger, default=0)  # 1e-6 of the currency, fixed at the time of use
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)


class AiSetting(Base):
    """A customer-supplied AI provider. scope 'user' belongs to one teacher; 'workspace' is set by an administrator."""

    __tablename__ = "ai_settings"
    __table_args__ = (UniqueConstraint("scope", "owner_id", name="uq_ai_scope_owner"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    scope: Mapped[str] = mapped_column(String(10))  # user | workspace
    owner_id: Mapped[str] = mapped_column(String(32), default="")  # user id, or "" for the workspace
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(100))
    fast_model: Mapped[str] = mapped_column(String(100), default="")
    base_url: Mapped[str] = mapped_column(String(300), default="")
    key_cipher: Mapped[str] = mapped_column(Text)  # Fernet token; the plaintext key is never stored
    key_hint: Mapped[str] = mapped_column(String(8), default="")
    updated_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity: Mapped[str] = mapped_column(String(40), default="")
    entity_id: Mapped[str] = mapped_column(String(40), default="")
    org_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
