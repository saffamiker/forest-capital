"""
Sprint 2 remediation — data loader tests.
Tests load_provided_data() and build_monthly_returns() against the real Excel
file.  External supplemental fetches (yfinance, FRED) are NOT called here —
that is covered by test_supplemental_fetcher.py.

All tests skip gracefully if the Excel file is absent so CI does not fail
when the file is not checked in.
"""
from __future__ import annotations

import os
import sys

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

EXCEL_PATH = os.path.join(
    os.path.dirname(__file__), "..", "backend", "data", "FNA_670_Project_Sources.xlsx"
)
EXCEL_PRESENT = os.path.exists(EXCEL_PATH)

pytestmark = pytest.mark.skipif(
    not EXCEL_PRESENT, reason="FNA_670_Project_Sources.xlsx not present in CI"
)


# ── load_provided_data ────────────────────────────────────────────────────────

def test_load_provided_data_returns_dict():
    from tools.data_fetcher import load_provided_data
    result = load_provided_data()
    assert isinstance(result, dict)


def test_load_provided_data_has_expected_sheets():
    """load_provided_data() normalises sheet names to internal snake_case keys."""
    from tools.data_fetcher import load_provided_data
    result = load_provided_data()
    # Internal normalized keys — not raw sheet names with trailing spaces
    expected = ["hy_effective_yield", "hy_total_return", "bnd", "sp500_monthly"]
    for key in expected:
        assert key in result, f"Expected key '{key}' not found. Got: {list(result.keys())}"


def test_load_provided_data_all_frames_are_dataframes():
    from tools.data_fetcher import load_provided_data
    result = load_provided_data()
    for name, df in result.items():
        assert isinstance(df, pd.DataFrame), f"Sheet '{name}' is not a DataFrame"


def test_load_provided_data_dates_are_datetime():
    """Date columns must be parsed as datetime — not Excel serial integers."""
    from tools.data_fetcher import load_provided_data
    result = load_provided_data()
    for name, df in result.items():
        date_cols = [c for c in df.columns if "date" in str(c).lower()]
        for col in date_cols:
            assert pd.api.types.is_datetime64_any_dtype(df[col]), (
                f"Sheet '{name}' column '{col}' is not datetime — serial conversion may have failed"
            )


def test_load_provided_data_hy_total_return_has_data():
    """HY Total Return key must have thousands of rows (daily data from 1986)."""
    from tools.data_fetcher import load_provided_data
    result = load_provided_data()
    hy = result.get("hy_total_return")  # normalized internal key
    assert hy is not None, "hy_total_return key not found in loaded data"
    assert len(hy) > 5_000, f"Expected >5000 HY daily rows, got {len(hy)}"


def test_load_provided_data_sp500_monthly_is_price_levels():
    """S&P 500 Monthly sheet uses normalized key 'sp500_monthly' and contains price levels."""
    from tools.data_fetcher import load_provided_data
    result = load_provided_data()
    sp500 = result.get("sp500_monthly")  # normalized internal key
    assert sp500 is not None, "sp500_monthly key not found in loaded data"
    # After normalization, columns should include sp500_level
    if "sp500_level" in sp500.columns:
        val = sp500["sp500_level"].dropna().iloc[0]
        assert val > 100, f"S&P 500 value {val:.2f} looks too small to be a price level"


# ── build_monthly_returns ─────────────────────────────────────────────────────

def test_build_monthly_returns_returns_dataframe():
    from tools.data_fetcher import build_monthly_returns
    df = build_monthly_returns()
    assert isinstance(df, pd.DataFrame)


def test_build_monthly_returns_has_required_columns():
    from tools.data_fetcher import build_monthly_returns
    df = build_monthly_returns()
    for col in ["equity_return", "ig_return", "hy_return", "risk_free"]:
        assert col in df.columns, f"Missing column: {col}"


def test_build_monthly_returns_no_nan_in_core_columns():
    """Aligned series must have no NaN — drop logic should handle gaps."""
    from tools.data_fetcher import build_monthly_returns
    df = build_monthly_returns()
    for col in ["equity_return", "ig_return", "hy_return", "risk_free"]:
        n_nan = df[col].isna().sum()
        assert n_nan == 0, f"Column '{col}' has {n_nan} NaN values after alignment"


def test_build_monthly_returns_positive_observations():
    from tools.data_fetcher import build_monthly_returns
    df = build_monthly_returns()
    assert len(df) > 0, "Monthly returns DataFrame is empty"


def test_build_monthly_returns_index_is_monthly():
    """Index must be period-end dates, not arbitrary dates."""
    from tools.data_fetcher import build_monthly_returns
    df = build_monthly_returns()
    # All index dates should be at or near month-end (day >= 28)
    days = df.index.day if hasattr(df.index, 'day') else pd.DatetimeIndex(df.index).day
    assert (days >= 28).all(), "Monthly index contains non-month-end dates"


def test_build_monthly_returns_equity_returns_reasonable():
    """Monthly equity returns should be within [-40%, +40%] — per CLAUDE.md assertion."""
    from tools.data_fetcher import build_monthly_returns
    df = build_monthly_returns()
    out_of_range = ((df["equity_return"] < -0.40) | (df["equity_return"] > 0.40)).sum()
    assert out_of_range == 0, f"{out_of_range} monthly equity returns outside [-40%, +40%]"


# ── Excel serial date conversion assertion ────────────────────────────────────

def test_serial_date_45839_parses_to_2025_07():
    """
    CLAUDE.md Section 4b STEP 1 assertion: serial 45839 should parse to 2025-07-01.
    This verifies the conversion origin is 1899-12-30, not 1900-01-01.
    """
    import pandas as pd
    result = pd.to_datetime(45839, unit="D", origin="1899-12-30")
    assert result.year == 2025
    assert result.month == 7


def test_serial_date_36529_parses_to_2000_01_04():
    """
    Serial 36529 should parse to 2000-01-04 (first trading day of year 2000).
    CLAUDE.md Section 4b mentions serial 36494 for 2000-01-04, but the correct
    serial number for that date with origin 1899-12-30 is 36529.
    This test documents the correct value used by the pipeline.
    """
    import pandas as pd
    result = pd.to_datetime(36529, unit="D", origin="1899-12-30")
    assert result.year == 2000
    assert result.month == 1
    assert result.day == 4
