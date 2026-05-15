"""
Sprint 3 remediation — backtester unit tests.
All strategy functions now accept a pre-loaded history dict — no data fetching
inside the backtester. Tests pass a synthetic history dict directly.

Tests cover:
  - verify_no_lookahead: same-day and future-signal assertions
  - _validate_weights: sum-to-1 and no-short-position assertions
  - run_benchmark: structural and numerical contract for the BENCHMARK strategy
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


# ── Synthetic history dict ────────────────────────────────────────────────────

def make_history(n_months: int = 60, seed: int = 42) -> dict:
    """
    Minimal synthetic history dict matching the structure produced by
    get_full_history(). Used to test backtester strategies in isolation
    without any network calls or Excel file dependency.

    60 months (5 years) is sufficient to produce non-trivial drawdowns and
    cover at least one full quarterly rebalance cycle for every strategy.
    Monthly returns are seeded for reproducibility.
    """
    np.random.seed(seed)
    idx_m = pd.date_range("2020-01-31", periods=n_months, freq="ME")
    n_daily = n_months * 21
    idx_d = pd.bdate_range("2020-01-01", periods=n_daily)

    equity_monthly = pd.Series(np.random.normal(0.008, 0.04, n_months), index=idx_m)
    ig_monthly = pd.Series(np.random.normal(0.003, 0.015, n_months), index=idx_m)
    hy_monthly = pd.Series(np.random.normal(0.005, 0.025, n_months), index=idx_m)
    # Monthly risk-free: ~5% annualised → ~0.41% per month
    rf_monthly = pd.Series(0.0041, index=idx_m)

    equity_daily = pd.Series(np.random.normal(0.0003, 0.012, n_daily), index=idx_d)
    ig_daily = pd.Series(np.random.normal(0.0001, 0.005, n_daily), index=idx_d)
    hy_daily = pd.Series(np.random.normal(0.0002, 0.008, n_daily), index=idx_d)
    rf_daily = pd.Series(0.045 / 252.0, index=idx_d)

    # VIX signal: values between 15 and 30
    vix = pd.Series(np.random.uniform(15, 30, n_daily), index=idx_d)

    return {
        "equity_monthly": equity_monthly,
        "ig_monthly": ig_monthly,
        "hy_monthly": hy_monthly,
        "risk_free_monthly": rf_monthly,
        "equity_daily": equity_daily,
        "ig_daily": ig_daily,
        "hy_daily": hy_daily,
        "risk_free_daily": rf_daily,
        "signals": {"vix": vix},
        "ff_factors": pd.DataFrame(
            {
                "Mkt-RF": np.random.normal(0.005, 0.04, n_months),
                "SMB": np.random.normal(0.001, 0.02, n_months),
                "HML": np.random.normal(0.001, 0.02, n_months),
                "RF": np.full(n_months, 0.0004),
            },
            index=idx_m,
        ),
    }


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


# ── run_benchmark ─────────────────────────────────────────────────────────────

def test_run_benchmark_returns_dict():
    from tools.backtester import run_benchmark
    result = run_benchmark(make_history())
    assert isinstance(result, dict)


def test_run_benchmark_strategy_name():
    from tools.backtester import run_benchmark
    result = run_benchmark(make_history())
    assert result["strategy_name"] == "100% Equity (Benchmark)"


def test_run_benchmark_strategy_type_static():
    from tools.backtester import run_benchmark
    result = run_benchmark(make_history())
    assert result["strategy_type"] == "static"


def test_run_benchmark_has_sharpe_ratio():
    from tools.backtester import run_benchmark
    result = run_benchmark(make_history())
    assert "sharpe_ratio" in result
    assert isinstance(result["sharpe_ratio"], float)


def test_run_benchmark_max_drawdown_negative():
    from tools.backtester import run_benchmark
    result = run_benchmark(make_history())
    # 60 months of normal returns will always produce at least one drawdown
    assert result["max_drawdown"] <= 0


def test_run_benchmark_no_transaction_costs():
    from tools.backtester import run_benchmark
    result = run_benchmark(make_history())
    # Benchmark is buy-and-hold with no rebalancing — zero turnover
    assert result["avg_monthly_turnover"] == 0.0


def test_run_benchmark_full_equity_weight():
    from tools.backtester import run_benchmark
    result = run_benchmark(make_history())
    assert result["avg_equity_weight"] == 1.0
    assert result["avg_bond_weight"] == 0.0


def test_run_benchmark_n_observations_positive():
    from tools.backtester import run_benchmark
    result = run_benchmark(make_history())
    assert result["n_observations"] > 0


def test_run_benchmark_is_not_significant():
    from tools.backtester import run_benchmark
    result = run_benchmark(make_history())
    # Benchmark is the reference — significance is undefined for the baseline itself
    assert result["is_significant"] is False


def test_run_benchmark_has_var_95():
    from tools.backtester import run_benchmark
    result = run_benchmark(make_history())
    assert "var_95" in result
    # With normal monthly returns (std=4%), the 5th percentile is negative
    assert result["var_95"] < 0


def test_run_benchmark_has_cvar_95():
    from tools.backtester import run_benchmark
    result = run_benchmark(make_history())
    assert "cvar_95" in result
    # CVaR (expected shortfall) must be at least as bad as VaR
    assert result["cvar_95"] <= result["var_95"]


def test_run_benchmark_includes_monthly_returns():
    """Regression: BENCHMARK previously omitted monthly_returns, which
    tools/chart_data.compute_chart_data reads as the universal reference
    series for active-return decomposition. Without it bm_returns came
    in empty and every per-strategy attribution returned the zero dict
    via the < 12 obs early-return guard → blank Performance Attribution
    Waterfall on every request.

    Pin both the field's presence AND the [iso_date, float] pair shape
    that _build_result (the helper for the other 9 strategies) uses.
    A drift in either side would put BENCHMARK back out of sync with
    the rest of the JSONB cache payload."""
    from tools.backtester import run_benchmark
    result = run_benchmark(make_history())

    assert "monthly_returns" in result, (
        "BENCHMARK result must include monthly_returns so the chart-data "
        "endpoint can use it as the universal active-return reference"
    )
    pairs = result["monthly_returns"]
    assert isinstance(pairs, list)
    assert len(pairs) > 0
    # Shape contract: each entry is [iso_date_str, return_float]. Catches
    # accidental Series→dict serialisation or tuple-instead-of-list.
    for entry in pairs[:3]:
        assert isinstance(entry, list) and len(entry) == 2
        assert isinstance(entry[0], str)
        assert isinstance(entry[1], (int, float))
    # The number of monthly returns must match n_observations — if these
    # ever diverge it means one of them is computed from a filtered
    # series and the other isn't, and the chart-data downstream will
    # produce subtly wrong attribution.
    assert len(pairs) == result["n_observations"]


# ── Weight validation ─────────────────────────────────────────────────────────

def test_validate_weights_passes_for_valid():
    from tools.backtester import _validate_weights
    _validate_weights({"equity": 0.6, "ig": 0.4}, "test")


def test_validate_weights_fails_for_non_unity():
    from tools.backtester import _validate_weights
    with pytest.raises(AssertionError, match="sum to 1"):
        _validate_weights({"equity": 0.5, "ig": 0.4}, "test")


def test_validate_weights_fails_for_short():
    from tools.backtester import _validate_weights
    with pytest.raises(AssertionError, match="short"):
        _validate_weights({"equity": 1.2, "ig": -0.2}, "test")
