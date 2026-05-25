"""Pins the UAT methodology-audit staleness fix (May 24 2026).

Bug: methodology audit reported "stale" even immediately after a
successful run. Root cause was three independent code paths each
computing a different strategy_hash for the same underlying data:

  (a) /api/backtest/compare wrote strategy_results_cache with
      `str(monthly.index[-1].date())` → "2025-12-31"
  (b) _current_strategy_hash used `str(monthly.index[-1])` →
      "2025-12-31 00:00:00" (Timestamp str form)
  (c) /api/qa/audit READ `monthly[-1].get("date")` from a LIST-of-
      pairs payload that is NEVER a dict, falling through to
      "unknown" every time.

is_audit_current() compared the strategy_hash side from (a) against
the qa_results_cache hash from (c) — the values never matched, so
qa_current was False forever.

FIX: collapse every QA-side hash computation onto the canonical
get_latest_strategy_hash() helper, which reads the actual value
stored in strategy_results_cache. The two halves of the comparison
are guaranteed to match by construction whenever the audit
verified the latest data.

These tests pin the contract: the QA audit endpoint stores the
SAME hash value as strategy_results_cache, and _current_strategy_hash
returns that canonical value. A regression on either path would
revive the staleness bug.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


class TestCurrentStrategyHashUsesCanonicalSource:
    """_current_strategy_hash must return the value get_latest_strategy_hash()
    reads from strategy_results_cache — NOT a freshly-recomputed hash from
    history. The whole staleness comparison depends on this equality."""

    def test_returns_canonical_hash_when_strategy_cache_populated(self):
        import asyncio
        import main as main_mod
        from tools import cache as cache_mod

        # Force the canonical helper to return a known string. The
        # in-process memo would mask repeated calls, so clear it first.
        cache_mod._hash_memo_clear()

        async def _fake_canonical():
            return "canonical_hash_abc"

        async def _fake_cache(_h):
            return {"BENCHMARK": {"name": "BENCHMARK"}}

        with patch.object(cache_mod, "get_latest_strategy_hash",
                          side_effect=_fake_canonical):
            with patch.object(cache_mod, "get_strategy_cache",
                              side_effect=_fake_cache):
                h, cached = asyncio.run(main_mod._current_strategy_hash())

        assert h == "canonical_hash_abc"
        assert cached is not None
        assert "BENCHMARK" in cached

    def test_returns_empty_string_when_no_strategy_cache(self):
        # No row in strategy_results_cache → canonical helper returns
        # None → _current_strategy_hash returns "" so caller's bool
        # gate (`if strategy_hash:`) trips False cleanly.
        import asyncio
        import main as main_mod
        from tools import cache as cache_mod

        cache_mod._hash_memo_clear()

        async def _none():
            return None

        with patch.object(cache_mod, "get_latest_strategy_hash",
                          side_effect=_none):
            h, cached = asyncio.run(main_mod._current_strategy_hash())

        assert h == ""
        assert cached is None


class TestQaAuditWritesCanonicalHashShape:
    """The fix replaced the broken monthly[-1].get("date") parse with
    get_latest_strategy_hash(). The /api/qa/audit endpoint must read
    the canonical value via that helper, NOT recompute its own."""

    def test_qa_audit_endpoint_source_uses_canonical_helper(self):
        # Source-level check: scan main.py for the EXECUTABLE form of
        # the OLD broken pattern. The defensive comment block ABOUT
        # the bug also contains the string, so we match the precise
        # broken call (with the default arg) — which only appears as
        # executable code, never in prose.
        import pathlib
        import re
        main_path = pathlib.Path(__file__).resolve().parents[1] / "backend" / "main.py"
        text = main_path.read_text(encoding="utf-8")
        # Strip comment lines before scanning so the bug's documentation
        # is allowed to reference the pattern.
        code_only = "\n".join(
            line for line in text.splitlines()
            if not line.lstrip().startswith("#")
        )
        # The exact broken call (with the "unknown" default) must not
        # appear in executable code.
        assert not re.search(
            r'monthly\[-1\]\.get\("date",\s*"unknown"\)', code_only,
        ), (
            "The QA audit endpoint must not parse monthly_returns as "
            "a dict — the field is a list of [iso_date, return] pairs. "
            "Use get_latest_strategy_hash() instead."
        )
        # The fix MUST use get_latest_strategy_hash inside the QA
        # audit endpoint. The import lives inside the request handler
        # (lazy import for circular-import safety).
        assert "get_latest_strategy_hash" in code_only, (
            "The QA audit endpoint must use get_latest_strategy_hash() "
            "as the canonical hash source. A regression that drops "
            "the import is the staleness bug returning."
        )


class TestIsAuditCurrentEqualityHolds:
    """End-to-end shape check: when the QA cache and strategy cache
    are written using the SAME hash (the post-fix contract),
    is_audit_current() reports qa_current=True. Verifies the fix
    closes the loop the bug opened."""

    def test_matching_hashes_produce_qa_current_true(self):
        import asyncio
        from tools.audit_assembler import is_audit_current
        from tools import cache as cache_mod
        from tools import audit_engine

        # Same strategy and QA hashes, plus matching audit data_hash —
        # the post-fix steady state.
        cache_mod._hash_memo_clear()

        async def _strat():
            return "matching_hash_xyz"
        async def _qa():
            return "matching_hash_xyz"
        async def _data():
            return "data_hash_abc"
        async def _last():
            return "data_hash_abc"

        with patch.object(cache_mod, "get_latest_strategy_hash",
                          side_effect=_strat):
            with patch.object(cache_mod, "get_latest_qa_hash",
                              side_effect=_qa):
                with patch("tools.audit_assembler.current_data_hash",
                           side_effect=_data):
                    with patch.object(audit_engine,
                                      "get_last_completed_audit_hash",
                                      side_effect=_last):
                        result = asyncio.run(is_audit_current())

        assert result["qa_current"] is True
        assert result["statistical_current"] is True
        assert result["is_current"] is True

    def test_diverged_qa_hash_produces_qa_current_false(self):
        # The pre-fix shape: strategy hash and QA hash differ.
        # Confirms is_audit_current correctly reports stale when
        # the two hashes don't match (the diagnostic stays sharp).
        import asyncio
        from tools.audit_assembler import is_audit_current
        from tools import cache as cache_mod
        from tools import audit_engine

        cache_mod._hash_memo_clear()

        async def _strat():
            return "current_hash"
        async def _qa():
            return "stale_hash"
        async def _data():
            return "data_hash"
        async def _last():
            return "data_hash"

        with patch.object(cache_mod, "get_latest_strategy_hash",
                          side_effect=_strat):
            with patch.object(cache_mod, "get_latest_qa_hash",
                              side_effect=_qa):
                with patch("tools.audit_assembler.current_data_hash",
                           side_effect=_data):
                    with patch.object(audit_engine,
                                      "get_last_completed_audit_hash",
                                      side_effect=_last):
                        result = asyncio.run(is_audit_current())

        assert result["qa_current"] is False
        assert result["statistical_current"] is True
        assert result["is_current"] is False
