"""Factory for paginated, sortable, filterable, tenant-scoped CRUD routers."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from kalpa_api.core.deps import Principal, Session, require
from kalpa_api.core.errors import ApiError
from kalpa_api.core.repo import OrgRepo
from kalpa_api.db.base import Base


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def verify_fks(
    session: Session, org_id: uuid.UUID, data: dict[str, Any], fk_checks: dict[str, type[Base]]
) -> None:
    """Every referenced id must exist in the SAME org (blocks cross-tenant references)."""
    for field, target in fk_checks.items():
        val = data.get(field)
        if val is None:
            continue
        found = (await session.execute(select(target.id).where(target.id == val, target.org_id == org_id))).first()  # type: ignore[attr-defined]
        if not found:
            raise ApiError(422, "invalid_reference", f"{field} does not exist in this organisation")


def build_crud[M: Base](
    *,
    prefix: str,
    tag: str,
    model: type[M],
    create_schema: Any,
    update_schema: Any,
    out_schema: Any,
    sortable: set[str],
    filterable: set[str],
    fk_checks: dict[str, type[Base]] | None = None,
    write_perm: str = "edit_master_data",
    read_perm: str = "view",
    name: str,
) -> APIRouter:
    """Build list/get/create/patch/delete routes for one resource."""
    router = APIRouter(prefix=prefix, tags=[tag])
    fks = fk_checks or {}
    Read = Annotated[Principal, Depends(require(read_perm))]  # noqa: N806
    Write = Annotated[Principal, Depends(require(write_perm))]  # noqa: N806

    def repo(session: Session, p: Principal, request: Request) -> OrgRepo[M]:
        return OrgRepo(model, session, p.org_id, p.user.id, _ip(request))

    @router.get("", response_model=Page[out_schema], operation_id=f"list_{name}")
    async def list_(
        request: Request,
        session: Session,
        p: Read,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=200)] = 50,
        sort: str | None = None,
    ) -> Any:
        """Paginated list (sort=field or -field; equality filters via ?field=value)."""
        reserved = {"page", "page_size", "sort"}
        filters = {k: v for k, v in request.query_params.items() if k not in reserved}
        bad = set(filters) - filterable
        if bad:
            raise ApiError(422, "invalid_filter", f"Unknown filter(s): {', '.join(sorted(bad))}")
        items, total = await repo(session, p, request).list(
            page=page, page_size=page_size, sort=sort, sortable=sortable, filters=filters, filterable=filterable
        )
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    @router.get("/{item_id}", response_model=out_schema, operation_id=f"get_{name}")
    async def get_(item_id: uuid.UUID, request: Request, session: Session, p: Read) -> Any:
        """Fetch one (404 if it belongs to another org)."""
        return await repo(session, p, request).get(item_id)

    @router.post("", response_model=out_schema, status_code=201, operation_id=f"create_{name}")
    async def create_(body: create_schema, request: Request, session: Session, p: Write) -> Any:
        """Create."""
        data = body.model_dump()
        await verify_fks(session, p.org_id, data, fks)
        return await repo(session, p, request).create(data)

    @router.patch("/{item_id}", response_model=out_schema, operation_id=f"update_{name}")
    async def update_(item_id: uuid.UUID, body: update_schema, request: Request, session: Session, p: Write) -> Any:
        """Partial update."""
        data = body.model_dump(exclude_unset=True)
        await verify_fks(session, p.org_id, data, fks)
        return await repo(session, p, request).update(item_id, data)

    @router.delete("/{item_id}", status_code=204, operation_id=f"delete_{name}")
    async def delete_(item_id: uuid.UUID, request: Request, session: Session, p: Write) -> None:
        """Delete."""
        await repo(session, p, request).delete(item_id)

    return router
