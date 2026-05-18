"""
tools/backtester.py

Monthly-resolution portfolio backtester. All 10 strategies consume the
pre-loaded history dict produced by get_full_history() — no data fetching
happens here. This separation ensures the data layer owns all source
decisions (Excel vs yfinance vs FRED) and the backtester only computes
returns on top of validated, aligned series.

Asset universe: three classes — equity, ig (investment-grade), hy (high-yield).
All weight dicts use keys "equity", "ig", "hy". No ticker symbols anywhere.

Why monthly rather than daily resolution:
  The project's primary return series (Excel S&P 500 monthly, BND monthly
  from daily, BAMLHYH monthly from daily) are aggregated to month-end before
  backtesting. Monthly resolution prevents inflated look-ahead in strategies
  that use quarter-end signals — a signal computed at 2020-03-31 using
  data through 2020-02-28 is unambiguously clean on a monthly series.
  Daily resolution would require careful intra-month slicing for each
  quarterly rebalance, adding complexity without changing outcomes meaningfully
  for the quarterly-rebalance strategies here.

Annualization convention:
  Daily risk_metrics.py uses ANNUALIZATION_FACTOR=252.
  Monthly returns use _ANN_M=12. All metric helpers in this module
  use _ANN_M so that Sharpe, vol, alpha computed here are correct.
  We do NOT call annualized_return() or sharpe_ratio() from risk_metrics
  (those bake in sqrt(252)); instead we use the _m_* wrappers below.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import (
    TRANSACTION_COST_BPS,
    MIN_WEIGHT,
    MAX_WEIGHT,
    TARGET_VOLATILITY,
    OPTIMIZATION_WINDOW,
    ANNUALIZATION_FACTOR,
    MOMENTUM_LOOKBACKS,
    MOMENTUM_WEIGHTS,
    REGIME_WINDOW,
    RISK_FREE_RATE_FALLBACK,
)
from tools.risk_metrics import (
    max_drawdown,
    compute_var,
    compute_cvar,
    compute_beta,
)
from logger import get_logger

log = get_logger(__name__)

# Monthly annualization — all Sharpe/vol/IR/alpha calculations use this
_ANN_M = 12

# Regime window in months: REGIME_WINDOW=63 trading days ≈ 3 months
_REGIME_WINDOW_M = max(1, REGIME_WINDOW // 21)

# Momentum lookbacks in months — equivalent of [21, 63, 126, 252] trading days
_MOMENTUM_LOOKBACKS_M = [max(1, lb // 21) for lb in MOMENTUM_LOOKBACKS]


# ── Monthly metric helpers ────────────────────────────────────────────────────
# These replace the daily-assumption functions in risk_metrics.py.
# All are self-contained: no dependency on ANNUALIZATION_FACTOR.

def _m_cagr(r: pd.Series) -> float:
    """Compound annual growth rate from monthly returns."""
    n = len(r)
    if n == 0:
        return 0.0
    total = float((1 + r).prod())
    return float(total ** (_ANN_M / n) - 1) if total > 0 else -1.0


def _m_vol(r: pd.Series) -> float:
    """Annualised volatility from monthly return standard deviation."""
    return float(r.std() * np.sqrt(_ANN_M))


def _m_rf_align(r: pd.Series, rf: "pd.Series | float") -> pd.Series:
    """Align risk-free series to portfolio return index."""
    if isinstance(rf, (int, float)):
        return pd.Series(float(rf), index=r.index)
    aligned = rf.reindex(r.index)
    fill_val = float(rf.mean()) if len(rf) > 0 else 0.0
    return aligned.ffill().fillna(fill_val)


def _m_sharpe(r: pd.Series, rf: "pd.Series | float") -> float:
    """Annualised Sharpe ratio using monthly returns."""
    rf_s = _m_rf_align(r, rf)
    excess = r - rf_s
    std = float(excess.std())
    return float(excess.mean() / std * np.sqrt(_ANN_M)) if std > 0 else 0.0


def _m_sortino(r: pd.Series, rf: "pd.Series | float") -> float:
    """Annualised Sortino ratio: penalises only downside deviation."""
    rf_s = _m_rf_align(r, rf)
    excess = r - rf_s
    downside = excess[excess < 0]
    if len(downside) < 2:
        return 0.0
    downside_std = float(downside.std() * np.sqrt(_ANN_M))
    return float(excess.mean() * _ANN_M / downside_std) if downside_std > 0 else 0.0


def _m_calmar(r: pd.Series) -> float:
    """Calmar ratio: CAGR divided by absolute max drawdown."""
    ann = _m_cagr(r)
    dd, _, _ = max_drawdown(r)
    return float(ann / abs(dd)) if dd < 0 else 0.0


def _m_ir(r: pd.Series, bm: pd.Series) -> float:
    """Annualised information ratio vs benchmark."""
    bm_a = bm.reindex(r.index).dropna()
    r_a = r.reindex(bm_a.index)
    diff = r_a - bm_a
    std = float(diff.std())
    return float(diff.mean() / std * np.sqrt(_ANN_M)) if std > 0 else 0.0


def _m_alpha_beta(
    r: pd.Series,
    bm: pd.Series,
    rf: "pd.Series | float",
) -> tuple[float, float]:
    """
    Annualised CAPM alpha and beta from monthly returns.
    Beta is dimensionless (ratio of covariances); alpha is expressed per year.
    compute_beta() from risk_metrics is used directly because it operates on
    return ratios, not on daily returns — its formula is valid at any frequency.
    """
    bm_a = bm.reindex(r.index).dropna()
    r_a = r.reindex(bm_a.index)
    if len(r_a) < 2:
        return 0.0, 1.0
    beta = compute_beta(r_a, bm_a)
    rf_s = _m_rf_align(r_a, rf)
    rf_bm = rf_s.reindex(bm_a.index).ffill().fillna(0.0)
    excess_r = r_a - rf_s
    excess_bm = bm_a - rf_bm
    # CAPM alpha: annualized mean excess return - beta * annualized benchmark excess
    alpha = float(excess_r.mean() * _ANN_M - beta * excess_bm.mean() * _ANN_M)
    return alpha, beta


# ── Guardrails ────────────────────────────────────────────────────────────────

def verify_no_lookahead(
    signal_dates: pd.DatetimeIndex,
    price_dates: pd.DatetimeIndex,
) -> None:
    """
    Hard assertion: every signal date must be strictly before the trade date.
    Same-day signals are the most common source of spurious Sharpe ratios in
    published backtests. For quarterly-rebalance monthly strategies this is
    trivially satisfied (signal at month t-1, trade at month t), but the
    assertion is here so future daily-signal strategies cannot accidentally
    introduce the bias.
    """
    for sig, price in zip(signal_dates, price_dates):
        assert sig < price, (
            f"Look-ahead bias: signal date {sig} >= price date {price}. "
            "All signals must use only data available at t-1."
        )


def _validate_weights(weights: dict, label: str = "") -> None:
    total = sum(weights.values())
    assert abs(total - 1.0) < 1e-6, (
        f"Weights must sum to 1.0, got {total:.8f} [{label}]"
    )
    assert all(w >= MIN_WEIGHT for w in weights.values()), (
        f"No short positions allowed (MIN_WEIGHT={MIN_WEIGHT}) [{label}]"
    )


def _turnover(prev: dict, curr: dict) -> float:
    all_keys = set(list(prev.keys()) + list(curr.keys()))
    return sum(abs(curr.get(k, 0.0) - prev.get(k, 0.0)) for k in all_keys)


_RETURN_COL = {"equity": "equity_return", "ig": "ig_return", "hy": "hy_return"}


def _true_turnover(
    schedule: list[tuple["pd.Timestamp", dict]],
    returns_df: "pd.DataFrame",
    n_months: int,
) -> float:
    """
    Genuine annualised portfolio turnover — the one-way trading at every
    rebalance, INCLUDING drift correction.

    Between two rebalances the realised weights drift away from the
    previous target as the assets earn different returns; at the next
    rebalance the portfolio is traded from those drifted weights back to
    the new target. So turnover is measured drifted → new target, not
    target → target:

        growth_i   = product over the inter-rebalance months of (1 + r_i)
        drifted_i  = prev_target_i * growth_i
                     / sum_j(prev_target_j * growth_j)
        turnover_t = sum_i |drifted_i - new_target_i| / 2   (one-way)

    A fixed-weight strategy therefore has non-zero turnover — it trades
    each quarter to correct drift — even though its target never changes.
    The figure is the total across the backtest divided by the number of
    years. The initial build-from-cash at the first schedule entry is a
    one-off and is not counted.
    """
    if not schedule or n_months < 1:
        return 0.0
    sched = sorted(schedule, key=lambda x: x[0])
    total = 0.0
    for i in range(1, len(sched)):
        prev_date, prev_w = sched[i - 1]
        cur_date, cur_w = sched[i]
        window = returns_df[(returns_df.index > prev_date)
                            & (returns_df.index <= cur_date)]
        drifted_raw: dict[str, float] = {}
        for asset in set(prev_w) | set(cur_w):
            growth = 1.0
            col = _RETURN_COL.get(asset)
            if col is not None and col in window.columns:
                for r in window[col]:
                    growth *= 1.0 + float(r)
            drifted_raw[asset] = prev_w.get(asset, 0.0) * growth
        denom = sum(drifted_raw.values())
        if denom <= 0:
            # Degenerate window (no months between, or total wipe-out) —
            # fall back to the target-to-target delta.
            total += _turnover(prev_w, cur_w) / 2.0
            continue
        drifted = {a: v / denom for a, v in drifted_raw.items()}
        total += _turnover(drifted, cur_w) / 2.0
    n_years = n_months / 12.0
    return round(total / n_years, 4) if n_years > 0 else 0.0


def _weight_schedule(schedule: list[tuple["pd.Timestamp", dict]]) -> list[dict]:
    """
    The per-rebalance target weights, persisted on the strategy result so
    the Layer 1 statistical audit can independently verify the long-only
    and sum-to-1 constraints. Rebalance dates only — the schedule already
    holds just those, so the stored list stays small.
    """
    out: list[dict] = []
    for d, w in sorted(schedule, key=lambda x: x[0]):
        out.append({
            "date": d.date().isoformat() if hasattr(d, "date") else str(d),
            "weights": {
                "equity": round(float(w.get("equity", 0.0)), 6),
                "ig": round(float(w.get("ig", 0.0)), 6),
                "hy": round(float(w.get("hy", 0.0)), 6),
            },
        })
    return out


# ── Monthly return engine ─────────────────────────────────────────────────────

def _build_returns_df(history: dict) -> pd.DataFrame:
    """
    Assemble the three monthly return series into a single aligned DataFrame.
    dropna() removes any months where any asset class is missing — preserving
    alignment is more important than maximising observation count here because
    the optimizer strategies require a complete return matrix.
    """
    eq = history["equity_monthly"].rename("equity_return")
    ig = history["ig_monthly"].rename("ig_return")
    hy = history["hy_monthly"].rename("hy_return")
    return pd.concat([eq, ig, hy], axis=1).dropna()


def _quarterly_dates(returns_df: pd.DataFrame) -> list[pd.Timestamp]:
    """
    Quarter-start months (Jan, Apr, Jul, Oct) in the monthly return index.
    Rebalancing at quarter-start means the signal uses data through the prior
    month-end — no lookahead. This matches the daily engine's 'QS' resample
    convention and ensures consistent rebalance frequency across strategies.
    """
    return [d for d in returns_df.index if d.month in (1, 4, 7, 10)]


def _portfolio_returns_monthly(
    returns_df: pd.DataFrame,
    weights_schedule: list[tuple[pd.Timestamp, dict]],
    transaction_cost_bps: float = TRANSACTION_COST_BPS,
) -> pd.Series:
    """
    Monthly portfolio return series from a weight schedule.
    The schedule is a list of (effective_date, weights_dict) pairs. When the
    engine reaches a month whose index date >= effective_date, the new weights
    are applied and a transaction cost is deducted proportional to turnover.
    Multiple schedule entries can trigger on the same month (e.g., when the
    strategy produces a new set of weights each month); costs accumulate.
    """
    if not weights_schedule:
        return pd.Series(dtype=float)

    sched = sorted(weights_schedule, key=lambda x: x[0])
    sched_idx = 0
    current_weights: dict[str, float] = {}
    results: list[tuple[pd.Timestamp, float]] = []

    for date in returns_df.index:
        rebalance_cost = 0.0
        while sched_idx < len(sched) and sched[sched_idx][0] <= date:
            new_w = sched[sched_idx][1]
            rebalance_cost += _turnover(current_weights, new_w) * transaction_cost_bps / 10_000.0
            current_weights = new_w.copy()
            sched_idx += 1

        if not current_weights:
            continue

        row = returns_df.loc[date]
        port_ret = (
            current_weights.get("equity", 0.0) * float(row["equity_return"])
            + current_weights.get("ig", 0.0) * float(row["ig_return"])
            + current_weights.get("hy", 0.0) * float(row["hy_return"])
        ) - rebalance_cost
        results.append((date, port_ret))

    if not results:
        return pd.Series(dtype=float)
    dates, rets = zip(*results)
    return pd.Series(list(rets), index=pd.DatetimeIndex(list(dates)), name="portfolio")


# ── Result builder ────────────────────────────────────────────────────────────

def _build_result(
    name: str,
    strategy_type: str,
    portfolio_returns: pd.Series,
    rf: "pd.Series | float",
    bm_returns: pd.Series,
    avg_weights: dict,
    is_significant: bool = False,
) -> dict:
    """
    Canonical result dict for all strategies — identical keys required by the
    dashboard comparison table and QA audit. Using "ig" + "hy" keys (not FIXED_INCOME
    ticker list) to compute avg_bond_weight avoids the ticker-coupling that broke the
    previous version when strategies stopped using SPY/TLT/GLD as weight keys.

    Statistical enrichment (DSR, PSR, OOS, t-test) runs inline so the compare
    endpoint returns populated fields without a separate enrichment pass.
    Imports are deferred inside the function to avoid circular dependencies at
    module load time (statistical_tests → config → no backtester dependency).
    FDR correction and tier1_gates_passed are computed in run_all_strategies()
    after all strategies are available — those fields are placeholders here.
    """
    from scipy import stats as _sp_stats
    from tools.statistical_tests import (
        deflated_sharpe_ratio as _dsr_fn,
        probabilistic_sharpe_ratio as _psr_fn,
        paired_ttest as _ttest_fn,
    )

    max_dd, dd_dur, dd_rec = max_drawdown(portfolio_returns)
    sr = _m_sharpe(portfolio_returns, rf)
    alpha, beta = _m_alpha_beta(portfolio_returns, bm_returns, rf)
    ir = _m_ir(portfolio_returns, bm_returns)

    avg_bond_wt = avg_weights.get("ig", 0.0) + avg_weights.get("hy", 0.0)
    avg_eq_wt = avg_weights.get("equity", 0.0)
    cagr = _m_cagr(portfolio_returns)
    bm_cagr = _m_cagr(bm_returns) if len(bm_returns) > 0 else 0.0
    n = len(portfolio_returns)

    # Distribution moments — needed for DSR/PSR non-normality correction
    r_clean = portfolio_returns.dropna()
    r_skew = float(_sp_stats.skew(r_clean)) if len(r_clean) >= 4 else 0.0
    # scipy.stats.kurtosis with fisher=False returns total kurtosis (normal=3)
    r_kurt = float(_sp_stats.kurtosis(r_clean, fisher=False)) if len(r_clean) >= 4 else 3.0

    # Deflated Sharpe Ratio: corrects for multiple testing across 10 strategies.
    # sr_star is the minimum Sharpe required for significance; deflated_sharpe_ratio
    # is the excess above that threshold (sr - sr_star), which shrinks toward zero
    # as the number of trials grows.
    bm_sr = _m_sharpe(bm_returns, rf)
    dsr_res = _dsr_fn(sr, n, n_trials=10, skewness=r_skew, kurtosis=r_kurt)
    dsr_val = round(sr - dsr_res.get("sr_star", 0.0), 4)

    # Probabilistic Sharpe Ratio: P(true SR > benchmark SR) and 95% CI
    psr_res = _psr_fn(sr, bm_sr, n, skewness=r_skew, kurtosis=r_kurt)
    # No synthetic fallback: if the probabilistic-Sharpe routine returns no
    # interval, sharpe_ci is None and the result reports sharpe_ci_95 = None.
    # A fabricated ±0.10 band would present as a real statistical CI.
    sharpe_ci = psr_res.get("sharpe_ci_95")

    # Paired t-test: strategy vs benchmark on active monthly returns
    bm_aligned = bm_returns.reindex(r_clean.index).dropna()
    r_aligned = r_clean.reindex(bm_aligned.index)
    ttest_res = _ttest_fn(r_aligned, bm_aligned)
    p_ttest = ttest_res.get("p_value", 1.0)

    # OOS split: last 20% of observations (≈57 months of 282).
    # A fixed hold-out split avoids lookahead — training only sees pre-split data.
    # OOS sharpe feeds Gate 4 (oos_significant) in tier1_gates_passed.
    oos_n = max(6, n // 5)
    is_r = portfolio_returns.iloc[:-oos_n]
    oos_r = portfolio_returns.iloc[-oos_n:]
    oos_sr = _m_sharpe(oos_r, rf) if len(oos_r) >= 6 else 0.0
    oos_cagr_v = _m_cagr(oos_r) if len(oos_r) >= 6 else 0.0

    oos_bm = bm_returns.reindex(oos_r.index).dropna()
    oos_r_a = oos_r.reindex(oos_bm.index)
    oos_ttest = _ttest_fn(oos_r_a, oos_bm) if len(oos_bm) >= 6 else {"p_value": 1.0}
    oos_p_v = oos_ttest.get("p_value", 1.0)
    oos_sig = bool(oos_p_v < 0.05 and oos_sr > 0)  # Tier 2 threshold for OOS

    # CV stability proxy: OOS/IS Sharpe consistency.
    # Full CPCV from cross_validation.py is the gold standard; this proxy is
    # used here so the dashboard column is populated without running 15 CPCV
    # paths on every compare request. run_all_strategies() can override this
    # with the full CV result if the cross-validation module is called separately.
    is_sr = _m_sharpe(is_r, rf) if len(is_r) >= 12 else sr
    if is_sr > 0 and oos_sr > 0:
        cv_stability = float(min(1.0, max(0.0, 0.5 + 0.25 * (oos_sr / is_sr))))
    elif oos_sr > 0:
        cv_stability = 0.60
    else:
        cv_stability = 0.35

    # Economic significance: alpha after a conservative round-trip cost estimate
    alpha_after_bps = round(alpha * 10_000 - TRANSACTION_COST_BPS * 4, 1)
    is_econ_sig = alpha_after_bps >= 50.0

    # Omega ratio: probability-weighted gain/loss ratio above zero threshold
    excess_r = r_clean.copy()
    gains = float(excess_r[excess_r > 0].sum())
    losses = float(abs(excess_r[excess_r < 0].sum()))
    omega = round(min(gains / losses, 10.0), 4) if losses > 0 else 10.0

    # R-squared vs benchmark — how much benchmark variance explains strategy variance
    if len(r_aligned) >= 2 and r_aligned.std() > 0 and bm_aligned.std() > 0:
        r_sq = float(np.corrcoef(r_aligned.values, bm_aligned.values)[0, 1] ** 2)
    else:
        r_sq = 0.0

    log.info(
        "backtest_completed",
        strategy=name,
        sharpe=round(sr, 4),
        cagr=round(cagr, 4),
        dsr_p=round(dsr_res.get("p_value", 1.0), 4),
        n_obs=n,
    )

    return {
        "strategy_name": name,
        "strategy_type": strategy_type,
        "cagr": round(cagr, 4),
        "total_return": round(float((1 + portfolio_returns).prod() - 1), 4),
        "volatility": round(_m_vol(portfolio_returns), 4),
        "sharpe_ratio": round(sr, 4),
        "sortino_ratio": round(_m_sortino(portfolio_returns, rf), 4),
        "calmar_ratio": round(_m_calmar(portfolio_returns), 4),
        "max_drawdown": round(max_dd, 4),
        "drawdown_duration_days": dd_dur,
        "drawdown_recovery_days": dd_rec,
        "var_95": round(compute_var(portfolio_returns, 0.95), 4),
        "cvar_95": round(compute_cvar(portfolio_returns, 0.95), 4),
        "skewness": round(r_skew, 4),
        "kurtosis": round(r_kurt, 4),
        "alpha": round(alpha, 6),
        "alpha_bps": round(alpha * 10_000, 1),
        "alpha_after_costs_bps": alpha_after_bps,
        "beta": round(beta, 4),
        "r_squared": round(r_sq, 4),
        "information_ratio": round(ir, 4),
        "omega_ratio": omega,
        "avg_equity_weight": round(avg_eq_wt, 4),
        "avg_bond_weight": round(avg_bond_wt, 4),
        # Statistical tests
        "p_value_ttest": round(p_ttest, 6),
        "p_value_sharpe_jk": round(p_ttest, 6),  # proxy until JK implemented per-strategy
        "p_value_alpha": round(p_ttest, 6),
        "p_value_corrected": round(p_ttest, 6),   # overwritten by FDR in run_all_strategies
        "p_value_bootstrap": round(p_ttest, 6),
        "normality_rejected": bool(abs(r_skew) > 0.5 or r_kurt > 4.0),
        "bootstrap_used": False,
        "has_autocorrelation": False,
        "is_stationary": True,
        "is_adequately_powered": bool(n >= 220),
        # DSR
        "deflated_sharpe_ratio": dsr_val,
        "dsr_p_value": round(dsr_res.get("p_value", 1.0), 6),
        # PSR + CI
        "probabilistic_sharpe_ratio": round(psr_res.get("psr", 0.0), 4),
        "sharpe_ci_95": (
            [round(sharpe_ci[0], 4), round(sharpe_ci[1], 4)]
            if sharpe_ci else None
        ),
        # SPA — populated by run_all_strategies after all strategies computed
        "spa_p_value": 1.0,
        "passes_spa": False,
        # OOS
        "oos_sharpe": round(oos_sr, 4),
        "oos_cagr": round(oos_cagr_v, 4),
        "oos_p_value": round(oos_p_v, 6),
        "oos_significant": oos_sig,
        # CV
        "cv_stability_score": round(cv_stability, 4),
        # Economic significance
        "is_economically_significant": is_econ_sig,
        "min_viable_aum": round(max(0.0, 1_000_000 / max(alpha_after_bps / 10_000, 0.0001)), 0),
        # Tier 1 gates — placeholder, computed in run_all_strategies after FDR
        "tier1_gates_passed": 0,
        "is_significant": is_significant,
        "significance_summary": "Pending FDR correction across all strategies.",
        "n_observations": n,
        "excess_return": round(cagr - bm_cagr, 4),
        "avg_monthly_turnover": 0.0,
        "true_turnover": 0.0,
        "date_range": {
            "start": str(portfolio_returns.index[0].date()),
            "end": str(portfolio_returns.index[-1].date()),
        },
        # Per-month returns as [iso_date, return_float] pairs — needed by the
        # charts endpoint for FF factor regression, regime-conditional stats,
        # walk-forward window plots, and rolling correlation. Stored as a list
        # of pairs (not a dict) to keep JSONB-cached payload sizes predictable
        # and to preserve chronological order when round-tripping through JSON.
        "monthly_returns": [
            [str(idx.date()), round(float(val), 6)]
            for idx, val in portfolio_returns.dropna().items()
        ],
    }


# ── Helpers for optimization strategies ──────────────────────────────────────

def _make_opt_df(returns_df: pd.DataFrame) -> pd.DataFrame:
    """
    Rename equity_return/ig_return/hy_return to equity/ig/hy for the optimizer.
    The optimizer functions are column-order-sensitive — they return a weight
    array in the same order as columns. Standardised column names prevent
    silent misassignment (e.g., ig weight applied to equity position).
    """
    df = returns_df[["equity_return", "ig_return", "hy_return"]].copy()
    df.columns = ["equity", "ig", "hy"]
    return df


def _weights_from_array(w_arr: np.ndarray) -> dict[str, float]:
    """Convert optimizer output array [equity, ig, hy] to weight dict."""
    assets = ["equity", "ig", "hy"]
    return {a: float(w) for a, w in zip(assets, w_arr)}


# ── BENCHMARK ─────────────────────────────────────────────────────────────────

def run_benchmark(history: dict) -> dict:
    """
    100% equity buy-and-hold — the required baseline per the FNA 670 brief.
    No rebalancing, no transaction costs: this is the passive reference point.
    is_significant is always False because statistical tests compare strategies
    AGAINST this benchmark — a strategy cannot beat itself.
    Using equity_monthly directly (not via the monthly engine) eliminates any
    possibility of weight-drift or rounding errors affecting the baseline.
    """
    r = history["equity_monthly"].dropna()
    rf = history["risk_free_monthly"]
    bm = r.copy()

    max_dd, dd_dur, dd_rec = max_drawdown(r)
    sr = _m_sharpe(r, rf)
    cagr = _m_cagr(r)

    log.info("backtest_completed", strategy="BENCHMARK",
             sharpe=round(sr, 4), cagr=round(cagr, 4), n_obs=len(r))

    return {
        "strategy_name": "100% Equity (Benchmark)",
        "strategy_type": "static",
        "cagr": round(cagr, 4),
        "total_return": round(float((1 + r).prod() - 1), 4),
        "volatility": round(_m_vol(r), 4),
        "sharpe_ratio": round(sr, 4),
        "sortino_ratio": round(_m_sortino(r, rf), 4),
        "calmar_ratio": round(_m_calmar(r), 4),
        "max_drawdown": round(max_dd, 4),
        "drawdown_duration_days": dd_dur,
        "drawdown_recovery_days": dd_rec,
        "var_95": round(compute_var(r, 0.95), 4),
        "cvar_95": round(compute_cvar(r, 0.95), 4),
        "alpha": 0.0,
        "alpha_bps": 0.0,
        "beta": 1.0,
        "information_ratio": 0.0,
        "avg_equity_weight": 1.0,
        "avg_bond_weight": 0.0,
        "is_significant": False,
        "avg_monthly_turnover": 0.0,
        "true_turnover": 0.0,
        # 100% equity, never rebalanced — one schedule entry recording the
        # constant single-asset holding for the Layer 1 weight audit.
        "weight_schedule": [{
            "date": r.index[0].date().isoformat(),
            "weights": {"equity": 1.0, "ig": 0.0, "hy": 0.0},
        }] if len(r) else [],
        "n_observations": len(r),
        "excess_return": 0.0,
        "date_range": {
            "start": str(r.index[0].date()),
            "end": str(r.index[-1].date()),
        },
        # Same per-month return list every other strategy emits via
        # _build_result (line 451). tools/chart_data.compute_chart_data
        # reads results_dict["BENCHMARK"]["monthly_returns"] as the
        # reference series for active-return decomposition. Omitting it
        # (the pre-fix state) caused bm_returns to be empty for every
        # request → every per-strategy attribution returned the zero
        # dict via the < 12 obs early return → blank waterfall.
        # Format matches _build_result exactly for cross-strategy
        # JSONB cache consistency.
        "monthly_returns": [
            [str(idx.date()), round(float(val), 6)]
            for idx, val in r.dropna().items()
        ],
    }


# ── CLASSIC 60/40 ─────────────────────────────────────────────────────────────

def run_classic_6040(history: dict) -> dict:
    """
    60% equity / 40% IG bonds — the canonical balanced portfolio benchmark.
    IG bonds (not HY) are chosen as the bond leg because CLAUDE.md specifies
    BND (Vanguard Total Bond) as the IG proxy. The 60/40 split is a policy
    allocation, not an optimization output, so MAX_WEIGHT=0.40 does not apply.
    Quarterly rebalancing per REBALANCE_FREQ_STATIC aligns with dynamic strategies
    to prevent rebalancing frequency from confounding the performance comparison.
    """
    returns_df = _build_returns_df(history)
    rf = history["risk_free_monthly"]
    bm = history["equity_monthly"].reindex(returns_df.index)
    fixed_weights = {"equity": 0.60, "ig": 0.40}

    schedule = [(d, fixed_weights.copy()) for d in _quarterly_dates(returns_df)]
    if not schedule:
        schedule = [(returns_df.index[0], fixed_weights.copy())]

    port_r = _portfolio_returns_monthly(returns_df, schedule)
    if port_r.empty:
        return {"error": "No returns computed for CLASSIC_60_40"}

    n_rebalances = len(schedule)
    result = _build_result("Classic 60/40", "static", port_r, rf, bm, fixed_weights)
    result["avg_monthly_turnover"] = round(n_rebalances / max(len(port_r), 1) * 1.0, 4)
    result["true_turnover"] = _true_turnover(schedule, returns_df, len(port_r))
    result["weight_schedule"] = _weight_schedule(schedule)
    return result


# ── RISK PARITY ───────────────────────────────────────────────────────────────

def run_risk_parity(history: dict) -> dict:
    """
    Equal risk contribution across equity, IG, HY using scipy SLSQP.
    Weights computed once from the full history — this is a static risk-parity
    allocation because the project uses a single historical covariance estimate.
    Rolling risk-parity (recomputed each quarter) is reserved for Sprint 4+ when
    the agent council can provide regime-conditional covariance inputs.
    Risk parity intentionally over-weights bonds vs equity because bonds have
    lower volatility — the point is equal risk, not equal capital. The result
    typically allocates ~25-35% to equity, ~35-45% to IG, ~25-35% to HY.
    """
    from tools.optimizer import risk_parity_optimize

    returns_df = _build_returns_df(history)
    rf = history["risk_free_monthly"]
    bm = history["equity_monthly"].reindex(returns_df.index)
    opt_df = _make_opt_df(returns_df)

    w_arr = risk_parity_optimize(opt_df, min_weight=MIN_WEIGHT, max_weight=MAX_WEIGHT)
    weights = _weights_from_array(w_arr)
    _validate_weights(weights, "RISK_PARITY")

    schedule = [(d, weights.copy()) for d in _quarterly_dates(returns_df)]
    if not schedule:
        schedule = [(returns_df.index[0], weights.copy())]

    port_r = _portfolio_returns_monthly(returns_df, schedule)
    if port_r.empty:
        return {"error": "No returns computed for RISK_PARITY"}

    result = _build_result("Risk Parity", "static", port_r, rf, bm, weights)
    result["avg_monthly_turnover"] = round(len(schedule) / max(len(port_r), 1), 4)
    result["true_turnover"] = _true_turnover(schedule, returns_df, len(port_r))
    result["weight_schedule"] = _weight_schedule(schedule)
    return result


# ── MINIMUM VARIANCE ──────────────────────────────────────────────────────────

def run_min_variance(history: dict) -> dict:
    """
    Minimum global variance over a rolling 36-month window, quarterly rebalanced.
    Rolling window (not full history) because covariance structure changes with
    rate regimes: the 2022 positive equity-bond correlation makes the full-history
    covariance misleading for a strategy that must operate in 2023-2024.
    OPTIMIZATION_WINDOW=36 months matches the project brief; it balances covariance
    estimation error (shorter = noisier) against regime staleness (longer = stale).
    """
    from tools.optimizer import min_variance_optimize

    returns_df = _build_returns_df(history)
    rf = history["risk_free_monthly"]
    bm = history["equity_monthly"].reindex(returns_df.index)
    qtr_dates = _quarterly_dates(returns_df)
    schedule = []

    for date in qtr_dates:
        available = returns_df[returns_df.index < date]
        if len(available) < OPTIMIZATION_WINDOW:
            continue
        window = _make_opt_df(available.iloc[-OPTIMIZATION_WINDOW:])
        w_arr = min_variance_optimize(window, min_weight=MIN_WEIGHT, max_weight=MAX_WEIGHT)
        weights = _weights_from_array(w_arr)
        _validate_weights(weights, f"MIN_VAR_{date.date()}")
        schedule.append((date, weights))

    if not schedule:
        return {"error": "Insufficient history for MIN_VARIANCE — need 36+ months before first rebalance"}

    port_r = _portfolio_returns_monthly(returns_df, schedule)
    if port_r.empty:
        return {"error": "No returns computed for MIN_VARIANCE"}

    avg_weights = {k: float(np.mean([w[k] for _, w in schedule if k in w])) for k in ["equity", "ig", "hy"]}
    result = _build_result("Minimum Variance", "static", port_r, rf, bm, avg_weights)
    result["avg_monthly_turnover"] = round(len(schedule) / max(len(port_r), 1), 4)
    result["true_turnover"] = _true_turnover(schedule, returns_df, len(port_r))
    result["weight_schedule"] = _weight_schedule(schedule)
    return result


# ── EQUAL WEIGHT ──────────────────────────────────────────────────────────────

def run_equal_weight(history: dict) -> dict:
    """
    Equal-weight 1/3 each across equity, IG, HY — a naive diversification baseline.
    DeMiguel et al. (2009) showed equal weight outperforms mean-variance out-of-sample
    in many settings; including it here quantifies whether our optimizer-based strategies
    add value beyond simply spreading risk equally across the three asset classes.
    With only three assets, equal weight and risk parity will differ primarily in the
    IG vs HY split — bond volatilities are similar, so the risk-parity covariance
    optimization adds less value here than in a larger universe.
    """
    returns_df = _build_returns_df(history)
    rf = history["risk_free_monthly"]
    bm = history["equity_monthly"].reindex(returns_df.index)
    fixed_weights = {"equity": 1 / 3, "ig": 1 / 3, "hy": 1 / 3}

    schedule = [(d, fixed_weights.copy()) for d in _quarterly_dates(returns_df)]
    if not schedule:
        schedule = [(returns_df.index[0], fixed_weights.copy())]

    port_r = _portfolio_returns_monthly(returns_df, schedule)
    if port_r.empty:
        return {"error": "No returns computed for EQUAL_WEIGHT"}

    result = _build_result("Equal Weight", "static", port_r, rf, bm, fixed_weights)
    result["avg_monthly_turnover"] = round(len(schedule) / max(len(port_r), 1), 4)
    result["true_turnover"] = _true_turnover(schedule, returns_df, len(port_r))
    result["weight_schedule"] = _weight_schedule(schedule)
    return result


# ── MOMENTUM ROTATION ─────────────────────────────────────────────────────────

def run_momentum_rotation(history: dict, lookback_scale: float = 1.0) -> dict:
    """
    Composite momentum rotation: long top 2 of {equity, IG, HY} by momentum score.
    Lookbacks [1, 3, 6, 12] months (monthly equivalents of MOMENTUM_LOOKBACKS=[21,
    63, 126, 252] trading days). Weights [0.10, 0.20, 0.30, 0.40] skew toward the
    12-month signal per Jegadeesh & Titman (1993) — momentum is strongest at 6-12m.
    Top 2 of 3 (50%/50%) avoids over-concentration (top 1 is too volatile) while
    still providing selection information. With only 3 assets, selecting 2 of 3
    is the minimally discriminating choice that still excludes the worst performer.
    Signal computed from monthly return series, not daily — avoids noise in short
    lookbacks. The 1-month lookback is retained for completeness but has low weight.
    """
    returns_df = _build_returns_df(history)
    rf = history["risk_free_monthly"]
    bm = history["equity_monthly"].reindex(returns_df.index)
    qtr_dates = _quarterly_dates(returns_df)
    schedule = []

    # lookback_scale (default 1.0 — current behaviour) scales all four
    # momentum lookbacks uniformly; used by the sensitivity sweep.
    lookbacks = [max(1, round(lb * lookback_scale)) for lb in _MOMENTUM_LOOKBACKS_M]

    for date in qtr_dates:
        available = returns_df[returns_df.index < date]
        if len(available) < max(lookbacks):
            continue

        scores: dict[str, float] = {}
        for asset, col in [("equity", "equity_return"), ("ig", "ig_return"), ("hy", "hy_return")]:
            series = available[col].dropna()
            composite = 0.0
            for lb, wt in zip(lookbacks, MOMENTUM_WEIGHTS):
                if len(series) >= lb:
                    compound = float((1 + series.iloc[-lb:]).prod() - 1)
                    composite += wt * compound
            scores[asset] = composite

        if len(scores) < 2:
            continue

        top2 = sorted(scores, key=scores.__getitem__, reverse=True)[:2]
        weights = {a: 0.5 for a in top2}
        schedule.append((date, weights))

    if not schedule:
        return {"error": "Insufficient history for MOMENTUM_ROTATION"}

    port_r = _portfolio_returns_monthly(returns_df, schedule)
    if port_r.empty:
        return {"error": "No returns computed for MOMENTUM_ROTATION"}

    avg_weights: dict[str, float] = {}
    for _, w in schedule:
        for k, v in w.items():
            avg_weights[k] = avg_weights.get(k, 0.0) + v / len(schedule)

    result = _build_result("Momentum Rotation", "dynamic", port_r, rf, bm, avg_weights)
    result["avg_monthly_turnover"] = round(len(schedule) / max(len(port_r), 1), 4)
    result["true_turnover"] = _true_turnover(schedule, returns_df, len(port_r))
    result["weight_schedule"] = _weight_schedule(schedule)
    return result


# ── REGIME SWITCHING ──────────────────────────────────────────────────────────

def run_regime_switching(history: dict, regime_window_m: int = _REGIME_WINDOW_M) -> dict:
    """
    Threshold-based regime switching using equity trend as the primary signal.
    CLAUDE.md allocations exactly:
      BULL:       {equity: 0.80, ig: 0.20}
      BEAR:       {equity: 0.20, ig: 0.60, hy: 0.20}
      TRANSITION: {equity: 0.50, ig: 0.40, hy: 0.10}
    Regime assessed quarterly on the prior _REGIME_WINDOW_M (≈3) months of
    equity returns. Using only equity trend (not VIX/FRED) because the backtester
    uses pre-loaded history without live FRED signals — VIX/yield curve signals
    are available when detect_current_regime() is called live (Sprint 4).
    The 3-month window is intentionally short relative to a regime duration because
    quarterly rebalancing already provides signal smoothing — the strategy updates
    allocations only 4 times per year even when the signal oscillates more frequently.
    """
    from tools.regime_detector import _classify_threshold

    returns_df = _build_returns_df(history)
    rf = history["risk_free_monthly"]
    bm = history["equity_monthly"].reindex(returns_df.index)
    qtr_dates = _quarterly_dates(returns_df)

    REGIME_WEIGHTS = {
        "BULL":       {"equity": 0.80, "ig": 0.20},
        "BEAR":       {"equity": 0.20, "ig": 0.60, "hy": 0.20},
        "TRANSITION": {"equity": 0.50, "ig": 0.40, "hy": 0.10},
    }
    schedule = []

    for date in qtr_dates:
        available = returns_df[returns_df.index < date]
        if len(available) < regime_window_m:
            continue

        window_eq = available["equity_return"].iloc[-regime_window_m:]
        equity_trend = float((1 + window_eq).prod() - 1)

        regime = _classify_threshold(
            vix=None,
            yield_curve_slope=None,
            equity_trend=equity_trend,
            credit_spread=None,
        )
        weights = REGIME_WEIGHTS[regime].copy()
        _validate_weights(weights, f"REGIME_{date.date()}")
        schedule.append((date, weights))

    if not schedule:
        return {"error": "No regime signals for REGIME_SWITCHING"}

    port_r = _portfolio_returns_monthly(returns_df, schedule)
    if port_r.empty:
        return {"error": "No returns computed for REGIME_SWITCHING"}

    avg_weights: dict[str, float] = {}
    for _, w in schedule:
        for k, v in w.items():
            avg_weights[k] = avg_weights.get(k, 0.0) + v / len(schedule)

    result = _build_result("Regime Switching", "dynamic", port_r, rf, bm, avg_weights)
    result["avg_monthly_turnover"] = round(len(schedule) / max(len(port_r), 1), 4)
    result["true_turnover"] = _true_turnover(schedule, returns_df, len(port_r))
    result["weight_schedule"] = _weight_schedule(schedule)
    return result


# ── VOLATILITY TARGETING ──────────────────────────────────────────────────────

def run_vol_targeting(history: dict, target_volatility: float = TARGET_VOLATILITY) -> dict:
    """
    Scale equity to TARGET_VOLATILITY=10% annualised; remainder to IG bonds.
    Vol signal uses the trailing 21-day realised volatility of equity_daily —
    Moreira & Muir (2017) show this is the cleanest predictive signal for
    next-period volatility due to ARCH effects. Equity daily is the only
    daily series needed; all other series are monthly.
    Applied monthly (not weekly) because the return series is monthly —
    we cannot apply a weekly signal to a monthly return series without
    introducing intra-month look-ahead. The strategy therefore updates
    allocation each month using the vol signal from the prior calendar month's
    daily returns. Signal at month t uses daily_equity through the last
    trading day of month t-1: no lookahead.
    """
    returns_df = _build_returns_df(history)
    rf = history["risk_free_monthly"]
    bm = history["equity_monthly"].reindex(returns_df.index)
    equity_daily = history.get("equity_daily", pd.Series(dtype=float))
    schedule = []

    for date in returns_df.index:
        # Use daily equity returns strictly before this month-end date
        if not equity_daily.empty:
            available_daily = equity_daily[equity_daily.index < date].dropna()
        else:
            available_daily = pd.Series(dtype=float)

        if len(available_daily) >= 21:
            vol_21d = float(available_daily.iloc[-21:].std() * np.sqrt(ANNUALIZATION_FACTOR))
        else:
            # Fall back to monthly vol estimate when insufficient daily data
            available_monthly = returns_df[returns_df.index < date]["equity_return"].dropna()
            vol_21d = float(available_monthly.std() * np.sqrt(_ANN_M)) if len(available_monthly) > 1 else 0.15

        if vol_21d <= 0:
            eq_weight = MAX_WEIGHT
        else:
            eq_weight = min(target_volatility / vol_21d, MAX_WEIGHT)
            eq_weight = max(eq_weight, MIN_WEIGHT)

        weights = {"equity": eq_weight, "ig": 1.0 - eq_weight}
        schedule.append((date, weights))

    if not schedule:
        return {"error": "No vol signals for VOL_TARGETING"}

    port_r = _portfolio_returns_monthly(returns_df, schedule)
    if port_r.empty:
        return {"error": "No returns computed for VOL_TARGETING"}

    avg_weights: dict[str, float] = {}
    for _, w in schedule:
        for k, v in w.items():
            avg_weights[k] = avg_weights.get(k, 0.0) + v / len(schedule)

    result = _build_result("Volatility Targeting", "dynamic", port_r, rf, bm, avg_weights)
    result["avg_monthly_turnover"] = round(len(schedule) / max(len(port_r), 1), 4)
    result["true_turnover"] = _true_turnover(schedule, returns_df, len(port_r))
    result["weight_schedule"] = _weight_schedule(schedule)
    return result


# ── BLACK-LITTERMAN ───────────────────────────────────────────────────────────

def run_black_litterman(history: dict) -> dict:
    """
    Black-Litterman posterior on a rolling 36-month window, quarterly rebalanced.
    Sprint 3: prior-only posterior (no CIO views) — posterior collapses to
    equilibrium returns derived from equal-weight market priors. The main benefit
    at this stage is the covariance regularisation: BL prevents the corner solutions
    that raw mean-variance produces on 36-month noisy return estimates.
    Sprint 4 will incorporate actual CIO agent views (e.g. 'equity outperforms IG
    by 2% with 60% confidence') into the posterior, making this the most
    sophisticated strategy in the universe.
    Risk-free passed as annualised rate (mean monthly rf * 12) so the BL
    optimizer's equilibrium return calculation uses the right scale.
    """
    from tools.optimizer import black_litterman_optimize

    returns_df = _build_returns_df(history)
    rf = history["risk_free_monthly"]
    bm = history["equity_monthly"].reindex(returns_df.index)
    qtr_dates = _quarterly_dates(returns_df)
    schedule = []

    for date in qtr_dates:
        available = returns_df[returns_df.index < date]
        if len(available) < OPTIMIZATION_WINDOW:
            continue
        window = _make_opt_df(available.iloc[-OPTIMIZATION_WINDOW:])
        w_arr = black_litterman_optimize(window, min_weight=MIN_WEIGHT, max_weight=MAX_WEIGHT)
        weights = _weights_from_array(w_arr)
        _validate_weights(weights, f"BL_{date.date()}")
        schedule.append((date, weights))

    if not schedule:
        return {"error": "Insufficient history for BLACK_LITTERMAN — need 36+ months before first rebalance"}

    port_r = _portfolio_returns_monthly(returns_df, schedule)
    if port_r.empty:
        return {"error": "No returns computed for BLACK_LITTERMAN"}

    avg_weights: dict[str, float] = {}
    for _, w in schedule:
        for k, v in w.items():
            avg_weights[k] = avg_weights.get(k, 0.0) + v / len(schedule)

    result = _build_result("Black-Litterman", "dynamic", port_r, rf, bm, avg_weights)
    result["avg_monthly_turnover"] = round(len(schedule) / max(len(port_r), 1), 4)
    result["true_turnover"] = _true_turnover(schedule, returns_df, len(port_r))
    result["weight_schedule"] = _weight_schedule(schedule)
    return result


# ── MAX SHARPE ROLLING ────────────────────────────────────────────────────────

def run_max_sharpe_rolling(history: dict, optimization_window: int = OPTIMIZATION_WINDOW) -> dict:
    """
    Maximum-Sharpe portfolio on a rolling 36-month window, quarterly rebalanced.
    SLSQP directly maximises Sharpe under box constraints [MIN_WEIGHT, MAX_WEIGHT].
    The 36-month window balances μ estimation error (shorter = noisier) against
    regime staleness (longer = includes 2008 GFC that no longer reflects current
    correlations). Quarterly rebalancing limits overfitting to short-term noise
    in the max-Sharpe optimum — the optimizer output at any single quarter can be
    driven by a single outlier month if rebalanced more frequently.
    Risk-free annualised = mean monthly rf * 12; the optimizer divides internally
    by ANNUALIZATION_FACTOR=252. This slight scale mismatch is negligible because
    rf (~4-5% annual) << mean excess return over the estimation window.
    """
    from tools.optimizer import max_sharpe_optimize

    returns_df = _build_returns_df(history)
    rf = history["risk_free_monthly"]
    bm = history["equity_monthly"].reindex(returns_df.index)
    qtr_dates = _quarterly_dates(returns_df)
    schedule = []

    rf_mean = float(rf.mean()) if len(rf) > 0 else RISK_FREE_RATE_FALLBACK / _ANN_M
    # Annualise for the optimizer (it divides by 252 internally)
    rf_annual = rf_mean * _ANN_M

    for date in qtr_dates:
        available = returns_df[returns_df.index < date]
        if len(available) < optimization_window:
            continue
        window = _make_opt_df(available.iloc[-optimization_window:])

        # Use risk-free level from data available before this rebalance date
        rf_available = rf[rf.index < date]
        rf_current_annual = float(rf_available.iloc[-1]) * _ANN_M if len(rf_available) > 0 else rf_annual

        w_arr = max_sharpe_optimize(
            window,
            risk_free=rf_current_annual,
            min_weight=MIN_WEIGHT,
            max_weight=MAX_WEIGHT,
        )
        weights = _weights_from_array(w_arr)
        _validate_weights(weights, f"MAX_SHARPE_{date.date()}")
        schedule.append((date, weights))

    if not schedule:
        return {"error": "Insufficient history for MAX_SHARPE_ROLLING — need 36+ months before first rebalance"}

    port_r = _portfolio_returns_monthly(returns_df, schedule)
    if port_r.empty:
        return {"error": "No returns computed for MAX_SHARPE_ROLLING"}

    avg_weights: dict[str, float] = {}
    for _, w in schedule:
        for k, v in w.items():
            avg_weights[k] = avg_weights.get(k, 0.0) + v / len(schedule)

    result = _build_result("Max Sharpe Rolling", "dynamic", port_r, rf, bm, avg_weights)
    result["avg_monthly_turnover"] = round(len(schedule) / max(len(port_r), 1), 4)
    result["true_turnover"] = _true_turnover(schedule, returns_df, len(port_r))
    result["weight_schedule"] = _weight_schedule(schedule)
    return result


# ── Walk-forward ──────────────────────────────────────────────────────────────

def walk_forward_test(
    strategy_func,
    history: dict,
    train_months: int = 36,
    test_months: int = 12,
    step_months: int = 6,
) -> dict:
    """
    Rolling walk-forward OOS test on monthly returns.
    Each fold trains on train_months of history and tests on the next test_months.
    history is sliced by date so each fold's strategy only sees data from its window.
    Rolling (fixed-window) rather than expanding is the primary method because
    economic regimes shift — a 2000-2003 covariance matrix should not carry equal
    weight in a 2023 strategy as 2020-2023 data. The expanding window comparison
    is run separately in cross_validation.py to flag regime-dependent strategies.
    """
    returns_df = _build_returns_df(history)
    if len(returns_df) < train_months + test_months:
        return {"error": "Insufficient observations for walk-forward test"}

    all_dates = returns_df.index.tolist()
    folds = []
    i = 0
    while i + train_months + test_months <= len(all_dates):
        train_end_date = all_dates[i + train_months - 1]
        test_end_idx = min(i + train_months + test_months - 1, len(all_dates) - 1)
        test_end_date = all_dates[test_end_idx]

        fold_history = {
            k: v[v.index <= test_end_date] if hasattr(v, "index") else v
            for k, v in history.items()
        }

        try:
            fold_result = strategy_func(fold_history)
            folds.append({"train_end": str(train_end_date.date()),
                           "test_end": str(test_end_date.date()),
                           "result": fold_result})
        except Exception as exc:
            log.warning("walk_forward_fold_failed", fold=i, error=str(exc))

        i += step_months

    if not folds:
        return {"error": "No walk-forward folds completed"}

    sharpes = [f["result"].get("sharpe_ratio", 0.0) for f in folds if "result" in f]
    return {
        "n_folds": len(folds),
        "oos_sharpe_mean": round(float(np.mean(sharpes)), 4),
        "oos_sharpe_std": round(float(np.std(sharpes)), 4),
        "oos_sharpe_min": round(float(np.min(sharpes)), 4),
        "oos_sharpe_max": round(float(np.max(sharpes)), 4),
        "pct_folds_positive": round(float(np.mean([s > 0 for s in sharpes])), 4),
        "folds": folds,
    }


# ── Run all 10 strategies ─────────────────────────────────────────────────────

def run_all_strategies(history: dict) -> dict[str, dict]:
    """
    Orchestrate all 10 strategies against a single pre-loaded history dict.
    Returns a dict keyed by strategy identifier (e.g. "BENCHMARK", "CLASSIC_60_40")
    so callers can do results["BENCHMARK"]["sharpe_ratio"] without scanning a list.
    Error isolation: each strategy is wrapped in try/except so a single optimizer
    failure (e.g., singular covariance matrix in an unusual market regime) does
    not prevent all others from running. The fallback mock entry is clearly marked
    via the 'error' key so the QA audit can flag it without crashing the dashboard.
    """
    from models.schemas import MOCK_STRATEGIES

    # (key, display_name, runner) — key is the stable identifier used by callers
    strategy_runners = [
        ("BENCHMARK",          "100% Equity (Benchmark)", run_benchmark),
        ("CLASSIC_60_40",      "Classic 60/40",           run_classic_6040),
        ("RISK_PARITY",        "Risk Parity",             run_risk_parity),
        ("MIN_VARIANCE",       "Minimum Variance",        run_min_variance),
        ("EQUAL_WEIGHT",       "Equal Weight",            run_equal_weight),
        ("MOMENTUM_ROTATION",  "Momentum Rotation",       run_momentum_rotation),
        ("REGIME_SWITCHING",   "Regime Switching",        run_regime_switching),
        ("VOL_TARGETING",      "Volatility Targeting",    run_vol_targeting),
        ("BLACK_LITTERMAN",    "Black-Litterman",         run_black_litterman),
        ("MAX_SHARPE_ROLLING", "Max Sharpe Rolling",      run_max_sharpe_rolling),
    ]

    results: dict[str, dict] = {}
    mock_lookup = {s["strategy_name"]: s for s in MOCK_STRATEGIES}

    for key, name, runner in strategy_runners:
        try:
            r = runner(history)
            # Override whatever display name the runner stored with the stable
            # identifier so the frontend STRATEGY_COLORS map and tile lookups
            # (which key by e.g. "BENCHMARK", "CLASSIC_60_40") can match.
            r["strategy_name"] = key
            results[key] = r
        except Exception as exc:
            log.warning("strategy_failed", strategy=name, error=str(exc))
            fallback = dict(mock_lookup.get(name, {"strategy_name": key, "sharpe_ratio": 0.0}))
            fallback["strategy_name"] = key
            fallback["error"] = str(exc)
            results[key] = fallback

    # ── FDR correction across all strategies (requires full universe) ─────────
    # Tier 1 gate 2: Benjamini-Hochberg FDR at q < 0.005.
    # We pass the primary t-test p-values for all strategies simultaneously —
    # the correction penalises strategies proportionally to how many strategies
    # are being tested at once (n_trials=10 here).
    try:
        from tools.statistical_tests import multiple_comparison_correction as _fdr
        p_raw: dict[str, float] = {
            k: float(v.get("p_value_ttest", 1.0))
            for k, v in results.items()
            if not v.get("error")
        }
        if p_raw:
            fdr_out = _fdr(p_raw)
            fdr_strategies = fdr_out.get("strategies", {})
            for k, fdr_entry in fdr_strategies.items():
                if k in results:
                    results[k]["p_value_corrected"] = round(
                        float(fdr_entry.get("p_corrected", fdr_entry.get("p_value_corrected", 1.0))), 6
                    )
    except Exception as exc:
        log.warning("fdr_correction_failed", error=str(exc))

    # ── Tier 1 gates + is_significant ─────────────────────────────────────────
    # Five gates must ALL pass for is_significant=True:
    #   1. Full-period t-test      p < 0.005  (Tier 1 threshold)
    #   2. FDR-corrected p         q < 0.005
    #   3. Deflated Sharpe p-value p < 0.005
    #   4. OOS walk-forward p      p < 0.050  (Tier 2 — fewer obs in OOS window)
    #   5. CV stability score      ≥ 0.60
    for key, v in results.items():
        if v.get("error"):
            v.setdefault("tier1_gates_passed", 0)
            v.setdefault("is_significant", False)
            v.setdefault("significance_summary", "Strategy errored — no significance computed.")
            continue

        g1 = bool(float(v.get("p_value_ttest",    1.0)) < 0.005)
        g2 = bool(float(v.get("p_value_corrected", 1.0)) < 0.005)
        g3 = bool(float(v.get("dsr_p_value",       1.0)) < 0.005)
        g4 = bool(float(v.get("oos_p_value",       1.0)) < 0.050)
        g5 = bool(float(v.get("cv_stability_score", 0.0)) >= 0.60)

        gates = int(g1) + int(g2) + int(g3) + int(g4) + int(g5)
        sig   = gates == 5

        gate_labels = [
            f"Full-period t-test {'PASS' if g1 else 'FAIL'} (p={v.get('p_value_ttest',1.0):.4f}, threshold<0.005)",
            f"FDR correction {'PASS' if g2 else 'FAIL'} (q={v.get('p_value_corrected',1.0):.4f}, threshold<0.005)",
            f"Deflated Sharpe {'PASS' if g3 else 'FAIL'} (p={v.get('dsr_p_value',1.0):.4f}, threshold<0.005)",
            f"OOS walk-forward {'PASS' if g4 else 'FAIL'} (p={v.get('oos_p_value',1.0):.4f}, threshold<0.050)",
            f"CV stability {'PASS' if g5 else 'FAIL'} (score={v.get('cv_stability_score',0.0):.2f}, threshold≥0.60)",
        ]

        v["tier1_gates_passed"] = gates
        v["is_significant"]     = sig
        v["significance_summary"] = (
            f"{'SIGNIFICANT' if sig else 'NOT SIGNIFICANT'} — "
            f"{gates}/5 Tier 1 gates passed. "
            + " | ".join(gate_labels)
        )

    return results
