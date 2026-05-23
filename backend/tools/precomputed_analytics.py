"""tools/precomputed_analytics.py — write/read for analytics_metrics_cache.

May 22 2026 — item 7 in the sprint queue. Backs the migration 028
analytics_metrics_cache table. The endpoint code reads a row by
(data_hash, metric_kind) and returns the payload verbatim; the
refresh hook computes the payload once when the strategy cache is
written and upserts the row.

PATTERN — every metric_kind has its own compute function in this
module, called by refresh_all_analytics on data-hash change. Adding
a new metric is two changes: a compute function here + an additional
entry in the refresh_all_analytics dispatch.

FAIL-OPEN EVERYWHERE. A database read miss returns None and the
endpoint computes inline. A compute failure during the refresh logs
and proceeds to the next metric (one bad metric does not block the
others). Mirrors the academic_context / macro_context fail-open
contract.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import structlog

log = structlog.get_logger(__name__)


async def get_metric(data_hash: str, metric_kind: str) -> dict[str, Any] | None:
    """Reads a pre-computed metric payload from the cache.

    Returns the payload dict on hit. Returns None on:
      - row not yet written for this (data_hash, metric_kind)
      - data_hash does not match any row (the latest data has not
        been refreshed yet — the cold-deploy case)
      - database unavailable

    The endpoint's fallback path runs the inline compute when this
    returns None, so the user always sees data — just slower on the
    first request after a fresh ingestion.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT payload, computed_at FROM analytics_metrics_cache "
                "WHERE data_hash = :h AND metric_kind = :k LIMIT 1"
            ), {"h": data_hash, "k": metric_kind})
            found = row.fetchone()
            if not found:
                return None
            payload = found[0]
            computed_at = found[1]
            if isinstance(payload, str):
                # asyncpg may return JSONB as a string in some setups.
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    return None
            if not isinstance(payload, dict):
                return None
            # Attach the computed_at so the endpoint can surface
            # freshness on the response.
            payload["_computed_at"] = (
                computed_at.isoformat() if computed_at else None)
            return payload
    except Exception as exc:  # noqa: BLE001
        log.warning("precomputed_analytics_read_failed",
                    metric_kind=metric_kind, error=str(exc))
        return None


async def set_metric(
    data_hash: str,
    metric_kind: str,
    payload: dict[str, Any],
    *,
    source: str | None = None,
) -> None:
    """Upserts a pre-computed metric payload into the cache.

    Idempotent — ON CONFLICT (data_hash, metric_kind) DO UPDATE so
    a re-fire of the refresh hook within the same data_hash window
    refreshes the row rather than failing.

    Strips internal underscore-prefixed keys before writing so a
    payload that was already read once (with _computed_at attached)
    can round-trip cleanly.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return
        clean = {k: v for k, v in payload.items() if not k.startswith("_")}
        async with AsyncSessionLocal() as session:
            await session.execute(text(
                "INSERT INTO analytics_metrics_cache "
                "(data_hash, metric_kind, payload, source) "
                "VALUES (:h, :k, :p, :s) "
                "ON CONFLICT (data_hash, metric_kind) DO UPDATE SET "
                " payload = EXCLUDED.payload, "
                " computed_at = now(), "
                " source = EXCLUDED.source"
            ), {
                "h": data_hash, "k": metric_kind,
                "p": json.dumps(clean, default=str),
                "s": source,
            })
            await session.commit()
        log.info("precomputed_analytics_written",
                 metric_kind=metric_kind, data_hash=data_hash[:8],
                 source=source)
    except Exception as exc:  # noqa: BLE001
        log.warning("precomputed_analytics_write_failed",
                    metric_kind=metric_kind, error=str(exc))


async def get_latest_metric(metric_kind: str) -> dict[str, Any] | None:
    """Returns the most-recently-written row for a metric_kind,
    regardless of data_hash. Used by the cold-deploy fallback when
    the current data_hash has not been refreshed yet — serving the
    last refresh is better than serving nothing.

    The endpoint surfaces _stale=True on the response when this
    fallback path is taken so the user knows the analytics are
    from a previous data ingestion.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT payload, computed_at, data_hash "
                "FROM analytics_metrics_cache "
                "WHERE metric_kind = :k "
                "ORDER BY computed_at DESC LIMIT 1"
            ), {"k": metric_kind})
            found = row.fetchone()
            if not found:
                return None
            payload = found[0]
            computed_at = found[1]
            row_hash = found[2]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    return None
            if not isinstance(payload, dict):
                return None
            payload["_computed_at"] = (
                computed_at.isoformat() if computed_at else None)
            payload["_data_hash"] = row_hash
            payload["_stale"] = True
            return payload
    except Exception as exc:  # noqa: BLE001
        log.warning("precomputed_analytics_latest_failed",
                    metric_kind=metric_kind, error=str(exc))
        return None


# ── Refresh dispatch ──────────────────────────────────────────────────────────


async def refresh_academic_analytics(data_hash: str) -> None:
    """Computes the /api/v1/analytics/academic payload and writes it
    to analytics_metrics_cache under metric_kind='academic_analytics'.

    Mirrors the inline compute path in main.get_academic_analytics
    exactly — same series prep, same 7 reductions — so the cached
    payload is bit-identical to what the endpoint would have
    produced inline. The endpoint then reads the cached row and
    returns it verbatim.
    """
    try:
        import pandas as pd
        from tools.cache import (
            get_monthly_returns, get_latest_strategy_cache, get_ff_factors,
        )
        from tools import analytics as an
        from strategy_metadata import STRATEGY_METADATA

        monthly = await get_monthly_returns()
        strategies = await get_latest_strategy_cache()
        ff = await get_ff_factors()
        if not monthly or not strategies:
            return

        idx = pd.to_datetime(monthly["dates"])
        equity = pd.Series(monthly["equity"], index=idx)
        ig = pd.Series(monthly["ig"], index=idx)
        hy = pd.Series(monthly["hy"], index=idx)
        rf = pd.Series(monthly["rf"], index=idx)

        benchmark = strategies.get("BENCHMARK", {})
        bench_series = an._pairs_to_series(
            benchmark.get("monthly_returns") or [])

        asset_series = {"EQUITY": equity, "IG": ig, "HY": hy}
        if not bench_series.empty:
            asset_series["BENCHMARK"] = bench_series

        payload = {
            "available": True,
            "study_period": {
                "start": str(idx[0].date()),
                "end": str(idx[-1].date()),
                "n_months": len(idx),
            },
            "summary_statistics": an.summary_statistics(asset_series, rf),
            "cumulative_returns": an.cumulative_returns(strategies),
            "rolling_correlation": an.rolling_correlation(
                equity, ig, hy, window=12),
            "rolling_excess_return": an.rolling_excess_return(
                strategies, window=12),
            "regime_conditional": an.regime_conditional_performance(
                strategies, rf),
            "drawdown_comparison": an.drawdown_comparison(strategies),
            "factor_loadings": an.factor_loadings(strategies, ff or []),
            "strategy_metadata": STRATEGY_METADATA,
        }
        await set_metric(data_hash, "academic_analytics", payload,
                         source="refresh_academic_analytics")
    except Exception as exc:  # noqa: BLE001
        log.warning("precomputed_academic_analytics_failed",
                    error=str(exc))


async def refresh_diversification_metrics(data_hash: str) -> None:
    """Item 8 — the seven diversification suite metrics. Pure NumPy /
    pandas / scipy; same data sources as refresh_academic_analytics.
    Each metric is its own metric_kind row in analytics_metrics_cache
    so the corresponding endpoint reads exactly one indexed row.

    Fail-open per metric — one bad compute does not block the others.
    """
    try:
        from tools.cache import get_latest_strategy_cache
        from tools import diversification_analytics as div

        strategies = await get_latest_strategy_cache()
        if not strategies:
            return

        # Each metric is independently try/except'd so one failure
        # doesn't sink the rest. The cache row is left absent on a
        # failure; the endpoint falls back to inline compute.
        computations = [
            ("correlation_matrices",
             lambda: div.correlation_matrices(strategies)),
            ("tail_risk",
             lambda: {"strategies": div.tail_risk(strategies)}),
            ("capture_ratios",
             lambda: {"strategies": div.capture_ratios(strategies)}),
            ("drawdown_duration",
             lambda: {"strategies": div.drawdown_duration(strategies)}),
            ("crisis_performance",
             lambda: div.crisis_performance(strategies)),
            ("marginal_contribution_to_risk",
             lambda: div.marginal_contribution_to_risk(
                 strategies, tangency_weights=None)),
            ("return_distribution",
             lambda: {"strategies": div.return_distribution(strategies)}),
        ]
        for metric_kind, fn in computations:
            try:
                payload = fn()
                await set_metric(
                    data_hash, metric_kind, payload,
                    source="refresh_diversification_metrics")
            except Exception as exc:  # noqa: BLE001
                log.warning("diversification_metric_failed",
                            metric_kind=metric_kind, error=str(exc))
        # After all metric rows land, refresh the agent-context cache
        # so the council / academic_review / explainer prompts see
        # the new diversification numbers on the next agent call.
        try:
            from tools.diversification_context import (
                refresh_diversification_context,
            )
            await refresh_diversification_context()
        except Exception as exc:  # noqa: BLE001
            log.warning("diversification_context_refresh_failed",
                        error=str(exc))
        # Item 5 (May 23 2026 — analytics narrative context). The
        # narrative layer rides the same refresh tick as the
        # structured diversification block so the three context
        # layers stay in step.
        try:
            from tools.analytics_context import refresh_analytics_context
            await refresh_analytics_context()
        except Exception as exc:  # noqa: BLE001
            log.warning("analytics_context_refresh_failed",
                        error=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.warning("diversification_refresh_failed", error=str(exc))


async def refresh_sensitivity(data_hash: str) -> None:
    """Parameter sensitivity (F1, May 22 2026). Runs compute_sensitivity
    on the FULL pipeline history — the same ~23-backtest computation
    the endpoint used to do inline on the request thread. By caching
    the result here, the endpoint becomes a single-row DB read on the
    hot path (get_full_history + compute_sensitivity never run inside
    a request handler).

    The compute_sensitivity helper has its own worker-local memo, so
    a refresh that re-runs on the same history is also cheap. The
    expensive work is the cold-deploy first call after each data
    ingestion.
    """
    try:
        from tools.data_fetcher import get_full_history
        from tools.sensitivity import compute_sensitivity

        history = get_full_history()
        if not history or not history.get("equity_monthly"):
            log.info("precomputed_sensitivity_no_history")
            return
        result = compute_sensitivity(history)
        payload = {"available": True, **result}
        await set_metric(data_hash, "sensitivity", payload,
                         source="refresh_sensitivity")
    except Exception as exc:  # noqa: BLE001
        log.warning("precomputed_sensitivity_failed", error=str(exc))


async def refresh_risk_free_rate_config(data_hash: str) -> None:
    """Risk-free rate config (F4, May 22 2026). The /api/v1/analytics/
    config endpoint computes the mean monthly DTB3 rate × 12 inline
    on every request. The value depends solely on the data hash, so
    cache it alongside the other metrics.

    Single scalar plus its source label; the cached row is tiny but
    eliminates the per-request 280-row sum + multiply on the
    request thread.
    """
    try:
        from tools.cache import get_monthly_returns

        monthly = await get_monthly_returns()
        rf_list = (monthly or {}).get("rf") or []
        rf_annual = (sum(rf_list) / len(rf_list) * 12) if rf_list else None
        payload = {
            "available": rf_annual is not None,
            "risk_free_rate":
                round(rf_annual, 4) if rf_annual is not None else None,
            "risk_free_source": (
                "FRED DTB3 (3-month T-bill, mean monthly rate, annualised)"),
        }
        await set_metric(data_hash, "risk_free_rate_config", payload,
                         source="refresh_risk_free_rate_config")
    except Exception as exc:  # noqa: BLE001
        log.warning("precomputed_risk_free_rate_config_failed",
                    error=str(exc))


async def refresh_all_analytics(data_hash: str) -> None:
    """Top-level dispatch — calls every refresh function. Fires from
    tools/cache.set_strategy_cache after a successful strategy write.
    Fail-open per-metric so one bad compute does not block the others.
    """
    log.info("precomputed_analytics_refresh_started",
             data_hash=data_hash[:8] if data_hash else None)
    await refresh_academic_analytics(data_hash)
    await refresh_diversification_metrics(data_hash)
    # F1 + F4 (May 22 2026) — sensitivity and the risk-free rate
    # config are the last two endpoints that did inline compute on
    # the request thread. Both now refresh through this dispatch and
    # serve from analytics_metrics_cache on the hot path.
    await refresh_sensitivity(data_hash)
    await refresh_risk_free_rate_config(data_hash)
    # Item 9 (May 22 2026) — strategy_characterisations is its own
    # table (one row per strategy) rather than another row in
    # analytics_metrics_cache, but it refreshes on the same data-
    # hash signal. Fail-open per strategy inside the refresh.
    try:
        from tools.strategy_characterisations import (
            refresh_strategy_characterisations,
        )
        await refresh_strategy_characterisations(data_hash)
    except Exception as exc:  # noqa: BLE001
        log.warning("strategy_characterisation_dispatch_failed",
                    error=str(exc))
    log.info("precomputed_analytics_refresh_complete",
             data_hash=data_hash[:8] if data_hash else None)


def trigger_refresh_async(data_hash: str) -> None:
    """Fire-and-forget spawn of refresh_all_analytics. Mirrors the
    audit_engine / research_engine async-trigger pattern: spawn on
    the running event loop when available, else spawn a daemon
    thread. The strategy_cache write hook uses this so the refresh
    runs in the background and doesn't block the write returning."""
    import asyncio
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            task = loop.create_task(refresh_all_analytics(data_hash))
            _refresh_tasks.add(task)
            task.add_done_callback(_refresh_tasks.discard)
        else:
            import threading
            threading.Thread(
                target=lambda: asyncio.run(refresh_all_analytics(data_hash)),
                daemon=True, name="analytics-refresh",
            ).start()
    except Exception as exc:  # noqa: BLE001
        log.warning("precomputed_analytics_spawn_failed",
                    error=str(exc))


# Module-level strong references so spawned tasks are not GC'd before
# they complete. Mirrors the research_engine pattern.
_refresh_tasks: set = set()


# Silence the structlog import-only false positive
_ = logging
