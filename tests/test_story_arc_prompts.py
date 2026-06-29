"""tests/test_story_arc_prompts.py -- story-arc prompt
contracts (June 22 2026).

Pins the four prompt changes so a future refactor can't
silently revert any of them:

  1. CENTRAL_QUESTION_AND_ANSWER carries the December 2025
     lock figures (0.91 / 0.49), NOT the earlier live 1.24 /
     0.73 strings the previous version used.
  2. The story-arc hierarchy (primary proof point ->
     secondary reinforcement -> honest limitation -> story
     arc) is documented in the shared frame.
  3. TOKEN MAPPING block tells Opus which token resolves to
     which figure.
  4. TOKEN MIXING PROHIBITION is present and explicit.
  5. _DECK_STORY_PLAN_BODY locks slide 1's non-negotiable
     numeric_anchors (0.91 / 0.49, not 0.63 / 0.54).
  6. _BRIEF_SECTION_PLAN_BODY contains the executive summary
     opening sentence template + the §3 finding-order
     instruction.
  7. ORAL_PRESENTATION_CONTEXT is composed into the deck
     speaker-notes prompt and explicitly says the live
     figure context is SPOKEN ONLY.

The prompts are the system-of-record for how Opus structures
the deck and the brief at Pass 1. A silent rewrite that
returned the live figures to the central frame would
re-introduce the headline drift PR #370 partially diagnosed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")


# ── 1. CENTRAL_QUESTION_AND_ANSWER -- December 2025 lock ─────────────


class TestCentralQuestionAndAnswer:

    def test_primary_proof_point_uses_dec_2025_lock_figures(self):
        from tools.story_plan import CENTRAL_QUESTION_AND_ANSWER
        text = CENTRAL_QUESTION_AND_ANSWER
        # Dec 2025 lock figures appear as the primary proof point.
        assert "OOS Sharpe 0.91 (blend) vs 0.49 (benchmark)" in text
        assert "98% improvement" in text
        assert "53 months" in text
        # The "December 2025 academic submission lock" framing is
        # explicit so Opus knows these are not live values.
        assert "December 2025 academic submission lock" in text

    def test_live_figures_documented_as_separate(self):
        """The live figures (1.24 / 0.73) get a one-line callout
        but are explicitly NOT what the brief / deck use. This
        pins both: the live figures are mentioned (so Opus
        knows about them) but framed as the platform's
        Performance Record state, not the submission record."""
        from tools.story_plan import CENTRAL_QUESTION_AND_ANSWER
        text = CENTRAL_QUESTION_AND_ANSWER
        assert "Live Figure" in text or "live figure" in text
        assert "1.24" in text and "0.73" in text
        # And explicitly says the brief / deck use the
        # conservative submission values.
        assert ("conservative submission values" in text
                or "submission record stands" in text
                or "academic record" in text)

    def test_old_live_figure_lead_is_gone(self):
        """The previous prompt led with "OOS Sharpe of 1.24
        versus 0.73 for the benchmark -- a 70% improvement".
        That exact sentence must NOT remain as the PRIMARY
        proof point. The 1.24 / 0.73 / 70 strings can still
        appear elsewhere (as the live-figure callout), but the
        primary proof point line must use 0.91 / 0.49 / 98."""
        from tools.story_plan import CENTRAL_QUESTION_AND_ANSWER
        text = CENTRAL_QUESTION_AND_ANSWER
        # Specifically the old "70% improvement" lead-in
        # framing should be gone -- the new prompt frames the
        # +70% as a LIVE-figure callout, not the primary
        # proof point. A grep that's tolerant of the live
        # context: pin that the "1.24 versus 0.73" SENTENCE
        # the old prompt used is not the primary proof point
        # SENTENCE in the new prompt.
        assert "OOS Sharpe of 1.24 versus 0.73" not in text


class TestStoryArcHierarchy:

    def test_hierarchy_blocks_are_present(self):
        from tools.story_plan import CENTRAL_QUESTION_AND_ANSWER
        text = CENTRAL_QUESTION_AND_ANSWER
        for header in (
            "PRIMARY PROOF POINT",
            "SECONDARY REINFORCEMENT",
            "HONEST LIMITATION",
            "STORY ARC HIERARCHY",
        ):
            assert header in text, (
                f"hierarchy block '{header}' missing -- the "
                "story arc must explicitly rank the proof "
                "points so Opus doesn't bury the OOS headline")

    def test_drawdown_reinforcement_present(self):
        from tools.story_plan import CENTRAL_QUESTION_AND_ANSWER
        text = CENTRAL_QUESTION_AND_ANSWER
        assert "-29.7%" in text and "-52.6%" in text
        assert "32" in text and "71" in text  # recovery months

    def test_honest_limitation_present(self):
        from tools.story_plan import CENTRAL_QUESTION_AND_ANSWER
        text = CENTRAL_QUESTION_AND_ANSWER
        assert "2 of 9" in text
        # And the framing: capital preservation, not market
        # timing.
        assert "capital preservation" in text.lower()
        assert "market timing" in text.lower()


# ── 2. TOKEN MAPPING + MIXING PROHIBITION ────────────────────────────


class TestTokenMappingBlock:

    def test_token_mapping_is_explicit(self):
        from tools.story_plan import CENTRAL_QUESTION_AND_ANSWER
        text = CENTRAL_QUESTION_AND_ANSWER
        assert "TOKEN MAPPING" in text
        # Each headline-token -> value mapping is documented.
        for token, value in (
            ("{{OOS_SHARPE_BLEND}}",        "0.91"),
            ("{{OOS_SHARPE_BENCHMARK}}",    "0.49"),
            ("{{REGIME_SWITCHING_SHARPE}}", "0.63"),
            ("{{BENCHMARK_SHARPE}}",        "0.54"),
        ):
            # The token and the value should appear nearby in
            # the mapping block.
            assert token in text, f"missing token mapping: {token}"
            assert value in text, f"missing value: {value}"

    def test_token_mixing_prohibition_is_present(self):
        """The new TOKEN MIXING PROHIBITION block is the user-
        added requirement. Pin it explicitly -- a future PR
        that drops this would re-allow the 0.91-vs-0.63
        ambiguity that the prohibition is designed to
        prevent."""
        import re
        from tools.story_plan import CENTRAL_QUESTION_AND_ANSWER
        # The multi-line concatenation in the constant introduces
        # extra whitespace at line boundaries; normalize to single
        # spaces before substring matching so the test isn't
        # brittle to formatting.
        normalized = re.sub(r"\s+", " ", CENTRAL_QUESTION_AND_ANSWER)
        assert "TOKEN MIXING PROHIBITION" in normalized
        # The specific prohibition language.
        assert "NEVER place {{OOS_SHARPE_BLEND}}" in normalized
        assert "{{REGIME_SWITCHING_SHARPE}}" in normalized
        # And the location-rules for OOS vs full-period.
        assert "section 1, the section 3 lead, and section 5" in (
            normalized)
        # June 22 2026 (12-slide deck): OOS figures appear on
        # slides 1, 4, 7, 12 -- the OOS headline, the risk-
        # adjusted numbers slide, the OOS validation slide, and
        # the closing answer. The 11-slide deck had OOS on
        # slides 1, 3, 6, 11.
        assert "slides 1, 4, 7, 12" in normalized
        assert 'labeled "Full-Period Sharpe"' in normalized

    def test_prohibition_names_both_blend_and_benchmark_pairs(self):
        from tools.story_plan import CENTRAL_QUESTION_AND_ANSWER
        text = CENTRAL_QUESTION_AND_ANSWER
        # Both pairs -- blend OOS vs full-period AND benchmark
        # OOS vs full-period -- are named in the prohibition.
        assert "{{OOS_SHARPE_BENCHMARK}}" in text
        assert "{{BENCHMARK_SHARPE}}" in text
        # And "0.43" + "0.54" are documented as the pair.
        assert "0.49" in text and "0.54" in text


# ── 3. _DECK_STORY_PLAN_BODY -- slide 1 non-negotiable ────────────────


class TestDeckStoryPlanBody:

    def test_old_live_figure_grounding_is_gone(self):
        """The previous prompt said "The answer must be
        grounded in OOS Sharpe 1.24 vs 0.73 benchmark". That
        line is replaced by the Dec 2025 lock language."""
        from tools.story_plan import _DECK_STORY_PLAN_BODY
        text = _DECK_STORY_PLAN_BODY
        assert "OOS Sharpe 1.24 vs 0.73 benchmark" not in text

    def test_dec_2025_grounding_is_present(self):
        from tools.story_plan import _DECK_STORY_PLAN_BODY
        text = _DECK_STORY_PLAN_BODY
        assert "December 2025 academic submission OOS Sharpe" in text
        assert "0.91" in text and "0.49" in text
        assert "53-month post-2022 window" in text

    def test_slide_1_non_negotiable_block_present(self):
        import re
        from tools.story_plan import _DECK_STORY_PLAN_BODY
        normalized = re.sub(r"\s+", " ", _DECK_STORY_PLAN_BODY)
        assert "SLIDE 1 -- NON-NEGOTIABLE OPENING" in normalized
        # The required anchors.
        assert "oos_sharpe_blend:" in normalized
        assert "0.91" in normalized
        assert "oos_sharpe_benchmark:" in normalized
        assert "0.49" in normalized
        assert "oos_sharpe_improvement_pct" in normalized
        # And the explicit DO NOT directives.
        assert "Do NOT open with a methodology overview" in normalized
        assert "Do NOT bury the headline" in normalized
        assert "Do NOT substitute the full-period Sharpe" in normalized

    def test_presentation_arc_block_present(self):
        """June 22 2026 (12-slide deck) -- new labels include the
        agenda at slide 2 and the AI methodology / live demo flip
        to slides 10/11. Old 11-slide labels (Slides 2-3, Slide 9
        as live demo) are gone."""
        from tools.story_plan import _DECK_STORY_PLAN_BODY
        text = _DECK_STORY_PLAN_BODY
        assert "PRESENTATION ARC" in text
        for slide_label in (
            "Slide 1:",    "Slide 2:",
            "Slides 3-4:", "Slides 5-6:",
            "Slide 7:",    "Slides 8-9:",
            "Slide 10:",   "Slide 11:", "Slide 12:",
        ):
            assert slide_label in text


class TestPresentationDisciplineAndSoWhat:
    """June 22 2026 -- discipline / framing / bullet rules pass
    added three blocks to _DECK_STORY_PLAN_BODY that constrain
    the Sonnet writer. These tests pin the blocks against drift
    so a future edit can't silently weaken the constraints."""

    def test_presentation_discipline_block_present(self):
        from tools.story_plan import _DECK_STORY_PLAN_BODY
        text = _DECK_STORY_PLAN_BODY
        assert "PRESENTATION DISCIPLINE" in text
        assert "Each slide has exactly ONE job" in text
        assert "8 seconds" in text

    def test_so_what_framing_block_present(self):
        from tools.story_plan import _DECK_STORY_PLAN_BODY
        text = _DECK_STORY_PLAN_BODY
        assert "SO WHAT FRAMING" in text
        assert "What does this prove?" in text
        assert "What is the question?" in text

    def test_locked_titles_list_present(self):
        """The 12 locked titles must be enumerated explicitly so
        the Sonnet writer treats them as verbatim."""
        from tools.story_plan import _DECK_STORY_PLAN_BODY
        text = _DECK_STORY_PLAN_BODY
        for snippet in (
            "Yes -- Regime-Conditional Beats 100% Equity",
            "Three Strategies, One Question",
            "53 Months of Unseen Data",
            "Why Static Allocation Failed in 2022",
            "Half the Drawdown, Half the Recovery Time",
            "Does It Hold Up Out-of-Sample? Yes.",
            "{{CURRENT_REGIME}}",
            "{{REGIME_CONFIDENCE}}",
            "2 of 9",
            "What We Learned",
            "Live Demo -- analyticsdesk.app",
            "Yes, With Conditions",
        ):
            assert snippet in text, (
                f"locked title snippet missing: {snippet!r}")
        # And the "LOCKED -- do not generate alternative" framing.
        assert "LOCKED" in text
        assert "verbatim" in text

    def test_bullet_discipline_uses_ceiling_not_target(self):
        from tools.story_plan import _DECK_STORY_PLAN_BODY
        text = _DECK_STORY_PLAN_BODY
        assert "BULLET DISCIPLINE" in text
        # CEILING framing -- not a target / floor.
        assert "CEILING" in text
        assert "Floor is zero" in text
        assert "Silence beats padding" in text

    def test_bullet_writing_rule_carries_wrong_right_examples(self):
        from tools.story_plan import _DECK_STORY_PLAN_BODY
        text = _DECK_STORY_PLAN_BODY
        assert "BULLET WRITING RULE" in text
        assert "Wrong:" in text
        assert "Right:" in text
        # Pin the substantive content of one example so a refactor
        # that strips the examples breaks the test.
        assert "four years of reinvestment" in text

    def test_max_bullets_field_in_schema(self):
        from tools.story_plan import _DECK_STORY_PLAN_BODY
        text = _DECK_STORY_PLAN_BODY
        assert "max_bullets" in text
        # Schema description must call it a CEILING.
        assert "CEILING" in text


class TestDeckPass1TokenCeiling:

    def test_deck_pass1_max_tokens_at_8000(self):
        """Pass 1 max_tokens was bumped 6000 -> 8000 to clear the
        12-slide JSON output plus the new max_bullets field per
        slide. A regression that drops this back to 6000 risks
        silent truncation -> fallback -> script gate locked.
        Pin the value."""
        import inspect
        from tools import story_plan
        source = inspect.getsource(story_plan)
        # The deck-side Pass 1 invocation is the one inside the
        # context after the "PRESENTATION ARC" comment block.
        # Counting deck-side appearances of max_tokens=8000:
        # there are TWO Pass-1 callsites currently (Pass 1a and
        # Pass 1b speaker notes); we just need to confirm the
        # deck-pass-1 (slide_plan) callsite is at 8000.
        assert "max_tokens=8000" in source
        # And the explanatory comment justifying the bump.
        assert ("12-slide structure (PR #375)" in source
                or "12-slide" in source)


class TestDeterministicFallbackCarriesGateComment:

    def test_fallback_docstring_warns_about_gate_behaviour(self):
        from tools.story_plan import _deterministic_deck_plan
        doc = (_deterministic_deck_plan.__doc__ or "")
        assert "GATE BEHAVIOUR" in doc
        assert "deterministic_fallback" in doc
        # Pin the actionable guidance: future improvements must
        # consider gate vs tag.
        assert "consider whether" in doc


# ── 4. _BRIEF_SECTION_PLAN_BODY -- §1 + §3 instructions ──────────────


class TestBriefSectionPlanBody:

    def test_executive_summary_template_present(self):
        from tools.story_plan import _BRIEF_SECTION_PLAN_BODY
        text = _BRIEF_SECTION_PLAN_BODY
        assert "EXECUTIVE SUMMARY OPENING SENTENCE" in text
        assert "{{OOS_SHARPE_BLEND}}" in text
        assert "{{OOS_SHARPE_BENCHMARK}}" in text
        assert "{{OOS_WINDOW_MONTHS}}" in text
        # The required anchors.
        assert "oos_sharpe_blend:" in text
        assert "0.91" in text
        assert "oos_sharpe_benchmark:" in text
        assert "0.49" in text
        assert "oos_window_months:" in text
        assert "53" in text

    def test_section_3_finding_order_present(self):
        from tools.story_plan import _BRIEF_SECTION_PLAN_BODY
        text = _BRIEF_SECTION_PLAN_BODY
        assert "KEY FINDINGS (section 3) STRUCTURE" in text
        assert "Finding 1 -- THE OOS proof point" in text
        assert "Finding 2 -- Drawdown reduction" in text
        assert "Finding 3 -- Honest limitation" in text
        assert "Finding 4" in text and "Full-period" in text
        # ORDER MATTERS instruction.
        assert "ORDER MATTERS" in text


# ── 5. ORAL_PRESENTATION_CONTEXT (deck speaker notes only) ────────────


class TestOralPresentationContext:

    def test_constant_exists(self):
        from tools.story_plan import ORAL_PRESENTATION_CONTEXT
        text = ORAL_PRESENTATION_CONTEXT
        assert text  # non-empty

    def test_spoken_only_directive_present(self):
        """The context note is for ORAL delivery only -- it
        must NOT appear on a slide, in a brief paragraph, or in
        the appendix. The prompt is explicit about that
        scope."""
        from tools.story_plan import ORAL_PRESENTATION_CONTEXT
        text = ORAL_PRESENTATION_CONTEXT
        # Lowercase the search to be tolerant of formatting
        # (the constant is in title case in the prompt).
        lowered = text.lower()
        assert "speaker notes only" in lowered
        assert "never on a written slide" in lowered
        assert ("never appear on" in lowered
                or "does not appear" in lowered.replace(
                    "does NOT appear", "does not appear"))

    def test_understates_current_performance_phrasing(self):
        """Pin the conservative-record framing the user
        specified. The submission UNDERSTATES current
        performance -- this exact language helps Bob frame the
        spoken context correctly without making it sound like
        we 'updated' the figures."""
        import re
        from tools.story_plan import ORAL_PRESENTATION_CONTEXT
        normalized = re.sub(r"\s+", " ", ORAL_PRESENTATION_CONTEXT)
        assert "UNDERSTATES current performance" in normalized
        # And the explicit prohibition on the wrong framing.
        assert "we updated the numbers" in normalized

    def test_threaded_into_deck_speaker_notes_prompt(self):
        """The constant is composed into the deck speaker-
        notes system prompt. The brief prompt should NOT
        include it -- the live-figure context is for ORAL
        delivery only."""
        from tools.story_plan import (
            _DECK_SPEAKER_NOTES_SYSTEM_PROMPT,
            ORAL_PRESENTATION_CONTEXT,
        )
        assert (
            ORAL_PRESENTATION_CONTEXT
            in _DECK_SPEAKER_NOTES_SYSTEM_PROMPT)


# ── 6. Composition pins -- make sure the composite prompts pick up
#    the new constants and the central frame ─────────────────────────


class TestCompositePrompts:

    def test_deck_pass1_prompt_uses_updated_central_frame(self):
        from tools.story_plan import (
            _DECK_STORY_PLAN_SYSTEM_PROMPT,
        )
        # The composed prompt must carry the new Dec 2025
        # primary proof point and NOT the old live lead.
        assert "0.91" in _DECK_STORY_PLAN_SYSTEM_PROMPT
        assert "0.49" in _DECK_STORY_PLAN_SYSTEM_PROMPT
        assert (
            "OOS Sharpe of 1.24 versus 0.73"
            not in _DECK_STORY_PLAN_SYSTEM_PROMPT)

    def test_brief_pass1_prompt_uses_updated_central_frame(self):
        from tools.story_plan import (
            _BRIEF_SECTION_PLAN_SYSTEM_PROMPT,
        )
        assert "0.91" in _BRIEF_SECTION_PLAN_SYSTEM_PROMPT
        assert "0.49" in _BRIEF_SECTION_PLAN_SYSTEM_PROMPT
        assert (
            "OOS Sharpe of 1.24 versus 0.73"
            not in _BRIEF_SECTION_PLAN_SYSTEM_PROMPT)

    def test_brief_pass1_prompt_does_not_include_oral_context(self):
        """The oral-presentation context is deck-only. The brief
        story plan prompt must NOT include it -- live-figure
        framing has no place in a written deliverable."""
        from tools.story_plan import (
            _BRIEF_SECTION_PLAN_SYSTEM_PROMPT,
            ORAL_PRESENTATION_CONTEXT,
        )
        assert (
            ORAL_PRESENTATION_CONTEXT
            not in _BRIEF_SECTION_PLAN_SYSTEM_PROMPT)
