"""
Sprint 1 — Config tests.
Verifies all required constants exist, have correct types, and hold the
values mandated by the project specification.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import config


# ── Critical thresholds ───────────────────────────────────────────────────────

def test_p_threshold_primary():
    assert config.P_THRESHOLD_PRIMARY == 0.005

def test_fdr_q_value():
    assert config.FDR_Q_VALUE == 0.005

def test_p_threshold_dsr():
    assert config.P_THRESHOLD_DSR == 0.005

def test_p_threshold_oos():
    assert config.P_THRESHOLD_OOS == 0.005

def test_p_threshold_permutation():
    assert config.P_THRESHOLD_PERMUTATION == 0.005

def test_p_threshold_subperiod():
    assert config.P_THRESHOLD_SUBPERIOD == 0.050

def test_p_threshold_cv_folds():
    assert config.P_THRESHOLD_CV_FOLDS == 0.050

def test_stress_test_no_pvalues():
    assert config.STRESS_TEST_USE_PVALUES is False


# ── Reproducibility ───────────────────────────────────────────────────────────

def test_random_seed():
    assert config.RANDOM_SEED == 42

def test_annualization_factor():
    """Must be 252 — never 260 or 365."""
    assert config.ANNUALIZATION_FACTOR == 252


# ── Types ─────────────────────────────────────────────────────────────────────

def test_p_threshold_primary_is_float():
    assert isinstance(config.P_THRESHOLD_PRIMARY, float)

def test_random_seed_is_int():
    assert isinstance(config.RANDOM_SEED, int)

def test_annualization_factor_is_int():
    assert isinstance(config.ANNUALIZATION_FACTOR, int)

def test_bootstrap_samples_is_int():
    assert isinstance(config.BOOTSTRAP_SAMPLES, int)

def test_bootstrap_samples_value():
    assert config.BOOTSTRAP_SAMPLES == 10_000

def test_block_size_is_int():
    assert isinstance(config.BLOCK_SIZE, int)


# ── Observation thresholds ────────────────────────────────────────────────────

def test_min_observations_for_power():
    assert config.MIN_OBSERVATIONS_FOR_POWER == 220

def test_min_observations_subperiod():
    assert config.MIN_OBSERVATIONS_SUBPERIOD == 60


# ── Portfolio construction ────────────────────────────────────────────────────

def test_min_weight():
    assert config.MIN_WEIGHT == 0.00

def test_max_weight():
    assert config.MAX_WEIGHT == 0.40

def test_target_volatility():
    assert config.TARGET_VOLATILITY == 0.10

def test_economic_significance_bps():
    assert config.ECONOMIC_SIGNIFICANCE_BPS == 50

def test_cv_stability_threshold():
    assert config.CV_STABILITY_THRESHOLD == 0.60

def test_expanding_wf_divergence():
    assert config.EXPANDING_WF_DIVERGENCE == 0.30


# ── Allowed emails ────────────────────────────────────────────────────────────

def test_allowed_emails_is_set():
    assert isinstance(config.ALLOWED_EMAILS, set)

def test_allowed_emails_count():
    """Exactly four authorised users — no exceptions."""
    assert len(config.ALLOWED_EMAILS) == 4

def test_allowed_emails_are_queens():
    for email in config.ALLOWED_EMAILS:
        assert email.endswith("@queens.edu"), f"{email} is not a queens.edu address"


# ── Stress scenarios ──────────────────────────────────────────────────────────

def test_stress_scenarios_count():
    assert len(config.STRESS_SCENARIOS) == 5

def test_stress_scenario_keys():
    expected = {"GFC_2008", "COVID_2020", "RATE_HIKE_2022", "DOTCOM_2000", "TAPER_TANTRUM"}
    assert set(config.STRESS_SCENARIOS.keys()) == expected

def test_stress_scenarios_are_tuples():
    for name, period in config.STRESS_SCENARIOS.items():
        assert isinstance(period, tuple), f"{name} period is not a tuple"
        assert len(period) == 2, f"{name} period does not have start and end"


# ── Asset universe ────────────────────────────────────────────────────────────

def test_benchmark_is_spy():
    assert config.BENCHMARK == "SPY"

def test_equities_list():
    assert isinstance(config.EQUITIES, list)
    assert "SPY" in config.EQUITIES

def test_fixed_income_list():
    assert isinstance(config.FIXED_INCOME, list)
    assert len(config.FIXED_INCOME) > 0

def test_alternatives_list():
    assert isinstance(config.ALTERNATIVES, list)
    assert "GLD" in config.ALTERNATIVES
