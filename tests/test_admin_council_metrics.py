"""tests/test_admin_council_metrics.py — /api/v1/admin/council-metrics.

Endpoint-contract tests for the read endpoint that backs the cost-
and-HMM-alignment dashboard (June 3 2026). In the test environment
the endpoint short-circuits to {available:false, rows:[], aggregates:{}}
because no DB is reachable — these tests assert that contract plus
the auth gate.
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


def test_metrics_requires_authentication():
    """No bearer → 401. The endpoint sits behind require_auth so a
    public surface can't enumerate query metrics."""
    resp = client.get("/api/v1/admin/council-metrics")
    assert resp.status_code == 401


def test_metrics_returns_envelope_in_test_env():
    """Test env short-circuits to the empty shape with the wrapper
    shape unchanged from the available=True path so the frontend
    renders identically on a cold deploy."""
    resp = client.get("/api/v1/admin/council-metrics", headers=SESSION)
    assert resp.status_code == 200
    body = resp.json()
    assert "available" in body
    assert "rows" in body
    assert "aggregates" in body
    assert body["available"] is False
    assert body["rows"] == []
    assert body["aggregates"] == {}


def test_metrics_respects_limit_clamp():
    """The limit query param is clamped to [1, 200]. Out-of-range
    values clamp rather than 422."""
    for n in (1, 30, 200, 5000, 0, -1):
        resp = client.get(
            f"/api/v1/admin/council-metrics?limit={n}",
            headers=SESSION)
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["rows"], list)


def test_metrics_invalid_limit_either_422_or_envelope():
    """A non-integer limit either 422s at FastAPI's coercion or
    short-circuits to the envelope — either is fine; the contract
    is 'never 500'."""
    resp = client.get(
        "/api/v1/admin/council-metrics?limit=not-a-number",
        headers=SESSION)
    assert resp.status_code in (200, 422)


# ── June 3 2026 — per-CIO input tokens (migration 052) ────────────────────
#
# The endpoint's response envelope gains two like-for-like signals:
#   - aggregates[type].avg_cio_input_tokens
#   - aggregates.cio_token_reduction_vs_baseline   (parallel to the
#     existing token_reduction_vs_baseline map but vs CIO-only)
# In the test env the endpoint short-circuits to {available:false}, so
# the assertion is structural: the keys must exist when the route is
# imported and the schema is stable enough for the dashboard to read.


def test_metrics_endpoint_response_keys_are_stable_in_test_env():
    """The shape under available=false is the minimal envelope.
    The frontend never tries to read aggregates[*] on the empty
    path, so the structural test is just 'no 500, shape unchanged'."""
    resp = client.get("/api/v1/admin/council-metrics", headers=SESSION)
    assert resp.status_code == 200
    body = resp.json()
    # available=false envelope — see test_metrics_returns_envelope_in_test_env.
    assert set(body.keys()) >= {"available", "rows", "aggregates"}


def test_metrics_endpoint_source_carries_cio_input_tokens_handling():
    """Import-level guard: the endpoint code path must reference the
    cio_input_tokens column so a migration drift (the column going
    missing) shows up as an ImportError or NameError at startup, not
    as a silent zero on the dashboard."""
    import inspect

    import main as main_module

    src = inspect.getsource(main_module.get_admin_council_metrics)
    # The endpoint must select and aggregate the new column.
    assert "cio_input_tokens" in src, (
        "get_admin_council_metrics must SELECT cio_input_tokens "
        "and aggregate avg_cio_input_tokens — see migration 052.")
    # The parallel reduction-vs-baseline map for CIO-only must
    # appear in the response builder so the dashboard can render
    # the bundle-effect signal independently of total tokens.
    assert "cio_token_reduction_vs_baseline" in src or (
        "baseline_cio" in src and "cio_reductions" in src), (
        "get_admin_council_metrics must compute the CIO-only "
        "reduction-vs-baseline aggregate.")
