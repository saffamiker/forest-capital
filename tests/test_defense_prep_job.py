"""Thesis Defense Prep — async job lifecycle.

The endpoint pattern (POST 202 → background task → GET polling) is
verified end-to-end in test_defense_prep_upload.py. This file pins the
lifecycle CONTRACT at the registry level: status transitions, the
public payload shape, owner isolation, and the failure path. Going
through the registry directly keeps the assertions deterministic — the
Starlette TestClient drives asyncio per-request, so a real background
task is not guaranteed to settle inside a single GET poll under
pytest's load. The endpoint tests cover the integrated flow; these
tests cover the contract every state the polling endpoint reads from.
"""
import io
import os
import sys
from datetime import datetime, timezone

import pytest
from docx import Document
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
from tools.generation_jobs import (  # noqa: E402
    _jobs, create_job, get_job, update_job,
)

client = TestClient(app)
OWNER = "ruurdsm@queens.edu"
OWNER_HEADERS = {"X-API-Key": generate_session_token(OWNER)}
OTHER_HEADERS = {"X-API-Key": generate_session_token("thaob@queens.edu")}


def _docx_bytes(*paragraphs: str) -> bytes:
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def clean_jobs():
    """Reset the in-memory job registry AND the slowapi rate-limit
    storage between tests so the assertion on orphan-job count is
    precise and the 10/minute defense-prep limit doesn't bleed
    across the broader Defense Prep suite (which also POSTs to the
    same endpoint)."""
    snap = dict(_jobs)
    _jobs.clear()
    # The limiter is module-level (app.state.limiter); resetting its
    # storage wipes the per-IP counters slowapi was tracking.
    try:
        app.state.limiter._storage.reset()
    except Exception:  # noqa: BLE001
        pass
    yield
    _jobs.clear()
    _jobs.update(snap)


# ── POST endpoint contract ────────────────────────────────────────────────

def test_post_returns_202_creates_pending_job(clean_jobs, monkeypatch):
    """A successful upload registers a pending job_id and returns 202
    BEFORE the background task does any LLM work — this is the whole
    point of the pattern."""
    from agents import peer_review
    monkeypatch.setattr(
        peer_review, "run_defense_prep_with_harness",
        lambda _ctx: "### Anticipated Q&A\n\n**Q1.** test")

    res = client.post(
        "/api/council/defense-prep",
        headers=OWNER_HEADERS,
        files={"file": ("paper.docx", _docx_bytes("Body."),
                        "application/vnd.openxmlformats-officedocument."
                        "wordprocessingml.document")},
    )
    assert res.status_code == 202
    body = res.json()
    assert body["status"] == "pending"
    job = get_job(body["job_id"])
    assert job is not None
    assert job["document_type"] == "defense_prep"
    assert job["owner_email"] == OWNER
    # The background task may have already run by the time we read,
    # since the request returned 202 before awaiting; either state is
    # legal — pending if pre-run, complete if post-run.
    assert job["status"] in ("pending", "running", "complete")


def test_post_unsupported_type_creates_no_job(clean_jobs):
    """Bad uploads (422) must NOT enqueue jobs — otherwise the registry
    fills with stuck-pending rows from rejected uploads, and the user's
    job-history endpoint shows phantom entries."""
    assert len(_jobs) == 0
    res = client.post(
        "/api/council/defense-prep",
        headers=OWNER_HEADERS,
        files={"file": ("notes.txt", b"plain", "text/plain")})
    assert res.status_code == 422
    assert len(_jobs) == 0


def test_post_empty_upload_creates_no_job(clean_jobs):
    assert len(_jobs) == 0
    res = client.post(
        "/api/council/defense-prep",
        headers=OWNER_HEADERS,
        files={"file": ("midpoint.docx", b"",
                        "application/octet-stream")})
    assert res.status_code == 422
    assert len(_jobs) == 0


# ── GET polling endpoint contract ─────────────────────────────────────────

def _seed_job(status: str, **extra) -> str:
    """Create a defense_prep job in the requested status directly via
    the registry — bypasses the endpoint so we can pin the polling
    response for every state without running the background task."""
    job = create_job("defense_prep", OWNER)
    update_job(
        job["job_id"],
        status=status,
        _filename="midpoint.docx",
        _draft_chars=1234,
        _word_count=210,
        _result_text=extra.get("result_text"),
        error=extra.get("error"),
        completed_at=extra.get("completed_at"),
    )
    return job["job_id"]


def test_get_pending_returns_status_pending(clean_jobs):
    jid = _seed_job("pending")
    res = client.get(f"/api/v1/defense-prep/{jid}", headers=OWNER_HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert body["job_id"] == jid
    assert body["status"] == "pending"
    assert body["filename"] == "midpoint.docx"
    assert body["word_count"] == 210
    # result_text and error are gated on terminal status — pending sees
    # neither, even if they happen to be set on the row.
    assert body["result_text"] is None
    assert body["error"] is None


def test_get_running_returns_status_running(clean_jobs):
    jid = _seed_job("running")
    res = client.get(f"/api/v1/defense-prep/{jid}", headers=OWNER_HEADERS)
    assert res.status_code == 200
    assert res.json()["status"] == "running"


def test_get_complete_returns_result_text(clean_jobs):
    jid = _seed_job(
        "complete",
        result_text="### Anticipated Q&A\n\n**Q1.** Answer.",
        completed_at=datetime.now(timezone.utc),
    )
    res = client.get(f"/api/v1/defense-prep/{jid}", headers=OWNER_HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "complete"
    assert body["result_text"] == "### Anticipated Q&A\n\n**Q1.** Answer."
    assert body["error"] is None
    assert body["completed_at"]
    assert body["elapsed_seconds"] is not None
    assert body["elapsed_seconds"] >= 0


def test_get_failed_returns_error_reason(clean_jobs):
    """The polling endpoint must surface the failure REASON so the UI
    can render it inline rather than spinning forever — this is the
    behaviour Molly explicitly asked for."""
    jid = _seed_job(
        "failed",
        error="Defense prep failed: connection reset",
        completed_at=datetime.now(timezone.utc),
    )
    res = client.get(f"/api/v1/defense-prep/{jid}", headers=OWNER_HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "failed"
    assert body["error"] == "Defense prep failed: connection reset"
    # result_text remains hidden on a failed job — the UI has nothing
    # to render from a non-complete job.
    assert body["result_text"] is None


def test_get_unknown_job_returns_404(clean_jobs):
    res = client.get(
        "/api/v1/defense-prep/does-not-exist", headers=OWNER_HEADERS)
    assert res.status_code == 404


def test_get_other_document_type_returns_404(clean_jobs):
    """A job_id from a different document_type (midpoint_paper, etc)
    is NOT a valid defense_prep job — owner-only is enforced AFTER
    document_type so a midpoint job leaks no metadata."""
    job = create_job("midpoint_paper", OWNER)
    res = client.get(
        f"/api/v1/defense-prep/{job['job_id']}", headers=OWNER_HEADERS)
    assert res.status_code == 404


def test_get_other_user_returns_404(clean_jobs):
    """Owner-only: a defense_prep job owned by user A must 404 for
    user B, even though the job exists."""
    jid = _seed_job("complete", result_text="x",
                    completed_at=datetime.now(timezone.utc))
    res = client.get(f"/api/v1/defense-prep/{jid}", headers=OTHER_HEADERS)
    assert res.status_code == 404


def test_get_requires_team_member(clean_jobs):
    """The polling endpoint is team-gated; an unauthenticated request
    returns 401, not 404 — the auth check fires before the lookup."""
    jid = _seed_job("running")
    res = client.get(f"/api/v1/defense-prep/{jid}")  # no headers
    assert res.status_code in (401, 403)
