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

# Carried in every audit run's metadata so the independent auditor — and
# the Analytical Appendix — see the post-Excel data provenance. The Excel
# file seeds the historical series; months after it are auto-extended from
# yfinance, which is a documented source change for the HY series.
DATA_SOURCE_NOTES = (
    "Historical monthly observations come from the provided Excel file "
    "(FNA_670_Project_Sources.xlsx), whose S&P 500 monthly sheet ends "
    "2025-12. Months after that period are auto-extended: SPY (equity), "
    "BND (investment grade) and HYG (high yield) total returns from "
    "yfinance, and DTB3 from FRED for the risk-free rate. SOURCE CHANGE: "
    "the historical HY series is the BAMLHYH0A0HYM2TRIV total-return "
    "index; the HYG ETF used for the extension is a tradeable proxy for "
    "that index (small expense-ratio drag and tracking error). Every "
    "extension row is tagged with its true source in market_data_monthly "
    "and data_series_registry — the extension months carry hy_source "
    "'hy_monthly_hyg_yf', not the Excel 'hy_monthly_baml'."
)

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
        "sum over rebalance dates of |drifted_t - new_target_t| / 2, "
        "then annualised by dividing by the number of years. The "
        "drifted weights compound the previous target through the "
        "inter-rebalance monthly returns, so a fixed-weight strategy "
        "shows non-zero turnover (it trades to correct drift). "
        "true_turnover CAN be independently recomputed from "
        "weight_schedule + returns_df: weight_schedule is persisted on "
        "every strategy result. The audit checks direction only "
        "(dynamic >= static), not the exact value."
    ),
    "rolling_correlation": (
        "12-month rolling Pearson correlation of the monthly return "
        "series. The pre/post-2022 split rule is applied to the ROLLING "
        "VALUE TIMESTAMP, not to each contributing observation in the "
        "12-month lookback window:\n"
        f"  pre_2022_avg = mean(rolling_corr[t]) for every t < {REGIME_BREAK_DATE}.\n"
        f"  post_2022_avg = mean(rolling_corr[t]) for every t >= {REGIME_BREAK_DATE}.\n"
        "The first 11 post-2022 rolling values (timestamps 2022-01-31 "
        "through 2022-11-30) carry pre-2022 history in their lookback "
        "windows by construction — this is the documented platform "
        "convention. Drop any NaN rolling values from both averages "
        "(the first 11 rolling values from the start of the series have "
        "no window). Apply this rule exactly; the platform applies it "
        "the same way."
    ),
    "regime_split": (
        f"Split at {REGIME_BREAK_DATE} applied UNIFORMLY across every "
        "component (regime_conditional_performance, rolling_correlation, "
        "chart markers, audit comparisons). The rule:\n"
        f"  pre_2022 = every observation whose timestamp is strictly "
        f"less than {REGIME_BREAK_DATE} (i.e., 2021-12-31 or earlier).\n"
        f"  post_2022 = every observation whose timestamp is greater "
        f"than or equal to {REGIME_BREAK_DATE} (i.e., 2022-01-01 or "
        f"later — January 2022 month-end 2022-01-31 is in POST).\n"
        "Apply at the observation-timestamp level for point-in-time "
        "metrics (Sharpe, CAGR) and at the rolling-value-timestamp "
        "level for rolling-window metrics."
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
    A lightweight fingerprint of the MARKET DATA the statistical audit
    verifies: row counts, newest dates, and last_updated timestamps of
    market_data_monthly and ff_factors_monthly. It changes only when new
    market data is ingested — not on a restart, and (June 22 2026) not
    when downstream caches are merely refreshed against unchanged data.

    HISTORICAL NOTE: through June 21 2026 this hash also included
    strategy_results_cache table metadata. That was a design flaw --
    running POST /api/v1/admin/refresh-appendix-caches (or any backtester
    run) updates strategy_results_cache.last_updated, which then flipped
    the platform fingerprint even when market data was unchanged. The
    c421fb89 -> 4de6bbbc hash drift that surfaced during executive brief
    + appendix regeneration was caused by exactly this -- not by market
    data drift. The strategy_results_cache table records derived state;
    derived state churn must not invalidate the upstream-data fingerprint.

    Cheap by design (get_data_status issues only COUNT/MAX queries, never
    a full payload assembly). Returns "" on any failure -- an empty hash
    never matches, so the audit is treated as stale rather than wrongly
    served from cache.
    """
    try:
        from tools.cache import get_data_status

        status = await get_data_status()
        if not status.get("available"):
            return ""
        # MARKET DATA TABLES ONLY -- never derived/cache tables.
        relevant = ("market_data_monthly", "ff_factors_monthly")
        parts: list[str] = []
        for t in status.get("tables", []):
            if t.get("name") in relevant:
                parts.append(
                    f"{t.get('name')}:{t.get('row_count')}:"
                    f"{t.get('max_date')}:{t.get('last_updated')}")
        if not parts:
            # No relevant table reported -- treat as "nothing to fingerprint"
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
    Whether BOTH audits — the statistical audit and the QA methodology
    audit — still reflect the current data. Smart audit caching serves a
    cached result only when is_current is True.

    Statistical currency: current_data_hash() matches the data_hash on
    the most recent COMPLETED audit_runs row.
    QA currency: the most recent non-expired qa_results_cache verdict was
    computed for the same strategy data as the latest strategy_results_cache
    row (the two strategy_hash values match).

    Returns a per-layer breakdown so the QA tab can show which audit is
    stale when only one has changed:
      {is_current, statistical_current, qa_current,
       current_data_hash, last_hash, qa_strategy_hash, qa_last_hash}
    Every field is fail-open — an empty/None hash never matches, so an
    audit reads as stale rather than wrongly served from cache.
    """
    from tools.audit_engine import get_last_completed_audit_hash
    from tools.cache import get_latest_qa_hash, get_latest_strategy_hash

    current = await current_data_hash()
    last = await get_last_completed_audit_hash()
    statistical_current = bool(current) and bool(last) and current == last

    strat_hash = await get_latest_strategy_hash()
    qa_hash = await get_latest_qa_hash()
    qa_current = bool(strat_hash) and bool(qa_hash) and strat_hash == qa_hash

    return {
        "is_current": statistical_current and qa_current,
        "statistical_current": statistical_current,
        "qa_current": qa_current,
        "current_data_hash": current,
        "last_hash": last,
        "qa_strategy_hash": strat_hash,
        "qa_last_hash": qa_hash,
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


def _by_strategy_key(
    rows: list[dict], strategies: dict[str, dict],
) -> dict[str, dict]:
    """Re-keys analytics rows by the strategies dict's CANONICAL key
    (e.g. 'BENCHMARK') rather than the row's `strategy` field — which
    may be a display name from the backtester output (BENCHMARK's
    `strategy_name` is "100% Equity (Benchmark)", not "BENCHMARK").

    The bug this guards against: audit_assembler's raw_data.
    strategy_returns is keyed by strategies.items() canonical keys
    ('BENCHMARK', 'CLASSIC_60_40', ...). The deterministic recomputer
    iterates THAT key set and looks each up in platform_loadings /
    platform_regime. If those tables are keyed by display name
    instead, every BENCHMARK metric falls through to None and
    surfaces as a spurious missing_value WARN.

    Build a display-name → row map, then walk strategies in
    canonical-key order; map each row back to its dict key. A row
    whose strategy_name doesn't exist in the strategies dict is
    dropped (analytics emitted a row for a strategy not in the
    cache — unusual but harmless to ignore).
    """
    by_display = {r.get("strategy"): r for r in rows}
    out: dict[str, dict] = {}
    for canonical_key, s in strategies.items():
        display = (s.get("strategy_name") or canonical_key) if isinstance(s, dict) else canonical_key
        row = by_display.get(display) or by_display.get(canonical_key)
        if row is None:
            continue
        out[str(canonical_key)] = {
            k: v for k, v in row.items() if k != "strategy"
        }
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

        # ── Frontier-aligned subset (May 25 2026 fix) ────────────────
        # refresh_efficient_frontier builds its mu vector and cov
        # matrix from a pd.DataFrame({EQUITY, IG, HY}).dropna(), so the
        # cached max-Sharpe point's (sigma, mu) are computed over a
        # subset of months where ALL THREE columns are non-NaN. If
        # any month has a NaN in any column (extension lag, partial
        # backfill), it drops out of the frontier sample.
        #
        # The auditor was previously handed the RAW arrays (with NaN),
        # so its recomputation of mu @ w averaged over a different
        # period and reported the platform's (sigma, mu) as inconsistent
        # with the weights even though the platform's own arithmetic
        # was internally consistent.
        #
        # Build the SAME aligned subset here, surface it under
        # platform_computed.efficient_frontier.aligned_returns, and
        # the frontier prompt sends THOSE arrays — not the raw ones —
        # so both sides agree on the sample.
        ef_frame = pd.DataFrame(
            {"EQUITY": equity, "IG": ig, "HY": hy, "rf": rf},
            index=idx,
        ).dropna()
        rf_annual_aligned = (
            round(float(ef_frame["rf"].mean()) * 12, 6)
            if not ef_frame.empty else 0.0
        )
        aligned_returns = {
            "equity": [round(float(v), 6) for v in ef_frame["EQUITY"]],
            "ig":     [round(float(v), 6) for v in ef_frame["IG"]],
            "hy":     [round(float(v), 6) for v in ef_frame["HY"]],
            "rf":     [round(float(v), 6) for v in ef_frame["rf"]],
            "dates":  [str(d.date()) for d in ef_frame.index],
            "n_obs":  int(len(ef_frame)),
            "rf_annual": rf_annual_aligned,
        }
        dropped_n = n_months - len(ef_frame)
        if dropped_n > 0:
            log.info(
                "audit_frontier_alignment_dropped_rows",
                full_months=n_months,
                aligned_months=len(ef_frame),
                dropped=dropped_n,
            )

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
            # Preserve the full [date, value] pairs per strategy
            # (May 25 2026 fix). The previous shape — flat values
            # only — silently broke alignment for short-history
            # strategies: MOMENTUM_ROTATION starts 2003-07 with 270
            # months while the global asset_returns dates index has
            # 282 months, so a pair-by-index zip misaligned by 12
            # months. The deterministic recomputer would either skip
            # the strategy (length-mismatch guard) or compute over
            # wrong dates. Pairs carry their own dates so analytics'
            # _pairs_to_series handles each strategy on its own
            # timeline.
            "strategy_returns": {
                name: list(s.get("monthly_returns") or [])
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
        # _max_sharpe_point is now async (May 23 2026 hotfix) — it
        # reads from the precomputed analytics_metrics_cache instead
        # of running the 10-30s SLSQP sweep inline. Cache miss
        # returns None and the audit's max_sharpe consistency
        # checks degrade to "not enough data" rather than blocking
        # the entire audit on a slow recompute.
        try:
            ef_max_sharpe = await _max_sharpe_point(
                equity, ig, hy, rf_annual)
        except Exception as exc:  # noqa: BLE001
            log.warning("audit_frontier_failed", error=str(exc))
            ef_max_sharpe = None

        platform_computed: dict[str, Any] = {
            # summary_statistics is keyed by asset name (EQUITY / IG /
            # HY / BENCHMARK); analytics writes those names directly
            # as the "asset" field, so a plain _list_to_dict is fine.
            "summary_statistics": _list_to_dict(
                an.summary_statistics(asset_series, rf), "asset"),
            # regime_conditional and factor_loadings are keyed by the
            # strategy_results dict key (e.g. 'BENCHMARK'), not by the
            # row's strategy field (which is the display name —
            # "100% Equity (Benchmark)" for the benchmark, ≠ 'BENCHMARK').
            # _by_strategy_key re-keys via the strategies dict so the
            # deterministic recomputer's lookups land.
            "regime_conditional": _by_strategy_key(
                an.regime_conditional_performance(strategies, rf), strategies),
            "factor_loadings": _by_strategy_key(
                an.factor_loadings(strategies, ff or []), strategies),
            "efficient_frontier": {
                "max_sharpe_point": ef_max_sharpe,
                # The aligned subset the frontier was actually computed
                # against. The auditor recomputes from THESE arrays so
                # mu @ w lands on the same sample the platform used.
                "aligned_returns": aligned_returns,
            },
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
            "data_source_notes": DATA_SOURCE_NOTES,
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


async def _max_sharpe_point(
    equity, ig, hy, rf_annual: float,
) -> dict[str, Any] | None:
    """The frontier's max-Sharpe (tangency) point — σ, μ and weights.

    Hotfix May 23 2026: this function previously called
    efficient_frontier() inline, running the same 100-point SLSQP
    sweep that caused the /api/optimize/weights endpoint to time
    out at 30s. The audit endpoint runs this function as part of
    assemble_audit_payload — so every audit run inherited the
    same 10-30s cost, which was eating the audit's time budget
    and leaving downstream checks INCOMPLETE.

    Now reads the precomputed frontier from analytics_metrics_cache
    (the same row /api/optimize/weights serves). On a cache hit
    the call drops from ~30s to ~50ms. On a cache miss we return
    None — the audit's max_sharpe consistency checks degrade to
    "not enough data to audit" rather than blocking the rest of
    the audit on a slow inline sweep.
    """
    try:
        from tools.precomputed_analytics import (
            get_latest_metric as get_latest_precomputed,
        )
        cached = await get_latest_precomputed("efficient_frontier")
        if not cached or not cached.get("frontier_points"):
            log.warning("audit_max_sharpe_cache_miss",
                        note="frontier cache cold — audit skips this check")
            return None
        points = cached["frontier_points"]
        best = max(points, key=lambda p: p.get("sharpe", float("-inf")))
        weights = best.get("weights", {}) or {}
        return {
            "sigma": round(float(best.get("volatility", 0.0)), 6),
            "mu": round(float(best.get("return", 0.0)), 6),
            "sharpe": round(float(best.get("sharpe", 0.0)), 6),
            "weights": {k: round(float(v), 6) for k, v in weights.items()},
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_max_sharpe_failed", error=str(exc))
        return None
