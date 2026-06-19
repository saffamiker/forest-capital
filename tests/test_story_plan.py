"""tests/test_story_plan.py -- the four-pass story plan generator.

Tests pin:
  * Pass 1 wires through GeneratorEvaluatorHarness with the document-
    type-specific evaluator prompt,
  * fail-open contracts at every pass,
  * cache reader returns None when DB is unavailable,
  * SQL shape of the guarded UPSERT (PR #324 recovery pattern adapted
    to the (data_hash, document_type) composite key),
  * deterministic fallback ALWAYS returns a valid plan shape,
  * harness scores are logged so per-run quality is visible in Render
    logs.

The 4-pass pipeline itself (real Opus + Grok + Gemini calls) is
verified on Render where the live keys exist; CI runs in
ENVIRONMENT=test which short-circuits Grok and Gemini and falls open
on Opus at the call_claude level.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")


# ── Evaluator prompt content ─────────────────────────────────────────────


class TestEvaluatorPrompts:
    """Both Pass 1 evaluator prompts must pin the rubric so a future
    refactor doesn't quietly drop a criterion."""

    def test_deck_evaluator_pins_all_five_criteria(self):
        from tools.story_plan import STORY_PLAN_EVALUATOR_PROMPT
        # The five rubric headers.
        for header in (
            "CENTRAL ARGUMENT", "NARRATIVE ARC", "NUMERIC DISCIPLINE",
            "SLIDE ECONOMY", "HONEST LIMITATIONS",
        ):
            assert header in STORY_PLAN_EVALUATOR_PROMPT, (
                f"missing rubric header: {header}")
        # The required figures the rubric calls out.
        assert "OOS Sharpe blend 1.24 vs benchmark 0.73" \
            in STORY_PLAN_EVALUATOR_PROMPT
        assert "2-of-9 value-add events" in STORY_PLAN_EVALUATOR_PROMPT

    def test_brief_evaluator_pins_six_rubric_sections(self):
        from tools.story_plan import BRIEF_PLAN_EVALUATOR_PROMPT
        # All six rubric sections named.
        for sec in (
            "executive summary", "methodology overview",
            "key findings and insights", "limitations and risks",
            "final recommendations", "visuals",
        ):
            assert sec in BRIEF_PLAN_EVALUATOR_PROMPT.lower()
        # The non-rubric content guard.
        assert "next steps" in BRIEF_PLAN_EVALUATOR_PROMPT.lower()
        assert "future work" in BRIEF_PLAN_EVALUATOR_PROMPT.lower()
        # The recommendations-framing guard (the exact failure mode
        # PR #326 fixed in the brief generator).
        assert "investment conclusions" \
            in BRIEF_PLAN_EVALUATOR_PROMPT.lower()


# ── JSON parsing hardening (mirrors cio_recommendation parser) ──────────


class TestPlanJsonParsing:

    def test_parses_clean_json(self):
        from tools.story_plan import _parse_plan_json
        out = _parse_plan_json(
            '{"central_argument": "x", "slide_plan": []}',
            log_key="t")
        assert out is not None
        assert out["central_argument"] == "x"

    def test_strips_markdown_fence(self):
        from tools.story_plan import _parse_plan_json
        raw = '```json\n{"central_argument": "x"}\n```'
        out = _parse_plan_json(raw, log_key="t")
        assert out is not None
        assert out["central_argument"] == "x"

    def test_strips_preamble_before_brace(self):
        from tools.story_plan import _parse_plan_json
        raw = 'Here is the plan: {"central_argument": "x"}'
        out = _parse_plan_json(raw, log_key="t")
        assert out is not None
        assert out["central_argument"] == "x"

    def test_returns_none_on_no_object(self):
        from tools.story_plan import _parse_plan_json
        assert _parse_plan_json("Sorry, no JSON.", log_key="t") is None

    def test_returns_none_on_array_root(self):
        from tools.story_plan import _parse_plan_json
        # An array at root is not a valid plan shape.
        assert _parse_plan_json("[1, 2, 3]", log_key="t") is None


# ── Deterministic fallback ───────────────────────────────────────────────


class TestDeterministicFallback:

    def test_deck_fallback_has_required_shape(self):
        from tools.story_plan import _deterministic_deck_plan
        ctx = {
            "validated_constants": {
                "oos_sharpe_regime_conditional": 0.86,
                "oos_sharpe_benchmark": 0.43,
            },
        }
        plan = _deterministic_deck_plan(ctx)
        assert plan["_model"] == "deterministic_fallback"
        assert plan["central_argument"]
        assert isinstance(plan["slide_plan"], list)
        assert len(plan["slide_plan"]) >= 1
        # Numeric anchors lifted from the validated constants.
        first = plan["slide_plan"][0]
        assert first["numeric_anchors"]["oos_sharpe_blend"] == 0.86
        assert first["numeric_anchors"]["oos_sharpe_benchmark"] == 0.43

    def test_brief_fallback_has_six_rubric_sections(self):
        from tools.story_plan import _deterministic_brief_plan
        ctx = {
            "validated_constants": {
                "oos_sharpe_regime_conditional": 0.86,
                "oos_sharpe_benchmark": 0.43,
            },
        }
        plan = _deterministic_brief_plan(ctx)
        assert plan["_model"] == "deterministic_fallback"
        sections = plan["section_plan"]
        # All six rubric keys, no extras.
        assert set(sections.keys()) == {
            "executive_summary", "methodology", "key_findings",
            "limitations_and_risks", "final_recommendations", "visuals",
        }

    def test_fallback_with_missing_constants_still_safe(self):
        from tools.story_plan import _deterministic_deck_plan
        # No validated_constants block at all -- the fallback must
        # still produce a valid plan shape, just with None anchors.
        plan = _deterministic_deck_plan({})
        assert plan["_model"] == "deterministic_fallback"
        assert plan["slide_plan"]


# ── Pass 1 harness wiring ────────────────────────────────────────────────


class TestPass1HarnessWiring:
    """Pass 1 must run through GeneratorEvaluatorHarness with the
    document-type-specific evaluator prompt. The harness retries on
    sub-threshold scores; the final score + attempt count are logged."""

    @pytest.mark.asyncio
    async def test_pass1_uses_harness_with_deck_evaluator(self, monkeypatch):
        from tools import story_plan

        captured: dict = {}

        class _FakeResult:
            response = (
                '{"central_argument": "x", "presentation_arc": "y", '
                '"slide_plan": [{"slide_number": 1, "title": "t", '
                '"headline": "h", "key_visual": "v", '
                '"numeric_anchors": {"oos_sharpe_blend": 0.86}, '
                '"slide_bullets": [], "speaker_notes": "n", '
                '"transition_to_next": "to"}]}')
            final_score = 8.0
            attempts = 1
            improved = False
            feedback_applied = ""
            initial_score = 8.0
            primary_score = None
            secondary_score = None

        class _FakeHarness:
            def __init__(self, *a, **kw):
                pass

            def run(self, *, generator_fn, evaluator_prompt,
                    generator_prompt, context, agent_id,
                    secondary_evaluator_prompt=None):
                captured["evaluator_prompt"] = evaluator_prompt
                captured["agent_id"] = agent_id
                return _FakeResult()

        monkeypatch.setattr(
            "agents.harness.GeneratorEvaluatorHarness", _FakeHarness)

        # Disable Grok + Gemini paths in this test -- ENVIRONMENT=test
        # is already set in conftest, but the helpers also rely on
        # API keys being absent. Pin both to empty.
        monkeypatch.setenv("ENVIRONMENT", "test")
        monkeypatch.setenv("GOOGLE_API_KEY", "")

        plan = story_plan.generate_deck_story_plan(
            {"validated_constants": {
                "oos_sharpe_regime_conditional": 0.86,
                "oos_sharpe_benchmark": 0.43,
            }},
            ["Slide 1", "Slide 2"])

        # The harness ran with the DECK evaluator prompt, not the
        # brief one.
        from tools.story_plan import STORY_PLAN_EVALUATOR_PROMPT
        assert captured["evaluator_prompt"] == STORY_PLAN_EVALUATOR_PROMPT
        assert captured["agent_id"] == "story_plan_deck"
        # Plan shape carries through.
        assert plan["central_argument"] == "x"
        assert plan["slide_plan"][0]["slide_number"] == 1
        assert plan["_model"] == "claude-opus-4-7"

    @pytest.mark.asyncio
    async def test_pass1_uses_harness_with_brief_evaluator(
            self, monkeypatch):
        from tools import story_plan

        captured: dict = {}

        class _FakeResult:
            response = (
                '{"central_argument": "x", '
                '"section_plan": {'
                '"executive_summary": {"key_message": "m", '
                '"numeric_anchors": {}, "target_length_words": 200}, '
                '"methodology": {"key_message": "m", '
                '"numeric_anchors": {}, "target_length_words": 300}, '
                '"key_findings": {"key_message": "m", '
                '"numeric_anchors": {}, "target_length_words": 500}, '
                '"limitations_and_risks": {"key_message": "m", '
                '"numeric_anchors": {}, "target_length_words": 300}, '
                '"final_recommendations": {"key_message": "m", '
                '"numeric_anchors": {}, "target_length_words": 350}, '
                '"visuals": {"key_message": "m", '
                '"numeric_anchors": {}, "target_length_words": 200}'
                '}}')
            final_score = 9.0
            attempts = 2
            improved = True
            feedback_applied = "tighter slides"
            initial_score = 6.0
            primary_score = None
            secondary_score = None

        class _FakeHarness:
            def __init__(self, *a, **kw):
                pass

            def run(self, *, generator_fn, evaluator_prompt,
                    generator_prompt, context, agent_id,
                    secondary_evaluator_prompt=None):
                captured["evaluator_prompt"] = evaluator_prompt
                captured["agent_id"] = agent_id
                return _FakeResult()

        monkeypatch.setattr(
            "agents.harness.GeneratorEvaluatorHarness", _FakeHarness)
        monkeypatch.setenv("ENVIRONMENT", "test")
        monkeypatch.setenv("GOOGLE_API_KEY", "")

        plan = story_plan.generate_brief_section_plan(
            {"validated_constants": {}}, ["s1", "s2"])

        from tools.story_plan import BRIEF_PLAN_EVALUATOR_PROMPT
        assert captured["evaluator_prompt"] == BRIEF_PLAN_EVALUATOR_PROMPT
        assert captured["agent_id"] == "story_plan_brief"
        assert plan["central_argument"] == "x"
        assert len(plan["section_plan"]) == 6


# ── Pass 1 failure paths ─────────────────────────────────────────────────


class TestPass1FailureFallsOpen:

    def test_pass1_exception_returns_deterministic_deck_plan(
            self, monkeypatch):
        from tools import story_plan

        class _ExplodingHarness:
            def __init__(self, *a, **kw):
                pass

            def run(self, **_kw):
                raise RuntimeError("opus down")

        monkeypatch.setattr(
            "agents.harness.GeneratorEvaluatorHarness", _ExplodingHarness)
        monkeypatch.setenv("ENVIRONMENT", "test")
        monkeypatch.setenv("GOOGLE_API_KEY", "")

        plan = story_plan.generate_deck_story_plan(
            {"validated_constants": {}}, ["s1"])
        assert plan["_model"] == "deterministic_fallback"

    def test_pass1_unparseable_json_returns_deterministic(
            self, monkeypatch):
        from tools import story_plan

        class _BadJsonResult:
            response = "Sorry, I cannot produce JSON right now."
            final_score = 9.0
            attempts = 1
            improved = False
            feedback_applied = ""
            initial_score = 9.0
            primary_score = None
            secondary_score = None

        class _Harness:
            def __init__(self, *a, **kw):
                pass

            def run(self, **_kw):
                return _BadJsonResult()

        monkeypatch.setattr(
            "agents.harness.GeneratorEvaluatorHarness", _Harness)
        monkeypatch.setenv("ENVIRONMENT", "test")
        monkeypatch.setenv("GOOGLE_API_KEY", "")

        plan = story_plan.generate_deck_story_plan(
            {"validated_constants": {}}, ["s1"])
        assert plan["_model"] == "deterministic_fallback"


# ── Cache + persistence ──────────────────────────────────────────────────


class TestPersistence:

    @pytest.mark.asyncio
    async def test_get_cached_returns_none_when_db_unavailable(
            self, monkeypatch):
        from tools import story_plan as sp
        monkeypatch.setattr(sp, "_DB_AVAILABLE", False)
        out = await sp.get_cached_story_plan("h", "deck")
        assert out is None

    @pytest.mark.asyncio
    async def test_persist_short_circuits_when_db_unavailable(
            self, monkeypatch):
        from tools import story_plan as sp
        monkeypatch.setattr(sp, "_DB_AVAILABLE", False)
        # Must not raise.
        await sp.persist_story_plan("h", "deck", {
            "central_argument": "x",
            "slide_plan": [],
            "_model": "claude-opus-4-7"})

    @pytest.mark.asyncio
    async def test_persist_sql_carries_guarded_do_update(self, monkeypatch):
        """The SQL must use the (data_hash, document_type) composite
        UPSERT with the deterministic_fallback guard. Captured SQL is
        pinned so a regression that loosens or drops the guard fails
        loudly at the statement-shape layer."""
        from tools import story_plan as sp

        captured: dict = {"sql": None}

        class _Session:
            async def execute(self, sql, params):
                captured["sql"] = str(sql)

            async def commit(self):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

        monkeypatch.setattr(sp, "_DB_AVAILABLE", True)
        monkeypatch.setattr(sp, "AsyncSessionLocal", lambda: _Session())

        await sp.persist_story_plan(
            "abc", "deck",
            {"central_argument": "x", "slide_plan": [],
             "full_script": "FULL SCRIPT TEXT",
             "anticipated_questions": [{"question": "q",
                                        "difficulty": "hard"}],
             "dissenting_view": "d",
             "limitations_surfaced": ["l1"],
             "_model": "claude-opus-4-7"})

        sql = captured["sql"] or ""
        assert "ON CONFLICT (data_hash, document_type)" in sql
        assert "DO UPDATE" in sql
        # Recovery half: only overwrite when existing is a fallback.
        assert (
            "story_plans.model "
            "      = 'deterministic_fallback'" in sql)
        # Reverse half: never overwrite a real row with a fallback.
        assert (
            "EXCLUDED.model "
            "      IS DISTINCT FROM 'deterministic_fallback'" in sql)
        # computed_at bumps so reads ordered by recency see the
        # recovery row immediately.
        assert "computed_at = now()" in sql
        # The pre-PR shape must be gone.
        assert "DO NOTHING" not in sql

    @pytest.mark.asyncio
    async def test_refresh_serves_from_cache_when_real_row_present(
            self, monkeypatch):
        """A cached non-fallback row short-circuits the 4-pass
        generation -- the function returns the cached plan directly
        with cache='hit'. This is the path the deck/brief consumer
        relies on for sub-second responses."""
        from tools import story_plan as sp

        async def _fake_cached(*_a):
            return {
                "central_argument": "cached",
                "slide_plan": [{"slide_number": 1}],
                "_model": "claude-opus-4-7"}

        # If generate_deck_story_plan fires we know the cache check
        # did NOT short-circuit -- the test fails loudly.
        def _should_not_run(*_a, **_kw):
            raise AssertionError(
                "generate_deck_story_plan should NOT fire on cache hit")

        monkeypatch.setattr(sp, "get_cached_story_plan", _fake_cached)
        monkeypatch.setattr(
            sp, "generate_deck_story_plan", _should_not_run)

        out = await sp.refresh_story_plan(
            "abc", "deck", deck_context={}, slide_titles=["s1"])
        assert out["central_argument"] == "cached"
        assert out["cache"] == "hit"

    @pytest.mark.asyncio
    async def test_refresh_regenerates_when_cached_is_fallback(
            self, monkeypatch):
        """A cached deterministic_fallback row does NOT count as a
        valid cache hit -- the 4-pass generator must re-fire so a
        real LLM plan can replace the fallback."""
        from tools import story_plan as sp

        async def _fake_cached(*_a):
            return {
                "central_argument": "stale",
                "slide_plan": [],
                "_model": "deterministic_fallback"}

        called = {"deck": 0, "persisted": 0}

        def _fake_gen(*_a, **_kw):
            called["deck"] += 1
            return {
                "central_argument": "fresh",
                "slide_plan": [{"slide_number": 1}],
                "_model": "claude-opus-4-7"}

        async def _fake_persist(*_a, **_kw):
            called["persisted"] += 1

        monkeypatch.setattr(sp, "get_cached_story_plan", _fake_cached)
        monkeypatch.setattr(sp, "generate_deck_story_plan", _fake_gen)
        monkeypatch.setattr(sp, "persist_story_plan", _fake_persist)

        out = await sp.refresh_story_plan(
            "abc", "deck", deck_context={}, slide_titles=["s1"])
        assert called["deck"] == 1
        assert called["persisted"] == 1
        assert out["central_argument"] == "fresh"
        assert out["cache"] == "miss"


# ── End-to-end: test environment short-circuits Grok + Gemini ────────────


class TestTestEnvironmentShortCircuits:
    """In ENVIRONMENT=test (CI), the Grok and Gemini helpers return
    empty defaults without ever hitting the network -- so the 4-pass
    generator always completes without external dependencies."""

    def test_anticipated_questions_returns_empty_in_test_env(self):
        from tools.story_plan import _generate_anticipated_questions
        assert _generate_anticipated_questions("anything") == []

    def test_blind_spots_returns_empty_in_test_env(self):
        from tools.story_plan import _generate_blind_spots
        out = _generate_blind_spots("anything")
        assert out["dissenting_view"] == ""
        assert out["limitations_to_surface"] == []
        assert out["blind_spots"] == []
