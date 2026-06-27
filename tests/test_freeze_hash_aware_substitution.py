"""tests/test_freeze_hash_aware_substitution.py -- June 27 2026.

PR 1 of three. Pins the submission-freeze substitution-table fix:

  * Document generators (brief / deck / appendix) MUST route CIO row
    loads through get_cached_for_hash(freeze_hash) when freeze is
    active, not get_latest_recommendation() (which returns the live
    row regardless of hash). Bob's slide 7 regression was the live
    confidence 62.7% leaking into the freeze-locked deck instead of
    the freeze-time 95.4%.
  * Regime signals get a hash-keyed snapshot (migration 065 +
    snapshot_regime_signals_for_hash / get_regime_snapshot_for_hash)
    so watchpoint tokens read freeze-correct values too.
  * Snapshot is captured on freeze activation
    (submission_freeze.set_freeze_config).
  * load_substitution_metric_sources accepts an optional data_hash
    arg and routes through get_metric(data_hash, kind) when supplied.
  * build_substitution_table has a hash_verified=True flag. A
    data_hash supplied without the flag logs a structured warning
    so a regression that re-introduces non-hash-aware reads is
    visible in operator logs.
  * _CACHE_VERSION bumped 4 -> 5 to invalidate every cached
    pre-fix substitution table on first post-deploy read.

Test groups:

  TestResolveHashAwareCioRow
    Freeze active + freeze hash has a row -> returns it.
    Freeze active + miss -> raises HTTPException 500 with the
    spec 'Run light refresh and try again' message.
    Freeze inactive -> falls through to get_latest_recommendation.

  TestResolveHashAwareLiveSignals
    Freeze active + snapshot present -> returns snapshot.
    Freeze active + no snapshot -> returns None + logs
    freeze_regime_unavailable.
    Freeze inactive -> falls through to get_regime_cache.
    NEVER falls back to live cache under freeze.

  TestLoadSubstitutionMetricSourcesHashAware
    With data_hash -> routes through get_metric.
    Without data_hash -> falls through to get_latest_metric.

  TestBuildSubstitutionTableHashVerifiedFlag
    data_hash supplied + hash_verified=False -> logs
    build_substitution_table_hash_unverified warning.
    data_hash supplied + hash_verified=True -> no warning.
    Empty data_hash -> no warning regardless of flag.

  TestCacheVersionBumped
    _CACHE_VERSION pinned at 5 so a regression that re-reverts the
    version surfaces immediately.

  TestSnapshotHelpers
    snapshot helper signatures + tools/cache.py exports.
"""
from __future__ import annotations

import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── _resolve_hash_aware_cio_row ─────────────────────────────────────


class TestResolveHashAwareCioRow:

    @pytest.mark.asyncio
    async def test_freeze_active_with_freeze_row_returns_it(
            self, monkeypatch):
        import main as m
        freeze_row = {"regime": "TRANSITION",
                      "confidence": 0.954,
                      "data_hash": "c421fb895347f924"}

        async def _hash_aware(h):
            return freeze_row if h == "c421fb895347f924" else None

        async def _live():
            raise AssertionError(
                "live get_latest_recommendation MUST NOT fire under freeze")

        monkeypatch.setattr(
            "tools.cio_recommendation.get_cached_for_hash",
            _hash_aware)
        monkeypatch.setattr(
            "tools.cio_recommendation.get_latest_recommendation",
            _live)
        out = await m._resolve_hash_aware_cio_row(
            data_hash="c421fb895347f924",
            live_hash="d0b1339e06845559",
            document_type="presentation_deck")
        assert out == freeze_row

    @pytest.mark.asyncio
    async def test_freeze_active_with_miss_raises_export_failed(
            self, monkeypatch):
        import main as m
        from fastapi import HTTPException

        async def _miss(h):
            return None

        monkeypatch.setattr(
            "tools.cio_recommendation.get_cached_for_hash", _miss)

        with pytest.raises(HTTPException) as exc:
            await m._resolve_hash_aware_cio_row(
                data_hash="c421fb895347f924",
                live_hash="d0b1339e06845559",
                document_type="presentation_deck")
        assert exc.value.status_code == 500
        detail = str(exc.value.detail)
        assert "Run light refresh and try again" in detail
        assert "c421fb89" in detail   # truncated freeze hash in message

    @pytest.mark.asyncio
    async def test_freeze_inactive_falls_through_to_live(
            self, monkeypatch):
        import main as m
        live_row = {"regime": "BULL", "confidence": 0.627}

        async def _live():
            return live_row

        async def _hash_aware(h):
            raise AssertionError(
                "hash-aware loader MUST NOT fire when freeze inactive")

        monkeypatch.setattr(
            "tools.cio_recommendation.get_latest_recommendation",
            _live)
        monkeypatch.setattr(
            "tools.cio_recommendation.get_cached_for_hash",
            _hash_aware)
        # data_hash == live_hash means freeze inactive.
        out = await m._resolve_hash_aware_cio_row(
            data_hash="d0b1339e06845559",
            live_hash="d0b1339e06845559",
            document_type="presentation_deck")
        assert out == live_row


# ── _resolve_hash_aware_live_signals ────────────────────────────────


class TestResolveHashAwareLiveSignals:

    @pytest.mark.asyncio
    async def test_freeze_active_with_snapshot_returns_snapshot(
            self, monkeypatch):
        import main as m
        snap = {"vix_level": 13.2, "threshold_regime": "TRANSITION"}

        async def _snap(h):
            return snap if h == "c421fb895347f924" else None

        async def _live():
            raise AssertionError(
                "live get_regime_cache MUST NOT fire under freeze")

        monkeypatch.setattr(
            "tools.cache.get_regime_snapshot_for_hash", _snap)
        monkeypatch.setattr(
            "tools.cache.get_regime_cache", _live)

        out = await m._resolve_hash_aware_live_signals(
            data_hash="c421fb895347f924",
            live_hash="d0b1339e06845559",
            document_type="presentation_deck")
        assert out == snap

    @pytest.mark.asyncio
    async def test_freeze_active_no_snapshot_returns_none_no_live_fallback(
            self, monkeypatch):
        import main as m

        async def _miss(h):
            return None

        async def _live():
            raise AssertionError(
                "live get_regime_cache MUST NOT fire under freeze "
                "even when no snapshot exists -- snapshots miss "
                "must render em-dash, NOT leak post-freeze signals")

        monkeypatch.setattr(
            "tools.cache.get_regime_snapshot_for_hash", _miss)
        monkeypatch.setattr(
            "tools.cache.get_regime_cache", _live)

        out = await m._resolve_hash_aware_live_signals(
            data_hash="c421fb895347f924",
            live_hash="d0b1339e06845559",
            document_type="presentation_deck")
        assert out is None

    @pytest.mark.asyncio
    async def test_freeze_inactive_falls_through_to_live(
            self, monkeypatch):
        import main as m
        live = {"vix_level": 18.0}

        async def _live():
            return live

        async def _snap(h):
            raise AssertionError(
                "snapshot loader MUST NOT fire when freeze inactive")

        monkeypatch.setattr(
            "tools.cache.get_regime_cache", _live)
        monkeypatch.setattr(
            "tools.cache.get_regime_snapshot_for_hash", _snap)

        out = await m._resolve_hash_aware_live_signals(
            data_hash="d0b1339e06845559",
            live_hash="d0b1339e06845559",
            document_type="presentation_deck")
        assert out == live


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
                return {"regime_conditional": [], "factor_loadings": []}
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
        # All three metric_kinds queried with the freeze hash.
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

        await load_substitution_metric_sources()  # no data_hash arg
        assert "academic_analytics" in seen
        assert "oos_cost_sensitivity" in seen
        assert "crisis_performance" in seen


# ── build_substitution_table hash_verified flag ───────────────────


class TestBuildSubstitutionTableHashVerifiedFlag:

    def test_data_hash_without_flag_logs_warning(
            self, monkeypatch):
        from tools import numeric_substitution as ns

        events: list[tuple[str, dict]] = []

        class _StubLog:
            def warning(self, event, **kw): events.append((event, kw))
            def info(self, *a, **kw): pass

        monkeypatch.setattr(ns, "log", _StubLog())
        ns.build_substitution_table(
            {}, None, "c421fb895347f924")  # no hash_verified
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


# ── Cache version bump ─────────────────────────────────────────────


class TestCacheVersionBumped:

    def test_cache_version_is_5(self):
        from tools.numeric_substitution import _CACHE_VERSION
        assert _CACHE_VERSION == 5


# ── Snapshot helpers + migration  ──────────────────────────────────


class TestSnapshotHelpers:

    def test_snapshot_helpers_exported(self):
        from tools.cache import (
            snapshot_regime_signals_for_hash,
            get_regime_snapshot_for_hash,
        )
        assert callable(snapshot_regime_signals_for_hash)
        assert callable(get_regime_snapshot_for_hash)

    def test_migration_065_present(self):
        import os
        path = os.path.join(
            "backend", "migrations", "versions",
            "065_regime_signals_snapshots.py")
        assert os.path.exists(path), (
            f"migration 065 missing at {path}")
        src = open(path, encoding="utf-8").read()
        # Table name + key explicit columns the substitution table
        # consumes (schema matches regime_signals_cache minus TTL).
        assert "regime_signals_snapshots" in src
        for col in (
            "data_hash", "threshold_regime", "hmm_regime",
            "hmm_probabilities", "regimes_agree",
            "vix_level", "yield_curve_slope", "credit_spread",
            "equity_trend", "pre_2022_avg_correlation",
            "post_2022_avg_correlation", "snapshotted_at",
        ):
            assert col in src, f"migration 065 missing column {col}"

    def test_set_freeze_config_invokes_snapshot_on_activation(
            self):
        """Source inspection -- set_freeze_config calls
        snapshot_regime_signals_for_hash(freeze_hash) when activating
        so the freeze always has a regime snapshot keyed to its hash."""
        import inspect
        from tools.submission_freeze import set_freeze_config
        src = inspect.getsource(set_freeze_config)
        assert "snapshot_regime_signals_for_hash" in src
        # Must be inside the active=True branch.
        assert "active and freeze_hash" in src

    def test_doc_generators_use_hash_aware_helpers(self):
        """Source inspection -- all three document generators route
        through _resolve_hash_aware_cio_row + _resolve_hash_aware_
        live_signals instead of the live loaders."""
        import inspect, re as _re
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
            # Strip docstring + comments so a legacy-name mention in
            # a comment doesn't trip the structural check.
            no_doc = _re.sub(
                r'"""[\s\S]*?"""', "", src)
            no_cmt = _re.sub(r"(?m)^\s*#.*$", "", no_doc)
            assert "_resolve_hash_aware_cio_row" in no_cmt, (
                f"{fn.__name__} missing hash-aware CIO call")
            assert "_resolve_hash_aware_live_signals" in no_cmt, (
                f"{fn.__name__} missing hash-aware live-signals call")
            # And MUST NOT call the live loaders directly.
            assert "await get_latest_recommendation(" not in no_cmt, (
                f"{fn.__name__} still calls get_latest_recommendation "
                "(should route through _resolve_hash_aware_cio_row)")
            assert "await get_regime_cache(" not in no_cmt, (
                f"{fn.__name__} still calls get_regime_cache "
                "(should route through _resolve_hash_aware_live_signals)")
