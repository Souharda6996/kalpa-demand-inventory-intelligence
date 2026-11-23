"""Declarative base and shared column mixins."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OrgScoped:
    """Mixin: every tenant table carries org_id (filtered on every query)."""

    @declared_attr
    def org_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805
        return mapped_column(Uuid, ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
