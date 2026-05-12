"""
Sprint 3 addendum — benchmark plausibility and implausibility guard tests.

Two categories:
  Historical range checks (requires Excel): BENCHMARK CAGR, Sharpe, max drawdown,
    and specific year returns must fall within known historical ranges.  A value
    outside these ranges indicates a data loading error, wrong return type
    (price vs total return), or arithmetic defect.

  Implausibility guards (no Excel required): run_all_strategies on synthetic
    data must not produce Sharpe > 2.0, CAGR > 20%, negative max_drawdown,
    or any NaN/Inf values.  These guards catch catastrophic errors like
    accidental look-ahead or division-by-zero before they reach presentation.
"""
from __future__ import annotations

import math
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
            "FNA_670_Project_Sources.xlsx not present — skipping plausibility tests"
        )


def _make_history(n_months: int = 60, seed: int = 42) -> dict:
    """Synthetic history — identical to make_history() in test_numerical_accuracy.py."""
    np.random.seed(seed)
    idx_m = pd.date_range("2020-01-31", periods=n_months, freq="ME")
    n_daily = n_months * 21
    idx_d = pd.bdate_range("2020-01-01", periods=n_daily)

    equity_monthly = pd.Series(np.random.normal(0.008, 0.04, n_months), index=idx_m)
    ig_monthly = pd.Series(np.random.normal(0.003, 0.015, n_months), index=idx_m)
    hy_monthly = pd.Series(np.random.normal(0.005, 0.025, n_months), index=idx_m)
    rf_monthly = pd.Series(0.000407, index=idx_m)

    equity_daily = pd.Series(np.random.normal(0.0003, 0.012, n_daily), index=idx_d)
    ig_daily = pd.Series(np.random.normal(0.0001, 0.005, n_daily), index=idx_d)
    hy_daily = pd.Series(np.random.normal(0.0002, 0.008, n_daily), index=idx_d)
    rf_daily = pd.Series(0.05 / 252.0, index=idx_d)
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
                "RF": np.full(n_months, 0.000407),
            },
            index=idx_m,
        ),
    }


# ── HISTORICAL RANGE CHECKS (requires Excel) ──────────────────────────────────

def test_benchmark_cagr_2002_2025_plausible():
    """
    BENCHMARK (100% S&P 500) 2002–2025 CAGR must fall between 7% and 11%.
    The S&P 500's long-run historical CAGR is approximately 9-10% (with dividends).
    A CAGR outside this range would indicate the equity return series is wrong —
    either price-only (missing dividends) or loaded incorrectly.
    """
    _skip_if_no_excel()
    from tools.backtester import run_benchmark
    from tools.data_fetcher import get_full_history

    history = get_full_history()
    result = run_benchmark(history)

    cagr = result.get("cagr", float("nan"))
    assert 0.07 <= cagr <= 0.11, (
        f"BENCHMARK CAGR = {cagr:.2%}; expected 7%–11% for S&P 500 2002–2025. "
        "Outside this range indicates a data loading or return calculation error."
    )


def test_benchmark_sharpe_2002_2025_plausible():
    """
    BENCHMARK Sharpe ratio must fall between 0.35 and 0.75.
    The historical risk-adjusted return of the S&P 500 over a full market cycle
    is well-established in the academic literature (Sharpe 1994).  Values outside
    0.35–0.75 indicate wrong risk-free rate, wrong volatility, or wrong return series.
    """
    _skip_if_no_excel()
    from tools.backtester import run_benchmark
    from tools.data_fetcher import get_full_history

    history = get_full_history()
    result = run_benchmark(history)

    sharpe = result.get("sharpe_ratio", float("nan"))
    assert 0.35 <= sharpe <= 0.75, (
        f"BENCHMARK Sharpe = {sharpe:.3f}; expected 0.35–0.75 over 2002–2025. "
        "Outside range suggests wrong risk-free rate or return calculation."
    )


def test_benchmark_max_drawdown_2002_2025_plausible():
    """
    BENCHMARK max drawdown must fall between -45% and -58%.
    The S&P 500 fell approximately -50.8% peak-to-trough during the 2008 GFC.
    This is the dominant drawdown in the 2002–2025 period and sets the expected range.
    A drawdown shallower than -45% means the 2008 crash is not in the dataset.
    A drawdown deeper than -58% would imply a data error.
    """
    _skip_if_no_excel()
    from tools.backtester import run_benchmark
    from tools.data_fetcher import get_full_history

    history = get_full_history()
    result = run_benchmark(history)

    max_dd = result.get("max_drawdown", float("nan"))
    assert -0.58 <= max_dd <= -0.45, (
        f"BENCHMARK max_drawdown = {max_dd:.2%}; expected -45% to -58%. "
        "GFC 2008 S&P 500 drawdown was approximately -50.8%."
    )


def test_benchmark_2022_return_negative():
    """
    2022 was the worst year for equities since 2008, with the S&P 500 returning
    approximately -18% to -22%.  If BENCHMARK's 2022 return is positive or
    shallower than -18%, the data does not include the 2022 rate hike drawdown —
    the central stress test of this project.
    """
    _skip_if_no_excel()
    from tools.data_fetcher import get_full_history

    history = get_full_history()
    eq_monthly = history.get("equity_monthly")
    if eq_monthly is None:
        pytest.skip("equity_monthly not available in history")

    returns_2022 = eq_monthly[eq_monthly.index.year == 2022]
    if len(returns_2022) == 0:
        pytest.skip("No 2022 data in equity_monthly")

    annual_return_2022 = float((1 + returns_2022).prod() - 1)
    assert -0.22 <= annual_return_2022 <= -0.18, (
        f"2022 equity return = {annual_return_2022:.2%}; expected -18% to -22%."
    )


def test_benchmark_2009_return_positive():
    """
    2009 was the strongest recovery year following the GFC, with the S&P 500
    returning approximately +20% to +30%.  A 2009 return outside this range
    indicates the recovery rally is missing or the data starts after 2009.
    """
    _skip_if_no_excel()
    from tools.data_fetcher import get_full_history

    history = get_full_history()
    eq_monthly = history.get("equity_monthly")
    if eq_monthly is None:
        pytest.skip("equity_monthly not available in history")

    returns_2009 = eq_monthly[eq_monthly.index.year == 2009]
    if len(returns_2009) == 0:
        pytest.skip("No 2009 data in equity_monthly")

    annual_return_2009 = float((1 + returns_2009).prod() - 1)
    assert 0.20 <= annual_return_2009 <= 0.30, (
        f"2009 equity return = {annual_return_2009:.2%}; expected +20% to +30%."
    )


# ── IMPLAUSIBILITY GUARDS (no Excel required) ─────────────────────────────────
# These run on synthetic data and catch catastrophic arithmetic errors.

class TestImplausibilityGuards:
    """
    No strategy running on synthetic data should produce:
      - Sharpe > 2.0  (would indicate look-ahead or arithmetic error)
      - CAGR > 20%    (implausible for long-only 3-asset diversified portfolio)
      - max_drawdown > 0  (drawdown must always be ≤ 0)
      - Any Inf or NaN in the core metrics

    These guards catch errors that compound before the presentation —
    a single wrong calculation that produces Sharpe=15 would be immediately
    visible to Forest Capital and destroy credibility.
    """

    def setup_method(self):
        from tools.backtester import run_all_strategies
        self.results = run_all_strategies(_make_history())

    @pytest.mark.parametrize("strategy_key", [
        "BENCHMARK", "CLASSIC_60_40", "RISK_PARITY", "MIN_VARIANCE",
        "EQUAL_WEIGHT", "MOMENTUM_ROTATION", "REGIME_SWITCHING",
        "VOL_TARGETING", "BLACK_LITTERMAN", "MAX_SHARPE_ROLLING",
    ])
    def test_sharpe_not_implausibly_high(self, strategy_key: str):
        """No look-ahead or arithmetic error produces Sharpe > 2.0."""
        r = self.results[strategy_key]
        sharpe = r.get("sharpe_ratio", float("nan"))
        assert not math.isnan(sharpe), f"{strategy_key}: sharpe_ratio is NaN"
        assert not math.isinf(sharpe), f"{strategy_key}: sharpe_ratio is Inf"
        assert sharpe <= 2.0, (
            f"{strategy_key}: Sharpe = {sharpe:.4f} > 2.0. "
            "This is implausible for a monthly diversified strategy — "
            "check for look-ahead bias or return calculation error."
        )

    @pytest.mark.parametrize("strategy_key", [
        "BENCHMARK", "CLASSIC_60_40", "RISK_PARITY", "MIN_VARIANCE",
        "EQUAL_WEIGHT", "MOMENTUM_ROTATION", "REGIME_SWITCHING",
        "VOL_TARGETING", "BLACK_LITTERMAN", "MAX_SHARPE_ROLLING",
    ])
    def test_cagr_not_implausibly_high(self, strategy_key: str):
        """No arithmetic error produces CAGR > 20% for a long-only 3-asset portfolio."""
        r = self.results[strategy_key]
        cagr = r.get("cagr", float("nan"))
        assert not math.isnan(cagr), f"{strategy_key}: cagr is NaN"
        assert not math.isinf(cagr), f"{strategy_key}: cagr is Inf"
        assert cagr <= 0.20, (
            f"{strategy_key}: CAGR = {cagr:.2%} > 20%. "
            "This is implausible for a long-only diversified portfolio."
        )

    @pytest.mark.parametrize("strategy_key", [
        "BENCHMARK", "CLASSIC_60_40", "RISK_PARITY", "MIN_VARIANCE",
        "EQUAL_WEIGHT", "MOMENTUM_ROTATION", "REGIME_SWITCHING",
        "VOL_TARGETING", "BLACK_LITTERMAN", "MAX_SHARPE_ROLLING",
    ])
    def test_max_drawdown_not_positive(self, strategy_key: str):
        """
        max_drawdown must be ≤ 0 for every strategy.
        A positive max_drawdown would mean the strategy never experienced a
        loss from its cumulative peak — impossible if there is any negative return.
        With synthetic data containing negative monthly returns, max_drawdown
        must be negative.
        """
        r = self.results[strategy_key]
        max_dd = r.get("max_drawdown", float("nan"))
        assert not math.isnan(max_dd), f"{strategy_key}: max_drawdown is NaN"
        assert max_dd <= 0, (
            f"{strategy_key}: max_drawdown = {max_dd:.4f} > 0. "
            "Drawdown must be ≤ 0 — a positive value indicates a calculation error."
        )

    @pytest.mark.parametrize("strategy_key", [
        "BENCHMARK", "CLASSIC_60_40", "RISK_PARITY", "MIN_VARIANCE",
        "EQUAL_WEIGHT", "MOMENTUM_ROTATION", "REGIME_SWITCHING",
        "VOL_TARGETING", "BLACK_LITTERMAN", "MAX_SHARPE_ROLLING",
    ])
    def test_all_core_metrics_are_finite(self, strategy_key: str):
        """
        sharpe_ratio, cagr, max_drawdown, volatility must all be finite floats.
        NaN or Inf in any metric means a division by zero, empty series, or
        other arithmetic failure that would propagate into every downstream
        statistical test and chart.
        """
        r = self.results[strategy_key]
        for metric in ("sharpe_ratio", "cagr", "max_drawdown", "volatility"):
            val = r.get(metric, None)
            if val is None:
                continue  # Metric not computed — skip rather than fail
            assert isinstance(val, (int, float)), (
                f"{strategy_key}.{metric} is not numeric: {type(val)}"
            )
            assert math.isfinite(val), (
                f"{strategy_key}.{metric} = {val} is not finite (NaN or Inf). "
                "A non-finite metric breaks all downstream calculations."
            )
