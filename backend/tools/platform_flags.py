"""tools/platform_flags.py -- June 28 2026.

Lightweight feature-flag readers for platform_config rows that
gate behavior changes which need to ship dark + roll forward
under operator control.

Each flag is one platform_config row keyed by the flag name; the
row's JSONB value is `{"enabled": true | false}`. A missing row,
unreadable DB, or malformed payload all degrade to flag=OFF, so
the platform behaves exactly as it did before the flag landed
until the operator explicitly flips it.

Usage in code:

    from tools.platform_flags import is_defer_substitution_enabled
    if await is_defer_substitution_enabled():
        # new behaviour
    else:
        # legacy behaviour

A sync cache wrapper is intentionally not provided -- platform
flags are read at decision time (typically once per request /
generation), so the cost of a single SELECT is acceptable. If a
hot-path flag is added later, add an in-process TTL cache here.
"""
from __future__ import annotations

import json
import logging
from typing import Any

try:
    import structlog
    log = structlog.get_logger(__name__)
except ImportError:
    log = logging.getLogger(__name__)  # type: ignore[assignment]


_DB_AVAILABLE = False
try:
    from database import AsyncSessionLocal
    _DB_AVAILABLE = AsyncSessionLocal is not None
except Exception:  # noqa: BLE001
    AsyncSessionLocal = None  # type: ignore[assignment]


# ── Generic flag reader ─────────────────────────────────────────


async def _read_flag(key: str, default: bool = False) -> bool:
    """Reads a {"enabled": bool} row from platform_config.
    Returns `default` on any failure (missing row, unreadable
    JSON, DB unavailable, malformed payload, etc).

    Fail-open to OFF so a flag landing without its row in DB
    keeps legacy behaviour."""
    if not _DB_AVAILABLE:
        return default
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            row = await session.execute(
                text("SELECT value FROM platform_config "
                     "WHERE key = :k"),
                {"k": key})
            r = row.fetchone()
            if not r or r[0] is None:
                return default
            value = r[0]
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except Exception:  # noqa: BLE001
                    return default
            if not isinstance(value, dict):
                return default
            v = value.get("enabled")
            if isinstance(v, bool):
                return v
            return default
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "platform_flag_read_failed", key=key,
            error=str(exc))
        return default


# ── DEFER_SUBSTITUTION_TO_EXPORT ────────────────────────────────
#
# When ENABLED:
#   * harness_narrative persists RAW (token-bearing) text to
#     content_json so {{TOKEN}} placeholders survive into the
#     stored draft.
#   * The harness evaluator still sees a SUBSTITUTED projection
#     of each candidate response so it can grade prose quality
#     against rendered values, not bare tokens.
#   * content_text (full-text-search shadow) is derived from the
#     substituted projection so word counts + search remain
#     value-aware.
#   * Export pipelines (DOCX / PPTX / editor render) resolve
#     tokens at render time via _apply_substitutions /
#     _substitute_pptx_text / the TipTap NodeView -- the same
#     three paths that already exist for figure captions + deck
#     slide cleanup.
#
# When DISABLED (default):
#   * Legacy behaviour -- substitution fires inside
#     _substituting_generator before the evaluator sees the text;
#     content_json holds resolved values; the dual-mode upgrade
#     pass finds 0 surviving {{TOKEN}} placeholders.

DEFER_SUBSTITUTION_TO_EXPORT_KEY = "defer_substitution_to_export"


async def is_defer_substitution_enabled() -> bool:
    """True when DEFER_SUBSTITUTION_TO_EXPORT flag is set
    enabled=true in platform_config. Default OFF."""
    return await _read_flag(
        DEFER_SUBSTITUTION_TO_EXPORT_KEY, default=False)


def is_defer_substitution_enabled_sync() -> bool:
    """Synchronous variant for callers inside non-async contexts
    (e.g. the build_*_executive_brief DOCX builders that run
    sync). Uses a per-process cache to avoid blocking the event
    loop on every brief paragraph render.

    Cache is keyed at module level + cleared via
    reset_flag_cache() in tests. Default OFF + same fail-open
    semantics as the async variant."""
    cached = _SYNC_CACHE.get(DEFER_SUBSTITUTION_TO_EXPORT_KEY)
    if cached is not None:
        return cached
    try:
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            # Cannot block the running loop from a sync helper;
            # caller is responsible for using the async variant
            # in async contexts. Fail-open to OFF.
            return False
        val = asyncio.run(is_defer_substitution_enabled())
        _SYNC_CACHE[DEFER_SUBSTITUTION_TO_EXPORT_KEY] = val
        return val
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "platform_flag_sync_read_failed",
            error=str(exc))
        return False


_SYNC_CACHE: dict[str, bool] = {}


def reset_flag_cache() -> None:
    """Test helper -- clears the sync cache. Production code
    never calls this; the cache is intentionally process-wide
    so a flag flip requires a deploy or a restart."""
    _SYNC_CACHE.clear()
