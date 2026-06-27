"""tests/test_freeze_hash_aware_substitution.py -- June 27 2026.

PR 1 of three. Pins the substitution-table fixes for the deck export
bug Bob reported (slide 7 regime confidence 62.7% instead of the
fresh 95.4%) + the historical-analytics freeze-awareness fix.

ARCHITECTURE REMINDER (operator clarification, June 27 2026):
  * Live CIO + regime tokens (slide 7 + 11 watchpoints, regime
    classification, confidence) are INTENTIONALLY LIVE -- they are
    the platform's live recommendation feature, not frozen
    submission figures. Submission freeze does NOT apply to them.
  * Historical analytics tokens (regime_conditional, factor
    loadings, cost sensitivity, crisis performance) ARE in the
    freeze scope and must read freeze-hash data when the freeze
    is active.

Bug 2 (slide 7 stale 62.7%) root cause:
  The _substitution_cache key tuple did not include CIO row
  identity, so when the live CIO row updated, the cached
  substitution table kept serving the OLD confidence. Fix: add
  the cio row's stable identity (id / recommendation_id /
  computed_at) to the cache key + bump _CACHE_VERSION.

Test groups:

  TestCacheVersionBumped
    _CACHE_VERSION pinned at 6 (was bumped 4 -> 6 in this PR).

  TestCacheKeyIncludesCioIdentity
    _cache_key returns different keys for different cio identities
    + same key when cio unchanged. Live CIO update naturally
    invalidates the cache.

  TestGetSubstitutionTableInvalidatesOnCioChange
    Two get_substitution_table calls with the same data_hash but
    different cio_recommendation rows return DIFFERENT cached
    tables (no stale read).

  TestLoadSubstitutionMetricSourcesHashAware
    Historical analytics IS in freeze scope. When data_hash is
    supplied to load_substitution_metric_sources, all reads route
    through get_metric(data_hash, kind). Without data_hash, falls
    through to get_latest_metric (live path).

  TestBuildSubstitutionTableHashVerifiedFlag
    Defensive: data_hash supplied without hash_verified=True logs
    a structured warning so a regression that reintroduces a non-
    hash-aware historical-analytics read surfaces in operator logs.

  TestLiveCioAndRegimeReadsUnchanged
    Source inspection -- the 3 document generators still call
    get_latest_recommendation() and get_regime_cache() directly.
    Per the operator clarification, live tokens are intentionally
    live; PR 1 must NOT replace these calls.
"""
from __future__ import annotations

import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── Cache version bump ─────────────────────────────────────────────


class TestCacheVersionBumped:

    def test_cache_version_is_6(self):
        from tools.numeric_substitution import _CACHE_VERSION
        assert _CACHE_VERSION == 6


# ── Cache key includes CIO identity ────────────────────────────────


class TestCacheKeyIncludesCioIdentity:

    def test_key_changes_when_cio_id_changes(self):
        from tools.numeric_substitution import _cache_key
        k1 = _cache_key(
            "abc", {},
            cio_recommendation={"id": 100, "regime": "BULL"})
        k2 = _cache_key(
            "abc", {},
            cio_recommendation={"id": 101, "regime": "BULL"})
        assert k1 != k2

    def test_key_changes_when_computed_at_changes(self):
        """recommendation_id is the preferred identity, but
        computed_at is the fallback when the CIO row carries no
        explicit id field."""
        from tools.numeric_substitution import _cache_key
        k1 = _cache_key(
            "abc", {},
            cio_recommendation={
                "computed_at": "2026-06-27T15:00:00Z"})
        k2 = _cache_key(
            "abc", {},
            cio_recommendation={
                "computed_at": "2026-06-27T16:00:00Z"})
        assert k1 != k2

    def test_same_cio_returns_same_key(self):
        from tools.numeric_substitution import _cache_key
        cio = {"id": 100, "regime": "BULL",
               "confidence": {"probability": 0.954}}
        k1 = _cache_key("abc", {}, cio_recommendation=cio)
        k2 = _cache_key("abc", {}, cio_recommendation=cio)
        assert k1 == k2

    def test_none_cio_yields_empty_identity_slot(self):
        from tools.numeric_substitution import _cache_key
        k = _cache_key("abc", {}, cio_recommendation=None)
        assert k[-1] == ""

    def test_cio_without_identifying_fields_yields_empty_slot(self):
        from tools.numeric_substitution import _cache_key
        k = _cache_key(
            "abc", {},
            cio_recommendation={"regime": "BULL"})
        assert k[-1] == ""


# ── get_substitution_table invalidates on CIO change ───────────────


class TestGetSubstitutionTableInvalidatesOnCioChange:

    def test_cio_update_returns_fresh_table(self):
        """The motivating bug: live CIO row updates (confidence
        95.4% -> 62.7%) used to leave the cached substitution table
        holding the STALE value. Now a CIO update naturally
        invalidates the cache."""
        from tools.numeric_substitution import (
            get_substitution_table, _substitution_cache,
        )
        _substitution_cache.clear()

        strat: dict = {}
        cio_v1 = {
            "id": 100,
            "regime": "TRANSITION",
            "confidence": {"probability": 0.954},
        }
        cio_v2 = {
            "id": 101,
            "regime": "TRANSITION",
            "confidence": {"probability": 0.627},
        }

        t1 = get_substitution_table("abc", strat, cio_v1)
        assert "{{REGIME_CONFIDENCE}}" in t1
        assert "95.4%" in t1["{{REGIME_CONFIDENCE}}"]

        t2 = get_substitution_table("abc", strat, cio_v2)
        assert "{{REGIME_CONFIDENCE}}" in t2
        assert "62.7%" in t2["{{REGIME_CONFIDENCE}}"]
        # Different instances + different rendered values.
        assert t1 is not t2
        assert t1["{{REGIME_CONFIDENCE}}"] != t2[
            "{{REGIME_CONFIDENCE}}"]

    def test_same_cio_returns_same_table_instance(self):
        """Cache integrity: brief / deck / appendix with the SAME
        data_hash + SAME cio_row get the SAME dict instance back
        (byte-identical cross-deliverable values)."""
        from tools.numeric_substitution import (
            get_substitution_table, _substitution_cache,
        )
        _substitution_cache.clear()
        cio = {"id": 200, "regime": "BULL",
               "confidence": {"probability": 0.78}}
        t1 = get_substitution_table("def", {}, cio)
        t2 = get_substitution_table("def", {}, cio)
        assert t1 is t2


# ── load_substitution_metric_sources hash-awareness ───────────────


class TestLoadSubstitutionMetricSourcesHashAware:

    @pytest.mark.asyncio
    async def test_with_data_hash_routes_through_get_metric(
            self, monkeypatch):
        from tools.academic_export import (
            load_substitution_metric_sources,
        )
        seen: list[tuple[str, str]] = []

        async def _by_hash(h, k):
            seen.append((h, k))
            if k == "academic_analytics":
                return {"regime_conditional": [],
                        "factor_loadings": []}
            return None

        async def _latest(k):
            raise AssertionError(
                "get_latest_metric MUST NOT fire when data_hash given")

        monkeypatch.setattr(
            "tools.precomputed_analytics.get_metric", _by_hash)
        monkeypatch.setattr(
            "tools.precomputed_analytics.get_latest_metric", _latest)

        await load_substitution_metric_sources(
            data_hash="c421fb895347f924")
        kinds = {k for _h, k in seen}
        assert "academic_analytics" in kinds
        assert "oos_cost_sensitivity" in kinds
        assert "crisis_performance" in kinds
        for h, _k in seen:
            assert h == "c421fb895347f924"

    @pytest.mark.asyncio
    async def test_without_data_hash_falls_through_to_latest(
            self, monkeypatch):
        from tools.academic_export import (
            load_substitution_metric_sources,
        )
        seen: list[str] = []

        async def _latest(k):
            seen.append(k)
            return None

        async def _by_hash(h, k):
            raise AssertionError(
                "get_metric MUST NOT fire when no data_hash given")

        monkeypatch.setattr(
            "tools.precomputed_analytics.get_latest_metric", _latest)
        monkeypatch.setattr(
            "tools.precomputed_analytics.get_metric", _by_hash)

        await load_substitution_metric_sources()
        assert "academic_analytics" in seen
        assert "oos_cost_sensitivity" in seen
        assert "crisis_performance" in seen


# ── build_substitution_table hash_verified flag ───────────────────


class TestBuildSubstitutionTableHashVerifiedFlag:
    """Defensive audit signal -- a non-hash-aware caller passing
    data_hash logs a structured warning so a regression that
    reintroduces non-hash-aware historical-analytics reads
    surfaces in operator logs."""

    def test_data_hash_without_flag_logs_warning(
            self, monkeypatch):
        from tools import numeric_substitution as ns

        events: list[tuple[str, dict]] = []

        class _StubLog:
            def warning(self, event, **kw): events.append((event, kw))
            def info(self, *a, **kw): pass

        monkeypatch.setattr(ns, "log", _StubLog())
        ns.build_substitution_table(
            {}, None, "c421fb895347f924")
        keys = [e[0] for e in events]
        assert "build_substitution_table_hash_unverified" in keys

    def test_data_hash_with_flag_no_warning(self, monkeypatch):
        from tools import numeric_substitution as ns

        events: list[tuple[str, dict]] = []

        class _StubLog:
            def warning(self, event, **kw): events.append((event, kw))
            def info(self, *a, **kw): pass

        monkeypatch.setattr(ns, "log", _StubLog())
        ns.build_substitution_table(
            {}, None, "c421fb895347f924",
            hash_verified=True)
        keys = [e[0] for e in events]
        assert (
            "build_substitution_table_hash_unverified" not in keys)

    def test_empty_data_hash_no_warning_regardless_of_flag(
            self, monkeypatch):
        from tools import numeric_substitution as ns

        events: list[tuple[str, dict]] = []

        class _StubLog:
            def warning(self, event, **kw): events.append((event, kw))
            def info(self, *a, **kw): pass

        monkeypatch.setattr(ns, "log", _StubLog())
        ns.build_substitution_table({}, None, "")
        keys = [e[0] for e in events]
        assert (
            "build_substitution_table_hash_unverified" not in keys)


# ── Live CIO + regime reads are INTENTIONALLY UNCHANGED ───────────


class TestLiveCioAndRegimeReadsUnchanged:
    """Per the operator clarification (June 27 2026):
      - Live CIO + regime tokens are INTENTIONALLY LIVE -- the
        platform's live recommendation feature, NOT frozen
        submission figures.
      - Submission freeze applies ONLY to historical analytics
        tokens (regime_conditional / factor_loadings / cost_
        sensitivity / crisis_performance).
      - This PR must NOT replace get_latest_recommendation() /
        get_regime_cache() in the 3 document generators.

    Pin via source inspection so a future PR that 'fixes' these
    calls under freeze gets caught."""

    def test_doc_generators_still_call_live_loaders(self):
        import inspect
        from main import (
            _generate_brief_document,
            _generate_appendix_document,
            _generate_deck_document,
        )
        for fn in (
            _generate_brief_document,
            _generate_appendix_document,
            _generate_deck_document,
        ):
            src = inspect.getsource(fn)
            assert "get_latest_recommendation" in src, (
                f"{fn.__name__} MUST still call "
                "get_latest_recommendation -- live CIO row is "
                "intentionally LIVE per operator spec; do NOT "
                "freeze it via get_cached_for_hash")
            assert "get_regime_cache" in src, (
                f"{fn.__name__} MUST still call get_regime_cache -- "
                "live regime signals are intentionally LIVE per "
                "operator spec; do NOT route through a snapshot "
                "table")
