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
    (e.g. the harness_narrative deferral-swap path running
    inside asyncio.to_thread). Always queries -- no caching.

    REPLACED June 28 2026: the prior implementation maintained
    a process-wide _SYNC_CACHE that NEVER invalidated. When the
    FIRST call ran from a context with a running event loop, the
    function fail-opened to False AND cached that False
    permanently. Every subsequent call (including the
    legitimate harness_narrative-in-asyncio.to_thread context
    where the query would otherwise succeed) returned the
    poisoned False. That was the operator-confirmed root cause
    of Phase 2 deferral silently no-op-ing for the executive
    brief on draft 74 / draft 75 despite
    DEFER_SUBSTITUTION_TO_EXPORT=true in platform_config.

    Current contract: must reliably return True whenever the
    platform_config row holds {"enabled": true}, regardless of
    thread context OR call order. Two cases:

      1. No running loop in the caller's thread -- typical for
         harness_narrative inside asyncio.to_thread (the default
         executor's worker threads have no event loop). Direct
         asyncio.run(is_defer_substitution_enabled()) succeeds.

      2. Running loop in the caller's thread -- typical for a
         test calling this helper from inside an async test or
         a hypothetical sync invocation from request-handling
         code. Delegate to a single-worker ThreadPoolExecutor so
         the query runs in a fresh thread that gets its own
         loop. The current thread blocks on .result() but does
         NOT deadlock (the running loop is unaffected; the
         query runs in a sibling thread).

    Per-call DB cost: one indexed-PK row select. With 6 brief
    sections that's 6 queries per generation -- negligible.
    The premature cache was buying microseconds at the cost of
    correctness."""
    try:
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            return asyncio.run(is_defer_substitution_enabled())
        # Running loop present -- delegate to a worker thread
        # that opens its own asyncio.run. concurrent.futures
        # avoids the run_until_complete-on-running-loop
        # deadlock.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=1) as pool:
            return pool.submit(
                lambda: asyncio.run(
                    is_defer_substitution_enabled())
            ).result(timeout=10)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "platform_flag_sync_read_failed",
            error=str(exc))
        return False


# Module-level cache kept ONLY for backward-compat with the
# reset_flag_cache() test helper -- the helper is a no-op now
# but existing tests still call it during setup/teardown.
_SYNC_CACHE: dict[str, bool] = {}


def reset_flag_cache() -> None:
    """Test helper -- clears the sync cache.

    June 28 2026: kept for API compatibility but is now a no-op
    since is_defer_substitution_enabled_sync no longer
    consults _SYNC_CACHE. Existing tests that call this
    continue to work without modification."""
    _SYNC_CACHE.clear()
