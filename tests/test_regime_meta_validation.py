"""Tests for tools/regime_meta_validation.py — Layer 3 out-of-sample
validation. Synthetic strategy_results + hmm_result so no hmmlearn / DB
is needed; the logic (train/test split, frozen-blend application,
baseline Sharpes, verdict) is exercised end to end."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")

import pandas as pd  # noqa: E402

from tools import regime_meta_validation as v  # noqa: E402


def _month_dates(n: int, start: str = "2017-01-31") -> list[str]:
    return [d.date().isoformat()
            for d in pd.date_range(start, periods=n, freq="ME")]


def _strategy_results(n_months: int = 96, seed: int = 7) -> dict[str, dict]:
    """Includes BENCHMARK and REGIME_SWITCHING so the baselines resolve
    from matrix columns, plus three more strategies for the blend."""
    rng = np.random.default_rng(seed)
    dates = _month_dates(n_months)
    profiles = {
        "BENCHMARK":         (0.006, 0.043),
        "REGIME_SWITCHING":  (0.006, 0.030),
        "MIN_VARIANCE":      (0.003, 0.016),
        "VOL_TARGETING":     (0.004, 0.018),
        "RISK_PARITY":       (0.004, 0.022),
    }
    out: dict[str, dict] = {}
    for name, (mean, vol) in profiles.items():
        rets = rng.normal(mean, vol, n_months)
        out[name] = {"monthly_returns": [
            [dates[t], round(float(rets[t]), 6)] for t in range(n_months)]}
    return out


def _hmm_result(n_months: int = 96) -> dict:
    dates = _month_dates(n_months)
    half = n_months // 2
    bull = [0.8] * half + [0.2] * (n_months - half)
    bear = [0.1] * half + [0.7] * (n_months - half)
    trans = [round(1.0 - b - e, 6) for b, e in zip(bull, bear)]
    return {"dates": dates,
            "historical_probs": {"BULL": bull, "BEAR": bear,
                                 "TRANSITION": trans}}


class TestOutOfSampleValidation:

    def test_shape_and_split(self):
        out = v.out_of_sample_validation(
            _strategy_results(96), _hmm_result(96),
            split_date="2021-01-01", min_effective_n=0.0)
        assert "error" not in out
        assert out["n_train_months"] + out["n_test_months"] == 96
        # 2017-01 .. 48 months -> 2020-12 is train; rest test.
        assert out["n_train_months"] == 48
        assert out["n_test_months"] == 48
        assert out["hmm_fit"] == "full_history"

    def test_train_blends_sum_to_one(self):
        out = v.out_of_sample_validation(
            _strategy_results(96), _hmm_result(96),
            split_date="2021-01-01", min_effective_n=0.0)
        for regime, blend in out["train_blends"].items():
            assert sum(blend.values()) == pytest.approx(1.0, abs=1e-5)

    def test_oos_has_all_baselines(self):
        out = v.out_of_sample_validation(
            _strategy_results(96), _hmm_result(96),
            split_date="2021-01-01", min_effective_n=0.0)
        oos = out["oos"]
        assert set(oos.keys()) >= {
            "regime_conditional", "equal_weight", "benchmark",
            "regime_switching"}
        for block in oos.values():
            assert "sharpe" in block and "n_months" in block
            assert block["n_months"] == out["n_test_months"]

    def test_equal_weight_is_mean_across_strategies(self):
        # The EW baseline must equal the row-mean of the test matrix.
        res = _strategy_results(96)
        out = v.out_of_sample_validation(
            res, _hmm_result(96), split_date="2021-01-01",
            min_effective_n=0.0)
        # Reconstruct EW mean-annualised independently.
        from tools.regime_meta_optimizer import build_strategy_matrix
        names, dates, matrix = build_strategy_matrix(res)
        test_mask = np.asarray(dates >= pd.Timestamp("2021-01-01"))
        ew = matrix[test_mask].mean(axis=1)
        assert out["oos"]["equal_weight"]["mean_ann"] == pytest.approx(
            round(float(ew.mean() * 12), 6), abs=1e-6)

    def test_verdict_booleans_consistent(self):
        out = v.out_of_sample_validation(
            _strategy_results(96), _hmm_result(96),
            split_date="2021-01-01", min_effective_n=0.0)
        rc = out["oos"]["regime_conditional"]["sharpe"]
        for key in ("equal_weight", "benchmark", "regime_switching"):
            other = out["oos"][key]["sharpe"]
            expected = (rc > other) if (rc is not None and other is not None) else None
            assert out["verdict"][f"beats_{key}"] == expected
        assert isinstance(out["verdict"]["summary"], str)

    def test_risk_free_scalar_lowers_excess_sharpe(self):
        # A positive risk-free rate reduces the Sharpe of a positive-mean
        # stream (excess return falls).
        res = _strategy_results(96)
        hmm = _hmm_result(96)
        zero = v.out_of_sample_validation(
            res, hmm, split_date="2021-01-01", min_effective_n=0.0)
        withrf = v.out_of_sample_validation(
            res, hmm, split_date="2021-01-01", min_effective_n=0.0,
            risk_free=0.003)
        assert withrf["risk_free"] == "supplied"
        a = zero["oos"]["benchmark"]["sharpe"]
        b = withrf["oos"]["benchmark"]["sharpe"]
        assert a is not None and b is not None and b < a

    def test_insufficient_test_window_errors(self):
        # Split after the data ends -> no test months.
        out = v.out_of_sample_validation(
            _strategy_results(96), _hmm_result(96),
            split_date="2099-01-01")
        assert out["error"] == "insufficient_train_or_test_window"

    def test_insufficient_data_errors(self):
        out = v.out_of_sample_validation({}, _hmm_result(96))
        assert out["error"] == "insufficient_strategy_return_data"

    def test_no_posteriors_errors(self):
        out = v.out_of_sample_validation(_strategy_results(96), {})
        assert out["error"] == "no_regime_posteriors"

    def test_bad_split_date_errors(self):
        out = v.out_of_sample_validation(
            _strategy_results(96), _hmm_result(96), split_date="not-a-date")
        assert out["error"] == "bad_split_date"
