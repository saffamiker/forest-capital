"""tests/test_deck_agenda_slide.py -- pins the agenda slide
contract (June 22 2026).

The agenda slide was inserted at position 2 to give the panel a
structural roadmap before the evidence slides begin. The
contract is:
  - It is SLIDE 2 (between the OOS proof on slide 1 and the
    three-strategy framing on slide 3)
  - It carries no data, no substitution tokens, no chart
  - It is EXCLUDED from brief grounding (structural, not
    analytical -- no brief excerpt should leak into it)
  - It has six agenda items that walk the audience through the
    deck structure
  - Speaker notes guide the presenter on timing + flow
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")


# ── Slide order + title ──────────────────────────────────────────────────


class TestAgendaSlidePosition:

    def test_agenda_is_slide_2(self):
        """SLIDE_TITLES indexes are 0-based; slide 2 is index 1."""
        from tools.academic_deck import SLIDE_TITLES
        assert SLIDE_TITLES[1] == "Agenda"

    def test_total_count_is_twelve(self):
        from tools.academic_deck import DECK_SLIDE_COUNT
        assert DECK_SLIDE_COUNT == 12

    def test_oos_proof_still_at_slide_1(self):
        """The agenda goes AFTER the OOS proof slide. Slide 1
        keeps the verdict-first opener."""
        from tools.academic_deck import SLIDE_TITLES
        assert SLIDE_TITLES[0] == "Does Diversification Beat 100% Equity?"

    def test_three_strategy_framing_pushed_to_slide_3(self):
        from tools.academic_deck import SLIDE_TITLES
        assert SLIDE_TITLES[2] == "Static, Dynamic, or Benchmark?"

    def test_ai_methodology_before_live_demo(self):
        """The flip puts AI methodology at slide 10 and the
        AnalyticsDesk live demo at slide 11. Rationale: the panel
        needs context on how the council works before they watch
        it operate live."""
        from tools.academic_deck import SLIDE_TITLES
        assert SLIDE_TITLES[9] == (
            "How We Used AI: What Worked and What Didn't")
        assert SLIDE_TITLES[10] == (
            "AnalyticsDesk: The Platform Behind the Analysis")

    def test_recommendation_pushed_to_slide_12(self):
        from tools.academic_deck import SLIDE_TITLES
        assert SLIDE_TITLES[11] == "The Answer: Yes, With Conditions"


# ── Slide spec contract ──────────────────────────────────────────────────


class TestAgendaSlideSpec:

    def test_slide_spec_exists_and_is_slice_able(self):
        from tools.academic_deck import _slice_slide_spec
        spec = _slice_slide_spec(2)
        assert spec
        assert "Slide 2 --" in spec
        assert "Agenda" in spec

    def test_spec_carries_all_six_agenda_items(self):
        """The six agenda items spec'd by the user (June 22 2026):
        Investment Case / Evidence / Why Static Failed / OOS
        Validation / Honest Limitations / AI Methodology, Live
        Demo, and Recommendation."""
        from tools.academic_deck import _slice_slide_spec
        spec = _slice_slide_spec(2)
        for item in (
            "The Investment Case",
            "The Evidence",
            "Why Static Failed",
            "Out-of-Sample Validation",
            "Honest Limitations",
            "AI Methodology, Live Demo, and Recommendation",
        ):
            assert item in spec, (
                f"agenda item missing from slide 2 spec: {item}")

    def test_spec_explicitly_disallows_data_tokens_chart(self):
        """The agenda is structural. The spec must explicitly
        instruct the LLM that no data, no tokens, no chart
        belongs here -- otherwise the generator might inject
        the locked OOS Sharpe numbers as decoration."""
        from tools.academic_deck import _slice_slide_spec
        spec = _slice_slide_spec(2)
        # Required table: none. Chart: none.
        assert "Required table: none." in spec
        assert "Chart: none." in spec
        # Explicit "no substitution tokens" instruction.
        assert "no substitution tokens" in spec

    def test_speaker_notes_carry_timing_and_flow(self):
        """The presenter needs orientation: total time + when
        the panel asks questions."""
        from tools.academic_deck import _slice_slide_spec
        spec = _slice_slide_spec(2)
        assert "18-20 minutes" in spec
        assert "questions after slide 12" in spec

    def test_no_substitution_tokens_in_spec_text(self):
        """Defensive grep -- the spec text itself must not
        contain any {{TOKEN}} markers (which the substitution
        layer would replace). The agenda must render identically
        on a cold cache and a warm cache."""
        import re
        from tools.academic_deck import _slice_slide_spec
        spec = _slice_slide_spec(2)
        # Strip the rest of the spec text by isolating just the
        # slide 2 block (everything between "Slide 2 --" and
        # the next "Slide N --"). The slice_slide_spec helper
        # already does this for us, so we just check the
        # returned string.
        matches = re.findall(r"\{\{[A-Z_0-9]+\}\}", spec)
        assert not matches, (
            f"agenda spec must not contain {{TOKEN}} markers; "
            f"found: {matches}")


# ── Brief-grounding exclusion ────────────────────────────────────────────


class TestAgendaSlideExcludedFromBriefGrounding:

    def test_slide_2_in_exclusion_set(self):
        from tools.brief_grounding import (
            SLIDES_EXCLUDED_FROM_BRIEF_GROUNDING,
        )
        assert 2 in SLIDES_EXCLUDED_FROM_BRIEF_GROUNDING

    def test_slide_2_not_in_slide_to_brief_section_map(self):
        """The map intentionally omits excluded slides so a naive
        direct lookup raises rather than silently returning a
        section name."""
        from tools.brief_grounding import SLIDE_TO_BRIEF_SECTION
        assert 2 not in SLIDE_TO_BRIEF_SECTION

    def test_dispatcher_returns_none_for_slide_2(self):
        from tools.brief_grounding import brief_section_for_slide
        assert brief_section_for_slide(2) is None


# ── Chart slot integrity ─────────────────────────────────────────────────


class TestAgendaSlideNoChart:

    def test_slide_2_not_in_slide_charts(self):
        """SLIDE_CHARTS maps slide_number -> chart role. The
        agenda has no chart so it must not appear in the map."""
        from tools.academic_deck import SLIDE_CHARTS
        assert 2 not in SLIDE_CHARTS

    def test_chart_slots_shifted_for_12_slide_deck(self):
        """The chart slots renumbered along with the slides. In
        the 11-slide deck charts were on slides 4, 5, 11; in the
        12-slide deck they're on slides 5, 6, 12 (each shifted
        by +1 due to the agenda insert; the closing-slide chart
        also shifted)."""
        from tools.academic_deck import SLIDE_CHARTS
        assert SLIDE_CHARTS == {
            5: "rolling_correlation",
            6: "strategy_comparison_oos_sharpe",
            12: "efficient_frontier",
        }


# ── Slide 8 -- Macro Context title tokenization ──────────────────────────


class TestSlide8MacroContextTitleTokenized:
    """Slide 8 (Macro Context) title carries the {{CURRENT_REGIME}}
    token so the slide title updates dynamically with the live HMM
    classification. The substitution layer resolves the token at
    generation time -- a 2026-06-22 generation under a BULL read
    renders 'Macro Context: Live Regime Signal -- BULL'; a future
    regeneration under BEAR would render '... BEAR' without any
    code change. Previously the title was hardcoded
    'Macro Context: Why Now Is a BEAR Regime' which would have
    been factually wrong on any BULL/TRANSITION regeneration."""

    def test_slide_8_title_carries_current_regime_token(self):
        from tools.academic_deck import SLIDE_TITLES
        title = SLIDE_TITLES[7]  # zero-indexed; slide 8
        assert "{{CURRENT_REGIME}}" in title
        # And the human-readable framing.
        assert "Live Regime Signal" in title

    def test_slide_8_spec_header_matches_title(self):
        """The marker line in SLIDE_SPECIFICATIONS must match the
        SLIDE_TITLES entry exactly so _slice_slide_spec finds the
        right block."""
        from tools.academic_deck import _slice_slide_spec
        spec = _slice_slide_spec(8)
        assert (
            "Slide 8 -- Macro Context: Live Regime Signal -- "
            "{{CURRENT_REGIME}}") in spec

    def test_slide_8_spec_instructs_llm_to_keep_token_literal(self):
        """The slide spec must instruct the LLM to reproduce the
        title VERBATIM, including the {{CURRENT_REGIME}} marker.
        Without that explicit instruction the LLM might try to
        substitute the regime label itself based on the deck
        context block."""
        from tools.academic_deck import _slice_slide_spec
        spec = _slice_slide_spec(8)
        assert "reproduce the title VERBATIM" in spec
        assert "Do NOT substitute the regime label yourself" in spec

    def test_slide_8_speaker_notes_have_molly_review_directive(self):
        """The speaker notes must carry a presenter-review note
        instructing Molly to refresh stale contextual event
        references before the July 1 panel. The watchpoint values
        are live but the narrative context is from generation
        time."""
        from tools.academic_deck import _slice_slide_spec
        spec = _slice_slide_spec(8)
        assert "PRESENTER REVIEW -- Molly" in spec
        assert "July 1 panel" in spec
        assert "contextual event references" in spec
        # The "live values vs static context" distinction is
        # the conceptual core of the directive -- pin it
        # explicitly.
        assert "watchpoint VALUES are live" in spec
        assert "NARRATIVE CONTEXT" in spec


# ── Timing budget ────────────────────────────────────────────────────────


class TestAgendaSlideTimingBudget:

    def test_slide_2_appears_in_timing_table(self):
        from tools.academic_docx import _SLIDE_TIMINGS_MIN
        slide_numbers = [s[0] for s in _SLIDE_TIMINGS_MIN]
        assert 2 in slide_numbers

    def test_slide_2_timing_is_thirty_seconds(self):
        """0.5 minutes = 30 seconds. The agenda is a structural
        walkthrough, not analytical content."""
        from tools.academic_docx import _SLIDE_TIMINGS_MIN
        slide_2_timing = next(
            (mins for n, mins, _label in _SLIDE_TIMINGS_MIN
             if n == 2), None)
        assert slide_2_timing == 0.5

    def test_total_timing_sums_to_18_to_20_minutes(self):
        """The agenda costs 30 seconds; the deck still totals
        18-20 minutes."""
        from tools.academic_docx import _SLIDE_TIMINGS_MIN
        total = sum(mins for _n, mins, _label in _SLIDE_TIMINGS_MIN)
        assert 18.0 <= total <= 20.0, (
            f"deck total {total} minutes outside 18-20 budget")
