"""
tests/test_pr_suggestions.py — coverage for Suggested Resolutions (Commit 7/7).

Backend surfaces:
  - tools.pr_suggestion_scanner.parse_pr_payload (5 reference formats,
    body + commit-message scan, not-merged → None contract)
  - tools.pr_suggestion_scanner.record_pr_suggestions (skip paths)
  - The four endpoints (GET, GET by-failure, approve, dismiss) —
    auth gates + body validation
  - Webhook endpoint signature gate + pull_request routing
  - approve cascade (auto-dismiss siblings) — DB-touching
  - The full happy-path: webhook → suggestion → approve → resolution
    landed on test_results — DB-touching

DB-touching tests are skipped via _db_ready() when no live Postgres
responds; the parse + auth-gate + signature tests exercise without a DB.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import uuid
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test_webhook_secret")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402
from auth import generate_session_token  # noqa: E402
from tools import pr_suggestion_scanner, pr_suggestions  # noqa: E402

client = TestClient(app)

SYSADMIN = {"X-API-Key": generate_session_token("ruurdsm@queens.edu")}
TEAM = {"X-API-Key": generate_session_token("thaob@queens.edu")}
VIEWER = {"X-API-Key": generate_session_token("panttserk@queens.edu")}

GET_URL = "/api/v1/testing/suggestions"
BY_FAILURE_URL = "/api/v1/testing/suggestions/by-failure"
WEBHOOK_URL = "/api/v1/activity/commits/webhook"


# ── Scanner — parse_pr_payload ──────────────────────────────────────────────

def _payload(body: str, commits: list[dict] | None = None) -> dict[str, Any]:
    """Builds a minimal closed+merged PR webhook payload."""
    return {
        "action": "closed",
        "pull_request": {
            "number": 99, "title": "Test PR",
            "html_url": "https://github.com/saffamiker/forest-capital/pull/99",
            "merged": True, "merged_at": "2026-05-22T10:00:00Z",
            "user": {"login": "saffamiker"},
            "body": body,
        },
        **({"__commits": commits} if commits is not None else {}),
    }


class TestParsePRPayload:
    """Pins the five reference formats + the body/commit scan contract."""

    @pytest.mark.parametrize("body,expected_ids", [
        ("Resolves failure #42 -- did the thing", [42]),
        ("Fixes failure #1", [1]),
        ("addresses failure #100 today", [100]),
        ("Closes failure #7", [7]),
        ("see failure #3 for context", [3]),
        ("RESOLVES FAILURE #42", [42]),  # case-insensitive
    ])
    def test_each_reference_format_matches(self, body, expected_ids):
        out = pr_suggestion_scanner.parse_pr_payload(_payload(body))
        assert out is not None
        actual = sorted(m["failure_id"] for m in out["matches"])
        assert actual == expected_ids

    def test_unique_failures_only_when_referenced_twice(self):
        body = "Resolves failure #5\nFixes failure #5"
        out = pr_suggestion_scanner.parse_pr_payload(_payload(body))
        assert out is not None
        # Same id twice in the body → ONE match row (the canonical
        # citation is the first prefixed reference seen).
        assert [m["failure_id"] for m in out["matches"]] == [5]

    def test_prefixed_form_wins_over_bare_form(self):
        body = "see failure #5 for context\nResolves failure #5"
        out = pr_suggestion_scanner.parse_pr_payload(_payload(body))
        assert out is not None
        # The bare-form scan runs second and skips IDs the prefixed
        # scan already matched, so the canonical citation is preserved.
        assert out["matches"][0]["matched_on"].startswith("Resolves failure #5")

    def test_not_merged_returns_none(self):
        p = _payload("Resolves failure #1")
        p["pull_request"]["merged"] = False
        assert pr_suggestion_scanner.parse_pr_payload(p) is None

    def test_non_closed_action_returns_none(self):
        p = _payload("Resolves failure #1")
        p["action"] = "opened"
        assert pr_suggestion_scanner.parse_pr_payload(p) is None

    def test_commit_message_references_are_picked_up(self):
        commits = [
            {"sha": "abc1234", "commit_message": "Closes failure #99"},
            {"sha": "def5678", "commit_message": "Unrelated"},
        ]
        out = pr_suggestion_scanner.parse_pr_payload(
            _payload("", commits=commits))
        assert out is not None
        assert {m["failure_id"] for m in out["matches"]} == {99}
        assert out["commit_shas"] == ["abc1234", "def5678"]

    def test_matched_on_captures_the_exact_line(self):
        body = "Some preamble.\n\nResolves failure #42 -- the bug\n\nFooter."
        out = pr_suggestion_scanner.parse_pr_payload(_payload(body))
        assert out is not None
        assert out["matches"][0]["matched_on"] \
            == "Resolves failure #42 -- the bug"

    def test_body_and_commits_both_scanned(self):
        body = "Resolves failure #1"
        commits = [{"sha": "abc", "commit_message": "Fixes failure #2"}]
        out = pr_suggestion_scanner.parse_pr_payload(
            _payload(body, commits=commits))
        assert out is not None
        ids = sorted(m["failure_id"] for m in out["matches"])
        assert ids == [1, 2]

    def test_no_references_returns_empty_matches(self):
        out = pr_suggestion_scanner.parse_pr_payload(
            _payload("Unrelated body content."))
        assert out is not None
        assert out["matches"] == []


# ── Endpoint auth gates ─────────────────────────────────────────────────────

class TestEndpointGates:
    def test_get_suggestions_rejects_a_viewer(self):
        assert client.get(GET_URL, headers=VIEWER).status_code == 403

    def test_get_suggestions_rejects_a_team_member(self):
        # team_member is below sysadmin in the permission hierarchy.
        assert client.get(GET_URL, headers=TEAM).status_code == 403

    def test_get_suggestions_unauthenticated_is_401(self):
        assert client.get(GET_URL).status_code == 401

    def test_get_suggestions_admits_the_sysadmin(self):
        r = client.get(GET_URL, headers=SYSADMIN)
        assert r.status_code == 200
        assert "suggestions" in r.json()

    def test_by_failure_rejects_a_viewer(self):
        assert client.get(BY_FAILURE_URL, headers=VIEWER).status_code == 403

    def test_by_failure_admits_the_sysadmin(self):
        r = client.get(BY_FAILURE_URL, headers=SYSADMIN)
        assert r.status_code == 200
        assert "by_failure" in r.json()

    def test_approve_rejects_a_viewer(self):
        r = client.post(f"{GET_URL}/1/approve", headers=VIEWER,
                        json={"root_cause": "x", "remediation_note": "y"})
        assert r.status_code == 403

    def test_dismiss_rejects_a_viewer(self):
        r = client.post(f"{GET_URL}/1/dismiss", headers=VIEWER, json={})
        assert r.status_code == 403


class TestApproveBodyValidation:
    """Body validation runs before the DB read, so these exercise
    without a database."""

    def test_missing_root_cause_is_422(self):
        r = client.post(
            f"{GET_URL}/1/approve", headers=SYSADMIN,
            json={"remediation_note": "x"})
        assert r.status_code == 422
        assert "root_cause" in r.json()["detail"]

    def test_blank_root_cause_is_422(self):
        r = client.post(
            f"{GET_URL}/1/approve", headers=SYSADMIN,
            json={"root_cause": "  ", "remediation_note": "x"})
        assert r.status_code == 422

    def test_missing_remediation_is_422(self):
        r = client.post(
            f"{GET_URL}/1/approve", headers=SYSADMIN,
            json={"root_cause": "x"})
        assert r.status_code == 422
        assert "remediation_note" in r.json()["detail"]


# ── Webhook endpoint — signature gate + routing ─────────────────────────────

def _sign(body: bytes) -> str:
    """Computes the X-Hub-Signature-256 header the webhook expects."""
    mac = hmac.new(
        b"test_webhook_secret", msg=body, digestmod=hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


class TestWebhookSignatureGate:
    """Tests in this class need GITHUB_WEBHOOK_SECRET to be exactly
    "test_webhook_secret" — but config.py reads the env var at import
    time, and if another test module imports config first, our
    setdefault() at the top of this file lands too late and the
    secret is the empty string. Pin it via monkeypatch on the config
    module so the test is robust to import order."""

    @pytest.fixture(autouse=True)
    def _pin_secret(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "GITHUB_WEBHOOK_SECRET",
                            "test_webhook_secret")

    def test_invalid_signature_is_401(self):
        body = json.dumps({"action": "opened"}).encode("utf-8")
        r = client.post(WEBHOOK_URL, content=body, headers={
            "x-github-event": "pull_request",
            "x-hub-signature-256": "sha256=deadbeef",
            "content-type": "application/json",
        })
        assert r.status_code == 401

    def test_missing_signature_is_401(self):
        body = json.dumps({"action": "opened"}).encode("utf-8")
        r = client.post(WEBHOOK_URL, content=body, headers={
            "x-github-event": "pull_request",
            "content-type": "application/json",
        })
        assert r.status_code == 401

    def test_signed_ping_event_is_ignored(self):
        body = json.dumps({"zen": "Hi there."}).encode("utf-8")
        r = client.post(WEBHOOK_URL, content=body, headers={
            "x-github-event": "ping",
            "x-hub-signature-256": _sign(body),
            "content-type": "application/json",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "ignored"

    def test_non_merged_pull_request_is_acked_silently(self):
        body = json.dumps({
            "action": "closed",
            "pull_request": {
                "number": 1, "merged": False, "title": "x",
                "html_url": "x", "user": {"login": "x"}, "body": "",
            }}).encode("utf-8")
        r = client.post(WEBHOOK_URL, content=body, headers={
            "x-github-event": "pull_request",
            "x-hub-signature-256": _sign(body),
            "content-type": "application/json",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ── DB-touching: scanner record_pr_suggestions + full approve flow ──────────

TEAM_EMAIL = "thaob@queens.edu"
ADMIN_EMAIL = "ruurdsm@queens.edu"


def _db_ready() -> bool:
    """True when a live Postgres responds — matches the pattern used
    in test_test_runner.py."""
    import database
    if database.AsyncSessionLocal is None:
        return False
    async def _probe() -> bool:
        try:
            async with database.AsyncSessionLocal() as session:
                from sqlalchemy import text
                await session.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001
            return False
    return asyncio.run(_probe())


async def _cleanup_script(script_id: str) -> None:
    from sqlalchemy import text
    from database import AsyncSessionLocal
    if AsyncSessionLocal is None:
        return
    async with AsyncSessionLocal() as session:
        await session.execute(text(
            "DELETE FROM test_results WHERE script_id = :sid"),
            {"sid": script_id})
        await session.commit()


async def _create_failure(script_id: str, step_id: str = "x") -> int:
    """Inserts a failed test_results row and returns its id."""
    from tools.test_runner import record_result, get_all_failures
    await record_result(
        user_email=TEAM_EMAIL, session_type="testing",
        script_id=script_id, step_id=step_id, result="fail",
        failure_description="testing")
    failures = await get_all_failures()
    return next(f for f in failures if f["script_id"] == script_id)["id"]


class TestRecordPRSuggestions:
    """Exercises record_pr_suggestions's three skip paths plus the
    happy-path INSERT."""

    def test_records_a_suggestion_for_an_open_failure(self):
        if not _db_ready():
            pytest.skip("no live database")
        sid = f"sc_{uuid.uuid4().hex[:8]}"

        async def scenario():
            from database import engine
            await engine.dispose()
            try:
                fid = await _create_failure(sid)
                parsed = pr_suggestion_scanner.parse_pr_payload(_payload(
                    f"Resolves failure #{fid}"))
                summary = await pr_suggestion_scanner.record_pr_suggestions(
                    parsed)
                assert summary["created"] == [fid]
                assert summary["skipped_missing"] == []
                assert summary["skipped_resolved"] == []
                assert summary["skipped_duplicate"] == []
            finally:
                await _cleanup_script(sid)

        asyncio.run(scenario())

    def test_skips_non_existent_failure_id(self):
        if not _db_ready():
            pytest.skip("no live database")

        async def scenario():
            from database import engine
            await engine.dispose()
            # Use a very high failure_id unlikely to exist.
            parsed = pr_suggestion_scanner.parse_pr_payload(_payload(
                "Resolves failure #9999999"))
            summary = await pr_suggestion_scanner.record_pr_suggestions(
                parsed)
            assert summary["created"] == []
            assert summary["skipped_missing"] == [9999999]

        asyncio.run(scenario())

    def test_skips_already_resolved_failure(self):
        if not _db_ready():
            pytest.skip("no live database")
        sid = f"sc_{uuid.uuid4().hex[:8]}"

        async def scenario():
            from database import engine
            await engine.dispose()
            try:
                from tools.test_runner import resolve_failure
                fid = await _create_failure(sid)
                await resolve_failure(
                    fid, ADMIN_EMAIL, "Already fixed by another path.",
                    resolution_type="no_bug_detected")
                parsed = pr_suggestion_scanner.parse_pr_payload(_payload(
                    f"Resolves failure #{fid}"))
                summary = await pr_suggestion_scanner.record_pr_suggestions(
                    parsed)
                assert summary["created"] == []
                assert summary["skipped_resolved"] == [fid]
            finally:
                await _cleanup_script(sid)

        asyncio.run(scenario())

    def test_idempotent_on_redelivery(self):
        """The UNIQUE constraint + ON CONFLICT DO NOTHING make a
        redelivered webhook a silent no-op rather than a duplicate row."""
        if not _db_ready():
            pytest.skip("no live database")
        sid = f"sc_{uuid.uuid4().hex[:8]}"

        async def scenario():
            from database import engine
            await engine.dispose()
            try:
                fid = await _create_failure(sid)
                payload = _payload(f"Resolves failure #{fid}")
                parsed = pr_suggestion_scanner.parse_pr_payload(payload)
                first = await pr_suggestion_scanner.record_pr_suggestions(
                    parsed)
                second = await pr_suggestion_scanner.record_pr_suggestions(
                    parsed)
                assert first["created"] == [fid]
                assert second["created"] == []
                assert second["skipped_duplicate"] == [fid]
            finally:
                await _cleanup_script(sid)

        asyncio.run(scenario())


class TestApproveCascade:
    """The approve flow auto-dismisses sibling pending suggestions on
    the same failure_id (decision point 4)."""

    def test_approve_cascades_dismissal_to_siblings(self):
        if not _db_ready():
            pytest.skip("no live database")
        sid = f"sc_{uuid.uuid4().hex[:8]}"

        async def scenario():
            from database import engine
            await engine.dispose()
            try:
                fid = await _create_failure(sid)
                # Two PRs both reference the same failure → two
                # pending suggestions.
                for pr_n in (101, 102):
                    payload = _payload(f"Resolves failure #{fid}")
                    payload["pull_request"]["number"] = pr_n
                    parsed = pr_suggestion_scanner.parse_pr_payload(payload)
                    await pr_suggestion_scanner.record_pr_suggestions(parsed)

                # Fetch the suggestion ids.
                pending = await pr_suggestions.list_pending_suggestions()
                mine = [s for s in pending if s["failure_report_id"] == fid]
                assert len(mine) == 2
                approve_id = mine[0]["suggestion_id"]
                sibling_id = mine[1]["suggestion_id"]

                # Approve one — the other should cascade-dismiss.
                result = await pr_suggestions.approve_suggestion(
                    approve_id, reviewed_by=ADMIN_EMAIL,
                    root_cause="Race condition.",
                    remediation_note="Added a lock.")
                assert result is not None
                assert sibling_id in result["siblings_dismissed"]

                # And the test_results row carries the structured
                # resolution from the approve flow.
                from tools.test_runner import get_all_failures
                row = next(f for f in await get_all_failures()
                           if f["script_id"] == sid)
                assert row["resolution_type"] == "code_fix_deployed"
                assert row["fix_reference"] is not None
            finally:
                await _cleanup_script(sid)

        asyncio.run(scenario())


class TestDismissDoesNotTouchFailure:
    """Dismiss is a queue-only action; the failure row stays Open."""

    def test_dismiss_leaves_failure_unresolved(self):
        if not _db_ready():
            pytest.skip("no live database")
        sid = f"sc_{uuid.uuid4().hex[:8]}"

        async def scenario():
            from database import engine
            await engine.dispose()
            try:
                fid = await _create_failure(sid)
                payload = _payload(f"Closes failure #{fid}")
                parsed = pr_suggestion_scanner.parse_pr_payload(payload)
                await pr_suggestion_scanner.record_pr_suggestions(parsed)

                pending = await pr_suggestions.list_pending_suggestions()
                mine = [s for s in pending if s["failure_report_id"] == fid]
                assert len(mine) == 1
                ok = await pr_suggestions.dismiss_suggestion(
                    mine[0]["suggestion_id"],
                    reviewed_by=ADMIN_EMAIL,
                    dismiss_reason="Not actually related.")
                assert ok is True

                # Failure stays Open.
                from tools.test_runner import get_all_failures
                row = next(f for f in await get_all_failures()
                           if f["script_id"] == sid)
                assert row["resolved_at"] is None
                assert row["resolution_type"] is None
            finally:
                await _cleanup_script(sid)

        asyncio.run(scenario())


class TestListPendingFiltersTerminalStates:
    """GET /suggestions returns ONLY pending_review rows — approved
    and dismissed rows are invisible to the UI."""

    def test_approved_rows_are_excluded_from_list(self):
        if not _db_ready():
            pytest.skip("no live database")
        sid = f"sc_{uuid.uuid4().hex[:8]}"

        async def scenario():
            from database import engine
            await engine.dispose()
            try:
                fid = await _create_failure(sid)
                payload = _payload(f"Resolves failure #{fid}")
                parsed = pr_suggestion_scanner.parse_pr_payload(payload)
                await pr_suggestion_scanner.record_pr_suggestions(parsed)
                pending = await pr_suggestions.list_pending_suggestions()
                mine_pending = [s for s in pending
                                if s["failure_report_id"] == fid]
                assert len(mine_pending) == 1

                # Approve it.
                await pr_suggestions.approve_suggestion(
                    mine_pending[0]["suggestion_id"],
                    reviewed_by=ADMIN_EMAIL,
                    root_cause="x", remediation_note="y")

                # Now the list excludes it.
                pending_after = await pr_suggestions.list_pending_suggestions()
                mine_after = [s for s in pending_after
                              if s["failure_report_id"] == fid]
                assert mine_after == []
            finally:
                await _cleanup_script(sid)

        asyncio.run(scenario())
