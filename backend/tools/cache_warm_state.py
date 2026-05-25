"""tools/cache_warm_state.py — process-wide state for the auto-warm
analytics cache subsystem (May 24 2026).

The user's directive: cache warming must be FULLY automatic on
startup. The previous lifespan hook ran the refresh synchronously,
competing with first-request handling on a cold Render boot. The
new approach:

  1. Startup hook fires `auto_warm_analytics()` as a non-blocking
     asyncio task immediately after the app is ready to accept
     requests. It does not block the lifespan handler.

  2. `auto_warm_analytics()` retries up to MAX_ATTEMPTS with
     exponential backoff. A transient DB or yfinance hiccup on a
     cold boot does not leave the cache cold forever.

  3. The state of the warm operation lives in this module:
     - status: 'idle' | 'warming' | 'warm' | 'failed'
     - last_attempt_at, last_success_at
     - last_attempt_error (when failed)
     - in_progress: True while a warm is running
     - attempts: total attempts since process start

  4. `GET /api/v1/admin/cache-status` returns this state plus the
     per-row landed booleans (academic_analytics, efficient_frontier)
     read from analytics_metrics_cache so the Admin UI can show:
       "Cache warm ✅ computed X minutes ago" — status == 'warm'
       "Cache cold — warming now…"           — status == 'warming'
       "Warm cache"                          — status == 'idle' or 'failed'

  5. The manual `POST /api/v1/admin/warm-analytics-cache` endpoint
     remains as the fallback. It bypasses the in-progress check by
     awaiting the warm op inline, so a sysadmin can force a fresh
     compute even when the auto-warm has just succeeded.

All state is in-memory. A Render redeploy resets it; that's
intentional — the auto-warm hook fires again on the next startup.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Awaitable, Callable

import structlog


log = structlog.get_logger(__name__)


# Retry policy — 3 attempts, exponential backoff 5s → 15s → 45s.
# The doubled stride gives a transient DB or yfinance outage time
# to recover without spamming the upstreams.
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (5, 15, 45)


@dataclass
class WarmState:
    """Process-wide auto-warm state. One instance per process."""
    status: str = 'idle'                          # idle | warming | warm | failed
    in_progress: bool = False
    attempts: int = 0
    last_attempt_at: float | None = None          # unix seconds
    last_success_at: float | None = None          # unix seconds
    last_attempt_error: str | None = None
    last_took_s: float | None = None
    last_landed: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Public-facing serialisation. Converts the unix timestamps
        to ISO strings (more useful for the UI) and adds a derived
        `last_success_age_seconds` so the UI can render "computed N
        minutes ago" without doing its own clock math."""
        from datetime import datetime, timezone
        d = asdict(self)
        for k in ('last_attempt_at', 'last_success_at'):
            ts = d.get(k)
            d[k] = (
                datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                if ts is not None else None
            )
        if self.last_success_at is not None:
            d['last_success_age_seconds'] = round(
                time.time() - self.last_success_at, 1)
        else:
            d['last_success_age_seconds'] = None
        return d


_STATE = WarmState()


def get_warm_state() -> WarmState:
    """Returns the singleton WarmState. The caller MUST NOT mutate
    the returned object directly — use the helper setters below so
    log entries land alongside state changes."""
    return _STATE


def _mark_warming() -> None:
    _STATE.status = 'warming'
    _STATE.in_progress = True
    _STATE.attempts += 1
    _STATE.last_attempt_at = time.time()
    _STATE.last_attempt_error = None
    log.info("analytics_cache_warm_started",
             attempt=_STATE.attempts)


def _mark_success(took_s: float, landed: dict[str, bool]) -> None:
    _STATE.status = 'warm'
    _STATE.in_progress = False
    _STATE.last_success_at = time.time()
    _STATE.last_took_s = round(took_s, 2)
    _STATE.last_landed = dict(landed)
    log.info("analytics_cache_warm_success",
             attempt=_STATE.attempts,
             took_s=_STATE.last_took_s,
             landed=_STATE.last_landed)


def _mark_failed(error: str) -> None:
    _STATE.status = 'failed'
    _STATE.in_progress = False
    _STATE.last_attempt_error = error
    log.warning("analytics_cache_warm_failed",
                attempt=_STATE.attempts,
                error=error)


async def auto_warm_analytics(
    warm_fn: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    *,
    max_attempts: int = MAX_ATTEMPTS,
    backoff_seconds: tuple[int, ...] = BACKOFF_SECONDS,
) -> WarmState:
    """Runs the analytics cache warm with retry + exponential backoff.

    Parameters:
      warm_fn        — the async no-arg callable that performs ONE
                       warm attempt and returns a `landed` dict like
                       {"academic_analytics": True, "efficient_frontier": True}.
                       Defaults to `_default_warm_fn` which calls
                       `refresh_all_analytics` and verifies the rows.
      max_attempts   — how many tries before giving up. Default 3.
      backoff_seconds — seconds to sleep between attempts. Indexed
                       by attempt number, so backoff_seconds[0] is
                       the wait AFTER the first failure, before the
                       second attempt.

    Returns the WarmState after the run completes. Never raises —
    a fully-failed warm leaves status='failed' and the next call
    (e.g. manual button click) can retry.

    Idempotent — calling this while a warm is in progress is a
    no-op (returns the current state immediately). The manual
    button bypasses this check by calling refresh_all_analytics
    directly.
    """
    if _STATE.in_progress:
        log.info("analytics_cache_warm_skipped_in_progress")
        return _STATE

    fn = warm_fn or _default_warm_fn
    for attempt in range(1, max_attempts + 1):
        _mark_warming()
        t0 = time.monotonic()
        try:
            landed = await fn()
            took_s = time.monotonic() - t0
            _mark_success(took_s, landed)
            return _STATE
        except Exception as exc:  # noqa: BLE001
            _mark_failed(str(exc))
            if attempt < max_attempts:
                # Backoff before the next attempt. Index defensively
                # in case the caller passed a shorter tuple.
                wait_s = (
                    backoff_seconds[attempt - 1]
                    if attempt - 1 < len(backoff_seconds)
                    else backoff_seconds[-1]
                )
                log.info(
                    "analytics_cache_warm_retry_scheduled",
                    next_attempt=attempt + 1, wait_s=wait_s)
                await asyncio.sleep(wait_s)
                # Loop continues to the next attempt — _mark_warming
                # increments .attempts again on the next iteration.

    # All attempts exhausted. State already shows status='failed'.
    log.warning("analytics_cache_warm_exhausted",
                total_attempts=max_attempts,
                last_error=_STATE.last_attempt_error)
    return _STATE


def _expected_strategy_ids() -> set[str]:
    """The canonical strategy id set the warm expects in a healthy
    strategy_results_cache row. Sourced from strategy_metadata so it
    stays in lock-step with the backtester's universe — adding or
    removing a strategy in metadata automatically updates this rule.

    Fail-open: a metadata import error returns an empty set, which
    makes _strategy_cache_is_healthy fall back to the per-strategy
    monthly_returns check below (better to skip the rerun than fail
    the warm entirely).
    """
    try:
        from strategy_metadata import STRATEGY_METADATA
        return {entry["id"] for entry in STRATEGY_METADATA if entry.get("id")}
    except Exception as exc:  # noqa: BLE001
        log.warning("strategy_metadata_import_failed", error=str(exc))
        return set()


def _strategy_cache_is_healthy(cached: dict | None) -> bool:
    """True when the strategy_results_cache row is usable input for
    AN01 / AN04. Healthy requires THREE conditions:

      1. The row exists.
      2. Every canonical strategy id (per strategy_metadata, currently
         10) is present in the row. A single-strategy cache row — e.g.
         a BENCHMARK-only fallback from a prior cold boot — passes
         condition 3 but fails this one. The downstream analytics
         layer needs ALL strategies, not just one.
      3. Every present strategy carries a non-empty monthly_returns
         list. A partial-fallback row (one real strategy, nine with
         empty monthly_returns) is NOT healthy — AN01 / AN04 read the
         row and produce empty downstream tables because the
         empty-series strategies get skipped.

    Co-located here (not in tools/cache) so the warm's health rule
    is visible alongside the warm itself; the rule may evolve
    independently of the cache's read/write API.
    """
    if not cached:
        return False
    # Condition 2 — the row must contain every canonical strategy.
    # A BENCHMARK-only cache passes the monthly_returns loop below
    # (one strategy, populated) but cannot drive AN01 / AN04 because
    # refresh_academic_analytics needs all 10 strategies to populate
    # the factor_loadings and regime_conditional tables.
    expected = _expected_strategy_ids()
    if expected:
        present = set(cached.keys())
        if not expected.issubset(present):
            return False
    # Condition 3 — every present strategy must carry a non-empty
    # monthly_returns list.
    for r in cached.values():
        mr = (r or {}).get("monthly_returns")
        if not isinstance(mr, list) or not mr:
            return False
    return True


async def _default_warm_fn() -> dict[str, bool]:
    """Default warm implementation — verifies strategy_results_cache
    is usable, runs the backtester if not, then refreshes analytics
    and confirms the two key rows landed in analytics_metrics_cache.

    May 28 2026 — strategy_results_cache repopulation. The previous
    implementation ran refresh_all_analytics against whatever was in
    strategy_results_cache, even if the row was a partial-fallback
    (one real strategy, others with empty monthly_returns). In that
    state AN01 (factor_loadings) and AN04 (regime_conditional) read
    the row, skipped every empty-series strategy, and produced
    empty downstream tables. The warm now proactively reruns
    run_all_strategies when the cache row is not healthy, so the
    analytics refresh runs against fresh strategy data.

    The backtester rerun is wrapped in its own try/except so a
    transient pipeline failure (FRED outage, yfinance hiccup) does
    not block the analytics refresh — refresh_all_analytics will
    still run against whatever the cache currently holds.

    Returns a `landed` dict so the WarmState records WHICH rows
    succeeded — a partial success (academic landed but frontier
    didn't) is informative for debugging.
    """
    import asyncio

    from tools.cache import (
        get_latest_strategy_cache, get_latest_strategy_hash,
        set_strategy_cache, _compute_data_hash,
    )
    from tools.precomputed_analytics import (
        get_metric as get_precomputed,
        refresh_all_analytics,
    )

    # ── Strategy-cache health check ───────────────────────────────────
    latest = await get_latest_strategy_cache()
    n_strategies = len(latest or {})
    healthy = _strategy_cache_is_healthy(latest)
    log.info("analytics_cache_warm_strategy_check",
             n_strategies=n_strategies, healthy=healthy)

    if not healthy:
        # Rerun the backtester so the analytics refresh below reads
        # fresh strategy results instead of a partial-fallback row.
        try:
            from tools.data_fetcher import get_full_history_async
            from tools.backtester import run_all_strategies

            history = await get_full_history_async()
            monthly = history.get("equity_monthly")
            n_rows = len(monthly) if monthly is not None else 0
            last_date = (
                str(monthly.index[-1].date())
                if monthly is not None and len(monthly) > 0
                else "unknown"
            )
            strategy_hash = _compute_data_hash(
                n_rows, last_date, n_strategies=10)

            # run_all_strategies is sync and CPU-bound — push to a
            # worker thread so the event loop stays free for any
            # concurrent request handling during the cold-boot warm.
            results_dict = await asyncio.to_thread(
                run_all_strategies, history)
            await set_strategy_cache(
                strategy_hash, results_dict, n_observations=n_rows)
            log.info(
                "analytics_cache_warm_backtester_complete",
                strategy_hash=strategy_hash[:8],
                n_strategies=len(results_dict),
                n_observations=n_rows,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "analytics_cache_warm_backtester_failed",
                error=str(exc),
            )
            # Fall through — refresh_all_analytics still runs against
            # whatever's in the cache, even if stale. AN01 / AN04
            # may stay empty but the analytics layer is not blocked.

    # ── Analytics refresh ─────────────────────────────────────────────
    latest_hash = await get_latest_strategy_hash()
    await refresh_all_analytics(latest_hash or "")
    sentinel = latest_hash or "BOOT-WARM"
    aa = await get_precomputed(sentinel, "academic_analytics")
    ef = await get_precomputed(sentinel, "efficient_frontier")
    return {
        "academic_analytics":  bool(aa),
        "efficient_frontier":  bool(ef),
    }


def reset_for_tests() -> None:
    """Resets the singleton state — test-only helper. Production
    code paths never call this; tests use it to start each case
    with a known-clean state."""
    global _STATE
    _STATE = WarmState()
