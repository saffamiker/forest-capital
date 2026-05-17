"""
tools/changelog.py

Data layer for the changelog feature — the release history shown in
Settings and the unseen-entries feed behind the What's New modal.

A user's "last seen" state lives in the users table (migration 012),
keyed by email. mark_changelog_seen UPSERTs, so a first-time user needs
no pre-seeded row — a missing row reads as last_changelog_seen_at NULL,
which correctly means "has seen nothing".

Every function is fail-open: a DB error is logged and swallowed, never
raised — the changelog must never block a login or a Settings load.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

try:
    import structlog
    log = structlog.get_logger(__name__)
except ImportError:  # pragma: no cover
    log = logging.getLogger(__name__)  # type: ignore[assignment]

from config import TOUR_VERSION

_DB_AVAILABLE = False
try:
    from database import AsyncSessionLocal
    _DB_AVAILABLE = AsyncSessionLocal is not None
except Exception:  # pragma: no cover
    pass

_SELECT_COLS = (
    "id, version, released_at, title, description, academic_rationale, "
    "tour_step_id"
)


def _row_to_entry(r: Any) -> dict[str, Any]:
    """Maps a changelog row tuple to the JSON entry shape."""
    released = r[2]
    return {
        "id": int(r[0]),
        "version": int(r[1]),
        "released_at": released.isoformat() if isinstance(released, datetime)
        else str(released),
        "title": r[3],
        "description": r[4],
        "academic_rationale": r[5],
        "tour_step_id": r[6],
    }


async def get_all_changelog() -> list[dict[str, Any]]:
    """Every changelog entry, newest version first — the Settings
    Release History. Returns [] when the database is unavailable."""
    if not _DB_AVAILABLE:
        return []
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            rows = await session.execute(text(
                f"SELECT {_SELECT_COLS} FROM changelog ORDER BY version DESC"))
            return [_row_to_entry(r) for r in rows.fetchall()]
    except Exception as exc:  # noqa: BLE001
        log.warning("changelog_read_failed", error=str(exc))
        return []


async def get_unseen_changelog(email: str) -> dict[str, Any]:
    """
    Changelog entries released after the user last dismissed What's New,
    plus the tour-update flag. A user with no row (or a NULL
    last_changelog_seen_at) sees every entry — correct for a first visit.
    """
    payload: dict[str, Any] = {
        "entries": [], "has_tour_update": TOUR_VERSION > 0,
        "tour_version": TOUR_VERSION,
    }
    if not _DB_AVAILABLE:
        return payload
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            urow = await session.execute(
                text("SELECT last_changelog_seen_at, last_tour_version_seen "
                     "FROM users WHERE email = :e"),
                {"e": email})
            user = urow.fetchone()
            seen_at = user[0] if user else None
            tour_seen = (user[1] if user and user[1] is not None else 0)

            # released_at > seen_at; a NULL seen_at means nothing is seen
            # yet. The parameter is CAST so asyncpg can infer its type —
            # a bare ":seen IS NULL" leaves the type ambiguous.
            rows = await session.execute(
                text(f"SELECT {_SELECT_COLS} FROM changelog "
                     "WHERE CAST(:seen AS TIMESTAMPTZ) IS NULL "
                     "OR released_at > CAST(:seen AS TIMESTAMPTZ) "
                     "ORDER BY version DESC"),
                {"seen": seen_at})
            payload["entries"] = [_row_to_entry(r) for r in rows.fetchall()]
            payload["has_tour_update"] = TOUR_VERSION > tour_seen
            return payload
    except Exception as exc:  # noqa: BLE001
        log.warning("changelog_unseen_failed", error=str(exc))
        return payload


async def mark_changelog_seen(
    email: str, tour_version_seen: int | None = None,
) -> bool:
    """
    Records that the user has seen the changelog up to now. UPSERTs the
    users row. When tour_version_seen is given it is stored; otherwise
    the existing tour value is kept. Returns True on a successful write.
    """
    if not _DB_AVAILABLE:
        return False
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            await session.execute(
                text(
                    "INSERT INTO users "
                    "(email, last_changelog_seen_at, last_tour_version_seen) "
                    "VALUES (:e, now(), COALESCE(:tv, 0)) "
                    "ON CONFLICT (email) DO UPDATE SET "
                    " last_changelog_seen_at = now(), "
                    " last_tour_version_seen = "
                    "  COALESCE(:tv, users.last_tour_version_seen)"
                ),
                {"e": email, "tv": tour_version_seen})
            await session.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("changelog_mark_seen_failed", error=str(exc))
        return False
