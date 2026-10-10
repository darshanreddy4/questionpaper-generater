from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import audit, get_state
from app.models import Organization, User
from app.services import tenancy
from app.security import (
    client_ip, create_token, current_user, hash_password, validate_new_credentials, verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_DUMMY_HASH = hash_password("not-a-real-password")  # keeps login timing similar for unknown emails


class RegisterBody(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=200)
    name: str = Field(min_length=1, max_length=120)
    organization: str = Field(default="", max_length=200)
    school_name: str = Field(default="", max_length=200)  # multi-school: registers a new school with you as its administrator
    invite_code: str = Field(default="", max_length=100)  # joins an existing school


class LoginBody(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=200)


def _user_json(u: User) -> dict:
    out = {"id": u.id, "email": u.email, "name": u.name, "organization": u.organization, "role": u.role,
           "org_id": u.org_id, "is_superadmin": u.is_superadmin}
    if getattr(u, "support_info", None):
        out["support"] = u.support_info
    return out


@router.get("/config")
def auth_config(request: Request):
    """Public: lets the sign-in page show the right registration choices."""
    s = get_state(request).settings
    return {"tenancy": s.tenancy, "allow_registration": s.allow_registration,
            "allow_school_signup": s.allow_school_signup and s.allow_registration}


@router.post("/register")
def register(body: RegisterBody, request: Request):
    state = get_state(request)
    cfg = state.settings
    email = body.email.strip().lower()
    validate_new_credentials(email, body.password)
    with state.db.session() as db:
        if db.query(User).filter(User.email == email).first():
            raise HTTPException(409, "An account with this email already exists.")
        role = "teacher"
        if body.invite_code.strip():  # an invitation always works, even when open sign-up is closed
            inv = tenancy.redeem_invite(db, body.invite_code, email)
            org, role = db.get(Organization, inv.org_id), inv.role
        elif cfg.tenancy == "single":
            if not cfg.allow_registration:
                raise HTTPException(403, "Registration is disabled. Ask an administrator for an account.")
            org = db.query(Organization).order_by(Organization.created_at).first()
            if org is None:
                org = tenancy.new_org(db, body.organization or "My School")
            role = "admin" if db.query(User).count() == 0 else "teacher"
        else:
            operator = email in cfg.superadmin_emails
            if not (cfg.allow_registration and cfg.allow_school_signup) and not operator:
                raise HTTPException(403, "School sign-up is closed. Ask your school administrator for an invitation.")
            school = (body.school_name or body.organization).strip() or ("ExamForge Platform" if operator else "")
            if not school:
                raise HTTPException(422, "Enter your school or college name, or use an invitation code.")
            org, role = tenancy.new_org(db, school), "admin"
        user = User(email=email, name=body.name.strip(), organization=org.name, org_id=org.id,
                    password_hash=hash_password(body.password), role=role)
        tenancy.sync_superadmin(cfg, user)
        db.add(user)
        db.flush()
        token = create_token(user, cfg.secret_key, cfg.token_ttl_minutes)
        payload = _user_json(user)
    audit(state, request, user, "auth.register", "user", user.id, f"role={role}")
    return {"token": token, "user": payload}


@router.post("/login")
def login(body: LoginBody, request: Request):
    state = get_state(request)
    email = body.email.strip().lower()
    key = f"{client_ip(request)}|{email}"
    allowed, retry = state.login_limiter.check(key)
    if not allowed:
        raise HTTPException(429, f"Too many attempts. Try again in {retry} seconds.", headers={"Retry-After": str(retry)})
    with state.db.session() as db:
        user = db.query(User).filter(User.email == email).first()
        ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
        if not user or not ok or not user.is_active:
            audit(state, request, None, "auth.login_failed", "user", "", email)
            raise HTTPException(401, "Incorrect email or password.")
        org = db.get(Organization, user.org_id) if user.org_id else None
        if org is not None and not org.is_active and not user.is_superadmin:
            raise HTTPException(403, "Your school's account is suspended. Contact ExamForge support.")
        tenancy.sync_superadmin(state.settings, user)
        token = create_token(user, state.settings.secret_key, state.settings.token_ttl_minutes)
        payload = _user_json(user)
    state.login_limiter.reset(key)
    audit(state, request, user, "auth.login", "user", user.id)
    return {"token": token, "user": payload}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return _user_json(user)
