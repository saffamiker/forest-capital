"""
tests/test_export_package.py

Tests for POST /api/v1/export/package — the academic export endpoint that
assembles a ZIP from client-rendered chart PNGs and table CSVs plus curated
metadata files.

Two tiers, the same pattern as test_activity.py:
  - Endpoint-contract tests (ZIP structure, auth) run everywhere including CI.
  - One DB round-trip test confirms the export is logged to agent_interactions
    for a team email and produces no row for a non-team email; it skips
    cleanly when no live PostgreSQL is reachable.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import uuid
import zipfile

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
TEAM_EMAIL = "ruurdsm@queens.edu"
NON_TEAM_EMAIL = "panttserk@queens.edu"
SESSION_HEADERS = {"X-API-Key": generate_session_token(TEAM_EMAIL)}

ENDPOINT = "/api/v1/export/package"

_METADATA = {
    "study_period_start": "2002-07-31",
    "study_period_end": "2025-12-31",
    "n_months": 282,
    "generated_at": "2026-05-17T12:00:00Z",
}


def _run(coro):
    return asyncio.run(coro)


def _multipart(headers: dict | None = None):
    """Builds the multipart payload — two fake PNG charts, two fake CSV
    tables, and a study-period metadata JSON string."""
    files = [
        ("charts", ("01_cumulative_returns.png", b"\x89PNG_fake_chart_1", "image/png")),
        ("charts", ("02_rolling_correlation.png", b"\x89PNG_fake_chart_2", "image/png")),
        ("tables", ("01_summary_statistics.csv", b"col_a,col_b\n1,2\n", "text/csv")),
        ("tables", ("02_drawdown_comparison.csv", b"strategy,max_dd\nx,-0.3\n", "text/csv")),
    ]
    data = {"metadata": json.dumps(_METADATA)}
    return client.post(ENDPOINT, files=files, data=data,
                       headers=headers if headers is not None else SESSION_HEADERS)


# ── DB availability probe (mirrors test_activity.py) ──────────────────────────

_db_ready_cache: bool | None = None


async def _fresh_session():
    """Disposes the pooled engine and returns a new session bound to the
    current event loop."""
    from database import engine, AsyncSessionLocal
    if engine is not None:
        await engine.dispose()
    return AsyncSessionLocal()  # type: ignore[union-attr]


def _db_ready() -> bool:
    """True when a live PostgreSQL with the activity tables is reachable."""
    global _db_ready_cache
    if _db_ready_cache is not None:
        return _db_ready_cache
    try:
        from tools.cache import _DB_AVAILABLE
        if not _DB_AVAILABLE:
            _db_ready_cache = False
            return False
        from sqlalchemy import text

        async def _probe() -> bool:
            async with await _fresh_session() as s:
                await s.execute(text("SELECT 1 FROM agent_interactions LIMIT 1"))
            return True

        _db_ready_cache = _run(_probe())
    except Exception:
        _db_ready_cache = False
    return _db_ready_cache


# ── Endpoint contract — runs in CI ────────────────────────────────────────────

class TestExportPackageContract:
    def test_returns_zip_with_attachment_header(self):
        resp = _multipart()
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        disposition = resp.headers["content-disposition"]
        assert "attachment" in disposition
        assert "forest_capital_academic_export_" in disposition
        assert disposition.endswith('.zip"')

    def test_response_is_a_valid_zip(self):
        resp = _multipart()
        assert resp.status_code == 200
        # Opening with ZipFile raises BadZipFile if the bytes are not a ZIP.
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        assert zf.namelist()  # non-empty archive

    def test_zip_contains_uploaded_charts_and_tables(self):
        resp = _multipart()
        names = set(zipfile.ZipFile(io.BytesIO(resp.content)).namelist())
        assert "charts/01_cumulative_returns.png" in names
        assert "charts/02_rolling_correlation.png" in names
        assert "tables/01_summary_statistics.csv" in names
        assert "tables/02_drawdown_comparison.csv" in names

    def test_uploaded_file_bytes_are_preserved(self):
        resp = _multipart()
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        assert zf.read("charts/01_cumulative_returns.png") == b"\x89PNG_fake_chart_1"
        assert zf.read("tables/01_summary_statistics.csv") == b"col_a,col_b\n1,2\n"

    def test_zip_contains_metadata_and_readme(self):
        resp = _multipart()
        names = set(zipfile.ZipFile(io.BytesIO(resp.content)).namelist())
        assert "metadata/study_period.txt" in names
        assert "metadata/chart_descriptions.txt" in names
        assert "README.txt" in names

    def test_study_period_txt_contains_metadata_values(self):
        resp = _multipart()
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        study = zf.read("metadata/study_period.txt").decode("utf-8")
        assert "2002-07-31" in study
        assert "2025-12-31" in study
        assert "282" in study
        assert "2026-05-17T12:00:00Z" in study
        assert "100% S&P 500" in study

    def test_chart_descriptions_describe_uploaded_charts(self):
        resp = _multipart()
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        desc = zf.read("metadata/chart_descriptions.txt").decode("utf-8")
        # Both uploaded charts named, with their curated descriptions.
        assert "01_cumulative_returns.png" in desc
        assert "02_rolling_correlation.png" in desc
        assert "regime break" in desc  # rolling_correlation curated text

    def test_missing_metadata_falls_back_gracefully(self):
        files = [
            ("charts", ("01_cumulative_returns.png", b"x", "image/png")),
        ]
        resp = client.post(ENDPOINT, files=files, data={"metadata": "{}"},
                            headers=SESSION_HEADERS)
        assert resp.status_code == 200
        study = zipfile.ZipFile(io.BytesIO(resp.content)).read(
            "metadata/study_period.txt").decode("utf-8")
        assert "—" in study  # placeholder for absent fields

    def test_empty_upload_still_produces_a_valid_zip(self):
        resp = client.post(ENDPOINT, data={"metadata": "{}"}, headers=SESSION_HEADERS)
        assert resp.status_code == 200
        names = set(zipfile.ZipFile(io.BytesIO(resp.content)).namelist())
        assert "README.txt" in names

    def test_requires_authentication(self):
        files = [("charts", ("01_cumulative_returns.png", b"x", "image/png"))]
        resp = client.post(ENDPOINT, files=files, data={"metadata": "{}"})
        assert resp.status_code == 401


# ── DB round-trip — skips without a live database ─────────────────────────────

class TestExportPackageLogging:
    def test_export_interaction_logged_and_team_gated(self):
        """The export endpoint records its run via log_agent_interaction
        with interaction_type='export'. This exercises that data layer
        directly — the same single-loop pattern test_activity.py uses —
        so it verifies 'export' is an accepted interaction type, a team
        user's export logs a row, and a non-team user is gated out."""
        if not _db_ready():
            pytest.skip("no live database")
        from tools.activity_log import log_agent_interaction
        from sqlalchemy import text

        async def scenario():
            from database import engine, AsyncSessionLocal
            await engine.dispose()
            sid = str(uuid.uuid4())
            try:
                # Team user — the export is logged.
                ok = await log_agent_interaction(
                    user_email=TEAM_EMAIL, session_id=sid,
                    session_type="analytical", interaction_type="export",
                    response_summary="2 charts, 2 tables")
                assert ok is True
                # Non-team user — gated out, no row.
                ok2 = await log_agent_interaction(
                    user_email=NON_TEAM_EMAIL, session_id=sid,
                    session_type="analytical", interaction_type="export")
                assert ok2 is False
                async with AsyncSessionLocal() as s:
                    row = await s.execute(
                        text("SELECT COUNT(*) FROM agent_interactions "
                             "WHERE session_id = :sid AND "
                             "interaction_type = 'export'"),
                        {"sid": sid})
                    assert row.scalar() == 1
            finally:
                async with AsyncSessionLocal() as s:
                    await s.execute(
                        text("DELETE FROM agent_interactions WHERE session_id = :sid"),
                        {"sid": sid})
                    await s.commit()

        _run(scenario())
