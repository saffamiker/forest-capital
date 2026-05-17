"""
tools/platform_users.py

The data layer for database-managed access control — the platform_users
table (migration 015). Roles are presets; the `permissions` array is the
authoritative capability set.

Fail-open by design: every read swallows database errors and the auth
layer falls back to the config allowlists (config_fallback). A database
problem must never lock the whole team out — see CLAUDE.md → Platform
User Management.
"""
from __future__ import annotations

from typing import Any

import structlog

from config import ALLOWED_EMAILS, PROJECT_TEAM_EMAILS, ROLE_PRESETS

log = structlog.get_logger(__name__)

_USER_COLS = (
    "id, email, display_name, role, permissions, is_active, "
    "created_at, created_by, last_login_at, notes"
)


def _row_to_dict(r: Any) -> dict[str, Any]:
    """Maps a platform_users row tuple (in _USER_COLS order) to a dict."""
    return {
        "id": r[0], "email": r[1], "display_name": r[2], "role": r[3],
        "permissions": list(r[4]) if r[4] else [], "is_active": r[5],
        "created_at": _iso(r[6]), "created_by": r[7],
        "last_login_at": _iso(r[8]), "notes": r[9],
    }


def _iso(value: Any) -> str | None:
    try:
        return value.isoformat() if value is not None else None
    except Exception:  # noqa: BLE001
        return None


# ── Config fallback — the emergency bypass ────────────────────────────────────

def config_fallback(email: str) -> dict[str, Any]:
    """
    Resolves a user's role and permissions from the config allowlists —
    used when platform_users is unreachable. PROJECT_TEAM_EMAILS →
    team_member; any other ALLOWED_EMAILS address → viewer. Sysadmin is
    deliberately NOT granted here: during a database outage the master
    API key (developer role) is the emergency admin path.
    """
    el = (email or "").strip().lower()
    if el in {e.lower() for e in PROJECT_TEAM_EMAILS}:
        role = "team_member"
    elif el in {e.lower() for e in ALLOWED_EMAILS}:
        role = "viewer"
    else:
        role = "viewer"
    return {"role": role, "display_name": None,
            "permissions": list(ROLE_PRESETS[role])}


# ── Reads ─────────────────────────────────────────────────────────────────────

async def get_active_user(email: str) -> dict[str, Any] | None:
    """
    The active platform_users row for an email, or None when no active
    user exists. Returns None on a database error too — the caller
    (magic-link request) then applies the config fallback.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                f"SELECT {_USER_COLS} FROM platform_users "
                "WHERE lower(email) = lower(:e) AND is_active = true"
            ), {"e": email})
            found = row.fetchone()
            return _row_to_dict(found) if found else None
    except Exception as exc:  # noqa: BLE001
        log.warning("platform_users_get_active_failed", error=str(exc))
        return None


async def is_login_allowed(email: str) -> bool:
    """
    True when an email may be sent a magic link. When platform_users is
    reachable the answer is authoritative — an active row is required, so
    a deactivated user is correctly refused. Only when the table is
    unreachable does it fall back to the config ALLOWED_EMAILS allowlist,
    so a database outage cannot lock the team out.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            raise RuntimeError("database not configured")
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT 1 FROM platform_users "
                "WHERE lower(email) = lower(:e) AND is_active = true"
            ), {"e": email})
            return row.fetchone() is not None
    except Exception as exc:  # noqa: BLE001
        log.warning("platform_users_login_check_fallback", error=str(exc))
        return (email or "").strip().lower() in {
            e.lower() for e in ALLOWED_EMAILS}


async def resolve_user(email: str) -> dict[str, Any]:
    """
    Resolves {role, display_name, permissions} for an authenticated
    email — the per-request resolution behind require_auth when the JWT
    did not carry permissions (an old or test-minted token). An active
    row wins; otherwise the config fallback applies. Always returns a
    dict — never raises.
    """
    user = await get_active_user(email)
    if user:
        return {"role": user["role"], "display_name": user["display_name"],
                "permissions": user["permissions"]}
    return config_fallback(email)


async def record_login(email: str) -> None:
    """Stamps last_login_at on a successful login. Fail-open."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return
        async with AsyncSessionLocal() as session:
            await session.execute(text(
                "UPDATE platform_users SET last_login_at = now() "
                "WHERE lower(email) = lower(:e)"
            ), {"e": email})
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("platform_users_record_login_failed", error=str(exc))
