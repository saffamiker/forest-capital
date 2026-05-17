"""
tests/test_activity.py

Tests for the Team Activity feature — the activity_log data layer, the
github_sync webhook helpers, and the /api/v1/activity/* endpoints.

Two tiers:
  - Pure-logic and endpoint-contract tests run everywhere, including CI
    (which has no PostgreSQL).
  - DB round-trip tests (insert / upsert / query / summary) need a live
    database; they run in full locally and skip cleanly when the DB is
    unreachable — the same pattern as the HMM-on-Windows skips.

DB tests run all their async work inside a SINGLE asyncio.run() and
dispose the shared engine first: the project's async engine pools
connections, and a connection bound to a prior asyncio.run() loop is
unusable in the next one. Disposing clears the pool so each scenario
gets fresh connections bound to its own loop.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import uuid

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

client = TestClient(app)
TEAM_EMAIL = "ruurdsm@queens.edu"
SESSION_HEADERS = {"X-API-Key": generate_session_token(TEAM_EMAIL)}


def _run(coro):
    return asyncio.run(coro)


# ── DB availability probe ─────────────────────────────────────────────────────

_db_ready_cache: bool | None = None


async def _fresh_session():
    """Disposes the pooled engine and returns a new session — the
    connection is then bound to the current event loop."""
    from database import engine, AsyncSessionLocal
    if engine is not None:
        await engine.dispose()
    return AsyncSessionLocal()  # type: ignore[union-attr]


def _db_ready() -> bool:
    """True when a live PostgreSQL with the activity tables is reachable.
    Probed once; DB round-trip tests skip when this is False."""
    global _db_ready_cache
    if _db_ready_cache is not None:
        return _db_ready_cache
    try:
        from tools.cache import _DB_AVAILABLE
        if not _DB_AVAILABLE:
            _db_ready_cache = False
            return False
        from sqlalchemy import text

        async def _probe() -> bool:
            async with await _fresh_session() as s:
                await s.execute(text("SELECT 1 FROM session_events LIMIT 1"))
            return True

        _db_ready_cache = _run(_probe())
    except Exception:
        _db_ready_cache = False
    return _db_ready_cache


# ── Pure logic — identity and the team-email filter ───────────────────────────

class TestIdentityHelpers:
    def test_team_member_recognised(self):
        from tools.activity_log import is_team_member
        assert is_team_member("ruurdsm@queens.edu") is True
        assert is_team_member("murdockm@queens.edu") is True

    def test_non_team_email_excluded(self):
        from tools.activity_log import is_team_member
        # Dr. Panttser is an authorised login but not a logged team member.
        assert is_team_member("panttserk@queens.edu") is False
        assert is_team_member("stranger@example.com") is False
        assert is_team_member(None) is False

    def test_git_author_resolves_to_platform_identity(self):
        from tools.activity_log import resolve_git_author
        # Michael's personal git email maps onto his platform login.
        assert resolve_git_author("mikeruurds@gmail.com") == "ruurdsm@queens.edu"
        assert resolve_git_author("MikeRuurds@Gmail.com") == "ruurdsm@queens.edu"

    def test_unmapped_git_author_returned_as_is(self):
        from tools.activity_log import resolve_git_author
        assert resolve_git_author("someone@elsewhere.com") == "someone@elsewhere.com"

    def test_display_name_lookup(self):
        from tools.activity_log import display_name
        assert display_name("ruurdsm@queens.edu") == "Michael Ruurds"
        assert display_name("unknown@x.com") == "unknown@x.com"


# ── Pure logic — webhook signature and payload parsing ────────────────────────

class TestWebhookHelpers:
    def test_valid_signature_accepted(self):
        from tools.github_sync import verify_signature
        secret, body = "s3cr3t", b'{"ref":"refs/heads/main"}'
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert verify_signature(secret, body, sig) is True

    def test_invalid_signature_rejected(self):
        from tools.github_sync import verify_signature
        assert verify_signature("s3cr3t", b"body", "sha256=deadbeef") is False

    def test_missing_secret_or_header_rejected(self):
        from tools.github_sync import verify_signature
        assert verify_signature("", b"body", "sha256=x") is False
        assert verify_signature("s3cr3t", b"body", None) is False

    def test_parse_push_payload_extracts_commits(self):
        from tools.github_sync import parse_push_payload
        payload = {
            "ref": "refs/heads/main",
            "commits": [
                {"id": "a" * 40, "message": "first",
                 "timestamp": "2026-05-16T10:00:00Z",
                 "author": {"name": "Michael Ruurds", "email": "mikeruurds@gmail.com"},
                 "url": "https://github.com/x/y/commit/" + "a" * 40,
                 "added": ["f1"], "removed": [], "modified": ["f2"]},
            ],
        }
        rows = parse_push_payload(payload)
        assert len(rows) == 1
        assert rows[0]["sha"] == "a" * 40
        assert rows[0]["author"] == "mikeruurds@gmail.com"
        assert rows[0]["files_changed"] == 2
        assert rows[0]["branch"] == "main"

    def test_parse_push_payload_ignores_non_push(self):
        from tools.github_sync import parse_push_payload
        assert parse_push_payload({"zen": "ping payload"}) == []


# ── Endpoint contracts — run in CI (DB fail-open) ─────────────────────────────

class TestWebhookEndpoint:
    def test_rejects_invalid_signature_401(self):
        resp = client.post(
            "/api/v1/activity/commits/webhook",
            content=b'{"ref":"refs/heads/main","commits":[]}',
            headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": "sha256=bad"},
        )
        assert resp.status_code == 401

    def test_ignores_non_push_event(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "GITHUB_WEBHOOK_SECRET", "testsecret")
        body = b'{"zen":"hello"}'
        sig = "sha256=" + hmac.new(b"testsecret", body, hashlib.sha256).hexdigest()
        resp = client.post(
            "/api/v1/activity/commits/webhook",
            content=body,
            headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": sig},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

    def test_valid_push_accepted(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "GITHUB_WEBHOOK_SECRET", "testsecret")
        payload = {
            "ref": "refs/heads/main",
            "commits": [
                {"id": "b" * 40, "message": "webhook test",
                 "timestamp": "2026-05-16T12:00:00Z",
                 "author": {"email": "mikeruurds@gmail.com"},
                 "url": "https://github.com/x/y", "added": [], "removed": [],
                 "modified": ["m"]},
            ],
        }
        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac.new(b"testsecret", body, hashlib.sha256).hexdigest()
        resp = client.post(
            "/api/v1/activity/commits/webhook",
            content=body,
            headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sig},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestActivityEndpoints:
    def test_events_endpoint_always_returns_200(self):
        """Activity logging must never block the UI — even a malformed
        body returns 200, never an error."""
        resp = client.post("/api/v1/activity/events", json={"events": "not-a-list"},
                            headers=SESSION_HEADERS)
        assert resp.status_code == 200
        assert "accepted" in resp.json()

    def test_events_endpoint_requires_auth(self):
        resp = client.post("/api/v1/activity/events", json={"events": []})
        assert resp.status_code == 401

    def test_team_endpoint_returns_timeline_shape(self):
        resp = client.get("/api/v1/activity/team", headers=SESSION_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert "events" in body and isinstance(body["events"], list)

    def test_summary_endpoint_returns_summary_shape(self):
        resp = client.get("/api/v1/activity/summary", headers=SESSION_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        for key in ("per_member", "commits", "most_active_agents",
                    "total_interactions"):
            assert key in body

    def test_council_endpoint_unaffected_by_logging(self):
        """A logging hook fires inside council_query — confirm the
        primary response is still a well-formed council payload."""
        resp = client.post("/api/council/query", json={"query": "Compare 60/40"},
                            headers=SESSION_HEADERS)
        assert resp.status_code == 200
        assert "messages" in resp.json() or "query" in resp.json()


# ── DB round-trips — skip without a live database ─────────────────────────────

class TestActivityLogDB:
    def test_session_events_insert_and_team_filter(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.activity_log import insert_session_events
        from sqlalchemy import text

        async def scenario():
            from database import engine, AsyncSessionLocal
            await engine.dispose()
            sid_team = str(uuid.uuid4())
            sid_other = str(uuid.uuid4())
            try:
                # A team member's events are written.
                n = await insert_session_events([
                    {"event_type": "page_view", "session_id": sid_team,
                     "session_type": "analytical", "page": "/dashboard"},
                    {"event_type": "feature_click", "session_id": sid_team,
                     "session_type": "analytical", "feature": "csv_export"},
                ], TEAM_EMAIL)
                assert n == 2
                # A non-team user (Dr. Panttser) produces no rows.
                n2 = await insert_session_events([
                    {"event_type": "page_view", "session_id": sid_other,
                     "page": "/x"},
                ], "panttserk@queens.edu")
                assert n2 == 0
            finally:
                async with AsyncSessionLocal() as s:
                    await s.execute(
                        text("DELETE FROM session_events WHERE session_id "
                             "IN (:a, :b)"), {"a": sid_team, "b": sid_other})
                    await s.commit()

        _run(scenario())

    def test_agent_interaction_insert_and_team_gate(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.activity_log import log_agent_interaction
        from sqlalchemy import text

        async def scenario():
            from database import engine, AsyncSessionLocal
            await engine.dispose()
            sid = str(uuid.uuid4())
            try:
                ok = await log_agent_interaction(
                    user_email=TEAM_EMAIL, session_id=sid,
                    session_type="analytical", interaction_type="council",
                    question_text="Q?", agents_involved=["cio"],
                    response_summary="A.")
                assert ok is True
                # Non-team user — gated out, no row.
                ok2 = await log_agent_interaction(
                    user_email="panttserk@queens.edu", session_id=sid,
                    session_type="analytical", interaction_type="council")
                assert ok2 is False
            finally:
                async with AsyncSessionLocal() as s:
                    await s.execute(
                        text("DELETE FROM agent_interactions "
                             "WHERE session_id = :sid"), {"sid": sid})
                    await s.commit()

        _run(scenario())

    def test_commit_upsert_dedups_on_sha(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.activity_log import upsert_commits
        from sqlalchemy import text

        async def scenario():
            from database import engine, AsyncSessionLocal
            await engine.dispose()
            sha = "test" + uuid.uuid4().hex[:36]
            commit = {"sha": sha, "author": "mikeruurds@gmail.com",
                      "message": "v1", "timestamp": "2026-05-16T08:00:00Z",
                      "files_changed": 1}
            try:
                await upsert_commits([commit])
                commit["message"] = "v2"
                await upsert_commits([commit])   # same sha — updates in place
                async with AsyncSessionLocal() as s:
                    r = await s.execute(
                        text("SELECT COUNT(*), MAX(message) FROM commit_activity "
                             "WHERE sha = :sha"), {"sha": sha})
                    n, msg = r.fetchone()
                assert n == 1 and msg == "v2"
            finally:
                async with AsyncSessionLocal() as s:
                    await s.execute(
                        text("DELETE FROM commit_activity WHERE sha = :sha"),
                        {"sha": sha})
                    await s.commit()

        _run(scenario())

    def test_team_timeline_sorted_and_session_filter(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.activity_log import log_agent_interaction, get_team_activity
        from sqlalchemy import text

        async def scenario():
            from database import engine, AsyncSessionLocal
            await engine.dispose()
            sid_a = str(uuid.uuid4())
            sid_t = str(uuid.uuid4())
            try:
                await log_agent_interaction(
                    user_email=TEAM_EMAIL, session_id=sid_a,
                    session_type="analytical", interaction_type="council",
                    question_text="analytical-q")
                await log_agent_interaction(
                    user_email=TEAM_EMAIL, session_id=sid_t,
                    session_type="testing", interaction_type="council",
                    question_text="testing-q")

                # Analytical view excludes the testing interaction.
                analytical = await get_team_activity(
                    activity_type="council", session_type="analytical", limit=500)
                qs = [e.get("question_text") for e in analytical["events"]]
                assert "analytical-q" in qs
                assert "testing-q" not in qs

                # Timeline sorted by timestamp descending.
                ts = [e["timestamp"] for e in analytical["events"] if e["timestamp"]]
                assert ts == sorted(ts, reverse=True)

                # session_type="all" includes both bands.
                both = await get_team_activity(
                    activity_type="council", session_type="all", limit=500)
                qs_all = [e.get("question_text") for e in both["events"]]
                assert "testing-q" in qs_all
            finally:
                async with AsyncSessionLocal() as s:
                    await s.execute(
                        text("DELETE FROM agent_interactions WHERE session_id "
                             "IN (:a, :b)"), {"a": sid_a, "b": sid_t})
                    await s.commit()

        _run(scenario())

    def test_testing_sessions_excluded_from_agent_context(self):
        """Agent context uses get_activity_summary(analytical_only=True);
        a testing-session interaction must not reach the agents."""
        if not _db_ready():
            pytest.skip("no live database")
        from tools.activity_log import log_agent_interaction, get_activity_summary
        from sqlalchemy import text

        async def scenario():
            from database import engine, AsyncSessionLocal
            await engine.dispose()
            sid_a = str(uuid.uuid4())
            sid_t = str(uuid.uuid4())
            try:
                # Baseline analytical council count for this user.
                base = await get_activity_summary(analytical_only=True)
                before = next((m["council_interactions"]
                               for m in base["per_member"]
                               if m["user"] == TEAM_EMAIL), 0)
                # One analytical, one testing.
                await log_agent_interaction(
                    user_email=TEAM_EMAIL, session_id=sid_a,
                    session_type="analytical", interaction_type="council",
                    question_text="analytical")
                await log_agent_interaction(
                    user_email=TEAM_EMAIL, session_id=sid_t,
                    session_type="testing", interaction_type="council",
                    question_text="testing")
                after = await get_activity_summary(analytical_only=True)
                now = next((m["council_interactions"]
                            for m in after["per_member"]
                            if m["user"] == TEAM_EMAIL), 0)
                # Only the analytical interaction is counted (+1, not +2).
                assert now == before + 1
            finally:
                async with AsyncSessionLocal() as s:
                    await s.execute(
                        text("DELETE FROM agent_interactions WHERE session_id "
                             "IN (:a, :b)"), {"a": sid_a, "b": sid_t})
                    await s.commit()

        _run(scenario())

    def test_summary_counts_per_member(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.activity_log import log_agent_interaction, get_activity_summary
        from sqlalchemy import text

        async def scenario():
            from database import engine, AsyncSessionLocal
            await engine.dispose()
            sid = str(uuid.uuid4())
            try:
                await log_agent_interaction(
                    user_email=TEAM_EMAIL, session_id=sid,
                    session_type="analytical", interaction_type="council",
                    question_text="q", agents_involved=["cio", "risk_manager"])
                summary = await get_activity_summary(analytical_only=True)
                mine = next((m for m in summary["per_member"]
                             if m["user"] == TEAM_EMAIL), None)
                assert mine is not None
                assert mine["council_interactions"] >= 1
                assert summary["total_interactions"] >= 1
            finally:
                async with AsyncSessionLocal() as s:
                    await s.execute(
                        text("DELETE FROM agent_interactions "
                             "WHERE session_id = :sid"), {"sid": sid})
                    await s.commit()

        _run(scenario())


# ── 11b — Team Activity as agent context ──────────────────────────────────────

class TestTeamActivityAgentContext:
    def _multi_user_summary(self) -> dict:
        return {
            "per_member": [
                {"user": "ruurdsm@queens.edu", "user_name": "Michael Ruurds",
                 "council_interactions": 8, "academic_review_sessions": 2,
                 "document_uploads": 3, "qa_audits": 1, "page_views": 40,
                 "last_active": "2026-05-16T10:00:00Z",
                 "most_used_features": ["csv_export"]},
                {"user": "thaob@queens.edu", "user_name": "Bob Thao",
                 "council_interactions": 2, "academic_review_sessions": 1,
                 "document_uploads": 0, "qa_audits": 0, "page_views": 12,
                 "last_active": "2026-05-15T09:00:00Z", "most_used_features": []},
            ],
            "commits": {"total": 142, "this_week": 18,
                        "by_author": {"ruurdsm@queens.edu": 142}},
            "most_active_agents": [{"agent": "cio", "count": 9}],
            "last_academic_review": None,
            "total_interactions": 17,
            "analytical_sessions_only": True,
        }

    def test_team_activity_block_assembles_with_multiple_users(self):
        from agents.academic_review import (
            format_team_activity_block, _team_activity_multi_user,
        )
        summary = self._multi_user_summary()
        block = "\n".join(format_team_activity_block(summary))
        # Every team member is listed — the two active ones with their
        # counts, plus Molly (no activity) noted neutrally.
        assert "Michael Ruurds" in block
        assert "Bob Thao" in block
        assert "Molly Murdock" in block
        assert "no recorded platform activity" in block
        assert "8 council" in block
        assert "142 total" in block
        # Two members are active -> the division-of-labour dimension fires.
        assert _team_activity_multi_user(summary) is True

    def test_single_active_user_does_not_trigger_division_dimension(self):
        from agents.academic_review import _team_activity_multi_user
        single = {
            "per_member": [
                {"user": "ruurdsm@queens.edu", "council_interactions": 5,
                 "academic_review_sessions": 0, "document_uploads": 0,
                 "qa_audits": 0, "page_views": 20},
            ],
            "commits": {"total": 0, "this_week": 0, "by_author": {}},
            "total_interactions": 5,
        }
        # Only one member active -> the dimension must be omitted so a
        # not-yet-adopted platform is not penalised.
        assert _team_activity_multi_user(single) is False

    def test_peer_question_gains_dimension_only_when_multi_user(self):
        from agents.academic_review import _peer_question
        assert "TEAM ENGAGEMENT AND TASK SHARING" in _peer_question(True)
        assert "TEAM ENGAGEMENT AND TASK SHARING" not in _peer_question(False)

    def test_arbiter_message_gains_section_6_only_when_multi_user(self):
        from agents.academic_review import build_arbiter_user_message
        with_team = build_arbiter_user_message("ctx", {"cio": "note"},
                                               multi_user=True)
        without = build_arbiter_user_message("ctx", {"cio": "note"},
                                             multi_user=False)
        assert "### 6. Team Engagement and Division of Labour" in with_team
        assert "### 6. Team Engagement and Division of Labour" not in without
