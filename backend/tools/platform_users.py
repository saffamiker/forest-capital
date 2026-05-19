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

from config import (
    ALLOWED_EMAILS, PROJECT_TEAM_EMAILS, ROLE_PRESETS, SYSADMIN_EMAILS,
)

log = structlog.get_logger(__name__)

_USER_COLS = (
    "id, email, display_name, role, permissions, is_active, "
    "created_at, created_by, last_login_at, notes, "
    "council_queries_used, council_queries_limit"
)


def _row_to_dict(r: Any) -> dict[str, Any]:
    """Maps a platform_users row tuple (in _USER_COLS order) to a dict."""
    return {
        "id": r[0], "email": r[1], "display_name": r[2], "role": r[3],
        "permissions": list(r[4]) if r[4] else [], "is_active": r[5],
        "created_at": _iso(r[6]), "created_by": r[7],
        "last_login_at": _iso(r[8]), "notes": r[9],
        # Lifetime council-query allocation. council_queries_limit is None
        # for unlimited users (team members, sysadmins).
        "council_queries_used": r[10] if r[10] is not None else 0,
        "council_queries_limit": r[11],
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
    used when platform_users is unreachable. Faithfully mirrors the
    migration-015 seed: SYSADMIN_EMAILS → sysadmin, PROJECT_TEAM_EMAILS →
    team_member, any other ALLOWED_EMAILS address → viewer. Mirroring the
    seed means a database outage degrades gracefully — Michael keeps
    administration, the team keep their access.
    """
    el = (email or "").strip().lower()
    if el in {e.lower() for e in SYSADMIN_EMAILS}:
        role = "sysadmin"
    elif el in {e.lower() for e in PROJECT_TEAM_EMAILS}:
        role = "team_member"
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


async def get_council_allocation(email: str) -> dict[str, Any] | None:
    """
    A user's council query allocation — {council_queries_used,
    council_queries_limit} — or None when the user is absent or the
    database is unavailable. A narrow read kept separate from
    get_active_user so the council endpoint can look up the allocation
    without going through (or being confused with) auth resolution.
    None means "no allowance on record" → the caller treats the user as
    unlimited (fail-open).
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT council_queries_used, council_queries_limit "
                "FROM platform_users "
                "WHERE lower(email) = lower(:e) AND is_active = true"
            ), {"e": email})
            r = row.fetchone()
            if r is None:
                return None
            return {
                "council_queries_used": r[0] if r[0] is not None else 0,
                "council_queries_limit": r[1],
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("platform_users_council_allocation_failed", error=str(exc))
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


async def list_all_users() -> list[dict[str, Any]]:
    """
    Every platform_users row, ordered sysadmin → team_member → viewer
    then email, each with an activity_count (agent_interactions +
    session_events for that email). The user-management table.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text(
                f"SELECT {_USER_COLS} FROM platform_users "
                "ORDER BY CASE role WHEN 'sysadmin' THEN 0 "
                "WHEN 'team_member' THEN 1 ELSE 2 END, email"
            ))
            users = [_row_to_dict(r) for r in rows.fetchall()]
            # Activity counts — one grouped query per source, merged.
            counts: dict[str, int] = {}
            for table in ("agent_interactions", "session_events"):
                agg = await session.execute(text(
                    f"SELECT user_email, COUNT(*) FROM {table} "
                    "GROUP BY user_email"))
                for email, n in agg.fetchall():
                    counts[email] = counts.get(email, 0) + int(n)
            # AI token spend per user — the estimated_cost_usd column on
            # agent_interactions (NULL for pre-token-logging rows). A
            # sysadmin reviewing the user table sees what each account
            # has cost, viewers included.
            cost_by_email: dict[str, float] = {}
            spend = await session.execute(text(
                "SELECT user_email, COALESCE(SUM(estimated_cost_usd), 0) "
                "FROM agent_interactions GROUP BY user_email"))
            for email, total in spend.fetchall():
                cost_by_email[email] = float(total or 0)
            for u in users:
                u["activity_count"] = counts.get(u["email"], 0)
                u["ai_cost_usd"] = round(cost_by_email.get(u["email"], 0.0), 6)
            return users
    except Exception as exc:  # noqa: BLE001
        log.warning("platform_users_list_failed", error=str(exc))
        # TEMPORARY DEBUG (do not commit) — log the full traceback so the
        # actual swallowed exception is visible in Render logs. Remove
        # once the sub-query isolation fix has landed.
        import traceback
        log.error("list_all_users_failed: " + traceback.format_exc())
        return []


async def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    """One platform_users row by id, or None."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                f"SELECT {_USER_COLS} FROM platform_users WHERE id = :id"
            ), {"id": user_id})
            found = row.fetchone()
            return _row_to_dict(found) if found else None
    except Exception as exc:  # noqa: BLE001
        log.warning("platform_users_get_by_id_failed", error=str(exc))
        return None


async def email_exists(email: str) -> bool:
    """True when a platform_users row already has this email."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return False
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT 1 FROM platform_users WHERE lower(email) = lower(:e)"
            ), {"e": email})
            return row.fetchone() is not None
    except Exception as exc:  # noqa: BLE001
        log.warning("platform_users_email_exists_failed", error=str(exc))
        return False


async def count_active_sysadmins() -> int:
    """
    The number of active users holding the manage_users permission — the
    "last sysadmin" guard counts this before a demotion / deactivation.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return 0
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT COUNT(*) FROM platform_users WHERE is_active = true "
                "AND 'manage_users' = ANY(permissions)"))
            found = row.fetchone()
            return int(found[0]) if found else 0
    except Exception as exc:  # noqa: BLE001
        log.warning("platform_users_count_sysadmins_failed", error=str(exc))
        return 0


async def create_user(
    *, email: str, display_name: str | None, role: str,
    permissions: list[str], notes: str | None, created_by: str,
) -> dict[str, Any] | None:
    """Inserts a new platform_users row. Returns the stored row or None.

    Council allocation by role: viewers get a finite lifetime allowance
    (5 queries); team members and sysadmins are unlimited (NULL)."""
    council_limit = None if role in ("team_member", "sysadmin") else 5
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "INSERT INTO platform_users "
                "(email, display_name, role, permissions, notes, created_by, "
                " council_queries_limit) "
                "VALUES (:email, :dn, :role, :perms, :notes, :cb, :cql) "
                f"RETURNING {_USER_COLS}"
            ), {"email": email, "dn": display_name, "role": role,
                "perms": permissions, "notes": notes, "cb": created_by,
                "cql": council_limit})
            stored = row.fetchone()
            await session.commit()
            return _row_to_dict(stored) if stored else None
    except Exception as exc:  # noqa: BLE001
        log.warning("platform_users_create_failed", error=str(exc))
        return None


async def update_user(user_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
    """
    Updates the supplied fields (display_name, role, permissions,
    is_active, notes, council_queries_used, council_queries_limit) of one
    user. email is immutable and ignored. Returns the updated row, or None.
    """
    allowed = ("display_name", "role", "permissions", "is_active", "notes",
               "council_queries_used", "council_queries_limit")
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return await get_user_by_id(user_id)
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        assignments = ", ".join(f"{k} = :{k}" for k in sets)
        params = dict(sets)
        params["id"] = user_id
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                f"UPDATE platform_users SET {assignments} WHERE id = :id "
                f"RETURNING {_USER_COLS}"
            ), params)
            stored = row.fetchone()
            await session.commit()
            return _row_to_dict(stored) if stored else None
    except Exception as exc:  # noqa: BLE001
        log.warning("platform_users_update_failed", error=str(exc))
        return None


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


async def increment_council_queries(email: str) -> dict[str, Any] | None:
    """
    Increments a user's lifetime council_queries_used by one and returns
    {council_queries_used, council_queries_limit} with the new values.
    Called once per council query by a limited (non-unlimited) user.
    Fail-open: returns None on any database error.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "UPDATE platform_users "
                "SET council_queries_used = council_queries_used + 1 "
                "WHERE lower(email) = lower(:e) "
                "RETURNING council_queries_used, council_queries_limit"
            ), {"e": email})
            stored = row.fetchone()
            await session.commit()
            if stored is None:
                return None
            return {"council_queries_used": stored[0],
                    "council_queries_limit": stored[1]}
    except Exception as exc:  # noqa: BLE001
        log.warning("platform_users_increment_council_failed", error=str(exc))
        return None
