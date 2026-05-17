"""
tests/test_harness.py

Tests for the generator-evaluator harness — the evaluate-and-retry
quality wrapper around agent text generation.

Unit tests mock the evaluator (agents.harness.call_claude) so the
score is fully controlled; the generator is a plain test callable.
Integration tests confirm the harness changes neither the council nor
the academic-review API response shape.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)

from main import app  # noqa: E402
from auth import generate_session_token  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(app)
SESSION_HEADERS = {"X-API-Key": generate_session_token("ruurdsm@queens.edu")}


def _score(overall: float, feedback: str = "needs work") -> str:
    """A valid evaluator JSON payload."""
    return json.dumps({
        "scores": {}, "overall": overall,
        "passed": overall >= 7.0, "feedback": feedback,
    })


# ── Harness unit tests ────────────────────────────────────────────────────────

class TestHarnessUnit:
    def test_score_above_threshold_accepts_first_attempt(self):
        from agents.harness import GeneratorEvaluatorHarness
        calls: list[str] = []

        def gen(prompt: str) -> str:
            calls.append(prompt)
            return "a strong, evidence-based response"

        harness = GeneratorEvaluatorHarness(threshold=7.0, max_retries=2)
        with patch("agents.harness.call_claude", return_value=_score(9.0)):
            result = harness.run(gen, "EVAL PROMPT", "ORIGINAL TASK",
                                 "context", "test_agent")
        assert result.attempts == 1
        assert len(calls) == 1                       # generated exactly once
        assert result.response == "a strong, evidence-based response"
        assert result.final_score == 9.0
        assert result.improved is False

    def test_below_threshold_retries_with_feedback_injected(self):
        from agents.harness import GeneratorEvaluatorHarness
        prompts: list[str] = []

        def gen(prompt: str) -> str:
            prompts.append(prompt)
            return f"response-{len(prompts)}"

        harness = GeneratorEvaluatorHarness(threshold=7.0, max_retries=2)
        with patch("agents.harness.call_claude",
                   return_value=_score(4.0, "BE MORE SPECIFIC")):
            harness.run(gen, "EVAL", "ORIGINAL TASK", "context", "test_agent")

        # 3 attempts (2 retries). First prompt is the unmodified task.
        assert len(prompts) == 3
        assert prompts[0] == "ORIGINAL TASK"
        # Retry prompts carry the injected feedback and the original task.
        assert "EVALUATOR FEEDBACK" in prompts[1]
        assert "BE MORE SPECIFIC" in prompts[1]
        assert "ORIGINAL TASK" in prompts[1]

    def test_after_max_retries_best_scoring_version_is_returned(self):
        from agents.harness import GeneratorEvaluatorHarness
        n = [0]

        def gen(prompt: str) -> str:
            n[0] += 1
            return f"resp{n[0]}"

        # Scores rise then fall — the best is attempt 2, never the last.
        scores = [_score(5.0), _score(6.5), _score(4.0)]
        harness = GeneratorEvaluatorHarness(threshold=7.0, max_retries=2)
        with patch("agents.harness.call_claude", side_effect=scores):
            result = harness.run(gen, "EVAL", "TASK", "context", "test_agent")
        assert result.attempts == 3
        assert result.final_score == 6.5
        assert result.response == "resp2"            # best, not the last
        assert result.improved is True               # 6.5 beat the initial 5.0

    def test_evaluator_parse_failure_returns_passthrough_score(self):
        from agents.harness import GeneratorEvaluatorHarness

        def gen(prompt: str) -> str:
            return "some response"

        harness = GeneratorEvaluatorHarness(threshold=7.0, max_retries=2)
        # Evaluator returns non-JSON — _evaluate must not raise; it scores
        # 8.0 (passthrough), which clears the threshold on the first attempt.
        with patch("agents.harness.call_claude", return_value="not json at all"):
            result = harness.run(gen, "EVAL", "TASK", "context", "test_agent")
        assert result.final_score == 8.0
        assert result.attempts == 1

    def test_generator_exception_returns_best_earlier_response(self):
        from agents.harness import GeneratorEvaluatorHarness
        n = [0]

        def gen(prompt: str) -> str:
            n[0] += 1
            if n[0] == 1:
                return "first response"
            raise RuntimeError("generator boom")

        # First attempt scores low → retry; the retry generator raises.
        harness = GeneratorEvaluatorHarness(threshold=7.0, max_retries=2)
        with patch("agents.harness.call_claude", return_value=_score(3.0)):
            result = harness.run(gen, "EVAL", "TASK", "context", "test_agent")
        # The best earlier response is used — no exception escapes.
        assert result.response == "first response"
        assert n[0] == 2

    def test_first_attempt_generator_exception_reraises(self):
        from agents.harness import GeneratorEvaluatorHarness

        def gen(prompt: str) -> str:
            raise RuntimeError("boom on attempt 1")

        # Nothing to fall back to — the harness re-raises so the caller's
        # own try/except handles it exactly as before the harness existed.
        harness = GeneratorEvaluatorHarness()
        with patch("agents.harness.call_claude", return_value=_score(9.0)):
            try:
                harness.run(gen, "EVAL", "TASK", "context", "test_agent")
                raised = False
            except RuntimeError:
                raised = True
        assert raised is True

    def test_passes_through_when_evaluator_errors(self):
        """When the evaluator itself raises, the response is scored 8.0
        (passthrough) so a flaky evaluator never blocks output."""
        from agents.harness import GeneratorEvaluatorHarness

        def gen(prompt: str) -> str:
            return "the response"

        harness = GeneratorEvaluatorHarness()
        with patch("agents.harness.call_claude",
                   side_effect=RuntimeError("evaluator down")):
            result = harness.run(gen, "EVAL", "TASK", "context", "test_agent")
        assert result.response == "the response"
        assert result.final_score == 8.0
        assert result.attempts == 1


# ── Harness metrics capture ───────────────────────────────────────────────────

class TestHarnessMetrics:
    def test_capture_aggregates_runs(self):
        from agents.harness import (
            GeneratorEvaluatorHarness, start_harness_capture,
            collect_harness_metrics,
        )
        start_harness_capture()
        harness = GeneratorEvaluatorHarness()
        with patch("agents.harness.call_claude", return_value=_score(9.0)):
            harness.run(lambda p: "r1", "EVAL", "T", "c", "agent_a")
            harness.run(lambda p: "r2", "EVAL", "T", "c", "agent_b")
        metrics = collect_harness_metrics()
        assert metrics["agents_retried"] == 0          # both passed first try
        assert metrics["average_final_score"] == 9.0
        assert "improvement_rate" in metrics

    def test_metrics_empty_without_capture(self):
        # With no capture active (the ContextVar at its None default),
        # collect_harness_metrics returns {} — the endpoints then omit
        # the harness metadata block entirely.
        from agents.harness import collect_harness_metrics, _harness_run_log
        token = _harness_run_log.set(None)
        try:
            assert collect_harness_metrics() == {}
        finally:
            _harness_run_log.reset(token)


# ── Integration — API shape is unchanged ──────────────────────────────────────

class TestHarnessIntegration:
    def test_council_endpoint_returns_response(self):
        resp = client.post("/api/council/query",
                            json={"query": "Compare the 60/40 strategy"},
                            headers=SESSION_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        # Same CouncilDebateResponse shape — harness changed nothing here.
        assert "messages" in body or "query" in body

    def test_academic_review_stream_shape_unchanged(self):
        resp = client.post("/api/council/academic-review", headers=SESSION_HEADERS)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = resp.text
        assert '"type": "peer_responses"' in body
        assert '"type": "arbiter_chunk"' in body
        assert "[DONE]" in body
        assert (body.index('"type": "peer_responses"')
                < body.index('"type": "arbiter_chunk"'))

    def test_academic_review_arbiter_verdict_has_five_sections(self):
        """After the harness pass, the streamed arbiter verdict still
        reassembles to a verdict with all five rubric sections."""
        resp = client.post("/api/council/academic-review", headers=SESSION_HEADERS)
        assert resp.status_code == 200
        verdict = ""
        for line in resp.text.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                continue
            try:
                evt = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if evt.get("type") == "arbiter_chunk":
                verdict += evt.get("text", "")
        # Five "### N." sections present in the reassembled verdict.
        for n in range(1, 6):
            assert f"### {n}." in verdict
