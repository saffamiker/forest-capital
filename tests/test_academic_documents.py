"""
tests/test_academic_documents.py

Tests for the document-upload feature (Feature 2):
  - tools/academic_context.py text extraction, formatting, injection
  - migration 008 structure
  - POST /api/v1/documents/academic/upload request validation

The DB-touching paths (insert / list / delete) are exercised in
deployment, not here — the test environment has no PostgreSQL, so these
tests cover the pure functions and the pre-DB validation branches.
"""
from __future__ import annotations

import io
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

client = TestClient(app)
SESSION_HEADERS = {"X-API-Key": generate_session_token("ruurdsm@queens.edu")}


# ── Text extraction ───────────────────────────────────────────────────────────

class TestTextExtraction:
    """extract_document_text() is PDF-only. The former non-PDF text branch
    was removed once .md handling moved upstream into the upload endpoint —
    so this function now only ever receives PDF content."""

    def test_pdf_text_is_extracted(self):
        """A text-based PDF must round-trip through pypdf."""
        from reportlab.pdfgen import canvas
        from tools.academic_context import extract_document_text

        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        c.drawString(100, 700, "FINAL PRESENTATION worth thirty five percent")
        c.save()
        out = extract_document_text("requirements.pdf", buf.getvalue())
        assert "thirty five percent" in out

    def test_extract_document_text_is_pdf_only(self):
        """Non-PDF bytes no longer silently decode as text — the text
        branch is gone. Markdown is handled in the upload endpoint and
        never reaches this function; passing non-PDF content here raises
        (pypdf rejects it) rather than returning a decoded string."""
        from tools.academic_context import extract_document_text
        with pytest.raises(Exception):
            extract_document_text("notes.md", b"# Markdown must not reach here")


# ── Formatting and injection ──────────────────────────────────────────────────

class TestFormatAndInject:
    def test_empty_documents_format_to_empty_string(self):
        from tools.academic_context import format_academic_context
        assert format_academic_context([]) == ""

    def test_documents_labelled_by_type(self):
        from tools.academic_context import format_academic_context
        out = format_academic_context([
            {"name": "rubric.pdf", "document_type": "midpoint_requirements",
             "content_text": "3 pages double spaced"},
            {"name": "final.txt", "document_type": "final_presentation_requirements",
             "content_text": "18-20 minutes"},
        ])
        assert "MIDPOINT CHECK-IN REQUIREMENTS" in out
        assert "FINAL PRESENTATION REQUIREMENTS" in out
        assert "3 pages double spaced" in out
        assert "18-20 minutes" in out
        assert "ACADEMIC CONTEXT" in out

    def test_inject_is_noop_when_no_documents(self):
        """With no uploaded documents the system prompt is returned
        unchanged — every agent can call inject unconditionally."""
        from tools.academic_context import inject_academic_context, _CONTEXT_CACHE
        _CONTEXT_CACHE["text"] = ""
        base = "You are an analyst."
        assert inject_academic_context(base) == base

    def test_inject_appends_cached_context(self):
        from tools.academic_context import inject_academic_context, _CONTEXT_CACHE
        _CONTEXT_CACHE["text"] = "\n\n=== ACADEMIC CONTEXT ===\n..."
        try:
            out = inject_academic_context("You are an analyst.")
            assert out.startswith("You are an analyst.")
            assert "ACADEMIC CONTEXT" in out
        finally:
            _CONTEXT_CACHE["text"] = ""

    def test_document_types_constant(self):
        from tools.academic_context import DOCUMENT_TYPES
        # Original three plus the three Academic Review types.
        for t in ("midpoint_requirements", "final_presentation_requirements",
                  "midpoint_draft", "presentation_slides", "presentation_script",
                  "other"):
            assert t in DOCUMENT_TYPES

    def test_new_document_types_accepted_by_upload(self):
        """The three Academic Review types must pass upload validation —
        a wrong type is the only thing the pre-DB branch rejects."""
        for t in ("midpoint_draft", "presentation_slides", "presentation_script"):
            r = client.post(
                "/api/v1/documents/academic/upload",
                files={"file": ("x.txt", b"content", "text/plain")},
                data={"document_type": t},
                headers=SESSION_HEADERS,
            )
            # Not 422 — the type is valid (a 500 here is just the test-env
            # DB being absent, which is fine; the point is type acceptance).
            assert r.status_code != 422


# ── Migration 008 ─────────────────────────────────────────────────────────────

class TestMigration008:
    def test_revision_chains_from_007(self):
        import importlib.util
        path = os.path.join(
            os.path.dirname(__file__), "..", "backend", "migrations", "versions",
            "008_create_academic_documents.py",
        )
        spec = importlib.util.spec_from_file_location("migration_008", path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.revision == "008"
        assert mod.down_revision == "007"
        assert hasattr(mod, "upgrade") and hasattr(mod, "downgrade")


# ── Upload endpoint validation ────────────────────────────────────────────────

class TestUploadValidation:
    """The validation branches fire before any database access, so they
    are testable without PostgreSQL."""

    def test_unknown_document_type_rejected(self):
        r = client.post(
            "/api/v1/documents/academic/upload",
            files={"file": ("x.txt", b"content", "text/plain")},
            data={"document_type": "not_a_real_type"},
            headers=SESSION_HEADERS,
        )
        assert r.status_code == 422
        assert "document_type" in r.text

    def test_empty_file_rejected(self):
        r = client.post(
            "/api/v1/documents/academic/upload",
            files={"file": ("x.txt", b"", "text/plain")},
            data={"document_type": "other"},
            headers=SESSION_HEADERS,
        )
        assert r.status_code == 422

    def test_upload_requires_auth(self):
        r = client.post(
            "/api/v1/documents/academic/upload",
            files={"file": ("x.txt", b"content", "text/plain")},
            data={"document_type": "other"},
        )
        assert r.status_code == 401

    def test_list_endpoint_requires_auth(self):
        assert client.get("/api/v1/documents/academic").status_code == 401


class TestMarkdownUpload:
    """Markdown (.md) files are accepted alongside PDFs; .txt is not."""

    def test_md_upload_stores_exact_content(self, monkeypatch):
        """A .md file is read directly as UTF-8 — content_text is the file
        content verbatim, with no pypdf extraction artifacts. insert is
        stubbed because the test environment has no database."""
        import tools.academic_context as ac
        captured: dict = {}

        async def _fake_insert(name, document_type, content_text):
            captured["name"] = name
            captured["content_text"] = content_text
            return "fake-doc-id"

        monkeypatch.setattr(ac, "insert_academic_document", _fake_insert)

        md = "# Midpoint Rubric\n\n- 3 pages, double-spaced\n- 12-point font"
        r = client.post(
            "/api/v1/documents/academic/upload",
            files={"file": ("rubric.md", md.encode("utf-8"), "text/plain")},
            data={"document_type": "midpoint_requirements"},
            headers=SESSION_HEADERS,
        )
        assert r.status_code == 200
        # Stored content is the file content exactly — no extraction artifacts.
        assert captured["content_text"] == md
        assert r.json()["file_type"] == "MD"

    def test_txt_upload_rejected_with_400(self):
        """Only PDF and Markdown are supported — a .txt file is a 400."""
        r = client.post(
            "/api/v1/documents/academic/upload",
            files={"file": ("notes.txt", b"plain text content", "text/plain")},
            data={"document_type": "other"},
            headers=SESSION_HEADERS,
        )
        assert r.status_code == 400
        assert "PDF and Markdown" in r.json()["detail"]
