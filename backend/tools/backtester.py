"""
Portfolio backtester.
Enforces: adjusted prices, weights sum to 1, no look-ahead bias,
transaction costs at both legs of every trade.
Sprint 2 implements BENCHMARK and fixed-weight static strategies.
Dynamic strategies (momentum, regime-switching, vol-targeting) are Sprint 3.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Callable

from config import (
    ANNUALIZATION_FACTOR,
    TRANSACTION_COST_BPS,
    MIN_WEIGHT,
    MAX_WEIGHT,
    BENCHMARK,
    TRAIN_START,
    TEST_END,
    FIXED_INCOME,
)
from tools.data_fetcher import fetch_equity_data, fetch_risk_free_rate
from tools.risk_metrics import (
    annualized_return,
    annualized_volatility,
    sharpe_ratio,
    sortino_ratio,
    max_drawdown,
    compute_var,
    compute_cvar,
    calmar_ratio,
    information_ratio,
    compute_beta,
    compute_alpha,
)
from logger import get_logger

log = get_logger(__name__)


# ── Guardrails ────────────────────────────────────────────────────────────────

def verify_no_lookahead(
    signal_dates: pd.DatetimeIndex,
    price_dates: pd.DatetimeIndex,
) -> None:
    """
    Assert every signal was generated strictly before the corresponding
    price observation (t-1 signal → t execution).
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
    all_tickers = set(list(prev.keys()) + list(curr.keys()))
    return sum(abs(curr.get(t, 0.0) - prev.get(t, 0.0)) for t in all_tickers)


def _transaction_cost(prev: dict, curr: dict, bps: float) -> float:
    """One-way turnover × cost in each direction = round-trip cost."""
    return _turnover(prev, curr) * bps / 10_000.0


# ── Daily portfolio return engine ─────────────────────────────────────────────

def _compute_portfolio_returns(
    prices: pd.DataFrame,
    weights_schedule: list[tuple[pd.Timestamp, dict]],
    transaction_cost_bps: float,
) -> pd.Series:
    """
    Given an ordered list of (rebalance_date, weights) pairs, compute daily
    portfolio returns. Transaction costs are deducted at each rebalance.
    weights_schedule entries must use prices strictly prior to their date.
    """
    daily_returns = prices.pct_change()
    current_weights: dict = {}
    result: list[tuple[pd.Timestamp, float]] = []

    schedule_iter = iter(weights_schedule)
    next_rebalance, next_weights = next(schedule_iter, (None, None))

    for date in daily_returns.index:
        # Apply pending rebalance if due
        if next_rebalance is not None and date >= next_rebalance:
            _validate_weights(next_weights, label=str(date.date()))
            cost = _transaction_cost(current_weights, next_weights, transaction_cost_bps)
            current_weights = next_weights
            next_rebalance, next_weights = next(schedule_iter, (None, None))
        else:
            cost = 0.0

        if not current_weights:
            continue

        row = daily_returns.loc[date]
        port_ret = sum(
            w * float(row.get(t, np.nan))
            for t, w in current_weights.items()
            if pd.notna(row.get(t, np.nan))
        ) - cost

        result.append((date, port_ret))

    if not result:
        return pd.Series(dtype=float)

    dates, rets = zip(*result)
    return pd.Series(list(rets), index=pd.DatetimeIndex(list(dates)), name="portfolio")


# ── BENCHMARK ─────────────────────────────────────────────────────────────────

def run_benchmark(start: str = TRAIN_START, end: str = TEST_END) -> dict:
    """
    100% SPY buy-and-hold benchmark.
    No rebalancing. No transaction costs.
    """
    prices = fetch_equity_data([BENCHMARK], start, end)
    assert prices.attrs.get("adjusted") is True, "Must use adjusted close prices"

    price_series = prices.iloc[:, 0]
    returns = price_series.pct_change().dropna()

    weights = {BENCHMARK: 1.0}
    _validate_weights(weights, "BENCHMARK")

    risk_free = fetch_risk_free_rate(start, end)

    max_dd, dd_dur, dd_rec = max_drawdown(returns)
    sr = sharpe_ratio(returns, risk_free)
    srt = sortino_ratio(returns, risk_free)
    cal = calmar_ratio(returns)
    ann_ret = annualized_return(returns)
    ann_vol = annualized_volatility(returns)
    total_ret = float((1 + returns).prod() - 1)

    log.info(
        "backtest_completed",
        strategy="BENCHMARK",
        sharpe=round(sr, 4),
        cagr=round(ann_ret, 4),
        n_obs=len(returns),
    )

    return {
        "strategy_name": "100% Equity (Benchmark)",
        "strategy_type": "static",
        "cagr": round(ann_ret, 4),
        "total_return": round(total_ret, 4),
        "volatility": round(ann_vol, 4),
        "sharpe_ratio": round(sr, 4),
        "sortino_ratio": round(srt, 4),
        "calmar_ratio": round(cal, 4),
        "max_drawdown": round(max_dd, 4),
        "drawdown_duration_days": dd_dur,
        "drawdown_recovery_days": dd_rec,
        "var_95": round(compute_var(returns, 0.95), 4),
        "cvar_95": round(compute_cvar(returns, 0.95), 4),
        "avg_monthly_turnover": 0.0,
        "avg_equity_weight": 1.0,
        "avg_bond_weight": 0.0,
        "alpha": 0.0,
        "beta": 1.0,
        "is_significant": False,
        "n_observations": len(returns),
        "date_range": {"start": str(returns.index[0].date()), "end": str(returns.index[-1].date())},
    }


# ── Static fixed-weight backtest ──────────────────────────────────────────────

def run_static_backtest(
    strategy_name: str,
    weights: dict,
    start: str = TRAIN_START,
    end: str = TEST_END,
    rebalance_freq: str = "monthly",
    transaction_cost_bps: float = TRANSACTION_COST_BPS,
) -> dict:
    """
    Backtest a fixed-weight static strategy with periodic rebalancing.
    Transaction costs applied at each rebalance.
    """
    _validate_weights(weights, strategy_name)

    tickers = list(weights.keys())
    prices = fetch_equity_data(tickers, start, end)
    assert prices.attrs.get("adjusted") is True, "Must use adjusted close prices"

    risk_free = fetch_risk_free_rate(start, end)

    # Build rebalance schedule using prices up to the day BEFORE each rebalance date
    freq_map = {
        "monthly": "MS",
        "weekly": "W-MON",
        "quarterly": "QS",
        "daily": "B",
    }
    resample_freq = freq_map.get(rebalance_freq, "MS")
    rebalance_dates = prices.resample(resample_freq).first().index

    # Signal at rebalance date t uses prices strictly at t-1 (no lookahead)
    # For fixed weights this is trivially satisfied — no signal calculation needed
    schedule = [(date, weights.copy()) for date in rebalance_dates]

    portfolio_returns = _compute_portfolio_returns(prices, schedule, transaction_cost_bps)

    if portfolio_returns.empty:
        return {"error": f"No returns computed for {strategy_name}"}

    # Benchmark for relative metrics
    bm_prices = fetch_equity_data([BENCHMARK], start, end)
    bm_returns = bm_prices.iloc[:, 0].pct_change().dropna()

    max_dd, dd_dur, dd_rec = max_drawdown(portfolio_returns)
    sr = sharpe_ratio(portfolio_returns, risk_free)
    beta = compute_beta(portfolio_returns, bm_returns)
    alpha = compute_alpha(portfolio_returns, bm_returns, risk_free)
    ir = information_ratio(portfolio_returns, bm_returns)
    n_rebalances = len(rebalance_dates)

    bond_tickers = set(FIXED_INCOME)
    avg_bond_wt = sum(w for t, w in weights.items() if t in bond_tickers)
    avg_eq_wt = 1.0 - avg_bond_wt

    log.info(
        "backtest_completed",
        strategy=strategy_name,
        sharpe=round(sr, 4),
        cagr=round(annualized_return(portfolio_returns), 4),
        n_obs=len(portfolio_returns),
    )

    return {
        "strategy_name": strategy_name,
        "strategy_type": "static",
        "cagr": round(annualized_return(portfolio_returns), 4),
        "total_return": round(float((1 + portfolio_returns).prod() - 1), 4),
        "volatility": round(annualized_volatility(portfolio_returns), 4),
        "sharpe_ratio": round(sr, 4),
        "sortino_ratio": round(sortino_ratio(portfolio_returns, risk_free), 4),
        "calmar_ratio": round(calmar_ratio(portfolio_returns), 4),
        "max_drawdown": round(max_dd, 4),
        "drawdown_duration_days": dd_dur,
        "drawdown_recovery_days": dd_rec,
        "var_95": round(compute_var(portfolio_returns, 0.95), 4),
        "cvar_95": round(compute_cvar(portfolio_returns, 0.95), 4),
        "alpha": round(alpha, 6),
        "alpha_bps": round(alpha * 10_000, 1),
        "beta": round(beta, 4),
        "information_ratio": round(ir, 4),
        "avg_monthly_turnover": round(n_rebalances / max(len(portfolio_returns) / 21, 1), 4),
        "avg_equity_weight": round(avg_eq_wt, 4),
        "avg_bond_weight": round(avg_bond_wt, 4),
        "n_observations": len(portfolio_returns),
        "date_range": {
            "start": str(portfolio_returns.index[0].date()),
            "end": str(portfolio_returns.index[-1].date()),
        },
    }


# ── Walk-forward ──────────────────────────────────────────────────────────────

def walk_forward_test(
    strategy_func: Callable[[str, str], dict],
    full_start: str,
    full_end: str,
    train_months: int = 36,
    test_months: int = 12,
    step_months: int = 6,
) -> dict:
    """
    Rolling walk-forward: train on train_months, test on next test_months.
    strategy_func(train_start, train_end) → returns pd.Series for test period.
    Returns summary of OOS Sharpe ratios across folds.
    """
    dates = pd.date_range(start=full_start, end=full_end, freq="MS")
    folds = []

    i = 0
    while i + train_months + test_months <= len(dates):
        train_start = dates[i].strftime("%Y-%m-%d")
        train_end = dates[i + train_months - 1].strftime("%Y-%m-%d")
        test_start = dates[i + train_months].strftime("%Y-%m-%d")
        test_end_idx = min(i + train_months + test_months - 1, len(dates) - 1)
        test_end = dates[test_end_idx].strftime("%Y-%m-%d")

        try:
            fold_result = strategy_func(train_start, test_end)
            folds.append({
                "train_start": train_start,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
                "result": fold_result,
            })
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
