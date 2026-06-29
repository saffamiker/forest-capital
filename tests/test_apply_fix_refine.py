"""tests/test_apply_fix_refine.py -- June 27 2026.

Pins the multi-round iterative refinement endpoint
(/api/v1/apply-fix/refine) + the apply-fix patch-instruction
substitution when refined_proposal_text is supplied.

Key contracts:
  * Refine endpoint is a stateless text-rewrite -- never reads /
    writes editor_drafts / council_debates / story_plans.
  * refinement_note capped at 500 chars (matches frontend textarea
    maxLength + the user spec).
  * current_proposal_text capped at REFINEMENT_PROPOSAL_MAX_CHARS
    (4000) defensively.
  * 422 on missing required fields; 413 on oversized inputs.
  * ENV=test returns a deterministic stub so the endpoint shape is
    exercised without a live Sonnet call.
  * post_apply_fix's source uses refined_proposal_text when present
    so the surgical splice runs against the refined text instead of
    the stored patch_instruction.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


os.environ.setdefault("ENVIRONMENT", "test")


# Auth headers -- team-member email per the existing test_audit
# convention. The refine endpoint requires the team_member
# permission tier.
import sys
sys.path.insert(0, "backend")
from auth import generate_session_token  # noqa: E402

_TEAM = {"X-API-Key": generate_session_token("thaob@queens.edu")}


# ── Constants + helper shape ────────────────────────────────────────


class TestRefineConstants:

    def test_refinement_note_cap_is_500(self):
        from main import REFINEMENT_NOTE_MAX_CHARS
        assert REFINEMENT_NOTE_MAX_CHARS == 500

    def test_proposal_text_cap_is_4000(self):
        from main import REFINEMENT_PROPOSAL_MAX_CHARS
        assert REFINEMENT_PROPOSAL_MAX_CHARS == 4000

    def test_endpoint_registered_on_app(self):
        from main import app
        routes = {r.path for r in app.routes}
        assert "/api/v1/apply-fix/refine" in routes


# ── Endpoint behaviour ──────────────────────────────────────────────


def _client() -> TestClient:
    from main import app
    return TestClient(app)


def _refine_body(**overrides) -> dict:
    base = {
        "fix_proposal_id": 42,
        "current_proposal_text": (
            "Add a sentence noting that drawdowns "
            "are reported gross of fees."),
        "refinement_note": (
            "Make the citation match Table B.1 specifically."),
        "document_type": "analytical_appendix",
        "section_name": "Section B",
        "refinement_round": 1,
    }
    base.update(overrides)
    return base


class TestRefineEndpoint:

    def test_test_env_returns_deterministic_stub(self):
        c = _client()
        r = c.post(
            "/api/v1/apply-fix/refine",
            json=_refine_body(),
            headers=_TEAM)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "refined_proposal_text" in data
        # Stub format: original + [refined: note]
        text = data["refined_proposal_text"]
        assert "drawdowns are reported gross of fees" in text
        assert "[refined: Make the citation match Table B.1" in text

    def test_missing_fix_proposal_id_returns_422(self):
        c = _client()
        body = _refine_body()
        body.pop("fix_proposal_id")
        r = c.post(
            "/api/v1/apply-fix/refine", json=body,
            headers=_TEAM)
        assert r.status_code == 422
        assert "fix_proposal_id" in r.json()["detail"]

    def test_missing_current_proposal_text_returns_422(self):
        c = _client()
        body = _refine_body(current_proposal_text="")
        r = c.post(
            "/api/v1/apply-fix/refine", json=body,
            headers=_TEAM)
        assert r.status_code == 422
        assert "current_proposal_text" in r.json()["detail"]

    def test_missing_refinement_note_returns_422(self):
        c = _client()
        body = _refine_body(refinement_note="")
        r = c.post(
            "/api/v1/apply-fix/refine", json=body,
            headers=_TEAM)
        assert r.status_code == 422
        assert "refinement_note" in r.json()["detail"]

    def test_oversized_refinement_note_returns_413(self):
        from main import REFINEMENT_NOTE_MAX_CHARS
        c = _client()
        body = _refine_body(
            refinement_note="X" * (REFINEMENT_NOTE_MAX_CHARS + 1))
        r = c.post(
            "/api/v1/apply-fix/refine", json=body,
            headers=_TEAM)
        assert r.status_code == 413
        assert "refinement_note" in r.json()["detail"]
        assert str(REFINEMENT_NOTE_MAX_CHARS) in r.json()["detail"]

    def test_oversized_proposal_text_returns_413(self):
        from main import REFINEMENT_PROPOSAL_MAX_CHARS
        c = _client()
        body = _refine_body(
            current_proposal_text=(
                "X" * (REFINEMENT_PROPOSAL_MAX_CHARS + 1)))
        r = c.post(
            "/api/v1/apply-fix/refine", json=body,
            headers=_TEAM)
        assert r.status_code == 413
        assert "current_proposal_text" in r.json()["detail"]
        assert str(REFINEMENT_PROPOSAL_MAX_CHARS) in (
            r.json()["detail"])

    def test_max_chars_boundary_is_accepted(self):
        """Exactly REFINEMENT_NOTE_MAX_CHARS chars is accepted; 1
        more is the 413. Boundary pinning."""
        from main import REFINEMENT_NOTE_MAX_CHARS
        c = _client()
        body = _refine_body(
            refinement_note="X" * REFINEMENT_NOTE_MAX_CHARS)
        r = c.post(
            "/api/v1/apply-fix/refine", json=body,
            headers=_TEAM)
        assert r.status_code == 200, r.text


# ── Refine endpoint is stateless ────────────────────────────────────


class TestRefineEndpointIsStateless:
    """Pin via source inspection that the refine endpoint never
    touches editor_drafts / council_debates / story_plans. A
    regression that adds a write would corrupt the contract."""

    def test_source_does_not_write_editor_drafts(self):
        import inspect
        import re as _re
        from main import post_apply_fix_refine
        src = inspect.getsource(post_apply_fix_refine)
        # Strip comments + docstrings before grepping so a comment
        # mentioning 'editor_drafts' (e.g. 'never touches
        # editor_drafts') doesn't trip the check.
        no_comments = _re.sub(r"(?m)^\s*#.*$", "", src)
        no_doc = _re.sub(
            r'"""[\s\S]*?"""', "", no_comments)
        # The endpoint MUST NOT call any update_draft / create_draft
        # helper OR import from tools.editor_drafts.
        for forbidden in (
            "update_draft(", "create_draft(",
            "from tools.editor_drafts",
        ):
            assert forbidden not in no_doc, (
                f"refine endpoint must not touch {forbidden} "
                f"(found in source)")

    def test_source_does_not_write_council_debates(self):
        import inspect
        from main import post_apply_fix_refine
        src = inspect.getsource(post_apply_fix_refine)
        # No UPDATE / INSERT against council_debates.
        for forbidden in (
            "UPDATE council_debates",
            "INSERT INTO council_debates",
            "council_debates SET",
        ):
            assert forbidden not in src

    def test_source_does_not_write_story_plans(self):
        import inspect
        from main import post_apply_fix_refine
        src = inspect.getsource(post_apply_fix_refine)
        for forbidden in (
            "UPDATE story_plans",
            "INSERT INTO story_plans",
            "story_plans SET",
        ):
            assert forbidden not in src


# ── apply-fix REJECTS refined_proposal_text (diff-preview mandate) ──


class TestApplyFixRejectsRefinedProposalText:
    """Per the diff-preview mandate, refined patch text MUST flow
    through /propose-fix-text so the user sees the diff before
    any write. Sending refined_proposal_text to /apply-fix is a
    422 -- prevents a frontend regression from creating a direct-
    commit path that skips the diff."""

    def test_apply_fix_with_refined_proposal_text_returns_422(self):
        c = _client()
        r = c.post(
            "/api/v1/documents/apply-fix",
            json={
                "document_type": "analytical_appendix",
                "finding_id": 42,
                "fix_proposal": {
                    "target": "section",
                    "section_name": "Section B",
                    "patch_instruction": "Tighten the wording.",
                },
                "refined_proposal_text": (
                    "REFINED text that should be rejected"),
                "debate_id": 1,
                "confirmed": True,
            },
            headers=_TEAM)
        assert r.status_code == 422
        assert "refined_proposal_text" in r.json()["detail"]
        assert "/propose-fix-text" in r.json()["detail"]

    def test_apply_fix_without_refined_field_still_works(self):
        """Sanity: the rejection branch only fires when
        refined_proposal_text is a non-empty string. Legacy callers
        with no field (or null / empty) are unaffected."""
        c = _client()
        r = c.post(
            "/api/v1/documents/apply-fix",
            json={
                "document_type": "analytical_appendix",
                "finding_id": 42,
                "fix_proposal": {
                    "target": "section",
                    "section_name": "Section B",
                    "patch_instruction": "Tighten the wording.",
                },
                "debate_id": 1,
                "confirmed": True,
            },
            headers=_TEAM)
        # ENV=test short-circuits the apply-fix body so this is a
        # 200 with a stub response.
        assert r.status_code == 200, r.text


# ── propose-fix-text ACCEPTS refined_proposal_text ─────────────────


class TestProposeFixTextAcceptsRefinedProposalText:
    """The mandatory diff-preview path. propose-fix-text takes the
    refined text on the body and uses it as the patch instruction
    for the section Sonnet call; the result lands in the diff
    panel the user sees BEFORE clicking Accept.

    Source inspection only -- the actual splice path is exercised
    by test_apply_fix_inline_splice.py (PR #450). This pins the
    substitution wiring + the cache-bypass on refined runs."""

    def test_post_propose_fix_text_source_reads_refined_proposal_text(
            self):
        import inspect
        from main import post_propose_fix_text
        src = inspect.getsource(post_propose_fix_text)
        assert "refined_proposal_text" in src
        assert "patch_instruction = refined_proposal_text" in src

    def test_post_propose_fix_text_skips_cache_on_refined_runs(self):
        """Refined inputs MUST recompute -- the cached preview was
        computed against the ORIGINAL stored patch_instruction."""
        import inspect
        from main import post_propose_fix_text
        src = inspect.getsource(post_propose_fix_text)
        assert "refined_proposal_text is None" in src

    def test_post_propose_fix_text_logs_refined_flag(self):
        import inspect
        from main import post_propose_fix_text
        src = inspect.getsource(post_propose_fix_text)
        assert "refined=bool(refined_proposal_text)" in src
        assert "refinement_chars" in src
        assert "propose_fix_text_invoked" in src
