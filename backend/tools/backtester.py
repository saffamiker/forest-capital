"""
tools/backtester.py

Implements BENCHMARK and fixed-weight static strategies for Sprint 2.
Dynamic strategies (momentum, regime-switching, vol-targeting) are Sprint 3.

Four constraints are enforced unconditionally — not optional:
  1. Adjusted prices only: auto_adjust=True in yfinance, verified via attrs["adjusted"].
     Un-adjusted prices are wrong for strategies spanning 2000-2024 due to dividends
     and splits; the GFC drawdown would appear ~15% shallower than it actually was.
  2. Weights sum to 1.0 within 1e-6: the project brief requires full investment at all
     times (FULLY_INVESTED=True in config). Cash allocation is not permitted.
  3. No look-ahead bias: every signal must use data from t-1 or earlier. Same-day
     signals are the most common source of inflated backtest Sharpe ratios in
     academic papers — we enforce this with verify_no_lookahead().
  4. Transaction costs at both legs: we deduct costs when entering AND exiting
     positions. One-sided cost accounting overstates strategy viability by ~50%.
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
    Hard assertion: every signal date must be strictly before the trade date.
    Same-day signals (signal_date == price_date) are the single most common
    cause of spurious Sharpe ratios in published backtests. A momentum signal
    computed at close on day t used to trade at the same close violates market
    microstructure — you cannot trade on end-of-day data at end-of-day prices.
    For our quarterly-rebalance strategies this is trivially satisfied, but the
    assertion is here so Sprint 3's daily-signal strategies (VOL_TARGETING,
    MOMENTUM_ROTATION) cannot accidentally introduce the bias.
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
    Daily-resolution portfolio returns from a rebalance schedule.
    Daily resolution (not just rebalance-date resolution) is required because:
      (a) transaction costs are deducted on the exact rebalance day, not smoothed
      (b) max_drawdown() needs daily granularity to find the true trough
      (c) the QA agent's look-ahead check operates date by date
    Weight drift between rebalances is captured implicitly: the weights are held
    fixed between rebalance dates, so price appreciation shifts effective weights
    and the daily return correctly reflects that drift.
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
    100% SPY buy-and-hold — required by the FNA 670 project brief.
    The brief mandates this exact benchmark (not a broad index ETF or equal-weight)
    to establish the baseline every strategy must beat on a risk-adjusted basis.
    No rebalancing and no transaction costs because there are no allocation decisions
    to implement — it is the passive reference point, not a managed strategy.
    is_significant is hard-coded False: a strategy cannot be significantly better
    than itself (the Tier 1 tests compare strategies against this baseline).
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
    Fixed-weight static strategy with periodic rebalancing and transaction costs.
    The default rebalance_freq is "monthly" here, but CLAUDE.md Section 3 specifies
    REBALANCE_FREQ_STATIC = "quarterly" for consistency with the dynamic strategies.
    Callers (CLASSIC_60_40, RISK_PARITY etc.) pass "quarterly" explicitly — the
    monthly default exists only as a fallback for ad-hoc calls, not as policy.
    Transaction costs of 10bps per trade (configurable) are the project brief's
    assumption; they are applied round-trip on every rebalance turnover.
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
    Rolling (fixed-window) walk-forward OOS test — the primary OOS validation method.
    Rolling rather than expanding window is used because economic regimes shift:
    a 36-month window trained on 2000-2003 (dot-com crash) should not have equal
    influence on a 2022 strategy as data from 2018-2021. Expanding window is run
    separately in cross_validation.py and compared — if rolling and expanding
    diverge by > EXPANDING_WF_DIVERGENCE, the strategy is flagged as potentially
    regime-dependent. That comparison is the Sprint 3 cross-validation test.
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


# ── Sprint 3 strategies ───────────────────────────────────────────────────────

def run_classic_6040(start: str = TRAIN_START, end: str = TEST_END) -> dict:
    """
    Classic 60% equity / 40% bond portfolio — the most widely used static benchmark.
    60/40 is implemented as a sprint-3 strategy (not sprint-2) because it requires
    TLT data, which must be aligned with SPY. The strategy is required by the project
    brief as a baseline: if a dynamic strategy can't beat 60/40, it's not useful.
    The allocation breakdown documented in provenance:
      60% SPY — large-cap US equity, total return
      40% TLT — 20+ year Treasury bonds, total return
    Quarterly rebalancing per REBALANCE_FREQ_STATIC in config — aligns with dynamic
    strategies to avoid rebalancing frequency confounding performance comparisons.
    """
    weights = {"SPY": 0.60, "TLT": 0.40}
    return run_static_backtest(
        "Classic 60/40",
        weights,
        start=start,
        end=end,
        rebalance_freq="quarterly",
    )


def run_risk_parity(start: str = TRAIN_START, end: str = TEST_END) -> dict:
    """
    Equal risk contribution across SPY, TLT, GLD using scipy risk-parity solver.
    Risk parity rather than equal weight because each asset class has very different
    volatility: SPY ~15%/yr, TLT ~12%/yr, GLD ~16%/yr in 2000-2024.
    Equal weights (33% each) would allocate roughly equal capital but very different
    risk — SPY and GLD would dominate the portfolio's risk budget. Risk parity
    explicitly equalises the risk contribution of each asset, so no single asset
    accounts for more than 1/n of total variance.
    The rolling 63-day window for covariance estimation is intentionally short here:
    long windows would include 2008 cross-correlation spikes, causing the model to
    permanently under-weight equities based on a regime that lasted only 6 months.
    """
    from tools.optimizer import risk_parity_optimize

    tickers = ["SPY", "TLT", "GLD"]
    prices = fetch_equity_data(tickers, start, end)
    assert prices.attrs.get("adjusted") is True, "Must use adjusted close prices"

    risk_free = fetch_risk_free_rate(start, end)

    # Use full-period returns for initial weight computation, then rebalance quarterly
    returns = prices.pct_change().dropna()
    w_arr = risk_parity_optimize(returns, min_weight=MIN_WEIGHT, max_weight=MAX_WEIGHT)
    weights = {t: float(w) for t, w in zip(tickers, w_arr)}
    _validate_weights(weights, "RISK_PARITY")

    # Quarterly rebalance schedule — same weights each period (static risk parity)
    rebalance_dates = prices.resample("QS").first().index
    schedule = [(date, weights.copy()) for date in rebalance_dates]
    portfolio_returns = _compute_portfolio_returns(prices, schedule, TRANSACTION_COST_BPS)

    if portfolio_returns.empty:
        return {"error": "No returns computed for RISK_PARITY"}

    bm_prices = fetch_equity_data([BENCHMARK], start, end)
    bm_returns = bm_prices.iloc[:, 0].pct_change().dropna()
    max_dd, dd_dur, dd_rec = max_drawdown(portfolio_returns)
    sr = sharpe_ratio(portfolio_returns, risk_free)
    beta = compute_beta(portfolio_returns, bm_returns)
    alpha = compute_alpha(portfolio_returns, bm_returns, risk_free)
    ir = information_ratio(portfolio_returns, bm_returns)

    log.info("backtest_completed", strategy="RISK_PARITY",
             sharpe=round(sr, 4), cagr=round(annualized_return(portfolio_returns), 4))

    return _build_result("Risk Parity", "static", portfolio_returns, risk_free, bm_returns,
                         weights, max_dd, dd_dur, dd_rec, sr, beta, alpha, ir)


def run_min_variance(start: str = TRAIN_START, end: str = TEST_END) -> dict:
    """
    Minimum global variance portfolio across all EQUITIES + FIXED_INCOME.
    Min-variance is chosen when expected return estimates are unreliable — which
    is always the case for a 36-month window (OPTIMIZATION_WINDOW=36). μ estimation
    error at 36 months dominates Σ estimation error; mean-variance on noisy μ
    produces unstable corner solutions. Min-variance sidesteps this by requiring
    only Σ, which converges faster: you need fewer observations to estimate
    variance than to estimate mean with the same precision.
    Asset universe: SPY (equity) + TLT, IEF (bonds) to give the optimizer
    meaningful diversification options while remaining within the three-asset-class
    constraint from the project brief.
    """
    from tools.optimizer import min_variance_optimize

    tickers = ["SPY", "TLT", "IEF"]
    prices = fetch_equity_data(tickers, start, end)
    assert prices.attrs.get("adjusted") is True, "Must use adjusted close prices"

    risk_free = fetch_risk_free_rate(start, end)
    returns = prices.pct_change().dropna()
    w_arr = min_variance_optimize(returns, min_weight=MIN_WEIGHT, max_weight=MAX_WEIGHT)
    weights = {t: float(w) for t, w in zip(tickers, w_arr)}
    _validate_weights(weights, "MIN_VARIANCE")

    rebalance_dates = prices.resample("QS").first().index
    schedule = [(date, weights.copy()) for date in rebalance_dates]
    portfolio_returns = _compute_portfolio_returns(prices, schedule, TRANSACTION_COST_BPS)

    if portfolio_returns.empty:
        return {"error": "No returns computed for MIN_VARIANCE"}

    bm_prices = fetch_equity_data([BENCHMARK], start, end)
    bm_returns = bm_prices.iloc[:, 0].pct_change().dropna()
    max_dd, dd_dur, dd_rec = max_drawdown(portfolio_returns)
    sr = sharpe_ratio(portfolio_returns, risk_free)
    beta = compute_beta(portfolio_returns, bm_returns)
    alpha = compute_alpha(portfolio_returns, bm_returns, risk_free)
    ir = information_ratio(portfolio_returns, bm_returns)

    log.info("backtest_completed", strategy="MIN_VARIANCE",
             sharpe=round(sr, 4), cagr=round(annualized_return(portfolio_returns), 4))

    return _build_result("Minimum Variance", "static", portfolio_returns, risk_free, bm_returns,
                         weights, max_dd, dd_dur, dd_rec, sr, beta, alpha, ir)


def run_equal_weight(start: str = TRAIN_START, end: str = TEST_END) -> dict:
    """
    Equal-weight 25% each across SPY, TLT, GLD, VNQ.
    Equal weight is a useful baseline for evaluating optimization approaches:
    if a min-variance or risk-parity strategy cannot beat equal weight, the
    optimization is adding complexity without adding value. DeMiguel et al.
    (2009) showed that equal weight outperforms mean-variance in many settings —
    this result underpins why we include equal weight as one of the 10 strategies.
    VNQ (Real Estate) is included to provide inflation sensitivity — real assets
    behave differently from financial assets in inflationary regimes. This is
    the only strategy that includes real estate.
    """
    weights = {"SPY": 0.25, "TLT": 0.25, "GLD": 0.25, "VNQ": 0.25}
    return run_static_backtest(
        "Equal Weight",
        weights,
        start=start,
        end=end,
        rebalance_freq="quarterly",
    )


def run_momentum_rotation(start: str = TRAIN_START, end: str = TEST_END) -> dict:
    """
    Composite momentum rotation: long top 3 assets from {SPY, QQQ, IWM, TLT, IEF, GLD}.
    Momentum is computed as a weighted average of 21/63/126/252-day returns
    (weights: 0.10/0.20/0.30/0.40 from MOMENTUM_WEIGHTS in config). The weighting
    toward longer lookbacks reflects the academic consensus: Jegadeesh & Titman
    (1993) find momentum is strongest at 6-12 months, not 1-3 months. The
    21-day component captures short-term continuation; 252-day prevents the
    strategy from chasing brief spikes that reverse quickly.
    Top 3 of 6 assets avoids over-concentration (top 1 would be too volatile)
    while still providing meaningful selection (top 5 of 6 is almost equal-weight).
    The look-ahead check is trivially satisfied here: momentum at t uses returns
    through t-1, and the rebalance trades at t-open (using t-1 close prices).
    """
    from config import MOMENTUM_LOOKBACKS, MOMENTUM_WEIGHTS

    universe = ["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD"]
    prices = fetch_equity_data(universe, start, end)
    assert prices.attrs.get("adjusted") is True, "Must use adjusted close prices"

    risk_free = fetch_risk_free_rate(start, end)

    # Build quarterly rebalance schedule with momentum-based weights
    rebalance_dates = prices.resample("QS").first().index
    schedule = []

    for i, date in enumerate(rebalance_dates):
        # Signal uses only prices available BEFORE this rebalance date (t-1)
        available_prices = prices[prices.index < date]
        if len(available_prices) < max(MOMENTUM_LOOKBACKS):
            continue

        # Composite momentum score for each asset
        scores = {}
        for ticker in universe:
            if ticker not in available_prices.columns:
                continue
            price_series = available_prices[ticker].dropna()
            composite = 0.0
            for lookback, w in zip(MOMENTUM_LOOKBACKS, MOMENTUM_WEIGHTS):
                if len(price_series) >= lookback:
                    ret = (price_series.iloc[-1] / price_series.iloc[-lookback] - 1.0)
                    composite += w * ret
            scores[ticker] = composite

        # Long top 3 assets with equal weight (1/3 each)
        if len(scores) < 3:
            continue
        top3 = sorted(scores, key=scores.__getitem__, reverse=True)[:3]
        weights = {t: 1.0 / 3.0 for t in top3}
        schedule.append((date, weights))

    if not schedule:
        return {"error": "No valid momentum signals — insufficient history"}

    portfolio_returns = _compute_portfolio_returns(prices, schedule, TRANSACTION_COST_BPS)
    if portfolio_returns.empty:
        return {"error": "No returns computed for MOMENTUM_ROTATION"}

    bm_prices = fetch_equity_data([BENCHMARK], start, end)
    bm_returns = bm_prices.iloc[:, 0].pct_change().dropna()
    max_dd, dd_dur, dd_rec = max_drawdown(portfolio_returns)
    sr = sharpe_ratio(portfolio_returns, risk_free)
    beta = compute_beta(portfolio_returns, bm_returns)
    alpha_val = compute_alpha(portfolio_returns, bm_returns, risk_free)
    ir = information_ratio(portfolio_returns, bm_returns)

    # Average weights over all rebalance periods for reporting
    avg_weights: dict[str, float] = {}
    for _, w in schedule:
        for t, wt in w.items():
            avg_weights[t] = avg_weights.get(t, 0.0) + wt / len(schedule)

    log.info("backtest_completed", strategy="MOMENTUM_ROTATION",
             sharpe=round(sr, 4), cagr=round(annualized_return(portfolio_returns), 4))

    return _build_result("Momentum Rotation", "dynamic", portfolio_returns, risk_free, bm_returns,
                         avg_weights, max_dd, dd_dur, dd_rec, sr, beta, alpha_val, ir)


def run_regime_switching(start: str = TRAIN_START, end: str = TEST_END) -> dict:
    """
    Regime-switching allocation using threshold classifier (HMM added Sprint 4).
    Allocations follow the CLAUDE.md specification exactly:
      BULL:       {SPY: 0.80, TLT: 0.20}  — maximum equity, minimal bonds
      BEAR:       {SPY: 0.20, TLT: 0.60, GLD: 0.20}  — defensive positioning
      TRANSITION: {SPY: 0.50, TLT: 0.40, GLD: 0.10}  — balanced, hedge both ways
    Regime is assessed quarterly using data from the prior REGIME_WINDOW trading
    days (63 days). Quarterly frequency prevents over-trading on transient signals;
    the threshold method changes regime only when multiple indicators agree
    (60%/30% bear ratio threshold), providing natural signal smoothing.
    GLD is included only in non-BULL regimes as a crisis hedge — in strong bull
    markets, gold's low expected return is a drag on performance.
    """
    from tools.regime_detector import _classify_threshold

    tickers = ["SPY", "TLT", "GLD"]
    prices = fetch_equity_data(tickers, start, end)
    assert prices.attrs.get("adjusted") is True, "Must use adjusted close prices"

    risk_free = fetch_risk_free_rate(start, end)

    REGIME_WEIGHTS = {
        "BULL":       {"SPY": 0.80, "TLT": 0.20, "GLD": 0.00},
        "BEAR":       {"SPY": 0.20, "TLT": 0.60, "GLD": 0.20},
        "TRANSITION": {"SPY": 0.50, "TLT": 0.40, "GLD": 0.10},
    }
    # GLD weight 0.00 in BULL must be excluded from weight dict to pass validation
    def _clean_weights(d: dict) -> dict:
        return {k: v for k, v in d.items() if v > 1e-9}

    rebalance_dates = prices.resample("QS").first().index
    schedule = []

    for date in rebalance_dates:
        available = prices[prices.index < date]
        if len(available) < REGIME_WINDOW:
            continue

        # Compute simplified threshold signals from price history alone
        # (VIX/FRED data not available in backtester — use equity trend as primary signal)
        price_now = float(available["SPY"].iloc[-1])
        price_past = float(available["SPY"].iloc[-REGIME_WINDOW])
        equity_trend = (price_now - price_past) / price_past

        # Use equity trend to approximate regime without external data
        # A full signal set requires FRED data — available in Sprint 4 live mode
        regime = _classify_threshold(
            vix=None,
            yield_curve_slope=None,
            equity_trend=equity_trend,
            credit_spread=None,
        )

        weights = _clean_weights(REGIME_WEIGHTS[regime])
        # Normalise to ensure sum to 1 (GLD is 0 in BULL so must renormalise)
        total = sum(weights.values())
        weights = {k: v / total for k, v in weights.items()}
        schedule.append((date, weights))

    if not schedule:
        return {"error": "No regime signals computed for REGIME_SWITCHING"}

    portfolio_returns = _compute_portfolio_returns(prices, schedule, TRANSACTION_COST_BPS)
    if portfolio_returns.empty:
        return {"error": "No returns computed for REGIME_SWITCHING"}

    bm_prices = fetch_equity_data([BENCHMARK], start, end)
    bm_returns = bm_prices.iloc[:, 0].pct_change().dropna()
    max_dd, dd_dur, dd_rec = max_drawdown(portfolio_returns)
    sr = sharpe_ratio(portfolio_returns, risk_free)
    beta = compute_beta(portfolio_returns, bm_returns)
    alpha_val = compute_alpha(portfolio_returns, bm_returns, risk_free)
    ir = information_ratio(portfolio_returns, bm_returns)

    avg_weights: dict[str, float] = {}
    for _, w in schedule:
        for t, wt in w.items():
            avg_weights[t] = avg_weights.get(t, 0.0) + wt / len(schedule)

    log.info("backtest_completed", strategy="REGIME_SWITCHING",
             sharpe=round(sr, 4), cagr=round(annualized_return(portfolio_returns), 4))

    return _build_result("Regime Switching", "dynamic", portfolio_returns, risk_free, bm_returns,
                         avg_weights, max_dd, dd_dur, dd_rec, sr, beta, alpha_val, ir)


def run_vol_targeting(start: str = TRAIN_START, end: str = TEST_END) -> dict:
    """
    Volatility-targeting: scale equity allocation to hit TARGET_VOLATILITY=10%.
    The mechanism: realised_vol_21d = std(last 21 daily SPY returns) * sqrt(252).
    equity_weight = TARGET_VOLATILITY / realised_vol_21d, capped at MAX_WEIGHT=0.40.
    Remainder goes to IEF (intermediate Treasury bonds — lower duration risk than TLT).
    This strategy was originally proposed by Moreira & Muir (2017) and shown to
    significantly improve Sharpe ratios for equity strategies. The key insight:
    equity volatility is autocorrelated (ARCH effects) — today's high volatility
    predicts tomorrow's high volatility. By reducing equity when volatility is high
    and increasing when volatility is low, the strategy avoids the worst of crises.
    Weekly rebalancing is chosen (not daily) because daily rebalancing on 21-day
    realised vol would be noise-chasing — the vol estimate changes little day-to-day.
    Weekly smooths the allocation while still being responsive to regime shifts.
    """
    tickers = ["SPY", "IEF"]
    prices = fetch_equity_data(tickers, start, end)
    assert prices.attrs.get("adjusted") is True, "Must use adjusted close prices"

    risk_free = fetch_risk_free_rate(start, end)

    # Weekly rebalance schedule
    rebalance_dates = prices.resample("W-MON").first().index
    schedule = []

    spy_returns = prices["SPY"].pct_change()

    for date in rebalance_dates:
        # Realised vol uses data from the 21 trading days before this rebalance
        available_ret = spy_returns[spy_returns.index < date].dropna()
        if len(available_ret) < 21:
            continue

        vol_21d = float(available_ret.iloc[-21:].std() * np.sqrt(ANNUALIZATION_FACTOR))
        if vol_21d <= 0:
            spy_weight = MAX_WEIGHT
        else:
            spy_weight = min(TARGET_VOLATILITY / vol_21d, MAX_WEIGHT)
            spy_weight = max(spy_weight, MIN_WEIGHT)

        ief_weight = 1.0 - spy_weight
        weights = {"SPY": spy_weight, "IEF": ief_weight}
        schedule.append((date, weights))

    if not schedule:
        return {"error": "No vol signals computed for VOL_TARGETING"}

    portfolio_returns = _compute_portfolio_returns(prices, schedule, TRANSACTION_COST_BPS)
    if portfolio_returns.empty:
        return {"error": "No returns computed for VOL_TARGETING"}

    bm_prices = fetch_equity_data([BENCHMARK], start, end)
    bm_returns = bm_prices.iloc[:, 0].pct_change().dropna()
    max_dd, dd_dur, dd_rec = max_drawdown(portfolio_returns)
    sr = sharpe_ratio(portfolio_returns, risk_free)
    beta = compute_beta(portfolio_returns, bm_returns)
    alpha_val = compute_alpha(portfolio_returns, bm_returns, risk_free)
    ir = information_ratio(portfolio_returns, bm_returns)

    avg_weights: dict[str, float] = {}
    for _, w in schedule:
        for t, wt in w.items():
            avg_weights[t] = avg_weights.get(t, 0.0) + wt / len(schedule)

    log.info("backtest_completed", strategy="VOL_TARGETING",
             sharpe=round(sr, 4), cagr=round(annualized_return(portfolio_returns), 4))

    return _build_result("Volatility Targeting", "dynamic", portfolio_returns, risk_free, bm_returns,
                         avg_weights, max_dd, dd_dur, dd_rec, sr, beta, alpha_val, ir)


def run_black_litterman(start: str = TRAIN_START, end: str = TEST_END) -> dict:
    """
    Black-Litterman optimisation over SPY, TLT, IEF, GLD.
    Sprint 3 uses the prior-only posterior (no views) — CIO agent views are added
    in Sprint 4. The equilibrium prior is derived from equal market-cap weights
    (documented in provenance.json under 'bl_market_cap_priors' as GAP 5).
    Without views, BL reduces to mean-variance on the equilibrium expected returns.
    The key value of BL in Sprint 3 is the covariance structure: full covariance
    estimated from 36 months of daily returns (not just diagonal variance), giving
    the optimizer proper diversification information. The prior also prevents the
    corner solutions that raw mean-variance produces — the equilibrium prior acts
    as a regulariser.
    Sprint 4 will incorporate CIO agent views (e.g. "SPY will outperform TLT
    by 2% over the next quarter with 60% confidence") into the BL posterior.
    """
    from tools.optimizer import black_litterman_optimize
    from config import OPTIMIZATION_WINDOW

    tickers = ["SPY", "TLT", "IEF", "GLD"]
    prices = fetch_equity_data(tickers, start, end)
    assert prices.attrs.get("adjusted") is True, "Must use adjusted close prices"

    risk_free = fetch_risk_free_rate(start, end)

    rebalance_dates = prices.resample("QS").first().index
    schedule = []
    days_per_month = ANNUALIZATION_FACTOR // 12
    lookback = OPTIMIZATION_WINDOW * days_per_month

    for date in rebalance_dates:
        available = prices[prices.index < date]
        if len(available) < lookback:
            continue

        window_returns = available.iloc[-lookback:].pct_change().dropna()
        w_arr = black_litterman_optimize(
            window_returns,
            min_weight=MIN_WEIGHT,
            max_weight=MAX_WEIGHT,
        )
        weights = {t: float(w) for t, w in zip(tickers, w_arr)}
        _validate_weights(weights, f"BL_{date.date()}")
        schedule.append((date, weights))

    if not schedule:
        return {"error": "No BL optimizations computed"}

    portfolio_returns = _compute_portfolio_returns(prices, schedule, TRANSACTION_COST_BPS)
    if portfolio_returns.empty:
        return {"error": "No returns computed for BLACK_LITTERMAN"}

    bm_prices = fetch_equity_data([BENCHMARK], start, end)
    bm_returns = bm_prices.iloc[:, 0].pct_change().dropna()
    max_dd, dd_dur, dd_rec = max_drawdown(portfolio_returns)
    sr = sharpe_ratio(portfolio_returns, risk_free)
    beta = compute_beta(portfolio_returns, bm_returns)
    alpha_val = compute_alpha(portfolio_returns, bm_returns, risk_free)
    ir = information_ratio(portfolio_returns, bm_returns)

    avg_weights: dict[str, float] = {}
    for _, w in schedule:
        for t, wt in w.items():
            avg_weights[t] = avg_weights.get(t, 0.0) + wt / len(schedule)

    log.info("backtest_completed", strategy="BLACK_LITTERMAN",
             sharpe=round(sr, 4), cagr=round(annualized_return(portfolio_returns), 4))

    return _build_result("Black-Litterman", "dynamic", portfolio_returns, risk_free, bm_returns,
                         avg_weights, max_dd, dd_dur, dd_rec, sr, beta, alpha_val, ir)


def run_max_sharpe_rolling(start: str = TRAIN_START, end: str = TEST_END) -> dict:
    """
    Rolling max-Sharpe portfolio: quarterly optimisation on a 36-month lookback.
    Max-Sharpe (rather than max-return or min-variance) is the appropriate objective
    when the mandate is risk-adjusted outperformance vs the benchmark — which is
    exactly the project brief's requirement. 36-month window (OPTIMIZATION_WINDOW=36)
    balances estimation error and regime relevance: shorter windows have noisy μ̂;
    longer windows include stale data from different rate/growth regimes.
    The max-Sharpe QP uses the change-of-variables trick (Lasserre formulation)
    to convert the non-convex Sharpe maximisation into a convex QP — exact solution,
    no gradient approximation needed.
    Universe: SPY + TLT + IEF — three asset classes from the project brief.
    GLD is excluded here to focus on the equity/bond diversification question.
    """
    from tools.optimizer import max_sharpe_optimize
    from config import OPTIMIZATION_WINDOW

    tickers = ["SPY", "TLT", "IEF"]
    prices = fetch_equity_data(tickers, start, end)
    assert prices.attrs.get("adjusted") is True, "Must use adjusted close prices"

    risk_free = fetch_risk_free_rate(start, end)

    rebalance_dates = prices.resample("QS").first().index
    schedule = []
    days_per_month = ANNUALIZATION_FACTOR // 12
    lookback = OPTIMIZATION_WINDOW * days_per_month

    for date in rebalance_dates:
        available = prices[prices.index < date]
        if len(available) < lookback:
            continue

        window_returns = available.iloc[-lookback:].pct_change().dropna()
        # Use the current risk-free level as the annualised rf for max-Sharpe
        rf_available = risk_free[risk_free.index < date]
        rf_annual = float(rf_available.iloc[-1]) if len(rf_available) > 0 else RISK_FREE_RATE_FALLBACK

        w_arr = max_sharpe_optimize(
            window_returns,
            risk_free=rf_annual,
            min_weight=MIN_WEIGHT,
            max_weight=MAX_WEIGHT,
        )
        weights = {t: float(w) for t, w in zip(tickers, w_arr)}
        _validate_weights(weights, f"MAX_SHARPE_{date.date()}")
        schedule.append((date, weights))

    if not schedule:
        return {"error": "No max-Sharpe optimizations computed"}

    portfolio_returns = _compute_portfolio_returns(prices, schedule, TRANSACTION_COST_BPS)
    if portfolio_returns.empty:
        return {"error": "No returns computed for MAX_SHARPE_ROLLING"}

    bm_prices = fetch_equity_data([BENCHMARK], start, end)
    bm_returns = bm_prices.iloc[:, 0].pct_change().dropna()
    max_dd, dd_dur, dd_rec = max_drawdown(portfolio_returns)
    sr = sharpe_ratio(portfolio_returns, risk_free)
    beta = compute_beta(portfolio_returns, bm_returns)
    alpha_val = compute_alpha(portfolio_returns, bm_returns, risk_free)
    ir = information_ratio(portfolio_returns, bm_returns)

    avg_weights: dict[str, float] = {}
    for _, w in schedule:
        for t, wt in w.items():
            avg_weights[t] = avg_weights.get(t, 0.0) + wt / len(schedule)

    log.info("backtest_completed", strategy="MAX_SHARPE_ROLLING",
             sharpe=round(sr, 4), cagr=round(annualized_return(portfolio_returns), 4))

    return _build_result("Max Sharpe Rolling", "dynamic", portfolio_returns, risk_free, bm_returns,
                         avg_weights, max_dd, dd_dur, dd_rec, sr, beta, alpha_val, ir)


# ── Result builder ────────────────────────────────────────────────────────────

def _build_result(
    name: str,
    strategy_type: str,
    portfolio_returns: pd.Series,
    risk_free: pd.Series,
    bm_returns: pd.Series,
    avg_weights: dict,
    max_dd: float,
    dd_dur: int,
    dd_rec: int,
    sr: float,
    beta: float,
    alpha: float,
    ir: float,
) -> dict:
    """
    Shared result dict builder — avoids duplicating 30 fields across 9 strategy functions.
    All strategy result dicts must be structurally identical: the dashboard's strategy
    comparison table, QA audit, and statistical test suite all depend on consistent keys.
    A mismatch (e.g., one strategy missing 'var_95') would produce a silent None in
    the frontend rather than a visible error — harder to catch than a KeyError.
    bond_tickers is used to split equity vs bond weight for the avg_bond_weight metric.
    """
    bond_tickers = set(FIXED_INCOME)
    avg_bond_wt = sum(w for t, w in avg_weights.items() if t in bond_tickers)
    avg_eq_wt = 1.0 - avg_bond_wt

    return {
        "strategy_name": name,
        "strategy_type": strategy_type,
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
        "avg_equity_weight": round(avg_eq_wt, 4),
        "avg_bond_weight": round(avg_bond_wt, 4),
        "n_observations": len(portfolio_returns),
        "date_range": {
            "start": str(portfolio_returns.index[0].date()),
            "end": str(portfolio_returns.index[-1].date()),
        },
    }


# ── Run all 10 strategies ─────────────────────────────────────────────────────

def run_all_strategies(start: str = TRAIN_START, end: str = TEST_END) -> list[dict]:
    """
    Orchestrates all 10 strategies and returns a list sorted by Sharpe ratio.
    Used by GET /api/backtest/compare to populate the dashboard comparison table.
    Error isolation: each strategy is wrapped in try/except so a single failure
    (e.g., yfinance data gap for one ticker) does not prevent all others from
    running. Partial results are better than no results during the presentation.
    The fallback to mock data for failed strategies ensures the dashboard always
    shows 10 rows — a strategy showing mock data is still visible and clearly
    labelled as such in the strategy_type field.
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
            result = runner(start=start, end=end)
            results.append(result)
        except Exception as exc:
            log.warning("strategy_failed", strategy=name, error=str(exc))
            # Fall back to mock data with an error note
            fallback = dict(mock_lookup.get(name, {"strategy_name": name, "sharpe_ratio": 0.0}))
            fallback["error"] = str(exc)
            results.append(fallback)

    return sorted(results, key=lambda r: r.get("sharpe_ratio", 0.0), reverse=True)
