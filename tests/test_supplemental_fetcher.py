"""
Sprint 2 remediation — supplemental fetcher tests.
Tests fetch_supplemental_data() with all external calls mocked.
Verifies: correct source types, SPY-only yfinance, VIX and DGS2 from FRED,
          Fama-French from datareader, provenance assertions.
No real network calls are made.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

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

# ── Synthetic data helpers ─────────────────────────────────────────────────────

def _make_spy_prices(n: int = 252, seed: int = 42) -> pd.DataFrame:
    np.random.seed(seed)
    prices = np.cumprod(1 + np.random.normal(0.0004, 0.01, n)) * 100.0
    return pd.DataFrame(
        {"SPY": prices},
        index=pd.bdate_range("2000-01-03", periods=n),
    )


def _make_vix_series(n: int = 252, seed: int = 7) -> pd.Series:
    np.random.seed(seed)
    return pd.Series(
        15.0 + np.random.normal(0, 3, n),
        index=pd.bdate_range("2000-01-03", periods=n),
        name="VIXCLS",
    )


def _make_dgs2_series(n: int = 252, value: float = 4.5) -> pd.Series:
    return pd.Series(
        value,
        index=pd.bdate_range("2000-01-03", periods=n),
        name="DGS2",
    )


def _make_ff_factors(n: int = 60) -> pd.DataFrame:
    # Monthly index from 2000 (within the supplemental fetch range) so date alignment works.
    # Percentage values: Mkt-RF typically ranges -20% to +20% monthly.
    # fetch_supplemental_data divides by 100 to convert to decimals.
    idx = pd.date_range("2000-01-31", periods=n, freq="ME")
    return pd.DataFrame(
        {
            "Mkt-RF": np.random.normal(0.5, 4.0, n),
            "SMB": np.random.normal(0.1, 2.0, n),
            "HML": np.random.normal(0.2, 2.0, n),
            "RF": np.full(n, 0.02),
        },
        index=idx,
    )


# ── fetch_supplemental_data ────────────────────────────────────────────────────

def test_supplemental_fetcher_returns_dict(tmp_path, monkeypatch):
    spy_prices = _make_spy_prices()
    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr("tools.data_fetcher._yfinance_fetch", lambda t, s, e: spy_prices)
    monkeypatch.setattr("tools.data_fetcher._fred_fetch", lambda sid, s, e: _make_vix_series().to_frame(sid))
    monkeypatch.setattr("tools.data_fetcher._famafrench_fetch", lambda d: _make_ff_factors())

    from tools.data_fetcher import fetch_supplemental_data
    result = fetch_supplemental_data("2000-01-01", "2001-01-01")
    assert isinstance(result, dict)


def test_supplemental_fetcher_has_spy_daily(tmp_path, monkeypatch):
    spy_prices = _make_spy_prices()
    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr("tools.data_fetcher._yfinance_fetch", lambda t, s, e: spy_prices)
    monkeypatch.setattr("tools.data_fetcher._fred_fetch", lambda sid, s, e: _make_vix_series().to_frame(sid))
    monkeypatch.setattr("tools.data_fetcher._famafrench_fetch", lambda d: _make_ff_factors())

    from tools.data_fetcher import fetch_supplemental_data
    result = fetch_supplemental_data("2000-01-01", "2001-01-01")
    assert "spy_daily" in result, "Missing spy_daily key"
    assert isinstance(result["spy_daily"], pd.Series)


def test_supplemental_fetcher_has_vix_daily(tmp_path, monkeypatch):
    spy_prices = _make_spy_prices()
    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr("tools.data_fetcher._yfinance_fetch", lambda t, s, e: spy_prices)
    monkeypatch.setattr("tools.data_fetcher._fred_fetch", lambda sid, s, e: _make_vix_series().to_frame(sid))
    monkeypatch.setattr("tools.data_fetcher._famafrench_fetch", lambda d: _make_ff_factors())

    from tools.data_fetcher import fetch_supplemental_data
    result = fetch_supplemental_data("2000-01-01", "2001-01-01")
    assert "vix_daily" in result, "Missing vix_daily key"


def test_supplemental_fetcher_has_dgs2_daily(tmp_path, monkeypatch):
    spy_prices = _make_spy_prices()
    call_log: list[str] = []

    def mock_fred(sid: str, s: str, e: str) -> pd.DataFrame:
        call_log.append(sid)
        if "VIX" in sid:
            return _make_vix_series().to_frame(sid)
        return _make_dgs2_series().to_frame(sid)

    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr("tools.data_fetcher._yfinance_fetch", lambda t, s, e: spy_prices)
    monkeypatch.setattr("tools.data_fetcher._fred_fetch", mock_fred)
    monkeypatch.setattr("tools.data_fetcher._famafrench_fetch", lambda d: _make_ff_factors())

    from tools.data_fetcher import fetch_supplemental_data
    result = fetch_supplemental_data("2000-01-01", "2001-01-01")
    assert "dgs2_daily" in result, "Missing dgs2_daily key"
    # DGS2 must come from FRED, not yfinance
    assert "DGS2" in call_log, "DGS2 was not fetched from FRED"


def test_supplemental_fetcher_has_ff_factors(tmp_path, monkeypatch):
    spy_prices = _make_spy_prices()
    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr("tools.data_fetcher._yfinance_fetch", lambda t, s, e: spy_prices)
    monkeypatch.setattr("tools.data_fetcher._fred_fetch", lambda sid, s, e: _make_vix_series().to_frame(sid))
    monkeypatch.setattr("tools.data_fetcher._famafrench_fetch", lambda d: _make_ff_factors())

    from tools.data_fetcher import fetch_supplemental_data
    result = fetch_supplemental_data("2000-01-01", "2001-01-01")
    assert "ff_factors" in result, "Missing ff_factors key"


def test_supplemental_fetcher_spy_is_returns_not_prices(tmp_path, monkeypatch):
    """spy_daily should be RETURNS (pct_change), not raw price levels."""
    spy_prices = _make_spy_prices()
    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr("tools.data_fetcher._yfinance_fetch", lambda t, s, e: spy_prices)
    monkeypatch.setattr("tools.data_fetcher._fred_fetch", lambda sid, s, e: _make_vix_series().to_frame(sid))
    monkeypatch.setattr("tools.data_fetcher._famafrench_fetch", lambda d: _make_ff_factors())

    from tools.data_fetcher import fetch_supplemental_data
    result = fetch_supplemental_data("2000-01-01", "2001-01-01")
    spy = result["spy_daily"]
    # Daily returns should be in the range [-0.20, +0.20], not [50, 200]
    assert spy.abs().max() < 0.20, f"spy_daily max value {spy.abs().max():.4f} — looks like prices, not returns"


def test_supplemental_fetcher_yfinance_called_with_spy_only(tmp_path, monkeypatch):
    """yfinance must only be called for SPY — never BND or HYG."""
    yfinance_calls: list[list[str]] = []

    def spy_only_fetch(tickers: list[str], s: str, e: str) -> pd.DataFrame:
        yfinance_calls.append(tickers)
        prices = np.cumprod(1 + np.random.normal(0.0004, 0.01, 252)) * 100.0
        return pd.DataFrame(
            {t: prices for t in tickers},
            index=pd.bdate_range("2000-01-03", periods=252),
        )

    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr("tools.data_fetcher._yfinance_fetch", spy_only_fetch)
    monkeypatch.setattr("tools.data_fetcher._fred_fetch", lambda sid, s, e: _make_vix_series().to_frame(sid))
    monkeypatch.setattr("tools.data_fetcher._famafrench_fetch", lambda d: _make_ff_factors())

    from tools.data_fetcher import fetch_supplemental_data
    fetch_supplemental_data("2000-01-01", "2001-01-01")

    all_tickers = [t for call in yfinance_calls for t in call]
    assert "BND" not in all_tickers, "BND fetched from yfinance — must come from Excel only"
    assert "HYG" not in all_tickers, "HYG fetched from yfinance — must come from Excel only"


def test_supplemental_fetcher_ff_factors_are_decimals(tmp_path, monkeypatch):
    """Fama-French factors must be in decimal form — not percentage points."""
    spy_prices = _make_spy_prices()
    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr("tools.data_fetcher._yfinance_fetch", lambda t, s, e: spy_prices)
    monkeypatch.setattr("tools.data_fetcher._fred_fetch", lambda sid, s, e: _make_vix_series().to_frame(sid))
    monkeypatch.setattr("tools.data_fetcher._famafrench_fetch", lambda d: _make_ff_factors())

    from tools.data_fetcher import fetch_supplemental_data
    result = fetch_supplemental_data("2000-01-01", "2001-01-01")
    ff = result["ff_factors"]
    assert isinstance(ff, pd.DataFrame), "ff_factors must be a DataFrame"
    assert "Mkt-RF" in ff.columns, f"Expected 'Mkt-RF' column, got: {list(ff.columns)}"
    # Monthly factor in % terms would be ~5.0 — in decimal it's ~0.05
    # Check on non-NaN values only
    non_nan = ff["Mkt-RF"].dropna()
    assert len(non_nan) > 0, "All Mkt-RF values are NaN after conversion"
    max_val = non_nan.abs().max()
    assert max_val < 1.0, f"Mkt-RF max {max_val:.3f} — factors may not be divided by 100"


# ── LQD bridge ─────────────────────────────────────────────────────────────────

def _make_lqd_prices(n: int = 1000, seed: int = 99) -> pd.DataFrame:
    """Synthetic LQD prices spanning 2002-07 to 2006-12 (pre-BND period)."""
    np.random.seed(seed)
    prices = np.cumprod(1 + np.random.normal(0.0002, 0.005, n)) * 90.0
    return pd.DataFrame(
        {"LQD": prices},
        index=pd.bdate_range("2002-08-01", periods=n),
    )


def test_lqd_bridge_survives_renamed_column(tmp_path, monkeypatch):
    """
    Regression test: the LQD bridge fetch must succeed even when the
    yfinance wrapper returns a DataFrame whose column is not literally
    named "LQD". This used to break when newer yfinance versions returned
    multi-level columns or renamed Close to Price — the old check
    `if "LQD" in lqd_prices.columns` would fail silently and the LQD
    bridge would be skipped, leaving the aligned monthly count at 224
    instead of 282. The fix accesses the close-price series positionally.
    """
    spy_prices = _make_spy_prices()
    # Simulate the wrapper having renamed the column (e.g. to the price
    # field name instead of the ticker)
    lqd_with_close_column = pd.DataFrame(
        {"Close": _make_lqd_prices()["LQD"].values},
        index=_make_lqd_prices().index,
    )

    def mock_yfinance(tickers: list[str], s: str, e: str) -> pd.DataFrame:
        if "SPY" in tickers:
            return spy_prices
        if "LQD" in tickers:
            return lqd_with_close_column
        return pd.DataFrame()

    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr("tools.data_fetcher._yfinance_fetch", mock_yfinance)
    monkeypatch.setattr("tools.data_fetcher._fred_fetch", lambda sid, s, e: _make_vix_series().to_frame(sid))
    monkeypatch.setattr("tools.data_fetcher._famafrench_fetch", lambda d: _make_ff_factors())

    from tools.data_fetcher import fetch_supplemental_data
    result = fetch_supplemental_data("2000-01-01", "2024-12-31")

    assert "lqd_bridge_daily" in result, (
        "LQD bridge skipped — positional access to close-price column failed"
    )
    bridge = result["lqd_bridge_daily"]
    assert len(bridge) > 0, "LQD bridge present but empty"
    assert bridge.abs().max() < 0.15, "Bridge looks like prices not returns"


def test_supplemental_fetcher_has_lqd_bridge_daily(tmp_path, monkeypatch):
    """
    fetch_supplemental_data() must include lqd_bridge_daily.
    LQD (pre-BND IG bridge) is the only permitted non-SPY yfinance fetch —
    it fills the gap between LQD's 2002 launch and BND's Excel coverage (2007).
    """
    spy_prices = _make_spy_prices()
    lqd_prices = _make_lqd_prices()

    def mock_yfinance(tickers: list[str], s: str, e: str) -> pd.DataFrame:
        if "SPY" in tickers:
            return spy_prices
        if "LQD" in tickers:
            return lqd_prices
        return pd.DataFrame()

    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr("tools.data_fetcher._yfinance_fetch", mock_yfinance)
    monkeypatch.setattr("tools.data_fetcher._fred_fetch", lambda sid, s, e: _make_vix_series().to_frame(sid))
    monkeypatch.setattr("tools.data_fetcher._famafrench_fetch", lambda d: _make_ff_factors())

    from tools.data_fetcher import fetch_supplemental_data
    result = fetch_supplemental_data("2000-01-01", "2024-12-31")

    assert "lqd_bridge_daily" in result, "Missing lqd_bridge_daily key"
    assert isinstance(result["lqd_bridge_daily"], pd.Series)
    # LQD bridge should be returns (small decimals), not price levels
    bridge = result["lqd_bridge_daily"]
    assert bridge.abs().max() < 0.15, "lqd_bridge_daily looks like prices, not returns"


def test_supplemental_fetcher_lqd_bnd_hyg_not_in_bond_fetches(tmp_path, monkeypatch):
    """
    BND and HYG must never be fetched from yfinance — they come from the Excel file.
    LQD is the only permitted non-SPY yfinance fetch.
    """
    yfinance_calls: list[list[str]] = []

    def mock_yfinance(tickers: list[str], s: str, e: str) -> pd.DataFrame:
        yfinance_calls.append(tickers)
        n = 252
        prices = np.cumprod(1 + np.random.normal(0.0004, 0.01, n)) * 100.0
        return pd.DataFrame({t: prices for t in tickers}, index=pd.bdate_range("2002-08-01", periods=n))

    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr("tools.data_fetcher._yfinance_fetch", mock_yfinance)
    monkeypatch.setattr("tools.data_fetcher._fred_fetch", lambda sid, s, e: _make_vix_series().to_frame(sid))
    monkeypatch.setattr("tools.data_fetcher._famafrench_fetch", lambda d: _make_ff_factors())

    from tools.data_fetcher import fetch_supplemental_data
    fetch_supplemental_data("2000-01-01", "2024-12-31")

    all_tickers = [t for call in yfinance_calls for t in call]
    assert "BND" not in all_tickers, "BND fetched from yfinance — must come from Excel"
    assert "HYG" not in all_tickers, "HYG fetched from yfinance — must come from Excel"
