"""
tests/test_qa_guard.py

The global QA-run concurrency guard (tools/qa_guard.py). Only one QA run
— a statistical audit or a methodology audit — may be in progress
platform-wide; a second is rejected with HTTP 409, not queued.

Covers: the guard reports the in-progress kind correctly, a blocked run
returns the 409 + message on every run-triggering endpoint, a run
proceeds when none is in progress, and the guard lifts cleanly when the
active run completes.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)

from main import app  # noqa: E402
from auth import generate_session_token  # noqa: E402
from tools import qa_guard  # noqa: E402

client = TestClient(app)
# ruurdsm@ is the sysadmin — holds team_member, so /api/v1/audit/run is reachable.
HEADERS = {"X-API-Key": generate_session_token("ruurdsm@queens.edu")}

# Every run-triggering endpoint the guard protects.
RUN_ENDPOINTS = [
    "/api/qa/audit",
    "/api/v1/qa/run",
    "/api/v1/qa/full-review",
    "/api/v1/audit/run",
]


@pytest.fixture(autouse=True)
def _reset_guard(monkeypatch):
    """
    Isolates each test from process-global and database state.

    The methodology flag is process-global — it is cleared before and
    after every test. The statistical check reads audit_runs from the
    live database; a stale 'running' row left by another suite would
    make the guard non-deterministic here, so is_audit_running is
    stubbed to "nothing running" by default. The statistical tests
    override the stub with their own monkeypatch.
    """
    qa_guard.end_methodology()

    async def _none():
        return None
    monkeypatch.setattr("tools.audit_engine.is_audit_running", _none)
    yield
    qa_guard.end_methodology()


def _run(coro):
    return asyncio.run(coro)


# ── qa_run_in_progress — the guard signal ─────────────────────────────────────

class TestGuardSignal:
    def test_no_run_in_progress_returns_none(self):
        qa_guard.end_methodology()
        assert _run(qa_guard.qa_run_in_progress()) is None

    def test_methodology_flag_reports_methodology(self):
        qa_guard.begin_methodology()
        assert qa_guard.methodology_in_progress() is True
        assert _run(qa_guard.qa_run_in_progress()) == "methodology"

    def test_guard_lifts_when_methodology_ends(self):
        qa_guard.begin_methodology()
        assert _run(qa_guard.qa_run_in_progress()) == "methodology"
        qa_guard.end_methodology()
        assert qa_guard.methodology_in_progress() is False
        assert _run(qa_guard.qa_run_in_progress()) is None

    def test_running_statistical_audit_reports_statistical(self, monkeypatch):
        # A 'running' audit_runs row → is_audit_running returns its id.
        async def _fake_running():
            return 42
        monkeypatch.setattr("tools.audit_engine.is_audit_running",
                            _fake_running)
        assert _run(qa_guard.qa_run_in_progress()) == "statistical"

    def test_statistical_check_fails_open(self, monkeypatch):
        # A database error in the statistical check must not wedge the
        # guard — it reports "no run in progress".
        async def _boom():
            raise RuntimeError("db down")
        monkeypatch.setattr("tools.audit_engine.is_audit_running", _boom)
        assert _run(qa_guard.qa_run_in_progress()) is None


# ── Endpoint contract — a blocked run returns 409 ─────────────────────────────

class TestBlockedRuns:
    def test_every_run_endpoint_409s_during_a_methodology_run(self):
        qa_guard.begin_methodology()
        for path in RUN_ENDPOINTS:
            resp = client.post(path, headers=HEADERS)
            assert resp.status_code == 409, f"{path} should be blocked"
            assert "QA run is currently in progress" in resp.json()["detail"]

    def test_run_endpoints_409_during_a_statistical_audit(self, monkeypatch):
        async def _fake_running():
            return 7
        monkeypatch.setattr("tools.audit_engine.is_audit_running",
                            _fake_running)
        for path in RUN_ENDPOINTS:
            resp = client.post(path, headers=HEADERS)
            assert resp.status_code == 409, f"{path} should be blocked"
            assert "QA run is currently in progress" in resp.json()["detail"]


# ── Endpoint contract — a run proceeds when the platform is free ──────────────

class TestRunsProceedWhenFree:
    def test_methodology_endpoints_proceed_when_nothing_running(self):
        # No run in progress → the methodology endpoints reach their
        # test-environment path (a 200, not a 409).
        qa_guard.end_methodology()
        for path in ("/api/qa/audit", "/api/v1/qa/run",
                     "/api/v1/qa/full-review"):
            resp = client.post(path, headers=HEADERS)
            assert resp.status_code == 200, f"{path} should proceed"

    def test_run_proceeds_after_the_guard_lifts(self):
        # Blocked while a run is active, proceeds once it completes.
        qa_guard.begin_methodology()
        assert client.post("/api/qa/audit", headers=HEADERS).status_code == 409
        qa_guard.end_methodology()
        assert client.post("/api/qa/audit", headers=HEADERS).status_code == 200

    def test_methodology_endpoint_clears_the_flag_after_running(self):
        # A completed run must leave the guard clear for the next run —
        # the endpoint's begin/end bracket lifts the guard on the way out.
        qa_guard.end_methodology()
        client.post("/api/qa/audit", headers=HEADERS)
        assert qa_guard.methodology_in_progress() is False
        assert _run(qa_guard.qa_run_in_progress()) is None
