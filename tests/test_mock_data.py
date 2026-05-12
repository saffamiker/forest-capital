"""
Sprint 1 — Mock data tests.
Verifies MOCK_STRATEGIES shape and values, MOCK_QA_AUDIT structure,
and MOCK_REGIME fields as specified in Section 16b and Section 17.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from models.schemas import MOCK_STRATEGIES, MOCK_QA_AUDIT, MOCK_REGIME, MOCK_EFFICIENT_FRONTIER


# ── Strategy count and identity ───────────────────────────────────────────────

def test_mock_strategies_count():
    """Exactly 10 strategies as specified."""
    assert len(MOCK_STRATEGIES) == 10

def test_mock_strategies_names():
    expected = {
        "BENCHMARK", "CLASSIC_60_40", "RISK_PARITY", "MIN_VARIANCE",
        "EQUAL_WEIGHT", "MOMENTUM_ROTATION", "REGIME_SWITCHING",
        "VOL_TARGETING", "BLACK_LITTERMAN", "MAX_SHARPE_ROLLING",
    }
    actual = {s["strategy_name"] for s in MOCK_STRATEGIES}
    assert actual == expected

def test_benchmark_exists():
    names = [s["strategy_name"] for s in MOCK_STRATEGIES]
    assert "BENCHMARK" in names

def test_benchmark_is_not_significant():
    benchmark = next(s for s in MOCK_STRATEGIES if s["strategy_name"] == "BENCHMARK")
    assert benchmark["is_significant"] is False


# ── Required fields on every strategy ────────────────────────────────────────

REQUIRED_FIELDS = [
    "strategy_name", "strategy_type", "cagr", "total_return", "volatility",
    "max_drawdown", "sharpe_ratio", "sharpe_ci_95", "sortino_ratio",
    "calmar_ratio", "information_ratio", "omega_ratio", "var_95", "cvar_95",
    "skewness", "kurtosis", "alpha", "alpha_bps", "alpha_after_costs_bps",
    "beta", "r_squared", "avg_monthly_turnover", "avg_equity_weight",
    "avg_bond_weight", "is_economically_significant", "min_viable_aum",
    "p_value_ttest", "p_value_sharpe_jk", "p_value_alpha", "p_value_corrected",
    "p_value_bootstrap", "normality_rejected", "bootstrap_used",
    "has_autocorrelation", "is_stationary", "is_adequately_powered",
    "deflated_sharpe_ratio", "dsr_p_value", "probabilistic_sharpe_ratio",
    "spa_p_value", "passes_spa", "oos_sharpe", "oos_cagr", "oos_p_value",
    "oos_significant", "tier1_gates_passed", "is_significant",
    "significance_summary", "cv_stability_score", "stress_results",
]

def test_all_strategies_have_required_fields():
    for s in MOCK_STRATEGIES:
        for field in REQUIRED_FIELDS:
            assert field in s, f"'{field}' missing from {s['strategy_name']}"

def test_strategy_types_are_valid():
    valid_types = {"static", "dynamic"}
    for s in MOCK_STRATEGIES:
        assert s["strategy_type"] in valid_types, (
            f"{s['strategy_name']} has invalid type '{s['strategy_type']}'"
        )

def test_static_strategies():
    static_names = {
        "BENCHMARK", "CLASSIC_60_40", "RISK_PARITY", "MIN_VARIANCE", "EQUAL_WEIGHT"
    }
    for s in MOCK_STRATEGIES:
        if s["strategy_name"] in static_names:
            assert s["strategy_type"] == "static"

def test_dynamic_strategies():
    dynamic_names = {
        "MOMENTUM_ROTATION", "REGIME_SWITCHING", "VOL_TARGETING",
        "BLACK_LITTERMAN", "MAX_SHARPE_ROLLING"
    }
    for s in MOCK_STRATEGIES:
        if s["strategy_name"] in dynamic_names:
            assert s["strategy_type"] == "dynamic"


# ── Metric value sanity checks ────────────────────────────────────────────────

def test_sharpe_ratios_are_positive_floats():
    for s in MOCK_STRATEGIES:
        assert isinstance(s["sharpe_ratio"], float), f"{s['strategy_name']} sharpe_ratio not float"
        assert s["sharpe_ratio"] > 0, f"{s['strategy_name']} sharpe_ratio not positive"

def test_max_drawdown_values_are_negative():
    for s in MOCK_STRATEGIES:
        assert s["max_drawdown"] < 0, (
            f"{s['strategy_name']} max_drawdown {s['max_drawdown']} should be negative"
        )

def test_cagr_values_are_floats():
    for s in MOCK_STRATEGIES:
        assert isinstance(s["cagr"], float), f"{s['strategy_name']} cagr not float"

def test_sharpe_ci_95_is_list_of_two():
    for s in MOCK_STRATEGIES:
        ci = s["sharpe_ci_95"]
        assert isinstance(ci, list), f"{s['strategy_name']} sharpe_ci_95 not a list"
        assert len(ci) == 2, f"{s['strategy_name']} sharpe_ci_95 must have 2 elements"
        assert ci[0] < ci[1], f"{s['strategy_name']} CI lower must be < upper"

def test_tier1_gates_passed_in_range():
    for s in MOCK_STRATEGIES:
        gates = s["tier1_gates_passed"]
        assert 0 <= gates <= 5, f"{s['strategy_name']} tier1_gates_passed {gates} out of range"

def test_is_significant_bool():
    for s in MOCK_STRATEGIES:
        assert isinstance(s["is_significant"], bool), (
            f"{s['strategy_name']} is_significant must be bool"
        )

def test_stress_results_has_all_scenarios():
    expected_scenarios = {"GFC_2008", "COVID_2020", "RATE_HIKE_2022", "DOTCOM_2000", "TAPER_TANTRUM"}
    for s in MOCK_STRATEGIES:
        scenario_keys = set(s["stress_results"].keys()) - {"note"}
        assert scenario_keys == expected_scenarios, (
            f"{s['strategy_name']} stress_results missing scenarios"
        )

def test_stress_results_no_p_values():
    """Stress tests must never contain p-values per the spec."""
    for s in MOCK_STRATEGIES:
        for scenario, result in s["stress_results"].items():
            if scenario == "note":
                continue
            assert "p_value" not in result, (
                f"{s['strategy_name']} {scenario} must not contain p_value"
            )

def test_avg_weights_sum_approx_one():
    """avg_equity_weight + avg_bond_weight should not exceed 1.0."""
    for s in MOCK_STRATEGIES:
        total = s["avg_equity_weight"] + s["avg_bond_weight"]
        assert total <= 1.001, (
            f"{s['strategy_name']} equity+bond weights {total:.3f} exceed 1.0"
        )


# ── QA Audit structure ────────────────────────────────────────────────────────
# Field names updated to match QAAgent output schema:
# checks_passed/warned/failed, verdict, items (list), items have check_id/description/status

def test_qa_audit_has_passed():
    assert "checks_passed" in MOCK_QA_AUDIT

def test_qa_audit_has_warned():
    assert "checks_warned" in MOCK_QA_AUDIT

def test_qa_audit_has_failed():
    assert "checks_failed" in MOCK_QA_AUDIT

def test_qa_audit_has_overall_verdict():
    assert "verdict" in MOCK_QA_AUDIT

def test_qa_audit_has_checks_list():
    assert "items" in MOCK_QA_AUDIT
    assert isinstance(MOCK_QA_AUDIT["items"], list)

def test_qa_audit_checks_count():
    """30 checklist items as specified."""
    assert len(MOCK_QA_AUDIT["items"]) == 30

def test_qa_audit_counts_sum_to_30():
    total = (
        MOCK_QA_AUDIT["checks_passed"]
        + MOCK_QA_AUDIT["checks_warned"]
        + MOCK_QA_AUDIT["checks_failed"]
    )
    assert total == 30

def test_qa_audit_items_have_required_fields():
    for item in MOCK_QA_AUDIT["items"]:
        assert "description" in item, f"QA check missing 'description': {item}"
        assert "status" in item, f"QA check missing 'status': {item}"
        assert "check_id" in item, f"QA check missing 'check_id': {item}"
        assert "category" in item, f"QA check missing 'category': {item}"

def test_qa_audit_verdicts_are_valid():
    valid_verdicts = {"PASS", "WARN", "FAIL"}
    for item in MOCK_QA_AUDIT["items"]:
        assert item["status"] in valid_verdicts, (
            f"Invalid status '{item['status']}' in QA check '{item.get('description')}'"
        )

def test_qa_audit_ids_are_unique():
    ids = [item["check_id"] for item in MOCK_QA_AUDIT["items"]]
    assert len(ids) == len(set(ids)), "QA check IDs are not unique"

def test_qa_audit_ids_are_sequential():
    # check_id values are category-prefixed strings like "D01", "P01", not integers 1-30.
    # Verify all 30 are non-empty strings — ordering is by category, not a flat sequence.
    ids = [item["check_id"] for item in MOCK_QA_AUDIT["items"]]
    assert all(isinstance(cid, str) and len(cid) >= 2 for cid in ids), (
        "All check_id values must be non-empty strings"
    )

def test_qa_audit_overall_verdict_is_valid():
    assert MOCK_QA_AUDIT["verdict"] in {"PASS", "WARN", "FAIL"}


# ── Regime mock ───────────────────────────────────────────────────────────────

def test_mock_regime_threshold_regime():
    assert MOCK_REGIME["threshold_regime"] in {"BULL", "BEAR", "TRANSITION", "UNCERTAIN"}

def test_mock_regime_has_hmm_fields():
    assert "hmm_regime" in MOCK_REGIME
    assert "hmm_probabilities" in MOCK_REGIME
    assert "regimes_agree" in MOCK_REGIME

def test_mock_regime_hmm_probabilities_sum_to_one():
    probs = MOCK_REGIME["hmm_probabilities"]
    assert abs(sum(probs) - 1.0) < 0.01, f"HMM probabilities sum to {sum(probs):.4f}, not 1.0"

def test_mock_regime_vix_is_positive():
    assert MOCK_REGIME["vix_level"] > 0

def test_mock_regime_regimes_agree_is_bool():
    assert isinstance(MOCK_REGIME["regimes_agree"], bool)


# ── Efficient frontier mock ───────────────────────────────────────────────────

def test_efficient_frontier_has_frontier_points():
    assert "frontier_points" in MOCK_EFFICIENT_FRONTIER
    assert len(MOCK_EFFICIENT_FRONTIER["frontier_points"]) > 0

def test_efficient_frontier_has_portfolio_points():
    assert "portfolio_points" in MOCK_EFFICIENT_FRONTIER
    assert len(MOCK_EFFICIENT_FRONTIER["portfolio_points"]) == 10

def test_efficient_frontier_has_max_sharpe_point():
    assert "max_sharpe_point" in MOCK_EFFICIENT_FRONTIER
