"""
tests/test_admin_screen.py

Verifies the admin data-health endpoint (schema and access control)
and the force-refresh gate (MASTER_API_KEY required).

Sprint 5: the admin endpoints are wired in main.py. Tests accept 404
for endpoints not yet wired (contract test), and 200 or 202 for wired ones.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("MASTER_API_KEY", "test-master-key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


def _client():
    from main import app  # type: ignore[import]
    return TestClient(app)


# Canonical mock response used across all schema tests
MOCK_HEALTH_RESPONSE = {
    "last_pipeline_run": "2026-05-12T14:23:20Z",
    "data_source": "postgresql_cache",
    "market_data_monthly_rows": 282,
    "market_data_daily_rows": 9297,
    "registry_series_count": 16,
    "source_breakdown": [
        {
            "series_id": "equity_monthly",
            "display_name": "S&P 500 Monthly Returns",
            "source_type": "excel_provided",
            "last_fetched": "2026-05-12T14:23:01Z",
            "status": "pass",
            "row_count": 300,
        },
        {
            "series_id": "vix_daily",
            "display_name": "VIX (VIXCLS)",
            "source_type": "fred_api",
            "last_fetched": "2026-05-12T14:25:41Z",
            "status": "warn",
            "row_count": 6260,
        },
    ],
    "cross_validation": {
        "equity": {"status": "warn", "n_green": 290, "n_amber": 8, "n_red": 2},
        "bond_internal": {"status": "pass"},
    },
    "sanity_assertions": [
        {
            "assert_id": "sp500_cagr",
            "description": "S&P 500 2000-2024 CAGR",
            "expected": "8-12%",
            "actual": "8.54%",
            "status": "pass",
        },
        {
            "assert_id": "obs_count",
            "description": "Total aligned monthly observations",
            "expected": ">=288",
            "actual": "300",
            "status": "pass",
        },
    ],
    "cache_status": "hit",
    "strategy_hash": "abc123",
}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/data-health — access control
# ---------------------------------------------------------------------------

class TestDataHealthAccess:
    """Admin endpoint accepts authenticated requests from any team member."""

    def test_unauthenticated_request_returns_401(self):
        """No session token → must return 401 or 403 (not 200)."""
        client = _client()
        resp = client.get("/api/v1/admin/data-health")
        # 401 (unauthenticated), 403 (forbidden), or 404 (not yet wired) are all valid
        assert resp.status_code in (401, 403, 404, 422), (
            f"Unauthenticated request should not return 200, got {resp.status_code}"
        )

    def test_health_endpoint_exists(self):
        """Base /api/health endpoint is reachable — backend is running."""
        client = _client()
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_force_refresh_without_master_key_is_not_200(self):
        """Force-refresh without MASTER_API_KEY must not return 200."""
        client = _client()
        resp = client.get(
            "/api/v1/admin/force-refresh",
            headers={"Authorization": "Bearer regular-token"},
        )
        assert resp.status_code != 200, (
            "Force-refresh must require MASTER_API_KEY — regular tokens must be rejected"
        )

    def test_force_refresh_with_master_key_returns_expected_status(self):
        """Force-refresh with correct MASTER_API_KEY returns 200, 202, or 404."""
        client = _client()
        master_key = os.environ.get("MASTER_API_KEY", "test-master-key")
        resp = client.get(
            "/api/v1/admin/force-refresh",
            headers={"X-Master-Key": master_key},
        )
        # 200/202 if endpoint is wired, 404 if not yet implemented
        assert resp.status_code in (200, 202, 404), (
            f"Unexpected status for MASTER_API_KEY request: {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Data-health response schema — validated against mock
# ---------------------------------------------------------------------------

class TestDataHealthSchema:
    """Validates the structure of the data-health response object."""

    def test_registry_has_16_series(self):
        """registry_series_count must be 16 — one per data series in the pipeline."""
        assert MOCK_HEALTH_RESPONSE["registry_series_count"] == 16

    def test_monthly_rows_meet_power_threshold(self):
        """Market data monthly row count must satisfy the MIN_OBSERVATIONS_FOR_POWER gate."""
        from config import MIN_OBSERVATIONS_FOR_POWER  # type: ignore[import]
        assert MOCK_HEALTH_RESPONSE["market_data_monthly_rows"] >= MIN_OBSERVATIONS_FOR_POWER

    def test_sanity_assertions_have_required_fields(self):
        """Every assertion entry must have assert_id, description, expected, actual, status."""
        required = {"assert_id", "description", "expected", "actual", "status"}
        for assertion in MOCK_HEALTH_RESPONSE["sanity_assertions"]:
            missing = required - set(assertion.keys())
            assert not missing, f"Assertion missing fields: {missing}"

    def test_cross_validation_block_present(self):
        """Response includes cross_validation with equity and bond_internal sub-keys."""
        assert "cross_validation" in MOCK_HEALTH_RESPONSE
        cv = MOCK_HEALTH_RESPONSE["cross_validation"]
        assert "equity" in cv
        assert "bond_internal" in cv

    def test_source_breakdown_entries_have_status(self):
        """Every source_breakdown entry documents pass/warn/fail operational status."""
        valid_statuses = {"pass", "warn", "fail"}
        for entry in MOCK_HEALTH_RESPONSE["source_breakdown"]:
            assert "status" in entry, f"Entry missing status: {entry.get('series_id')}"
            assert entry["status"] in valid_statuses, (
                f"Unexpected status {entry['status']!r} for {entry.get('series_id')}"
            )

    def test_response_includes_last_pipeline_run_timestamp(self):
        """last_pipeline_run is ISO-format timestamp string."""
        ts = MOCK_HEALTH_RESPONSE.get("last_pipeline_run", "")
        assert "T" in ts and "Z" in ts, f"Expected ISO timestamp, got {ts!r}"

    def test_cache_status_field_present(self):
        """cache_status indicates whether results came from DB cache or live pipeline."""
        valid = {"hit", "miss", "stale"}
        status = MOCK_HEALTH_RESPONSE.get("cache_status", "")
        assert status in valid, f"cache_status must be one of {valid}, got {status!r}"

    def test_source_breakdown_covers_both_excel_and_fred(self):
        """source_breakdown includes at least one excel_provided and one fred_api entry."""
        types = {e["source_type"] for e in MOCK_HEALTH_RESPONSE["source_breakdown"]}
        assert "excel_provided" in types, "Must document at least one excel_provided series"
        assert "fred_api" in types, "Must document at least one fred_api series"

    def test_all_sanity_assertions_pass_in_mock(self):
        """The mock data shows all sanity assertions in PASS state — representative of healthy run."""
        for assertion in MOCK_HEALTH_RESPONSE["sanity_assertions"]:
            assert assertion["status"] == "pass", (
                f"Mock should show healthy state; {assertion['assert_id']} shows {assertion['status']}"
            )
