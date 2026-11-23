"""ORM models for every table in Part D2 (portable types; JSON works on Postgres + SQLite)."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import Index as Idx
from sqlalchemy import false as sa_false
from sqlalchemy.orm import Mapped, mapped_column

from kalpa_api.db.base import Base, OrgScoped

UUIDFK = Uuid


def fk(table: str) -> Mapped[uuid.UUID]:
    """Required FK column to <table>.id."""
    return mapped_column(UUIDFK, ForeignKey(f"{table}.id", ondelete="CASCADE"), index=True)


def opt_fk(table: str) -> Mapped[uuid.UUID | None]:
    """Optional FK column to <table>.id."""
    return mapped_column(UUIDFK, ForeignKey(f"{table}.id", ondelete="SET NULL"), nullable=True)


class Org(Base):
    __tablename__ = "orgs"
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    timezone: Mapped[str] = mapped_column(String(60), default="Asia/Kolkata")
    region: Mapped[str] = mapped_column(String(60), default="IN")
    # decision-engine assumptions: holding_pct_year, stockout_penalty_ratio, review_days (see policy.inputs)
    cost_params: Mapped[dict[str, float] | None] = mapped_column(JSON, nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())  # live simulator allowed


class User(Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True)  # lower-cased by the service
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "org_id"),)
    user_id: Mapped[uuid.UUID] = fk("users")
    org_id: Mapped[uuid.UUID] = fk("orgs")
    role: Mapped[str] = mapped_column(String(20))  # owner|planner|store_manager|viewer
    # store_manager scope: list of location ids (str); None = all locations
    location_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)


class Invite(OrgScoped, Base):
    """Pending invitation. Only a SHA-256 hash of the token is stored; the token is shown once at creation."""

    __tablename__ = "invites"
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(20))
    location_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = opt_fk("users")


class IdempotencyKey(OrgScoped, Base):
    """POS idempotency: same (org, key) returns the stored response instead of re-selling."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("org_id", "key"),)
    key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)


class Location(OrgScoped, Base):
    __tablename__ = "locations"
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(10))  # store|dc
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    capacity_units: Mapped[int | None] = mapped_column(Integer)
    external_id: Mapped[str | None] = mapped_column(String(100))


class Supplier(OrgScoped, Base):
    __tablename__ = "suppliers"
    name: Mapped[str] = mapped_column(String(200))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    default_lead_time_days: Mapped[int] = mapped_column(Integer, default=7)
    reliability_score: Mapped[float] = mapped_column(Float, default=0.95)


class Product(OrgScoped, Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("org_id", "sku"), Idx("ix_products_org_cat", "org_id", "category"))
    sku: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(100))
    dept: Mapped[str | None] = mapped_column(String(100))
    uom: Mapped[str] = mapped_column(String(20), default="unit")
    price_tier: Mapped[str | None] = mapped_column(String(20))
    launch_date: Mapped[date | None] = mapped_column(Date)
    shelf_life_days: Mapped[int | None] = mapped_column(Integer)


class SupplierProduct(OrgScoped, Base):
    __tablename__ = "supplier_products"
    __table_args__ = (UniqueConstraint("supplier_id", "product_id"),)
    supplier_id: Mapped[uuid.UUID] = fk("suppliers")
    product_id: Mapped[uuid.UUID] = fk("products")
    unit_cost: Mapped[float] = mapped_column(Float)
    lead_time_days: Mapped[int] = mapped_column(Integer)
    moq: Mapped[int] = mapped_column(Integer, default=1)
    pack_size: Mapped[int] = mapped_column(Integer, default=1)


class InventoryPosition(OrgScoped, Base):
    __tablename__ = "inventory_positions"
    __table_args__ = (UniqueConstraint("location_id", "product_id"),)
    location_id: Mapped[uuid.UUID] = fk("locations")
    product_id: Mapped[uuid.UUID] = fk("products")
    on_hand: Mapped[int] = mapped_column(Integer, default=0)
    on_order: Mapped[int] = mapped_column(Integer, default=0)
    backorder: Mapped[int] = mapped_column(Integer, default=0)
    reorder_point: Mapped[int | None] = mapped_column(Integer)
    order_up_to: Mapped[int | None] = mapped_column(Integer)


class SalesDaily(OrgScoped, Base):
    __tablename__ = "sales_daily"
    __table_args__ = (UniqueConstraint("org_id", "location_id", "product_id", "date"),)
    location_id: Mapped[uuid.UUID] = fk("locations")
    product_id: Mapped[uuid.UUID] = fk("products")
    date: Mapped[date] = mapped_column(Date, index=True)
    units: Mapped[int] = mapped_column(Integer)
    revenue: Mapped[float] = mapped_column(Float, default=0.0)
    avg_price: Mapped[float | None] = mapped_column(Float)
    stockout_flag: Mapped[bool] = mapped_column(Boolean, default=False)


class Price(OrgScoped, Base):
    __tablename__ = "prices"
    product_id: Mapped[uuid.UUID] = fk("products")
    location_id: Mapped[uuid.UUID | None] = opt_fk("locations")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    price: Mapped[float] = mapped_column(Float)


class Promotion(OrgScoped, Base):
    __tablename__ = "promotions"
    name: Mapped[str] = mapped_column(String(200))
    product_id: Mapped[uuid.UUID | None] = opt_fk("products")
    category: Mapped[str | None] = mapped_column(String(100))
    location_id: Mapped[uuid.UUID | None] = opt_fk("locations")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    discount_pct: Mapped[float] = mapped_column(Float)


class CalendarEvent(OrgScoped, Base):
    __tablename__ = "calendar_events"
    date: Mapped[date] = mapped_column(Date, index=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(20))  # festival|holiday|event|snap
    region: Mapped[str | None] = mapped_column(String(60))


class WeatherDaily(OrgScoped, Base):
    __tablename__ = "weather_daily"
    __table_args__ = (UniqueConstraint("location_id", "date"),)
    location_id: Mapped[uuid.UUID] = fk("locations")
    date: Mapped[date] = mapped_column(Date)
    temp_max: Mapped[float | None] = mapped_column(Float)
    temp_min: Mapped[float | None] = mapped_column(Float)
    precip_mm: Mapped[float | None] = mapped_column(Float)


class PurchaseOrder(OrgScoped, Base):
    __tablename__ = "purchase_orders"
    supplier_id: Mapped[uuid.UUID] = fk("suppliers")
    location_id: Mapped[uuid.UUID] = fk("locations")
    status: Mapped[str] = mapped_column(String(30), default="draft")
    expected_date: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[uuid.UUID | None] = opt_fk("users")
    approved_by: Mapped[uuid.UUID | None] = opt_fk("users")
    total_cost: Mapped[float] = mapped_column(Float, default=0.0)


class PoLine(OrgScoped, Base):
    __tablename__ = "po_lines"
    po_id: Mapped[uuid.UUID] = fk("purchase_orders")
    product_id: Mapped[uuid.UUID] = fk("products")
    qty: Mapped[int] = mapped_column(Integer)
    unit_cost: Mapped[float] = mapped_column(Float)
    received_qty: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[dict[str, object] | None] = mapped_column(JSON)


class ModelRun(OrgScoped, Base):
    __tablename__ = "model_runs"
    model_name: Mapped[str] = mapped_column(String(50))
    version: Mapped[str] = mapped_column(String(50))
    config: Mapped[dict[str, object] | None] = mapped_column(JSON)
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    metrics: Mapped[dict[str, object] | None] = mapped_column(JSON)
    is_champion: Mapped[bool] = mapped_column(Boolean, default=False)
    scope: Mapped[str] = mapped_column(String(100), default="all")


class Forecast(OrgScoped, Base):
    __tablename__ = "forecasts"
    __table_args__ = (Idx("ix_forecasts_key", "org_id", "location_id", "product_id", "target_date"),)
    run_id: Mapped[uuid.UUID] = fk("model_runs")
    location_id: Mapped[uuid.UUID] = fk("locations")
    product_id: Mapped[uuid.UUID] = fk("products")
    target_date: Mapped[date] = mapped_column(Date)
    p10: Mapped[float] = mapped_column(Float)
    p50: Mapped[float] = mapped_column(Float)
    p90: Mapped[float] = mapped_column(Float)
    p10_cal: Mapped[float | None] = mapped_column(Float)
    p90_cal: Mapped[float | None] = mapped_column(Float)
    level: Mapped[str] = mapped_column(String(20), default="sku")


class ForecastExplanation(OrgScoped, Base):
    __tablename__ = "forecast_explanations"
    run_id: Mapped[uuid.UUID] = fk("model_runs")
    location_id: Mapped[uuid.UUID] = fk("locations")
    product_id: Mapped[uuid.UUID] = fk("products")
    static_w: Mapped[dict[str, object] | None] = mapped_column(JSON)
    encoder_w: Mapped[dict[str, object] | None] = mapped_column(JSON)
    decoder_w: Mapped[dict[str, object] | None] = mapped_column(JSON)
    attention: Mapped[dict[str, object] | None] = mapped_column(JSON)


class BacktestResult(OrgScoped, Base):
    __tablename__ = "backtest_results"
    run_id: Mapped[uuid.UUID] = fk("model_runs")
    fold: Mapped[int] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(String(20))
    key: Mapped[str] = mapped_column(String(200))
    wape: Mapped[float | None] = mapped_column(Float)
    mase: Mapped[float | None] = mapped_column(Float)
    bias: Mapped[float | None] = mapped_column(Float)
    pinball: Mapped[float | None] = mapped_column(Float)
    coverage80: Mapped[float | None] = mapped_column(Float)
    cost: Mapped[float | None] = mapped_column(Float)
    fill_rate: Mapped[float | None] = mapped_column(Float)


class Scenario(OrgScoped, Base):
    __tablename__ = "scenarios"
    name: Mapped[str] = mapped_column(String(200))
    scope: Mapped[dict[str, object] | None] = mapped_column(JSON)
    changes: Mapped[dict[str, object] | None] = mapped_column(JSON)
    result: Mapped[dict[str, object] | None] = mapped_column(JSON)
    created_by: Mapped[uuid.UUID | None] = opt_fk("users")


class Job(OrgScoped, Base):
    __tablename__ = "jobs"
    type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    payload: Mapped[dict[str, object] | None] = mapped_column(JSON)
    result: Mapped[dict[str, object] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    log: Mapped[str | None] = mapped_column(Text)  # tail of progress messages, newest last
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Upload(OrgScoped, Base):
    """Raw uploaded file (medallion 'raw' layer); object path stored, not bytes."""

    __tablename__ = "uploads"
    filename: Mapped[str] = mapped_column(String(300))
    object_path: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="uploaded")  # uploaded|committed|rejected
    mapping: Mapped[dict[str, object] | None] = mapped_column(JSON)


class DataQualityReport(OrgScoped, Base):
    __tablename__ = "data_quality_reports"
    upload_id: Mapped[uuid.UUID | None] = opt_fk("uploads")
    rows_in: Mapped[int] = mapped_column(Integer)
    rows_out: Mapped[int] = mapped_column(Integer)
    issues: Mapped[dict[str, object] | None] = mapped_column(JSON)
    score: Mapped[float] = mapped_column(Float)


class AuditLog(OrgScoped, Base):
    __tablename__ = "audit_log"
    __table_args__ = (Idx("ix_audit_org_created", "org_id", "created_at"),)
    user_id: Mapped[uuid.UUID | None] = opt_fk("users")
    action: Mapped[str] = mapped_column(String(100))
    entity: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict[str, object] | None] = mapped_column(JSON)
    after: Mapped[dict[str, object] | None] = mapped_column(JSON)
    ip: Mapped[str | None] = mapped_column(String(64))
