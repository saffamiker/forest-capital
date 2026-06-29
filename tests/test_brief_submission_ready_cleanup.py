"""tests/test_brief_submission_ready_cleanup.py -- June 28 2026.

Pins for the 4 submission-ready brief-gen fixes.
"""
from __future__ import annotations

import inspect
import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── Fix 1: Section 6 orphan removed from brief DOCX builder ─


class TestSection6OrphanRemoved:

    def test_brief_docx_builder_no_section_6_heading(self):
        """Source-pin: build_executive_brief no longer emits
        a '6. Visuals' heading nor reads narratives["visuals"].
        The June 26 2026 spec removed the brief_visuals agent;
        this fix removes the corresponding orphan DOCX block."""
        import inspect as _i
        from tools.academic_docx import build_executive_brief
        src = _i.getsource(build_executive_brief)
        assert "6. Visuals to Demonstrate the Insights" not in (
            src), (
            "orphan Section 6 heading still emitted by "
            "build_executive_brief")
        assert 'narratives.get("visuals"' not in src, (
            "orphan narratives['visuals'] lookup still present")

    def test_brief_docx_builder_still_ends_at_section_5(self):
        from tools.academic_docx import build_executive_brief
        src = inspect.getsource(build_executive_brief)
        # Section 5 heading still emitted.
        assert "5. Final Recommendations" in src


# ── Fix 2: APA dedup strips markdown ────────────────────────


class TestApaDedupStripsMarkdown:

    def test_italic_and_plain_collapse_to_one(self):
        from tools.academic_docx import _sort_apa_citations
        cites = [
            "Benjamin, D. J., et al. (2018). Title. Vol. 1.",
            "*Benjamin, D. J., et al. (2018). Title. Vol. 1.*",
            "Benjamini, Y., & Hochberg, Y. (1995). FDR.",
            "_Benjamini, Y., & Hochberg, Y. (1995). FDR._",
        ]
        out = _sort_apa_citations(cites)
        # Both pairs should collapse to one citation each.
        assert len(out) == 2
        # Display form has markdown stripped.
        for line in out:
            assert "*" not in line
            assert "_" not in line

    def test_clean_pair_still_dedupes(self):
        from tools.academic_docx import _sort_apa_citations
        cites = [
            "Smith, J. (2020). Title.",
            "Smith, J. (2020). Title.",
            "Smith, J. (2020). Title.  ",  # trailing whitespace
        ]
        out = _sort_apa_citations(cites)
        assert len(out) == 1


# ── Fix 3: brief_methodology prompt uses canonical tokens ──


class TestMethodologyPromptCanonicalTokens:

    def test_prompt_references_classic_6040_weight_tokens(self):
        import main as _main
        with open(
                _main.__file__, encoding="utf-8") as f:
            src = f.read()
        # Find the brief_methodology spec.
        idx = src.find('"brief_methodology"')
        assert idx > -1
        slice_ = src[idx:idx + 6000]
        assert "{{CLASSIC_6040_WEIGHT_EQUITY}}" in slice_
        assert "{{CLASSIC_6040_WEIGHT_BOND}}" in slice_

    def test_prompt_forbids_lowercase_variants(self):
        import main as _main
        with open(
                _main.__file__, encoding="utf-8") as f:
            src = f.read()
        idx = src.find('"brief_methodology"')
        slice_ = src[idx:idx + 6000]
        # Explicit "TOKEN HYGIENE" guidance forbidding the
        # invented lowercase forms.
        assert "static_equity_weight_pct" in slice_
        assert "static_bond_weight_pct" in slice_
        assert "TOKEN HYGIENE" in slice_


# ── Fix 4: REBALANCE_THRESHOLD_PP added ─────────────────────


class TestRebalanceThresholdToken:

    def test_token_in_substitution_table(self):
        from tools.numeric_substitution import (
            get_substitution_table,
        )
        t = get_substitution_table(
            "test_rebal_001", {}, None, hash_verified=True)
        assert t.get("{{REBALANCE_THRESHOLD_PP}}") == "2"

    def test_token_in_catalog(self):
        from tools.data_reference_catalog import CATALOG
        tokens = set()
        for _ck, _cl, entries in CATALOG:
            for e in entries:
                tokens.add(e.token)
        assert "{{REBALANCE_THRESHOLD_PP}}" in tokens

    def test_token_has_provenance(self):
        from tools.data_reference_catalog import (
            LOCKED_CONSTANT_PROVENANCE,
        )
        assert (
            "constant 2 (methodology rebalance gate)" in
            LOCKED_CONSTANT_PROVENANCE)

    def test_methodology_prompt_uses_token(self):
        import main as _main
        with open(
                _main.__file__, encoding="utf-8") as f:
            src = f.read()
        idx = src.find('"brief_methodology"')
        slice_ = src[idx:idx + 6000]
        # Prompt now uses the token; bare "2 percentage" was
        # the prior form.
        assert "{{REBALANCE_THRESHOLD_PP}}" in slice_


# ── Appendix Fix 1: section restate strip covers letters ────


class TestSectionRestateLetters:

    def test_section_letter_b_matches(self):
        from tools.academic_docx import _SECTION_RESTATE_RE
        for text in [
            "Section B: Full Strategy Performance",
            "Section C: Statistical Tests",
            "Section E: Factor Loadings",
            "Section H: Validation Audit Summary",
            "**Section B: Bootstrap CI**",
            "## Section D: Cross-strategy comparison",
            "section a -- intro",
        ]:
            assert _SECTION_RESTATE_RE.match(text), (
                f"failed to match: {text!r}")

    def test_section_digit_still_matches(self):
        from tools.academic_docx import _SECTION_RESTATE_RE
        for text in [
            "Section 1: Executive Summary",
            "**Section 3: Key Findings**",
        ]:
            assert _SECTION_RESTATE_RE.match(text)

    def test_non_section_still_skipped(self):
        from tools.academic_docx import _SECTION_RESTATE_RE
        for text in [
            "Executive Summary",
            "1. Executive Summary",
            "This section explores 5 strategies",
        ]:
            assert not _SECTION_RESTATE_RE.match(text)


# ── Appendix Fix 2: editor-export sub_table for appendix ────


class TestAppendixEditorExportSubTable:

    def test_substitution_table_built_for_appendix_too(self):
        """Source-pin: _editor_export gate builds the
        substitution table for executive_brief OR
        analytical_appendix (was brief-only)."""
        import main as _main
        with open(
                _main.__file__, encoding="utf-8") as f:
            src = f.read()
        # Look for the gate that includes both types.
        assert (
            '("executive_brief", "analytical_appendix")'
            in src) or (
            "_needs_sub_table" in src)


# ── Appendix Fix 3: bootstrap tokens + prompt ──────────────


class TestBootstrapMethodologyTokens:

    def test_block_length_token_resolves(self):
        from tools.numeric_substitution import (
            get_substitution_table,
        )
        t = get_substitution_table(
            "test_boot_001", {}, None, hash_verified=True)
        assert t.get("{{BOOTSTRAP_BLOCK_LENGTH}}") == "12"
        assert t.get("{{BOOTSTRAP_SEED}}") == "42"

    def test_tokens_in_catalog(self):
        from tools.data_reference_catalog import CATALOG
        tokens = set()
        for _ck, _cl, entries in CATALOG:
            for e in entries:
                tokens.add(e.token)
        assert "{{BOOTSTRAP_BLOCK_LENGTH}}" in tokens
        assert "{{BOOTSTRAP_SEED}}" in tokens

    def test_appendix_d_prompt_uses_tokens(self):
        import main as _main
        with open(
                _main.__file__, encoding="utf-8") as f:
            src = f.read()
        idx = src.find('"appendix_d"')
        assert idx > -1
        slice_ = src[idx:idx + 1200]
        assert "{{BOOTSTRAP_BLOCK_LENGTH}}" in slice_
        assert "{{BOOTSTRAP_SEED}}" in slice_


# ── Appendix Fix 4: crisis table footnote ──────────────────


class TestCrisisTableDaggerFootnote:

    def test_explicit_footnote_paragraph_after_table(self):
        """Source-pin: an italic Note. paragraph explaining †
        is added after Table F1. Replaces the
        title-parenthetical form that the operator reported as
        too easy to miss."""
        from tools.academic_docx import (
            build_analytical_appendix,
        )
        src = inspect.getsource(build_analytical_appendix)
        assert "Note. † indicates partial-overlap" in src

    def test_title_no_longer_carries_dagger_parenthetical(
            self):
        from tools.academic_docx import (
            build_analytical_appendix,
        )
        src = inspect.getsource(build_analytical_appendix)
        assert (
            "Crisis Window († indicates partial-overlap)"
            not in src), (
            "title-parenthetical form not removed; footnote "
            "explanation should be the only dagger doc")
