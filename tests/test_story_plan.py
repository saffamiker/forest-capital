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


class TestMidpointFeedbackFraming:
    """PR #334 -- midpoint panel feedback (Dr. Panttser) directed three
    structural constraints into every generator prompt: a three-strategy
    lens, a front-loaded central question + answer, and an investable-
    conclusion guard. These tests pin each constant + verify each is
    composed into the appropriate system prompts so a future refactor
    cannot quietly drop the framing the midpoint review required."""

    def test_three_strategy_frame_constant(self):
        from tools.story_plan import THREE_STRATEGY_FRAME
        # The three strategies named and labelled.
        assert "Benchmark: S&P 500" in THREE_STRATEGY_FRAME
        assert "Static blend: Classic 60/40" in THREE_STRATEGY_FRAME
        assert "Dynamic blend: regime-conditional" in THREE_STRATEGY_FRAME
        # The 10-strategy engine relegated to the appendix.
        assert "appendix material" in THREE_STRATEGY_FRAME

    def test_central_question_and_answer_constant(self):
        from tools.story_plan import CENTRAL_QUESTION_AND_ANSWER
        # The verbatim question + the answer with quantified
        # evidence.
        #
        # June 22 2026 (story-arc PR) -- the primary proof point
        # was switched from the live figures (1.24 vs 0.73,
        # +70%) to the December 2025 academic submission lock
        # (0.86 vs 0.43, +98% over 53 months). The live
        # figures are still mentioned in the block as the
        # Performance Record context but they are not the
        # lead-with answer; that role belongs to the
        # submission-lock figures the panel defends.
        assert "diversification improve risk-adjusted performance" \
            in CENTRAL_QUESTION_AND_ANSWER
        assert "OOS Sharpe 0.86 (blend) vs 0.43 (benchmark)" \
            in CENTRAL_QUESTION_AND_ANSWER
        assert "98% improvement" in CENTRAL_QUESTION_AND_ANSWER
        assert "53 months" in CENTRAL_QUESTION_AND_ANSWER

    def test_investable_conclusion_guard_constant(self):
        from tools.story_plan import INVESTABLE_CONCLUSION_GUARD
        # The forbidden academic-hedging phrases pinned.
        assert "future research suggests" in INVESTABLE_CONCLUSION_GUARD
        assert "further study would benefit from" \
            in INVESTABLE_CONCLUSION_GUARD
        # The CIO-memo voice contract.
        assert "CIO memo" in INVESTABLE_CONCLUSION_GUARD
        # The "conditions to revisit" close.
        assert "would be revisited" in INVESTABLE_CONCLUSION_GUARD

    def test_deck_pass1_prompt_carries_all_three_framing_constants(self):
        from tools.story_plan import (
            CENTRAL_QUESTION_AND_ANSWER, INVESTABLE_CONCLUSION_GUARD,
            THREE_STRATEGY_FRAME, _DECK_STORY_PLAN_SYSTEM_PROMPT,
        )
        # The system prompt sent to Opus on Pass 1 must carry every
        # framing constant verbatim -- a substring check is enough
        # because the constants are stable strings.
        assert THREE_STRATEGY_FRAME in _DECK_STORY_PLAN_SYSTEM_PROMPT
        assert CENTRAL_QUESTION_AND_ANSWER \
            in _DECK_STORY_PLAN_SYSTEM_PROMPT
        assert INVESTABLE_CONCLUSION_GUARD \
            in _DECK_STORY_PLAN_SYSTEM_PROMPT

    def test_deck_pass1_prompt_carries_economic_storytelling(self):
        from tools.story_plan import _DECK_STORY_PLAN_SYSTEM_PROMPT
        # The WHY / WHEN layer pinned -- midpoint feedback called this
        # out as the missing economic-intuition layer.
        assert "ECONOMIC STORYTELLING REQUIREMENT" \
            in _DECK_STORY_PLAN_SYSTEM_PROMPT
        assert "WHY: HMM identifies structural market state changes" \
            in _DECK_STORY_PLAN_SYSTEM_PROMPT
        assert "WHEN: Concrete examples from the play-by-play" \
            in _DECK_STORY_PLAN_SYSTEM_PROMPT
        # The 2022 BEAR call + Liberation Day examples cited verbatim.
        assert "2022 BEAR call" in _DECK_STORY_PLAN_SYSTEM_PROMPT
        assert "Liberation Day" in _DECK_STORY_PLAN_SYSTEM_PROMPT
        # Original schema body still present.
        assert "central_argument" in _DECK_STORY_PLAN_SYSTEM_PROMPT
        assert "slide_plan" in _DECK_STORY_PLAN_SYSTEM_PROMPT

    def test_script_pass2_prompt_carries_audience_calibration(self):
        from tools.story_plan import (
            CENTRAL_QUESTION_AND_ANSWER, THREE_STRATEGY_FRAME,
            _DECK_FULL_SCRIPT_SYSTEM_PROMPT,
        )
        # The script gets the central question + three-strategy frame
        # AND a script-specific audience-calibration block translating
        # every technical claim into an investment implication.
        assert THREE_STRATEGY_FRAME in _DECK_FULL_SCRIPT_SYSTEM_PROMPT
        assert CENTRAL_QUESTION_AND_ANSWER \
            in _DECK_FULL_SCRIPT_SYSTEM_PROMPT
        assert "AUDIENCE CALIBRATION" in _DECK_FULL_SCRIPT_SYSTEM_PROMPT
        # The "every technical result -> investment implication" rule
        # + the exact example patterns the midpoint feedback flagged.
        assert "the next sentence must translate it to an investment " \
               "implication" in _DECK_FULL_SCRIPT_SYSTEM_PROMPT
        assert "stay invested rather than panic-selling" \
            in _DECK_FULL_SCRIPT_SYSTEM_PROMPT
        # Original schema body still present.
        assert "full_script" in _DECK_FULL_SCRIPT_SYSTEM_PROMPT
        assert "estimated_duration_minutes" \
            in _DECK_FULL_SCRIPT_SYSTEM_PROMPT

    def test_brief_pass1_prompt_carries_all_three_framing_constants(self):
        from tools.story_plan import (
            CENTRAL_QUESTION_AND_ANSWER, INVESTABLE_CONCLUSION_GUARD,
            THREE_STRATEGY_FRAME, _BRIEF_SECTION_PLAN_SYSTEM_PROMPT,
        )
        assert THREE_STRATEGY_FRAME \
            in _BRIEF_SECTION_PLAN_SYSTEM_PROMPT
        assert CENTRAL_QUESTION_AND_ANSWER \
            in _BRIEF_SECTION_PLAN_SYSTEM_PROMPT
        assert INVESTABLE_CONCLUSION_GUARD \
            in _BRIEF_SECTION_PLAN_SYSTEM_PROMPT
        # Original schema body (the six rubric sections) survives.
        assert "executive_summary" in _BRIEF_SECTION_PLAN_SYSTEM_PROMPT
        assert "section_plan" in _BRIEF_SECTION_PLAN_SYSTEM_PROMPT


class TestEvaluatorCriterion6:
    """PR #334 -- both evaluator rubrics gain a sixth criterion
    (INVESTABLE CONCLUSION) so the harness retries any plan that's
    technically correct but communicates poorly to a non-technical
    decision-maker. The threshold scales proportionally from 7.0/10 to
    8.4/12."""

    def test_deck_evaluator_has_criterion_6_investable_conclusion(self):
        from tools.story_plan import STORY_PLAN_EVALUATOR_PROMPT
        assert "6. INVESTABLE CONCLUSION (0-2)" \
            in STORY_PLAN_EVALUATOR_PROMPT
        # The "non-technical decision-maker" lens pinned.
        assert "non-technical decision-maker" \
            in STORY_PLAN_EVALUATOR_PROMPT
        assert "Forest Capital representative" \
            in STORY_PLAN_EVALUATOR_PROMPT
        # The 12-point ceiling + 8.4 threshold pinned.
        assert "12 points" in STORY_PLAN_EVALUATOR_PROMPT
        assert "8.4" in STORY_PLAN_EVALUATOR_PROMPT
        # The midpoint-feedback grounding quote.
        assert "too academic" in STORY_PLAN_EVALUATOR_PROMPT.lower()

    def test_brief_evaluator_has_criterion_6_investable_conclusion(self):
        from tools.story_plan import BRIEF_PLAN_EVALUATOR_PROMPT
        # PR #335 expanded the brief rubric to 7 criteria (14-point
        # ceiling, 9.8 threshold). The deck rubric is still at 6
        # criteria (12-point, 8.4 threshold) -- the deck does not
        # require a References section so the academic-grounding
        # criterion is brief-only.
        assert "6. INVESTABLE CONCLUSION (0-2)" \
            in BRIEF_PLAN_EVALUATOR_PROMPT
        assert "non-technical decision-maker" \
            in BRIEF_PLAN_EVALUATOR_PROMPT
        assert "14 points" in BRIEF_PLAN_EVALUATOR_PROMPT
        assert "9.8" in BRIEF_PLAN_EVALUATOR_PROMPT


class TestExecutiveVoiceRequirement:
    """June 21 2026 -- EXECUTIVE_VOICE_REQUIREMENT threads memo voice
    into the brief Pass 1 prompt AND the per-section Sonnet specs.
    Pins the constant's load-bearing content + verifies it composes
    into both call sites + confirms BRIEF_PLAN_EVALUATOR_PROMPT
    flags the prohibited academic phrases."""

    def test_constant_exists_with_voice_rules(self):
        from tools.story_plan import EXECUTIVE_VOICE_REQUIREMENT
        # Header + audience targeting.
        assert "VOICE AND AUDIENCE REQUIREMENT" \
            in EXECUTIVE_VOICE_REQUIREMENT
        assert "senior investment professional addressing a CIO" \
            in EXECUTIVE_VOICE_REQUIREMENT
        # The six memo-voice rules each surface a load-bearing phrase.
        assert "Lead every section with the conclusion" \
            in EXECUTIVE_VOICE_REQUIREMENT
        assert "Translate every metric into a business consequence" \
            in EXECUTIVE_VOICE_REQUIREMENT
        assert "2022 drawdown as the emotional anchor" \
            in EXECUTIVE_VOICE_REQUIREMENT
        assert "Make the recommendation unambiguous" \
            in EXECUTIVE_VOICE_REQUIREMENT
        assert "Keep sentences short" in EXECUTIVE_VOICE_REQUIREMENT
        assert "Never use passive voice" in EXECUTIVE_VOICE_REQUIREMENT

    def test_constant_lists_prohibited_phrases(self):
        from tools.story_plan import EXECUTIVE_VOICE_REQUIREMENT
        # Each prohibited phrase from the spec must appear so a
        # downstream caller (Sonnet section writer, the evaluator)
        # can lean on the verbatim text rather than re-inventing
        # the list.
        for phrase in (
            "It is worth noting that",
            "Further research would benefit from",
            "The results suggest",
            "It could be argued that",
            "One limitation is that",
        ):
            assert phrase in EXECUTIVE_VOICE_REQUIREMENT, (
                f"prohibited phrase missing from constant: {phrase}")

    def test_threaded_into_brief_pass1_system_prompt(self):
        from tools.story_plan import (
            EXECUTIVE_VOICE_REQUIREMENT,
            _BRIEF_SECTION_PLAN_SYSTEM_PROMPT,
        )
        assert EXECUTIVE_VOICE_REQUIREMENT \
            in _BRIEF_SECTION_PLAN_SYSTEM_PROMPT
        # Original schema body (the six rubric sections) survives.
        assert "executive_summary" in _BRIEF_SECTION_PLAN_SYSTEM_PROMPT
        assert "section_plan" in _BRIEF_SECTION_PLAN_SYSTEM_PROMPT

    def test_threaded_into_per_section_spec_injector(self):
        """_inject_brief_section_plan in main.py prepends the
        executive-voice rules to each per-section Sonnet task so the
        downstream writer sees the rules even though its conversation
        is independent of the Pass-1 system prompt."""
        import main
        from tools.story_plan import EXECUTIVE_VOICE_REQUIREMENT
        section_plan = {
            "executive_summary": {
                "key_message": "Diversification works.",
                "numeric_anchors": {"oos_sharpe": 1.24},
                "target_length_words": 200,
            },
        }
        specs = [{"key": "executive_summary", "task": "Original task"}]
        out = main._inject_brief_section_plan(specs, section_plan)
        assert EXECUTIVE_VOICE_REQUIREMENT in out[0]["task"]
        # The locked plan + the original task both survive.
        assert "Diversification works." in out[0]["task"]
        assert "Original task" in out[0]["task"]

    def test_not_threaded_into_deck_prompts(self):
        """Per spec, the deck has its own audience calibration via
        _SCRIPT_AUDIENCE_CALIBRATION and must NOT receive the
        executive-voice rules (deck and brief target different
        audiences and the deck's calibration is already calibrated)."""
        from tools.story_plan import (
            EXECUTIVE_VOICE_REQUIREMENT,
            _DECK_STORY_PLAN_SYSTEM_PROMPT,
            _DECK_FULL_SCRIPT_SYSTEM_PROMPT,
        )
        assert EXECUTIVE_VOICE_REQUIREMENT \
            not in _DECK_STORY_PLAN_SYSTEM_PROMPT
        assert EXECUTIVE_VOICE_REQUIREMENT \
            not in _DECK_FULL_SCRIPT_SYSTEM_PROMPT

    def test_brief_evaluator_flags_prohibited_phrases(self):
        from tools.story_plan import BRIEF_PLAN_EVALUATOR_PROMPT
        # Criterion 4 (SENIOR AUDIENCE CALIBRATION) now lists the
        # specific phrases that knock 0.5 points each and the
        # methodology-first leading rule that zeros the criterion.
        assert "prohibited academic phrases" \
            in BRIEF_PLAN_EVALUATOR_PROMPT
        for phrase in (
            "it is worth noting", "further research",
            "results suggest", "it could be argued",
            "one limitation is",
        ):
            assert phrase in BRIEF_PLAN_EVALUATOR_PROMPT, (
                f"evaluator missing prohibited phrase: {phrase}")
        assert "reduces this criterion by 0.5 points" \
            in BRIEF_PLAN_EVALUATOR_PROMPT
        assert "leads with methodology rather than conclusion" \
            in BRIEF_PLAN_EVALUATOR_PROMPT

    def test_constant_contains_anti_ai_writing_rules(self):
        """Commit 2 extends EXECUTIVE_VOICE_REQUIREMENT with the
        NATURAL WRITING REQUIREMENT block and the eight prohibited
        AI patterns. Spot check on the load-bearing prohibited words
        + header to confirm the appended block is present."""
        from tools.story_plan import EXECUTIVE_VOICE_REQUIREMENT
        assert "NATURAL WRITING REQUIREMENT" in EXECUTIVE_VOICE_REQUIREMENT
        assert "PROHIBITED AI WRITING PATTERNS" in EXECUTIVE_VOICE_REQUIREMENT
        # The two highest-signal AI tells the spec calls out.
        assert "delve into" in EXECUTIVE_VOICE_REQUIREMENT
        assert '"leverage" (as a verb' in EXECUTIVE_VOICE_REQUIREMENT
        # Other prohibited words must surface too -- list keeps the
        # constant from being silently shortened in a future edit.
        for word in (
            '"importantly,"', '"notably,"',
            '"a testament to"', '"game-changing"',
            '"cutting-edge"', '"it goes without saying"',
        ):
            assert word in EXECUTIVE_VOICE_REQUIREMENT, (
                f"prohibited AI word missing from constant: {word}")
        # The eight numbered AI patterns each surface their header.
        for pattern in (
            "Hollow openers", "Parallel list structures",
            "Transition signposting", "Adjective stacking",
            "Suspiciously perfect structure", "Numeric padding",
            "Voice consistency",
        ):
            assert pattern in EXECUTIVE_VOICE_REQUIREMENT, (
                f"AI pattern missing from constant: {pattern}")
        # The read-aloud test -- the final guidance the spec called out.
        assert "read each paragraph aloud" in EXECUTIVE_VOICE_REQUIREMENT

    def test_brief_evaluator_flags_ai_writing_patterns(self):
        """Criterion 4 now references the AI writing patterns
        alongside the prohibited academic phrases."""
        from tools.story_plan import BRIEF_PLAN_EVALUATOR_PROMPT
        assert "AI writing patterns" in BRIEF_PLAN_EVALUATOR_PROMPT
        # Specific pattern names called out for the evaluator.
        for token in (
            "parallel three-item lists", "hollow opener sentences",
            "adjective stacking",
        ):
            assert token in BRIEF_PLAN_EVALUATOR_PROMPT, (
                f"evaluator missing AI pattern: {token}")
        # The same prohibited-word set surfaces here too so the
        # evaluator has the exact same dictionary the prompt does.
        for word in ("leverage", "delve", "importantly", "notably"):
            assert f'"{word}"' in BRIEF_PLAN_EVALUATOR_PROMPT, (
                f"evaluator missing prohibited word: {word}")


class TestRubricCoverageFixes:
    """PR #335 -- the rubric-coverage audit (Q2-Q5) identified four
    structural gaps: per-slide duration anchoring (Q2), static
    allocation theoretical justification missing as a Part I
    deliverable (Q3), rebalancing frequency disclosure (Q4) with an
    inherited accuracy bug in editor_content.py, and academic
    citation grounding (Q5). All four fixes thread through the
    shared framing constant pattern PR #334 introduced so the brief,
    deck, and script all inherit the same contract."""

    def test_static_allocation_justification_constant_present(self):
        from tools.story_plan import STATIC_ALLOCATION_JUSTIFICATION
        # Part I rubric language pinned.
        assert "Part I rubric" in STATIC_ALLOCATION_JUSTIFICATION
        # The four required elements: fixed allocation, Markowitz
        # justification, historical case, 2022 limitation.
        assert "60% S&P 500" in STATIC_ALLOCATION_JUSTIFICATION
        assert "Markowitz (1952)" in STATIC_ALLOCATION_JUSTIFICATION
        assert "mean-variance theory" \
            in STATIC_ALLOCATION_JUSTIFICATION
        assert "reliably negative during 2000-2020" \
            in STATIC_ALLOCATION_JUSTIFICATION
        assert "2022 limitation" in STATIC_ALLOCATION_JUSTIFICATION

    def test_academic_grounding_requirement_constant_present(self):
        from tools.story_plan import ACADEMIC_GROUNDING_REQUIREMENT
        # All seven required citations named in the in-text usage
        # rules (each rule appears as a numbered list item with the
        # canonical author-year form).
        for citation in (
            "Hamilton (1989)", "Ang and Bekaert (2002)",
            "Markowitz (1952)", "Fama and French (1993)",
            "Carhart (1997)", "Sharpe (1994)", "Lo (2002)",
        ):
            assert citation in ACADEMIC_GROUNDING_REQUIREMENT, (
                f"missing in-text reference: {citation}")
        # The "use them exactly as provided" + mandatory-requirements
        # block pinned so a future trim cannot quietly relax the
        # contract.
        assert "must be used exactly as provided" \
            in ACADEMIC_GROUNDING_REQUIREMENT
        assert "Every citation used in-text must appear in References" \
            in ACADEMIC_GROUNDING_REQUIREMENT
        assert "Do not add citations not in this list" \
            in ACADEMIC_GROUNDING_REQUIREMENT

    def test_verified_citations_constant_carries_all_seven_dois(self):
        """The seven primary-source citations are pre-verified from
        their original journals; hardcoded into the constant so the
        brief LLM does not have to web-search and cannot drift to
        similar-but-different citations."""
        from tools.story_plan import VERIFIED_CITATIONS
        # All seven keys present (expanded from the initial four in
        # the same PR to cover Sharpe ratio + Fama-French + Lo
        # Deflated Sharpe Ratio).
        assert set(VERIFIED_CITATIONS.keys()) == {
            "hamilton_1989", "ang_bekaert_2002",
            "markowitz_1952", "carhart_1997",
            "sharpe_1994", "fama_french_1993", "lo_2002",
        }
        # The verified DOIs pinned individually so any value drift
        # surfaces immediately.
        assert "10.2307/1912559" in VERIFIED_CITATIONS["hamilton_1989"]
        assert "10.1093/rfs/15.4.1137" \
            in VERIFIED_CITATIONS["ang_bekaert_2002"]
        assert "10.1111/j.1540-6261.1952.tb01525.x" \
            in VERIFIED_CITATIONS["markowitz_1952"]
        assert "10.1111/j.1540-6261.1997.tb03808.x" \
            in VERIFIED_CITATIONS["carhart_1997"]
        assert "10.3905/jpm.1994.409501" \
            in VERIFIED_CITATIONS["sharpe_1994"]
        assert "10.1016/0304-405X(93)90023-5" \
            in VERIFIED_CITATIONS["fama_french_1993"]
        assert "10.2469/faj.v58.n4.2453" \
            in VERIFIED_CITATIONS["lo_2002"]
        # Each citation includes a DOI (defensive against a
        # truncation that drops the second half).
        for key, citation in VERIFIED_CITATIONS.items():
            assert "https://doi.org/" in citation, (
                f"missing DOI in {key}")

    def test_grounding_requirement_injects_all_seven_dois_verbatim(self):
        """Each of the seven verified DOIs lands in the brief Pass-1
        prompt verbatim -- the brief LLM sees the bibliographic
        details exactly as verified and cannot drift to a similar-
        but-different citation. Hamilton + Lo are spot-checked
        explicitly per the user's directive; the loop below pins the
        remaining five."""
        from tools.story_plan import (
            ACADEMIC_GROUNDING_REQUIREMENT, VERIFIED_CITATIONS,
        )
        # Spot-check the two ends of the bibliography.
        assert "10.2307/1912559" in ACADEMIC_GROUNDING_REQUIREMENT
        assert "10.2469/faj.v58.n4.2453" \
            in ACADEMIC_GROUNDING_REQUIREMENT
        # The remaining five verified DOIs land too.
        for key, citation in VERIFIED_CITATIONS.items():
            doi_fragment = citation.split("https://doi.org/")[-1]
            assert doi_fragment in ACADEMIC_GROUNDING_REQUIREMENT, (
                f"DOI fragment for {key} did not land in the "
                "grounding requirement")

    def test_no_web_search_citation_step_in_brief_path(self):
        """The user's directive: citations are compile-time constants,
        not runtime web-search lookups. Pin the absence of any
        web-search-driven citation resolution helper in story_plan.py
        so a future refactor cannot reintroduce the runtime lookup
        that the verified-citation constant supersedes."""
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "backend"
               / "tools" / "story_plan.py").read_text(encoding="utf-8")
        # No web-search-driven citation resolver function or constant.
        assert "_resolve_academic_citations" not in src
        assert "STEP 0" not in src
        # WEB_SEARCH_TOOL is not imported into story_plan -- the
        # citation grounding is purely compile-time.
        assert "WEB_SEARCH_TOOL" not in src

    def test_static_justification_threaded_into_deck_pass1(self):
        from tools.story_plan import (
            STATIC_ALLOCATION_JUSTIFICATION,
            _DECK_STORY_PLAN_SYSTEM_PROMPT,
        )
        assert STATIC_ALLOCATION_JUSTIFICATION \
            in _DECK_STORY_PLAN_SYSTEM_PROMPT

    def test_static_justification_threaded_into_brief_pass1(self):
        from tools.story_plan import (
            STATIC_ALLOCATION_JUSTIFICATION,
            _BRIEF_SECTION_PLAN_SYSTEM_PROMPT,
        )
        assert STATIC_ALLOCATION_JUSTIFICATION \
            in _BRIEF_SECTION_PLAN_SYSTEM_PROMPT

    def test_academic_grounding_threaded_into_brief_pass1_only(self):
        """The brief gets full citations + a References section; the
        deck does not require a formal Reference section in slides so
        only a verbal-mention note rides along on the deck path."""
        from tools.story_plan import (
            ACADEMIC_GROUNDING_REQUIREMENT,
            _BRIEF_SECTION_PLAN_SYSTEM_PROMPT,
            _DECK_STORY_PLAN_SYSTEM_PROMPT,
        )
        assert ACADEMIC_GROUNDING_REQUIREMENT \
            in _BRIEF_SECTION_PLAN_SYSTEM_PROMPT
        # The deck path does NOT carry the full grounding requirement
        # (no References section in slides) -- it carries the verbal-
        # mention note instead.
        assert ACADEMIC_GROUNDING_REQUIREMENT \
            not in _DECK_STORY_PLAN_SYSTEM_PROMPT
        # The verbal-mention note IS on the deck path.
        assert "ACADEMIC GROUNDING (deck speaker notes)" \
            in _DECK_STORY_PLAN_SYSTEM_PROMPT
        assert "Hamilton (1989)" in _DECK_STORY_PLAN_SYSTEM_PROMPT
        assert "Ang and Bekaert (2002)" \
            in _DECK_STORY_PLAN_SYSTEM_PROMPT

    def test_brief_evaluator_has_criterion_7_academic_grounding(self):
        from tools.story_plan import BRIEF_PLAN_EVALUATOR_PROMPT
        assert "7. ACADEMIC GROUNDING (0-2)" \
            in BRIEF_PLAN_EVALUATOR_PROMPT
        # Criterion 7 references the expanded "five of the seven"
        # threshold (updated when VERIFIED_CITATIONS expanded from
        # 4 to 7 entries).
        assert "five of the seven" in BRIEF_PLAN_EVALUATOR_PROMPT
        # All seven required citations called out by name in the
        # rubric.
        for citation in (
            "Hamilton 1989", "Ang and Bekaert 2002", "Markowitz 1952",
            "Carhart 1997", "Sharpe 1994", "Fama and French 1993",
            "Lo 2002",
        ):
            assert citation in BRIEF_PLAN_EVALUATOR_PROMPT, (
                f"criterion 7 missing reference: {citation}")
        # The contextual placement rules (Hamilton in methodology, etc.)
        # are pinned -- scoring criterion 2 requires correct PLACEMENT
        # too, not just presence.
        assert ("Hamilton (1989) in methodology"
                in BRIEF_PLAN_EVALUATOR_PROMPT)
        # The 14-point ceiling + 9.8 threshold pinned.
        assert "seven criteria" in BRIEF_PLAN_EVALUATOR_PROMPT
        assert "14 points" in BRIEF_PLAN_EVALUATOR_PROMPT
        assert "9.8" in BRIEF_PLAN_EVALUATOR_PROMPT

    def test_pass1b_speaker_notes_carry_duration_band(self):
        """Q2 -- the speaker_notes word band pins the per-slide
        cadence (~90-120 seconds per slide). Originally lived in
        the Pass-1 body; June 21 2026 split moved it to the
        dedicated _DECK_SPEAKER_NOTES_BODY constant so Pass 1a's
        lean schema stays under the 4000-token ceiling."""
        from tools.story_plan import _DECK_SPEAKER_NOTES_BODY
        assert "200-280 words" in _DECK_SPEAKER_NOTES_BODY
        assert "90-120 seconds" in _DECK_SPEAKER_NOTES_BODY

    def test_pass2_script_carries_per_slide_word_band(self):
        """Q2 -- the Pass-2 script body pins the per-slide word band
        + the 11-slide total so the script does not over-allocate to
        opening slides at the expense of the AI methodology +
        conclusion slides."""
        from tools.story_plan import _DECK_FULL_SCRIPT_BODY
        assert "150-200 words" in _DECK_FULL_SCRIPT_BODY
        assert "1650-2200 words" in _DECK_FULL_SCRIPT_BODY
        # The "do not over-allocate" guard.
        assert "Do not over-allocate to opening slides" \
            in _DECK_FULL_SCRIPT_BODY
        # The AI methodology + conclusion get full allocations.
        assert "AI methodology slide (slide 10)" \
            in _DECK_FULL_SCRIPT_BODY

    def test_editor_content_quarterly_rebalancing_string_removed(self):
        """Q4 (accuracy bug) -- the midpoint paper section description
        previously claimed 'quarterly rebalancing' which contradicts
        the production behaviour (monthly evaluation, 2pp threshold).
        Pinning the absence of the wrong string + the presence of the
        correct one."""
        from pathlib import Path
        import re
        raw = (Path(__file__).resolve().parents[1] / "backend"
               / "tools" / "editor_content.py").read_text(
                   encoding="utf-8")
        # Collapse Python string-literal continuations so multi-line
        # adjacent-string-concat (which Python concatenates at parse
        # time) is matched as a single string by these assertions.
        src = re.sub(r'"\s*\n\s+"', '', raw)
        assert "quarterly rebalancing" not in src.lower()
        assert "monthly evaluation" in src
        assert "2 percentage points" in src
        assert "event-driven, not calendar-driven" in src

    def test_brief_methodology_task_carries_rebalancing_disclosure(
            self):
        """Q4 -- the brief Section 2 Methodology task instructs an
        explicit rebalancing-frequency disclosure paragraph + the
        justification for monthly rather than quarterly cadence."""
        from pathlib import Path
        import re
        raw = (Path(__file__).resolve().parents[1] / "backend"
               / "main.py").read_text(encoding="utf-8")
        # Collapse Python string-literal continuations the same way
        # the existing structure test does.
        source = re.sub(r'"\s*\n\s+"', '', raw)
        assert "Disclose the rebalancing frequency explicitly" \
            in source
        assert "2 percentage points" in source
        assert "monthly evaluation matches the cadence at which the " \
               "HMM produces regime updates" in source

    def test_brief_methodology_task_carries_required_citations(self):
        """Q5 -- the brief Section 2 Methodology task names all four
        required academic citations inline so the per-section LLM
        pass cannot drift back to citation-free prose."""
        from pathlib import Path
        import re
        raw = (Path(__file__).resolve().parents[1] / "backend"
               / "main.py").read_text(encoding="utf-8")
        source = re.sub(r'"\s*\n\s+"', '', raw)
        assert "Hamilton (1989)" in source
        assert "Ang and Bekaert (2002)" in source
        assert "Markowitz (1952)" in source
        assert "Carhart, 1997" in source or "Carhart (1997)" in source


class TestAppendixFramingPrelude:
    """PR #334 -- the analytical appendix is not a thin scaffolding
    layer; it carries the FULL 10-strategy evidence base. The framing
    prelude threaded into every section task gives the writer the
    audience contract + the economic-intuition obligation (the WHAT
    -> WHY translation that midpoint feedback called out as missing)."""

    def test_appendix_prelude_pins_audience_and_economic_intuition(self):
        from main import _APPENDIX_FRAMING_PRELUDE
        # The audience contract.
        assert "analytical evidence base" \
            in _APPENDIX_FRAMING_PRELUDE
        # The three-strategy lens explicitly does NOT apply to the
        # appendix -- the full 10-strategy table is appropriate here.
        assert "ALL strategies from the cache" \
            in _APPENDIX_FRAMING_PRELUDE
        # The economic-intuition layer.
        assert "economic intuition explaining what the result MEANS" \
            in _APPENDIX_FRAMING_PRELUDE
        # The sensitivity-analysis-with-interpretation requirement.
        assert "Sensitivity analysis results must be presented with " \
               "interpretation" in _APPENDIX_FRAMING_PRELUDE


class TestBriefExecutiveSummaryEvaluator:
    """June 21 2026 -- the brief executive_summary section was
    scoring 5.45 on the primary evaluator across all 3 attempts. Root
    cause: academic_review_peer_evaluator_prompt's criteria
    (rubric_mapped, data_specific, requirements_aligned,
    actionable_next_steps) score a PEER REVIEW VERDICT, not a written
    document section. A correct executive summary scores structurally
    poorly on those criteria. brief_executive_summary_evaluator_prompt
    replaces it for this section only, scoring against criteria the
    section was actually written to satisfy."""

    def test_evaluator_prompt_pins_five_section_appropriate_criteria(self):
        from agents.evaluator_prompts import (
            brief_executive_summary_evaluator_prompt,
        )
        prompt = brief_executive_summary_evaluator_prompt()
        for criterion in (
            "opens_with_verdict",
            "numeric_anchors_used",
            "three_strategy_frame_referenced",
            "closes_with_forward_reference",
            "length_in_target",
        ):
            assert criterion in prompt, (
                f"executive summary evaluator missing criterion: {criterion}")
        # The criteria that DO NOT apply must be absent so a future
        # edit can't quietly re-introduce the peer-review framing.
        for peer_criterion in (
            "rubric_mapped", "data_specific",
            "requirements_aligned", "actionable_next_steps",
        ):
            assert peer_criterion not in prompt, (
                f"peer-review criterion leaked into the executive "
                f"summary evaluator: {peer_criterion}")

    def test_evaluator_calls_out_that_full_recommendation_is_section_5(self):
        """The load-bearing instruction: a correct executive summary
        defers the full recommendation to Section 5. Without this
        guidance the evaluator could mis-penalise the closing copy
        for not making the full investment recommendation."""
        from agents.evaluator_prompts import (
            brief_executive_summary_evaluator_prompt,
        )
        prompt = brief_executive_summary_evaluator_prompt()
        assert "SECTION 5" in prompt
        # The forward-reference criterion must explain that a full
        # recommendation in the closing is OVER-STEPPING, not the goal.
        assert "OVER-stepping" in prompt
        # The 'must score 8+' guidance is the canonical instruction
        # the spec called out -- pin it verbatim.
        assert "must score 8+ overall" in prompt

    def test_harness_routes_brief_executive_summary_to_new_evaluator(
        self, monkeypatch,
    ):
        """The harness_narrative call site must pick
        brief_executive_summary_evaluator_prompt when agent_id ==
        'brief_executive_summary'. Other agent_ids retain the original
        peer-review evaluator (other sections aren't in this PR's
        scope -- pinned so a future caller doesn't have to re-derive
        the contract)."""
        import os
        # The test-env short-circuit returns DATA_PENDING before any
        # evaluator wiring -- bypass that branch by patching the
        # environment check the function reads.
        from tools import academic_export

        captured: dict = {}

        class _StubResult:
            response = ""

        class _StubHarness:
            def run(self, *, evaluator_prompt, **kwargs):
                captured["evaluator"] = evaluator_prompt
                captured["agent_id"] = kwargs.get("agent_id")
                return _StubResult()

        monkeypatch.setattr(academic_export, "ENVIRONMENT", "production")
        monkeypatch.setattr(
            "agents.harness.GeneratorEvaluatorHarness",
            lambda: _StubHarness())

        # Defang every downstream import the function performs so the
        # call reaches the harness wiring without hitting an LLM.
        import sys
        from unittest.mock import MagicMock
        for mod in ("agents.academic_writer", "agents.base",
                    "tools.chart_vision", "tools.strategy_context"):
            sys.modules.setdefault(mod, MagicMock())
        monkeypatch.setattr(
            "agents.base.call_claude", lambda *a, **k: "",
            raising=False)
        # Patch chart-vision so snapshots_dir_exists short-circuits.
        sys.modules["tools.chart_vision"].snapshots_dir_exists = (
            lambda: False)

        # Trigger with the executive summary agent_id; the new
        # evaluator must land in the harness.run call.
        academic_export.harness_narrative(
            "brief_executive_summary", task="x", context={})
        from agents.evaluator_prompts import (
            brief_executive_summary_evaluator_prompt,
        )
        assert captured["agent_id"] == "brief_executive_summary"
        assert captured["evaluator"] == \
            brief_executive_summary_evaluator_prompt()

        # June 21 2026 -- the five remaining brief sections
        # (methodology, key_findings, limitations,
        # final_recommendations, visuals) each got their own
        # section-specific evaluator that scores against the
        # criteria the section was actually written to satisfy.
        # The peer-review evaluator is no longer used for any
        # brief section.
        academic_export.harness_narrative(
            "brief_methodology", task="x", context={})
        from agents.evaluator_prompts import (
            brief_section_evaluator_prompt,
        )
        assert captured["agent_id"] == "brief_methodology"
        assert captured["evaluator"] == \
            brief_section_evaluator_prompt("methodology")

        # A non-brief agent_id (e.g. the council/peer path) still
        # falls through to the peer-review evaluator -- the brief
        # section table is opt-in by agent_id prefix.
        academic_export.harness_narrative(
            "midpoint_section_x", task="x", context={})
        from agents.evaluator_prompts import (
            academic_review_peer_evaluator_prompt,
        )
        assert captured["agent_id"] == "midpoint_section_x"
        assert captured["evaluator"] == \
            academic_review_peer_evaluator_prompt("academic writer")


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

    def test_truncation_logs_specific_diagnostic(self, monkeypatch):
        """June 21 2026 -- a truncated response (opens with '{', never
        gets a closing brace) must log story_plan_*_truncated with a
        'consider raising max_tokens' hint, not the generic _no_object.
        The hint is the load-bearing signal for the operator -- without
        it the cryptic 'Expecting , delimiter' error from json.loads
        sends the operator chasing a non-existent JSON-shape bug.

        We patch the module-level log.warning directly to capture the
        event name + structured kwargs -- more reliable than depending
        on structlog's output routing, which varies based on test
        ordering and config side-effects from other imports."""
        from tools import story_plan as sp
        calls: list[tuple[str, dict]] = []

        def _capture(event, **kwargs):
            calls.append((event, kwargs))

        monkeypatch.setattr(sp.log, "warning", _capture)
        # An object that began but never closed -- the canonical
        # truncation shape an LLM emits when it hits max_tokens.
        raw = '{"central_argument": "x", "slide_plan": [{"slide_number": 1'
        out = sp._parse_plan_json(raw, log_key="story_plan_deck_pass1")
        assert out is None
        events = [c[0] for c in calls]
        assert "story_plan_deck_pass1_truncated" in events, (
            f"expected truncation diagnostic; got events: {events}")
        # The hint kwarg surfaces the operator-actionable message.
        truncated = next(c for c in calls
                         if c[0] == "story_plan_deck_pass1_truncated")
        assert "max_tokens" in truncated[1].get("hint", "")

    def test_long_parse_failure_hints_at_max_tokens(self, monkeypatch):
        """A long raw_length on a parse failure also surfaces the
        max_tokens hint -- the case where the body contains stray
        braces that match (so find/rfind succeed) but json.loads
        still fails because an unclosed string ran past the ceiling."""
        from tools import story_plan as sp
        calls: list[tuple[str, dict]] = []

        def _capture(event, **kwargs):
            calls.append((event, kwargs))

        monkeypatch.setattr(sp.log, "warning", _capture)
        # 5000+ chars of content that does NOT parse as JSON. Picking
        # a length above the 4500 threshold the parser uses to
        # distinguish 'malformed' from 'truncated'.
        raw = '{"a": "' + ("x" * 5000) + ',"b": "}'  # unclosed string + stray }
        sp._parse_plan_json(raw, log_key="story_plan_deck_pass1")
        events = [c[0] for c in calls]
        assert "story_plan_deck_pass1_parse_failed" in events, (
            f"expected parse_failed diagnostic; got events: {events}")
        # The hint must mention max_tokens / truncation when the body
        # is long; for shorter bodies it would say 'JSON malformed'.
        parse_failed = next(c for c in calls
                            if c[0] == "story_plan_deck_pass1_parse_failed")
        assert "max_tokens" in parse_failed[1].get("hint", "")


class TestDeckPass1aMaxTokens:
    """Pin that deck Pass 1a's max_tokens budget is appropriate for
    the LEAN slide_plan schema (no speaker_notes -- those moved to
    Pass 1b). June 21 2026 (second iteration). Pass 1 used to emit
    speaker_notes inline and hit 8000 tokens reliably; the split
    means Pass 1a only carries the slide_plan structure (headlines +
    anchors + bullets + transitions) which fits comfortably in 4000
    tokens for 11 slides. This test prevents a regression that
    bumps Pass 1a back up to the old 8000 ceiling -- if 4000 isn't
    enough, the right answer is to make the lean schema leaner, not
    to bloat the budget back."""

    def test_deck_pass1a_max_tokens_is_lean(self, monkeypatch):
        """The deck Pass 1a harness call must request <= 4000
        tokens. Pass 1b (speaker_notes) gets a separate 5000-token
        budget and a focused prose schema."""
        from tools import story_plan as sp

        captured: dict = {}

        def _capture(**kwargs):
            captured["max_tokens"] = kwargs.get("max_tokens")
            # Raise so the function returns the deterministic
            # fallback without continuing into Pass 1b/2/3/4 --
            # we only care about the kwarg that was passed.
            raise RuntimeError("captured")

        monkeypatch.setattr(
            sp, "_run_pass1_with_harness", _capture)
        sp.generate_deck_story_plan(
            deck_context={"validated_constants": {}},
            slide_titles=["A", "B"])
        assert captured["max_tokens"] <= 4000, (
            f"deck Pass 1a max_tokens drifted to "
            f"{captured['max_tokens']} -- the lean schema "
            "(no speaker_notes) should fit comfortably in 4000 "
            "tokens for 11 slides; a budget above this signals "
            "scope creep back into Pass 1a's schema")


# ── Pass 1b: speaker_notes split (June 21 2026) ──────────────────────────


class TestDeckPass1aSchemaIsLean:
    """The Pass 1a JSON schema constant must NOT instruct the model
    to emit speaker_notes. That's the entire point of the split --
    Pass 1a fits in 4000 tokens because speaker_notes (3000-4000
    tokens by themselves across 11 slides) live in Pass 1b."""

    def test_pass1a_schema_omits_speaker_notes(self):
        from tools.story_plan import _DECK_STORY_PLAN_BODY
        # The schema sample inside the prompt is the contract;
        # speaker_notes as a JSON key in the schema would tell the
        # model to emit it inline (defeating the split).
        body = _DECK_STORY_PLAN_BODY
        # The schema block is the section inside the JSON braces.
        # We assert speaker_notes isn't a *schema key* there -- the
        # word may still appear in narrative explanation (and
        # indeed the comment explains the split).
        schema_block = body.split("{")[1].split("}")[0]
        assert "speaker_notes" not in schema_block, (
            "Pass 1a schema still asks the model for speaker_notes "
            "inline -- the June 21 2026 split moved this to Pass 1b")


class TestDeckPass1bExists:

    def test_pass1b_speaker_notes_helper_is_defined(self):
        from tools.story_plan import _generate_deck_speaker_notes
        assert callable(_generate_deck_speaker_notes)

    def test_pass1b_system_prompt_includes_live_demo_sequence(self):
        from tools.story_plan import (
            LIVE_DEMO_SEQUENCE, _DECK_SPEAKER_NOTES_SYSTEM_PROMPT,
        )
        assert LIVE_DEMO_SEQUENCE in _DECK_SPEAKER_NOTES_SYSTEM_PROMPT
        # The constant itself must structure the 3.5-minute demo.
        assert "analyticsdesk.app" in LIVE_DEMO_SEQUENCE
        assert "dissenting view" in LIVE_DEMO_SEQUENCE.lower()


class TestDeckPass1bFailOpen:
    """Pass 1b failure must NOT block the deck. The slide_plan
    remains intact; speaker_notes default to empty strings; the
    per-slide Sonnet writer downstream still produces serviceable
    notes from context."""

    def test_empty_slide_plan_returns_empty_notes(self):
        from tools.story_plan import _generate_deck_speaker_notes
        out = _generate_deck_speaker_notes(
            slide_plan=[], central_argument="x",
            deck_context={}, duration_minutes=19)
        assert out == {"speaker_notes": {}}

    def test_pass1b_call_failure_returns_empty_notes(self, monkeypatch):
        """Any exception from the underlying Opus call surfaces as
        {"speaker_notes": {}} -- the caller then merges what's
        returned, defaulting any missing slide to an empty string."""
        from tools import story_plan as sp

        def _raise(*args, **kwargs):
            raise RuntimeError("opus down")

        monkeypatch.setattr(
            sp, "_parse_plan_json", lambda *a, **k: {"speaker_notes": {}})
        # Patch the agents.base import that _generate_deck_speaker_
        # notes makes locally.
        import agents.base as _ab
        monkeypatch.setattr(_ab, "call_claude", _raise)
        out = sp._generate_deck_speaker_notes(
            slide_plan=[{"slide_number": 1}],
            central_argument="x",
            deck_context={},
            duration_minutes=19)
        assert out == {"speaker_notes": {}}

    def test_pass1b_merge_defaults_missing_slides_to_empty_string(
        self, monkeypatch,
    ):
        """When Pass 1b returns notes for only some slides, the
        merge step in generate_deck_story_plan must default any
        slide without notes to an empty string (not None, not
        missing)."""
        from tools import story_plan as sp

        # Stub Pass 1a -- return a 3-slide lean plan, no speaker_notes.
        def _stub_pass1a(**kwargs):
            return (
                '{"central_argument": "x", "presentation_arc": "y", '
                '"slide_plan": [{"slide_number": 1, "title": "a"}, '
                '{"slide_number": 2, "title": "b"}, '
                '{"slide_number": 3, "title": "c"}]}',
                10.0, 1)

        # Stub Pass 1b -- return notes for slide 1 and 3 ONLY (slide
        # 2 is missing on purpose, to exercise the default).
        def _stub_pass1b(**kwargs):
            return {"speaker_notes": {"1": "notes one", "3": "notes three"}}

        # Stub Grok + Gemini to no-ops so the test stays
        # deterministic + offline.
        monkeypatch.setattr(
            sp, "_run_pass1_with_harness", _stub_pass1a)
        monkeypatch.setattr(
            sp, "_generate_deck_speaker_notes", _stub_pass1b)
        monkeypatch.setattr(
            sp, "_generate_anticipated_questions", lambda _s: [])
        monkeypatch.setattr(
            sp, "_generate_blind_spots", lambda _s: {})

        plan = sp.generate_deck_story_plan(
            deck_context={"validated_constants": {}},
            slide_titles=["A", "B", "C"])
        slides_by_num = {
            s["slide_number"]: s for s in plan["slide_plan"]}
        assert slides_by_num[1]["speaker_notes"] == "notes one"
        # Slide 2 was missing from Pass 1b output -- must default
        # to "" rather than crash or carry None.
        assert slides_by_num[2]["speaker_notes"] == ""
        assert slides_by_num[3]["speaker_notes"] == "notes three"

    def test_pass1b_exception_during_merge_leaves_empty_strings(
        self, monkeypatch,
    ):
        """If _generate_deck_speaker_notes itself raises, the
        caller's try/except must default every slide's
        speaker_notes to empty string (not leave them missing)."""
        from tools import story_plan as sp

        def _stub_pass1a(**kwargs):
            return (
                '{"central_argument": "x", "presentation_arc": "y", '
                '"slide_plan": [{"slide_number": 1, "title": "a"}, '
                '{"slide_number": 2, "title": "b"}]}',
                10.0, 1)

        def _raise(*a, **k):
            raise RuntimeError("pass 1b explosion")

        monkeypatch.setattr(
            sp, "_run_pass1_with_harness", _stub_pass1a)
        monkeypatch.setattr(
            sp, "_generate_deck_speaker_notes", _raise)
        monkeypatch.setattr(
            sp, "_generate_anticipated_questions", lambda _s: [])
        monkeypatch.setattr(
            sp, "_generate_blind_spots", lambda _s: {})

        plan = sp.generate_deck_story_plan(
            deck_context={"validated_constants": {}},
            slide_titles=["A", "B"])
        for slide in plan["slide_plan"]:
            assert slide["speaker_notes"] == ""


class TestEvaluatorPromptNotesPass1bSplit:
    """The evaluator prompt scores Pass 1a output -- which does NOT
    contain speaker_notes. The prompt must explicitly note this so
    a runaway evaluator doesn't penalise the absence."""

    def test_evaluator_prompt_documents_speaker_notes_separation(self):
        from tools.story_plan import STORY_PLAN_EVALUATOR_PROMPT
        # The text doesn't need a specific phrasing; we pin that
        # the prompt acknowledges speaker_notes are generated
        # elsewhere so the rubric doesn't deduct for their
        # absence in the Pass 1a output it's scoring.
        lower = STORY_PLAN_EVALUATOR_PROMPT.lower()
        assert "speaker_notes" in lower or "speaker notes" in lower
        assert "pass 1b" in lower or "separate pass" in lower


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
