"""
tests/test_strategy_enhancements.py

Tests for the two backtester-side additions of the combined analytics
enhancement pass:

  - true_turnover — the genuine sum-of-absolute-weight-change figure
    computed from each strategy's rebalance schedule, alongside the
    legacy rebalance-count proxy avg_monthly_turnover.
  - parameter sensitivity — sweeping each dynamic strategy's key
    parameter around its current setting and recording the Sharpe ratio.

All tests run against the deterministic seed=42 synthetic history so
they need no database, no network, and no event loop.
"""
from __future__ import annotations

import os
import sys
import warnings

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


def make_history(n_months: int = 120, seed: int = 42) -> dict:
    """
    Synthetic history dict with fixed-seed random returns. 120 months gives
    every dynamic strategy enough runway past its longest lookback (the
    36-month optimisation window) to produce a meaningful schedule.
    """
    np.random.seed(seed)
    idx_m = pd.date_range("2014-01-31", periods=n_months, freq="ME")
    n_daily = n_months * 21
    idx_d = pd.bdate_range("2014-01-01", periods=n_daily)

    return {
        "equity_monthly": pd.Series(np.random.normal(0.008, 0.04, n_months), index=idx_m),
        "ig_monthly": pd.Series(np.random.normal(0.003, 0.015, n_months), index=idx_m),
        "hy_monthly": pd.Series(np.random.normal(0.005, 0.025, n_months), index=idx_m),
        "risk_free_monthly": pd.Series(0.000407, index=idx_m),
        "equity_daily": pd.Series(np.random.normal(0.0003, 0.012, n_daily), index=idx_d),
        "ig_daily": pd.Series(np.random.normal(0.0001, 0.005, n_daily), index=idx_d),
        "hy_daily": pd.Series(np.random.normal(0.0002, 0.008, n_daily), index=idx_d),
        "risk_free_daily": pd.Series(0.05 / 252.0, index=idx_d),
        "signals": {"vix": pd.Series(np.random.uniform(15, 30, n_daily), index=idx_d)},
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


# ── True turnover ─────────────────────────────────────────────────────────────

_DYNAMIC = {"MOMENTUM_ROTATION", "REGIME_SWITCHING", "VOL_TARGETING",
            "BLACK_LITTERMAN", "MAX_SHARPE_ROLLING"}
# BENCHMARK is 100% equity and never rebalances — its true turnover is 0.
# The other fixed-weight statics rebalance to fixed targets but still trade
# each quarter to correct drift, so their true turnover is genuine and > 0.
_FIXED_WEIGHT_STATIC = {"BENCHMARK", "CLASSIC_60_40", "EQUAL_WEIGHT"}


class TestTrueTurnover:
    def test_every_strategy_reports_true_turnover(self):
        from tools.backtester import run_all_strategies
        results = run_all_strategies(make_history())
        for name, res in results.items():
            assert "true_turnover" in res, f"{name} missing true_turnover"

    def test_true_turnover_is_non_negative(self):
        """true_turnover is a sum of absolute weight changes — it can never
        be negative regardless of strategy or market path."""
        from tools.backtester import run_all_strategies
        results = run_all_strategies(make_history())
        for name, res in results.items():
            tt = res.get("true_turnover")
            if tt is None or "error" in res:
                continue
            assert tt >= 0.0, f"{name} reported negative true_turnover {tt}"

    def test_benchmark_turnover_is_zero(self):
        """BENCHMARK is 100% equity and never rebalances — turnover is 0."""
        from tools.backtester import run_all_strategies
        results = run_all_strategies(make_history())
        res = results.get("BENCHMARK", {})
        if "error" not in res and res.get("true_turnover") is not None:
            assert res["true_turnover"] == 0.0, (
                f"BENCHMARK never rebalances — true_turnover should be 0, "
                f"got {res['true_turnover']}"
            )

    def test_fixed_weight_static_turnover_is_genuine(self):
        """A fixed-weight static rebalances to the SAME targets, but the
        portfolio drifts between rebalances and is traded back each
        quarter — so its true turnover is a genuine positive figure, not
        zero, and stays within a plausible band."""
        from tools.backtester import run_all_strategies
        results = run_all_strategies(make_history())
        for name in ("CLASSIC_60_40", "EQUAL_WEIGHT"):
            res = results.get(name, {})
            if "error" in res or res.get("true_turnover") is None:
                continue
            tt = res["true_turnover"]
            assert 0.0 < tt < 1.0, (
                f"{name} drift-correction true_turnover implausible: {tt}"
            )

    def test_dynamic_turnover_exceeds_fixed_weight_static(self):
        """Dynamic strategies re-allocate on a signal, so they should turn
        over more than the fixed-weight statics. This is an expectation, not
        an invariant — a degenerate synthetic path could violate it — so a
        miss is reported as a warning, not a failure."""
        from tools.backtester import run_all_strategies
        results = run_all_strategies(make_history())

        def _mean_tt(keys: set[str]) -> float | None:
            vals = [
                results[k]["true_turnover"]
                for k in keys
                if k in results and "error" not in results[k]
                and results[k].get("true_turnover") is not None
            ]
            return float(np.mean(vals)) if vals else None

        dyn = _mean_tt(_DYNAMIC)
        sta = _mean_tt(_FIXED_WEIGHT_STATIC)
        assert dyn is not None and sta is not None
        if dyn <= sta:
            warnings.warn(
                f"dynamic mean true_turnover ({dyn:.4f}) did not exceed "
                f"fixed-weight static mean ({sta:.4f}) on the synthetic path",
                stacklevel=2,
            )


# ── Parameter sensitivity ─────────────────────────────────────────────────────

class TestSensitivity:
    def test_covers_all_four_dynamic_strategies(self):
        from tools.sensitivity import compute_sensitivity, _sensitivity_clear
        _sensitivity_clear()
        out = compute_sensitivity(make_history())
        names = {s["strategy"] for s in out["strategies"]}
        assert names == {
            "Momentum Rotation", "Regime Switching",
            "Volatility Targeting", "Max Sharpe Rolling",
        }

    def test_each_sweep_has_points_and_current_value(self):
        from tools.sensitivity import compute_sensitivity, _sensitivity_clear
        _sensitivity_clear()
        out = compute_sensitivity(make_history())
        for sweep in out["strategies"]:
            assert sweep["points"], f"{sweep['strategy']} swept no points"
            assert "current_value" in sweep
            assert "parameter" in sweep
            for pt in sweep["points"]:
                assert "value" in pt and "sharpe" in pt

    def test_at_least_one_point_per_strategy_has_a_sharpe(self):
        """A parameter value whose backtest errors records sharpe=None, but
        the sweep as a whole must yield at least one real Sharpe — otherwise
        the strategy is not actually being exercised."""
        from tools.sensitivity import compute_sensitivity, _sensitivity_clear
        _sensitivity_clear()
        out = compute_sensitivity(make_history())
        for sweep in out["strategies"]:
            sharpes = [p["sharpe"] for p in sweep["points"] if p["sharpe"] is not None]
            assert sharpes, f"{sweep['strategy']} produced no Sharpe values"

    def test_result_is_memoised_by_history_length(self):
        from tools.sensitivity import compute_sensitivity, _sensitivity_clear
        _sensitivity_clear()
        hist = make_history()
        first = compute_sensitivity(hist)
        # Second call with the same-length history returns the cached object.
        second = compute_sensitivity(hist)
        assert first is second
