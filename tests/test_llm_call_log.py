"""
tests/test_llm_call_log.py

Pins the llm_call structured log emitted by call_claude, call_gemini,
and the four direct-SDK call sites (research_agent, audit_layer2,
academic_advisor, contrarian_analyst). PR-LLM-1, May 25 2026.

Why a single test file for the helper:
The helper is the foundation for the entire LLM token-audit workstream
(PRs 2-5 build on it). Pinning the emit shape here means a future
change that alters the field names will break this test before it
reaches production, where every leak dashboard query depends on the
field names being stable.
"""
from __future__ import annotations

from unittest.mock import patch

from agents.llm_log import TRIGGER_UNSPECIFIED, log_llm_call


class TestLogLlmCallShape:
    """The structured-log shape is the public contract for every
    downstream filter, so each field is asserted by name."""

    def test_emits_event_with_all_required_fields(self) -> None:
        with patch("agents.llm_log.log") as mock_log:
            log_llm_call(
                function="test_fn",
                model="claude-sonnet-4-6",
                trigger="council_specialist:equity_analyst",
                input_tokens=1000,
                output_tokens=500,
                hash_gate=False,
            )
            mock_log.info.assert_called_once()
            event, *_ = mock_log.info.call_args[0]
            kw = mock_log.info.call_args[1]
            assert event == "llm_call"
            assert kw["function"] == "test_fn"
            assert kw["model"] == "claude-sonnet-4-6"
            assert kw["trigger"] == "council_specialist:equity_analyst"
            assert kw["input_tokens"] == 1000
            assert kw["output_tokens"] == 500
            assert kw["hash_gate"] is False

    def test_defaults_trigger_to_unspecified(self) -> None:
        """An un-labeled caller still emits a queryable log line so
        the trigger field is searchable: `trigger:unspecified` finds
        the call sites that haven't been threaded yet."""
        with patch("agents.llm_log.log") as mock_log:
            log_llm_call(function="x", model="m")
            assert mock_log.info.call_args[1]["trigger"] == TRIGGER_UNSPECIFIED

    def test_defaults_hash_gate_to_false(self) -> None:
        """Most call sites today have no gate. The default reflects
        reality so an absent kwarg doesn't accidentally claim a gate."""
        with patch("agents.llm_log.log") as mock_log:
            log_llm_call(function="x", model="m")
            assert mock_log.info.call_args[1]["hash_gate"] is False

    def test_none_tokens_normalised_to_zero(self) -> None:
        """A failed/aborted call before usage is reported should still
        emit a queryable line — the log shape stays consistent."""
        with patch("agents.llm_log.log") as mock_log:
            log_llm_call(function="x", model="m",
                         input_tokens=None, output_tokens=None)
            assert mock_log.info.call_args[1]["input_tokens"] == 0
            assert mock_log.info.call_args[1]["output_tokens"] == 0

    def test_extra_kwargs_passed_through(self) -> None:
        """Callers attach context-specific fields (n_searches,
        n_fetches, provider) without changing the helper signature."""
        with patch("agents.llm_log.log") as mock_log:
            log_llm_call(function="x", model="m",
                         n_searches=3, n_fetches=2, provider="openrouter")
            kw = mock_log.info.call_args[1]
            assert kw["n_searches"] == 3
            assert kw["n_fetches"] == 2
            assert kw["provider"] == "openrouter"

    def test_fail_open_on_log_error(self) -> None:
        """Telemetry must never break an LLM call. A log emit that
        raises is swallowed silently — the LLM response still ships."""
        with patch("agents.llm_log.log") as mock_log:
            mock_log.info.side_effect = RuntimeError("structlog blew up")
            # Must not raise.
            log_llm_call(function="x", model="m")

    def test_hash_gate_true_when_caller_passes_true(self) -> None:
        """A future call site that adds a hash gate (PRs 3-5) flips
        this bit. The log filter `hash_gate:true` becomes the proof
        a leak was sealed, and this test guards that signal."""
        with patch("agents.llm_log.log") as mock_log:
            log_llm_call(function="x", model="m", hash_gate=True)
            assert mock_log.info.call_args[1]["hash_gate"] is True

    def test_trigger_unspecified_sentinel_value(self) -> None:
        """The grep-able sentinel is part of the public API — a future
        rename would break leak triage queries written against the
        current value."""
        assert TRIGGER_UNSPECIFIED == "unspecified"


class TestCallClaudeEmitsLog:
    """call_claude in agents/base.py forwards trigger + hash_gate into
    the emit. Pinning the wiring here means a future refactor of the
    wrapper can't silently drop the telemetry."""

    def test_call_claude_emits_llm_call_log(self) -> None:
        """The wrapper emits one llm_call event per invocation, carrying
        the kwarg-supplied trigger and hash_gate fields."""
        from types import SimpleNamespace

        from agents import base

        fake_msg = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=SimpleNamespace(
                input_tokens=42, output_tokens=7, server_tool_use=None,
            ),
        )
        fake_client = SimpleNamespace(
            messages=SimpleNamespace(create=lambda **_kw: fake_msg))
        with patch.object(base, "get_anthropic_client",
                          return_value=fake_client), \
             patch("agents.llm_log.log") as mock_log:
            base.call_claude(
                model="claude-sonnet-4-6",
                system_prompt="sys",
                user_message="msg",
                trigger="harness_evaluator",
                hash_gate=True,
            )
            assert mock_log.info.called
            event_call = next(
                c for c in mock_log.info.call_args_list
                if c[0] and c[0][0] == "llm_call")
            kw = event_call[1]
            assert kw["function"] == "call_claude"
            assert kw["trigger"] == "harness_evaluator"
            assert kw["hash_gate"] is True
            assert kw["input_tokens"] == 42
            assert kw["output_tokens"] == 7

    def test_call_claude_defaults_trigger_unspecified(self) -> None:
        """A caller that hasn't been threaded yet still produces a
        log line — the trigger reads 'unspecified' so it's grep-able."""
        from types import SimpleNamespace

        from agents import base

        fake_msg = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=SimpleNamespace(
                input_tokens=1, output_tokens=1, server_tool_use=None),
        )
        fake_client = SimpleNamespace(
            messages=SimpleNamespace(create=lambda **_kw: fake_msg))
        with patch.object(base, "get_anthropic_client",
                          return_value=fake_client), \
             patch("agents.llm_log.log") as mock_log:
            base.call_claude(
                model="claude-sonnet-4-6",
                system_prompt="sys",
                user_message="msg",
            )
            event_call = next(
                c for c in mock_log.info.call_args_list
                if c[0] and c[0][0] == "llm_call")
            assert event_call[1]["trigger"] == "unspecified"
            assert event_call[1]["hash_gate"] is False
