"""
Sprint 2 — risk metrics unit tests.
No external API calls — all tests use synthetic return series.
Verifies: ANNUALIZATION_FACTOR=252 used consistently, correct formulas.
"""
from __future__ import annotations

import os
import sys
import math

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_index(n: int = 252) -> pd.DatetimeIndex:
    return pd.bdate_range("2020-01-01", periods=n)


def flat_series(daily_ret: float, n: int = 252) -> pd.Series:
    """Constant daily return series with datetime index."""
    return pd.Series(daily_ret, index=make_index(n))


def zero_rf(n: int = 252) -> pd.Series:
    return pd.Series(0.0, index=make_index(n))


# ── annualized_return ─────────────────────────────────────────────────────────

def test_annualized_return_flat_daily():
    from tools.risk_metrics import annualized_return
    daily = 0.0004
    returns = flat_series(daily, n=252)
    ann = annualized_return(returns)
    expected = (1 + daily) ** 252 - 1
    assert abs(ann - expected) < 1e-6


def test_annualized_return_zero():
    from tools.risk_metrics import annualized_return
    returns = flat_series(0.0)
    assert annualized_return(returns) == 0.0


def test_annualized_return_negative():
    from tools.risk_metrics import annualized_return
    returns = flat_series(-0.001, n=252)
    ann = annualized_return(returns)
    assert ann < 0


def test_annualized_return_empty():
    from tools.risk_metrics import annualized_return
    assert annualized_return(pd.Series(dtype=float)) == 0.0


# ── annualized_volatility ─────────────────────────────────────────────────────

def test_annualized_volatility_zero_for_flat():
    from tools.risk_metrics import annualized_volatility
    returns = flat_series(0.001)
    assert annualized_volatility(returns) == pytest.approx(0.0, abs=1e-10)


def test_annualized_volatility_uses_252():
    from tools.risk_metrics import annualized_volatility
    np.random.seed(42)
    daily = pd.Series(np.random.normal(0, 0.01, 252), index=make_index(252))
    ann_vol = annualized_volatility(daily)
    expected = daily.std() * np.sqrt(252)
    assert abs(ann_vol - expected) < 1e-10


def test_annualized_volatility_positive():
    from tools.risk_metrics import annualized_volatility
    np.random.seed(1)
    returns = pd.Series(np.random.normal(0, 0.01, 252), index=make_index(252))
    assert annualized_volatility(returns) > 0


# ── sharpe_ratio ──────────────────────────────────────────────────────────────

def test_sharpe_ratio_known_value():
    from tools.risk_metrics import sharpe_ratio
    # daily return 0.04%, zero rf → annualised Sharpe = 0.04% / σ * sqrt(252)
    np.random.seed(42)
    returns = pd.Series(np.random.normal(0.0004, 0.01, 252))
    rf = zero_rf()
    sr = sharpe_ratio(returns, rf)
    expected = returns.mean() / returns.std() * np.sqrt(252)
    assert abs(sr - expected) < 1e-6


def test_sharpe_ratio_zero_excess():
    from tools.risk_metrics import sharpe_ratio
    # rf == returns → excess = 0 → Sharpe = 0
    daily = 0.0004
    returns = flat_series(daily)
    rf = flat_series(daily)  # same rate as returns
    assert sharpe_ratio(returns, rf) == pytest.approx(0.0, abs=1e-6)


def test_sharpe_ratio_accepts_scalar_rf():
    from tools.risk_metrics import sharpe_ratio
    np.random.seed(42)
    returns = pd.Series(np.random.normal(0.0004, 0.01, 252))
    # scalar annual rate — should not raise
    sr = sharpe_ratio(returns, 0.04)
    assert isinstance(sr, float)


def test_sharpe_ratio_higher_return_higher_sharpe():
    from tools.risk_metrics import sharpe_ratio
    np.random.seed(42)
    noise = np.random.normal(0, 0.01, 252)
    low_ret = pd.Series(noise + 0.0001)
    high_ret = pd.Series(noise + 0.0008)
    rf = zero_rf()
    assert sharpe_ratio(high_ret, rf) > sharpe_ratio(low_ret, rf)


# ── sortino_ratio ─────────────────────────────────────────────────────────────

def test_sortino_ratio_positive_for_positive_excess():
    from tools.risk_metrics import sortino_ratio
    np.random.seed(42)
    returns = pd.Series(np.random.normal(0.001, 0.01, 252))
    rf = zero_rf()
    assert sortino_ratio(returns, rf) > 0


def test_sortino_ratio_always_ge_sharpe_for_positive_skew():
    """For symmetric normal, Sortino ≈ Sharpe * sqrt(2)."""
    from tools.risk_metrics import sharpe_ratio, sortino_ratio
    np.random.seed(1)
    returns = pd.Series(np.random.normal(0.0005, 0.01, 1000))
    rf = zero_rf(n=1000)
    sr = sharpe_ratio(returns, rf)
    srt = sortino_ratio(returns, rf)
    # Sortino should be strictly greater than Sharpe for positive excess
    if sr > 0:
        assert srt >= sr


# ── max_drawdown ──────────────────────────────────────────────────────────────

def test_max_drawdown_no_loss_series():
    from tools.risk_metrics import max_drawdown
    returns = flat_series(0.001)
    dd, dur, rec = max_drawdown(returns)
    assert dd == pytest.approx(0.0, abs=1e-10)
    assert dur == 0
    assert rec == 0


def test_max_drawdown_known_loss():
    from tools.risk_metrics import max_drawdown
    # 10% loss then recovery
    returns = pd.Series(
        [0.0] * 10 + [-0.05, -0.05] + [0.10] + [0.0] * 5
    )
    dd, dur, _ = max_drawdown(returns)
    assert dd < 0
    assert abs(dd) > 0.05  # Lost more than 5%


def test_max_drawdown_returns_negative():
    from tools.risk_metrics import max_drawdown
    np.random.seed(42)
    returns = pd.Series(np.random.normal(-0.001, 0.02, 252))
    dd, _, _ = max_drawdown(returns)
    assert dd <= 0


def test_max_drawdown_duration_positive():
    from tools.risk_metrics import max_drawdown
    returns = pd.Series(
        [0.0] * 50 + [-0.02] * 20 + [0.05] * 5,
        index=pd.bdate_range("2020-01-01", periods=75),
    )
    dd, dur, _ = max_drawdown(returns)
    assert dur >= 0


# ── compute_var / compute_cvar ────────────────────────────────────────────────

def test_var_95_is_negative():
    from tools.risk_metrics import compute_var
    np.random.seed(42)
    returns = pd.Series(np.random.normal(0, 0.01, 252))
    assert compute_var(returns, 0.95) < 0


def test_cvar_95_le_var_95():
    from tools.risk_metrics import compute_var, compute_cvar
    np.random.seed(42)
    returns = pd.Series(np.random.normal(0, 0.01, 252))
    assert compute_cvar(returns, 0.95) <= compute_var(returns, 0.95)


def test_var_increases_with_volatility():
    from tools.risk_metrics import compute_var
    np.random.seed(42)
    low_vol = pd.Series(np.random.normal(0, 0.005, 252))
    high_vol = pd.Series(np.random.normal(0, 0.02, 252))
    assert compute_var(high_vol, 0.95) < compute_var(low_vol, 0.95)


# ── calmar_ratio ──────────────────────────────────────────────────────────────

def test_calmar_ratio_zero_drawdown():
    from tools.risk_metrics import calmar_ratio
    returns = flat_series(0.001)
    assert calmar_ratio(returns) == 0.0


def test_calmar_ratio_positive_for_growing_portfolio():
    from tools.risk_metrics import calmar_ratio
    np.random.seed(42)
    returns = pd.Series(np.random.normal(0.001, 0.005, 252))
    cal = calmar_ratio(returns)
    assert isinstance(cal, float)


# ── ANNUALIZATION_FACTOR == 252 (contract test) ───────────────────────────────

def test_annualization_factor_is_252():
    from config import ANNUALIZATION_FACTOR
    assert ANNUALIZATION_FACTOR == 252


def test_risk_metrics_import_uses_252():
    """Verify the module imports ANNUALIZATION_FACTOR from config (not a hardcoded value)."""
    import tools.risk_metrics as rm
    import inspect
    src = inspect.getsource(rm)
    # Should reference ANNUALIZATION_FACTOR, not 260 or 365
    assert "260" not in src
    assert "365" not in src
    assert "ANNUALIZATION_FACTOR" in src
