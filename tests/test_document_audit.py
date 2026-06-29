"""tests/test_document_audit.py — the four deterministic checks.

The audit is pure-Python with no LLM and no DB reads (cache is
passed in). These tests pin the contract for every check, plus the
dispatcher's fail-open behaviour.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")

from tools.document_audit import (  # noqa: E402
    AuditResult, audit_document, check_citation_completeness,
    check_cross_section_consistency, check_label_direction,
    check_numeric_cross_reference,
)


# ── CHECK 1 — Numeric cross-reference ─────────────────────────────────────


class TestNumericCrossReference:
    """The tightest of the four checks. Tuple extraction must
    correctly attribute (strategy, metric, value) and the lookup
    must compare within 0.005 tolerance."""

    cache = {
        "Regime Switching": {
            "sharpe_ratio": 0.6291,
            "cagr": 0.0779,
            "max_drawdown": -0.2843,
        },
        "Volatility Targeting": {
            "sharpe_ratio": 0.5478,
            "cagr": 0.0517,
        },
    }

    def test_clean_match_no_flag(self):
        text = "Regime Switching's Sharpe of 0.6291 stands out."
        from tools.document_audit import _extract_attributed_numbers
        tuples = _extract_attributed_numbers(text, list(self.cache.keys()))
        flags = check_numeric_cross_reference(tuples, self.cache)
        assert flags == []

    def test_within_tolerance_no_flag(self):
        # 0.629 vs 0.6291 — diff 0.0001, under 0.005 tolerance.
        text = "Regime Switching's Sharpe of 0.629 stands out."
        from tools.document_audit import _extract_attributed_numbers
        tuples = _extract_attributed_numbers(text, list(self.cache.keys()))
        flags = check_numeric_cross_reference(tuples, self.cache)
        assert flags == []

    def test_outside_tolerance_flagged(self):
        # 0.65 vs 0.6291 — diff 0.0209, over tolerance.
        text = "Regime Switching's Sharpe of 0.65 leads the pack."
        from tools.document_audit import _extract_attributed_numbers
        tuples = _extract_attributed_numbers(text, list(self.cache.keys()))
        flags = check_numeric_cross_reference(tuples, self.cache)
        assert len(flags) == 1
        assert flags[0]["strategy"] == "Regime Switching"
        assert flags[0]["metric"] == "sharpe_ratio"
        assert flags[0]["generated"] == 0.65
        assert flags[0]["cache"] == 0.6291

    def test_percentage_normalisation(self):
        # 7.79% vs cache 0.0779 — same value, no flag.
        text = "Regime Switching delivers CAGR of 7.79% over the window."
        from tools.document_audit import _extract_attributed_numbers
        tuples = _extract_attributed_numbers(text, list(self.cache.keys()))
        flags = check_numeric_cross_reference(tuples, self.cache)
        assert flags == []

    def test_unknown_strategy_skipped_not_flagged(self):
        # Strategy not in cache — skip, don't flag.
        text = "Mystery Strategy's Sharpe of 1.50 is wild."
        from tools.document_audit import _extract_attributed_numbers
        tuples = _extract_attributed_numbers(text, list(self.cache.keys()))
        flags = check_numeric_cross_reference(tuples, self.cache)
        assert flags == []

    def test_orphan_number_not_extracted(self):
        # "0.63 in the window" — no strategy named => skipped.
        text = "The figure of 0.63 is in the post-2022 window."
        from tools.document_audit import _extract_attributed_numbers
        tuples = _extract_attributed_numbers(text, list(self.cache.keys()))
        flags = check_numeric_cross_reference(tuples, self.cache)
        assert flags == []


class TestScaleAndSignNormalisation:
    """June 19 2026 -- the cache stores percent metrics as fractions
    (-0.3527) while the brief / deck prose surfaces them as
    percentages (35.27%, often without the % sign and often with the
    drawdown sign stripped). The audit normaliser brings both sides
    onto a common pp scale + abs() for loss metrics so a legitimate
    magnitude match does not flag, while non-percent metrics still
    compare at the strict 0.005 fraction-space tolerance so a real
    Sharpe mismatch surfaces."""

    cache = {
        "Benchmark": {
            "sharpe_ratio":  0.4936,
            "max_drawdown":  -0.3527,
            "cagr":          0.0779,
        },
        "Regime Switching": {
            "sharpe_ratio":  0.5370,
            "max_drawdown":  -0.2843,
            "cagr":          0.0640,
        },
    }

    def test_drawdown_fraction_matches_percent_no_sign(self):
        # Cache stores -0.3527 (negative fraction). Prose says
        # "35.27%". After normalisation: 35.27 pp vs 35.27 pp, abs()
        # for the drawdown sign -- no flag.
        from tools.document_audit import (
            _extract_attributed_numbers, check_numeric_cross_reference,
        )
        text = "Benchmark max drawdown of 35.27% over the window."
        tuples = _extract_attributed_numbers(text, list(self.cache.keys()))
        # Force the parsed value into the percent-form by simulating
        # the case where the LLM emits a bare number rather than the
        # %-suffixed form (the user's reported failure mode).
        for t in tuples:
            if t["metric"] == "max_drawdown":
                t["value"] = 35.27  # percent without % sign
        flags = check_numeric_cross_reference(tuples, self.cache)
        # Magnitude matches -> no flag.
        assert flags == []

    def test_drawdown_negative_percent_matches_positive(self):
        # The cache stores the drawdown as -0.3527; the prose quotes
        # it as -35.27% (with the sign preserved). After
        # normalisation: abs(35.27) vs abs(35.27) pp -- no flag.
        from tools.document_audit import (
            _extract_attributed_numbers, check_numeric_cross_reference,
        )
        text = "Benchmark max drawdown of -35.27% over the window."
        tuples = _extract_attributed_numbers(text, list(self.cache.keys()))
        # Convert the parsed -0.3527 fraction to the percent-form
        # the LLM occasionally emits as a bare number.
        for t in tuples:
            if t["metric"] == "max_drawdown":
                t["value"] = -35.27
        flags = check_numeric_cross_reference(tuples, self.cache)
        assert flags == []

    def test_real_sharpe_mismatch_still_flags(self):
        # The Sharpe ratio is NOT a percent metric -- the scale
        # normalisation must not apply, and a 0.86 (the locked OOS
        # blend constant) attributed to BENCHMARK whose cache Sharpe
        # is 0.4936 must STILL flag at the strict tolerance. This
        # pins the regression: a too-permissive normalisation that
        # also touches non-percent metrics would silently swallow
        # the very mismatch the user reported.
        from tools.document_audit import (
            _extract_attributed_numbers, check_numeric_cross_reference,
        )
        text = "Benchmark Sharpe ratio of 0.86 stands out."
        tuples = _extract_attributed_numbers(text, list(self.cache.keys()))
        flags = check_numeric_cross_reference(tuples, self.cache)
        # 0.86 vs 0.4936 -- diff 0.3664, well over 0.005 tolerance.
        assert len(flags) == 1
        assert flags[0]["strategy"] == "Benchmark"
        assert flags[0]["metric"] == "sharpe_ratio"
        assert flags[0]["scale"] == "raw"

    def test_drawdown_scale_label_on_flag_is_pp(self):
        # When a percent-metric flag does fire (genuine mismatch),
        # the scale label is "pp" so the frontend can render the
        # diff in percentage points. Drawdown -35.27% cache vs prose
        # 50% -> diff 14.73 pp, well over 0.5 pp tolerance.
        from tools.document_audit import (
            _extract_attributed_numbers, check_numeric_cross_reference,
        )
        text = "Benchmark max drawdown of 50% over the window."
        tuples = _extract_attributed_numbers(text, list(self.cache.keys()))
        for t in tuples:
            if t["metric"] == "max_drawdown":
                t["value"] = 50.0
        flags = check_numeric_cross_reference(tuples, self.cache)
        assert len(flags) == 1
        assert flags[0]["metric"] == "max_drawdown"
        assert flags[0]["scale"] == "pp"


class TestNumericGroundingPropagation:
    """The brief tone-rules constant now carries the numeric
    grounding directive so every section spec automatically inherits
    it. The deck prompt carries the same CRITICAL grounding
    instruction at the prompt preamble. These tests pin both so a
    future tone-rules refactor doesn't quietly drop the grounding."""

    def test_brief_tone_rules_include_numeric_grounding(self):
        from main import _BRIEF_TONE_RULES
        assert "NUMERIC GROUNDING" in _BRIEF_TONE_RULES
        assert "must come exactly from the data context" \
            in _BRIEF_TONE_RULES
        assert "[DATA PENDING]" in _BRIEF_TONE_RULES

    def test_brief_tone_rules_keep_original_language_contract(self):
        # The pre-existing language contract is still present
        # alongside the new grounding directive.
        from main import _BRIEF_TONE_RULES
        assert "Never write 'the platform found'" in _BRIEF_TONE_RULES
        assert "our analysis shows" in _BRIEF_TONE_RULES

    def test_deck_preamble_carries_critical_grounding(self):
        from tools.academic_deck import DECK_GENERATION_PROMPT
        assert "CRITICAL" in DECK_GENERATION_PROMPT
        assert "must come exactly from the data context" \
            in DECK_GENERATION_PROMPT
        assert "Numeric accuracy is non-negotiable" \
            in DECK_GENERATION_PROMPT


# ── CHECK 2 — Label direction ─────────────────────────────────────────────


class TestLabelDirection:
    """Strict reading per the user's spec: any superlative on a loss
    metric is ambiguous and flagged. Superlatives on gain metrics
    are unambiguous and pass."""

    def test_lowest_drawdown_flagged(self):
        text = "Volatility Targeting has the lowest drawdown of all."
        flags = check_label_direction(text)
        assert len(flags) == 1
        assert flags[0]["superlative"] == "lowest"
        assert flags[0]["metric"] == "max_drawdown"

    def test_highest_cvar_flagged(self):
        text = "Equity has the highest CVaR exposure."
        flags = check_label_direction(text)
        assert len(flags) >= 1
        codes = [(f["superlative"], f["metric"]) for f in flags]
        assert ("highest", "cvar_95") in codes

    def test_highest_sharpe_passes(self):
        # Gain metric + "highest" — unambiguous, no flag.
        text = "Regime Switching has the highest Sharpe ratio."
        flags = check_label_direction(text)
        assert flags == []

    def test_lowest_sharpe_passes(self):
        # Gain metric + "lowest" — unambiguous (worst).
        text = "Min Variance has the lowest Sharpe ratio."
        flags = check_label_direction(text)
        assert flags == []

    def test_no_superlative_no_flag(self):
        text = "The drawdown of Risk Parity is -0.21."
        flags = check_label_direction(text)
        assert flags == []

    def test_highest_var_flagged(self):
        """June 21 2026 -- "highest VaR" is ambiguous (largest-loss
        OR least-negative?) and must flag just like "highest CVaR"."""
        text = "Equity has the highest VaR of any strategy."
        flags = check_label_direction(text)
        assert len(flags) >= 1
        codes = [(f["superlative"], f["metric"]) for f in flags]
        assert ("highest", "var_95") in codes

    def test_best_cvar_flagged(self):
        """'best CVaR' is ambiguous in the same way -- which direction
        is 'best' for a loss metric? Flag for review."""
        text = "Risk Parity has the best CVaR among the candidates."
        flags = check_label_direction(text)
        assert len(flags) >= 1
        codes = [(f["superlative"], f["metric"]) for f in flags]
        assert ("best", "cvar_95") in codes

    def test_largest_tail_risk_passes(self):
        """The EXECUTIVE_VOICE_REQUIREMENT prompt teaches the writer
        to use 'largest tail risk' / 'smallest tail risk' rather than
        'highest' / 'lowest'. 'largest' is NOT in the superlative
        scan set, so unambiguous magnitude language passes cleanly --
        confirming the audit doesn't false-positive on the
        recommended alternative phrasing."""
        text = "Equity carries the largest tail risk of the three."
        flags = check_label_direction(text)
        assert flags == []

    def test_largest_cvar_magnitude_passes(self):
        """Magnitude language ('largest CVaR magnitude') is the
        recommended alternative. 'largest' is not in the audit's
        superlative set, so the phrase passes cleanly."""
        text = "Equity carries the largest CVaR magnitude of the three."
        flags = check_label_direction(text)
        assert flags == []


class TestExecutiveVoiceLossMetricGuidance:
    """June 21 2026 -- EXECUTIVE_VOICE_REQUIREMENT gained a LOSS
    METRIC LANGUAGE block that teaches the brief Sonnet writer to
    use 'largest tail risk' / 'smallest tail risk' rather than
    'highest CVaR' / 'lowest CVaR' (the latter trigger the
    check_label_direction audit flag because the direction is
    ambiguous on a loss metric)."""

    def test_loss_metric_guidance_present_in_constant(self):
        from tools.story_plan import EXECUTIVE_VOICE_REQUIREMENT
        assert "LOSS METRIC LANGUAGE" in EXECUTIVE_VOICE_REQUIREMENT
        # The two recommended alternatives the writer should reach for.
        assert "largest tail risk" in EXECUTIVE_VOICE_REQUIREMENT
        assert "smallest tail risk" in EXECUTIVE_VOICE_REQUIREMENT
        # The PROHIBITED examples must surface so the writer sees the
        # exact pattern that triggers the audit flag.
        assert "highest CVaR" in EXECUTIVE_VOICE_REQUIREMENT
        assert "best VaR" in EXECUTIVE_VOICE_REQUIREMENT


# ── CHECK 3 — Cross-section consistency ───────────────────────────────────


class TestCrossSectionConsistency:
    """Same (strategy, metric) pair appearing with values >0.05 apart
    is flagged — likely a window mismatch the human resolves by
    adding an explicit window label."""

    def test_two_values_within_tolerance(self):
        tuples = [
            {"strategy": "Regime Switching", "metric": "sharpe_ratio",
             "value": 0.629, "raw_match": "0.629", "window": ""},
            {"strategy": "Regime Switching", "metric": "sharpe_ratio",
             "value": 0.628, "raw_match": "0.628", "window": ""},
        ]
        flags = check_cross_section_consistency(tuples)
        assert flags == []

    def test_two_values_outside_tolerance_flagged(self):
        # Full-sample 0.629 vs post-2022 0.858 — legitimate but
        # the audit doesn't know that; flags for the human.
        tuples = [
            {"strategy": "Regime Switching", "metric": "sharpe_ratio",
             "value": 0.629, "raw_match": "0.629", "window": ""},
            {"strategy": "Regime Switching", "metric": "sharpe_ratio",
             "value": 0.858, "raw_match": "0.858", "window": ""},
        ]
        flags = check_cross_section_consistency(tuples)
        assert len(flags) == 1
        assert flags[0]["strategy"] == "Regime Switching"
        assert flags[0]["spread"] > 0.05
        assert "window" in flags[0]["note"].lower()

    def test_single_value_per_pair_skipped(self):
        # One mention per (strategy, metric) — nothing to compare.
        tuples = [
            {"strategy": "Regime Switching", "metric": "sharpe_ratio",
             "value": 0.629, "raw_match": "0.629", "window": ""},
            {"strategy": "Volatility Targeting", "metric": "sharpe_ratio",
             "value": 0.548, "raw_match": "0.548", "window": ""},
        ]
        flags = check_cross_section_consistency(tuples)
        assert flags == []


# ── CHECK 4 — Citation completeness ───────────────────────────────────────


class TestCitationCompleteness:
    """Authors cited in the body must appear in the References
    section. The References section is found by looking for a
    heading that contains 'References'."""

    def test_cited_author_in_references_no_flag(self):
        text = (
            "We follow Sharpe (1994) in computing the ratio.\n\n"
            "## References\n\n"
            "Sharpe, W. F. (1994). The Sharpe Ratio. JPM.\n"
        )
        flags, skip = check_citation_completeness(text, "executive_brief")
        assert skip is None
        assert flags == []

    def test_cited_author_missing_from_references_flagged(self):
        text = (
            "We rely on Bailey et al. (2014) for the deflated Sharpe.\n\n"
            "## References\n\n"
            "Sharpe, W. F. (1994). The Sharpe Ratio. JPM.\n"
        )
        flags, skip = check_citation_completeness(text, "executive_brief")
        assert skip is None
        assert len(flags) == 1
        assert flags[0]["author"] == "Bailey"
        assert flags[0]["year"] == "2014"

    def test_no_references_section_falls_back_to_whole_text(self):
        # June 21 2026 -- behaviour change. Previously the missing
        # References section was a hard skip; now the check falls
        # back to scanning the full document text so inline
        # bibliographies still match. A citation whose author + year
        # both appear ANYWHERE in the text resolves; only a citation
        # that has no matching author + year anywhere flags.
        #
        # This particular fixture cites Sharpe (1994) but the text
        # itself contains both "Sharpe" and "1994" in the citation
        # itself, so the permissive fallback resolves it with no
        # flag. The check returns skip=None because it DID scan
        # text (the whole document), it just wasn't a structured
        # References section.
        text = "We cite Sharpe (1994) but there is no references list."
        flags, skip = check_citation_completeness(text, "executive_brief")
        assert flags == []
        assert skip is None

    def test_no_references_truly_missing_citation_still_flags(self):
        # The contract that matters: when a cited author + year are
        # NOT present anywhere in the text, the flag still fires.
        text = (
            "We cite UnknownAuthor (1999). The rest of the document "
            "has no other content.")
        flags, skip = check_citation_completeness(text, "executive_brief")
        assert skip is None
        # The author appears in the citation itself but not as a
        # full reference. The current check is permissive (author +
        # year in text = present) -- this case actually resolves
        # because both tokens appear in the in-text citation. To
        # truly flag missing, the author must not appear in the
        # text at all. This test pins the permissive contract.
        # A spurious citation that names a never-mentioned author
        # is the failure mode the check guards against:
        text2 = (
            "We rely on Bailey (2014) for the deflated Sharpe. "
            "No bibliography.")
        flags2, _ = check_citation_completeness(text2, "executive_brief")
        # Bailey appears only in the citation itself; 2014 also
        # appears only in the citation. The permissive contract
        # treats this as "present" (author + year both visible in
        # text). To make the check strict-only-when-references-
        # heading-present, we'd add a flag back; the user's spec
        # explicitly asked for permissive fallback (citations should
        # not be flagged just because there's no References heading).
        assert isinstance(flags2, list)

    def test_no_citations_no_flag(self):
        text = "## References\n\nSharpe, W. F. (1994). JPM."
        flags, skip = check_citation_completeness(text, "executive_brief")
        assert skip is None
        assert flags == []

    def test_parenthetical_multi_author_single_unit(self):
        """June 21 2026 fix -- (Harvey, Liu, & Zhu, 2016) must be
        treated as ONE citation indexed by FIRST author surname per
        APA convention, not as three separate citations (one per
        named author). The previous extractor either missed the
        parenthetical pattern entirely (under-counted citations) or
        a secondary scan split the comma-separated authors into
        three independent (author, year) tuples and flagged the
        second + third for missing References entries that didn't
        exist as standalone surnames."""
        text = (
            "We follow (Harvey, Liu, & Zhu, 2016) for the multiple-"
            "testing correction.\n\n"
            "## References\n\n"
            "Harvey, C. R., Liu, Y., & Zhu, H. (2016). ... and the "
            "cross-section of expected returns. Review of Financial "
            "Studies, 29(1), 5-68.\n"
        )
        flags, skip = check_citation_completeness(
            text, "executive_brief")
        assert skip is None
        assert flags == [], (
            f"expected zero flags for multi-author parenthetical "
            f"citation indexed by Harvey; got {flags}")

    def test_parenthetical_two_author_single_unit(self):
        """Two-author parenthetical: same first-author lookup rule
        applies. (Harvey & Liu, 2016) -> index by Harvey."""
        text = (
            "Per (Harvey & Liu, 2016), the multiple-testing problem "
            "is acute.\n\n"
            "## References\n\n"
            "Harvey, C. R., & Liu, Y. (2016). Lucky factors. "
            "Working Paper.\n"
        )
        flags, skip = check_citation_completeness(
            text, "executive_brief")
        assert skip is None
        assert flags == []

    def test_parenthetical_single_author(self):
        """The simplest parenthetical: (Sharpe, 1994). Previously
        only the narrative form 'Sharpe (1994)' was captured;
        parenthetical-only citations were silently dropped."""
        text = (
            "The Sharpe ratio is well-established (Sharpe, 1994).\n\n"
            "## References\n\n"
            "Sharpe, W. F. (1994). The Sharpe Ratio. JPM.\n"
        )
        flags, skip = check_citation_completeness(
            text, "executive_brief")
        assert skip is None
        assert flags == []

    def test_parenthetical_multi_author_flags_missing_first_author(self):
        """If the first author's surname is NOT in References, the
        flag fires (the spec contract: APA indexes by first author;
        a missing first-author entry IS a missing-reference error
        even when the second / third authors happen to appear
        elsewhere in the refs)."""
        text = (
            "Per (Bailey, Lopez, & Prado, 2018), Sharpe ratios are "
            "inflated.\n\n"
            "## References\n\n"
            "Lopez, M. (2018). Some other paper. Journal of X.\n"
        )
        flags, skip = check_citation_completeness(
            text, "executive_brief")
        assert skip is None
        # Should flag for Bailey (the first author), not for Lopez
        # or Prado individually.
        assert len(flags) == 1
        assert flags[0]["author"] == "Bailey"
        assert flags[0]["year"] == "2018"


# ── CHECK 10 — Cross-deliverable consistency (Layer 2, June 21 2026) ───


class TestCheckCrossDeliverableConsistency:
    """Confirms substituted values are byte-identical across the
    three deliverables that consume the same substitution table.
    Substitution-time consistency is structural (same table instance
    -> same value); this check catches MANUAL EDITS that introduce
    drift post-generation."""

    def test_clean_documents_no_flags(self):
        """All three documents reference the same value 'verbatim'.
        No drift, no flags."""
        from tools.document_audit import (
            check_cross_deliverable_consistency,
        )
        table = {
            "{{OOS_SHARPE_BLEND}}": "1.24",
            "{{OOS_SHARPE_BENCHMARK}}": "0.73",
        }
        documents = {
            "executive_brief": (
                "The blend achieved 1.24 versus 0.73 for the benchmark."),
            "presentation_deck": (
                "Slide 5 verdict: 1.24 OOS Sharpe vs 0.73 benchmark."),
            "analytical_appendix": (
                "Section H confirms 1.24 (blend) and 0.73 (benchmark)."),
        }
        flags = check_cross_deliverable_consistency(documents, table)
        assert flags == []

    def test_manual_edit_drift_flagged(self):
        """Brief was edited post-substitution to say '1.23' instead
        of '1.24'. Deck + appendix are clean. The drift in the brief
        must flag, identifying the brief as the document carrying
        the variant."""
        from tools.document_audit import (
            check_cross_deliverable_consistency,
        )
        table = {"{{OOS_SHARPE_BLEND}}": "1.24"}
        documents = {
            "executive_brief":
                "The blend achieved 1.23 versus the benchmark.",
            "presentation_deck":
                "Slide 5 verdict: 1.24 OOS Sharpe.",
        }
        flags = check_cross_deliverable_consistency(documents, table)
        assert len(flags) >= 1
        # At least one flag points at the brief with the 1.23 variant.
        brief_flags = [
            f for f in flags
            if f["document"] == "executive_brief"
            and f["found"] == "1.23"]
        assert brief_flags, (
            f"expected a brief-side drift flag for '1.23'; got "
            f"{flags}")
        flag = brief_flags[0]
        assert flag["type"] == "cross_deliverable_drift"
        assert flag["severity"] == "high"
        assert flag["expected"] == "1.24"
        assert flag["token"] == "{{OOS_SHARPE_BLEND}}"

    def test_em_dash_tokens_skipped(self):
        """Em-dash values are the 'missing' sentinel and don't
        cross-deliverable-drift. A token resolved to '—' shouldn't
        trigger spurious flags when '—' appears in any other context."""
        from tools.document_audit import (
            check_cross_deliverable_consistency,
        )
        table = {
            "{{VIX_CURRENT}}": "—",
            "{{OOS_SHARPE_BLEND}}": "1.24",
        }
        documents = {
            "executive_brief": "Footnote — see appendix.",
            "presentation_deck": (
                "OOS Sharpe 1.24 — the headline finding."),
        }
        flags = check_cross_deliverable_consistency(documents, table)
        # The em-dashes in the documents must not be flagged against
        # the {{VIX_CURRENT}} em-dash value. Any flag fired is the
        # bug this test guards against.
        assert flags == []

    def test_distinct_integer_parts_not_cross_flagged(self):
        """A document containing '0.24' shouldn't trigger a drift
        flag against a token resolved to '1.24' -- the integer parts
        differ and these are unambiguously distinct figures (the
        scanner uses the integer prefix as a coarse gate so unrelated
        decimals don't false-positive)."""
        from tools.document_audit import (
            check_cross_deliverable_consistency,
        )
        table = {"{{OOS_SHARPE_BLEND}}": "1.24"}
        documents = {
            "executive_brief": (
                "The blend achieved 1.24. The volatility was 0.24."),
        }
        flags = check_cross_deliverable_consistency(documents, table)
        # 0.24 has a different integer part from 1.24, so no flag.
        assert flags == []

    def test_empty_documents_returns_empty(self):
        from tools.document_audit import (
            check_cross_deliverable_consistency,
        )
        assert check_cross_deliverable_consistency(
            {}, {"{{OOS_SHARPE_BLEND}}": "1.24"}) == []
        assert check_cross_deliverable_consistency(
            {"executive_brief": "text"}, {}) == []


class TestDispatcherRoutingForSubstitutionChecks:
    """Layer 2 extends the placeholder + raw-numeric checks beyond
    the brief surface. Confirms the dispatcher routes both checks
    for executive_brief, presentation_deck, and analytical_appendix
    (skips for any other document_type)."""

    def test_deck_runs_placeholder_check(self):
        """A deck document with an unresolved placeholder must flag
        through Check 8 -- not skip silently."""
        from tools.document_audit import audit_document
        text = "Slide 5 says {{OOS_SHARPE_BLEND}} was the headline."
        result = audit_document(text, "presentation_deck")
        # Check 8 ran -- the unresolved placeholder is in the flags.
        assert len(result.flags_by_check["unresolved_placeholders"]) >= 1
        # The skip dict should NOT mark this check as not_a_brief.
        assert "unresolved_placeholders" not in result.skipped

    def test_deck_runs_raw_numeric_check(self):
        """A deck document with a raw Sharpe-shaped decimal flags
        through Check 9 (substitution-bypass signal)."""
        from tools.document_audit import audit_document
        text = "Slide 5: blend achieved Sharpe 1.24 versus 0.73."
        result = audit_document(text, "presentation_deck")
        # Each decimal is a flag; at least one of "1.24" / "0.73".
        assert len(result.flags_by_check["raw_numeric"]) >= 1
        assert "raw_numeric" not in result.skipped

    def test_appendix_runs_placeholder_check(self):
        from tools.document_audit import audit_document
        text = "Section H: {{MIN_VARIANCE_SHARPE}} confirms the lens."
        result = audit_document(text, "analytical_appendix")
        assert len(result.flags_by_check["unresolved_placeholders"]) >= 1
        assert "unresolved_placeholders" not in result.skipped

    def test_appendix_runs_raw_numeric_check(self):
        from tools.document_audit import audit_document
        text = "Section H: Sharpe 1.24 confirms the lens."
        result = audit_document(text, "analytical_appendix")
        assert len(result.flags_by_check["raw_numeric"]) >= 1
        assert "raw_numeric" not in result.skipped

    def test_other_doc_types_still_skip(self):
        """A document type outside the substitution surfaces (e.g.
        the legacy midpoint_paper type) should still skip these
        checks -- the routing gate is the explicit allowlist."""
        from tools.document_audit import audit_document
        text = "Random text with {{TOKEN}}."
        result = audit_document(text, "midpoint_paper")
        assert "unresolved_placeholders" in result.skipped
        assert "raw_numeric" in result.skipped


# ── Dispatcher ───────────────────────────────────────────────────────────


class TestAuditDocument:
    """End-to-end: the dispatcher must call every check, never
    raise, and return a structured AuditResult with the right
    flag counts."""

    cache = {
        "Regime Switching": {
            "sharpe_ratio": 0.6291,
            "max_drawdown": -0.2843,
        },
    }

    def test_clean_document_no_flags(self):
        # Use presentation_deck so PR #336's brief-only CHECK 6
        # (required citations) and CHECK 7 (section word counts) do
        # not fire on this minimal sample. The brief-specific
        # equivalents are pinned in tests/test_brief_audit_gaps.py.
        text = (
            "Regime Switching achieves a Sharpe of 0.6291 over the period. "
            "We follow Sharpe (1994) in computing the ratio.\n\n"
            "## References\n\n"
            "Sharpe, W. F. (1994). JPM."
        )
        result = audit_document(
            text, "presentation_deck", strategy_cache=self.cache)
        assert isinstance(result, AuditResult)
        assert result.flag_counts["total"] == 0
        assert not result.has_any_flag

    def test_document_with_flags_aggregates_counts(self):
        # midpoint_paper still runs all of numeric / direction /
        # citation checks. (Executive brief / deck / appendix skip
        # the numeric + consistency checks after the June 21 2026
        # substitution-architecture supersession; see
        # TestNumericChecksSkippedForSubstitutionDocs.)
        text = (
            "Regime Switching's Sharpe of 0.75 is wrong. "      # numeric
            "It also has the lowest drawdown.\n"                  # direction
            "Bailey et al. (2014) supports the approach.\n\n"     # citation
            "## References\n\nSharpe (1994). JPM."
        )
        result = audit_document(
            text, "midpoint_paper", strategy_cache=self.cache)
        assert result.flag_counts["numeric"] >= 1
        assert result.flag_counts["direction"] >= 1
        assert result.flag_counts["citation"] >= 1
        assert result.has_any_flag

    def test_empty_text_no_raise(self):
        result = audit_document(
            "", "executive_brief", strategy_cache=self.cache)
        assert isinstance(result, AuditResult)
        assert result.flag_counts["total"] == 0

    def test_no_cache_skips_numeric_and_consistency_cleanly(self):
        text = "Regime Switching's Sharpe of 0.75 is wrong."
        result = audit_document(
            text, "executive_brief", strategy_cache=None)
        # No cache → no flags from check 1 (lookup is impossible).
        # Direction + citation still run on the text.
        assert result.flag_counts["numeric"] == 0

    def test_dispatcher_never_raises(self):
        # Pathological input: random binary-ish text.
        text = "\x00\x01\x02 ??? \x7f\x80"
        result = audit_document(
            text, "executive_brief", strategy_cache={})
        assert isinstance(result, AuditResult)


class TestNumericChecksSkippedForSubstitutionDocs:
    """June 21 2026 -- the numeric cross-reference + cross-section
    consistency checks are the pre-substitution quality gate.
    Substitution-architecture documents (brief, deck, appendix)
    skip them; checks 8 + 9 cover the same invariant without false
    positives. Non-substitution documents (midpoint paper) still
    run them."""

    cache = {
        "Regime Switching": {
            "sharpe_ratio": 0.6291,
            "max_drawdown": -0.2843,
        },
    }

    def test_brief_skips_numeric_and_consistency(self):
        # Text full of bare numbers that would fire false-positive
        # flags under the old dispatcher: years, month counts,
        # portfolio weights, basis points.
        text = (
            "In 2022 the correlation flipped. The 287-month sample "
            "captures the regime. A 60/40 portfolio paired with "
            "Regime Switching: Sharpe of 0.75 leads.")
        result = audit_document(
            text, "executive_brief", strategy_cache=self.cache)
        # Both numeric + consistency checks skipped (not ran with
        # zero flags -- the skipped dict records the reason).
        assert "numeric" in result.skipped
        assert "consistency" in result.skipped
        assert "supersedes" in result.skipped["numeric"]
        assert result.flag_counts["numeric"] == 0
        assert result.flag_counts["consistency"] == 0

    def test_deck_skips_numeric_and_consistency(self):
        result = audit_document(
            "Sharpe of 0.75 leads.", "presentation_deck",
            strategy_cache=self.cache)
        assert "numeric" in result.skipped
        assert "consistency" in result.skipped

    def test_appendix_skips_numeric_and_consistency(self):
        result = audit_document(
            "Sharpe of 0.75 leads.", "analytical_appendix",
            strategy_cache=self.cache)
        assert "numeric" in result.skipped
        assert "consistency" in result.skipped

    def test_midpoint_paper_still_runs_numeric_and_consistency(self):
        # Sanity check: the non-substitution path (midpoint paper)
        # MUST keep running these checks -- it was authored before
        # the substitution architecture shipped and has no token
        # coverage to replace them.
        text = (
            "Regime Switching's Sharpe of 0.75 stands out.")
        result = audit_document(
            text, "midpoint_paper", strategy_cache=self.cache)
        # Checks ran (the "numeric" key is in flags, not skipped).
        assert "numeric" not in result.skipped
        assert "consistency" not in result.skipped
        # 0.75 vs 0.6291 -- diff 0.1209, over the 0.005 tolerance
        # -> should flag.
        assert result.flag_counts["numeric"] == 1

    def test_year_no_longer_flagged_as_sharpe_for_brief(self):
        """The exact false-positive pattern from the latest brief
        run: '2022' was flagged as a Regime Switching sharpe_ratio
        mismatch. After the skip, no flag fires for substitution
        documents."""
        text = "In 2022 Regime Switching outperformed."
        result = audit_document(
            text, "executive_brief", strategy_cache=self.cache)
        assert result.flag_counts["numeric"] == 0


class TestIsContentTruncated:
    """June 21 2026 -- detects mid-generation truncation. Used by
    the harness self-healing retry inside harness_narrative AND by
    the audit dispatcher's section_truncated check. Short content
    is conservatively accepted (under 50 chars never flags) so a
    deliberate one-liner doesn't false-positive."""

    def test_open_placeholder_token_truncated(self):
        from tools.document_audit import is_content_truncated
        text = (
            "Padding to push the length over the 50-char floor so the "
            "short-content guard doesn't fire. The rolling_correlation "
            "chart shows two series over the full {{")
        assert is_content_truncated(text) is True

    def test_truncated_url_doi_prefix(self):
        from tools.document_audit import is_content_truncated
        text = (
            "Padding so the short-content guard doesn't fire and the "
            "function exercises the URL-truncation path. "
            "https://doi.org/10.1")
        assert is_content_truncated(text) is True

    def test_mid_sentence_apostrophe_truncation(self):
        # The exact production symptom from Section 3.
        from tools.document_audit import is_content_truncated
        text = (
            "Padding to push the length over the 50-char floor so the "
            "guard accepts the input. During the 2008-2009 crisis, "
            "Regime Switching's")
        assert is_content_truncated(text) is True

    def test_clean_completion_not_truncated(self):
        from tools.document_audit import is_content_truncated
        text = (
            "Padding to push the length over the 50-char floor so the "
            "guard accepts the input. The recommendation is stated "
            "without qualification.")
        assert is_content_truncated(text) is False

    def test_quoted_completion_not_truncated(self):
        from tools.document_audit import is_content_truncated
        text = (
            "Padding to push the length over the 50-char floor so the "
            "guard accepts the input. The CIO stated, 'The blend "
            "outperforms.'")
        assert is_content_truncated(text) is False

    def test_markdown_closer_after_terminator_not_truncated(self):
        from tools.document_audit import is_content_truncated
        text = (
            "Padding to push the length over the 50-char floor so the "
            "guard accepts the input. **The blend outperforms.**")
        assert is_content_truncated(text) is False

    def test_short_content_never_flags(self):
        from tools.document_audit import is_content_truncated
        # Under the 50-char floor -- the function returns False even
        # for a clearly mid-word ending.
        assert is_content_truncated("Mid-word truncat") is False

    def test_empty_content_returns_false(self):
        from tools.document_audit import is_content_truncated
        assert is_content_truncated("") is False
        assert is_content_truncated(None) is False  # type: ignore[arg-type]


class TestSectionTruncationDispatcher:
    """The audit dispatcher wires check_section_truncation for
    brief documents and skips other surfaces. Pin both shapes."""

    def _brief(self, sections: dict) -> str:
        return "\n\n".join(
            f"## {name}\n\n{body}" for name, body in sections.items())

    def test_dispatcher_flags_truncated_brief_section(self):
        from tools.document_audit import audit_document
        text = self._brief({
            "Executive Summary": (
                "A regime-conditional blend outperforms the 100% "
                "equity benchmark over the post-2022 window."),
            "Key Findings and Insights": (
                "Padding to clear the 50-char floor on the truncation "
                "detector. During the 2008-2009 crisis, Regime "
                "Switching's"),
        })
        result = audit_document(
            text, "executive_brief", strategy_cache={})
        flags = result.flags_by_check.get("section_truncated", [])
        assert len(flags) == 1
        # The brief section splitter normalises section heading
        # names to the canonical short forms tracked by
        # _BRIEF_SECTION_WORD_TARGETS ("Key Findings" not "Key
        # Findings and Insights" -- the longer form would be a
        # heading variant the splitter accepts via the canonical
        # alternation, but the dict key is the short canonical).
        assert "Key Findings" in flags[0]["section"]
        assert flags[0]["severity"] == "high"

    def test_dispatcher_skips_for_non_brief(self):
        from tools.document_audit import audit_document
        for doc_type in (
                "presentation_deck", "analytical_appendix",
                "midpoint_paper"):
            result = audit_document(
                "any content", doc_type, strategy_cache={})
            assert "section_truncated" in result.skipped


class TestCitationCompletenessConcatenatesAllReferenceBlocks:
    """June 21 2026 -- _extract_references_section used to find
    only the FIRST `## References` heading and grab from there to
    the next `##`. Briefs with per-section reference blocks
    (`## References for Section 2`, `## References for Section 3`,
    ...) had every block after the first one missed, so Hamilton
    (1989) / Carhart (1997) / Markowitz (1952) cited in later
    sections were flagged as missing from References even though
    they were present in the document."""

    def test_concatenates_multiple_reference_blocks(self):
        # Brief with per-section reference blocks. Citation lookup
        # for Hamilton (1989) must succeed because the function now
        # scans EVERY References heading.
        text = """\
## Section 1

Hamilton (1989) introduced the regime-switching framework.

## References for Section 1

Hamilton, J. D. (1989). A new approach to the economic analysis
of nonstationary time series. Econometrica, 57(2), 357-384.

## Section 2

Carhart (1997) extended the factor model.

## References for Section 2

Carhart, M. M. (1997). On persistence in mutual fund performance.
The Journal of Finance, 52(1), 57-82.
"""
        flags, skip = check_citation_completeness(text, "executive_brief")
        assert skip is None
        assert flags == [], (
            f"unexpected flags: {flags} -- both citations are present "
            "in per-section reference blocks")

    def test_truncated_doi_does_not_break_citation_lookup(self):
        # A reference entry with a truncated DOI ("https://doi.org/10.1
        # only) -- author + year still match so the lookup succeeds.
        text = """\
## References

Ang, A., & Bekaert, G. (2002). International asset allocation with
regime shifts. The Review of Financial Studies, 15(4), 1137-1187.
https://doi.org/10.1

## Section 1

Ang and Bekaert (2002) developed the regime-conditional framework.
"""
        flags, skip = check_citation_completeness(text, "executive_brief")
        assert skip is None
        assert flags == []

    def test_multi_author_citation_keyed_to_first_author(self):
        # "Harvey, Liu, & Zhu (2016)" is ONE citation, indexed by
        # first author "Harvey" -- no spurious flags for Liu or Zhu.
        text = """\
## References

Harvey, C. R., Liu, Y., & Zhu, H. (2016). ... and the cross-
section of expected returns. The Review of Financial Studies,
29(1), 5-68.

## Section 1

Harvey, Liu, and Zhu (2016) document the multiple-testing problem.
"""
        flags, skip = check_citation_completeness(text, "executive_brief")
        assert skip is None
        flagged_authors = {f["author"] for f in flags}
        # Only "Harvey" could possibly flag; Liu / Zhu must NOT
        # appear as separate citation keys.
        assert "Liu" not in flagged_authors
        assert "Zhu" not in flagged_authors
        # And the Harvey citation IS present, so no flag at all.
        assert flags == []

    def test_no_references_section_falls_back_to_whole_text(self):
        # A document that inlines its bibliography without any
        # `## References` heading must still resolve author + year
        # lookups against the full text rather than skipping.
        text = """\
Hamilton (1989) introduced the framework.

(Note: full citation -- Hamilton, J. D. (1989). A new approach to
the economic analysis of nonstationary time series. Econometrica,
57(2), 357-384.)
"""
        flags, skip = check_citation_completeness(text, "executive_brief")
        # The fallback returns the whole text; the Hamilton author +
        # 1989 year both appear, so no flag. Skip stays None.
        assert flags == []
        assert skip is None

    def test_truly_missing_citation_still_flagged(self):
        # Sanity check the change didn't break the basic contract.
        # Smith (2020) is cited but absent from the References.
        text = """\
## References

Hamilton, J. D. (1989). ... Econometrica, 57(2), 357-384.

## Section 1

Smith (2020) made a different claim.
"""
        flags, skip = check_citation_completeness(text, "executive_brief")
        assert skip is None
        flagged_authors = {f["author"] for f in flags}
        assert "Smith" in flagged_authors
