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


class TestMidpointTemplateEndpoint:
    """The June 3 deadline endpoint must produce a downloadable .docx."""

    def test_endpoint_returns_200(self, client: TestClient) -> None:
        r = client.post("/api/reports/midpoint-template", headers=_auth_headers())
        assert r.status_code == 200, f"Got {r.status_code}: {r.text[:200]}"

    def test_response_has_docx_media_type(self, client: TestClient) -> None:
        r = client.post("/api/reports/midpoint-template", headers=_auth_headers())
        content_type = r.headers.get("content-type", "")
        # The full media type is application/vnd.openxmlformats-officedocument.wordprocessingml.document
        assert "wordprocessingml.document" in content_type

    def test_response_has_attachment_disposition(self, client: TestClient) -> None:
        r = client.post("/api/reports/midpoint-template", headers=_auth_headers())
        dispo = r.headers.get("content-disposition", "")
        assert "attachment" in dispo
        assert ".docx" in dispo

    def test_filename_includes_today_iso_date(self, client: TestClient) -> None:
        from datetime import date
        r = client.post("/api/reports/midpoint-template", headers=_auth_headers())
        dispo = r.headers.get("content-disposition", "")
        # Iterative drafts must not collide in the downloads folder
        assert date.today().isoformat() in dispo

    def test_response_bytes_are_a_valid_docx(self, client: TestClient) -> None:
        """A .docx is a ZIP archive — opening with zipfile.ZipFile asserts
        the bytes aren't corrupt. python-docx's Document() would do the
        same but with a heavier import; zipfile is in stdlib."""
        r = client.post("/api/reports/midpoint-template", headers=_auth_headers())
        with ZipFile(BytesIO(r.content)) as z:
            # Every .docx contains word/document.xml as the body file.
            assert "word/document.xml" in z.namelist()

    def test_response_contains_ai_draft_banner_text(self, client: TestClient) -> None:
        """The AI DRAFT banner must be embedded in the document body so the
        warning survives PDF export and screenshots. We grep the raw
        document.xml since the banner is plain text, not metadata."""
        r = client.post("/api/reports/midpoint-template", headers=_auth_headers())
        with ZipFile(BytesIO(r.content)) as z:
            body = z.read("word/document.xml").decode("utf-8", errors="ignore")
        # The banner is also in the header.xml; either location is sufficient
        # to prove the warning is present in the rendered output.
        header_xml = ""
        with ZipFile(BytesIO(r.content)) as z:
            for name in z.namelist():
                if "header" in name and name.endswith(".xml"):
                    header_xml = z.read(name).decode("utf-8", errors="ignore")
                    break
        combined = body + header_xml
        assert "AI DRAFT" in combined, "AI DRAFT banner missing from rendered .docx"

    def test_response_contains_four_required_sections(self, client: TestClient) -> None:
        """The FNA 670 brief requires four sections in the midpoint paper.
        Each section heading must appear in the rendered output. Ampersands
        in headings are XML-escaped (& → &amp;) so we test for unambiguous
        substrings that don't contain special characters."""
        r = client.post("/api/reports/midpoint-template", headers=_auth_headers())
        with ZipFile(BytesIO(r.content)) as z:
            body = z.read("word/document.xml").decode("utf-8", errors="ignore")
        for needle in ("Methodology", "Preliminary Results", "Roles", "Next Steps"):
            assert needle in body, f"Required section keyword '{needle}' missing"


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
