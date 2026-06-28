"""tests/test_hard_lock_expanded_exemptions.py -- June 28 2026.

Pins the expanded structural-exemption registry + the
references-section skip + the study-metadata anchor
augmentation. All three are responses to the brief-gen
hard-lock cap failure (sections 2-5 hitting the 3-pass limit
because the LLM emits raw bibliographic / ordinal / methodology
constants that the original 4-pattern registry didn't cover).

Substitution-table priority is preserved at every layer: any
value that IS in the substitution table flags as
token_available with a swap suggestion, even when it
coincidentally sits inside a structural pattern.
"""
from __future__ import annotations

import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── Helpers ────────────────────────────────────────────────────


def _scan(text, sub=None, anchors=None):
    from tools.untoken_numeric_check import (
        find_untoken_backed_numerics,
    )
    return find_untoken_backed_numerics(text, sub, anchors)


# ── Original 4 exemptions still hold ─────────────────────────


class TestOriginalExemptionsStillHold:

    def test_sp_500_index_name_exempted(self):
        assert _scan("The S&P 500 benchmark returned.") == []

    def test_100pct_equity_exempted(self):
        assert _scan("The benchmark holds 100% equity.") == []

    def test_balanced_portfolio_ref_exempted(self):
        assert _scan("The 60/40 portfolio is the baseline.") == []

    def test_stat_threshold_exempted(self):
        assert _scan("Significant at p < 0.005.") == []


# ── New June 28 2026 exemptions ───────────────────────────────


class TestBalancedAllocationWeights:

    def test_60pct_40pct_definitional_exempted(self):
        assert _scan(
            "Classic 60%/40% portfolio holds bonds + stocks.") == []

    def test_70pct_30pct_variant_exempted(self):
        assert _scan(
            "An aggressive 70%/30% allocation.") == []


class TestCourseNumber:

    def test_fna_670_exempted(self):
        assert _scan(
            "FNA 670 practicum at Queens University.") == []

    def test_fna_670_with_dash_exempted(self):
        assert _scan(
            "Project: FNA 670 -- Summer 2026.") == []


class TestFedTarget:

    def test_fed_2pct_target_exempted(self):
        assert _scan(
            "The Fed's 2% inflation target is intact.") == []

    def test_federal_reserve_2pct_goal_exempted(self):
        assert _scan(
            "Federal Reserve 2% inflation goal.") == []

    def test_2pct_inflation_target_bare_exempted(self):
        assert _scan(
            "The 2% inflation target was set in 2012.") == []


class TestOrdinalLabel:

    def test_section_2_exempted(self):
        assert _scan("See Section 2 of the brief.") == []

    def test_figure_3_exempted(self):
        assert _scan("Figure 3 shows the rolling correlation.") == []

    def test_table_b1_exempted(self):
        assert _scan("Table B.1 lists every strategy.") == []

    def test_slide_7_exempted(self):
        assert _scan("Slide 7 carries the live regime signal.") == []

    def test_appendix_c_exempted(self):
        assert _scan("Appendix C shows the bootstrap CI.") == []

    def test_part_ii_roman_exempted(self):
        assert _scan("Part II covers the methodology.") == []


class TestCitationYears:

    def test_parenthetical_year_1952_exempted(self):
        assert _scan(
            "Markowitz (1952) introduced MPT.") == []

    def test_smith_2020_exempted(self):
        assert _scan(
            "Recent work (Smith, 2020) confirms.") == []

    def test_2018a_variant_exempted(self):
        assert _scan(
            "Earlier results (Jones, 2018a) support.") == []


class TestDefinitionalWeight:

    def test_60pct_equity_exempted(self):
        assert _scan(
            "The Classic strategy holds 60% equity.") == []

    def test_40pct_bonds_exempted(self):
        assert _scan(
            "Allocations include 40% bonds throughout.") == []

    def test_80pct_stocks_exempted(self):
        assert _scan(
            "An aggressive 80% stocks allocation.") == []


class TestStatNotationExtended:

    def test_q_threshold_exempted(self):
        assert _scan("Significant at q < 0.05.") == []

    def test_beta_threshold_exempted(self):
        assert _scan("Factor beta > 0.85 in the model.") == []

    def test_95pct_confidence_interval_exempted(self):
        assert _scan(
            "The 95% confidence interval excludes zero.") == []


class TestDocumentFormat:

    def test_5_pages_exempted(self):
        assert _scan("The brief runs 5 pages.") == []

    def test_2000_words_exempted(self):
        assert _scan("Target length 2000 words per section.") == []

    def test_20_25_minutes_exempted(self):
        assert _scan(
            "The presentation is 20-25 minutes total.") == []

    def test_16_slides_exempted(self):
        assert _scan("The deck has 16 slides.") == []


class TestMethodologyCount:

    def test_10000_resamples_exempted(self):
        assert _scan(
            "We ran 10,000 resamples for the bootstrap.") == []

    def test_1000_bootstrap_iterations_exempted(self):
        assert _scan(
            "1000 bootstrap iterations per fold.") == []

    def test_12_month_block_exempted(self):
        assert _scan(
            "The block bootstrap uses a 12-month block "
            "length.") == []

    def test_10_fold_cv_exempted(self):
        assert _scan(
            "We use 10-fold cross-validation.") == []


# ── Substitution-table priority preserved ──────────────────


class TestSubstitutionTablePriorityPreserved:

    def test_substitution_value_inside_structural_still_flags(self):
        """Operator constraint preserved: even if a numeric sits
        inside a structural pattern (e.g. 2% inside fed_target),
        when that value IS in the substitution table it must
        flag as token_available so the LLM gets a swap
        suggestion. The exemption does NOT short-circuit the
        substitution check."""
        viols = _scan(
            "The Fed's 2% target is the policy anchor.",
            sub={"{{FED_INFLATION_TARGET}}": "2%"})
        assert len(viols) >= 1
        assert any(v.severity == "token_available"
                   and v.suggested_token == (
                       "{{FED_INFLATION_TARGET}}")
                   for v in viols)


# ── References-section skip ─────────────────────────────────


class TestReferencesSectionSkip:

    def test_references_heading_strips_subsequent_content(self):
        text = (
            "## Body\n"
            "This claims 99.99 as a finding.\n"
            "\n"
            "## References\n"
            "Smith, J. (2020). Vol. 42, pp. 100-130.\n"
            "Jones, K. (2018). Issue 12, pp. 55-77.\n"
        )
        viols = _scan(text)
        # The "99.99" in the body should flag (no token, no
        # anchor, no structural exemption).
        body_flagged = any(v.raw_value == "99.99" for v in viols)
        # Reference-section numerics (42, 100-130, 12, 55-77)
        # must NOT flag -- they're bibliographic constants.
        ref_flagged = any(
            v.raw_value in {"42", "100", "130", "12", "55", "77"}
            for v in viols)
        assert body_flagged
        assert not ref_flagged

    def test_bibliography_heading_also_recognised(self):
        text = (
            "## Body\n"
            "Standard content.\n"
            "\n"
            "## Bibliography\n"
            "Anderson, M. (2015). Vol. 88, pp. 25-49.\n"
        )
        viols = _scan(text)
        ref_flagged = any(
            v.raw_value in {"88", "25", "49"}
            for v in viols)
        assert not ref_flagged

    def test_bold_inline_references_paragraph_recognised(self):
        """The earlier brief drafts used **References** as an
        inline-bold paragraph (NOT a ## heading). The
        preprocessor must recognise the bold form too."""
        text = (
            "Body text with 99.99 as a finding.\n"
            "\n"
            "**References**\n"
            "Smith, J. (2020). Vol. 42, pp. 100-130.\n"
        )
        viols = _scan(text)
        body_flagged = any(v.raw_value == "99.99" for v in viols)
        ref_flagged = any(
            v.raw_value in {"42", "100", "130"}
            for v in viols)
        assert body_flagged
        assert not ref_flagged

    def test_bold_inline_with_colon_recognised(self):
        """**References:** -- colon inside the bold close."""
        text = (
            "Body 88.88 finding.\n"
            "\n"
            "**References:**\n"
            "Anderson, M. (2015). Vol. 77.\n"
        )
        viols = _scan(text)
        assert not any(v.raw_value == "77" for v in viols)
        assert any(v.raw_value == "88.88" for v in viols)

    def test_bold_bibliography_recognised(self):
        text = (
            "Finding 77.77.\n"
            "\n"
            "**Bibliography**\n"
            "Brown, P. (2019). Vol. 33.\n"
        )
        viols = _scan(text)
        assert not any(v.raw_value == "33" for v in viols)
        assert any(v.raw_value == "77.77" for v in viols)

    def test_references_block_ends_at_next_heading(self):
        text = (
            "## Body\n"
            "Content one.\n"
            "\n"
            "## References\n"
            "Smith (2020), Vol. 99, pp. 1-50.\n"
            "\n"
            "## Appendix\n"
            "And here 88.77 is a body finding.\n"
        )
        viols = _scan(text)
        # The post-references appendix body's 88.77 must flag.
        appendix_flagged = any(
            v.raw_value == "88.77" for v in viols)
        # Reference content must not flag.
        ref_flagged = any(
            v.raw_value in {"99", "50"} for v in viols)
        assert appendix_flagged
        assert not ref_flagged


# ── Study-metadata augmentation ─────────────────────────────


class TestStudyMetadataAugmentation:

    def test_287_with_study_months_anchor_not_flagged(self):
        """Reproduces the operator-reported bug: LLM emits raw
        '287 months' (not as {{STUDY_MONTHS}}). With the
        study-metadata augmentation, 287 is an implicit anchor
        and is NOT flagged."""
        viols = _scan(
            "The study runs 287 months from July 2002 to "
            "May 2026.",
            anchors={"study_months": 287.0})
        assert viols == []

    def test_augment_helper_adds_study_months(self):
        from main import _augment_anchors_with_study_metadata
        out = _augment_anchors_with_study_metadata(
            anchors={"existing": 1.5},
            substitution_table={"{{STUDY_MONTHS}}": "287",
                                "{{N_STRATEGIES}}": "10"})
        assert out["existing"] == 1.5
        assert out["study_months"] == 287.0
        assert out["n_strategies"] == 10.0

    def test_augment_helper_skips_em_dash(self):
        from main import _augment_anchors_with_study_metadata
        out = _augment_anchors_with_study_metadata(
            anchors={},
            substitution_table={"{{STUDY_MONTHS}}": "—"})
        assert "study_months" not in out

    def test_augment_helper_no_table_returns_anchors_copy(self):
        from main import _augment_anchors_with_study_metadata
        anchors = {"x": 1.0}
        out = _augment_anchors_with_study_metadata(anchors, None)
        assert out == {"x": 1.0}


# ── New token availability ─────────────────────────────────


class TestNewTokensInTable:

    def test_classic_6040_weight_equity_resolves(self):
        from tools.numeric_substitution import (
            get_substitution_table,
        )
        # Use a unique data_hash per test to bypass the
        # process-wide _substitution_cache.
        t = get_substitution_table(
            "test_weights_001", {}, None, hash_verified=True)
        assert t.get("{{CLASSIC_6040_WEIGHT_EQUITY}}") == "60%"
        assert t.get("{{CLASSIC_6040_WEIGHT_BOND}}") == "40%"

    def test_classic_60_40_underscored_aliases_exist(self):
        from tools.numeric_substitution import (
            get_substitution_table,
        )
        t = get_substitution_table(
            "test_aliases_002", {}, None, hash_verified=True)
        # Underscored variants present even when source data
        # missing (will resolve to em-dash but the KEY must
        # exist so the substitution layer can match the LLM's
        # underscored emissions).
        assert "{{CLASSIC_60_40_RECOVERY}}" in t
        assert "{{CLASSIC_60_40_MAX_DD}}" in t
        assert "{{CLASSIC_60_40_SHARPE}}" in t
        assert "{{CLASSIC_60_40_RECOVERY_MONTHS}}" in t

    def test_pre_2022_months_resolves_with_n_observations(self):
        from tools.numeric_substitution import (
            get_substitution_table,
        )
        t = get_substitution_table(
            "test_pre2022_287_003",
            {"n_observations": 287},
            None, hash_verified=True)
        # 287 - 53 (Jan 2022 -> May 2026) = 234
        assert t.get("{{PRE_2022_MONTHS}}") == "234"

    def test_pre_2022_months_em_dash_when_unknown(self):
        from tools.numeric_substitution import (
            get_substitution_table,
        )
        t = get_substitution_table(
            "test_pre2022_empty_004",
            {}, None, hash_verified=True)
        assert t.get("{{PRE_2022_MONTHS}}") == "—"


# ── Data reference catalog entries ────────────────────────


class TestDeferralStashBannerStripping:
    """REGRESSION pin for draft 73 -- the operator-reported
    failure where content_json carried resolved values instead
    of {{TOKEN}} placeholders despite DEFER_SUBSTITUTION_TO_EXPORT
    being ON.

    Root cause: the deferral swap at the end of harness_narrative
    looks up `_raw_per_substituted.get(final_text)` where
    final_text is the BANNER-STRIPPED form (result of
    _strip_banner(result.response)). The stash was keyed by the
    UN-STRIPPED substituted form, so the lookup missed + the
    swap fell through to substituted.

    Fix: also stash under the banner-stripped key so the
    lookup matches whether final_text was stripped or not."""

    def test_stash_keyed_by_both_stripped_and_unstripped(self):
        """Source-inspection pin: the stash population stores
        the raw text under TWO keys -- the un-stripped
        substituted form (for harness internal use) AND the
        banner-stripped form (for the deferral swap lookup)."""
        import inspect
        from tools.academic_export import harness_narrative
        src = inspect.getsource(harness_narrative)
        # The fix must store under stripped_sub (banner-
        # stripped substituted) so the deferral swap finds
        # the raw form even when final_text was stripped.
        assert (
            "_raw_per_substituted[stripped_sub]" in src)
        # Plus the un-stripped form for legacy callers.
        assert "_raw_per_substituted[substituted]" in src


class TestNewCatalogEntries:

    def test_new_tokens_have_catalog_entries(self):
        from tools.data_reference_catalog import CATALOG
        all_tokens = set()
        for _ck, _cl, entries in CATALOG:
            for e in entries:
                all_tokens.add(e.token)
        # New tokens added in this PR must each have a catalog
        # entry so the data reference sheet surfaces them.
        for token in [
            "{{CLASSIC_6040_WEIGHT_EQUITY}}",
            "{{CLASSIC_6040_WEIGHT_BOND}}",
            "{{PRE_2022_MONTHS}}",
            "{{CLASSIC_60_40_RECOVERY}}",
            "{{CLASSIC_60_40_RECOVERY_MONTHS}}",
            "{{CLASSIC_60_40_MAX_DD}}",
            "{{CLASSIC_60_40_SHARPE}}",
        ]:
            assert token in all_tokens, (
                f"{token} missing from data_reference_catalog")
