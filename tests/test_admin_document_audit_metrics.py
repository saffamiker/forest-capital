"""tests/test_admin_document_audit_metrics.py — endpoint contract.

In the test env the document_audit_metrics endpoint short-circuits
to {available:false, rows:[], aggregates:{}}. These tests assert
that contract plus the auth gate.
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
SESSION = {"X-API-Key": generate_session_token("ruurdsm@queens.edu")}


def test_requires_authentication():
    resp = client.get("/api/v1/admin/document-audit-metrics")
    assert resp.status_code == 401


def test_returns_envelope_in_test_env():
    resp = client.get(
        "/api/v1/admin/document-audit-metrics", headers=SESSION)
    assert resp.status_code == 200
    body = resp.json()
    assert "available" in body and "rows" in body and "aggregates" in body
    assert body["available"] is False
    assert body["rows"] == []
    assert body["aggregates"] == {}


def test_respects_limit_clamp():
    for n in (1, 30, 200, 5000, 0, -1):
        resp = client.get(
            f"/api/v1/admin/document-audit-metrics?limit={n}",
            headers=SESSION)
        assert resp.status_code == 200
