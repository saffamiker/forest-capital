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

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:770519@localhost:5432/forestcapital")

# Swap postgres:// → postgresql+asyncpg:// if Render provides the legacy scheme.
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL and not DATABASE_URL.startswith("postgresql+asyncpg://"):
    # Ensure asyncpg driver is specified — plain postgresql:// uses psycopg2.
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine: AsyncEngine | None = (
    create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
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
