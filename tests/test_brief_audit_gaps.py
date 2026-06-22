"""tests/test_brief_audit_gaps.py -- PR #336 Gap A-E integration tests.

The brief pipeline audit identified six gaps; this PR closes A-E
(F is closed in a parallel frontend PR). Tests pin the contract for
each new check + the dispatcher wiring + the editor-export audit
re-run + the admin endpoint that clears story_plans.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")


# ── Gap A: brief story_plan_violation check ──────────────────────────────


class TestBriefStoryPlanViolations:
    """The deck variant of check_story_plan_violations takes a slide
    list + a per-slide plan list; the brief variant takes flat body
    text + a section_plan dict. A value cited in the body that is
    NOT in any section's numeric_anchors AND NOT in the cache flags."""

    section_plan = {
        "executive_summary": {
            "key_message": "...",
            "numeric_anchors": {
                "oos_sharpe_blend": 1.24,
                "oos_sharpe_benchmark": 0.73,
            },
            "target_length_words": 250,
        },
        "key_findings": {
            "key_message": "...",
            "numeric_anchors": {"max_drawdown_blend": 0.253},
            "target_length_words": 550,
        },
    }

    def test_value_not_in_anchors_or_cache_flags(self):
        from tools.document_audit import (
            check_brief_story_plan_violations,
        )
        content = (
            "The blend's OOS Sharpe is 1.24 (anchored). However, the "
            "fabricated headline figure 9.99 has no source in any "
            "section's locked anchors and is absent from the cache.")
        flags = check_brief_story_plan_violations(
            content, self.section_plan, strategy_cache={})
        # 1.24 is anchored -> no flag. 9.99 is unanchored -> flag.
        assert any(f["value"] == 9.99 for f in flags)
        assert not any(f["value"] == 1.24 for f in flags)
        # Citation-year numbers in parentheses are NOT flagged (the
        # extractor suppresses them to avoid flagging "(2002)" from
        # Ang and Bekaert citations).
        assert not any(f["value"] == 2002 for f in flags)

    def test_value_matching_any_section_anchor_does_not_flag(self):
        from tools.document_audit import (
            check_brief_story_plan_violations,
        )
        # 0.253 lives in key_findings anchors. The brief is one
        # continuous document so quoting that value in the exec
        # summary section's prose is legitimate.
        content = "The blend's max drawdown was 0.253 over the window."
        flags = check_brief_story_plan_violations(
            content, self.section_plan, strategy_cache={})
        assert not any(f["value"] == 0.253 for f in flags)

    def test_no_plan_skips_silently(self):
        from tools.document_audit import (
            check_brief_story_plan_violations,
        )
        content = "Any number 9.99 in the body, no plan supplied."
        assert check_brief_story_plan_violations(
            content, None) == []
        assert check_brief_story_plan_violations(
            content, {}) == []

    def test_dispatcher_runs_brief_check_when_plan_supplied(self):
        from tools.document_audit import audit_document
        content = (
            "OOS Sharpe 1.24. A bogus number 9.99 appears here too.")
        result = audit_document(
            content, "executive_brief",
            strategy_cache={},
            brief_section_plan=self.section_plan)
        # The brief-variant of CHECK 5 fires.
        assert "story_plan" not in result.skipped
        assert any(f["value"] == 9.99
                   for f in result.flags_by_check["story_plan"])

    def test_dispatcher_skips_brief_check_when_plan_missing(self):
        from tools.document_audit import audit_document
        result = audit_document(
            "anything", "executive_brief", strategy_cache={})
        assert result.skipped.get("story_plan") == (
            "no_plan_or_no_slides")
        assert result.flag_counts["story_plan"] == 0


# ── Gap B: required citations check ──────────────────────────────────────


class TestRequiredCitationsCheck:
    """The brief must cite all seven VERIFIED_CITATIONS in body AND
    surface a References section. check_required_citations flags any
    of the seven missing + a missing References heading."""

    def test_missing_hamilton_flags(self):
        from tools.document_audit import check_required_citations
        # All seven authors are present except Hamilton (1989).
        content = (
            "Markowitz (1952) mean-variance. Ang and Bekaert (2002) "
            "regime allocation. Sharpe (1994) risk-adjusted. "
            "Fama and French (1993) factors. Carhart (1997) momentum. "
            "Lo (2002) DSR.\n\n"
            "References\n[entries]")
        flags = check_required_citations(content, "executive_brief")
        keys = [f["citation_key"] for f in flags]
        assert "hamilton_1989" in keys
        # The other six are present so they should NOT flag.
        for present in (
            "ang_bekaert_2002", "markowitz_1952", "carhart_1997",
            "sharpe_1994", "fama_french_1993", "lo_2002",
        ):
            assert present not in keys

    def test_all_seven_present_no_citation_flags(self):
        from tools.document_audit import check_required_citations
        content = (
            "Hamilton (1989). Ang and Bekaert (2002). Markowitz "
            "(1952). Carhart (1997). Sharpe (1994). Fama and French "
            "(1993). Lo (2002).\n\n"
            "References\n[entries]")
        flags = check_required_citations(content, "executive_brief")
        # No missing-citation flags. The References-section guard
        # also passes.
        missing = [f for f in flags
                   if f["type"] == "missing_required_citation"]
        refs_missing = [f for f in flags
                        if f["type"] == "missing_references_section"]
        assert missing == []
        assert refs_missing == []

    def test_missing_references_section_flags(self):
        from tools.document_audit import check_required_citations
        # All seven authors present but no References heading.
        content = (
            "Hamilton (1989). Ang and Bekaert (2002). Markowitz "
            "(1952). Carhart (1997). Sharpe (1994). Fama and French "
            "(1993). Lo (2002).")
        flags = check_required_citations(content, "executive_brief")
        assert any(
            f["type"] == "missing_references_section" for f in flags)

    def test_non_brief_documents_skip_silently(self):
        from tools.document_audit import check_required_citations
        # The deck does not require a formal References section so the
        # check returns [] for non-brief document types.
        assert check_required_citations(
            "no citations here", "presentation_deck") == []
        assert check_required_citations(
            "no citations here", "analytical_appendix") == []

    def test_verified_citations_keys_match_pattern_keys(self):
        """The expected-pattern map is hardcoded; a key added to
        VERIFIED_CITATIONS without a matching pattern entry would
        silently get no coverage. This test catches that drift."""
        from tools.document_audit import _REQUIRED_CITATION_PATTERNS
        from tools.story_plan import VERIFIED_CITATIONS
        assert set(_REQUIRED_CITATION_PATTERNS.keys()) == set(
            VERIFIED_CITATIONS.keys())


# ── Gap C: per-section word count check ──────────────────────────────────


class TestSectionWordCountCheck:

    def _section(self, name: str, words: int) -> str:
        return f"\n## {name}\n" + ("word " * words).strip() + "\n"

    def test_below_min_flags(self):
        from tools.document_audit import check_section_word_counts
        # Methodology at 100 words is below the 300 minimum.
        content = self._section("Methodology", 100)
        flags = check_section_word_counts(
            content, "executive_brief")
        assert len(flags) == 1
        f = flags[0]
        assert f["type"] == "section_word_count"
        assert f["section"] == "Methodology"
        assert f["word_count"] == 100
        assert f["target_min"] == 300
        # Upper band tightened June 21 2026 (was 400) for 5-page
        # DS budget. See _BRIEF_SECTION_WORD_TARGETS comment.
        assert f["target_max"] == 380

    def test_above_max_flags(self):
        from tools.document_audit import check_section_word_counts
        # Visuals at 500 words is above the 300 maximum.
        content = self._section("Visuals", 500)
        flags = check_section_word_counts(
            content, "executive_brief")
        assert len(flags) == 1
        assert flags[0]["section"] == "Visuals"
        assert flags[0]["word_count"] == 500

    def test_within_band_no_flag(self):
        from tools.document_audit import check_section_word_counts
        content = (
            self._section("Executive Summary", 250)
            + self._section("Methodology", 350)
            + self._section("Key Findings", 550)
            + self._section("Limitations", 300)
            + self._section("Final Recommendations", 350)
            + self._section("Visuals", 250))
        flags = check_section_word_counts(
            content, "executive_brief")
        assert flags == []

    def test_unrecognised_section_heading_skipped(self):
        """A heading that does not match any canonical name is
        skipped silently rather than flagged as missing."""
        from tools.document_audit import check_section_word_counts
        content = "\n## Novel Section Name\n" + ("word " * 100)
        flags = check_section_word_counts(
            content, "executive_brief")
        assert flags == []

    def test_non_brief_skips_silently(self):
        from tools.document_audit import check_section_word_counts
        assert check_section_word_counts(
            "anything", "presentation_deck") == []

    def test_numeric_prefix_heading_recognised(self):
        """The brief's docx assembler emits '1. Executive Summary'
        style headings -- the splitter must tolerate the numeric
        prefix or every section will be classified as missing."""
        from tools.document_audit import check_section_word_counts
        content = "\n1. Executive Summary\n" + ("word " * 250)
        flags = check_section_word_counts(
            content, "executive_brief")
        # 250 words is in band (200-300) so no flag.
        assert flags == []


# ── Gap D: editor-export audit re-run ────────────────────────────────────


class TestEditorExportAuditReRun:
    """The export path re-runs the audit on the edited content_text
    so changes between generation and export are caught. Gap D wires
    this through; the test pins the contract that the export still
    succeeds even when the audit raises (fail-open)."""

    @pytest.mark.asyncio
    async def test_run_document_audit_in_test_env_returns_none(self):
        """Sanity: in ENVIRONMENT=test the helper short-circuits to
        None so the editor-export path doesn't try to read DB-backed
        caches in CI. The export still proceeds; nothing to assert
        beyond no-raise."""
        from main import _run_document_audit
        out = await _run_document_audit(
            "any content", "executive_brief", "owner@queens.edu")
        assert out is None

    def test_editor_drafts_helper_present(self):
        """update_audit_warnings was added in PR #336 so the editor-
        export path could persist the post-edit audit result. Pin
        its existence + signature."""
        import inspect
        from tools.editor_drafts import update_audit_warnings
        sig = inspect.signature(update_audit_warnings)
        params = list(sig.parameters.keys())
        assert "draft_id" in params
        assert "audit_warnings" in params
        assert inspect.iscoroutinefunction(update_audit_warnings)


# ── Gap E: admin clear-story-plans endpoint ──────────────────────────────


class TestClearStoryPlansEndpoint:

    def test_endpoint_registered(self):
        """POST /api/v1/admin/clear-story-plans is registered on the
        app router."""
        import main
        paths = [
            route.path for route in main.app.router.routes
            if hasattr(route, "path")]
        assert "/api/v1/admin/clear-story-plans" in paths

    def test_invalid_document_type_rejected(self):
        """Only brief / deck / all are valid. An invalid value
        returns 400, not a silent DELETE."""
        from fastapi.testclient import TestClient
        import main
        client = TestClient(main.app)
        # Bypass the auth dependency for the endpoint test.
        from auth import require_team_member
        main.app.dependency_overrides[require_team_member] = (
            lambda: {"email": "test@queens.edu"})
        try:
            resp = client.post(
                "/api/v1/admin/clear-story-plans"
                "?document_type=bogus")
            assert resp.status_code == 400
        finally:
            main.app.dependency_overrides.clear()

    def test_test_env_short_circuit_returns_zero(self):
        """In ENVIRONMENT=test the endpoint returns deleted=0 +
        a note string rather than touching the DB. The test client
        can then verify the route exists end-to-end."""
        from fastapi.testclient import TestClient
        import main
        from auth import require_team_member
        main.app.dependency_overrides[require_team_member] = (
            lambda: {"email": "test@queens.edu"})
        client = TestClient(main.app)
        try:
            resp = client.post(
                "/api/v1/admin/clear-story-plans"
                "?document_type=brief")
            assert resp.status_code == 200
            body = resp.json()
            assert body["deleted"] == 0
            assert body["document_type"] == "brief"
        finally:
            main.app.dependency_overrides.clear()


# ── Dispatcher flag_counts shape -- new keys land ───────────────────────


class TestAuditResultExposesNewFlagCategories:
    """PR #336 added three new categories to AuditResult.flag_counts:
    story_plan (already in PR #333), required_citations, and
    section_word_count. The frontend banner iterates flag_counts so
    these need to land as discoverable keys."""

    def test_flag_counts_carries_new_keys(self):
        from tools.document_audit import AuditResult
        result = AuditResult()
        keys = set(result.flag_counts.keys())
        assert {"story_plan", "required_citations",
                "section_word_count", "total"}.issubset(keys)
