"""
tests/test_token_cost.py

AI token-usage logging and cost tracking (migration 020).

Three tiers:
  - config.calculate_cost — pure pricing arithmetic, runs everywhere.
  - agents/usage.py — the per-request ContextVar accumulator; runs
    everywhere (no DB, no network).
  - the cost-summary endpoint contract and the DB round-trip — the
    round-trip skips cleanly when no live database is reachable, the
    same pattern as the rest of the activity suite.
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

import config  # noqa: E402
from main import app  # noqa: E402
from auth import generate_session_token  # noqa: E402

client = TestClient(app)
SESSION_HEADERS = {"X-API-Key": generate_session_token("ruurdsm@queens.edu")}


def _run(coro):
    return asyncio.run(coro)


# ── config.calculate_cost ─────────────────────────────────────────────────────

class TestCalculateCost:
    def test_known_model_arithmetic(self):
        # 1000 in @ $0.000003 + 1000 out @ $0.000015 = $0.018
        cost = config.calculate_cost("claude-sonnet-4-6", 1000, 1000)
        assert cost == pytest.approx(0.018)

    def test_opus_costs_more_than_sonnet(self):
        opus = config.calculate_cost("claude-opus-4-7", 1000, 1000)
        sonnet = config.calculate_cost("claude-sonnet-4-6", 1000, 1000)
        assert opus > sonnet

    def test_dated_model_string_prefix_match(self):
        # A provider may return a dated string — the prefix still resolves.
        cost = config.calculate_cost("claude-haiku-4-5-20251001", 1000, 0)
        assert cost == pytest.approx(0.0008)

    def test_unknown_model_returns_none(self):
        assert config.calculate_cost("some-unknown-model", 1000, 1000) is None

    def test_none_tokens_returns_none(self):
        assert config.calculate_cost("claude-sonnet-4-6", None, 100) is None
        assert config.calculate_cost("claude-sonnet-4-6", 100, None) is None

    def test_non_numeric_tokens_returns_none(self):
        assert config.calculate_cost("claude-sonnet-4-6", "abc", 100) is None

    def test_zero_tokens_is_zero_cost(self):
        assert config.calculate_cost("claude-sonnet-4-6", 0, 0) == 0.0


# ── agents/usage.py — the ContextVar accumulator ──────────────────────────────

class TestUsageAccumulator:
    def test_no_capture_active_is_a_no_op(self):
        # record_usage before any start_usage_capture must not raise, and
        # collect_usage returns the empty shape with null totals.
        from agents.usage import record_usage, collect_usage
        record_usage("claude-sonnet-4-6", 100, 50)
        usage = collect_usage()
        assert usage["input_tokens"] is None
        assert usage["estimated_cost_usd"] is None
        assert usage["per_agent"] == {}

    def test_capture_aggregates_totals(self):
        from agents.usage import (
            start_usage_capture, record_usage, collect_usage,
        )
        start_usage_capture()
        record_usage("claude-sonnet-4-6", 1000, 1000)
        record_usage("claude-sonnet-4-6", 500, 200)
        usage = collect_usage()
        assert usage["input_tokens"] == 1500
        assert usage["output_tokens"] == 1200
        assert usage["estimated_cost_usd"] is not None
        assert usage["estimated_cost_usd"] > 0
        assert usage["model_used"] == "claude-sonnet-4-6"

    def test_multiple_models_reported_as_multiple(self):
        from agents.usage import (
            start_usage_capture, record_usage, collect_usage,
        )
        start_usage_capture()
        record_usage("claude-sonnet-4-6", 100, 100)
        record_usage("claude-opus-4-7", 100, 100)
        assert collect_usage()["model_used"] == "multiple"

    def test_per_agent_breakdown(self):
        from agents.usage import (
            start_usage_capture, set_current_agent, record_usage,
            collect_usage,
        )
        start_usage_capture()
        set_current_agent("equity_analyst")
        record_usage("claude-sonnet-4-6", 1000, 500)
        set_current_agent("cio")
        record_usage("claude-opus-4-7", 2000, 800)
        record_usage("claude-opus-4-7", 1000, 200)
        per_agent = collect_usage()["per_agent"]
        assert set(per_agent) == {"equity_analyst", "cio"}
        assert per_agent["equity_analyst"]["calls"] == 1
        assert per_agent["cio"]["calls"] == 2
        assert per_agent["cio"]["input_tokens"] == 3000

    def test_bad_counts_dropped_not_raised(self):
        from agents.usage import (
            start_usage_capture, record_usage, collect_usage,
        )
        start_usage_capture()
        record_usage("claude-sonnet-4-6", "not-a-number", 100)
        record_usage("claude-sonnet-4-6", 100, 100)
        # The bad record is dropped; the good one still aggregates.
        assert collect_usage()["input_tokens"] == 100


# ── cost-summary endpoint contract ────────────────────────────────────────────

class TestCostSummaryEndpoint:
    def test_requires_auth(self):
        assert client.get("/api/v1/activity/cost-summary").status_code == 401

    def test_returns_cost_summary_shape(self):
        resp = client.get("/api/v1/activity/cost-summary",
                           headers=SESSION_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        for key in ("total_cost_usd", "total_input_tokens",
                    "total_output_tokens", "total_interactions",
                    "by_member", "by_type", "analytical_sessions_only"):
            assert key in body
        assert isinstance(body["by_member"], list)
        assert isinstance(body["by_type"], list)


# ── DB round-trip — skips without a live database ─────────────────────────────

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
        from sqlalchemy import text
        from database import engine, AsyncSessionLocal

        async def _probe() -> bool:
            if engine is not None:
                await engine.dispose()
            async with AsyncSessionLocal() as s:  # type: ignore[union-attr]
                await s.execute(
                    text("SELECT estimated_cost_usd FROM agent_interactions "
                         "LIMIT 1"))
            return True

        _db_ready_cache = _run(_probe())
    except Exception:
        _db_ready_cache = False
    return _db_ready_cache


class TestCostSummaryDB:
    def test_logged_tokens_appear_in_cost_summary(self):
        if not _db_ready():
            pytest.skip("no live database with the migration-020 columns")
        from tools.activity_log import (
            log_agent_interaction, get_cost_summary,
        )
        from database import engine

        async def _scenario():
            if engine is not None:
                await engine.dispose()
            wrote = await log_agent_interaction(
                user_email="ruurdsm@queens.edu",
                session_id=str(uuid.uuid4()),
                session_type="analytical",
                interaction_type="council",
                question_text="cost-tracking round-trip test",
                response_summary="ok",
                input_tokens=4321,
                output_tokens=1234,
                model_used="claude-sonnet-4-6",
                estimated_cost_usd=0.0312,
            )
            assert wrote is True
            summary = await get_cost_summary(analytical_only=True)
            return summary

        summary = _run(_scenario())
        assert summary["total_cost_usd"] >= 0.0312
        assert summary["total_input_tokens"] >= 4321
        council = next((t for t in summary["by_type"]
                        if t["interaction_type"] == "council"), None)
        assert council is not None
        mine = next((m for m in summary["by_member"]
                     if m["user"] == "ruurdsm@queens.edu"), None)
        assert mine is not None
        assert mine["cost_usd"] >= 0.0312
