"""
tests/test_draft_academic_review_status.py — May 25 2026.

Pins GET /api/v1/documents/drafts/{id}/academic-review-status — the
endpoint the editor reads on draft load to render the header score
pill and the midpoint advisory banner.

Two surfaces:
  1. The activity_log query (get_latest_academic_review_for_draft) —
     finds the most recent academic_review row whose metadata.draft_id
     matches. Tested against a live DB when available, skipped
     otherwise.
  2. The endpoint itself — auth gating, ownership 404, and the empty/
     populated response shapes. Tested with the live DB and the
     activity_log helper mocked, so the contract is checkable
     regardless of whether the DB is up.
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
BOB = {"X-API-Key": generate_session_token("thaob@queens.edu")}
MOLLY = {"X-API-Key": generate_session_token("murdockm@queens.edu")}
VIEWER = {"X-API-Key": generate_session_token("panttserk@queens.edu")}


def _run(coro):
    return asyncio.run(coro)


_db_ready_cache: bool | None = None


def _db_ready() -> bool:
    """Mirrors the editor-drafts probe — DB-gated tests use this."""
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


# ── Auth + ownership contract ────────────────────────────────────────────────


class TestAuthGating:
    """The endpoint requires team_member; viewers and unauthenticated
    requests get 401 / 403 — never a glimpse of someone else's draft."""

    def test_rejects_unauthenticated(self):
        r = client.get(
            "/api/v1/documents/drafts/1/academic-review-status")
        assert r.status_code in (401, 403)

    def test_rejects_viewer(self):
        r = client.get(
            "/api/v1/documents/drafts/1/academic-review-status",
            headers=VIEWER)
        assert r.status_code in (401, 403)


# ── Endpoint shape — populated + empty + foreign-draft 404 ───────────────────


class TestEndpointShape:
    """Endpoint contract pinned with a live DB. We create an editor
    draft owned by Bob, insert a fake academic_review row tagged with
    metadata.draft_id, then assert the endpoint stitches them together."""

    @pytest.mark.skipif(not _db_ready(),
                        reason="endpoint contract test needs a live DB")
    def test_returns_missing_when_no_review_exists(
        self, clean_editor_drafts,
    ):
        from tools.editor_drafts import create_draft

        async def _setup() -> int:
            draft = await create_draft(
                "midpoint_paper", "thaob@queens.edu",
                "Bob's midpoint",
                {"type": "doc", "content": []},
                "Body of the paper.", created_from="manual",
            )
            assert draft is not None
            return draft["id"]

        draft_id = _run(_setup())
        r = client.get(
            f"/api/v1/documents/drafts/{draft_id}/academic-review-status",
            headers=BOB)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "missing"
        assert body["score"] is None
        assert body["rating"] is None
        assert body["advisory"] is False
        assert body["document_type"] == "midpoint_paper"
        # The threshold is a stable contract — the frontend reads it
        # to anchor the banner copy ("score below X.X").
        assert body["threshold"] == 6.0
        assert body["section_ratings"] == {}

    @pytest.mark.skipif(not _db_ready(),
                        reason="endpoint contract test needs a live DB")
    def test_returns_score_when_auto_review_present(
        self, clean_editor_drafts,
    ):
        """An academic_review row whose metadata.draft_id matches is
        surfaced. The score parses out, the rating surfaces, advisory
        fires below 6.0."""
        import json

        from sqlalchemy import text

        from database import AsyncSessionLocal
        from tools.editor_drafts import create_draft

        async def _setup() -> int:
            draft = await create_draft(
                "midpoint_paper", "thaob@queens.edu",
                "Bob's midpoint",
                {"type": "doc", "content": []},
                "Body of the paper.", created_from="manual",
            )
            assert draft is not None
            d_id = draft["id"]
            metadata = {
                "draft_id": d_id,
                "document_type": "midpoint_paper",
                "automatic": True,
                "advisory": True,
                "score": 5.5,
                "overall_rating": "Needs Work",
                "section_ratings": {
                    "data_sufficiency": "Strong",
                    "requirements": "Developing",
                    "deliverable": "Needs Work",
                    "investigation": "Needs Work",
                    "readiness": "Needs Work",
                },
                "sections_rated": 5,
            }
            async with AsyncSessionLocal() as s:  # type: ignore[union-attr]
                await s.execute(text(
                    "INSERT INTO agent_interactions "
                    "(user_email, session_id, session_type, "
                    " interaction_type, agents_involved, "
                    " response_summary, metadata) "
                    "VALUES (:e, '', 'analytical', 'academic_review', "
                    " CAST(:a AS JSONB), 'auto-fired review', "
                    " CAST(:m AS JSONB))"),
                    {"e": "thaob@queens.edu",
                     "a": json.dumps(["academic_advisor"]),
                     "m": json.dumps(metadata)})
                await s.commit()
            return d_id

        draft_id = _run(_setup())
        r = client.get(
            f"/api/v1/documents/drafts/{draft_id}/academic-review-status",
            headers=BOB)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "complete"
        assert body["score"] == 5.5
        assert body["rating"] == "Needs Work"
        # Midpoint + score < 6.0 → advisory true.
        assert body["advisory"] is True
        assert body["section_ratings"]["data_sufficiency"] == "Strong"
        assert body["document_type"] == "midpoint_paper"
        assert body["run_at"] is not None

    @pytest.mark.skipif(not _db_ready(),
                        reason="endpoint contract test needs a live DB")
    def test_advisory_false_for_executive_brief_below_threshold(
        self, clean_editor_drafts,
    ):
        """Advisory is midpoint-only — an executive brief with the
        same low score is NOT advisory because the exec brief uses
        the hard gate, not the in-editor advisory banner."""
        import json

        from sqlalchemy import text

        from database import AsyncSessionLocal
        from tools.editor_drafts import create_draft

        async def _setup() -> int:
            draft = await create_draft(
                "executive_brief", "thaob@queens.edu",
                "Bob's brief",
                {"type": "doc", "content": []},
                "Body.", created_from="manual",
            )
            assert draft is not None
            d_id = draft["id"]
            async with AsyncSessionLocal() as s:  # type: ignore[union-attr]
                await s.execute(text(
                    "INSERT INTO agent_interactions "
                    "(user_email, session_id, session_type, "
                    " interaction_type, agents_involved, "
                    " response_summary, metadata) "
                    "VALUES (:e, '', 'analytical', 'academic_review', "
                    " CAST(:a AS JSONB), 'auto-fired review', "
                    " CAST(:m AS JSONB))"),
                    {"e": "thaob@queens.edu",
                     "a": json.dumps(["academic_advisor"]),
                     "m": json.dumps({
                         "draft_id": d_id,
                         "document_type": "executive_brief",
                         "score": 5.5, "overall_rating": "Needs Work"})})
                await s.commit()
            return d_id

        draft_id = _run(_setup())
        r = client.get(
            f"/api/v1/documents/drafts/{draft_id}/academic-review-status",
            headers=BOB)
        assert r.status_code == 200
        body = r.json()
        # Score surfaces, but advisory is False — the exec brief gate
        # is the surface that blocks; the in-editor banner does not
        # fire for it.
        assert body["score"] == 5.5
        assert body["document_type"] == "executive_brief"
        assert body["advisory"] is False

    @pytest.mark.skipif(not _db_ready(),
                        reason="endpoint contract test needs a live DB")
    def test_team_members_see_other_users_drafts(
        self, clean_editor_drafts,
    ):
        """June 25 2026 -- documents are team-shared (PR #399 removed
        owner-scoping from the canonical drafts endpoints; PR #406
        removed it here too). Bob now sees the status of Molly's
        draft because the team needs to be able to review each
        other's drafts in the editor regardless of who generated
        them. Viewers (Dr. Panttser) still get 403 via require_team_
        member -- that gate is the authoritative access boundary.
        Previously the endpoint owner-scoped and returned 404 to
        non-owners."""
        from tools.editor_drafts import create_draft

        async def _setup() -> int:
            draft = await create_draft(
                "midpoint_paper", "murdockm@queens.edu",  # Molly's
                "Molly's midpoint",
                {"type": "doc", "content": []},
                "Body.", created_from="manual",
            )
            assert draft is not None
            return draft["id"]

        draft_id = _run(_setup())
        r = client.get(
            f"/api/v1/documents/drafts/{draft_id}/academic-review-status",
            headers=BOB)
        assert r.status_code == 200
        body = r.json()
        assert body["draft_id"] == draft_id
        assert body["document_type"] == "midpoint_paper"

    @pytest.mark.skipif(not _db_ready(),
                        reason="endpoint contract test needs a live DB")
    def test_returns_empty_shape_for_non_review_doctypes(
        self, clean_editor_drafts,
    ):
        """A presentation deck draft has no auto-fired review (the
        deck path doesn't schedule one). The endpoint returns the
        empty shape with the deck's document_type so the frontend
        knows to hide the indicator entirely."""
        from tools.editor_drafts import create_draft

        async def _setup() -> int:
            draft = await create_draft(
                "presentation_deck", "thaob@queens.edu",
                "Bob's deck",
                {"slides": []},
                "[deck content]", created_from="manual",
            )
            assert draft is not None
            return draft["id"]

        draft_id = _run(_setup())
        r = client.get(
            f"/api/v1/documents/drafts/{draft_id}/academic-review-status",
            headers=BOB)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "missing"
        assert body["document_type"] == "presentation_deck"
        assert body["advisory"] is False
