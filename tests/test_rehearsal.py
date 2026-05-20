"""
tests/test_rehearsal.py

Tests for the presentation rehearsal mode backend:
  - tools/rehearsal.parse_script_sections — TipTap → per-slide section
    parser (the data the rehearsal overlay's left panel renders)
  - GET /api/v1/documents/rehearsal endpoint contract — auth, the
    deck-and-script payload shape, the 150-wpm minutes estimate, and
    the two 404 paths (deck missing / script missing)

The parser tests are pure-Python and run everywhere. The endpoint
tests use monkey-patching to inject fake editor_drafts, so they pass
without a database.
"""
from __future__ import annotations

import os
import sys

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
from tools.rehearsal import parse_script_sections  # noqa: E402

client = TestClient(app)
ENDPOINT = "/api/v1/documents/rehearsal"
TEAM = {"X-API-Key": generate_session_token("thaob@queens.edu")}
VIEWER = {"X-API-Key": generate_session_token("panttserk@queens.edu")}


# ── parse_script_sections ─────────────────────────────────────────────────────

def _h(level: int, text: str) -> dict:
    return {"type": "heading", "attrs": {"level": level},
            "content": [{"type": "text", "text": text}]}


def _p(text: str) -> dict:
    return {"type": "paragraph",
            "content": [{"type": "text", "text": text}]}


def _bq(text: str) -> dict:
    return {"type": "blockquote",
            "content": [_p(text)]}


def _doc(*nodes) -> dict:
    return {"type": "doc", "content": list(nodes)}


class TestParseScriptSections:
    def test_h2_h3_paragraph_blockquote_round_trip(self):
        doc = _doc(
            _h(2, "Slide 1: Opening"),
            _h(3, "Speaker: Molly"),
            _p("Good evening. The question we set out to answer..."),
            _bq("Transition: Let me show you what we built."),
            _h(2, "Slide 2: Findings"),
            _h(3, "Speaker: Bob"),
            _p("The 2022 regime break is the central finding."),
            _p("Two strategies show meaningful post-2022 outperformance."),
        )
        sections = parse_script_sections(doc)
        assert len(sections) == 2

        s1 = sections[0]
        assert s1["slide_number"] == 1
        assert s1["title"] == "Opening"
        assert s1["speaker"] == "Molly"
        assert "set out to answer" in s1["script_text"]
        assert s1["transition"] == "Let me show you what we built."
        assert s1["word_count"] > 0

        s2 = sections[1]
        assert s2["slide_number"] == 2
        assert s2["title"] == "Findings"
        assert s2["speaker"] == "Bob"
        # Two paragraphs joined into the script_text.
        assert "regime break" in s2["script_text"]
        assert "post-2022 outperformance" in s2["script_text"]

    def test_word_count_drives_the_delivery_estimate(self):
        # 150 words of prose ⇒ word_count = 150 ⇒ ~1 minute at 150 wpm.
        prose = " ".join(["word"] * 150)
        sections = parse_script_sections(_doc(
            _h(2, "Slide 1: Test"),
            _h(3, "Speaker: Alice"),
            _p(prose),
        ))
        assert sections[0]["word_count"] == 150

    def test_h2_with_no_slide_pattern_is_treated_as_body(self):
        # An H2 that does not match "Slide N: ..." attaches to the
        # current section as body content — never starts a new section.
        sections = parse_script_sections(_doc(
            _h(2, "Slide 1: Opening"),
            _h(2, "Sub-heading inside the section"),  # body, not a new slide
            _p("First paragraph."),
        ))
        assert len(sections) == 1
        assert sections[0]["slide_number"] == 1

    def test_no_slide_headings_fall_back_to_a_single_section(self):
        # A draft without any "Slide N:" headings is malformed; the
        # rehearsal still renders — everything goes into one section.
        sections = parse_script_sections(_doc(
            _p("Some prose."),
            _p("More prose."),
        ))
        assert len(sections) == 1
        assert sections[0]["slide_number"] is None
        assert "Some prose." in sections[0]["script_text"]
        assert "More prose." in sections[0]["script_text"]

    def test_empty_or_malformed_doc_returns_empty_list(self):
        assert parse_script_sections({}) == []
        assert parse_script_sections({"type": "doc"}) == []
        assert parse_script_sections({"type": "doc", "content": "garbage"}) == []
        assert parse_script_sections(None) == []
        assert parse_script_sections("not a dict") == []

    def test_blockquote_without_transition_prefix_is_body(self):
        sections = parse_script_sections(_doc(
            _h(2, "Slide 1: Test"),
            _bq("Just a quoted line, not a transition."),
        ))
        assert sections[0]["transition"] == ""
        assert "Just a quoted line" in sections[0]["script_text"]


# ── /api/v1/documents/rehearsal endpoint ──────────────────────────────────────

def _deck_draft() -> dict:
    return {
        "id": 1, "document_type": "presentation_deck",
        "owner_email": "thaob@queens.edu", "title": "Deck",
        "content_json": {
            "slides": [{
                "id": 1, "title": "Opening", "background": "#FFFFFF",
                "speaker_notes": "Hold for applause.",
                "speaker": "Molly",
                "elements": [{
                    "id": "el_001", "type": "text", "x": 50, "y": 60,
                    "width": 800, "height": 100, "content": "Opening",
                    "fontSize": 48, "fontWeight": "bold",
                    "fontStyle": "normal", "color": "#1A1A2E",
                }],
            }],
        },
        "content_text": "", "word_count": 0, "version": 1,
        "is_current": True, "is_deleted": False,
        "created_from": "generated", "created_at": None, "updated_at": None,
    }


def _script_draft() -> dict:
    return {
        "id": 2, "document_type": "presentation_script",
        "owner_email": "thaob@queens.edu", "title": "Script",
        "content_json": _doc(
            _h(2, "Slide 1: Opening"),
            _h(3, "Speaker: Molly"),
            _p("Good evening. The question we set out to answer..."),
        ),
        "content_text": "", "word_count": 12, "version": 1,
        "is_current": True, "is_deleted": False,
        "created_from": "generated", "created_at": None, "updated_at": None,
    }


class TestRehearsalEndpoint:
    def test_requires_auth(self):
        assert client.get(ENDPOINT).status_code == 401

    def test_rejects_a_viewer(self):
        # team_member-gated — a viewer is 403.
        assert client.get(ENDPOINT, headers=VIEWER).status_code == 403

    def test_returns_deck_and_script(self, monkeypatch):
        # Patch the data layer so both drafts are present — the
        # successful path returns {deck, script}.
        from tools import editor_drafts

        async def _get(doc_type: str, _email: str):
            if doc_type == "presentation_deck":
                return _deck_draft()
            if doc_type == "presentation_script":
                return _script_draft()
            return None

        monkeypatch.setattr(editor_drafts, "get_current_draft", _get)
        resp = client.get(ENDPOINT, headers=TEAM)
        assert resp.status_code == 200
        body = resp.json()
        # Top-level shape.
        assert set(body) == {"deck", "script"}
        # Deck — draft_id + slides.
        assert body["deck"]["draft_id"] == 1
        assert isinstance(body["deck"]["slides"], list)
        assert body["deck"]["slides"][0]["title"] == "Opening"
        # Script — draft_id + sections + word/min estimates.
        assert body["script"]["draft_id"] == 2
        assert isinstance(body["script"]["sections"], list)
        assert body["script"]["sections"][0]["slide_number"] == 1
        assert body["script"]["sections"][0]["speaker"] == "Molly"
        assert body["script"]["total_words"] > 0
        assert body["script"]["estimated_minutes"] >= 1

    def test_404_with_message_when_deck_missing(self, monkeypatch):
        from tools import editor_drafts

        async def _get(doc_type: str, _email: str):
            if doc_type == "presentation_deck":
                return None
            return _script_draft()

        monkeypatch.setattr(editor_drafts, "get_current_draft", _get)
        resp = client.get(ENDPOINT, headers=TEAM)
        assert resp.status_code == 404
        assert "deck" in resp.json()["detail"].lower()
        assert "generate your deck" in resp.json()["detail"].lower()

    def test_404_with_message_when_script_missing(self, monkeypatch):
        from tools import editor_drafts

        async def _get(doc_type: str, _email: str):
            if doc_type == "presentation_script":
                return None
            return _deck_draft()

        monkeypatch.setattr(editor_drafts, "get_current_draft", _get)
        resp = client.get(ENDPOINT, headers=TEAM)
        assert resp.status_code == 404
        assert "script" in resp.json()["detail"].lower()
        assert "generate your script" in resp.json()["detail"].lower()

    def test_estimated_minutes_at_150_wpm(self, monkeypatch):
        # 300 words of prose ⇒ 2 minutes at 150 wpm.
        from tools import editor_drafts

        script = _script_draft()
        prose = " ".join(["word"] * 300)
        script["content_json"] = _doc(
            _h(2, "Slide 1: Test"),
            _h(3, "Speaker: Alice"),
            _p(prose),
        )

        async def _get(doc_type: str, _email: str):
            return _deck_draft() if doc_type == "presentation_deck" else script

        monkeypatch.setattr(editor_drafts, "get_current_draft", _get)
        resp = client.get(ENDPOINT, headers=TEAM)
        assert resp.status_code == 200
        body = resp.json()
        assert body["script"]["total_words"] == 300
        # 300 / 150 = 2.0 → rounded to 2.
        assert body["script"]["estimated_minutes"] == 2
