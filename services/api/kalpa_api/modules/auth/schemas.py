"""Auth schemas."""

import uuid

from pydantic import BaseModel, EmailStr, Field


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    org_name: str = Field(min_length=1, max_length=200)
    currency: str = Field(default="INR", pattern="^(INR|USD)$")


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    org_id: uuid.UUID | None = None


class SwitchOrgIn(BaseModel):
    org_id: uuid.UUID


class OrgBrief(BaseModel):
    id: uuid.UUID
    name: str
    role: str


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    name: str
    org_id: uuid.UUID
    role: str
    permissions: list[str]
    location_ids: list[str] | None
    orgs: list[OrgBrief]
