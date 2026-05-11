"""
Portfolio risk and performance metrics.
Always uses ANNUALIZATION_FACTOR = 252.
sharpe_ratio() requires a pd.Series risk-free rate — never a fixed float constant.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import ANNUALIZATION_FACTOR
from logger import get_logger

log = get_logger(__name__)


# ── Return series helpers ─────────────────────────────────────────────────────

def compute_returns(prices: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    """Convert prices to simple returns."""
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
    """CAGR from a daily simple-return series."""
    n = len(returns)
    if n == 0:
        return 0.0
    total = float((1 + returns).prod())
    if total <= 0:
        return -1.0
    return float(total ** (ANNUALIZATION_FACTOR / n) - 1)


def annualized_volatility(returns: pd.Series) -> float:
    """Annualised standard deviation using 252 trading-day factor."""
    return float(returns.std() * np.sqrt(ANNUALIZATION_FACTOR))


def sharpe_ratio(returns: pd.Series, risk_free_rate: pd.Series | float) -> float:
    """
    Annualised Sharpe ratio.
    risk_free_rate must be a pd.Series of daily decimal rates aligned to returns,
    or a scalar annual rate (converted internally to daily — provided only as
    a fallback, never as the primary path).
    """
    rf = _align_rf(returns, risk_free_rate)
    excess = returns - rf
    std = excess.std()
    if std == 0:
        return 0.0
    return float(excess.mean() / std * np.sqrt(ANNUALIZATION_FACTOR))


def sortino_ratio(returns: pd.Series, risk_free_rate: pd.Series | float) -> float:
    """Sortino ratio using downside deviation below the risk-free rate."""
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
    """Calmar = annualised return / abs(max drawdown)."""
    ann_ret = annualized_return(returns)
    max_dd, _, _ = max_drawdown(returns)
    if max_dd == 0:
        return 0.0
    return float(ann_ret / abs(max_dd))


def compute_var(returns: pd.Series, confidence_level: float = 0.95) -> float:
    """Value-at-Risk at confidence_level (negative number = loss)."""
    return float(np.percentile(returns.dropna(), (1 - confidence_level) * 100))


def compute_cvar(returns: pd.Series, confidence_level: float = 0.95) -> float:
    """Conditional VaR (Expected Shortfall) at confidence_level."""
    clean = returns.dropna()
    var = compute_var(clean, confidence_level)
    tail = clean[clean <= var]
    return float(tail.mean()) if len(tail) > 0 else var


def compute_tail_risk(returns: pd.Series) -> dict:
    """Skewness, kurtosis, and drawdown distribution summary."""
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
    """Annualised information ratio vs benchmark."""
    aligned_s, aligned_b = strategy_returns.align(benchmark_returns, join="inner")
    active = aligned_s - aligned_b
    std = active.std()
    if std == 0:
        return 0.0
    return float(active.mean() / std * np.sqrt(ANNUALIZATION_FACTOR))


def compute_beta(
    strategy_returns: pd.Series, benchmark_returns: pd.Series
) -> float:
    """OLS beta of strategy vs benchmark."""
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
    """Jensen's alpha (annualised)."""
    rf = _align_rf(strategy_returns, risk_free_rate)
    aligned_s, aligned_b = strategy_returns.align(benchmark_returns, join="inner")
    rf_aligned = rf.reindex(aligned_s.index, method="ffill")

    beta = compute_beta(aligned_s, aligned_b)
    excess_s = aligned_s - rf_aligned
    excess_b = aligned_b - rf_aligned.reindex(aligned_b.index, method="ffill")
    alpha_daily = float(excess_s.mean() - beta * excess_b.mean())
    return alpha_daily * ANNUALIZATION_FACTOR
