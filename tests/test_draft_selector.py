"""tests/test_draft_selector.py — Draft selector backend contract.

May 23 2026. Adds a "Draft" dropdown next to the Template selector
on /reports/writer so Bob can switch between his saved generation
drafts instead of starting fresh every login. PR #104 widened the
pipeline-audit restore window to 14 days, but it only restored the
SINGLE most recent draft — these tests pin the multi-draft listing
endpoint and its auth gate.

Backend surface:
  GET /api/v1/reports/generations?template_id=<id>&limit=<n>
  Returns {drafts: [{id, template_id, flag_count, word_count_total,
                     generated_at, preview}]}
  Auth: team_member only (sames as the rest of /api/v1/reports/).

Helper:
  tools.report_generator.list_generations_for_user(email, limit,
                                                    template_id?)
  Fail-open: returns [] for missing DB / missing email / SQL error.
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


class TestListGenerationsEndpoint:

    def test_list_generations_team_only(self):
        client, team, viewer = _client()
        # Team member — 200 with shape.
        r = client.get("/api/v1/reports/generations", headers=team)
        assert r.status_code == 200
        body = r.json()
        assert "drafts" in body
        # Test env short-circuits to empty list — that's the contract.
        assert isinstance(body["drafts"], list)
        # Viewer — 403.
        r = client.get("/api/v1/reports/generations", headers=viewer)
        assert r.status_code == 403
        # Unauthed — 401.
        r = client.get("/api/v1/reports/generations")
        assert r.status_code == 401

    def test_list_accepts_template_id_filter(self):
        client, team, _ = _client()
        r = client.get(
            "/api/v1/reports/generations"
            "?template_id=midpoint_check_fna670",
            headers=team)
        assert r.status_code == 200
        assert "drafts" in r.json()

    def test_list_accepts_limit_parameter(self):
        client, team, _ = _client()
        r = client.get(
            "/api/v1/reports/generations?limit=5", headers=team)
        assert r.status_code == 200

    def test_list_caps_limit_server_side(self):
        # A pathological caller asking for a million rows must not
        # blow up the response — the endpoint caps at 100. Returns
        # 200 regardless (server clamps silently).
        client, team, _ = _client()
        r = client.get(
            "/api/v1/reports/generations?limit=999999", headers=team)
        assert r.status_code == 200

    def test_list_does_not_collide_with_existing_id_route(self):
        # /api/v1/reports/generations/{id} is a separate endpoint that
        # has shipped since item 12. The new list route at the bare
        # /generations path must not be shadowed by the {id} route.
        # In FastAPI the no-param route is matched first when
        # registered first; verify the list route does respond.
        client, team, _ = _client()
        r = client.get("/api/v1/reports/generations", headers=team)
        assert r.status_code == 200
        # The {id} route still answers (test env short-circuit).
        r = client.get(
            "/api/v1/reports/generations/123", headers=team)
        # Test env returns the test-env shape; either 200 or 404.
        # Both are fine — we just want NOT 405 (Method Not Allowed,
        # which would mean the path was matched to the list route).
        assert r.status_code != 405


class TestListGenerationsForUserHelper:
    """tools.report_generator.list_generations_for_user must fail
    open: no database, no email, or a SQL error all return []
    rather than raising. Tests exercise the no-database path
    (AsyncSessionLocal is None — the same fail-open path the
    existing helpers use)."""

    def test_returns_empty_list_without_database(self, monkeypatch):
        import asyncio
        import database as db_mod
        from tools import report_generator as rg
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        out = asyncio.run(
            rg.list_generations_for_user("thaob@queens.edu", limit=10))
        assert out == []

    def test_returns_empty_list_for_empty_email(self):
        import asyncio
        from tools import report_generator as rg
        # No DB lookup attempted when email is empty — short-circuits.
        out = asyncio.run(rg.list_generations_for_user("", limit=10))
        assert out == []

    def test_returns_empty_list_on_sql_error(self, monkeypatch):
        # Stub AsyncSessionLocal so the helper enters the try block
        # but raises mid-flight. The fail-open path must catch and
        # return [] rather than propagate.
        import asyncio
        from tools import report_generator as rg

        class _ExplodingSession:
            async def __aenter__(self):
                raise RuntimeError("simulated DB blow-up")

            async def __aexit__(self, *a):
                return False

        def _fake_session_local():
            return _ExplodingSession()

        import database as db_mod
        monkeypatch.setattr(
            db_mod, "AsyncSessionLocal", _fake_session_local)
        out = asyncio.run(
            rg.list_generations_for_user("thaob@queens.edu", limit=10))
        assert out == []

    def test_accepts_optional_template_id_filter(self):
        # Helper signature — keyword-only template_id, default None.
        # Calling with the filter must not raise; result is [] in the
        # no-database test env regardless of filter.
        import asyncio
        from tools import report_generator as rg
        out = asyncio.run(rg.list_generations_for_user(
            "thaob@queens.edu", limit=5,
            template_id="midpoint_check_fna670"))
        assert out == []
