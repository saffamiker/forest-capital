"""tests/test_brief_gen_consolidated_fixes.py -- June 28 2026.

Pins for the 10 consolidated brief-gen fixes. Each fix gets a
focused source-pin or behavior-pin.
"""
from __future__ import annotations

import inspect
import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── Fix 4 + 5a: new tokens ──────────────────────────────────


class TestNewBriefTokensInTable:

    def test_sensitivity_cost_tokens_present(self):
        from tools.numeric_substitution import (
            get_substitution_table,
        )
        t = get_substitution_table(
            "test_sensitivity_001", {}, None, hash_verified=True)
        assert t.get("{{SENSITIVITY_COST_BPS_LOW}}") == "10"
        assert t.get("{{SENSITIVITY_COST_BPS_MID}}") == "15"
        assert t.get("{{SENSITIVITY_COST_BPS_HIGH}}") == "20"

    def test_bh_significance_threshold_present(self):
        from tools.numeric_substitution import (
            get_substitution_table,
        )
        t = get_substitution_table(
            "test_bh_002", {}, None, hash_verified=True)
        assert (
            t.get("{{BH_SIGNIFICANCE_THRESHOLD}}") == "0.005")

    def test_new_tokens_have_catalog_entries(self):
        from tools.data_reference_catalog import CATALOG
        tokens = set()
        for _ck, _cl, entries in CATALOG:
            for e in entries:
                tokens.add(e.token)
        for required in [
            "{{SENSITIVITY_COST_BPS_LOW}}",
            "{{SENSITIVITY_COST_BPS_MID}}",
            "{{SENSITIVITY_COST_BPS_HIGH}}",
            "{{BH_SIGNIFICANCE_THRESHOLD}}",
        ]:
            assert required in tokens, (
                f"{required} missing from catalog")


# ── Fix 4b + 5b: brief prompts reference the new tokens ────


class TestBriefPromptUsesNewTokens:

    def test_limitations_prompt_references_sensitivity_tokens(
            self):
        with open(
                "backend/main.py", encoding="utf-8") as f:
            src = f.read()
        assert "{{SENSITIVITY_COST_BPS_LOW}}" in src
        assert "{{SENSITIVITY_COST_BPS_MID}}" in src
        assert "{{SENSITIVITY_COST_BPS_HIGH}}" in src

    def test_methodology_uses_bh_threshold_token(self):
        with open(
                "backend/main.py", encoding="utf-8") as f:
            src = f.read()
        # Source-pin: the BH significance token appears (at
        # least 2 references for the brief's two prompts that
        # used the raw 0.005 literal). Python string-literal
        # continuation may split the surrounding "q < " /
        # "p < " across lines so a strict substring match
        # would fail; count token references instead.
        assert src.count("{{BH_SIGNIFICANCE_THRESHOLD}}") >= 2


# ── Fix 6 + 9: story-plan check consults structural exemptions ─


class TestStoryPlanCheckHonoursStructuralExemptions:

    def test_function_accepts_substitution_table_kwarg(self):
        from tools.academic_export import (
            _count_unauthorized_numbers,
        )
        sig = inspect.signature(_count_unauthorized_numbers)
        assert "substitution_table" in sig.parameters

    def test_ordinal_label_not_flagged(self):
        from tools.academic_export import (
            _count_unauthorized_numbers,
        )
        text = "See Section 2 of the brief and Figure 3 below."
        bad = _count_unauthorized_numbers(text, [0.86])
        # "2" and "3" are ordinal labels -- structurally exempt.
        assert bad == [], (
            f"ordinals leaked through: {bad}")

    def test_sp_500_not_flagged(self):
        from tools.academic_export import (
            _count_unauthorized_numbers,
        )
        text = "The S&P 500 benchmark returned."
        bad = _count_unauthorized_numbers(text, [0.86])
        assert bad == []

    def test_citation_year_not_flagged(self):
        from tools.academic_export import (
            _count_unauthorized_numbers,
        )
        text = "Markowitz (1952) introduced MPT."
        bad = _count_unauthorized_numbers(text, [0.86])
        assert bad == []

    def test_definitional_weight_not_flagged_no_table(self):
        from tools.academic_export import (
            _count_unauthorized_numbers,
        )
        text = "The Classic strategy holds 60% equity."
        bad = _count_unauthorized_numbers(text, [0.86])
        # Without substitution_table, 60 is structurally
        # exempt as definitional_weight.
        assert bad == []

    def test_substitution_table_priority_preserved(self):
        """Sub-table-priority: when 60% IS in the table,
        it MUST flag (LLM gets swap suggestion). The
        structural exemption does NOT short-circuit the
        sub-table check."""
        from tools.academic_export import (
            _count_unauthorized_numbers,
        )
        text = "The Classic strategy holds 60% equity."
        bad = _count_unauthorized_numbers(
            text, [0.86],
            substitution_table={
                "{{CLASSIC_6040_WEIGHT_EQUITY}}": "60%"})
        # Sub-table priority -- 60 flags as available.
        assert "60%" in bad or "60" in bad, (
            f"sub-table-priority NOT preserved: {bad}")


# ── Fix 1 + 2: DOCX strips section restate + bold wrappers ─


class TestDocxSectionRestateStrip:

    def test_section_restate_re_matches_variants(self):
        from tools.academic_docx import _SECTION_RESTATE_RE
        for text in [
            "Section 1: Executive Summary",
            "Section 2: Methodology",
            "**Section 3: Key Findings**",
            "**Section 4:** Limitations",
            "section 5: final recommendations",
        ]:
            assert _SECTION_RESTATE_RE.match(text), (
                f"failed to match: {text!r}")

    def test_section_restate_re_skips_non_matches(self):
        from tools.academic_docx import _SECTION_RESTATE_RE
        for text in [
            "Executive Summary",
            "1. Executive Summary",
            "This section explores 5 strategies",
        ]:
            assert not _SECTION_RESTATE_RE.match(text), (
                f"false match on: {text!r}")

    def test_heading_bold_wrap_re_strips(self):
        from tools.academic_docx import (
            _HEADING_BOLD_WRAP_RE,
        )
        cleaned = _HEADING_BOLD_WRAP_RE.sub(
            "", "**Final Recommendations**").strip()
        assert cleaned == "Final Recommendations"


# ── Fix 8a: upgrade pass walker handles marked text ────────


class TestUpgradeWalkerHandlesMarks:

    def test_marked_text_with_token_gets_upgraded(self):
        from tools.draft_token_upgrade import (
            upgrade_content_json_to_token_values,
        )
        cj = {"type": "doc", "content": [
            {"type": "paragraph", "content": [
                {"type": "text",
                 "text": "The {{OOS_SHARPE_BLEND}} headline.",
                 "marks": [{"type": "bold"}]},
            ]},
        ]}
        manifest = {
            "0.86": {
                "token": "{{OOS_SHARPE_BLEND}}",
                "data_hash": "abc12345",
                "generated_at": "2026-06-28T20:00:00Z"},
        }
        upgraded, stats = (
            upgrade_content_json_to_token_values(cj, manifest))
        # The token MUST be upgraded even though the parent
        # text node carries bold marks.
        assert stats["nodes_upgraded"] == 1
        # The token_value node should also carry the marks
        # so the NodeView preserves the styling.
        import json
        flat = json.dumps(upgraded)
        assert '"type": "token_value"' in flat
        # marks present on the token_value (re-attached).
        assert '"marks": [{"type": "bold"}]' in flat


# ── Fix 8b: auto-upgrade hook present ──────────────────────


class TestAutoUpgradeHookWired:

    def test_helper_exists_in_main(self):
        from main import _auto_upgrade_draft_to_token_values
        assert callable(
            _auto_upgrade_draft_to_token_values)

    def test_helper_short_circuits_when_flag_off(self):
        """Source-pin: when the deferral flag is OFF, the
        helper must return immediately without DB I/O."""
        from main import _auto_upgrade_draft_to_token_values
        src = inspect.getsource(
            _auto_upgrade_draft_to_token_values)
        assert "is_defer_substitution_enabled" in src
        # The flag check happens BEFORE the SELECT.
        flag_idx = src.find(
            "await is_defer_substitution_enabled()")
        select_idx = src.find("SELECT content_json")
        assert flag_idx > -1
        assert select_idx > -1
        assert flag_idx < select_idx

    def test_brief_generator_calls_auto_upgrade(self):
        """Source-pin: the brief draft persist site invokes
        the auto-upgrade hook."""
        with open(
                "backend/main.py", encoding="utf-8") as f:
            src = f.read()
        # Auto-upgrade call right after value_manifest persist
        # for executive_brief.
        assert (
            '_auto_upgrade_draft_to_token_values(\n'
            in src) or (
            "_auto_upgrade_draft_to_token_values(" in src)
        # At least 2 call sites (brief + appendix).
        assert src.count(
            "_auto_upgrade_draft_to_token_values(") >= 2


# ── Fix 10: APA references consolidation ────────────────────


class TestReferencesConsolidation:

    def test_extract_references_splits_body_and_refs(self):
        from tools.academic_docx import _extract_references
        narrative = (
            "Body text with a finding.\n"
            "More body content.\n"
            "\n"
            "## References\n"
            "Smith, J. (2020). Article title. "
            "Journal, 42(3), 100-130.\n"
            "Jones, K. (2018). Another title. "
            "Journal, 12(1), 55-77.\n"
        )
        body, refs = _extract_references(narrative)
        assert "Body text with a finding." in body
        assert "References" not in body
        assert len(refs) == 2
        assert any("Smith" in r for r in refs)
        assert any("Jones" in r for r in refs)

    def test_extract_references_handles_bold_inline(self):
        from tools.academic_docx import _extract_references
        narrative = (
            "Body.\n"
            "\n"
            "**References**\n"
            "Anderson, M. (2015). Title. Journal, 88.\n"
        )
        body, refs = _extract_references(narrative)
        assert refs == [
            "Anderson, M. (2015). Title. Journal, 88."]
        assert "References" not in body
        assert "Anderson" not in body

    def test_sort_apa_citations_dedupes_and_sorts(self):
        from tools.academic_docx import _sort_apa_citations
        cites = [
            "Smith, J. (2020). Title A.",
            "Jones, K. (2018). Title B.",
            "Smith, J. (2020). Title A.",  # exact dup
            "  smith, j. (2020). Title A.  ",  # casing/space dup
            "Anderson, M. (2015). Title C.",
        ]
        out = _sort_apa_citations(cites)
        # Three uniques, alphabetised by first author.
        assert len(out) == 3
        assert out[0].startswith("Anderson")
        assert out[1].startswith("Jones")
        assert out[2].startswith("Smith")
