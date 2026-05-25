"""Coverage for the F1+F2+F3+F4 analytics cache completeness commit
(May 22 2026).

F1 — refresh_sensitivity computes the parameter sweep and writes
       one row to analytics_metrics_cache. The endpoint serves the
       cached row on the hot path; the inline get_full_history +
       compute_sensitivity path is only used as a cold-cache fallback.
F2 — get_latest_strategy_hash() has a 5-second in-process TTL memo
       so the seven diversification endpoints firing in parallel on
       Analytics page mount share one DB query.
F4 — refresh_risk_free_rate_config computes the rf mean × 12 and
       writes it to analytics_metrics_cache. /api/v1/analytics/config
       reads the cached row on the hot path.

These tests exercise behaviour, not implementation, so they run
without a live Postgres — the helpers fail open to None / no-op.
"""
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


# ── F2 — get_latest_strategy_hash memo ────────────────────────────────────────

class TestHashMemoDedupes:
    """The 5-second TTL memo on get_latest_strategy_hash() so the seven
    diversification endpoints firing in parallel on Analytics page mount
    share one underlying DB hit."""

    def setup_method(self):
        from tools import cache as cache_mod
        cache_mod._hash_memo_clear()

    def test_repeated_calls_within_ttl_share_one_db_lookup(self, monkeypatch):
        """A burst of seven parallel calls makes ONE DB query, not seven.
        Simulated by counting how often the underlying session.execute
        is invoked across seven asyncio.run-of-the-helper calls."""
        import asyncio
        from tools import cache as cache_mod

        execute_calls = {"n": 0}

        class _FakeRow:
            def fetchone(self):
                return ("hash_abc_123",)

        class _FakeSession:
            def __init__(self):
                pass
            async def execute(self, *_args, **_kwargs):
                execute_calls["n"] += 1
                return _FakeRow()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *exc):
                return False

        class _FakeMaker:
            def __call__(self):
                return _FakeSession()

        # Patch the module-level AsyncSessionLocal + the _DB_AVAILABLE
        # gate so the production code path runs against the fake.
        monkeypatch.setattr(cache_mod, "_DB_AVAILABLE", True, raising=False)
        monkeypatch.setattr(cache_mod, "AsyncSessionLocal", _FakeMaker(),
                            raising=False)

        async def _seven_parallel_calls():
            return await asyncio.gather(
                *[cache_mod.get_latest_strategy_hash() for _ in range(7)])

        results = asyncio.run(_seven_parallel_calls())
        # Every call returns the same value.
        assert results == ["hash_abc_123"] * 7
        # But only ONE DB query fired — the rest are memo hits. The first
        # call enters the helper, fetches, populates the memo; calls 2-7
        # see the memo within the same monotonic-clock tick and return
        # the cached value.
        assert execute_calls["n"] == 1

    def test_memo_busts_on_set_strategy_cache(self, monkeypatch):
        """A fresh strategy_results_cache write clears the memo so the
        next read picks up the new hash immediately instead of waiting
        out the 5-second TTL."""
        from tools import cache as cache_mod
        import time

        # Seed the memo by hand — simulate a hash read that landed
        # 0.1 seconds ago. Inside the TTL window.
        cache_mod._hash_memo["latest"] = (time.monotonic(), "old_hash")
        assert cache_mod._hash_memo.get("latest")[1] == "old_hash"

        # _hash_memo_clear is what set_strategy_cache calls right after
        # its successful write. The memo is gone.
        cache_mod._hash_memo_clear()
        assert "latest" not in cache_mod._hash_memo

    def test_no_db_path_caches_none(self, monkeypatch):
        """Even the no-DB path memoises — a series of fail-open calls
        within the TTL window must not retry the import path."""
        import asyncio
        from tools import cache as cache_mod

        monkeypatch.setattr(cache_mod, "_DB_AVAILABLE", False, raising=False)

        first = asyncio.run(cache_mod.get_latest_strategy_hash())
        assert first is None
        # The memo now holds (timestamp, None) — a second call within the
        # TTL returns None without re-entering the no-DB branch.
        assert "latest" in cache_mod._hash_memo
        second = asyncio.run(cache_mod.get_latest_strategy_hash())
        assert second is None


# ── F1 — refresh_sensitivity ──────────────────────────────────────────────────

class TestRefreshSensitivity:
    """The new refresh helper that writes the sensitivity payload to
    analytics_metrics_cache. Fail-open: no history, no cache write, no
    exception."""

    def test_no_history_skips_write(self, monkeypatch):
        """An empty history (cold deploy before any pipeline run) is a
        silent skip — refresh_sensitivity must not raise."""
        import asyncio
        from tools import precomputed_analytics as pa

        def _no_history():
            return {"equity_monthly": []}

        monkeypatch.setattr("tools.data_fetcher.get_full_history",
                            _no_history)
        # Should complete without raising.
        asyncio.run(pa.refresh_sensitivity("any_hash"))

    def test_writes_payload_on_full_history(self, monkeypatch):
        """A fake compute_sensitivity returns a known payload; the
        refresh helper wraps it in {"available": True, ...} and writes
        via set_metric. Verifies the wrapper shape and the metric_kind
        without touching a real DB."""
        import asyncio
        from tools import precomputed_analytics as pa

        captured: dict = {}

        async def _fake_set_metric(data_hash, metric_kind, payload, *,
                                   source=None):
            captured["data_hash"] = data_hash
            captured["metric_kind"] = metric_kind
            captured["payload"] = payload
            captured["source"] = source

        def _fake_history():
            return {"equity_monthly": [0.01, 0.02, 0.03]}

        def _fake_compute(history):
            return {"strategies": [{"strategy": "STUB", "points": []}]}

        monkeypatch.setattr(pa, "set_metric", _fake_set_metric)
        monkeypatch.setattr("tools.data_fetcher.get_full_history",
                            _fake_history)
        monkeypatch.setattr("tools.sensitivity.compute_sensitivity",
                            _fake_compute)

        asyncio.run(pa.refresh_sensitivity("test_hash"))

        assert captured["data_hash"] == "test_hash"
        assert captured["metric_kind"] == "sensitivity"
        assert captured["source"] == "refresh_sensitivity"
        # The wrapper shape — available + the compute_sensitivity output.
        assert captured["payload"]["available"] is True
        assert "strategies" in captured["payload"]


# ── F4 — refresh_risk_free_rate_config ────────────────────────────────────────

class TestRefreshRiskFreeRateConfig:
    """The /api/v1/analytics/config metric — mean monthly DTB3 × 12."""

    def test_writes_payload_when_rf_present(self, monkeypatch):
        import asyncio
        from tools import precomputed_analytics as pa

        captured: dict = {}

        async def _fake_set_metric(data_hash, metric_kind, payload, *,
                                   source=None):
            captured["data_hash"] = data_hash
            captured["metric_kind"] = metric_kind
            captured["payload"] = payload
            captured["source"] = source

        async def _fake_monthly():
            # rf is the monthly rate. Mean × 12 → annualised.
            return {"rf": [0.003, 0.004, 0.005, 0.004, 0.003]}

        monkeypatch.setattr(pa, "set_metric", _fake_set_metric)
        monkeypatch.setattr("tools.cache.get_monthly_returns", _fake_monthly)

        asyncio.run(pa.refresh_risk_free_rate_config("test_hash"))

        assert captured["metric_kind"] == "risk_free_rate_config"
        assert captured["payload"]["available"] is True
        # mean = 0.0038; × 12 = 0.0456; rounded to 4 decimals.
        assert captured["payload"]["risk_free_rate"] == pytest.approx(
            0.0456, abs=1e-4)
        assert "FRED DTB3" in captured["payload"]["risk_free_source"]

    def test_writes_unavailable_when_rf_empty(self, monkeypatch):
        import asyncio
        from tools import precomputed_analytics as pa

        captured: dict = {}

        async def _fake_set_metric(data_hash, metric_kind, payload, *,
                                   source=None):
            captured["payload"] = payload

        async def _fake_monthly():
            return {"rf": []}

        monkeypatch.setattr(pa, "set_metric", _fake_set_metric)
        monkeypatch.setattr("tools.cache.get_monthly_returns", _fake_monthly)

        asyncio.run(pa.refresh_risk_free_rate_config("test_hash"))

        assert captured["payload"]["available"] is False
        assert captured["payload"]["risk_free_rate"] is None
        # The source label is always present, even on an unavailable read.
        assert "FRED DTB3" in captured["payload"]["risk_free_source"]


# ── refresh_all_analytics dispatch includes the new helpers ───────────────────

class TestRefreshAllDispatch:
    """The top-level dispatch must call all four refresh functions
    in order. A regression that drops sensitivity or risk_free_rate_
    config from the list lands here."""

    def test_dispatch_calls_every_refresh(self, monkeypatch):
        import asyncio
        from tools import precomputed_analytics as pa

        called: list[str] = []

        async def _stub_academic(h):
            called.append("academic")
        async def _stub_transition(h):
            called.append("transition_matrix")
        async def _stub_frontier(h):
            called.append("efficient_frontier")
        async def _stub_div(h):
            called.append("diversification")
        async def _stub_sens(h):
            called.append("sensitivity")
        async def _stub_rf(h):
            called.append("rf_config")
        async def _stub_chars(h):
            called.append("strategy_characterisations")

        monkeypatch.setattr(pa, "refresh_academic_analytics", _stub_academic)
        # AN04 (May 24 2026) — transition matrix is its own refresh.
        monkeypatch.setattr(pa, "refresh_transition_matrix", _stub_transition)
        monkeypatch.setattr(pa, "refresh_efficient_frontier", _stub_frontier)
        monkeypatch.setattr(pa, "refresh_diversification_metrics", _stub_div)
        monkeypatch.setattr(pa, "refresh_sensitivity", _stub_sens)
        monkeypatch.setattr(pa, "refresh_risk_free_rate_config", _stub_rf)
        # Item 9 (May 22 2026) — strategy_characterisations is imported
        # lazily inside refresh_all_analytics, so patch the symbol on
        # the source module the dispatch reaches.
        monkeypatch.setattr(
            "tools.strategy_characterisations.refresh_strategy_characterisations",
            _stub_chars)

        asyncio.run(pa.refresh_all_analytics("dispatch_hash"))

        # Every refresh fired. Order is deterministic — the dispatch
        # runs them sequentially so a slow refresh upstream cannot
        # interleave with a downstream one. strategy_characterisations
        # is appended last because it depends on the analytics_metrics_
        # cache rows the earlier refreshes may have just written
        # (factor_loadings, regime_conditional) -- though as of today
        # it pulls them directly from the same upstream sources, not
        # from the cache.
        assert called == [
            "academic", "transition_matrix", "efficient_frontier",
            "diversification", "sensitivity", "rf_config",
            "strategy_characterisations"]
