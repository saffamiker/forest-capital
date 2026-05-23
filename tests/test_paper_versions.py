"""tests/test_paper_versions.py — version control + collaborative
editing primitives for report paper_md.

May 23 2026 (item 2 — collaborative editing + version control).

Covers tools/paper_versions.py + the three version endpoints. The
DB write paths exercise their fail-open contracts (no DB → safe
default) and the endpoint gating layer; the actual SQL is exercised
by the CI integration suite that runs against a live Postgres.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")


from tools import paper_versions as pv  # noqa: E402


# ── Fail-open without a database ─────────────────────────────────────────────


class TestFailOpenWithoutDatabase:
    """Every reader returns a safe default when the DB is down so
    the frontend renders an empty state rather than 500ing."""

    def test_list_versions_returns_empty(self, monkeypatch):
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        assert asyncio.run(pv.list_versions(99)) == []

    def test_get_version_returns_none(self, monkeypatch):
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        assert asyncio.run(pv.get_version(99, 1)) is None

    def test_check_revision_returns_none(self, monkeypatch):
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        assert asyncio.run(pv.check_revision(99)) is None

    def test_save_version_returns_none(self, monkeypatch):
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        out = asyncio.run(pv.save_version(
            99, "x", saved_by_email="a@b.c"))
        assert out is None

    def test_bump_paper_revision_returns_none(self, monkeypatch):
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        assert asyncio.run(pv.bump_paper_revision(99)) is None


# ── restore_version short-circuit ────────────────────────────────────────────


class TestRestoreShortCircuits:
    """The restore path threads through three internal calls. Any
    one of them failing must return None — never partially apply."""

    def test_returns_none_when_source_version_missing(self, monkeypatch):
        # Force get_version to return None — restore_version must
        # short-circuit without writing anything.
        async def _none(gid, vn): return None
        monkeypatch.setattr(pv, "get_version", _none)
        out = asyncio.run(pv.restore_version(
            1, 5, reviewer_email="bob@queens.edu"))
        assert out is None

    def test_returns_none_when_update_paper_md_fails(self, monkeypatch):
        # Source exists, but the paper_md write fails. Nothing must
        # be persisted to the version table — otherwise the history
        # would show a "restore" that didn't actually take effect.
        async def _src(gid, vn):
            return {
                "paper_md": "restored body", "flag_count": 0,
                "word_counts": {}, "version_number": vn,
            }

        async def _failing_update(*a, **kw): return False

        monkeypatch.setattr(pv, "get_version", _src)
        import tools.report_generator as rg
        monkeypatch.setattr(rg, "_update_paper_md", _failing_update)
        out = asyncio.run(pv.restore_version(
            1, 5, reviewer_email="bob@queens.edu"))
        assert out is None


# ── Endpoint gating ──────────────────────────────────────────────────────────


class TestVersionEndpointGating:
    """All three endpoints require team_member. Unauthenticated is
    a 401; a viewer-tier session is a 403."""

    def _client(self):
        from fastapi.testclient import TestClient
        from main import app
        return TestClient(app)

    def test_list_versions_unauthenticated_is_401(self):
        c = self._client()
        r = c.get("/api/v1/reports/generations/1/versions")
        assert r.status_code == 401

    def test_save_version_unauthenticated_is_401(self):
        c = self._client()
        r = c.post("/api/v1/reports/generations/1/versions",
                   json={"label": "v1"})
        assert r.status_code == 401

    def test_restore_version_unauthenticated_is_401(self):
        c = self._client()
        r = c.post("/api/v1/reports/generations/1/versions/1/restore")
        assert r.status_code == 401


# ── Concurrent-edit detection ────────────────────────────────────────────────


class TestConcurrentEditDetection:
    """The PATCH paper-md endpoint accepts expected_revision and
    returns 409 if the row's actual revision has moved past it."""

    def test_revision_mismatch_is_409(self, monkeypatch):
        # Mock auth + the update_paper_md call so the endpoint
        # reaches the mismatch branch with no DB.
        from auth import require_team_member
        from main import app
        import tools.report_generator as rg

        async def _fake_team():
            return {"email": "bob@queens.edu",
                    "permissions": ["team_member"]}

        async def _mismatch_result(gid, paper_md, **kwargs):
            return {
                "error":             "revision_mismatch",
                "current_revision":  7,
                "expected_revision": 5,
            }

        app.dependency_overrides[require_team_member] = _fake_team
        # Temporarily flip the environment so the endpoint reaches
        # update_paper_md instead of the test-env short-circuit.
        monkeypatch.setenv("ENVIRONMENT", "development")
        import main as main_mod
        monkeypatch.setattr(main_mod, "ENVIRONMENT", "development")
        monkeypatch.setattr(rg, "update_paper_md", _mismatch_result)
        try:
            from fastapi.testclient import TestClient
            c = TestClient(app)
            r = c.patch(
                "/api/v1/reports/generations/1/paper-md",
                json={
                    "paper_md":          "new text",
                    "expected_revision": 5,
                })
            assert r.status_code == 409
            detail = r.json()["detail"]
            assert detail["current_revision"] == 7
            assert detail["expected_revision"] == 5
            assert "concurrent" in detail["message"].lower() \
                or "another reviewer" in detail["message"].lower()
        finally:
            app.dependency_overrides.pop(require_team_member, None)

    def test_missing_paper_md_is_422(self, monkeypatch):
        from auth import require_team_member
        from main import app

        async def _fake_team():
            return {"email": "bob@queens.edu",
                    "permissions": ["team_member"]}

        app.dependency_overrides[require_team_member] = _fake_team
        # Flip to dev so the validation branch runs (the test-env
        # short-circuit returns 200 regardless of body content,
        # intentionally — the auto-save loop relies on that path).
        monkeypatch.setenv("ENVIRONMENT", "development")
        import main as main_mod
        monkeypatch.setattr(main_mod, "ENVIRONMENT", "development")
        try:
            from fastapi.testclient import TestClient
            c = TestClient(app)
            r = c.patch(
                "/api/v1/reports/generations/1/paper-md",
                json={"expected_revision": 5})
            assert r.status_code == 422
        finally:
            app.dependency_overrides.pop(require_team_member, None)
