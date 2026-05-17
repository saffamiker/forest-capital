"""
backend/database.py

Async SQLAlchemy engine and session factory for PostgreSQL persistence.

DATABASE_URL is optional — if absent (CI, local dev without Postgres) the
engine is None and callers skip DB writes gracefully.  provenance.json is
always written regardless, so the test suite never needs a live database.

Conditional approach chosen over mandatory Postgres because Render's free
tier starts cold and tests in GitHub Actions have no Postgres service.
Sprint 3+ may add a pg service container if we need DB-backed tests in CI.
"""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:770519@localhost:5432/forestcapital")

# Swap postgres:// → postgresql+asyncpg:// if Render provides the legacy scheme.
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL and not DATABASE_URL.startswith("postgresql+asyncpg://"):
    # Ensure asyncpg driver is specified — plain postgresql:// uses psycopg2.
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# In the test environment the engine uses NullPool. Tests reach the DB
# both through asyncio.run() (the DB round-trip tests) and through
# Starlette's TestClient, which runs each request on its own per-request
# portal event loop. A POOLED asyncpg connection returned to the pool is
# orphaned when that loop closes; a later pool_pre_ping probe of the
# cross-loop connection is interrupted, and asyncpg schedules a
# Connection._cancel task on the dead loop — the "coroutine
# 'Connection._cancel' was never awaited" RuntimeWarning. NullPool keeps
# no connection between checkouts, so every connection opens and closes
# inside its own loop. Production keeps the pooled, pool_pre_ping engine.
_IS_TEST = os.getenv("ENVIRONMENT") == "test"
engine: AsyncEngine | None = (
    (create_async_engine(DATABASE_URL, echo=False, poolclass=NullPool)
     if _IS_TEST
     else create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True))
    if DATABASE_URL
    else None
)

# Session factory — only usable when engine is not None.
AsyncSessionLocal: sessionmaker | None = (
    sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)  # type: ignore[call-overload]
    if engine
    else None
)


async def get_db() -> AsyncSession:  # type: ignore[return]
    """FastAPI dependency — yields a DB session for the request lifetime."""
    if AsyncSessionLocal is None:
        raise RuntimeError(
            "DATABASE_URL is not configured. "
            "Set it in .env to enable database persistence."
        )
    async with AsyncSessionLocal() as session:
        yield session
