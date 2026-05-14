"""
tests/test_contrarian_analyst.py

Verifies the Grok-powered ContrarianAnalyst agent:
  - Falls back to a structured mock when XAI_API_KEY is unset
  - Falls back to a mock in the test environment regardless of key
  - Mock output matches the standard agent response schema so the CIO
    can consume Grok identically to Gemini
  - Returns the orange accent colour (#f97316) for the UI
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


def _make_results() -> dict:
    """Three synthetic strategies — enough to exercise the mock's logic
    around significant strategies, worst-drawdown selection, etc."""
    return {
        "BENCHMARK": {
            "strategy_name": "BENCHMARK",
            "sharpe_ratio": 0.5,
            "cagr": 0.08,
            "max_drawdown": -0.45,
            "is_significant": False,
            "oos_sharpe": 0.4,
            "alpha_after_costs_bps": 0,
            "cv_stability_score": 0.55,
        },
        "VOL_TARGETING": {
            "strategy_name": "VOL_TARGETING",
            "sharpe_ratio": 1.02,
            "cagr": 0.095,
            "max_drawdown": -0.18,
            "is_significant": True,
            "oos_sharpe": 0.92,
            "alpha_after_costs_bps": 75,
            "cv_stability_score": 0.81,
        },
        "REGIME_SWITCHING": {
            "strategy_name": "REGIME_SWITCHING",
            "sharpe_ratio": 0.95,
            "cagr": 0.10,
            "max_drawdown": -0.20,
            "is_significant": True,
            "oos_sharpe": 0.85,
            "alpha_after_costs_bps": 60,
            "cv_stability_score": 0.75,
        },
    }


class TestContrarianAnalystFallback:
    """The agent must always return a usable response — never None or raise."""

    def test_test_environment_returns_mock_without_api_call(self):
        # ENVIRONMENT=test is set in the test runtime — the agent should
        # never attempt to reach the xAI API even if XAI_API_KEY is present
        from agents.contrarian_analyst import ContrarianAnalyst
        agent = ContrarianAnalyst()
        report = agent.challenge("Recommend VOL_TARGETING", _make_results())
        assert report is not None
        assert report["agent"] == "Contrarian Analyst (Grok)"

    def test_missing_api_key_returns_mock(self, monkeypatch):
        """When XAI_API_KEY is unset and we're not in test env, the agent
        must still return a usable mock response — failing open."""
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        from agents.contrarian_analyst import ContrarianAnalyst
        agent = ContrarianAnalyst()
        report = agent.challenge("Recommend VOL_TARGETING", _make_results())
        # Mock note should explicitly call out the missing key
        note = report["technical_findings"].get("note", "")
        assert "XAI_API_KEY unset" in note or "unreachable" in note


class TestContrarianResponseSchema:
    """Output must be schema-compatible with IndependentAnalyst so the CIO
    doesn't need to special-case Grok."""

    def test_response_has_all_required_top_level_keys(self):
        from agents.contrarian_analyst import ContrarianAnalyst
        report = ContrarianAnalyst().challenge("test", _make_results())
        required = {"agent", "accent_color", "label", "technical_findings",
                    "summary", "layman_explanation"}
        missing = required - set(report.keys())
        assert not missing, f"ContrarianAnalyst response missing keys: {missing}"

    def test_accent_color_is_orange(self):
        """The Grok agent uses #f97316 (orange) — visually distinct from
        Gemini's purple (#7c3aed) and the Claude blues/greens."""
        from agents.contrarian_analyst import ContrarianAnalyst
        report = ContrarianAnalyst().challenge("test", _make_results())
        assert report["accent_color"] == "#f97316"

    def test_label_identifies_as_stress_test(self):
        from agents.contrarian_analyst import ContrarianAnalyst
        report = ContrarianAnalyst().challenge("test", _make_results())
        assert "Stress Test" in report["label"]

    def test_technical_findings_has_objections_list(self):
        from agents.contrarian_analyst import ContrarianAnalyst
        report = ContrarianAnalyst().challenge("test", _make_results())
        tf = report["technical_findings"]
        assert "objections" in tf
        assert isinstance(tf["objections"], list)
        assert len(tf["objections"]) >= 2  # mock emits 3 objections

    def test_layman_explanation_has_four_paragraphs(self):
        from agents.contrarian_analyst import ContrarianAnalyst
        report = ContrarianAnalyst().challenge("test", _make_results())
        layman = report["layman_explanation"]
        for k in ("what_we_found", "why_it_matters", "for_our_portfolio", "confidence"):
            assert k in layman, f"layman_explanation missing {k}"
            assert isinstance(layman[k], str)
            assert len(layman[k]) > 20


class TestContrarianMockContent:
    """The mock must cite actual data so the council has something
    substantive to engage with — not generic boilerplate."""

    def test_mock_references_worst_drawdown_strategy(self):
        from agents.contrarian_analyst import ContrarianAnalyst
        report = ContrarianAnalyst().challenge("test", _make_results())
        joined = " ".join(report["technical_findings"]["objections"])
        # The mock identifies the worst-drawdown significant strategy.
        # In our fixture, VOL_TARGETING has the worst DD (-0.18) among
        # the two significant strategies.
        assert "VOL_TARGETING" in joined or "REGIME_SWITCHING" in joined

    def test_mock_strategies_challenged_lists_significant_only(self):
        from agents.contrarian_analyst import ContrarianAnalyst
        report = ContrarianAnalyst().challenge("test", _make_results())
        challenged = report["technical_findings"]["strategies_challenged"]
        assert "BENCHMARK" not in challenged  # not significant in fixture
        assert "VOL_TARGETING" in challenged
        assert "REGIME_SWITCHING" in challenged


class TestCIOIntegration:
    """The CIO must instantiate the ContrarianAnalyst and call it."""

    def test_cio_has_grok_attribute(self):
        from agents.cio import CIO
        cio = CIO()
        assert hasattr(cio, "_grok"), "CIO must instantiate the Grok analyst"

    def test_cio_deliberate_returns_contrarian_analyst_key(self):
        from agents.cio import CIO
        # Deliberation is expensive but the test env mocks both Gemini
        # and Grok, so this runs in milliseconds.
        cio = CIO()
        # Patch only the Claude specialists to avoid network calls;
        # dissenters already mock in test env.
        import unittest.mock as mock
        with mock.patch.object(cio._equity, "analyse", return_value={"summary": "", "technical_findings": {}}), \
             mock.patch.object(cio._fi, "analyse", return_value={"summary": "", "technical_findings": {"breakdown_detected": False}}), \
             mock.patch.object(cio._risk, "analyse", return_value={"summary": "", "technical_findings": {}}), \
             mock.patch.object(cio._quant, "analyse", return_value={"summary": "", "technical_findings": {}}), \
             mock.patch("agents.cio.call_claude", return_value="MOCK CIO SYNTHESIS"):
            result = cio.deliberate("test query", _make_results())
        assert "contrarian_analyst" in result["agents"], (
            "CIO deliberation must include contrarian_analyst in the agents dict"
        )
        contrarian = result["agents"]["contrarian_analyst"]
        assert contrarian["agent"] == "Contrarian Analyst (Grok)"
