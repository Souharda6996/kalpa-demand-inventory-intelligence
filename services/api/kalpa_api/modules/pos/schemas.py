"""POS schemas."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class PosLineIn(BaseModel):
    product_id: uuid.UUID
    qty: int = Field(ge=1, le=1000)
    unit_price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    discount_pct: Decimal = Field(default=Decimal(0), ge=0, le=100)
    gst_pct: Decimal = Field(default=Decimal(5), ge=0, le=28)


class CheckoutIn(BaseModel):
    location_id: uuid.UUID
    lines: list[PosLineIn] = Field(min_length=1, max_length=100)
    payment_method: str = Field(default="cash", pattern="^(cash|card|upi)$")


class PosLineOut(BaseModel):
    product_id: uuid.UUID
    qty: int
    unit_price: float
    discount: float
    taxable: float
    gst: float
    total: float


class Receipt(BaseModel):
    receipt_id: uuid.UUID
    location_id: uuid.UUID
    payment_method: str
    lines: list[PosLineOut]
    subtotal: float
    discount: float
    gst: float
    total: float
    created_at: datetime
