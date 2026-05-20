"""
tests/test_generation_jobs.py

The async document-generation job registry and the four job endpoints
that back the frontend's polling and download flow.

Two tiers, the same pattern as test_document_generation.py:

  - Registry-level tests exercise tools/generation_jobs.py directly —
    create / get / update / list / public_view, the two-hour TTL, the
    owner filter. No HTTP, no auth, no event loop.

  - Endpoint-contract tests exercise the four routes:
        GET    /api/v1/jobs/{id}          → state poll
        GET    /api/v1/jobs               → caller's last-10 list
        GET    /api/v1/jobs/{id}/download → completed file bytes
        DELETE /api/v1/jobs/{id}          → cancel
    The job is set up in the registry directly (the background generation
    task does not complete reliably under Starlette's TestClient — see
    the test_document_generation.py contract note).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

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
from tools import generation_jobs  # noqa: E402

client = TestClient(app)
OWNER = "ruurdsm@queens.edu"
OTHER = "thaob@queens.edu"
OWNER_HEADERS = {"X-API-Key": generate_session_token(OWNER)}
OTHER_HEADERS = {"X-API-Key": generate_session_token(OTHER)}


@pytest.fixture(autouse=True)
def _clear_jobs():
    """Every test starts with an empty registry."""
    generation_jobs._jobs.clear()
    yield
    generation_jobs._jobs.clear()


# ── Registry ──────────────────────────────────────────────────────────────────

class TestRegistry:
    def test_create_job_initialises_pending_state(self):
        job = generation_jobs.create_job("midpoint_paper", OWNER)
        assert job["status"] == "pending"
        assert job["document_type"] == "midpoint_paper"
        assert job["owner_email"] == OWNER
        assert job["draft_id"] is None
        assert job["download_url"] is None
        assert job["error"] is None
        # Unique hex id, length 32, no dashes — uuid4().hex shape.
        assert len(job["job_id"]) == 32 and "-" not in job["job_id"]

    def test_get_job_returns_the_registered_job(self):
        job = generation_jobs.create_job("executive_brief", OWNER)
        assert generation_jobs.get_job(job["job_id"]) is job

    def test_get_unknown_job_returns_none(self):
        assert generation_jobs.get_job("does-not-exist") is None

    def test_update_job_patches_in_place(self):
        job = generation_jobs.create_job("presentation_deck", OWNER)
        updated = generation_jobs.update_job(
            job["job_id"], status="running", draft_id=42)
        assert updated is not None
        assert updated["status"] == "running"
        assert updated["draft_id"] == 42
        # Same dict object — the registry is patched, not replaced.
        assert generation_jobs.get_job(job["job_id"]) is updated

    def test_update_unknown_job_returns_none(self):
        assert generation_jobs.update_job("missing", status="complete") is None

    def test_list_jobs_returns_owner_only_most_recent_first(self):
        a = generation_jobs.create_job("midpoint_paper", OWNER)
        b = generation_jobs.create_job("executive_brief", OTHER)
        c = generation_jobs.create_job("presentation_deck", OWNER)
        # Force ordering — created_at is at second resolution.
        a["created_at"] = datetime.now(timezone.utc) - timedelta(minutes=5)
        c["created_at"] = datetime.now(timezone.utc)
        owner_jobs = generation_jobs.list_jobs(OWNER)
        assert [j["job_id"] for j in owner_jobs] == [c["job_id"], a["job_id"]]
        # b is the OTHER user's — must not appear.
        assert b["job_id"] not in {j["job_id"] for j in owner_jobs}

    def test_list_jobs_caps_at_ten(self):
        for _ in range(15):
            generation_jobs.create_job("midpoint_paper", OWNER)
        assert len(generation_jobs.list_jobs(OWNER)) == 10

    def test_public_view_strips_internal_fields(self):
        job = generation_jobs.create_job("midpoint_paper", OWNER)
        generation_jobs.update_job(
            job["job_id"], _file_bytes=b"large file bytes",
            _filename="midpoint.docx", _media_type="application/...",
            _task=object())
        view = generation_jobs.public_view(generation_jobs.get_job(job["job_id"]))
        # The four internal underscore-keys are never serialised.
        for key in ("_file_bytes", "_filename", "_media_type", "_task"):
            assert key not in view
        # Timestamps are ISO strings, not datetimes — JSON-safe.
        assert isinstance(view["created_at"], str)
        assert view["completed_at"] is None  # not set yet

    def test_prune_drops_jobs_older_than_two_hours(self):
        job = generation_jobs.create_job("midpoint_paper", OWNER)
        # Move it three hours back — past the two-hour TTL.
        job["created_at"] = datetime.now(timezone.utc) - timedelta(hours=3)
        # get_job() prunes on read; the stale job is now gone.
        assert generation_jobs.get_job(job["job_id"]) is None
        assert job["job_id"] not in generation_jobs._jobs


# ── Endpoints — auth ──────────────────────────────────────────────────────────

class TestEndpointAuth:
    def test_get_job_requires_auth(self):
        assert client.get("/api/v1/jobs/anything").status_code == 401

    def test_list_jobs_requires_auth(self):
        assert client.get("/api/v1/jobs").status_code == 401

    def test_download_requires_auth(self):
        assert client.get("/api/v1/jobs/anything/download").status_code == 401

    def test_cancel_requires_auth(self):
        assert client.delete("/api/v1/jobs/anything").status_code == 401


# ── Endpoints — owner-only access ─────────────────────────────────────────────

class TestOwnerGates:
    def _seed_other_job(self) -> str:
        job = generation_jobs.create_job("midpoint_paper", OTHER)
        return job["job_id"]

    def test_get_someone_elses_job_is_forbidden(self):
        jid = self._seed_other_job()
        resp = client.get(f"/api/v1/jobs/{jid}", headers=OWNER_HEADERS)
        assert resp.status_code == 403

    def test_download_someone_elses_job_is_forbidden(self):
        jid = self._seed_other_job()
        # File bytes set so the only barrier is ownership.
        generation_jobs.update_job(
            jid, status="complete", _file_bytes=b"x",
            _filename="x.docx",
            _media_type=("application/vnd.openxmlformats-officedocument."
                          "wordprocessingml.document"))
        resp = client.get(f"/api/v1/jobs/{jid}/download", headers=OWNER_HEADERS)
        assert resp.status_code == 403

    def test_cancel_someone_elses_job_is_forbidden(self):
        jid = self._seed_other_job()
        resp = client.delete(f"/api/v1/jobs/{jid}", headers=OWNER_HEADERS)
        assert resp.status_code == 403


# ── Endpoints — state contract ────────────────────────────────────────────────

class TestStateContract:
    def test_get_job_returns_public_view_only(self):
        job = generation_jobs.create_job("midpoint_paper", OWNER)
        generation_jobs.update_job(
            job["job_id"], _file_bytes=b"secret", _task=object())
        resp = client.get(f"/api/v1/jobs/{job['job_id']}", headers=OWNER_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        # Same key set the public_view test asserts — no internal leakage.
        assert set(body) == {
            "job_id", "document_type", "owner_email", "status", "draft_id",
            "download_url", "error", "created_at", "completed_at",
        }

    def test_unknown_job_id_returns_404(self):
        resp = client.get("/api/v1/jobs/does-not-exist", headers=OWNER_HEADERS)
        assert resp.status_code == 404

    def test_list_returns_only_callers_jobs(self):
        a = generation_jobs.create_job("midpoint_paper", OWNER)
        b = generation_jobs.create_job("executive_brief", OTHER)
        resp = client.get("/api/v1/jobs", headers=OWNER_HEADERS)
        assert resp.status_code == 200
        ids = {j["job_id"] for j in resp.json()["jobs"]}
        assert a["job_id"] in ids
        assert b["job_id"] not in ids


# ── Endpoints — download ──────────────────────────────────────────────────────

class TestDownload:
    def test_complete_job_serves_file_bytes(self):
        job = generation_jobs.create_job("midpoint_paper", OWNER)
        bytes_ = b"PK\x03\x04stub docx content"
        generation_jobs.update_job(
            job["job_id"], status="complete", _file_bytes=bytes_,
            _filename="midpoint_paper.docx",
            _media_type=("application/vnd.openxmlformats-officedocument."
                          "wordprocessingml.document"))
        resp = client.get(
            f"/api/v1/jobs/{job['job_id']}/download", headers=OWNER_HEADERS)
        assert resp.status_code == 200
        assert resp.content == bytes_
        assert "wordprocessingml" in resp.headers["content-type"]
        # Attachment disposition with the rendered filename.
        assert "midpoint_paper.docx" in resp.headers["content-disposition"]

    def test_pending_job_download_returns_409(self):
        # Pending — no file bytes — download must refuse cleanly.
        job = generation_jobs.create_job("midpoint_paper", OWNER)
        resp = client.get(
            f"/api/v1/jobs/{job['job_id']}/download", headers=OWNER_HEADERS)
        assert resp.status_code == 409

    def test_failed_job_download_returns_409(self):
        job = generation_jobs.create_job("midpoint_paper", OWNER)
        generation_jobs.update_job(
            job["job_id"], status="failed",
            error="Generation failed (ref: abcd1234)")
        resp = client.get(
            f"/api/v1/jobs/{job['job_id']}/download", headers=OWNER_HEADERS)
        assert resp.status_code == 409

    def test_unknown_job_download_returns_404(self):
        resp = client.get(
            "/api/v1/jobs/does-not-exist/download", headers=OWNER_HEADERS)
        assert resp.status_code == 404

    def test_download_clears_bytes_after_first_serve(self):
        # Large PPTX renders (2 MB+) would otherwise sit in the _jobs
        # dict for the full 2-hour TTL. The first successful download
        # serves the bytes, then flips _bytes_served and nulls
        # _file_bytes / _filename / _media_type. The job record itself
        # stays so the client can still poll status.
        job = generation_jobs.create_job("presentation_deck", OWNER)
        bytes_ = b"PK\x03\x04stub pptx" * 1024
        generation_jobs.update_job(
            job["job_id"], status="complete", _file_bytes=bytes_,
            _filename="deck.pptx",
            _media_type=("application/vnd.openxmlformats-officedocument."
                          "presentationml.presentation"))
        resp = client.get(
            f"/api/v1/jobs/{job['job_id']}/download", headers=OWNER_HEADERS)
        assert resp.status_code == 200
        assert resp.content == bytes_
        stored = generation_jobs.get_job(job["job_id"])
        assert stored is not None
        assert stored["_file_bytes"] is None         # buffer cleared
        assert stored["_filename"] is None
        assert stored["_media_type"] is None
        assert stored["_bytes_served"] is True       # served flag set
        # Status preserved so the client's polling still sees "complete".
        assert stored["status"] == "complete"

    def test_second_download_returns_410_gone(self):
        # After the bytes have been served once, a re-attempt is a 410
        # Gone with regenerate guidance — never a 409 or 500. The first
        # download succeeded; the buffer is intentionally absent now.
        job = generation_jobs.create_job("executive_brief", OWNER)
        generation_jobs.update_job(
            job["job_id"], status="complete", _file_bytes=b"once",
            _filename="brief.docx",
            _media_type=("application/vnd.openxmlformats-officedocument."
                          "wordprocessingml.document"))
        # First download — succeeds.
        first = client.get(
            f"/api/v1/jobs/{job['job_id']}/download", headers=OWNER_HEADERS)
        assert first.status_code == 200
        # Second download — 410.
        second = client.get(
            f"/api/v1/jobs/{job['job_id']}/download", headers=OWNER_HEADERS)
        assert second.status_code == 410
        detail = second.json().get("detail", "")
        assert "already been served" in detail.lower()
        assert "regenerate" in detail.lower()


# ── Endpoints — cancellation ──────────────────────────────────────────────────

class TestCancel:
    def test_cancel_pending_job_transitions_to_cancelled(self):
        job = generation_jobs.create_job("midpoint_paper", OWNER)
        resp = client.delete(
            f"/api/v1/jobs/{job['job_id']}", headers=OWNER_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"
        assert generation_jobs.get_job(job["job_id"])["status"] == "cancelled"

    def test_cancel_running_job_transitions_to_cancelled(self):
        job = generation_jobs.create_job("midpoint_paper", OWNER)
        generation_jobs.update_job(job["job_id"], status="running")
        resp = client.delete(
            f"/api/v1/jobs/{job['job_id']}", headers=OWNER_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cancel_running_job_cancels_the_task_handle(self):
        # A real-ish task stand-in — done()/cancel() are the only methods
        # the endpoint touches. Verifies the in-flight asyncio task is
        # cancelled when the user cancels the job.
        class _StubTask:
            def __init__(self):
                self.cancelled = False
                self._done = False

            def done(self):
                return self._done

            def cancel(self):
                self.cancelled = True

        task = _StubTask()
        job = generation_jobs.create_job("midpoint_paper", OWNER)
        generation_jobs.update_job(
            job["job_id"], status="running", _task=task)
        resp = client.delete(
            f"/api/v1/jobs/{job['job_id']}", headers=OWNER_HEADERS)
        assert resp.status_code == 200
        assert task.cancelled is True

    def test_cancel_completed_job_is_a_noop(self):
        # Already terminal — cancel must NOT rewrite the status.
        job = generation_jobs.create_job("midpoint_paper", OWNER)
        generation_jobs.update_job(
            job["job_id"], status="complete", draft_id=99)
        resp = client.delete(
            f"/api/v1/jobs/{job['job_id']}", headers=OWNER_HEADERS)
        assert resp.status_code == 200
        # Status preserved.
        assert resp.json()["status"] == "complete"
        assert generation_jobs.get_job(job["job_id"])["status"] == "complete"

    def test_cancel_unknown_job_returns_404(self):
        resp = client.delete(
            "/api/v1/jobs/does-not-exist", headers=OWNER_HEADERS)
        assert resp.status_code == 404


# ── Three POSTs return a 202 job — already in test_document_generation.py ─────
# (The .post() → 202 → job_id contract is covered there; the registry side
# is covered above. This file owns the per-endpoint behavioural contract.)
