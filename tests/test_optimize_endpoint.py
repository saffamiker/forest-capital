"""
tests/test_optimize_endpoint.py

Endpoint-level tests for POST /api/optimize/weights.

Context — these tests were added after a report that the endpoint
returned 401 "Provide X-API-Key header" even with a valid session
token. Investigation showed the route was already correct: it uses
Depends(require_auth) — the same session-token dependency as every
other user-facing endpoint — and require_auth reads the session token
from the `X-API-Key` header (the app-wide convention; it is NOT
master-key-only auth). The 401 was a client sending the token in a
non-existent `X-Session-Token` header.

These tests pin the auth contract so the route's dependency can never
silently drift to require_master_key, and so the "session token goes
in X-API-Key" convention is documented in CI.
"""
from __future__ import annotations

import os
import sys

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

TEST_EMAIL = "ruurdsm@queens.edu"
SESSION_TOKEN = generate_session_token(TEST_EMAIL)
# The app-wide convention: the session token travels in the X-API-Key
# header. require_auth accepts a session token OR the master key here.
SESSION_HEADERS = {"X-API-Key": SESSION_TOKEN}


class TestOptimizeWeightsAuth:
    """The route must accept a normal user session token — it must NOT
    require the master/developer key."""

    def test_accepts_session_token_via_x_api_key(self):
        """A logged-in user's session token in X-API-Key → 200.
        This is the path the frontend actually uses
        (axios.defaults.headers.common['X-API-Key'] = token)."""
        r = client.post(
            "/api/optimize/weights",
            json={"method": "MAX_SHARPE"},
            headers=SESSION_HEADERS,
        )
        assert r.status_code == 200, (
            f"Session token via X-API-Key must be accepted; got "
            f"{r.status_code}: {r.text[:200]}"
        )

    def test_rejects_missing_auth_header(self):
        """No auth header at all → 401."""
        r = client.post("/api/optimize/weights", json={"method": "MAX_SHARPE"})
        assert r.status_code == 401

    def test_session_token_in_wrong_header_is_not_read(self):
        """Pins the reported bug's true cause: the backend reads ONLY
        the X-API-Key header. A token placed in X-Session-Token is
        ignored — require_auth then sees no X-API-Key and 401s. This is
        client error, not a route bug, and it would happen identically
        on every endpoint."""
        r = client.post(
            "/api/optimize/weights",
            json={"method": "MAX_SHARPE"},
            headers={"X-Session-Token": SESSION_TOKEN},
        )
        assert r.status_code == 401
        assert "X-API-Key" in r.json().get("detail", "")

    def test_master_key_also_accepted(self):
        """require_auth also accepts the master key — the developer path.
        Confirms the route is on require_auth (token OR master), not
        require_master_key (master only)."""
        r = client.post(
            "/api/optimize/weights",
            json={"method": "MAX_SHARPE"},
            headers={"X-API-Key": os.environ["MASTER_API_KEY"]},
        )
        assert r.status_code == 200


class TestOptimizeWeightsContract:
    """Basic request/response contract checks for the endpoint."""

    def test_unknown_method_returns_422(self):
        r = client.post(
            "/api/optimize/weights",
            json={"method": "NOT_A_REAL_METHOD"},
            headers=SESSION_HEADERS,
        )
        assert r.status_code == 422

    @pytest.mark.parametrize(
        "method",
        ["MEAN_VARIANCE", "RISK_PARITY", "MIN_VARIANCE",
         "BLACK_LITTERMAN", "MAX_SHARPE", "MIN_DRAWDOWN"],
    )
    def test_all_six_methods_accepted(self, method: str):
        """Every method in the optimizer's valid set must be accepted by
        the endpoint's method allow-list."""
        r = client.post(
            "/api/optimize/weights",
            json={"method": method},
            headers=SESSION_HEADERS,
        )
        assert r.status_code == 200

    def test_response_has_weights_and_frontier(self):
        """In ENVIRONMENT=test the endpoint returns the mock payload —
        it must still carry the keys the frontend reads."""
        r = client.post(
            "/api/optimize/weights",
            json={"method": "MAX_SHARPE"},
            headers=SESSION_HEADERS,
        )
        body = r.json()
        assert "weights" in body
        assert "efficient_frontier" in body
        assert body["method"] == "MAX_SHARPE"

    def test_efficient_frontier_is_structured_not_a_flat_list(self):
        """The EfficientFrontier component destructures
        {frontier_points, portfolio_points, ...}. A flat list left the
        chart blank — pin the structured shape in both real and mock paths."""
        r = client.post(
            "/api/optimize/weights",
            json={"method": "MAX_SHARPE"},
            headers=SESSION_HEADERS,
        )
        ef = r.json()["efficient_frontier"]
        assert isinstance(ef, dict), "efficient_frontier must be an object, not a list"
        assert "frontier_points" in ef
        assert "portfolio_points" in ef


class TestBuildEfficientFrontier:
    """Unit tests for the reshape that turns the optimizer's flat sweep
    into the component's structured payload. Pure function — no DB, no
    event loop — so the previously endpoint-only logic is now covered."""

    def test_renames_return_to_expected_return(self):
        """The optimizer keys annualised return as `return`; the component
        reads `expected_return`. The reshape must rename it."""
        from main import _build_efficient_frontier
        raw = [{"volatility": 0.10, "return": 0.08, "sharpe": 0.8}]
        out = _build_efficient_frontier(raw, [])
        assert out["frontier_points"][0]["expected_return"] == 0.08
        assert "return" not in out["frontier_points"][0]

    def test_empty_sweep_yields_none_reference_points(self):
        """An empty frontier must not raise on max()/min() — the
        decorative reference points are simply absent."""
        from main import _build_efficient_frontier
        out = _build_efficient_frontier([], [])
        assert out["frontier_points"] == []
        assert out["max_sharpe_point"] is None
        assert out["min_variance_point"] is None

    def test_malformed_point_is_skipped_not_raised_on(self):
        """A point dict missing a key must be skipped, never KeyError."""
        from main import _build_efficient_frontier
        raw = [
            {"volatility": 0.10, "return": 0.08, "sharpe": 0.8},
            {"volatility": 0.12},  # missing `return` — skip, do not raise
        ]
        out = _build_efficient_frontier(raw, [])
        assert len(out["frontier_points"]) == 1

    def test_reference_points_derived_from_sweep(self):
        """max_sharpe_point = highest sharpe; min_variance_point = lowest vol."""
        from main import _build_efficient_frontier
        raw = [
            {"volatility": 0.08, "return": 0.05, "sharpe": 0.5},
            {"volatility": 0.14, "return": 0.13, "sharpe": 0.9},
            {"volatility": 0.20, "return": 0.15, "sharpe": 0.7},
        ]
        out = _build_efficient_frontier(raw, [])
        assert out["max_sharpe_point"]["sharpe"] == 0.9
        assert out["min_variance_point"]["volatility"] == 0.08

    def test_portfolio_points_passed_through_unchanged(self):
        """portfolio_points come from strategy_results_cache — the reshape
        forwards them verbatim, it does not recompute them."""
        from main import _build_efficient_frontier
        pts = [{"strategy": "BENCHMARK", "volatility": 0.19,
                "expected_return": 0.10, "sharpe": 0.52}]
        out = _build_efficient_frontier([], pts)
        assert out["portfolio_points"] == pts
