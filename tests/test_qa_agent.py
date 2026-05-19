"""
tests/test_qa_agent.py

Sprint 4 — QA Agent isolated tests.

The QA agent is the council's independent auditor. These tests verify
that its deterministic checks are correct (not hallucinated) and that
the limitations/caveats it generates are non-empty and well-formed.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, "backend")
os.environ.setdefault("ENVIRONMENT", "test")

# Strategy results with varying significance levels to exercise all branches
FULL_MOCK_RESULTS = {
    "BENCHMARK": {
        "strategy_name": "BENCHMARK",
        "strategy_type": "static",
        "sharpe_ratio": 0.522,
        "cagr": 0.086,
        "max_drawdown": -0.508,
        "volatility": 0.164,
        "is_significant": False,
        "p_value_ttest": 0.042,
        "p_value_corrected": 0.089,
        "dsr_p_value": 0.051,
        "oos_p_value": 0.061,
        "oos_sharpe": 0.480,
        "alpha_after_costs_bps": 0.0,
        "avg_monthly_turnover": 0.0,
        "cross_validation": {"cv_stability_score": 0.55},
        "deflated_sharpe_ratio": 0.40,
        "probabilistic_sharpe_ratio": 0.61,
    },
    "CLASSIC_60_40": {
        "strategy_name": "CLASSIC_60_40",
        "strategy_type": "static",
        "sharpe_ratio": 0.629,
        "cagr": 0.077,
        "max_drawdown": -0.327,
        "volatility": 0.122,
        "is_significant": True,
        "p_value_ttest": 0.003,
        "p_value_corrected": 0.004,
        "dsr_p_value": 0.003,
        "oos_p_value": 0.004,
        "oos_sharpe": 0.590,
        "alpha_after_costs_bps": 55.0,
        "avg_monthly_turnover": 0.05,
        "cross_validation": {"cv_stability_score": 0.72},
        "deflated_sharpe_ratio": 0.58,
        "probabilistic_sharpe_ratio": 0.81,
    },
    "VOL_TARGETING": {
        "strategy_name": "VOL_TARGETING",
        "strategy_type": "dynamic",
        "sharpe_ratio": 1.02,
        "cagr": 0.095,
        "max_drawdown": -0.183,
        "volatility": 0.093,
        "is_significant": True,
        "p_value_ttest": 0.001,
        "p_value_corrected": 0.003,
        "dsr_p_value": 0.002,
        "oos_p_value": 0.004,
        "oos_sharpe": 0.96,
        "alpha_after_costs_bps": 72.0,
        "avg_monthly_turnover": 0.12,
        "cross_validation": {"cv_stability_score": 0.81},
        "deflated_sharpe_ratio": 0.85,
        "probabilistic_sharpe_ratio": 0.94,
    },
}


@pytest.fixture
def qa():
    from agents.qa_agent import QAAgent
    return QAAgent()


class TestQADeterministicChecks:
    def test_deterministic_checks_returns_dict(self, qa):
        result = qa._run_deterministic_checks(FULL_MOCK_RESULTS)
        assert isinstance(result, dict)

    def test_all_gates_required_key_present(self, qa):
        result = qa._run_deterministic_checks(FULL_MOCK_RESULTS)
        assert "all_gates_required" in result

    def test_cv_stability_key_present(self, qa):
        result = qa._run_deterministic_checks(FULL_MOCK_RESULTS)
        assert "cv_stability" in result

    def test_alpha_after_costs_key_present(self, qa):
        result = qa._run_deterministic_checks(FULL_MOCK_RESULTS)
        assert "alpha_after_costs" in result

    def test_deflated_sharpe_key_present(self, qa):
        result = qa._run_deterministic_checks(FULL_MOCK_RESULTS)
        assert "deflated_sharpe" in result

    def test_probabilistic_sharpe_key_present(self, qa):
        result = qa._run_deterministic_checks(FULL_MOCK_RESULTS)
        assert "probabilistic_sharpe" in result

    def test_no_short_positions_key_present(self, qa):
        result = qa._run_deterministic_checks(FULL_MOCK_RESULTS)
        assert "no_short_positions" in result

    def test_weights_sum_key_present(self, qa):
        result = qa._run_deterministic_checks(FULL_MOCK_RESULTS)
        assert "weights_sum" in result

    def test_all_deterministic_check_values_are_dicts_with_status(self, qa):
        result = qa._run_deterministic_checks(FULL_MOCK_RESULTS)
        for key, val in result.items():
            assert isinstance(val, dict), f"Check '{key}' did not return a dict"
            assert "status" in val, f"Check '{key}' missing 'status'"
            assert val["status"] in ("PASS", "WARN", "FAIL"), (
                f"Check '{key}' has invalid status: {val['status']}"
            )


class TestQAAuditStructure:
    def test_run_audit_returns_dict(self, qa):
        result = qa.run_audit(FULL_MOCK_RESULTS, run_full_checklist=True)
        assert isinstance(result, dict)

    def test_all_checklist_items_always_present(self, qa):
        result = qa.run_audit(FULL_MOCK_RESULTS, run_full_checklist=True)
        assert len(result["items"]) == 39

    def test_item_statuses_sum_correctly(self, qa):
        result = qa.run_audit(FULL_MOCK_RESULTS, run_full_checklist=True)
        total = result["checks_passed"] + result["checks_warned"] + result["checks_failed"]
        assert total == 39

    def test_verdict_is_fail_if_any_fail(self, qa):
        # Inject a strategy with clearly problematic data
        bad_results = dict(FULL_MOCK_RESULTS)
        bad_results["BAD_STRATEGY"] = {
            "strategy_name": "BAD_STRATEGY",
            "strategy_type": "static",
            "sharpe_ratio": 5.0,  # implausibly high
            "is_significant": True,
            "p_value_ttest": 0.001,
            "p_value_corrected": 0.001,
            "dsr_p_value": 0.001,
            "oos_p_value": 0.001,
            "oos_sharpe": 4.8,
            "cagr": 0.50,
            "max_drawdown": -0.01,
            "volatility": 0.01,
            "alpha_after_costs_bps": 40.0,  # below 50bps threshold
            "avg_monthly_turnover": 0.5,
            "cross_validation": {"cv_stability_score": 0.45},  # below 0.60
            "deflated_sharpe_ratio": None,
            "probabilistic_sharpe_ratio": None,
        }
        result = qa.run_audit(bad_results, run_full_checklist=True)
        # WARN or FAIL expected — not PASS with implausible data
        assert result["verdict"] in ("WARN", "FAIL")


class TestQAPerCheckFiltering:
    """_parse_audit_response splits the QA analysis per check id — each
    item carries only its own section, and a check with no section gets
    an honest fallback message, never the whole analysis blob."""

    def test_check_with_a_section_shows_only_that_section(self, qa):
        response = (
            "**P03 — Transaction costs**\n"
            "Costs are applied bidirectionally.\nVerdict: PASS\n\n"
            "**S06 — Autocorrelation**\n"
            "Ljung-Box runs; Newey-West applied.\nVerdict: PASS\n"
        )
        result = qa._parse_audit_response(response, FULL_MOCK_RESULTS, {})
        p03 = next(i for i in result["items"] if i["check_id"] == "P03")
        assert "bidirectionally" in p03["evidence"]
        # Never another check's analysis.
        assert "Autocorrelation" not in p03["evidence"]

    def test_missing_section_falls_back_to_an_honest_message(self, qa):
        # Only P03 has a section — every other check must get the
        # fallback message, never the whole blob.
        response = (
            "**P03 — Transaction costs**\n"
            "Costs are applied bidirectionally.\nVerdict: PASS\n"
        )
        result = qa._parse_audit_response(response, FULL_MOCK_RESULTS, {})
        missing = [i for i in result["items"] if i["check_id"] != "P03"]
        assert missing
        for item in missing:
            assert item["evidence"] == (
                "The QA agent did not return analysis for this check. "
                "Re-run the QA audit to generate a full report.")

    def test_no_item_ever_shows_the_whole_blob(self, qa):
        response = (
            "**P03 — Transaction costs**\nCosts applied.\nVerdict: PASS\n"
        )
        result = qa._parse_audit_response(response, FULL_MOCK_RESULTS, {})
        for item in result["items"]:
            assert item["evidence"] != response

    def test_no_fix_carries_the_cross_reference_artifact(self, qa):
        response = (
            "**P03 — Transaction costs**\nCosts applied.\nVerdict: WARN\n"
        )
        result = qa._parse_audit_response(response, FULL_MOCK_RESULTS, {})
        for item in result["items"]:
            fix = item.get("fix")
            assert not (fix and "analysis section above" in fix)


class TestQALimitationsGeneration:
    def test_limitations_list_nonempty(self, qa):
        result = qa.run_audit(FULL_MOCK_RESULTS)
        assert isinstance(result["limitations"], list)
        assert len(result["limitations"]) >= 1

    def test_data_caveats_list_nonempty(self, qa):
        result = qa.run_audit(FULL_MOCK_RESULTS)
        assert isinstance(result["data_caveats"], list)
        assert len(result["data_caveats"]) >= 1

    def test_model_assumptions_list_nonempty(self, qa):
        result = qa.run_audit(FULL_MOCK_RESULTS)
        assert isinstance(result["model_assumptions"], list)
        assert len(result["model_assumptions"]) >= 1

    def test_all_limitation_strings_nonempty(self, qa):
        result = qa.run_audit(FULL_MOCK_RESULTS)
        for lim in result["limitations"]:
            assert isinstance(lim, str)
            assert len(lim) > 10

    def test_quick_audit_has_5_items(self, qa):
        """build_quick_audit() provides a 5-point fast sanity check."""
        result = qa._build_quick_audit(FULL_MOCK_RESULTS)
        assert len(result) == 5

    def test_quick_audit_items_have_status(self, qa):
        result = qa._build_quick_audit(FULL_MOCK_RESULTS)
        for item in result:
            assert item.get("status") in ("PASS", "WARN", "FAIL")


class TestQAChecklist:
    """Verify the checklist covers all required categories."""

    REQUIRED_CATEGORIES = {
        "DATA_INTEGRITY",
        "PORTFOLIO_MECHANICS",
        "STATISTICAL_INTEGRITY",
        "CROSS_VALIDATION",
        "OVERFITTING",
        "ECONOMIC_SIGNIFICANCE",
        "PRESENTATION",
    }

    def test_all_categories_represented(self, qa):
        from agents.qa_agent import _CHECKLIST_ITEMS
        categories = {item["category"] for item in _CHECKLIST_ITEMS}
        for cat in self.REQUIRED_CATEGORIES:
            assert cat in categories, f"Missing category: {cat}"

    def test_checklist_has_exactly_39_items(self, qa):
        from agents.qa_agent import _CHECKLIST_ITEMS
        assert len(_CHECKLIST_ITEMS) == 39

    def test_all_checklist_items_have_required_keys(self, qa):
        from agents.qa_agent import _CHECKLIST_ITEMS
        for item in _CHECKLIST_ITEMS:
            assert "check_id" in item
            assert "category" in item
            assert "check" in item
            assert "description" in item
