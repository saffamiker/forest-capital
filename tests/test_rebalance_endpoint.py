"""tests/test_rebalance_endpoint.py — Two-pass draft generation
Pass 2 (May 24 2026) contract.

Backend coverage:
  1. /api/v1/reports/generations/{id}/rebalance is registered and
     team_member-gated.
  2. In test env, the endpoint short-circuits with a fixed payload
     (no live writer call).
  3. rebalance_paper fail-open: returns the no-DB shape rather than
     raising when AsyncSessionLocal is None.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault(
    "SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,"
    "murdockm@queens.edu,panttserk@queens.edu")


def _client():
    from fastapi.testclient import TestClient
    from main import app
    from auth import generate_session_token
    client = TestClient(app)
    team = {"X-API-Key": generate_session_token("thaob@queens.edu")}
    viewer = {"X-API-Key": generate_session_token(
        "panttserk@queens.edu")}
    return client, team, viewer


def test_rebalance_route_registered():
    from main import app
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/api/v1/reports/generations/{generation_id}/rebalance" in paths


def test_rebalance_requires_team_member():
    client, _, viewer = _client()
    r = client.post(
        "/api/v1/reports/generations/123/rebalance")
    assert r.status_code == 401
    r = client.post(
        "/api/v1/reports/generations/123/rebalance",
        headers=viewer)
    assert r.status_code == 403


def test_rebalance_test_env_short_circuits():
    # ENVIRONMENT=test path returns the fixed shape so endpoint
    # tests don't need a generation row + a writer key.
    client, team, _ = _client()
    r = client.post(
        "/api/v1/reports/generations/123/rebalance",
        headers=team)
    assert r.status_code == 200
    body = r.json()
    assert body["rebalanced"] is False
    assert "paper_md" in body


def test_rebalance_paper_returns_generation_not_found_without_db(
    monkeypatch,
):
    # The function is fail-open on a cold DB. Confirm the
    # generation_not_found error shape so the endpoint's 404
    # path is exercised independently of test-env short-circuit.
    import asyncio
    import database as db_mod
    from tools import report_generator as rg
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
    out = asyncio.run(rg.rebalance_paper(9999))
    # No DB -> get_generation returns None -> we exit with
    # generation_not_found.
    assert out.get("error") == "generation_not_found"
