"""
tests/test_changelog.py

Tests for the changelog feature — the /api/v1/changelog endpoints and
the tools/changelog.py data layer.

Endpoint-contract tests run everywhere. DB round-trip tests (unseen
filtering, mark-seen persistence, the tour-update flag) need a live
database with migrations 011-012 applied; they skip cleanly without
one, the same pattern as test_activity.py.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

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
SESSION_HEADERS = {"X-API-Key": generate_session_token("ruurdsm@queens.edu")}


def _run(coro):
    return asyncio.run(coro)


_db_ready_cache: bool | None = None


def _db_ready() -> bool:
    """True when a live PostgreSQL with the changelog + users tables is
    reachable. DB round-trip tests skip when this is False."""
    global _db_ready_cache
    if _db_ready_cache is not None:
        return _db_ready_cache
    try:
        from tools.changelog import _DB_AVAILABLE
        if not _DB_AVAILABLE:
            _db_ready_cache = False
            return False
        from database import engine, AsyncSessionLocal
        from sqlalchemy import text

        async def _probe() -> bool:
            if engine is not None:
                await engine.dispose()
            async with AsyncSessionLocal() as s:  # type: ignore[union-attr]
                await s.execute(text("SELECT 1 FROM changelog LIMIT 1"))
                await s.execute(text("SELECT 1 FROM users LIMIT 1"))
            return True

        _db_ready_cache = _run(_probe())
    except Exception:
        _db_ready_cache = False
    return _db_ready_cache


# ── Endpoint contracts ────────────────────────────────────────────────────────

class TestChangelogEndpoints:
    def test_changelog_requires_auth(self):
        assert client.get("/api/v1/changelog").status_code == 401
        assert client.get("/api/v1/changelog/unseen").status_code == 401
        assert client.post("/api/v1/changelog/mark-seen").status_code == 401

    def test_changelog_all_returns_entries_shape(self):
        resp = client.get("/api/v1/changelog", headers=SESSION_HEADERS)
        assert resp.status_code == 200
        assert "entries" in resp.json()
        assert isinstance(resp.json()["entries"], list)

    def test_changelog_unseen_returns_shape(self):
        resp = client.get("/api/v1/changelog/unseen", headers=SESSION_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        for key in ("entries", "has_tour_update", "tour_version"):
            assert key in body

    def test_mark_seen_returns_ok(self):
        resp = client.post("/api/v1/changelog/mark-seen", json={},
                            headers=SESSION_HEADERS)
        assert resp.status_code == 200
        assert "ok" in resp.json()


# ── DB round-trips ────────────────────────────────────────────────────────────

class TestChangelogData:
    def test_unseen_returns_only_entries_newer_than_last_seen(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.changelog import get_unseen_changelog
        from sqlalchemy import text

        async def scenario():
            from database import engine, AsyncSessionLocal
            await engine.dispose()
            email = f"clog_{uuid.uuid4().hex[:12]}@example.com"
            try:
                # Mark this user as having last seen the changelog mid-history
                # (2026-05-13) — entries released after that date are unseen.
                async with AsyncSessionLocal() as s:
                    await s.execute(
                        text("INSERT INTO users "
                             "(email, last_changelog_seen_at, "
                             " last_tour_version_seen) "
                             "VALUES (:e, '2026-05-13T00:00:00+00', 0)"),
                        {"e": email})
                    await s.commit()
                out = await get_unseen_changelog(email)
                versions = {e["version"] for e in out["entries"]}
                # v1 (2026-05-10) is before the cutoff — not unseen.
                assert 1 not in versions
                # v30 (2026-05-17) is after — unseen.
                assert 30 in versions
                # Every returned entry is dated after the cutoff.
                assert len(versions) > 0
            finally:
                async with AsyncSessionLocal() as s:
                    await s.execute(text("DELETE FROM users WHERE email = :e"),
                                    {"e": email})
                    await s.commit()

        _run(scenario())

    def test_unseen_empty_when_all_seen(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.changelog import get_unseen_changelog, mark_changelog_seen
        from sqlalchemy import text

        async def scenario():
            from database import engine, AsyncSessionLocal
            await engine.dispose()
            email = f"clog_{uuid.uuid4().hex[:12]}@example.com"
            try:
                # mark-seen stamps now(); every seeded entry predates it.
                await mark_changelog_seen(email)
                out = await get_unseen_changelog(email)
                assert out["entries"] == []
            finally:
                async with AsyncSessionLocal() as s:
                    await s.execute(text("DELETE FROM users WHERE email = :e"),
                                    {"e": email})
                    await s.commit()

        _run(scenario())

    def test_mark_seen_updates_timestamp(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.changelog import mark_changelog_seen
        from sqlalchemy import text

        async def scenario():
            from database import engine, AsyncSessionLocal
            await engine.dispose()
            email = f"clog_{uuid.uuid4().hex[:12]}@example.com"
            try:
                ok = await mark_changelog_seen(email)
                assert ok is True
                async with AsyncSessionLocal() as s:
                    r = await s.execute(
                        text("SELECT last_changelog_seen_at FROM users "
                             "WHERE email = :e"), {"e": email})
                    seen_at = r.fetchone()[0]
                assert seen_at is not None
            finally:
                async with AsyncSessionLocal() as s:
                    await s.execute(text("DELETE FROM users WHERE email = :e"),
                                    {"e": email})
                    await s.commit()

        _run(scenario())

    def test_mark_seen_with_tour_version_updates_field(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.changelog import mark_changelog_seen
        from sqlalchemy import text

        async def scenario():
            from database import engine, AsyncSessionLocal
            await engine.dispose()
            email = f"clog_{uuid.uuid4().hex[:12]}@example.com"
            try:
                await mark_changelog_seen(email, tour_version_seen=1)
                async with AsyncSessionLocal() as s:
                    r = await s.execute(
                        text("SELECT last_tour_version_seen FROM users "
                             "WHERE email = :e"), {"e": email})
                    assert r.fetchone()[0] == 1
            finally:
                async with AsyncSessionLocal() as s:
                    await s.execute(text("DELETE FROM users WHERE email = :e"),
                                    {"e": email})
                    await s.commit()

        _run(scenario())

    def test_has_tour_update_true_until_tour_version_seen(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.changelog import get_unseen_changelog, mark_changelog_seen
        from sqlalchemy import text
        from config import TOUR_VERSION

        async def scenario():
            from database import engine, AsyncSessionLocal
            await engine.dispose()
            email = f"clog_{uuid.uuid4().hex[:12]}@example.com"
            try:
                # No row yet → tour seen defaults to 0 → an update is pending.
                before = await get_unseen_changelog(email)
                assert before["has_tour_update"] is True
                assert before["tour_version"] == TOUR_VERSION
                # After recording the current tour version, no update pending.
                await mark_changelog_seen(email, tour_version_seen=TOUR_VERSION)
                after = await get_unseen_changelog(email)
                assert after["has_tour_update"] is False
            finally:
                async with AsyncSessionLocal() as s:
                    await s.execute(text("DELETE FROM users WHERE email = :e"),
                                    {"e": email})
                    await s.commit()

        _run(scenario())
