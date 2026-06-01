"""Thesis Defense Prep — explicit-document-upload flow.

May 30 2026 — the synchronous SSE stream was timing out on Render
(Opus + harness can run 30-180s; the gateway killed idle SSE at
~100s). The endpoint now returns 202 + job_id immediately and the
frontend polls GET /api/v1/defense-prep/{job_id} every 3s. Tests
cover the four acceptance criteria from the original spec, adapted
to the job pattern:

  1. Upload accepted, text extracted (docx end-to-end; pdf path
     exercised via the dispatcher).
  2. Context injected correctly — the rendered block leads with the
     labelled "SUBMITTED ACADEMIC DOCUMENT" prefix and the extracted
     text, and the harness is called with exactly that block when
     the background task runs.
  3. Session-only — the endpoint never reaches editor_drafts or
     paper_versions, so a sabotaged getter never fires.
  4. Graceful handling of unsupported file types — surfaces as a
     synchronous 422 with a clear detail, never a 5xx, and NEVER a
     job_id (no orphan jobs from bad uploads).
"""
import asyncio
import io
import os
import sys
import time

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

client = TestClient(app)
TEAM_HEADERS = {"X-API-Key": generate_session_token("ruurdsm@queens.edu")}


@pytest.fixture(autouse=True)
def _reset_defense_prep_rate_limit():
    """Defense Prep is rate-limited 10/minute per IP. With several
    tests POSTing to /api/council/defense-prep inside one pytest
    session (and other Defense Prep test files in the same run),
    the limit will trip and turn legitimate uploads into 429s.
    Reset the slowapi in-memory storage between tests so each test
    starts with a fresh counter."""
    try:
        app.state.limiter._storage.reset()
    except Exception:  # noqa: BLE001
        pass
    yield


def _docx_bytes(*paragraphs: str) -> bytes:
    """Build a tiny in-memory docx and return its bytes."""
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _wait_for_terminal(job_id: str, timeout: float = 5.0) -> dict:
    """Poll the GET endpoint until status is complete | failed | cancelled
    or the timeout expires. Returns the final job payload."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        res = client.get(
            f"/api/v1/defense-prep/{job_id}", headers=TEAM_HEADERS)
        assert res.status_code == 200, res.text
        last = res.json()
        if last["status"] in ("complete", "failed", "cancelled"):
            return last
        # Let the spawned background task make progress — TestClient
        # drives the asyncio loop per-request, so a small sleep on the
        # test thread lets the task complete between polls.
        time.sleep(0.05)
    raise AssertionError(
        f"job {job_id} did not reach terminal state within {timeout}s; "
        f"last status={last}")


# ── extractors (pure) ──────────────────────────────────────────────────────

def test_extract_docx_text_in_memory():
    from tools.academic_context import extract_docx_text
    raw = _docx_bytes("First paragraph.", "Second paragraph with detail.")
    text = extract_docx_text(raw)
    assert "First paragraph" in text
    assert "Second paragraph with detail" in text


def test_extract_uploaded_text_dispatcher_docx_and_unsupported():
    from tools.academic_context import extract_uploaded_text
    raw = _docx_bytes("Hello world.")
    assert "Hello world" in extract_uploaded_text("midpoint.docx", raw)
    with pytest.raises(ValueError, match="Unsupported file type"):
        extract_uploaded_text("paper.txt", b"plain text")


def test_extract_uploaded_text_dispatcher_pdf_path(monkeypatch):
    """The .pdf branch delegates to extract_document_text — verify the
    dispatcher routes correctly without needing a real PDF byte stream."""
    from tools import academic_context
    calls: dict = {}

    def _fake_pdf(filename, raw):
        calls["filename"] = filename
        calls["raw_len"] = len(raw)
        return "pdf-extracted text"

    monkeypatch.setattr(academic_context, "extract_document_text", _fake_pdf)
    out = academic_context.extract_uploaded_text("paper.pdf", b"%PDF-fake")
    assert out == "pdf-extracted text"
    assert calls == {"filename": "paper.pdf", "raw_len": len(b"%PDF-fake")}


# ── renderer leads with the labelled document ──────────────────────────────

def test_render_includes_label_and_extracted_text():
    from agents.peer_review import (
        build_defense_prep_context_block,
        render_defense_prep_context_block,
    )
    ctx = build_defense_prep_context_block(
        "Forest Capital", "MIDPOINT BODY TEXT 12345",
        source_name="midpoint.docx")
    rendered = render_defense_prep_context_block(ctx)
    # The label leads, naming the filename, with the user's exact
    # instruction line.
    assert "SUBMITTED ACADEMIC DOCUMENT: midpoint.docx" in rendered
    assert ("The following is the submitted academic document. "
            "Answer all questions using this as the primary source.") in rendered
    # The extracted text appears inside the BEGIN/END markers.
    begin = rendered.index("--- BEGIN SUBMITTED DOCUMENT ---")
    end = rendered.index("--- END SUBMITTED DOCUMENT ---")
    assert "MIDPOINT BODY TEXT 12345" in rendered[begin:end]
    # The labelled block comes BEFORE the team / categories framing.
    assert rendered.index("SUBMITTED ACADEMIC DOCUMENT") \
        < rendered.index("=== TEAM SUBMISSION ===")


# ── endpoint integration ───────────────────────────────────────────────────

def _patch_harness(monkeypatch) -> dict:
    """Stub run_defense_prep_with_harness so the background task
    completes without an LLM call; capture the context_block arg for
    assertions. The endpoint imports the harness lazily inside the
    background task, so we patch the source module."""
    from agents import peer_review
    seen: dict = {}

    def _fake(context_block: str) -> str:
        seen["context_block"] = context_block
        return ("### Anticipated Q&A\n\n**Q1.** A canned answer.\n\n"
                "**Q2.** Another canned answer.")
    monkeypatch.setattr(peer_review,
                        "run_defense_prep_with_harness", _fake)
    return seen


def test_endpoint_returns_202_with_job_id(monkeypatch):
    """POST 202 + job_id is the success contract — the LLM run is
    spawned as a background task, NOT awaited inside the request."""
    _patch_harness(monkeypatch)
    raw = _docx_bytes("Midpoint Paper", "Section 1: Methodology.")
    res = client.post(
        "/api/council/defense-prep",
        headers=TEAM_HEADERS,
        files={"file": ("midpoint.docx", raw,
                        "application/vnd.openxmlformats-officedocument."
                        "wordprocessingml.document")},
    )
    assert res.status_code == 202, res.text
    body = res.json()
    assert isinstance(body.get("job_id"), str) and body["job_id"]
    assert body["status"] == "pending"
    assert body["filename"] == "midpoint.docx"
    assert body["word_count"] > 0


def test_polling_endpoint_returns_complete_result(monkeypatch):
    """The polling endpoint must reach `complete` with the verdict
    text and the elapsed timer set.

    Drives `_run_defense_prep_job` directly via `asyncio.run` rather
    than POSTing through TestClient and polling. The TestClient
    portal creates a per-request event loop that closes when the
    POST returns, so a background task spawned with `asyncio.create_
    task` on that loop is never guaranteed to settle inside a
    follow-up GET poll. The polling-endpoint contract is read via
    the same GET, but only AFTER the background task has been
    driven to completion synchronously. Mirrors the registry-direct
    pattern documented in tests/test_defense_prep_job.py."""
    import asyncio
    from main import _run_defense_prep_job
    from tools.generation_jobs import create_job, update_job

    seen = _patch_harness(monkeypatch)

    # Build the job + payload exactly as the POST endpoint would,
    # then await the background task in place so it lands on the
    # same event loop. The completed registry row is what the GET
    # then reads.
    raw = _docx_bytes(
        "Midpoint Paper", "Section 1: Methodology.",
        "Section 2: Preliminary results.")
    from tools.academic_context import extract_uploaded_text
    draft_text = extract_uploaded_text("midpoint.docx", raw)
    job = create_job("defense_prep", "ruurdsm@queens.edu")
    update_job(job["job_id"], _filename="midpoint.docx",
               _draft_chars=len(draft_text),
               _word_count=len(draft_text.split()),
               _result_text=None)
    asyncio.run(_run_defense_prep_job(
        job["job_id"], "midpoint.docx", draft_text,
        "ruurdsm@queens.edu", {"email": "ruurdsm@queens.edu"}, ""))

    res = client.get(
        f"/api/v1/defense-prep/{job['job_id']}", headers=TEAM_HEADERS)
    assert res.status_code == 200
    final = res.json()
    assert final["status"] == "complete", final
    assert final["filename"] == "midpoint.docx"
    assert final["word_count"] > 0
    assert final["result_text"]
    assert "Anticipated Q&A" in final["result_text"]
    assert final["error"] is None
    assert final["completed_at"]
    assert final["elapsed_seconds"] is not None

    # The harness was called with the labelled context block + the
    # actual extracted text — verifies the background task threaded
    # the upload through correctly.
    ctx = seen["context_block"]
    assert "SUBMITTED ACADEMIC DOCUMENT: midpoint.docx" in ctx
    assert "Section 1: Methodology" in ctx
    assert "Section 2: Preliminary results" in ctx


def test_endpoint_rejects_unsupported_file_with_422_no_job(monkeypatch):
    """Upload validation is SYNCHRONOUS — a bad extension returns 422
    with a JSON detail, and NEVER creates a job. This prevents the job
    table filling up with stuck-pending rows from rejected uploads."""
    _patch_harness(monkeypatch)
    # Snapshot the job count so we can assert no orphan job was
    # created by the bad upload.
    from tools.generation_jobs import _jobs
    n_before = len(_jobs)

    res = client.post(
        "/api/council/defense-prep",
        headers=TEAM_HEADERS,
        files={"file": ("notes.txt", b"plain text body", "text/plain")},
    )
    assert res.status_code == 422, res.text
    detail = res.json().get("detail", "")
    assert "Unsupported file type" in detail
    assert ".pdf or .docx" in detail
    assert len(_jobs) == n_before, "Bad upload must not create a job."


def test_endpoint_rejects_empty_upload(monkeypatch):
    _patch_harness(monkeypatch)
    res = client.post(
        "/api/council/defense-prep",
        headers=TEAM_HEADERS,
        files={"file": ("midpoint.docx", b"", "application/octet-stream")},
    )
    assert res.status_code == 422
    assert "empty" in res.json()["detail"].lower()


def test_endpoint_rejects_oversize_upload(monkeypatch):
    _patch_harness(monkeypatch)
    huge = b"\x00" * (10 * 1024 * 1024 + 1)
    res = client.post(
        "/api/council/defense-prep",
        headers=TEAM_HEADERS,
        files={"file": ("paper.docx", huge,
                        "application/vnd.openxmlformats-officedocument."
                        "wordprocessingml.document")},
    )
    assert res.status_code == 422
    assert "10 MB" in res.json()["detail"]


def test_polling_unknown_job_returns_404():
    res = client.get(
        "/api/v1/defense-prep/no-such-job-id", headers=TEAM_HEADERS)
    assert res.status_code == 404


def test_polling_other_users_job_returns_404(monkeypatch):
    """Owner-only: a Defense Prep job_id created by user A must 404
    for user B, even though the job exists."""
    _patch_harness(monkeypatch)
    raw = _docx_bytes("Midpoint body.")
    res = client.post(
        "/api/council/defense-prep",
        headers=TEAM_HEADERS,  # ruurdsm@
        files={"file": ("paper.docx", raw,
                        "application/vnd.openxmlformats-officedocument."
                        "wordprocessingml.document")},
    )
    assert res.status_code == 202
    job_id = res.json()["job_id"]

    other = {"X-API-Key": generate_session_token("thaob@queens.edu")}
    res2 = client.get(
        f"/api/v1/defense-prep/{job_id}", headers=other)
    assert res2.status_code == 404


def test_endpoint_is_session_only_does_not_touch_saved_drafts(monkeypatch):
    """The defense-prep upload path must never reach the editor_drafts
    or paper_versions persistence layer; sabotaging those getters to
    raise loudly proves the upload path never calls them.

    Drives `_run_defense_prep_job` directly (same reason as
    test_polling_endpoint_returns_complete_result — TestClient's
    per-request event loop closes before a background task can land
    a terminal status). The harness mock returns instantly so this
    test exercises ONLY the session-isolation contract; the
    sabotaged getters are the assertion."""
    import asyncio
    from main import _run_defense_prep_job
    from tools.generation_jobs import create_job, get_job, update_job

    captured: dict[str, str] = {}

    def _instant_verdict(context_block: str) -> str:
        captured["context_block"] = context_block
        return ("### Anticipated Q&A\n\n"
                "**Q1.** Session-isolation test — canned answer.")

    def _boom(*_a, **_k):
        raise AssertionError(
            "Defense Prep MUST NOT read from the saved-draft DB on the "
            "upload path — session-only.")

    # `agents.peer_review` is the source module — the background
    # task imports `run_defense_prep_with_harness` from there. Patch
    # on the source module so the import inside the task picks up
    # the instant-verdict mock regardless of import timing.
    from agents import peer_review
    monkeypatch.setattr(
        peer_review, "run_defense_prep_with_harness", _instant_verdict)

    import tools.editor_drafts as ed
    monkeypatch.setattr(ed, "get_current_draft", _boom)
    try:
        import tools.paper_versions as pv
        for name in ("get_canonical_version", "get_final_version"):
            if hasattr(pv, name):
                monkeypatch.setattr(pv, name, _boom)
    except ImportError:
        pass

    raw = _docx_bytes("Document body that the panel should ground in.")
    from tools.academic_context import extract_uploaded_text
    draft_text = extract_uploaded_text("paper.docx", raw)
    job = create_job("defense_prep", "ruurdsm@queens.edu")
    update_job(job["job_id"], _filename="paper.docx",
               _draft_chars=len(draft_text),
               _word_count=len(draft_text.split()),
               _result_text=None)
    # If the sabotaged getters fire during the task, the AssertionError
    # they raise propagates out of `_run_defense_prep_job` via its
    # try/except as a `failed` status — caught below.
    asyncio.run(_run_defense_prep_job(
        job["job_id"], "paper.docx", draft_text,
        "ruurdsm@queens.edu", {"email": "ruurdsm@queens.edu"}, ""))

    final = get_job(job["job_id"])
    assert final["status"] == "complete", (
        f"Defense Prep failed unexpectedly — saved-draft sabotage may "
        f"have fired. Status={final['status']!r}, error={final.get('error')!r}")
    assert "Document body that the panel should ground in." \
        in captured.get("context_block", "")
