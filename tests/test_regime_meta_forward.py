"""Tests for tools/regime_meta_forward.py: Layer 4 forward Monte Carlo
confidence bands. Synthetic strategy_results + hmm_result so no hmmlearn /
DB is needed; the simulation (initial-regime sampling, transition stepping,
regime-conditional return draws, band percentiles, matched benchmark
outperformance) is exercised end to end. n_paths is kept modest for speed."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")

import pandas as pd  # noqa: E402

from tools import regime_meta_forward as f  # noqa: E402

_N_PATHS = 2000


def _month_dates(n: int, start: str = "2017-01-31") -> list[str]:
    return [d.date().isoformat()
            for d in pd.date_range(start, periods=n, freq="ME")]


def _strategy_results(n_months: int = 96, seed: int = 7) -> dict[str, dict]:
    """Includes BENCHMARK so the matched outperformance stat is exercised,
    plus four more strategies for the blend."""
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


def _hmm_result(n_months: int = 96, *, with_transition: bool = True) -> dict:
    """A synthetic HMM result. When with_transition is True it carries a
    transition_matrix so the 'hmm' source path is exercised; when False
    the persistence fallback is exercised."""
    dates = _month_dates(n_months)
    half = n_months // 2
    bull = [0.8] * half + [0.2] * (n_months - half)
    bear = [0.1] * half + [0.7] * (n_months - half)
    trans = [round(1.0 - b - e, 6) for b, e in zip(bull, bear)]
    result: dict = {"dates": dates,
                    "historical_probs": {"BULL": bull, "BEAR": bear,
                                         "TRANSITION": trans}}
    if with_transition:
        result["transition_matrix"] = {
            "BULL":       {"BULL": 0.85, "BEAR": 0.05, "TRANSITION": 0.10},
            "BEAR":       {"BULL": 0.05, "BEAR": 0.85, "TRANSITION": 0.10},
            "TRANSITION": {"BULL": 0.30, "BEAR": 0.30, "TRANSITION": 0.40},
        }
    return result


_POSTERIOR = {"BULL": 0.6, "BEAR": 0.2, "TRANSITION": 0.2}


class TestForwardMonteCarlo:

    def test_shape_and_keys(self):
        out = f.forward_monte_carlo(
            _strategy_results(96), _hmm_result(96), _POSTERIOR,
            n_paths=_N_PATHS, min_effective_n=0.0)
        assert "error" not in out
        assert set(out.keys()) >= {
            "names", "n_paths", "seed", "horizons_months", "blend_weights",
            "bands", "transition_source"}
        assert out["n_paths"] == _N_PATHS
        assert out["seed"] == 42
        assert out["horizons_months"] == [1, 3, 6, 12]
        # One band per horizon, each with the four reported fields.
        assert set(out["bands"].keys()) == {"1", "3", "6", "12"}
        for band in out["bands"].values():
            assert set(band.keys()) == {
                "median", "p05", "p95", "p_outperform_benchmark"}

    def test_band_ordering_every_horizon(self):
        out = f.forward_monte_carlo(
            _strategy_results(96), _hmm_result(96), _POSTERIOR,
            n_paths=_N_PATHS, min_effective_n=0.0)
        for h, band in out["bands"].items():
            assert band["p05"] <= band["median"] <= band["p95"], h

    def test_outperformance_probability_in_unit_interval(self):
        out = f.forward_monte_carlo(
            _strategy_results(96), _hmm_result(96), _POSTERIOR,
            n_paths=_N_PATHS, min_effective_n=0.0)
        for band in out["bands"].values():
            p = band["p_outperform_benchmark"]
            assert p is not None
            assert 0.0 <= p <= 1.0

    def test_reproducible_same_seed(self):
        res, hmm = _strategy_results(96), _hmm_result(96)
        a = f.forward_monte_carlo(res, hmm, _POSTERIOR, n_paths=_N_PATHS,
                                  seed=42, min_effective_n=0.0)
        b = f.forward_monte_carlo(res, hmm, _POSTERIOR, n_paths=_N_PATHS,
                                  seed=42, min_effective_n=0.0)
        assert a["bands"] == b["bands"]

    def test_different_seed_changes_bands(self):
        res, hmm = _strategy_results(96), _hmm_result(96)
        a = f.forward_monte_carlo(res, hmm, _POSTERIOR, n_paths=_N_PATHS,
                                  seed=42, min_effective_n=0.0)
        b = f.forward_monte_carlo(res, hmm, _POSTERIOR, n_paths=_N_PATHS,
                                  seed=99, min_effective_n=0.0)
        # The medians should generally differ across the horizons; require
        # at least one to differ so the seed genuinely drives the draws.
        diffs = [a["bands"][h]["median"] != b["bands"][h]["median"]
                 for h in a["bands"]]
        assert any(diffs)

    def test_transition_source_hmm_when_present(self):
        out = f.forward_monte_carlo(
            _strategy_results(96), _hmm_result(96, with_transition=True),
            _POSTERIOR, n_paths=_N_PATHS, min_effective_n=0.0)
        assert out["transition_source"] == "hmm"

    def test_transition_source_persistence_when_absent(self):
        out = f.forward_monte_carlo(
            _strategy_results(96), _hmm_result(96, with_transition=False),
            _POSTERIOR, n_paths=_N_PATHS, min_effective_n=0.0)
        assert out["transition_source"] == "persistence_fallback"

    def test_blend_weights_sum_to_one(self):
        out = f.forward_monte_carlo(
            _strategy_results(96), _hmm_result(96), _POSTERIOR,
            n_paths=_N_PATHS, min_effective_n=0.0)
        assert out["blend_weights"]
        assert sum(out["blend_weights"].values()) == pytest.approx(
            1.0, abs=1e-5)

    def test_no_benchmark_outperformance_is_none(self):
        # Exclude BENCHMARK from the universe -> no matched benchmark draw.
        out = f.forward_monte_carlo(
            _strategy_results(96), _hmm_result(96), _POSTERIOR,
            n_paths=_N_PATHS, exclude=("BENCHMARK",), min_effective_n=0.0)
        assert "BENCHMARK" not in out["names"]
        for band in out["bands"].values():
            assert band["p_outperform_benchmark"] is None

    def test_custom_horizons(self):
        out = f.forward_monte_carlo(
            _strategy_results(96), _hmm_result(96), _POSTERIOR,
            n_paths=_N_PATHS, horizons=(2, 4), min_effective_n=0.0)
        assert out["horizons_months"] == [2, 4]
        assert set(out["bands"].keys()) == {"2", "4"}

    def test_unusable_posterior_falls_back_to_uniform(self):
        # An all-zero posterior is unusable; the simulation should still
        # run (uniform initial mix) and produce valid bands.
        out = f.forward_monte_carlo(
            _strategy_results(96), _hmm_result(96),
            {"BULL": 0.0, "BEAR": 0.0, "TRANSITION": 0.0},
            n_paths=_N_PATHS, min_effective_n=0.0)
        assert "error" not in out
        for band in out["bands"].values():
            assert band["p05"] <= band["median"] <= band["p95"]

    def test_insufficient_data_errors(self):
        out = f.forward_monte_carlo({}, _hmm_result(96), _POSTERIOR,
                                    n_paths=_N_PATHS)
        assert out["error"] == "insufficient_strategy_return_data"

    def test_no_posteriors_errors(self):
        out = f.forward_monte_carlo(_strategy_results(96), {}, _POSTERIOR,
                                    n_paths=_N_PATHS)
        assert out["error"] == "no_regime_posteriors"

    def test_propagates_blend_error(self):
        # A single-strategy universe cannot build a matrix (covariance
        # needs >= 2 assets) -> the build step errors first.
        one = {"BENCHMARK": _strategy_results(96)["BENCHMARK"]}
        out = f.forward_monte_carlo(one, _hmm_result(96), _POSTERIOR,
                                    n_paths=_N_PATHS)
        assert "error" in out
