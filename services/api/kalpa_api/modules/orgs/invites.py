"""Invitations: owners create a one-time link; the invitee sets a password (or proves an existing one) and joins."""

import asyncio
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from kalpa_api.core.audit import write_audit
from kalpa_api.core.config import get_settings
from kalpa_api.core.deps import Principal, Session, require
from kalpa_api.core.errors import ApiError
from kalpa_api.core.security import DUMMY_HASH, hash_password, set_session_cookies, verify_password
from kalpa_api.db.models import Invite, Location, Membership, Org, User

org_router = APIRouter(prefix="/org/invites", tags=["org"])
auth_router = APIRouter(prefix="/auth", tags=["auth"])
Owner = Annotated[Principal, Depends(require("manage_org"))]
INVITE_DAYS = 7
ROLE = "^(owner|planner|store_manager|viewer)$"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class InviteIn(BaseModel):
    email: EmailStr
    role: str = Field(pattern=ROLE)
    location_ids: list[uuid.UUID] | None = None


class InviteCreated(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime
    token: str  # shown ONCE; only its hash is stored
    accept_path: str


class InviteOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime
    accepted: bool


class InviteInfo(BaseModel):
    email: str
    role: str
    org_name: str
    existing_user: bool


class AcceptIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=10, max_length=128)
    name: str | None = Field(default=None, max_length=200)  # required for new users


async def _valid(session: Session, token: str) -> Invite:
    inv = (await session.execute(select(Invite).where(Invite.token_hash == _hash(token)))).scalar_one_or_none()
    exp = inv.expires_at if inv and inv.expires_at.tzinfo else (inv.expires_at.replace(tzinfo=UTC) if inv else None)
    if inv is None or inv.accepted_at is not None or exp is None or exp < datetime.now(UTC):
        raise ApiError(404, "invite_invalid", "This invitation is invalid, expired or already used")
    return inv


@org_router.post("", response_model=InviteCreated, status_code=201, operation_id="createInvite")
async def create_invite(body: InviteIn, request: Request, session: Session, p: Owner) -> InviteCreated:
    """Create a one-time invite link (7 days). No email is sent: share the link yourself."""
    if body.location_ids:
        n = len(
            (
                await session.execute(
                    select(Location.id).where(Location.org_id == p.org_id, Location.id.in_(body.location_ids))
                )
            ).all()
        )
        if n != len(set(body.location_ids)):
            raise ApiError(422, "invalid_reference", "location_ids contain unknown locations")
    token = secrets.token_urlsafe(32)
    inv = Invite(
        org_id=p.org_id,
        email=body.email.lower(),
        role=body.role,
        token_hash=_hash(token),
        created_by=p.user.id,
        location_ids=[str(i) for i in body.location_ids] if body.location_ids else None,
        expires_at=datetime.now(UTC) + timedelta(days=INVITE_DAYS),
    )
    session.add(inv)
    await session.flush()
    write_audit(
        session,
        org_id=p.org_id,
        user_id=p.user.id,
        action="create",
        entity="invites",
        entity_id=inv.id,
        after={"email": inv.email, "role": inv.role},
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    return InviteCreated(
        id=inv.id,
        email=inv.email,
        role=inv.role,
        expires_at=inv.expires_at,
        token=token,
        accept_path=f"/invite?token={token}",
    )


@org_router.get("", response_model=list[InviteOut], operation_id="listInvites")
async def list_invites(session: Session, p: Owner) -> list[InviteOut]:
    """Pending and used invites (never includes tokens)."""
    rows = (
        (await session.execute(select(Invite).where(Invite.org_id == p.org_id).order_by(Invite.created_at.desc())))
        .scalars()
        .all()
    )
    return [
        InviteOut(id=i.id, email=i.email, role=i.role, expires_at=i.expires_at, accepted=i.accepted_at is not None)
        for i in rows
    ]


@org_router.delete("/{invite_id}", status_code=204, operation_id="revokeInvite")
async def revoke_invite(invite_id: uuid.UUID, session: Session, p: Owner) -> None:
    """Revoke an invite."""
    inv = (
        await session.execute(select(Invite).where(Invite.id == invite_id, Invite.org_id == p.org_id))
    ).scalar_one_or_none()
    if inv is None:
        raise ApiError(404, "not_found", "invite not found")
    await session.delete(inv)
    await session.commit()


@auth_router.get("/invite-info", response_model=InviteInfo, operation_id="getInviteInfo")
async def invite_info(token: str, session: Session) -> InviteInfo:
    """What the invite page shows before accepting (valid token required)."""
    inv = await _valid(session, token)
    org = (await session.execute(select(Org.name).where(Org.id == inv.org_id))).scalar_one()
    exists = (await session.execute(select(User.id).where(User.email == inv.email))).first() is not None
    return InviteInfo(email=inv.email, role=inv.role, org_name=org, existing_user=exists)


@auth_router.post("/accept-invite", status_code=204, operation_id="acceptInvite")
async def accept_invite(body: AcceptIn, request: Request, response: Response, session: Session) -> None:
    """Join the org. New email: creates the account. Existing email: the account's password must match."""
    inv = await _valid(session, body.token)
    user = (await session.execute(select(User).where(User.email == inv.email))).scalar_one_or_none()
    if user is None:
        if not body.name:
            raise ApiError(422, "validation_error", "name is required for a new account")
        user = User(
            email=inv.email, password_hash=await asyncio.to_thread(hash_password, body.password), name=body.name
        )
        session.add(user)
        await session.flush()
    elif not (
        await asyncio.to_thread(verify_password, user.password_hash or DUMMY_HASH, body.password) and user.is_active
    ):
        raise ApiError(401, "invalid_credentials", "Password does not match the existing account")
    already = (
        await session.execute(
            select(Membership.id).where(Membership.user_id == user.id, Membership.org_id == inv.org_id)
        )
    ).first()
    if not already:
        session.add(Membership(user_id=user.id, org_id=inv.org_id, role=inv.role, location_ids=inv.location_ids))
    inv.accepted_at = datetime.now(UTC)
    write_audit(
        session,
        org_id=inv.org_id,
        user_id=user.id,
        action="accept",
        entity="invites",
        entity_id=inv.id,
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    set_session_cookies(response, get_settings(), user.id, inv.org_id)
