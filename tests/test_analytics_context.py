"""tests/test_analytics_context.py — narrative analytics context.

Item 5 (May 23 2026 — analytics context injection).

Covers the narrative builder, accessor contracts, fail-open
behaviour for refresh, and the /api/v1/context/freshness endpoint.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")


from tools import analytics_context as ac  # noqa: E402


# ── Builder logic — pure, no DB ──────────────────────────────────────────────


class TestNarrativeBuilder:
    """Five sentences in order. A missing input drops the dependent
    sentence rather than the whole block."""

    def test_with_full_inputs_renders_five_sentences(self):
        strategies = [
            {"strategy_name": "BENCHMARK", "sharpe_ratio": 0.50},
            {"strategy_name": "REGIME_SWITCHING",
             "sharpe_ratio": 0.63},
            {"strategy_name": "EQUAL_WEIGHT", "sharpe_ratio": 0.57},
        ]
        corr = {"pre_2022_avg": -0.31, "post_2022_avg": 0.48}
        narrative = ac._build_narrative(strategies, corr,
                                         macro_in_use=True)
        assert narrative != ""
        # Sentence 1 — headline.
        assert "2022" in narrative
        assert "-0.31" in narrative or "−0.31" in narrative
        # Sentence 2 — implication.
        assert "60/40" in narrative.lower() \
            or "static" in narrative.lower()
        # Sentence 3 — leader.
        assert "REGIME_SWITCHING" in narrative
        # Sentence 4 — caveat.
        assert "0/10" in narrative or "p < 0.005" in narrative \
            or "Benjamin" in narrative
        # Sentence 5 — macro frame (only when macro_in_use=True).
        assert "macro" in narrative.lower()

    def test_macro_sentence_omitted_when_macro_layer_cold(self):
        strategies = [
            {"strategy_name": "BENCHMARK", "sharpe_ratio": 0.50},
            {"strategy_name": "REGIME_SWITCHING", "sharpe_ratio": 0.63},
        ]
        corr = {"pre_2022_avg": -0.31, "post_2022_avg": 0.48}
        narrative = ac._build_narrative(strategies, corr,
                                         macro_in_use=False)
        # Headline + implication + leader + caveat are present.
        assert "REGIME_SWITCHING" in narrative
        # The macro-frame sentence is gated on macro_in_use=True.
        assert "macro regime read" not in narrative.lower()
        assert "next-quarter outlook" not in narrative.lower()

    def test_empty_inputs_render_empty_block(self):
        # No strategies AND no correlation data → empty block, not
        # a hollow paragraph.
        narrative = ac._build_narrative(None, None, macro_in_use=False)
        assert narrative == ""

    def test_partial_inputs_drop_dependent_sentences(self):
        # Only strategies; no correlation. Headline + implication
        # depend on the correlation data — they should drop. The
        # leader + caveat sentences should still render.
        strategies = [
            {"strategy_name": "BENCHMARK", "sharpe_ratio": 0.50},
            {"strategy_name": "REGIME_SWITCHING", "sharpe_ratio": 0.63},
        ]
        narrative = ac._build_narrative(strategies, None,
                                         macro_in_use=False)
        # No 2022 headline since the correlation pair is missing.
        assert "regime shift" not in narrative.lower()
        # Leader sentence is still there.
        assert "REGIME_SWITCHING" in narrative


class TestWrapInBlock:
    def test_returns_empty_on_empty_narrative(self):
        assert ac._wrap_in_block("", "2026-05-23T12:00:00+00:00") == ""

    def test_wraps_with_timestamp_header(self):
        text = ac._wrap_in_block(
            "This is the narrative.", "2026-05-23T12:00:00+00:00")
        assert "ANALYTICAL NARRATIVE" in text
        assert "2026-05-23T12:00:00+00:00" in text
        assert "This is the narrative." in text


# ── Accessors ────────────────────────────────────────────────────────────────


class TestAccessors:
    """Cold cache → empty string + None freshness; primed cache →
    the cached values."""

    def test_cold_cache_returns_empty_and_none(self):
        ac._set_cache_for_test("")  # reset
        assert ac.get_analytics_context() == ""
        assert ac.get_analytics_freshness() is None

    def test_primed_cache_returns_cached_values(self):
        ac._set_cache_for_test(
            "primed text", "2026-05-23T13:00:00+00:00")
        try:
            assert ac.get_analytics_context() == "primed text"
            assert ac.get_analytics_freshness() == \
                "2026-05-23T13:00:00+00:00"
        finally:
            ac._set_cache_for_test("")  # reset

    def test_inject_is_noop_on_cold_cache(self):
        ac._set_cache_for_test("")
        assert ac.inject_analytics_context("hello") == "hello"

    def test_inject_appends_when_primed(self):
        ac._set_cache_for_test("\n\nNARRATIVE", "2026-05-23T13:00:00+00:00")
        try:
            assert ac.inject_analytics_context("system_prompt") == \
                "system_prompt\n\nNARRATIVE"
        finally:
            ac._set_cache_for_test("")


# ── Refresh fail-open ────────────────────────────────────────────────────────


class TestRefreshFailOpen:
    """Any read error → previous cache preserved + no raise."""

    def test_refresh_with_no_db_does_not_raise(self, monkeypatch):
        # The cache stays empty (or whatever was there before) and
        # no exception propagates. This is the cold-deploy contract.
        ac._set_cache_for_test("previous text",
                                "2026-05-23T10:00:00+00:00")
        try:
            asyncio.run(ac.refresh_analytics_context())
            # Previous cache is preserved on an empty-build path.
            assert ac.get_analytics_context() == "previous text"
        finally:
            ac._set_cache_for_test("")


# ── /api/v1/context/freshness endpoint ───────────────────────────────────────


class TestFreshnessEndpoint:
    def _client(self):
        from fastapi.testclient import TestClient
        from main import app
        return TestClient(app)

    def test_unauthenticated_is_401(self):
        c = self._client()
        r = c.get("/api/v1/context/freshness")
        assert r.status_code == 401

    def test_authenticated_returns_three_layer_map(self, monkeypatch):
        # Mock the require_auth dependency so we reach the body.
        from auth import require_auth
        from main import app

        async def _fake_auth():
            return {"email": "viewer@queens.edu",
                    "permissions": ["view_analytics"]}

        app.dependency_overrides[require_auth] = _fake_auth
        try:
            # Prime the analytics cache so the endpoint returns a
            # populated freshness value for at least one layer.
            ac._set_cache_for_test(
                "narrative", "2026-05-23T14:00:00+00:00")
            c = self._client()
            r = c.get("/api/v1/context/freshness")
            assert r.status_code == 200
            body = r.json()
            assert "macro_context" in body
            assert "analytics_context" in body
            assert "diversification_context" in body
            assert body["analytics_context"] == \
                "2026-05-23T14:00:00+00:00"
        finally:
            app.dependency_overrides.pop(require_auth, None)
            ac._set_cache_for_test("")

    def test_diversification_reads_analytics_metrics_cache_timestamp(
            self, monkeypatch):
        """June 30 2026 incident pin -- the endpoint must reflect the
        REAL diversification write timestamp from
        analytics_metrics_cache, NOT proxy off strategy_results_cache.

        Scenario from prod (2026-06-30):
          * strategy_results_cache.MAX(computed_at) = 2026-06-29T16:43:37
            (older -- the underlying market data hash did not change)
          * analytics_metrics_cache.MAX(computed_at) for the seven
            diversification metric_kind rows = 2026-06-30T18:01:53
            (newer -- only the derived analytics were recomputed)
        Endpoint must return the NEWER timestamp on
        diversification_context. The prior implementation returned the
        OLDER strategy_results_cache value and read "stale" on the
        dashboard.
        """
        import datetime as _dt
        import sys
        import types

        from auth import require_auth
        from main import app

        async def _fake_auth():
            return {"email": "viewer@queens.edu",
                    "permissions": ["view_analytics"]}

        # The scenario: a fresh write to analytics_metrics_cache for the
        # diversification metric_kinds. The fake session captures the
        # SQL so we can pin that the endpoint now hits the correct
        # table + WHERE clause, and returns a fresh timestamp.
        fresh_ts = _dt.datetime(
            2026, 6, 30, 18, 1, 53, tzinfo=_dt.timezone.utc)
        captured_sql: list[str] = []

        class _FakeResult:
            def __init__(self, ts):
                self._ts = ts

            def fetchone(self):
                return (self._ts,)

        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_a):
                return False

            async def execute(self, stmt, *_a, **_kw):
                captured_sql.append(str(stmt))
                return _FakeResult(fresh_ts)

        fake_db = types.ModuleType("database")
        fake_db.AsyncSessionLocal = lambda: _FakeSession()  # noqa: E731
        monkeypatch.setitem(sys.modules, "database", fake_db)

        app.dependency_overrides[require_auth] = _fake_auth
        try:
            c = self._client()
            r = c.get("/api/v1/context/freshness")
            assert r.status_code == 200
            body = r.json()
            # 1. The fresh timestamp from analytics_metrics_cache is
            #    surfaced -- NOT the stale strategy_results_cache one.
            assert body["diversification_context"] == (
                "2026-06-30T18:01:53+00:00")
            # 2. Source-pin: the SELECT now targets the correct table
            #    and filters by the seven diversification metric_kinds.
            assert any(
                "analytics_metrics_cache" in s for s in captured_sql), (
                    f"endpoint must SELECT FROM analytics_metrics_cache; "
                    f"got: {captured_sql}")
            assert not any(
                "strategy_results_cache" in s for s in captured_sql), (
                    "endpoint must NOT proxy off strategy_results_cache "
                    "anymore; the bug was this exact proxy")
            for metric_kind in (
                "correlation_matrices",
                "tail_risk",
                "capture_ratios",
                "drawdown_duration",
                "crisis_performance",
                "marginal_contribution_to_risk",
                "return_distribution",
            ):
                assert any(metric_kind in s for s in captured_sql), (
                    f"diversification metric_kind '{metric_kind}' must "
                    f"appear in the WHERE clause")
        finally:
            app.dependency_overrides.pop(require_auth, None)
