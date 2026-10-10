"""Time-limited, read-only support access to a school, granted by that school's administrator."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from app.models import SupportGrant

MAX_GRANT_HOURS = 72
MAX_SESSION_MINUTES = 120  # one support session; the operator opens a new one if more time is needed within the grant


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def active_grant(db, org_id: str) -> Optional[SupportGrant]:
    """The grant in force right now: not revoked, not expired. Checked on every support request, so revoking is immediate."""
    return (db.query(SupportGrant)
            .filter(SupportGrant.org_id == org_id, SupportGrant.revoked_at.is_(None), SupportGrant.expires_at > _now())
            .order_by(SupportGrant.expires_at.desc()).first())


def grant_json(g: Optional[SupportGrant]) -> dict:
    if g is None:
        return {"active": False}
    return {"active": True, "expires_at": g.expires_at.isoformat() + "Z", "note": g.note, "granted_at": g.created_at.isoformat() + "Z"}


def create_grant(db, org_id: str, user_id: str, hours: int, note: str) -> SupportGrant:
    """One grant at a time: a new one replaces any that is still running."""
    now = _now()
    for old in db.query(SupportGrant).filter(SupportGrant.org_id == org_id, SupportGrant.revoked_at.is_(None),
                                             SupportGrant.expires_at > now).all():
        old.revoked_at = now
    grant = SupportGrant(org_id=org_id, granted_by=user_id, note=note.strip()[:300],
                         expires_at=now + dt.timedelta(hours=hours))
    db.add(grant)
    db.flush()
    return grant


def revoke(db, org_id: str) -> int:
    now = _now()
    rows = db.query(SupportGrant).filter(SupportGrant.org_id == org_id, SupportGrant.revoked_at.is_(None),
                                         SupportGrant.expires_at > now).all()
    for g in rows:
        g.revoked_at = now
    return len(rows)
