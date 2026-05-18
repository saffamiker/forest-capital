"""
tests/test_triage.py

Tests for the automated feedback triage system:
  - the sysadmin gate on the /api/v1/testing/triage endpoints
  - run_triage early-return / concurrency behaviour
  - the five-section triage report generation
  - the threshold and test-pass automation triggers (main._triage_trigger)
  - the run_triage orchestration (item count, status, fail-open GitHub)
  - GitHub issue creation / label setup failing open with no token

The triage engine's database reads all fail open to a safe default, so
the early-return, generation, trigger-logic and fail-open tests all run
with no database. The orchestration test monkeypatches the engine's
database helpers so run_triage executes end to end without Postgres.
"""
from __future__ import annotations

import asyncio
import os
import sys

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
from tools import triage_engine  # noqa: E402

client = TestClient(app)

# ruurdsm@ → sysadmin (manage_users) via config_fallback; the others not.
SYSADMIN = {"X-API-Key": generate_session_token("ruurdsm@queens.edu")}
TEAM = {"X-API-Key": generate_session_token("thaob@queens.edu")}
VIEWER = {"X-API-Key": generate_session_token("panttserk@queens.edu")}

TRIAGE = "/api/v1/testing/triage"

SAMPLE_FAILURES = [
    {"id": 1, "step_id": "s1", "script_id": "sc1", "severity": "blocking",
     "failure_description": "Dashboard crashes on load",
     "actual_result": "white screen", "browser_info": "Chrome 120",
     "user_email": "thaob@queens.edu", "attested_at": "2026-05-17T10:00:00",
     "resolved_at": None},
    {"id": 2, "step_id": "s2", "script_id": "sc1", "severity": "minor",
     "failure_description": "Tooltip slightly misaligned",
     "actual_result": "off by 2px", "browser_info": "Firefox",
     "user_email": "thaob@queens.edu", "attested_at": "2026-05-17T10:05:00",
     "resolved_at": None},
]
SAMPLE_FEEDBACK = [
    {"id": 10, "feedback_type": "feature_request", "title": "Add dark mode",
     "description": "A dark theme would help evening work.",
     "ai_category": "enhancement", "ai_severity": "minor",
     "ai_effort_estimate": "small", "ai_tags": ["ui"],
     "ai_summary": "Dark theme request", "ai_confidence": 0.8,
     "status": "new", "user_email": "murdockm@queens.edu",
     "submitted_at": "2026-05-17T11:00:00"},
]


# ── Endpoint gating ───────────────────────────────────────────────────────────

class TestTriageEndpointGating:
    def test_run_triage_rejects_a_viewer(self):
        assert client.post(TRIAGE, headers=VIEWER).status_code == 403

    def test_run_triage_rejects_a_team_member(self):
        # A team member lacks manage_users — the triage endpoints are
        # sysadmin only.
        assert client.post(TRIAGE, headers=TEAM).status_code == 403

    def test_run_triage_admits_the_sysadmin(self):
        resp = client.post(TRIAGE, headers=SYSADMIN)
        assert resp.status_code == 200
        assert resp.json()["status"] == "triage_started"

    def test_run_triage_unauthenticated_is_401(self):
        assert client.post(TRIAGE).status_code == 401

    def test_list_reports_rejects_a_team_member(self):
        assert client.get(TRIAGE, headers=TEAM).status_code == 403

    def test_list_reports_admits_the_sysadmin(self):
        resp = client.get(TRIAGE, headers=SYSADMIN)
        assert resp.status_code == 200
        # With no database the read fails open to an empty list.
        assert resp.json() == {"reports": []}

    def test_latest_report_is_sysadmin_gated_and_returns_the_report_key(self):
        # GET /triage/latest is the triage login-notification's data
        # source — sysadmin only, and always returns a {"report": ...} key.
        assert client.get(f"{TRIAGE}/latest", headers=VIEWER).status_code == 403
        resp = client.get(f"{TRIAGE}/latest", headers=SYSADMIN)
        assert resp.status_code == 200
        assert "report" in resp.json()


# ── run_triage early return / concurrency ─────────────────────────────────────

class TestRunTriageGuards:
    def test_empty_backlog_returns_early_without_a_report(self):
        # No database → _gather_unaddressed reads fail open to empty, so
        # run_triage returns early and never creates a report row.
        result = asyncio.run(triage_engine.run_triage())
        assert result["status"] == "skipped"
        assert result["reason"] == "empty_backlog"

    def test_concurrent_run_is_skipped(self, monkeypatch):
        # A triage already in the 'running' state blocks a second run.
        async def _running() -> bool:
            return True
        monkeypatch.setattr(triage_engine, "is_triage_running", _running)
        result = asyncio.run(triage_engine.run_triage())
        assert result["status"] == "skipped"
        assert result["reason"] == "already_running"


# ── Report generation ─────────────────────────────────────────────────────────

class TestTriageReportGeneration:
    def test_report_contains_all_five_sections(self):
        # In the test environment _generate_triage_report uses the
        # deterministic mock — it must still carry every required section.
        report = triage_engine._generate_triage_report(
            SAMPLE_FAILURES, SAMPLE_FEEDBACK)
        for section in triage_engine._REQUIRED_SECTIONS:
            assert section in report

    def test_blocking_item_appears_in_immediate_actions(self):
        report = triage_engine._generate_triage_report(
            SAMPLE_FAILURES, SAMPLE_FEEDBACK)
        immediate = report.split("## QUICK WINS")[0]
        assert "Dashboard crashes on load" in immediate

    def test_immediate_items_are_the_high_severity_set(self):
        # The blocking failure qualifies; the minor failure and the minor
        # feedback do not.
        items = triage_engine._immediate_items(SAMPLE_FAILURES, SAMPLE_FEEDBACK)
        ids = {(i["item_type"], i["item_id"]) for i in items}
        assert ("failure", 1) in ids
        assert ("failure", 2) not in ids
        assert ("feedback", 10) not in ids


# ── Automation triggers ───────────────────────────────────────────────────────

class TestTriageTriggers:
    """main._triage_trigger — the threshold and test-pass hook logic."""

    def _spy_run_triage(self, monkeypatch):
        calls: list[str] = []

        async def _fake_run(triggered_by: str = "manual"):
            calls.append(triggered_by)
            return {"status": "complete"}

        async def _not_running() -> bool:
            return False

        monkeypatch.setattr(triage_engine, "run_triage", _fake_run)
        monkeypatch.setattr(triage_engine, "is_triage_running", _not_running)
        return calls

    def test_threshold_does_not_trigger_below_five(self, monkeypatch):
        import main
        calls = self._spy_run_triage(monkeypatch)

        async def _count(since=None) -> int:
            return 4

        async def _last():
            return None
        monkeypatch.setattr(triage_engine, "count_unaddressed_items", _count)
        monkeypatch.setattr(triage_engine, "last_triage_at", _last)
        asyncio.run(main._triage_trigger("threshold"))
        assert calls == []

    def test_threshold_triggers_at_exactly_five(self, monkeypatch):
        import main
        calls = self._spy_run_triage(monkeypatch)

        async def _count(since=None) -> int:
            return 5

        async def _last():
            return None
        monkeypatch.setattr(triage_engine, "count_unaddressed_items", _count)
        monkeypatch.setattr(triage_engine, "last_triage_at", _last)
        asyncio.run(main._triage_trigger("threshold"))
        assert calls == ["threshold"]

    def test_test_pass_triggers_unconditionally(self, monkeypatch):
        import main
        calls = self._spy_run_triage(monkeypatch)
        asyncio.run(main._triage_trigger("test_pass"))
        assert calls == ["test_pass"]

    def test_concurrent_run_blocks_both_triggers(self, monkeypatch):
        import main
        calls: list[str] = []

        async def _fake_run(triggered_by: str = "manual"):
            calls.append(triggered_by)
            return {}

        async def _running() -> bool:
            return True
        monkeypatch.setattr(triage_engine, "run_triage", _fake_run)
        monkeypatch.setattr(triage_engine, "is_triage_running", _running)
        asyncio.run(main._triage_trigger("threshold"))
        asyncio.run(main._triage_trigger("test_pass"))
        assert calls == []


# ── run_triage orchestration (DB helpers monkeypatched) ───────────────────────

class TestRunTriageOrchestration:
    """run_triage end to end with the engine's database helpers stubbed —
    exercises the item count, the status, and the fail-open GitHub step
    without a database."""

    def test_run_triage_stores_report_with_correct_count(self, monkeypatch):
        finalised: dict = {}

        async def _not_running() -> bool:
            return False

        async def _gather():
            return SAMPLE_FAILURES, SAMPLE_FEEDBACK

        async def _create(triggered_by: str):
            return 1

        async def _finalise(report_id, **kw):
            finalised.update(kw)
            finalised["report_id"] = report_id

        async def _mark(ids):
            return None

        monkeypatch.setattr(triage_engine, "is_triage_running", _not_running)
        monkeypatch.setattr(triage_engine, "_gather_unaddressed", _gather)
        monkeypatch.setattr(triage_engine, "_create_running_report", _create)
        monkeypatch.setattr(triage_engine, "_finalise_report", _finalise)
        monkeypatch.setattr(triage_engine, "_mark_feedback_triaged", _mark)

        result = asyncio.run(triage_engine.run_triage("manual"))

        # The item count is the full backlog (2 failures + 1 feedback).
        assert result["items_assessed"] == 3
        assert finalised["items_assessed"] == 3
        # The GitHub step failed open (no token) — the run still completes.
        assert finalised["status"] == "complete"
        assert finalised["github_issues_created"] == 0
        # The stored report carries every required section.
        for section in triage_engine._REQUIRED_SECTIONS:
            assert section in finalised["report_text"]


# ── GitHub fail-open ──────────────────────────────────────────────────────────

class TestGitHubFailOpen:
    def test_create_issue_returns_none_without_a_token(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "GITHUB_TOKEN", "")
        result = asyncio.run(
            triage_engine._create_github_issue("Title", "Body", ["bug"]))
        assert result is None

    def test_open_issues_is_fail_open_without_a_token(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "GITHUB_TOKEN", "")
        items = triage_engine._immediate_items(SAMPLE_FAILURES, SAMPLE_FEEDBACK)
        # No token → no issues created, and no exception raised.
        created = asyncio.run(triage_engine._open_issues_for(items))
        assert created == []

    def test_ensure_labels_is_fail_open_without_a_token(self, monkeypatch):
        import config
        from tools.github_labels import ensure_triage_labels
        monkeypatch.setattr(config, "GITHUB_TOKEN", "")
        assert asyncio.run(ensure_triage_labels()) == 0
