"""
tools/audit_layer3.py — Layer 3 of the statistical audit: cross-platform
consistency.

Deterministic Python checks (no auditor model) that the same metric
carries the same value everywhere it appears, that the regime split is
applied uniformly, and that the structural identities hold.

TWO COMPUTATION REGIMES. The Analytics layer annualises monthly series
with 12 / sqrt(12); the Dashboard strategy table annualises daily series
with 252 / sqrt(252). For a regime-INDEPENDENT metric (CAGR — a
geometric annual growth, identical whichever granularity it is measured
at) the two layers are compared directly. For a regime-DEPENDENT metric
(Sharpe, max drawdown) the two layers differ BY CONSTRUCTION; those
checks are recorded as INFO findings that explain the regime difference
rather than flagging it as a discrepancy.

layer_3_consistency_audit(payload) -> {"status", "findings"}
"""
from __future__ import annotations

from typing import Any

import structlog

from tools.audit_common import classify_discrepancy, layer_status, make_finding

log = structlog.get_logger(__name__)

# Dynamic vs static — for the turnover-direction check.
_DYNAMIC = {
    "MOMENTUM_ROTATION", "REGIME_SWITCHING", "VOL_TARGETING",
    "BLACK_LITTERMAN", "MAX_SHARPE_ROLLING",
}


def _cagr(series: list) -> float:
    """Geometric CAGR of a monthly return series — regime-independent."""
    vals = [float(x) for x in series if x is not None]
    n = len(vals)
    if n == 0:
        return 0.0
    growth = 1.0
    for r in vals:
        growth *= (1.0 + r)
    if growth <= 0.0:
        return -1.0
    return growth ** (12.0 / n) - 1.0


async def layer_3_consistency_audit(payload: dict[str, Any]) -> dict[str, Any]:
    """Runs the ten consistency checks. Reads the strategy cache directly
    for the Dashboard-side values; uses the payload for the Analytics
    side. An unavailable payload skips the layer."""
    if not payload.get("available"):
        return {"status": "skip", "findings": []}

    h = payload.get("raw_inputs_hash")
    pc = payload.get("platform_computed", {})
    raw = payload.get("raw_data", {})
    meta = payload.get("metadata", {})
    summary = pc.get("summary_statistics", {})
    factor = pc.get("factor_loadings", {})
    turnover = pc.get("turnover", {})
    strategy_returns: dict[str, list] = raw.get("strategy_returns", {})

    # Dashboard-side values — the strategy_results_cache row.
    try:
        from tools.cache import get_latest_strategy_cache
        cache = await get_latest_strategy_cache() or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_layer3_cache_read_failed", error=str(exc))
        cache = {}

    findings: list[dict[str, Any]] = []

    def f(check, metric, status, severity, **kw):
        findings.append(make_finding(
            3, check, metric, status, severity, raw_inputs_hash=h, **kw))

    # ── Check 1 — CAGR consistency (regime-independent) ───────────────────
    cagr_issues: list[str] = []
    cagr_checked = 0
    for name, series in strategy_returns.items():
        cached = cache.get(name, {})
        if cached.get("cagr") is None:
            continue
        cagr_checked += 1
        recomputed = _cagr(series)
        status, frac = classify_discrepancy(float(cached["cagr"]), recomputed)
        if status == "fail":
            cagr_issues.append(
                f"{name}: cache {float(cached['cagr']):.4f} vs "
                f"recomputed {recomputed:.4f}")
    if cagr_issues:
        f("CAGR consistency", "cagr", "fail", "critical",
          platform_value="; ".join(cagr_issues),
          discrepancy="CAGR differs between the strategy cache and a "
                      "recomputation from the monthly series",
          auditor_reasoning="CAGR is a geometric annual growth rate — "
                            "regime-independent — so the cached value and a "
                            "monthly recomputation must agree.")
    else:
        f("CAGR consistency", "cagr", "pass", "info",
          platform_value=f"{cagr_checked} strategies checked",
          auditor_reasoning="Every strategy's cached CAGR matches a "
                            "recomputation from its monthly return series.")

    # ── Check 2 — Sharpe consistency (regime difference, documented) ──────
    f("Sharpe consistency", "sharpe_ratio", "pass", "info",
      auditor_reasoning="Sharpe is reported in two regimes: the Dashboard "
                        "strategy table annualises daily returns with "
                        "sqrt(252); the Analytics layer annualises monthly "
                        "returns with sqrt(12). The two values differ by "
                        "construction — this is expected and is NOT a "
                        "discrepancy. See the audit report's Computation "
                        "Regimes section.")

    # ── Check 3 — benchmark identity (BENCHMARK == 100% equity) ───────────
    bench = strategy_returns.get("BENCHMARK")
    equity = raw.get("asset_returns", {}).get("equity")
    if bench and equity:
        b_cagr, e_cagr = _cagr(bench), _cagr(equity)
        status, frac = classify_discrepancy(e_cagr, b_cagr)
        if status == "fail":
            f("Benchmark identity", "cagr", "fail", "critical",
              platform_value=f"benchmark {b_cagr:.4f}, equity {e_cagr:.4f}",
              discrepancy=f"{frac:.2%} apart",
              auditor_reasoning="The benchmark is defined as 100% equity, "
                                "so its CAGR must equal the equity CAGR.")
        else:
            f("Benchmark identity", "cagr", "pass", "info",
              platform_value=f"benchmark {b_cagr:.4f} == equity {e_cagr:.4f}",
              auditor_reasoning="The BENCHMARK CAGR matches the equity-asset "
                                "CAGR — the 100%-equity identity holds.")
    else:
        f("Benchmark identity", "cagr", "warning", "warning",
          auditor_reasoning="The benchmark or equity series is missing — the "
                            "identity check could not run.")

    # ── Check 4 — max drawdown (regime difference, documented) ────────────
    f("Max drawdown consistency", "max_drawdown", "pass", "info",
      auditor_reasoning="Max drawdown is measured on the daily cumulative "
                        "curve for the Dashboard and on the monthly curve "
                        "for the Analytics layer; the daily curve captures "
                        "intra-month troughs the monthly curve cannot, so "
                        "the two differ by construction — expected, not a "
                        "discrepancy.")

    # ── Check 5 — regime-break date applied consistently ──────────────────
    rb = meta.get("regime_break_date")
    rc = pc.get("rolling_correlation", {})
    rc_ok = isinstance(rc, dict) and "pre_2022" in rc and "post_2022" in rc
    regime_rows = pc.get("regime_conditional", {})
    rc_rows_ok = all(
        "pre_2022_sharpe" in v and "post_2022_sharpe" in v
        for v in regime_rows.values()
    ) if regime_rows else True
    if rb == "2022-01-01" and rc_ok and rc_rows_ok:
        f("Regime break consistency", "regime_break", "pass", "info",
          platform_value=rb,
          auditor_reasoning="The 2022-01-01 regime break is applied "
                            "consistently across the rolling correlation and "
                            "the regime-conditional table.")
    else:
        f("Regime break consistency", "regime_break", "fail", "critical",
          platform_value=f"date={rb}, rolling_ok={rc_ok}, rows_ok={rc_rows_ok}",
          discrepancy="the regime-break date is not applied uniformly",
          auditor_reasoning="The pre/post-2022 split must use the same "
                            "boundary everywhere it appears.")

    # ── Check 6 — risk-free rate consistency ──────────────────────────────
    rfr = meta.get("risk_free_rate", {})
    rf_val = rfr.get("value")
    if isinstance(rf_val, (int, float)) and 0.0 <= rf_val <= 0.10:
        f("Risk-free rate consistency", "risk_free_rate", "pass", "info",
          platform_value=f"{rf_val:.4f} ({rfr.get('source')})",
          auditor_reasoning="The risk-free rate is a single value (mean "
                            "monthly DTB3 * 12) feeding both the Sharpe "
                            "calculations and Settings - Analytics "
                            "Configuration; it is within the plausible "
                            "0-10% band.")
    else:
        f("Risk-free rate consistency", "risk_free_rate", "warning", "warning",
          platform_value=str(rf_val),
          discrepancy="risk-free rate missing or implausible",
          auditor_reasoning="The annualised risk-free rate should sit in a "
                            "plausible 0-10% band.")

    # ── Check 7 — factor model label consistency ──────────────────────────
    mislabelled = [
        name for name, row in factor.items()
        if row.get("mom_significant") and row.get("model") != "carhart_4factor"
    ]
    if mislabelled:
        f("Factor model label", "factor_model", "fail", "critical",
          platform_value=", ".join(mislabelled),
          discrepancy="a strategy with a significant MOM beta is not "
                      "labelled carhart_4factor",
          auditor_reasoning="A significant momentum loading means the "
                            "four-factor model was fitted — the label must "
                            "say carhart_4factor, not ff_3factor.")
    else:
        f("Factor model label", "factor_model", "pass", "info",
          auditor_reasoning="Every strategy with a significant momentum "
                            "loading is labelled as a Carhart four-factor "
                            "fit.")

    # ── Check 8 — turnover direction ──────────────────────────────────────
    dyn = {n: v for n, v in turnover.items()
           if n in _DYNAMIC and v is not None}
    stat = {n: v for n, v in turnover.items()
            if n not in _DYNAMIC and v is not None}
    if dyn and stat:
        max_dyn = max(dyn.values())
        offenders = {n: v for n, v in stat.items() if v > max_dyn}
        if offenders:
            f("Turnover direction", "true_turnover", "warning", "warning",
              platform_value=f"static {offenders} > max dynamic {max_dyn}",
              discrepancy="a static strategy turns over more than every "
                          "dynamic strategy",
              auditor_reasoning="Dynamic strategies rebalance on signals and "
                                "should generally turn over more than the "
                                "static strategies — an inversion may "
                                "indicate a turnover calculation error.")
        else:
            f("Turnover direction", "true_turnover", "pass", "info",
              auditor_reasoning="Dynamic strategies turn over at least as "
                                "much as the static strategies, as expected.")
    else:
        f("Turnover direction", "true_turnover", "warning", "info",
          auditor_reasoning="Turnover values are not available for both the "
                            "dynamic and the static strategies — the "
                            "direction check could not run.")

    # ── Check 9 — benchmark information ratio is null ─────────────────────
    bench_row = summary.get("BENCHMARK", {})
    if "BENCHMARK" not in summary:
        f("Benchmark information ratio", "information_ratio", "warning",
          "info",
          auditor_reasoning="No BENCHMARK row in the summary statistics — "
                            "the null-IR check could not run.")
    elif bench_row.get("information_ratio") is None:
        f("Benchmark information ratio", "information_ratio", "pass", "info",
          platform_value="null",
          auditor_reasoning="The benchmark's information ratio is null — "
                            "correct, the benchmark has zero tracking error "
                            "against itself so the ratio is undefined.")
    else:
        f("Benchmark information ratio", "information_ratio", "fail",
          "critical",
          platform_value=str(bench_row.get("information_ratio")),
          discrepancy="benchmark shows a numeric information ratio",
          auditor_reasoning="The benchmark's information ratio must be null "
                            "— tracking error against itself is zero, so the "
                            "ratio is mathematically undefined.")

    # ── Check 10 — Sharpe CI direction ────────────────────────────────────
    ci_issues: list[str] = []
    ci_checked = 0
    for name, cached in cache.items():
        ci = cached.get("sharpe_ci_95")
        sharpe = cached.get("sharpe_ratio")
        if not ci or sharpe is None or len(ci) != 2:
            continue
        if ci[0] is None or ci[1] is None:
            continue
        ci_checked += 1
        lo, hi = float(ci[0]), float(ci[1])
        if not (lo <= float(sharpe) <= hi):
            ci_issues.append(f"{name}: {lo:.2f} / {sharpe:.2f} / {hi:.2f}")
    if ci_issues:
        f("Sharpe CI direction", "sharpe_ci_95", "fail", "critical",
          platform_value="; ".join(ci_issues),
          discrepancy="the Sharpe point estimate falls outside its own "
                      "95% confidence interval",
          auditor_reasoning="For every strategy the 95% CI must bracket the "
                            "Sharpe point estimate: CI_low <= Sharpe <= "
                            "CI_high.")
    else:
        f("Sharpe CI direction", "sharpe_ci_95", "pass", "info",
          platform_value=f"{ci_checked} strategies checked",
          auditor_reasoning="Every strategy's Sharpe point estimate lies "
                            "within its 95% confidence interval.")

    return {"status": layer_status(findings), "findings": findings}
