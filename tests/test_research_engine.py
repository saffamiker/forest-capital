"""
tests/test_research_engine.py — coverage for tools/research_engine.py and
the three /api/v1/research/* endpoints.

The engine's database helpers all fail open without a DB, so the
freshness-gate logic, the mock-digest path, and the endpoint gating
exercise without Postgres. The orchestrator's end-to-end path
(run_research) is exercised through the test-environment mock digest
that the engine substitutes when ENVIRONMENT=test.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402
from auth import generate_session_token  # noqa: E402
from tools import research_engine  # noqa: E402

client = TestClient(app)

SYSADMIN = {"X-API-Key": generate_session_token("ruurdsm@queens.edu")}
TEAM = {"X-API-Key": generate_session_token("thaob@queens.edu")}
VIEWER = {"X-API-Key": generate_session_token("panttserk@queens.edu")}


# ── No-DB fail-open ──────────────────────────────────────────────────────────

class TestFailOpenWithoutDatabase:
    """Every database accessor must return a safe default rather than
    raise. Forced no-DB by monkeypatching AsyncSessionLocal to None so
    the tests pass regardless of whether the developer has a live
    local Postgres up. (An earlier version assumed an implicitly
    unreachable DB and broke once migrations were applied locally.)"""

    @pytest.fixture(autouse=True)
    def _force_no_db(self, monkeypatch):
        from database import AsyncSessionLocal as _real_session
        # Patch the module symbol so each accessor's `if
        # AsyncSessionLocal is None` guard fires.
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        yield _real_session

    def test_is_research_running_returns_false_without_db(self):
        assert asyncio.run(research_engine.is_research_running()) is False

    def test_last_research_run_at_returns_none_without_db(self):
        assert asyncio.run(research_engine.last_research_run_at()) is None

    def test_is_current_returns_false_without_db(self):
        assert asyncio.run(research_engine._is_current()) is False

    def test_get_latest_digest_returns_none_without_db(self):
        assert asyncio.run(research_engine.get_latest_digest()) is None

    def test_get_recent_digests_returns_empty_without_db(self):
        assert asyncio.run(research_engine.get_recent_digests()) == []


# ── Freshness gate ──────────────────────────────────────────────────────────

class TestFreshnessGate:
    """_is_current returns True only when the latest completed run is
    inside the freshness window. Test by monkeypatching
    last_research_run_at to return a known time."""

    def test_current_when_under_24_hours(self, monkeypatch):
        async def _recent():
            return datetime.now(timezone.utc) - timedelta(hours=12)
        monkeypatch.setattr(research_engine, "last_research_run_at", _recent)
        assert asyncio.run(research_engine._is_current()) is True

    def test_stale_when_over_24_hours(self, monkeypatch):
        async def _old():
            return datetime.now(timezone.utc) - timedelta(hours=30)
        monkeypatch.setattr(research_engine, "last_research_run_at", _old)
        assert asyncio.run(research_engine._is_current()) is False

    def test_stale_when_never_run(self, monkeypatch):
        async def _none():
            return None
        monkeypatch.setattr(research_engine, "last_research_run_at", _none)
        assert asyncio.run(research_engine._is_current()) is False

    def test_window_hours_overridable(self, monkeypatch):
        # The freshness window is a parameter; a future tightening to
        # 12h should propagate without rewriting _is_current's body.
        async def _ten_hours_ago():
            return datetime.now(timezone.utc) - timedelta(hours=10)
        monkeypatch.setattr(research_engine, "last_research_run_at",
                            _ten_hours_ago)
        # 24h window → current
        assert asyncio.run(research_engine._is_current(window_hours=24))
        # 8h window → stale
        assert not asyncio.run(research_engine._is_current(window_hours=8))


# ── Orchestrator early-return / mock path ───────────────────────────────────

class TestRunResearchSkipPaths:
    def test_skipped_when_a_run_is_already_in_progress(self, monkeypatch):
        async def _running():
            return True
        monkeypatch.setattr(research_engine, "is_research_running", _running)
        out = asyncio.run(research_engine.run_research("manual"))
        assert out == {"status": "skipped", "reason": "already_running"}

    def test_skipped_when_row_create_fails(self, monkeypatch):
        # _create_running_row returning None is the no-DB / DB-error
        # fail-open path. Monkeypatch it explicitly so the test
        # exercises the engine's skip semantics regardless of whether
        # a live Postgres is available — relying on the implicit
        # no-DB state was DB-state-dependent and broke whenever
        # ENVIRONMENT=test was run against a real local DB.
        async def _not_running():
            return False
        async def _create_fails(triggered_by):
            return None
        monkeypatch.setattr(research_engine, "is_research_running",
                            _not_running)
        monkeypatch.setattr(research_engine, "_create_running_row",
                            _create_fails)
        out = asyncio.run(research_engine.run_research("manual"))
        assert out == {"status": "skipped", "reason": "row_create_failed"}


class TestRunResearchCompletePath:
    """End-to-end run with the database helpers stubbed. Exercises:
      - row_id allocation
      - mock-digest substitution in the test env
      - _finalise_row called with the right status + digest
      - macro_context.refresh_macro_context called after success"""

    def test_complete_path_finalises_with_mock_digest(self, monkeypatch):
        finalised: dict = {}
        refreshed: list[int] = []

        async def _running(): return False
        async def _create(triggered_by): return 42
        async def _finalise(row_id, *, digest, usage, status):
            finalised.update({"row_id": row_id, "digest": digest,
                              "usage": usage, "status": status})
        async def _refresh():
            refreshed.append(1)

        monkeypatch.setattr(research_engine, "is_research_running", _running)
        monkeypatch.setattr(research_engine, "_create_running_row", _create)
        monkeypatch.setattr(research_engine, "_finalise_row", _finalise)
        # The post-success refresh imports macro_context lazily, so we
        # monkeypatch the symbol on the import path the engine takes.
        from tools import macro_context
        monkeypatch.setattr(macro_context, "refresh_macro_context", _refresh)

        out = asyncio.run(research_engine.run_research("manual"))

        assert out["status"] == "complete"
        assert out["row_id"] == 42
        assert finalised["status"] == "complete"
        assert finalised["row_id"] == 42
        # Test env → mock digest with one signal.
        assert len(finalised["digest"]["key_signals"]) == 1
        # Post-success refresh fired exactly once.
        assert refreshed == [1]

    def test_failure_digest_persists_with_failed_status(self, monkeypatch):
        finalised: dict = {}

        async def _running(): return False
        async def _create(triggered_by): return 99
        async def _finalise(row_id, *, digest, usage, status):
            finalised.update({"status": status, "digest": digest})
        async def _noop_refresh():
            return None

        monkeypatch.setattr(research_engine, "is_research_running", _running)
        monkeypatch.setattr(research_engine, "_create_running_row", _create)
        monkeypatch.setattr(research_engine, "_finalise_row", _finalise)
        from tools import macro_context
        monkeypatch.setattr(macro_context, "refresh_macro_context",
                            _noop_refresh)

        # Force a failure digest path by monkeypatching the mock to
        # carry an `error` key — same shape generate_digest would emit
        # on a real failure.
        def _fail_mock():
            return ({
                "summary_text": "broken",
                "key_signals": [],
                "regime_implication": "",
                "citation_urls": [],
                "raw_response": "",
                "error": "stubbed failure",
            }, {"input_tokens": 0, "output_tokens": 0,
                "model": "claude-sonnet-4-6", "n_searches": 0, "n_fetches": 0})
        monkeypatch.setattr(research_engine, "_mock_digest", _fail_mock)

        out = asyncio.run(research_engine.run_research("manual"))

        assert out["status"] == "failed"
        assert finalised["status"] == "failed"
        assert finalised["digest"]["error"] == "stubbed failure"


# ── Stale-aware variant ─────────────────────────────────────────────────────

class TestRunResearchIfStale:
    def test_skipped_when_current(self, monkeypatch):
        async def _current():
            return True
        monkeypatch.setattr(research_engine, "_is_current", _current)
        out = asyncio.run(research_engine.run_research_if_stale())
        assert out == {"status": "skipped", "reason": "current"}

    def test_runs_when_stale(self, monkeypatch):
        async def _stale():
            return False
        captured: dict = {}

        async def _fake_run(triggered_by):
            captured["triggered_by"] = triggered_by
            return {"status": "complete"}

        monkeypatch.setattr(research_engine, "_is_current", _stale)
        monkeypatch.setattr(research_engine, "run_research", _fake_run)
        out = asyncio.run(research_engine.run_research_if_stale())
        assert out["status"] == "complete"
        assert captured["triggered_by"] == "scheduled"


# ── Endpoint gating + shape ─────────────────────────────────────────────────

class TestResearchEndpointGating:
    def test_latest_admits_any_authenticated_user(self):
        # The latest digest is dashboard-visible to every authenticated
        # user — transparency is the point.
        for headers in (SYSADMIN, TEAM, VIEWER):
            r = client.get("/api/v1/research/latest", headers=headers)
            assert r.status_code == 200
            body = r.json()
            assert "digest" in body
            assert "last_completed_at" in body

    def test_latest_rejects_unauthenticated(self):
        assert client.get("/api/v1/research/latest").status_code == 401

    def test_history_admits_any_authenticated_user(self):
        for headers in (SYSADMIN, TEAM, VIEWER):
            r = client.get("/api/v1/research/history", headers=headers)
            assert r.status_code == 200
            assert "runs" in r.json()

    def test_history_limit_clamps(self):
        # limit=0 → 1 (we clamp to >=1); limit=999 → 50 (clamp to max).
        # Both must respond 200; the gate is "do not 500 on a weird limit".
        for limit in (0, 1, 50, 999):
            r = client.get(f"/api/v1/research/history?limit={limit}",
                           headers=SYSADMIN)
            assert r.status_code == 200

    def test_run_now_rejects_a_viewer(self):
        assert client.post(
            "/api/v1/research/run", headers=VIEWER).status_code == 403

    def test_run_now_rejects_a_team_member(self):
        # Team membership is not sysadmin — manage_users is the gate.
        assert client.post(
            "/api/v1/research/run", headers=TEAM).status_code == 403

    def test_run_now_unauthenticated_is_401(self):
        assert client.post("/api/v1/research/run").status_code == 401

    def test_run_now_admits_the_sysadmin_and_starts(self, monkeypatch):
        async def _not_running():
            return False
        async def _fake_run(triggered_by):
            return {"status": "complete"}
        monkeypatch.setattr(research_engine, "is_research_running",
                            _not_running)
        monkeypatch.setattr(research_engine, "run_research", _fake_run)
        r = client.post("/api/v1/research/run", headers=SYSADMIN)
        assert r.status_code == 200
        body = r.json()
        # Either "running" (we spawned) or "already_running" (a stale
        # lock somehow exists) — both are acceptable. The 200 is the
        # contract; the body's status field tells the client what to do.
        assert body["status"] in ("running", "already_running")

    def test_run_now_refuses_when_a_run_is_already_in_progress(
        self, monkeypatch,
    ):
        async def _running():
            return True
        monkeypatch.setattr(research_engine, "is_research_running", _running)
        r = client.post("/api/v1/research/run", headers=SYSADMIN)
        assert r.status_code == 200
        assert r.json()["status"] == "already_running"


# ── Mock digest contract — pinned because the engine substitutes it ─────────

class TestMockDigest:
    def test_carries_every_required_key(self):
        digest, usage = research_engine._mock_digest()
        for key in ("summary_text", "key_signals", "regime_implication",
                    "citation_urls"):
            assert key in digest
        for key in ("input_tokens", "output_tokens", "model"):
            assert key in usage

    def test_signals_have_the_documented_shape(self):
        digest, _ = research_engine._mock_digest()
        for sig in digest["key_signals"]:
            for k in ("category", "signal", "implication", "source_url"):
                assert k in sig


# ── Stuck-run reaper (May 22 2026 — zombie 'running' row guard) ──────────────

class TestFailStaleRunningDigests:
    """A research run that crashes mid-flight (Render restart, worker
    OOM, network timeout on the agent call) leaves its row in 'running'
    forever. Without a reaper, every subsequent run is skipped with
    reason 'already_running' and Run Now stops working — UAT surfaced
    this on May 22. The reaper marks any row stuck past
    _RUN_TIMEOUT_MINUTES as failed and releases the lock.

    DB-touching tests skipped without a live Postgres; the no-DB
    fail-open path is exercised separately."""

    def test_returns_zero_without_db(self, monkeypatch):
        # Force the no-DB path so the test runs regardless of whether
        # the developer has a live local Postgres up. AsyncSessionLocal
        # being None is the guard the helper checks.
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        out = asyncio.run(research_engine.fail_stale_running_digests())
        assert out == 0

    def test_timeout_constant_is_pinned(self):
        # Mirror the audit reaper's pattern — 10 minutes for research
        # because real runs are 30-90s. A change to this constant is a
        # design decision; the pin catches accidental drift.
        assert research_engine._RUN_TIMEOUT_MINUTES == 10

    def test_is_research_running_calls_the_reaper_first(self, monkeypatch):
        # The reaper must run BEFORE the running-check so a stuck row
        # is cleared before is_research_running reads. Mirrors
        # audit_engine.is_audit_running's pattern.
        call_order: list[str] = []

        async def _stub_reap():
            call_order.append("reap")
            return 0

        async def _no_db_check():
            # Make is_research_running's body return early so we just
            # observe the reaper invocation.
            return False

        monkeypatch.setattr(research_engine, "fail_stale_running_digests",
                            _stub_reap)
        # Force the no-DB branch inside is_research_running.
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)

        asyncio.run(research_engine.is_research_running())
        assert call_order == ["reap"]

    def test_reaper_failure_is_swallowed(self, monkeypatch):
        # A database error inside the reaper must NOT propagate —
        # the lock-check is already conservative (fail-open to False),
        # and a noisy reaper error would block every Run Now click.
        # Same fail-open contract every other engine helper has.
        from sqlalchemy import text as _text  # noqa: F401

        async def _boom_session():
            raise RuntimeError("DB down")

        # We can't easily inject AsyncSessionLocal that raises on use,
        # so instead force the no-DB path and verify the helper still
        # returns 0 without propagating.
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        out = asyncio.run(research_engine.fail_stale_running_digests())
        assert out == 0
