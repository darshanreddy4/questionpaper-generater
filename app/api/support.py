"""Support access: a school administrator allows platform support to look at the school, and the operator opens a
read-only session while that permission lasts."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import audit, get_state
from app.models import AuditLog, Organization, SupportGrant, User
from app.security import client_ip, create_token, require_admin, require_superadmin
from app.services import support

router = APIRouter(prefix="/api", tags=["support"])


class GrantBody(BaseModel):
    hours: int = Field(default=24, ge=1, le=support.MAX_GRANT_HOURS)
    note: str = Field(default="", max_length=300)


@router.get("/admin/support")
def support_status(request: Request, admin: User = Depends(require_admin)):
    state = get_state(request)
    with state.db.session() as db:
        grants = (db.query(SupportGrant).filter(SupportGrant.org_id == admin.org_id)
                  .order_by(SupportGrant.created_at.desc()).limit(10).all())
        out = support.grant_json(support.active_grant(db, admin.org_id))
        out["history"] = [{"granted_at": g.created_at.isoformat() + "Z", "expires_at": g.expires_at.isoformat() + "Z",
                           "ended_early": bool(g.revoked_at), "note": g.note} for g in grants]
        return out


@router.post("/admin/support", status_code=201)
def allow_support(body: GrantBody, request: Request, admin: User = Depends(require_admin)):
    state = get_state(request)
    with state.db.session() as db:
        grant = support.create_grant(db, admin.org_id, admin.id, body.hours, body.note)
        out = support.grant_json(grant)
    audit(state, request, admin, "support.allowed", "organization", admin.org_id,
          f"{body.hours} hour(s){': ' + body.note.strip() if body.note.strip() else ''}")
    return out


@router.delete("/admin/support", status_code=204)
def stop_support(request: Request, admin: User = Depends(require_admin)):
    state = get_state(request)
    with state.db.session() as db:
        ended = support.revoke(db, admin.org_id)
    if ended:
        audit(state, request, admin, "support.revoked", "organization", admin.org_id)


@router.post("/platform/orgs/{org_id}/support-session", status_code=201)
def open_support_session(org_id: str, request: Request, op: User = Depends(require_superadmin)):
    """A short read-only session on one school. Refused unless that school's administrator has allowed it."""
    state = get_state(request)
    with state.db.session() as db:
        org = db.get(Organization, org_id)
        if org is None:
            raise HTTPException(404, "School not found.")
        grant = support.active_grant(db, org_id)
        if grant is None:
            raise HTTPException(403, "This school has not allowed support access. Ask its administrator to allow it.")
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        minutes = max(1, min(support.MAX_SESSION_MINUTES, int((grant.expires_at - now).total_seconds() // 60)))
        token = create_token(op, state.settings.secret_key, minutes, support_org=org_id)
        db.add(AuditLog(user_id=op.id, org_id=org_id, action="support.session_start", entity="organization",
                        entity_id=org_id, detail=f"{op.email} (platform support) for up to {minutes} min",
                        ip=client_ip(request)))
        name = org.name
    return {"token": token, "school": name, "minutes": minutes}
