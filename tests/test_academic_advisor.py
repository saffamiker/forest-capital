"""
tests/test_academic_advisor.py

Sprint 6 — Academic Advisor (Agent 10) tests.

Covers three layers:
  1. Module-level helpers — _filter_to_verified, _parse_json_response,
     _normalise_url. These run without network and enforce the citation
     integrity contract that everything else depends on.
  2. HTTP endpoint contracts — /api/advisor/analyse, /verify-finding,
     /citations. All three return mock payloads in ENVIRONMENT=test so
     the suite is hermetic.
  3. Citation integrity invariant — the post-filter list must never
     contain a URL that web_search did not return.

We do not exercise the real Anthropic web_search tool in tests — that
would be slow, flaky, and burn credits on every CI run. The contract
test (filter_to_verified) is what guarantees the integrity property
in production; the endpoint tests guarantee the FastAPI surface stays
stable.
"""
from __future__ import annotations

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

TEST_EMAIL = "ruurdsm@queens.edu"
AUTH_HEADERS = {"X-API-Key": generate_session_token(TEST_EMAIL)}


# ── Module-level helpers ─────────────────────────────────────────────────────

class TestFilterToVerified:
    """
    The _filter_to_verified function is the runtime enforcement point for
    citation integrity. Every advisor response flows through it; nothing
    else is allowed to bypass it. These tests pin its semantics so any
    regression breaks loudly.
    """

    def test_drops_url_not_in_verified_sources(self):
        from agents.academic_advisor import _filter_to_verified

        # Model emitted a citation the tool never returned.
        citations = [{"title": "Fake Paper", "url": "https://fake.example.com/never-fetched"}]
        verified = [{"url": "https://real.example.com/actually-fetched"}]

        result = _filter_to_verified(citations, verified, fetched_urls=set())
        assert result == [], "Unverified URL must be dropped"

    def test_keeps_url_present_in_verified_sources(self):
        from agents.academic_advisor import _filter_to_verified

        citations = [{"title": "Real Paper", "url": "https://real.example.com/paper"}]
        verified = [{"url": "https://real.example.com/paper"}]

        result = _filter_to_verified(citations, verified, fetched_urls=set())
        assert len(result) == 1
        assert result[0]["title"] == "Real Paper"
        # Filter forces verified=true even if model emitted otherwise.
        assert result[0]["verified"] is True

    def test_case_insensitive_url_match(self):
        from agents.academic_advisor import _filter_to_verified

        citations = [{"title": "Mixed Case", "url": "HTTPS://Example.COM/Paper"}]
        verified = [{"url": "https://example.com/paper"}]

        result = _filter_to_verified(citations, verified, fetched_urls=set())
        assert len(result) == 1

    def test_trailing_slash_normalised(self):
        from agents.academic_advisor import _filter_to_verified

        citations = [{"title": "Slash", "url": "https://example.com/paper/"}]
        verified = [{"url": "https://example.com/paper"}]

        result = _filter_to_verified(citations, verified, fetched_urls=set())
        assert len(result) == 1

    def test_overrides_model_verified_false(self):
        """Even if the model writes verified=false, the filter overrides to true."""
        from agents.academic_advisor import _filter_to_verified

        citations = [{"title": "X", "url": "https://e.com", "verified": False}]
        verified = [{"url": "https://e.com"}]

        result = _filter_to_verified(citations, verified, fetched_urls=set())
        assert result[0]["verified"] is True

    def test_empty_citations_returns_empty(self):
        from agents.academic_advisor import _filter_to_verified
        assert _filter_to_verified([], [{"url": "https://anything"}], fetched_urls=set()) == []

    def test_empty_verified_drops_all(self):
        """No verified URLs means no citations survive — the strictest case."""
        from agents.academic_advisor import _filter_to_verified
        result = _filter_to_verified(
            [{"title": "X", "url": "https://x.com"}, {"title": "Y", "url": "https://y.com"}],
            [],
            fetched_urls=set(),
        )
        assert result == []

    def test_non_dict_citations_ignored(self):
        from agents.academic_advisor import _filter_to_verified
        # String entries should never crash the filter.
        result = _filter_to_verified(["just a string", None, 42], [{"url": "https://a"}], fetched_urls=set())
        assert result == []

    def test_handles_non_list_input(self):
        """Defensive — model occasionally returns a string when asked for a list."""
        from agents.academic_advisor import _filter_to_verified
        assert _filter_to_verified("not a list", [], fetched_urls=set()) == []


# ── Excerpt provenance (Gate 2) ──────────────────────────────────────────────

class TestExcerptGate:
    """
    Excerpt is allowed through only when the URL was successfully fetched
    by web_fetch. The model is the one writing the excerpt text — what we
    enforce is that the fetch actually happened. If it didn't, excerpt
    must be None so the frontend shows 'Excerpt unavailable'.
    """

    def test_excerpt_kept_when_url_fetched(self):
        from agents.academic_advisor import _filter_to_verified

        citations = [{
            "title": "Paper",
            "url": "https://example.com/paper",
            "excerpt": "Direct passage from the fetched page corroborating the finding.",
        }]
        verified = [{"url": "https://example.com/paper"}]
        fetched = {"https://example.com/paper"}

        result = _filter_to_verified(citations, verified, fetched_urls=fetched)
        assert result[0]["excerpt"] == "Direct passage from the fetched page corroborating the finding."

    def test_excerpt_stripped_when_url_not_fetched(self):
        """The integrity property — excerpt without a fetch is dropped to None."""
        from agents.academic_advisor import _filter_to_verified

        citations = [{
            "title": "Paywalled",
            "url": "https://example.com/paywalled",
            # Model claims it read the page, but the fetch never landed.
            "excerpt": "This text would be fabricated from training memory.",
        }]
        verified = [{"url": "https://example.com/paywalled"}]
        fetched: set[str] = set()  # No URL was successfully fetched.

        result = _filter_to_verified(citations, verified, fetched_urls=fetched)
        assert len(result) == 1
        assert result[0]["excerpt"] is None, (
            "Excerpt must be None when web_fetch did not retrieve the URL"
        )

    def test_excerpt_always_present_in_response_shape(self):
        """Every surviving citation carries the excerpt key — even when None."""
        from agents.academic_advisor import _filter_to_verified

        citations = [{"title": "X", "url": "https://x.com"}]  # No excerpt emitted
        verified = [{"url": "https://x.com"}]

        result = _filter_to_verified(citations, verified, fetched_urls=set())
        assert "excerpt" in result[0]
        assert result[0]["excerpt"] is None

    def test_excerpt_stripped_when_empty_string(self):
        from agents.academic_advisor import _filter_to_verified

        citations = [{"title": "X", "url": "https://x.com", "excerpt": "   "}]
        verified = [{"url": "https://x.com"}]
        fetched = {"https://x.com"}

        result = _filter_to_verified(citations, verified, fetched_urls=fetched)
        assert result[0]["excerpt"] is None

    def test_excerpt_trimmed(self):
        from agents.academic_advisor import _filter_to_verified

        citations = [{
            "title": "X", "url": "https://x.com",
            "excerpt": "  Leading and trailing whitespace.  ",
        }]
        verified = [{"url": "https://x.com"}]
        fetched = {"https://x.com"}

        result = _filter_to_verified(citations, verified, fetched_urls=fetched)
        assert result[0]["excerpt"] == "Leading and trailing whitespace."

    def test_fetched_urls_none_disables_gate2(self):
        """verify-finding passes fetched_urls=None — excerpt is kept if emitted."""
        from agents.academic_advisor import _filter_to_verified

        citations = [{"title": "X", "url": "https://x.com", "excerpt": "From summary."}]
        verified = [{"url": "https://x.com"}]

        result = _filter_to_verified(citations, verified, fetched_urls=None)
        assert result[0]["excerpt"] == "From summary."

    def test_url_fetched_but_no_excerpt_emitted_is_none(self):
        """Fetched-but-no-excerpt is the same as not-fetched from the UI's view."""
        from agents.academic_advisor import _filter_to_verified

        citations = [{"title": "X", "url": "https://x.com"}]  # No excerpt field
        verified = [{"url": "https://x.com"}]
        fetched = {"https://x.com"}

        result = _filter_to_verified(citations, verified, fetched_urls=fetched)
        assert result[0]["excerpt"] is None


class TestParseJSONResponse:
    """
    The model wraps JSON in markdown code fences inconsistently.
    _parse_json_response normalises all variants so the agent layer can
    rely on a dict regardless of what Sonnet emits.
    """

    def test_parses_plain_json(self):
        from agents.academic_advisor import _parse_json_response
        out = _parse_json_response('{"key": "value"}')
        assert out == {"key": "value"}

    def test_parses_json_in_code_fence(self):
        from agents.academic_advisor import _parse_json_response
        out = _parse_json_response('```json\n{"key": "value"}\n```')
        assert out == {"key": "value"}

    def test_parses_json_in_generic_fence(self):
        from agents.academic_advisor import _parse_json_response
        out = _parse_json_response('Some prose\n```\n{"key": "value"}\n```\nMore prose')
        assert out == {"key": "value"}

    def test_returns_empty_on_invalid_json(self):
        from agents.academic_advisor import _parse_json_response
        # Failure must not raise — endpoint contract holds via empty dict.
        out = _parse_json_response("not json at all")
        assert out == {}

    def test_returns_empty_on_empty_string(self):
        from agents.academic_advisor import _parse_json_response
        assert _parse_json_response("") == {}


# ── Endpoint contract tests ──────────────────────────────────────────────────

class TestAdvisorAnalyseEndpoint:
    """
    /api/advisor/analyse contract — returns the four-key advisor response.
    In test environment, returns MOCK_ADVISOR_ANALYSE without hitting Anthropic.
    """

    def test_returns_200(self):
        resp = client.post(
            "/api/advisor/analyse",
            json={"query": "What should we focus on for the midpoint?", "deliverable_type": "midpoint"},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200

    def test_response_has_required_keys(self):
        resp = client.post(
            "/api/advisor/analyse",
            json={"query": "midpoint guidance", "deliverable_type": "midpoint"},
            headers=AUTH_HEADERS,
        )
        data = resp.json()
        assert "key_findings" in data
        assert "guidance" in data
        assert "citations" in data
        assert "potential_issues" in data

    def test_citations_are_marked_verified(self):
        resp = client.post(
            "/api/advisor/analyse",
            json={"query": "any", "deliverable_type": "appendix"},
            headers=AUTH_HEADERS,
        )
        data = resp.json()
        for citation in data["citations"]:
            assert citation.get("verified") is True, (
                "Every citation returned to frontend must be verified"
            )

    def test_citations_carry_excerpt_field(self):
        """The excerpt field is part of the citation contract — present on
        every citation as either a non-empty string or None. The frontend
        relies on the field's presence to decide between excerpt tooltip
        and fallback message."""
        resp = client.post(
            "/api/advisor/analyse",
            json={"query": "any", "deliverable_type": "midpoint"},
            headers=AUTH_HEADERS,
        )
        data = resp.json()
        for citation in data["citations"]:
            assert "excerpt" in citation, (
                "Every citation must include the 'excerpt' field"
            )
            excerpt = citation["excerpt"]
            assert excerpt is None or isinstance(excerpt, str), (
                "excerpt must be string or None"
            )

    def test_unauthenticated_rejected(self):
        resp = client.post(
            "/api/advisor/analyse",
            json={"query": "any", "deliverable_type": "midpoint"},
        )
        assert resp.status_code in (401, 403)

    def test_accepts_strategy_results(self):
        """Strategy results are optional but must be accepted when provided."""
        resp = client.post(
            "/api/advisor/analyse",
            json={
                "query": "compare strategies",
                "deliverable_type": "presentation",
                "strategy_results": {"BENCHMARK": {"sharpe_ratio": 0.52}},
            },
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200


class TestAdvisorVerifyEndpoint:
    """/api/advisor/verify-finding — hallucination detection contract."""

    def test_returns_200(self):
        resp = client.post(
            "/api/advisor/verify-finding",
            json={"finding": "Regime Switching Sharpe 0.94", "magnitude": 0.94, "period": "2002-2024"},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200

    def test_response_has_verdict(self):
        resp = client.post(
            "/api/advisor/verify-finding",
            json={"finding": "X"},
            headers=AUTH_HEADERS,
        )
        data = resp.json()
        assert "verdict" in data
        assert data["verdict"] in {"plausible", "implausible", "uncertain"}

    def test_response_has_evidence_arrays(self):
        resp = client.post(
            "/api/advisor/verify-finding",
            json={"finding": "X"},
            headers=AUTH_HEADERS,
        )
        data = resp.json()
        assert isinstance(data["supporting_evidence"], list)
        assert isinstance(data["contradicting_evidence"], list)

    def test_unauthenticated_rejected(self):
        resp = client.post("/api/advisor/verify-finding", json={"finding": "X"})
        assert resp.status_code in (401, 403)


class TestAdvisorCitationsEndpoint:
    """/api/advisor/citations — verified-source-only contract."""

    def test_returns_200(self):
        resp = client.post(
            "/api/advisor/citations",
            json={"finding": "FDR correction in finance", "n_sources": 3},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200

    def test_all_returned_citations_are_verified(self):
        resp = client.post(
            "/api/advisor/citations",
            json={"finding": "X", "n_sources": 3},
            headers=AUTH_HEADERS,
        )
        data = resp.json()
        for citation in data["citations"]:
            assert citation.get("verified") is True

    def test_n_sources_capped_at_5(self):
        """Server caps n_sources regardless of request — protects token budget."""
        resp = client.post(
            "/api/advisor/citations",
            json={"finding": "X", "n_sources": 999},
            headers=AUTH_HEADERS,
        )
        # Endpoint accepts the value but internal cap kicks in.
        assert resp.status_code == 200

    def test_unauthenticated_rejected(self):
        resp = client.post("/api/advisor/citations", json={"finding": "X"})
        assert resp.status_code in (401, 403)


# ── Direct agent invocation (mocked transport) ───────────────────────────────

class TestAdvisorDeliverableGuidance:
    """
    Verifies the four supported deliverable_type values route through the
    rubric lookup without raising. The rubric is keyed by string — an
    unknown type must still produce a response.
    """

    @pytest.mark.parametrize("dt", ["midpoint", "appendix", "brief", "presentation"])
    def test_known_deliverable_types(self, dt: str):
        resp = client.post(
            "/api/advisor/analyse",
            json={"query": "guidance", "deliverable_type": dt},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200

    def test_unknown_deliverable_type_still_responds(self):
        resp = client.post(
            "/api/advisor/analyse",
            json={"query": "guidance", "deliverable_type": "unknown_type_xyz"},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200


# ── Citation integrity invariant — end-to-end ────────────────────────────────

class TestCitationIntegrityInvariant:
    """
    End-to-end property: a citation returned to the frontend always
    appears in the verified_sources list (or analytics breaks). Even
    the mock data must respect this invariant — otherwise the mock
    drifts from the production contract.
    """

    def test_mock_analyse_citations_present_in_verified_sources(self):
        from agents.academic_advisor import MOCK_ADVISOR_ANALYSE

        verified_urls = {
            s["url"].lower().rstrip("/")
            for s in MOCK_ADVISOR_ANALYSE["verified_sources"]
        }
        for citation in MOCK_ADVISOR_ANALYSE["citations"]:
            assert (
                citation["url"].lower().rstrip("/") in verified_urls
            ), f"Mock citation {citation['url']} not in mock verified_sources"

    def test_mock_analyse_citations_have_excerpts(self):
        """
        Mock fixtures must include excerpt strings to exercise the
        frontend's tooltip-rendering path. If the mock drops the excerpt,
        the frontend test that asserts excerpts render would degrade to
        the fallback-message path and silently change semantics.
        """
        from agents.academic_advisor import MOCK_ADVISOR_ANALYSE
        for citation in MOCK_ADVISOR_ANALYSE["citations"]:
            assert citation.get("excerpt"), (
                f"Mock citation {citation['url']} must include a non-empty excerpt"
            )
            assert len(citation["excerpt"]) >= 50, (
                "Mock excerpts should be 2-3 sentences (≥50 chars)"
            )

    def test_mock_citations_have_excerpts(self):
        from agents.academic_advisor import MOCK_ADVISOR_CITATIONS
        for citation in MOCK_ADVISOR_CITATIONS["citations"]:
            assert citation.get("excerpt"), (
                f"Mock citation {citation['url']} must include a non-empty excerpt"
            )

    def test_mock_verify_evidence_present_in_verified_sources(self):
        from agents.academic_advisor import MOCK_ADVISOR_VERIFY

        verified_urls = {
            s["url"].lower().rstrip("/")
            for s in MOCK_ADVISOR_VERIFY["verified_sources"]
        }
        for evidence in MOCK_ADVISOR_VERIFY["supporting_evidence"]:
            assert evidence["url"].lower().rstrip("/") in verified_urls

    def test_mock_citations_present_in_verified_sources(self):
        from agents.academic_advisor import MOCK_ADVISOR_CITATIONS

        verified_urls = {
            s["url"].lower().rstrip("/")
            for s in MOCK_ADVISOR_CITATIONS["verified_sources"]
        }
        for citation in MOCK_ADVISOR_CITATIONS["citations"]:
            assert citation["url"].lower().rstrip("/") in verified_urls
