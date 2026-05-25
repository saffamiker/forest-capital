"""
tests/test_chart_vision_wiring.py

Tests that pin the EVALUATOR GUARD contract for chart vision —
the generator path injects visual_context (multi-block content
including chart images), and the evaluator path never does
(string content only).

This is the cross-cutting contract that makes chart vision safe
to enable: visual context improves the GENERATED text, but if it
leaked into the evaluator it would muddle the text-quality signal
the harness scores against.

The unit-level fail-open contract for the snapshot reader lives in
test_chart_vision.py. The hash-skip guard for the renderer lives in
test_chart_snapshots.py. This module covers ONLY the wiring between
the two — that the generators in the council, academic review, and
academic writer pass visual_context, and that the harness evaluator
does not.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")


@pytest.fixture
def snapshot_dir(tmp_path, monkeypatch):
    """Redirect chart_vision to a temp directory with two fake PNGs so
    get_charts_for_context returns non-empty content blocks. Without
    on-disk snapshots the wiring falls back to visual_context=None,
    and we couldn't tell whether the generator wired the kwarg at all
    or merely passed None — the fake PNGs let us distinguish."""
    from tools import chart_vision
    monkeypatch.setattr(chart_vision, "CHART_SNAPSHOT_DIR", str(tmp_path))
    monkeypatch.setattr(chart_vision, "_DESCRIPTIONS_CACHE", None)
    # Two charts present on disk — rolling_correlation is in every
    # registered set (COUNCIL, ACADEMIC_REVIEW, DOCUMENT_GENERATION),
    # so any wiring will see at least one chart and pass a non-empty
    # visual_context list.
    for key in ("rolling_correlation", "cumulative_returns"):
        with open(os.path.join(tmp_path, f"{key}.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nfake-" + key.encode())
    return tmp_path


def _make_strategy_results() -> dict:
    """Minimal strategy_results bundle every specialist accepts."""
    return {
        "BENCHMARK": {
            "sharpe_ratio": 0.52, "cagr": 0.085, "max_drawdown": -0.51,
            "volatility": 0.16, "avg_equity_weight": 1.0,
            "is_significant": False, "strategy_type": "static",
            "monthly_returns": [], "stress_results": {},
        },
        "REGIME_SWITCHING": {
            "sharpe_ratio": 0.63, "cagr": 0.077, "max_drawdown": -0.19,
            "volatility": 0.10, "avg_equity_weight": 0.5,
            "avg_bond_weight": 0.5, "is_significant": False,
            "strategy_type": "dynamic", "oos_sharpe": 0.58, "oos_cagr": 0.07,
            "true_turnover": 0.20, "alpha_after_costs_bps": 50.0,
            "cross_validation": {"cv_stability_score": 0.65},
            "p_value_ttest": 0.04, "p_value_corrected": 0.40,
            "dsr_p_value": 0.30, "oos_p_value": 0.20,
            "stress_results": {}, "monthly_returns": [],
        },
    }


def _capture_call_claude(monkeypatch, target_module: str):
    """Replace call_claude on a module with a recorder that captures
    kwargs and returns a stub response. Returns the captured-call list
    so tests can assert on the visual_context kwarg."""
    captured: list[dict] = []

    def _fake(model, system_prompt, user_message, max_tokens=1024,
              tools=None, *, visual_context=None, **_kwargs):
        # **_kwargs absorbs the trigger / hash_gate telemetry kwargs
        # added in PR-LLM-1. These tests assert on the visual_context
        # wiring contract; the telemetry kwargs are orthogonal.
        captured.append({
            "model": model,
            "max_tokens": max_tokens,
            "tools": tools,
            "visual_context": visual_context,
            "user_message_type": type(user_message).__name__,
        })
        return "STUB RESPONSE for tests"

    monkeypatch.setattr(f"{target_module}.call_claude", _fake)
    return captured


# ── Specialist generators ─────────────────────────────────────────────────────


class TestSpecialistsInjectVisualContext:
    """Every council specialist must pass visual_context to call_claude
    when chart snapshots exist on disk."""

    def test_equity_analyst_passes_visual_context(self, snapshot_dir, monkeypatch):
        from agents import equity_analyst as ea
        captured = _capture_call_claude(monkeypatch, "agents.equity_analyst")
        # Short-circuit the harness so the generator runs exactly once.
        monkeypatch.setattr(
            ea, "GeneratorEvaluatorHarness",
            lambda *a, **kw: _SingleShotHarness())
        analyst = ea.EquityAnalyst()
        analyst.analyse(_make_strategy_results())
        assert captured, "call_claude was not invoked"
        # The generator must have received non-None visual_context.
        # rolling_correlation + cumulative_returns each emit 2 blocks
        # (image + caption), so the list has 4 entries.
        vc = captured[0]["visual_context"]
        assert vc is not None
        assert len(vc) == 4
        assert vc[0]["type"] == "image"
        assert vc[1]["type"] == "text"

    def test_fixed_income_analyst_passes_visual_context(
        self, snapshot_dir, monkeypatch,
    ):
        from agents import fixed_income_analyst as fi
        captured = _capture_call_claude(monkeypatch,
                                         "agents.fixed_income_analyst")
        monkeypatch.setattr(
            fi, "GeneratorEvaluatorHarness",
            lambda *a, **kw: _SingleShotHarness())
        analyst = fi.FixedIncomeAnalyst()
        analyst.analyse(_make_strategy_results())
        assert captured
        vc = captured[0]["visual_context"]
        assert vc is not None
        assert any(b["type"] == "image" for b in vc)

    def test_risk_manager_passes_visual_context(self, snapshot_dir, monkeypatch):
        from agents import risk_manager as rm
        captured = _capture_call_claude(monkeypatch, "agents.risk_manager")
        monkeypatch.setattr(
            rm, "GeneratorEvaluatorHarness",
            lambda *a, **kw: _SingleShotHarness())
        analyst = rm.RiskManager()
        analyst.analyse(_make_strategy_results())
        assert captured
        assert captured[0]["visual_context"] is not None

    def test_quant_backtester_passes_visual_context(
        self, snapshot_dir, monkeypatch,
    ):
        from agents import quant_backtester as qb
        captured = _capture_call_claude(monkeypatch, "agents.quant_backtester")
        monkeypatch.setattr(
            qb, "GeneratorEvaluatorHarness",
            lambda *a, **kw: _SingleShotHarness())
        analyst = qb.QuantBacktester()
        analyst.analyse(_make_strategy_results())
        assert captured
        assert captured[0]["visual_context"] is not None


class TestSpecialistsFallOpenWhenNoSnapshots:
    """When no snapshots exist on disk the generators must pass
    visual_context=None — the call_claude path then sends string
    content (bitwise identical to the pre-vision behaviour)."""

    def test_equity_analyst_no_snapshots_passes_none(self, tmp_path, monkeypatch):
        # CHART_SNAPSHOT_DIR points at an empty directory.
        from tools import chart_vision
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setattr(chart_vision, "CHART_SNAPSHOT_DIR", str(empty))
        monkeypatch.setattr(chart_vision, "_DESCRIPTIONS_CACHE", None)

        from agents import equity_analyst as ea
        captured = _capture_call_claude(monkeypatch, "agents.equity_analyst")
        monkeypatch.setattr(
            ea, "GeneratorEvaluatorHarness",
            lambda *a, **kw: _SingleShotHarness())
        analyst = ea.EquityAnalyst()
        analyst.analyse(_make_strategy_results())
        assert captured
        assert captured[0]["visual_context"] is None


# ── Harness evaluator guard ───────────────────────────────────────────────────


class TestHarnessEvaluatorOmitsVisualContext:
    """The evaluator path in the harness MUST NOT pass visual_context —
    text-only evaluation scores text quality, and chart blocks would
    muddle the signal. The guard is enforced by omission at the
    evaluator's only call site."""

    def test_evaluator_call_uses_default_none(self, monkeypatch):
        from agents import harness as h
        captured: list[dict] = []

        def _fake(model, system_prompt, user_message, max_tokens=1024,
                  tools=None, *, visual_context=None, **_kwargs):
            # **_kwargs absorbs the trigger / hash_gate telemetry kwargs
            # added in PR-LLM-1. The contract this test pins is the
            # visual_context omission for evaluator calls.
            captured.append({"visual_context": visual_context,
                              "user_message_type": type(user_message).__name__})
            return '{"overall": 9.0, "feedback": ""}'

        monkeypatch.setattr(h, "call_claude", _fake)

        harness = h.GeneratorEvaluatorHarness()
        score, feedback = harness._evaluate(
            response="some generated text",
            evaluator_prompt="score it",
            context="reference",
        )
        assert score == 9.0
        # The evaluator MUST have been called without visual_context.
        # _capture_call_claude's signature mirrors call_claude — the
        # default None is the contract this test pins.
        assert captured
        assert captured[0]["visual_context"] is None
        # And the user_message must be a plain string, not a list of
        # content blocks — confirms the legacy string-content wire
        # format that scoring relies on.
        assert captured[0]["user_message_type"] == "str"


# ── CIO direct call_claude paths ──────────────────────────────────────────────


class TestCIODirectCallsInjectVisualContext:
    """The CIO's _compile_draft_consensus and _synthesise both call
    call_claude directly (not through the harness). Both must pass
    visual_context."""

    def test_compile_draft_consensus_passes_visual_context(
        self, snapshot_dir, monkeypatch,
    ):
        from agents import cio
        captured = _capture_call_claude(monkeypatch, "agents.cio")
        cio_instance = cio.CIO()
        results = _make_strategy_results()
        cio_instance._compile_draft_consensus(
            "test query",
            equity_report={"summary": "ok"},
            fi_report={"summary": "ok",
                       "technical_findings": {"breakdown_detected": True}},
            risk_report={"summary": "ok",
                          "technical_findings": {"n_strategies_significant": 0}},
            quant_report={"summary": "ok"},
            strategy_results=results,
        )
        assert captured
        assert captured[0]["visual_context"] is not None

    def test_synthesise_passes_visual_context(self, snapshot_dir, monkeypatch):
        from agents import cio
        captured = _capture_call_claude(monkeypatch, "agents.cio")
        cio_instance = cio.CIO()
        results = _make_strategy_results()
        cio_instance._synthesise(
            "test query",
            draft_consensus="draft",
            gemini_report={"technical_findings": {"objections": []}},
            grok_report={"technical_findings": {"objections": []}},
            equity_report={"summary": "ok"},
            fi_report={"summary": "ok"},
            risk_report={"summary": "ok"},
            quant_report={"summary": "ok"},
            strategy_results=results,
        )
        assert captured
        assert captured[0]["visual_context"] is not None


# ── Test helper ───────────────────────────────────────────────────────────────


class _SingleShotHarness:
    """Stand-in GeneratorEvaluatorHarness that runs the generator exactly
    once and returns its raw output. Used to bypass the harness's
    evaluate-retry loop in wiring tests — we only need to confirm the
    generator's call_claude invocation, not the loop's behaviour."""

    def run(self, generator_fn, evaluator_prompt, generator_prompt,
            context, agent_id, *, secondary_evaluator_prompt=None):
        response = generator_fn(generator_prompt)
        from agents.harness import HarnessResult
        return HarnessResult(
            response=response, final_score=9.0, attempts=1,
            improved=False, feedback_applied="", initial_score=9.0,
            primary_score=9.0, secondary_score=None,
        )
