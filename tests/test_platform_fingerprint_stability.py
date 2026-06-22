"""tests/test_platform_fingerprint_stability.py -- PR 2 (June 22
2026).

current_data_hash() previously included strategy_results_cache
table metadata (row_count, max_date, last_updated) in its hash
inputs. Running POST /api/v1/admin/refresh-appendix-caches (or
any backtester run that writes a new strategy_results_cache row)
updated last_updated on that table, which then flipped the
platform fingerprint EVEN WHEN MARKET DATA WAS UNCHANGED.

That caused the c421fb89 -> 4de6bbbc drift observed in
production: the brief was generated when the fingerprint was
c421fb89, then the admin endpoint ran and rewrote
strategy_results_cache, flipping the fingerprint to 4de6bbbc
despite market_data_monthly and ff_factors_monthly being
identical between the two reads.

Fix: limit the fingerprint inputs to market data tables only --
market_data_monthly + ff_factors_monthly. Cache-table churn no
longer invalidates the fingerprint.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")


def _make_status(*, with_strategy_cache_change=False):
    """Builds a get_data_status() payload with the three tables.
    If with_strategy_cache_change is True, the strategy_results_cache
    last_updated timestamp shifts to a NEWER value (simulating a
    backtester run). market_data_monthly + ff_factors_monthly stay
    identical."""
    cache_last_updated = (
        "2026-06-22T01:00:00Z" if with_strategy_cache_change
        else "2026-06-20T01:00:00Z")
    return {
        "available": True,
        "tables": [
            {"name": "market_data_monthly",
             "row_count": 287,
             "max_date": "2026-05-31",
             "last_updated": "2026-06-01T00:00:00Z"},
            {"name": "ff_factors_monthly",
             "row_count": 287,
             "max_date": "2026-05-31",
             "last_updated": "2026-06-01T00:00:00Z"},
            {"name": "strategy_results_cache",
             "row_count": 1,
             "max_date": None,
             "last_updated": cache_last_updated},
            # Unrelated tables that have never been in the input
            # set should also never affect the fingerprint.
            {"name": "users",
             "row_count": 10,
             "max_date": None,
             "last_updated": "2026-06-22T00:00:00Z"},
        ],
    }


class TestPlatformFingerprintStability:
    """The contract: when market_data_monthly and
    ff_factors_monthly are identical, current_data_hash() MUST
    return the same value -- regardless of strategy_results_cache
    state or any other table's churn."""

    def test_fingerprint_stable_across_strategy_cache_refresh(
        self, monkeypatch,
    ):
        """The exact production scenario: market data unchanged,
        strategy_results_cache rewritten. Fingerprint must NOT
        flip."""
        import asyncio
        from tools.audit_assembler import current_data_hash

        before_status = _make_status(with_strategy_cache_change=False)
        after_status = _make_status(with_strategy_cache_change=True)

        async def _before():
            return before_status

        async def _after():
            return after_status

        monkeypatch.setattr(
            "tools.cache.get_data_status", _before)
        before = asyncio.run(current_data_hash())

        monkeypatch.setattr(
            "tools.cache.get_data_status", _after)
        after = asyncio.run(current_data_hash())

        assert before, "expected non-empty fingerprint"
        assert before == after, (
            f"fingerprint flipped on strategy cache refresh: "
            f"{before} -> {after}; market data was unchanged")

    def test_fingerprint_changes_when_market_data_changes(
        self, monkeypatch,
    ):
        """The other direction: if market_data_monthly does
        change (new month ingested), the fingerprint MUST
        change. Without this, the audit would serve stale
        results after a real data drift."""
        import asyncio
        from tools.audit_assembler import current_data_hash

        before_status = _make_status()
        after_status = _make_status()
        # New month added to market_data_monthly.
        for t in after_status["tables"]:
            if t["name"] == "market_data_monthly":
                t["row_count"] = 288
                t["max_date"] = "2026-06-30"
                t["last_updated"] = "2026-07-01T00:00:00Z"

        async def _before():
            return before_status

        async def _after():
            return after_status

        monkeypatch.setattr(
            "tools.cache.get_data_status", _before)
        before = asyncio.run(current_data_hash())

        monkeypatch.setattr(
            "tools.cache.get_data_status", _after)
        after = asyncio.run(current_data_hash())

        assert before != after, (
            "fingerprint did not change despite new market_data "
            "row -- audit would wrongly serve stale results")

    def test_fingerprint_changes_when_ff_factors_changes(
        self, monkeypatch,
    ):
        """Same contract for the second market data table:
        ff_factors_monthly changes must flip the fingerprint."""
        import asyncio
        from tools.audit_assembler import current_data_hash

        before_status = _make_status()
        after_status = _make_status()
        for t in after_status["tables"]:
            if t["name"] == "ff_factors_monthly":
                t["row_count"] = 288
                t["max_date"] = "2026-06-30"
                t["last_updated"] = "2026-07-01T00:00:00Z"

        async def _before():
            return before_status

        async def _after():
            return after_status

        monkeypatch.setattr(
            "tools.cache.get_data_status", _before)
        before = asyncio.run(current_data_hash())

        monkeypatch.setattr(
            "tools.cache.get_data_status", _after)
        after = asyncio.run(current_data_hash())

        assert before != after, (
            "fingerprint did not change despite new ff_factors "
            "row -- audit would wrongly serve stale results")

    def test_strategy_cache_metadata_excluded_from_hash_inputs(self):
        """Source-level pin: the canonical input set must NOT
        include strategy_results_cache (or any other cache /
        derived table). The hash inputs must be market data
        tables only. A future PR that re-adds cache table
        metadata to the input list would silently revert this
        fix; pin it at the source level."""
        import inspect
        from tools import audit_assembler
        src = inspect.getsource(audit_assembler.current_data_hash)
        # The tuple must contain ONLY market data tables.
        assert '"market_data_monthly"' in src
        assert '"ff_factors_monthly"' in src
        # And must NOT include strategy_results_cache or any
        # other derived/cache table.
        # The hash-inputs tuple is the `relevant = (...)` line.
        # Find that exact line and confirm cache tables are not
        # in it.
        start = src.find("relevant = (")
        assert start >= 0, "relevant tuple not found"
        end = src.find(")", start)
        relevant_block = src[start:end + 1]
        assert "strategy_results_cache" not in relevant_block, (
            "strategy_results_cache must not be in "
            "current_data_hash inputs -- derived state churn "
            "must not invalidate the market data fingerprint")
        assert "analytics_metrics_cache" not in relevant_block
        assert "agent_interactions" not in relevant_block
