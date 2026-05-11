"""
Sprint 2 — data fetcher unit tests.
All external API calls (yfinance, pandas_datareader) are mocked.
Tests cover: fetch, cache hit/miss, fallback, validation.
"""
from __future__ import annotations

import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

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


# ── Synthetic data helpers ────────────────────────────────────────────────────

def make_price_df(tickers: list[str], n: int = 252, seed: int = 42) -> pd.DataFrame:
    np.random.seed(seed)
    prices = np.cumprod(
        1 + np.random.normal(0.0004, 0.01, (n, len(tickers))), axis=0
    ) * 100.0
    df = pd.DataFrame(
        prices,
        index=pd.bdate_range("2020-01-01", periods=n),
        columns=tickers,
    )
    df.attrs["adjusted"] = True
    return df


def make_fred_series(n: int = 252, value: float = 5.0) -> pd.Series:
    """Constant FRED series (e.g., Fed Funds Rate = 5%)."""
    return pd.Series(
        value,
        index=pd.bdate_range("2020-01-01", periods=n),
        name="DFF",
    )


# ── fetch_equity_data ─────────────────────────────────────────────────────────
# Patch _yfinance_fetch (the internal normalised fetcher) rather than yfinance.download
# so tests don't depend on yfinance's ever-changing output format.

def test_fetch_equity_data_calls_yfinance(tmp_path, monkeypatch):
    synth = make_price_df(["SPY"])
    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr("tools.data_fetcher._yfinance_fetch", lambda t, s, e: synth)
    from tools.data_fetcher import fetch_equity_data
    df = fetch_equity_data(["SPY"], "2020-01-01", "2021-01-01")
    assert "SPY" in df.columns


def test_fetch_equity_data_returns_dataframe(tmp_path, monkeypatch):
    synth = make_price_df(["SPY", "QQQ"])
    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr("tools.data_fetcher._yfinance_fetch", lambda t, s, e: synth)
    from tools.data_fetcher import fetch_equity_data
    df = fetch_equity_data(["SPY", "QQQ"], "2020-01-01", "2021-01-01")
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0


def test_fetch_equity_data_sets_adjusted_attr(tmp_path, monkeypatch):
    synth = make_price_df(["SPY"])
    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr("tools.data_fetcher._yfinance_fetch", lambda t, s, e: synth)
    from tools.data_fetcher import fetch_equity_data
    df = fetch_equity_data(["SPY"], "2020-01-01", "2021-01-01")
    assert df.attrs.get("adjusted") is True


def test_fetch_equity_data_cache_hit_skips_yfinance(tmp_path, monkeypatch):
    synth = make_price_df(["SPY"])
    call_count = {"n": 0}

    def counting_fetch(t, s, e):
        call_count["n"] += 1
        return synth

    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr("tools.data_fetcher._yfinance_fetch", counting_fetch)
    from tools.data_fetcher import fetch_equity_data

    fetch_equity_data(["SPY"], "2020-01-01", "2021-01-01")
    assert call_count["n"] == 1

    # Second call: cache should be hit, no second yfinance call
    fetch_equity_data(["SPY"], "2020-01-01", "2021-01-01")
    assert call_count["n"] == 1


def test_fetch_equity_data_positive_prices(tmp_path, monkeypatch):
    synth = make_price_df(["SPY"])
    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr("tools.data_fetcher._yfinance_fetch", lambda t, s, e: synth)
    from tools.data_fetcher import fetch_equity_data
    df = fetch_equity_data(["SPY"], "2020-01-01", "2021-01-01")
    assert (df > 0).all().all()


# ── fetch_fred_series ─────────────────────────────────────────────────────────

def test_fetch_fred_series_returns_series(tmp_path, monkeypatch):
    fred_df = make_fred_series().to_frame("DFF")
    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr("tools.data_fetcher._fred_fetch", lambda sid, s, e: fred_df)
    from tools.data_fetcher import fetch_fred_series
    s = fetch_fred_series("DFF", "2020-01-01", "2021-01-01")
    assert isinstance(s, pd.Series)
    assert len(s) > 0


def test_fetch_fred_series_cache_hit(tmp_path, monkeypatch):
    fred_df = make_fred_series().to_frame("DFF")
    call_count = {"n": 0}

    def counting_fred(sid, s, e):
        call_count["n"] += 1
        return fred_df

    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr("tools.data_fetcher._fred_fetch", counting_fred)
    from tools.data_fetcher import fetch_fred_series
    fetch_fred_series("DFF", "2020-01-01", "2021-01-01")
    assert call_count["n"] == 1
    fetch_fred_series("DFF", "2020-01-01", "2021-01-01")
    assert call_count["n"] == 1  # Cache hit


# ── fetch_risk_free_rate ──────────────────────────────────────────────────────

def test_fetch_risk_free_rate_returns_daily_decimal(tmp_path, monkeypatch):
    fred_df = make_fred_series(value=5.0).to_frame("DFF")
    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr("tools.data_fetcher._fred_fetch", lambda sid, s, e: fred_df)
    from tools.data_fetcher import fetch_risk_free_rate
    rf = fetch_risk_free_rate("2020-01-01", "2021-01-01")
    assert isinstance(rf, pd.Series)
    # 5% / 252 ≈ 0.0001984
    assert abs(rf.mean() - 5.0 / 100.0 / 252.0) < 1e-6


def test_fetch_risk_free_rate_falls_back_on_error(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.data_fetcher._CACHE_PATH", tmp_path)
    monkeypatch.setattr(
        "tools.data_fetcher._fred_fetch",
        lambda sid, s, e: (_ for _ in ()).throw(Exception("FRED unavailable")),
    )
    from tools.data_fetcher import fetch_risk_free_rate
    from config import RISK_FREE_RATE_FALLBACK
    rf = fetch_risk_free_rate("2020-01-01", "2021-01-01")
    assert isinstance(rf, pd.Series)
    assert len(rf) > 0
    assert abs(rf.mean() - RISK_FREE_RATE_FALLBACK / 252.0) < 1e-9


# ── validate_data ─────────────────────────────────────────────────────────────

def test_validate_data_valid_passes():
    from tools.data_fetcher import validate_data
    df = make_price_df(["SPY", "TLT"])
    result = validate_data(df)
    assert result.is_valid is True
    assert result.n_assets == 2
    assert result.n_rows == 252


def test_validate_data_nan_gap_flagged():
    from tools.data_fetcher import validate_data
    df = make_price_df(["SPY"])
    # Inject a 6-day NaN run
    df.iloc[10:16, 0] = np.nan
    result = validate_data(df)
    assert result.is_valid is False
    assert any("NaN gap" in issue for issue in result.issues)


def test_validate_data_short_nan_gap_ok():
    from tools.data_fetcher import validate_data
    df = make_price_df(["SPY"])
    # 5-day gap is the maximum allowed
    df.iloc[10:15, 0] = np.nan
    result = validate_data(df)
    # Should not flag a gap of exactly 5
    assert not any("NaN gap" in issue for issue in result.issues)


def test_validate_data_negative_price_flagged():
    from tools.data_fetcher import validate_data
    df = make_price_df(["SPY"])
    df.iloc[5, 0] = -1.0
    result = validate_data(df)
    assert result.is_valid is False
    assert any("non-positive" in issue for issue in result.issues)


def test_validate_data_date_range_populated():
    from tools.data_fetcher import validate_data
    df = make_price_df(["SPY"])
    result = validate_data(df)
    assert result.date_range[0] != ""
    assert result.date_range[1] != ""
