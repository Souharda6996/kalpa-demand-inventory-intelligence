"""Tenant-scoped repository: EVERY query is filtered by org_id; foreign rows look like 404s."""

import uuid
from datetime import date
from typing import Any

from sqlalchemy import Boolean, Date, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kalpa_api.core.audit import write_audit
from kalpa_api.core.errors import ApiError
from kalpa_api.db.base import Base


def row_dict(obj: Base) -> dict[str, Any]:
    """Column values of an ORM row."""
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def _coerce(col: Any, raw: str) -> Any:
    try:
        if isinstance(col.type, Boolean):
            return raw.lower() in ("1", "true", "yes")
        if isinstance(col.type, Date):
            return date.fromisoformat(raw)
        pt = col.type.python_type
        return raw if pt is str else pt(raw)
    except (ValueError, TypeError, NotImplementedError) as e:
        raise ApiError(422, "invalid_filter", f"Bad value for filter '{col.name}'") from e


class OrgRepo[M: Base]:
    """CRUD for one org-scoped model."""

    def __init__(
        self, model: type[M], session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID | None, ip: str | None = None
    ) -> None:
        self.model, self.s, self.org_id, self.user_id, self.ip = model, session, org_id, user_id, ip
        self.entity = model.__tablename__

    def _scope(self) -> Any:
        return self.model.org_id == self.org_id  # type: ignore[attr-defined]

    async def list(
        self,
        *,
        page: int,
        page_size: int,
        sort: str | None,
        sortable: set[str],
        filters: dict[str, str],
        filterable: set[str],
        extra: list[Any] | None = None,
    ) -> tuple[list[M], int]:
        """Paginated, sortable, filterable list."""
        where = [self._scope(), *(extra or [])]
        for k, v in filters.items():
            if k in filterable:
                where.append(getattr(self.model, k) == _coerce(self.model.__table__.c[k], v))
        total = (await self.s.execute(select(func.count()).select_from(self.model).where(*where))).scalar_one()
        q = select(self.model).where(*where)
        if sort:
            name = sort.lstrip("-")
            if name not in sortable:
                raise ApiError(422, "invalid_sort", f"Cannot sort by '{name}'")
            col = getattr(self.model, name)
            q = q.order_by(col.desc() if sort.startswith("-") else col.asc())
        else:
            q = q.order_by(self.model.created_at.desc(), self.model.id)
        q = q.limit(page_size).offset((page - 1) * page_size)
        return list((await self.s.execute(q)).scalars().all()), total

    async def get(self, id_: uuid.UUID) -> M:
        """Fetch by id within the org, else 404 (never reveals other orgs' rows)."""
        obj = (await self.s.execute(select(self.model).where(self.model.id == id_, self._scope()))).scalar_one_or_none()
        if obj is None:
            raise ApiError(404, "not_found", f"{self.entity} not found")
        return obj

    async def create(self, data: dict[str, Any]) -> M:
        """Insert with org_id forced server-side."""
        obj = self.model(**{**data, "org_id": self.org_id})
        self.s.add(obj)
        await self.s.flush()
        write_audit(
            self.s,
            org_id=self.org_id,
            user_id=self.user_id,
            action="create",
            entity=self.entity,
            entity_id=obj.id,
            after=row_dict(obj),
            ip=self.ip,
        )
        await self.s.commit()
        await self.s.refresh(obj)
        return obj

    async def update(self, id_: uuid.UUID, data: dict[str, Any]) -> M:
        """Patch fields (org_id/id are never writable)."""
        obj = await self.get(id_)
        before = row_dict(obj)
        for k, v in data.items():
            if k not in ("id", "org_id"):
                setattr(obj, k, v)
        await self.s.flush()
        await self.s.refresh(obj)  # server-side onupdate columns are expired after flush
        write_audit(
            self.s,
            org_id=self.org_id,
            user_id=self.user_id,
            action="update",
            entity=self.entity,
            entity_id=id_,
            before=before,
            after=row_dict(obj),
            ip=self.ip,
        )
        await self.s.commit()
        await self.s.refresh(obj)
        return obj

    async def delete(self, id_: uuid.UUID) -> None:
        """Delete by id within the org."""
        obj = await self.get(id_)
        before = row_dict(obj)
        await self.s.delete(obj)
        write_audit(
            self.s,
            org_id=self.org_id,
            user_id=self.user_id,
            action="delete",
            entity=self.entity,
            entity_id=id_,
            before=before,
            ip=self.ip,
        )
        await self.s.commit()
