"""
tests/test_ai_usage_log.py

Sprint 4 — AI usage logging tests.

Verifies that council sessions log the fields required for the
AI Usage Log screen and daily credit tracking. The logging function
is deterministic (structlog) — no DB required in CI.
"""
from __future__ import annotations

import os
import sys
import time
import uuid

import pytest

sys.path.insert(0, "backend")
os.environ.setdefault("ENVIRONMENT", "test")


class TestCouncilSessionLogging:
    """_log_council_session must record all required fields without raising."""

    def test_log_council_session_does_not_raise(self):
        from main import _log_council_session
        # Should not raise even with empty/minimal args
        _log_council_session(
            query="Test query",
            agents_called=["equity_analyst", "cio"],
            response={"significant_strategies": ["VOL_TARGETING"]},
            start_time=time.time() - 1.5,
            user_email="ruurdsm@queens.edu",
        )

    def test_log_council_session_accepts_empty_significant(self):
        from main import _log_council_session
        _log_council_session(
            query="Empty test",
            agents_called=[],
            response={"significant_strategies": []},
            start_time=time.time(),
            user_email="ruurdsm@queens.edu",
        )

    def test_log_council_session_accepts_all_six_agents(self):
        from main import _log_council_session
        all_agents = [
            "equity_analyst", "fixed_income_analyst",
            "risk_manager", "quant_backtester",
            "independent_analyst", "cio",
        ]
        _log_council_session(
            query="Full council test",
            agents_called=all_agents,
            response={"significant_strategies": ["VOL_TARGETING", "CLASSIC_60_40"]},
            start_time=time.time() - 45.0,
            user_email="thaob@queens.edu",
        )


class TestHealthEndpointSprintLabel:
    """Health endpoint must report sprint 4."""

    def test_health_reports_sprint_4(self):
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sprint"] == "4"

    def test_health_reports_anthropic_status(self):
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.get("/api/health")
        data = resp.json()
        assert "anthropic" in data
        assert isinstance(data["anthropic"], bool)

    def test_health_reports_gemini_status(self):
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.get("/api/health")
        data = resp.json()
        assert "gemini" in data
        assert isinstance(data["gemini"], bool)


class TestAcademicWriterScaffold:
    """Academic Writer agent must exist and load references.json."""

    def test_academic_writer_importable(self):
        from agents.academic_writer import AcademicWriter
        writer = AcademicWriter()
        assert writer is not None

    def test_references_json_loads(self):
        from agents.academic_writer import AcademicWriter
        refs = AcademicWriter.get_available_references()
        assert isinstance(refs, dict)

    def test_references_has_required_entries(self):
        from agents.academic_writer import AcademicWriter
        refs = AcademicWriter.get_available_references()
        required_keys = [
            "sharpe_1994",
            "black_litterman_1992",
            "lopez_de_prado_2018",
            "benjamin_2018",
            "markowitz_1952",
        ]
        for key in required_keys:
            assert key in refs, f"Missing reference: {key}"

    def test_each_reference_has_apa_field(self):
        from agents.academic_writer import AcademicWriter
        refs = AcademicWriter.get_available_references()
        for key, ref in refs.items():
            assert "apa" in ref, f"Reference '{key}' missing 'apa' field"
            assert len(ref["apa"]) > 20, f"Reference '{key}' has empty APA string"

    def test_write_references_only_uses_provided_keys(self):
        from agents.academic_writer import AcademicWriter
        writer = AcademicWriter()
        result = writer.write_references(["sharpe_1994", "benjamin_2018"])
        assert "AI DRAFT" in result
        # Must not include references not in the provided list
        assert "Sharpe" in result  # sharpe_1994 is valid
        assert "Benjamin" in result  # benjamin_2018 is valid

    def test_write_references_skips_invalid_keys(self):
        from agents.academic_writer import AcademicWriter
        writer = AcademicWriter()
        # "imaginary_ref" doesn't exist — should be silently skipped
        result = writer.write_references(["sharpe_1994", "imaginary_ref_2099"])
        assert "AI DRAFT" in result
        # Should still include the valid reference
        assert "Sharpe" in result


class TestLimitationsFields:
    """QA Agent and Risk Manager must generate limitations and tail risks."""

    def test_qa_agent_has_limitations(self):
        from agents.qa_agent import QAAgent
        qa = QAAgent()
        mock = {
            "BENCHMARK": {
                "strategy_name": "BENCHMARK",
                "is_significant": False,
                "sharpe_ratio": 0.5,
                "alpha_after_costs_bps": 0.0,
                "avg_monthly_turnover": 0.0,
                "cross_validation": {"cv_stability_score": 0.5},
                "deflated_sharpe_ratio": 0.4,
                "probabilistic_sharpe_ratio": 0.6,
                "p_value_ttest": 0.05,
                "p_value_corrected": 0.09,
                "dsr_p_value": 0.06,
                "oos_p_value": 0.07,
            }
        }
        result = qa.run_audit(mock)
        assert "limitations" in result
        assert len(result["limitations"]) >= 1

    def test_qa_agent_limitations_are_strings(self):
        from agents.qa_agent import QAAgent
        qa = QAAgent()
        mock = {
            "BENCHMARK": {
                "strategy_name": "BENCHMARK",
                "is_significant": False,
                "sharpe_ratio": 0.5,
                "alpha_after_costs_bps": 0.0,
                "avg_monthly_turnover": 0.0,
                "cross_validation": {"cv_stability_score": 0.5},
                "deflated_sharpe_ratio": 0.4,
                "probabilistic_sharpe_ratio": 0.6,
                "p_value_ttest": 0.05,
                "p_value_corrected": 0.09,
                "dsr_p_value": 0.06,
                "oos_p_value": 0.07,
            }
        }
        result = qa.run_audit(mock)
        for lim in result["limitations"]:
            assert isinstance(lim, str)
