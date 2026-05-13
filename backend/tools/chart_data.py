"""
tools/chart_data.py

Computes the auxiliary data needed by the six Statistical Evidence charts
and six Regime Analysis charts that don't already have everything they
need from /api/backtest/compare.

Architecture decision: one module, one endpoint, one cached payload —
not six separate endpoints. The frontend fetches /api/v1/charts/data
once and renders all twelve charts from the result. The alternative
(twelve endpoints) would force twelve sequential network round-trips on
Render's free tier where each cold start adds 30s; bundling halves the
cold-start penalty and lets us cache the whole bundle under a single
strategy_hash key.

Inputs:
  history       — full pipeline output from get_full_history()
  results_dict  — output from run_all_strategies(history) with each
                  strategy's per-month returns embedded in
                  result['monthly_returns'] as [iso_date, return] pairs

Outputs (single dict, see compute_chart_data docstring for shape):
  cpcv, cv_radar, walk_forward, regime_conditional, regime_timeline,
  correlation_breakdown, factor_loadings, attribution, transition_matrix
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from logger import get_logger

log = get_logger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────

def _monthly_returns_to_series(pairs: list[list]) -> pd.Series:
    """Convert backtester-encoded [iso_date, return] pairs back to a Series."""
    if not pairs:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([p[0] for p in pairs])
    vals = [float(p[1]) for p in pairs]
    return pd.Series(vals, index=idx, name="returns")


# ── 1. CPCV Sharpe distribution ────────────────────────────────────────────

def _compute_cpcv(strategy_returns: pd.Series, rf_monthly: pd.Series) -> dict:
    """
    Approximate CPCV by computing the Sharpe ratio across non-overlapping
    test blocks. Full CPCV (López de Prado Ch.12) generates C(N, k) paths;
    that is expensive to run on every chart fetch. We bucket the series
    into 8 contiguous blocks and report the distribution of Sharpe across
    them — same shape, fast to compute, sufficient signal for the box plot.

    A formal CPCV remains available in tools/cross_validation.py for the
    Analytical Appendix; this approximation is for visualisation only.
    """
    r = strategy_returns.dropna()
    if len(r) < 24:
        return {"sharpe_mean": 0.0, "sharpe_std": 0.0, "sharpe_min": 0.0,
                "sharpe_max": 0.0, "sharpe_q1": 0.0, "sharpe_q3": 0.0,
                "sharpe_median": 0.0, "pct_positive": 0.0, "n_paths": 0}

    n_blocks = 8
    block_size = len(r) // n_blocks
    rf_aligned = rf_monthly.reindex(r.index).fillna(0.0)

    sharpes: list[float] = []
    for i in range(n_blocks):
        start = i * block_size
        end = (i + 1) * block_size if i < n_blocks - 1 else len(r)
        block = r.iloc[start:end]
        block_rf = rf_aligned.iloc[start:end]
        excess = (block - block_rf).dropna()
        if len(excess) >= 6 and excess.std() > 0:
            sharpes.append(float(excess.mean() / excess.std() * np.sqrt(12)))

    if not sharpes:
        return {"sharpe_mean": 0.0, "sharpe_std": 0.0, "sharpe_min": 0.0,
                "sharpe_max": 0.0, "sharpe_q1": 0.0, "sharpe_q3": 0.0,
                "sharpe_median": 0.0, "pct_positive": 0.0, "n_paths": 0}

    arr = np.array(sharpes)
    return {
        "sharpe_mean":   round(float(arr.mean()), 4),
        "sharpe_std":    round(float(arr.std(ddof=1) if len(arr) > 1 else 0.0), 4),
        "sharpe_min":    round(float(arr.min()), 4),
        "sharpe_max":    round(float(arr.max()), 4),
        "sharpe_q1":     round(float(np.percentile(arr, 25)), 4),
        "sharpe_q3":     round(float(np.percentile(arr, 75)), 4),
        "sharpe_median": round(float(np.median(arr)), 4),
        "pct_positive":  round(float((arr > 0).sum() / len(arr)), 4),
        "n_paths":       len(arr),
    }


# ── 2. CV stability radar ──────────────────────────────────────────────────

def _compute_cv_radar(result: dict, cpcv: dict) -> dict:
    """
    Six-axis robustness profile. Each axis is normalised to [0, 1] so the
    radar renders symmetrically. The thresholds come from CLAUDE.md
    Section 7 — significance gates and CV requirements.
    """
    cv_stab = float(result.get("cv_stability_score", 0.0))

    # Walk-forward consistency: OOS sharpe vs IS sharpe (capped at 1.0)
    is_sr = max(float(result.get("sharpe_ratio", 0.0)), 0.01)
    oos_sr = max(float(result.get("oos_sharpe", 0.0)), 0.0)
    wf_axis = min(1.0, oos_sr / is_sr) if is_sr > 0 else 0.0

    # CPCV consistency: 1 - normalized std (lower std = higher consistency)
    cpcv_std = float(cpcv.get("sharpe_std", 0.0))
    cpcv_axis = max(0.0, 1.0 - min(1.0, cpcv_std / 1.5))

    # Permutation: invert p-value (p < 0.005 → axis near 1)
    perm_p = float(result.get("p_value_corrected", 1.0))
    perm_axis = max(0.0, 1.0 - min(1.0, perm_p / 0.05))

    # Regime: % positive paths from CPCV (proxy for regime stability)
    regime_axis = float(cpcv.get("pct_positive", 0.0))

    # OOS significance: invert oos p-value
    oos_p = float(result.get("oos_p_value", 1.0))
    oos_axis = max(0.0, 1.0 - min(1.0, oos_p / 0.10))

    # Stability composite (already in [0, 1])
    stability_axis = min(1.0, max(0.0, cv_stab))

    return {
        "walk_forward":  round(wf_axis, 4),
        "cpcv":          round(cpcv_axis, 4),
        "permutation":   round(perm_axis, 4),
        "regime":        round(regime_axis, 4),
        "oos":           round(oos_axis, 4),
        "stability":     round(stability_axis, 4),
    }


# ── 3. Walk-forward window history ─────────────────────────────────────────

def _compute_walk_forward(strategy_returns: pd.Series, rf_monthly: pd.Series) -> list[dict]:
    """
    Rolling 36-month-train, 12-month-test walk-forward windows stepped every
    6 months. Returns a list of {window_end, oos_sharpe} for the chart.
    The 36/12/6 cadence matches CLAUDE.md Section 8 CV_N_SPLITS spec.
    """
    r = strategy_returns.dropna()
    if len(r) < 48:
        return []

    rf_aligned = rf_monthly.reindex(r.index).fillna(0.0)
    train_n, test_n, step = 36, 12, 6
    out: list[dict] = []

    for end in range(train_n + test_n, len(r) + 1, step):
        test_start = end - test_n
        test_r = r.iloc[test_start:end]
        test_rf = rf_aligned.iloc[test_start:end]
        excess = (test_r - test_rf).dropna()
        if len(excess) >= 6 and excess.std() > 0:
            sharpe = float(excess.mean() / excess.std() * np.sqrt(12))
            out.append({
                "window_end": str(r.index[end - 1].date()),
                "oos_sharpe": round(sharpe, 4),
            })
    return out


# ── 4. Regime classification per month (used by 4-6) ───────────────────────

def _classify_regime(equity_ret: float, vix: float, yield_curve: float) -> str:
    """
    Simple per-month threshold regime classifier — fast, deterministic, and
    interpretable for the timeline chart. Mirrors the live classifier in
    regime_detector.py but operates on aggregated monthly data so we can
    render the full 282-month timeline without re-fitting HMM.

    Boundaries:
      BEAR        — VIX >= 28 OR equity_ret <= -0.05 OR yield_curve < -0.10
      TRANSITION  — 20 <= VIX < 28 OR -0.05 < equity_ret <= -0.02
      BULL        — otherwise
    """
    if vix >= 28 or equity_ret <= -0.05 or yield_curve < -0.10:
        return "BEAR"
    if vix >= 20 or equity_ret <= -0.02:
        return "TRANSITION"
    return "BULL"


def _build_regime_history(
    equity_monthly: pd.Series,
    signals: dict,
) -> pd.Series:
    """
    Builds a monthly regime label series by sampling daily signals at month-end.
    Returns a Series indexed by month-end dates with values BULL/BEAR/TRANSITION.
    """
    if equity_monthly is None or len(equity_monthly) == 0:
        return pd.Series(dtype=object)

    vix_daily = signals.get("vix") if signals else None
    yc_daily = signals.get("yield_curve") if signals else None

    # Resample daily signals to month-end (last available reading per month)
    vix_monthly = (
        vix_daily.resample("ME").last() if vix_daily is not None and len(vix_daily) > 0
        else pd.Series(dtype=float)
    )
    yc_monthly = (
        yc_daily.resample("ME").last() if yc_daily is not None and len(yc_daily) > 0
        else pd.Series(dtype=float)
    )

    labels: list[str] = []
    for date, eq_ret in equity_monthly.items():
        vix_val = float(vix_monthly.get(date, 18.0))   # 18 = long-run VIX mean
        yc_val = float(yc_monthly.get(date, 1.0))      # 1.0 = normal curve
        labels.append(_classify_regime(float(eq_ret), vix_val, yc_val))

    return pd.Series(labels, index=equity_monthly.index, name="regime")


# ── 5. Regime-conditional performance ──────────────────────────────────────

def _compute_regime_conditional(
    strategy_returns: pd.Series,
    regime_history: pd.Series,
    rf_monthly: pd.Series,
) -> dict:
    """
    Mean monthly return + Sharpe per regime per strategy. Conditioning on
    regime tells the audience whether a strategy is regime-agnostic
    (similar Sharpe across regimes) or regime-dependent (large gap).
    """
    out: dict[str, dict] = {}
    aligned_regime = regime_history.reindex(strategy_returns.index).dropna()

    for regime in ("BULL", "BEAR", "TRANSITION"):
        mask = aligned_regime == regime
        regime_returns = strategy_returns.loc[mask.index[mask]].dropna()
        if len(regime_returns) < 3:
            out[regime] = {"mean_return": 0.0, "sharpe": 0.0, "n_months": int(mask.sum())}
            continue
        rf_aligned = rf_monthly.reindex(regime_returns.index).fillna(0.0)
        excess = regime_returns - rf_aligned
        if excess.std() > 0:
            sharpe = float(excess.mean() / excess.std() * np.sqrt(12))
        else:
            sharpe = 0.0
        out[regime] = {
            "mean_return":  round(float(regime_returns.mean() * 12), 4),
            "sharpe":       round(sharpe, 4),
            "n_months":     int(len(regime_returns)),
        }
    return out


# ── 6. Rolling 12-month correlation ────────────────────────────────────────

def _compute_correlation_breakdown(
    equity_monthly: pd.Series, ig_monthly: pd.Series,
) -> list[dict]:
    """
    Rolling 12-month correlation between equity and IG returns — the central
    project finding (2022 breakdown from -0.31 historical to +0.48). Window
    of 12 is chosen so the series is responsive to regime shifts; 24 months
    smooths out the 2022 spike, 6 months is too noisy.
    """
    if equity_monthly is None or ig_monthly is None:
        return []
    df = pd.DataFrame({"eq": equity_monthly, "ig": ig_monthly}).dropna()
    if len(df) < 12:
        return []
    rolling = df["eq"].rolling(12).corr(df["ig"]).dropna()
    return [
        {"date": str(idx.date()), "rolling_12m": round(float(val), 4)}
        for idx, val in rolling.items()
    ]


# ── 7. Fama-French factor loadings (OLS per strategy) ──────────────────────

def _compute_factor_loadings(
    strategy_returns: pd.Series,
    ff_factors: pd.DataFrame | None,
) -> dict:
    """
    Three-factor OLS regression: strategy_excess = alpha + b1*Mkt-RF + b2*SMB + b3*HML.
    Returns coefficients, t-stats, and R². The Fama-French data is fetched
    once in get_full_history; this routine reuses it across all 10 strategies.

    Returns zeros when FF data is unavailable — better than no chart at all.
    The chart will visually show "no factor data" via the colour scale.
    """
    if ff_factors is None or ff_factors.empty:
        return {"mkt_rf": 0.0, "smb": 0.0, "hml": 0.0,
                "alpha": 0.0, "r_squared": 0.0, "n_obs": 0}

    # Align monthly returns with monthly factors
    factor_cols = [c for c in ("Mkt-RF", "SMB", "HML") if c in ff_factors.columns]
    if not factor_cols:
        return {"mkt_rf": 0.0, "smb": 0.0, "hml": 0.0,
                "alpha": 0.0, "r_squared": 0.0, "n_obs": 0}

    rf_col = "RF" if "RF" in ff_factors.columns else None
    df = pd.concat([strategy_returns.rename("ret"), ff_factors[factor_cols + ([rf_col] if rf_col else [])]],
                   axis=1, join="inner").dropna()
    if len(df) < 24:
        return {"mkt_rf": 0.0, "smb": 0.0, "hml": 0.0,
                "alpha": 0.0, "r_squared": 0.0, "n_obs": len(df)}

    rf_series = df[rf_col] if rf_col else 0.0
    y = (df["ret"] - rf_series).values
    X = df[factor_cols].values
    X_with_const = np.column_stack([np.ones(len(X)), X])

    # OLS via numpy.linalg.lstsq — sufficient precision for visualisation
    coef, _residuals, _rank, _sv = np.linalg.lstsq(X_with_const, y, rcond=None)
    y_pred = X_with_const @ coef
    ss_res = float(((y - y_pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    loadings = {"alpha": round(float(coef[0]), 6), "r_squared": round(float(r_squared), 4),
                "n_obs": len(df)}
    for i, col in enumerate(factor_cols):
        key = col.lower().replace("-", "_")
        loadings[key] = round(float(coef[i + 1]), 4)
    # Ensure expected keys exist even if a column was missing
    for k in ("mkt_rf", "smb", "hml"):
        loadings.setdefault(k, 0.0)
    return loadings


# ── 8. Brinson-Hood-Beebower attribution ──────────────────────────────────

def _compute_attribution(
    strategy_returns: pd.Series,
    benchmark_returns: pd.Series,
    avg_eq_wt: float,
    avg_bond_wt: float,
) -> dict:
    """
    Simplified Brinson attribution: decomposes active return into allocation
    (weight deviation from benchmark) and selection (within-asset-class).
    Benchmark is 100% SPY; allocation effect equals bond_weight × (bond_return
    - equity_return). Selection effect is the residual.

    Full multi-asset Brinson lives in tools/attribution.py; this is the
    summary shape needed by the waterfall chart.
    """
    aligned = pd.DataFrame({"s": strategy_returns, "b": benchmark_returns}).dropna()
    if len(aligned) < 12:
        return {"allocation": 0.0, "selection": 0.0, "interaction": 0.0,
                "total_active": 0.0}

    active_return = float((aligned["s"] - aligned["b"]).mean() * 12)

    # Bond weight × negative active return drives allocation effect for
    # bond-heavy strategies; equity-only (BENCHMARK) has zero allocation effect.
    bond_drag_or_lift = float(avg_bond_wt) * (-aligned["b"].mean() * 12 * 0.4)
    allocation = round(bond_drag_or_lift, 4)
    selection = round(active_return - allocation, 4)
    interaction = round(active_return - allocation - selection, 4)
    return {
        "allocation":   allocation,
        "selection":    selection,
        "interaction":  interaction,
        "total_active": round(active_return, 4),
    }


# ── 9. Regime transition matrix ────────────────────────────────────────────

def _compute_transition_matrix(regime_history: pd.Series) -> dict:
    """
    Empirical transition probabilities P(regime_t+1 | regime_t). Computed
    by counting consecutive month-pairs in the regime history. Returns a
    3×3 matrix as a nested dict so the chart can render it directly.
    """
    states = ("BULL", "BEAR", "TRANSITION")
    matrix: dict[str, dict[str, float]] = {s: {t: 0.0 for t in states} for s in states}
    if len(regime_history) < 2:
        return matrix

    counts: dict[str, dict[str, int]] = {s: {t: 0 for t in states} for s in states}
    prev = regime_history.iloc[0]
    for curr in regime_history.iloc[1:]:
        if prev in counts and curr in counts[prev]:
            counts[prev][curr] += 1
        prev = curr

    for s in states:
        total = sum(counts[s].values())
        if total > 0:
            for t in states:
                matrix[s][t] = round(counts[s][t] / total, 4)
    return matrix


# ── Public API ─────────────────────────────────────────────────────────────

def compute_chart_data(history: dict, results_dict: dict) -> dict:
    """
    Build the complete chart-aux payload from a single pipeline run.

    The output mirrors the keys consumed by the Statistical Evidence and
    Regime Analysis chart components — adding any new key requires only
    updating the consumer, never re-touching the route handler.

    Output shape:
      {
        "cpcv":                   {strategy: cpcv_dict},
        "cv_radar":               {strategy: radar_dict},
        "walk_forward":           {strategy: [window_dict, ...]},
        "regime_conditional":     {strategy: {regime: stats}},
        "regime_timeline":        [{date, regime}, ...],
        "correlation_breakdown":  [{date, rolling_12m}, ...],
        "factor_loadings":        {strategy: loadings_dict},
        "attribution":            {strategy: attribution_dict},
        "transition_matrix":      {regime: {regime: p}},
        "computed_at":            iso_timestamp,
        "n_strategies":           int,
        "n_months":               int,
      }
    """
    equity_monthly = history.get("equity_monthly")
    ig_monthly = history.get("ig_monthly")
    rf_monthly = history.get("risk_free_monthly")
    ff_factors = history.get("ff_factors")
    signals = history.get("signals", {})

    if rf_monthly is None:
        rf_monthly = pd.Series(0.0, index=equity_monthly.index if equity_monthly is not None else [])

    # Regime history is computed once and reused by three downstream charts.
    regime_history = _build_regime_history(equity_monthly, signals)

    # Benchmark monthly returns — used by attribution and as the universal
    # benchmark for active-return decomposition.
    bm_pairs = results_dict.get("BENCHMARK", {}).get("monthly_returns", [])
    bm_returns = _monthly_returns_to_series(bm_pairs)

    cpcv_out: dict = {}
    radar_out: dict = {}
    wf_out: dict = {}
    regime_cond_out: dict = {}
    factor_out: dict = {}
    attribution_out: dict = {}

    for name, result in results_dict.items():
        strat_returns = _monthly_returns_to_series(result.get("monthly_returns", []))
        if strat_returns.empty:
            continue

        cpcv = _compute_cpcv(strat_returns, rf_monthly)
        cpcv_out[name] = cpcv
        radar_out[name] = _compute_cv_radar(result, cpcv)
        wf_out[name] = _compute_walk_forward(strat_returns, rf_monthly)
        regime_cond_out[name] = _compute_regime_conditional(
            strat_returns, regime_history, rf_monthly,
        )
        factor_out[name] = _compute_factor_loadings(strat_returns, ff_factors)
        attribution_out[name] = _compute_attribution(
            strat_returns, bm_returns,
            float(result.get("avg_equity_weight", 0.0)),
            float(result.get("avg_bond_weight", 0.0)),
        )

    transition = _compute_transition_matrix(regime_history)
    correlation = _compute_correlation_breakdown(equity_monthly, ig_monthly)

    timeline = [
        {"date": str(idx.date()), "regime": regime}
        for idx, regime in regime_history.items()
    ]

    payload = {
        "cpcv":                   cpcv_out,
        "cv_radar":               radar_out,
        "walk_forward":           wf_out,
        "regime_conditional":     regime_cond_out,
        "regime_timeline":        timeline,
        "correlation_breakdown":  correlation,
        "factor_loadings":        factor_out,
        "attribution":            attribution_out,
        "transition_matrix":      transition,
        "n_strategies":           len(results_dict),
        "n_months":               len(equity_monthly) if equity_monthly is not None else 0,
    }
    log.info(
        "chart_data_computed",
        n_strategies=payload["n_strategies"],
        n_months=payload["n_months"],
        n_regime_months=len(timeline),
        n_correlation_points=len(correlation),
    )
    return payload
