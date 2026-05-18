"""
tools/audit_assembler.py — the statistical-audit payload.

assemble_audit_payload() gathers everything the three audit layers
need: the raw monthly data, the platform's computed analytics outputs,
and the formula specifications. The independent auditor (claude-opus-4-7)
receives the raw data and the formula specs only — never the platform's
intermediate calculations — and recomputes every metric from scratch.

All figures come from data already in PostgreSQL — market_data_monthly,
strategy_results_cache, ff_factors_monthly — via the cache read layer.
A SHA256 of the raw data is returned so an audit run is reproducible.

TWO COMPUTATION REGIMES — important. The Analytics layer (tools/analytics)
annualises MONTHLY series with 12 / sqrt(12). The Dashboard strategy
table is a SEPARATE layer that annualises DAILY series with 252 /
sqrt(252). The two report different volatility and Sharpe for the same
entity by construction. This audit targets the Analytics layer; the
formula specs below describe that layer, and the regime difference is
documented in formula_specifications so a cross-layer value gap is
explained rather than flagged.

WEIGHT SCHEDULES: the backtester persists each strategy's per-rebalance
target weights on the result (weight_schedule), so raw_data.
strategy_weights carries the full {dates, equity, ig, hy} columns and
Layer 1's long-only / sum-to-1 weight-constraint check runs in full. A
strategy cached before weight persistence shipped has no weight_schedule
and contributes empty columns — Layer 1 then warns rather than failing
until the cache is refreshed (POST /api/v1/cache/invalidate).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import structlog

log = structlog.get_logger(__name__)

REGIME_BREAK_DATE = "2022-01-01"
BENCHMARK_LABEL = "100% equity (S&P 500)"
FACTOR_MODEL = "Carhart four-factor (MKT-RF, SMB, HML, MOM)"

# Formula specifications — written to match what the Analytics layer
# (tools/analytics.py) ACTUALLY computes, so the auditor's independent
# recomputation does not raise false discrepancies.
FORMULA_SPECIFICATIONS: dict[str, str] = {
    "cagr": (
        "(1 + monthly_returns).prod() ** (12 / n_months) - 1 — geometric "
        "compound annual growth; the monthly series is annualised with 12. "
        "If the cumulative growth factor is <= 0, CAGR is -1.0."
    ),
    "volatility": (
        "monthly_returns.std(ddof=1) * sqrt(12) — sample standard "
        "deviation of the monthly series, annualised with sqrt(12)."
    ),
    "sharpe": (
        "excess = monthly_returns - rf_monthly (rf reindexed to the "
        "return index, missing values filled with 0); "
        "mean(excess) / excess.std(ddof=1) * sqrt(12). Note this is the "
        "ratio of the arithmetic mean monthly excess return to its sample "
        "std, annualised — NOT geometric-CAGR-over-annualised-vol."
    ),
    "sharpe_ci_95": (
        "Bailey & Lopez de Prado (2012): "
        "var_sr = (1 + 0.5*sr^2*(excess_kurtosis + 1) - sr*skew "
        "+ 0.25*sr^2*skew^2) / (n_obs - 1); std_sr = sqrt(var_sr); "
        "the 95% CI is sharpe +/- 1.96 * std_sr."
    ),
    "max_drawdown": (
        "curve = (1 + monthly_returns).cumprod(); "
        "min(curve / curve.cummax() - 1) — the largest peak-to-trough "
        "fractional loss of the cumulative curve (a negative number)."
    ),
    "skewness": (
        "pandas Series.skew() — the bias-corrected sample skewness (G1)."
    ),
    "excess_return": (
        "strategy_cagr - benchmark_cagr — the difference of the two "
        "geometric CAGRs (NOT a mean of monthly differences)."
    ),
    "information_ratio": (
        "monthly_excess = strategy_monthly - benchmark_monthly (inner "
        "join on date); mean(monthly_excess) / monthly_excess.std(ddof=1) "
        "* sqrt(12). Undefined (null) for the benchmark itself."
    ),
    "factor_regression": (
        "statsmodels OLS: (strategy_monthly_return - rf) ~ const "
        "+ MKT_RF + SMB + HML + MOM, with the Fama-French factors in "
        "decimal form (the published percent values divided by 100). "
        "alpha_annualised = const * 12; a coefficient is flagged "
        "significant when its p-value < 0.05. Carhart four-factor; a "
        "strategy whose history predates the MOM backfill (MOM null for "
        ">= 12 months) falls back to a three-factor fit."
    ),
    "true_turnover": (
        "mean over rebalance dates of sum(|w_t - w_{t-1}|) / 2, then "
        "annualised by dividing by the number of years. NOTE: the "
        "per-rebalance weight series is not persisted, so true_turnover "
        "cannot be independently recomputed from stored data — it is "
        "audited only for direction/consistency, not by recomputation."
    ),
    "rolling_correlation": (
        "12-month rolling Pearson correlation of the monthly return "
        "series; the pre/post-2022 averages split the rolling series at "
        f"{REGIME_BREAK_DATE}."
    ),
    "regime_split": (
        f"Split at {REGIME_BREAK_DATE}: the pre-2022 sub-period is every "
        "month strictly before that date; the post-2022 sub-period is "
        "every month on or after it."
    ),
    "efficient_frontier": (
        "scipy SLSQP target-return sweep on the three-asset monthly "
        "returns: minimise variance subject to fully-invested (sum w = 1) "
        "and long-only (0 <= w <= 1); monthly moments annualised as "
        "mu*12 and cov*12. The max-Sharpe point is the frontier point "
        "with the highest (mu - risk_free) / sigma."
    ),
    "annualisation_regimes": (
        "The Analytics layer annualises MONTHLY series with 12 and "
        "sqrt(12) — these specifications describe that layer. The "
        "Dashboard strategy table is a separate layer that annualises "
        "DAILY series with 252 and sqrt(252). The two layers therefore "
        "report different volatility and Sharpe for the same entity by "
        "construction; a value gap between the two is EXPECTED and must "
        "not be flagged as a discrepancy."
    ),
}


def _payload_hash(raw_data: dict[str, Any]) -> str:
    """SHA256 of the raw-data block — the reproducibility key for a run."""
    canonical = json.dumps(raw_data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Smart audit caching — the data fingerprint ────────────────────────────────
#
# An audit run independently recomputes every metric with claude-opus-4-7 —
# it is the most expensive operation on the platform. Re-running it on
# unchanged data spends the Opus budget and tells the team nothing new.
# current_data_hash() is a CHEAP fingerprint of the data the audit verifies
# (three COUNT/MAX queries via get_data_status — never the full payload),
# so the QA tab can call it on every mount and is_audit_current() can decide
# whether the last completed audit still holds.


async def current_data_hash() -> str:
    """
    A lightweight fingerprint of the data the statistical audit verifies:
    the row counts and newest dates of market_data_monthly,
    ff_factors_monthly and strategy_results_cache. It changes only when
    new rows are appended or the strategy cache is recomputed — not on a
    restart — so a matching hash means the audit is genuinely still current.

    Cheap by design (get_data_status issues only COUNT/MAX queries, never
    a full payload assembly). Returns "" on any failure — an empty hash
    never matches, so the audit is treated as stale rather than wrongly
    served from cache.
    """
    try:
        from tools.cache import get_data_status

        status = await get_data_status()
        if not status.get("available"):
            return ""
        relevant = ("market_data_monthly", "ff_factors_monthly",
                    "strategy_results_cache")
        parts: list[str] = []
        for t in status.get("tables", []):
            if t.get("name") in relevant:
                parts.append(
                    f"{t.get('name')}:{t.get('row_count')}:"
                    f"{t.get('max_date')}:{t.get('last_updated')}")
        if not parts:
            # No relevant table reported — treat as "nothing to fingerprint"
            # rather than hashing an empty string into a matchable value.
            return ""
        parts.sort()
        canonical = "|".join(parts)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    except Exception as exc:  # noqa: BLE001
        log.warning("current_data_hash_failed", error=str(exc))
        return ""


async def is_audit_current() -> dict[str, Any]:
    """
    Whether the last completed statistical audit still reflects the
    current data. Compares current_data_hash() to the data_hash stored on
    the most recent COMPLETED audit_runs row.

    Returns {is_current, current_data_hash, last_hash}. is_current is
    False when there is no prior completed run, when either hash is empty
    (fail-open), or when the two hashes differ.
    """
    from tools.audit_engine import get_last_completed_audit_hash

    current = await current_data_hash()
    last = await get_last_completed_audit_hash()
    is_current = bool(current) and bool(last) and current == last
    return {
        "is_current": is_current,
        "current_data_hash": current,
        "last_hash": last,
    }


def _list_to_dict(rows: list[dict], key: str) -> dict[str, dict]:
    """Turns a list of analytics rows into a dict keyed by `key` (e.g.
    'asset' or 'strategy') — the per-entity shape the auditor expects."""
    out: dict[str, dict] = {}
    for r in rows:
        name = r.get(key)
        if name is not None:
            out[str(name)] = {k: v for k, v in r.items() if k != key}
    return out


def _weight_columns(weight_schedule: list[dict]) -> dict[str, list]:
    """
    Converts a strategy's persisted weight_schedule — a list of
    {date, weights:{equity,ig,hy}} — into the columnar shape the Layer 1
    weight-constraint check consumes: {dates, equity, ig, hy}.
    """
    return {
        "dates": [e.get("date") for e in weight_schedule],
        "equity": [float((e.get("weights") or {}).get("equity", 0.0))
                   for e in weight_schedule],
        "ig": [float((e.get("weights") or {}).get("ig", 0.0))
               for e in weight_schedule],
        "hy": [float((e.get("weights") or {}).get("hy", 0.0))
               for e in weight_schedule],
    }


async def assemble_audit_payload() -> dict[str, Any]:
    """
    Builds the full audit payload. Returns a dict with an `available`
    flag; when False (test environment, or caches not yet warm) the
    layers skip cleanly. Never raises — any failure returns
    available=False with a note.
    """
    import os
    if os.getenv("ENVIRONMENT", "").lower() == "test":
        return {"available": False,
                "note": "audit payload unavailable in the test environment"}
    try:
        import pandas as pd

        from tools import analytics as an
        from tools.cache import (
            get_ff_factors, get_latest_strategy_cache, get_monthly_returns,
        )

        monthly = await get_monthly_returns()
        strategies = await get_latest_strategy_cache()
        ff = await get_ff_factors()
        if not monthly or not strategies:
            return {"available": False,
                    "note": "market data or strategy cache not yet warm"}

        idx = pd.to_datetime(monthly["dates"])
        equity = pd.Series(monthly["equity"], index=idx)
        ig = pd.Series(monthly["ig"], index=idx)
        hy = pd.Series(monthly["hy"], index=idx)
        rf = pd.Series(monthly["rf"], index=idx)
        n_months = len(idx)
        rf_annual = round(float(rf.mean()) * 12, 6) if n_months else 0.0

        benchmark = strategies.get("BENCHMARK", {})
        bench_series = an._pairs_to_series(benchmark.get("monthly_returns") or [])
        asset_series = {"EQUITY": equity, "IG": ig, "HY": hy}
        if not bench_series.empty:
            asset_series["BENCHMARK"] = bench_series

        # ── raw_data — exactly what the auditor recomputes from ───────────
        raw_data: dict[str, Any] = {
            "asset_returns": {
                "equity": monthly["equity"], "ig": monthly["ig"],
                "hy": monthly["hy"], "rf": monthly["rf"],
                "dates": monthly["dates"],
            },
            "ff_factors": {
                "dates": [str(f.get("yyyymm")) for f in (ff or [])],
                "mkt_rf": [f.get("mkt_rf") for f in (ff or [])],
                "smb": [f.get("smb") for f in (ff or [])],
                "hml": [f.get("hml") for f in (ff or [])],
                "mom": [f.get("mom") for f in (ff or [])],
                "rf": [f.get("rf") for f in (ff or [])],
            },
            "strategy_returns": {
                name: [p[1] for p in (s.get("monthly_returns") or [])]
                for name, s in strategies.items()
            },
            # Per-rebalance target weights, from each strategy's persisted
            # weight_schedule — the columnar shape Layer 1's weight check
            # consumes. A strategy cached before weight persistence shipped
            # has no weight_schedule and contributes an empty column set;
            # Layer 1 then warns rather than failing.
            "strategy_weights": {
                name: _weight_columns(s.get("weight_schedule") or [])
                for name, s in strategies.items()
            },
        }

        # ── platform_computed — the platform's current analytics output ──
        try:
            ef_max_sharpe = _max_sharpe_point(equity, ig, hy, rf_annual)
        except Exception as exc:  # noqa: BLE001
            log.warning("audit_frontier_failed", error=str(exc))
            ef_max_sharpe = None

        platform_computed: dict[str, Any] = {
            "summary_statistics": _list_to_dict(
                an.summary_statistics(asset_series, rf), "asset"),
            "regime_conditional": _list_to_dict(
                an.regime_conditional_performance(strategies, rf), "strategy"),
            "factor_loadings": _list_to_dict(
                an.factor_loadings(strategies, ff or []), "strategy"),
            "efficient_frontier": {"max_sharpe_point": ef_max_sharpe},
            "turnover": {
                name: s.get("true_turnover")
                for name, s in strategies.items()
                if s.get("true_turnover") is not None
            },
            "rolling_correlation": an.rolling_correlation(
                equity, ig, hy, window=12),
        }

        # Per-strategy actual data periods. The dynamic strategies consume
        # an initialisation lookback window, so they start later than the
        # 2002-07 study period — carrying the actual start dates here means
        # the Layer 1 return-series-length note can cite them rather than
        # rely on hardcoded approximations.
        strategy_periods = {}
        for name, s in strategies.items():
            pairs = s.get("monthly_returns") or []
            strategy_periods[name] = {
                "start": pairs[0][0] if pairs else None,
                "end": pairs[-1][0] if pairs else None,
                "months": len(pairs),
            }

        metadata = {
            "study_period": {
                "start": str(idx[0].date()) if n_months else None,
                "end": str(idx[-1].date()) if n_months else None,
                "months": n_months,
            },
            "strategy_periods": strategy_periods,
            "risk_free_rate": {
                "source": "FRED DTB3",
                "value": rf_annual,
                "calculation": "mean monthly rate * 12",
            },
            "regime_break_date": REGIME_BREAK_DATE,
            "benchmark": BENCHMARK_LABEL,
            "factor_model": FACTOR_MODEL,
        }

        return {
            "available": True,
            "metadata": metadata,
            "raw_data": raw_data,
            "platform_computed": platform_computed,
            "formula_specifications": FORMULA_SPECIFICATIONS,
            "raw_inputs_hash": _payload_hash(raw_data),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_payload_failed", error=str(exc))
        return {"available": False, "note": "audit payload assembly failed"}


def _max_sharpe_point(
    equity, ig, hy, rf_annual: float,
) -> dict[str, Any] | None:
    """The frontier's max-Sharpe (tangency) point — σ, μ and weights —
    computed the same way /api/optimize/weights does."""
    import pandas as pd

    from tools.optimizer import efficient_frontier

    df = pd.DataFrame({"equity": equity, "ig": ig, "hy": hy}).dropna()
    if len(df) < 12:
        return None
    points = efficient_frontier(
        df, periods_per_year=12, risk_free=rf_annual)
    if not points:
        return None
    best = max(points, key=lambda p: p.get("sharpe", float("-inf")))
    weights = best.get("weights", {}) or {}
    return {
        "sigma": round(float(best.get("volatility", 0.0)), 6),
        "mu": round(float(best.get("return", 0.0)), 6),
        "sharpe": round(float(best.get("sharpe", 0.0)), 6),
        "weights": {k: round(float(v), 6) for k, v in weights.items()},
    }
