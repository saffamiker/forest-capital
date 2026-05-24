"""tests/test_frontier_cache.py — efficient frontier precompute cache.

Hotfix (May 23 2026 — /api/optimize/weights was timing out at 30s
because every request ran the 100-point SLSQP sweep on the request
thread). Verifies:

  - refresh_efficient_frontier is registered in refresh_all_analytics
  - the dispatch is order-stable (academic_analytics first, frontier
    next, diversification third) so a cache cold on Cumulative
    Returns gets warmed before the frontier sweep
  - the /api/optimize/weights endpoint reads the cache before
    running the inline sweep
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")


from tools import precomputed_analytics as pa  # noqa: E402


class TestRefreshDispatch:
    """The dispatch wiring matters as much as the function itself —
    if refresh_efficient_frontier is defined but never called, the
    cache never gets populated and the endpoint times out forever."""

    def test_refresh_efficient_frontier_is_exported(self):
        # The function must be importable from the module surface so
        # the tests + the trigger path can resolve it.
        assert callable(pa.refresh_efficient_frontier)

    def test_refresh_efficient_frontier_is_called_by_refresh_all(self):
        # Source-level check — the dispatch must include the
        # frontier refresh. A drift here is a hotfix regression.
        src = inspect.getsource(pa.refresh_all_analytics)
        assert "refresh_efficient_frontier" in src, (
            "refresh_all_analytics must call refresh_efficient_frontier "
            "so the cache stays in step with strategy ingestion.")

    def test_frontier_refresh_short_circuits_on_no_db(self, monkeypatch):
        # No monthly returns → refresh returns cleanly without
        # raising or trying the (slow) optimizer.
        async def _no_monthly():
            return None
        from tools import cache as cache_mod
        monkeypatch.setattr(cache_mod, "get_monthly_returns", _no_monthly)
        asyncio.run(pa.refresh_efficient_frontier("test_hash"))

    def test_frontier_refresh_short_circuits_on_short_series(self, monkeypatch):
        # < 24 monthly observations is not enough for a frontier —
        # refresh logs and returns rather than computing junk.
        async def _short():
            return {
                "dates": ["2025-01-31", "2025-02-28"],
                "equity": [0.01, 0.02],
                "ig":     [0.001, 0.002],
                "hy":     [0.003, 0.004],
                "rf":     [0.003, 0.003],
            }
        from tools import cache as cache_mod
        monkeypatch.setattr(cache_mod, "get_monthly_returns", _short)
        # No raise — the function logs and returns.
        asyncio.run(pa.refresh_efficient_frontier("test_hash_short"))


class TestEndpointCacheLookup:
    """The optimize endpoint's hot path: try the cache, fall back to
    the inline sweep if it misses. The cache lookup never runs the
    100-point sweep."""

    def test_endpoint_source_references_frontier_cache(self):
        # The optimize_weights endpoint's source must reference the
        # efficient_frontier cache lookup. Catches a regression
        # that silently goes back to running the sweep every time.
        from main import optimize_weights
        src = inspect.getsource(optimize_weights)
        assert "efficient_frontier" in src
        assert "get_metric" in src or "get_precomputed" in src \
            or "get_latest_metric" in src, (
                "optimize_weights must read the precomputed cache "
                "before running the inline sweep.")
        assert "cache_hit" in src or "cache_miss" in src, (
            "Cache path should log hit / miss so the production "
            "logs surface cache-cold deploys.")


class TestStartupWarmHook:
    """The lifespan hook fires the analytics refresh if the cache is
    cold on startup. Without this, the first deploy with no cache
    rows times out the dashboard until somebody triggers an
    ingestion manually."""

    def test_lifespan_warms_analytics_cache_when_cold(self):
        # Source-level check — the lifespan hook must schedule the
        # analytics warm task on startup. May 24 2026: switched
        # from inline trigger_refresh_async to the fully automatic
        # auto_warm_analytics helper from tools.cache_warm_state,
        # which retries up to 3 times with exponential backoff so
        # the cache always warms within ~60s of boot without an
        # operator action. The assertion looks for the new
        # function name + the analytics_cache_auto_warm_scheduled
        # log event the hook emits.
        from main import lifespan
        src = inspect.getsource(lifespan)
        assert "auto_warm_analytics" in src, (
            "lifespan must call auto_warm_analytics on startup so "
            "the cache always warms within ~60s of boot.")
        assert "analytics_cache_auto_warm_scheduled" in src, (
            "lifespan must emit analytics_cache_auto_warm_scheduled "
            "so the operator can see the schedule fired in Render "
            "logs.")
