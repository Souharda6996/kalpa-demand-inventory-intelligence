"""Org + member schemas."""

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class CostParams(BaseModel):
    """Decision-engine assumptions (holding cost, stock-out penalty, review period)."""

    holding_pct_year: float = Field(ge=0, le=2)
    stockout_penalty_ratio: float = Field(ge=0, le=20)
    review_days: int = Field(ge=1, le=28)


class OrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    slug: str
    currency: str
    timezone: str
    region: str
    cost_params: dict[str, float] | None = None


class OrgPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    currency: str | None = Field(default=None, pattern="^(INR|USD)$")
    timezone: str | None = None
    region: str | None = None
    cost_params: CostParams | None = None


class MemberOut(BaseModel):
    membership_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    name: str
    role: str
    location_ids: list[str] | None


class MemberIn(BaseModel):
    email: EmailStr
    role: str = Field(pattern="^(owner|planner|store_manager|viewer)$")
    name: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, min_length=10, max_length=128)  # required for new users
    location_ids: list[uuid.UUID] | None = None


class MemberPatch(BaseModel):
    role: str | None = Field(default=None, pattern="^(owner|planner|store_manager|viewer)$")
    location_ids: list[uuid.UUID] | None = None
