"""
tests/test_strategy_cache_empty_guard.py — May 28 2026.

The strategy-results cache must NOT overwrite a known-good row with a
run where every strategy is a fallback (empty monthly_returns). AN01
and AN04 read this cache and produce empty downstream payloads when
every strategy lacks return data; the guard preserves the prior row
in that case and surfaces the broken run via a structured log line.

Two layers:
  1. Backtester fallback path emits an explicit `monthly_returns: []`
     on every fallback entry — the field is always present, never
     key-missing.
  2. set_strategy_cache refuses to write when every strategy has empty
     monthly_returns (the all-fallback signature).
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


# ── Backtester fallback emits explicit monthly_returns: [] ────────────────────

class TestBacktesterFallbackHasExplicitEmptyMonthlyReturns:
    """A strategy that raises during run_all_strategies must produce
    a fallback dict with an EXPLICIT `monthly_returns: []` so the
    "no return data" state is legible to anyone inspecting the cached
    results_json — never the prior key-missing form."""

    def _broken_history(self) -> dict:
        """A history dict shaped just enough that the strategy runners
        can crash early — every series is empty so any access raises."""
        import pandas as pd
        empty = pd.Series([], dtype=float)
        return {
            "equity_monthly": empty, "ig_monthly": empty, "hy_monthly": empty,
            "risk_free_monthly": empty,
            "equity_daily": empty, "ig_daily": empty, "hy_daily": empty,
            "risk_free_daily": empty,
            "signals": {"vix": empty, "yield_curve": empty},
            "ff_factors": [],
        }

    def test_every_fallback_carries_empty_monthly_returns_key(self):
        from tools.backtester import run_all_strategies

        results = run_all_strategies(self._broken_history())
        # Every strategy in the run produced a result dict.
        assert len(results) >= 1
        # Every strategy that fell back to a mock carries the explicit
        # `monthly_returns: []` key — not the prior key-missing form.
        for name, r in results.items():
            if r.get("error"):
                assert "monthly_returns" in r, (
                    f"{name}: fallback missing explicit monthly_returns key"
                )
                assert r["monthly_returns"] == [], (
                    f"{name}: fallback should carry empty list, got "
                    f"{r['monthly_returns']!r}"
                )


# ── set_strategy_cache empty-results guard ───────────────────────────────────

class TestSetStrategyCacheRefusesAllEmpty:
    """When every strategy in `results` has empty monthly_returns
    (the all-fallback signature), set_strategy_cache must NOT
    overwrite the prior cache row. A partial fallback (some empty,
    some real) still writes through — the downstream analytics
    correctly skip empty-series strategies row by row."""

    def _all_empty_results(self) -> dict[str, dict]:
        return {
            "BENCHMARK":        {"sharpe_ratio": 0.0, "monthly_returns": [],
                                 "error": "boom"},
            "CLASSIC_60_40":    {"sharpe_ratio": 0.0, "monthly_returns": [],
                                 "error": "boom"},
            "REGIME_SWITCHING": {"sharpe_ratio": 0.0, "monthly_returns": [],
                                 "error": "boom"},
        }

    def _one_empty_results(self) -> dict[str, dict]:
        return {
            "BENCHMARK": {"sharpe_ratio": 0.55, "monthly_returns": [
                ["2024-01-31", 0.01], ["2024-02-29", -0.02],
            ]},
            "CLASSIC_60_40": {"sharpe_ratio": 0.0, "monthly_returns": [],
                              "error": "boom"},
        }

    def test_all_empty_results_does_not_write(self):
        from tools.cache import set_strategy_cache

        # Patch _DB_AVAILABLE to True so the guard branch runs (the
        # function early-returns when DB is unavailable, which would
        # mask the guard's behaviour). Patch AsyncSessionLocal so we
        # observe whether a session was ever opened.
        with patch("tools.cache._DB_AVAILABLE", True), \
             patch("tools.cache.AsyncSessionLocal") as mock_session:
            asyncio.run(set_strategy_cache("hash_all_empty",
                                           self._all_empty_results()))
            # No session opened — the guard refused the write before
            # touching the database.
            assert not mock_session.called, (
                "Cache write should have been refused; AsyncSessionLocal "
                "was opened anyway."
            )

    def test_partial_fallback_still_writes(self):
        """One strategy with real monthly_returns is enough to clear
        the guard — the downstream analytics will skip the empty
        strategy on a per-row basis and still produce a usable
        factor_loadings / regime_conditional table from the real one."""
        from tools.cache import set_strategy_cache

        async def _go():
            session = MockSession()
            with patch("tools.cache._DB_AVAILABLE", True), \
                 patch("tools.cache.AsyncSessionLocal",
                       return_value=session):
                await set_strategy_cache("hash_partial",
                                         self._one_empty_results())
                return session

        session = asyncio.run(_go())
        # The session was opened and execute() was called — the
        # guard did not block the write.
        assert session.execute_calls >= 1

    def test_empty_results_dict_does_not_trigger_guard(self):
        """An empty results dict is a degenerate input — the guard
        is keyed on "every strategy is empty" (`empties == total`),
        which is vacuously true for total=0. The function falls
        through to the write path; the OPERATIONAL guard against
        zero-strategy writes lives elsewhere (the empty INSERT is
        harmless)."""
        # No assertion on guard behaviour here — this test exists to
        # PIN the contract: an empty dict does not raise inside the
        # guard. Without the explicit `if results:` check, the
        # `len(results)` and `sum(...)` against an empty dict would
        # silently total to 0 == 0 and refuse the write. With the
        # check in place, an empty dict falls through to the write.
        from tools.cache import set_strategy_cache

        async def _go():
            session = MockSession()
            with patch("tools.cache._DB_AVAILABLE", True), \
                 patch("tools.cache.AsyncSessionLocal",
                       return_value=session):
                await set_strategy_cache("hash_empty", {})
                return session

        session = asyncio.run(_go())
        # The write path ran (no guard short-circuit) — the session
        # was opened.
        assert session.execute_calls >= 1


class MockSession:
    """Async context manager mock — records execute() calls so the
    test can assert whether the cache write path was entered."""

    def __init__(self):
        self.execute_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, *args, **kwargs):
        self.execute_calls += 1
        return MockResult()

    async def commit(self):
        pass


class MockResult:
    def fetchone(self):
        return None
