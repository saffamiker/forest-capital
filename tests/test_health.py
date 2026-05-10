"""
Sprint 1 — Health endpoint tests.
Uses FastAPI TestClient to verify GET /api/health without a live server.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "michael_dev_key_here")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)

import pytest
from fastapi.testclient import TestClient
from main import app
from config import MASTER_API_KEY

client = TestClient(app)


# ── Health endpoint ───────────────────────────────────────────────────────────

def test_health_returns_200():
    response = client.get("/api/health")
    assert response.status_code == 200

def test_health_response_is_json():
    response = client.get("/api/health")
    data = response.json()
    assert isinstance(data, dict)

def test_health_has_status_field():
    response = client.get("/api/health")
    assert "status" in response.json()

def test_health_status_is_ok():
    response = client.get("/api/health")
    assert response.json()["status"] == "ok"

def test_health_has_anthropic_field():
    response = client.get("/api/health")
    assert "anthropic" in response.json()

def test_health_has_gemini_field():
    response = client.get("/api/health")
    assert "gemini" in response.json()

def test_health_has_cache_field():
    response = client.get("/api/health")
    assert "cache" in response.json()

def test_health_has_environment_field():
    response = client.get("/api/health")
    assert "environment" in response.json()

def test_health_has_sprint_field():
    response = client.get("/api/health")
    assert "sprint" in response.json()

def test_health_anthropic_is_bool():
    response = client.get("/api/health")
    assert isinstance(response.json()["anthropic"], bool)

def test_health_gemini_is_bool():
    response = client.get("/api/health")
    assert isinstance(response.json()["gemini"], bool)

def test_health_cache_is_true():
    response = client.get("/api/health")
    assert response.json()["cache"] is True

def test_health_no_auth_required():
    """Health endpoint must be accessible without X-API-Key."""
    response = client.get("/api/health")
    assert response.status_code == 200

def test_health_environment_field_is_string():
    response = client.get("/api/health")
    assert isinstance(response.json()["environment"], str)


# ── Auth endpoints (no auth required) ────────────────────────────────────────

def test_request_magic_link_known_email_returns_200():
    """Any queued email (even authorised) always returns 200 — no enumeration."""
    response = client.post(
        "/api/auth/request-link",
        json={"email": "ruurdsm@queens.edu"},
    )
    assert response.status_code == 200

def test_request_magic_link_unknown_email_also_returns_200():
    """Unknown emails must return 200 to prevent email enumeration."""
    response = client.post(
        "/api/auth/request-link",
        json={"email": "attacker@evil.com"},
    )
    assert response.status_code == 200

def test_request_magic_link_response_is_generic():
    """The response body must not reveal whether the email is registered."""
    r1 = client.post("/api/auth/request-link", json={"email": "ruurdsm@queens.edu"})
    r2 = client.post("/api/auth/request-link", json={"email": "nobody@evil.com"})
    assert r1.json()["message"] == r2.json()["message"]


# ── Protected endpoints require auth ─────────────────────────────────────────

def test_backtest_compare_without_auth_returns_401():
    response = client.get("/api/backtest/compare")
    assert response.status_code == 401

def test_regime_current_without_auth_returns_401():
    response = client.get("/api/regime/current")
    assert response.status_code == 401

def test_strategies_list_without_auth_returns_401():
    response = client.get("/api/strategies/list")
    assert response.status_code == 401


# ── Master key grants access ──────────────────────────────────────────────────

def test_backtest_compare_with_master_key_returns_200():
    response = client.get(
        "/api/backtest/compare",
        headers={"X-API-Key": MASTER_API_KEY},
    )
    assert response.status_code == 200

def test_backtest_compare_returns_10_strategies():
    response = client.get(
        "/api/backtest/compare",
        headers={"X-API-Key": MASTER_API_KEY},
    )
    data = response.json()
    assert "strategies" in data
    assert len(data["strategies"]) == 10

def test_regime_with_master_key_returns_200():
    response = client.get(
        "/api/regime/current",
        headers={"X-API-Key": MASTER_API_KEY},
    )
    assert response.status_code == 200
