"""
tests/test_academic_review.py

Tests for the Academic Review council flow (agents/academic_review.py and
POST /api/council/academic-review). The compute helpers are pure and
unit-testable; the streaming endpoint is exercised end-to-end against the
test environment, where every agent and the arbiter fall back to mocks.
"""
from __future__ import annotations

import asyncio
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

client = TestClient(app)
SESSION_HEADERS = {"X-API-Key": generate_session_token("ruurdsm@queens.edu")}


# ── 1. Context assembly — documents present ───────────────────────────────────

def test_context_assembly_returns_expected_keys_when_documents_present():
    from agents.academic_review import (
        group_documents_by_type, build_review_context_block, DOC_TYPE_LABELS,
    )
    docs = [
        {"name": "rubric.md", "document_type": "midpoint_requirements",
         "content_text": "Midpoint is worth 10% of the grade."},
        {"name": "slides.pdf", "document_type": "presentation_slides",
         "content_text": "Slide deck draft."},
    ]
    grouped = group_documents_by_type(docs)
    # Every document type is a key — present types populated, others empty.
    assert set(grouped.keys()) == set(DOC_TYPE_LABELS.keys())
    assert len(grouped["midpoint_requirements"]) == 1
    assert len(grouped["presentation_slides"]) == 1

    analytics = {"strategy_count": 10,
                 "performance_range": {"start": "2002-07-31", "end": "2025-12-31",
                                       "n_months": 282},
                 "risk_free_rate": 0.045,
                 "analytics_components": ["summary statistics", "rolling correlation"]}
    block = build_review_context_block(analytics, grouped)
    # The block carries the analytics inventory and the uploaded doc content.
    assert "Strategies analysed: 10" in block
    assert "282 months" in block
    assert "Midpoint is worth 10%" in block
    assert "MIDPOINT CHECK-IN REQUIREMENTS" in block


# ── 2. Context assembly — missing document types ──────────────────────────────

def test_context_assembly_handles_missing_document_types_gracefully():
    from agents.academic_review import (
        group_documents_by_type, build_review_context_block, DOC_TYPE_LABELS,
    )
    grouped = group_documents_by_type([])   # no documents uploaded
    assert set(grouped.keys()) == set(DOC_TYPE_LABELS.keys())
    assert all(v == [] for v in grouped.values())

    block = build_review_context_block(
        {"strategy_count": 0, "performance_range": None,
         "risk_free_rate": None, "analytics_components": []},
        grouped,
    )
    # Missing types render as "(not yet uploaded)" — never an error.
    assert "(not yet uploaded)" in block
    # Every document-type label still appears.
    for label in DOC_TYPE_LABELS.values():
        assert label in block


# ── 3. Peer fan-out invokes all non-arbiter agents ────────────────────────────

def test_peer_fan_out_invokes_all_non_arbiter_agents():
    from agents.academic_review import peer_agent_ids, run_peer_fan_out
    ids = peer_agent_ids()
    # The academic advisor is the arbiter — never a peer.
    assert "academic_advisor" not in ids
    # The seven council peers.
    assert set(ids) == {
        "equity_analyst", "fixed_income_analyst", "risk_manager",
        "quant_backtester", "cio", "independent_analyst", "contrarian_analyst",
    }
    # The fan-out actually produces a response for every peer.
    responses = asyncio.run(run_peer_fan_out("CONTEXT BLOCK"))
    assert set(responses.keys()) == set(ids)
    assert all(isinstance(v, str) and v for v in responses.values())


# ── 4. Arbiter receives all peer responses ────────────────────────────────────

def test_arbiter_message_contains_every_peer_response():
    from agents.academic_review import build_arbiter_user_message, peer_agent_ids
    peer_responses = {
        aid: f"PEER-MARKER-{aid}-unique-text" for aid in peer_agent_ids()
    }
    msg = build_arbiter_user_message("CONTEXT BLOCK", peer_responses)
    # Every peer's response text reaches the arbiter prompt.
    for aid, text in peer_responses.items():
        assert text in msg, f"{aid} response missing from arbiter message"
    # The five-section verdict instructions are present.
    assert "five rubric sections" in msg
    assert "Overall Academic Readiness" in msg
    # The PM dual-verdict additions surface in the arbiter prompt too —
    # the top-level **Academic rigour:** + **Portfolio Manager insight:**
    # lines must reach the arbiter so the user sees both lenses.
    assert "**Academic rigour:**" in msg
    assert "**Portfolio Manager insight:**" in msg


# ── 5. Streaming order — peer_responses before arbiter chunks ──────────────────

def test_stream_emits_peer_responses_before_arbiter_chunks():
    r = client.post("/api/council/academic-review", headers=SESSION_HEADERS)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    body = r.text
    assert '"type": "peer_responses"' in body
    assert '"type": "arbiter_chunk"' in body
    assert "[DONE]" in body
    # peer_responses must arrive before any arbiter chunk.
    assert body.index('"type": "peer_responses"') < body.index('"type": "arbiter_chunk"')
    # ...and [DONE] is the final frame.
    assert body.rindex("[DONE]") > body.index('"type": "arbiter_chunk"')


def test_academic_review_requires_auth():
    assert client.post("/api/council/academic-review").status_code == 401


# ── Script rubric — applied per document_type ────────────────────────────────

class TestScriptRubric:
    """The Academic Review arbiter applies a script-specific rubric when
    the request carries ?document_type=presentation_script. Other
    document types use the default written-submission rubric. The two
    rubrics differ in BOTH evaluation categories (coherence / clarity /
    coverage / speaker differentiation vs data sufficiency / requirements
    / quality / etc.) AND the rating scale (Strong / Needs Work /
    Incomplete vs Strong / Developing / Needs Work)."""

    def test_default_rubric_used_when_document_type_absent(self):
        from agents.academic_review import (
            build_arbiter_user_message, peer_agent_ids,
        )
        peer_responses = {aid: "ok" for aid in peer_agent_ids()}
        msg = build_arbiter_user_message("CTX", peer_responses)
        # Default rubric — sections 1-5 from the written-submission set.
        assert "Data Sufficiency and Methodology" in msg
        assert "Requirements and Rubric Alignment" in msg
        assert "Overall Academic Readiness" in msg
        # Script-specific section headings do NOT appear.
        assert "Argument Coherence Across Slides" not in msg
        assert "Speaker Differentiation" not in msg
        # Default rating scale — "Developing" is present.
        assert "Developing" in msg

    def test_script_rubric_used_when_script_review_true(self):
        from agents.academic_review import (
            build_arbiter_user_message, peer_agent_ids,
        )
        peer_responses = {aid: "ok" for aid in peer_agent_ids()}
        msg = build_arbiter_user_message(
            "CTX", peer_responses, script_review=True)
        # Script rubric — the five script-specific section headings.
        assert "Argument Coherence Across Slides" in msg
        assert "Clarity for a Mixed Faculty / Investor Audience" in msg
        assert "Coverage of Key Findings" in msg
        assert "Speaker Differentiation and Voice" in msg
        assert "Overall Delivery Readiness" in msg
        # Written-submission sections do NOT appear.
        assert "Data Sufficiency and Methodology" not in msg
        assert "Requirements and Rubric Alignment" not in msg
        # Script rating scale — Incomplete replaces Developing.
        assert "Strong | Needs Work | Incomplete" in msg
        # Exclusion list is explicit — citation formatting etc. is
        # called out as "DOES NOT evaluate" so the model doesn't
        # accidentally score it.
        assert "Citation formatting" in msg

    def test_script_review_ignores_multi_user_section_6(self):
        # The division-of-labour section is only relevant to the
        # written deliverables. A script verdict stays focused on
        # delivery readiness.
        from agents.academic_review import (
            build_arbiter_user_message, peer_agent_ids,
        )
        peer_responses = {aid: "ok" for aid in peer_agent_ids()}
        msg = build_arbiter_user_message(
            "CTX", peer_responses, multi_user=True, script_review=True)
        # Section 6 (Team Engagement and Division of Labour) must NOT
        # appear in a script verdict, even when multi_user is true.
        assert "Team Engagement and Division of Labour" not in msg

    def test_endpoint_routes_script_query_param_to_arbiter(self, monkeypatch):
        # Verify the query param threads through to run_arbiter_with_harness
        # with script_review=True. Intercept the call and assert the kwargs.
        from agents import academic_review

        captured: dict[str, object] = {}

        def _fake_arbiter(context_block, peer_responses, multi_user,
                          script_review):
            captured["multi_user"] = multi_user
            captured["script_review"] = script_review
            return "stub verdict"

        monkeypatch.setattr(
            academic_review, "run_arbiter_with_harness", _fake_arbiter)

        # presentation_script → script_review=True
        r = client.post(
            "/api/council/academic-review"
            "?document_type=presentation_script",
            headers=SESSION_HEADERS)
        assert r.status_code == 200
        assert captured.get("script_review") is True

        # absent → script_review=False
        captured.clear()
        r = client.post("/api/council/academic-review",
                         headers=SESSION_HEADERS)
        assert r.status_code == 200
        assert captured.get("script_review") is False

        # midpoint_paper or any other type → script_review=False
        captured.clear()
        r = client.post("/api/council/academic-review"
                         "?document_type=midpoint_paper",
                         headers=SESSION_HEADERS)
        assert r.status_code == 200
        assert captured.get("script_review") is False
