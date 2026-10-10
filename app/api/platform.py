"""Platform operator console: schools, their commercial terms, and what each owes this month.

Only accounts named in EXAMFORGE_SUPERADMIN_EMAILS can use it. The operator sees schools and their usage
figures, never their documents, papers or questions.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func

from app.api.deps import audit, get_state
from app.models import Organization, Paper, UsageLog, User
from app.security import require_superadmin
from app.services import support, tenancy
from app.services.usage import _month_bounds

router = APIRouter(prefix="/api/platform", tags=["platform"])


def _org_json(db, org: Organization, usage: Dict[str, Any], state) -> Dict[str, Any]:
    price = tenancy.org_pricing(state, org.id)
    tokens, charge = usage.get(org.id, (0, 0))
    fee = price["fee"]
    return {
        "id": org.id, "name": org.name, "is_active": org.is_active, "created_at": org.created_at.isoformat() + "Z",
        "users": db.query(User).filter(User.org_id == org.id).count(),
        "papers": db.query(Paper).join(User, User.id == Paper.owner_id).filter(User.org_id == org.id).count(),
        "support": support.grant_json(support.active_grant(db, org.id)),
        "terms": {"platform_fee_monthly": price["fee"], "price_input_per_million": price["input"],
                  "price_output_per_million": price["output"],
                  "custom": any(v is not None for v in (org.platform_fee_monthly, org.price_input_per_million,
                                                         org.price_output_per_million))},
        "month": {"platform_tokens": int(tokens), "token_charge": round(charge / 1_000_000, 4), "fee": fee,
                  "total": round(fee + charge / 1_000_000, 4)},
    }


@router.get("/orgs")
def list_orgs(request: Request, month: Optional[str] = Query(None), op: User = Depends(require_superadmin)):
    state = get_state(request)
    start, end, label = _month_bounds(month)
    with state.db.session() as db:
        rows = (db.query(UsageLog.org_id, func.sum(UsageLog.prompt_tokens + UsageLog.completion_tokens),
                         func.sum(UsageLog.charge_micros))
                .filter(UsageLog.billing == "platform", UsageLog.created_at >= start, UsageLog.created_at < end)
                .group_by(UsageLog.org_id).all())
        usage = {oid: (int(t or 0), int(c or 0)) for oid, t, c in rows}
        orgs = [_org_json(db, o, usage, state) for o in db.query(Organization).order_by(Organization.created_at).all()]
    return {"month": label, "currency": state.settings.currency, "orgs": orgs,
            "totals": {"schools": len(orgs), "active": sum(1 for o in orgs if o["is_active"]),
                       "platform_tokens": sum(o["month"]["platform_tokens"] for o in orgs),
                       "revenue": round(sum(o["month"]["total"] for o in orgs if o["is_active"]), 4)}}


class OrgCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    admin_email: str = Field(default="", max_length=255)


@router.post("/orgs", status_code=201)
def create_org(body: OrgCreate, request: Request, op: User = Depends(require_superadmin)):
    """Onboard a school by hand: creates it and returns a one-time invitation for its first administrator."""
    state = get_state(request)
    with state.db.session() as db:
        org = tenancy.new_org(db, body.name)
        org_id, name = org.id, org.name
    code, inv = tenancy.create_invite(state, org_id, op.id, email=body.admin_email, role="admin", days=30)
    audit(state, request, op, "platform.org_create", "organization", org_id, name)
    return {"id": org_id, "name": name, "admin_invite_code": code, "expires_at": inv.expires_at.isoformat() + "Z",
            "note": "Send this code to the school's administrator; it cannot be shown again."}


class OrgPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=200)
    is_active: Optional[bool] = None
    platform_fee_monthly: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    price_input_per_million: Optional[float] = Field(default=None, ge=0, le=100_000)
    price_output_per_million: Optional[float] = Field(default=None, ge=0, le=100_000)


@router.patch("/orgs/{org_id}")
def patch_org(org_id: str, body: OrgPatch, request: Request, op: User = Depends(require_superadmin)):
    """Fields left out stay as they are; sending a pricing field as null returns it to the platform default."""
    state = get_state(request)
    sent = body.model_fields_set
    with state.db.session() as db:
        org = db.get(Organization, org_id)
        if org is None:
            raise HTTPException(404, "School not found.")
        if "name" in sent and body.name:
            org.name = body.name.strip()
            for u in db.query(User).filter(User.org_id == org.id).all():
                u.organization = org.name
        if "is_active" in sent and body.is_active is not None:
            if org_id == op.org_id and not body.is_active:
                raise HTTPException(409, "You cannot suspend the school your own account belongs to.")
            org.is_active = body.is_active
        for field in ("platform_fee_monthly", "price_input_per_million", "price_output_per_million"):
            if field in sent:
                setattr(org, field, getattr(body, field))
    audit(state, request, op, "platform.org_update", "organization", org_id, str(sorted(sent)))
    return {"ok": True}


class AdminInvite(BaseModel):
    email: str = Field(default="", max_length=255)


@router.post("/orgs/{org_id}/admin-invite", status_code=201)
def new_admin_invite(org_id: str, body: AdminInvite, request: Request, op: User = Depends(require_superadmin)):
    state = get_state(request)
    with state.db.session() as db:
        if db.get(Organization, org_id) is None:
            raise HTTPException(404, "School not found.")
    code, inv = tenancy.create_invite(state, org_id, op.id, email=body.email, role="admin", days=30)
    audit(state, request, op, "platform.admin_invite", "organization", org_id)
    return {"code": code, "expires_at": inv.expires_at.isoformat() + "Z"}
