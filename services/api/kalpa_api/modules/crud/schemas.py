"""Schemas for master-data resources (create / patch / out)."""

import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


class _Patch(BaseModel):
    """Base for PATCH bodies: every field optional."""


# --- locations
class LocationIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str = Field(pattern="^(store|dc)$")
    city: str | None = None
    state: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    capacity_units: int | None = Field(default=None, ge=0)


class LocationPatch(_Patch):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    type: str | None = Field(default=None, pattern="^(store|dc)$")
    city: str | None = None
    state: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    capacity_units: int | None = Field(default=None, ge=0)


class LocationOut(_Out, LocationIn):
    pass


# --- suppliers
class SupplierIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    contact_email: str | None = None
    default_lead_time_days: int = Field(default=7, ge=0, le=365)
    reliability_score: float = Field(default=0.95, ge=0, le=1)


class SupplierPatch(_Patch):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    contact_email: str | None = None
    default_lead_time_days: int | None = Field(default=None, ge=0, le=365)
    reliability_score: float | None = Field(default=None, ge=0, le=1)


class SupplierOut(_Out, SupplierIn):
    pass


# --- products
class ProductIn(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=300)
    category: str = Field(min_length=1, max_length=100)
    dept: str | None = None
    uom: str = "unit"
    price_tier: str | None = None
    launch_date: date | None = None
    shelf_life_days: int | None = Field(default=None, ge=0)


class ProductPatch(_Patch):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    dept: str | None = None
    uom: str | None = None
    price_tier: str | None = None
    launch_date: date | None = None
    shelf_life_days: int | None = Field(default=None, ge=0)


class ProductOut(_Out, ProductIn):
    pass


# --- supplier_products
class SupplierProductIn(BaseModel):
    supplier_id: uuid.UUID
    product_id: uuid.UUID
    unit_cost: float = Field(ge=0)
    lead_time_days: int = Field(ge=0, le=365)
    moq: int = Field(default=1, ge=1)
    pack_size: int = Field(default=1, ge=1)


class SupplierProductPatch(_Patch):
    unit_cost: float | None = Field(default=None, ge=0)
    lead_time_days: int | None = Field(default=None, ge=0, le=365)
    moq: int | None = Field(default=None, ge=1)
    pack_size: int | None = Field(default=None, ge=1)


class SupplierProductOut(_Out, SupplierProductIn):
    pass


# --- inventory positions
class InventoryIn(BaseModel):
    location_id: uuid.UUID
    product_id: uuid.UUID
    on_hand: int = Field(default=0, ge=0)
    on_order: int = Field(default=0, ge=0)
    backorder: int = Field(default=0, ge=0)
    reorder_point: int | None = Field(default=None, ge=0)
    order_up_to: int | None = Field(default=None, ge=0)


class InventoryPatch(_Patch):
    on_hand: int | None = Field(default=None, ge=0)
    on_order: int | None = Field(default=None, ge=0)
    backorder: int | None = Field(default=None, ge=0)
    reorder_point: int | None = Field(default=None, ge=0)
    order_up_to: int | None = Field(default=None, ge=0)


class InventoryOut(_Out, InventoryIn):
    pass


# --- promotions
class PromotionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    product_id: uuid.UUID | None = None
    category: str | None = None
    location_id: uuid.UUID | None = None
    start_date: date
    end_date: date
    discount_pct: float = Field(ge=0, le=100)


class PromotionPatch(_Patch):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    discount_pct: float | None = Field(default=None, ge=0, le=100)


class PromotionOut(_Out, PromotionIn):
    pass
