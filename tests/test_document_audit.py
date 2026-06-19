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

    def test_no_references_section_skipped(self):
        text = "We cite Sharpe (1994) but there is no references list."
        flags, skip = check_citation_completeness(text, "executive_brief")
        assert flags == []
        assert skip is not None
        assert "References" in skip

    def test_no_citations_no_flag(self):
        text = "## References\n\nSharpe, W. F. (1994). JPM."
        flags, skip = check_citation_completeness(text, "executive_brief")
        assert skip is None
        assert flags == []


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
        text = (
            "Regime Switching achieves a Sharpe of 0.6291 over the period. "
            "We follow Sharpe (1994) in computing the ratio.\n\n"
            "## References\n\n"
            "Sharpe, W. F. (1994). JPM."
        )
        result = audit_document(
            text, "executive_brief", strategy_cache=self.cache)
        assert isinstance(result, AuditResult)
        assert result.flag_counts["total"] == 0
        assert not result.has_any_flag

    def test_document_with_flags_aggregates_counts(self):
        text = (
            "Regime Switching's Sharpe of 0.75 is wrong. "      # numeric
            "It also has the lowest drawdown.\n"                  # direction
            "Bailey et al. (2014) supports the approach.\n\n"     # citation
            "## References\n\nSharpe (1994). JPM."
        )
        result = audit_document(
            text, "executive_brief", strategy_cache=self.cache)
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
