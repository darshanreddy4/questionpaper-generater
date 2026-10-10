"""Schools (organizations): creation, invitations, platform pricing per school, and migration of older data."""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from typing import Optional, Tuple

from fastapi import HTTPException

from app.config import Settings
from app.models import (
    AiSetting, AuditLog, Invite, Organization, UsageLog, User,
)
from app.state import AppState

INVITE_DAYS_DEFAULT = 14


def _hash(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()


def new_org(db, name: str) -> Organization:
    org = Organization(name=name.strip()[:200] or "My School")
    db.add(org)
    db.flush()
    return org


def sync_superadmin(settings: Settings, user: User) -> None:
    """The platform operator is whoever the deployment's environment names; it cannot be granted through the app."""
    user.is_superadmin = user.email.lower() in settings.superadmin_emails


# ------------------------------------------------------------------ invitations

def create_invite(state: AppState, org_id: str, created_by: str, *, email: str = "", role: str = "teacher",
                  days: int = INVITE_DAYS_DEFAULT) -> Tuple[str, Invite]:
    """Returns the one-time code (shown once; only its hash is stored) and the stored invite."""
    if role not in ("teacher", "admin"):
        raise HTTPException(422, "Role must be teacher or admin.")
    code = "inv_" + secrets.token_urlsafe(18)
    with state.db.session() as db:
        inv = Invite(org_id=org_id, code_hash=_hash(code), email=email.strip().lower(), role=role,
                     expires_at=dt.datetime.utcnow() + dt.timedelta(days=max(1, min(days, 90))), created_by=created_by)
        db.add(inv)
        db.flush()
        db.expunge(inv)
    return code, inv


def redeem_invite(db, code: str, email: str) -> Invite:
    """Validates and consumes an invitation inside the caller's transaction."""
    inv = db.query(Invite).filter(Invite.code_hash == _hash(code)).first()
    bad = HTTPException(422, "This invitation is not valid. Ask your administrator for a new one.")
    if inv is None or inv.used_at is not None or inv.expires_at < dt.datetime.utcnow():
        raise bad
    if inv.email and inv.email != email.lower():
        raise HTTPException(422, "This invitation was issued to a different email address.")
    org = db.get(Organization, inv.org_id)
    if org is None or not org.is_active:
        raise bad
    inv.used_at = dt.datetime.utcnow()
    return inv


# ------------------------------------------------------------------ commercial terms

def org_pricing(state: AppState, org_id: str) -> dict:
    s = state.settings
    out = {"input": s.price_input_per_million, "output": s.price_output_per_million, "fee": s.platform_fee_monthly}
    if not org_id:
        return out
    with state.db.session() as db:
        org = db.get(Organization, org_id)
    if org is not None:
        if org.price_input_per_million is not None:
            out["input"] = org.price_input_per_million
        if org.price_output_per_million is not None:
            out["output"] = org.price_output_per_million
        if org.platform_fee_monthly is not None:
            out["fee"] = org.platform_fee_monthly
    return out


def org_of(state: AppState, user_id: str) -> str:
    with state.db.session() as db:
        u = db.get(User, user_id)
        return u.org_id if u else ""


# ------------------------------------------------------------------ migration of single-school data

def ensure_default_org(state: AppState) -> Optional[str]:
    """Databases created before multi-school support have users without a school; put them in one and
    stamp every history row, so nothing is orphaned and isolation checks work on old data too."""
    with state.db.session() as db:
        orphans = db.query(User).filter((User.org_id == "") | (User.org_id.is_(None))).all()
        for u in db.query(User).all():
            sync_superadmin(state.settings, u)
        if not orphans:
            return None
        admin = next((u for u in orphans if u.role == "admin"), orphans[0])
        org = new_org(db, admin.organization or "My School")
        for u in orphans:
            u.org_id = org.id
            u.organization = u.organization or org.name
        db.flush()
        owners = {u.id: u.org_id for u in db.query(User).all()}
        for model in (UsageLog, AuditLog):
            for row in db.query(model).filter((model.org_id == "") | (model.org_id.is_(None))).all():
                uid = getattr(row, "user_id", None)
                if uid in owners:
                    row.org_id = owners[uid]
        for row in db.query(AiSetting).filter(AiSetting.scope == "workspace", AiSetting.owner_id == "").all():
            row.owner_id = org.id  # the old single workspace key now belongs to that school
        return org.id
