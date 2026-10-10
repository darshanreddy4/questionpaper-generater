from __future__ import annotations

from typing import Optional, Type, TypeVar

from fastapi import HTTPException, Request

from app.models import AuditLog, User
from app.security import client_ip
from app.state import AppState

T = TypeVar("T")


def get_state(request: Request) -> AppState:
    return request.app.state.ctx


def audit(state: AppState, request: Optional[Request], user: Optional[User], action: str, entity: str = "",
          entity_id: str = "", detail: str = "") -> None:
    with state.db.session() as db:
        db.add(AuditLog(user_id=user.id if user else None, org_id=user.org_id if user else "", action=action, entity=entity, entity_id=entity_id,
                        detail=detail[:500], ip=client_ip(request) if request else ""))


def school_shared(state: AppState) -> bool:
    """With several schools on one service, a school's work is shared inside the school. A single-school install
    keeps every teacher's work private to them (administrators still see all)."""
    return state.settings.tenancy == "multi"


def _same_school(db, obj, user: User) -> bool:
    owner = db.get(User, obj.owner_id)
    return owner is not None and owner.org_id == user.org_id


def get_visible(db, model: Type[T], obj_id: str, user: User, shared: bool) -> T:
    """A row the user may read: their own, any colleague's in the same school when the school shares its work, and
    any in the school for an administrator. Missing rows and other schools' rows are both a plain 404."""
    obj = db.get(model, obj_id)
    if obj is not None and (obj.owner_id == user.id or ((shared or user.role == "admin") and _same_school(db, obj, user))):
        return obj
    raise HTTPException(404, "Not found.")


def get_for_change(db, model: Type[T], obj_id: str, user: User, shared: bool) -> T:
    """A visible row the user may also edit or delete: their own, or anything in their school if they administer it."""
    obj = get_visible(db, model, obj_id, user, shared)
    if obj.owner_id != user.id and user.role != "admin":
        raise HTTPException(403, "Only the person who created this, or your school administrator, can change it.")
    return obj


def get_owned(db, model: Type[T], obj_id: str, user: User) -> T:
    """Single-school rule: the owner, or an administrator of the same school."""
    return get_visible(db, model, obj_id, user, False)


def school_user_ids(db, user: User):
    """Subquery of every account in the caller's school."""
    return db.query(User.id).filter(User.org_id == user.org_id)


def scope_to_visible(query, owner_col, db, user: User, shared: bool):
    """Restrict a list query to the rows the user may see."""
    if shared or user.role == "admin":
        return query.filter(owner_col.in_(school_user_ids(db, user)))
    return query.filter(owner_col == user.id)


def names_of(db, user_ids) -> dict:
    ids = list({i for i in user_ids if i})
    return {u.id: u.name for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
