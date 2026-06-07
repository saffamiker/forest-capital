"""
tests/test_academic_review_score.py — May 25 2026.

Pins the academic_review_score parser. The arbiter writes five
`### N. Section\n**Rating:** Strong | Developing | Needs Work` blocks
and the editor surfaces a single 0-10 score derived from those
section ratings. The parser is pure and runs without a DB.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from tools.academic_review_score import (  # noqa: E402
    ADVISORY_THRESHOLD, compute_review_score,
)


# A representative arbiter verdict — all five sections rated, mixed
# levels. Used as the happy-path input across the parse / score /
# rating tests so a change to the mapping is caught in one place.
_VERDICT_MIXED = """\
### 1. Data Sufficiency and Methodology

**Rating:** Strong

The data layer is well-documented...

### 2. Requirements and Rubric Alignment

**Rating:** Developing

Section 3 (Roles) still carries the AI pre-seed callout...

### 3. Deliverable Quality

**Rating:** Developing

Prose is academic but Section 2 reads thin on interpretation...

### 4. Priority Areas for Further Investigation

**Rating:** Needs Work

The midpoint paper omits the FDR-corrected p-value framing...

### 5. Overall Academic Readiness

**Rating:** Developing

The paper is on track for submission but needs the Section 4 issue resolved.
"""


class TestParseSectionRatings:
    """Every section's rating is parsed independently — a malformed
    one is silently skipped so a single broken heading does not
    zero out the others."""

    def test_parses_all_five_section_ratings(self):
        result = compute_review_score(_VERDICT_MIXED)
        sr = result["section_ratings"]
        assert sr["data_sufficiency"] == "Strong"
        assert sr["requirements"] == "Developing"
        assert sr["deliverable"] == "Developing"
        assert sr["investigation"] == "Needs Work"
        assert sr["readiness"] == "Developing"
        assert result["sections_rated"] == 5

    def test_normalises_rating_token_variants(self):
        """'Needs work' lowercase and 'Needs-Work' hyphenated both
        parse to the canonical 'Needs Work' — the arbiter occasionally
        drifts between these forms."""
        verdict = """\
### 1. A

**Rating:** Needs work

### 2. B

**Rating:** Needs-Work

### 3. C

**Rating:** STRONG

### 4. D

**Rating:** developing

### 5. E

**Rating:** Strong
"""
        sr = compute_review_score(verdict)["section_ratings"]
        assert sr["data_sufficiency"] == "Needs Work"
        assert sr["requirements"] == "Needs Work"
        assert sr["deliverable"] == "Strong"
        assert sr["investigation"] == "Developing"
        assert sr["readiness"] == "Strong"

    def test_skips_unrecognised_ratings_without_zeroing_others(self):
        verdict = """\
### 1. A

**Rating:** Strong

### 2. B

**Rating:** Incomplete

### 3. C

**Rating:** Developing

### 4. D

**Rating:** Strong

### 5. E

**Rating:** Strong
"""
        result = compute_review_score(verdict)
        # 'Incomplete' is dropped; the other four are scored.
        assert "requirements" not in result["section_ratings"]
        assert result["sections_rated"] == 4


class TestComputeReviewScore:
    """The numeric 0-10 score averages the rated sections; the
    threshold semantics are anchored so a midpoint-advisory test
    can read them from one source of truth."""

    def test_all_strong_scores_8_5(self):
        verdict = "\n".join(
            f"### {i}. Heading {i}\n\n**Rating:** Strong\n"
            for i in range(1, 6))
        result = compute_review_score(verdict)
        assert result["score"] == 8.5
        assert result["rating"] == "Strong"
        assert result["advisory"] is False

    def test_all_developing_scores_exactly_6_0(self):
        """6.0 is the advisory cutoff — 'all Developing' is the
        boundary case. NOT advisory at the threshold itself; the
        advisory only fires when score < 6.0."""
        verdict = "\n".join(
            f"### {i}. Heading {i}\n\n**Rating:** Developing\n"
            for i in range(1, 6))
        result = compute_review_score(verdict)
        assert result["score"] == 6.0
        assert result["advisory"] is False  # equal to threshold, not below

    def test_single_needs_work_drops_below_threshold(self):
        """The threshold's meaning: 'at least one section needs work'.
        One Needs Work + four Developing = 5.5 average → advisory."""
        verdict = (
            "### 1. A\n\n**Rating:** Needs Work\n\n"
            "### 2. B\n\n**Rating:** Developing\n\n"
            "### 3. C\n\n**Rating:** Developing\n\n"
            "### 4. D\n\n**Rating:** Developing\n\n"
            "### 5. E\n\n**Rating:** Developing\n"
        )
        result = compute_review_score(verdict)
        assert result["score"] is not None
        assert result["score"] < ADVISORY_THRESHOLD
        assert result["advisory"] is True

    def test_all_needs_work_scores_3_5(self):
        verdict = "\n".join(
            f"### {i}. Heading {i}\n\n**Rating:** Needs Work\n"
            for i in range(1, 6))
        result = compute_review_score(verdict)
        assert result["score"] == 3.5
        assert result["advisory"] is True

    def test_score_is_rounded_to_one_decimal(self):
        # Three Strong + two Developing → (3*8.5 + 2*6.0) / 5 = 7.5
        verdict = (
            "### 1. A\n\n**Rating:** Strong\n\n"
            "### 2. B\n\n**Rating:** Strong\n\n"
            "### 3. C\n\n**Rating:** Strong\n\n"
            "### 4. D\n\n**Rating:** Developing\n\n"
            "### 5. E\n\n**Rating:** Developing\n"
        )
        result = compute_review_score(verdict)
        assert result["score"] == 7.5

    def test_returns_none_score_on_empty_verdict(self):
        result = compute_review_score("")
        assert result["score"] is None
        assert result["rating"] is None
        assert result["advisory"] is False
        assert result["sections_rated"] == 0

    def test_returns_none_score_on_malformed_verdict(self):
        result = compute_review_score("This is not a verdict — no headings.")
        assert result["score"] is None
        assert result["sections_rated"] == 0


class TestRatingExtraction:
    """The 'rating' field surfaces the section-5 overall rating
    distinct from the numeric score average. Used by the editor
    pill's title attribute."""

    def test_overall_rating_is_section_5(self):
        result = compute_review_score(_VERDICT_MIXED)
        # Section 5 is "Developing" in the fixture.
        assert result["rating"] == "Developing"

    def test_overall_rating_is_none_when_section_5_missing(self):
        verdict = """\
### 1. A

**Rating:** Strong

### 2. B

**Rating:** Strong
"""
        result = compute_review_score(verdict)
        assert result["rating"] is None
        # But the score still averages over the two sections present.
        assert result["score"] == 8.5
        assert result["sections_rated"] == 2


# ── Bridge #82: parse_error vs partial-truncation distinction ─────────

class TestParseErrorFlag:
    """Bridge #82: a non-empty arbiter response that yields zero
    sections must surface as `parse_error=True` so the IN02 finding
    can describe an unparseable response rather than calling it a
    valid zero-section result. A partial response (1-4 sections
    parsed) is NOT a parse error — the parser kept what was there."""

    def test_empty_verdict_is_not_a_parse_error(self):
        result = compute_review_score("")
        assert result["sections_rated"] == 0
        assert result["parse_error"] is False

    def test_none_verdict_is_not_a_parse_error(self):
        result = compute_review_score(None)
        assert result["sections_rated"] == 0
        assert result["parse_error"] is False

    def test_whitespace_only_verdict_is_not_a_parse_error(self):
        # Whitespace only is treated as empty — nothing to fail on.
        result = compute_review_score("   \n\n  \t\n")
        assert result["sections_rated"] == 0
        assert result["parse_error"] is False

    def test_non_empty_response_with_zero_sections_is_a_parse_error(self):
        """The arbiter refused, drifted from the rubric headings, or
        returned an error payload — non-trivial text with no parseable
        sections is exactly the case the IN02 finding must distinguish
        from a clean zero-section result."""
        result = compute_review_score(
            "I cannot fulfill this academic review request at this time. "
            "Please try again later or escalate to a human reviewer.")
        assert result["sections_rated"] == 0
        assert result["parse_error"] is True

    def test_partial_response_is_not_a_parse_error(self):
        """Three sections parsed out of five is a partial result, not
        a parse error. The parser kept what it found — the IN02
        finding describes truncation, not parse failure."""
        verdict = (
            "### 1. A\n\n**Rating:** Strong\n\n"
            "### 2. B\n\n**Rating:** Developing\n\n"
            "### 3. C\n\n**Rating:** Strong\n"
        )
        result = compute_review_score(verdict)
        assert result["sections_rated"] == 3
        assert result["parse_error"] is False

    def test_single_section_parsed_is_not_a_parse_error(self):
        # Even one parseable section is enough that it's not a parse
        # failure — the response was structured correctly, just short.
        verdict = "### 1. A\n\n**Rating:** Strong\n"
        result = compute_review_score(verdict)
        assert result["sections_rated"] == 1
        assert result["parse_error"] is False

    def test_full_five_section_response_is_not_a_parse_error(self):
        result = compute_review_score(_VERDICT_MIXED)
        assert result["sections_rated"] == 5
        assert result["parse_error"] is False
