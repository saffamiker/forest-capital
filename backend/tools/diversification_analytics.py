"""tools/diversification_analytics.py — item 8 diversification suite.

Seven new analytics metrics built on the same monthly return series
+ benchmark as the existing tools/analytics.py reductions. Pure
NumPy / pandas / scipy; storage via analytics_metrics_cache (one row
per metric_kind keyed by data_hash) hooked into the strategy_cache
write path through tools/precomputed_analytics.py.

ALL METRICS:
  1. correlation_matrices    — 11x11 Pearson (full + pre/post-2022)
  2. tail_risk               — VaR + CVaR at 95% / 99%, monthly + annual
  3. capture_ratios          — up/down + capture score (3 windows)
  4. drawdown_duration       — avg / max / recovery / current per strategy
  5. crisis_performance      — CAGR + max DD + Sharpe over 5 windows
  6. marginal_contribution_to_risk — MCTR + % contribution (equal + tangency)
  7. return_distribution     — skewness / kurtosis / JB / best+worst months
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import structlog

log = structlog.get_logger(__name__)

# Pre/post-2022 split — the platform's central regime-break finding.
# Mirrors tools/analytics.REGIME_BREAK; imported rather than
# redeclared so a future shift to the cutover date propagates.
from tools.analytics import (
    REGIME_BREAK as _DIVERSIFICATION_REGIME_BREAK,
    _cagr, _max_drawdown, _sharpe, _pairs_to_series,
)


def _series_map(strategy_results: dict[str, dict]) -> dict[str, pd.Series]:
    """strategy_name -> monthly return series. Empty series filtered."""
    out: dict[str, pd.Series] = {}
    for name, res in strategy_results.items():
        s = _pairs_to_series(res.get("monthly_returns") or [])
        if not s.empty:
            out[res.get("strategy_name") or name] = s
    return out


# ── 1. Correlation matrix (full + pre-2022 + post-2022) ────────────────────

def correlation_matrices(
    strategy_results: dict[str, dict],
) -> dict[str, Any]:
    """Pairwise Pearson correlation across all strategies + benchmark.
    Three matrices: full period, pre-2022, post-2022. Diagonal is
    always 1.0. Order-preserving on input strategy names."""
    series_map = _series_map(strategy_results)
    if not series_map:
        return {"labels": [], "full": [], "pre_2022": [], "post_2022": [],
                "diagonal": 1.0}
    labels = list(series_map.keys())
    df = pd.DataFrame(series_map).sort_index()

    def _matrix(frame: pd.DataFrame) -> list[list[Any]]:
        if frame.empty or len(frame) < 2:
            return [[1.0 if i == j else None for j in range(len(labels))]
                    for i in range(len(labels))]
        corr = frame[labels].corr()
        return [[round(float(corr.iloc[i, j]), 4)
                 for j in range(len(labels))]
                for i in range(len(labels))]

    pre = df[df.index < _DIVERSIFICATION_REGIME_BREAK]
    post = df[df.index >= _DIVERSIFICATION_REGIME_BREAK]
    return {
        "labels":     labels,
        "full":       _matrix(df),
        "pre_2022":   _matrix(pre),
        "post_2022":  _matrix(post),
        "diagonal":   1.0,
    }


# ── 2. Tail risk — VaR + CVaR ──────────────────────────────────────────────

def _historical_var(returns: pd.Series, confidence: float) -> float:
    """Historical-simulation VaR — empirical percentile, NOT parametric.
    99% confidence = 1st percentile (more negative than 95% = 5th)."""
    if len(returns) == 0:
        return 0.0
    pct = 1.0 - confidence
    return float(returns.quantile(pct))


def _historical_cvar(returns: pd.Series, confidence: float) -> float:
    """Conditional VaR (Expected Shortfall) — mean of returns below
    the VaR threshold. CVaR <= VaR at the same confidence level."""
    if len(returns) == 0:
        return 0.0
    var = _historical_var(returns, confidence)
    tail = returns[returns <= var]
    return float(tail.mean()) if len(tail) > 0 else var


def tail_risk(strategy_results: dict[str, dict]) -> list[dict]:
    """VaR + CVaR per strategy at 95% / 99% confidence, monthly +
    annualised. Sorted by cvar_99_annual ASCENDING (most-negative =
    highest tail risk first)."""
    series_map = _series_map(strategy_results)
    rows: list[dict] = []
    sqrt12 = 12.0 ** 0.5
    for name, s in series_map.items():
        v95 = _historical_var(s, 0.95)
        v99 = _historical_var(s, 0.99)
        c95 = _historical_cvar(s, 0.95)
        c99 = _historical_cvar(s, 0.99)
        rows.append({
            "strategy":         name,
            "var_95_monthly":   round(v95, 4),
            "var_99_monthly":   round(v99, 4),
            "cvar_95_monthly":  round(c95, 4),
            "cvar_99_monthly":  round(c99, 4),
            "var_95_annual":    round(v95 * sqrt12, 4),
            "var_99_annual":    round(v99 * sqrt12, 4),
            "cvar_95_annual":   round(c95 * sqrt12, 4),
            "cvar_99_annual":   round(c99 * sqrt12, 4),
        })
    rows.sort(key=lambda r: r["cvar_99_annual"])
    return rows


# ── 3. Up / Down capture ratios ────────────────────────────────────────────

def _capture_for_window(
    strategy: pd.Series, benchmark: pd.Series,
) -> dict[str, Any]:
    aligned = pd.concat([strategy, benchmark], axis=1,
                        keys=["s", "b"]).dropna()
    if aligned.empty:
        return {"up_capture": None, "down_capture": None,
                "capture_score": None}
    up = aligned[aligned["b"] > 0]
    dn = aligned[aligned["b"] < 0]
    up_b = float(up["b"].mean()) if len(up) > 0 else 0.0
    up_s = float(up["s"].mean()) if len(up) > 0 else 0.0
    dn_b = float(dn["b"].mean()) if len(dn) > 0 else 0.0
    dn_s = float(dn["s"].mean()) if len(dn) > 0 else 0.0
    up_cap = (up_s / up_b * 100.0) if abs(up_b) > 1e-12 else None
    dn_cap = (dn_s / dn_b * 100.0) if abs(dn_b) > 1e-12 else None
    score = (up_cap / dn_cap) if (
        up_cap is not None and dn_cap is not None
        and abs(dn_cap) > 1e-12
    ) else None
    return {
        "up_capture":    round(up_cap, 2) if up_cap is not None else None,
        "down_capture":  round(dn_cap, 2) if dn_cap is not None else None,
        "capture_score": round(score, 3) if score is not None else None,
    }


def capture_ratios(strategy_results: dict[str, dict]) -> list[dict]:
    """Up / Down capture vs benchmark over full + pre-2022 + post-2022."""
    series_map = _series_map(strategy_results)
    if "BENCHMARK" not in series_map:
        return []
    bench = series_map["BENCHMARK"]
    bench_pre = bench[bench.index < _DIVERSIFICATION_REGIME_BREAK]
    bench_post = bench[bench.index >= _DIVERSIFICATION_REGIME_BREAK]
    rows: list[dict] = []
    for name, s in series_map.items():
        if name == "BENCHMARK":
            continue
        s_pre = s[s.index < _DIVERSIFICATION_REGIME_BREAK]
        s_post = s[s.index >= _DIVERSIFICATION_REGIME_BREAK]
        rows.append({
            "strategy":   name,
            "full":       _capture_for_window(s, bench),
            "pre_2022":   _capture_for_window(s_pre, bench_pre),
            "post_2022":  _capture_for_window(s_post, bench_post),
        })
    rows.sort(key=lambda r: (r["full"].get("capture_score") or -999),
              reverse=True)
    return rows


# ── 4. Drawdown duration ───────────────────────────────────────────────────

def _drawdown_episodes(series: pd.Series) -> list[dict[str, Any]]:
    """Cumulative-return walk -> list of {start, trough, end,
    duration, recovery}. Open-ended episode at series end carries
    end=None, recovery=None."""
    if len(series) == 0:
        return []
    curve = (1.0 + series).cumprod()
    peak = curve.cummax()
    in_dd = (curve < peak).to_numpy()
    episodes: list[dict[str, Any]] = []
    start = None
    trough_i = None
    trough_val = float("inf")
    for i in range(len(curve)):
        if in_dd[i]:
            if start is None:
                start = i
                trough_i = i
                trough_val = float(curve.iloc[i])
            elif float(curve.iloc[i]) < trough_val:
                trough_i = i
                trough_val = float(curve.iloc[i])
        else:
            if start is not None:
                episodes.append({
                    "start":    str(curve.index[start].date()),
                    "trough":   str(curve.index[trough_i].date())
                                if trough_i is not None else None,
                    "end":      str(curve.index[i].date()),
                    "duration": i - start,
                    "recovery": i - trough_i if trough_i is not None else None,
                })
                start = None
                trough_i = None
                trough_val = float("inf")
    if start is not None:
        episodes.append({
            "start":    str(curve.index[start].date()),
            "trough":   str(curve.index[trough_i].date())
                        if trough_i is not None else None,
            "end":      None,
            "duration": len(curve) - 1 - start,
            "recovery": None,
        })
    return episodes


def drawdown_duration(strategy_results: dict[str, dict]) -> list[dict]:
    """Avg / max drawdown duration + recovery time + current
    in-drawdown state per strategy. Sorted by max_duration desc."""
    series_map = _series_map(strategy_results)
    rows: list[dict] = []
    for name, s in series_map.items():
        eps = _drawdown_episodes(s)
        completed_durations = [e["duration"] for e in eps
                               if e["end"] is not None]
        completed_recoveries = [e["recovery"] for e in eps
                                if e["recovery"] is not None]
        open_ep = next((e for e in eps if e["end"] is None), None)
        rows.append({
            "strategy": name,
            "avg_duration_months":
                round(sum(completed_durations)
                      / len(completed_durations), 1)
                if completed_durations else 0.0,
            "max_duration_months":
                int(max(e["duration"] for e in eps)) if eps else 0,
            "avg_recovery_months":
                round(sum(completed_recoveries)
                      / len(completed_recoveries), 1)
                if completed_recoveries else 0.0,
            "longest_recovery_months":
                int(max(completed_recoveries))
                if completed_recoveries else 0,
            "currently_in_drawdown":
                open_ep is not None,
            "current_drawdown_months":
                int(open_ep["duration"]) if open_ep else 0,
        })
    rows.sort(key=lambda r: r["max_duration_months"], reverse=True)
    return rows


# ── 5. Crisis period performance ───────────────────────────────────────────

_CRISIS_WINDOWS: dict[str, tuple[str, str]] = {
    "GFC_2008-2009":      ("2008-01-01", "2009-03-31"),
    "EU_Debt_2011":       ("2011-07-01", "2011-10-31"),
    "COVID_Crash_2020":   ("2020-02-01", "2020-03-31"),
    "COVID_Recovery":     ("2020-04-01", "2021-12-31"),
    "Rate_Shock_2022":    ("2022-01-01", "2022-12-31"),
}


def crisis_performance(strategy_results: dict[str, dict]) -> dict[str, Any]:
    """CAGR / max-DD / Sharpe per strategy over 5 crisis windows.
    Partial overlap (strategy starts after the window's start, or
    series ends before the window's end) flagged as partial=True."""
    series_map = _series_map(strategy_results)
    rows: dict[str, dict[str, Any]] = {}
    for name, s in series_map.items():
        rows[name] = {}
        for crisis, (start_iso, end_iso) in _CRISIS_WINDOWS.items():
            start = pd.Timestamp(start_iso)
            end = pd.Timestamp(end_iso)
            window = s[(s.index >= start) & (s.index <= end)]
            if len(window) < 2:
                rows[name][crisis] = {
                    "cagr": None, "max_dd": None, "sharpe": None,
                    "partial": True, "n_months": int(len(window)),
                }
                continue
            actual_start = window.index[0]
            actual_end = window.index[-1]
            partial = (
                actual_start > start
                or actual_end < end - pd.Timedelta(days=31)
            )
            rows[name][crisis] = {
                "cagr":     round(_cagr(window), 4),
                "max_dd":   round(_max_drawdown(window), 4),
                "sharpe":   round(_sharpe(window), 3),
                "partial":  bool(partial),
                "n_months": int(len(window)),
            }
    return {
        "windows": {
            crisis: {"start": s, "end": e}
            for crisis, (s, e) in _CRISIS_WINDOWS.items()
        },
        "rows": rows,
    }


# ── 6. Marginal Contribution to Risk ───────────────────────────────────────

def marginal_contribution_to_risk(
    strategy_results: dict[str, dict],
    tangency_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """MCTR + % risk contribution for equal-weight and tangency-weight
    portfolios. MCTR_i = (Sigma w)_i / sigma_p ; pct = w_i * MCTR_i / sigma_p

    May 25 2026 — tangency weights now computed inline when the caller
    omits them. The frontend Marginal Contribution to Risk toggle had
    'Tangency (max Sharpe)' permanently disabled because the
    /api/v1/analytics/risk-contribution endpoint called this helper
    without tangency_weights — leaving every tangency_* field None and
    the UI button greyed out. The fix wires the optimizer in here so a
    bare call still produces tangency outputs.

    Fallback semantics: max_sharpe_optimize itself falls back to
    min_variance when every strategy's excess return is non-positive
    (the SLSQP problem is then infeasible). When that fallback fires,
    the weights returned are min-variance weights — still a valid
    long-only mix, but not strictly the Sharpe tangency. A
    `tangency_fallback_to_min_variance` flag is set on the response so
    the frontend can label the toggle accurately rather than
    misrepresenting min-variance weights as 'max Sharpe'.
    """
    series_map = {k: v for k, v in _series_map(strategy_results).items()
                  if k != "BENCHMARK"}
    if len(series_map) < 2:
        return {"labels": [], "mctr_equal_weight": [],
                "pct_risk_contribution_equal": [],
                "mctr_tangency_weight": None,
                "pct_risk_contribution_tangency": None,
                "tangency_weights": None,
                "tangency_fallback_to_min_variance": False}

    labels = list(series_map.keys())
    df = pd.DataFrame(series_map).dropna()
    cov = df.cov().to_numpy()
    n = len(labels)

    def _compute(weights: list[float]) -> tuple[list[float], list[float]]:
        w = np.array(weights)
        port_var = float(w @ cov @ w)
        port_sigma = port_var ** 0.5 if port_var > 0 else 0.0
        if port_sigma <= 0:
            return [0.0] * n, [0.0] * n
        mctr_vec = (cov @ w) / port_sigma
        pct = (w * mctr_vec / port_sigma) * 100.0
        return [round(float(x), 5) for x in mctr_vec], \
               [round(float(x), 3) for x in pct]

    equal_w = [1.0 / n] * n
    mctr_eq, pct_eq = _compute(equal_w)
    out: dict[str, Any] = {
        "labels":                        labels,
        "mctr_equal_weight":             mctr_eq,
        "pct_risk_contribution_equal":   pct_eq,
        "mctr_tangency_weight":          None,
        "pct_risk_contribution_tangency": None,
        "tangency_weights":              None,
        # Default False — set True only when we KNOW the optimizer
        # fell back. A caller-provided tangency_weights dict is taken
        # at face value (no fallback claim).
        "tangency_fallback_to_min_variance": False,
    }

    # If the caller passed explicit weights, use them verbatim.
    # Otherwise compute via the max-Sharpe optimizer so the UI toggle
    # works on a bare call.
    if tangency_weights is None:
        try:
            from tools.optimizer import max_sharpe_optimize
            # Detect the all-non-positive-excess case BEFORE
            # max_sharpe_optimize runs — its internal fallback to
            # min_variance is silent (only a log line), but we want
            # the response flag so the frontend can relabel.
            mu = df.mean().to_numpy()
            # The optimizer infers periods_per_year from the index
            # when None — monthly returns → 12. Use the same
            # convention here so the fallback detection matches.
            periods_per_year = 12
            rf_per_period = 0.0  # caller passes risk_free=0 below too
            excess = mu - rf_per_period
            fallback = bool(float(np.max(excess)) <= 0.0)
            weights_arr = max_sharpe_optimize(df, risk_free=0.0)
            tangency_weights = {
                lbl: float(w) for lbl, w in zip(labels, weights_arr)
            }
            out["tangency_fallback_to_min_variance"] = fallback
        except Exception as exc:  # noqa: BLE001
            # Optimizer unavailable (cvxpy missing in a test env) or a
            # solver crash on degenerate covariance — leave the
            # tangency_* fields None so the frontend disables the
            # toggle exactly as before. Better to surface "unavailable"
            # than to claim a verdict from a failed solve.
            log.warning("mctr_tangency_compute_failed", error=str(exc))
            return out

    tw = [float(tangency_weights.get(lbl, 0.0)) for lbl in labels]
    s_tw = sum(tw)
    if s_tw > 0:
        tw = [w / s_tw for w in tw]
        mctr_tg, pct_tg = _compute(tw)
        out["mctr_tangency_weight"] = mctr_tg
        out["pct_risk_contribution_tangency"] = pct_tg
        out["tangency_weights"] = [round(w, 4) for w in tw]
    return out


# ── 7. Return distribution metrics ─────────────────────────────────────────

def return_distribution(strategy_results: dict[str, dict]) -> list[dict]:
    """Skewness / excess kurtosis / Jarque-Bera + best/worst 3 monthly
    returns per strategy + benchmark."""
    series_map = _series_map(strategy_results)
    try:
        from scipy import stats as scipy_stats
    except ImportError:
        scipy_stats = None
    rows: list[dict] = []
    for name, s in series_map.items():
        if len(s) < 4:
            continue
        sk = float(s.skew())
        ek = float(s.kurtosis())  # pandas returns EXCESS kurtosis
        jb_stat = None
        jb_p = None
        if scipy_stats is not None:
            try:
                jb = scipy_stats.jarque_bera(s.dropna())
                jb_stat = float(jb.statistic)
                jb_p = float(jb.pvalue)
            except Exception:  # noqa: BLE001
                pass
        sorted_desc = s.sort_values(ascending=False)
        best_3 = [{"date": str(d.date()), "ret": round(float(v), 4)}
                  for d, v in sorted_desc.head(3).items()]
        worst_3 = [{"date": str(d.date()), "ret": round(float(v), 4)}
                   for d, v in sorted_desc.tail(3).items()]
        rows.append({
            "strategy":          name,
            "skewness":          round(sk, 3),
            "excess_kurtosis":   round(ek, 3),
            "jarque_bera_stat":  round(jb_stat, 3) if jb_stat is not None else None,
            "jarque_bera_p":     round(jb_p, 4) if jb_p is not None else None,
            "normality_passes":  (jb_p is not None and jb_p >= 0.05),
            "best_months":       best_3,
            "worst_months":      worst_3,
        })
    rows.sort(key=lambda r: r["strategy"])
    return rows
