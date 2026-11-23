"""Transactional POS checkout."""

import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from kalpa_api.core.audit import write_audit
from kalpa_api.core.deps import Principal
from kalpa_api.core.errors import ApiError
from kalpa_api.core.events import publish_event
from kalpa_api.db.models import IdempotencyKey, InventoryPosition, Location, Org, Product, SalesDaily
from kalpa_api.modules.pos.schemas import CheckoutIn, PosLineOut, Receipt

CENT = Decimal("0.01")


def _q(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def price_lines(body: CheckoutIn) -> tuple[list[PosLineOut], Decimal, Decimal, Decimal, Decimal]:
    """Compute per-line and total amounts (Decimal, banker-safe half-up to paise)."""
    out: list[PosLineOut] = []
    sub = disc = tax = total = Decimal(0)
    for ln in body.lines:
        gross = ln.unit_price * ln.qty
        d = _q(gross * ln.discount_pct / 100)
        taxable = gross - d
        g = _q(taxable * ln.gst_pct / 100)
        t = _q(taxable + g)
        out.append(
            PosLineOut(
                product_id=ln.product_id,
                qty=ln.qty,
                unit_price=float(ln.unit_price),
                discount=float(d),
                taxable=float(_q(taxable)),
                gst=float(g),
                total=float(t),
            )
        )
        sub, disc, tax, total = sub + _q(gross), disc + d, tax + g, total + t
    return out, sub, disc, tax, total


def _hash(body: CheckoutIn) -> str:
    return hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


async def _add_sale(
    session: AsyncSession, org_id: uuid.UUID, loc: uuid.UUID, prod: uuid.UUID, day: object, qty: int, revenue: float
) -> None:
    """Add to today's sales_daily row (update-first, insert on miss, retry on race)."""
    cond = (
        SalesDaily.org_id == org_id,
        SalesDaily.location_id == loc,
        SalesDaily.product_id == prod,
        SalesDaily.date == day,
    )
    upd = update(SalesDaily).where(*cond).values(units=SalesDaily.units + qty, revenue=SalesDaily.revenue + revenue)
    if (await session.execute(upd)).rowcount:  # type: ignore[attr-defined]
        return
    try:
        async with session.begin_nested():
            session.add(
                SalesDaily(
                    org_id=org_id,
                    location_id=loc,
                    product_id=prod,
                    date=day,
                    units=qty,
                    revenue=revenue,
                    avg_price=revenue / qty,
                    stockout_flag=False,
                )
            )
    except IntegrityError:  # another checkout inserted the row first
        await session.execute(upd)


async def checkout(
    session: AsyncSession,
    redis: Redis,
    p: Principal,
    body: CheckoutIn,
    key: str,
    ip: str | None,
    sale_date: date | None = None,
    simulated: bool = False,
) -> Receipt:
    """Atomically sell: idempotency key + stock decrement + sales_daily + audit; then publish events.

    `sale_date` / `simulated` are for the live simulator only (a simulated business day); the HTTP route never sets
    them, so real POS sales always land on the organisation's current date.
    """
    h = _hash(body)
    try:
        async with session.begin_nested():
            session.add(IdempotencyKey(org_id=p.org_id, key=key, request_hash=h))
    except IntegrityError:
        prior = (
            await session.execute(
                select(IdempotencyKey).where(IdempotencyKey.org_id == p.org_id, IdempotencyKey.key == key)
            )
        ).scalar_one()
        if prior.request_hash != h:
            raise ApiError(422, "idempotency_key_reuse", "Idempotency-Key was used with a different request") from None
        if prior.response is None:
            raise ApiError(409, "in_progress", "A request with this key is still processing") from None
        return Receipt.model_validate(prior.response)

    if not p.can_access_location(body.location_id):
        raise ApiError(403, "forbidden", "No access to this location")
    loc = (
        await session.execute(
            select(Location.id, Location.name).where(Location.id == body.location_id, Location.org_id == p.org_id)
        )
    ).first()
    if loc is None:
        raise ApiError(404, "not_found", "location not found")
    ids = {ln.product_id for ln in body.lines}
    found = {
        r.id: r
        for r in (
            await session.execute(
                select(Product.id, Product.name, Product.sku).where(Product.org_id == p.org_id, Product.id.in_(ids))
            )
        ).all()
    }
    if set(found) != ids:
        raise ApiError(404, "not_found", "product not found")

    lines, sub, disc, tax, total = price_lines(body)
    qty_by_product: dict[uuid.UUID, int] = {}
    rev_by_product: dict[uuid.UUID, float] = {}
    for ln, out in zip(body.lines, lines, strict=True):
        qty_by_product[ln.product_id] = qty_by_product.get(ln.product_id, 0) + ln.qty
        rev_by_product[ln.product_id] = rev_by_product.get(ln.product_id, 0.0) + out.taxable
    stock_after: dict[uuid.UUID, tuple[int, int | None]] = {}  # product -> (on_hand, reorder_point) after this sale
    for prod, qty in sorted(qty_by_product.items(), key=lambda kv: str(kv[0])):  # stable order avoids deadlocks
        res = await session.execute(
            update(InventoryPosition)
            .where(
                InventoryPosition.org_id == p.org_id,
                InventoryPosition.location_id == body.location_id,
                InventoryPosition.product_id == prod,
                InventoryPosition.on_hand >= qty,
            )
            .values(on_hand=InventoryPosition.on_hand - qty)
            .returning(InventoryPosition.on_hand, InventoryPosition.reorder_point)
        )
        row = res.first()
        if row is None:
            await session.rollback()  # discards the idempotency key too, so the client may retry later
            raise ApiError(
                409, "insufficient_stock", "Not enough stock for one or more lines", {"product_id": str(prod)}
            )
        stock_after[prod] = (int(row[0]), None if row[1] is None else int(row[1]))

    tz = (await session.execute(select(Org.timezone).where(Org.id == p.org_id))).scalar_one()
    today = sale_date or datetime.now(ZoneInfo(tz)).date()
    for prod, qty in qty_by_product.items():
        await _add_sale(session, p.org_id, body.location_id, prod, today, qty, rev_by_product[prod])

    receipt = Receipt(
        receipt_id=uuid.uuid4(),
        location_id=body.location_id,
        payment_method=body.payment_method,
        lines=lines,
        subtotal=float(sub),
        discount=float(disc),
        gst=float(tax),
        total=float(total),
        created_at=datetime.now(UTC),
    )
    idem = (
        await session.execute(
            select(IdempotencyKey).where(IdempotencyKey.org_id == p.org_id, IdempotencyKey.key == key)
        )
    ).scalar_one()
    idem.response = json.loads(receipt.model_dump_json())
    write_audit(
        session,
        org_id=p.org_id,
        user_id=p.user.id,
        action="pos.checkout",
        entity="receipt",
        entity_id=receipt.receipt_id,
        after={"total": receipt.total, "lines": len(lines), **({"simulated": True} if simulated else {})},
        ip=ip,
    )
    await session.commit()
    revenue = round(sum(rev_by_product.values()), 2)
    await publish_event(
        redis,
        p.org_id,
        {
            "type": "sale",
            "receipt_id": str(receipt.receipt_id),
            "location_id": str(body.location_id),
            "location": loc.name,
            "date": today.isoformat(),
            "total": receipt.total,
            "revenue": revenue,
            "units": sum(qty_by_product.values()),
            "simulated": simulated,
            "stock": [
                {
                    "product_id": str(prod),
                    "product": found[prod].name,
                    "sku": found[prod].sku,
                    "on_hand": oh,
                    "reorder_point": rop,
                }
                for prod, (oh, rop) in stock_after.items()
            ],
        },
    )
    for prod, (
        on_hand,
        rop,
    ) in stock_after.items():  # alert exactly when this sale moved stock across the reorder point
        if rop is not None and on_hand + qty_by_product[prod] > rop >= on_hand:
            await publish_event(
                redis,
                p.org_id,
                {
                    "type": "alert",
                    "kind": "reorder",
                    "location_id": str(body.location_id),
                    "location": loc.name,
                    "product_id": str(prod),
                    "product": found[prod].name,
                    "sku": found[prod].sku,
                    "on_hand": on_hand,
                    "reorder_point": rop,
                    "simulated": simulated,
                },
            )
    return receipt
