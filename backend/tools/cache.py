"""
tools/cache.py

PostgreSQL-backed caching for the two most expensive backend operations:
  1. strategy_results_cache  — caches run_all_strategies() output by a hash
     of the underlying market data.  A cache hit saves 30s+ of computation
     on every cold start.  The hash changes only when new monthly data is
     appended, so historical recalculations are never triggered.

  2. regime_signals_cache   — caches /api/regime/current for 15 minutes.
     Without this, a FRED timeout (30-60s) blocks every dashboard load.
     The DB row survives Render restarts; the in-process dict in
     regime_detector.py does not.

Both caches are write-through: on a miss the result is computed and written
to the DB before being returned.  A subsequent restart finds the row
and returns instantly without touching FRED or the backtester.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
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
except Exception:
    pass


# ── Off-loop write engine ─────────────────────────────────────────────────────
# database.py's engine uses a connection POOL. An asyncpg connection is bound to
# the event loop it was created on; checked back into the pool after an
# asyncio.run() loop closes, it is orphaned on a dead loop, and its eventual
# teardown emits "coroutine 'Connection._cancel' was never awaited".
#
# QA Tier 2/3 cache writes run in a background thread via asyncio.run() (see
# main.py's _writer and qa_tiered._tier2_run_and_cache) — exactly that case.
# This NullPool engine retains no connection between checkouts, so each write
# opens and closes a fresh connection entirely within its own loop. It mirrors
# data_fetcher._get_readonly_engine; the engine object lives for the process.
_write_engine = None  # lazily-created NullPool AsyncEngine | None


def _get_write_engine():
    """Process-wide NullPool engine for DB writes issued OUTSIDE the FastAPI
    event loop (a background-thread asyncio.run). None when DATABASE_URL is
    unset — callers fall back gracefully."""
    global _write_engine
    from database import DATABASE_URL
    if not DATABASE_URL:
        return None
    if _write_engine is None:
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool
        _write_engine = create_async_engine(
            DATABASE_URL, echo=False, poolclass=NullPool,
        )
        log.info("write_engine_created")
    return _write_engine


def _compute_data_hash(n_rows: int, last_date: str, n_strategies: int) -> str:
    """
    Stable hash of pipeline inputs.  Changes only when new monthly rows
    are appended — not on every restart.  Using row count + last date is
    faster than hashing all 300 rows and captures the same signal.
    """
    key = f"{n_rows}:{last_date}:{n_strategies}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


async def get_strategy_cache(strategy_hash: str) -> dict[str, Any] | None:
    """
    Returns cached strategy results if the hash matches, else None.
    A miss means the pipeline input has changed and recomputation is needed.
    """
    if not _DB_AVAILABLE:
        return None
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            row = await session.execute(
                text(
                    "SELECT results_json FROM strategy_results_cache "
                    "WHERE strategy_hash = :h LIMIT 1"
                ),
                {"h": strategy_hash},
            )
            result = row.fetchone()
            if result:
                log.info("strategy_cache_hit", strategy_hash=strategy_hash)
                return dict(result[0])
    except Exception as exc:
        log.warning("strategy_cache_read_error", error=str(exc))
    return None


async def get_latest_strategy_cache() -> dict[str, Any] | None:
    """
    Returns the MOST RECENTLY computed strategy results, regardless of
    which data hash produced them.

    Used by the efficient-frontier endpoint to plot the ten strategies'
    (volatility, return) coordinates. Unlike get_strategy_cache() it does
    not require the caller to know the current data hash — so it needs
    neither get_full_history() nor run_all_strategies(), keeping an
    optimize request light. A marginally stale point set is acceptable
    here: the scatter is a visual reference and /api/backtest/compare
    keeps this table current on every dashboard load.
    """
    if not _DB_AVAILABLE:
        return None
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            row = await session.execute(
                text(
                    "SELECT results_json FROM strategy_results_cache "
                    "ORDER BY computed_at DESC LIMIT 1"
                )
            )
            result = row.fetchone()
            if result:
                return dict(result[0])
    except Exception as exc:
        log.warning("strategy_cache_latest_read_error", error=str(exc))
    return None


async def get_monthly_returns() -> dict[str, list[Any]] | None:
    """
    Returns the equity / IG / HY monthly return series from
    market_data_monthly — the SAME three-asset universe the ten strategies
    are built on.

    Used by /api/optimize/weights to compute an efficient frontier that is
    directly comparable to the strategy scatter: same assets, same monthly
    frequency, no yfinance dependency (yfinance drops tickers to NaN from
    Render's cloud IPs, and SPY/TLT/IEF/GLD were a different universe from
    the strategies anyway — the cause of the curve/scatter disconnect).

    Shape: {"dates": [...], "equity": [...], "ig": [...], "hy": [...],
            "rf": [...]}. Returns None if the table is unavailable or empty.

    SOFT LEAK 2 (post-submission backlog -- June 27 2026):
    this read is not hash-aware. Under freeze, market_data_monthly
    extends to May 2026 so STUDY_MONTHS=287 and rolling correlation
    tokens reflect live data rather than the freeze date. Values
    are academically correct (the study genuinely uses 287 months)
    but the freeze boundary is not structurally enforced here.
    Fix: make get_monthly_returns accept a data_hash arg and
    filter rows by the hash's max_date from the market data
    fingerprint. Tracked in audit findings dated 2026-06-27,
    superseding the June 27 SOFT LEAK 2 docstring in
    tools/academic_export.gather_document_data which made the
    same observation.
    """
    if not _DB_AVAILABLE:
        return None
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            rows = await session.execute(
                text(
                    "SELECT date, equity_return, ig_return, hy_return, "
                    "       risk_free_rate "
                    "FROM market_data_monthly ORDER BY date"
                )
            )
            fetched = rows.fetchall()
            if not fetched:
                return None
            return {
                "dates":  [str(r[0]) for r in fetched],
                "equity": [float(r[1]) for r in fetched],
                "ig":     [float(r[2]) for r in fetched],
                "hy":     [float(r[3]) for r in fetched],
                "rf":     [float(r[4]) if r[4] is not None else 0.0 for r in fetched],
            }
    except Exception as exc:
        log.warning("monthly_returns_read_error", error=str(exc))
    return None


async def get_ff_factors() -> list[dict[str, Any]] | None:
    """
    Returns the Fama-French monthly factors from ff_factors_monthly —
    [{yyyymm, mkt_rf, smb, hml, mom, rf}, ...] ordered by month.

    Used by the analytics layer's factor-loadings regression. mom is
    nullable — it is absent for the earliest months that predate the
    momentum-factor backfill — so it is returned as None where missing
    and the regression drops those rows. Returns None if the table is
    unavailable or empty.
    """
    if not _DB_AVAILABLE:
        return None
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            rows = await session.execute(
                text(
                    "SELECT yyyymm, mkt_rf, smb, hml, mom, rf "
                    "FROM ff_factors_monthly ORDER BY yyyymm"
                )
            )
            fetched = rows.fetchall()
            if not fetched:
                return None
            return [
                {
                    "yyyymm": int(r[0]),
                    "mkt_rf": float(r[1]),
                    "smb":    float(r[2]),
                    "hml":    float(r[3]),
                    "mom":    float(r[4]) if r[4] is not None else None,
                    "rf":     float(r[5]) if r[5] is not None else 0.0,
                }
                for r in fetched
            ]
    except Exception as exc:
        log.warning("ff_factors_read_error", error=str(exc))
    return None


_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _display_label(iso_date: str | None) -> str | None:
    """A human month-year label for a table's max_date — the data
    currency indicator shows '2026-04-30' as 'April 2026'."""
    if not iso_date:
        return None
    try:
        parts = str(iso_date).split("-")
        return f"{_MONTH_NAMES[int(parts[1]) - 1]} {int(parts[0])}"
    except Exception:  # noqa: BLE001
        return None


async def get_data_status() -> dict[str, Any]:
    """
    Read-only status of the data tables feeding the analytics layer:
    row count, data date range, last-updated timestamp where the table
    carries one, a green/amber/red staleness pill, and a human
    display_label of the newest data month ("April 2026").

    Staleness keys off the newest data date vs today:
      red   — newest data > 30 days behind
      amber — 15 to 30 days behind
      green — within 15 days
    Used by the Settings → Data and Study Period section.
    """
    empty = {"available": False, "study_period": None, "tables": []}
    if not _DB_AVAILABLE:
        return empty

    from sqlalchemy import text

    now = datetime.now(timezone.utc)

    def _to_dt(v: Any) -> datetime | None:
        if v is None:
            return None
        if isinstance(v, datetime):
            return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)

    def _staleness(max_dt: datetime | None) -> str:
        if max_dt is None:
            return "unknown"
        days = (now - max_dt).days
        return "red" if days > 30 else ("amber" if days >= 15 else "green")

    def _ym_to_date(ym: Any) -> datetime | None:
        """Year-month integer (e.g. 200207) → last day of that month."""
        if not ym:
            return None
        y, m = int(ym) // 100, int(ym) % 100
        first_next = datetime(y + (m // 12), (m % 12) + 1, 1, tzinfo=timezone.utc)
        return first_next - timedelta(days=1)

    tables: list[dict[str, Any]] = []
    try:
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            # Tables keyed by a DATE column. Names are hardcoded constants,
            # not user input — safe to interpolate.
            for name in ("market_data_monthly", "market_data_daily"):
                try:
                    r = await session.execute(text(
                        f"SELECT COUNT(*), MIN(date), MAX(date) FROM {name}"))
                    cnt, mn, mx = r.fetchone()
                    tables.append({
                        "name": name,
                        "row_count": int(cnt or 0),
                        "min_date": str(mn) if mn else None,
                        "max_date": str(mx) if mx else None,
                        "last_updated": None,
                        "staleness": _staleness(_to_dt(mx)) if cnt else "unknown",
                    })
                except Exception as exc:
                    log.warning("data_status_table_error", table=name, error=str(exc))

            # ff_factors_monthly — keyed by a yyyymm integer.
            try:
                r = await session.execute(text(
                    "SELECT COUNT(*), MIN(yyyymm), MAX(yyyymm) FROM ff_factors_monthly"))
                cnt, mn, mx = r.fetchone()
                mx_dt = _ym_to_date(mx)
                mn_dt = _ym_to_date(mn)
                tables.append({
                    "name": "ff_factors_monthly",
                    "row_count": int(cnt or 0),
                    "min_date": mn_dt.date().isoformat() if mn_dt else None,
                    "max_date": mx_dt.date().isoformat() if mx_dt else None,
                    "last_updated": None,
                    "staleness": _staleness(mx_dt) if cnt else "unknown",
                })
            except Exception as exc:
                log.warning("data_status_table_error", table="ff_factors_monthly", error=str(exc))

            # Tables keyed by a write-timestamp — strategy results and
            # uploaded academic documents.
            for name, tcol in (("strategy_results_cache", "computed_at"),
                               ("academic_documents", "uploaded_at")):
                try:
                    r = await session.execute(text(
                        f"SELECT COUNT(*), MIN({tcol}), MAX({tcol}) FROM {name}"))
                    cnt, mn, mx = r.fetchone()
                    tables.append({
                        "name": name,
                        "row_count": int(cnt or 0),
                        "min_date": mn.date().isoformat() if mn else None,
                        "max_date": mx.date().isoformat() if mx else None,
                        "last_updated": mx.isoformat() if mx else None,
                        "staleness": _staleness(_to_dt(mx)) if cnt else "unknown",
                    })
                except Exception as exc:
                    log.warning("data_status_table_error", table=name, error=str(exc))
    except Exception as exc:
        log.warning("data_status_failed", error=str(exc))
        return empty

    # A human "April 2026" label per table — the data currency indicator
    # on each visualisation screen reads it instead of reformatting dates.
    for t in tables:
        t["display_label"] = _display_label(t.get("max_date"))

    # Study period — derived from market_data_monthly's range.
    study_period = None
    mdm = next((t for t in tables if t["name"] == "market_data_monthly"), None)
    if mdm and mdm["row_count"]:
        study_period = {
            "start": mdm["min_date"],
            "end": mdm["max_date"],
            "n_months": mdm["row_count"],
        }

    return {"available": True, "study_period": study_period, "tables": tables}


async def set_strategy_cache(
    strategy_hash: str,
    results: dict[str, Any],
    n_observations: int | None = None,
    *,
    risk_free_monthly: Any = None,
) -> None:
    """
    Upserts strategy results into the cache.  Called after recomputation
    so the next restart returns instantly.

    May 28 2026 — empty-results guard. AN01 (factor_loadings) and AN04
    (regime_conditional) read this cache and produce empty downstream
    payloads when every strategy has empty `monthly_returns` (the
    fallback path's signature). A backtester run that returned only
    mock fallbacks should not overwrite a known-good cache row — the
    prior row stays in place and the broken run's symptoms are
    visible in the strategies_fallback_summary log line. The guard
    fires only when EVERY strategy has an empty (or missing)
    monthly_returns array — a partial fallback (one or two strategies
    broken) still writes through, because the downstream analytics
    correctly skip empty-series strategies row by row.
    """
    if not _DB_AVAILABLE:
        return
    # Empty-results guard — refuse to overwrite a known-good cache row
    # with a run where every strategy is a fallback.
    if results:
        total = len(results)
        empties = sum(
            1 for r in results.values()
            if not (r or {}).get("monthly_returns")
        )
        if empties == total:
            log.warning(
                "strategy_cache_write_refused_all_empty",
                strategy_hash=strategy_hash,
                n_strategies=total,
                reason="every strategy has empty monthly_returns; "
                       "preserving the prior known-good cache row",
            )
            return
    # ── Invariant pre-write gate (May 30 2026) ───────────────────────
    # The data-level half of the framework (Cat 1, Cat 5) runs against
    # the in-memory results BEFORE the row is committed. A hard
    # failure aborts the write, preserves the previous cache row, and
    # logs `invariant_hard_failure` per assertion. Catches the F3 class
    # of bug at write time rather than at display time.
    #
    # Wrapped in try/except so a framework defect itself can never
    # take the warm offline — a runner exception is logged and the
    # write proceeds (the framework is a safety net, not the
    # primary correctness layer).
    try:
        from tools.invariant_checks import run_all_invariants
        invariant_result = run_all_invariants(
            results, risk_free_rate=risk_free_monthly)
        # Persist the summary so a separate process (the daily digest
        # cron) can read the verdict — module-level `_latest_result`
        # is in-memory only and gets wiped on every Render redeploy.
        # Fail-open: a write failure logs and never blocks the cache
        # path. June 2 2026 digest fix.
        try:
            from tools.precomputed_analytics import set_metric
            payload = invariant_result.to_dict()
            from datetime import datetime, timezone as _tz
            payload["ran_at"] = datetime.now(_tz.utc).isoformat()
            await set_metric(
                strategy_hash or "BOOT-WARM",
                "invariant_summary",
                payload,
                source="set_strategy_cache_invariant_persist")
        except Exception as persist_exc:  # noqa: BLE001
            log.warning("invariant_summary_persist_failed",
                        error=str(persist_exc))
        if not invariant_result.passed:
            log.warning(
                "strategy_cache_write_refused_invariants",
                strategy_hash=strategy_hash,
                hard_failures=len(invariant_result.hard_failures),
                soft_warnings=len(invariant_result.soft_warnings),
                first_failure=(
                    invariant_result.hard_failures[0].to_dict()
                    if invariant_result.hard_failures else None),
                reason="invariant hard-failure(s); preserving the "
                       "prior cache row and aborting the write",
            )
            return
    except Exception as inv_exc:  # noqa: BLE001
        log.warning("invariant_runner_failed",
                    error=str(inv_exc),
                    note=("Invariant framework raised; proceeding "
                          "with cache write to avoid taking the warm "
                          "offline. Fix the runner."))
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            await session.execute(
                text(
                    "INSERT INTO strategy_results_cache "
                    "(strategy_hash, results_json, n_strategies, n_observations) "
                    "VALUES (:h, :r, :n, :obs) "
                    "ON CONFLICT (strategy_hash) DO UPDATE "
                    "SET results_json = EXCLUDED.results_json, "
                    "    computed_at = now(), "
                    "    n_strategies = EXCLUDED.n_strategies, "
                    "    n_observations = EXCLUDED.n_observations"
                ),
                {
                    "h": strategy_hash,
                    "r": json.dumps(results),
                    "n": len(results),
                    "obs": n_observations,
                },
            )
            await session.commit()
            log.info("strategy_cache_written", strategy_hash=strategy_hash, n_strategies=len(results))
        # Bust the get_latest_strategy_hash memo so the new hash is
        # picked up immediately — otherwise the next batch of
        # analytics requests within the 5-second TTL would still see
        # the previous hash and miss the freshly-refreshed metrics.
        _hash_memo_clear()
        # Item 7 (May 22 2026) — fire the pre-computed analytics
        # refresh so the next /api/v1/analytics/academic request
        # reads a single cached row instead of recomputing 7 NumPy
        # reductions inline. Fire-and-forget: the strategy cache
        # write returns immediately; the analytics row appears
        # within a second or two. Item 8 (diversification suite)
        # adds more metric_kinds to the same refresh.
        try:
            from tools.precomputed_analytics import trigger_refresh_async
            trigger_refresh_async(strategy_hash)
        except Exception as exc:  # noqa: BLE001
            log.warning("analytics_refresh_spawn_failed", error=str(exc))
    except Exception as exc:
        log.warning("strategy_cache_write_error", error=str(exc))


async def clear_strategy_cache() -> int:
    """
    Deletes every strategy_results_cache row so the backtester recomputes
    from fresh data on the next request — used after a data update or to
    repopulate results that predate a new result-dict field (e.g. the
    weight_schedule). Returns the number of rows removed; fail-open to 0.
    """
    if not _DB_AVAILABLE:
        return 0
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            res = await session.execute(
                text("DELETE FROM strategy_results_cache"))
            await session.commit()
            removed = res.rowcount or 0
            log.info("strategy_cache_cleared", rows=removed)
        # The cleared table has no hash anymore — drop the memo so
        # the next read goes to the DB and reflects the empty state.
        _hash_memo_clear()
        return removed
    except Exception as exc:
        log.warning("strategy_cache_clear_error", error=str(exc))
        return 0


# ── Strategy-hash memo (F2 fix, May 22 2026) ──────────────────────────────────
# The seven diversification analytics endpoints each call
# get_latest_strategy_hash() at the top of their three-tier read. When
# the Analytics page mounts, those seven endpoints fire in parallel —
# seven identical 'SELECT strategy_hash ... ORDER BY computed_at DESC
# LIMIT 1' queries within a few ms of each other. A small in-process
# TTL memo coalesces them into one DB hit.
#
# Why module-level (not per-request): the seven requests are seven
# distinct HTTP requests, each with its own request.state — a per-
# request decorator would memoise within one request (where the value
# is fetched exactly once anyway) and do nothing for the cross-request
# duplication. A short module-level TTL is the only shape that
# eliminates the seven duplicate queries the audit identified.
#
# Why 5 seconds: the hash only changes when new strategies are
# computed (rare — a data ingestion event). A 5-second staleness window
# is fine: at worst, one batch of analytics requests right after a fresh
# ingestion sees the previous hash and falls back to the stale-cache
# row in analytics_metrics_cache — still valid data, just one
# generation behind. The refresh hook fires asynchronously after the
# strategy write, so subsequent batches pick up the new hash.
_HASH_MEMO_TTL_SECONDS = 5.0
_hash_memo: dict[str, tuple[float, str | None]] = {}


def _hash_memo_clear() -> None:
    """Drops the in-process strategy_hash memo. Called by tests and by
    the strategy_cache write path so a fresh ingestion is picked up
    immediately rather than waiting out the TTL."""
    _hash_memo.clear()


async def get_latest_strategy_hash() -> str | None:
    """
    The strategy_hash of the most recently computed strategy_results_cache
    row. Smart audit caching compares it against the latest QA verdict's
    hash to tell whether the methodology audit is still current. None when
    no strategies have been computed (or on a database error — fail-open).

    Memoised for 5 seconds in-process to coalesce the seven parallel
    diversification endpoint calls that fire on Analytics page mount.
    The strategy hash changes only on data ingestion; a short TTL is
    cheap insurance against a thundering herd.
    """
    now = time.monotonic()
    cached = _hash_memo.get("latest")
    if cached and (now - cached[0]) < _HASH_MEMO_TTL_SECONDS:
        return cached[1]
    if not _DB_AVAILABLE:
        _hash_memo["latest"] = (now, None)
        return None
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            row = await session.execute(text(
                "SELECT strategy_hash FROM strategy_results_cache "
                "ORDER BY computed_at DESC LIMIT 1"))
            r = row.fetchone()
            value = str(r[0]) if r and r[0] else None
            _hash_memo["latest"] = (now, value)
            return value
    except Exception as exc:
        log.warning("latest_strategy_hash_read_error", error=str(exc))
    return None


async def get_latest_qa_hash() -> str | None:
    """
    The strategy_hash of the most recent NON-EXPIRED qa_results_cache row
    — the data block the latest methodology audit verified. The QA audit
    is "current" when this matches get_latest_strategy_hash(). None when
    no fresh QA verdict exists (or on a database error — fail-open).
    """
    if not _DB_AVAILABLE:
        return None
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            row = await session.execute(text(
                "SELECT strategy_hash FROM qa_results_cache "
                "WHERE expires_at > now() ORDER BY run_at DESC LIMIT 1"))
            r = row.fetchone()
            return str(r[0]) if r and r[0] else None
    except Exception as exc:
        log.warning("latest_qa_hash_read_error", error=str(exc))
    return None


async def get_most_recent_qa_run(
    min_tier: int = 1,
) -> dict[str, Any] | None:
    """
    The MOST RECENT QA verdict in the cache REGARDLESS OF strategy_hash —
    used by the /api/qa/audit minimum-interval guard to cap token burn.
    `get_latest_qa(hash)` filters to a specific hash; this helper does
    not. Returns None if the table is empty or on any error (fail-open).

    Shape mirrors get_latest_qa: {tier, verdict, checklist, run_at,
    expires_at, strategy_hash}.
    """
    if not _DB_AVAILABLE:
        return None
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            row = await session.execute(
                text(
                    "SELECT tier, verdict, checklist_json, run_at, "
                    "       expires_at, strategy_hash "
                    "FROM qa_results_cache "
                    "WHERE tier >= :mt "
                    "ORDER BY run_at DESC LIMIT 1"
                ),
                {"mt": min_tier},
            )
            r = row.fetchone()
            if not r:
                return None
            checklist = r[2] if isinstance(r[2], dict) else json.loads(r[2])
            return {
                "tier":           int(r[0]),
                "verdict":        str(r[1]),
                "checklist":      checklist,
                "run_at":         r[3].isoformat() if r[3] else None,
                "expires_at":     r[4].isoformat() if r[4] else None,
                "strategy_hash":  str(r[5]) if r[5] else "",
            }
    except Exception as exc:
        log.warning("most_recent_qa_read_error", error=str(exc))
    return None


async def get_regime_cache() -> dict[str, Any] | None:
    """
    Returns cached regime signals if not expired, else None.
    Expiry is stored in the DB row so it survives restarts.
    """
    if not _DB_AVAILABLE:
        return None
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            row = await session.execute(
                text(
                    "SELECT threshold_regime, hmm_regime, hmm_probabilities, "
                    "       regimes_agree, vix_level, yield_curve_slope, "
                    "       credit_spread, equity_trend, "
                    "       pre_2022_avg_correlation, post_2022_avg_correlation, "
                    "       fetched_at, expires_at "
                    "FROM regime_signals_cache "
                    "ORDER BY fetched_at DESC LIMIT 1"
                )
            )
            result = row.fetchone()
            if result:
                # Check expiry against current UTC time
                expires_at = result[11]
                if expires_at and expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                if expires_at and now < expires_at:
                    log.info("regime_db_cache_hit", expires_at=str(expires_at))
                    # hmm_probabilities is JSONB after migration 007 and
                    # asyncpg normally returns it as a Python dict already.
                    # On configurations that route the JSONB column through
                    # a Text codec it can come back as a string instead —
                    # defensively decode so the API always returns the
                    # canonical dict shape the frontend expects.
                    hp_raw = result[2]
                    if isinstance(hp_raw, str):
                        try:
                            hp_value = json.loads(hp_raw)
                        except json.JSONDecodeError:
                            hp_value = None
                    else:
                        hp_value = hp_raw
                    return {
                        "threshold_regime": result[0],
                        "hmm_regime": result[1],
                        "hmm_probabilities": hp_value,
                        "regimes_agree": result[3],
                        "vix_level": result[4],
                        "yield_curve_slope": result[5],
                        "credit_spread": result[6],
                        "equity_trend": result[7],
                        "pre_2022_avg_correlation": result[8],
                        "post_2022_avg_correlation": result[9],
                        # When these signals were fetched — lets the UI show
                        # an "as of" time so a 15-min-cached value is never
                        # mistaken for a live reading.
                        "as_of": result[10].isoformat() if result[10] else None,
                    }
    except Exception as exc:
        log.warning("regime_db_cache_read_error", error=str(exc))
    return None


async def set_regime_cache(regime_data: dict[str, Any], ttl_minutes: int = 15) -> None:
    """
    Writes regime signals to the DB cache with a 15-minute TTL.
    The row survives Render restarts — on the next cold start the first
    /api/regime/current call returns instantly from the DB.
    """
    if not _DB_AVAILABLE:
        return
    try:
        from sqlalchemy import text
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=ttl_minutes)
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            # hmm_probabilities is JSONB after migration 007 (was
            # ARRAY(Float) before — see migration 007 docstring for the
            # mismatch story). The detector emits a dict
            # {"BULL": 0.82, ...}; we json.dumps it here and CAST it on
            # the SQL side so the bound parameter is unambiguously a
            # JSON value. The same pattern is used for
            # strategy_results_cache.results_json elsewhere in this file.
            # Defensively json.dumps(None) → "null" which JSONB accepts;
            # an unset hmm_probabilities round-trips correctly.
            hp_raw = regime_data.get("hmm_probabilities")
            hp_json = json.dumps(hp_raw) if hp_raw is not None else None
            await session.execute(
                text(
                    "INSERT INTO regime_signals_cache "
                    "(threshold_regime, hmm_regime, hmm_probabilities, regimes_agree, "
                    " vix_level, yield_curve_slope, credit_spread, equity_trend, "
                    " pre_2022_avg_correlation, post_2022_avg_correlation, "
                    " fetched_at, expires_at) "
                    "VALUES (:tr, :hr, CAST(:hp AS JSONB), :ra, :vix, :yc, :cs, :et, "
                    "        :p22, :po22, now(), :exp)"
                ),
                {
                    "tr": regime_data.get("threshold_regime"),
                    "hr": regime_data.get("hmm_regime"),
                    "hp": hp_json,
                    "ra": regime_data.get("regimes_agree", True),
                    "vix": regime_data.get("vix_level"),
                    "yc": regime_data.get("yield_curve_slope"),
                    "cs": regime_data.get("credit_spread"),
                    "et": regime_data.get("equity_trend"),
                    "p22": regime_data.get("pre_2022_avg_correlation"),
                    "po22": regime_data.get("post_2022_avg_correlation"),
                    "exp": expires_at,
                },
            )
            await session.commit()
            log.info("regime_db_cache_written", expires_at=str(expires_at))
    except Exception as exc:
        log.warning("regime_db_cache_write_error", error=str(exc))


async def log_auth_attempt(
    email: str,
    ip_address: str | None,
    user_agent: str | None,
    status: str,
    geo: dict[str, str | None] | None = None,
) -> None:
    """
    Records every /auth/request-link call for the /admin screen.
    Fail-open: if the DB write fails, the auth flow is not affected.
    """
    if not _DB_AVAILABLE:
        return
    try:
        from sqlalchemy import text
        geo = geo or {}
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            await session.execute(
                text(
                    "INSERT INTO auth_attempts "
                    "(email, ip_address, user_agent, country, country_code, "
                    " city, isp, org, status) "
                    "VALUES (:e, :ip, :ua, :country, :cc, :city, :isp, :org, :status)"
                ),
                {
                    "e": email,
                    "ip": ip_address,
                    "ua": user_agent,
                    "country": geo.get("country"),
                    "cc": geo.get("country_code"),
                    "city": geo.get("city"),
                    "isp": geo.get("isp"),
                    "org": geo.get("org"),
                    "status": status,
                },
            )
            await session.commit()
    except Exception as exc:
        log.warning("auth_attempt_log_error", error=str(exc))


async def is_jti_used(jti: str) -> bool:
    """Returns True if this JTI has already been consumed (single-use enforcement)."""
    if not _DB_AVAILABLE:
        return False
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            row = await session.execute(
                text("SELECT 1 FROM used_magic_tokens WHERE jti = :jti LIMIT 1"),
                {"jti": jti},
            )
            return row.fetchone() is not None
    except Exception as exc:
        log.warning("jti_check_error", error=str(exc))
    return False


# ── QA results cache (Sprint 6 tiered QA) ──────────────────────────────

async def get_latest_qa(
    strategy_hash: str,
    min_tier: int = 1,
) -> dict[str, Any] | None:
    """
    Returns the MOST RECENT non-expired QA verdict for the given hash,
    across any tier ≥ min_tier. The Present-mode gate uses this with
    min_tier=1 — Tier 1 deterministic checks are enough to unlock Present
    mode; Tier 2 and Tier 3 simply refine the narrative.

    Returns None if no fresh verdict exists for this strategy_hash. The
    caller is responsible for triggering a fresh Tier 1 run on a miss.
    """
    if not _DB_AVAILABLE:
        return None
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            row = await session.execute(
                text(
                    "SELECT tier, verdict, checklist_json, run_at, expires_at "
                    "FROM qa_results_cache "
                    "WHERE strategy_hash = :h AND tier >= :mt AND expires_at > now() "
                    "ORDER BY run_at DESC LIMIT 1"
                ),
                {"h": strategy_hash, "mt": min_tier},
            )
            r = row.fetchone()
            if not r:
                return None
            checklist = r[2] if isinstance(r[2], dict) else json.loads(r[2])
            return {
                "tier":           int(r[0]),
                "verdict":        str(r[1]),
                "checklist":      checklist,
                "run_at":         r[3].isoformat() if r[3] else None,
                "expires_at":     r[4].isoformat() if r[4] else None,
                "strategy_hash":  strategy_hash,
            }
    except Exception as exc:
        log.warning("qa_cache_read_error", error=str(exc))
    return None


async def set_qa_cache(
    strategy_hash: str,
    verdict_payload: dict[str, Any],
    tier: int,
    off_loop: bool = False,
) -> None:
    """
    Appends a new QA verdict row. Never updates an existing row — the
    table is an immutable audit log so the Admin screen can show the
    full history of tier upgrades over a single strategy_hash.

    TTL hours per tier come from qa_tiered.TIER_TTL_HOURS so the two
    modules can never disagree on freshness windows.

    off_loop=True routes the write through the NullPool _get_write_engine
    instead of the pooled AsyncSessionLocal — required when the caller runs
    this inside a background-thread asyncio.run() (QA Tier 2/3), where a
    pooled connection would be orphaned across the loop boundary. On-loop
    callers (awaited on the FastAPI loop) leave off_loop False.
    """
    if not _DB_AVAILABLE:
        return
    try:
        from sqlalchemy import text
        from tools.qa_tiered import TIER_TTL_HOURS
        ttl_hours = TIER_TTL_HOURS.get(int(tier), 24)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
        if off_loop:
            from sqlalchemy.ext.asyncio import AsyncSession
            write_engine = _get_write_engine()
            if write_engine is None:
                return
            session_ctx: Any = AsyncSession(write_engine)
        else:
            session_ctx = AsyncSessionLocal()  # type: ignore[union-attr]
        async with session_ctx as session:
            await session.execute(
                text(
                    "INSERT INTO qa_results_cache "
                    "(tier, strategy_hash, verdict, checklist_json, expires_at) "
                    "VALUES (:t, :h, :v, :c, :e)"
                ),
                {
                    "t": int(tier),
                    "h": strategy_hash,
                    "v": verdict_payload.get("verdict", "UNKNOWN"),
                    "c": json.dumps(verdict_payload),
                    "e": expires_at,
                },
            )
            await session.commit()
            log.info(
                "qa_cache_written",
                strategy_hash=strategy_hash[:8],
                tier=tier,
                verdict=verdict_payload.get("verdict"),
            )
    except Exception as exc:
        log.warning("qa_cache_write_error", error=str(exc))


async def mark_jti_used(jti: str, expires_at: datetime, email: str) -> None:
    """Persists a consumed JTI so single-use protection survives Render restarts."""
    if not _DB_AVAILABLE:
        return
    try:
        import hashlib
        from sqlalchemy import text
        email_hash = hashlib.sha256(email.encode()).hexdigest()[:16]
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            await session.execute(
                text(
                    "INSERT INTO used_magic_tokens (jti, expires_at, email_hash) "
                    "VALUES (:jti, :exp, :eh) ON CONFLICT (jti) DO NOTHING"
                ),
                {"jti": jti, "exp": expires_at, "eh": email_hash},
            )
            await session.commit()
    except Exception as exc:
        log.warning("jti_mark_used_error", error=str(exc))
