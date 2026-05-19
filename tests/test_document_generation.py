"""
tests/test_document_generation.py

Tests for the academic document-generation endpoints — the three graded
deliverables assembled from real platform data, light-mode charts and
AI-generated narrative:

  POST /api/v1/export/midpoint-paper      → 3-page midpoint paper (.docx)
  POST /api/v1/export/executive-brief     → 5-page executive brief (.docx)
  POST /api/v1/export/presentation-deck   → 16-slide final deck (.pptx)

Two tiers, the same pattern as test_export_package.py:
  - Endpoint-contract tests run everywhere including CI. In the test
    environment the analytics caches are cold and no academic documents
    are stored, so these tests double as the graceful-degradation tests:
    every section falls back to a [DATA PENDING] marker and the document
    still assembles into a valid, parseable file.
  - One DB round-trip confirms a document-generation run logs to
    agent_interactions for a team email and is gated out for a non-team
    email; it skips cleanly when no live PostgreSQL is reachable.
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import uuid

import pytest
from docx import Document
from fastapi.testclient import TestClient
from pptx import Presentation

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
NON_TEAM_EMAIL = "panttserk@queens.edu"
SESSION_HEADERS = {"X-API-Key": generate_session_token(TEAM_EMAIL)}

MIDPOINT = "/api/v1/export/midpoint-paper"
BRIEF = "/api/v1/export/executive-brief"
DECK = "/api/v1/export/presentation-deck"

_DOCX_CT = "wordprocessingml"
_PPTX_CT = "presentationml"


def _run(coro):
    return asyncio.run(coro)


def _docx_text(content: bytes) -> str:
    """All header, paragraph and table text from a .docx, for content checks."""
    doc = Document(io.BytesIO(content))
    parts: list[str] = []
    for section in doc.sections:
        parts.extend(p.text for p in section.header.paragraphs)
    parts.extend(p.text for p in doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def _pptx_text(content: bytes) -> str:
    """All shape text from a .pptx, for [DATA PENDING] checks."""
    prs = Presentation(io.BytesIO(content))
    parts: list[str] = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                parts.append(shape.text_frame.text)
            if shape.has_table:
                for row in shape.table.rows:
                    parts.extend(c.text for c in row.cells)
    return "\n".join(parts)


# ── Endpoint contract — runs in CI ────────────────────────────────────────────

class TestDocumentGenerationContract:
    # The endpoints are async: a POST creates a job and returns 202 with
    # a job_id; the file is produced by a background task. The document
    # CONTENT checks call the generation helpers directly — the
    # background task does not complete under Starlette's TestClient.

    def test_midpoint_paper_returns_202_job(self):
        resp = client.post(MIDPOINT, headers=SESSION_HEADERS)
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "pending"
        assert body["job_id"]

    def test_executive_brief_returns_202_job(self):
        resp = client.post(BRIEF, headers=SESSION_HEADERS)
        assert resp.status_code == 202
        assert resp.json()["status"] == "pending"
        assert resp.json()["job_id"]

    def test_presentation_deck_returns_202_job(self):
        resp = client.post(DECK, headers=SESSION_HEADERS)
        assert resp.status_code == 202
        assert resp.json()["status"] == "pending"
        assert resp.json()["job_id"]

    def test_midpoint_document_is_a_valid_docx_with_headings(self):
        import main
        docx_bytes, filename, media, _draft = _run(
            main._generate_midpoint_document(TEAM_EMAIL))
        assert _DOCX_CT in media
        assert filename.endswith(".docx")
        text = _docx_text(docx_bytes)
        assert "Data and Methodology" in text
        assert "Preliminary Results" in text
        assert "Roles and Division of Labor" in text
        assert "Next Steps" in text
        assert "AI DRAFT" in text          # mandatory banner
        # Cold caches in the test environment — every data-dependent
        # section degrades to a [DATA PENDING] marker.
        assert "[DATA PENDING]" in text

    def test_executive_brief_document_is_a_valid_docx_with_headings(self):
        import main
        docx_bytes, filename, media, _draft = _run(
            main._generate_brief_document(TEAM_EMAIL))
        assert _DOCX_CT in media
        assert filename.endswith(".docx")
        text = _docx_text(docx_bytes)
        assert "Executive Summary" in text
        assert "Methodology Overview" in text
        assert "Key Findings" in text
        assert "Limitations and Risks" in text
        assert "Final Recommendations" in text
        assert "[DATA PENDING]" in text

    def test_presentation_deck_document_is_a_valid_16_slide_pptx(self):
        import main
        pptx_bytes, filename, media, _draft = _run(
            main._generate_deck_document(TEAM_EMAIL))
        assert _PPTX_CT in media
        assert filename.endswith(".pptx")
        prs = Presentation(io.BytesIO(pptx_bytes))
        assert len(prs.slides) == 16
        # Missing analytics data / no matplotlib must not fail the deck.
        assert "[DATA PENDING]" in _pptx_text(pptx_bytes)

    def test_all_three_require_authentication(self):
        for endpoint in (MIDPOINT, BRIEF, DECK):
            assert client.post(endpoint).status_code == 401


# ── DB round-trip — skips without a live database ─────────────────────────────

_db_ready_cache: bool | None = None


async def _fresh_session():
    """Disposes the pooled engine and returns a session on the current loop."""
    from database import engine, AsyncSessionLocal
    if engine is not None:
        await engine.dispose()
    return AsyncSessionLocal()  # type: ignore[union-attr]


def _db_ready() -> bool:
    """True when a live PostgreSQL with the activity tables is reachable."""
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
                await s.execute(text("SELECT 1 FROM agent_interactions LIMIT 1"))
            return True

        _db_ready_cache = _run(_probe())
    except Exception:
        _db_ready_cache = False
    return _db_ready_cache


class TestDocumentGenerationLogging:
    def test_document_generation_logged_and_team_gated(self):
        """Document generation records a run via log_agent_interaction with
        interaction_type='export' and a deliverable in metadata. Exercises
        that data layer directly — the single-loop pattern test_activity.py
        uses — so it verifies a team user's generation logs a row and a
        non-team user is gated out."""
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
                    session_type="analytical", interaction_type="export",
                    agents_involved=["academic_writer"],
                    response_summary="Midpoint paper generated",
                    metadata={"deliverable": "midpoint_paper"})
                assert ok is True
                ok2 = await log_agent_interaction(
                    user_email=NON_TEAM_EMAIL, session_id=sid,
                    session_type="analytical", interaction_type="export",
                    metadata={"deliverable": "midpoint_paper"})
                assert ok2 is False
                async with AsyncSessionLocal() as s:
                    row = await s.execute(
                        text("SELECT COUNT(*) FROM agent_interactions "
                             "WHERE session_id = :sid AND "
                             "interaction_type = 'export'"),
                        {"sid": sid})
                    assert row.scalar() == 1
            finally:
                async with AsyncSessionLocal() as s:
                    await s.execute(
                        text("DELETE FROM agent_interactions WHERE session_id = :sid"),
                        {"sid": sid})
                    await s.commit()

        _run(scenario())
