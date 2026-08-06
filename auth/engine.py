"""SA Core async engine + FastAPI connection dependency.

ponytail: no ORM, no sessionmaker. `AsyncEngine.begin()` gives us a
connection with an implicit transaction — that's all the app needs.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from .settings import load

if TYPE_CHECKING:
    pass

_engine: AsyncEngine | None = None


def engine() -> AsyncEngine:
    """Process-wide async engine. Constructed lazily on first call.

    Pool defaults are fine for a LAN app: 5 connections, 10 overflow, 30s recycle.
    Bump only if p99 shows connect-wait contention.
    """
    global _engine
    if _engine is None:
        s = load()
        _engine = create_async_engine(
            s.database_url,
            pool_pre_ping=True,
            pool_recycle=1800,
            # ponytail: leave pool_size/max_overflow at SA defaults (5/10)
            # until a real workload shows we need more.
        )
    return _engine


async def dispose() -> None:
    """Close the pool. Call from FastAPI lifespan shutdown."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncConnection]:
    """Yield a connection inside a transaction. Rolls back on error."""
    async with engine().begin() as conn:
        yield conn


# ---- FastAPI dependency ------------------------------------------------

async def get_conn() -> AsyncIterator[AsyncConnection]:
    """FastAPI dependency: `conn: AsyncConnection = Depends(get_conn)`.

    Wraps the handler in a transaction. Errors → automatic rollback.
    Handlers that only read still get a tx (cheap) — Postgres begins on
    first statement.
    """
    async with transaction() as conn:
        yield conn
