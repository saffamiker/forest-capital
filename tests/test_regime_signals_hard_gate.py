"""tests/test_regime_signals_hard_gate.py -- June 27 2026.

Pins the deck-tier regime_signals freshness HARD GATE added to:
  * _generate_deck_document   (full deck generation)
  * council_academic_review   (when document_type=presentation_deck)
  * post_light_refresh        (the cache-warming workflow)

Per the spec, the brief / appendix keep the existing graceful em-dash
fallback (their docs don't surface a live CIO recommendation). The
deck blocks because slides 7 + 11 include a live recommendation the
panel will read as current.

Key contract pins:

  1. The helper has a HARD 10-second timeout. detect_current_regime()
     hits live market data APIs that can hang for minutes; without
     the timeout the blocking 503 itself could take minutes to
     surface, worse UX than the original stale-cache behaviour.
  2. The blocking error message is the user-spec string verbatim --
     downstream toast / banner copy keys off this exact text.
  3. ENVIRONMENT=test short-circuits to (True, None) so the existing
     ~3900-test suite doesn't have to mock the cache for every gen
     spec.

Test groups:

  TestRegimeHelperShape
    The helper exports + constants are present and have the
    expected values.

  TestRegimeHelperTestEnvShortCircuit
    ENVIRONMENT=test returns (True, None) without touching the
    cache or the detector.

  TestRegimeHelperTimeout
    A hanging detect_current_regime() is bounded by the 10s timeout
    (lowered to 0.5s for the test) -- the coroutine returns within
    the timeout regardless of the worker thread's eventual
    completion.

  TestRegimeHelperCacheHit
    A fresh cache row short-circuits the detect call.

  TestRegimeHelperCacheMissThenSuccess
    A miss followed by a successful detect returns (True, signals)
    and writes through to the cache.

  TestRegimeBlockingErrorMessage
    The error string carries the user-spec key phrases so frontend
    copy can pattern-match.
"""
from __future__ import annotations

import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── Helper shape ────────────────────────────────────────────────────


class TestRegimeHelperShape:

    def test_helper_exported(self):
        from main import _regime_signals_fresh_or_refresh
        assert callable(_regime_signals_fresh_or_refresh)

    def test_timeout_constant_is_ten_seconds(self):
        from main import _REGIME_REFRESH_TIMEOUT_S
        assert _REGIME_REFRESH_TIMEOUT_S == 10.0

    def test_blocking_error_constant_present(self):
        from main import _REGIME_BLOCKING_ERROR_DETAIL
        # User-spec key phrases.
        assert "regime signals unavailable" in (
            _REGIME_BLOCKING_ERROR_DETAIL.lower())
        assert "live cio recommendation" in (
            _REGIME_BLOCKING_ERROR_DETAIL.lower())
        assert "try again in a few minutes" in (
            _REGIME_BLOCKING_ERROR_DETAIL.lower())


# ── Test-env short-circuit ──────────────────────────────────────────


class TestRegimeHelperTestEnvShortCircuit:

    @pytest.mark.asyncio
    async def test_test_env_returns_true_none(self):
        from main import _regime_signals_fresh_or_refresh
        ok, signals = await _regime_signals_fresh_or_refresh()
        assert ok is True
        assert signals is None


# ── Timeout guard ────────────────────────────────────────────────────


class TestRegimeHelperTimeout:

    @pytest.mark.asyncio
    async def test_hanging_detect_bounded_by_timeout(
            self, monkeypatch):
        """Repro of the user's primary risk: detect_current_regime()
        hangs on a live market-data API call. The async caller MUST
        unblock within the configured timeout regardless of what
        the worker thread does. We can't kill the underlying thread
        (Python doesn't support thread cancellation), but the
        coroutine returns and the 503 fires on schedule."""
        import asyncio as _asyncio
        import time as _time
        import main as m
        # Force non-test env so the helper goes through the detect
        # path.
        monkeypatch.setattr(m, "ENVIRONMENT", "production")
        # Lower the timeout so the spec is fast.
        monkeypatch.setattr(
            m, "_REGIME_REFRESH_TIMEOUT_S", 0.5)
        # Force cache miss.
        from tools import cache as cache_mod

        async def _miss():
            return None

        monkeypatch.setattr(cache_mod, "get_regime_cache", _miss)

        # Force detect to hang.
        from tools import regime_detector as rd

        def _hang():
            _time.sleep(5.0)
            return {}

        monkeypatch.setattr(
            rd, "detect_current_regime", _hang)

        loop_start = _asyncio.get_event_loop().time()
        ok, signals = await m._regime_signals_fresh_or_refresh()
        coro_elapsed = (
            _asyncio.get_event_loop().time() - loop_start)
        assert ok is False
        assert signals is None
        # Coroutine MUST return within the configured timeout
        # (with a generous 1.5x slack for test scheduling
        # jitter). The underlying worker thread keeps running but
        # the async event loop is unblocked -- that's the
        # contract the spec needs.
        assert coro_elapsed < 1.5, (
            f"coro should have returned at {m._REGIME_REFRESH_TIMEOUT_S}s "
            f"timeout; took {coro_elapsed:.2f}s")


# ── Cache hit short-circuits detect ─────────────────────────────────


class TestRegimeHelperCacheHit:

    @pytest.mark.asyncio
    async def test_cache_hit_returns_signals_without_calling_detect(
            self, monkeypatch):
        import main as m
        monkeypatch.setattr(m, "ENVIRONMENT", "production")
        from tools import cache as cache_mod
        cached = {
            "threshold_regime": "BULL", "vix_level": 13.0,
            "yield_curve_slope": 0.5, "credit_spread": 0.95,
        }

        async def _hit():
            return cached

        monkeypatch.setattr(cache_mod, "get_regime_cache", _hit)
        # If detect IS called, raise so the test fails loudly.
        from tools import regime_detector as rd
        sentinel = {"called": False}

        def _should_not_call():
            sentinel["called"] = True
            raise AssertionError("detect must not be called on hit")

        monkeypatch.setattr(
            rd, "detect_current_regime", _should_not_call)
        ok, signals = await m._regime_signals_fresh_or_refresh()
        assert ok is True
        assert signals == cached
        assert sentinel["called"] is False


# ── Cache miss + successful detect ──────────────────────────────────


class TestRegimeHelperCacheMissThenSuccess:

    @pytest.mark.asyncio
    async def test_miss_then_successful_detect_writes_cache(
            self, monkeypatch):
        import main as m
        monkeypatch.setattr(m, "ENVIRONMENT", "production")
        from tools import cache as cache_mod

        async def _miss():
            return None

        write_calls: list[tuple[dict, int]] = []

        async def _set(regime_data, ttl_minutes=15):
            write_calls.append((regime_data, ttl_minutes))

        monkeypatch.setattr(cache_mod, "get_regime_cache", _miss)
        monkeypatch.setattr(cache_mod, "set_regime_cache", _set)

        from tools import regime_detector as rd
        fresh = {"threshold_regime": "BEAR", "vix_level": 26.5}

        def _ok():
            return fresh

        monkeypatch.setattr(
            rd, "detect_current_regime", _ok)

        ok, signals = await m._regime_signals_fresh_or_refresh()
        assert ok is True
        assert signals == fresh
        # Write-through should fire with the spec's 15-min TTL.
        assert len(write_calls) == 1
        data, ttl = write_calls[0]
        assert data == fresh
        assert ttl == 15


# ── Blocking error message contract ────────────────────────────────


class TestRegimeBlockingErrorMessage:

    def test_user_spec_message_verbatim(self):
        """Frontend / e2e copy keys off this exact string. Pin the
        full user-spec phrasing so a casual edit doesn't drift it."""
        from main import _REGIME_BLOCKING_ERROR_DETAIL
        spec = (
            "Live regime signals unavailable. The deck includes a "
            "live CIO recommendation that requires current data. "
            "Please try again in a few minutes.")
        assert _REGIME_BLOCKING_ERROR_DETAIL == spec
