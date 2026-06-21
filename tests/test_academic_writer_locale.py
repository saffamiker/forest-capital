"""tests/test_academic_writer_locale.py — American English pin.

May 23 2026 bug report: the Academic Writer agent was producing
drafts in British English ("initialisation", "optimisation",
"minimise", "favour", "behaviour"). The team wants American
English (en-US) throughout — the project audience is U.S.
portfolio managers and faculty.

The fix added an explicit LANGUAGE LOCALE block to the system
prompt with a paired list of common British → American word
forms. This test pins both contracts:

  1. The locale block is present in the prompt — a regression
     that drops it would re-allow the British spellings.
  2. The prompt itself contains NO British-spelling words
     (since the writer mirrors the prompt's own style — the
     model picks up locale cues from its own instructions).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")


from agents import academic_writer as aw  # noqa: E402


class TestAmericanEnglishLocale:
    def test_locale_block_is_present(self):
        # The block has a clearly-marked header so the model sees
        # the locale instruction near the top of the prompt.
        assert "AMERICAN ENGLISH" in aw._SYSTEM_PROMPT
        assert "en-US" in aw._SYSTEM_PROMPT

    def test_pairs_include_the_user_named_words(self):
        # The user explicitly named these as British forms that
        # were leaking into output. The locale block must name
        # each one's American replacement so the model has a
        # clear directive.
        for british, american in [
            ("initialisation", "initialization"),
            ("optimisation",  "optimization"),
            ("minimise",      "minimize"),
            ("favour",        "favor"),
            ("behaviour",     "behavior"),
        ]:
            assert british in aw._SYSTEM_PROMPT, (
                f"Locale block must name '{british}' explicitly so "
                f"the model knows the form to avoid.")
            assert american in aw._SYSTEM_PROMPT, (
                f"Locale block must name '{american}' as the "
                f"required form.")

    def test_prompt_itself_uses_american_spelling(self):
        # The prompt is the model's primary style cue. If the
        # prompt uses British spellings ANYWHERE, the model
        # mirrors them in its output. The locale block sits
        # near the top so the model sees the directive; but the
        # rest of the prompt must match — no "specialising" /
        # "generalisations" / "optimisation" elsewhere.
        #
        # The paired list inside the locale block deliberately
        # names each British form as a thing to AVOID, so we
        # check the prompt body OUTSIDE that block. The block
        # opens with "AMERICAN ENGLISH" and ends at the next
        # ALL-CAPS section header.
        prompt = aw._SYSTEM_PROMPT
        locale_start = prompt.index("LANGUAGE LOCALE")
        # The locale block runs to "PUNCTUATION AND STRUCTURE:".
        locale_end = prompt.index(
            "PUNCTUATION AND STRUCTURE:", locale_start)
        body = prompt[:locale_start] + prompt[locale_end:]
        # These are the specific British forms that were leaking
        # into output OR appearing in the prompt body. A
        # regression that re-introduces any of them is a real
        # locale drift.
        for british in [
            "specialising", "specialisation",
            "optimising", "optimisation",
            "generalisations", "minimising",
            "behaviours", "favoured",
        ]:
            assert british not in body, (
                f"Prompt body must use American English — found "
                f"British '{british}' outside the locale block. "
                f"The model mirrors the prompt's own spelling, "
                f"so any British form here will leak into output.")


class TestStyleRuleset:
    """May 23 2026 — the user named a complete style ruleset for
    the academic writer prompt. These tests pin each clause so a
    future prompt edit can't quietly drop one of them."""

    def test_em_dashes_are_prohibited(self):
        assert "NO em dashes" in aw._SYSTEM_PROMPT \
            or "No em dashes" in aw._SYSTEM_PROMPT

    def test_thirty_five_word_sentence_cap(self):
        assert "35 words" in aw._SYSTEM_PROMPT

    def test_ai_affectations_are_named_and_prohibited(self):
        # The named phrases must appear in the PROHIBITED list so
        # the model knows specifically what to avoid.
        prompt = aw._SYSTEM_PROMPT
        for phrase in [
            "it is worth noting",
            "it is important to highlight",
            "notably",
            "crucially",
            "importantly",
            "needless to say",
            "as mentioned above",
            "in this context",
            "in summary",
            "to summarize",
            "it is clear that",
            "While it is true that",
            "It should be noted that",
        ]:
            assert phrase in prompt, (
                f"AI affectation '{phrase}' must be explicitly named "
                f"in the prompt's prohibited list.")

    def test_redundant_intensifiers_are_prohibited(self):
        prompt = aw._SYSTEM_PROMPT
        # The list must name each intensifier; a regression that
        # collapses the list to "etc." would let them through.
        for intensifier in ["very", "quite", "rather",
                            "somewhat", "fairly"]:
            assert f'"{intensifier}"' in prompt, (
                f"Redundant intensifier '{intensifier}' must be "
                f"named so the model knows to avoid it.")

    def test_voice_rules_present(self):
        prompt = aw._SYSTEM_PROMPT
        assert "Active voice preferred" in prompt
        assert "Third person throughout" in prompt
        # The third-person rule must explicitly call out the
        # "we find" / "our results show" anti-pattern.
        assert "we find" in prompt
        assert "our results show" in prompt

    def test_nominalization_anti_pattern_named(self):
        prompt = aw._SYSTEM_PROMPT
        assert "conduct an analysis of" in prompt
        assert "make a determination" in prompt

    def test_number_register_rules(self):
        prompt = aw._SYSTEM_PROMPT
        # Numbers <10 spelled out; 10+ as numerals.
        assert "Numbers below 10" in prompt \
            or "below 10 are spelled out" in prompt
        # Percentages use % symbol.
        assert "Percentages always use numerals" in prompt \
            or "% symbol" in prompt

    def test_figure_and_table_reference_rule(self):
        prompt = aw._SYSTEM_PROMPT
        # No "the chart above" — Figures/tables by number.
        assert "Figure 1" in prompt or "by their number" in prompt
        assert "the chart above" in prompt  # named as anti-pattern

    def test_citation_placement_rule(self):
        prompt = aw._SYSTEM_PROMPT
        assert "placed at the END of the claim" in prompt \
            or "end of the claim" in prompt.lower()

    def test_rules_apply_to_bob_blocks(self):
        # The user explicitly asked that the ruleset apply to
        # [BOB] pre-populated blocks too, not just the prose.
        prompt = aw._SYSTEM_PROMPT
        assert "BOB" in prompt
        assert "pre-populated" in prompt.lower() \
            or "pre populated" in prompt.lower()


class TestWebSearchDisabled:
    """June 21 2026 -- web search removed from the brief / deck /
    appendix narrative writer. The writer used to web-search per
    section (Anthropic's server-side web_search, max_uses=3),
    bloating input context and burning output budget on URL/DOI
    formatting -- which fired
    section_content_truncated_unrecoverable on Sections 3 + 6.
    The platform's curated registry at data/references.json
    already carries every academic citation the writer
    historically searched for; the registry is now the only
    permitted source."""

    def test_system_prompt_disables_web_search(self):
        # The CITATIONS block must explicitly say DO NOT web-search.
        prompt = aw._SYSTEM_PROMPT
        assert "DO NOT web" in prompt
        # And the old "search for and include at least one supporting
        # academic citation" instruction must be gone.
        assert "search for and include at least one" not in prompt

    def test_absolute_prohibitions_no_longer_mention_web_search(self):
        # The ABSOLUTE PROHIBITIONS clause used to read "Never cite a
        # source unless it is in the provided references list OR you
        # have verified it via the web_search tool". The "OR you have
        # verified it via the web_search tool" escape hatch is gone.
        prompt = aw._SYSTEM_PROMPT
        assert "verified it via the web_search tool" not in prompt
        # The replacement must say web search is disabled.
        assert "Web search is disabled" in prompt

    def test_citations_block_lists_registry_keys(self):
        # The new CITATIONS block must enumerate at least the seven
        # core hardcoded papers the project depends on so the writer
        # knows what's available without inspecting references.json.
        prompt = aw._SYSTEM_PROMPT
        assert "Hamilton (1989)" in prompt
        assert "Carhart (1997)" in prompt
        assert "Markowitz (1952)" in prompt
        assert "Harvey, Liu, and Zhu (2016)" in prompt

    def test_harness_narrative_does_not_pass_web_search_tool(self):
        # The Sonnet call inside harness_narrative must NOT pass
        # tools=[WEB_SEARCH_TOOL]. Source inspection is sufficient:
        # the only path is the _call_sonnet closure inside
        # harness_narrative, and any future regression that re-adds
        # the tool would show up as a literal token in the source.
        # The removal comment is allowed to reference the symbol so
        # the next reader can grep their way back.
        import inspect
        from tools import academic_export
        src = inspect.getsource(academic_export.harness_narrative)
        assert "tools=[WEB_SEARCH_TOOL]" not in src
        # WEB_SEARCH_TOOL may appear in a comment documenting the
        # removal -- pin only that the ACTIVE wiring is gone.
        active_tokens = [
            line for line in src.splitlines()
            if "WEB_SEARCH_TOOL" in line and not line.strip().startswith("#")
        ]
        assert not active_tokens, (
            f"Unexpected active references to WEB_SEARCH_TOOL: "
            f"{active_tokens}")
