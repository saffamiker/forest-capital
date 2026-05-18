"""
tests/test_editor_drafts.py

Tests for the in-platform document editor — the editor_drafts /
editor_draft_versions data layer (migration 021), the
/api/v1/documents/drafts endpoints, the draft created on document
generation, and Academic Review reading the editor draft.

Endpoint-contract tests (auth gating) run everywhere. The CRUD
round-trips need a live database and skip cleanly without one — the
same pattern as the rest of the suite. Every DB test uses the
clean_editor_drafts fixture so it leaves no rows behind.
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
TEAM = {"X-API-Key": generate_session_token("thaob@queens.edu")}     # team member
VIEWER = {"X-API-Key": generate_session_token("panttserk@queens.edu")}  # viewer
DRAFTS = "/api/v1/documents/drafts"


def _run(coro):
    return asyncio.run(coro)


_db_ready_cache: bool | None = None


def _db_ready() -> bool:
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


# ── Endpoint gating ───────────────────────────────────────────────────────────

class TestEditorEndpointGating:
    def test_list_drafts_requires_auth(self):
        assert client.get(DRAFTS).status_code == 401

    def test_list_drafts_rejects_a_viewer(self):
        # A viewer has no team_member permission.
        assert client.get(DRAFTS, headers=VIEWER).status_code == 403

    def test_list_drafts_admits_a_team_member(self):
        resp = client.get(DRAFTS, headers=TEAM)
        assert resp.status_code == 200
        assert "drafts" in resp.json()

    def test_create_rejects_a_bad_document_type(self):
        resp = client.post(DRAFTS, headers=TEAM,
                            json={"document_type": "novel", "title": "x"})
        assert resp.status_code == 422


# ── Marker helpers — pure logic ───────────────────────────────────────────────

class TestEditorContentBuilders:
    def test_midpoint_to_editor_builds_tiptap_and_text(self):
        from tools.editor_content import midpoint_to_editor
        cj, ct = midpoint_to_editor({
            "methodology": "Method para.", "results": "Results para.",
            "roles": "Roles para.", "next_steps": "Next steps para."})
        assert cj["type"] == "doc"
        # Four H1 section headings.
        headings = [n for n in cj["content"] if n.get("type") == "heading"]
        assert len(headings) == 4
        # The Roles and Next Steps [[BOB]] callouts are embedded.
        assert ct.count("[[BOB:") == 2

    def test_deck_to_editor_builds_sixteen_slides(self):
        from tools.editor_content import deck_to_editor
        cj, ct = deck_to_editor({"conclusions": "- one", "thesis": "A thesis."})
        assert len(cj["slides"]) == 16
        assert all("speaker_notes" in s for s in cj["slides"])
        # Speaker notes start empty — Molly writes her own.
        assert all(s["speaker_notes"] == "" for s in cj["slides"])
        assert "Slide 1:" in ct


# ── CRUD round-trips — skip without a live database ───────────────────────────

class TestEditorCRUD:
    def test_create_get_patch_version_restore_delete(self, clean_editor_drafts):
        if not _db_ready():
            pytest.skip("no live database with the migration-021 tables")

        # Create — word count is computed from content_text.
        created = client.post(DRAFTS, headers=TEAM, json={
            "document_type": "midpoint_paper", "title": "Round-trip",
            "content_json": {"type": "doc", "content": []},
            "content_text": "one two three"})
        assert created.status_code == 201
        draft = created.json()
        did = draft["id"]
        assert draft["word_count"] == 3
        assert draft["version"] == 1

        # Get.
        got = client.get(f"{DRAFTS}/{did}", headers=TEAM)
        assert got.status_code == 200 and got.json()["id"] == did

        # Auto-save (PATCH) — updates content, word count updates on edit,
        # and does NOT create a version.
        patched = client.patch(f"{DRAFTS}/{did}", headers=TEAM, json={
            "content_text": "one two three four five"})
        assert patched.status_code == 200
        after = client.get(f"{DRAFTS}/{did}", headers=TEAM).json()
        assert after["word_count"] == 5
        assert after["version"] == 1   # auto-save creates no version
        assert client.get(f"{DRAFTS}/{did}/versions",
                           headers=TEAM).json()["versions"] == []

        # Save a named version checkpoint.
        ver = client.post(f"{DRAFTS}/{did}/versions", headers=TEAM,
                          json={"version_label": "checkpoint one"})
        assert ver.status_code == 201
        version_id = ver.json()["id"]
        assert ver.json()["version_label"] == "checkpoint one"
        versions = client.get(f"{DRAFTS}/{did}/versions",
                              headers=TEAM).json()["versions"]
        assert len(versions) == 1

        # Edit again, then restore the saved version.
        client.patch(f"{DRAFTS}/{did}", headers=TEAM,
                     json={"content_text": "completely different text now"})
        restored = client.post(f"{DRAFTS}/{did}/restore/{version_id}",
                                headers=TEAM)
        assert restored.status_code == 200
        assert restored.json()["content_text"] == "one two three four five"

        # Soft delete — the draft then 404s.
        assert client.delete(f"{DRAFTS}/{did}",
                             headers=TEAM).status_code == 200
        assert client.get(f"{DRAFTS}/{did}", headers=TEAM).status_code == 404

    def test_create_sets_is_current_and_unsets_siblings(self, clean_editor_drafts):
        if not _db_ready():
            pytest.skip("no live database")
        first = client.post(DRAFTS, headers=TEAM, json={
            "document_type": "executive_brief", "title": "First",
            "content_text": "first"}).json()
        second = client.post(DRAFTS, headers=TEAM, json={
            "document_type": "executive_brief", "title": "Second",
            "content_text": "second"}).json()
        # The newest draft of a type is current; the previous one is not.
        drafts = {d["id"]: d for d in
                  client.get(DRAFTS, headers=TEAM).json()["drafts"]}
        assert drafts[second["id"]]["is_current"] is True
        assert drafts[first["id"]]["is_current"] is False


class TestDraftOnGeneration:
    def test_midpoint_generation_creates_a_draft(self, clean_editor_drafts):
        if not _db_ready():
            pytest.skip("no live database")
        resp = client.post("/api/v1/export/midpoint-paper", headers=TEAM)
        assert resp.status_code == 200
        # The generated content is loaded into an editor draft; its id
        # rides back in the X-Draft-Id header.
        draft_id = resp.headers.get("x-draft-id")
        assert draft_id is not None
        got = client.get(f"{DRAFTS}/{draft_id}", headers=TEAM)
        assert got.status_code == 200
        assert got.json()["created_from"] == "generated"


class TestAcademicReviewReadsDraft:
    def test_review_context_overlays_the_editor_draft(self, clean_editor_drafts):
        if not _db_ready():
            pytest.skip("no live database")
        from agents.academic_review import gather_review_context
        from tools.editor_drafts import create_draft
        from database import engine

        async def _scenario():
            if engine is not None:
                await engine.dispose()
            await create_draft(
                "midpoint_paper", "thaob@queens.edu", "Review draft",
                {"type": "doc", "content": []},
                "DRAFT-MARKER unique editor content for review",
                created_from="generated")
            return await gather_review_context(
                reviewer_email="thaob@queens.edu")

        ctx = _run(_scenario())
        midpoint = ctx["documents_by_type"].get("midpoint_draft", [])
        # The editor draft stands in for the uploaded midpoint document.
        assert any("DRAFT-MARKER" in (d.get("content_text") or "")
                   for d in midpoint)
