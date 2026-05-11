"""
Sprint 2 — backtester unit tests.
External fetch functions (fetch_equity_data, fetch_risk_free_rate) are mocked.
Tests cover: BENCHMARK returns correct structure, no-lookahead assertion,
             weights-sum-to-1 assertion, transaction costs applied.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch, MagicMock

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

def make_prices(tickers: list[str], n: int = 504, seed: int = 42) -> pd.DataFrame:
    np.random.seed(seed)
    prices = np.cumprod(
        1 + np.random.normal(0.0004, 0.01, (n, len(tickers))), axis=0
    ) * 100.0
    df = pd.DataFrame(
        prices,
        index=pd.bdate_range("2019-01-01", periods=n),
        columns=tickers,
    )
    df.attrs["adjusted"] = True
    return df


def make_rf(n: int = 504, annual_rate: float = 0.04) -> pd.Series:
    return pd.Series(
        annual_rate / 252.0,
        index=pd.bdate_range("2019-01-01", periods=n),
        name="risk_free_rate",
    )


# ── verify_no_lookahead ────────────────────────────────────────────────────────

def test_no_lookahead_passes_for_t_minus_1():
    from tools.backtester import verify_no_lookahead
    signals = pd.date_range("2020-01-01", periods=5, freq="B")
    prices = pd.date_range("2020-01-02", periods=5, freq="B")
    verify_no_lookahead(signals, prices)  # Should not raise


def test_no_lookahead_raises_for_same_day():
    from tools.backtester import verify_no_lookahead
    same = pd.date_range("2020-01-01", periods=5, freq="B")
    with pytest.raises(AssertionError, match="Look-ahead bias"):
        verify_no_lookahead(same, same)


def test_no_lookahead_raises_for_future_signal():
    from tools.backtester import verify_no_lookahead
    prices = pd.date_range("2020-01-01", periods=5, freq="B")
    signals = pd.date_range("2020-01-02", periods=5, freq="B")  # signal after price
    with pytest.raises(AssertionError, match="Look-ahead bias"):
        verify_no_lookahead(signals, prices)


# ── run_benchmark (mocked) ────────────────────────────────────────────────────

def _patch_benchmark(monkeypatch, spy_prices: pd.DataFrame, rf: pd.Series):
    """Patch data fetcher functions used by run_benchmark."""
    monkeypatch.setattr("tools.backtester.fetch_equity_data", lambda tickers, start, end: spy_prices)
    monkeypatch.setattr("tools.backtester.fetch_risk_free_rate", lambda start, end: rf)


def test_run_benchmark_returns_dict(monkeypatch):
    spy = make_prices(["SPY"])
    rf = make_rf()
    _patch_benchmark(monkeypatch, spy, rf)
    from tools.backtester import run_benchmark
    result = run_benchmark("2019-01-01", "2020-12-31")
    assert isinstance(result, dict)


def test_run_benchmark_strategy_name(monkeypatch):
    spy = make_prices(["SPY"])
    rf = make_rf()
    _patch_benchmark(monkeypatch, spy, rf)
    from tools.backtester import run_benchmark
    result = run_benchmark("2019-01-01", "2020-12-31")
    assert result["strategy_name"] == "100% Equity (Benchmark)"


def test_run_benchmark_strategy_type_static(monkeypatch):
    spy = make_prices(["SPY"])
    rf = make_rf()
    _patch_benchmark(monkeypatch, spy, rf)
    from tools.backtester import run_benchmark
    result = run_benchmark("2019-01-01", "2020-12-31")
    assert result["strategy_type"] == "static"


def test_run_benchmark_has_sharpe_ratio(monkeypatch):
    spy = make_prices(["SPY"])
    rf = make_rf()
    _patch_benchmark(monkeypatch, spy, rf)
    from tools.backtester import run_benchmark
    result = run_benchmark("2019-01-01", "2020-12-31")
    assert "sharpe_ratio" in result
    assert isinstance(result["sharpe_ratio"], float)


def test_run_benchmark_max_drawdown_negative(monkeypatch):
    spy = make_prices(["SPY"])
    rf = make_rf()
    _patch_benchmark(monkeypatch, spy, rf)
    from tools.backtester import run_benchmark
    result = run_benchmark("2019-01-01", "2020-12-31")
    assert result["max_drawdown"] <= 0


def test_run_benchmark_no_transaction_costs(monkeypatch):
    spy = make_prices(["SPY"])
    rf = make_rf()
    _patch_benchmark(monkeypatch, spy, rf)
    from tools.backtester import run_benchmark
    result = run_benchmark("2019-01-01", "2020-12-31")
    assert result["avg_monthly_turnover"] == 0.0


def test_run_benchmark_full_equity_weight(monkeypatch):
    spy = make_prices(["SPY"])
    rf = make_rf()
    _patch_benchmark(monkeypatch, spy, rf)
    from tools.backtester import run_benchmark
    result = run_benchmark("2019-01-01", "2020-12-31")
    assert result["avg_equity_weight"] == 1.0
    assert result["avg_bond_weight"] == 0.0


def test_run_benchmark_n_observations_positive(monkeypatch):
    spy = make_prices(["SPY"])
    rf = make_rf()
    _patch_benchmark(monkeypatch, spy, rf)
    from tools.backtester import run_benchmark
    result = run_benchmark("2019-01-01", "2020-12-31")
    assert result["n_observations"] > 0


def test_run_benchmark_is_not_significant(monkeypatch):
    spy = make_prices(["SPY"])
    rf = make_rf()
    _patch_benchmark(monkeypatch, spy, rf)
    from tools.backtester import run_benchmark
    result = run_benchmark("2019-01-01", "2020-12-31")
    assert result["is_significant"] is False


def test_run_benchmark_has_var_95(monkeypatch):
    spy = make_prices(["SPY"])
    rf = make_rf()
    _patch_benchmark(monkeypatch, spy, rf)
    from tools.backtester import run_benchmark
    result = run_benchmark("2019-01-01", "2020-12-31")
    assert "var_95" in result
    assert result["var_95"] < 0


def test_run_benchmark_has_cvar_95(monkeypatch):
    spy = make_prices(["SPY"])
    rf = make_rf()
    _patch_benchmark(monkeypatch, spy, rf)
    from tools.backtester import run_benchmark
    result = run_benchmark("2019-01-01", "2020-12-31")
    assert "cvar_95" in result
    assert result["cvar_95"] <= result["var_95"]


# ── Weight validation ─────────────────────────────────────────────────────────

def test_validate_weights_passes_for_valid():
    from tools.backtester import _validate_weights
    _validate_weights({"SPY": 0.6, "TLT": 0.4}, "test")


def test_validate_weights_fails_for_non_unity():
    from tools.backtester import _validate_weights
    with pytest.raises(AssertionError, match="sum to 1"):
        _validate_weights({"SPY": 0.5, "TLT": 0.4}, "test")


def test_validate_weights_fails_for_short():
    from tools.backtester import _validate_weights
    with pytest.raises(AssertionError, match="short"):
        _validate_weights({"SPY": 1.2, "TLT": -0.2}, "test")
