from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import audit, get_state
from app.models import AiSetting, User
from app.security import current_user, require_admin
from app.services import ai_settings as svc
from app.services.usage import billing_by_user, billing_summary

router = APIRouter(prefix="/api", tags=["ai-and-billing"])


class AiSettingBody(BaseModel):
    scope: str = Field(default="user", pattern="^(user|workspace)$")
    provider: str = Field(max_length=30)
    model: str = Field(default="", max_length=100)
    fast_model: str = Field(default="", max_length=100)
    base_url: str = Field(default="", max_length=300)
    api_key: str = Field(default="", max_length=400)


def _owner(scope: str, user: User) -> str:
    return user.id if scope == "user" else user.org_id  # a school-wide key belongs to the school


def _need_scope(scope: str, user: User) -> None:
    if scope == "workspace" and user.role != "admin":
        raise HTTPException(403, "Only an administrator can set the school-wide AI key.")


@router.get("/ai/settings")
def get_ai_settings(request: Request, user: User = Depends(current_user)):
    state = get_state(request)
    resolved = svc.resolve_ai(state, user.id)
    with state.db.session() as db:
        mine = db.query(AiSetting).filter(AiSetting.scope == "user", AiSetting.owner_id == user.id).first()
        shared = db.query(AiSetting).filter(AiSetting.scope == "workspace", AiSetting.owner_id == user.org_id).first()
        user_view, shared_view = svc.row_view(mine), svc.row_view(shared)
    s = state.settings
    platform = state.provider
    return {
        "effective": {"source": resolved.source, "label": resolved.label, "billing": resolved.billing,
                      "provider": resolved.provider.name, "model": resolved.provider.model,
                      "vision": resolved.provider.supports_vision and not resolved.provider.is_offline},
        "platform": {"available": not platform.is_offline, "model": None if platform.is_offline else platform.model},
        "user": user_view,
        # Teachers learn that a school key exists, but only administrators see its details.
        "workspace": shared_view if user.role == "admin" else ({"configured": True} if shared_view else None),
        "providers": [{"id": k, **{kk: vv for kk, vv in v.items() if kk != "base_url"}} for k, v in svc.PROVIDERS.items()],
        "can_set_workspace": user.role == "admin",
        "pricing": {"currency": s.currency, "input_per_million": s.price_input_per_million,
                    "output_per_million": s.price_output_per_million, "platform_fee_monthly": s.platform_fee_monthly},
    }


@router.put("/ai/settings")
def put_ai_settings(body: AiSettingBody, request: Request, user: User = Depends(current_user)):
    state = get_state(request)
    _need_scope(body.scope, user)
    ok, wait = state.ai_limiter.check(user.id)
    if not ok:
        raise HTTPException(429, f"Too many changes; try again in {wait} seconds.")
    svc.save_setting(state, body.scope, _owner(body.scope, user), user.id, provider=body.provider, model=body.model,
                     fast_model=body.fast_model, base_url=body.base_url, api_key=body.api_key)
    audit(state, request, user, "ai.key_saved", "ai_setting", body.scope, f"{body.provider}/{body.model}")
    return {"ok": True}


@router.delete("/ai/settings", status_code=204)
def delete_ai_settings(request: Request, scope: str = Query("user", pattern="^(user|workspace)$"),
                       user: User = Depends(current_user)):
    state = get_state(request)
    _need_scope(scope, user)
    if not svc.delete_setting(state, scope, _owner(scope, user)):
        raise HTTPException(404, "No key is saved for this scope.")
    audit(state, request, user, "ai.key_removed", "ai_setting", scope)


class TestBody(BaseModel):
    scope: str = Field(default="user", pattern="^(user|workspace)$")


@router.post("/ai/test")
def test_ai_settings(body: TestBody, request: Request, user: User = Depends(current_user)):
    state = get_state(request)
    _need_scope(body.scope, user)
    ok, wait = state.ai_limiter.check(user.id)
    if not ok:
        raise HTTPException(429, f"Too many tests; try again in {wait} seconds.")
    return svc.test_setting(state, body.scope, _owner(body.scope, user))


@router.get("/billing")
def my_billing(request: Request, month: Optional[str] = Query(None), user: User = Depends(current_user)):
    return billing_summary(get_state(request), user.id, month, user.org_id)


@router.get("/admin/billing")
def workspace_billing(request: Request, month: Optional[str] = Query(None), admin: User = Depends(require_admin)):
    state = get_state(request)
    return {"summary": billing_summary(state, None, month, admin.org_id), "by_user": billing_by_user(state, month, admin.org_id)}
