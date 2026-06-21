"""
tests/test_reports_endpoints.py

Smoke + structural tests for the Sprint 6 reports endpoints:

  POST /api/reports/midpoint-template — Priority 1 (June 3 deadline).
                                         Returns a .docx with Academic
                                         Writer prose + AI DRAFT banner.
  GET  /api/reports/manifest           — Returns the deliverable card list
                                         the Reports screen renders.

The Academic Writer prose path is exercised via the test-env fast path
(no real LLM call) — we verify the .docx is structurally valid, contains
the AI DRAFT banner, and that the response carries the correct
Content-Disposition. Wire-level correctness, not LLM output quality.
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
    """Import MASTER_API_KEY from config so the header matches whatever the
    surrounding environment loaded — same fix applied to test_qa_endpoints."""
    from config import MASTER_API_KEY  # type: ignore[import]
    return {"X-API-Key": MASTER_API_KEY}


@pytest.fixture
def client() -> TestClient:
    from main import app  # type: ignore[import]
    return TestClient(app)


class TestRetiredEndpointsReturn410:
    """PR-B (June 2026) retired four endpoints whose UI surfaces were
    removed in PR #338. Each is preserved as a 410 Gone stub so
    existing clients receive a clear "this existed and is now gone"
    signal rather than a 404 connection error. Each stub carries a
    canonical_path pointing at the executive-brief endpoint, which is
    the only generation surface that remains."""

    def test_midpoint_template_returns_410(self, client: TestClient):
        r = client.post(
            "/api/reports/midpoint-template", headers=_auth_headers())
        assert r.status_code == 410
        body = r.json()
        assert body["error"] == "gone"
        assert body["canonical_path"] == (
            "/api/v1/export/executive-brief")
        assert "Report Writer midpoint pipeline has been retired" \
            in body["message"]

    def test_executive_brief_template_returns_410(
            self, client: TestClient):
        r = client.post(
            "/api/reports/executive-brief-template",
            headers=_auth_headers())
        assert r.status_code == 410
        body = r.json()
        assert body["error"] == "gone"
        assert body["canonical_path"] == (
            "/api/v1/export/executive-brief")
        assert "Report Writer pipeline has been retired" \
            in body["message"]

    def test_council_peer_review_returns_410(
            self, client: TestClient):
        # Peer review used to take multipart/form-data with a file
        # upload; the 410 stub takes nothing -- it just returns the
        # retirement marker. Any client that still POSTs to it
        # (file or no file) sees the same 410 response shape.
        r = client.post(
            "/api/council/peer-review", headers=_auth_headers())
        assert r.status_code == 410
        body = r.json()
        assert body["error"] == "gone"
        assert "Peer review has been retired" in body["message"]


class TestReportsManifestEndpoint:
    """The manifest powers the Reports screen card grid."""

    def test_manifest_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/reports/manifest", headers=_auth_headers())
        assert r.status_code == 200

    def test_manifest_has_two_owner_groups(self, client: TestClient) -> None:
        r = client.get("/api/reports/manifest", headers=_auth_headers())
        body = r.json()
        assert "owner_bob" in body
        assert "owner_molly" in body

    def test_manifest_lists_midpoint_as_available(self, client: TestClient) -> None:
        """Midpoint is the only Priority-1 deliverable. The Reports screen
        depends on this status field to know which Generate button to enable."""
        r = client.get("/api/reports/manifest", headers=_auth_headers())
        body = r.json()
        midpoint = next(
            (c for c in body["owner_bob"] if c["id"] == "midpoint_template"),
            None,
        )
        assert midpoint is not None, "midpoint_template missing from manifest"
        assert midpoint["status"] == "available"
        assert midpoint["endpoint"] == "/api/reports/midpoint-template"

    def test_each_card_has_required_keys(self, client: TestClient) -> None:
        """Frontend DeliverableCard reads these keys — protect the contract."""
        r = client.get("/api/reports/manifest", headers=_auth_headers())
        body = r.json()
        required = {"id", "title", "description", "endpoint", "method",
                    "format", "status", "deadline"}
        for group in (body["owner_bob"], body["owner_molly"]):
            for card in group:
                missing = required - set(card.keys())
                assert not missing, f"Card '{card.get('id')}' missing keys: {missing}"


class TestDocxGeneratorUnit:
    """Direct unit tests against tools.docx_generator — no HTTP layer."""

    def test_build_docx_returns_nonempty_bytes(self) -> None:
        from tools.docx_generator import build_docx
        out = build_docx(
            title="Test Document",
            subtitle=None,
            sections=[{"heading": "Intro", "body": "Some prose."}],
        )
        assert isinstance(out, bytes)
        assert len(out) > 1000  # smallest valid .docx is ~3kb

    def test_build_docx_is_a_valid_zip(self) -> None:
        from tools.docx_generator import build_docx
        out = build_docx(
            title="Test",
            subtitle="Subtitle",
            sections=[{"heading": "S1", "body": "Body."}],
        )
        with ZipFile(BytesIO(out)) as z:
            assert "word/document.xml" in z.namelist()

    def test_build_docx_includes_strategy_table_after_results(self) -> None:
        """When strategy_results is provided, the table is inserted after
        the Results section so prose can reference 'Table 1' naturally."""
        from tools.docx_generator import build_docx
        out = build_docx(
            title="With Table",
            subtitle=None,
            sections=[
                {"heading": "Methodology", "body": "Method prose."},
                {"heading": "Results", "body": "Results prose."},
            ],
            strategy_results={
                "BENCHMARK": {"cagr": 0.085, "sharpe_ratio": 0.52,
                              "max_drawdown": -0.50, "p_value_corrected": 0.04,
                              "cv_stability_score": 0.55, "tier1_gates_passed": 2,
                              "is_significant": False},
            },
        )
        with ZipFile(BytesIO(out)) as z:
            body = z.read("word/document.xml").decode("utf-8", errors="ignore")
        assert "Table 1: Strategy Comparison" in body
        assert "BENCHMARK" in body

    def test_build_docx_renders_ai_draft_in_header(self) -> None:
        from tools.docx_generator import build_docx
        out = build_docx(
            title="Banner Test",
            subtitle=None,
            sections=[{"heading": "S", "body": "B"}],
        )
        with ZipFile(BytesIO(out)) as z:
            # Find the header file — name varies by docx layout
            header_xml = ""
            for name in z.namelist():
                if "header" in name and name.endswith(".xml"):
                    header_xml = z.read(name).decode("utf-8", errors="ignore")
                    break
        assert "AI DRAFT" in header_xml


class TestAnalyticalAppendixEndpoint:
    """The Analytical Appendix is 35% of the grade. The HTML deliverable
    must contain all six required sections, the AI DRAFT banner, and the
    strategy comparison table for the grader to cross-reference."""

    def test_endpoint_returns_200(self, client: TestClient) -> None:
        r = client.post("/api/reports/analytical-appendix", headers=_auth_headers())
        assert r.status_code == 200, f"Got {r.status_code}: {r.text[:200]}"

    def test_returns_html_media_type(self, client: TestClient) -> None:
        r = client.post("/api/reports/analytical-appendix", headers=_auth_headers())
        assert "text/html" in r.headers.get("content-type", "")

    def test_response_carries_ai_draft_banner(self, client: TestClient) -> None:
        r = client.post("/api/reports/analytical-appendix", headers=_auth_headers())
        assert "AI DRAFT" in r.text, "AI DRAFT banner missing from HTML body"
        # The banner subtitle must also be present — it's the auditable text
        # that explains why human review is required.
        assert "verified by a team member" in r.text

    def test_response_contains_all_six_sections(self, client: TestClient) -> None:
        """Per CLAUDE.md Section 14, the appendix has 6 named sections.
        Each section's heading must appear in the rendered HTML or the
        grader will dock points for missing required content."""
        r = client.post("/api/reports/analytical-appendix", headers=_auth_headers())
        for needle in (
            "Abstract",
            "Data Sources and Provenance",
            "Portfolio Construction Methodology",
            "Statistical Results",
            "Sensitivity Analysis",
            "Reproducibility Notes",
        ):
            assert needle in r.text, f"Required section '{needle}' missing"

    def test_response_includes_references_when_available(self, client: TestClient) -> None:
        """references.json carries the curated bibliography. When loaded,
        the appendix must surface them in a References block."""
        r = client.post("/api/reports/analytical-appendix", headers=_auth_headers())
        # The Academic Writer's references DB has at minimum these authors.
        # If references load fails the section is silently omitted — so
        # this test only fires when at least one cite makes it through.
        if "References" in r.text:
            # When the section is present, at least one citation must render.
            assert "López de Prado" in r.text or "Sharpe" in r.text

    def test_filename_includes_today_iso_date(self, client: TestClient) -> None:
        from datetime import date
        r = client.post("/api/reports/analytical-appendix", headers=_auth_headers())
        dispo = r.headers.get("content-disposition", "")
        assert date.today().isoformat() in dispo
        assert ".html" in dispo

    def test_response_is_valid_html(self, client: TestClient) -> None:
        """Smoke-check: must start with a doctype declaration and have a
        single closing </html> tag. Catches the case where the builder
        emits malformed markup that browsers might tolerate but graders
        viewing source will flag."""
        r = client.post("/api/reports/analytical-appendix", headers=_auth_headers())
        assert r.text.strip().startswith("<!DOCTYPE html>")
        assert r.text.count("</html>") == 1


class TestManifestNewGenerators:
    """The two new generators must appear in the manifest with
    status='available' so the Reports screen renders the Generate buttons
    rather than the disabled 'Planned' state."""

    def test_executive_brief_is_available(self, client: TestClient) -> None:
        r = client.get("/api/reports/manifest", headers=_auth_headers())
        body = r.json()
        card = next(
            (c for c in body["owner_bob"] if c["id"] == "executive_brief"),
            None,
        )
        assert card is not None
        assert card["status"] == "available"
        assert card["endpoint"] == "/api/reports/executive-brief-template"

    def test_analytical_appendix_is_available(self, client: TestClient) -> None:
        r = client.get("/api/reports/manifest", headers=_auth_headers())
        body = r.json()
        card = next(
            (c for c in body["owner_bob"] if c["id"] == "analytical_appendix"),
            None,
        )
        assert card is not None
        assert card["status"] == "available"
        assert card["format"] == "html"


class TestAgentPersonasEndpoint:
    """GET /api/agents/personas powers the PersonaModal in CouncilDebate."""

    def test_endpoint_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/agents/personas", headers=_auth_headers())
        assert r.status_code == 200

    def test_response_lists_all_seven_agents(self, client: TestClient) -> None:
        """Seven agents in the council: 4 specialists + 2 dissenters + CIO.
        The Explainer Agent is not a council member (background-only) and
        the QA Agent isn't either (audit role)."""
        r = client.get("/api/agents/personas", headers=_auth_headers())
        body = r.json()
        names = {a["agent"] for a in body["agents"]}
        for expected in (
            "Equity Analyst",
            "Fixed Income Analyst",
            "Risk Manager",
            "Quant Backtester",
            "Independent Analyst (Gemini)",
            "Contrarian Analyst (Grok)",
            "CIO",
        ):
            assert expected in names, f"Agent '{expected}' missing from personas"

    def test_each_agent_has_required_fields(self, client: TestClient) -> None:
        r = client.get("/api/agents/personas", headers=_auth_headers())
        body = r.json()
        required = {"agent", "model", "system_prompt", "prompt_summary_first_sentence"}
        for a in body["agents"]:
            missing = required - set(a.keys())
            assert not missing, f"Agent {a.get('agent')} missing keys: {missing}"

    def test_system_prompt_is_non_empty_for_implemented_agents(self, client: TestClient) -> None:
        """Every agent module that exists must surface a non-empty prompt.
        The endpoint falls back to empty string if the module import fails;
        a passing test confirms all module imports succeed in CI."""
        r = client.get("/api/agents/personas", headers=_auth_headers())
        body = r.json()
        for a in body["agents"]:
            assert len(a["system_prompt"]) > 50, (
                f"Agent {a['agent']} has suspiciously short prompt — "
                "import likely failed silently."
            )

    def test_unauthenticated_rejected(self, client: TestClient) -> None:
        r = client.get("/api/agents/personas")
        assert r.status_code in (401, 403)


class TestSectionDocumentEndpoints:
    """Bob's section editor uses three endpoints:
       POST /api/documents/section-doc/draft       — creates the doc
       POST /api/documents/{id}/sections/{sid}/regenerate — single-section re-run
       POST /api/documents/{id}/export             — download current draft
       The standard documents API (get / patch / versions / restore) is
       already tested in test_storyboard_endpoints — these tests focus
       only on the section-document-specific behaviour.
    """

    def test_section_draft_rejects_unknown_doc_type(self, client: TestClient) -> None:
        r = client.post(
            "/api/documents/section-doc/draft",
            headers=_auth_headers(),
            json={"doc_type": "random_garbage"},
        )
        assert r.status_code == 422

    def test_section_draft_creates_midpoint_with_correct_sections(self, client: TestClient) -> None:
        r = client.post(
            "/api/documents/section-doc/draft",
            headers=_auth_headers(),
            json={"doc_type": "midpoint_paper"},
        )
        assert r.status_code == 201  # creation endpoint (Level 1 review M10)
        body = r.json()
        # Without a DATABASE_URL the endpoint returns persistence=unavailable
        # but still surfaces the content for the UI to render. Both paths
        # must include the section structure.
        content = body["content"]
        assert content["doc_type"] == "midpoint_paper"
        section_ids = [s["id"] for s in content["sections"]]
        # Midpoint has 4 required sections per FNA 670 brief.
        for expected in ("methodology", "results", "roles", "next_steps"):
            assert expected in section_ids, f"Section '{expected}' missing"

    def test_section_draft_creates_executive_brief_with_six_sections(self, client: TestClient) -> None:
        r = client.post(
            "/api/documents/section-doc/draft",
            headers=_auth_headers(),
            json={"doc_type": "executive_brief"},
        )
        assert r.status_code == 201  # creation endpoint (Level 1 review M10)
        content = r.json()["content"]
        section_ids = [s["id"] for s in content["sections"]]
        assert len(section_ids) == 6
        for expected in (
            "executive_summary", "methodology", "key_findings",
            "limitations", "recommendations", "appendix_charts",
        ):
            assert expected in section_ids

    def test_section_draft_creates_analytical_appendix_with_six_sections(self, client: TestClient) -> None:
        r = client.post(
            "/api/documents/section-doc/draft",
            headers=_auth_headers(),
            json={"doc_type": "analytical_appendix"},
        )
        assert r.status_code == 201  # creation endpoint (Level 1 review M10)
        content = r.json()["content"]
        section_ids = [s["id"] for s in content["sections"]]
        for expected in (
            "abstract", "data_sources", "methodology",
            "statistical_results", "sensitivity", "reproducibility",
        ):
            assert expected in section_ids

    def test_each_section_has_required_fields(self, client: TestClient) -> None:
        """SectionEditor UI reads id, title, ai_draft, content, last_edited.
        All four must be present on every section or the UI breaks."""
        r = client.post(
            "/api/documents/section-doc/draft",
            headers=_auth_headers(),
            json={"doc_type": "executive_brief"},
        )
        content = r.json()["content"]
        for s in content["sections"]:
            for required in ("id", "title", "ai_draft", "content", "last_edited"):
                assert required in s, f"Section missing field: {required}"
            # ai_draft and content should be equal on creation (Bob hasn't
            # edited yet). After Bob edits, they diverge — that's the whole
            # point of having both fields.
            assert s["ai_draft"] == s["content"]

    def test_regenerate_section_returns_404_for_unknown_document(self, client: TestClient) -> None:
        r = client.post(
            "/api/documents/nonexistent-uuid/sections/methodology/regenerate",
            headers=_auth_headers(),
        )
        # Without a DB-persisted doc the endpoint returns 404 (no draft found).
        assert r.status_code == 404

    def test_export_returns_404_for_unknown_document(self, client: TestClient) -> None:
        r = client.post(
            "/api/documents/nonexistent-uuid/export",
            headers=_auth_headers(),
        )
        assert r.status_code == 404


class TestMigration004Importable:
    def test_migration_module_imports(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "m004",
            os.path.join(os.path.dirname(__file__), "..", "backend", "migrations",
                         "versions", "004_create_documents_tables.py"),
        )
        assert spec is not None and spec.loader is not None
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        assert m.revision == "004"
        assert m.down_revision == "003"
        assert callable(m.upgrade)
        assert callable(m.downgrade)
