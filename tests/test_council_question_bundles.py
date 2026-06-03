"""tests/test_council_question_bundles.py — classifier + bundles.

Covers tools/council_question_bundles.py (keyword classifier + 5
bundle resolvers) and tools/council_direction_extractor.py (keyword
direction + refined alignment score). No DB / no API — pure-Python
unit tests.

The five-bundle classifier is the highest-impact contract — every
production query depends on it picking the right bundle (or falling
back cleanly). Each rule from the design doc is asserted explicitly:

  - >= 2 distinct hits + zero hits elsewhere = confident match
  - 1 hit anywhere = fall back to None
  - tie across multiple bundles = fall back to None
  - empty / malformed query = None, never raises

The bundle resolvers are smoke-tested against an empty test env
where every cache read fails open. Each one must return None on a
cold cache rather than {}, so the upstream fallback chain in main.py
detects "nothing in cache" cleanly.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")

from tools.council_direction_extractor import (  # noqa: E402
    DIRECTION_BALANCED, DIRECTION_DEFENSIVE, DIRECTION_RISK_ON,
    alignment_score, extract_direction,
)
from tools.council_question_bundles import (  # noqa: E402
    QUESTION_TYPE_FORWARD, QUESTION_TYPE_RECOMMENDATION,
    QUESTION_TYPE_REGIME, QUESTION_TYPE_RISK, QUESTION_TYPE_STATISTICAL,
    classify_question, recommendation_bundle, regime_bundle,
    resolve_bundle, risk_bundle, statistical_bundle, forward_bundle,
)


# ── Classifier ────────────────────────────────────────────────────────────


class TestClassifyQuestion:
    """The disjoint-evidence rule: a bundle fires when it scored >= 2
    keyword hits AND every other bundle scored zero. Everything else
    falls back to None and the upstream code uses the wider page
    bundle (or no context)."""

    def test_regime_question_classifies_as_regime(self):
        # Two keywords from the REGIME set: "regime" and "hmm".
        q = "What is the current HMM regime and the macro signal?"
        assert classify_question(q) == QUESTION_TYPE_REGIME

    def test_recommendation_question_classifies(self):
        # "recommendation" + "allocation" — both in the set.
        q = "What is your allocation recommendation given the data?"
        assert classify_question(q) == QUESTION_TYPE_RECOMMENDATION

    def test_risk_question_classifies(self):
        # "drawdown" + "tail" — both in the set.
        q = "What's the worst drawdown and tail risk for the blend?"
        assert classify_question(q) == QUESTION_TYPE_RISK

    def test_statistical_question_classifies(self):
        # "statistically" + "significant" — both in the set.
        q = "Is the outperformance statistically significant?"
        assert classify_question(q) == QUESTION_TYPE_STATISTICAL

    def test_forward_question_classifies(self):
        # "forward" + "outlook" — both in the set.
        q = "Give me the forward outlook for the next quarter."
        assert classify_question(q) == QUESTION_TYPE_FORWARD

    def test_single_hit_falls_back_to_none(self):
        # Only one regime-keyword and nothing else — below threshold.
        q = "Show me the regime."
        assert classify_question(q) is None

    def test_split_evidence_falls_back_to_none(self):
        # Two regime hits, one risk hit → not disjoint → None.
        q = "Is the bear regime driving a deeper drawdown?"
        assert classify_question(q) is None

    def test_zero_hits_falls_back(self):
        q = "Hello, how are the markets today?"
        assert classify_question(q) is None

    def test_empty_query_returns_none(self):
        assert classify_question("") is None
        assert classify_question(None) is None      # type: ignore[arg-type]

    def test_classifier_is_case_insensitive(self):
        q = "REGIME and HMM"
        assert classify_question(q) == QUESTION_TYPE_REGIME

    def test_multi_word_keyword_matches(self):
        # "yield curve" + "credit spread" — both multi-word keywords.
        q = "How does the yield curve and credit spread inform regime?"
        # Three regime hits ("yield curve", "credit spread", "regime"),
        # zero elsewhere. Confident regime classification.
        assert classify_question(q) == QUESTION_TYPE_REGIME

    def test_hyphenated_keyword_matches(self):
        # "risk-on" — explicitly hyphenated keyword in RECOMMENDATION
        # bundle's set. Spec says risk_on is the DIRECTION, but the
        # classifier set includes "risk-on" as a strong signal for
        # an allocation-direction question. Pair with another
        # recommendation keyword.
        q = "Should we go risk-on with this allocation recommendation?"
        assert classify_question(q) == QUESTION_TYPE_RECOMMENDATION


# ── Bundle resolvers (cold-cache fail-open) ───────────────────────────────


class TestBundleResolvers:
    """Resolver-level contract tests. The bundle resolvers must
    NEVER raise — every cache read is wrapped in try/except and
    returns None on failure. They MUST also include the "always-on"
    pieces every call site relies on:

      - RECOMMENDATION carries a constraints block sourced from
        config.py (synchronous, always-available).
      - STATISTICAL carries the fixed-copy FDR / sample-size note
        so the agent answers "is X significant?" with the project's
        standard framing regardless of cache state.

    REGIME / RISK / FORWARD may legitimately return a partially-
    populated dict in the test env when the live regime read
    succeeds (FRED is hit live) but the analytics caches are cold.
    The contract is "no raise" — what's inside the dict varies with
    cache state and is verified end-to-end in test_council_endpoint
    integration tests."""

    @pytest.mark.asyncio
    async def test_regime_bundle_does_not_raise(self):
        # May return None (cold) or a partial dict (live regime read
        # succeeded but analytics caches are cold). Either is fine —
        # the contract is "doesn't raise".
        out = await regime_bundle()
        assert out is None or isinstance(out, dict)

    @pytest.mark.asyncio
    async def test_recommendation_bundle_always_has_constraints(self):
        # RECOMMENDATION's constraints sub-block reads from config.py
        # synchronously — present even when no analytics caches are
        # warm. So this bundle always returns a non-None dict.
        out = await recommendation_bundle()
        assert out is not None
        assert "constraints" in out
        assert "min_weight" in out["constraints"]

    @pytest.mark.asyncio
    async def test_risk_bundle_cold_cache_returns_none(self):
        # No live signal in the risk bundle — it's pure cache reads
        # (strategy_results_cache + crisis_performance metric +
        # academic_analytics factor_loadings). All cold in test env.
        assert await risk_bundle() is None

    @pytest.mark.asyncio
    async def test_statistical_bundle_always_has_fdr_note(self):
        # STATISTICAL embeds a fixed-copy FDR / sample-size statement
        # so the agent answers "is X significant?" with the project's
        # standard framing even when the cache is cold. Always present.
        out = await statistical_bundle()
        assert out is not None
        assert "fdr_and_sample_size_note" in out
        assert "Benjamin et al." in out["fdr_and_sample_size_note"]
        assert "p < 0.005" in out["fdr_and_sample_size_note"]

    @pytest.mark.asyncio
    async def test_forward_bundle_does_not_raise(self):
        # Same as regime_bundle — the live regime read may
        # contribute, the forward_projection cache read won't.
        out = await forward_bundle()
        assert out is None or isinstance(out, dict)

    @pytest.mark.asyncio
    async def test_resolve_bundle_unknown_returns_none(self):
        assert await resolve_bundle("not_a_real_bundle") is None
        assert await resolve_bundle("full") is None
        assert await resolve_bundle("baseline_full") is None


# ── Direction extraction ─────────────────────────────────────────────────


class TestExtractDirection:
    """Keyword-based extraction from the synthesis prose. The
    extractor never raises — every input maps to one of risk_on /
    defensive / balanced."""

    def test_risk_on_phrase(self):
        text = "We recommend an overweight equity stance for the next quarter."
        assert extract_direction(text) == DIRECTION_RISK_ON

    def test_defensive_phrase(self):
        text = "Time to shift to bonds and reduce equity risk."
        # "shift to bonds" + "reduce equity" — both defensive hits.
        assert extract_direction(text) == DIRECTION_DEFENSIVE

    def test_balanced_phrase(self):
        text = "Maintain a balanced allocation across equity and bonds."
        assert extract_direction(text) == DIRECTION_BALANCED

    def test_no_keywords_returns_balanced(self):
        text = "Markets are interesting but no strong view."
        assert extract_direction(text) == DIRECTION_BALANCED

    def test_empty_returns_balanced(self):
        assert extract_direction("") == DIRECTION_BALANCED
        assert extract_direction(None) == DIRECTION_BALANCED

    def test_tie_between_risk_on_and_defensive_resolves_to_balanced(self):
        # A hedging synthesis that mentions both directions
        # symmetrically should resolve to balanced — the synthesis is
        # not taking a clear side.
        text = ("Consider an overweight equity stance while also "
                "increasing bonds for defensive ballast.")
        assert extract_direction(text) == DIRECTION_BALANCED


# ── Refined alignment score ──────────────────────────────────────────────


class TestAlignmentScore:
    """The June 3 2026 refinement: final_score = base * confidence.
    base = 1.0 on clean match, 0.0 on clean mismatch, 0.5 on
    TRANSITION / balanced. confidence clamped to [0, 1].

    The refined formula replaces the older 0/0.5/1.0 ladder so a
    correct call in a 95%-confidence regime scores 0.95 — directly
    proportional to the regime's strength."""

    def test_bull_plus_risk_on_high_confidence(self):
        # Clean match × high confidence = high score.
        assert alignment_score(DIRECTION_RISK_ON, "BULL", 0.95) == 0.95

    def test_bear_plus_defensive_high_confidence(self):
        assert alignment_score(DIRECTION_DEFENSIVE, "BEAR", 0.92) == 0.92

    def test_bull_plus_defensive_mismatch_zeros_out(self):
        # Clean mismatch * any confidence = 0.
        assert alignment_score(DIRECTION_DEFENSIVE, "BULL", 0.95) == 0.0

    def test_bear_plus_risk_on_mismatch_zeros_out(self):
        assert alignment_score(DIRECTION_RISK_ON, "BEAR", 0.95) == 0.0

    def test_transition_regime_caps_at_half_score(self):
        # No regime to align to → base 0.5 regardless of direction.
        # High confidence in a TRANSITION still scores 0.5 * conf.
        assert alignment_score(
            DIRECTION_RISK_ON, "TRANSITION", 0.8) == 0.4

    def test_balanced_direction_caps_at_half_score(self):
        # Balanced recommendation → base 0.5 regardless of regime.
        assert alignment_score(
            DIRECTION_BALANCED, "BULL", 0.95) == round(0.5 * 0.95, 4)

    def test_low_confidence_proportionally_dampens(self):
        # Correct call in a 51%-confidence BULL scores 0.51.
        assert alignment_score(
            DIRECTION_RISK_ON, "BULL", 0.51) == 0.51

    def test_missing_confidence_falls_back_to_half(self):
        # The None-confidence fallback is 0.5 (treated as low conf,
        # not zero). 1.0 base × 0.5 fallback confidence = 0.5.
        assert alignment_score(DIRECTION_RISK_ON, "BULL", None) == 0.5

    def test_missing_direction_treated_as_balanced(self):
        # None direction collapses to balanced → base 0.5.
        assert alignment_score(None, "BULL", 1.0) == 0.5

    def test_unknown_state_treated_as_neutral(self):
        # A garbage regime label resolves to neutral (0.5).
        assert alignment_score(
            DIRECTION_RISK_ON, "ASTROLOGICAL", 1.0) == 0.5

    def test_confidence_clamped_to_unit_interval(self):
        # Defensive guard against upstream data drift — a 1.5 from
        # a stale cache must not produce a 1.5 score.
        assert alignment_score(DIRECTION_RISK_ON, "BULL", 1.5) == 1.0
        assert alignment_score(DIRECTION_RISK_ON, "BULL", -0.5) == 0.0
