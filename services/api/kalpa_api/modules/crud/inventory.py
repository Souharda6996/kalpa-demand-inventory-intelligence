"""Inventory positions: scoped reads for store managers; writes for owner/planner."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from kalpa_api.core.deps import Principal, Session, require
from kalpa_api.core.errors import ApiError
from kalpa_api.core.repo import OrgRepo
from kalpa_api.db.models import InventoryPosition, Location, Product
from kalpa_api.modules.crud.generic import Page, verify_fks
from kalpa_api.modules.crud.schemas import InventoryIn, InventoryOut, InventoryPatch

router = APIRouter(prefix="/inventory", tags=["inventory"])
Read = Annotated[Principal, Depends(require("view"))]
Write = Annotated[Principal, Depends(require("edit_master_data"))]
_FKS: dict[str, Any] = {"location_id": Location, "product_id": Product}


def _repo(session: Session, p: Principal, request: Request) -> OrgRepo[InventoryPosition]:
    return OrgRepo(InventoryPosition, session, p.org_id, p.user.id, request.client.host if request.client else None)


@router.get("", response_model=Page[InventoryOut], operation_id="list_inventory")
async def list_inventory(
    request: Request,
    session: Session,
    p: Read,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    sort: str | None = None,
    location_id: uuid.UUID | None = None,
    category: str | None = None,
) -> Any:
    """Inventory list; store managers only see their own locations."""
    extra: list[Any] = []
    if location_id:
        extra.append(InventoryPosition.location_id == location_id)
    if category:
        extra.append(
            InventoryPosition.product_id.in_(
                select(Product.id).where(Product.org_id == p.org_id, Product.category == category)
            )
        )
    if p.location_ids is not None:
        extra.append(InventoryPosition.location_id.in_([uuid.UUID(x) for x in p.location_ids]))
    items, total = await _repo(session, p, request).list(
        page=page,
        page_size=page_size,
        sort=sort,
        sortable={"on_hand", "on_order", "created_at"},
        filters={},
        filterable=set(),
        extra=extra,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("", response_model=InventoryOut, status_code=201, operation_id="create_inventory")
async def create_inventory(body: InventoryIn, request: Request, session: Session, p: Write) -> Any:
    """Create a position (one per location+product)."""
    data = body.model_dump()
    await verify_fks(session, p.org_id, data, _FKS)
    dup = (
        await session.execute(
            select(InventoryPosition.id).where(
                InventoryPosition.org_id == p.org_id,
                InventoryPosition.location_id == body.location_id,
                InventoryPosition.product_id == body.product_id,
            )
        )
    ).first()
    if dup:
        raise ApiError(409, "duplicate", "Position already exists for this location and product")
    return await _repo(session, p, request).create(data)


@router.patch("/{item_id}", response_model=InventoryOut, operation_id="update_inventory")
async def update_inventory(
    item_id: uuid.UUID, body: InventoryPatch, request: Request, session: Session, p: Write
) -> Any:
    """Adjust a position."""
    return await _repo(session, p, request).update(item_id, body.model_dump(exclude_unset=True))
