"""
tests/test_chart_data.py

Smoke tests for backend/tools/chart_data.py — the module that powers the
six Statistical Evidence charts and six Regime Analysis charts via the
/api/v1/charts/data endpoint.

Synthetic input only; no network calls. Verifies output schema, not
the financial correctness of individual statistics (those are covered
by the existing CPCV / cross-validation tests).
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
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


def _make_history(n_months: int = 240, seed: int = 42) -> dict:
    """Synthetic monthly history covering 20 years for chart_data input."""
    np.random.seed(seed)
    idx = pd.date_range("2005-01-31", periods=n_months, freq="ME")
    equity = pd.Series(np.random.normal(0.008, 0.04, n_months), index=idx)
    ig = pd.Series(np.random.normal(0.003, 0.015, n_months), index=idx)
    rf = pd.Series(0.003, index=idx)
    # FF factors: monthly index aligned with equity
    ff = pd.DataFrame(
        {
            "Mkt-RF": np.random.normal(0.005, 0.04, n_months),
            "SMB":    np.random.normal(0.0, 0.025, n_months),
            "HML":    np.random.normal(0.0, 0.025, n_months),
            "RF":     0.003,
        },
        index=idx,
    )
    # Synthetic VIX/yield_curve daily signals — chart_data resamples to monthly
    daily_idx = pd.date_range("2005-01-01", periods=n_months * 21, freq="B")
    vix = pd.Series(18.0 + np.random.normal(0, 4, len(daily_idx)), index=daily_idx)
    yc = pd.Series(1.0 + np.random.normal(0, 0.3, len(daily_idx)), index=daily_idx)

    return {
        "equity_monthly":    equity,
        "ig_monthly":        ig,
        "risk_free_monthly": rf,
        "ff_factors":        ff,
        "signals":           {"vix": vix, "yield_curve": yc},
    }


def _make_results(history: dict) -> dict:
    """Two synthetic strategies with monthly_returns pairs from the history."""
    eq = history["equity_monthly"]
    ig = history["ig_monthly"]

    def pairs(s: pd.Series) -> list[list]:
        return [[str(idx.date()), round(float(val), 6)] for idx, val in s.dropna().items()]

    return {
        "BENCHMARK": {
            "strategy_name": "BENCHMARK",
            "monthly_returns": pairs(eq),
            "sharpe_ratio": 0.5, "oos_sharpe": 0.45,
            "cv_stability_score": 0.55, "p_value_corrected": 0.05,
            "oos_p_value": 0.10,
            "avg_equity_weight": 1.0, "avg_bond_weight": 0.0,
        },
        "CLASSIC_60_40": {
            "strategy_name": "CLASSIC_60_40",
            "monthly_returns": pairs(0.6 * eq + 0.4 * ig),
            "sharpe_ratio": 0.7, "oos_sharpe": 0.6,
            "cv_stability_score": 0.72, "p_value_corrected": 0.001,
            "oos_p_value": 0.02,
            "avg_equity_weight": 0.6, "avg_bond_weight": 0.4,
        },
    }


class TestChartDataPayloadShape:
    """Validates the public contract returned by compute_chart_data."""

    def test_returns_all_required_keys(self):
        from tools.chart_data import compute_chart_data
        out = compute_chart_data(_make_history(), _make_results(_make_history()))
        expected = {
            "cpcv", "cv_radar", "walk_forward", "regime_conditional",
            "regime_timeline", "correlation_breakdown", "factor_loadings",
            "attribution", "transition_matrix", "n_strategies", "n_months",
        }
        missing = expected - set(out.keys())
        assert not missing, f"chart_data payload missing keys: {missing}"

    def test_n_strategies_matches_input(self):
        from tools.chart_data import compute_chart_data
        hist = _make_history()
        out = compute_chart_data(hist, _make_results(hist))
        assert out["n_strategies"] == 2
        assert out["n_months"] == 240

    def test_per_strategy_dicts_populated_for_each_input_strategy(self):
        from tools.chart_data import compute_chart_data
        hist = _make_history()
        out = compute_chart_data(hist, _make_results(hist))
        for key in ("cpcv", "cv_radar", "walk_forward", "regime_conditional",
                    "factor_loadings", "attribution"):
            assert "BENCHMARK" in out[key], f"BENCHMARK missing from {key}"
            assert "CLASSIC_60_40" in out[key], f"CLASSIC_60_40 missing from {key}"


class TestCPCVOutput:
    def test_cpcv_emits_8_path_distribution(self):
        from tools.chart_data import compute_chart_data
        hist = _make_history()
        out = compute_chart_data(hist, _make_results(hist))
        bench_cpcv = out["cpcv"]["BENCHMARK"]
        assert bench_cpcv["n_paths"] == 8
        # Statistics must be ordered: min <= q1 <= median <= q3 <= max
        assert bench_cpcv["sharpe_min"] <= bench_cpcv["sharpe_q1"]
        assert bench_cpcv["sharpe_q1"] <= bench_cpcv["sharpe_median"]
        assert bench_cpcv["sharpe_median"] <= bench_cpcv["sharpe_q3"]
        assert bench_cpcv["sharpe_q3"] <= bench_cpcv["sharpe_max"]


class TestCVRadar:
    def test_radar_values_all_in_unit_interval(self):
        from tools.chart_data import compute_chart_data
        hist = _make_history()
        out = compute_chart_data(hist, _make_results(hist))
        for name, point in out["cv_radar"].items():
            for axis, v in point.items():
                assert 0.0 <= v <= 1.0, f"{name}.{axis} = {v} out of [0,1]"


class TestRegimeTimeline:
    def test_timeline_one_entry_per_month(self):
        from tools.chart_data import compute_chart_data
        hist = _make_history()
        out = compute_chart_data(hist, _make_results(hist))
        assert len(out["regime_timeline"]) == 240

    def test_timeline_only_emits_valid_regimes(self):
        from tools.chart_data import compute_chart_data
        hist = _make_history()
        out = compute_chart_data(hist, _make_results(hist))
        valid = {"BULL", "BEAR", "TRANSITION"}
        for entry in out["regime_timeline"]:
            assert entry["regime"] in valid


class TestTransitionMatrix:
    def test_rows_sum_to_approximately_one(self):
        from tools.chart_data import compute_chart_data
        hist = _make_history()
        out = compute_chart_data(hist, _make_results(hist))
        matrix = out["transition_matrix"]
        for from_state, transitions in matrix.items():
            row_sum = sum(transitions.values())
            # All-zero rows are valid (regime never visited); otherwise must sum to 1
            assert row_sum == 0.0 or abs(row_sum - 1.0) < 0.01, (
                f"transition matrix row {from_state} sums to {row_sum}"
            )


class TestCorrelationBreakdown:
    def test_correlation_values_in_valid_range(self):
        from tools.chart_data import compute_chart_data
        hist = _make_history()
        out = compute_chart_data(hist, _make_results(hist))
        for point in out["correlation_breakdown"]:
            assert -1.0 <= point["rolling_12m"] <= 1.0


class TestFactorLoadings:
    def test_r_squared_in_unit_interval(self):
        from tools.chart_data import compute_chart_data
        hist = _make_history()
        out = compute_chart_data(hist, _make_results(hist))
        for name, l in out["factor_loadings"].items():
            assert 0.0 <= l["r_squared"] <= 1.0, f"{name} R² = {l['r_squared']}"
            assert l["n_obs"] > 0

    def test_factor_loadings_include_required_keys(self):
        from tools.chart_data import compute_chart_data
        hist = _make_history()
        out = compute_chart_data(hist, _make_results(hist))
        required = {"mkt_rf", "smb", "hml", "alpha", "r_squared", "n_obs"}
        for name, l in out["factor_loadings"].items():
            missing = required - set(l.keys())
            assert not missing, f"{name} factor_loadings missing: {missing}"


class TestWalkForwardWindows:
    def test_walk_forward_emits_increasing_window_ends(self):
        from tools.chart_data import compute_chart_data
        hist = _make_history()
        out = compute_chart_data(hist, _make_results(hist))
        for name, windows in out["walk_forward"].items():
            dates = [w["window_end"] for w in windows]
            assert dates == sorted(dates), f"{name} walk-forward dates not sorted"


class TestRegimeConditional:
    def test_each_regime_present_per_strategy(self):
        from tools.chart_data import compute_chart_data
        hist = _make_history()
        out = compute_chart_data(hist, _make_results(hist))
        for name, regimes in out["regime_conditional"].items():
            for r in ("BULL", "BEAR", "TRANSITION"):
                assert r in regimes, f"{name} missing regime {r}"
                assert "sharpe" in regimes[r]
                assert "n_months" in regimes[r]


class TestEmptyInputs:
    def test_empty_results_dict_returns_empty_per_strategy_payloads(self):
        from tools.chart_data import compute_chart_data
        out = compute_chart_data(_make_history(), {})
        assert out["cpcv"] == {}
        assert out["cv_radar"] == {}
        assert out["factor_loadings"] == {}
        assert out["attribution"] == {}

    def test_missing_ff_factors_returns_zero_loadings_with_zero_r_squared(self):
        from tools.chart_data import compute_chart_data
        hist = _make_history()
        hist["ff_factors"] = None
        out = compute_chart_data(hist, _make_results(hist))
        for name, l in out["factor_loadings"].items():
            assert l["r_squared"] == 0.0
            assert l["n_obs"] == 0
