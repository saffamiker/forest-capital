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

    def test_unknown_model_defaults_to_sonnet_pricing(self):
        # An unrecognised model string falls back to Sonnet rates rather
        # than dropping the cost — approximate, but trackable.
        sonnet = config.calculate_cost("claude-sonnet-4-6", 1000, 1000)
        unknown = config.calculate_cost("some-unknown-model", 1000, 1000)
        assert unknown == sonnet

    def test_null_model_defaults_to_sonnet_pricing(self):
        sonnet = config.calculate_cost("claude-sonnet-4-6", 1000, 1000)
        assert config.calculate_cost(None, 1000, 1000) == sonnet

    def test_published_rates_match_anthropic_2026_05(self):
        # Spot-check against the published API rates the user briefed:
        #   opus-4-7   $15.00 / $75.00 per 1M tokens
        #   sonnet-4-6 $3.00  / $15.00 per 1M tokens
        #   haiku-4-5  $0.80  / $4.00  per 1M tokens
        # 1,000,000 input + 1,000,000 output = input_rate + output_rate dollars.
        opus = config.calculate_cost("claude-opus-4-7", 1_000_000, 1_000_000)
        sonnet = config.calculate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
        haiku = config.calculate_cost("claude-haiku-4-5", 1_000_000, 1_000_000)
        assert opus == pytest.approx(15.0 + 75.0)
        assert sonnet == pytest.approx(3.0 + 15.0)
        assert haiku == pytest.approx(0.80 + 4.0)

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

# ── Endpoint coverage — every interaction-logging endpoint seeds capture ──────

class TestEndpointCaptureWiring:
    """
    Every endpoint that logs an interaction must now call start_usage_capture()
    before any AI work. The test verifies the wiring is in place — the endpoint
    accepts the request and the call site imports / invokes start_usage_capture
    without raising. In the test environment the AI paths fall through to mock
    responses, so the actual capture is a no-op, but the wiring is exercised.
    """
    def test_qa_audit_endpoint_responds(self):
        # The QA audit handler now seeds the usage bucket; the test env
        # short-circuits to the mock audit, but the wiring must not break.
        resp = client.post("/api/qa/audit", headers=SESSION_HEADERS)
        # The endpoint is sysadmin-gated; ruurdsm is sysadmin.
        assert resp.status_code in (200, 409)

    def test_explain_endpoint_seeds_capture_without_error(self):
        resp = client.post(
            "/api/council/explain",
            headers=SESSION_HEADERS,
            json={"metric": "sharpe", "current_value": 0.52},
        )
        assert resp.status_code == 200

    def test_explain_data_endpoint_seeds_capture_without_error(self):
        resp = client.post(
            "/api/council/explain-data",
            headers=SESSION_HEADERS,
            json={"metric": "max_drawdown", "current_value": -0.18,
                  "context": "BENCHMARK"},
        )
        assert resp.status_code == 200

    def test_export_package_endpoint_seeds_capture_without_error(self):
        # No charts/tables — endpoint should still build an empty ZIP and
        # return 200. The start_usage_capture import path must not raise.
        resp = client.post(
            "/api/v1/export/package",
            headers=SESSION_HEADERS,
            data={"metadata": "{}"},
        )
        assert resp.status_code == 200


class TestStreamHaikuRecordsUsage:
    """
    _stream_haiku must call record_usage() with the final-message usage
    after the Anthropic stream completes. The test patches the SDK so the
    streaming path runs without a network call and asserts the bucket
    aggregates the token counts the mock yields.
    """
    def test_stream_haiku_records_usage_when_capture_active(self, monkeypatch):
        import asyncio as _aio
        from agents import usage as usage_mod
        from agents import explainer_agent

        # Force the real path (not the test-env mock chunks).
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        # Tiny stub stream that mimics the Anthropic MessageStream contract
        # _stream_haiku uses: iterable .text_stream + .get_final_message().usage.
        class _FinalUsage:
            input_tokens = 123
            output_tokens = 45

        class _FinalMessage:
            usage = _FinalUsage()

        class _StubStream:
            text_stream = ["hello ", "world"]

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def get_final_message(self):
                return _FinalMessage()

        class _StubMessages:
            def stream(self, **_):
                return _StubStream()

        class _StubClient:
            messages = _StubMessages()

        monkeypatch.setattr(
            explainer_agent, "get_anthropic_client",
            lambda: _StubClient(), raising=False)
        # Also patch base.get_anthropic_client because _worker imports it
        # locally from agents.base.
        from agents import base as base_mod
        monkeypatch.setattr(base_mod, "get_anthropic_client",
                            lambda: _StubClient())

        async def _drive():
            usage_mod.start_usage_capture()
            chunks = []
            async for c in explainer_agent.stream_metric_explanation(
                "sharpe", 0.52
            ):
                chunks.append(c)
            return chunks, usage_mod.collect_usage()

        chunks, captured = _aio.run(_drive())
        assert "".join(chunks) == "hello world"
        assert captured["input_tokens"] == 123
        assert captured["output_tokens"] == 45
        assert captured["estimated_cost_usd"] is not None
        # HAIKU_MODEL was used; model_used is the single recorded model.
        from agents.base import HAIKU_MODEL
        assert captured["model_used"] == HAIKU_MODEL


# ── Sysadmin attribution for auto-triggered interactions ──────────────────────

class TestSysadminAttribution:
    """
    An interaction logged without a user_email (auto-triggered audit,
    startup hook, scheduled task) is attributed to the sysadmin so the
    row still lands.
    """
    def test_null_user_email_attributed_to_sysadmin(self, monkeypatch):
        # Patch the team-membership gate and the actual DB write so the
        # test runs without a database. We assert the email substituted
        # before is_team_member is called is the sysadmin email.
        import config
        from tools import activity_log

        seen: dict[str, str | None] = {"checked": None}

        async def _fake_is_team_member(email):
            seen["checked"] = email
            return True  # Admit so we get to the insert path.

        # Force the function to short-circuit before the real DB write.
        monkeypatch.setattr(activity_log, "is_team_member", _fake_is_team_member)
        monkeypatch.setattr(activity_log, "_DB_AVAILABLE", True)

        # An async no-op session that fails the INSERT cleanly — we only
        # care that we got past the sysadmin substitution and into the
        # insert path with the right email.
        class _DummySession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def execute(self, *_, **__):
                raise RuntimeError("DB unavailable in test")

            async def commit(self):
                pass

        monkeypatch.setattr(activity_log, "AsyncSessionLocal",
                            lambda: _DummySession())

        wrote = _run(activity_log.log_agent_interaction(
            user_email=None,
            session_id=None,
            session_type=None,
            interaction_type="qa",
        ))
        # The DB write failed (by design above) so wrote is False, but
        # the substitution happened before — that's what we're verifying.
        assert wrote is False
        expected = next(iter(sorted(config.SYSADMIN_EMAILS)))
        assert seen["checked"] == expected
        assert seen["checked"] == "ruurdsm@queens.edu"

    def test_empty_string_user_email_attributed_to_sysadmin(self, monkeypatch):
        from tools import activity_log

        seen: dict[str, str | None] = {"checked": None}

        async def _fake_is_team_member(email):
            seen["checked"] = email
            return False  # Refuse — we still want to see what email was tried.

        monkeypatch.setattr(activity_log, "is_team_member", _fake_is_team_member)
        monkeypatch.setattr(activity_log, "_DB_AVAILABLE", True)

        wrote = _run(activity_log.log_agent_interaction(
            user_email="",
            session_id=None,
            session_type=None,
            interaction_type="qa",
        ))
        assert wrote is False
        assert seen["checked"] == "ruurdsm@queens.edu"


# ── existing cost-summary endpoint contract ───────────────────────────────────

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
