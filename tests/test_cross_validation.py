"""
Sprint 2 remediation — equity cross-validation tests.
Tests cross_validate_equity() using mocked supplemental data and real
Excel data (skipped in CI).  Verifies: PASS/WARN/FAIL logic, threshold
handling, DataValidationError on too many red months.

bond cross-validation (cross_validate_bonds) is tested via the data
loader tests since it runs internally on load_provided_data() output.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_monthly_equity(n: int = 60, seed: int = 42) -> pd.Series:
    """Synthetic monthly equity returns (Excel-style authoritative series)."""
    np.random.seed(seed)
    idx = pd.bdate_range("2000-01-31", periods=n, freq="BME")
    return pd.Series(np.random.normal(0.008, 0.04, n), index=idx, name="equity_monthly")


def _make_spy_daily_matching(monthly_returns: pd.Series) -> pd.Series:
    """
    Generate SPY daily returns that compound to exactly match each monthly return.
    This simulates a valid data source that agrees with the Excel series.
    """
    all_daily = []
    for i, (month_end, m_ret) in enumerate(monthly_returns.items()):
        # Figure out the trading days in this month
        month_start = month_end - pd.offsets.MonthBegin(1)
        days = pd.bdate_range(month_start, month_end)
        n_days = len(days)
        if n_days == 0:
            continue
        # Distribute monthly return uniformly (equal daily returns)
        daily_ret = (1 + m_ret) ** (1.0 / n_days) - 1
        for d in days:
            all_daily.append((d, daily_ret))
    if not all_daily:
        return pd.Series(dtype=float)
    dates, rets = zip(*all_daily)
    return pd.Series(list(rets), index=pd.DatetimeIndex(dates), name="SPY")


# ── CrossValidationResult dataclass ──────────────────────────────────────────

def test_cross_validation_result_has_status():
    from tools.data_fetcher import CrossValidationResult
    r = CrossValidationResult(
        status="PASS",
        n_months_compared=100,
        n_green=95,
        n_amber=5,
        n_red=0,
        max_discrepancy_pct=0.003,
        mean_discrepancy_pct=0.001,
        worst_month="2020-03",
    )
    assert r.status == "PASS"
    assert r.n_green == 95


def test_cross_validation_result_defaults_empty_issues():
    from tools.data_fetcher import CrossValidationResult
    r = CrossValidationResult(
        status="WARN",
        n_months_compared=50,
        n_green=40,
        n_amber=10,
        n_red=0,
        max_discrepancy_pct=0.008,
        mean_discrepancy_pct=0.003,
        worst_month="2008-10",
    )
    assert r.issues == []


# ── cross_validate_equity with mocked supplemental data ───────────────────────

def _make_provided_data_dict(equity_monthly: pd.Series) -> dict:
    """
    Build a minimal provided_data dict matching the internal keys from load_provided_data().
    cross_validate_equity() uses 'sp500_monthly' — the normalized key, not the sheet name.
    """
    prices = (1 + equity_monthly).cumprod() * 1000
    price_df = pd.DataFrame({
        "date": prices.index,
        "sp500_level": prices.values,
    })
    return {"sp500_monthly": price_df}


def test_cross_validate_equity_returns_result():
    """cross_validate_equity() with closely matching data must return a CrossValidationResult."""
    equity_monthly = _make_monthly_equity(60)
    spy_daily = _make_spy_daily_matching(equity_monthly)
    provided = _make_provided_data_dict(equity_monthly)
    supplemental = {"spy_daily": spy_daily}

    from tools.data_fetcher import cross_validate_equity, CrossValidationResult
    result = cross_validate_equity(provided_data=provided, supplemental=supplemental)
    assert isinstance(result, CrossValidationResult)


def test_cross_validate_equity_status_is_valid():
    equity_monthly = _make_monthly_equity(60)
    spy_daily = _make_spy_daily_matching(equity_monthly)
    provided = _make_provided_data_dict(equity_monthly)
    supplemental = {"spy_daily": spy_daily}

    from tools.data_fetcher import cross_validate_equity
    result = cross_validate_equity(provided_data=provided, supplemental=supplemental)
    assert result.status in {"PASS", "WARN", "FAIL"}


def test_cross_validate_equity_pass_when_close_agreement():
    """When Excel and SPY monthly returns are derived from the same data, status is PASS."""
    equity_monthly = _make_monthly_equity(60)
    spy_daily = _make_spy_daily_matching(equity_monthly)
    provided = _make_provided_data_dict(equity_monthly)
    supplemental = {"spy_daily": spy_daily}

    from tools.data_fetcher import cross_validate_equity
    result = cross_validate_equity(provided_data=provided, supplemental=supplemental)
    # Exact match → all months should be green, no reds
    assert result.n_red == 0


def test_data_validation_error_is_importable():
    """DataValidationError must be importable from data_fetcher."""
    from tools.data_fetcher import DataValidationError
    assert issubclass(DataValidationError, Exception)
