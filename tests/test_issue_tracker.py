"""
tests/test_issue_tracker.py — coverage for the Issue Tracker endpoint
and the compute_issue_status state machine (Prompt B).

Pins:
  1. compute_issue_status returns the four documented states for the
     four combinations of (resolved_at, result, resolution_type).
  2. GET /api/v1/testing/issue-tracker is view_admin-gated.
  3. get_issue_tracker_rows fails open without a DB.
  4. The UPSERT in record_result preserves resolution fields on a
     fail → pass re-attestation (DB-touching; skipped without one).
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

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
from tools import test_runner  # noqa: E402

client = TestClient(app)

SYSADMIN = {"X-API-Key": generate_session_token("ruurdsm@queens.edu")}
TEAM = {"X-API-Key": generate_session_token("thaob@queens.edu")}
VIEWER = {"X-API-Key": generate_session_token("panttserk@queens.edu")}


# ── compute_issue_status — the four-state machine ───────────────────────────

class TestComputeIssueStatus:
    """Pin the mapping from (resolved_at, result, resolution_type)
    onto the four Issue Tracker statuses. A regression here flips
    "Pending re-test" into "Closed" or worse, so the test is exhaustive."""

    def test_open_when_no_resolution(self):
        # A fresh failure with nothing resolved.
        assert test_runner.compute_issue_status({
            "resolved_at": None, "result": "fail",
            "resolution_type": None,
        }) == "open"

    def test_open_even_when_pass_but_unresolved(self):
        # A clean pass that was never resolved (probably never appears
        # in the tracker because the query filters those out, but the
        # status function should still classify it sanely).
        assert test_runner.compute_issue_status({
            "resolved_at": None, "result": "pass",
            "resolution_type": None,
        }) == "open"

    def test_pending_retest_for_no_bug_detected(self):
        assert test_runner.compute_issue_status({
            "resolved_at": "2026-05-21T10:00:00Z", "result": "fail",
            "resolution_type": "no_bug_detected",
        }) == "pending_retest"

    def test_pending_retest_for_code_fix_deployed(self):
        assert test_runner.compute_issue_status({
            "resolved_at": "2026-05-21T10:00:00Z", "result": "fail",
            "resolution_type": "code_fix_deployed",
        }) == "pending_retest"

    def test_passed_when_resolved_and_reattested_pass(self):
        # Tester re-attested as PASS after the resolution → terminal
        # "Passed" state. The UPSERT preserves resolution fields on
        # the fail→pass transition; that preservation is what makes
        # this state observable.
        assert test_runner.compute_issue_status({
            "resolved_at": "2026-05-21T10:00:00Z", "result": "pass",
            "resolution_type": "code_fix_deployed",
        }) == "passed"

    def test_closed_for_wont_fix(self):
        assert test_runner.compute_issue_status({
            "resolved_at": "2026-05-21T10:00:00Z", "result": "fail",
            "resolution_type": "wont_fix",
        }) == "closed"

    def test_wont_fix_beats_passed_in_the_classification_order(self):
        # Defence in depth: even if a wont_fix row somehow ended up with
        # result='pass' (an inconsistent state that shouldn't happen
        # under the current UPSERT), it still reads as Closed. The
        # order of the if-chain in compute_issue_status pins this.
        assert test_runner.compute_issue_status({
            "resolved_at": "2026-05-21T10:00:00Z", "result": "pass",
            "resolution_type": "wont_fix",
        }) == "closed"

    def test_unknown_resolution_type_falls_back_to_pending_retest(self):
        # Defensive: a future resolution_type the function doesn't
        # know about (added to RESOLUTION_TYPES but not handled in
        # compute_issue_status) lands in pending_retest, NOT closed.
        # Catch-bucket avoids surprising "auto-closed" rows.
        assert test_runner.compute_issue_status({
            "resolved_at": "2026-05-21T10:00:00Z", "result": "fail",
            "resolution_type": "future_value",
        }) == "pending_retest"


# ── ISSUE_STATUS vocabulary — pinned vs the frontend ────────────────────────

class TestIssueStatusVocab:
    def test_vocabulary_pinned(self):
        # The frontend's STATUS_LABEL keys must equal this tuple. A
        # divergence would surface a "—" badge on a real row.
        assert test_runner.ISSUE_STATUS == (
            "open", "pending_retest", "passed", "closed",
        )


# ── get_issue_tracker_rows fail-open ────────────────────────────────────────

class TestGetIssueTrackerFailOpen:
    @pytest.fixture(autouse=True)
    def _force_no_db(self, monkeypatch):
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)

    def test_returns_empty_list_without_db(self):
        out = asyncio.run(test_runner.get_issue_tracker_rows())
        assert out == []


# ── Endpoint gating ────────────────────────────────────────────────────────

class TestIssueTrackerEndpoint:
    URL = "/api/v1/testing/issue-tracker"

    def test_admits_the_sysadmin(self):
        r = client.get(self.URL, headers=SYSADMIN)
        assert r.status_code == 200
        body = r.json()
        assert "issues" in body
        assert isinstance(body["issues"], list)

    def test_rejects_a_viewer(self):
        # The viewer preset does NOT carry view_uat_status (the new
        # gate) — view_analytics + ask_council only.
        assert client.get(self.URL, headers=VIEWER).status_code == 403

    def test_admits_a_team_member(self):
        # UAT #119 (May 24 2026) — the endpoint was relaxed from
        # view_admin to view_uat_status. A team_member READS the
        # tracker so Bob and Molly see real-time UAT progress; the
        # mutation endpoints (resolve, suggestions/approve) remain
        # sysadmin-only.
        assert client.get(self.URL, headers=TEAM).status_code == 200

    def test_unauthenticated_is_401(self):
        assert client.get(self.URL).status_code == 401


# ── DB-touching upsert behaviour ────────────────────────────────────────────

TEAM_EMAIL = "thaob@queens.edu"
ADMIN_EMAIL = "ruurdsm@queens.edu"


def _db_ready() -> bool:
    """True when a live Postgres responds — same probe pattern as
    test_test_runner.py uses for its persistence checks."""
    import database
    if database.AsyncSessionLocal is None:
        return False
    async def _probe():
        try:
            async with database.AsyncSessionLocal() as session:
                from sqlalchemy import text
                await session.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001
            return False
    return asyncio.run(_probe())


async def _cleanup(script_id: str):
    """Delete every test_results row matching the synthetic script_id."""
    from sqlalchemy import text
    from database import AsyncSessionLocal
    if AsyncSessionLocal is None:
        return
    async with AsyncSessionLocal() as session:
        await session.execute(text(
            "DELETE FROM test_results WHERE script_id = :sid"),
            {"sid": script_id})
        await session.commit()


class TestUpsertPreservesResolutionOnPass:
    """The UPSERT in record_result is now conditional: on a
    fail → pass transition the resolution fields are preserved (this
    is what makes the "Passed" status observable in the tracker). A
    fail → fail transition (regression) clears them as before."""

    def test_fail_pass_preserves_resolution_for_passed_status(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.test_runner import (
            record_result, resolve_failure, get_issue_tracker_rows)

        sid = f"sc_{uuid.uuid4().hex[:8]}"

        async def scenario():
            from database import engine
            await engine.dispose()
            try:
                # 1. Tester reports a failure.
                await record_result(
                    user_email=TEAM_EMAIL, session_type="testing",
                    script_id=sid, step_id="z", result="fail",
                    failure_description="broke")
                # 2. Admin marks it resolved with code_fix_deployed.
                from tools.test_runner import get_all_failures
                failures = await get_all_failures()
                fid = next(f for f in failures
                           if f["script_id"] == sid)["id"]
                await resolve_failure(
                    fid, ADMIN_EMAIL, "Root cause.",
                    resolution_type="code_fix_deployed",
                    fix_reference="abc1234",
                    remediation_note="Fixed the bug.")
                # 3. Tester re-attests as PASS — this is the critical
                #    transition. Resolution fields MUST survive.
                await record_result(
                    user_email=TEAM_EMAIL, session_type="testing",
                    script_id=sid, step_id="z", result="pass")
                # 4. The tracker now shows the row as Passed AND still
                #    carries the resolution evidence.
                rows = await get_issue_tracker_rows()
                row = next(r for r in rows if r["script_id"] == sid)
                assert row["status"] == "passed"
                assert row["result"] == "pass"
                assert row["resolved_at"] is not None
                assert row["resolution_type"] == "code_fix_deployed"
                assert row["fix_reference"] == "abc1234"
                assert row["remediation_note"] == "Fixed the bug."
            finally:
                await _cleanup(sid)

        asyncio.run(scenario())

    def test_fail_fail_clears_resolution_for_regression(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.test_runner import (
            record_result, resolve_failure, get_issue_tracker_rows)

        sid = f"sc_{uuid.uuid4().hex[:8]}"

        async def scenario():
            from database import engine
            await engine.dispose()
            try:
                # 1. Fail → resolve → fail again (regression).
                await record_result(
                    user_email=TEAM_EMAIL, session_type="testing",
                    script_id=sid, step_id="z", result="fail",
                    failure_description="broke")
                from tools.test_runner import get_all_failures
                failures = await get_all_failures()
                fid = next(f for f in failures
                           if f["script_id"] == sid)["id"]
                await resolve_failure(
                    fid, ADMIN_EMAIL, "Root cause.",
                    resolution_type="code_fix_deployed",
                    fix_reference="abc1234",
                    remediation_note="Fixed.")
                await record_result(
                    user_email=TEAM_EMAIL, session_type="testing",
                    script_id=sid, step_id="z", result="fail",
                    failure_description="broke again")
                # The regression-clear contract: prior resolution is
                # discarded so the row reads as a fresh Open failure.
                rows = await get_issue_tracker_rows()
                row = next(r for r in rows if r["script_id"] == sid)
                assert row["status"] == "open"
                assert row["resolved_at"] is None
                assert row["resolution_type"] is None
                assert row["fix_reference"] is None
                assert row["remediation_note"] is None
            finally:
                await _cleanup(sid)

        asyncio.run(scenario())

    def test_wont_fix_resolution_renders_as_closed_in_tracker(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.test_runner import (
            record_result, resolve_failure, get_issue_tracker_rows)

        sid = f"sc_{uuid.uuid4().hex[:8]}"

        async def scenario():
            from database import engine
            await engine.dispose()
            try:
                await record_result(
                    user_email=TEAM_EMAIL, session_type="testing",
                    script_id=sid, step_id="z", result="fail",
                    failure_description="by design")
                from tools.test_runner import get_all_failures
                failures = await get_all_failures()
                fid = next(f for f in failures
                           if f["script_id"] == sid)["id"]
                await resolve_failure(
                    fid, ADMIN_EMAIL, "Sysadmin-only feature.",
                    resolution_type="wont_fix")
                rows = await get_issue_tracker_rows()
                row = next(r for r in rows if r["script_id"] == sid)
                assert row["status"] == "closed"
                assert row["resolution_type"] == "wont_fix"
            finally:
                await _cleanup(sid)

        asyncio.run(scenario())
