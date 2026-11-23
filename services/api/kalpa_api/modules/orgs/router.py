"""Org settings and member management (owner only for changes)."""

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select

from kalpa_api.core.audit import write_audit
from kalpa_api.core.deps import Principal, Session, require
from kalpa_api.core.errors import ApiError
from kalpa_api.core.security import hash_password
from kalpa_api.db.models import Location, Membership, Org, User
from kalpa_api.modules.orgs.schemas import MemberIn, MemberOut, MemberPatch, OrgOut, OrgPatch

router = APIRouter(prefix="/org", tags=["org"])
Viewer = Annotated[Principal, Depends(require("view"))]
Owner = Annotated[Principal, Depends(require("manage_org"))]


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _scope(session: Session, org_id: uuid.UUID, ids: list[uuid.UUID] | None) -> list[str] | None:
    """Validate location ids belong to the org; returns str list for storage."""
    if ids is None:
        return None
    found = (
        await session.execute(
            select(func.count()).select_from(Location).where(Location.org_id == org_id, Location.id.in_(ids))
        )
    ).scalar_one()
    if found != len(set(ids)):
        raise ApiError(422, "invalid_reference", "location_ids contain unknown locations")
    return [str(i) for i in ids]


async def _owners(session: Session, org_id: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(Membership).where(Membership.org_id == org_id, Membership.role == "owner")
        )
    ).scalar_one()


@router.get("", response_model=OrgOut, operation_id="getOrg")
async def get_org(session: Session, p: Viewer) -> Org:
    """Current organisation."""
    return (await session.execute(select(Org).where(Org.id == p.org_id))).scalar_one()


@router.patch("", response_model=OrgOut, operation_id="updateOrg")
async def update_org(body: OrgPatch, request: Request, session: Session, p: Owner) -> Org:
    """Update org settings."""
    org = (await session.execute(select(Org).where(Org.id == p.org_id))).scalar_one()
    before = {"name": org.name, "currency": org.currency, "timezone": org.timezone, "region": org.region}
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(org, k, v)  # cost_params is a validated dict (see CostParams)
    write_audit(
        session,
        org_id=p.org_id,
        user_id=p.user.id,
        action="update",
        entity="orgs",
        entity_id=org.id,
        before=before,
        after=body.model_dump(exclude_unset=True),
        ip=_ip(request),
    )
    await session.commit()
    return org


@router.get("/members", response_model=list[MemberOut], operation_id="listMembers")
async def list_members(session: Session, p: Owner) -> list[MemberOut]:
    """Members of this org."""
    rows = (
        await session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.org_id == p.org_id)
            .order_by(User.email)
        )
    ).all()
    return [
        MemberOut(
            membership_id=m.id, user_id=u.id, email=u.email, name=u.name, role=m.role, location_ids=m.location_ids
        )
        for m, u in rows
    ]


@router.post("/members", response_model=MemberOut, status_code=201, operation_id="addMember")
async def add_member(body: MemberIn, request: Request, session: Session, p: Owner) -> MemberOut:
    """Add a member: attaches an existing user by email, or creates one (name + password required)."""
    email = body.email.lower()
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        if not body.password or not body.name:
            raise ApiError(422, "validation_error", "name and password are required for a new user")
        user = User(email=email, password_hash=await asyncio.to_thread(hash_password, body.password), name=body.name)
        session.add(user)
        await session.flush()
    elif (
        await session.execute(select(Membership.id).where(Membership.user_id == user.id, Membership.org_id == p.org_id))
    ).first():
        raise ApiError(409, "already_member", "User is already a member")
    m = Membership(
        user_id=user.id,
        org_id=p.org_id,
        role=body.role,
        location_ids=await _scope(session, p.org_id, body.location_ids),
    )
    session.add(m)
    await session.flush()
    write_audit(
        session,
        org_id=p.org_id,
        user_id=p.user.id,
        action="create",
        entity="memberships",
        entity_id=m.id,
        after={"email": email, "role": body.role},
        ip=_ip(request),
    )
    await session.commit()
    return MemberOut(
        membership_id=m.id, user_id=user.id, email=user.email, name=user.name, role=m.role, location_ids=m.location_ids
    )


@router.patch("/members/{membership_id}", response_model=MemberOut, operation_id="updateMember")
async def update_member(
    membership_id: uuid.UUID, body: MemberPatch, request: Request, session: Session, p: Owner
) -> MemberOut:
    """Change a member's role / store scope (cannot demote the last owner)."""
    m = (
        await session.execute(select(Membership).where(Membership.id == membership_id, Membership.org_id == p.org_id))
    ).scalar_one_or_none()
    if m is None:
        raise ApiError(404, "not_found", "member not found")
    data = body.model_dump(exclude_unset=True)
    if "role" in data and m.role == "owner" and data["role"] != "owner" and await _owners(session, p.org_id) <= 1:
        raise ApiError(409, "last_owner", "An organisation needs at least one owner")
    before = {"role": m.role, "location_ids": m.location_ids}
    if "role" in data:
        m.role = data["role"]
    if "location_ids" in data:
        m.location_ids = await _scope(session, p.org_id, data["location_ids"])
    write_audit(
        session,
        org_id=p.org_id,
        user_id=p.user.id,
        action="update",
        entity="memberships",
        entity_id=m.id,
        before=before,
        after={"role": m.role, "location_ids": m.location_ids},
        ip=_ip(request),
    )
    await session.commit()
    u = (await session.execute(select(User).where(User.id == m.user_id))).scalar_one()
    return MemberOut(
        membership_id=m.id, user_id=u.id, email=u.email, name=u.name, role=m.role, location_ids=m.location_ids
    )


@router.delete("/members/{membership_id}", status_code=204, operation_id="removeMember")
async def remove_member(membership_id: uuid.UUID, request: Request, session: Session, p: Owner) -> None:
    """Remove a member (cannot remove the last owner)."""
    m = (
        await session.execute(select(Membership).where(Membership.id == membership_id, Membership.org_id == p.org_id))
    ).scalar_one_or_none()
    if m is None:
        raise ApiError(404, "not_found", "member not found")
    if m.role == "owner" and await _owners(session, p.org_id) <= 1:
        raise ApiError(409, "last_owner", "An organisation needs at least one owner")
    await session.delete(m)
    write_audit(
        session,
        org_id=p.org_id,
        user_id=p.user.id,
        action="delete",
        entity="memberships",
        entity_id=membership_id,
        before={"role": m.role},
        ip=_ip(request),
    )
    await session.commit()
