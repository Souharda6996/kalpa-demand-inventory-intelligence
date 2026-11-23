"""Purchase orders: draft -> approved -> sent -> partially_received -> received (or cancelled).

Sending a PO raises on_order; receiving moves units from on_order to on_hand; cancelling releases the open remainder.
Every transition is role-gated, validated, audited and tenant-scoped.
"""

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kalpa_api.core.audit import write_audit
from kalpa_api.core.deps import Principal, Session, require
from kalpa_api.core.errors import ApiError
from kalpa_api.db.models import InventoryPosition, Location, PoLine, Product, PurchaseOrder, Supplier, SupplierProduct

router = APIRouter(prefix="/purchase-orders", tags=["purchase-orders"])
Creator = Annotated[Principal, Depends(require("create_po"))]
Approver = Annotated[Principal, Depends(require("approve_po"))]
Receiver = Annotated[Principal, Depends(require("receive_po"))]
Viewer = Annotated[Principal, Depends(require("view"))]

STATUSES = ("draft", "approved", "sent", "partially_received", "received", "cancelled")


class LineIn(BaseModel):
    product_id: uuid.UUID
    qty: int = Field(ge=1, le=1_000_000)
    reason: dict[str, Any] | None = None  # e.g. {"chips": ["Diwali in 9 days", "lead time 6 d"]}


class PoIn(BaseModel):
    supplier_id: uuid.UUID
    location_id: uuid.UUID
    expected_date: date | None = None
    lines: list[LineIn] = Field(min_length=1, max_length=500)


class LineOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    product: str
    qty: int
    unit_cost: float
    received_qty: int
    reason: dict[str, Any] | None


class PoOut(BaseModel):
    id: uuid.UUID
    supplier_id: uuid.UUID
    supplier: str
    location_id: uuid.UUID
    location: str
    status: str
    expected_date: date | None
    total_cost: float
    created_at: datetime
    lines: list[LineOut]


class ReceiveLine(BaseModel):
    line_id: uuid.UUID
    qty: int = Field(ge=1)


class ReceiveIn(BaseModel):
    lines: list[ReceiveLine] = Field(min_length=1)


async def _load(session: AsyncSession, p: Principal, po_id: uuid.UUID) -> PurchaseOrder:
    po = (
        await session.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id, PurchaseOrder.org_id == p.org_id))
    ).scalar_one_or_none()
    if po is None or not p.can_access_location(po.location_id):
        raise ApiError(404, "not_found", "purchase order not found")
    return po


async def _out(session: AsyncSession, po: PurchaseOrder) -> PoOut:
    lines = (
        await session.execute(
            select(PoLine, Product)
            .join(Product, Product.id == PoLine.product_id)
            .where(PoLine.po_id == po.id)
            .order_by(Product.sku)
        )
    ).all()
    sup = (await session.execute(select(Supplier.name).where(Supplier.id == po.supplier_id))).scalar_one()
    loc = (await session.execute(select(Location.name).where(Location.id == po.location_id))).scalar_one()
    return PoOut(
        id=po.id,
        supplier_id=po.supplier_id,
        supplier=sup,
        location_id=po.location_id,
        location=loc,
        status=po.status,
        expected_date=po.expected_date,
        total_cost=po.total_cost,
        created_at=po.created_at,
        lines=[
            LineOut(
                id=ln.id,
                product_id=ln.product_id,
                sku=pr.sku,
                product=pr.name,
                qty=ln.qty,
                unit_cost=ln.unit_cost,
                received_qty=ln.received_qty,
                reason=ln.reason,
            )
            for ln, pr in lines
        ],
    )


async def _position(session: AsyncSession, org_id: uuid.UUID, loc: uuid.UUID, prod: uuid.UUID) -> InventoryPosition:
    pos = (
        await session.execute(
            select(InventoryPosition).where(
                InventoryPosition.org_id == org_id,
                InventoryPosition.location_id == loc,
                InventoryPosition.product_id == prod,
            )
        )
    ).scalar_one_or_none()
    if pos is None:
        pos = InventoryPosition(org_id=org_id, location_id=loc, product_id=prod, on_hand=0, on_order=0, backorder=0)
        session.add(pos)
        await session.flush()
    return pos


def _transition(po: PurchaseOrder, allowed: tuple[str, ...], action: str) -> None:
    if po.status not in allowed:
        raise ApiError(
            409,
            "invalid_transition",
            f"Cannot {action} a purchase order that is '{po.status}'",
            {"status": po.status, "allowed_from": list(allowed)},
        )


def _audit(
    session: AsyncSession, p: Principal, request: Request, po: PurchaseOrder, action: str, before: str | None
) -> None:
    write_audit(
        session,
        org_id=p.org_id,
        user_id=p.user.id,
        action=f"po.{action}",
        entity="purchase_orders",
        entity_id=po.id,
        before={"status": before} if before else None,
        after={"status": po.status},
        ip=request.client.host if request.client else None,
    )


@router.post("", response_model=PoOut, status_code=201, operation_id="createPurchaseOrder")
async def create_po(body: PoIn, request: Request, session: Session, p: Creator) -> PoOut:
    """Create a draft PO. Quantities must respect the supplier's MOQ and pack size."""
    if not p.can_access_location(body.location_id):
        raise ApiError(403, "forbidden", "No access to this location")
    sup = (
        await session.execute(select(Supplier).where(Supplier.id == body.supplier_id, Supplier.org_id == p.org_id))
    ).scalar_one_or_none()
    loc = (
        await session.execute(select(Location).where(Location.id == body.location_id, Location.org_id == p.org_id))
    ).scalar_one_or_none()
    if sup is None or loc is None:
        raise ApiError(404, "not_found", "supplier or location not found")
    ids = [ln.product_id for ln in body.lines]
    if len(set(ids)) != len(ids):
        raise ApiError(422, "duplicate_line", "Each product may appear once per purchase order")
    terms = {
        sp.product_id: sp
        for sp in (
            await session.execute(
                select(SupplierProduct).where(
                    SupplierProduct.org_id == p.org_id,
                    SupplierProduct.supplier_id == sup.id,
                    SupplierProduct.product_id.in_(ids),
                )
            )
        ).scalars()
    }
    po = PurchaseOrder(
        org_id=p.org_id, supplier_id=sup.id, location_id=loc.id, status="draft", created_by=p.user.id, total_cost=0.0
    )
    session.add(po)
    await session.flush()
    total, lead = 0.0, 0
    for ln in body.lines:
        sp = terms.get(ln.product_id)
        if sp is None:
            raise ApiError(
                422,
                "not_supplied",
                "This supplier does not supply one of the products",
                {"product_id": str(ln.product_id)},
            )
        if ln.qty % sp.pack_size != 0 or ln.qty < sp.moq:
            raise ApiError(
                422,
                "pack_or_moq",
                f"Quantity must be a multiple of {sp.pack_size} and at least {sp.moq}",
                {"product_id": str(ln.product_id), "pack_size": sp.pack_size, "moq": sp.moq},
            )
        session.add(
            PoLine(
                org_id=p.org_id,
                po_id=po.id,
                product_id=ln.product_id,
                qty=ln.qty,
                unit_cost=sp.unit_cost,
                reason=ln.reason,
            )
        )
        total += ln.qty * sp.unit_cost
        lead = max(lead, sp.lead_time_days)
    po.total_cost = round(total, 2)
    po.expected_date = body.expected_date or (date.today() + timedelta(days=lead))
    _audit(session, p, request, po, "create", None)
    await session.commit()
    return await _out(session, po)


@router.get("", response_model=list[PoOut], operation_id="listPurchaseOrders")
async def list_pos(
    session: Session,
    p: Viewer,
    status: str | None = None,
    supplier_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[PoOut]:
    """Purchase orders (newest first), with lines."""
    q = select(PurchaseOrder).where(PurchaseOrder.org_id == p.org_id)
    if status:
        if status not in STATUSES:
            raise ApiError(422, "invalid_filter", "Unknown status")
        q = q.where(PurchaseOrder.status == status)
    if supplier_id:
        q = q.where(PurchaseOrder.supplier_id == supplier_id)
    if location_id:
        q = q.where(PurchaseOrder.location_id == location_id)
    if p.location_ids is not None:
        q = q.where(PurchaseOrder.location_id.in_([uuid.UUID(x) for x in p.location_ids]))
    pos = (await session.execute(q.order_by(PurchaseOrder.created_at.desc()).limit(limit))).scalars().all()
    return [await _out(session, po) for po in pos]


@router.get("/{po_id}", response_model=PoOut, operation_id="getPurchaseOrder")
async def get_po(po_id: uuid.UUID, session: Session, p: Viewer) -> PoOut:
    """One purchase order with lines."""
    return await _out(session, await _load(session, p, po_id))


@router.post("/{po_id}/approve", response_model=PoOut, operation_id="approvePurchaseOrder")
async def approve(po_id: uuid.UUID, request: Request, session: Session, p: Approver) -> PoOut:
    """draft -> approved (owner/planner only)."""
    po = await _load(session, p, po_id)
    _transition(po, ("draft",), "approve")
    po.status, po.approved_by = "approved", p.user.id
    _audit(session, p, request, po, "approve", "draft")
    await session.commit()
    return await _out(session, po)


@router.post("/{po_id}/send", response_model=PoOut, operation_id="sendPurchaseOrder")
async def send(po_id: uuid.UUID, request: Request, session: Session, p: Approver) -> PoOut:
    """approved -> sent; the ordered units become 'on order' at the location."""
    po = await _load(session, p, po_id)
    _transition(po, ("approved",), "send")
    for ln in (await session.execute(select(PoLine).where(PoLine.po_id == po.id))).scalars():
        (await _position(session, p.org_id, po.location_id, ln.product_id)).on_order += ln.qty
    po.status = "sent"
    _audit(session, p, request, po, "send", "approved")
    await session.commit()
    return await _out(session, po)


@router.post("/{po_id}/receive", response_model=PoOut, operation_id="receivePurchaseOrder")
async def receive(po_id: uuid.UUID, body: ReceiveIn, request: Request, session: Session, p: Receiver) -> PoOut:
    """sent / partially_received -> partially_received or received; on_hand rises, on_order falls."""
    po = await _load(session, p, po_id)
    _transition(po, ("sent", "partially_received"), "receive")
    lines = {ln.id: ln for ln in (await session.execute(select(PoLine).where(PoLine.po_id == po.id))).scalars()}
    for r in body.lines:
        ln = lines.get(r.line_id)
        if ln is None:
            raise ApiError(404, "not_found", "line not found on this purchase order")
        if r.qty > ln.qty - ln.received_qty:
            raise ApiError(
                422,
                "over_receipt",
                "Cannot receive more than is outstanding",
                {"line_id": str(ln.id), "outstanding": ln.qty - ln.received_qty},
            )
        pos = await _position(session, p.org_id, po.location_id, ln.product_id)
        pos.on_hand += r.qty
        pos.on_order = max(0, pos.on_order - r.qty)
        ln.received_qty += r.qty
    before = po.status
    po.status = "received" if all(ln.received_qty >= ln.qty for ln in lines.values()) else "partially_received"
    _audit(session, p, request, po, "receive", before)
    await session.commit()
    return await _out(session, po)


@router.post("/{po_id}/cancel", response_model=PoOut, operation_id="cancelPurchaseOrder")
async def cancel(po_id: uuid.UUID, request: Request, session: Session, p: Creator) -> PoOut:
    """Cancel an open PO; units still 'on order' are released."""
    po = await _load(session, p, po_id)
    _transition(po, ("draft", "approved", "sent", "partially_received"), "cancel")
    if po.status in ("sent", "partially_received"):
        for ln in (await session.execute(select(PoLine).where(PoLine.po_id == po.id))).scalars():
            pos = await _position(session, p.org_id, po.location_id, ln.product_id)
            pos.on_order = max(0, pos.on_order - (ln.qty - ln.received_qty))
    before, po.status = po.status, "cancelled"
    _audit(session, p, request, po, "cancel", before)
    await session.commit()
    return await _out(session, po)
