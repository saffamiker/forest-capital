"""tests/test_draft_80_docx_export_fixes.py -- June 28 2026.

Source + behaviour pins for the 6 draft-80 DOCX export fixes.
"""
from __future__ import annotations

import inspect
import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── Issue 1: editor-export substitution_table kwargs ──────


class TestEditorExportSubstitutionTableComplete:

    def test_call_includes_full_generation_kwargs(self):
        """The editor-export path at main.py:14150 must invoke
        get_substitution_table with the SAME kwarg set the
        generation-time call uses, otherwise tokens like
        {{STUDY_START}}, {{CURRENT_REGIME}}, {{SENSITIVITY_
        COST_BPS_*}} won't resolve at editor-export time."""
        import main as _main
        src = inspect.getsource(_main)
        # Find the editor-export call -- it's the one inside a
        # function that mentions 'editor_export_substitution_'.
        ee_idx = src.find(
            "editor_export_substitution_table_failed")
        assert ee_idx > -1
        # Walk backwards ~3000 chars to capture the build call.
        slice_ = src[max(0, ee_idx - 4000):ee_idx + 500]
        # Required kwargs the prior call was missing:
        for required in [
            "study_months=",
            "implied_allocation=",
            "live_signals=",
            "oos_window_pct_of_study=",
            "hash_verified=True",
        ]:
            assert required in slice_, (
                f"editor-export get_substitution_table call "
                f"missing kwarg: {required}")

    def test_freeze_aware_hash_used(self):
        """Editor-export must use get_effective_data_hash to
        respect the submission freeze (the same wiring the
        generation path uses)."""
        import main as _main
        # The import is aliased as _ee_eff_hash. Search the
        # full source for the import (broader scope than the
        # ee_substitution slice -- the import lives near the
        # endpoint top).
        with open(
                _main.__file__, encoding="utf-8") as f:
            src = f.read()
        assert (
            "get_effective_data_hash as _ee_eff_hash" in src), (
            "editor-export must import freeze-aware "
            "get_effective_data_hash for the substitution "
            "table build")


# ── Issue 2 + 3: walker normalisations in build_editor_docx ─


class TestEditorDocxWalkerNormalisations:

    def test_section_restate_skip_in_walker(self):
        """Source-pin: build_editor_docx walker calls
        _is_section_restate gate so duplicate Section N: lines
        are skipped at render time."""
        from tools.academic_docx import build_editor_docx
        src = inspect.getsource(build_editor_docx)
        assert "_is_section_restate(" in src
        assert "_SECTION_RESTATE_RE" in src

    def test_references_block_consolidation_in_walker(self):
        """Source-pin: build_editor_docx accumulates citations
        from inline References blocks + emits a consolidated
        APA References section at end."""
        from tools.academic_docx import build_editor_docx
        src = inspect.getsource(build_editor_docx)
        assert "consolidated_refs" in src
        assert "in_refs_block" in src
        assert "_sort_apa_citations(" in src
        assert "_is_references_heading(" in src

    def test_walker_strips_bold_wrap_from_md_headings(self):
        """Source-pin: the markdown-heading-in-paragraph path
        applies _HEADING_BOLD_WRAP_RE so '## **Heading**'
        renders as 'Heading' (Heading style template bolds)."""
        from tools.academic_docx import build_editor_docx
        src = inspect.getsource(build_editor_docx)
        assert "_HEADING_BOLD_WRAP_RE" in src


# ── Issue 4: lowercase placeholder hygiene in prompt ────────


class TestExecutiveSummaryPromptDropsLowercasePlaceholders:

    def test_prompt_warns_against_lowercase_play_by_play(self):
        import main as _main
        with open(
                _main.__file__, encoding="utf-8") as f:
            src = f.read()
        # Source-pin: the brief_executive_summary task must
        # contain the token-hygiene warning that explicitly
        # names the two lowercase placeholders.
        assert "play_by_play_events" in src
        assert "play_by_play_add_value" in src
        assert "TOKEN HYGIENE" in src


# ── Issue 5: bare 0.005 stat threshold exemption ────────────


class TestBare0005StatThresholdExemption:

    def test_bare_0005_exempt_without_operator(self):
        from tools.untoken_numeric_check import (
            find_untoken_backed_numerics,
        )
        text = (
            "The 0.005 threshold under Benjamini-Hochberg is "
            "the academic standard.")
        viols = find_untoken_backed_numerics(text, {})
        # Bare 0.005 (no preceding operator) must NOT flag.
        assert all(
            v.raw_value != "0.005" for v in viols), (
            "bare 0.005 leaked: " +
            str([v.raw_value for v in viols]))

    def test_operator_prefixed_still_exempt(self):
        from tools.untoken_numeric_check import (
            find_untoken_backed_numerics,
        )
        text = "Significant at p < 0.005."
        viols = find_untoken_backed_numerics(text, {})
        assert viols == []

    def test_sub_table_priority_preserved_for_non_overridden_values(
            self):
        """June 28 2026 -- 0.005 is now in
        _ALWAYS_EXEMPT_BARE_VALUES (Issue A override). For
        every value NOT in the override set the sub-table-
        priority constraint still holds: a value in the table
        flags as token_available so the LLM gets the swap
        suggestion. Test uses a non-overridden value (0.86)."""
        from tools.untoken_numeric_check import (
            find_untoken_backed_numerics,
        )
        text = "The 0.86 figure is the headline Sharpe."
        viols = find_untoken_backed_numerics(
            text,
            substitution_table={
                "{{OOS_SHARPE_BLEND}}": "0.86"})
        assert len(viols) >= 1
        assert any(
            v.suggested_token == "{{OOS_SHARPE_BLEND}}"
            for v in viols)


# ── Issue A: always-exempt bare 0.005 ─────────────────────


class TestAlwaysExemptBare0005Override:
    """0.005 IS in the substitution table as
    {{BH_SIGNIFICANCE_THRESHOLD}}. The first occurrence in any
    section gets substituted correctly. Correction-pass retries
    regenerate fresh prose with bare 0.005 (paraphrased without
    the operator) and Sonnet stubbornly refuses to swap. The
    override fires BEFORE the sub-table-priority gate so the
    bare form is allowed through this specific failure mode.
    """

    def test_set_exists_and_includes_0005(self):
        from tools.untoken_numeric_check import (
            _ALWAYS_EXEMPT_BARE_VALUES,
        )
        assert "0.005" in _ALWAYS_EXEMPT_BARE_VALUES

    def test_bare_0005_passes_when_in_sub_table(self):
        """The load-bearing semantic: bare 0.005 with
        {{BH_SIGNIFICANCE_THRESHOLD}} in the table must NOT
        flag (operator-blessed override of sub-table-priority
        for this specific value)."""
        from tools.untoken_numeric_check import (
            find_untoken_backed_numerics,
        )
        text = "Sonnet rewrote: 'the 0.005 threshold for BH-FDR.'"
        viols = find_untoken_backed_numerics(
            text,
            substitution_table={
                "{{BH_SIGNIFICANCE_THRESHOLD}}": "0.005"})
        assert all(
            v.raw_value != "0.005" for v in viols), (
            f"0.005 leaked despite always-exempt override: "
            f"{[v.raw_value for v in viols]}")

    def test_other_values_still_respect_sub_table_priority(
            self):
        """Sub-table-priority preserved for every value NOT in
        _ALWAYS_EXEMPT_BARE_VALUES -- the override is targeted,
        not a general gate weakening."""
        from tools.untoken_numeric_check import (
            find_untoken_backed_numerics,
        )
        text = "The Sharpe is 0.86 over the period."
        viols = find_untoken_backed_numerics(
            text,
            substitution_table={
                "{{OOS_SHARPE_BLEND}}": "0.86"})
        # 0.86 NOT in the override set -- still flags as
        # token_available.
        assert any(
            v.raw_value == "0.86"
            and v.severity == "token_available"
            for v in viols)


# ── Issue B: brief_key_findings correlation token guidance ─


class TestBriefKeyFindingsCorrelationTokenGuidance:

    def test_prompt_directs_to_use_correlation_tokens(self):
        import main as _main
        with open(
                _main.__file__, encoding="utf-8") as f:
            src = f.read()
        kf_idx = src.find('"brief_key_findings"')
        assert kf_idx > -1
        slice_ = src[kf_idx:kf_idx + 6000]
        # Required tokens for the correlation regime
        # description.
        assert "{{PRE_2022_EQ_IG_CORR}}" in slice_
        assert "{{POST_2022_EQ_IG_CORR}}" in slice_
        # And an explicit guard against the bare +0.5 form.
        assert "+0.5" in slice_, (
            "prompt should explicitly call out the +0.5 "
            "approximation as forbidden")
        # CORRELATION REGIME framing.
        assert "CORRELATION REGIME" in slice_


# ── Soft-fail on hard-lock cap (NEW) ──────────────────────


class TestHardLockSoftFail:
    """Operator-directed change for the June 30 deadline:
    [DATA PENDING] in any section is a hard submission blocker;
    a flagged numeric is recoverable via human review of the
    audit_warnings banner. The hard-lock 3-pass cap now soft-
    fails -- logs a warning + persists the best-attempt
    narrative instead of raising UntokenNumericLockError.
    """

    def test_harness_does_not_raise_on_lock_cap(self):
        """Source-pin: the hard-lock cap branch must NOT call
        the UntokenNumericLockError constructor. The class
        name may still appear in comments / imports describing
        the prior behaviour; the load-bearing semantic is the
        absence of the constructor call."""
        import inspect
        from tools.academic_export import harness_narrative
        src = inspect.getsource(harness_narrative)
        # The constructor call form is
        # `raise UntokenNumericLockError(` -- distinguishes
        # the actual raise statement from textual mentions in
        # docstrings / inline comments.
        assert (
            "raise UntokenNumericLockError("
            not in src), (
            "soft-fail violated: the cap branch raises "
            "UntokenNumericLockError instead of breaking "
            "out with best-attempt prose")

    def test_soft_fail_log_event_present(self):
        """Source-pin: the cap branch logs untoken_lock_soft_fail
        with the remaining-violations count + sample offenders
        so the operator can grep Render logs for affected
        sections."""
        import inspect
        from tools.academic_export import harness_narrative
        src = inspect.getsource(harness_narrative)
        assert "untoken_lock_soft_fail" in src
        assert "remaining_violations" in src
        assert "sample_offenders" in src


# ── Issue 6: brief_final_recommendations token guidance ────


class TestBriefFinalRecommendationsTokenGuidance:

    def test_prompt_directs_to_use_current_regime_tokens(self):
        import main as _main
        with open(
                _main.__file__, encoding="utf-8") as f:
            src = f.read()
        # Source-pin: the brief_final_recommendations task must
        # contain MANDATORY guidance to use the live tokens
        # rather than hardcoding raw values.
        # Find the brief_final_recommendations task block.
        fr_idx = src.find('"brief_final_recommendations"')
        assert fr_idx > -1
        # Walk ~5000 chars forward to capture the full task.
        slice_ = src[fr_idx:fr_idx + 6000]
        for required in [
            "{{CURRENT_REGIME}}",
            "{{REGIME_CONFIDENCE}}",
            "{{CURRENT_EQUITY_PCT}}",
        ]:
            assert required in slice_, (
                f"brief_final_recommendations task missing "
                f"token reference: {required}")
        # And the MANDATORY-language framing.
        assert "MANDATORY" in slice_
