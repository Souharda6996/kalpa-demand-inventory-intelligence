"""Audit log read endpoint (owner only)."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from kalpa_api.core.deps import Principal, Session, require
from kalpa_api.db.models import AuditLog
from kalpa_api.modules.crud.generic import Page

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    user_id: uuid.UUID | None
    action: str
    entity: str
    entity_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    ip: str | None
    created_at: datetime


@router.get("", response_model=Page[AuditOut], operation_id="listAudit")
async def list_audit(
    session: Session,
    p: Annotated[Principal, Depends(require("manage_org"))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    entity: str | None = None,
    action: str | None = None,
) -> Any:
    """Filterable audit trail for this org, newest first."""
    where = [AuditLog.org_id == p.org_id]
    if entity:
        where.append(AuditLog.entity == entity)
    if action:
        where.append(AuditLog.action == action)
    total = (await session.execute(select(func.count()).select_from(AuditLog).where(*where))).scalar_one()
    rows = (
        (
            await session.execute(
                select(AuditLog)
                .where(*where)
                .order_by(AuditLog.created_at.desc(), AuditLog.id)
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        )
        .scalars()
        .all()
    )
    return {"items": rows, "total": total, "page": page, "page_size": page_size}
