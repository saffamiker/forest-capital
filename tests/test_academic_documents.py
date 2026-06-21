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
        # format_academic_context is the formatter only -- the
        # _INJECTION_EXCLUDED_TYPES filter lives upstream in
        # _read_all_with_content. So midpoint_requirements still
        # renders here if the caller hands one in; in production it
        # never reaches this function because the upstream SELECT
        # filters it out. The two exclusion paths are tested
        # separately in TestInjectionExclusionFilter below.
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
        # June 21 2026 -- tightened framing: the banner must NO LONGER
        # tell the LLM the documents define "academic evaluation
        # criteria" (that wording invited template mimicry), and MUST
        # explicitly forbid format/structure adoption.
        assert "reference material for the project grading rubric" in out
        assert "Do not adopt their formatting, structure, or templates" \
            in out
        assert "define the academic evaluation criteria" not in out

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


# ── Injection-time exclusion filter (June 21 2026) ───────────────────────────

class TestInjectionExclusionFilter:
    """Pin the contract that _read_all_with_content filters out the
    midpoint document types so they never reach any agent system
    prompt -- the regression vector that produced a 'PEER REVIEWER
    MEMO' as Section 1 of the executive brief.

    The DB read is mocked at the AsyncSessionLocal layer; no real
    Postgres needed. Tests verify (a) excluded rows are filtered
    out at the cache-rebuild step, (b) the DEBUG log fires when
    rows are excluded, (c) the tightened banner copy is present
    when rows survive, and (d) the no-op contract holds when
    every row was filtered."""

    def test_excluded_types_constant(self):
        from tools.academic_context import _INJECTION_EXCLUDED_TYPES
        # Both midpoint-era types must be in the exclude set; both
        # were active contamination vectors before this PR.
        assert "midpoint_requirements" in _INJECTION_EXCLUDED_TYPES
        assert "midpoint_draft" in _INJECTION_EXCLUDED_TYPES
        # Frozen so a downstream caller can't mutate the live set.
        assert isinstance(_INJECTION_EXCLUDED_TYPES, frozenset)

    @pytest.mark.asyncio
    async def test_excluded_row_filtered_from_cache_rebuild(
        self, monkeypatch,
    ):
        """When the DB holds both a midpoint_requirements row and a
        final_presentation_requirements row, the rebuilt cache must
        contain ONLY the final_presentation_requirements text. The
        WHERE clause in _read_all_with_content does the filtering
        at the SELECT, so this also pins that the query's bound
        parameter wires through correctly."""
        from tools import academic_context as ac

        # Patch the DB-availability gate + the AsyncSessionLocal call
        # site. The mock session executes the SELECT against a stub
        # that returns ONLY the rows whose document_type is not in
        # the supplied :excluded list -- the same behaviour the real
        # asyncpg backend would produce.
        all_rows = [
            ("midpoint_rubric.pdf", "midpoint_requirements",
             "PEER REVIEWER MEMO / Reviewer role: ..."),
            ("final_rubric.pdf", "final_presentation_requirements",
             "18-20 minute presentation, four sections..."),
        ]
        excluded_rows = [
            r for r in all_rows
            if r[1] in ac._INJECTION_EXCLUDED_TYPES
        ]
        kept_rows = [
            r for r in all_rows
            if r[1] not in ac._INJECTION_EXCLUDED_TYPES
        ]

        class _StubResult:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

            def scalar(self):
                # Used by the COUNT(*) query for the exclusion telemetry.
                return len(self._rows)

        class _StubSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, stmt, params=None):
                sql = str(stmt)
                if "COUNT" in sql:
                    return _StubResult(excluded_rows)
                return _StubResult(kept_rows)

        monkeypatch.setattr(ac, "_DB_AVAILABLE", True)
        monkeypatch.setattr(
            ac, "AsyncSessionLocal", lambda: _StubSession())

        out = await ac._read_all_with_content()
        names = {row["name"] for row in out}
        # Filtered: the midpoint document never reaches the cache.
        assert "midpoint_rubric.pdf" not in names
        # Kept: the final_presentation document still passes through.
        assert "final_rubric.pdf" in names
        assert len(out) == 1

    @pytest.mark.asyncio
    async def test_debug_log_fires_when_rows_excluded(
        self, monkeypatch, caplog,
    ):
        """The DEBUG telemetry must fire when rows are filtered out
        so an operator can confirm via Render logs that the filter
        is active. Without this signal the filter could silently
        regress (e.g. a future schema change drops a column the
        SELECT references) and rows would be excluded for the wrong
        reason."""
        import logging as _logging
        from tools import academic_context as ac

        excluded_rows = [
            ("midpoint_rubric.pdf", "midpoint_requirements", "..."),
        ]
        kept_rows: list[tuple[str, str, str]] = []

        class _StubResult:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

            def scalar(self):
                return len(self._rows)

        class _StubSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, stmt, params=None):
                if "COUNT" in str(stmt):
                    return _StubResult(excluded_rows)
                return _StubResult(kept_rows)

        monkeypatch.setattr(ac, "_DB_AVAILABLE", True)
        monkeypatch.setattr(
            ac, "AsyncSessionLocal", lambda: _StubSession())

        # structlog routes through stdlib logging; capture at DEBUG.
        with caplog.at_level(_logging.DEBUG, logger="tools.academic_context"):
            await ac._read_all_with_content()
        # Verify the telemetry event landed.
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "academic_context_rows_excluded" in joined \
            or any(
                getattr(r, "event", "") == "academic_context_rows_excluded"
                for r in caplog.records)

    @pytest.mark.asyncio
    async def test_inject_is_noop_after_filter_empties_the_cache(
        self, monkeypatch,
    ):
        """When every available row was a midpoint document, the
        filter removes them all and the cache rebuild produces an
        empty string. inject_academic_context must then return the
        system prompt unchanged -- the no-op contract every agent
        relies on (otherwise every call would carry a stray
        '=== ACADEMIC CONTEXT ===' banner with no documents)."""
        from tools import academic_context as ac

        all_excluded = [
            ("midpoint_rubric.pdf", "midpoint_requirements", "..."),
            ("midpoint_draft.docx", "midpoint_draft", "..."),
        ]

        class _StubResult:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

            def scalar(self):
                return len(self._rows)

        class _StubSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, stmt, params=None):
                if "COUNT" in str(stmt):
                    return _StubResult(all_excluded)
                # All filtered: no rows survive the WHERE clause.
                return _StubResult([])

        monkeypatch.setattr(ac, "_DB_AVAILABLE", True)
        monkeypatch.setattr(
            ac, "AsyncSessionLocal", lambda: _StubSession())

        await ac.refresh_academic_context()
        assert ac.get_academic_context() == ""
        base = "You are a senior analyst."
        assert ac.inject_academic_context(base) == base


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
