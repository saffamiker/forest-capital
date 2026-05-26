"""tests/test_citation_review.py — citation review workflow.

May 23 2026 (item 1 — full citation review workflow).

Covers the 7-state machine + 3-pass search additions in
tools/template_pipeline.py and the two reviewer endpoints in
main.py.

The DB-touching paths (persist + read + review-apply) are
exercised against the fail-open contract; the actual SQL is
exercised by the integration suite that runs against a live
Postgres in CI. Endpoint gating tests use the TestClient and
verify the auth contract without touching the database.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

# The fail-open / mock paths key off ENVIRONMENT=test; mirror the
# pattern used in test_template_pipeline.py so the import-time
# decisions inside source_citations resolve to the test path
# regardless of how pytest was invoked.
os.environ.setdefault("ENVIRONMENT", "test")


from tools import template_pipeline as tp  # noqa: E402


# ── 7-state constants ────────────────────────────────────────────────────────


class TestCitationStateMachine:
    """The 7 states are the spec — drift here breaks every downstream
    pipeline check + the frontend badge palette + the docx writer's
    inline-citation policy."""

    def test_seven_canonical_states_exist(self):
        # Drift-protection: every state the spec names must be a
        # module-level constant. A future change that renames or
        # removes a state must update this test deliberately.
        expected = {
            "not_found",
            "pending_review",
            "verified",
            "human_verified",
            "search_selected",
            "manually_added",
            "rejected",
        }
        actual = {
            tp.CITATION_STATE_NOT_FOUND,
            tp.CITATION_STATE_PENDING_REVIEW,
            tp.CITATION_STATE_VERIFIED,
            tp.CITATION_STATE_HUMAN_VERIFIED,
            tp.CITATION_STATE_SEARCH_SELECTED,
            tp.CITATION_STATE_MANUALLY_ADDED,
            tp.CITATION_STATE_REJECTED,
        }
        assert actual == expected

    def test_verified_states_set_carries_four_passing_states(self):
        # Citation quality and post_check_citations both gate on this
        # set — every state in it is treated as a real citation.
        assert tp.CITATION_VERIFIED_STATES == frozenset({
            "verified",
            "human_verified",
            "search_selected",
            "manually_added",
        })

    def test_needs_review_states_include_legacy_alias(self):
        # Rows written by the pre-review-workflow code path used
        # "untrusted_source"; the new pipeline writes "pending_review".
        # The needs-review set must accept BOTH so a paper generated
        # under the old code path still surfaces in the review panel.
        assert "untrusted_source" in tp.CITATION_NEEDS_REVIEW_STATES
        assert "pending_review" in tp.CITATION_NEEDS_REVIEW_STATES
        assert "not_found" in tp.CITATION_NEEDS_REVIEW_STATES

    def test_review_actions_are_pinned(self):
        # The endpoint validates incoming actions against this set.
        # A drift change must update the frontend's button labels too.
        assert tp.CITATION_REVIEW_ACTIONS == frozenset({
            "accept_untrusted",
            "select_alternative",
            "reject",
            "manual_add",
        })


# ── Domain classification ────────────────────────────────────────────────────


class TestDomainClassification:
    """Three-pass search relies on three nested domain lists. The
    classification functions must agree on which URL falls into
    which pass."""

    def test_trusted_urls_are_pass_1(self):
        assert tp._is_trusted_url("https://www.nber.org/papers/w29845")
        assert tp._is_trusted_url("https://www.aqr.com/research/123")
        # JSTOR
        assert tp._is_trusted_url("https://www.jstor.org/stable/2329297")
        # NOT trusted
        assert not tp._is_trusted_url("https://www.harvard.edu/paper")
        assert not tp._is_trusted_url("")

    def test_academic_urls_are_pass_2_not_pass_1(self):
        # Pass 2 catches .edu, regional Feds, sec.gov, publishing
        # houses — explicitly NOT the pass-1 trusted set.
        assert tp._is_academic_url("https://www.stlouisfed.org/x.pdf")
        assert tp._is_academic_url("https://www.sec.gov/news/y")
        assert tp._is_academic_url("https://stanford.edu/faculty/paper")
        # Trusted URLs are NOT also academic — exclusivity matters
        # because the caller treats the two classes differently.
        assert not tp._is_academic_url("https://www.nber.org/papers/w1")
        assert not tp._is_academic_url("")

    def test_publishable_urls_are_pass_3(self):
        # Pass 3 catches anything on .org / .gov / .edu / .int that
        # isn't a banned domain.
        assert tp._is_publishable_url("https://imf.org/research/x.html")
        assert tp._is_publishable_url("https://un.int/doc/y")
        # Banned domains never pass at any level.
        assert not tp._is_publishable_url(
            "https://investopedia.com/articles/x")
        assert not tp._is_publishable_url(
            "https://en.wikipedia.org/wiki/Sharpe_ratio")


# ── 3-pass search orchestration ──────────────────────────────────────────────


class TestThreePassSearch:
    """source_citations runs pass 1, then 2 if 1 missed, then 3 if
    2 missed. Each pass returns a JSON-parsable citation or None."""

    def test_pass_1_trusted_hit_stops_at_pass_1(self, monkeypatch):
        # A trusted-domain hit on pass 1 should NOT trigger pass 2 or 3.
        passes_called: list[int] = []

        def _fake_run(call_fn, model, *, query, concept_id, pass_index,
                      prior_types=None):
            passes_called.append(pass_index)
            if pass_index == 1:
                return {
                    "author": "Sharpe, W. F.",
                    "year": "1994",
                    "title": "The Sharpe Ratio",
                    "journal_or_institution":
                        "Journal of Portfolio Management",
                    "volume_issue_pages": "21(1), 49-58",
                    "url": "https://www.jstor.org/stable/jpm.21.1.49",
                    "verification_status": tp.CITATION_STATE_VERIFIED,
                }
            return None

        monkeypatch.setattr(tp, "_run_citation_pass", _fake_run)
        # Force the live path by unsetting the test-env flag and
        # mocking the agent import.
        monkeypatch.setenv("ENVIRONMENT", "development")
        from agents import base as agents_base
        monkeypatch.setattr(
            agents_base, "call_claude",
            lambda **kwargs: "{}", raising=False)

        out = asyncio.run(tp.source_citations([
            {"concept_id": "sharpe", "search_query": "sharpe ratio"},
        ]))
        assert out["sharpe"]["verification_status"] == tp.CITATION_STATE_VERIFIED
        assert out["sharpe"]["passes_run"] == 1
        assert passes_called == [1]
        # Restore env.
        monkeypatch.setenv("ENVIRONMENT", "test")

    def test_pass_3_hit_stored_as_pending_review(self, monkeypatch):
        # Pass 1 and 2 both miss; pass 3 hits with a .gov URL.
        # The primary entry is pending_review; pass-1 and pass-2 misses
        # are tracked in alternatives.
        def _fake_run(call_fn, model, *, query, concept_id, pass_index,
                      prior_types=None):
            if pass_index == 3:
                return {
                    "author": "Smith, J.",
                    "year": "2024",
                    "title": "Macro Conditions",
                    "journal_or_institution": "Treasury Brief",
                    "volume_issue_pages": "",
                    "url": "https://treasury.gov/papers/x.pdf",
                    "verification_status":
                        tp.CITATION_STATE_PENDING_REVIEW,
                }
            return None

        monkeypatch.setattr(tp, "_run_citation_pass", _fake_run)
        monkeypatch.setenv("ENVIRONMENT", "development")
        from agents import base as agents_base
        monkeypatch.setattr(
            agents_base, "call_claude",
            lambda **kwargs: "{}", raising=False)

        out = asyncio.run(tp.source_citations([
            {"concept_id": "macro", "search_query": "macro briefing"},
        ]))
        # Pass 3 hit became the primary — but treasury.gov isn't on
        # _ACADEMIC_DOMAINS so it falls through to the publishable
        # check.
        assert out["macro"]["verification_status"] == tp.CITATION_STATE_PENDING_REVIEW
        assert out["macro"]["passes_run"] == 3
        monkeypatch.setenv("ENVIRONMENT", "test")

    def test_all_passes_miss_is_not_found(self, monkeypatch):
        # No pass returns a hit — the entry stays not_found.
        monkeypatch.setattr(
            tp, "_run_citation_pass",
            lambda *a, **kw: None)
        monkeypatch.setenv("ENVIRONMENT", "development")
        from agents import base as agents_base
        monkeypatch.setattr(
            agents_base, "call_claude",
            lambda **kwargs: "{}", raising=False)

        out = asyncio.run(tp.source_citations([
            {"concept_id": "mystery", "search_query": "no such concept"},
        ]))
        assert out["mystery"]["verification_status"] == tp.CITATION_STATE_NOT_FOUND
        assert out["mystery"]["passes_run"] == 3
        assert out["mystery"]["alternatives"] == []
        monkeypatch.setenv("ENVIRONMENT", "test")


# ── Sourcing prompt — citation type diversity (May 26 2026) ─────────────────


class TestSourcingPromptTaxonomy:
    """The sourcing prompt was returning every citation as
    'theoretical' because it never asked the LLM to classify type
    OR to search broadly across the six-type taxonomy. These tests
    pin the prompt's new contract: it must name every type, ask
    for the citation_type field in the JSON response, and inject a
    diversity steer when prior passes have already returned types."""

    def test_taxonomy_block_lists_all_six_types(self):
        # All six types named verbatim so the model has a single
        # decision-ready definition for each. A typo or dropped type
        # would silently bias the model's output.
        block = tp._CITATION_TYPE_TAXONOMY
        for t in ("theoretical", "empirical", "methodological",
                  "regulatory", "data_source", "practitioner"):
            assert t in block, f"taxonomy block missing {t!r}"

    def test_taxonomy_block_has_concrete_examples(self):
        # The model needs CONCRETE source examples per type, not
        # abstract definitions. Verify each example category is
        # represented so the LLM can pattern-match.
        block = tp._CITATION_TYPE_TAXONOMY
        # data_source: name the canonical FRED / index providers
        assert "fred.stlouisfed.org" in block
        # regulatory: SEC / Basel III / ESMA
        assert "SEC" in block and "Basel" in block
        # empirical: NBER working papers
        assert "NBER" in block
        # methodological: the canonical CV reference
        assert "López de Prado" in block or "Lopez de Prado" in block
        # practitioner: a named institutional manager
        assert "AQR" in block

    def test_diversity_steer_empty_on_pass_1(self):
        # Pass 1 has no prior context — the steer must be empty so
        # the prompt doesn't carry a meaningless "previous passes
        # returned: []" instruction.
        assert tp._build_diversity_instruction([]) == ""

    def test_diversity_steer_lists_prior_types(self):
        steer = tp._build_diversity_instruction(
            ["theoretical", "empirical"])
        assert "theoretical" in steer
        assert "empirical" in steer
        assert "DIFFERENT type" in steer

    def test_diversity_steer_deduplicates_prior_types(self):
        # If both prior passes returned 'theoretical' we should
        # only mention it once in the steer.
        steer = tp._build_diversity_instruction(
            ["theoretical", "theoretical"])
        # 'theoretical' appears exactly once in the list portion
        # (the prompt itself uses the word once more in the steer
        # body — that's expected). Count occurrences in the bracket.
        bracketed = steer.split("[", 1)[1].split("]", 1)[0]
        assert bracketed.count("theoretical") == 1


class TestRunCitationPassPrompt:
    """The system prompt assembled by _run_citation_pass must carry
    the type taxonomy AND require citation_type in the JSON. These
    tests exercise the prompt assembly by capturing the system_prompt
    arg passed to call_claude_fn."""

    def _capture_prompt(self, monkeypatch, prior_types=None):
        """Calls _run_citation_pass with a stub call_claude that
        captures the system prompt for inspection. Returns the
        captured prompt string (or None if the call did not fire)."""
        captured = {}
        def _stub(**kwargs):
            captured["system_prompt"] = kwargs.get("system_prompt")
            return '{"unverified": true}'
        tp._run_citation_pass(
            _stub, "claude-sonnet-4-6",
            query="test query", concept_id="test_concept",
            pass_index=1, prior_types=prior_types)
        return captured.get("system_prompt")

    def test_prompt_includes_taxonomy(self, monkeypatch):
        prompt = self._capture_prompt(monkeypatch)
        assert prompt is not None
        assert "CITATION TYPES" in prompt
        assert "data_source" in prompt

    def test_prompt_requires_citation_type_field(self, monkeypatch):
        prompt = self._capture_prompt(monkeypatch)
        assert prompt is not None
        assert "citation_type" in prompt
        # The instruction must require one of the six values, not
        # leave the LLM free to invent its own.
        assert "one of the six values" in prompt

    def test_prompt_carries_diversity_steer_when_prior_types_set(self, monkeypatch):
        prompt = self._capture_prompt(
            monkeypatch, prior_types=["theoretical"])
        assert prompt is not None
        assert "DIVERSITY STEER" in prompt
        assert "theoretical" in prompt

    def test_prompt_no_diversity_steer_on_pass_1(self, monkeypatch):
        # Default prior_types=None on pass 1; the steer must be
        # absent. (The taxonomy block is still present; only the
        # "previous passes returned" sentence is gated.)
        prompt = self._capture_prompt(monkeypatch, prior_types=None)
        assert prompt is not None
        assert "DIVERSITY STEER" not in prompt


class TestCitationTypeValidation:
    """The parser must validate the LLM's citation_type against the
    canonical set. An unknown value collapses to 'theoretical' (the
    same default the persistence layer applies for missing values)
    but is logged so a model regression is visible."""

    def _stub_call(self, raw_response):
        def _stub(**kwargs):
            return raw_response
        return _stub

    def test_valid_data_source_type_preserved(self):
        raw = (
            '{"author":"Federal Reserve Bank of St. Louis",'
            '"year":"2025","title":"BAMLH0A0HYM2",'
            '"journal_or_institution":"FRED Economic Data",'
            '"volume_issue_pages":null,'
            '"url":"https://fred.stlouisfed.org/series/BAMLH0A0HYM2",'
            '"citation_type":"data_source",'
            '"supporting_extract":"Series description.",'
            '"selection_rationale":"Primary FRED source.",'
            '"finding_supported":"HY spread series."}'
        )
        result = tp._run_citation_pass(
            self._stub_call(raw), "claude-sonnet-4-6",
            query="hy spread", concept_id="hy_spread", pass_index=1)
        assert result is not None
        assert result["citation_type"] == "data_source"

    def test_unknown_type_falls_back_to_theoretical(self):
        raw = (
            '{"author":"X","year":"2024","title":"T","url":"https://nber.org/x",'
            '"citation_type":"made_up_type",'
            '"supporting_extract":"e","selection_rationale":"r","finding_supported":"f"}'
        )
        result = tp._run_citation_pass(
            self._stub_call(raw), "claude-sonnet-4-6",
            query="q", concept_id="c", pass_index=1)
        assert result is not None
        assert result["citation_type"] == "theoretical"

    def test_missing_type_defaults_to_theoretical(self):
        # No citation_type field at all — preserve backward compat
        # with anything pre-redesign and the documented persistence
        # default.
        raw = (
            '{"author":"X","year":"2024","title":"T","url":"https://nber.org/x",'
            '"supporting_extract":"e","selection_rationale":"r","finding_supported":"f"}'
        )
        result = tp._run_citation_pass(
            self._stub_call(raw), "claude-sonnet-4-6",
            query="q", concept_id="c", pass_index=1)
        assert result is not None
        assert result["citation_type"] == "theoretical"

    def test_case_insensitive_normalization(self):
        raw = (
            '{"author":"X","year":"2024","title":"T","url":"https://sec.gov/x",'
            '"citation_type":"REGULATORY",'
            '"supporting_extract":"e","selection_rationale":"r","finding_supported":"f"}'
        )
        result = tp._run_citation_pass(
            self._stub_call(raw), "claude-sonnet-4-6",
            query="q", concept_id="c", pass_index=1)
        assert result is not None
        assert result["citation_type"] == "regulatory"


# ── citation_quality counts every verified bucket ────────────────────────────


class TestCitationQuality:
    def test_counts_all_verified_states_not_just_auto(self):
        # 8 citations across the four verified states + 2 not_found
        # → green (>= 8).
        cits = {
            "a": {"verification_status": tp.CITATION_STATE_VERIFIED},
            "b": {"verification_status": tp.CITATION_STATE_VERIFIED},
            "c": {"verification_status": tp.CITATION_STATE_HUMAN_VERIFIED},
            "d": {"verification_status": tp.CITATION_STATE_HUMAN_VERIFIED},
            "e": {"verification_status": tp.CITATION_STATE_SEARCH_SELECTED},
            "f": {"verification_status": tp.CITATION_STATE_SEARCH_SELECTED},
            "g": {"verification_status": tp.CITATION_STATE_MANUALLY_ADDED},
            "h": {"verification_status": tp.CITATION_STATE_MANUALLY_ADDED},
            "i": {"verification_status": tp.CITATION_STATE_NOT_FOUND},
            "j": {"verification_status": tp.CITATION_STATE_REJECTED},
        }
        assert tp.citation_quality(cits) == "green"

    def test_pending_review_does_not_count_as_verified(self):
        cits = {f"c{i}": {"verification_status": tp.CITATION_STATE_PENDING_REVIEW}
                for i in range(10)}
        assert tp.citation_quality(cits) == "red"

    def test_rejected_does_not_count_as_verified(self):
        cits = {f"c{i}": {"verification_status": tp.CITATION_STATE_REJECTED}
                for i in range(10)}
        assert tp.citation_quality(cits) == "red"


# ── apply_citation_review action gating ──────────────────────────────────────


class TestApplyReviewActionGating:
    """The four actions each have a specific payload + state
    transition. Unknown actions and missing payloads are refused
    before any database write — these tests run without a DB."""

    def test_unknown_action_returns_none(self, monkeypatch):
        out = asyncio.run(tp.apply_citation_review(
            1, "wat", "x@y.z"))
        assert out is None

    def test_select_alternative_without_payload_returns_none(self, monkeypatch):
        out = asyncio.run(tp.apply_citation_review(
            1, "select_alternative", "x@y.z"))
        assert out is None

    def test_manual_add_without_payload_returns_none(self, monkeypatch):
        out = asyncio.run(tp.apply_citation_review(
            1, "manual_add", "x@y.z"))
        assert out is None

    def test_no_db_returns_none_fail_open(self, monkeypatch):
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        out = asyncio.run(tp.apply_citation_review(
            1, "accept_untrusted", "x@y.z"))
        assert out is None


# ── endpoint gating ──────────────────────────────────────────────────────────


class TestCitationEndpoints:
    """The two endpoints both require team_member. Unauthenticated
    is a 401; a viewer-tier session is a 403."""

    def _client(self):
        from fastapi.testclient import TestClient
        from main import app
        return TestClient(app)

    def test_get_citations_unauthenticated_is_401(self):
        c = self._client()
        r = c.get("/api/v1/citations/123")
        assert r.status_code == 401

    def test_post_review_unauthenticated_is_401(self):
        c = self._client()
        r = c.post("/api/v1/citations/123/review",
                   json={"action": "reject"})
        assert r.status_code == 401

    def test_post_review_unknown_action_is_422(self, monkeypatch):
        # Mock auth with a team member token so we reach the
        # action-validation branch.
        from auth import require_team_member
        from main import app
        async def _fake_team(): return {"email": "bob@queens.edu",
                                         "permissions": ["team_member"]}
        app.dependency_overrides[require_team_member] = _fake_team
        try:
            c = self._client()
            r = c.post("/api/v1/citations/123/review",
                       json={"action": "made_up_action"})
            assert r.status_code == 422
            assert "Unknown action" in r.json()["detail"]
        finally:
            app.dependency_overrides.pop(require_team_member, None)

    def test_post_review_missing_action_is_422(self):
        from auth import require_team_member
        from main import app
        async def _fake_team(): return {"email": "bob@queens.edu",
                                         "permissions": ["team_member"]}
        app.dependency_overrides[require_team_member] = _fake_team
        try:
            c = self._client()
            r = c.post("/api/v1/citations/123/review", json={})
            assert r.status_code == 422
            assert "action is required" in r.json()["detail"]
        finally:
            app.dependency_overrides.pop(require_team_member, None)


# ── test-env shape contract ──────────────────────────────────────────────────


class TestTestEnvFailOpen:
    def test_test_env_returns_not_found_for_every_concept(self):
        # ENVIRONMENT=test is the default in conftest; the live agent
        # path is bypassed and every concept comes back not_found.
        out = asyncio.run(tp.source_citations([
            {"concept_id": "a", "search_query": "q1"},
            {"concept_id": "b", "search_query": "q2"},
        ]))
        assert out["a"]["verification_status"] == tp.CITATION_STATE_NOT_FOUND
        assert out["b"]["verification_status"] == tp.CITATION_STATE_NOT_FOUND
        # The shape carries the new alternatives + passes_run fields.
        assert out["a"]["alternatives"] == []
        assert out["a"]["passes_run"] == 0
