"""tests/test_presentation_script_export.py -- the Presentation Script
export endpoint + docx renderer.

Pins:
  * POST /api/v1/export/presentation-script returns the DOCX when
    story_plans has a cached deck plan,
  * the same endpoint returns 404 with the spec'd message when there
    is no cached plan (or only a deterministic_fallback row),
  * the rendered .docx contains the four required headings
    (HOW TO USE THIS SCRIPT, PRESENTER SCRIPT, ANTICIPATED COMMITTEE
    QUESTIONS, SLIDE TIMING REFERENCE) plus the AI DRAFT banner,
  * [SLIDE N: title] markers in full_script become bold sub-headings
    in the rendered document,
  * a null / empty anticipated_questions list degrades to a graceful
    fallback message without crashing,
  * /api/v1/report/readiness includes deck_story_plan_available in
    its response shape.

Wire-level correctness against a mocked story_plan cache -- the
real cache + LLM passes are exercised in test_story_plan.py and on
Render where the live keys exist.
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


# ── Fixture payloads ────────────────────────────────────────────────────


_FAKE_FULL_SCRIPT = (
    "[SLIDE 1: Does Diversification Beat 100% Equity?]\n"
    "Welcome -- the question we set out to answer was simple.\n"
    "\n"
    "[SLIDE 2: Static, Dynamic, or Benchmark?]\n"
    "Three families of strategies: static blends, dynamic regime-aware "
    "switches, and the 100% equity benchmark.\n"
)
_FAKE_ANTICIPATED_QUESTIONS = [
    {
        "question": "How does your strategy survive a 2008-scale crisis?",
        "suggested_answer":
            "We stress-test against the 2008 and 2022 drawdowns -- the "
            "dynamic strategies cut max drawdown by 38% vs benchmark.",
        "difficulty": "HARD",
    },
    {
        "question": "What is your turnover cost assumption?",
        "suggested_answer":
            "12 bps per round trip; the sensitivity analysis shows the "
            "Sharpe ranking holds at 50 bps.",
        "difficulty": "MEDIUM",
    },
]


def _patch_cache(
    monkeypatch, *, plan: dict | None,
    data_hash: str = "test_hash_abc",
):
    """Patch the two reads the endpoint makes: current_data_hash() +
    get_cached_story_plan(). Any pattern -- present plan, missing plan,
    deterministic_fallback plan -- is driven through this helper."""
    from tools import audit_assembler, story_plan

    async def _fake_hash():
        return data_hash

    async def _fake_plan(_hash, _doc_type):
        return plan

    monkeypatch.setattr(audit_assembler, "current_data_hash", _fake_hash)
    monkeypatch.setattr(story_plan, "get_cached_story_plan", _fake_plan)


# ── Endpoint contract ──────────────────────────────────────────────────


class TestExportPresentationScriptEndpoint:
    """Wire-level contract: 200 DOCX on cache hit, 404 on cold."""

    def test_returns_200_docx_on_cache_hit(
        self, monkeypatch, client: TestClient,
    ):
        _patch_cache(monkeypatch, plan={
            "full_script": _FAKE_FULL_SCRIPT,
            "anticipated_questions": _FAKE_ANTICIPATED_QUESTIONS,
            "_model": "claude-opus-4-7",
            "computed_at": "2026-06-19T10:00:00+00:00",
        })
        r = client.post(
            "/api/v1/export/presentation-script", headers=_auth_headers())
        assert r.status_code == 200
        assert "wordprocessingml.document" in r.headers.get(
            "content-type", "")
        dispo = r.headers.get("content-disposition", "")
        assert "attachment" in dispo
        assert ".docx" in dispo

    def test_returns_404_when_no_cached_plan(
        self, monkeypatch, client: TestClient,
    ):
        _patch_cache(monkeypatch, plan=None)
        r = client.post(
            "/api/v1/export/presentation-script", headers=_auth_headers())
        assert r.status_code == 404
        body = r.json()
        # The spec'd message text must appear so the operator knows what
        # to do (generate the deck first). Asserting the load-bearing
        # substring rather than a verbatim match so we can iterate the
        # copy without breaking the test.
        assert "Generate the Presentation Deck first" \
            in body["detail"]

    def test_returns_404_when_plan_is_deterministic_fallback(
        self, monkeypatch, client: TestClient,
    ):
        """A fallback row is persisted alongside real plans when an LLM
        pass fails. The endpoint must treat fallback rows as 'not yet
        generated' so the user is nudged to regenerate the deck rather
        than letting them download a script built around a degraded
        outline."""
        _patch_cache(monkeypatch, plan={
            "full_script": "fallback content",
            "anticipated_questions": [],
            "_model": "deterministic_fallback",
            "computed_at": "2026-06-19T10:00:00+00:00",
        })
        r = client.post(
            "/api/v1/export/presentation-script", headers=_auth_headers())
        assert r.status_code == 404


# ── DOCX structure ─────────────────────────────────────────────────────


def _docx_body_xml(content: bytes) -> str:
    """Extract word/document.xml as a single decoded string for grep-
    style assertions. Mirrors the pattern used by tests/test_audit_summary
    and tests/test_reports_endpoints."""
    with ZipFile(BytesIO(content)) as z:
        return z.read("word/document.xml").decode("utf-8", errors="ignore")


class TestPresentationScriptDocxStructure:
    """The rendered .docx must carry the four spec'd sections + the
    AI DRAFT banner + the bold slide-marker sub-headings."""

    def test_contains_four_required_section_headings(
        self, monkeypatch, client: TestClient,
    ):
        _patch_cache(monkeypatch, plan={
            "full_script": _FAKE_FULL_SCRIPT,
            "anticipated_questions": _FAKE_ANTICIPATED_QUESTIONS,
            "_model": "claude-opus-4-7",
            "computed_at": "2026-06-19T10:00:00+00:00",
        })
        r = client.post(
            "/api/v1/export/presentation-script", headers=_auth_headers())
        body = _docx_body_xml(r.content)
        for heading in (
            "HOW TO USE THIS SCRIPT",
            "PRESENTER SCRIPT",
            "ANTICIPATED COMMITTEE QUESTIONS",
            "SLIDE TIMING REFERENCE",
        ):
            assert heading in body, f"missing section heading: {heading}"

    def test_carries_ai_draft_banner(
        self, monkeypatch, client: TestClient,
    ):
        _patch_cache(monkeypatch, plan={
            "full_script": _FAKE_FULL_SCRIPT,
            "anticipated_questions": _FAKE_ANTICIPATED_QUESTIONS,
            "_model": "claude-opus-4-7",
            "computed_at": "2026-06-19T10:00:00+00:00",
        })
        r = client.post(
            "/api/v1/export/presentation-script", headers=_auth_headers())
        # The banner is in the page header (header*.xml) on every page
        # plus the title page in the document body.
        header_xml = ""
        with ZipFile(BytesIO(r.content)) as z:
            for name in z.namelist():
                if "header" in name and name.endswith(".xml"):
                    header_xml = z.read(name).decode(
                        "utf-8", errors="ignore")
                    break
        combined = _docx_body_xml(r.content) + header_xml
        assert "AI DRAFT" in combined

    def test_slide_markers_become_bold_subheadings(
        self, monkeypatch, client: TestClient,
    ):
        _patch_cache(monkeypatch, plan={
            "full_script": _FAKE_FULL_SCRIPT,
            "anticipated_questions": _FAKE_ANTICIPATED_QUESTIONS,
            "_model": "claude-opus-4-7",
            "computed_at": "2026-06-19T10:00:00+00:00",
        })
        r = client.post(
            "/api/v1/export/presentation-script", headers=_auth_headers())
        body = _docx_body_xml(r.content)
        # The marker text [SLIDE N: title] becomes 'SLIDE N: title' in
        # the rendered output -- the brackets are stripped because the
        # marker is converted to a bold heading paragraph, not body
        # prose. Both slide numbers from the fixture must surface.
        assert "SLIDE 1: Does Diversification Beat 100% Equity?" \
            in body
        assert "SLIDE 2: Static, Dynamic, or Benchmark?" in body
        # The literal bracket-wrapped form must NOT remain anywhere in
        # the rendered body (that would indicate the marker walker
        # missed it and the renderer dumped the marker as prose).
        assert "[SLIDE" not in body

    def test_renders_anticipated_questions_with_difficulty_badges(
        self, monkeypatch, client: TestClient,
    ):
        _patch_cache(monkeypatch, plan={
            "full_script": _FAKE_FULL_SCRIPT,
            "anticipated_questions": _FAKE_ANTICIPATED_QUESTIONS,
            "_model": "claude-opus-4-7",
            "computed_at": "2026-06-19T10:00:00+00:00",
        })
        r = client.post(
            "/api/v1/export/presentation-script", headers=_auth_headers())
        body = _docx_body_xml(r.content)
        # Each question carries its numbered Q1/Q2 prefix + a
        # difficulty badge wrapped in square brackets.
        assert "Q1" in body and "[HARD]" in body
        assert "Q2" in body and "[MEDIUM]" in body
        # Question texts and answer prefixes must both render.
        assert "How does your strategy survive a 2008-scale crisis?" \
            in body
        assert "Suggested answer:" in body

    def test_null_anticipated_questions_renders_graceful_fallback(
        self, monkeypatch, client: TestClient,
    ):
        """A cached deck plan whose Pass 3 failed leaves
        anticipated_questions as null/empty. The renderer must not
        crash -- it surfaces a clear 'not yet available' note instead."""
        _patch_cache(monkeypatch, plan={
            "full_script": _FAKE_FULL_SCRIPT,
            "anticipated_questions": None,
            "_model": "claude-opus-4-7",
            "computed_at": "2026-06-19T10:00:00+00:00",
        })
        r = client.post(
            "/api/v1/export/presentation-script", headers=_auth_headers())
        assert r.status_code == 200
        body = _docx_body_xml(r.content)
        assert "Q&amp;A preparation not yet available" in body \
            or "Q&A preparation not yet available" in body
        # The heading still renders -- the section never disappears,
        # only its content degrades to the fallback note.
        assert "ANTICIPATED COMMITTEE QUESTIONS" in body

    def test_timing_reference_uses_canonical_slide_titles(
        self, monkeypatch, client: TestClient,
    ):
        """The timing-reference table reads SLIDE_TITLES from
        academic_deck so the reference stays in lockstep with the
        rendered deck. Pinning a couple of titles guards against a
        future renumbering breaking the cross-reference silently."""
        _patch_cache(monkeypatch, plan={
            "full_script": _FAKE_FULL_SCRIPT,
            "anticipated_questions": _FAKE_ANTICIPATED_QUESTIONS,
            "_model": "claude-opus-4-7",
            "computed_at": "2026-06-19T10:00:00+00:00",
        })
        r = client.post(
            "/api/v1/export/presentation-script", headers=_auth_headers())
        body = _docx_body_xml(r.content)
        # Slide 1 + slide 9 titles must both appear in the table cells
        # (they're already in full_script, but the table is the
        # contract surface the user clicked through for in the spec).
        assert "Does Diversification Beat 100% Equity?" in body
        assert "AnalyticsDesk: The Platform Behind the Analysis" in body
        # Live demo annotation surfaces only on slide 9.
        assert "Live demo" in body


# ── Readiness payload includes the new flag ────────────────────────────


class TestReadinessSurfacesDeckStoryPlanAvailable:
    """The /api/v1/report/readiness payload must include the
    deck_story_plan_available field so the frontend can flip the
    Presentation Script card's button state without a second round-trip."""

    def test_field_present_in_response(
        self, monkeypatch, client: TestClient,
    ):
        """Field must always be present (true OR false) so the
        frontend type contract holds. Patching get_cached_story_plan
        to return a plan -> true; explicit assert covers the present-
        and-true branch."""
        _patch_cache(monkeypatch, plan={
            "full_script": _FAKE_FULL_SCRIPT,
            "anticipated_questions": _FAKE_ANTICIPATED_QUESTIONS,
            "_model": "claude-opus-4-7",
            "computed_at": "2026-06-19T10:00:00+00:00",
        })
        r = client.get(
            "/api/v1/report/readiness", headers=_auth_headers())
        assert r.status_code == 200
        body = r.json()
        assert "deck_story_plan_available" in body
        assert body["deck_story_plan_available"] is True

    def test_field_is_false_when_no_cached_plan(
        self, monkeypatch, client: TestClient,
    ):
        _patch_cache(monkeypatch, plan=None)
        r = client.get(
            "/api/v1/report/readiness", headers=_auth_headers())
        assert r.status_code == 200
        body = r.json()
        assert body["deck_story_plan_available"] is False

    def test_field_is_false_when_plan_is_deterministic_fallback(
        self, monkeypatch, client: TestClient,
    ):
        """A fallback row should NOT enable the script download --
        the same gate as the endpoint itself, otherwise a stale flag
        could let the user download a script built around a degraded
        outline."""
        _patch_cache(monkeypatch, plan={
            "full_script": "fallback",
            "anticipated_questions": [],
            "_model": "deterministic_fallback",
            "computed_at": "2026-06-19T10:00:00+00:00",
        })
        r = client.get(
            "/api/v1/report/readiness", headers=_auth_headers())
        body = r.json()
        assert body["deck_story_plan_available"] is False
