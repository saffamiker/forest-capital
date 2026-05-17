"""
Sprint 2 remediation — /api/v1/provenance endpoint tests.
Tests that the endpoint returns correct structure and handles missing
provenance.json gracefully.  No live database required.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)

# The /api/v1/provenance endpoint requires auth (Level 1 review M15).
from auth import generate_session_token  # noqa: E402
_PROVENANCE_AUTH = {"X-API-Key": generate_session_token("ruurdsm@queens.edu")}

# ── Fixture helpers ───────────────────────────────────────────────────────────

SAMPLE_PROVENANCE = {
    "series": [
        {
            "series_id": "equity_monthly",
            "display_name": "S&P 500 Monthly Returns",
            "source_type": "excel_provided",
            "source_detail": {
                "file": "FNA_670_Project_Sources.xlsx",
                "sheet": "S&P 500 Monthly Returns",
                "provided_by": "Dr. Panttser (FNA 670)",
                "original_source": "Y-charts",
            },
            "frequency": "monthly",
            "date_range_start": "2000-01-31",
            "date_range_end": "2024-12-31",
            "row_count": 300,
            "loaded_at": "2026-05-11T14:00:00Z",
            "last_validated": None,
            "validation_status": "pass",
        },
        {
            "series_id": "equity_daily_spy",
            "display_name": "SPY Daily Equity Returns",
            "source_type": "yfinance",
            "source_detail": {
                "ticker": "SPY",
                "auto_adjust": True,
                "interval": "1d",
                "fetched_at": "2026-05-11T14:00:00Z",
            },
            "frequency": "daily",
            "date_range_start": "2000-01-03",
            "date_range_end": "2024-12-31",
            "row_count": 6260,
            "loaded_at": "2026-05-11T14:00:00Z",
            "last_validated": None,
            "validation_status": "pass",
        },
        {
            "series_id": "vix_daily",
            "display_name": "VIX (CBOE Volatility Index)",
            "source_type": "fred_api",
            "source_detail": {
                "series_id": "VIXCLS",
                "fetched_at": "2026-05-11T14:00:00Z",
                "fred_url": "https://fred.stlouisfed.org/series/VIXCLS",
            },
            "frequency": "daily",
            "date_range_start": "2000-01-03",
            "date_range_end": "2024-12-31",
            "row_count": 6260,
            "loaded_at": "2026-05-11T14:00:00Z",
            "last_validated": None,
            "validation_status": "pass",
        },
    ],
    "cross_validation": {},
    "last_pipeline_run": "2026-05-11T14:00:00Z",
}


def _client_with_provenance(tmp_prov: Path):
    """Returns a TestClient with _PROVENANCE_PATH patched to tmp_prov."""
    from fastapi.testclient import TestClient
    import importlib
    import main as main_module
    importlib.reload(main_module)

    # Patch the path used by the endpoint
    with patch.object(main_module, "_PROVENANCE_PATH", tmp_prov):
        client = TestClient(main_module.app, headers=_PROVENANCE_AUTH)
        yield client


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_provenance_endpoint_returns_200_when_file_exists():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(SAMPLE_PROVENANCE, f)
        tmp = Path(f.name)
    try:
        from fastapi.testclient import TestClient
        import importlib
        import main as main_module
        importlib.reload(main_module)
        with patch.object(main_module, "_PROVENANCE_PATH", tmp):
            client = TestClient(main_module.app, headers=_PROVENANCE_AUTH)
            resp = client.get("/api/v1/provenance")
        assert resp.status_code == 200
    finally:
        tmp.unlink(missing_ok=True)


def test_provenance_endpoint_returns_series_list():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(SAMPLE_PROVENANCE, f)
        tmp = Path(f.name)
    try:
        from fastapi.testclient import TestClient
        import importlib
        import main as main_module
        importlib.reload(main_module)
        with patch.object(main_module, "_PROVENANCE_PATH", tmp):
            client = TestClient(main_module.app, headers=_PROVENANCE_AUTH)
            resp = client.get("/api/v1/provenance")
            data = resp.json()
        assert "series" in data
        assert isinstance(data["series"], list)
        assert len(data["series"]) == 3
    finally:
        tmp.unlink(missing_ok=True)


def test_provenance_endpoint_returns_200_when_file_missing():
    """When provenance.json doesn't exist, return 200 with empty series (not 500)."""
    from fastapi.testclient import TestClient
    import importlib
    import main as main_module
    importlib.reload(main_module)
    missing = Path("/nonexistent/path/provenance.json")
    with patch.object(main_module, "_PROVENANCE_PATH", missing):
        client = TestClient(main_module.app, headers=_PROVENANCE_AUTH)
        resp = client.get("/api/v1/provenance")
    assert resp.status_code == 200
    data = resp.json()
    assert data["series"] == []


def test_provenance_endpoint_series_have_required_fields():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(SAMPLE_PROVENANCE, f)
        tmp = Path(f.name)
    try:
        from fastapi.testclient import TestClient
        import importlib
        import main as main_module
        importlib.reload(main_module)
        with patch.object(main_module, "_PROVENANCE_PATH", tmp):
            client = TestClient(main_module.app, headers=_PROVENANCE_AUTH)
            resp = client.get("/api/v1/provenance")
            data = resp.json()
        required = {"series_id", "display_name", "source_type", "source_detail", "frequency"}
        for s in data["series"]:
            missing = required - set(s.keys())
            assert not missing, f"Series missing fields: {missing}"
    finally:
        tmp.unlink(missing_ok=True)


def test_provenance_endpoint_requires_auth():
    """Provenance requires authentication (Level 1 review M15) — an
    unauthenticated request is rejected with 401, consistent with every
    other /api/v1/* route."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(SAMPLE_PROVENANCE, f)
        tmp = Path(f.name)
    try:
        from fastapi.testclient import TestClient
        import importlib
        import main as main_module
        importlib.reload(main_module)
        with patch.object(main_module, "_PROVENANCE_PATH", tmp):
            client = TestClient(main_module.app)  # no auth header
            resp = client.get("/api/v1/provenance")
        assert resp.status_code == 401
    finally:
        tmp.unlink(missing_ok=True)
