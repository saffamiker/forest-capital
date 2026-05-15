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

    Shape: {"dates": [...], "equity": [...], "ig": [...], "hy": [...]}.
    Returns None if the table is unavailable or empty.
    """
    if not _DB_AVAILABLE:
        return None
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            rows = await session.execute(
                text(
                    "SELECT date, equity_return, ig_return, hy_return "
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
            }
    except Exception as exc:
        log.warning("monthly_returns_read_error", error=str(exc))
    return None


async def set_strategy_cache(
    strategy_hash: str,
    results: dict[str, Any],
    n_observations: int | None = None,
) -> None:
    """
    Upserts strategy results into the cache.  Called after recomputation
    so the next restart returns instantly.
    """
    if not _DB_AVAILABLE:
        return
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
    except Exception as exc:
        log.warning("strategy_cache_write_error", error=str(exc))


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
) -> None:
    """
    Appends a new QA verdict row. Never updates an existing row — the
    table is an immutable audit log so the Admin screen can show the
    full history of tier upgrades over a single strategy_hash.

    TTL hours per tier come from qa_tiered.TIER_TTL_HOURS so the two
    modules can never disagree on freshness windows.
    """
    if not _DB_AVAILABLE:
        return
    try:
        from sqlalchemy import text
        from tools.qa_tiered import TIER_TTL_HOURS
        ttl_hours = TIER_TTL_HOURS.get(int(tier), 24)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
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
