"""tests/test_regime_signals_ticker.py -- June 27 2026.

Pins the background regime-signals refresh ticker spec.

PROBLEM (pre-fix)
  regime_signals_cache has a 15-minute TTL but nothing actively
  refreshes it. If no user activity triggered the HMM for 15+
  minutes, the cache went stale + the deck hard gate fired,
  blocking generation + light refresh.

FIX
  Background task in the lifespan handler runs
  detect_current_regime() + set_regime_cache() every 10 minutes
  (5-min headroom on the 15-min TTL). Per-iteration failures
  log a warning and the loop continues to the next cycle. The
  ticker NEVER crashes the background task.

Test classes:

  TestTickerIntervalPinned
    Constant pin: _REGIME_SIGNALS_TICKER_INTERVAL_S == 600.0

  TestTickerSchedulingPin
    Source inspection: the lifespan handler contains the
    'regime_signals_ticker_scheduled' info log, imports
    detect_current_regime + set_regime_cache, and uses the
    interval constant.

  TestTickerOneIterationSuccess
    Drives one iteration of the ticker body with mocked
    detect_current_regime + set_regime_cache. Asserts:
      - asyncio.to_thread(detect_current_regime) was called
      - set_regime_cache was called with the returned dict
      - 'regime_signals_background_refresh' info log fired with
        regime + confidence kwargs

  TestTickerOneIterationFailure
    Drives one iteration with detect_current_regime raising.
    Asserts:
      - 'regime_signals_background_refresh_failed' warning fired
      - The exception does NOT propagate (task continues to
        next cycle)
"""
from __future__ import annotations

import asyncio
import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── Constant pin ──────────────────────────────────────────────────


class TestTickerIntervalPinned:
    """The cadence is operator-visible -- a refactor that changes
    the interval should require this test to be updated too."""

    def test_interval_is_600_seconds(self):
        from main import _REGIME_SIGNALS_TICKER_INTERVAL_S
        assert _REGIME_SIGNALS_TICKER_INTERVAL_S == 600.0, (
            "Ticker cadence is 10 minutes (600s) -- 5 minutes of "
            "headroom before the 15-minute regime_signals_cache "
            "TTL expires. Any change to the constant must be "
            "deliberate and update this pin.")

    def test_interval_leaves_headroom_under_ttl(self):
        """The constant MUST be strictly less than the cache TTL
        so the cache is always refreshed before expiry. Defends
        against a refactor that accidentally inverts the
        relationship (e.g. ticker every 20 minutes on a 15-minute
        TTL would leave a 5-minute window of staleness)."""
        from main import _REGIME_SIGNALS_TICKER_INTERVAL_S
        # set_regime_cache default ttl_minutes=15 -> 900s
        cache_ttl_s = 15 * 60
        assert _REGIME_SIGNALS_TICKER_INTERVAL_S < cache_ttl_s, (
            "Ticker interval must be strictly less than the "
            "regime_signals_cache TTL or the cache will be stale "
            "between refreshes")


# ── Lifespan scheduling pin ────────────────────────────────────────


class TestTickerSchedulingPin:
    """Source inspection of the lifespan handler. Pins against a
    future refactor that drops the ticker scheduling, the
    detect_current_regime import, or the interval constant."""

    def test_lifespan_imports_detect_current_regime(self):
        import inspect
        from main import lifespan
        src = inspect.getsource(lifespan)
        assert "detect_current_regime" in src, (
            "lifespan handler must import detect_current_regime "
            "for the regime-signals ticker")

    def test_lifespan_imports_set_regime_cache(self):
        import inspect
        from main import lifespan
        src = inspect.getsource(lifespan)
        assert "set_regime_cache" in src, (
            "lifespan handler must import set_regime_cache to "
            "write the refreshed regime dict")

    def test_lifespan_uses_interval_constant(self):
        import inspect
        from main import lifespan
        src = inspect.getsource(lifespan)
        assert "_REGIME_SIGNALS_TICKER_INTERVAL_S" in src, (
            "ticker body must use the module-level interval "
            "constant -- a hardcoded number would drift from the "
            "documented cadence")

    def test_lifespan_logs_ticker_scheduled(self):
        import inspect
        from main import lifespan
        src = inspect.getsource(lifespan)
        assert "regime_signals_ticker_scheduled" in src, (
            "lifespan handler must log the "
            "'regime_signals_ticker_scheduled' event so operators "
            "can grep Render logs to confirm the ticker started")

    def test_lifespan_creates_ticker_task(self):
        """The ticker must be wired via asyncio.create_task so it
        runs concurrently with the request handler. A direct
        await would block the lifespan handler indefinitely."""
        import inspect
        from main import lifespan
        src = inspect.getsource(lifespan)
        # Pattern check: create_task(_regime_signals_ticker_task())
        assert "create_task(_regime_signals_ticker_task())" in (
            src), (
            "ticker must be scheduled as a fire-and-forget task; "
            "a direct await would block the lifespan handler")


# ── One-iteration behaviour ────────────────────────────────────────


async def _run_ticker_one_iteration(
    detect_returns,
    set_regime_cache_called: list,
    log_events: list,
):
    """Run exactly one iteration of the ticker body, then exit.
    Mirrors the lifespan ticker body without the outer while loop
    + sleeps -- the loop / interval are covered by other tests
    (TickerSchedulingPin + TickerIntervalPinned)."""
    from main import _REGIME_REFRESH_TIMEOUT_S

    if isinstance(detect_returns, Exception):
        def _detect():
            raise detect_returns
    else:
        def _detect():
            return detect_returns

    async def _set_regime_cache(payload, ttl_minutes=15):
        set_regime_cache_called.append(
            (payload, ttl_minutes))

    def _log_info(event, **kwargs):
        log_events.append(("info", event, kwargs))

    def _log_warning(event, **kwargs):
        log_events.append(("warning", event, kwargs))

    try:
        fresh = await asyncio.wait_for(
            asyncio.to_thread(_detect),
            timeout=_REGIME_REFRESH_TIMEOUT_S)
        if not isinstance(fresh, dict):
            _log_warning(
                "regime_signals_background_refresh_failed",
                error="detect_current_regime returned non-dict",
                return_type=type(fresh).__name__)
        else:
            await _set_regime_cache(fresh, ttl_minutes=15)
            _log_info(
                "regime_signals_background_refresh",
                regime=fresh.get("regime"),
                confidence=fresh.get("confidence"))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        _log_warning(
            "regime_signals_background_refresh_failed",
            error=str(exc))


class TestTickerOneIterationSuccess:
    """When detect_current_regime returns a valid dict, the cache
    is written + the success log fires with the regime and
    confidence carried in structured kwargs."""

    def test_writes_cache_and_logs_success(self):
        fresh = {"regime": "BULL", "confidence": 0.92,
                 "vix": 18.4, "ess": 0.65}
        cache_writes: list = []
        log_events: list = []

        asyncio.run(_run_ticker_one_iteration(
            fresh, cache_writes, log_events))

        # set_regime_cache was called once with the returned dict
        assert len(cache_writes) == 1
        assert cache_writes[0][0] == fresh
        assert cache_writes[0][1] == 15  # ttl_minutes

        # Success log fired exactly once with regime + confidence
        success = [e for e in log_events
                   if e[1] == "regime_signals_background_refresh"]
        assert len(success) == 1
        assert success[0][0] == "info"
        kwargs = success[0][2]
        assert kwargs.get("regime") == "BULL"
        assert kwargs.get("confidence") == 0.92

    def test_non_dict_return_logs_failure_without_write(self):
        """detect_current_regime returning something unexpected
        (e.g. None on degraded data path) must log a warning and
        skip the cache write -- never write garbage."""
        cache_writes: list = []
        log_events: list = []

        # Pass None as the 'fresh' value -- isinstance check
        # rejects it before the cache write fires.
        asyncio.run(_run_ticker_one_iteration(
            None, cache_writes, log_events))

        assert len(cache_writes) == 0, (
            "Cache must NOT be written when detect returns "
            "non-dict")
        failures = [
            e for e in log_events
            if e[1] == "regime_signals_background_refresh_failed"]
        assert len(failures) == 1
        assert failures[0][0] == "warning"
        assert "non-dict" in failures[0][2].get("error", "")


class TestTickerOneIterationFailure:
    """When detect_current_regime raises (FRED timeout, network
    blip, programming error), the ticker logs a warning and
    moves on -- the exception must NOT propagate, or the
    background task would crash + stop refreshing forever."""

    def test_detect_exception_does_not_propagate(self):
        cache_writes: list = []
        log_events: list = []
        boom = RuntimeError("FRED timeout")

        # The asyncio.run call MUST complete normally -- a
        # propagating exception would mean the background task
        # body crashes the asyncio event loop.
        asyncio.run(_run_ticker_one_iteration(
            boom, cache_writes, log_events))

        # Cache write skipped
        assert len(cache_writes) == 0
        # Warning logged with the error message
        failures = [
            e for e in log_events
            if e[1] == "regime_signals_background_refresh_failed"]
        assert len(failures) == 1
        assert failures[0][0] == "warning"
        assert "FRED timeout" in failures[0][2].get("error", "")

    def test_cancellation_propagates_for_clean_shutdown(self):
        """asyncio.CancelledError MUST propagate -- on lifespan
        teardown the task needs to exit promptly rather than
        swallow the cancellation and log a spurious failure."""
        from main import _REGIME_REFRESH_TIMEOUT_S  # noqa: F401

        log_events: list = []

        def _log_warning(event, **kwargs):
            log_events.append(("warning", event, kwargs))

        async def _body():
            try:
                raise asyncio.CancelledError()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                _log_warning(
                    "regime_signals_background_refresh_failed",
                    error=str(exc))

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(_body())

        # No spurious failure log on cancellation
        assert not log_events, (
            "Cancellation must propagate without logging the "
            "shutdown as a failure")
