"""
tests/test_cache_warm_strategy_repop.py — May 28 2026.

The startup analytics warm must repopulate strategy_results_cache
when the latest row is not healthy (missing strategies or any
strategy with empty monthly_returns). The previous implementation
ran refresh_all_analytics against whatever was in the cache, even
if the row was a partial-fallback — AN01 / AN04 then produced
empty downstream tables.

Two layers:
  1. _strategy_cache_is_healthy() — the rule that decides whether
     the warm rebuilds the strategy cache.
  2. _default_warm_fn() — proactively calls run_all_strategies +
     set_strategy_cache when the cache row is not healthy.
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "test-master-key")


# ── Health rule ──────────────────────────────────────────────────────────────

def _all_strategies_with_returns() -> dict[str, dict]:
    """All 10 canonical strategy ids each with a one-row
    monthly_returns list — the minimum shape a healthy
    strategy_results_cache row must carry."""
    from strategy_metadata import STRATEGY_METADATA
    return {
        entry["id"]: {"monthly_returns": [["2024-01-31", 0.01]]}
        for entry in STRATEGY_METADATA
    }


class TestStrategyCacheHealthRule:
    """The warm calls run_all_strategies when the cache row is NOT
    healthy. Healthy = the row exists AND contains every canonical
    strategy id AND every strategy carries a non-empty
    monthly_returns list. A BENCHMARK-only cache (the production
    symptom that prompted this fix) reads as not healthy because
    the downstream analytics layer needs all 10 strategies."""

    def test_none_cache_is_not_healthy(self):
        from tools.cache_warm_state import _strategy_cache_is_healthy
        assert _strategy_cache_is_healthy(None) is False

    def test_empty_cache_is_not_healthy(self):
        from tools.cache_warm_state import _strategy_cache_is_healthy
        assert _strategy_cache_is_healthy({}) is False

    def test_strategy_with_missing_monthly_returns_is_not_healthy(self):
        from tools.cache_warm_state import _strategy_cache_is_healthy
        # Even when all 10 strategies are present, a single broken
        # monthly_returns entry (the error fallback shape) is unhealthy.
        cached = _all_strategies_with_returns()
        cached["CLASSIC_60_40"] = {"sharpe_ratio": 0.0, "error": "boom"}
        assert _strategy_cache_is_healthy(cached) is False

    def test_strategy_with_empty_monthly_returns_is_not_healthy(self):
        from tools.cache_warm_state import _strategy_cache_is_healthy
        cached = _all_strategies_with_returns()
        cached["CLASSIC_60_40"] = {"sharpe_ratio": 0.0, "monthly_returns": []}
        assert _strategy_cache_is_healthy(cached) is False

    def test_benchmark_only_row_is_not_healthy(self):
        """The production symptom: a strategy_results_cache row with
        only BENCHMARK populated reads as healthy under the prior rule
        (BENCHMARK has monthly_returns populated). Under the new rule
        the missing 9 strategies make it unhealthy — the warm reruns
        run_all_strategies and writes a complete row."""
        from tools.cache_warm_state import _strategy_cache_is_healthy
        cached = {
            "BENCHMARK": {"monthly_returns": [["2024-01-31", 0.01]]},
        }
        assert _strategy_cache_is_healthy(cached) is False

    def test_partial_subset_with_data_is_not_healthy(self):
        """Two strategies present, both with monthly_returns, but the
        other 8 are missing entirely. Not healthy — downstream
        analytics needs ALL canonical strategies."""
        from tools.cache_warm_state import _strategy_cache_is_healthy
        cached = {
            "BENCHMARK": {"monthly_returns": [["2024-01-31", 0.01]]},
            "CLASSIC_60_40": {"monthly_returns": [["2024-01-31", 0.005]]},
        }
        assert _strategy_cache_is_healthy(cached) is False

    def test_all_canonical_strategies_with_data_is_healthy(self):
        from tools.cache_warm_state import _strategy_cache_is_healthy
        assert _strategy_cache_is_healthy(
            _all_strategies_with_returns()) is True


# ── _default_warm_fn behaviour ───────────────────────────────────────────────

class TestDefaultWarmFnRepopulatesStrategies:
    """When the cache row is not healthy, _default_warm_fn calls
    run_all_strategies + set_strategy_cache BEFORE refresh_all_analytics
    so the analytics layer reads fresh strategy data."""

    def test_unhealthy_cache_triggers_backtester_rerun(self):
        from tools import cache_warm_state

        # Mock every external dependency the warm reaches into.
        backtester_called: list[bool] = []
        set_cache_called: list[bool] = []

        # Returned by get_latest_strategy_cache — partial fallback.
        partial_fallback = {
            "BENCHMARK": {"monthly_returns": [["2024-01-31", 0.01]]},
            "CLASSIC_60_40": {"monthly_returns": []},
        }

        async def _fake_get_latest_strategy_cache():
            return partial_fallback

        async def _fake_get_latest_strategy_hash():
            return "abc123"

        async def _fake_set_strategy_cache(*args, **kwargs):
            set_cache_called.append(True)

        async def _fake_get_full_history_async():
            import pandas as pd
            idx = pd.DatetimeIndex(["2024-01-31", "2024-02-29"])
            return {"equity_monthly": pd.Series([0.01, 0.02], index=idx)}

        def _fake_run_all_strategies(history):
            backtester_called.append(True)
            return {
                "BENCHMARK": {"monthly_returns": [
                    ["2024-01-31", 0.01], ["2024-02-29", 0.02]]},
            }

        async def _fake_refresh_all_analytics(data_hash):
            return None

        async def _fake_get_precomputed(data_hash, kind):
            return {"available": True}

        with patch("tools.cache.get_latest_strategy_cache",
                   _fake_get_latest_strategy_cache), \
             patch("tools.cache.get_latest_strategy_hash",
                   _fake_get_latest_strategy_hash), \
             patch("tools.cache.set_strategy_cache",
                   _fake_set_strategy_cache), \
             patch("tools.data_fetcher.get_full_history_async",
                   _fake_get_full_history_async), \
             patch("tools.backtester.run_all_strategies",
                   _fake_run_all_strategies), \
             patch("tools.precomputed_analytics.refresh_all_analytics",
                   _fake_refresh_all_analytics), \
             patch("tools.precomputed_analytics.get_metric",
                   _fake_get_precomputed):
            asyncio.run(cache_warm_state._default_warm_fn())

        # The partial-fallback row triggered the backtester rerun.
        assert backtester_called, (
            "run_all_strategies should have been called for the unhealthy "
            "cache row, but the warm skipped it.")
        assert set_cache_called, (
            "set_strategy_cache should have been called to persist the "
            "rerun results.")

    def test_benchmark_only_cache_triggers_backtester_rerun(self):
        """Regression: the production state after PR #159's first
        merge — a strategy_results_cache row with only BENCHMARK
        populated. Under the prior health rule this read as healthy
        (BENCHMARK has monthly_returns) so the warm skipped the
        rerun. Under the new rule the missing 9 strategies make it
        unhealthy and the warm reruns run_all_strategies."""
        from tools import cache_warm_state

        backtester_called: list[bool] = []
        set_cache_called: list[bool] = []

        # The exact production shape: BENCHMARK populated, no others.
        benchmark_only = {
            "BENCHMARK": {"monthly_returns": [
                ["2024-01-31", 0.01], ["2024-02-29", 0.02]]},
        }

        async def _fake_get_latest_strategy_cache():
            return benchmark_only

        async def _fake_get_latest_strategy_hash():
            return "abc123"

        async def _fake_set_strategy_cache(*args, **kwargs):
            set_cache_called.append(True)

        async def _fake_get_full_history_async():
            import pandas as pd
            idx = pd.DatetimeIndex(["2024-01-31", "2024-02-29"])
            return {"equity_monthly": pd.Series([0.01, 0.02], index=idx)}

        def _fake_run_all_strategies(history):
            backtester_called.append(True)
            return _all_strategies_with_returns()

        async def _fake_refresh_all_analytics(data_hash):
            return None

        async def _fake_get_precomputed(data_hash, kind):
            return {"available": True}

        with patch("tools.cache.get_latest_strategy_cache",
                   _fake_get_latest_strategy_cache), \
             patch("tools.cache.get_latest_strategy_hash",
                   _fake_get_latest_strategy_hash), \
             patch("tools.cache.set_strategy_cache",
                   _fake_set_strategy_cache), \
             patch("tools.data_fetcher.get_full_history_async",
                   _fake_get_full_history_async), \
             patch("tools.backtester.run_all_strategies",
                   _fake_run_all_strategies), \
             patch("tools.precomputed_analytics.refresh_all_analytics",
                   _fake_refresh_all_analytics), \
             patch("tools.precomputed_analytics.get_metric",
                   _fake_get_precomputed):
            asyncio.run(cache_warm_state._default_warm_fn())

        assert backtester_called, (
            "A BENCHMARK-only cache row must trigger run_all_strategies "
            "— the missing 9 canonical strategies make the row unhealthy "
            "even though BENCHMARK itself is populated.")
        assert set_cache_called, (
            "set_strategy_cache must be called to persist the rerun "
            "results.")

    def test_healthy_cache_skips_backtester_rerun(self):
        from tools import cache_warm_state

        backtester_called: list[bool] = []

        # Returned by get_latest_strategy_cache — all 10 canonical
        # strategies present with monthly_returns. Anything less fails
        # the new "every canonical strategy present" health rule and
        # would trigger a rerun even when each present strategy is
        # individually populated.
        healthy_row = _all_strategies_with_returns()

        async def _fake_get_latest_strategy_cache():
            return healthy_row

        async def _fake_get_latest_strategy_hash():
            return "abc123"

        def _fake_run_all_strategies(history):
            backtester_called.append(True)
            return {}

        async def _fake_refresh_all_analytics(data_hash):
            return None

        async def _fake_get_precomputed(data_hash, kind):
            return {"available": True}

        with patch("tools.cache.get_latest_strategy_cache",
                   _fake_get_latest_strategy_cache), \
             patch("tools.cache.get_latest_strategy_hash",
                   _fake_get_latest_strategy_hash), \
             patch("tools.backtester.run_all_strategies",
                   _fake_run_all_strategies), \
             patch("tools.precomputed_analytics.refresh_all_analytics",
                   _fake_refresh_all_analytics), \
             patch("tools.precomputed_analytics.get_metric",
                   _fake_get_precomputed):
            asyncio.run(cache_warm_state._default_warm_fn())

        # A healthy cache row means no rerun — the existing data
        # already gives the analytics layer everything it needs.
        assert not backtester_called, (
            "run_all_strategies should NOT be called when the cache row "
            "is already healthy.")

    def test_backtester_failure_does_not_block_analytics_refresh(self):
        """If run_all_strategies raises (FRED outage, pipeline
        failure), the warm logs the error but still proceeds to
        refresh_all_analytics — the analytics layer runs against
        whatever's in the cache, even if stale. The user sees AN01
        / AN04 stay empty but the rest of the analytics surface
        keeps working."""
        from tools import cache_warm_state

        refresh_called: list[bool] = []

        async def _fake_get_latest_strategy_cache():
            return {}  # unhealthy → backtester rerun attempted

        async def _fake_get_latest_strategy_hash():
            return ""

        async def _fake_get_full_history_async():
            return {"equity_monthly": None}

        def _fake_run_all_strategies(history):
            raise RuntimeError("simulated pipeline failure")

        async def _fake_refresh_all_analytics(data_hash):
            refresh_called.append(True)

        async def _fake_get_precomputed(data_hash, kind):
            return None

        with patch("tools.cache.get_latest_strategy_cache",
                   _fake_get_latest_strategy_cache), \
             patch("tools.cache.get_latest_strategy_hash",
                   _fake_get_latest_strategy_hash), \
             patch("tools.data_fetcher.get_full_history_async",
                   _fake_get_full_history_async), \
             patch("tools.backtester.run_all_strategies",
                   _fake_run_all_strategies), \
             patch("tools.precomputed_analytics.refresh_all_analytics",
                   _fake_refresh_all_analytics), \
             patch("tools.precomputed_analytics.get_metric",
                   _fake_get_precomputed):
            # No exception escapes — the warm logs and continues.
            result = asyncio.run(cache_warm_state._default_warm_fn())

        # Analytics refresh still ran despite the backtester failure.
        assert refresh_called, (
            "refresh_all_analytics must run even when the backtester "
            "rerun fails; the analytics layer should not be blocked by "
            "a transient pipeline error.")
        # The warm returns a `landed` dict; both entries report False
        # because the patched get_precomputed returned None.
        assert result == {"academic_analytics": False,
                          "efficient_frontier": False}
