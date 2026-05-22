"""
tests/test_agents.py

Sprint 4 — agent schema and response quality tests.

Each agent is instantiated in isolation with minimal mock strategy results.
Tests verify the response schema rather than the analytical content —
we cannot test the LLM narrative, but we can guarantee the structure
the frontend depends on is always present.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, "backend")
os.environ.setdefault("ENVIRONMENT", "test")

# Minimal strategy results matching the backtester schema.
# Enough fields to exercise every agent without running the full pipeline.
MOCK_RESULTS = {
    "BENCHMARK": {
        "strategy_name": "BENCHMARK",
        "strategy_type": "static",
        "sharpe_ratio": 0.522,
        "cagr": 0.0858,
        "max_drawdown": -0.508,
        "volatility": 0.164,
        "is_significant": False,
        "p_value_ttest": 0.042,
        "p_value_corrected": 0.089,
        "dsr_p_value": 0.051,
        "oos_p_value": 0.061,
        "oos_sharpe": 0.480,
        "oos_cagr": 0.079,
        "alpha_after_costs_bps": 0.0,
        "avg_monthly_turnover": 0.0,
        # BENCHMARK never rebalances → genuine annualised one-way
        # turnover is exactly zero.
        "true_turnover": 0.0,
        "avg_bond_weight": 0.0,
        "avg_equity_weight": 1.0,
        "cross_validation": {"cv_stability_score": 0.55},
        "deflated_sharpe_ratio": 0.40,
        "probabilistic_sharpe_ratio": 0.61,
        "stress_results": {
            "RATE_HIKE_2022": {"return": -0.186, "max_dd": -0.258, "vs_benchmark": 0.0}
        },
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
        "oos_cagr": 0.088,
        "alpha_after_costs_bps": 72.0,
        "avg_monthly_turnover": 0.12,
        # 42% genuine annualised one-way turnover — typical for a
        # dynamic vol-targeting strategy that rescales each month.
        "true_turnover": 0.42,
        "avg_bond_weight": 0.35,
        "avg_equity_weight": 0.65,
        "cross_validation": {"cv_stability_score": 0.81},
        "deflated_sharpe_ratio": 0.85,
        "probabilistic_sharpe_ratio": 0.94,
        "stress_results": {
            "RATE_HIKE_2022": {"return": -0.062, "max_dd": -0.089, "vs_benchmark": 0.124}
        },
    },
}


class TestEquityAnalyst:
    def test_returns_dict(self):
        from agents.equity_analyst import EquityAnalyst
        agent = EquityAnalyst()
        result = agent.analyse(MOCK_RESULTS)
        assert isinstance(result, dict)

    def test_has_required_schema_keys(self):
        from agents.equity_analyst import EquityAnalyst
        agent = EquityAnalyst()
        result = agent.analyse(MOCK_RESULTS)
        assert "technical_findings" in result
        assert "summary" in result
        assert "layman_explanation" in result

    def test_summary_is_nonempty_string(self):
        from agents.equity_analyst import EquityAnalyst
        agent = EquityAnalyst()
        result = agent.analyse(MOCK_RESULTS)
        assert isinstance(result["summary"], str)
        assert len(result["summary"]) > 0

    def test_layman_explanation_has_four_fields(self):
        from agents.equity_analyst import EquityAnalyst
        agent = EquityAnalyst()
        result = agent.analyse(MOCK_RESULTS)
        le = result["layman_explanation"]
        assert "what_we_found" in le
        assert "why_it_matters" in le
        assert "for_our_portfolio" in le
        assert "confidence" in le

    def test_all_layman_fields_nonempty(self):
        from agents.equity_analyst import EquityAnalyst
        agent = EquityAnalyst()
        result = agent.analyse(MOCK_RESULTS)
        le = result["layman_explanation"]
        for field in ("what_we_found", "why_it_matters", "for_our_portfolio", "confidence"):
            assert isinstance(le[field], str)
            assert len(le[field]) > 0


class TestFixedIncomeAnalyst:
    def test_returns_dict(self):
        from agents.fixed_income_analyst import FixedIncomeAnalyst
        agent = FixedIncomeAnalyst()
        result = agent.analyse(MOCK_RESULTS)
        assert isinstance(result, dict)

    def test_has_required_schema_keys(self):
        from agents.fixed_income_analyst import FixedIncomeAnalyst
        agent = FixedIncomeAnalyst()
        result = agent.analyse(MOCK_RESULTS)
        assert "technical_findings" in result
        assert "summary" in result
        assert "layman_explanation" in result

    def test_summary_nonempty(self):
        from agents.fixed_income_analyst import FixedIncomeAnalyst
        agent = FixedIncomeAnalyst()
        result = agent.analyse(MOCK_RESULTS)
        assert isinstance(result["summary"], str) and len(result["summary"]) > 0

    def test_breakdown_detected_is_bool(self):
        from agents.fixed_income_analyst import FixedIncomeAnalyst
        agent = FixedIncomeAnalyst()
        result = agent.analyse(MOCK_RESULTS)
        tf = result["technical_findings"]
        assert isinstance(tf.get("breakdown_detected"), bool)

    def test_correlation_without_history_returns_available_false(self):
        from agents.fixed_income_analyst import FixedIncomeAnalyst
        agent = FixedIncomeAnalyst()
        corr = agent._compute_correlation_summary(None)
        assert corr["available"] is False


class TestRiskManager:
    def test_returns_dict(self):
        from agents.risk_manager import RiskManager
        agent = RiskManager()
        result = agent.analyse(MOCK_RESULTS)
        assert isinstance(result, dict)

    def test_has_required_schema_keys(self):
        from agents.risk_manager import RiskManager
        agent = RiskManager()
        result = agent.analyse(MOCK_RESULTS)
        assert "technical_findings" in result
        assert "summary" in result
        assert "layman_explanation" in result

    def test_n_significant_correct(self):
        from agents.risk_manager import RiskManager
        agent = RiskManager()
        result = agent.analyse(MOCK_RESULTS)
        tf = result["technical_findings"]
        assert tf["n_strategies_significant"] == 1  # Only VOL_TARGETING is significant

    def test_significant_strategies_list(self):
        from agents.risk_manager import RiskManager
        agent = RiskManager()
        result = agent.analyse(MOCK_RESULTS)
        tf = result["technical_findings"]
        assert "VOL_TARGETING" in tf["significant_strategies"]
        assert "BENCHMARK" not in tf["significant_strategies"]


class TestQuantBacktester:
    def test_returns_dict(self):
        from agents.quant_backtester import QuantBacktester
        agent = QuantBacktester()
        result = agent.analyse(MOCK_RESULTS)
        assert isinstance(result, dict)

    def test_has_required_schema_keys(self):
        from agents.quant_backtester import QuantBacktester
        agent = QuantBacktester()
        result = agent.analyse(MOCK_RESULTS)
        assert "technical_findings" in result
        assert "summary" in result
        assert "layman_explanation" in result

    def test_oos_comparison_computed(self):
        from agents.quant_backtester import QuantBacktester
        agent = QuantBacktester()
        result = agent.analyse(MOCK_RESULTS)
        tf = result["technical_findings"]
        assert "oos_comparison" in tf

    def test_overfitting_flag_logic(self):
        # VOL_TARGETING: IS=1.02, OOS=0.96, degradation=0.059 < 0.20 → not overfitted
        from agents.quant_backtester import QuantBacktester
        agent = QuantBacktester()
        summary = agent._compute_quant_summary(MOCK_RESULTS)
        vt = summary["oos_comparison"]["VOL_TARGETING"]
        assert vt["potentially_overfitted"] is False

    def test_cost_drag_uses_true_turnover_not_avg_monthly(self):
        # The cost-drag arithmetic must read true_turnover (annualised
        # one-way) rather than the legacy avg_monthly_turnover proxy
        # (a rebalance-count). VOL_TARGETING has true_turnover=0.42 and
        # avg_monthly_turnover=0.12 in the mock — the two would yield
        # very different cost figures, so this assertion catches a
        # silent regression to the old field.
        from agents.quant_backtester import QuantBacktester
        from config import TRANSACTION_COST_BPS
        agent = QuantBacktester()
        summary = agent._compute_quant_summary(MOCK_RESULTS)
        vt = summary["oos_comparison"]["VOL_TARGETING"]
        # true_turnover=0.42, cost=TRANSACTION_COST_BPS → 0.42 × cost
        assert vt["cost_drag_bps_year"] == pytest.approx(
            0.42 * TRANSACTION_COST_BPS, rel=0.01)

    def test_cost_drag_is_not_multiplied_by_12(self):
        # true_turnover is ALREADY annualised — multiplying by 12 was
        # the bug in the legacy path that treated a monthly proxy as
        # if it were a monthly rate. Verify the formula does not
        # double-annualise.
        from agents.quant_backtester import QuantBacktester
        from config import TRANSACTION_COST_BPS
        agent = QuantBacktester()
        summary = agent._compute_quant_summary(MOCK_RESULTS)
        vt = summary["oos_comparison"]["VOL_TARGETING"]
        # If the cost-drag were still ×12 the figure would be ~50 bps;
        # the correct figure for a 42% turnover at 10bps is ~4.2 bps.
        wrong_old_formula = 0.42 * TRANSACTION_COST_BPS * 12
        correct_formula = 0.42 * TRANSACTION_COST_BPS
        assert vt["cost_drag_bps_year"] != pytest.approx(
            wrong_old_formula, rel=0.01)
        assert vt["cost_drag_bps_year"] == pytest.approx(
            correct_formula, rel=0.01)

    def test_llm_context_carries_true_turnover_field(self):
        # The Sonnet agent's review prompt must narrate on the genuine
        # turnover figure, not the legacy proxy. _build_context emits a
        # JSON blob — every per-strategy block must carry true_turnover
        # and must NOT carry avg_monthly_turnover (which would let the
        # model reach back to the wrong field).
        import json
        from agents.quant_backtester import QuantBacktester
        agent = QuantBacktester()
        summary = agent._compute_quant_summary(MOCK_RESULTS)
        ctx_json = agent._build_context(MOCK_RESULTS, summary)
        ctx = json.loads(ctx_json)
        for name in ("BENCHMARK", "VOL_TARGETING"):
            block = ctx["strategy_metrics"][name]
            assert "true_turnover" in block, (
                f"{name} must carry true_turnover in the LLM context")
            assert "avg_monthly_turnover" not in block, (
                f"{name} must NOT carry the legacy avg_monthly_turnover")
        # The value passed for VOL_TARGETING is the mock's true_turnover.
        assert ctx["strategy_metrics"]["VOL_TARGETING"][
            "true_turnover"] == pytest.approx(0.42, rel=0.01)


class TestIndependentAnalyst:
    def test_returns_dict_in_test_env(self):
        from agents.independent_analyst import IndependentAnalyst
        agent = IndependentAnalyst()
        result = agent.challenge("The council recommends VOL_TARGETING.", MOCK_RESULTS)
        assert isinstance(result, dict)

    def test_has_agent_key(self):
        from agents.independent_analyst import IndependentAnalyst
        agent = IndependentAnalyst()
        result = agent.challenge("Council summary.", MOCK_RESULTS)
        assert result.get("agent") == "Independent Analyst (Gemini)"

    def test_has_accent_color(self):
        from agents.independent_analyst import IndependentAnalyst
        agent = IndependentAnalyst()
        result = agent.challenge("Council summary.", MOCK_RESULTS)
        assert result.get("accent_color") == "#7c3aed"

    def test_has_label(self):
        from agents.independent_analyst import IndependentAnalyst
        agent = IndependentAnalyst()
        result = agent.challenge("Council summary.", MOCK_RESULTS)
        assert "Dissenting View" in result.get("label", "")

    def test_technical_findings_has_objections(self):
        from agents.independent_analyst import IndependentAnalyst
        agent = IndependentAnalyst()
        result = agent.challenge("Council summary.", MOCK_RESULTS)
        tf = result.get("technical_findings", {})
        assert isinstance(tf.get("objections"), list)
        assert len(tf["objections"]) > 0

    def test_mock_challenge_grounds_in_data(self):
        # Mock challenge should reference actual data — not generic boilerplate
        from agents.independent_analyst import IndependentAnalyst
        agent = IndependentAnalyst()
        result = agent._mock_challenge("consensus", MOCK_RESULTS)
        objections = result["technical_findings"]["objections"]
        # At least one objection should be non-empty
        assert any(len(o) > 20 for o in objections)


class TestQAAgent:
    def test_returns_dict(self):
        from agents.qa_agent import QAAgent
        agent = QAAgent()
        result = agent.run_audit(MOCK_RESULTS, run_full_checklist=True)
        assert isinstance(result, dict)

    def test_has_checks_summary(self):
        from agents.qa_agent import QAAgent
        agent = QAAgent()
        result = agent.run_audit(MOCK_RESULTS)
        assert "checks_passed" in result
        assert "checks_warned" in result
        assert "checks_failed" in result

    def test_passes_plus_warned_plus_failed_equals_total(self):
        # May 22 2026 — INCOMPLETE is a fourth status alongside PASS /
        # WARN / FAIL. The sum must include it for the total to match.
        # The QAAgent._build_report assertion enforces this server-side
        # so a parser bug surfaces immediately; this assertion mirrors
        # it on the client side.
        from agents.qa_agent import QAAgent
        agent = QAAgent()
        result = agent.run_audit(MOCK_RESULTS, run_full_checklist=True)
        total = (result["checks_passed"]
                 + result["checks_warned"]
                 + result["checks_failed"]
                 + result.get("checks_incomplete", 0))
        assert total == 39

    def test_has_limitations_list(self):
        from agents.qa_agent import QAAgent
        agent = QAAgent()
        result = agent.run_audit(MOCK_RESULTS)
        assert isinstance(result.get("limitations"), list)
        assert len(result["limitations"]) > 0

    def test_has_data_caveats_list(self):
        from agents.qa_agent import QAAgent
        agent = QAAgent()
        result = agent.run_audit(MOCK_RESULTS)
        assert isinstance(result.get("data_caveats"), list)
        assert len(result["data_caveats"]) > 0

    def test_has_model_assumptions_list(self):
        from agents.qa_agent import QAAgent
        agent = QAAgent()
        result = agent.run_audit(MOCK_RESULTS)
        assert isinstance(result.get("model_assumptions"), list)
        assert len(result["model_assumptions"]) > 0

    def test_verdict_is_valid_value(self):
        from agents.qa_agent import QAAgent
        agent = QAAgent()
        result = agent.run_audit(MOCK_RESULTS)
        assert result.get("verdict") in ("PASS", "WARN", "FAIL")

    def test_items_list_has_all_entries(self):
        from agents.qa_agent import QAAgent
        agent = QAAgent()
        result = agent.run_audit(MOCK_RESULTS, run_full_checklist=True)
        assert len(result.get("items", [])) == 39

    def test_each_item_has_required_keys(self):
        # May 22 2026 — INCOMPLETE is a fourth first-class status value
        # alongside PASS / WARN / FAIL. A check the agent could not
        # examine surfaces as INCOMPLETE, NOT a baseless WARN (which
        # was the prior false-quality-signal bug fixed in ace4150).
        from agents.qa_agent import QAAgent
        agent = QAAgent()
        result = agent.run_audit(MOCK_RESULTS, run_full_checklist=True)
        for item in result.get("items", []):
            assert "check_id" in item
            assert "category" in item
            assert "check" in item
            assert "status" in item
            assert item["status"] in (
                "PASS", "WARN", "FAIL", "INCOMPLETE")

    def test_deterministic_checks_override_llm(self):
        """
        is_significant=True for both strategies but BENCHMARK is False —
        the QA should flag that BENCHMARK is correctly excluded from
        significant strategies. Deterministic checks run before LLM.
        """
        from agents.qa_agent import QAAgent
        agent = QAAgent()
        checks = agent._run_deterministic_checks(MOCK_RESULTS)
        # all_gates_required: all Tier 1 gates must pass for is_significant=True
        # VOL_TARGETING has all fields present so this should pass
        assert "all_gates_required" in checks


class TestSpecialistQueryThreading:
    """May 22 2026 — Molly UAT FAILURE GROUP 1.

    The four Claude specialist agents previously did not accept the
    user's query at all; CIO.deliberate called .analyse(strategy_results)
    only. Result: any meta question ("what would a peer reviewer ask?",
    "how should I frame the methodology section?") returned a stock
    strategy analysis that looked like the council was answering the
    PREVIOUS question. The fix threads `query` through every specialist's
    .analyse() signature, and each specialist injects it into the LLM
    user_message so the prompt names the question explicitly.

    These tests pin the contract — a future refactor that drops the
    parameter (or stops surfacing it in the prompt) trips here, not in
    Molly's next UAT pass."""

    @staticmethod
    def _capture_prompt(monkeypatch, agent_module):
        """Mocks GeneratorEvaluatorHarness.run on the agent module so the
        prompt argument is captured rather than running the LLM. Returns
        a one-element list the test inspects after .analyse()."""
        captured: list[str] = []

        class _StubResult:
            response = "stub LLM response"

        def _stub_run(self, **kwargs):  # noqa: ANN001 — kwargs-only signature
            captured.append(kwargs["generator_prompt"])
            return _StubResult()

        monkeypatch.setattr(agent_module.GeneratorEvaluatorHarness,
                            "run", _stub_run)
        return captured

    def test_equity_analyst_includes_query_in_prompt(self, monkeypatch):
        from agents import equity_analyst as ea
        captured = self._capture_prompt(monkeypatch, ea)
        ea.EquityAnalyst().analyse(
            MOCK_RESULTS,
            query="What questions would a peer reviewer ask about regime methodology?",
        )
        assert captured, "harness.run was never called"
        prompt = captured[0]
        assert "peer reviewer" in prompt
        assert "regime methodology" in prompt
        # The USER QUESTION header is the explicit framing — pin it.
        assert "USER QUESTION:" in prompt

    def test_fixed_income_analyst_includes_query_in_prompt(self, monkeypatch):
        from agents import fixed_income_analyst as fi
        captured = self._capture_prompt(monkeypatch, fi)
        fi.FixedIncomeAnalyst().analyse(
            MOCK_RESULTS, history=None,
            query="Explain the bond diversification story to a panellist.",
        )
        assert captured
        assert "bond diversification" in captured[0]
        assert "panellist" in captured[0]

    def test_risk_manager_includes_query_in_prompt(self, monkeypatch):
        from agents import risk_manager as rm
        captured = self._capture_prompt(monkeypatch, rm)
        rm.RiskManager().analyse(
            MOCK_RESULTS,
            query="What tail-risk caveats should be in the brief?",
        )
        assert captured
        assert "tail-risk" in captured[0]
        assert "brief" in captured[0]

    def test_quant_backtester_includes_query_in_prompt(self, monkeypatch):
        from agents import quant_backtester as qb
        captured = self._capture_prompt(monkeypatch, qb)
        qb.QuantBacktester().analyse(
            MOCK_RESULTS,
            query="How robust is REGIME_SWITCHING out-of-sample?",
        )
        assert captured
        assert "REGIME_SWITCHING" in captured[0]
        assert "out-of-sample" in captured[0]

    def test_empty_query_omits_question_header(self, monkeypatch):
        """An empty query (the test-suite default and the back-compat
        path for any caller that doesn't pass one) must NOT prepend the
        USER QUESTION block — the council's behaviour stays bitwise
        identical to the pre-fix path when query=""."""
        from agents import equity_analyst as ea
        captured = self._capture_prompt(monkeypatch, ea)
        ea.EquityAnalyst().analyse(MOCK_RESULTS, query="")
        assert captured
        assert "USER QUESTION:" not in captured[0]


class TestCIO:
    def test_deliberate_returns_dict(self):
        from agents.cio import CIO
        cio = CIO()
        result = cio.deliberate("Which strategies should I use?", MOCK_RESULTS)
        assert isinstance(result, dict)

    def test_deliberate_threads_query_to_all_specialists(self, monkeypatch):
        """The CIO must pass the query to every specialist. Without this,
        the four parallel .analyse() workers do not see the question and
        produce stock analyses regardless of what the user asked — the
        Molly UAT failure root cause."""
        from agents import cio as cio_mod
        seen: dict[str, str] = {}

        def _stub_specialist(name):
            def _do_analyse(*args, **kwargs):
                seen[name] = kwargs.get("query", "")
                return {"summary": f"{name} stub",
                        "technical_findings": {}, "layman_explanation": {
                            "what_we_found": "x", "why_it_matters": "y",
                            "for_our_portfolio": "z", "confidence": "w"}}
            return _do_analyse

        cio = cio_mod.CIO()
        monkeypatch.setattr(cio._equity, "analyse", _stub_specialist("equity"))
        monkeypatch.setattr(cio._fi, "analyse", _stub_specialist("fi"))
        monkeypatch.setattr(cio._risk, "analyse", _stub_specialist("risk"))
        monkeypatch.setattr(cio._quant, "analyse", _stub_specialist("quant"))
        # Stub the downstream calls so the test exits after the
        # specialist fan-out without hitting Gemini / Grok / Opus.
        monkeypatch.setattr(cio, "_compile_draft_consensus",
                            lambda *a, **kw: "draft")
        monkeypatch.setattr(cio._gemini, "challenge",
                            lambda *a, **kw: {"technical_findings": {"objections": []},
                                              "summary": "g", "layman_explanation": {
                                                  "what_we_found": "x", "why_it_matters": "y",
                                                  "for_our_portfolio": "z", "confidence": "w"}})
        monkeypatch.setattr(cio._grok, "challenge",
                            lambda *a, **kw: {"technical_findings": {"objections": []},
                                              "summary": "g", "layman_explanation": {
                                                  "what_we_found": "x", "why_it_matters": "y",
                                                  "for_our_portfolio": "z", "confidence": "w"}})
        monkeypatch.setattr(cio, "_synthesise", lambda *a, **kw: {
            "technical_findings": {}, "summary": "syn",
            "layman_explanation": {"what_we_found": "x", "why_it_matters": "y",
                                   "for_our_portfolio": "z", "confidence": "w"}})

        cio.deliberate("CENTRAL FINDING TEST QUERY", MOCK_RESULTS)
        # All four specialists must have received the exact query the
        # CIO was called with — no truncation, no rewording, no empty
        # fall-through.
        assert seen == {
            "equity": "CENTRAL FINDING TEST QUERY",
            "fi": "CENTRAL FINDING TEST QUERY",
            "risk": "CENTRAL FINDING TEST QUERY",
            "quant": "CENTRAL FINDING TEST QUERY",
        }

    def test_has_agents_key(self):
        from agents.cio import CIO
        cio = CIO()
        result = cio.deliberate("Best strategies?", MOCK_RESULTS)
        assert "agents" in result

    def test_agents_has_all_specialists(self):
        from agents.cio import CIO
        cio = CIO()
        result = cio.deliberate("Best strategies?", MOCK_RESULTS)
        agents = result["agents"]
        for expected in ("equity_analyst", "fixed_income_analyst", "risk_manager",
                         "quant_backtester", "independent_analyst", "cio"):
            assert expected in agents

    def test_significant_strategies_matches_data(self):
        from agents.cio import CIO
        cio = CIO()
        result = cio.deliberate("Which are significant?", MOCK_RESULTS)
        assert "VOL_TARGETING" in result["significant_strategies"]
        assert "BENCHMARK" not in result["significant_strategies"]

    def test_get_significant_static_method(self):
        from agents.cio import CIO
        sig = CIO._get_significant(MOCK_RESULTS)
        assert sig == ["VOL_TARGETING"]


class TestExplainerAgent:
    def test_fallback_terms_covers_every_glossary_key(self):
        # The fallback is the safety net — it must resolve every key that
        # ExplainableText looks up, so no wrapped term is ever "dark".
        from agents.explainer_agent import (
            _GLOSSARY_TERM_KEYS, ExplainerAgent,
        )
        agent = ExplainerAgent()
        result = agent._fallback_terms()
        assert isinstance(result, dict)
        for key in _GLOSSARY_TERM_KEYS:
            assert key in result, f"fallback missing {key}"
            entry = result[key]
            for field in ("hover", "what", "why", "this_session"):
                assert entry.get(field), f"{key}.{field} empty"

    def test_glossary_term_keys_include_the_required_set(self):
        # The 19 keys the four ExplainableText components hard-code.
        from agents.explainer_agent import _GLOSSARY_TERM_KEYS
        required = {
            "cagr", "sharpe_ratio", "sharpe_ci", "max_drawdown",
            "volatility", "turnover", "tier", "dsr", "p_fdr", "cv_score",
            "tier1_gates", "tier1_t_test", "tier1_fdr_correction",
            "tier1_dsr", "tier1_oos", "tier1_cv", "walk_forward_oos",
            "regime_classification", "equity_bond_correlation_breakdown",
        }
        assert required <= set(_GLOSSARY_TERM_KEYS)

    def test_fallback_chart_returns_required_keys(self):
        from agents.explainer_agent import ExplainerAgent
        agent = ExplainerAgent()
        result = agent._fallback_chart("cumulative_returns", "line", ["VOL_TARGETING"])
        assert "chart_id" in result
        assert "hover_summary" in result
        assert "key_callouts" in result
        assert "narrative" in result

    def test_explain_terms_returns_every_glossary_key(self):
        """explain_terms merges the LLM result over the fallback, so every
        _GLOSSARY_TERM_KEYS key resolves regardless of the LLM."""
        from agents.explainer_agent import (
            _GLOSSARY_TERM_KEYS, ExplainerAgent,
        )
        agent = ExplainerAgent()
        result = agent.explain_terms({
            "significant_strategies": ["VOL_TARGETING"], "agents": {},
        })
        assert isinstance(result, dict)
        for key in _GLOSSARY_TERM_KEYS:
            assert key in result, f"explain_terms missing {key}"
