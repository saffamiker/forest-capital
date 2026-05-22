"""
tests/test_qa_endpoints.py

Smoke-tests for the Sprint 6 tiered-QA endpoints:
  GET  /api/v1/qa/status        — nav badge poll target
  POST /api/v1/qa/run            — Tier 1 sync + Tier 2 background
  POST /api/v1/qa/full-review    — Tier 3 manual (Opus)

Test environment forces the deterministic mock paths so we don't make
real LLM or DB calls. The endpoints exist only so the frontend can wire
up reliably — the verdict math is tested in test_qa_tiered.py.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "test-master-key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


def _auth_headers() -> dict:
    """
    Build the X-API-Key header from the SAME value the server loaded.

    Hardcoding "test-master-key" failed in CI because the workflow sets
    MASTER_API_KEY=test_master_key (underscore variant). os.environ.setdefault
    above is a no-op when the var is already set, so the hardcoded hyphenated
    header didn't match the underscore-cased value config.py had loaded —
    every authenticated request returned 401.

    Importing from config means we send whatever the server actually checks
    against, regardless of which variant the surrounding environment chose.
    """
    from config import MASTER_API_KEY  # type: ignore[import]
    return {"X-API-Key": MASTER_API_KEY}


@pytest.fixture
def client() -> TestClient:
    from main import app  # noqa: WPS433
    return TestClient(app)


class TestQAStatusEndpoint:
    """GET /api/v1/qa/status returns the nav-badge contract."""

    def test_status_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/v1/qa/status", headers=_auth_headers())
        assert r.status_code == 200

    def test_status_payload_has_required_keys(self, client: TestClient) -> None:
        r = client.get("/api/v1/qa/status", headers=_auth_headers())
        body = r.json()
        required = {
            "verdict", "tier", "run_at", "age_hours",
            "strategy_hash", "present_mode_allowed", "running",
        }
        missing = required - set(body.keys())
        assert not missing, f"QA status missing keys: {missing}"

    def test_status_verdict_is_known_value(self, client: TestClient) -> None:
        r = client.get("/api/v1/qa/status", headers=_auth_headers())
        body = r.json()
        assert body["verdict"] in ("PASS", "WARN", "FAIL", "UNKNOWN")

    def test_status_present_mode_allowed_is_bool(self, client: TestClient) -> None:
        r = client.get("/api/v1/qa/status", headers=_auth_headers())
        assert isinstance(r.json()["present_mode_allowed"], bool)


class TestQARunEndpoint:
    """POST /api/v1/qa/run — Tier 1 synchronous + Tier 2 fire-and-forget."""

    def test_run_returns_200(self, client: TestClient) -> None:
        r = client.post("/api/v1/qa/run", headers=_auth_headers())
        assert r.status_code == 200

    def test_run_returns_tier_1(self, client: TestClient) -> None:
        r = client.post("/api/v1/qa/run", headers=_auth_headers())
        # In test env this returns the mock payload tier=1
        body = r.json()
        assert body.get("tier") == 1


class TestQAFullReviewEndpoint:
    """POST /api/v1/qa/full-review — Tier 3 deep review (Opus)."""

    def test_full_review_returns_200(self, client: TestClient) -> None:
        r = client.post("/api/v1/qa/full-review", headers=_auth_headers())
        assert r.status_code == 200

    def test_full_review_returns_tier_3(self, client: TestClient) -> None:
        r = client.post("/api/v1/qa/full-review", headers=_auth_headers())
        body = r.json()
        assert body.get("tier") == 3


class TestMigration003Importable:
    """Migration 003 must load cleanly before alembic upgrade head runs."""

    def test_migration_module_imports(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "m003",
            os.path.join(os.path.dirname(__file__), "..", "backend", "migrations",
                         "versions", "003_create_qa_results_cache.py"),
        )
        assert spec is not None
        assert spec.loader is not None
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        assert m.revision == "003"
        assert m.down_revision == "002"
        assert callable(m.upgrade)
        assert callable(m.downgrade)


class TestQAFlagForFix:
    """POST /api/v1/qa/findings/{check_id}/flag-for-fix — auth gating
    and request-validation contract. The DB-touching path is covered
    by the migration test below + the frontend tests; without a real
    DB the endpoint either returns 503 (database unavailable) or 500
    (the test environment's no-Postgres no-op). Either response means
    "would have created the triage row if the DB were live" and is
    enough to pin the route contract."""

    def test_rejects_unauthenticated(self, client: TestClient):
        r = client.post(
            "/api/v1/qa/findings/P03/flag-for-fix",
            json={"check_title": "test"},
        )
        assert r.status_code == 401

    def test_rejects_oversized_check_id(self, client: TestClient):
        r = client.post(
            "/api/v1/qa/findings/" + "X" * 50 + "/flag-for-fix",
            headers=_auth_headers(),
            json={"check_title": "test"},
        )
        assert r.status_code == 422
        assert "check_id" in r.json().get("detail", "").lower()

    def test_accepts_team_member_request(self, client: TestClient):
        # The master key bypasses team_member gating (developer role).
        # We assert the endpoint accepts the request without 401/403/
        # 422 — it may 500/503 on the DB write in the no-DB test env,
        # but the auth + validation contract must pass.
        r = client.post(
            "/api/v1/qa/findings/P03/flag-for-fix",
            headers=_auth_headers(),
            json={
                "check_title": "Transaction costs applied",
                "finding": "Turnover sums |Δw|.",
                "implication": "Could be intentional.",
                "remediation": "Confirm intent.",
                "severity": "major",
            },
        )
        assert r.status_code not in (401, 403, 422)


class TestQAMarkIntentional:
    """POST /api/v1/qa/findings/{check_id}/mark-intentional — same
    contract surface as flag-for-fix above."""

    def test_rejects_unauthenticated(self, client: TestClient):
        r = client.post(
            "/api/v1/qa/findings/P03/mark-intentional",
            json={"note": "intentional"},
        )
        assert r.status_code == 401

    def test_rejects_empty_check_id(self, client: TestClient):
        # FastAPI rejects empty path segments before the handler runs,
        # so this lands as a 404 (no matching route). Either 404 or
        # 422 is acceptable — both mean "did not reach the handler".
        r = client.post(
            "/api/v1/qa/findings//mark-intentional",
            headers=_auth_headers(),
            json={"note": "test"},
        )
        assert r.status_code in (404, 422)

    def test_accepts_team_member_request(self, client: TestClient):
        r = client.post(
            "/api/v1/qa/findings/P03/mark-intentional",
            headers=_auth_headers(),
            json={"note": "Reviewed; intentional double-sided capture."},
        )
        assert r.status_code not in (401, 403, 422)


class TestQAIntentionalOverridesList:
    """GET /api/v1/qa/intentional-overrides — auth + fail-open contract."""

    def test_rejects_unauthenticated(self, client: TestClient):
        r = client.get("/api/v1/qa/intentional-overrides")
        assert r.status_code == 401

    def test_returns_overrides_envelope_fail_open(self, client: TestClient):
        # Without a DB the endpoint must still return the {"overrides": {}}
        # envelope so the frontend's loadOverrides() does not crash on
        # an unexpected response shape.
        r = client.get(
            "/api/v1/qa/intentional-overrides",
            headers=_auth_headers(),
        )
        assert r.status_code == 200
        body = r.json()
        assert "overrides" in body
        assert isinstance(body["overrides"], dict)


class TestMigration027Loads:
    """The qa_intentional_overrides migration loads, has the expected
    revision identifiers, and exposes callable upgrade/downgrade."""

    def test_loads(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "mig_027",
            os.path.join(os.path.dirname(__file__), "..", "backend",
                         "migrations", "versions",
                         "027_qa_intentional_overrides.py"),
        )
        assert spec is not None
        assert spec.loader is not None
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        assert m.revision == "027"
        assert m.down_revision == "026"
        assert callable(m.upgrade)
        assert callable(m.downgrade)
