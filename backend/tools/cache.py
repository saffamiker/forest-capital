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
                    return {
                        "threshold_regime": result[0],
                        "hmm_regime": result[1],
                        "hmm_probabilities": result[2],
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
            await session.execute(
                text(
                    "INSERT INTO regime_signals_cache "
                    "(threshold_regime, hmm_regime, hmm_probabilities, regimes_agree, "
                    " vix_level, yield_curve_slope, credit_spread, equity_trend, "
                    " pre_2022_avg_correlation, post_2022_avg_correlation, "
                    " fetched_at, expires_at) "
                    "VALUES (:tr, :hr, :hp, :ra, :vix, :yc, :cs, :et, :p22, :po22, now(), :exp)"
                ),
                {
                    "tr": regime_data.get("threshold_regime"),
                    "hr": regime_data.get("hmm_regime"),
                    "hp": regime_data.get("hmm_probabilities"),
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
