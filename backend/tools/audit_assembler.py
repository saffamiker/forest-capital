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

PERSISTED-WEIGHTS LIMITATION: the backtester computes per-rebalance
weight schedules but does not persist them — only monthly returns are
cached. So raw_data.strategy_weights is empty and true_turnover cannot
be independently recomputed. Layer 1's weight-constraint check degrades
to a SKIP finding accordingly.
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


def _list_to_dict(rows: list[dict], key: str) -> dict[str, dict]:
    """Turns a list of analytics rows into a dict keyed by `key` (e.g.
    'asset' or 'strategy') — the per-entity shape the auditor expects."""
    out: dict[str, dict] = {}
    for r in rows:
        name = r.get(key)
        if name is not None:
            out[str(name)] = {k: v for k, v in r.items() if k != key}
    return out


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
            # Per-rebalance weights are not persisted — see the module
            # docstring. Empty by design; Layer 1's weight-constraint
            # check skips accordingly.
            "strategy_weights": {},
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

        metadata = {
            "study_period": {
                "start": str(idx[0].date()) if n_months else None,
                "end": str(idx[-1].date()) if n_months else None,
                "months": n_months,
            },
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
