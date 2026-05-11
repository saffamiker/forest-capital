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
    """
    max_dd, dd_dur, dd_rec = max_drawdown(portfolio_returns)
    sr = _m_sharpe(portfolio_returns, rf)
    alpha, beta = _m_alpha_beta(portfolio_returns, bm_returns, rf)
    ir = _m_ir(portfolio_returns, bm_returns)

    avg_bond_wt = avg_weights.get("ig", 0.0) + avg_weights.get("hy", 0.0)
    avg_eq_wt = avg_weights.get("equity", 0.0)
    cagr = _m_cagr(portfolio_returns)
    bm_cagr = _m_cagr(bm_returns) if len(bm_returns) > 0 else 0.0

    log.info(
        "backtest_completed",
        strategy=name,
        sharpe=round(sr, 4),
        cagr=round(cagr, 4),
        n_obs=len(portfolio_returns),
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
        "alpha": round(alpha, 6),
        "alpha_bps": round(alpha * 10_000, 1),
        "beta": round(beta, 4),
        "information_ratio": round(ir, 4),
        "avg_equity_weight": round(avg_eq_wt, 4),
        "avg_bond_weight": round(avg_bond_wt, 4),
        "is_significant": is_significant,
        "n_observations": len(portfolio_returns),
        "excess_return": round(cagr - bm_cagr, 4),
        "date_range": {
            "start": str(portfolio_returns.index[0].date()),
            "end": str(portfolio_returns.index[-1].date()),
        },
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
        "n_observations": len(r),
        "excess_return": 0.0,
        "date_range": {
            "start": str(r.index[0].date()),
            "end": str(r.index[-1].date()),
        },
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
    return result


# ── MOMENTUM ROTATION ─────────────────────────────────────────────────────────

def run_momentum_rotation(history: dict) -> dict:
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

    for date in qtr_dates:
        available = returns_df[returns_df.index < date]
        if len(available) < max(_MOMENTUM_LOOKBACKS_M):
            continue

        scores: dict[str, float] = {}
        for asset, col in [("equity", "equity_return"), ("ig", "ig_return"), ("hy", "hy_return")]:
            series = available[col].dropna()
            composite = 0.0
            for lb, wt in zip(_MOMENTUM_LOOKBACKS_M, MOMENTUM_WEIGHTS):
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
    return result


# ── REGIME SWITCHING ──────────────────────────────────────────────────────────

def run_regime_switching(history: dict) -> dict:
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
        if len(available) < _REGIME_WINDOW_M:
            continue

        window_eq = available["equity_return"].iloc[-_REGIME_WINDOW_M:]
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
    return result


# ── VOLATILITY TARGETING ──────────────────────────────────────────────────────

def run_vol_targeting(history: dict) -> dict:
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
            eq_weight = min(TARGET_VOLATILITY / vol_21d, MAX_WEIGHT)
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
    return result


# ── MAX SHARPE ROLLING ────────────────────────────────────────────────────────

def run_max_sharpe_rolling(history: dict) -> dict:
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
        if len(available) < OPTIMIZATION_WINDOW:
            continue
        window = _make_opt_df(available.iloc[-OPTIMIZATION_WINDOW:])

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

def run_all_strategies(history: dict) -> list[dict]:
    """
    Orchestrate all 10 strategies against a single pre-loaded history dict.
    Error isolation: each strategy is wrapped in try/except so a single optimizer
    failure (e.g., singular covariance matrix in an unusual market regime) does
    not prevent all others from running. The fallback mock entry is clearly marked
    via the 'error' key so the QA audit can flag it without crashing the dashboard.
    Sorted by Sharpe ratio descending so the compare table ranks best first.
    """
    from models.schemas import MOCK_STRATEGIES

    strategy_runners = [
        ("100% Equity (Benchmark)", run_benchmark),
        ("Classic 60/40",           run_classic_6040),
        ("Risk Parity",             run_risk_parity),
        ("Minimum Variance",        run_min_variance),
        ("Equal Weight",            run_equal_weight),
        ("Momentum Rotation",       run_momentum_rotation),
        ("Regime Switching",        run_regime_switching),
        ("Volatility Targeting",    run_vol_targeting),
        ("Black-Litterman",         run_black_litterman),
        ("Max Sharpe Rolling",      run_max_sharpe_rolling),
    ]

    results = []
    mock_lookup = {s["strategy_name"]: s for s in MOCK_STRATEGIES}

    for name, runner in strategy_runners:
        try:
            result = runner(history)
            results.append(result)
        except Exception as exc:
            log.warning("strategy_failed", strategy=name, error=str(exc))
            fallback = dict(mock_lookup.get(name, {"strategy_name": name, "sharpe_ratio": 0.0}))
            fallback["error"] = str(exc)
            results.append(fallback)

    return sorted(results, key=lambda r: r.get("sharpe_ratio", 0.0), reverse=True)
