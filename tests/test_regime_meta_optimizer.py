"""tests/test_regime_meta_optimizer.py — Layer 2 of the
Regime-Conditional Meta-Portfolio Optimizer.

Covers:
  - build_strategy_matrix: common-date alignment, exclude, too-few
  - align_regime_posteriors: reindex + ffill onto the matrix dates
  - regime_conditional_moments: weighted mean/cov, unbiased reduction
    to the ordinary sample covariance when weights are uniform, Kish
    effective-N
  - meta_mean_variance: box-constrained sum-1 long-only, infeasible
    box fallback, equal-weight fallback paths
  - compute_regime_blends: per-regime blends, low-ESS fallback,
    error shapes
  - probability_weighted_blend: w = Σ P(r)·w_r, renormalisation,
    partial-regime degradation
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault(
    "SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,"
    "murdockm@queens.edu,panttserk@queens.edu")

from tools import regime_meta_optimizer as rmo  # noqa: E402


# ── Fixtures ────────────────────────────────────────────────────────────────


def _month_dates(n: int, start: str = "2010-01-31") -> list[str]:
    import pandas as pd
    return [d.date().isoformat()
            for d in pd.date_range(start, periods=n, freq="ME")]


def _strategy_results(n_months: int = 60, n_strats: int = 5,
                      seed: int = 42) -> dict[str, dict]:
    """Synthetic run_all_strategies()-shaped output. Each strategy
    gets a distinct mean/vol so the optimizer has something to
    discriminate."""
    rng = np.random.default_rng(seed)
    dates = _month_dates(n_months)
    out: dict[str, dict] = {}
    for k in range(n_strats):
        mean = 0.003 + 0.001 * k
        vol = 0.02 + 0.005 * k
        rets = rng.normal(mean, vol, n_months)
        out[f"STRAT_{k}"] = {
            "monthly_returns": [
                [dates[t], round(float(rets[t]), 6)]
                for t in range(n_months)
            ]
        }
    return out


def _hmm_result(n_months: int = 60) -> dict:
    """Synthetic Layer-1 fit_hmm_historical() output: a BULL posterior
    that is high in the first half, a BEAR posterior high in the
    second, and a flat TRANSITION."""
    dates = _month_dates(n_months)
    half = n_months // 2
    bull = [0.8] * half + [0.2] * (n_months - half)
    bear = [0.1] * half + [0.7] * (n_months - half)
    trans = [round(1.0 - b - e, 6) for b, e in zip(bull, bear)]
    return {
        "dates": dates,
        "historical_probs": {
            "BULL": bull, "BEAR": bear, "TRANSITION": trans,
        },
    }


# ── build_strategy_matrix ───────────────────────────────────────────────────


class TestBuildStrategyMatrix:

    def test_basic_shape_and_order(self):
        names, dates, m = rmo.build_strategy_matrix(
            _strategy_results(60, 5))
        assert names == [f"STRAT_{k}" for k in range(5)]
        assert len(dates) == 60
        assert m.shape == (60, 5)

    def test_exclude_drops_a_strategy(self):
        names, _, m = rmo.build_strategy_matrix(
            _strategy_results(60, 5), exclude=("STRAT_0",))
        assert "STRAT_0" not in names
        assert m.shape == (60, 4)

    def test_common_date_intersection(self):
        # STRAT_A spans 60 months, STRAT_B starts 12 months later.
        res = _strategy_results(60, 1)
        res = {"STRAT_A": res["STRAT_0"]}
        late = _strategy_results(48, 1)["STRAT_0"]
        # shift its dates to start 12 months in
        late_dates = _month_dates(48, start="2011-01-31")
        late["monthly_returns"] = [
            [late_dates[t], late["monthly_returns"][t][1]]
            for t in range(48)
        ]
        res["STRAT_B"] = late
        names, dates, m = rmo.build_strategy_matrix(res)
        assert set(names) == {"STRAT_A", "STRAT_B"}
        # Intersection is the 48 overlapping months.
        assert m.shape[0] == 48

    def test_too_few_strategies_returns_empty(self):
        names, dates, m = rmo.build_strategy_matrix(
            {"ONLY": _strategy_results(60, 1)["STRAT_0"]})
        assert names == []
        assert m.size == 0


# ── align_regime_posteriors ─────────────────────────────────────────────────


class TestAlignPosteriors:

    def test_aligns_onto_matrix_dates(self):
        _, dates, _ = rmo.build_strategy_matrix(_strategy_results(60, 5))
        post = rmo.align_regime_posteriors(dates, _hmm_result(60))
        assert set(post.keys()) == {"BULL", "BEAR", "TRANSITION"}
        for label in post:
            assert post[label].shape == (60,)

    def test_no_posteriors_returns_empty(self):
        _, dates, _ = rmo.build_strategy_matrix(_strategy_results(60, 5))
        assert rmo.align_regime_posteriors(dates, {}) == {}

    def test_two_state_fit_omits_transition(self):
        _, dates, _ = rmo.build_strategy_matrix(_strategy_results(60, 5))
        hmm = _hmm_result(60)
        del hmm["historical_probs"]["TRANSITION"]
        post = rmo.align_regime_posteriors(dates, hmm)
        assert "TRANSITION" not in post
        assert set(post.keys()) == {"BULL", "BEAR"}


# ── regime_conditional_moments ──────────────────────────────────────────────


class TestRegimeConditionalMoments:

    def test_uniform_weights_match_sample_moments(self):
        # With every weight equal, the weighted moments must equal the
        # ordinary sample mean and (unbiased) sample covariance.
        rng = np.random.default_rng(7)
        X = rng.normal(0.0, 1.0, (80, 4))
        post = np.ones(80)
        mu, cov, ess = rmo.regime_conditional_moments(X, post)
        np.testing.assert_allclose(mu, X.mean(axis=0), atol=1e-9)
        np.testing.assert_allclose(
            cov, np.cov(X, rowvar=False, ddof=1), atol=1e-9)
        # Kish ESS of uniform weights is exactly T.
        assert ess == pytest.approx(80.0)

    def test_zero_weights_return_zero_moments(self):
        X = np.ones((10, 3))
        mu, cov, ess = rmo.regime_conditional_moments(X, np.zeros(10))
        assert np.allclose(mu, 0.0)
        assert np.allclose(cov, 0.0)
        assert ess == 0.0

    def test_concentrated_weights_lower_effective_n(self):
        X = np.random.default_rng(1).normal(0, 1, (50, 3))
        # All weight on one month → ESS = 1.
        p = np.zeros(50)
        p[0] = 1.0
        _, _, ess = rmo.regime_conditional_moments(X, p)
        assert ess == pytest.approx(1.0)


# ── meta_mean_variance ──────────────────────────────────────────────────────


class TestMetaMeanVariance:

    def test_weights_sum_to_one_and_respect_box(self):
        rng = np.random.default_rng(3)
        X = rng.normal(0.004, 0.03, (120, 6))
        mu = X.mean(axis=0)
        cov = np.cov(X, rowvar=False, ddof=1)
        w = rmo.meta_mean_variance(mu, cov)
        assert w.shape == (6,)
        assert w.sum() == pytest.approx(1.0, abs=1e-6)
        assert np.all(w >= -1e-9)
        assert np.all(w <= rmo._META_MAX_WEIGHT + 1e-6)

    def test_infeasible_box_falls_back_to_equal_weight(self):
        # 2 assets with a 0.40 cap cannot sum to 1 → equal weight.
        mu = np.array([0.01, 0.02])
        cov = np.eye(2) * 0.01
        w = rmo.meta_mean_variance(mu, cov, max_weight=0.40)
        np.testing.assert_allclose(w, [0.5, 0.5])

    def test_nonfinite_moments_fall_back(self):
        mu = np.array([np.nan, 0.01, 0.02])
        cov = np.eye(3) * 0.01
        w = rmo.meta_mean_variance(mu, cov)
        np.testing.assert_allclose(w, np.full(3, 1 / 3))

    def test_empty_returns_empty(self):
        w = rmo.meta_mean_variance(np.empty(0), np.empty((0, 0)))
        assert w.size == 0


# ── compute_regime_blends ───────────────────────────────────────────────────


class TestComputeRegimeBlends:

    def test_produces_a_blend_per_regime(self):
        out = rmo.compute_regime_blends(
            _strategy_results(120, 6), _hmm_result(120),
            min_effective_n=0.0)   # disable the ESS floor for the test
        assert "error" not in out
        assert set(out["blends"].keys()) == {
            "BULL", "BEAR", "TRANSITION"}
        for regime, blend in out["blends"].items():
            assert len(blend) == 6
            assert sum(blend.values()) == pytest.approx(1.0, abs=1e-5)

    def test_low_effective_n_falls_back_to_equal_weight(self):
        # A high floor forces every regime to the equal-weight fallback.
        res = _strategy_results(120, 5)
        out = rmo.compute_regime_blends(
            res, _hmm_result(120), min_effective_n=10_000.0)
        assert set(out["fallback"]) >= {"BULL", "BEAR"}
        for blend in out["blends"].values():
            # equal weight across 5 strategies
            assert all(w == pytest.approx(0.2, abs=1e-6)
                       for w in blend.values())

    def test_insufficient_data_errors(self):
        out = rmo.compute_regime_blends({}, _hmm_result(60))
        assert out["error"] == "insufficient_strategy_return_data"

    def test_no_posteriors_errors(self):
        out = rmo.compute_regime_blends(
            _strategy_results(60, 5), {})
        assert out["error"] == "no_regime_posteriors"


# ── probability_weighted_blend ──────────────────────────────────────────────


class TestProbabilityWeightedBlend:

    def test_mixes_by_posterior(self):
        blends = {
            "BULL": {"A": 0.6, "B": 0.4},
            "BEAR": {"A": 0.2, "B": 0.8},
        }
        out = rmo.probability_weighted_blend(
            blends, {"BULL": 0.75, "BEAR": 0.25})
        # A = 0.75*0.6 + 0.25*0.2 = 0.50 ; B = 0.50
        assert out["A"] == pytest.approx(0.5, abs=1e-6)
        assert out["B"] == pytest.approx(0.5, abs=1e-6)
        assert sum(out.values()) == pytest.approx(1.0, abs=1e-6)

    def test_renormalises_unnormalised_posterior(self):
        blends = {"BULL": {"A": 1.0}, "BEAR": {"A": 1.0}}
        # Posterior doesn't sum to 1 — must still yield a valid blend.
        out = rmo.probability_weighted_blend(
            blends, {"BULL": 3.0, "BEAR": 1.0})
        assert out["A"] == pytest.approx(1.0, abs=1e-6)

    def test_degrades_when_regime_blend_missing(self):
        # Posterior names TRANSITION but only BULL/BEAR have blends.
        blends = {"BULL": {"A": 0.5, "B": 0.5},
                  "BEAR": {"A": 0.5, "B": 0.5}}
        out = rmo.probability_weighted_blend(
            blends, {"BULL": 0.4, "BEAR": 0.4, "TRANSITION": 0.2})
        # TRANSITION dropped; BULL/BEAR renormalised to 0.5/0.5.
        assert sum(out.values()) == pytest.approx(1.0, abs=1e-6)

    def test_no_common_regime_returns_empty(self):
        out = rmo.probability_weighted_blend(
            {"BULL": {"A": 1.0}}, {"BEAR": 1.0})
        assert out == {}

    def test_end_to_end_blend_then_mix(self):
        # Full path: blends from real synthetic data, then mix by a
        # current posterior. Result is a valid allocation.
        res = _strategy_results(120, 6)
        built = rmo.compute_regime_blends(
            res, _hmm_result(120), min_effective_n=0.0)
        live = rmo.probability_weighted_blend(
            built["blends"],
            {"BULL": 0.5, "BEAR": 0.3, "TRANSITION": 0.2})
        assert set(live.keys()) == set(built["names"])
        assert sum(live.values()) == pytest.approx(1.0, abs=1e-5)
        assert all(v >= -1e-9 for v in live.values())


# ── box-constraint surfacing + sensitivity ──────────────────────────────────


class TestBoxConstraint:

    def test_output_echoes_cap_and_note(self):
        out = rmo.compute_regime_blends(
            _strategy_results(120, 6), _hmm_result(120),
            min_effective_n=0.0)
        assert out["max_weight"] == rmo._META_MAX_WEIGHT
        note = out["box_constraint_note"]
        assert "diversification constraint" in note
        assert "institutional mandate" in note
        # The cap is echoed as a percentage in the note.
        assert f"{rmo._META_MAX_WEIGHT:.0%}" in note

    def test_lower_cap_forces_more_strategies(self):
        # A tighter cap cannot be met by fewer strategies, so the count
        # of non-zero weights must not fall when the cap drops.
        res = _strategy_results(120, 6)
        hmm = _hmm_result(120)
        wide = rmo.compute_regime_blends(
            res, hmm, max_weight=0.50, min_effective_n=0.0)
        tight = rmo.compute_regime_blends(
            res, hmm, max_weight=0.30, min_effective_n=0.0)
        for regime in wide["blends"]:
            n_wide = sum(1 for w in wide["blends"][regime].values()
                         if w > 1e-6)
            n_tight = sum(1 for w in tight["blends"][regime].values()
                          if w > 1e-6)
            assert n_tight >= n_wide
            # No weight may exceed the tighter cap (within rounding).
            assert max(tight["blends"][regime].values()) <= 0.30 + 1e-6

    def test_note_reflects_custom_cap(self):
        out = rmo.compute_regime_blends(
            _strategy_results(120, 6), _hmm_result(120),
            max_weight=0.30, min_effective_n=0.0)
        assert out["max_weight"] == 0.30
        assert "30%" in out["box_constraint_note"]


# ── regime_strategy_diagnostics ─────────────────────────────────────────────


class TestRegimeStrategyDiagnostics:

    def test_shape_and_ranks(self):
        diag = rmo.regime_strategy_diagnostics(
            _strategy_results(120, 6), _hmm_result(120))
        assert "error" not in diag
        assert len(diag["names"]) == 6
        for regime, info in diag["regimes"].items():
            per = info["per_strategy"]
            assert len(per) == 6
            # Ranks are a permutation of 1..6.
            ranks = sorted(m["rank"] for m in per.values())
            assert ranks == list(range(1, 7))
            # top_sharpe is the rank-1 strategy.
            top = info["top_sharpe"]
            assert per[top]["rank"] == 1
            # The top_sharpe strategy has the maximum sharpe_ann.
            best = max(per.values(), key=lambda m: m["sharpe_ann"])
            assert per[top]["sharpe_ann"] == pytest.approx(
                best["sharpe_ann"])

    def test_insufficient_data_errors(self):
        out = rmo.regime_strategy_diagnostics({}, _hmm_result(60))
        assert out["error"] == "insufficient_strategy_return_data"

    def test_no_posteriors_errors(self):
        out = rmo.regime_strategy_diagnostics(
            _strategy_results(60, 5), {})
        assert out["error"] == "no_regime_posteriors"
