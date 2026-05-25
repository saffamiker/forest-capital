"""Tests for tools/citation_sourcing.py — the multi-layered citation
sourcing foundation (May 24 2026).

Pins the spec's invariants:
  - Trust flag is always one of TRUST_FLAGS (enum-restricted)
  - confidence_score is always in [0.0, 1.0] (clamped)
  - generate_queries never raises on malformed input
  - score_citation never raises on missing fields
  - The 40/35/15/10 weight contract holds across all paths
  - Every trust-flag variant is reachable through the classifier

The user's guardrails explicitly require:
  - Test scoring with a perfect citation (expect score >= 0.75)
  - Test scoring with a blog/low-quality source (expect score < 0.50)
  - Test query generator with a complete finding payload
  - Test query generator with a null/missing evidence field (must
    not throw)
  - Test confidence_score clamping at boundaries (0.0 and 1.0)
  - Test each trust flag variant is reachable
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


# ── Enums + module-level constants ────────────────────────────────────────────

class TestEnums:
    """Trust flags and citation types are enum-restricted constants —
    never free-text. A regression that admits a stray string would
    bypass the Citation Review panel's grouping + filter logic."""

    def test_trust_flag_set_is_exactly_five(self):
        from tools.citation_sourcing import TRUST_FLAGS
        assert TRUST_FLAGS == frozenset({
            "verified", "unverified", "paywalled", "stale", "mismatch",
        })

    def test_citation_type_set_is_exactly_four(self):
        from tools.citation_sourcing import CITATION_TYPES
        assert CITATION_TYPES == frozenset({
            "theoretical", "empirical", "methodological", "practitioner",
        })

    def test_weights_sum_to_one(self):
        from tools.citation_sourcing import (
            WEIGHT_PUBLICATION, WEIGHT_RELEVANCE,
            WEIGHT_RECENCY, WEIGHT_VERIFIABILITY,
        )
        total = (WEIGHT_PUBLICATION + WEIGHT_RELEVANCE
                 + WEIGHT_RECENCY + WEIGHT_VERIFIABILITY)
        assert abs(total - 1.0) < 1e-9

    def test_fallback_score_is_conservative(self):
        # The fallback fail-open shape is 0.0 + unverified so a
        # failed scoring call never crosses the 0.50 surface
        # threshold the Citation Review panel applies.
        from tools.citation_sourcing import FALLBACK_SCORE
        assert FALLBACK_SCORE["confidence_score"] == 0.0
        assert FALLBACK_SCORE["trust_flag"] == "unverified"


# ── score_citation — clamping + determinism ──────────────────────────────────

class TestScoringClamping:
    """confidence_score is clamped to [0.0, 1.0] regardless of
    inputs. A regression that allowed an overflow would break the
    Citation Review panel's 0.75 / 0.50 threshold gates."""

    def test_perfect_inputs_dont_exceed_one(self):
        # Top-tier peer-reviewed journal + same-hypothesis same-asset
        # + 2024 + DOI resolved → would otherwise sum to >1.0 with
        # the publication bonus. Clamp must hold.
        from tools.citation_sourcing import score_citation
        result = score_citation(
            {
                "journal_or_institution": "Journal of Finance",
                "year": 2024,
                "doi": "10.1111/jofi.12345",
                "doi_resolved": True,
                "url": "https://doi.org/10.1111/jofi.12345",
                "url_status": "live",
                "citation_type": "empirical",
            },
            {"relevance_tier": "same_hypothesis_same_asset_class"},
        )
        assert 0.0 <= result["confidence_score"] <= 1.0

    def test_worst_inputs_dont_go_below_zero(self):
        from tools.citation_sourcing import score_citation
        result = score_citation({}, {})
        assert 0.0 <= result["confidence_score"] <= 1.0


class TestScoringDeterminism:
    """Same inputs always produce the same output. No randomness,
    no clock-dependence, no LLM call."""

    def test_repeated_calls_are_identical(self):
        from tools.citation_sourcing import score_citation
        citation = {
            "journal_or_institution": "Journal of Asset Management",
            "year": 2023, "url_status": "live",
            "doi": "10.1057/s41260-023-00350-z",
            "doi_resolved": True,
        }
        context = {"relevance_tier": "same_hypothesis_same_asset_class"}
        a = score_citation(citation, context)
        b = score_citation(citation, context)
        c = score_citation(citation, context)
        assert a == b == c


class TestScoringPerfectCitation:
    """A top-tier peer-reviewed paper, 2024, DOI resolved, exact
    hypothesis match: confidence_score must be >= 0.75 per the
    user's guardrail."""

    def test_perfect_empirical_scores_above_threshold(self):
        from tools.citation_sourcing import score_citation
        result = score_citation(
            {
                "journal_or_institution": "Journal of Finance",
                "year": 2024,
                "doi": "10.1111/jofi.12345",
                "doi_resolved": True,
                "url_status": "live",
                "citation_type": "empirical",
            },
            {"relevance_tier": "same_hypothesis_same_asset_class"},
        )
        assert result["confidence_score"] >= 0.75


class TestScoringBlogSource:
    """A blog / explainer with no DOI, generic relevance: score
    must be < 0.50 (the Citation Review surface threshold) so the
    Review panel never offers it as a candidate."""

    def test_blog_scores_below_threshold(self):
        from tools.citation_sourcing import score_citation
        result = score_citation(
            {
                "journal_or_institution": "Seeking Alpha",
                "url": "https://seekingalpha.com/article/123",
                "year": 2020,
                "url_status": "live",
                "citation_type": "empirical",
            },
            {"relevance_tier": "tangential"},
        )
        # Blog publication score (0.30) + tangential relevance (0.25)
        # + recency 2020 (0.80) + verifiability live (0.80)
        # = 0.30*.4 + 0.25*.35 + 0.80*.15 + 0.80*.10
        # = 0.12 + 0.0875 + 0.12 + 0.08
        # = 0.4075 < 0.50 ✓
        assert result["confidence_score"] < 0.50


# ── score_citation — never raises on missing fields ──────────────────────────

class TestScoringMissingFields:
    """The scoring function must never raise on missing / null
    fields. Every missing field substitutes a conservative default."""

    def test_completely_empty_citation_returns_valid_shape(self):
        from tools.citation_sourcing import score_citation, TRUST_FLAGS
        result = score_citation({}, {})
        assert 0.0 <= result["confidence_score"] <= 1.0
        assert result["trust_flag"] in TRUST_FLAGS
        assert isinstance(result["scoring_rationale"], str)

    def test_none_citation_returns_valid_shape(self):
        from tools.citation_sourcing import score_citation, TRUST_FLAGS
        result = score_citation(None, None)  # type: ignore[arg-type]
        assert 0.0 <= result["confidence_score"] <= 1.0
        assert result["trust_flag"] in TRUST_FLAGS

    def test_unknown_year_falls_back_to_conservative_recency(self):
        from tools.citation_sourcing import score_citation
        result = score_citation(
            {"year": "not-a-year"},
            {"relevance_tier": "related_hypothesis"},
        )
        # Conservative midpoint for unknown year — function must not raise.
        assert 0.0 <= result["confidence_score"] <= 1.0

    def test_unknown_citation_type_defaults_to_theoretical(self):
        from tools.citation_sourcing import score_citation
        # Unknown type should not raise; the rationale should
        # surface 'theoretical' (the safe default).
        result = score_citation(
            {"citation_type": "nonsense"},
            {"relevance_tier": "related_hypothesis"},
        )
        assert "theoretical" in result["scoring_rationale"]


# ── Trust-flag reachability ──────────────────────────────────────────────────

class TestTrustFlagReachability:
    """Every flag in TRUST_FLAGS must be reachable through some
    combination of inputs. A flag the classifier can never assign
    would be dead code in the panel."""

    def test_verified_is_reachable_via_doi_resolved(self):
        from tools.citation_sourcing import score_citation
        result = score_citation(
            {"year": 2024, "doi_resolved": True, "url_status": "live"},
            {"relevance_tier": "same_hypothesis_same_asset_class"},
        )
        assert result["trust_flag"] == "verified"

    def test_verified_is_reachable_via_url_live(self):
        from tools.citation_sourcing import score_citation
        result = score_citation(
            {"year": 2024, "url_status": "live"},
            {"relevance_tier": "same_hypothesis_same_asset_class"},
        )
        assert result["trust_flag"] == "verified"

    def test_paywalled_is_reachable(self):
        from tools.citation_sourcing import score_citation
        result = score_citation(
            {"year": 2024, "url_status": "paywalled"},
            {"relevance_tier": "related_hypothesis"},
        )
        assert result["trust_flag"] == "paywalled"

    def test_stale_is_reachable_for_pre_2015_non_theoretical(self):
        from tools.citation_sourcing import score_citation
        result = score_citation(
            {"year": 2010, "url_status": "live",
             "citation_type": "empirical"},
            {"relevance_tier": "related_hypothesis"},
        )
        assert result["trust_flag"] == "stale"

    def test_stale_is_NOT_assigned_to_theoretical_pre_2015(self):
        # Pre-2015 is ACCEPTABLE for theoretical type — the seminal
        # Markowitz 1952 / Sharpe 1966 references must not flag stale.
        from tools.citation_sourcing import score_citation
        result = score_citation(
            {"year": 1952, "url_status": "live",
             "citation_type": "theoretical"},
            {"relevance_tier": "same_hypothesis_same_asset_class"},
        )
        assert result["trust_flag"] != "stale"

    def test_mismatch_is_reachable_via_tangential(self):
        from tools.citation_sourcing import score_citation
        result = score_citation(
            {"year": 2024, "url_status": "live"},
            {"relevance_tier": "tangential"},
        )
        assert result["trust_flag"] == "mismatch"

    def test_mismatch_is_reachable_via_explicit_flag(self):
        from tools.citation_sourcing import score_citation
        result = score_citation(
            {"year": 2024, "url_status": "live", "explicit_mismatch": True},
            {"relevance_tier": "related_hypothesis"},
        )
        assert result["trust_flag"] == "mismatch"

    def test_unverified_is_the_fallback(self):
        from tools.citation_sourcing import score_citation
        # No DOI, no url_status, not tangential, not stale → unverified.
        result = score_citation(
            {"year": 2024, "journal_or_institution": "Some Journal"},
            {"relevance_tier": "related_hypothesis"},
        )
        assert result["trust_flag"] == "unverified"


# ── generate_queries — graceful handling ─────────────────────────────────────

class TestGenerateQueriesGraceful:
    """The query generator must never raise on missing / null fields.
    A finding payload missing 'evidence' must still produce queries
    (falling back to title-only when needed)."""

    def test_complete_finding_returns_four_queries(self):
        # In the test environment the LLM is not invoked; the
        # function falls back to the title-only generator and
        # returns all four citation types keyed off the title.
        from tools.citation_sourcing import (
            generate_queries, CITATION_TYPES,
        )
        finding = {
            "title": "REGIME SHIFT EVIDENCE",
            "finding": "Regime switching beats benchmark post-2022.",
            "implication": "Tactical allocation justified.",
            "evidence": [
                "Sharpe pre-2022: 0.51 vs 0.48",
                "Sharpe post-2022: 0.65 vs 0.32",
            ],
            "nugget_strength": "HIGH",
        }
        queries = generate_queries(finding)
        assert isinstance(queries, dict)
        # Title-only fallback produces ALL four keys.
        assert set(queries.keys()) == CITATION_TYPES
        # Every query is a non-empty string.
        for k, v in queries.items():
            assert isinstance(v, str) and v.strip(), f"{k} is empty"

    def test_missing_evidence_field_does_not_throw(self):
        from tools.citation_sourcing import generate_queries
        # No evidence field at all — must not raise.
        queries = generate_queries({
            "title": "TITLE ONLY",
            "finding": "X.",
            "implication": "Y.",
        })
        # Title-only fallback still produces queries.
        assert isinstance(queries, dict)
        assert len(queries) > 0

    def test_null_finding_returns_empty_dict_without_raising(self):
        from tools.citation_sourcing import generate_queries
        assert generate_queries(None) == {}  # type: ignore[arg-type]
        assert generate_queries({}) == {}

    def test_finding_without_title_returns_empty_dict(self):
        # When even the title is missing the title-only fallback
        # has nothing to work with — return {} so the caller's
        # "skip this finding" branch fires cleanly.
        from tools.citation_sourcing import generate_queries
        result = generate_queries({"finding": "x", "implication": "y"})
        assert result == {}

    def test_query_text_is_title_derived(self):
        # The fallback queries include the title text (lowercased)
        # in every query string — a regression that dropped the
        # title would make every query identical.
        from tools.citation_sourcing import generate_queries
        finding = {"title": "REGIME SHIFT EVIDENCE"}
        queries = generate_queries(finding)
        for v in queries.values():
            assert "regime shift evidence" in v


# ── _parse_query_response — defensive JSON parsing ───────────────────────────

class TestParseQueryResponse:
    """The LLM may wrap its output in a markdown code fence, return
    non-JSON, or hit a key the spec does not allow. The parser
    must handle every malformed shape without raising."""

    def test_clean_json_parses(self):
        from tools.citation_sourcing import _parse_query_response
        raw = (
            '{"theoretical": "X", "empirical": "Y", '
            '"methodological": "Z", "practitioner": "W"}'
        )
        out = _parse_query_response(raw)
        assert out == {"theoretical": "X", "empirical": "Y",
                       "methodological": "Z", "practitioner": "W"}

    def test_markdown_fence_is_stripped(self):
        from tools.citation_sourcing import _parse_query_response
        raw = '```json\n{"theoretical": "X"}\n```'
        out = _parse_query_response(raw)
        assert out == {"theoretical": "X"}

    def test_non_json_returns_empty(self):
        from tools.citation_sourcing import _parse_query_response
        assert _parse_query_response("hello not json") == {}

    def test_empty_string_returns_empty(self):
        from tools.citation_sourcing import _parse_query_response
        assert _parse_query_response("") == {}

    def test_extra_keys_are_dropped(self):
        # The LLM might include an extra key (e.g. 'commentary').
        # Restrict to the four valid types so a downstream consumer
        # never sees an unrecognised tag.
        from tools.citation_sourcing import _parse_query_response
        raw = (
            '{"theoretical": "X", "commentary": "drop me", '
            '"empirical": "Y"}'
        )
        out = _parse_query_response(raw)
        assert out == {"theoretical": "X", "empirical": "Y"}


# ── Pipeline-wiring guardrail ────────────────────────────────────────────────

class TestNoPipelineWiring:
    """The module is foundation-only in this PR. The user's guardrail
    explicitly says: 'citation_sourcing.py must not be imported by
    or called from source_citations endpoint or any pipeline step.
    No changes to existing pipeline files -- new module only. Flag
    any import of citation_sourcing outside of tests as a review
    blocker.'

    This test enforces the rule by scanning every existing backend
    file for an import of citation_sourcing. The new module file
    itself + the test file are allowed; nothing else is."""

    def test_no_existing_backend_file_imports_citation_sourcing(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        backend = root / "backend"
        offenders: list[str] = []
        for path in backend.rglob("*.py"):
            # Skip the module itself + __pycache__.
            if "__pycache__" in path.parts:
                continue
            if path.name == "citation_sourcing.py":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
            if "citation_sourcing" in text:
                offenders.append(str(path.relative_to(root)))
        assert not offenders, (
            f"citation_sourcing imported from existing backend files "
            f"(review blocker per user guardrail): {offenders}"
        )
