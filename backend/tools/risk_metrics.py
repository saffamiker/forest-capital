"""
tools/risk_metrics.py

Risk and performance metrics for all 10 portfolio strategies.

ANNUALIZATION_FACTOR = 252 throughout — never 260 or 365. The project brief
requires consistency; mixing factors across functions would make Sharpe ratios
incomparable between strategies. 252 is the industry standard for trading-day
annualisation.

All Sharpe calculations require a pd.Series risk-free rate rather than a scalar.
Our backtest spans 2000-2024: near-zero rates (2011-2015) through 5%+ rates (2023).
A fixed 4.5% would overstate Sharpe in the low-rate period and understate it in
2023, making cross-strategy rankings misleading for precisely the years that matter.

Simple returns throughout (not log). Simple returns are additive across assets,
which is required for the weighted-sum portfolio return calculation. Log returns
are additive over time but not across assets; using them here would require
geometric recombination that complicates the backtester without benefit.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import ANNUALIZATION_FACTOR
from logger import get_logger

log = get_logger(__name__)


# ── Return series helpers ─────────────────────────────────────────────────────

def compute_returns(prices: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    """
    Simple (not log) returns from prices.
    Portfolio return = Σ(weight × asset return) requires returns that are additive
    across assets. Log returns are additive over time, not across assets — using
    them here would require geometric recombination at every rebalance, adding
    complexity with no benefit for our monthly frequency.
    """
    return prices.pct_change().dropna()


def _align_rf(returns: pd.Series, risk_free_rate: pd.Series | float) -> pd.Series:
    """Align risk-free rate to returns index, returning a daily-decimal pd.Series."""
    if isinstance(risk_free_rate, (int, float)):
        return pd.Series(
            risk_free_rate / ANNUALIZATION_FACTOR,
            index=returns.index,
            name="rf",
        )
    # If index dtypes are incompatible, broadcast the mean rather than reindex
    try:
        rf = risk_free_rate.reindex(returns.index, method="ffill")
        rf = rf.fillna(float(risk_free_rate.mean()))
        return rf
    except TypeError:
        return pd.Series(
            float(risk_free_rate.mean()),
            index=returns.index,
            name="rf",
        )


# ── Core metrics ─────────────────────────────────────────────────────────────

def annualized_return(returns: pd.Series) -> float:
    """
    CAGR (geometric annualised return), not arithmetic mean return.
    CAGR compounds daily returns to a single annual rate, which correctly
    accounts for the compounding effect over multi-year backtests. Arithmetic
    mean overstates long-run performance whenever returns are volatile — a
    -50% followed by +50% averages to 0% but produces a -25% compound loss.
    For a 25-year backtest the difference is material.
    """
    n = len(returns)
    if n == 0:
        return 0.0
    total = float((1 + returns).prod())
    if total <= 0:
        return -1.0
    return float(total ** (ANNUALIZATION_FACTOR / n) - 1)


def annualized_volatility(returns: pd.Series) -> float:
    """
    Annualised standard deviation — 252 trading-day factor, not 260 or 365.
    252 is the convention in quantitative finance (standard equity trading days
    per year). 365 conflates calendar and trading days; 260 includes holidays
    that markets are closed. Using 252 here is required by CLAUDE.md config
    to keep Sharpe ratios comparable across all 10 strategies.
    """
    return float(returns.std() * np.sqrt(ANNUALIZATION_FACTOR))


def sharpe_ratio(returns: pd.Series, risk_free_rate: pd.Series | float) -> float:
    """
    Annualised Sharpe ratio with time-varying risk-free rate.
    The pd.Series path is the required primary path; the scalar float path
    exists only as a fallback for edge cases. Our backtest spans 2000-2024:
    near-zero rates (2011-2015), negative real rates (2020-2021), and 5%+
    rates (2023). A fixed scalar would under/overstate Sharpe for every
    sub-period, making cross-strategy comparisons misleading for exactly the
    years that determine whether dynamic strategies beat static ones.
    """
    rf = _align_rf(returns, risk_free_rate)
    excess = returns - rf
    std = excess.std()
    if std == 0:
        return 0.0
    return float(excess.mean() / std * np.sqrt(ANNUALIZATION_FACTOR))


def sortino_ratio(returns: pd.Series, risk_free_rate: pd.Series | float) -> float:
    """
    Sortino ratio — penalises only downside deviation, not total volatility.
    Reported alongside Sharpe to surface strategies that generate upside
    variance (which Sharpe penalises equally to downside variance). If a
    strategy's Sortino >> Sharpe, its volatility is predominantly upside —
    a positive characteristic that Sharpe alone would obscure. In 2008 and
    2022, the strategies with the best Sharpe/Sortino spread are the ones
    worth recommending to Forest Capital for drawdown-sensitive mandates.
    """
    rf = _align_rf(returns, risk_free_rate)
    excess = returns - rf
    downside = excess[excess < 0]
    if len(downside) == 0:
        return float("inf")
    downside_vol = float(np.sqrt((downside ** 2).mean()) * np.sqrt(ANNUALIZATION_FACTOR))
    if downside_vol == 0:
        return 0.0
    ann_excess = float(excess.mean() * ANNUALIZATION_FACTOR)
    return ann_excess / downside_vol


def max_drawdown(returns: pd.Series) -> tuple[float, int, int]:
    """
    Maximum peak-to-trough drawdown.
    Returns (max_drawdown, duration_days, recovery_days).
    recovery_days = -1 if not yet recovered.
    """
    cumulative = (1 + returns).cumprod()
    rolling_max = cumulative.cummax()
    drawdown_series = (cumulative - rolling_max) / rolling_max

    if drawdown_series.min() == 0:
        return 0.0, 0, 0

    trough_idx = drawdown_series.idxmin()
    max_dd = float(drawdown_series.min())

    # Peak: last date before trough where cumulative equalled its rolling max
    pre_trough = rolling_max[:trough_idx]
    peak_value = pre_trough.max()
    peak_candidates = pre_trough[pre_trough >= peak_value]
    peak_idx = peak_candidates.index[-1] if len(peak_candidates) > 0 else returns.index[0]

    diff = trough_idx - peak_idx
    duration = int(diff.days) if hasattr(diff, "days") else int(diff)

    # Recovery: first date after trough where cumulative exceeds peak_value
    post_trough = cumulative[trough_idx:]
    recovery_mask = post_trough >= rolling_max[trough_idx]
    if recovery_mask.any():
        recovery_idx = recovery_mask[recovery_mask].index[0]
        rdiff = recovery_idx - trough_idx
        recovery_days = int(rdiff.days) if hasattr(rdiff, "days") else int(rdiff)
    else:
        recovery_days = -1

    return max_dd, duration, recovery_days


def calmar_ratio(returns: pd.Series) -> float:
    """
    Calmar ratio: annualised return per unit of maximum drawdown.
    Captures a different failure mode than Sharpe. A strategy can have a
    high Sharpe (low daily volatility) but a catastrophic drawdown if losses
    are autocorrelated into a prolonged decline. Calmar is the primary metric
    for Forest Capital's drawdown tolerance question: "how much could we lose
    in the worst case, and was the return worth it?" The 2008 GFC is the key
    stress test — strategies with Calmar < 0.3 there are effectively unacceptable.
    """
    ann_ret = annualized_return(returns)
    max_dd, _, _ = max_drawdown(returns)
    if max_dd == 0:
        return 0.0
    return float(ann_ret / abs(max_dd))


def compute_var(returns: pd.Series, confidence_level: float = 0.95) -> float:
    """
    Historical (non-parametric) VaR — quantile of the empirical return distribution.
    Non-parametric because financial returns have fat tails; parametric (normal)
    VaR systematically underestimates tail losses. The QA agent checks that 2008
    drawdowns are visible in VaR — a parametric model would smooth over them.
    """
    return float(np.percentile(returns.dropna(), (1 - confidence_level) * 100))


def compute_cvar(returns: pd.Series, confidence_level: float = 0.95) -> float:
    """
    CVaR (Expected Shortfall) — mean of returns beyond the VaR threshold.
    Reported alongside VaR because VaR answers "what is the threshold loss at
    p% confidence" while CVaR answers "given that we exceed VaR, how bad is it?"
    CVaR is coherent (subadditive); VaR is not. The pair together characterise
    the entire left tail, which matters for the 2008 GFC stress test analysis.
    """
    clean = returns.dropna()
    var = compute_var(clean, confidence_level)
    tail = clean[clean <= var]
    return float(tail.mean()) if len(tail) > 0 else var


def compute_tail_risk(returns: pd.Series) -> dict:
    """
    Tail risk profile: skewness, kurtosis, and VaR/CVaR at two confidence levels.
    This bundle is used downstream by the QA agent to decide whether to use the
    block bootstrap (when normality is rejected) and by the statistical tests module
    for the Deflated Sharpe Ratio correction (which requires skewness and kurtosis
    to adjust the Sharpe significance threshold). Negative skewness in a strategy
    that looks good on Sharpe is a red flag — it means gains are frequent and small
    but losses are rare and large, which is exactly what fails in a crisis.
    """
    clean = returns.dropna()
    return {
        "skewness": float(clean.skew()),
        "kurtosis": float(clean.kurt()),
        "var_95": compute_var(clean, 0.95),
        "var_99": compute_var(clean, 0.99),
        "cvar_95": compute_cvar(clean, 0.95),
        "cvar_99": compute_cvar(clean, 0.99),
    }


def information_ratio(
    strategy_returns: pd.Series, benchmark_returns: pd.Series
) -> float:
    """
    Annualised information ratio: active return divided by tracking error.
    Distinct from alpha in that IR uses tracking error (consistency of
    outperformance) rather than benchmark volatility as the denominator.
    A strategy with high alpha but inconsistent active returns has a low IR —
    it only outperformed in certain regimes, which makes it fragile. Forest
    Capital needs strategies that beat the benchmark consistently, not
    occasionally by large amounts. IR captures that consistency criterion.
    """
    aligned_s, aligned_b = strategy_returns.align(benchmark_returns, join="inner")
    active = aligned_s - aligned_b
    std = active.std()
    if std == 0:
        return 0.0
    return float(active.mean() / std * np.sqrt(ANNUALIZATION_FACTOR))


def compute_beta(
    strategy_returns: pd.Series, benchmark_returns: pd.Series
) -> float:
    """
    OLS market beta vs benchmark. The 30-observation floor before returning
    the neutral 1.0 default is intentional: OLS covariance estimates are
    unreliable with fewer than ~30 data points and can produce extreme or
    negative betas that misrepresent a strategy's actual market exposure.
    Returning 1.0 is honest — it signals "we don't have enough data to estimate
    this" rather than producing a spurious number that looks precise but isn't.
    """
    aligned_s, aligned_b = strategy_returns.align(benchmark_returns, join="inner")
    if len(aligned_s) < 30:
        return 1.0
    cov = np.cov(aligned_s, aligned_b)
    bm_var = cov[1, 1]
    if bm_var == 0:
        return 1.0
    return float(cov[0, 1] / bm_var)


def compute_alpha(
    strategy_returns: pd.Series,
    benchmark_returns: pd.Series,
    risk_free_rate: pd.Series | float,
) -> float:
    """
    Jensen's alpha: excess return unexplained by benchmark exposure.
    Jensen's (alpha = excess_strategy - beta × excess_benchmark) is the right
    definition here because the project asks whether diversification adds
    return beyond what you'd get by simply scaling up or down equity exposure.
    Raw outperformance vs benchmark would conflate skill with leverage;
    Jensen's controls for beta so only genuine allocation skill is captured.
    Annualised by multiplying daily alpha × 252 — consistent with the rest of
    the metric suite and with how CLAUDE.md requires metrics to be reported.
    """
    rf = _align_rf(strategy_returns, risk_free_rate)
    aligned_s, aligned_b = strategy_returns.align(benchmark_returns, join="inner")
    rf_aligned = rf.reindex(aligned_s.index, method="ffill")

    beta = compute_beta(aligned_s, aligned_b)
    excess_s = aligned_s - rf_aligned
    excess_b = aligned_b - rf_aligned.reindex(aligned_b.index, method="ffill")
    alpha_daily = float(excess_s.mean() - beta * excess_b.mean())
    return alpha_daily * ANNUALIZATION_FACTOR
