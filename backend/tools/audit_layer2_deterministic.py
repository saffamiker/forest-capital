"""
tools/audit_layer2_deterministic.py — May 25 2026.

Layer 2 audit recomputation, in deterministic Python instead of
LLM arithmetic.

The previous architecture asked claude-opus-4-7 to recompute σ, μ,
Sharpe, regime splits and factor regressions from raw arrays — and
to emit a JSON comparison verdict. Two failure modes plagued it:

  1. LLM arithmetic hallucination. The auditor occasionally
     returned values that were close-enough-looking to be plausible
     but actually wrong (off by a couple of percent on Sharpe, etc.).
     The user couldn't trust a WARN finding without recomputing the
     "auditor value" themselves by hand.
  2. Response truncation. Verbose step-by-step reasoning routinely
     overshot the 8000-token output cap, the JSON truncated past
     the closing '}', and the whole group degraded to a single
     parse-failure WARN.

Both failure modes vanish when we recompute deterministically in
Python: the numbers are exact (modulo floating-point), the response
is the comparison verdict itself (no LLM call needed for the math),
and the LLM is reserved for QUALITATIVE INTERPRETATION of a real
discrepancy — not for the arithmetic itself.

CONTRACT — each public function returns the same shape the LLM
path used to return, so _checks_to_findings can consume it:

    {"strategy": "<group_label>", "checks": [
        {"metric": "<asset.metric_name>" | "<strategy.metric_name>",
         "platform_value": <num> | None,
         "auditor_value":  <num> | None,
         "discrepancy_pct": <num>,
         "status": "pass" | "warning" | "fail",
         "reasoning": "<factual one-sentence note>",
         "flag": ""}
    ]}

A status of `pass` means the Python recompute landed within
TOLERANCE_PASS of the platform's reported value. `warning` is a
discrepancy in (TOLERANCE_PASS, TOLERANCE_FAIL]; `fail` is beyond
TOLERANCE_FAIL or directionally wrong (sign disagreement).

TOLERANCES — the LLM path used 0.01% (pass), 0.1% (warning).
Python recomputation is exact to floating-point precision, so we
can hold a tighter pass threshold AND treat any non-trivial
discrepancy as a real bug rather than a "rounding difference."
TOLERANCE_PASS = 0.01% remains because the platform's stored
values are themselves rounded (round(_, 4) at the analytics
layer), so two correct implementations can differ in the 5th
decimal place by ±0.5 * 10^-4 — within 0.01% on any value > 0.05.

FAIL-OPEN — a recompute failure (missing field, NaN-only series,
solver convergence on edge cases) returns a "warning" status with
auditor_value=None and a reasoning that names the cause. The audit
overall verdict never blocks on a recompute error.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

import structlog

log = structlog.get_logger(__name__)


# ── Tolerance thresholds ─────────────────────────────────────────────────────

# A discrepancy within TOLERANCE_PASS is treated as zero — both
# implementations agree to floating-point precision. The 4-decimal
# rounding the platform applies to every cached metric is the floor
# on achievable agreement, so 0.01% sits comfortably above the
# rounding noise on any metric > 0.05 in magnitude.
TOLERANCE_PASS: float = 0.01

# A discrepancy in (TOLERANCE_PASS, TOLERANCE_FAIL] is a warning —
# the values agree to 0.1% but not closer. Possible causes: minor
# implementation differences (skipna semantics, ddof choice, etc.).
TOLERANCE_FAIL: float = 0.1


# ── Comparison primitive ─────────────────────────────────────────────────────

def _compare(
    metric: str,
    platform: Any,
    auditor: float | None,
    *,
    note: str | None = None,
) -> dict[str, Any]:
    """Builds one check dict.

    A None on either side falls back to a `warning` with an explicit
    explanation — the audit does not silently pass on missing data.
    A sign disagreement is always a FAIL regardless of magnitude
    (a wrong-direction Sharpe is a real bug, not a rounding gap).
    """
    if platform is None or auditor is None:
        return {
            "metric": metric,
            "platform_value": platform,
            "auditor_value":  auditor,
            "discrepancy_pct": None,
            "status": "warning",
            "reasoning": (note
                          or "One of platform / auditor value was None — "
                             "the recompute could not be verified."),
            "flag": "missing_value",
        }
    try:
        p_full = float(platform)
        a_full = float(auditor)
    except (TypeError, ValueError):
        return {
            "metric": metric,
            "platform_value": platform,
            "auditor_value":  auditor,
            "discrepancy_pct": None,
            "status": "warning",
            "reasoning": "Platform or auditor value was not numeric.",
            "flag": "non_numeric_value",
        }
    if not (math.isfinite(p_full) and math.isfinite(a_full)):
        return {
            "metric": metric,
            "platform_value": p_full, "auditor_value": a_full,
            "discrepancy_pct": None,
            "status": "warning",
            "reasoning": "Platform or auditor value was non-finite "
                         "(NaN / Inf) — recompute could not be compared.",
            "flag": "non_finite_value",
        }

    # Round both sides to the platform's storage precision (4dp) before
    # computing the discrepancy. The analytics layer rounds every stored
    # metric — round(_cagr(...), 4), round(_ann_vol(...), 4) etc. — so
    # the platform value the auditor receives is already truncated. A
    # full-precision auditor value would otherwise show a 0.04% spurious
    # discrepancy on every small-magnitude metric just from the 5th
    # decimal place. Rounding both to 4dp puts them on the same scale.
    p = round(p_full, 4)
    a = round(a_full, 4)

    # Discrepancy as a percentage of |platform|. For platform values
    # close to zero, use the absolute difference instead so a tiny
    # platform value doesn't make every reasonable auditor value look
    # like a huge percent miss.
    if abs(p) < 1e-6:
        # Tiny platform value — use absolute discrepancy in basis points
        # so the threshold semantics still apply.
        discrepancy_pct = abs(a - p) * 100.0
    else:
        discrepancy_pct = abs(a - p) / abs(p) * 100.0

    sign_mismatch = (p > 0 > a) or (p < 0 < a)
    if sign_mismatch and abs(p) > 1e-4 and abs(a) > 1e-4:
        status = "fail"
        reasoning = (
            f"Sign disagreement: platform={p:.4f}, recompute={a:.4f}. "
            f"This is a directional bug, not a rounding gap.")
    elif discrepancy_pct <= TOLERANCE_PASS:
        status = "pass"
        reasoning = (
            f"Platform {p:.4f} and Python recompute {a:.4f} agree to "
            f"{discrepancy_pct:.4f}% (within the {TOLERANCE_PASS}% "
            f"tolerance).")
    elif discrepancy_pct <= TOLERANCE_FAIL:
        status = "warning"
        reasoning = (
            f"Platform {p:.4f} and Python recompute {a:.4f} differ by "
            f"{discrepancy_pct:.4f}% — within {TOLERANCE_FAIL}% but "
            f"beyond the strict {TOLERANCE_PASS}% pass threshold.")
    else:
        status = "fail"
        reasoning = (
            f"Platform {p:.4f} and Python recompute {a:.4f} differ by "
            f"{discrepancy_pct:.4f}% — beyond the {TOLERANCE_FAIL}% "
            f"warning threshold.")
    return {
        "metric": metric,
        "platform_value": round(p, 6),
        "auditor_value":  round(a, 6),
        "discrepancy_pct": round(discrepancy_pct, 4),
        "status": status,
        "reasoning": reasoning,
        "flag": "" if status == "pass" else "discrepancy",
    }


# ── Series builders — shared across recomputes ───────────────────────────────

def _asset_series_from_raw(
    raw: dict[str, list[float]],
) -> dict[str, pd.Series]:
    """Builds {EQUITY, IG, HY, BENCHMARK} indexed by date.

    BENCHMARK falls back to EQUITY (100% equity strategy) when no
    separate benchmark series is present in raw_data.
    """
    dates = raw.get("dates") or []
    if not dates:
        return {}
    idx = pd.to_datetime(dates)
    out: dict[str, pd.Series] = {}
    for key, label in (("equity", "EQUITY"), ("ig", "IG"), ("hy", "HY")):
        values = raw.get(key) or []
        if len(values) == len(idx):
            out[label] = pd.Series(values, index=idx, dtype=float).dropna()
    if "EQUITY" in out:
        out["BENCHMARK"] = out["EQUITY"]
    return out


def _rf_series_from_raw(raw: dict[str, list[float]]) -> pd.Series | None:
    """Monthly risk-free series, aligned to the dates index."""
    dates = raw.get("dates") or []
    rf_values = raw.get("rf") or []
    if not dates or not rf_values or len(dates) != len(rf_values):
        return None
    idx = pd.to_datetime(dates)
    return pd.Series(rf_values, index=idx, dtype=float)


# ── Summary statistics — 7 metrics per asset ─────────────────────────────────

def recompute_summary_statistics(
    asset: str, payload: dict[str, Any], platform: dict[str, Any],
) -> dict[str, Any]:
    """Recomputes the seven summary statistics for one asset and
    compares with the platform's reported values.

    Returns the {strategy, checks: [...]} shape _checks_to_findings
    expects. Uses tools.analytics primitives (the SAME functions the
    platform's analytics layer uses) for the recompute, but applied
    against raw_data.asset_returns instead of strategy_results_cache
    — so the check verifies the cached values match a fresh recompute
    from the underlying market data.
    """
    from tools import analytics as an

    raw = payload.get("raw_data", {}).get("asset_returns", {})
    asset_series = _asset_series_from_raw(raw)
    rf_series = _rf_series_from_raw(raw)

    series = asset_series.get(asset)
    if series is None or series.empty:
        return {
            "strategy": asset,
            "checks": [{
                "metric": f"{asset}.recompute",
                "platform_value": None, "auditor_value": None,
                "discrepancy_pct": None,
                "status": "warning",
                "reasoning": (f"No {asset} series in raw_data.asset_returns; "
                              "Python recompute could not run."),
                "flag": "no_data",
            }],
        }

    bench_series = asset_series.get("BENCHMARK")
    bench_cagr = an._cagr(bench_series) if bench_series is not None else None

    # The seven metrics, each computed via the analytics primitive.
    auditor_cagr     = an._cagr(series)
    auditor_vol      = an._ann_vol(series)
    auditor_sharpe   = an._sharpe(series, rf_series)
    auditor_dd       = an._max_drawdown(series)
    try:
        from scipy.stats import skew as _skew
        auditor_skew = float(_skew(series.to_numpy(), bias=False))
    except Exception:  # noqa: BLE001
        auditor_skew = None
    auditor_excess   = (
        auditor_cagr - bench_cagr
        if bench_cagr is not None and asset != "BENCHMARK" else 0.0
    )
    # Information ratio — None for BENCHMARK and for EQUITY (the
    # benchmark IS equity); 0/0 otherwise.
    auditor_ir: float | None
    if (bench_series is None or asset in ("BENCHMARK", "EQUITY")):
        auditor_ir = None
    else:
        # Aligned monthly excess returns vs benchmark.
        aligned = pd.concat(
            [series.rename("a"), bench_series.rename("b")],
            axis=1, join="inner").dropna()
        if len(aligned) < 2:
            auditor_ir = None
        else:
            diff = aligned["a"] - aligned["b"]
            sd = float(diff.std(ddof=1))
            auditor_ir = (
                None if sd < 1e-12
                else float(diff.mean() / sd * np.sqrt(12)))

    # Platform field-name map (tools.analytics.summary_statistics).
    # Two metrics differ from the recompute's natural name:
    #   volatility  ← stored as  ann_volatility
    #   sharpe      ← stored as  sharpe_ratio
    # All other names match. Looking up the wrong key returns None,
    # which surfaces as a "missing_value" WARN — the May 25 2026 bug.
    checks = [
        _compare(f"{asset}.cagr",
                 platform.get("cagr"), auditor_cagr),
        _compare(f"{asset}.volatility",
                 platform.get("ann_volatility"), auditor_vol),
        _compare(f"{asset}.sharpe",
                 platform.get("sharpe_ratio"), auditor_sharpe),
        _compare(f"{asset}.max_drawdown",
                 platform.get("max_drawdown"), auditor_dd),
        _compare(f"{asset}.skewness",
                 platform.get("skewness"), auditor_skew),
        _compare(f"{asset}.excess_return",
                 platform.get("excess_return"), auditor_excess),
        _compare(f"{asset}.information_ratio",
                 platform.get("information_ratio"), auditor_ir),
    ]
    return {"strategy": asset, "checks": checks}


# ── Efficient frontier — sigma, mu at the max-Sharpe point ───────────────────

def recompute_efficient_frontier(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Verifies the platform's reported max-Sharpe σ and μ by
    recomputing mu @ w and sqrt(w · cov · w) from the aligned
    returns the platform's frontier was built against.

    Uses the platform-cached weights — the test is "did the platform
    correctly evaluate σ and μ at THESE weights?" If the weights are
    optimal is a separate concern (Layer 3 checks the consistency of
    the max-Sharpe point against the rest of the frontier).
    """
    ef = (payload.get("platform_computed", {})
          .get("efficient_frontier") or {})
    max_sharpe = ef.get("max_sharpe_point") or {}
    aligned = ef.get("aligned_returns") or {}
    if not max_sharpe or not aligned:
        return {
            "strategy": "efficient_frontier",
            "checks": [{
                "metric": "max_sharpe.recompute",
                "platform_value": None, "auditor_value": None,
                "discrepancy_pct": None,
                "status": "warning",
                "reasoning": ("No max_sharpe_point or aligned_returns "
                              "block in platform_computed.efficient_frontier "
                              "— recompute skipped."),
                "flag": "no_data",
            }],
        }

    # Recover the aligned returns and the platform's weights.
    weights = max_sharpe.get("weights") or {}
    equity = np.array(aligned.get("equity") or [], dtype=float)
    ig     = np.array(aligned.get("ig") or [], dtype=float)
    hy     = np.array(aligned.get("hy") or [], dtype=float)
    rf_annual = float(aligned.get("rf_annual", 0.0))
    if equity.size < 2 or equity.size != ig.size or equity.size != hy.size:
        return {
            "strategy": "efficient_frontier",
            "checks": [{
                "metric": "max_sharpe.recompute",
                "platform_value": None, "auditor_value": None,
                "discrepancy_pct": None,
                "status": "warning",
                "reasoning": (f"Aligned series mismatch — equity={equity.size}, "
                              f"ig={ig.size}, hy={hy.size}. Recompute skipped."),
                "flag": "no_data",
            }],
        }

    w = np.array([weights.get("EQUITY", 0.0),
                  weights.get("IG", 0.0),
                  weights.get("HY", 0.0)], dtype=float)
    # mu and cov of the monthly series, annualised by ×12.
    monthly = np.stack([equity, ig, hy], axis=1)
    mu_monthly = monthly.mean(axis=0)
    cov_monthly = np.cov(monthly, rowvar=False, ddof=1)
    mu = mu_monthly * 12.0
    cov = cov_monthly * 12.0

    auditor_mu = float(mu @ w)
    auditor_sigma = float(np.sqrt(max(w @ cov @ w, 0.0)))
    auditor_sharpe = (
        (auditor_mu - rf_annual) / auditor_sigma
        if auditor_sigma > 1e-12 else 0.0)

    checks = [
        _compare("max_sharpe.mu",
                 max_sharpe.get("mu"), auditor_mu),
        _compare("max_sharpe.sigma",
                 max_sharpe.get("sigma"), auditor_sigma),
        _compare("max_sharpe.sharpe",
                 max_sharpe.get("sharpe"), auditor_sharpe),
    ]
    return {"strategy": "efficient_frontier", "checks": checks}


# ── Regime split — pre/post-2022 Sharpe + CAGR per strategy ──────────────────

def recompute_regime_split(
    subset_names: list[str], payload: dict[str, Any],
) -> dict[str, Any]:
    """Recomputes pre/post-2022 Sharpe and CAGR for each strategy in
    subset_names, using the analytics layer's _safe_sharpe / _safe_cagr
    (the SAME helpers regime_conditional_performance uses). Compares
    against the platform's regime_conditional payload."""
    from tools.analytics import (
        REGIME_BREAK, _pairs_to_series, _safe_cagr, _safe_sharpe,
    )

    raw_strategies = payload.get("raw_data", {}).get("strategy_returns", {})
    raw_asset = payload.get("raw_data", {}).get("asset_returns", {})
    platform_regime = (payload.get("platform_computed", {})
                       .get("regime_conditional") or {})
    rf_series = _rf_series_from_raw(raw_asset)
    dates = raw_asset.get("dates") or []
    idx = pd.to_datetime(dates) if dates else None

    checks: list[dict[str, Any]] = []
    for name in subset_names:
        platform = platform_regime.get(name) or {}
        raw_vals = raw_strategies.get(name)
        if raw_vals is None or idx is None or len(raw_vals) != len(idx):
            # Reconstitute from the strategy_results_cache structure
            # (the platform stores monthly_returns as [[date, val], ...]).
            # The audit_assembler currently strips dates from
            # strategy_returns, so we use the aligned monthly idx here.
            # Fall through to a warning if neither shape lines up.
            checks.append({
                "metric": f"{name}.post_2022_sharpe",
                "platform_value": platform.get("post_2022_sharpe"),
                "auditor_value": None,
                "discrepancy_pct": None, "status": "warning",
                "reasoning": (f"Could not align {name} monthly returns to "
                              "the dates index — recompute skipped."),
                "flag": "alignment_error",
            })
            continue

        series = pd.Series(raw_vals, index=idx, dtype=float).dropna()
        pre = series[series.index < REGIME_BREAK]
        post = series[series.index >= REGIME_BREAK]
        auditor_pre_sharpe  = _safe_sharpe(pre,  rf_series)
        auditor_post_sharpe = _safe_sharpe(post, rf_series)
        auditor_pre_cagr    = _safe_cagr(pre)
        auditor_post_cagr   = _safe_cagr(post)

        checks.append(_compare(
            f"{name}.pre_2022_sharpe",
            platform.get("pre_2022_sharpe"), auditor_pre_sharpe))
        checks.append(_compare(
            f"{name}.post_2022_sharpe",
            platform.get("post_2022_sharpe"), auditor_post_sharpe))
        checks.append(_compare(
            f"{name}.pre_2022_cagr",
            platform.get("pre_2022_cagr"), auditor_pre_cagr))
        checks.append(_compare(
            f"{name}.post_2022_cagr",
            platform.get("post_2022_cagr"), auditor_post_cagr))

    return {"strategy": "regime_split", "checks": checks}


# ── Rolling correlation — pre/post-2022 averages ─────────────────────────────

def recompute_rolling_correlation(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """12-month rolling correlation of equity vs IG, equity vs HY,
    averaged pre/post-2022. Compares with platform_computed.
    rolling_correlation."""
    from tools.analytics import REGIME_BREAK

    raw = payload.get("raw_data", {}).get("asset_returns", {})
    platform = payload.get("platform_computed", {}).get(
        "rolling_correlation") or {}
    asset_series = _asset_series_from_raw(raw)
    equity = asset_series.get("EQUITY")
    ig     = asset_series.get("IG")
    hy     = asset_series.get("HY")
    if equity is None or ig is None or hy is None:
        return {
            "strategy": "rolling_correlation",
            "checks": [{
                "metric": "rolling.recompute",
                "platform_value": None, "auditor_value": None,
                "discrepancy_pct": None, "status": "warning",
                "reasoning": ("Missing one of equity / IG / HY series in "
                              "raw_data.asset_returns; recompute skipped."),
                "flag": "no_data",
            }],
        }

    # Rolling 12-month correlations.
    eq_ig_roll = equity.rolling(window=12).corr(ig).dropna()
    eq_hy_roll = equity.rolling(window=12).corr(hy).dropna()

    def _split_average(roll: pd.Series) -> tuple[float | None, float | None]:
        pre = roll[roll.index < REGIME_BREAK]
        post = roll[roll.index >= REGIME_BREAK]
        return (
            float(pre.mean()) if not pre.empty else None,
            float(post.mean()) if not post.empty else None,
        )

    pre_eq_ig, post_eq_ig = _split_average(eq_ig_roll)
    pre_eq_hy, post_eq_hy = _split_average(eq_hy_roll)

    # The platform's payload shape: {pre_2022: {equity_ig, equity_hy},
    # post_2022: {equity_ig, equity_hy}}. Tolerant to either flat
    # key naming or the structured shape.
    def _get(period: str, pair: str) -> Any:
        period_block = platform.get(period) or {}
        if isinstance(period_block, dict):
            return period_block.get(pair)
        return None

    checks = [
        _compare("equity_ig.pre_2022",
                 _get("pre_2022", "equity_ig"), pre_eq_ig),
        _compare("equity_ig.post_2022",
                 _get("post_2022", "equity_ig"), post_eq_ig),
        _compare("equity_hy.pre_2022",
                 _get("pre_2022", "equity_hy"), pre_eq_hy),
        _compare("equity_hy.post_2022",
                 _get("post_2022", "equity_hy"), post_eq_hy),
    ]
    return {"strategy": "rolling_correlation", "checks": checks}


# ── Factor loadings — Carhart 4-factor (or 3-factor fallback) ────────────────

def recompute_factor_loadings(
    subset_names: list[str], payload: dict[str, Any],
) -> dict[str, Any]:
    """Recomputes the Carhart factor regression for each strategy in
    subset_names via tools.analytics.factor_loadings (the SAME OLS
    routine the platform's analytics layer uses), then compares the
    betas / alpha / R-squared against the platform's stored values.

    The recompute requires the strategy_results dict shape
    (monthly_returns as [[date, val], ...]); we reconstruct it from
    the audit payload's flat strategy_returns + dates."""
    from tools import analytics as an

    raw_strategies = payload.get("raw_data", {}).get("strategy_returns", {})
    raw_asset = payload.get("raw_data", {}).get("asset_returns", {})
    ff_raw = payload.get("raw_data", {}).get("ff_factors", {})
    platform_loadings = (payload.get("platform_computed", {})
                          .get("factor_loadings") or {})
    dates = raw_asset.get("dates") or []

    checks: list[dict[str, Any]] = []

    # Reconstitute the strategy_results shape factor_loadings() expects.
    strategy_results: dict[str, dict] = {}
    for name in subset_names:
        values = raw_strategies.get(name)
        if values is None or len(values) != len(dates):
            continue
        strategy_results[name] = {
            "strategy_name": name,
            "monthly_returns": [[d, v] for d, v in zip(dates, values)],
        }

    if not strategy_results:
        return {
            "strategy": "factor_loadings",
            "checks": [{
                "metric": "factor.recompute",
                "platform_value": None, "auditor_value": None,
                "discrepancy_pct": None, "status": "warning",
                "reasoning": ("No usable strategy_returns in raw_data for "
                              f"{subset_names}; factor recompute skipped."),
                "flag": "no_data",
            }],
        }

    # Reconstitute ff_factors as a list[dict] — factor_loadings()
    # consumes that shape via DataFrame coercion.
    ff_dates = ff_raw.get("dates") or []
    ff_rows: list[dict] = []
    for i, dt in enumerate(ff_dates):
        try:
            ff_rows.append({
                "yyyymm": int(str(dt).replace("-", "")[:6]),
                "mkt_rf": ff_raw["mkt_rf"][i],
                "smb":    ff_raw["smb"][i],
                "hml":    ff_raw["hml"][i],
                "mom":    ff_raw["mom"][i] if i < len(ff_raw.get("mom") or []) else None,
                "rf":     ff_raw["rf"][i] if i < len(ff_raw.get("rf") or []) else 0.0,
            })
        except (IndexError, KeyError, TypeError, ValueError):
            continue

    if not ff_rows:
        return {
            "strategy": "factor_loadings",
            "checks": [{
                "metric": "factor.recompute",
                "platform_value": None, "auditor_value": None,
                "discrepancy_pct": None, "status": "warning",
                "reasoning": ("Could not reconstitute ff_factors from raw "
                              "payload; recompute skipped."),
                "flag": "no_data",
            }],
        }

    try:
        auditor_rows = an.factor_loadings(strategy_results, ff_rows)
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_recompute_factor_loadings_failed", error=str(exc))
        return {
            "strategy": "factor_loadings",
            "checks": [{
                "metric": "factor.recompute",
                "platform_value": None, "auditor_value": None,
                "discrepancy_pct": None, "status": "warning",
                "reasoning": (f"Factor regression raised: {exc}"),
                "flag": "recompute_error",
            }],
        }

    auditor_by_strategy = {row["strategy"]: row for row in auditor_rows}
    for name in subset_names:
        platform = platform_loadings.get(name) or {}
        auditor = auditor_by_strategy.get(name) or {}
        for coef in ("mkt_rf", "smb", "hml", "mom",
                     "alpha_annualized", "r_squared"):
            checks.append(_compare(
                f"{name}.{coef}",
                platform.get(coef), auditor.get(coef)))

    return {"strategy": "factor_loadings", "checks": checks}
