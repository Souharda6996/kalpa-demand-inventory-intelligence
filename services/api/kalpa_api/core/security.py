"""Password hashing (argon2), JWT cookies, CSRF double-submit."""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Response

from kalpa_api.core.config import Settings

ACCESS_COOKIE = "kalpa_access"
REFRESH_COOKIE = "kalpa_refresh"
CSRF_COOKIE = "kalpa_csrf"
CSRF_HEADER = "x-csrf-token"
REFRESH_PATH = "/api/v1/auth"

_hasher = PasswordHasher()
# Verified when the user is unknown so login timing does not reveal which emails exist.
DUMMY_HASH = _hasher.hash("kalpa-dummy-password")


def hash_password(password: str) -> str:
    """Argon2id hash."""
    return _hasher.hash(password)


def verify_password(hash_: str, password: str) -> bool:
    """Constant-time verify; False on any mismatch/invalid hash."""
    try:
        return _hasher.verify(hash_, password)
    except (VerificationError, InvalidHashError):
        return False


def _encode(payload: dict[str, Any], secret: str, ttl: timedelta, kind: str) -> str:
    now = datetime.now(UTC)
    body = {**payload, "typ": kind, "iat": now, "exp": now + ttl, "jti": uuid.uuid4().hex}
    return jwt.encode(body, secret, algorithm="HS256")


def make_access_token(s: Settings, user_id: uuid.UUID, org_id: uuid.UUID) -> str:
    """Short-lived access token carrying user + active org (role is looked up per request)."""
    return _encode(
        {"sub": str(user_id), "org": str(org_id)},
        s.jwt_access_secret,
        timedelta(minutes=s.access_ttl_minutes),
        "access",
    )


def make_refresh_token(s: Settings, user_id: uuid.UUID, org_id: uuid.UUID) -> str:
    """Long-lived refresh token."""
    return _encode(
        {"sub": str(user_id), "org": str(org_id)}, s.jwt_refresh_secret, timedelta(days=s.refresh_ttl_days), "refresh"
    )


def decode_token(s: Settings, token: str, kind: str) -> dict[str, Any] | None:
    """Decode + verify signature/expiry/type; None if invalid."""
    secret = s.jwt_access_secret if kind == "access" else s.jwt_refresh_secret
    try:
        data: dict[str, Any] = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return data if data.get("typ") == kind else None


def set_session_cookies(resp: Response, s: Settings, user_id: uuid.UUID, org_id: uuid.UUID) -> None:
    """Set HttpOnly access + refresh cookies and a readable CSRF cookie."""
    common: dict[str, Any] = {"secure": s.cookie_secure, "samesite": "lax"}
    resp.set_cookie(
        ACCESS_COOKIE,
        make_access_token(s, user_id, org_id),
        httponly=True,
        max_age=s.access_ttl_minutes * 60,
        path="/",
        **common,
    )
    resp.set_cookie(
        REFRESH_COOKIE,
        make_refresh_token(s, user_id, org_id),
        httponly=True,
        max_age=s.refresh_ttl_days * 86400,
        path=REFRESH_PATH,
        **common,
    )
    resp.set_cookie(
        CSRF_COOKIE, secrets.token_urlsafe(32), httponly=False, max_age=s.refresh_ttl_days * 86400, path="/", **common
    )


def clear_session_cookies(resp: Response) -> None:
    """Remove all session cookies."""
    resp.delete_cookie(ACCESS_COOKIE, path="/")
    resp.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH)
    resp.delete_cookie(CSRF_COOKIE, path="/")
