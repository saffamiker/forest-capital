"""
tests/test_explainer_endpoint.py

Tests for the inline metric explainer — POST /api/council/explain and
the "explain" agent-interaction logging path.

The endpoint contract tests run everywhere (test env streams a
deterministic mock). The DB round-trip test skips cleanly without a
live database, the same pattern as test_activity.py.
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
TEAM_EMAIL = "ruurdsm@queens.edu"
SESSION_HEADERS = {"X-API-Key": generate_session_token(TEAM_EMAIL)}


def _run(coro):
    return asyncio.run(coro)


_db_ready_cache: bool | None = None


def _db_ready() -> bool:
    global _db_ready_cache
    if _db_ready_cache is not None:
        return _db_ready_cache
    try:
        from tools.cache import _DB_AVAILABLE
        if not _DB_AVAILABLE:
            _db_ready_cache = False
            return False
        from database import engine, AsyncSessionLocal
        from sqlalchemy import text

        async def _probe() -> bool:
            if engine is not None:
                await engine.dispose()
            async with AsyncSessionLocal() as s:  # type: ignore[union-attr]
                await s.execute(text("SELECT 1 FROM agent_interactions LIMIT 1"))
            return True

        _db_ready_cache = _run(_probe())
    except Exception:
        _db_ready_cache = False
    return _db_ready_cache


# ── Endpoint contract ─────────────────────────────────────────────────────────

class TestExplainEndpoint:
    def test_returns_200_with_valid_metric_and_auth(self):
        resp = client.post("/api/council/explain",
                            json={"metric": "Sharpe Ratio",
                                  "current_value": "0.63",
                                  "context": "academic_project"},
                            headers=SESSION_HEADERS)
        assert resp.status_code == 200
        # Test env streams the deterministic mock explanation.
        assert "Sharpe Ratio" in resp.text

    def test_returns_401_without_authentication(self):
        resp = client.post("/api/council/explain", json={"metric": "DSR"})
        assert resp.status_code == 401

    def test_returns_422_when_metric_missing(self):
        resp = client.post("/api/council/explain", json={},
                            headers=SESSION_HEADERS)
        assert resp.status_code == 422


# ── Interaction logging ───────────────────────────────────────────────────────

class TestExplainInteractionLogging:
    def test_explain_is_a_valid_interaction_type(self):
        # The endpoint logs interaction_type "explain"; it must be in the
        # validated set or log_agent_interaction would drop the row.
        from tools.activity_log import _INTERACTION_TYPES
        assert "explain" in _INTERACTION_TYPES

    def test_explain_logged_for_team_not_for_non_team(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.activity_log import log_agent_interaction
        from sqlalchemy import text

        async def scenario():
            from database import engine, AsyncSessionLocal
            await engine.dispose()
            sid = str(uuid.uuid4())
            try:
                # Team member — the explain interaction is recorded.
                ok = await log_agent_interaction(
                    user_email=TEAM_EMAIL, session_id=sid,
                    session_type="analytical", interaction_type="explain",
                    question_text="Sharpe Ratio")
                assert ok is True
                # Non-team user (Dr. Panttser) — gated out, no row.
                ok2 = await log_agent_interaction(
                    user_email="panttserk@queens.edu", session_id=sid,
                    session_type="analytical", interaction_type="explain",
                    question_text="Sharpe Ratio")
                assert ok2 is False
            finally:
                async with AsyncSessionLocal() as s:
                    await s.execute(
                        text("DELETE FROM agent_interactions "
                             "WHERE session_id = :sid"), {"sid": sid})
                    await s.commit()

        _run(scenario())
