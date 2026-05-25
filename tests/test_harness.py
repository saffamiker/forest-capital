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

import pytest  # noqa: E402

from main import app  # noqa: E402
from auth import generate_session_token  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(app)
SESSION_HEADERS = {"X-API-Key": generate_session_token("ruurdsm@queens.edu")}


# PR-LLM-2 (May 28 2026) — the evaluator cache is process-wide. Every
# test in this file resets it before running so a verdict cached by an
# earlier test does not bleed into a later one. The PMSecondaryEvaluator
# tests use `lambda p: "the response"` generators that re-emit the same
# response on every retry — without this reset, the second retry's
# evaluator call would hit the previous run's cache and skip the
# expected LLM call.
@pytest.fixture(autouse=True)
def _reset_evaluator_cache():
    from agents.harness import reset_evaluator_cache_for_tests
    reset_evaluator_cache_for_tests()
    yield
    reset_evaluator_cache_for_tests()


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

    def test_evaluator_truncated_json_retries_with_higher_max_tokens(self):
        """PR-LLM-2 (May 28 2026) — when the first call returns
        truncated JSON ("Unterminated string at char N"), the
        evaluator retries ONCE with max_tokens=1500. If the retry
        succeeds, the second call's score is used."""
        from agents.harness import GeneratorEvaluatorHarness

        def gen(prompt: str) -> str:
            return "the response under evaluation"

        # First call: truncated JSON (no closing quote on "feedback").
        # Second call: valid JSON with score=8.5.
        truncated = '{"scores": {}, "overall": 6.0, "feedback": "needs'
        valid = _score(8.5, feedback="much better")
        max_tokens_seen: list[int] = []

        def fake_call_claude(model, system, user, **kwargs):
            max_tokens_seen.append(kwargs.get("max_tokens"))
            return truncated if len(max_tokens_seen) == 1 else valid

        harness = GeneratorEvaluatorHarness(threshold=7.0, max_retries=1)
        with patch("agents.harness.call_claude",
                   side_effect=fake_call_claude):
            result = harness.run(gen, "EVAL", "TASK", "context", "test_agent")
        # The first call ran at 600; the retry bumped to 1500.
        assert max_tokens_seen[0] == 600
        assert max_tokens_seen[1] == 1500
        # The second call's score landed.
        assert result.final_score == 8.5
        # max_retries=1 so we get one generator + the evaluator's
        # two parse attempts on the same generator output.
        assert result.attempts == 1

    def test_evaluator_both_attempts_failing_passes_through(self):
        """If the retry ALSO fails to parse, the evaluator returns
        the passthrough score — the review session never hard-fails."""
        from agents.harness import GeneratorEvaluatorHarness

        def gen(prompt: str) -> str:
            return "the response"

        # Both calls return malformed JSON.
        with patch("agents.harness.call_claude",
                   return_value='{"unterminated'):
            harness = GeneratorEvaluatorHarness(threshold=7.0, max_retries=1)
            result = harness.run(gen, "EVAL", "TASK", "context", "test_agent")
        # Passthrough (8.0) clears the threshold — review proceeds.
        assert result.final_score == 8.0


# ── Evaluator cache (PR-LLM-2, May 28 2026) ─────────────────────────────────

class TestEvaluatorCache:
    """The evaluator caches (model, rubric, response, context) → (score,
    feedback) so repeated calls with identical inputs skip the LLM
    call entirely. Process-wide LRU, cap 256, reset_for_tests helper."""

    def setup_method(self):
        """Each case starts with a clean cache."""
        from agents.harness import reset_evaluator_cache_for_tests
        reset_evaluator_cache_for_tests()

    def test_identical_inputs_hit_the_cache(self):
        from agents.harness import GeneratorEvaluatorHarness

        # Two harness runs against the SAME generator output → the
        # evaluator inputs are identical → the second harness run's
        # _evaluate should be a cache hit.
        def gen(prompt: str) -> str:
            return "identical response text"

        call_count = [0]

        def fake_call_claude(*args, **kwargs):
            call_count[0] += 1
            return _score(8.5, feedback="good")

        with patch("agents.harness.call_claude",
                   side_effect=fake_call_claude):
            harness = GeneratorEvaluatorHarness(threshold=7.0, max_retries=0)
            r1 = harness.run(gen, "EVAL", "TASK", "context", "test_agent")
            r2 = harness.run(gen, "EVAL", "TASK", "context", "test_agent")

        # Same score on both — the cached verdict was reused.
        assert r1.final_score == 8.5
        assert r2.final_score == 8.5
        # Two harness runs but only ONE evaluator LLM call (cache
        # hit on the second).
        assert call_count[0] == 1

    def test_different_response_misses_cache(self):
        from agents.harness import GeneratorEvaluatorHarness

        outputs = iter(["response A", "response B"])

        def gen(prompt: str) -> str:
            return next(outputs)

        call_count = [0]

        def fake_call_claude(*args, **kwargs):
            call_count[0] += 1
            return _score(7.5, feedback="ok")

        with patch("agents.harness.call_claude",
                   side_effect=fake_call_claude):
            harness = GeneratorEvaluatorHarness(threshold=7.0, max_retries=0)
            harness.run(gen, "EVAL", "TASK", "context", "test_agent")
            harness.run(gen, "EVAL", "TASK", "context", "test_agent")

        # Different responses → different cache keys → two LLM calls.
        assert call_count[0] == 2

    def test_different_rubric_misses_cache(self):
        from agents.harness import GeneratorEvaluatorHarness

        def gen(prompt: str) -> str:
            return "same response text"

        call_count = [0]

        def fake_call_claude(*args, **kwargs):
            call_count[0] += 1
            return _score(8.0)

        with patch("agents.harness.call_claude",
                   side_effect=fake_call_claude):
            harness = GeneratorEvaluatorHarness(threshold=7.0, max_retries=0)
            # Same response + context, DIFFERENT evaluator prompts → two calls.
            harness.run(gen, "RUBRIC A", "TASK", "ctx", "agent")
            harness.run(gen, "RUBRIC B", "TASK", "ctx", "agent")

        assert call_count[0] == 2

    def test_parse_failure_is_not_cached(self):
        """A passthrough score from a parse failure should NOT pollute
        the cache. A subsequent identical-input request must still
        attempt to parse fresh, in case the model now returns a
        well-formed response."""
        from agents.harness import GeneratorEvaluatorHarness

        def gen(prompt: str) -> str:
            return "the response"

        # First two calls return malformed JSON (passthrough), then
        # a third returns valid JSON. If the parse failure had been
        # cached, the third request would have used the passthrough
        # and never actually called the evaluator a third time.
        outputs = iter([
            "malformed", "malformed",        # first harness run
            _score(9.0),                     # second harness run
        ])

        def fake_call_claude(*args, **kwargs):
            return next(outputs)

        with patch("agents.harness.call_claude",
                   side_effect=fake_call_claude):
            harness = GeneratorEvaluatorHarness(threshold=7.0, max_retries=0)
            r1 = harness.run(gen, "EVAL", "TASK", "ctx", "agent")
            r2 = harness.run(gen, "EVAL", "TASK", "ctx", "agent")

        # First was passthrough (parse failed twice), second
        # successfully parsed 9.0 — proving the passthrough was NOT
        # cached.
        assert r1.final_score == 8.0
        assert r2.final_score == 9.0

    def test_lru_eviction_drops_oldest_entry(self):
        """The cache is bounded at _EVALUATOR_CACHE_MAX_SIZE. Past
        that, the least-recently-used entry is evicted."""
        from agents.harness import (
            _evaluator_cache, _evaluator_cache_get, _evaluator_cache_set,
            _EVALUATOR_CACHE_MAX_SIZE, reset_evaluator_cache_for_tests,
        )

        reset_evaluator_cache_for_tests()
        # Fill the cache to capacity.
        for i in range(_EVALUATOR_CACHE_MAX_SIZE):
            _evaluator_cache_set(f"key_{i}", (float(i), ""))
        # The oldest entry is still present.
        assert _evaluator_cache_get("key_0") is not None
        # Adding ONE more triggers eviction. Re-fetching key_0
        # touched its LRU position, so key_1 is the oldest now.
        _evaluator_cache_set("key_new", (99.0, ""))
        assert len(_evaluator_cache) == _EVALUATOR_CACHE_MAX_SIZE
        # key_1 is the LRU after we touched key_0 above.
        assert _evaluator_cache_get("key_1") is None
        assert _evaluator_cache_get("key_new") == (99.0, "")

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


# ── PM secondary evaluator — dual-evaluation behaviour ───────────────────────

def _pm_score(verdict: str, feedback: str = "") -> str:
    """A valid PM-evaluator JSON payload mapping verdict → numeric overall."""
    overall = {"STRONG": 9.0, "DEVELOPING": 7.5, "NEEDS WORK": 3.0}[verdict]
    return json.dumps({
        "scores": {
            "insight_beyond_obvious":  "PASS",
            "regime_mechanism":        "PASS",
            "actionable_signals":      "N/A",
            "contradictions_pressed":  "PASS",
            "so_what_explicit":        "PASS",
        },
        "verdict": verdict,
        "overall": overall,
        "passed": verdict != "NEEDS WORK",
        "feedback": feedback,
    })


class TestPMSecondaryEvaluator:
    """The harness scores against BOTH primary (academic) and secondary
    (PM) evaluators when secondary_evaluator_prompt is set. Effective
    score is the WORSE of the two — a retry fires when EITHER rubric
    returns NEEDS WORK, matching the spec."""

    def test_secondary_runs_alongside_primary(self):
        # When the harness is configured with both evaluators, each
        # generation attempt is scored TWICE — once per rubric.
        from agents.harness import GeneratorEvaluatorHarness
        call_log: list[str] = []

        def fake_call_claude(model, system_prompt, user_message, **kwargs):
            call_log.append(system_prompt)
            return _score(9.0) if "ACADEMIC" in system_prompt \
                else _pm_score("STRONG")

        harness = GeneratorEvaluatorHarness(threshold=7.0, max_retries=2)
        with patch("agents.harness.call_claude", side_effect=fake_call_claude):
            result = harness.run(
                lambda p: "the response",
                evaluator_prompt="ACADEMIC RUBRIC",
                generator_prompt="TASK", context="ctx",
                agent_id="test_agent",
                secondary_evaluator_prompt="PM RUBRIC",
            )
        # Both evaluators were consulted on the single attempt.
        assert len(call_log) == 2
        assert any("ACADEMIC" in c for c in call_log)
        assert any("PM" in c for c in call_log)
        # Both scores surface on the result.
        assert result.primary_score == 9.0
        assert result.secondary_score == 9.0
        assert result.final_score == 9.0   # min(9, 9) = 9 — passes

    def test_pm_needs_work_triggers_retry(self):
        # The academic rubric scores PASS (9.0) but the PM rubric
        # returns NEEDS WORK (3.0). The harness retries because the
        # EFFECTIVE score (min) is below threshold.
        from agents.harness import GeneratorEvaluatorHarness
        attempts: list[str] = []

        def fake_call_claude(model, system_prompt, user_message, **kwargs):
            return _score(9.0) if "ACADEMIC" in system_prompt \
                else _pm_score("NEEDS WORK", "MISSING SO-WHAT STATEMENTS")

        harness = GeneratorEvaluatorHarness(threshold=7.0, max_retries=2)
        with patch("agents.harness.call_claude", side_effect=fake_call_claude):
            result = harness.run(
                lambda p: (attempts.append(p), "the response")[1],
                evaluator_prompt="ACADEMIC RUBRIC",
                generator_prompt="TASK", context="ctx",
                agent_id="test_agent",
                secondary_evaluator_prompt="PM RUBRIC",
            )
        # Three generation attempts — initial + 2 retries — because PM
        # NEEDS WORK never lifts.
        assert result.attempts == 3
        # The combined feedback injected on retry must name BOTH rubrics
        # so the writer addresses each lens explicitly.
        assert "PORTFOLIO MANAGER RUBRIC" in attempts[1]
        assert "MISSING SO-WHAT STATEMENTS" in attempts[1]

    def test_pm_developing_passes_threshold(self):
        # DEVELOPING maps to 7.5 — above the 7.0 threshold. The harness
        # accepts on the first attempt; only NEEDS WORK triggers retry.
        from agents.harness import GeneratorEvaluatorHarness

        def fake_call_claude(model, system_prompt, user_message, **kwargs):
            return _score(9.0) if "ACADEMIC" in system_prompt \
                else _pm_score("DEVELOPING")

        harness = GeneratorEvaluatorHarness(threshold=7.0, max_retries=2)
        with patch("agents.harness.call_claude", side_effect=fake_call_claude):
            result = harness.run(
                lambda p: "the response",
                evaluator_prompt="ACADEMIC RUBRIC",
                generator_prompt="TASK", context="ctx",
                agent_id="test_agent",
                secondary_evaluator_prompt="PM RUBRIC",
            )
        assert result.attempts == 1
        assert result.primary_score == 9.0
        assert result.secondary_score == 7.5
        # Effective = min(9.0, 7.5) = 7.5 — above threshold, no retry.
        assert result.final_score == 7.5

    def test_academic_needs_work_also_triggers_retry(self):
        # Symmetric — primary NEEDS WORK with PM STRONG also retries.
        # Confirms the OR-of-failures contract works both directions.
        from agents.harness import GeneratorEvaluatorHarness

        def fake_call_claude(model, system_prompt, user_message, **kwargs):
            return _score(3.0, "NOT EVIDENCE-BASED") \
                if "ACADEMIC" in system_prompt else _pm_score("STRONG")

        harness = GeneratorEvaluatorHarness(threshold=7.0, max_retries=2)
        with patch("agents.harness.call_claude", side_effect=fake_call_claude):
            result = harness.run(
                lambda p: "the response",
                evaluator_prompt="ACADEMIC RUBRIC",
                generator_prompt="TASK", context="ctx",
                agent_id="test_agent",
                secondary_evaluator_prompt="PM RUBRIC",
            )
        assert result.attempts == 3   # primary kept failing
        assert result.final_score == 3.0

    def test_secondary_none_keeps_existing_single_evaluator_behaviour(self):
        # Backward compatibility — no secondary evaluator means the
        # harness behaves exactly as before this change. Council
        # specialists, the AR arbiter, the AR peers, the triage agent
        # and the presentation script writer all rely on this path.
        from agents.harness import GeneratorEvaluatorHarness
        call_log: list[str] = []

        def fake_call_claude(model, system_prompt, user_message, **kwargs):
            call_log.append(system_prompt)
            return _score(9.0)

        harness = GeneratorEvaluatorHarness(threshold=7.0, max_retries=2)
        with patch("agents.harness.call_claude", side_effect=fake_call_claude):
            result = harness.run(
                lambda p: "the response",
                evaluator_prompt="ACADEMIC RUBRIC",
                generator_prompt="TASK", context="ctx",
                agent_id="test_agent",
                # No secondary_evaluator_prompt.
            )
        assert len(call_log) == 1                 # only ONE evaluator call
        assert result.primary_score == 9.0
        assert result.secondary_score is None     # confirms not invoked


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
