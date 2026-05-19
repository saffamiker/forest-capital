"""
tests/test_qa_guard.py

The per-type QA-run concurrency guards (tools/qa_guard.py).

There are two INDEPENDENT locks — methodology and statistical — and one
must never block the other. A second run OF THE SAME TYPE is rejected
with HTTP 409; a run of the OTHER type proceeds.

Covers: each guard reports its own type correctly, the methodology flag
auto-clears when stale, a same-type second run 409s on its endpoints, a
cross-type run is NOT blocked, and the guard lifts cleanly on completion.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

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
from tools import audit_engine, qa_guard  # noqa: E402

client = TestClient(app)
# ruurdsm@ is the sysadmin — holds team_member, so /api/v1/audit/run is reachable.
HEADERS = {"X-API-Key": generate_session_token("ruurdsm@queens.edu")}

METHODOLOGY_ENDPOINTS = [
    "/api/qa/audit",
    "/api/v1/qa/run",
    "/api/v1/qa/full-review",
]
STATISTICAL_ENDPOINT = "/api/v1/audit/run"


async def _none():
    return None


async def _fake_start_audit(triggered_by: str, email: str) -> dict:
    """Stubs start_audit so a contract test never inserts a real
    'running' audit_runs row that would leak into later tests."""
    return {"status": "started", "audit_id": 1}


@pytest.fixture(autouse=True)
def _reset_guard(monkeypatch):
    """
    Isolates each test from process-global and database state.

    The methodology flag is process-global — cleared before and after
    every test. The statistical check reads audit_runs from the live
    database; a stale 'running' row from another suite would make the
    guard non-deterministic, so is_audit_running is stubbed to "nothing
    running" by default. Statistical tests override the stub themselves.
    """
    qa_guard.end_methodology()
    monkeypatch.setattr("tools.audit_engine.is_audit_running", _none)
    yield
    qa_guard.end_methodology()


def _run(coro):
    return asyncio.run(coro)


# ── Guard signals ─────────────────────────────────────────────────────────────

class TestGuardSignal:
    def test_no_methodology_run_returns_false(self):
        qa_guard.end_methodology()
        assert qa_guard.methodology_in_progress() is False

    def test_methodology_flag_reports_in_progress(self):
        qa_guard.begin_methodology()
        assert qa_guard.methodology_in_progress() is True

    def test_methodology_guard_lifts_on_end(self):
        qa_guard.begin_methodology()
        assert qa_guard.methodology_in_progress() is True
        qa_guard.end_methodology()
        assert qa_guard.methodology_in_progress() is False

    def test_methodology_flag_auto_clears_when_stale(self):
        # A flag set more than 15 minutes ago is a crashed run that never
        # reached end_methodology — methodology_in_progress() reaps it.
        qa_guard.begin_methodology()
        qa_guard._methodology["started_at"] = time.time() - 16 * 60
        assert qa_guard.methodology_in_progress() is False

    def test_statistical_in_progress_true_when_audit_running(self, monkeypatch):
        async def _running():
            return 42
        monkeypatch.setattr("tools.audit_engine.is_audit_running", _running)
        assert _run(qa_guard.statistical_audit_in_progress()) is True

    def test_statistical_in_progress_false_when_none(self):
        assert _run(qa_guard.statistical_audit_in_progress()) is False

    def test_statistical_check_fails_open(self, monkeypatch):
        # A database error must not wedge the guard — it reports "free".
        async def _boom():
            raise RuntimeError("db down")
        monkeypatch.setattr("tools.audit_engine.is_audit_running", _boom)
        assert _run(qa_guard.statistical_audit_in_progress()) is False


# ── Per-type independence — one type never blocks the other ───────────────────

class TestPerTypeIndependence:
    def test_a_methodology_run_does_not_block_a_statistical_audit(
        self, monkeypatch,
    ):
        # A methodology audit is in progress; a statistical audit must
        # still be allowed to start. start_audit is stubbed so this stays
        # a contract test (no real audit_runs row inserted).
        qa_guard.begin_methodology()
        monkeypatch.setattr("tools.audit_engine.start_audit",
                            _fake_start_audit)
        resp = client.post(STATISTICAL_ENDPOINT, headers=HEADERS)
        assert resp.status_code == 200, "a methodology run must not block it"

    def test_a_statistical_audit_does_not_block_methodology_runs(
        self, monkeypatch,
    ):
        # A statistical audit is in progress; every methodology endpoint
        # must still proceed (test-env 200, not 409).
        async def _running():
            return 7
        monkeypatch.setattr("tools.audit_engine.is_audit_running", _running)
        for path in METHODOLOGY_ENDPOINTS:
            resp = client.post(path, headers=HEADERS)
            assert resp.status_code == 200, f"{path} must not be blocked"


# ── Same-type rejection — a second run of the same type 409s ──────────────────

class TestBlockedRuns:
    def test_methodology_endpoints_409_during_a_methodology_run(self):
        qa_guard.begin_methodology()
        for path in METHODOLOGY_ENDPOINTS:
            resp = client.post(path, headers=HEADERS)
            assert resp.status_code == 409, f"{path} should be blocked"
            assert "methodology audit is already in progress" \
                in resp.json()["detail"]

    def test_audit_run_409s_during_a_statistical_audit(self, monkeypatch):
        async def _running():
            return 7
        monkeypatch.setattr("tools.audit_engine.is_audit_running", _running)
        resp = client.post(STATISTICAL_ENDPOINT, headers=HEADERS)
        assert resp.status_code == 409
        assert "statistical audit is already in progress" \
            in resp.json()["detail"]


# ── A run proceeds when the platform is free ──────────────────────────────────

class TestRunsProceedWhenFree:
    def test_methodology_endpoints_proceed_when_nothing_running(self):
        qa_guard.end_methodology()
        for path in METHODOLOGY_ENDPOINTS:
            resp = client.post(path, headers=HEADERS)
            assert resp.status_code == 200, f"{path} should proceed"

    def test_methodology_run_proceeds_after_the_guard_lifts(self):
        qa_guard.begin_methodology()
        assert client.post("/api/qa/audit", headers=HEADERS).status_code == 409
        qa_guard.end_methodology()
        assert client.post("/api/qa/audit", headers=HEADERS).status_code == 200

    def test_methodology_endpoint_clears_the_flag_after_running(self):
        qa_guard.end_methodology()
        client.post("/api/qa/audit", headers=HEADERS)
        assert qa_guard.methodology_in_progress() is False
