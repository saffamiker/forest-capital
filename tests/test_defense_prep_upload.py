"""Thesis Defense Prep — explicit-document-upload flow.

Replaces the saved-draft database lookup (editor_drafts /
paper_versions Final marker) with a multipart upload as the single
source of truth for the Q&A grounding. Tests cover the four
acceptance criteria from the spec:

  1. Upload accepted and text extracted (docx end-to-end; pdf path
     exercised via the dispatcher).
  2. Context injected correctly — the rendered block leads with the
     labelled "SUBMITTED ACADEMIC DOCUMENT" prefix and the extracted
     text, and the harness is called with exactly that block.
  3. Session-only — the endpoint never reaches editor_drafts or
     paper_versions, so a sabotaged getter never fires.
  4. Graceful handling of unsupported file types — surfaces as an
     SSE error frame, never a 5xx.
"""
import io
import os
import sys

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


def _docx_bytes(*paragraphs: str) -> bytes:
    """Build a tiny in-memory docx and return its bytes."""
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _parse_sse(text: str) -> list[tuple[str, str]]:
    """Split an SSE response body into (event_type, payload) tuples."""
    frames: list[tuple[str, str]] = []
    for block in text.split("\n\n"):
        line = block.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            frames.append(("done", ""))
            continue
        # Extract just the "type" field cheaply.
        import json
        try:
            evt = json.loads(payload)
        except ValueError:
            continue
        frames.append((evt.get("type", "unknown"), payload))
    return frames


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
    """Stub run_defense_prep_with_harness so the endpoint completes
    without an LLM call; capture the context_block arg for assertions."""
    from agents import peer_review
    seen: dict = {}

    def _fake(context_block: str) -> str:
        seen["context_block"] = context_block
        return ("### Anticipated Q&A\n\n**Q1.** A canned answer.\n\n"
                "**Q2.** Another canned answer.")
    monkeypatch.setattr(peer_review,
                        "run_defense_prep_with_harness", _fake)
    return seen


def test_endpoint_accepts_docx_upload_and_streams_qa(monkeypatch):
    seen = _patch_harness(monkeypatch)
    raw = _docx_bytes(
        "Midpoint Paper", "Section 1: Methodology.",
        "Section 2: Preliminary results.")
    res = client.post(
        "/api/council/defense-prep",
        headers=TEAM_HEADERS,
        files={"file": ("midpoint.docx", raw,
                        "application/vnd.openxmlformats-officedocument."
                        "wordprocessingml.document")},
    )
    assert res.status_code == 200
    frames = _parse_sse(res.text)
    types = [t for t, _ in frames]
    assert types[0] == "draft_meta"
    assert "arbiter_chunk" in types
    assert types[-1] == "done"
    # The draft_meta carries the uploaded filename as the title.
    import json
    meta = json.loads(frames[0][1])
    assert meta["title"] == "midpoint.docx"
    assert meta["source"] == "upload"
    assert meta["word_count"] > 0
    # The harness was called with the labelled context block + the
    # actual extracted text.
    ctx = seen["context_block"]
    assert "SUBMITTED ACADEMIC DOCUMENT: midpoint.docx" in ctx
    assert "Section 1: Methodology" in ctx
    assert "Section 2: Preliminary results" in ctx


def test_endpoint_rejects_unsupported_file_with_error_frame(monkeypatch):
    _patch_harness(monkeypatch)
    res = client.post(
        "/api/council/defense-prep",
        headers=TEAM_HEADERS,
        files={"file": ("notes.txt", b"plain text body",
                        "text/plain")},
    )
    assert res.status_code == 200  # SSE error, not HTTP error
    frames = _parse_sse(res.text)
    types = [t for t, _ in frames]
    assert "error" in types
    err = next(p for t, p in frames if t == "error")
    assert "Unsupported file type" in err
    assert ".pdf or .docx" in err


def test_endpoint_is_session_only_does_not_touch_saved_drafts(monkeypatch):
    """The endpoint must never reach the editor_drafts or paper_versions
    persistence layer; sabotaging those getters to raise loudly proves
    the endpoint never calls them on the upload path."""
    seen = _patch_harness(monkeypatch)

    def _boom(*_a, **_k):
        raise AssertionError(
            "Defense Prep MUST NOT read from the saved-draft DB on the "
            "upload path — session-only.")

    # Patch any path the old endpoint used (importing-then-calling).
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
    res = client.post(
        "/api/council/defense-prep",
        headers=TEAM_HEADERS,
        files={"file": ("paper.docx", raw,
                        "application/vnd.openxmlformats-officedocument."
                        "wordprocessingml.document")},
    )
    assert res.status_code == 200
    types = [t for t, _ in _parse_sse(res.text)]
    assert "draft_meta" in types and "arbiter_chunk" in types
    assert "Document body that the panel should ground in." \
        in seen["context_block"]
