"""tests/test_github_sync_negative_cache.py -- June 27 2026.

Pins the negative-cache + 401-specific log added to the GitHub PR-
count flow. Two bugs prompted the change:

  * GitHub API was returning repeated 401s in Render logs because
    fetch_merged_pr_count's failures were uncached -- every
    Academic Review run (via get_activity_summary) fired a fresh
    GET against api.github.com and re-logged github_pr_count_failed.

  * The generic 'github_pr_count_failed status=401' log didn't
    point operators at the obvious fix (expired GITHUB_TOKEN).

Behaviour pinned here:

  * 401 fires github_pr_count_unauthorized with a hint mentioning
    GITHUB_TOKEN + Render + required scope.
  * Non-401 non-200 still fires the legacy github_pr_count_failed.
  * Negative cache (5 min TTL) suppresses further fetch attempts so
    api.github.com is hit AT MOST ONCE per failure window.
  * A successful fetch resets the negative cache.
  * Cached positive value continues to dominate (existing 1-hour
    TTL unchanged).
"""
from __future__ import annotations

import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── 401-specific log ────────────────────────────────────────────────


class TestFetch401LogsUnauthorized:
    """fetch_merged_pr_count emits github_pr_count_unauthorized
    (not the generic github_pr_count_failed) so the operator can
    grep logs for the canonical 'token broken' signal."""

    @pytest.mark.asyncio
    async def test_401_logs_unauthorized_event(self, monkeypatch):
        import structlog
        from tools import github_sync

        captured: list[tuple[str, dict]] = []

        class _StubLog:
            def warning(self, event, **kw):
                captured.append((event, kw))
            def info(self, event, **kw):
                captured.append((event, kw))

        monkeypatch.setattr(github_sync, "log", _StubLog())

        import httpx

        class _StubResp:
            status_code = 401
            def json(self): return {}

        class _StubClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def get(self, *a, **kw): return _StubResp()

        monkeypatch.setattr(
            httpx, "AsyncClient", _StubClient)

        out = await github_sync.fetch_merged_pr_count(
            "saffamiker/forest-capital", "bad-token")
        assert out is None
        events = [e[0] for e in captured]
        assert "github_pr_count_unauthorized" in events
        # Hint mentions the operator-facing fix.
        unauth = next(
            kw for evt, kw in captured
            if evt == "github_pr_count_unauthorized")
        assert "GITHUB_TOKEN" in unauth.get("hint", "")
        assert "repo" in unauth.get("hint", "")

    @pytest.mark.asyncio
    async def test_500_logs_generic_failed(self, monkeypatch):
        from tools import github_sync

        captured: list[tuple[str, dict]] = []
        class _StubLog:
            def warning(self, event, **kw):
                captured.append((event, kw))
            def info(self, event, **kw): pass
        monkeypatch.setattr(github_sync, "log", _StubLog())

        import httpx
        class _Resp:
            status_code = 500
            def json(self): return {}
        class _Client:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def get(self, *a, **kw): return _Resp()
        monkeypatch.setattr(httpx, "AsyncClient", _Client)

        out = await github_sync.fetch_merged_pr_count(
            "saffamiker/forest-capital", "tok")
        assert out is None
        # Generic failed event fires; auth event does NOT.
        events = [e[0] for e in captured]
        assert "github_pr_count_failed" in events
        assert "github_pr_count_unauthorized" not in events


# ── Negative cache suppresses repeated calls ────────────────────────


class TestGetMergedPrCountNegativeCache:

    @pytest.mark.asyncio
    async def test_first_failure_caches_then_no_further_calls(
            self, monkeypatch):
        from tools import activity_log

        activity_log._reset_pr_count_cache()

        call_count = {"n": 0}

        async def _fake_fetch(repo, token):
            call_count["n"] += 1
            return None  # simulate auth failure

        monkeypatch.setattr(
            "tools.github_sync.fetch_merged_pr_count",
            _fake_fetch)

        # First call -- hits the API + caches the failure.
        out1 = await activity_log.get_merged_pr_count()
        assert out1 is None
        assert call_count["n"] == 1

        # Second + third calls -- negative cache HIT, no API call.
        out2 = await activity_log.get_merged_pr_count()
        out3 = await activity_log.get_merged_pr_count()
        assert out2 is None and out3 is None
        assert call_count["n"] == 1, (
            f"negative cache must suppress further fetches; "
            f"got {call_count['n']} calls")

    @pytest.mark.asyncio
    async def test_negative_cache_expires_after_ttl(
            self, monkeypatch):
        from tools import activity_log

        activity_log._reset_pr_count_cache()

        call_count = {"n": 0}

        async def _fake_fetch(repo, token):
            call_count["n"] += 1
            return None

        monkeypatch.setattr(
            "tools.github_sync.fetch_merged_pr_count",
            _fake_fetch)

        await activity_log.get_merged_pr_count()
        assert call_count["n"] == 1

        # Fast-forward past the failure TTL.
        ttl = activity_log._PR_COUNT_FAILURE_TTL_SECONDS
        import time as _time
        real_time = _time.time
        monkeypatch.setattr(
            _time, "time", lambda: real_time() + ttl + 10)

        await activity_log.get_merged_pr_count()
        assert call_count["n"] == 2, (
            "TTL expired; next call must hit the API again")

    @pytest.mark.asyncio
    async def test_successful_fetch_resets_negative_cache(
            self, monkeypatch):
        from tools import activity_log

        activity_log._reset_pr_count_cache()

        responses = [None, 42, None]   # fail, succeed, then fail
        idx = {"i": 0}

        async def _fake_fetch(repo, token):
            r = responses[idx["i"]]
            idx["i"] += 1
            return r

        monkeypatch.setattr(
            "tools.github_sync.fetch_merged_pr_count",
            _fake_fetch)

        # 1. Fail -> negative cache armed.
        await activity_log.get_merged_pr_count()
        assert activity_log._pr_count_cache["failed_at"] > 0

        # 2. Force-expire the negative cache so attempt #2 fires.
        import time as _time
        real_time = _time.time
        monkeypatch.setattr(
            _time, "time",
            lambda: real_time()
            + activity_log._PR_COUNT_FAILURE_TTL_SECONDS + 10)

        # 3. Succeed -> failed_at must reset to 0.
        out = await activity_log.get_merged_pr_count()
        assert out == 42
        assert activity_log._pr_count_cache["failed_at"] == 0.0
        assert activity_log._pr_count_cache["count"] == 42

    @pytest.mark.asyncio
    async def test_positive_cache_short_circuits_first(
            self, monkeypatch):
        """Pre-existing contract: a fresh successful count stays
        cached for the 1-hour positive TTL even if the underlying
        fetch would now return None. The negative cache only
        engages when the positive cache is stale or empty."""
        from tools import activity_log

        activity_log._reset_pr_count_cache()
        # Pre-populate a fresh positive cache.
        import time as _time
        activity_log._pr_count_cache["count"] = 100
        activity_log._pr_count_cache["ts"] = _time.time()

        async def _fake_fetch(repo, token):
            raise AssertionError(
                "positive cache must short-circuit fetch")

        monkeypatch.setattr(
            "tools.github_sync.fetch_merged_pr_count",
            _fake_fetch)

        out = await activity_log.get_merged_pr_count()
        assert out == 100


# ── Source contract: no retry on 401 ───────────────────────────────


class TestSourceContractNoRetryOn401:
    """fetch_merged_pr_count source must not contain any retry
    loop -- one attempt per call. The caller's negative cache is
    the only suppression mechanism."""

    def test_no_retry_loop_in_fetch(self):
        import inspect
        from tools.github_sync import fetch_merged_pr_count
        src = inspect.getsource(fetch_merged_pr_count)
        # Heuristic -- no 'for _ in range' loop wrapping the GET,
        # no 'while attempt' loop, no asyncio.sleep before a retry.
        for forbidden in (
            "for _ in range",
            "while attempt",
            "await asyncio.sleep",
        ):
            assert forbidden not in src, (
                f"fetch_merged_pr_count must not retry on failure "
                f"(found '{forbidden}' in source)")
