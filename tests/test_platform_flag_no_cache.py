"""tests/test_platform_flag_no_cache.py -- June 28 2026.

Regression pins for the is_defer_substitution_enabled_sync
cache-poisoning fix. The prior implementation maintained a
process-wide _SYNC_CACHE that never invalidated -- a single
False seed (from any call from a running-loop context)
permanently poisoned the flag for every subsequent call
including from legitimate harness_narrative-in-asyncio.to_thread
contexts.

The replacement always queries. Two cases:
  1. No running loop -- asyncio.run directly.
  2. Running loop -- delegate to a worker-thread that opens
     its own asyncio.run via concurrent.futures.
"""
from __future__ import annotations

import asyncio
import os
from unittest import mock

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


class TestNoCachePoisoning:

    def test_no_cache_consulted_on_repeated_calls(self):
        """Source-inspection pin: the sync function MUST NOT
        return early based on _SYNC_CACHE.get() -- that's the
        poisoning vector. The replacement always queries."""
        import inspect
        from tools.platform_flags import (
            is_defer_substitution_enabled_sync,
        )
        src = inspect.getsource(is_defer_substitution_enabled_sync)
        # The function body must NOT contain a cache-hit
        # early-return at the top.
        assert "_SYNC_CACHE.get(" not in src, (
            "is_defer_substitution_enabled_sync must not read "
            "the poisonable _SYNC_CACHE")
        # And must NOT write to it (would re-introduce the
        # poisoning vector).
        assert "_SYNC_CACHE[" not in src

    def test_repeat_calls_each_hit_db(self):
        """Behaviour pin: invoking sync N times triggers N reads
        of the underlying flag (no caching). Verifies via a
        patched _read_flag that the call count matches."""
        from tools.platform_flags import (
            is_defer_substitution_enabled_sync,
            reset_flag_cache,
        )
        reset_flag_cache()  # no-op now, but kept for parity
        async def stub_true(key, default=False):
            return True
        with mock.patch(
                "tools.platform_flags._read_flag",
                side_effect=stub_true) as m:
            r1 = is_defer_substitution_enabled_sync()
            r2 = is_defer_substitution_enabled_sync()
            r3 = is_defer_substitution_enabled_sync()
        assert r1 is True
        assert r2 is True
        assert r3 is True
        assert m.call_count == 3, (
            f"expected 3 DB reads, got {m.call_count}")

    def test_recovers_from_flag_flip(self):
        """The core poisoning regression: if the DB returns
        False once + True later, the sync helper must return
        True on the second call. The prior implementation
        returned cached False forever."""
        from tools.platform_flags import (
            is_defer_substitution_enabled_sync,
            reset_flag_cache,
        )
        reset_flag_cache()
        flag_value = [False]
        async def stub_dynamic(key, default=False):
            return flag_value[0]
        with mock.patch(
                "tools.platform_flags._read_flag",
                side_effect=stub_dynamic):
            assert (
                is_defer_substitution_enabled_sync() is False)
            flag_value[0] = True
            assert (
                is_defer_substitution_enabled_sync() is True)
            flag_value[0] = False
            assert (
                is_defer_substitution_enabled_sync() is False)


class TestThreadContextHandling:

    def test_works_from_no_running_loop_context(self):
        """Direct sync call from a plain pytest test
        (no running loop). Should hit the asyncio.run branch."""
        from tools.platform_flags import (
            is_defer_substitution_enabled_sync,
        )
        async def stub_true(key, default=False):
            return True
        with mock.patch(
                "tools.platform_flags._read_flag",
                side_effect=stub_true):
            assert (
                is_defer_substitution_enabled_sync() is True)

    def test_works_from_running_loop_context(self):
        """Reproduces the asyncio.to_thread sibling-thread
        scenario: a running loop in the parent thread + sync
        helper invoked from a worker thread that DOES have a
        loop visible. Must NOT fail-open to False."""
        from tools.platform_flags import (
            is_defer_substitution_enabled_sync,
        )
        async def stub_true(key, default=False):
            return True

        async def run_with_loop():
            with mock.patch(
                    "tools.platform_flags._read_flag",
                    side_effect=stub_true):
                # Direct call from within an async context --
                # asyncio.get_running_loop() returns the live
                # loop, so the worker-thread fallback path
                # MUST fire instead of returning False.
                return is_defer_substitution_enabled_sync()

        result = asyncio.run(run_with_loop())
        assert result is True, (
            "Sync helper must succeed even when called from a "
            "running-loop context -- prior implementation "
            "fail-opened to False here")


class TestResetFlagCacheBackwardCompat:

    def test_reset_flag_cache_no_longer_raises(self):
        """API-compat: existing tests call reset_flag_cache()
        during setup/teardown. Must remain callable + non-
        raising even though the cache is no longer consulted."""
        from tools.platform_flags import reset_flag_cache
        # Must not raise.
        reset_flag_cache()
        reset_flag_cache()
