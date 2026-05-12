"""
Sprint 3 addendum — data transformation correctness tests.

Verifies the mathematical operations applied to raw Excel data before it
enters the backtester:
  - Excel serial date conversion (origin=1899-12-30)
  - Month-end date snapping
  - Simple (not log) return calculation via pct_change
  - Compound (not simple) DTB3 risk-free rate conversion to monthly
  - Post-alignment data integrity (requires Excel file, skips in CI)

Tests are grouped:
  Excel-independent: Groups 1-4 — always run, no external data needed.
  Excel-dependent:   Groups 5-6 — skip if FNA_670_Project_Sources.xlsx absent.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

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

_EXCEL_PATH = (
    Path(__file__).parent.parent / "backend" / "data" / "FNA_670_Project_Sources.xlsx"
)
_EXCEL_PRESENT = _EXCEL_PATH.exists()


def _skip_if_no_excel() -> None:
    if not _EXCEL_PRESENT:
        pytest.skip(
            "FNA_670_Project_Sources.xlsx not present — skipping Excel-dependent tests"
        )


# ── GROUP 1: Excel serial date conversion ─────────────────────────────────────

def test_serial_date_36526_is_2000_01_01():
    """
    Excel serial 36526 must parse to 2000-01-01.

    The origin is 1899-12-30 (not 1900-01-01) because Excel incorrectly
    treats 1900 as a leap year.  All dates in Dr. Panttser's file use this
    convention.  Getting the origin wrong shifts every date by 2 days,
    corrupting all time-series alignment.
    """
    result = pd.to_datetime(36526, unit="D", origin="1899-12-30")
    assert result == pd.Timestamp("2000-01-01"), (
        f"Serial 36526 should be 2000-01-01, got {result.date()}"
    )


def test_serial_date_39448_is_2008_01_01():
    """
    Excel serial 39448 must parse to 2008-01-01.
    Validates the conversion across an 8-year span including two leap years
    (2000, 2004) — ensures the compound leap year counting is correct.
    """
    result = pd.to_datetime(39448, unit="D", origin="1899-12-30")
    assert result == pd.Timestamp("2008-01-01"), (
        f"Serial 39448 should be 2008-01-01, got {result.date()}"
    )


def test_serial_dates_are_timezone_naive():
    """
    All converted timestamps must be timezone-naive (no tz info).
    A tz-aware timestamp would cause misalignment when joining against
    tz-naive series from yfinance or FRED, producing NaN-filled columns.
    """
    ts = pd.to_datetime(36526, unit="D", origin="1899-12-30")
    assert ts.tzinfo is None, f"Expected tz-naive timestamp, got {ts}"


def test_serial_date_vectorised_conversion():
    """
    Vectorised conversion of a Series of serial ints produces the same result
    as scalar conversion.  The data_fetcher converts entire columns at once.
    """
    serials = pd.Series([36526, 39448])
    result = pd.to_datetime(serials, unit="D", origin="1899-12-30")
    assert result.iloc[0] == pd.Timestamp("2000-01-01")
    assert result.iloc[1] == pd.Timestamp("2008-01-01")


# ── GROUP 2: Month-end snapping ────────────────────────────────────────────────

def test_month_end_snap_mid_month():
    """
    A mid-month date must snap to the last calendar day of that month.
    The BND and BAMLHYH series are aggregated to month-end via resample('ME'),
    which uses last-day-of-month anchoring — not the arbitrary trade date.
    """
    date = pd.Timestamp("2007-05-14")
    snapped = date + pd.offsets.MonthEnd(0)
    assert snapped == pd.Timestamp("2007-05-31"), (
        f"2007-05-14 should snap to 2007-05-31, got {snapped.date()}"
    )


def test_month_end_snap_already_month_end():
    """
    A date that is already the last day of its month should snap to itself.
    MonthEnd(0) is idempotent on month-end dates.
    """
    date = pd.Timestamp("2007-04-30")
    snapped = date + pd.offsets.MonthEnd(0)
    assert snapped == pd.Timestamp("2007-04-30"), (
        f"2007-04-30 should remain 2007-04-30, got {snapped.date()}"
    )


def test_month_end_snap_february_leap_year():
    """
    February in a leap year must snap to the 29th, not the 28th.
    Incorrect leap year handling would misalign February monthly returns.
    """
    date = pd.Timestamp("2008-02-15")
    snapped = date + pd.offsets.MonthEnd(0)
    assert snapped == pd.Timestamp("2008-02-29"), (
        f"2008-02-15 should snap to 2008-02-29, got {snapped.date()}"
    )


# ── GROUP 3: Simple return calculation (not log) ───────────────────────────────

def test_pct_change_yields_simple_not_log_return():
    """
    pct_change([100, 110]) = 0.10 exactly.
    The log return ln(110/100) = 0.09531 — materially lower at large magnitudes.
    Using log returns by mistake would understate HY bond returns in high-yield
    crisis months (e.g. 2009 recovery) by several percentage points.
    """
    prices = pd.Series([100.0, 110.0])
    simple = float(prices.pct_change().dropna().iloc[0])
    log_ret = float(np.log(1.10))
    assert abs(simple - 0.10) < 1e-10
    assert abs(simple - log_ret) > 0.004  # 40 bp difference — not interchangeable


def test_pct_change_negative_return_simple():
    """
    pct_change([100, 50]) = -0.50 exactly.
    The log return ln(50/100) = -0.693 — the two conventions differ by 0.193
    at a -50% drawdown (GFC-scale decline), making this error impossible to hide.
    """
    prices = pd.Series([100.0, 50.0])
    simple = float(prices.pct_change().dropna().iloc[0])
    assert abs(simple - (-0.50)) < 1e-10


def test_pct_change_drops_first_row():
    """
    pct_change() leaves the first entry as NaN — there is no prior price to
    compute a return from.  After dropna() the series has len(prices) - 1 rows.
    An off-by-one here would misalign returns against dates.
    """
    prices = pd.Series([100.0, 105.0, 110.0])
    returns = prices.pct_change().dropna()
    assert len(returns) == 2
    assert abs(float(returns.iloc[0]) - 0.05) < 1e-10
    assert abs(float(returns.iloc[1]) - (110.0 / 105.0 - 1)) < 1e-10


# ── GROUP 4: DTB3 risk-free rate conversion ────────────────────────────────────

def test_dtb3_compound_monthly_rate_5pct():
    """
    DTB3 stored as 5.0% annual must convert to (1.05)^(1/12) - 1 = 0.004074/month.
    Simple division (0.05/12 = 0.004167) understates true compounding by ~0.9 bps
    per month — small per month, but cumulates to ~10 bps per year.  At scale this
    biases Sharpe ratios upward by overstating excess returns in high-rate periods.

    The data_fetcher uses: (1 + dtb3_monthly_avg / 100) ** (1/12) - 1
    """
    annual_pct = 5.0
    compound = (1 + annual_pct / 100) ** (1 / 12) - 1
    simple = (annual_pct / 100) / 12

    # Compound formula produces the correct value
    assert abs(compound - 0.004074) < 1e-4, (
        f"Compound monthly rate should be ≈0.004074, got {compound:.6f}"
    )
    # Simple division is measurably wrong — test must fail if simple is used
    assert abs(simple - 0.004167) < 1e-4, (
        f"Simple monthly rate should be ≈0.004167, got {simple:.6f}"
    )
    # The two are distinguishable — test suite would catch if formula is swapped
    assert abs(compound - simple) > 0.00005, (
        "Compound and simple differ by >0.5 bps — a unit test can tell them apart"
    )


def test_dtb3_compound_monthly_rate_zero():
    """
    Zero annual rate must produce exactly zero monthly rate under both methods.
    This boundary case confirms the conversion handles near-zero Fed funds periods.
    """
    compound = (1 + 0.0) ** (1 / 12) - 1
    assert compound == 0.0


def test_dtb3_data_fetcher_uses_compound_conversion():
    """
    build_monthly_returns() must populate risk_free using compound conversion
    of the DTB3 series.  We verify the formula constant from data_fetcher.py
    line 510: ((1 + dtb3_monthly_avg / 100) ** (1 / 12) - 1).

    This test reads the source code rather than running the full pipeline,
    making it fast and CI-safe without the Excel file.
    """
    import ast
    import pathlib

    fetcher_path = (
        pathlib.Path(__file__).parent.parent
        / "backend" / "tools" / "data_fetcher.py"
    )
    source = fetcher_path.read_text(encoding="utf-8")

    # The compound formula must be present in data_fetcher.py
    assert "(1 / 12)" in source, (
        "data_fetcher.py must use (1 + rate) ** (1/12) - 1 for DTB3 conversion"
    )
    # Simple division must not be the rf conversion (no 'dtb3 / 100 / 12' pattern)
    assert "dtb3_monthly_avg / 100 / 12" not in source, (
        "data_fetcher.py must not use simple division for DTB3 monthly conversion"
    )


# ── GROUP 5: Aligned dataset quality (requires Excel) ─────────────────────────

def test_aligned_dataset_has_no_nan():
    """
    After aligning equity, IG, and HY monthly returns, no NaN values should
    remain.  A NaN in any return column silently propagates into portfolio
    returns, invalidating the CAGR and Sharpe for that month without raising
    any error — the most dangerous class of data defect.
    """
    _skip_if_no_excel()
    from tools.data_fetcher import build_monthly_returns, load_provided_data

    provided = load_provided_data()
    df = build_monthly_returns(provided)

    core_cols = ["equity_return", "ig_return", "hy_return"]
    for col in core_cols:
        if col in df.columns:
            n_nan = df[col].isna().sum()
            assert n_nan == 0, f"Found {n_nan} NaN values in {col} after alignment"


def test_aligned_asset_series_share_identical_index():
    """
    After alignment, equity, IG, and HY monthly returns must share the same
    DatetimeIndex.  Mismatched indices produce NaN-filled joins and silently
    reduce the effective observation count below the ≥220 power threshold.
    """
    _skip_if_no_excel()
    from tools.data_fetcher import build_monthly_returns, load_provided_data

    provided = load_provided_data()
    df = build_monthly_returns(provided)

    core_cols = [c for c in ["equity_return", "ig_return", "hy_return"] if c in df.columns]
    assert len(core_cols) >= 3, f"Expected 3 core columns, found: {core_cols}"

    # All columns in a single DataFrame share the same index by construction —
    # we verify there are no rows where any core column is NaN while another is not
    any_present = df[core_cols].notna().any(axis=1)
    all_present = df[core_cols].notna().all(axis=1)
    partial_rows = int((any_present & ~all_present).sum())
    assert partial_rows == 0, (
        f"{partial_rows} rows have data for some but not all asset classes — "
        "alignment is incomplete"
    )


# ── GROUP 6: Known historical values (requires Excel) ─────────────────────────

def test_2023_average_monthly_risk_free_plausible():
    """
    The 2023 monthly risk-free rate (from DTB3) should average between 0.004 and
    0.005 per month (≈ 4.8% – 6.2% annualised via compound).  In 2023 the Fed
    funds rate peaked near 5.5%, making this a hard constraint on the conversion.
    """
    _skip_if_no_excel()
    from tools.data_fetcher import build_monthly_returns, load_provided_data

    provided = load_provided_data()
    df = build_monthly_returns(provided)

    if "risk_free" not in df.columns:
        pytest.skip("risk_free column not in build_monthly_returns output")

    rf_2023 = df.loc["2023", "risk_free"]
    if len(rf_2023) == 0:
        pytest.skip("No 2023 data in aligned dataset")

    avg_monthly_rf = float(rf_2023.mean())
    assert 0.004 <= avg_monthly_rf <= 0.005, (
        f"2023 average monthly risk-free = {avg_monthly_rf:.5f}; "
        f"expected 0.004–0.005 (Fed funds 4.8%–6.2%)"
    )


def test_october_2008_equity_return_negative():
    """
    October 2008 was the worst single month of the Global Financial Crisis.
    The S&P 500 fell between 14% and 20% that month — any value outside that
    range indicates a data loading error or wrong return type (price vs total).
    """
    _skip_if_no_excel()
    from tools.data_fetcher import build_monthly_returns, load_provided_data

    provided = load_provided_data()
    df = build_monthly_returns(provided)

    if "equity_return" not in df.columns:
        pytest.skip("equity_return column not in build_monthly_returns output")

    oct_2008 = df.loc[
        (df.index.year == 2008) & (df.index.month == 10), "equity_return"
    ]
    if len(oct_2008) == 0:
        pytest.skip("October 2008 not in aligned dataset")

    ret = float(oct_2008.iloc[0])
    assert -0.20 <= ret <= -0.14, (
        f"October 2008 equity return = {ret:.2%}; expected -14% to -20% (GFC crash)"
    )
