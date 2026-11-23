"""Audit log writer (who / what / before-after) - called from the repository + auth/POS services."""

import uuid
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from kalpa_api.db.models import AuditLog

_SENSITIVE = {"password_hash", "password"}


def clean(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """JSON-safe copy with secrets removed."""
    if data is None:
        return None
    return {k: v for k, v in jsonable_encoder(data).items() if k not in _SENSITIVE}


def write_audit(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    action: str,
    entity: str,
    entity_id: object = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """Queue an audit row in the caller's transaction (commits/rolls back together with the change)."""
    session.add(
        AuditLog(
            org_id=org_id,
            user_id=user_id,
            action=action,
            entity=entity,
            entity_id=str(entity_id) if entity_id is not None else None,
            before=clean(before),
            after=clean(after),
            ip=ip,
        )
    )
