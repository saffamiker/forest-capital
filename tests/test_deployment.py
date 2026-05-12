"""
tests/test_deployment.py

Live production deployment verification.
Hits the actual Render backend and Vercel frontend — not mocks.

Run selectively: pytest -m deployment
Skipped in normal CI runs (these call live URLs with cold-start latency).

Three checks:
  1. Render backend health endpoint returns expected production fields
  2. Vercel frontend serves the React app (HTTP 200)
  3. Vercel /api rewrite correctly proxies to Render (HTTP 200)
"""
import pytest
import httpx

RENDER_URL = "https://forest-capital.onrender.com"
VERCEL_URL = "https://forest-capital.vercel.app"

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
    """Vercel deployment is live and serves the React app."""
    response = httpx.get(VERCEL_URL, timeout=TIMEOUT)
    assert response.status_code == 200


@pytest.mark.deployment
def test_vercel_api_rewrite():
    """
    Vercel /api/:path* rewrite is correctly proxying to the Render backend.
    Calling /api/health through Vercel must return the same 200 the backend serves.
    This verifies the rewrite rule in frontend/vercel.json is active.
    """
    response = httpx.get(f"{VERCEL_URL}/api/health", timeout=TIMEOUT)
    assert response.status_code == 200
