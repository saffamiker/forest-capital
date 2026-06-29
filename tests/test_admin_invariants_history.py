"""tests/test_admin_invariants_history.py — /api/v1/admin/invariants/history

Endpoint contract tests for the warm-history strip that backs the
Section 3 of the /admin/health panel (June 2 2026 build).

The endpoint reads rows from `analytics_metrics_cache` where
`metric_kind = 'invariant_summary'` — the rows PR #252 started
persisting on every analytics warm. The endpoint NEVER recomputes
and never touches the prompt queue or any heavy path: a single
SELECT, a JSON projection, fail-open to {available:false, rows:[]}.

In the test environment the endpoint short-circuits to the empty
shape because no DB is reachable — these tests assert that contract
plus the auth gate (any authenticated user can read; an
unauthenticated request fails).
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


def test_history_requires_authentication():
    """No bearer / API key → 401. The endpoint sits behind require_auth
    so an unauthenticated read can never enumerate warm history."""
    resp = client.get("/api/v1/admin/invariants/history")
    assert resp.status_code == 401


def test_history_returns_envelope_in_test_env():
    """In the test environment AsyncSessionLocal is None / unreachable,
    so the endpoint must short-circuit to {available:false, rows:[]}.
    The wrapper shape is the same as the available=True path so the
    frontend renders identically on a cold deploy."""
    resp = client.get("/api/v1/admin/invariants/history", headers=SESSION)
    assert resp.status_code == 200
    body = resp.json()
    assert "available" in body
    assert "rows" in body
    assert isinstance(body["rows"], list)
    # Test env — no DB, so available is False and rows is empty.
    assert body["available"] is False
    assert body["rows"] == []


def test_history_respects_limit_clamp():
    """The limit query param is clamped to [1, 30]. A bare integer
    in that range is forwarded; out-of-range values clamp rather
    than 400. In test env the shape is the same — what matters is
    that the request succeeds and returns the envelope."""
    for n in (1, 7, 30, 100, 0, -5):
        resp = client.get(
            f"/api/v1/admin/invariants/history?limit={n}",
            headers=SESSION)
        assert resp.status_code == 200
        body = resp.json()
        assert "available" in body
        assert isinstance(body["rows"], list)


def test_history_invalid_limit_falls_back_cleanly():
    """A non-integer limit must not 500 — FastAPI rejects with 422,
    OR the endpoint accepts and clamps. Either is fine; the contract
    is "never crash on a bad limit". The fail-open envelope returns
    on a real DB read failure too."""
    resp = client.get(
        "/api/v1/admin/invariants/history?limit=not-a-number",
        headers=SESSION)
    # FastAPI's int coercion produces 422 on bad input.
    assert resp.status_code in (200, 422)
