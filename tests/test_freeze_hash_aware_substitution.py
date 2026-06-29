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


# ── LEAK 1 -- strategy_cache hash-aware via gather_document_data ──


class TestGatherDocumentDataHashAware:
    """PR 1 v3 (LEAK 1 closer). The dominant substitution-table
    leak was gather_document_data calling get_latest_strategy_cache()
    -- which returns the LIVE latest strategy_results_cache row
    regardless of data_hash. Under freeze, ~20 deck/brief/appendix
    headline tokens (Sharpe / max_drawdown / recovery / blend
    weights) silently received LIVE values.

    Fix: gather_document_data accepts optional data_hash. When
    supplied, routes through get_strategy_cache(data_hash) instead
    of get_latest_strategy_cache. On miss raises
    StrategyCacheMissingForHashError -- the 3 doc generators
    propagate (the outer try/except logs + raises so the job
    worker writes the error to the job row, surfacing to the user
    as 'Run light refresh and try again')."""

    @pytest.mark.asyncio
    async def test_with_data_hash_routes_through_hash_aware_loader(
            self, monkeypatch):
        from tools import academic_export as ae
        # Force non-test branch so the strategy load fires.
        monkeypatch.setattr(ae, "ENVIRONMENT", "production")

        seen: list[str] = []

        async def _hash_aware(h):
            seen.append(("hash_aware", h))
            return None  # simulate miss -> raises

        async def _latest():
            seen.append(("latest", None))
            raise AssertionError(
                "get_latest_strategy_cache MUST NOT fire when "
                "data_hash supplied")

        monkeypatch.setattr(
            "tools.cache.get_strategy_cache", _hash_aware)
        monkeypatch.setattr(
            "tools.cache.get_latest_strategy_cache", _latest)
        # Other reads in gather_document_data -- short-circuit so
        # the function returns once the strategy load resolves.
        async def _empty():
            return {}
        async def _empty_l():
            return []
        monkeypatch.setattr(
            "tools.cache.get_monthly_returns", _empty)
        monkeypatch.setattr(
            "tools.cache.get_ff_factors", _empty_l)

        with pytest.raises(
                ae.StrategyCacheMissingForHashError) as exc:
            await ae.gather_document_data(
                data_hash="c421fb895347f924")
        assert "c421fb89" in str(exc.value)
        assert "light refresh" in str(exc.value)
        # The hash-aware loader fired, the latest loader did NOT.
        assert ("hash_aware", "c421fb895347f924") in seen
        assert all(k != "latest" for k, _v in seen)

    @pytest.mark.asyncio
    async def test_without_data_hash_falls_through_to_latest(
            self, monkeypatch):
        from tools import academic_export as ae
        monkeypatch.setattr(ae, "ENVIRONMENT", "production")

        called: dict[str, bool] = {
            "hash_aware": False, "latest": False}

        async def _hash_aware(h):
            called["hash_aware"] = True
            raise AssertionError(
                "get_strategy_cache MUST NOT fire when no "
                "data_hash supplied (legacy path uses latest)")

        async def _latest():
            called["latest"] = True
            return None  # cold cache -> bundle stays empty

        monkeypatch.setattr(
            "tools.cache.get_strategy_cache", _hash_aware)
        monkeypatch.setattr(
            "tools.cache.get_latest_strategy_cache", _latest)
        async def _empty():
            return {}
        async def _empty_l():
            return []
        monkeypatch.setattr(
            "tools.cache.get_monthly_returns", _empty)
        monkeypatch.setattr(
            "tools.cache.get_ff_factors", _empty_l)

        bundle = await ae.gather_document_data()  # no data_hash
        # Falls through gracefully -- no exception.
        assert bundle["available"] is False
        assert called["latest"] is True
        assert called["hash_aware"] is False

    def test_three_generators_thread_data_hash_through(self):
        """Source inspection: every doc-generator path that calls
        gather_document_data or gather_analytical_appendix_data
        must pass data_hash=. Catches a future regression that
        accidentally drops the kwarg."""
        import inspect
        from main import (
            _generate_brief_document,
            _generate_appendix_document,
            _build_deck_context,
        )
        for fn in (
            _generate_brief_document,
            _generate_appendix_document,
            _build_deck_context,
        ):
            src = inspect.getsource(fn)
            # Each generator must thread data_hash through to the
            # gather function. The exact kwarg name is 'data_hash'.
            assert "data_hash=" in src, (
                f"{fn.__name__} not threading data_hash through "
                "the gather call")

    def test_exception_class_carries_spec_message(self):
        from tools.academic_export import (
            StrategyCacheMissingForHashError,
        )
        err = StrategyCacheMissingForHashError(
            "c421fb895347f924")
        msg = str(err)
        assert "c421fb89" in msg
        assert "Run light refresh and try again" in msg
        # The error class also carries the hash on the attribute.
        assert err.data_hash == "c421fb895347f924"


# ── LEAK 1 self-heal -- light refresh uses freeze hash ────────────


class TestLightRefreshUsesFreezeHashWhenActive:
    """PR 1 v4 (architectural-rule closure). Under freeze, the doc
    generators raise StrategyCacheMissingForHashError when the
    freeze hash has no strategy_results_cache row. The user-facing
    spec message is 'Run light refresh and try again' -- which
    REQUIRES light refresh to warm the strategy cache under the
    FREEZE hash, not the live hash. Otherwise the error is NOT
    self-healing and the user loops infinitely:

      deck export -> miss -> error -> light refresh -> cache
      populated under LIVE hash -> freeze-hash slot still empty
      -> deck export -> miss -> error -> ...

    Fix: post_light_refresh routes the strategy_hash through
    get_effective_data_hash(live_hash). All downstream writes
    (set_strategy_cache, refresh_academic_analytics,
    refresh_oos_cost_sensitivity, the editor_drafts UPDATE, the
    response's strategy_hash field) inherit the effective hash."""

    def test_post_light_refresh_threads_effective_hash(self):
        """Source inspection: post_light_refresh MUST call
        get_effective_data_hash(live_hash) before writing the
        strategy cache. Catches a future regression that drops the
        freeze-aware indirection."""
        import inspect
        from main import post_light_refresh
        src = inspect.getsource(post_light_refresh)
        assert "get_effective_data_hash" in src, (
            "post_light_refresh MUST route the strategy_hash "
            "through get_effective_data_hash so light refresh "
            "warms the FREEZE hash slot under freeze. Without "
            "this, StrategyCacheMissingForHashError is not "
            "self-healing -- the user loops infinitely.")
        # The downstream writes must use the resolved
        # strategy_hash (which now carries the effective hash),
        # NOT a separate live-hash variable.
        assert "set_strategy_cache(\n            strategy_hash" in src \
            or "set_strategy_cache(strategy_hash" in src, (
                "set_strategy_cache MUST be passed strategy_hash "
                "(the effective hash), not live_hash directly")
        assert "refresh_academic_analytics(strategy_hash)" in src, (
            "refresh_academic_analytics MUST be passed "
            "strategy_hash (the effective hash)")
        assert "refresh_oos_cost_sensitivity(strategy_hash)" in src, (
            "refresh_oos_cost_sensitivity MUST be passed "
            "strategy_hash (the effective hash)")

    def test_get_effective_data_hash_returns_freeze_when_active(
            self, monkeypatch):
        """End-to-end pin on the upstream helper. When freeze is
        active with freeze_hash 'c421fb895347f924',
        get_effective_data_hash returns the freeze hash REGARDLESS
        of the live hash. post_light_refresh's three-line pattern
        depends on this -- pin it so a future submission_freeze
        refactor cannot accidentally invert the semantics."""
        import asyncio
        from tools import submission_freeze as sf

        async def _frozen_config():
            return {
                "active": True,
                "freeze_hash": "c421fb895347f924",
            }

        monkeypatch.setattr(
            sf, "get_freeze_config", _frozen_config)

        async def _run():
            return await sf.get_effective_data_hash(
                "d0b1339e06845559")  # different live hash

        result = asyncio.run(_run())
        assert result == "c421fb895347f924", (
            "get_effective_data_hash MUST return the freeze hash "
            "when freeze is active, regardless of the live hash. "
            "Without this, light refresh would warm the cache "
            "under the wrong slot.")

    def test_get_effective_data_hash_returns_live_when_inactive(
            self, monkeypatch):
        """Mirror pin: when freeze is OFF,
        get_effective_data_hash MUST return the live hash unchanged.
        This is the legacy / live-platform path -- a refactor that
        always returned the freeze hash would break the
        live-dashboard generators."""
        import asyncio
        from tools import submission_freeze as sf

        async def _off_config():
            return {"active": False, "freeze_hash": None}

        monkeypatch.setattr(
            sf, "get_freeze_config", _off_config)

        async def _run():
            return await sf.get_effective_data_hash(
                "d0b1339e06845559")

        result = asyncio.run(_run())
        assert result == "d0b1339e06845559"
