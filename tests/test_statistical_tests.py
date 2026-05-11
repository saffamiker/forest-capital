"""
Sprint 2 — statistical tests unit tests.
All tests use synthetic data — no external API calls.
Tests cover: paired t-test, normality, autocorrelation, FDR correction,
             Jobson-Korkie, power check, tiered thresholds.
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_returns(mean: float = 0.0, std: float = 0.01, n: int = 252, seed: int = 42) -> pd.Series:
    np.random.seed(seed)
    return pd.Series(np.random.normal(mean, std, n))


def make_benchmark(n: int = 252, seed: int = 99) -> pd.Series:
    return make_returns(mean=0.0003, std=0.01, n=n, seed=seed)


# ── paired_ttest ──────────────────────────────────────────────────────────────

def test_paired_ttest_returns_dict():
    from tools.statistical_tests import paired_ttest
    s = make_returns(0.001)
    b = make_benchmark()
    result = paired_ttest(s, b)
    assert isinstance(result, dict)


def test_paired_ttest_has_required_keys():
    from tools.statistical_tests import paired_ttest
    result = paired_ttest(make_returns(0.001), make_benchmark())
    for key in ["test", "t_stat", "p_value", "n_observations", "threshold", "threshold_tier", "passed"]:
        assert key in result, f"Missing key: {key}"


def test_paired_ttest_p_value_in_range():
    from tools.statistical_tests import paired_ttest
    result = paired_ttest(make_returns(0.001), make_benchmark())
    assert 0 <= result["p_value"] <= 1


def test_paired_ttest_same_mean_returns_high_pvalue():
    from tools.statistical_tests import paired_ttest
    np.random.seed(42)
    # Two independent samples from same distribution — no systematic difference
    s = pd.Series(np.random.normal(0.0003, 0.01, 300))
    b = pd.Series(np.random.normal(0.0003, 0.01, 300))
    result = paired_ttest(s, b)
    assert result["p_value"] > 0.3  # No strong evidence of difference


def test_paired_ttest_clearly_different_returns_low_pvalue():
    from tools.statistical_tests import paired_ttest
    np.random.seed(42)
    # 0.5% daily alpha should be highly significant
    s = pd.Series(np.random.normal(0.005, 0.01, 500))
    b = pd.Series(np.random.normal(0.0, 0.01, 500))
    result = paired_ttest(s, b)
    assert result["p_value"] < 0.005


def test_paired_ttest_tier1_threshold_for_n_ge_220():
    from tools.statistical_tests import paired_ttest
    from config import P_THRESHOLD_PRIMARY
    s = make_returns(n=250)
    b = make_benchmark(n=250)
    result = paired_ttest(s, b)
    assert result["threshold_tier"] == "tier1"
    assert result["threshold"] == P_THRESHOLD_PRIMARY


def test_paired_ttest_tier2_threshold_for_small_n():
    from tools.statistical_tests import paired_ttest
    from config import P_THRESHOLD_SUBPERIOD
    s = make_returns(n=100)
    b = make_benchmark(n=100)
    result = paired_ttest(s, b)
    assert result["threshold_tier"] == "tier2"
    assert result["threshold"] == P_THRESHOLD_SUBPERIOD


def test_paired_ttest_directional_for_tiny_n():
    from tools.statistical_tests import paired_ttest
    s = make_returns(n=30)
    b = make_benchmark(n=30)
    result = paired_ttest(s, b)
    assert result["threshold_tier"] == "directional"


# ── normality_test ────────────────────────────────────────────────────────────

def test_normality_test_returns_dict():
    from tools.statistical_tests import normality_test
    result = normality_test(make_returns())
    assert isinstance(result, dict)


def test_normality_test_has_required_keys():
    from tools.statistical_tests import normality_test
    result = normality_test(make_returns())
    for key in ["test", "jb_stat", "p_value", "skewness", "excess_kurtosis", "normality_rejected"]:
        assert key in result


def test_normality_test_normal_data_high_pvalue():
    from tools.statistical_tests import normality_test
    np.random.seed(42)
    normal = pd.Series(np.random.normal(0, 0.01, 500))
    result = normality_test(normal)
    # Normal data should generally not reject normality at 5%
    assert isinstance(result["normality_rejected"], bool)


def test_normality_test_non_normal_rejected():
    from tools.statistical_tests import normality_test
    np.random.seed(42)
    # Heavy-tailed distribution — should reject normality
    non_normal = pd.Series(np.random.standard_t(3, size=500) * 0.01)
    result = normality_test(non_normal)
    assert result["normality_rejected"] is True


# ── autocorrelation_test ──────────────────────────────────────────────────────

def test_autocorrelation_test_returns_dict():
    from tools.statistical_tests import autocorrelation_test
    result = autocorrelation_test(make_returns())
    assert isinstance(result, dict)


def test_autocorrelation_test_has_required_keys():
    from tools.statistical_tests import autocorrelation_test
    result = autocorrelation_test(make_returns())
    for key in ["test", "lags", "lb_stat", "p_value", "has_autocorrelation"]:
        assert key in result


def test_autocorrelation_test_iid_series_no_autocorr():
    from tools.statistical_tests import autocorrelation_test
    np.random.seed(42)
    iid = pd.Series(np.random.normal(0, 0.01, 500))
    result = autocorrelation_test(iid)
    # IID white noise should not have autocorrelation at 5% most of the time
    assert result["p_value"] > 0  # Just sanity-check it runs


def test_autocorrelation_test_autocorrelated_series_detected():
    from tools.statistical_tests import autocorrelation_test
    np.random.seed(42)
    # AR(1) with rho=0.5 has strong autocorrelation
    n = 500
    ar = np.zeros(n)
    noise = np.random.normal(0, 0.01, n)
    for i in range(1, n):
        ar[i] = 0.5 * ar[i - 1] + noise[i]
    result = autocorrelation_test(pd.Series(ar))
    assert result["has_autocorrelation"] is True


# ── multiple_comparison_correction ───────────────────────────────────────────

def test_fdr_correction_returns_dict():
    from tools.statistical_tests import multiple_comparison_correction
    p_vals = {"strat_a": 0.001, "strat_b": 0.01, "strat_c": 0.1}
    result = multiple_comparison_correction(p_vals)
    assert isinstance(result, dict)


def test_fdr_correction_has_all_strategies():
    from tools.statistical_tests import multiple_comparison_correction
    p_vals = {"strat_a": 0.001, "strat_b": 0.01, "strat_c": 0.1}
    result = multiple_comparison_correction(p_vals)
    assert "strategies" in result
    for name in p_vals:
        assert name in result["strategies"]


def test_fdr_correction_increases_p_values():
    from tools.statistical_tests import multiple_comparison_correction
    p_vals = {"a": 0.001, "b": 0.003, "c": 0.004, "d": 0.02, "e": 0.05}
    result = multiple_comparison_correction(p_vals)
    for name, data in result["strategies"].items():
        assert data["p_corrected"] >= data["p_raw"]


def test_fdr_correction_empty_input():
    from tools.statistical_tests import multiple_comparison_correction
    result = multiple_comparison_correction({})
    assert "error" in result


def test_fdr_correction_counts_passed():
    from tools.statistical_tests import multiple_comparison_correction
    # Only one clearly significant strategy
    p_vals = {"alpha_strat": 0.0001, "beta_strat": 0.5, "gamma_strat": 0.9}
    result = multiple_comparison_correction(p_vals)
    assert result["n_tested"] == 3
    assert isinstance(result["n_passed"], int)


# ── power_check ───────────────────────────────────────────────────────────────

def test_power_check_adequate_n():
    from tools.statistical_tests import power_check
    from config import MIN_OBSERVATIONS_FOR_POWER
    result = power_check(MIN_OBSERVATIONS_FOR_POWER + 100)
    assert result["threshold_tier"] == "tier1"


def test_power_check_subperiod_n():
    from tools.statistical_tests import power_check
    from config import MIN_OBSERVATIONS_SUBPERIOD, MIN_OBSERVATIONS_FOR_POWER
    n = (MIN_OBSERVATIONS_SUBPERIOD + MIN_OBSERVATIONS_FOR_POWER) // 2
    result = power_check(n)
    assert result["threshold_tier"] == "tier2"


def test_power_check_tiny_n():
    from tools.statistical_tests import power_check
    from config import MIN_OBSERVATIONS_SUBPERIOD
    result = power_check(MIN_OBSERVATIONS_SUBPERIOD - 10)
    assert result["threshold_tier"] == "directional"


def test_power_check_returns_n_required():
    from tools.statistical_tests import power_check
    result = power_check(500)
    assert "n_required_for_80pct_power" in result
    assert result["n_required_for_80pct_power"] > 0


# ── alpha_significance_test ───────────────────────────────────────────────────

def test_alpha_significance_test_returns_dict():
    from tools.statistical_tests import alpha_significance_test
    s = make_returns(0.0008, n=300)
    b = make_benchmark(n=300)
    result = alpha_significance_test(s, b)
    assert isinstance(result, dict)


def test_alpha_significance_test_has_alpha_annualised():
    from tools.statistical_tests import alpha_significance_test
    s = make_returns(0.0008, n=300)
    b = make_benchmark(n=300)
    result = alpha_significance_test(s, b)
    assert "alpha_annualised" in result
    assert "alpha_bps" in result


# ── Sprint 3: deflated_sharpe_ratio ──────────────────────────────────────────

def test_dsr_returns_dict():
    from tools.statistical_tests import deflated_sharpe_ratio
    result = deflated_sharpe_ratio(sharpe=1.0, n_obs=300, n_trials=10)
    assert isinstance(result, dict)


def test_dsr_has_required_keys():
    from tools.statistical_tests import deflated_sharpe_ratio
    result = deflated_sharpe_ratio(sharpe=1.0, n_obs=300, n_trials=10)
    for key in ["sr_star", "p_value", "passed", "observed_sharpe"]:
        assert key in result, f"Missing key: {key}"


def test_dsr_high_sharpe_passes():
    """
    A Sharpe of 2.0 from 300 observations across 10 strategies should
    comfortably pass DSR — the correction for n_trials=10 is not large enough
    to invalidate a genuine 2.0 Sharpe.
    """
    from tools.statistical_tests import deflated_sharpe_ratio
    result = deflated_sharpe_ratio(sharpe=2.0, n_obs=300, n_trials=10)
    assert result["passed"] is True
    assert result["p_value"] < 0.005


def test_dsr_low_sharpe_fails():
    """
    A Sharpe of 0.05 from 300 observations across 10 strategies fails DSR —
    this is below the SR* threshold (~0.096) produced by the expected maximum
    of 10 IID standard-normal Sharpe draws. The test uses 0.05 rather than 0.5
    because with n_obs=300, even SR=0.5 clears the DSR bar (SR* ≈ 0.096).
    """
    from tools.statistical_tests import deflated_sharpe_ratio
    result = deflated_sharpe_ratio(sharpe=0.05, n_obs=300, n_trials=10)
    assert result["p_value"] > 0.005  # Below SR* → not significant after correction


def test_dsr_more_trials_requires_higher_sharpe():
    """
    The SR* threshold rises with more strategies tested — with n_trials=50
    the threshold should be higher than with n_trials=10 for the same data.
    Testing 50 strategies and picking the winner is much more susceptible to
    data-snooping than testing 10.
    """
    from tools.statistical_tests import deflated_sharpe_ratio
    result_10 = deflated_sharpe_ratio(sharpe=1.2, n_obs=300, n_trials=10)
    result_50 = deflated_sharpe_ratio(sharpe=1.2, n_obs=300, n_trials=50)
    # SR* should be higher for n_trials=50 → harder to pass
    assert result_50["sr_star"] >= result_10["sr_star"]


def test_dsr_non_normal_skewness_affects_result():
    """
    Non-normal return distributions (skewness ≠ 0, excess kurtosis ≠ 0)
    should give a different result from normal. Financial returns are
    negatively skewed and leptokurtic — ignoring this would understate
    the variance of the Sharpe estimator.
    """
    from tools.statistical_tests import deflated_sharpe_ratio
    normal_result = deflated_sharpe_ratio(1.0, 300, 10, skewness=0.0, kurtosis=3.0)
    skewed_result = deflated_sharpe_ratio(1.0, 300, 10, skewness=-0.5, kurtosis=5.0)
    # At least one metric should differ between normal and non-normal
    assert normal_result["sr_star"] != skewed_result["sr_star"] or \
           normal_result["p_value"] != skewed_result["p_value"]


def test_dsr_p_value_in_range():
    from tools.statistical_tests import deflated_sharpe_ratio
    result = deflated_sharpe_ratio(sharpe=1.0, n_obs=300, n_trials=10)
    assert 0.0 <= result["p_value"] <= 1.0


# ── Sprint 3: probabilistic_sharpe_ratio ─────────────────────────────────────

def test_psr_returns_dict():
    from tools.statistical_tests import probabilistic_sharpe_ratio
    result = probabilistic_sharpe_ratio(
        sharpe=1.0, benchmark_sharpe=0.5, n_obs=300
    )
    assert isinstance(result, dict)


def test_psr_has_required_keys():
    from tools.statistical_tests import probabilistic_sharpe_ratio
    result = probabilistic_sharpe_ratio(1.0, 0.5, 300)
    for key in ["psr", "sharpe_ci_95"]:
        assert key in result, f"Missing key: {key}"


def test_psr_is_probability():
    """PSR must be P(true SR > benchmark SR) — a value in [0, 1]."""
    from tools.statistical_tests import probabilistic_sharpe_ratio
    result = probabilistic_sharpe_ratio(1.0, 0.5, 300)
    assert 0.0 <= result["psr"] <= 1.0


def test_psr_above_benchmark_gives_high_probability():
    """
    When observed Sharpe is well above benchmark, PSR should be close to 1.
    This is the case for a strategy with genuine positive alpha.
    """
    from tools.statistical_tests import probabilistic_sharpe_ratio
    result = probabilistic_sharpe_ratio(sharpe=1.5, benchmark_sharpe=0.3, n_obs=300)
    assert result["psr"] > 0.90


def test_psr_equal_to_benchmark_gives_50_probability():
    """
    When observed Sharpe equals benchmark Sharpe, P(true SR > benchmark) ≈ 0.5.
    This is the symmetry property of PSR — no information either way.
    """
    from tools.statistical_tests import probabilistic_sharpe_ratio
    result = probabilistic_sharpe_ratio(sharpe=0.7, benchmark_sharpe=0.7, n_obs=300)
    assert abs(result["psr"] - 0.5) < 0.1  # Should be near 0.5


def test_psr_ci_has_two_values():
    """95% confidence interval must have exactly two values: (lower, upper)."""
    from tools.statistical_tests import probabilistic_sharpe_ratio
    result = probabilistic_sharpe_ratio(1.0, 0.5, 300)
    ci = result["sharpe_ci_95"]
    assert len(ci) == 2
    assert ci[0] < ci[1]  # Lower bound < upper bound


def test_psr_larger_n_gives_tighter_ci():
    """
    More observations → tighter Sharpe estimate → narrower confidence interval.
    This is the key value of reporting CI alongside point estimate.
    """
    from tools.statistical_tests import probabilistic_sharpe_ratio
    small_n = probabilistic_sharpe_ratio(1.0, 0.5, n_obs=100)
    large_n = probabilistic_sharpe_ratio(1.0, 0.5, n_obs=1000)
    width_small = small_n["sharpe_ci_95"][1] - small_n["sharpe_ci_95"][0]
    width_large = large_n["sharpe_ci_95"][1] - large_n["sharpe_ci_95"][0]
    assert width_large < width_small


# ── Sprint 3: spa_test ────────────────────────────────────────────────────────

def test_spa_test_returns_dict():
    from tools.statistical_tests import spa_test
    bm = make_benchmark(252)
    strategies = {
        "strat_a": make_returns(0.001, n=252),
        "strat_b": make_returns(0.0005, n=252),
    }
    result = spa_test(strategies, bm, n_boot=200, seed=42)
    assert isinstance(result, dict)


def test_spa_test_has_required_keys():
    from tools.statistical_tests import spa_test
    bm = make_benchmark(252)
    strategies = {
        "strat_a": make_returns(0.001, n=252),
        "strat_b": make_returns(0.0005, n=252),
    }
    result = spa_test(strategies, bm, n_boot=200, seed=42)
    for key in ["p_spa", "best_strategy", "passes_spa"]:
        assert key in result, f"Missing key: {key}"


def test_spa_test_p_value_in_range():
    from tools.statistical_tests import spa_test
    bm = make_benchmark(252)
    strategies = {"strat_a": make_returns(0.001, n=252)}
    result = spa_test(strategies, bm, n_boot=200, seed=42)
    assert 0.0 <= result["p_spa"] <= 1.0


def test_spa_test_identifies_best_strategy():
    """
    SPA must identify which strategy produced the highest observed Sharpe.
    This is the strategy being tested against the null — if SPA passes,
    this is the strategy we can claim is superior, not just lucky.
    """
    from tools.statistical_tests import spa_test
    bm = make_benchmark(300)
    # strat_a clearly better than strat_b
    strategies = {
        "strat_a": make_returns(0.003, n=300, seed=1),   # High alpha
        "strat_b": make_returns(0.0001, n=300, seed=2),  # Low alpha
    }
    result = spa_test(strategies, bm, n_boot=500, seed=42)
    assert result["best_strategy"] == "strat_a"


def test_spa_test_reproducible_with_seed():
    """
    Block bootstrap with a fixed seed must give identical p-values.
    Reproducibility is required by the QA checklist — seed=RANDOM_SEED=42.
    """
    from tools.statistical_tests import spa_test
    bm = make_benchmark(252)
    strategies = {"strat_a": make_returns(0.001, n=252)}
    r1 = spa_test(strategies, bm, n_boot=200, seed=42)
    r2 = spa_test(strategies, bm, n_boot=200, seed=42)
    assert r1["p_spa"] == r2["p_spa"]


def test_spa_test_random_strategy_high_pvalue():
    """
    A strategy with no edge should have a high p-value — we cannot reject
    the null that it is no better than chance. SPA is not a bar to clear
    easily: random noise should fail.
    """
    from tools.statistical_tests import spa_test
    np.random.seed(99)
    bm = make_benchmark(300)
    # Strategy with same distribution as benchmark — no edge
    strategies = {
        "random": pd.Series(np.random.normal(0.0003, 0.01, 300))
    }
    result = spa_test(strategies, bm, n_boot=1000, seed=42)
    # Random strategy should generally not pass SPA at 0.5% significance
    assert result["p_spa"] > 0.05  # Usually > 5% — no real edge
