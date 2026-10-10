from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, Optional, Tuple

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.models import Organization, User

_PBKDF2_ITERATIONS = 600_000  # OWASP guidance for PBKDF2-HMAC-SHA256; hashlib.scrypt is missing on some builds
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_b64, digest_b64 = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def validate_new_credentials(email: str, password: str) -> None:
    if not _EMAIL_RE.match(email) or len(email) > 255:
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    if len(password) < 10:
        raise HTTPException(status_code=422, detail="Password must be at least 10 characters.")
    if len(password) > 200:
        raise HTTPException(status_code=422, detail="Password is too long.")


def create_token(user: User, secret: str, ttl_minutes: int, support_org: str = "") -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user.id, "role": user.role, "iat": now, "exp": now + timedelta(minutes=ttl_minutes)}
    if support_org:
        payload["sup"] = support_org  # a read-only support session on this school
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_payload(token: str, secret: str) -> Optional[dict]:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def decode_token(token: str, secret: str) -> Optional[str]:
    data = decode_payload(token, secret)
    return data.get("sub") if data else None


# What a support session may not touch even by reading: keys, people, billing, and downloads of papers.
_SUPPORT_BLOCKED = ("/api/ai", "/api/admin", "/api/platform", "/api/billing", "/api/usage")
_SUPPORT_VIEWED = re.compile(r"^/api/(papers|documents|bank)/[0-9a-f]{32}")


class RateLimiter:
    """Sliding-window limiter keyed by an arbitrary string (in-process)."""

    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> Tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False, int(self.window - (now - hits[0])) + 1
            hits.append(now)
            return True, 0

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


_bearer = HTTPBearer(auto_error=False)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def current_user(
    request: Request, creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated.")
    state = request.app.state.ctx
    payload = decode_payload(creds.credentials, state.settings.secret_key)
    user_id = payload.get("sub") if payload else None
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session.")
    with state.db.session() as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not available.")
        if payload.get("sup"):
            return _support_user(request, db, user, payload["sup"])
        org = db.get(Organization, user.org_id) if user.org_id else None
        if org is not None and not org.is_active and not user.is_superadmin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your school's account is suspended. Contact ExamForge support.")
        return user


def _support_user(request: Request, db, operator: User, org_id: str) -> User:
    """The platform operator looking at one school: read-only, content only, and only while the school's administrator
    allows it. Returned as a transient 'support' member of that school, never saved."""
    from app.models import AuditLog
    from app.services import support

    if not operator.is_superadmin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform operator access required.")
    org = db.get(Organization, org_id)
    grant = support.active_grant(db, org_id) if org is not None else None
    if org is None or grant is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This school's support access has ended.")
    path = request.url.path
    if request.method not in ("GET", "HEAD") or path.startswith(_SUPPORT_BLOCKED) or "/export" in path:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Support view is read-only and shows content only.")
    if _SUPPORT_VIEWED.match(path):  # opening a particular paper, file or bank item is recorded in the school's log
        db.add(AuditLog(user_id=operator.id, org_id=org_id, action="support.view", entity=path.split("/")[2],
                        entity_id=path.split("/")[3], detail=f"{operator.email} (platform support)",
                        ip=client_ip(request)))
    view = User(id=operator.id, email=operator.email, name=operator.name, organization=org.name, org_id=org_id,
                role="support", is_active=True, is_superadmin=False)
    view.support_info = {"school": org.name, "expires_at": grant.expires_at.isoformat() + "Z"}
    return view


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access required.")
    return user


def require_superadmin(user: User = Depends(current_user)) -> User:
    if not user.is_superadmin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform operator access required.")
    return user
