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
