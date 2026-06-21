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


# ── 2b. Team role division context (May 28 2026) ──────────────────────────────
#
# The arbiter (and peers) must read role-division framing alongside
# the team-activity counts so the verdict does not misread Michael's
# front-loaded engineering as analytical disengagement.

def test_context_block_carries_team_role_division_context():
    from agents.academic_review import (
        build_review_context_block, group_documents_by_type,
    )
    grouped = group_documents_by_type([])
    block = build_review_context_block(
        {"strategy_count": 0, "performance_range": None,
         "risk_free_rate": None, "analytics_components": []},
        grouped,
    )
    # May 26 2026 — assertions track the layered-ownership rewrite of
    # the TEAM ROLE DIVISION CONTEXT. The prior framing (Michael as
    # "platform engineer, not analytical disengagement") was replaced
    # with a positive ownership statement: validation infrastructure
    # (Michael) / analytical narrative (Bob) / human UAT (Molly).
    assert "TEAM ROLE DIVISION CONTEXT" in block
    assert "Michael Ruurds builds and operates the validation" in block
    assert "Bob Thao interprets" in block
    assert "Molly Murdock conducts human UAT" in block
    assert "layered ownership model" in block
    # The layered-ownership framing is the new explicit guidance.
    # The old "unresolved [[BOB]] markers" / "engineering commit
    # disparity" phrases are gone (proactive marker-hunting was the
    # bug PR was tracking).
    assert "Michael's engineering activity" in block
    assert "human-only division of labor" in block


def test_team_role_context_appears_before_team_engagement():
    # The role framing must be read by the arbiter BEFORE the raw
    # activity counts — otherwise the verdict could anchor on the
    # numbers and miss the framing.
    from agents.academic_review import (
        build_review_context_block, group_documents_by_type,
    )
    grouped = group_documents_by_type([])
    team_activity = {"per_member": []}
    block = build_review_context_block(
        {"strategy_count": 0, "performance_range": None,
         "risk_free_rate": None, "analytics_components": []},
        grouped, team_activity=team_activity,
        team_members=[("bob@queens.edu", "Bob Thao")],
    )
    role_idx = block.find("TEAM ROLE DIVISION CONTEXT")
    engagement_idx = block.find("TEAM ENGAGEMENT")
    assert role_idx != -1 and engagement_idx != -1
    assert role_idx < engagement_idx


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
    # May 26 2026 — the five rubric sections are the FNA 670 midpoint
    # rubric (sections 1-4) + Section 5 "Overall Academic Readiness"
    # (kept under that title so the Section-5 truncation detector
    # and fallback continue to work — UAT #53 / #59 / #128 / #125
    # history). The grade-impact-ranked Priority Areas list now
    # lives INSIDE Section 5.
    assert "five rubric sections" in msg
    assert "Data and Methodology" in msg
    assert "Preliminary Results and Diagnostics" in msg
    assert "Roles and Division of Labor" in msg
    assert "Next Steps and Open Questions" in msg
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
        # May 26 2026 — default rubric was rewritten to the FNA 670
        # midpoint check sections (1p / 1p / 0.5p / 0.5p) for
        # sections 1-4. Section 5 is kept as "Overall Academic
        # Readiness" so the Section-5 truncation detector / fallback
        # continues to work. The grade-impact-ranked Priority Areas
        # list now lives INSIDE Section 5.
        assert "Data and Methodology" in msg
        assert "Preliminary Results and Diagnostics" in msg
        assert "Roles and Division of Labor" in msg
        assert "Next Steps and Open Questions" in msg
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
        assert "Data and Methodology" not in msg
        assert "Preliminary Results and Diagnostics" not in msg
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
                          script_review, n_strategies=None,
                          brief_review=False, deck_review=False,
                          appendix_review=False):
            captured["multi_user"] = multi_user
            captured["script_review"] = script_review
            captured["n_strategies"] = n_strategies
            captured["brief_review"] = brief_review
            captured["deck_review"] = deck_review
            captured["appendix_review"] = appendix_review
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


# ── Section 5 fallback (UAT #128 / #125) ─────────────────────────────────────


class TestSectionFiveFallback:
    """UAT #128 reported "Overall Readiness Assessment section absent",
    UAT #125 reported "Only 4 sections returned" — both were the same
    truncation symptom: the arbiter's 2000-token cap (now 4000) clipped
    Section 5, and the evaluator's lenient scoring (8/10 for 4 sections)
    cleared the 7.0 threshold so the harness never retried. This block
    pins the three defences:
      1. Detection — _verdict_has_section_5 spots both the missing
         heading and an off-rubric title.
      2. Fallback — _assemble_section_5_fallback aggregates a rating
         from the present section verdicts so the rendered output
         always carries a Section 5 with substantive content.
      3. Evaluator scoring — the tightened all_sections_present rubric
         pushes a 4-section verdict below 7.0 so the harness retries
         instead of accepting it."""

    def test_detector_catches_completely_missing_section_5(self):
        from agents.academic_review import _verdict_has_section_5
        # Verdict with sections 1-4 only — the canonical UAT #128 shape.
        verdict = (
            "### 1. Data Sufficiency and Methodology\n**Rating:** Strong\n…\n"
            "### 2. Requirements and Rubric Alignment\n**Rating:** Strong\n…\n"
            "### 3. Deliverable Quality\n**Rating:** Developing\n…\n"
            "### 4. Priority Areas for Further Investigation\n"
            "**Rating:** Developing\n…\n"
        )
        assert _verdict_has_section_5(verdict, script_review=False) is False

    def test_detector_catches_off_rubric_section_5(self):
        # A fake Section 5 with the wrong title (the model hallucinated
        # a different heading) — still flagged as missing.
        from agents.academic_review import _verdict_has_section_5
        verdict = (
            "### 1. Data Sufficiency and Methodology\n**Rating:** Strong\n"
            "### 5. Some Other Heading\n**Rating:** Developing\n"
        )
        assert _verdict_has_section_5(verdict, script_review=False) is False

    def test_detector_accepts_a_well_formed_section_5(self):
        from agents.academic_review import _verdict_has_section_5
        verdict = (
            "### 1. Data Sufficiency and Methodology\n**Rating:** Strong\n"
            "### 5. Overall Academic Readiness\n**Rating:** Strong\n"
        )
        assert _verdict_has_section_5(verdict, script_review=False) is True

    def test_detector_uses_script_rubric_title_when_script_review(self):
        from agents.academic_review import _verdict_has_section_5
        # Script rubric expects "Overall Delivery Readiness".
        delivery = "### 5. Overall Delivery Readiness\n**Rating:** Strong\n"
        # Under PR-LLM-2 (May 28 2026) the detector now accepts ANY
        # `### 5. Overall <words>` heading — including the academic
        # rubric's title — because the body content is usable and
        # the wrong-title rename branch in the fallback handles the
        # title rewrite downstream. The strict-only check was firing
        # the fallback every run.
        assert _verdict_has_section_5(delivery, script_review=True) is True

    def test_detector_accepts_overall_variant_titles(self):
        """PR-LLM-2 (May 28 2026) — the strict-title detector fired
        the fallback on every review run because the arbiter wrote
        close-but-not-exact titles like "Overall Readiness" or
        "Overall Project Readiness". The detector now accepts any
        `### 5. Overall <words>` heading. The body content is usable
        as-is; the wrong-title rename branch in the fallback handles
        the title rewrite downstream when callers reach that path."""
        from agents.academic_review import _verdict_has_section_5
        for variant in (
            "### 5. Overall Readiness\n**Rating:** Strong\n",
            "### 5. Overall Project Readiness\n**Rating:** Developing\n",
            "### 5. Overall Verdict\n**Rating:** Needs Work\n",
            "### 5. Overall Readiness Assessment\n**Rating:** Strong\n",
        ):
            assert _verdict_has_section_5(variant, script_review=False) is True

    def test_detector_still_rejects_off_overall_titles(self):
        # A heading that does NOT start with "Overall" still trips
        # the fallback — the loosening is bounded.
        from agents.academic_review import _verdict_has_section_5
        verdict = "### 5. Final Verdict\n**Rating:** Strong\n"
        assert _verdict_has_section_5(verdict, script_review=False) is False

    def test_fallback_appends_section_5_with_aggregated_rating(self):
        from agents.academic_review import _assemble_section_5_fallback
        # 4-section verdict with a Needs Work in section 3.
        verdict = (
            "### 1. Data Sufficiency and Methodology\n**Rating:** Strong\n"
            "### 2. Requirements and Rubric Alignment\n**Rating:** Developing\n"
            "### 3. Deliverable Quality\n**Rating:** Needs Work\n"
            "### 4. Priority Areas for Further Investigation\n"
            "**Rating:** Developing\n"
        )
        peers = {"equity_analyst": "x", "fixed_income_analyst": "x"}
        out = _assemble_section_5_fallback(
            verdict, peers, script_review=False)
        # Section 5 added.
        assert "### 5. Overall Academic Readiness" in out
        # Aggregated rating reflects the worst — Needs Work present.
        assert "**Rating:** Needs Work" in out
        # Peer count surfaces in the body so the fallback reads
        # substantive, not placeholder.
        assert "2 peer agents" in out

    def test_fallback_aggregates_developing_when_no_needs_work(self):
        from agents.academic_review import _assemble_section_5_fallback
        verdict = (
            "### 1. Data Sufficiency and Methodology\n**Rating:** Strong\n"
            "### 2. Requirements and Rubric Alignment\n**Rating:** Developing\n"
            "### 3. Deliverable Quality\n**Rating:** Strong\n"
            "### 4. Priority Areas for Further Investigation\n"
            "**Rating:** Strong\n"
        )
        out = _assemble_section_5_fallback(verdict, {}, script_review=False)
        # Any Developing present → aggregate Developing (not Strong).
        assert "### 5. Overall Academic Readiness" in out
        # The fallback paragraph carries the aggregated rating.
        section_5_start = out.index("### 5.")
        assert "**Rating:** Developing" in out[section_5_start:]

    def test_fallback_uses_script_rubric_rating_scale(self):
        from agents.academic_review import _assemble_section_5_fallback
        verdict = (
            "### 1. Argument Coherence Across Slides\n**Rating:** Strong\n"
            "### 2. Clarity for a Mixed Faculty\n**Rating:** Needs Work\n"
            "### 3. Coverage of Key Findings\n**Rating:** Strong\n"
            "### 4. Speaker Differentiation and Voice\n**Rating:** Strong\n"
        )
        out = _assemble_section_5_fallback(verdict, {}, script_review=True)
        # Script rubric uses Overall Delivery Readiness.
        assert "### 5. Overall Delivery Readiness" in out
        section_5_start = out.index("### 5.")
        # Any Needs Work present → aggregate Needs Work (no
        # Incomplete here so it doesn't escalate further).
        assert "**Rating:** Needs Work" in out[section_5_start:]

    def test_evaluator_prompt_penalises_missing_sections_strictly(self):
        # May 26 2026 — the midpoint-rubric revision counts SEVEN
        # required elements (2 top-line summary lines + 5 sections),
        # not 5. The tightened scale is now: all 7 → 10, missing
        # 1 → 5, missing 2+ → 0. UAT issue references are kept so a
        # future maintainer who tries to relax the rubric reads the
        # historical context first.
        from agents.evaluator_prompts import (
            academic_review_arbiter_evaluator_prompt,
        )
        prompt = academic_review_arbiter_evaluator_prompt()
        # The new scale wording is present.
        assert "all 7 elements present scores 10" in prompt
        assert "missing one of the seven scores 5" in prompt
        assert "missing two or more scores 0" in prompt
        # The fix references UAT #128/#125 so a future maintainer who
        # tries to relax the rubric reads the historical context first.
        assert "#128" in prompt and "#125" in prompt

    def test_arbiter_max_tokens_increased_for_full_five_sections(self):
        # The previous 2000-token cap was the truncation root cause.
        # 4000 gives the verdict comfortable headroom.
        from agents.academic_review import ARBITER_MAX_TOKENS
        assert ARBITER_MAX_TOKENS >= 4000


# ── Brief-specific rubric (PR — academic review brief-specific rubric) ──────
#
# Background: applying the midpoint rubric to the executive brief
# scored Section 5 (Final Recommendations) Needs Work mechanically
# because the midpoint rubric expects "Next Steps and Open Questions"
# framing — PR #344's INVESTABLE_CONCLUSION_GUARD deliberately frames
# Section 5 as investment conclusions. The brief-specific rubric
# replaces the four midpoint sections with the six brief sections
# (Executive Summary / Methodology / Key Findings / Limitations /
# Final Recommendations / Visuals) weighted 15/20/25/15/20/5.

class TestBriefSpecificRubric:
    """The brief-specific rubric removes the structural 5.5/10 floor
    that the midpoint rubric was producing on every brief review."""

    def test_brief_arbiter_instructions_constant_contains_six_sections(self):
        from agents.academic_review import _ARBITER_INSTRUCTIONS_BRIEF
        # All six section heading words present.
        assert "Executive Summary" in _ARBITER_INSTRUCTIONS_BRIEF
        assert "Methodology" in _ARBITER_INSTRUCTIONS_BRIEF
        assert "Key Findings" in _ARBITER_INSTRUCTIONS_BRIEF
        assert "Limitations" in _ARBITER_INSTRUCTIONS_BRIEF
        assert "Final Recommendations" in _ARBITER_INSTRUCTIONS_BRIEF
        assert "Visuals" in _ARBITER_INSTRUCTIONS_BRIEF
        # Header signals brief mode (not midpoint, not script).
        assert "ARBITER VERDICT (EXECUTIVE BRIEF)" in _ARBITER_INSTRUCTIONS_BRIEF
        # Audience framing — senior investment + FNA 670 academic panel.
        assert "senior investment audience" in _ARBITER_INSTRUCTIONS_BRIEF
        assert "FNA 670" in _ARBITER_INSTRUCTIONS_BRIEF
        # Top-line summary lines.
        assert "**Academic rigour:**" in _ARBITER_INSTRUCTIONS_BRIEF
        assert "**Portfolio Manager insight:**" in _ARBITER_INSTRUCTIONS_BRIEF
        # Section weights spelled out in the section headings.
        assert "(15%)" in _ARBITER_INSTRUCTIONS_BRIEF
        assert "(20%)" in _ARBITER_INSTRUCTIONS_BRIEF
        assert "(25%)" in _ARBITER_INSTRUCTIONS_BRIEF
        assert "(5%)" in _ARBITER_INSTRUCTIONS_BRIEF

    def test_brief_prohibited_patterns_listed(self):
        # The PROHIBITED PATTERNS block must call out the harness
        # artifacts that have leaked into briefs before.
        from agents.academic_review import _ARBITER_INSTRUCTIONS_BRIEF
        assert "Further research would benefit from" in _ARBITER_INSTRUCTIONS_BRIEF
        assert "Next steps include" in _ARBITER_INSTRUCTIONS_BRIEF
        # Roles content out of scope for the brief.
        assert "Roles and division of labor" in _ARBITER_INSTRUCTIONS_BRIEF
        # The PM_CRITERION + harness-table detector pattern is verbatim
        # so the arbiter scores those sections Needs Work on sight.
        assert "PM_CRITERION" in _ARBITER_INSTRUCTIONS_BRIEF
        assert "Prior Issue" in _ARBITER_INSTRUCTIONS_BRIEF
        assert "Resolution Applied" in _ARBITER_INSTRUCTIONS_BRIEF
        # OOS Sharpe window-definition prohibition.
        assert "OOS Sharpe" in _ARBITER_INSTRUCTIONS_BRIEF

    def test_editor_to_review_type_routes_executive_brief_to_brief_review(self):
        # Previously "other" (the catch-all) which routed the verdict
        # through the midpoint rubric. The new value is the signal the
        # rubric switch reads.
        from agents.academic_review import _EDITOR_TO_REVIEW_TYPE
        assert _EDITOR_TO_REVIEW_TYPE["executive_brief"] == "brief_review"

    def test_build_arbiter_user_message_brief_mode_uses_brief_instructions(self):
        # Wire-level: brief_review=True must pick the brief rubric.
        from agents.academic_review import (
            build_arbiter_user_message, peer_agent_ids,
            _ARBITER_INSTRUCTIONS_BRIEF, _ARBITER_INSTRUCTIONS,
        )
        peer_responses = {aid: "ok" for aid in peer_agent_ids()}
        msg = build_arbiter_user_message(
            "CTX", peer_responses, brief_review=True)
        # Brief rubric heading is present.
        assert "ARBITER VERDICT (EXECUTIVE BRIEF)" in msg
        # The full brief instructions block is embedded.
        assert _ARBITER_INSTRUCTIONS_BRIEF in msg
        # The default (midpoint) rubric header is NOT present.
        assert "ARBITER VERDICT (MIDPOINT CHECK)" not in msg
        # Spot-check section weights.
        assert "Key Findings and Insights (25%)" in msg
        assert "Visuals (5%)" in msg

    def test_brief_review_ignores_multi_user_section_6(self):
        # Section 6 (Team Engagement and Division of Labour) is
        # midpoint-only — a brief's verdict stays focused on the
        # senior-investor read.
        from agents.academic_review import (
            build_arbiter_user_message, peer_agent_ids,
        )
        peer_responses = {aid: "ok" for aid in peer_agent_ids()}
        msg = build_arbiter_user_message(
            "CTX", peer_responses, multi_user=True, brief_review=True)
        assert "Team Engagement and Division of Labour" not in msg

    def test_default_rubric_still_used_when_brief_review_false(self):
        # Backward compatibility — every existing caller (script_review
        # / midpoint / no kwargs) keeps its current behaviour.
        from agents.academic_review import (
            build_arbiter_user_message, peer_agent_ids,
        )
        peer_responses = {aid: "ok" for aid in peer_agent_ids()}
        msg = build_arbiter_user_message("CTX", peer_responses)
        # Midpoint rubric content is present, brief content is not.
        assert "ARBITER VERDICT (MIDPOINT CHECK)" in msg
        assert "ARBITER VERDICT (EXECUTIVE BRIEF)" not in msg

    def test_endpoint_routes_brief_query_param_to_arbiter(self, monkeypatch):
        # Verify the SSE endpoint threads document_type=executive_brief
        # through to run_arbiter_with_harness with brief_review=True.
        from agents import academic_review
        captured: dict[str, object] = {}

        def _fake_arbiter(context_block, peer_responses, multi_user,
                          script_review, n_strategies=None,
                          brief_review=False, deck_review=False,
                          appendix_review=False):
            captured["script_review"] = script_review
            captured["brief_review"] = brief_review
            captured["deck_review"] = deck_review
            captured["appendix_review"] = appendix_review
            return "stub verdict"

        monkeypatch.setattr(
            academic_review, "run_arbiter_with_harness", _fake_arbiter)

        # executive_brief → brief_review=True, script_review=False
        r = client.post(
            "/api/council/academic-review"
            "?document_type=executive_brief",
            headers=SESSION_HEADERS)
        assert r.status_code == 200
        assert captured.get("brief_review") is True
        assert captured.get("script_review") is False

        # absent → brief_review=False
        captured.clear()
        r = client.post(
            "/api/council/academic-review", headers=SESSION_HEADERS)
        assert r.status_code == 200
        assert captured.get("brief_review") is False

        # presentation_script → brief_review=False (script wins).
        captured.clear()
        r = client.post(
            "/api/council/academic-review"
            "?document_type=presentation_script",
            headers=SESSION_HEADERS)
        assert r.status_code == 200
        assert captured.get("brief_review") is False
        assert captured.get("script_review") is True


class TestDeckAndAppendixSpecificRubrics:
    """The deck-specific + appendix-specific rubrics extend PR #351
    (brief rubric) to the other two final-deliverable document
    types. Both previously routed through the midpoint rubric and
    landed at the structural 5.5/10 floor the brief used to."""

    def test_deck_arbiter_instructions_six_sections_with_weights(self):
        from agents.academic_review import _ARBITER_INSTRUCTIONS_DECK
        assert "Opening and Central Argument" in _ARBITER_INSTRUCTIONS_DECK
        assert "Analytical Evidence" in _ARBITER_INSTRUCTIONS_DECK
        assert "Economic Storytelling" in _ARBITER_INSTRUCTIONS_DECK
        assert "Live Demo and AI Methodology" in _ARBITER_INSTRUCTIONS_DECK
        assert "Investment Recommendation" in _ARBITER_INSTRUCTIONS_DECK
        assert "Presentation Quality" in _ARBITER_INSTRUCTIONS_DECK
        # Header signals deck mode.
        assert ("ARBITER VERDICT (PRESENTATION DECK)"
                in _ARBITER_INSTRUCTIONS_DECK)
        # The two top-line summary lines (academic + PM).
        assert "**Academic rigour:**" in _ARBITER_INSTRUCTIONS_DECK
        assert "**Portfolio Manager insight:**" in _ARBITER_INSTRUCTIONS_DECK
        # All six section weights spelled out in the headings.
        for w in ("(15%)", "(25%)", "(20%)", "(5%)"):
            assert w in _ARBITER_INSTRUCTIONS_DECK

    def test_appendix_arbiter_instructions_five_sections_with_weights(self):
        from agents.academic_review import _ARBITER_INSTRUCTIONS_APPENDIX
        # Five sections, not six.
        assert ("Data Sources and Methodology"
                in _ARBITER_INSTRUCTIONS_APPENDIX)
        assert ("Portfolio Construction Methodology"
                in _ARBITER_INSTRUCTIONS_APPENDIX)
        assert ("All Calculations and Models"
                in _ARBITER_INSTRUCTIONS_APPENDIX)
        assert ("Performance Metrics and Visualizations"
                in _ARBITER_INSTRUCTIONS_APPENDIX)
        assert ("Sensitivity and Robustness Analysis"
                in _ARBITER_INSTRUCTIONS_APPENDIX)
        # Header signals appendix mode.
        assert ("ARBITER VERDICT (ANALYTICAL APPENDIX)"
                in _ARBITER_INSTRUCTIONS_APPENDIX)
        # All five section weights present in the headings.
        for w in ("(20%)", "(25%)", "(15%)"):
            assert w in _ARBITER_INSTRUCTIONS_APPENDIX

    def test_deck_prohibited_patterns_listed(self):
        from agents.academic_review import _ARBITER_INSTRUCTIONS_DECK
        # Brief-style prohibitions carry over.
        assert ("Further research would benefit from"
                in _ARBITER_INSTRUCTIONS_DECK)
        assert "Roles and division of labor" in _ARBITER_INSTRUCTIONS_DECK
        # Deck-specific: promotional AI language flagged.
        assert "Promotional AI language" in _ARBITER_INSTRUCTIONS_DECK
        # PM_CRITERION + harness-table detector pattern verbatim.
        assert "PM_CRITERION" in _ARBITER_INSTRUCTIONS_DECK
        assert "Prior Issue" in _ARBITER_INSTRUCTIONS_DECK

    def test_appendix_prohibited_patterns_listed(self):
        from agents.academic_review import _ARBITER_INSTRUCTIONS_APPENDIX
        # Appendix-specific prohibitions: traceability and APA.
        assert ("Figures not traceable to the data hash"
                in _ARBITER_INSTRUCTIONS_APPENDIX)
        assert ("Charts without figure numbers or APA notes"
                in _ARBITER_INSTRUCTIONS_APPENDIX)
        assert ("Sharpe ratios cited without study period definition"
                in _ARBITER_INSTRUCTIONS_APPENDIX)
        # Section 4 specifically references APA figure numbers.
        assert "APA figure numbers" in _ARBITER_INSTRUCTIONS_APPENDIX

    def test_editor_to_review_type_routes_deck_and_appendix(self):
        from agents.academic_review import _EDITOR_TO_REVIEW_TYPE
        assert _EDITOR_TO_REVIEW_TYPE["presentation_deck"] == "deck_review"
        assert (_EDITOR_TO_REVIEW_TYPE["analytical_appendix"]
                == "appendix_review")

    def test_build_arbiter_message_deck_mode_uses_deck_instructions(self):
        from agents.academic_review import (
            build_arbiter_user_message, peer_agent_ids,
            _ARBITER_INSTRUCTIONS_DECK,
        )
        peer_responses = {aid: "ok" for aid in peer_agent_ids()}
        msg = build_arbiter_user_message(
            "CTX", peer_responses, deck_review=True)
        assert "ARBITER VERDICT (PRESENTATION DECK)" in msg
        assert _ARBITER_INSTRUCTIONS_DECK in msg
        # Brief / midpoint / appendix rubrics NOT present.
        assert "ARBITER VERDICT (EXECUTIVE BRIEF)" not in msg
        assert "ARBITER VERDICT (MIDPOINT CHECK)" not in msg
        assert "ARBITER VERDICT (ANALYTICAL APPENDIX)" not in msg

    def test_build_arbiter_message_appendix_mode_uses_appendix(self):
        from agents.academic_review import (
            build_arbiter_user_message, peer_agent_ids,
            _ARBITER_INSTRUCTIONS_APPENDIX,
        )
        peer_responses = {aid: "ok" for aid in peer_agent_ids()}
        msg = build_arbiter_user_message(
            "CTX", peer_responses, appendix_review=True)
        assert "ARBITER VERDICT (ANALYTICAL APPENDIX)" in msg
        assert _ARBITER_INSTRUCTIONS_APPENDIX in msg
        assert "ARBITER VERDICT (PRESENTATION DECK)" not in msg
        assert "ARBITER VERDICT (EXECUTIVE BRIEF)" not in msg
        assert "ARBITER VERDICT (MIDPOINT CHECK)" not in msg

    def test_deck_and_appendix_ignore_multi_user_section_6(self):
        # Section 6 (Team Engagement) is midpoint-only.
        from agents.academic_review import (
            build_arbiter_user_message, peer_agent_ids,
        )
        peer_responses = {aid: "ok" for aid in peer_agent_ids()}
        deck_msg = build_arbiter_user_message(
            "CTX", peer_responses, multi_user=True, deck_review=True)
        assert "Team Engagement and Division of Labour" not in deck_msg
        appx_msg = build_arbiter_user_message(
            "CTX", peer_responses, multi_user=True, appendix_review=True)
        assert "Team Engagement and Division of Labour" not in appx_msg

    def test_endpoint_routes_deck_and_appendix(self, monkeypatch):
        # Verify the SSE endpoint threads document_type=presentation_
        # deck / analytical_appendix through to run_arbiter_with_harness
        # with the matching flag set.
        from agents import academic_review
        captured: dict[str, object] = {}

        def _fake_arbiter(context_block, peer_responses, multi_user,
                          script_review, n_strategies=None,
                          brief_review=False, deck_review=False,
                          appendix_review=False):
            captured["brief_review"] = brief_review
            captured["deck_review"] = deck_review
            captured["appendix_review"] = appendix_review
            return "stub verdict"

        monkeypatch.setattr(
            academic_review, "run_arbiter_with_harness", _fake_arbiter)

        # presentation_deck -> deck_review=True
        r = client.post(
            "/api/council/academic-review"
            "?document_type=presentation_deck",
            headers=SESSION_HEADERS)
        assert r.status_code == 200
        assert captured.get("deck_review") is True
        assert captured.get("brief_review") is False
        assert captured.get("appendix_review") is False

        # analytical_appendix -> appendix_review=True
        captured.clear()
        r = client.post(
            "/api/council/academic-review"
            "?document_type=analytical_appendix",
            headers=SESSION_HEADERS)
        assert r.status_code == 200
        assert captured.get("appendix_review") is True
        assert captured.get("brief_review") is False
        assert captured.get("deck_review") is False
