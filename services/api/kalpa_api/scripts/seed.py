"""Idempotent seeds: `python -m kalpa_api.scripts.seed demo|benchmark`.

Each run wipes that tenant's ingested data and reloads it, so repeated runs give identical state.
"""

import argparse
import asyncio
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import cast

import polars as pl
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from kalpa_api.db.models import (
    CalendarEvent,
    DataQualityReport,
    InventoryPosition,
    Org,
    Supplier,
    SupplierProduct,
    WeatherDaily,
)
from kalpa_api.db.session import make_engine, make_sessionmaker
from kalpa_api.modules.data import ingest
from kalpa_ml.data.m5_loader import M5Subset, load_m5
from kalpa_ml.data.synth import (
    CATEGORIES,
    CATEGORY_SUPPLIER,
    SUPPLIERS,
    SynthConfig,
    generate,
    load_festivals,
    render_card,
)
from kalpa_ml.data.validate import curate_sales, quality_report, validate_sales

DEMO_SLUG, BENCH_SLUG = "bengaluru-freshmart", "m5-benchmark"


async def ensure_org(session: AsyncSession, slug: str, name: str, currency: str, region: str) -> uuid.UUID:
    """Get-or-create an org by slug."""
    org = (await session.execute(select(Org).where(Org.slug == slug))).scalar_one_or_none()
    if org is None:
        org = Org(name=name, slug=slug, currency=currency, region=region)
        session.add(org)
        await session.flush()
    org_id: uuid.UUID = org.id
    return org_id


async def seed_demo(session: AsyncSession, cfg: SynthConfig | None = None) -> dict[str, int]:
    """Seed the Bengaluru FreshMart demo tenant (synthetic, INR). Returns counts."""
    org_id = await ensure_org(session, DEMO_SLUG, "Bengaluru FreshMart (synthetic demo)", "INR", "IN")
    return await load_demo_data(session, org_id, cfg or SynthConfig(), wipe=True)


async def load_demo_data(session: AsyncSession, org_id: uuid.UUID, cfg: SynthConfig, *, wipe: bool) -> dict[str, int]:
    """Load the synthetic FreshMart dataset into an existing org (wipe=False keeps rows the user already made)."""
    data = generate(cfg)
    if wipe:
        await ingest.wipe_org_data(session, org_id)
    loc_ids = await ingest.ensure_locations(
        session, org_id, [{**r, "state": "Karnataka"} for r in data["stores"].iter_rows(named=True)]
    )
    cat = data["catalog"]
    sku_ids = await ingest.ensure_products(session, org_id, cat.select("sku", "name", "category").to_dicts())
    sup_ids: dict[str, uuid.UUID] = {}
    for name, lt0, rel in SUPPLIERS:
        s = Supplier(org_id=org_id, name=name, default_lead_time_days=lt0, reliability_score=rel)
        session.add(s)
        await session.flush()
        sup_ids[name] = s.id
    lead = {n: lt for n, lt, _ in SUPPLIERS}
    await session.execute(
        insert(SupplierProduct),
        [
            {
                "id": uuid.uuid4(),
                "org_id": org_id,
                "supplier_id": sup_ids[CATEGORY_SUPPLIER[r["category"]]],
                "product_id": sku_ids[r["sku"]],
                "unit_cost": round(r["price"] * r["cost_ratio"], 2),
                "lead_time_days": lead[CATEGORY_SUPPLIER[r["category"]]],
                "moq": 12,
                "pack_size": 6,
            }
            for r in cat.iter_rows(named=True)
        ],
    )
    sales = data["sales"]
    n = await ingest.load_sales(
        session, org_id, sales.select("location", "sku", "date", "units", "price"), loc_ids, sku_ids
    )
    last = cast(date, sales["date"].max())
    recent = (
        sales.filter(pl.col("date") > last - timedelta(days=28))
        .group_by(["location", "sku"])
        .agg((pl.col("units").mean() * 7).round().cast(pl.Int64).alias("oh"))
    )
    await session.execute(
        insert(InventoryPosition),
        [
            {
                "id": uuid.uuid4(),
                "org_id": org_id,
                "location_id": loc_ids[r["location"]],
                "product_id": sku_ids[r["sku"]],
                "on_hand": int(r["oh"]),
                "on_order": 0,
                "backorder": 0,
            }
            for r in recent.iter_rows(named=True)
        ],
    )
    fest = load_festivals()
    await session.execute(
        insert(CalendarEvent),
        [
            {"id": uuid.uuid4(), "org_id": org_id, "date": d, "name": nm, "type": "festival", "region": "IN"}
            for d, nm in fest.iter_rows()
        ],
    )
    w = data["weather"]
    for chunk in w.iter_slices(20_000):
        await session.execute(
            insert(WeatherDaily),
            [
                {
                    "id": uuid.uuid4(),
                    "org_id": org_id,
                    "location_id": loc_ids[r["location"]],
                    "date": r["date"],
                    "temp_max": r["temp_max"],
                    "precip_mm": r["precip_mm"],
                }
                for r in chunk.iter_rows(named=True)
            ],
        )
    rep = quality_report(sales, curate_sales(sales.select("location", "sku", "date", "units")))
    session.add(
        DataQualityReport(org_id=org_id, rows_in=rep.rows_in, rows_out=rep.rows_out, issues=rep.issues, score=rep.score)
    )
    org = await session.get(Org, org_id)
    if org is not None:
        org.is_demo = True  # the live store simulator is only offered on the demo tenant
    await session.commit()
    return {"sales_rows": n, "series": sales.select("location", "sku").unique().height, "skus": cat.height}


async def seed_benchmark(session: AsyncSession, raw_dir: Path, subset: M5Subset) -> dict[str, int]:
    """Seed the M5 benchmark tenant from Kaggle files in raw_dir."""
    m5 = load_m5(raw_dir, subset)
    sales = validate_sales(m5["sales"].drop("category", "dept").with_columns(pl.col("units")))
    meta = m5["sales"].select("sku", "category", "dept").unique(subset="sku")
    org_id = await ensure_org(session, BENCH_SLUG, "M5 Benchmark (Walmart, public dataset)", "USD", "US")
    await ingest.wipe_org_data(session, org_id)
    loc_ids = await ingest.ensure_locations(
        session, org_id, [{"name": s, "type": "store"} for s in sales["location"].unique()]
    )
    sku_ids = await ingest.ensure_products(session, org_id, meta.rename({}).to_dicts())
    n = await ingest.load_sales(
        session,
        org_id,
        curate_sales(sales).join(
            sales.select("location", "sku", "date", "price"), on=["location", "sku", "date"], how="left"
        ),
        loc_ids,
        sku_ids,
    )
    rep = quality_report(sales, curate_sales(sales.select("location", "sku", "date", "units")))
    session.add(
        DataQualityReport(org_id=org_id, rows_in=rep.rows_in, rows_out=rep.rows_out, issues=rep.issues, score=rep.score)
    )
    await session.commit()
    return {"sales_rows": n, "series": sales.select("location", "sku").unique().height}


async def forecast_demo(session: AsyncSession, models: list[str]) -> dict[str, object]:
    """Run a 1-fold backtest + 28-day future forecast for the demo tenant (powers the landing-page chart)."""
    from kalpa_api.db.models import Job
    from kalpa_api.modules.forecasts.service import execute_backtest

    org_id = await ensure_org(session, DEMO_SLUG, "Bengaluru FreshMart (synthetic demo)", "INR", "IN")
    job = Job(org_id=org_id, type="backtest", status="queued", payload={"models": models, "folds": 1})
    session.add(job)
    await session.commit()
    return await execute_backtest(session, org_id, job.id, models, 1)


async def _main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("target", choices=["demo", "benchmark", "forecast", "card"])
    p.add_argument(
        "--compact", action="store_true", help="demo: 6 stores x 100 SKUs x 2025 (fast) instead of the full set"
    )
    p.add_argument("--models", default="seasonal_naive,lightgbm_tweedie", help="forecast: comma-separated models")
    p.add_argument("--raw-dir", default="data/raw/m5")
    p.add_argument("--stores", default="", help="comma-separated M5 store ids (default all 10)")
    p.add_argument("--dept", default="FOODS_3")
    p.add_argument("--top-n", type=int, default=500)
    a = p.parse_args()
    if a.target == "card":
        out = Path("docs/GENERATOR_CARD.md")
        out.write_text(render_card(SynthConfig()), encoding="utf-8")
        print(f"wrote {out}")
        return
    engine = make_engine()
    async with make_sessionmaker(engine)() as session:
        if a.target == "demo":
            cfg = (
                SynthConfig(start="2025-01-01", end="2025-12-31", categories={c: 10 for c in CATEGORIES})
                if a.compact
                else None
            )
            print(await seed_demo(session, cfg))
        elif a.target == "forecast":
            print(await forecast_demo(session, [m for m in a.models.split(",") if m]))
        else:
            subset = M5Subset(stores=[s for s in a.stores.split(",") if s], dept=a.dept, top_n_skus=a.top_n)
            print(await seed_benchmark(session, Path(a.raw_dir), subset))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
