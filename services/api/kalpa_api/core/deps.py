"""FastAPI dependencies: redis, current principal (user + org + role), permission checks."""

import hmac
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kalpa_api.core.config import Settings, get_settings
from kalpa_api.core.errors import ApiError
from kalpa_api.core.rbac import STORE_SCOPED, allowed
from kalpa_api.core.security import ACCESS_COOKIE, CSRF_COOKIE, CSRF_HEADER, decode_token
from kalpa_api.db.models import Membership, User
from kalpa_api.db.session import get_session

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
Session = Annotated[AsyncSession, Depends(get_session)]


def get_redis(request: Request) -> Redis:
    """Redis client attached to the app."""
    client: Redis = request.app.state.redis
    return client


def check_csrf(request: Request) -> None:
    """Double-submit CSRF check for state-changing requests."""
    cookie, header = request.cookies.get(CSRF_COOKIE), request.headers.get(CSRF_HEADER)
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise ApiError(403, "csrf_failed", "Missing or invalid CSRF token")


@dataclass
class Principal:
    """Authenticated caller in the context of one organisation."""

    user: User
    org_id: uuid.UUID
    role: str
    location_ids: list[str] | None  # store_manager scope; None = all

    def can_access_location(self, location_id: uuid.UUID) -> bool:
        """Owners/planners/viewers see all; scoped store managers only their stores."""
        return self.location_ids is None or str(location_id) in self.location_ids


async def current_principal(request: Request, session: Session) -> Principal:
    """Resolve the cookie JWT to (user, org, role); membership is re-checked on every request."""
    settings: Settings = get_settings()
    token = request.cookies.get(ACCESS_COOKIE)
    claims = decode_token(settings, token, "access") if token else None
    if not claims:
        raise ApiError(401, "not_authenticated", "Authentication required")
    if request.method not in SAFE_METHODS:
        check_csrf(request)
    user_id, org_id = uuid.UUID(claims["sub"]), uuid.UUID(claims["org"])
    row = (
        await session.execute(
            select(User, Membership)
            .join(Membership, Membership.user_id == User.id)
            .where(User.id == user_id, Membership.org_id == org_id, User.is_active.is_(True))
        )
    ).first()
    if row is None:
        raise ApiError(401, "not_authenticated", "Session is no longer valid")
    user, m = row
    return Principal(user=user, org_id=org_id, role=m.role, location_ids=m.location_ids)


def require(permission: str) -> Callable[[Principal], Coroutine[Any, Any, Principal]]:
    """Dependency factory: 403 unless the caller's role holds the permission."""

    async def _dep(p: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if not allowed(p.role, permission):
            raise ApiError(403, "forbidden", f"Role '{p.role}' lacks permission '{permission}'")
        if p.role == "store_manager" and permission not in STORE_SCOPED:
            raise ApiError(403, "forbidden", "Not permitted for this role")
        return p

    return _dep
