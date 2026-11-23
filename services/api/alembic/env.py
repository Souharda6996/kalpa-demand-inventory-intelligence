"""Alembic environment (async engine, URL from settings or -x url=...)."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from kalpa_api.core.config import get_settings
from kalpa_api.db.base import Base
from kalpa_api.db.models import *  # noqa: F403  (register all tables)
from kalpa_api.db.session import make_engine

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def _url() -> str:
    return context.get_x_argument(as_dictionary=True).get("url") or get_settings().database_url


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = make_engine(_url())
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


asyncio.run(_run_async())
