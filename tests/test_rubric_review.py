"""
tests/test_rubric_review.py — May 25 2026.

Pins the POST /api/v1/documents/drafts/{id}/rubric-review endpoint:
the Writing Assistant panel's "Review Against Rubric" button. A single
Gemini call against the FNA 670 midpoint rubric — read-only, never
modifies the draft.

Two layers tested:
  1. _parse_rubric_review — the JSON-parse helper that tolerates the
     Gemini response shapes Gemini can return (raw JSON, JSON wrapped
     in ```json fences, JSON with a stray preamble). Pure function;
     runs without a DB.
  2. Endpoint contract — auth gating + the test-env stub shape. The
     real Gemini path is exercised by integration tests only.
"""
from __future__ import annotations

import asyncio
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
TEAM = {"X-API-Key": generate_session_token("thaob@queens.edu")}
VIEWER = {"X-API-Key": generate_session_token("panttserk@queens.edu")}


def _run(coro):
    return asyncio.run(coro)


_db_ready_cache: bool | None = None


def _db_ready() -> bool:
    """Mirrors the editor-drafts probe — only the live-DB tests need
    it. The endpoint-contract tests run without a DB by mocking
    get_draft."""
    global _db_ready_cache
    if _db_ready_cache is not None:
        return _db_ready_cache
    try:
        from tools.cache import _DB_AVAILABLE
        if not _DB_AVAILABLE:
            _db_ready_cache = False
            return False
        from sqlalchemy import text
        from database import engine, AsyncSessionLocal

        async def _probe() -> bool:
            if engine is not None:
                await engine.dispose()
            async with AsyncSessionLocal() as s:  # type: ignore[union-attr]
                await s.execute(text("SELECT 1 FROM editor_drafts LIMIT 1"))
            return True

        _db_ready_cache = _run(_probe())
    except Exception:
        _db_ready_cache = False
    return _db_ready_cache


# ── _parse_rubric_review — pure helper ────────────────────────────────────────


class TestParseRubricReview:
    """Gemini returns JSON one way most of the time and three ways the
    rest of the time. The parser must tolerate every form Gemini has
    emitted in production — raw JSON, ```json fenced, and JSON wrapped
    in a preamble — and return None for genuinely unparseable input."""

    def test_parses_raw_json(self):
        from main import _parse_rubric_review
        raw = '{"sections": {"methodology": {"verdict": "pass"}}}'
        result = _parse_rubric_review(raw)
        assert result is not None
        assert result["sections"]["methodology"]["verdict"] == "pass"

    def test_strips_markdown_fence(self):
        """Gemini sometimes wraps the JSON in ```json fences despite
        the no-fence instruction in the prompt."""
        from main import _parse_rubric_review
        raw = '```json\n{"overall": {"verdict": "ready"}}\n```'
        result = _parse_rubric_review(raw)
        assert result is not None
        assert result["overall"]["verdict"] == "ready"

    def test_extracts_json_from_preamble(self):
        """A preamble like 'Here is the review:' before the JSON
        is stripped — the parser anchors on the outer {...} block."""
        from main import _parse_rubric_review
        raw = ('Here is the review:\n\n'
               '{"overall": {"verdict": "needs_work"}}\n')
        result = _parse_rubric_review(raw)
        assert result is not None
        assert result["overall"]["verdict"] == "needs_work"

    def test_returns_none_on_unparseable_input(self):
        from main import _parse_rubric_review
        assert _parse_rubric_review("not JSON, no braces at all") is None
        assert _parse_rubric_review("") is None

    def test_returns_none_on_malformed_braces(self):
        from main import _parse_rubric_review
        # Looks like JSON but isn't — closing brace missing fields.
        assert _parse_rubric_review('{"sections":') is None


# ── Endpoint contract — auth + test-env stub ──────────────────────────────────


class TestRubricReviewAuthGating:
    """The endpoint requires team_member. Viewer accounts and missing
    auth get 401 or 403 — never a glimpse of the rubric or the draft."""

    def test_rejects_unauthenticated(self):
        # No auth header — fail-fast at the require_team_member gate.
        r = client.post("/api/v1/documents/drafts/1/rubric-review")
        assert r.status_code in (401, 403)

    def test_rejects_viewer(self):
        # A viewer account (panttserk@queens.edu — not in
        # PROJECT_TEAM_EMAILS) gets 403 from require_team_member.
        r = client.post("/api/v1/documents/drafts/1/rubric-review",
                        headers=VIEWER)
        assert r.status_code in (401, 403)


class TestRubricReviewEndpointShape:
    """When the draft exists AND ENVIRONMENT==test, the endpoint
    returns the deterministic stub. The stub mirrors the structured
    shape a real Gemini response would produce — pinning it prevents
    the frontend from drifting against the contract."""

    @pytest.mark.skipif(not _db_ready(),
                        reason="endpoint contract test needs a live DB")
    def test_returns_structured_response_on_a_midpoint_draft(
            self, clean_editor_drafts):
        # Create a midpoint draft owned by Bob, then call the endpoint.
        async def _setup() -> int:
            from tools.editor_drafts import create_draft
            draft = await create_draft(
                "midpoint_paper", "thaob@queens.edu",
                "Test Midpoint",
                {"type": "doc", "content": []},
                "This is the draft body. " * 30,
                created_from="manual",
            )
            assert draft is not None
            return draft["id"]

        draft_id = _run(_setup())
        r = client.post(
            f"/api/v1/documents/drafts/{draft_id}/rubric-review",
            headers=TEAM)
        assert r.status_code == 200
        body = r.json()
        # Sections — all four required.
        assert set(body["sections"].keys()) >= {
            "methodology", "results", "roles", "next_steps"}
        for key in ("methodology", "results", "roles", "next_steps"):
            assert body["sections"][key]["verdict"] in ("pass", "fail")
            assert isinstance(body["sections"][key]["reasoning"], str)
        # Overall verdict — one of the three canonical values.
        assert body["overall"]["verdict"] in (
            "ready", "needs_work", "not_ready")
        # Edits — a list, possibly empty.
        assert isinstance(body["edits"], list)

    @pytest.mark.skipif(not _db_ready(),
                        reason="endpoint contract test needs a live DB")
    def test_rejects_non_midpoint_document_types(
            self, clean_editor_drafts):
        """The rubric is midpoint-specific; running it on an
        executive_brief or deck would mislead. The endpoint 422s
        rather than blindly applying the wrong rubric."""
        async def _setup() -> int:
            from tools.editor_drafts import create_draft
            draft = await create_draft(
                "executive_brief", "thaob@queens.edu",
                "Brief", {"type": "doc", "content": []},
                "Brief body.", created_from="manual",
            )
            assert draft is not None
            return draft["id"]

        draft_id = _run(_setup())
        r = client.post(
            f"/api/v1/documents/drafts/{draft_id}/rubric-review",
            headers=TEAM)
        assert r.status_code == 422
        assert "midpoint" in r.json()["detail"].lower()

    @pytest.mark.skipif(not _db_ready(),
                        reason="endpoint contract test needs a live DB")
    def test_rejects_other_users_drafts(self, clean_editor_drafts):
        """A draft Bob does not own is a 404 — not a 403 — so the
        endpoint never confirms another user's draft exists."""
        async def _setup() -> int:
            from tools.editor_drafts import create_draft
            draft = await create_draft(
                "midpoint_paper", "murdockm@queens.edu",  # Molly's
                "Molly's Midpoint", {"type": "doc", "content": []},
                "Molly's body.", created_from="manual",
            )
            assert draft is not None
            return draft["id"]

        draft_id = _run(_setup())
        # Bob (TEAM) tries to read Molly's draft — 404, not 403.
        r = client.post(
            f"/api/v1/documents/drafts/{draft_id}/rubric-review",
            headers=TEAM)
        assert r.status_code == 404


class TestUnavailablePayload:
    """The _rubric_review_unavailable_payload helper is the
    consistent fall-through for every failure path (no key, parse
    failure, transient Gemini error). The frontend reads `unavailable`
    to render the warning banner cleanly."""

    def test_unavailable_payload_has_consistent_shape(self):
        from main import _rubric_review_unavailable_payload
        result = _rubric_review_unavailable_payload("Test reason here.")
        assert result["unavailable"] is True
        assert set(result["sections"].keys()) == {
            "methodology", "results", "roles", "next_steps"}
        for key in result["sections"]:
            assert result["sections"][key]["verdict"] == "fail"
            assert "Test reason here." in result["sections"][key]["reasoning"]
        assert result["overall"]["verdict"] == "not_ready"
        assert "Test reason here." in result["overall"]["reasoning"]
        # Edits list present (empty) so the UI doesn't crash on a
        # missing key.
        assert result["edits"] == []
