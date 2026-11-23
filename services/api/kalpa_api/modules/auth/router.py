"""Auth routes: signup, login, refresh, logout, me, switch-org."""

import asyncio
import re
import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from redis.asyncio import Redis
from sqlalchemy import select

from kalpa_api.core.audit import write_audit
from kalpa_api.core.config import get_settings
from kalpa_api.core.deps import Principal, Session, check_csrf, current_principal, get_redis
from kalpa_api.core.errors import ApiError
from kalpa_api.core.ratelimit import per_ip, signup_limit
from kalpa_api.core.rbac import permissions_for
from kalpa_api.core.security import (
    DUMMY_HASH,
    REFRESH_COOKIE,
    clear_session_cookies,
    decode_token,
    hash_password,
    set_session_cookies,
    verify_password,
)
from kalpa_api.db.models import Membership, Org, User
from kalpa_api.modules.auth.schemas import LoginIn, MeOut, OrgBrief, SignupIn, SwitchOrgIn

router = APIRouter(prefix="/auth", tags=["auth"])
Rds = Annotated[Redis, Depends(get_redis)]


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "org"
    return f"{base}-{secrets.token_hex(3)}"


async def _me(session: Session, user: User, org_id: uuid.UUID, role: str, scope: list[str] | None) -> MeOut:
    rows = (
        await session.execute(
            select(Org.id, Org.name, Membership.role)
            .join(Membership, Membership.org_id == Org.id)
            .where(Membership.user_id == user.id)
            .order_by(Org.name)
        )
    ).all()
    return MeOut(
        user_id=user.id,
        email=user.email,
        name=user.name,
        org_id=org_id,
        role=role,
        permissions=permissions_for(role),
        location_ids=scope,
        orgs=[OrgBrief(id=i, name=n, role=r) for i, n, r in rows],
    )


@router.post(
    "/signup",
    response_model=MeOut,
    status_code=201,
    operation_id="signup",
    dependencies=[Depends(per_ip("signup", signup_limit, 3600))],
)
async def signup(body: SignupIn, request: Request, response: Response, session: Session) -> MeOut:
    """Create a user + organisation (caller becomes owner) and start a session."""
    email = body.email.lower()
    if (await session.execute(select(User.id).where(User.email == email))).first():
        raise ApiError(409, "email_taken", "An account with this email already exists")
    org = Org(
        name=body.org_name,
        slug=_slug(body.org_name),
        currency=body.currency,
        region="IN" if body.currency == "INR" else "US",
    )
    user = User(email=email, password_hash=await asyncio.to_thread(hash_password, body.password), name=body.name)
    session.add_all([org, user])
    await session.flush()
    session.add(Membership(user_id=user.id, org_id=org.id, role="owner"))
    write_audit(
        session, org_id=org.id, user_id=user.id, action="signup", entity="orgs", entity_id=org.id, ip=_ip(request)
    )
    await session.commit()
    set_session_cookies(response, get_settings(), user.id, org.id)
    return await _me(session, user, org.id, "owner", None)


@router.post("/login", response_model=MeOut, operation_id="login")
async def login(body: LoginIn, request: Request, response: Response, session: Session, redis: Rds) -> MeOut:
    """Verify credentials (rate-limited per IP+email) and start a session."""
    s = get_settings()
    email = body.email.lower()
    key = f"kalpa:rl:login:{_ip(request)}:{email}"
    failures = int(await redis.get(key) or 0)
    if failures >= s.login_max_failures:
        raise ApiError(429, "too_many_attempts", "Too many failed logins; try again later")
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    # argon2 is deliberately slow (~100 ms of CPU): in a thread so concurrent logins do not freeze the event loop
    verified = await asyncio.to_thread(verify_password, user.password_hash if user else DUMMY_HASH, body.password)
    ok = verified and user is not None and user.is_active
    if not ok or user is None:
        await redis.incr(key)
        await redis.expire(key, s.login_lockout_seconds)
        raise ApiError(401, "invalid_credentials", "Invalid email or password")
    q = select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at)
    if body.org_id:
        q = q.where(Membership.org_id == body.org_id)
    m = (await session.execute(q)).scalars().first()
    if m is None:
        raise ApiError(403, "no_membership", "No organisation access")
    await redis.delete(key)
    write_audit(
        session, org_id=m.org_id, user_id=user.id, action="login", entity="users", entity_id=user.id, ip=_ip(request)
    )
    await session.commit()
    set_session_cookies(response, s, user.id, m.org_id)
    return await _me(session, user, m.org_id, m.role, m.location_ids)


@router.post("/refresh", response_model=MeOut, operation_id="refresh")
async def refresh(request: Request, response: Response, session: Session, redis: Rds) -> MeOut:
    """Rotate tokens using the refresh cookie (CSRF-protected; revoked tokens rejected)."""
    check_csrf(request)
    s = get_settings()
    token = request.cookies.get(REFRESH_COOKIE)
    claims = decode_token(s, token, "refresh") if token else None
    if not claims or await redis.exists(f"kalpa:revoked:{claims['jti']}"):
        raise ApiError(401, "not_authenticated", "Refresh token invalid")
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
    await redis.set(f"kalpa:revoked:{claims['jti']}", "1", ex=s.refresh_ttl_days * 86400)  # one-time use
    set_session_cookies(response, s, user_id, org_id)
    return await _me(session, row[0], org_id, row[1].role, row[1].location_ids)


@router.post("/logout", status_code=204, operation_id="logout")
async def logout(request: Request, response: Response, redis: Rds) -> None:
    """Clear cookies and revoke the refresh token."""
    check_csrf(request)
    token = request.cookies.get(REFRESH_COOKIE)
    claims = decode_token(get_settings(), token, "refresh") if token else None
    if claims:
        await redis.set(f"kalpa:revoked:{claims['jti']}", "1", ex=get_settings().refresh_ttl_days * 86400)
    clear_session_cookies(response)


@router.get("/me", response_model=MeOut, operation_id="getMe")
async def me(p: Annotated[Principal, Depends(current_principal)], session: Session) -> MeOut:
    """Current user, active org, role, permissions and org list."""
    return await _me(session, p.user, p.org_id, p.role, p.location_ids)


@router.post("/switch-org", response_model=MeOut, operation_id="switchOrg")
async def switch_org(
    body: SwitchOrgIn, response: Response, p: Annotated[Principal, Depends(current_principal)], session: Session
) -> MeOut:
    """Re-issue the session for another org the user belongs to."""
    m = (
        await session.execute(
            select(Membership).where(Membership.user_id == p.user.id, Membership.org_id == body.org_id)
        )
    ).scalar_one_or_none()
    if m is None:
        raise ApiError(404, "not_found", "Organisation not found")
    set_session_cookies(response, get_settings(), p.user.id, m.org_id)
    return await _me(session, p.user, m.org_id, m.role, m.location_ids)
