"""
tools/audit_layer1.py — Layer 1 of the statistical audit: raw data
verification.

Six deterministic Python checks on the raw data — no auditor model, no
network. Fast (well under five seconds). They catch the gross data
errors (an implausible benchmark return, an out-of-bounds monthly
return, a broken weight constraint) before the expensive Layer 2
recomputation runs.

layer_1_raw_data_audit(payload) -> {"status": pass|fail|skip,
                                     "findings": [...]}
"""
from __future__ import annotations

from typing import Any

import structlog

from tools.audit_common import layer_status, make_finding

log = structlog.get_logger(__name__)

_RETURN_BOUND = 0.50          # |monthly return| above this is a data error
_WEIGHT_TOLERANCE = 0.001     # |sum(weights) - 1| allowed

# The reasoning attached to a "Return series length" warning — a shorter
# strategy series is expected by construction, not a data gap. Carried on
# the finding so it surfaces identically in the audit export report and
# the Settings → Statistical Audit findings detail.
_RETURN_SERIES_NOTE = (
    "EXPECTED BEHAVIOUR: Dynamic strategies have shorter return series "
    "than the full asset history because they consume an initialisation "
    "lookback window. MIN_VARIANCE, BLACK_LITTERMAN, and "
    "MAX_SHARPE_ROLLING require 36 months (start ~2005-07), "
    "MOMENTUM_ROTATION 12 months (start ~2003-07), and REGIME_SWITCHING "
    "3 months (start ~2002-10). This is correct by construction. "
    "Comparative metrics for these strategies cover their actual start "
    "date to 2025-12-31, not the full 2002-07-31 study period."
)


def _total_return(series: list[float]) -> float:
    """Cumulative total return of a monthly series — product of (1+r) - 1."""
    growth = 1.0
    for r in series:
        growth *= (1.0 + float(r))
    return growth - 1.0


def _cagr(series: list[float]) -> float:
    """Geometric CAGR — the Analytics-layer formula, 12-month annualised."""
    n = len(series)
    if n == 0:
        return 0.0
    growth = _total_return(series) + 1.0
    if growth <= 0.0:
        return -1.0
    return growth ** (12.0 / n) - 1.0


def layer_1_raw_data_audit(payload: dict[str, Any]) -> dict[str, Any]:
    """Runs the six raw-data checks. Returns the layer status and its
    findings. A payload that is not available skips the whole layer."""
    if not payload.get("available"):
        return {"status": "skip", "findings": []}

    raw = payload.get("raw_data", {})
    h = payload.get("raw_inputs_hash")
    assets = raw.get("asset_returns", {})
    equity = [float(x) for x in assets.get("equity", [])]
    ig = [float(x) for x in assets.get("ig", [])]
    hy = [float(x) for x in assets.get("hy", [])]
    strategy_returns: dict[str, list] = raw.get("strategy_returns", {})
    ff = raw.get("ff_factors", {})

    findings: list[dict[str, Any]] = []

    def f(check, metric, status, severity, **kw):
        findings.append(make_finding(
            1, check, metric, status, severity, raw_inputs_hash=h, **kw))

    # ── Check 1 — benchmark (S&P 500) CAGR sanity ─────────────────────────
    # The benchmark is 100% equity; its CAGR over 2002-2025 should sit
    # around 8-11%. 5-15% is the outer band — beyond it is a data error.
    bench_cagr = _cagr(equity)
    if 0.08 <= bench_cagr <= 0.11:
        f("Benchmark CAGR sanity", "cagr", "pass", "info",
          platform_value=f"{bench_cagr:.4f}",
          auditor_reasoning=f"S&P 500 CAGR {bench_cagr:.2%} is within the "
                            "expected 8-11% band.")
    elif 0.05 <= bench_cagr <= 0.15:
        f("Benchmark CAGR sanity", "cagr", "warning", "warning",
          platform_value=f"{bench_cagr:.4f}",
          discrepancy=f"CAGR {bench_cagr:.2%} outside 8-11%",
          auditor_reasoning=f"S&P 500 CAGR {bench_cagr:.2%} is outside the "
                            "8-11% band but within the 5-15% outer band.")
    else:
        f("Benchmark CAGR sanity", "cagr", "fail", "critical",
          platform_value=f"{bench_cagr:.4f}",
          discrepancy=f"CAGR {bench_cagr:.2%} outside 5-15%",
          auditor_reasoning=f"S&P 500 CAGR {bench_cagr:.2%} is implausible — "
                            "outside the 5-15% band; suspect a data error.")

    # ── Check 2 — asset-class return ordering ─────────────────────────────
    eq_tr, ig_tr, hy_tr = (_total_return(equity), _total_return(ig),
                           _total_return(hy))
    if eq_tr > ig_tr:
        f("Asset return ordering", "total_return", "pass", "info",
          platform_value=f"equity {eq_tr:.2%}, ig {ig_tr:.2%}, hy {hy_tr:.2%}",
          auditor_reasoning="Equity total return exceeds investment-grade "
                            "bonds over the full period, as expected.")
    else:
        f("Asset return ordering", "total_return", "warning", "warning",
          platform_value=f"equity {eq_tr:.2%}, ig {ig_tr:.2%}",
          discrepancy="equity total return <= IG total return",
          auditor_reasoning="Equity did not out-return IG bonds over the "
                            "full period — unusual; verify the return series.")

    # ── Check 3 — factor data alignment ───────────────────────────────────
    mkt = [float(x) for x in ff.get("mkt_rf", []) if x is not None]
    if not mkt:
        f("Factor data alignment", "mkt_rf", "warning", "warning",
          auditor_reasoning="No Fama-French factor data is present — the "
                            "factor-loadings audit cannot run.")
    else:
        # FF factors are published as percent; annualise the mean.
        mkt_annual_pct = (sum(mkt) / len(mkt)) * 12.0
        if 2.0 <= mkt_annual_pct <= 15.0:
            f("Factor data alignment", "mkt_rf", "pass", "info",
              platform_value=f"{mkt_annual_pct:.2f}% annual",
              auditor_reasoning=f"MKT-RF averages {mkt_annual_pct:.1f}% "
                                "annualised — within the plausible 2-15% band.")
        else:
            f("Factor data alignment", "mkt_rf", "warning", "warning",
              platform_value=f"{mkt_annual_pct:.2f}% annual",
              discrepancy=f"MKT-RF annual {mkt_annual_pct:.1f}% outside 2-15%",
              auditor_reasoning="The market factor's long-run average is "
                                "outside the plausible range; verify the "
                                "Fama-French series.")

    # ── Check 4 — monthly return bounds ───────────────────────────────────
    breaches: list[str] = []
    for name, series in [("equity", equity), ("ig", ig), ("hy", hy)]:
        for i, r in enumerate(series):
            if abs(float(r)) > _RETURN_BOUND:
                breaches.append(f"{name}[{i}]={float(r):.2%}")
    for name, series in strategy_returns.items():
        for i, r in enumerate(series):
            if r is not None and abs(float(r)) > _RETURN_BOUND:
                breaches.append(f"{name}[{i}]={float(r):.2%}")
    if breaches:
        f("Monthly return bounds", "monthly_return", "fail", "critical",
          platform_value="; ".join(breaches[:10]),
          discrepancy=f"{len(breaches)} monthly return(s) exceed +/-50%",
          auditor_reasoning="A monthly return beyond +/-50% indicates a "
                            "data error — no asset or strategy in this study "
                            "should move that far in a month.")
    else:
        f("Monthly return bounds", "monthly_return", "pass", "info",
          auditor_reasoning="Every monthly return is within +/-50%.")

    # ── Check 5 — weight constraints ──────────────────────────────────────
    # The backtester persists each strategy's per-rebalance target weights
    # (weight_schedule), so this check runs in full: at every rebalance
    # the weights must sum to 1.0, be non-negative (long-only) and not
    # exceed 1. A strategy cached before weight persistence shipped has
    # empty columns — the check warns rather than failing for it.
    weights = raw.get("strategy_weights", {})
    populated = {n: cols for n, cols in weights.items()
                 if cols and (cols.get("dates"))}
    if not populated:
        f("Weight constraints", "weights", "warning", "info",
          auditor_reasoning="No persisted weight schedule is present in the "
                            "audit payload — the sum-to-1 and long-only "
                            "checks cannot run. Refresh the strategy cache "
                            "(POST /api/v1/cache/invalidate) so the "
                            "backtester repopulates the weight schedule.")
    else:
        bad: list[str] = []
        n_rebalances = 0
        for name, cols in populated.items():
            dates = cols.get("dates") or []
            eq = cols.get("equity") or []
            ig = cols.get("ig") or []
            hy = cols.get("hy") or []
            for i in range(len(dates)):
                n_rebalances += 1
                e, g, h = float(eq[i]), float(ig[i]), float(hy[i])
                if abs((e + g + h) - 1.0) > _WEIGHT_TOLERANCE:
                    bad.append(f"{name}@{dates[i]} sum={e + g + h:.4f}")
                if min(e, g, h) < -_WEIGHT_TOLERANCE:
                    bad.append(f"{name}@{dates[i]} negative weight")
                if max(e, g, h) > 1.0 + _WEIGHT_TOLERANCE:
                    bad.append(f"{name}@{dates[i]} weight>1")
                # BENCHMARK is 100% equity at all times.
                if name == "BENCHMARK" and (
                        abs(e - 1.0) > _WEIGHT_TOLERANCE
                        or g > _WEIGHT_TOLERANCE or h > _WEIGHT_TOLERANCE):
                    bad.append(f"BENCHMARK@{dates[i]} not 100% equity")
        if bad:
            f("Weight constraints", "weights", "fail", "critical",
              platform_value="; ".join(bad[:10]),
              discrepancy=f"{len(bad)} weight-constraint violation(s)",
              auditor_reasoning="Strategy weights must sum to 1.0, be "
                                "non-negative and not exceed 1 at every "
                                "rebalance.")
        else:
            f("Weight constraints", "weights", "pass", "info",
              platform_value=f"{len(populated)} strategies × "
                             f"{n_rebalances} rebalances",
              auditor_reasoning=f"{len(populated)} strategies × "
                                f"{n_rebalances} rebalances verified: all "
                                "weights sum to 1.0, all non-negative, none "
                                "exceed 1.")

    # ── Check 6 — return-series length consistency ────────────────────────
    n_assets = len(equity)
    mismatches: list[str] = []
    for name, series in strategy_returns.items():
        n = len(series)
        if n == 0:
            mismatches.append(f"{name}: empty")
        elif n > n_assets:
            mismatches.append(f"{name}: {n} > {n_assets}")
    if mismatches:
        f("Return series length", "series_length", "fail", "critical",
          platform_value="; ".join(mismatches),
          discrepancy="strategy series longer than the asset series, "
                      "or empty",
          auditor_reasoning="A strategy return series cannot be longer than "
                            "the asset return series, nor empty.")
    else:
        # A strategy series shorter than the asset series is expected —
        # the dynamic strategies consume a lookback window before they
        # produce their first return.
        short = {name: len(s) for name, s in strategy_returns.items()
                 if len(s) < n_assets}
        if short:
            f("Return series length", "series_length", "warning", "info",
              platform_value=f"asset months={n_assets}; shorter: {short}",
              auditor_reasoning=_RETURN_SERIES_NOTE)
        else:
            f("Return series length", "series_length", "pass", "info",
              platform_value=f"all series = {n_assets} months",
              auditor_reasoning="Every strategy return series matches the "
                                "asset series length.")

    return {"status": layer_status(findings), "findings": findings}
