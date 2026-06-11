"""Async SQLAlchemy engine + session factory."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

settings = get_settings()

# Echo emits raw SQL + bound parameters at INFO; that includes hashed
# tokens, audit details, and PII. Only honour `debug` in development.
_echo = settings.debug and settings.app_env == "development"

_connect_args: dict = {}
if settings.database_url.startswith("sqlite"):
    # WAL mode allows concurrent readers alongside a single writer, which
    # prevents "database is locked" errors when the in-process worker and
    # the request handlers share the same SQLite file. The busy_timeout
    # makes writers wait up to 5 s before raising instead of failing immediately.
    _connect_args = {"timeout": 5}

engine = create_async_engine(
    settings.database_url,
    echo=_echo,
    future=True,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):  # type: ignore[misc]
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
