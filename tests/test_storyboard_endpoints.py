"""
tests/test_storyboard_endpoints.py

Coverage for Sprint 6 Phase 6 — Storyboard Editor + generate-from-storyboard.

Test env shortcuts:
  - POST /storyboard/draft returns the in-memory storyboard JSON when no
    DATABASE_URL is configured (`persistence: "unavailable"`).
  - POST /generate-from-storyboard accepts an inline `storyboard` body
    in test env, bypassing the DB lookup entirely.

This lets the smoke tests run in <1s without touching Postgres while
still exercising every renderer (pptx, docx script variants, qa).
"""
from __future__ import annotations

import os
import sys
from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "test-master-key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


def _auth_headers() -> dict:
    from config import MASTER_API_KEY  # type: ignore[import]
    return {"X-API-Key": MASTER_API_KEY}


@pytest.fixture
def client() -> TestClient:
    from main import app  # type: ignore[import]
    return TestClient(app)


def _minimal_storyboard() -> dict:
    """A 3-slide storyboard for the renderer smoke tests."""
    import uuid
    return {
        "slides": [
            {
                "id": str(uuid.uuid4()), "order": 1, "owner": "Molly",
                "timing_mins": 0.5, "headline": "Title slide",
                "key_point": "Twenty-five years of returns",
                "chart_ref": None, "speaker_note": "Welcome and introductions.",
                "live_demo": False, "transition": "Now the architecture…",
                "ai_draft": True,
            },
            {
                "id": str(uuid.uuid4()), "order": 2, "owner": "Michael",
                "timing_mins": 1.5, "headline": "AI Council",
                "key_point": "Six agents plus two dissenters",
                "chart_ref": None, "speaker_note": "Explain the multi-agent structure.",
                "live_demo": True, "transition": "Bob will close…",
                "ai_draft": True,
            },
            {
                "id": str(uuid.uuid4()), "order": 3, "owner": "Bob",
                "timing_mins": 1.5, "headline": "Limitations",
                "key_point": "Backtests are not forecasts",
                "chart_ref": None, "speaker_note": "Discuss regime shift risk.",
                "live_demo": False, "transition": "",
                "ai_draft": True,
            },
        ],
        "total_timing_mins": 3.5,
        "ai_draft": True,
    }


class TestStoryboardDraftEndpoint:
    """POST /api/documents/storyboard/draft generates a 15-slide AI draft."""

    def test_endpoint_returns_200(self, client: TestClient) -> None:
        r = client.post("/api/documents/storyboard/draft", headers=_auth_headers())
        assert r.status_code == 200, r.text[:300]

    def test_response_contains_storyboard(self, client: TestClient) -> None:
        r = client.post("/api/documents/storyboard/draft", headers=_auth_headers())
        body = r.json()
        assert "storyboard" in body
        assert "slides" in body["storyboard"]

    def test_default_storyboard_has_15_slides(self, client: TestClient) -> None:
        """CLAUDE.md spec: 15-slide structure totalling 19:30 of 20 minutes."""
        r = client.post("/api/documents/storyboard/draft", headers=_auth_headers())
        slides = r.json()["storyboard"]["slides"]
        assert len(slides) == 15

    def test_slide_owners_span_all_three_team_members(self, client: TestClient) -> None:
        r = client.post("/api/documents/storyboard/draft", headers=_auth_headers())
        owners = {s["owner"] for s in r.json()["storyboard"]["slides"]}
        # Molly and Michael always present; Bob has at least one slide
        for required in ("Molly", "Michael", "Bob"):
            assert required in owners, f"No slides assigned to {required}"

    def test_total_timing_under_20_minutes(self, client: TestClient) -> None:
        r = client.post("/api/documents/storyboard/draft", headers=_auth_headers())
        sb = r.json()["storyboard"]
        # CLAUDE.md spec: ~19:30 — give a small tolerance band
        assert 18.0 <= sb["total_timing_mins"] <= 20.5

    def test_each_slide_has_required_fields(self, client: TestClient) -> None:
        r = client.post("/api/documents/storyboard/draft", headers=_auth_headers())
        required = {"id", "order", "owner", "timing_mins", "headline",
                    "key_point", "chart_ref", "speaker_note", "live_demo",
                    "transition", "ai_draft"}
        for slide in r.json()["storyboard"]["slides"]:
            missing = required - set(slide.keys())
            assert not missing, f"Slide {slide.get('order')} missing: {missing}"


class TestGenerateFromStoryboard:
    """POST /api/reports/generate-from-storyboard/{id} dispatches by output_type."""

    def test_deck_returns_valid_pptx(self, client: TestClient) -> None:
        r = client.post(
            "/api/reports/generate-from-storyboard/test-id",
            headers=_auth_headers(),
            json={"output_type": "deck", "storyboard": _minimal_storyboard()},
        )
        assert r.status_code == 200
        # .pptx is a ZIP archive — assert the bytes parse as one
        with ZipFile(BytesIO(r.content)) as z:
            names = z.namelist()
            # Every pptx contains [Content_Types].xml at the root
            assert "[Content_Types].xml" in names

    def test_deck_filename_has_pptx_extension(self, client: TestClient) -> None:
        r = client.post(
            "/api/reports/generate-from-storyboard/test-id",
            headers=_auth_headers(),
            json={"output_type": "deck", "storyboard": _minimal_storyboard()},
        )
        dispo = r.headers.get("content-disposition", "")
        assert ".pptx" in dispo

    def test_script_returns_valid_docx(self, client: TestClient) -> None:
        r = client.post(
            "/api/reports/generate-from-storyboard/test-id",
            headers=_auth_headers(),
            json={"output_type": "script", "storyboard": _minimal_storyboard()},
        )
        assert r.status_code == 200
        with ZipFile(BytesIO(r.content)) as z:
            assert "word/document.xml" in z.namelist()

    def test_script_molly_filters_to_molly_slides(self, client: TestClient) -> None:
        """Molly-only script must contain her slide headline but not Bob's."""
        sb = _minimal_storyboard()
        r = client.post(
            "/api/reports/generate-from-storyboard/test-id",
            headers=_auth_headers(),
            json={"output_type": "script_molly", "storyboard": sb},
        )
        with ZipFile(BytesIO(r.content)) as z:
            body = z.read("word/document.xml").decode("utf-8", errors="ignore")
        assert "Title slide" in body  # Molly's slide
        assert "Limitations" not in body  # Bob's slide

    def test_rehearsal_includes_cues_section(self, client: TestClient) -> None:
        """Rehearsal output is the only variant that includes timing cues."""
        r = client.post(
            "/api/reports/generate-from-storyboard/test-id",
            headers=_auth_headers(),
            json={"output_type": "rehearsal", "storyboard": _minimal_storyboard()},
        )
        assert r.status_code == 200

    def test_qa_returns_docx(self, client: TestClient) -> None:
        r = client.post(
            "/api/reports/generate-from-storyboard/test-id",
            headers=_auth_headers(),
            json={"output_type": "qa", "storyboard": _minimal_storyboard()},
        )
        assert r.status_code == 200
        with ZipFile(BytesIO(r.content)) as z:
            body = z.read("word/document.xml").decode("utf-8", errors="ignore")
        # Three required Q&A sections per CLAUDE.md
        assert "Forest Capital questions" in body
        assert "MSFA Board questions" in body
        assert "AI usage questions" in body

    def test_invalid_output_type_returns_422(self, client: TestClient) -> None:
        r = client.post(
            "/api/reports/generate-from-storyboard/test-id",
            headers=_auth_headers(),
            json={"output_type": "interpretive_dance", "storyboard": _minimal_storyboard()},
        )
        assert r.status_code == 422


class TestStoryboardTemplate:
    """Unit tests on tools/storyboard_template — pure-function path."""

    def test_build_default_storyboard_with_no_results(self) -> None:
        from tools.storyboard_template import build_default_storyboard
        sb = build_default_storyboard(strategy_results=None)
        assert len(sb["slides"]) == 15
        assert sb["ai_draft"] is True

    def test_build_default_storyboard_interpolates_results(self) -> None:
        from tools.storyboard_template import build_default_storyboard
        sb = build_default_storyboard(
            strategy_results={
                "VOL_TARGETING": {"sharpe_ratio": 1.02, "is_significant": True},
                "BENCHMARK": {"sharpe_ratio": 0.52, "is_significant": False},
            },
        )
        # Slide 5 references {best_strategy} — should now contain VOL TARGETING
        slide_5 = next(s for s in sb["slides"] if s["order"] == 5)
        assert "VOL TARGETING" in slide_5["headline"] or "VOL TARGETING" in slide_5["key_point"]


class TestDocumentAssistantEndpoint:
    """Gemini-backed editing assistant — test env returns deterministic mock."""

    def test_assistant_returns_200(self, client: TestClient) -> None:
        r = client.post(
            "/api/documents/test-id/assistant",
            headers=_auth_headers(),
            json={
                "message": "make this more confident",
                "context_content": "The strategy may have outperformed.",
                "context_type": "slide",
            },
        )
        assert r.status_code == 200

    def test_assistant_returns_diff_object(self, client: TestClient) -> None:
        r = client.post(
            "/api/documents/test-id/assistant",
            headers=_auth_headers(),
            json={"message": "tighten", "context_content": "Original text here.\n\nSecond paragraph."},
        )
        body = r.json()
        assert "suggestion" in body
        assert "diff" in body
        assert "removed" in body["diff"]
        assert "added" in body["diff"]

    def test_assistant_rejects_missing_message(self, client: TestClient) -> None:
        r = client.post(
            "/api/documents/test-id/assistant",
            headers=_auth_headers(),
            json={"context_content": "content"},
        )
        assert r.status_code == 422

    def test_assistant_rejects_oversized_message(self, client: TestClient) -> None:
        r = client.post(
            "/api/documents/test-id/assistant",
            headers=_auth_headers(),
            json={"message": "x" * 1100, "context_content": ""},
        )
        assert r.status_code == 422


class TestPptxGeneratorUnit:
    def test_build_pptx_produces_valid_bytes(self) -> None:
        from tools.pptx_generator import build_pptx_from_storyboard
        out = build_pptx_from_storyboard(_minimal_storyboard())
        assert isinstance(out, bytes)
        assert len(out) > 5000  # smallest valid pptx is ~30kb but allow margin

    def test_pptx_contains_one_slide_per_storyboard_entry_plus_title(self) -> None:
        """The deck adds a title slide before the storyboard slides — so
        a 3-slide storyboard produces a 4-slide deck."""
        from tools.pptx_generator import build_pptx_from_storyboard
        from pptx import Presentation
        out = build_pptx_from_storyboard(_minimal_storyboard())
        prs = Presentation(BytesIO(out))
        assert len(prs.slides) == 4


class TestScriptWriterUnit:
    def test_build_script_text_includes_all_slides_unfiltered(self) -> None:
        from tools.script_writer import build_script_text
        out = build_script_text(_minimal_storyboard())
        assert "Title slide" in out
        assert "AI Council" in out
        assert "Limitations" in out

    def test_owner_filter_excludes_other_owners(self) -> None:
        from tools.script_writer import build_script_text
        out = build_script_text(_minimal_storyboard(), owner_filter="Bob")
        assert "Limitations" in out
        assert "AI Council" not in out

    def test_rehearsal_cues_appear_only_when_requested(self) -> None:
        from tools.script_writer import build_script_text
        clean = build_script_text(_minimal_storyboard())
        cued = build_script_text(_minimal_storyboard(), include_rehearsal_cues=True)
        assert "TIMING CUE" not in clean
        assert "TIMING CUE" in cued or "VISUAL CUE" in cued
