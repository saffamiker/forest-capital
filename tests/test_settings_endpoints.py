"""
tests/test_settings_endpoints.py

Tests for the backend endpoints behind the /settings page:
  - GET /api/v1/admin/data-status     (Data and Study Period section)
  - GET /api/v1/analytics/config      (Analytics Configuration section)

The test environment has no PostgreSQL, so these confirm the response
contract and the auth gate — the DB-populated paths run in deployment.
"""
from __future__ import annotations

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


class TestDataStatus:
    def test_requires_auth(self):
        assert client.get("/api/v1/admin/data-status").status_code == 401

    def test_returns_contract_shape(self):
        r = client.get("/api/v1/admin/data-status", headers=SESSION_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert "available" in body
        assert "study_period" in body
        assert "tables" in body
        assert isinstance(body["tables"], list)
