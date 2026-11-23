"""Public, unauthenticated, read-only endpoints. They expose ONLY the synthetic demo organisation."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import select

from kalpa_api.core.deps import Session, get_redis
from kalpa_api.core.errors import ApiError
from kalpa_api.db.models import Forecast, Location, Org, Product, SalesDaily
from kalpa_api.modules.forecasts.router import _latest_runs
from kalpa_api.modules.forecasts.schemas import ForecastPoint, HistoryPoint
from kalpa_api.scripts.seed import DEMO_SLUG

router = APIRouter(prefix="/public", tags=["public"])
CACHE_KEY = "kalpa:public:demo-forecast"
CACHE_SECONDS = 60
RATE_LIMIT_PER_MINUTE = 60


class DemoForecast(BaseModel):
    location: str
    product: str
    model: str
    history: list[HistoryPoint]
    points: list[ForecastPoint]
    note: str


@router.get("/demo-forecast", response_model=DemoForecast, operation_id="getDemoForecast")
async def demo_forecast(
    request: Request, session: Session, redis: Annotated[Redis, Depends(get_redis)]
) -> DemoForecast:
    """One series (Kaju Katli at Koramangala when present) from the synthetic demo tenant, for the landing page."""
    ip = request.client.host if request.client else "unknown"
    rl = f"kalpa:rl:public:{ip}"
    if await redis.incr(rl) == 1:
        await redis.expire(rl, 60)
    if int(await redis.get(rl) or 0) > RATE_LIMIT_PER_MINUTE:
        raise ApiError(429, "rate_limited", "Too many requests")
    cached = await redis.get(CACHE_KEY)
    if cached:
        return DemoForecast.model_validate_json(cached)

    org = (await session.execute(select(Org).where(Org.slug == DEMO_SLUG))).scalar_one_or_none()
    runs = await _latest_runs(session, org.id) if org else []
    if org is None or not runs:
        raise ApiError(404, "demo_unavailable", "The demo tenant has no forecasts yet")
    run = runs[0]  # best WAPE
    pick = (
        await session.execute(
            select(Forecast.location_id, Forecast.product_id)
            .join(Location, Location.id == Forecast.location_id)
            .join(Product, Product.id == Forecast.product_id)
            .where(Forecast.run_id == run.id)
            .order_by((Product.name == "Kaju Katli").desc(), (Location.name == "Koramangala").desc(), Product.sku)
            .limit(1)
        )
    ).first()
    if pick is None:
        raise ApiError(404, "demo_unavailable", "The demo tenant has no forecasts yet")
    loc_id, prod_id = pick
    hist = (
        await session.execute(
            select(SalesDaily.date, SalesDaily.units)
            .where(SalesDaily.org_id == org.id, SalesDaily.location_id == loc_id, SalesDaily.product_id == prod_id)
            .order_by(SalesDaily.date.desc())
            .limit(90)
        )
    ).all()
    pts = (
        await session.execute(
            select(Forecast.target_date, Forecast.p10, Forecast.p50, Forecast.p90)
            .where(Forecast.run_id == run.id, Forecast.location_id == loc_id, Forecast.product_id == prod_id)
            .order_by(Forecast.target_date)
        )
    ).all()
    loc = (await session.execute(select(Location.name).where(Location.id == loc_id))).scalar_one()
    prod = (await session.execute(select(Product.name).where(Product.id == prod_id))).scalar_one()
    out = DemoForecast(
        location=loc,
        product=prod,
        model=run.model_name,
        history=[HistoryPoint(date=d, units=u) for d, u in reversed(hist)],
        points=[ForecastPoint(date=d, p10=a, p50=b, p90=c) for d, a, b, c in pts],
        note="Synthetic demo data (not a real retailer).",
    )
    await redis.set(CACHE_KEY, out.model_dump_json(), ex=CACHE_SECONDS)
    return out
