"""
tests/test_script_writer.py

Tests for the presentation script writer:
  - script generation (tools/script_generation) — the deterministic
    fallback assembles a complete 16-slide script in the test
    environment, so coverage and speaker-carry-through are checked
    without an LLM
  - the markdown → TipTap parser and the prompt builder
  - the master / per-speaker DOCX export (tools/script_docx)
  - the /api/v1/documents/script/generate and
    /api/v1/documents/drafts/{id}/export endpoint contracts

The CRUD round-trips need a live database and skip cleanly without one.
"""
from __future__ import annotations

import asyncio
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
from tools.script_generation import (  # noqa: E402
    build_script_prompt, deck_speakers, generate_script, script_to_tiptap,
)
from tools.script_docx import build_script_docx, script_speakers  # noqa: E402

client = TestClient(app)
TEAM = {"X-API-Key": generate_session_token("thaob@queens.edu")}
VIEWER = {"X-API-Key": generate_session_token("panttserk@queens.edu")}
DRAFTS = "/api/v1/documents/drafts"
GENERATE = "/api/v1/documents/script/generate"


def _deck_draft(speakers: str = "alt") -> dict:
    """A 16-slide presentation_deck draft. speakers='alt' alternates
    Molly/Bob; 'none' leaves every slide unassigned."""
    slides = []
    for i in range(1, 17):
        sp = None if speakers == "none" else (
            "Molly" if i % 2 else "Bob")
        slides.append({
            "id": i, "title": f"Slide {i}", "background": "#FFFFFF",
            "speaker": sp, "speaker_notes": f"notes {i}",
            "elements": [{"id": f"e{i}", "type": "text",
                          "content": f"body text {i}"}],
        })
    return {"document_type": "presentation_deck", "title": "Deck",
            "content_json": {"slides": slides}}


def _node_text(node: dict) -> str:
    if node.get("text"):
        return str(node["text"])
    return "".join(_node_text(c) for c in (node.get("content") or []))


def _docx_text(blob: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(blob))
    return "\n".join(p.text for p in doc.paragraphs)


def _db_ready() -> bool:
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return False

        async def _probe() -> bool:
            async with AsyncSessionLocal() as s:
                await s.execute(text("SELECT 1 FROM editor_drafts LIMIT 1"))
            return True

        return asyncio.run(_probe())
    except Exception:
        return False


# ── Script generation ─────────────────────────────────────────────────────────

class TestScriptGeneration:
    def test_generated_script_covers_every_slide(self):
        result = generate_script(_deck_draft(), None, None)
        assert result["slide_count"] == 16
        h2 = [n for n in result["content_json"]["content"]
              if n["type"] == "heading" and n["attrs"]["level"] == 2]
        slide_headers = [n for n in h2 if "Slide " in _node_text(n)]
        assert len(slide_headers) == 16

    def test_speaker_assignments_carry_into_the_script(self):
        result = generate_script(_deck_draft(), None, None)
        h3 = [_node_text(n) for n in result["content_json"]["content"]
              if n["type"] == "heading" and n["attrs"]["level"] == 3]
        assert len(h3) == 16
        assert any("Molly" in t for t in h3)
        assert any("Bob" in t for t in h3)
        assert result["speaker_count"] == 2

    def test_generation_handles_absent_academic_context(self):
        # No executive brief and no midpoint draft — generation still
        # produces a complete script.
        result = generate_script(_deck_draft(), None, None)
        assert result["slide_count"] == 16
        assert result["word_count"] > 0

    def test_prompt_includes_the_executive_brief_when_present(self):
        slides = _deck_draft()["content_json"]["slides"]
        prompt = build_script_prompt(
            slides, "EXECBRIEFMARKER — the 2022 correlation break.", None)
        assert "EXECBRIEFMARKER" in prompt

    def test_prompt_marks_academic_context_not_available_when_absent(self):
        slides = _deck_draft()["content_json"]["slides"]
        prompt = build_script_prompt(slides, None, None)
        assert "Not available" in prompt

    def test_script_to_tiptap_classifies_every_node_kind(self):
        raw = ("OPENING\n\nScene-setter.\n\n"
               "## Slide 1: Opening\n\n**Speaker: Molly**\n\n"
               "Delivery paragraph.\n\n*Transition: now to slide 2*")
        content_json, _ = script_to_tiptap(raw)
        kinds = [(n["type"], (n.get("attrs") or {}).get("level"))
                 for n in content_json["content"]]
        assert ("heading", 2) in kinds      # ## Slide and OPENING
        assert ("heading", 3) in kinds      # **Speaker: …**
        assert ("blockquote", None) in kinds  # *Transition: …*
        assert ("paragraph", None) in kinds

    def test_deck_speakers_is_unique_and_first_seen(self):
        assert deck_speakers(
            _deck_draft()["content_json"]["slides"]) == ["Molly", "Bob"]
        assert deck_speakers(
            _deck_draft("none")["content_json"]["slides"]) == []


# ── DOCX export ───────────────────────────────────────────────────────────────

class TestScriptDocx:
    def _script_draft(self) -> dict:
        result = generate_script(_deck_draft(), None, None)
        return {"document_type": "presentation_script",
                "title": "Presentation Script",
                "content_json": result["content_json"]}

    def test_master_docx_contains_every_slide(self):
        blob = build_script_docx(self._script_draft(), None)
        assert blob[:2] == b"PK"
        text = _docx_text(blob)
        for i in range(1, 17):
            assert f"Slide {i}:" in text

    def test_per_speaker_docx_contains_only_that_speaker(self):
        draft = self._script_draft()
        molly = _docx_text(build_script_docx(draft, "Molly"))
        # Odd slides are Molly's, even slides are Bob's.
        assert "Slide 1:" in molly
        assert "Slide 2:" not in molly
        assert "SPEAKER: Molly" in molly
        assert "SPEAKER: Bob" not in molly

    def test_per_speaker_docx_includes_slide_numbers_and_titles(self):
        molly = _docx_text(build_script_docx(self._script_draft(), "Molly"))
        # A later Molly slide — numbered and titled so she can follow along.
        assert "Slide 3:" in molly

    def test_script_speakers_lists_unique_names(self):
        assert script_speakers(
            self._script_draft()["content_json"]) == ["Molly", "Bob"]


# ── Shared speaker-colour utility ─────────────────────────────────────────────

class TestSpeakerColourUtility:
    """The script editor (frontend) and the DOCX builder (backend) must
    agree on which colour belongs to which speaker. Both consume the
    same palette and resolver — tools/speaker_colours.py here, mirrored
    in frontend/src/lib/speakerColours.ts on the frontend."""

    def test_palette_matches_team_colour_spec(self):
        from tools.speaker_colours import SPEAKER_COLOURS
        # Five distinct colours per FIX 1: navy / amber / green /
        # purple / red — must mirror the frontend palette EXACTLY.
        assert SPEAKER_COLOURS == (
            "#1B2A4A", "#B45309", "#059669", "#7C3AED", "#DC2626",
        )

    def test_first_seen_speaker_gets_navy(self):
        from tools.speaker_colours import get_speaker_colour
        assert get_speaker_colour(
            "Molly", ["Molly", "Bob", "Michael"]) == "#1B2A4A"

    def test_per_speaker_assignment_is_stable_across_indices(self):
        from tools.speaker_colours import get_speaker_colour
        order = ["Molly", "Bob", "Michael"]
        assert get_speaker_colour("Molly", order) == "#1B2A4A"
        assert get_speaker_colour("Bob", order) == "#B45309"
        assert get_speaker_colour("Michael", order) == "#059669"

    def test_sixth_speaker_cycles_back_to_navy(self):
        from tools.speaker_colours import get_speaker_colour
        order = ["A", "B", "C", "D", "E", "F"]
        # Index 5 % 5 == 0 → first colour again.
        assert get_speaker_colour("F", order) == "#1B2A4A"

    def test_unknown_speaker_falls_back_to_first_colour(self):
        # Defensive: a name not in the list maps to Speaker 1 rather
        # than throwing. Real callers always pass the speaker's own
        # list, but a typo must not crash the export.
        from tools.speaker_colours import get_speaker_colour
        assert get_speaker_colour(
            "Unknown", ["Molly", "Bob"]) == "#1B2A4A"


# ── Endpoint contracts ────────────────────────────────────────────────────────

class TestScriptEndpoints:
    """June 25 2026 -- POST /api/v1/documents/script/generate
    refactored to the async job pattern. Returns 202 + job_id
    instead of inline 200 + draft_id. The body is no longer read --
    the endpoint reads the team-shared current deck draft via
    get_current_draft_by_type. Missing-deck and missing-speakers
    conditions now surface inside the job (status='failed') rather
    than as synchronous HTTPException. Tests below pin the new
    async contract; the in-job error coverage is left to the
    document_generation tests that already exercise _generate_async."""

    def test_generate_unauthenticated_is_401(self):
        assert client.post(GENERATE).status_code == 401

    def test_generate_rejects_a_viewer(self):
        assert client.post(GENERATE, headers=VIEWER).status_code == 403

    def test_generate_team_member_returns_202_job_id(
            self, clean_editor_drafts):
        # The endpoint dispatches a background job and returns
        # immediately. job_id flows back so the frontend can poll
        # /api/v1/jobs/<id> for completion. Body is ignored under
        # the async contract.
        resp = client.post(GENERATE, headers=TEAM, json={})
        assert resp.status_code == 202
        body = resp.json()
        assert "job_id" in body
        assert body.get("status") == "pending"

    def test_generate_ignores_legacy_draft_id_in_body(self):
        # Legacy clients passing {draft_id: ...} continue to get
        # a 202 -- the value is silently dropped.
        resp = client.post(GENERATE, headers=TEAM,
                           json={"draft_id": 999999999})
        assert resp.status_code == 202

    def test_export_unauthenticated_is_401(self):
        assert client.post(f"{DRAFTS}/1/export").status_code == 401

    def test_export_rejects_a_viewer(self):
        assert client.post(f"{DRAFTS}/1/export",
                           headers=VIEWER).status_code == 403

    def test_export_unknown_draft_is_404(self):
        assert client.post(f"{DRAFTS}/999999999/export",
                           headers=TEAM).status_code == 404
