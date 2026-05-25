"""
tests/test_deployment.py

Live production deployment verification.
Hits the actual Render backend and the production frontend — not mocks.

Run selectively: pytest -m deployment
Skipped in normal CI runs (these call live URLs with cold-start latency).

Three checks:
  1. Render backend health endpoint returns expected production fields
  2. Production frontend serves the React app (HTTP 200)
  3. Frontend /api rewrite correctly proxies to Render (HTTP 200)

May 24 2026 — the production frontend domain moved from
forest-capital.vercel.app to analyticsdesk.app. The old domain now
308-redirects to the new one (Vercel handles the redirect
automatically), but these tests assert HTTP 200, not a redirect,
so the tests must hit the new domain directly. PLATFORM_URL is
the single source of truth for the domain — future moves are a
one-line update here.

Override RENDER_URL or PLATFORM_URL via env vars if a CI run needs
to verify a staging / preview environment instead of production.
"""
import os

import pytest
import httpx

RENDER_URL = os.getenv("RENDER_URL", "https://forest-capital.onrender.com")
PLATFORM_URL = os.getenv("PLATFORM_URL", "https://analyticsdesk.app")

# Render free tier can take 10–30s to wake from cold start.
TIMEOUT = 60


@pytest.mark.deployment
def test_render_health_endpoint():
    """Backend is up, reports production environment, and both AI keys are live."""
    response = httpx.get(f"{RENDER_URL}/api/health", timeout=TIMEOUT)
    assert response.status_code == 200
    data = response.json()
    assert data["environment"] == "production"
    assert data["anthropic"] is True
    assert data["gemini"] is True


@pytest.mark.deployment
def test_vercel_frontend_serves():
    """Production frontend (analyticsdesk.app) is live and serves the React app."""
    response = httpx.get(PLATFORM_URL, timeout=TIMEOUT)
    assert response.status_code == 200


@pytest.mark.deployment
def test_vercel_api_rewrite():
    """
    Frontend /api/:path* rewrite correctly proxies to the Render backend.
    Calling /api/health through the production domain must return the
    same 200 the backend serves. This verifies the rewrite rule in
    frontend/vercel.json is active.
    """
    response = httpx.get(f"{PLATFORM_URL}/api/health", timeout=TIMEOUT)
    assert response.status_code == 200
