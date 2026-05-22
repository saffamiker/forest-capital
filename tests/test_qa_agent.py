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
        # May 22 2026 — INCOMPLETE is a fourth status (alongside PASS /
        # WARN / FAIL). The sum must include it for the total to match.
        # The _build_report assertion enforces this server-side too.
        result = qa.run_audit(FULL_MOCK_RESULTS, run_full_checklist=True)
        total = (result["checks_passed"]
                 + result["checks_warned"]
                 + result["checks_failed"]
                 + result["checks_incomplete"])
        assert total == 39
        assert result["checks_total"] == 39

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
        # May 22 2026 — a check with no section is INCOMPLETE, not WARN.
        # The earlier default (WARN, "The QA agent did not return
        # analysis…") was a false quality signal — it implied a concern
        # was found when in fact no examination took place. Now the
        # signal is "the audit did not finish this check", with status
        # INCOMPLETE and action_type rerun_required so the UI shows a
        # Re-run Audit affordance.
        response = (
            "**P03 — Transaction costs**\n"
            "Costs are applied bidirectionally.\nVerdict: PASS\n"
        )
        result = qa._parse_audit_response(response, FULL_MOCK_RESULTS, {})
        missing = [i for i in result["items"] if i["check_id"] != "P03"]
        assert missing
        for item in missing:
            assert item["evidence"] == (
                "Analysis not completed — re-run the QA audit to "
                "generate a full report.")
            assert item["status"] == "INCOMPLETE"
            # INCOMPLETE pairs with rerun_required so the UI shows the
            # Re-run Audit button rather than a Flag-for-Fix.
            assert item["action_type"] == "rerun_required"

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


class TestQAStructuredFields:
    """May 22 2026 — every WARN and FAIL section emits structured
    FINDING / IMPLICATION / REMEDIATION / ACTION_TYPE (and
    DISCLOSURE_TEXT when action_type=disclosure_required) labelled
    fields. The audit-response parser extracts these into per-item
    keys so the UI cards render Finding / Implication / Action
    Required sections separately. Pin the parser contract."""

    def test_extracts_structured_fields_from_a_warn_section(self, qa):
        from agents.qa_agent import _structured_fields_from_section
        section = (
            "**P03 — Transaction costs**\n"
            "Costs apply on both legs of every rebalance.\n"
            "FINDING: turnover sums |Δw| across all assets, capturing "
            "both the sell side and the buy side.\n"
            "IMPLICATION: could be intentional double-sided capture "
            "(correct) or accidental double-counting (wrong).\n"
            "REMEDIATION: confirm the design intent — if intentional, "
            "mark this as PASS with a methodology note; if accidental, "
            "halve the cost-drag formula.\n"
            "ACTION_TYPE: methodology_decision\n"
            "Verdict: WARN\n"
        )
        fields = _structured_fields_from_section(section)
        assert fields["finding"].startswith("turnover sums")
        assert "intentional double-sided" in fields["implication"]
        assert "confirm the design intent" in fields["remediation"]
        assert fields["action_type"] == "methodology_decision"
        assert fields["disclosure_text"] is None

    def test_extracts_disclosure_text_when_action_is_disclosure_required(self, qa):
        from agents.qa_agent import _structured_fields_from_section
        section = (
            "**D02 — No survivorship bias**\n"
            "Index reconstitution introduces minor survivorship effects.\n"
            "FINDING: the S&P 500 evolves through inclusions and "
            "deletions; the index has no surviving company that has "
            "been delisted, contributing a small upward bias.\n"
            "IMPLICATION: equity returns are marginally overstated.\n"
            "REMEDIATION: disclose in the methodology section.\n"
            "ACTION_TYPE: disclosure_required\n"
            "DISCLOSURE_TEXT: The S&P 500 series used in this analysis "
            "reflects post-reconstitution constituents and therefore "
            "carries a small survivorship bias; this is a known limit "
            "of the dataset and the magnitude is empirically small "
            "(roughly 0.1-0.2% per annum).\n"
            "Verdict: WARN\n"
        )
        fields = _structured_fields_from_section(section)
        assert fields["action_type"] == "disclosure_required"
        assert fields["disclosure_text"].startswith("The S&P 500 series")

    def test_rejects_unknown_action_type(self, qa):
        # The action_type set is locked. A model that hallucinates a
        # fifth value (e.g. "compliance_review") must produce None so
        # the UI does not render a button for an unknown variant.
        from agents.qa_agent import _structured_fields_from_section
        section = (
            "FINDING: something.\nIMPLICATION: matters.\n"
            "REMEDIATION: fix.\n"
            "ACTION_TYPE: compliance_review\nVerdict: WARN\n"
        )
        fields = _structured_fields_from_section(section)
        assert fields["action_type"] is None

    def test_empty_section_returns_all_nones(self, qa):
        from agents.qa_agent import _structured_fields_from_section
        fields = _structured_fields_from_section("")
        assert all(v is None for v in fields.values())

    def test_section_without_labelled_fields_returns_all_nones(self, qa):
        # PASS sections typically have no labelled fields — only the
        # evidence and the Verdict line. Every structured field must
        # return None so the UI suppresses the Action Required row.
        from agents.qa_agent import _structured_fields_from_section
        section = (
            "**C01 — Walk-forward**\nRolling and expanding windows both "
            "run.\nVerdict: PASS\n")
        fields = _structured_fields_from_section(section)
        assert all(v is None for v in fields.values())

    def test_parse_audit_response_threads_fields_into_items(self, qa):
        response = (
            "**P03 — Transaction costs**\n"
            "Costs apply on both legs of every rebalance.\n"
            "FINDING: turnover sums |Δw|, capturing both sides.\n"
            "IMPLICATION: design ambiguity.\n"
            "REMEDIATION: confirm intent.\n"
            "ACTION_TYPE: methodology_decision\n"
            "Verdict: WARN\n"
        )
        result = qa._parse_audit_response(response, FULL_MOCK_RESULTS, {})
        p03 = next(i for i in result["items"] if i["check_id"] == "P03")
        # The structured fields land as top-level keys on the item dict
        # (where the UI cards read them).
        assert p03["finding"] == "turnover sums |Δw|, capturing both sides."
        assert p03["implication"] == "design ambiguity."
        assert p03["remediation"] == "confirm intent."
        assert p03["action_type"] == "methodology_decision"
        assert p03["disclosure_text"] is None


class TestQAIncompleteStatus:
    """May 22 2026 contract — INCOMPLETE is a first-class status value
    alongside PASS / WARN / FAIL. It signals "the audit did not finish
    this check", NOT "this check has a concern". INCOMPLETE checks do
    not contribute to the WARN or FAIL totals; the report surfaces
    them in a separate checks_incomplete counter."""

    def test_incomplete_checks_count_separately(self, qa):
        # A response with only P03 leaves 38 checks without a section.
        # Those should all be INCOMPLETE — they do NOT inflate the
        # WARN total.
        response = "**P03 — Transaction costs**\nFine.\nVerdict: PASS\n"
        result = qa._parse_audit_response(response, FULL_MOCK_RESULTS, {})
        # P03 PASS; the 38 missing checks → INCOMPLETE (minus any
        # deterministic-check coverage, which override INCOMPLETE).
        assert result["checks_incomplete"] > 0
        # Sanity — the four counters sum to total.
        total = (result["checks_passed"] + result["checks_warned"]
                 + result["checks_failed"] + result["checks_incomplete"])
        assert total == result["checks_total"]

    def test_incomplete_does_not_drive_verdict(self, qa):
        # Even with many INCOMPLETE checks, if no FAIL and no WARN
        # exists, the verdict stays PASS. INCOMPLETE is not a quality
        # signal — it is an audit-completeness signal.
        response = "**P03 — Transaction costs**\nFine.\nVerdict: PASS\n"
        result = qa._parse_audit_response(response, FULL_MOCK_RESULTS, {})
        # The remaining checks are INCOMPLETE; no FAIL or WARN was
        # introduced. The verdict reflects the substance, not the gaps.
        assert result["checks_failed"] == 0
        # Note: deterministic checks may produce a WARN of their own
        # (e.g. AN05 / S08 / S09) — so verdict may be WARN, but it is
        # never driven by INCOMPLETE.
        assert result["verdict"] in ("PASS", "WARN")

    def test_explicit_incomplete_verdict_from_model_is_respected(self, qa):
        # If the model writes Verdict: INCOMPLETE explicitly, the
        # parser must respect it (not coerce to WARN).
        response = (
            "**P03 — Transaction costs**\n"
            "Insufficient evidence in this run.\n"
            "Verdict: INCOMPLETE\n"
        )
        result = qa._parse_audit_response(response, FULL_MOCK_RESULTS, {})
        p03 = next(i for i in result["items"] if i["check_id"] == "P03")
        assert p03["status"] == "INCOMPLETE"

    def test_summary_line_reports_incomplete_count_separately(self, qa):
        response = "**P03 — Transaction costs**\nFine.\nVerdict: PASS\n"
        result = qa._parse_audit_response(response, FULL_MOCK_RESULTS, {})
        if result["checks_incomplete"] > 0:
            # The summary line surfaces incompletes so the user sees
            # the gap rather than assuming the audit was complete.
            assert "incomplete" in result["summary"].lower()
            assert "re-run" in result["summary"].lower()

    def test_deterministic_audit_marks_non_det_checks_incomplete(self, qa):
        # When the LLM is unavailable, non-deterministic checks default
        # to INCOMPLETE (not WARN). This is the May 22 2026 contract
        # change — a baseless WARN is replaced with an honest
        # "audit did not run" signal.
        det = qa._run_deterministic_checks(FULL_MOCK_RESULTS)
        report = qa._build_deterministic_audit(det, FULL_MOCK_RESULTS)
        non_det_items = [
            i for i in report["items"]
            if i["status"] not in ("PASS",)
        ]
        # At least some non-deterministic checks must surface as
        # INCOMPLETE (with rerun_required action_type for the UI).
        incomplete = [i for i in non_det_items if i["status"] == "INCOMPLETE"]
        assert incomplete, "deterministic audit should flag non-det checks as INCOMPLETE"
        for item in incomplete:
            assert item["action_type"] == "rerun_required"


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
