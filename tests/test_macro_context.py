"""
tests/test_macro_context.py — coverage for tools/macro_context.py.

Pins the digest → context-block formatting, the inject helper's no-op
contract on an empty cache, and the refresh hook's fail-open behaviour.

The injection points (call_claude in agents/base.py, the four manual
sites in academic_advisor / contrarian_analyst / independent_analyst)
are covered indirectly by the agent test suites — this module is
focused on the context module itself.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")

import pytest  # noqa: E402

from tools import macro_context  # noqa: E402


SAMPLE_DIGEST = {
    "id": 7,
    "generated_at": "2026-05-21T10:00:00+00:00",
    "triggered_by": "scheduled",
    "summary_text": "Fed paused; CPI cooler.",
    "regime_implication": "Mildly risk-on; IG attractive.",
    "key_signals": [
        {"category": "monetary_policy",
         "signal": "Fed holds at 5.25-5.50%.",
         "implication": "IG duration tailwind.",
         "source_url": "https://federalreserve.gov/example"},
        {"category": "inflation",
         "signal": "CPI 3.1% vs 3.2% expected.",
         "implication": "Dovish across asset classes.",
         "source_url": "https://bls.gov/example"},
    ],
    "citation_urls": [
        "https://federalreserve.gov/example",
        "https://bls.gov/example",
    ],
    "model": "claude-sonnet-4-6",
    "metadata": {},
}


@pytest.fixture(autouse=True)
def _reset_cache():
    """The module-level cache is process-wide; reset between tests so
    one test's _set_cache_for_test does not bleed into the next."""
    macro_context._CACHE["text"] = ""
    yield
    macro_context._CACHE["text"] = ""


# ── _format_digest_block ─────────────────────────────────────────────────────

class TestFormatDigestBlock:
    def test_empty_digest_returns_empty_string(self):
        assert macro_context._format_digest_block(None) == ""
        assert macro_context._format_digest_block({}) == ""

    def test_digest_with_no_content_returns_empty_string(self):
        # A digest that has the shape but no actual content — render
        # nothing rather than a hollow header that wastes input tokens
        # for every agent on every call.
        empty = {"summary_text": "", "key_signals": [],
                 "regime_implication": "", "generated_at": "2026-05-21"}
        assert macro_context._format_digest_block(empty) == ""

    def test_renders_summary(self):
        block = macro_context._format_digest_block(SAMPLE_DIGEST)
        assert "Fed paused" in block
        assert "Summary:" in block

    def test_renders_each_signal_with_category_implication_source(self):
        block = macro_context._format_digest_block(SAMPLE_DIGEST)
        assert "[monetary_policy]" in block
        assert "Fed holds at 5.25-5.50%" in block
        assert "Implication: IG duration tailwind." in block
        assert "https://federalreserve.gov/example" in block
        assert "[inflation]" in block
        assert "https://bls.gov/example" in block

    def test_renders_regime_implication(self):
        block = macro_context._format_digest_block(SAMPLE_DIGEST)
        assert "Regime read:" in block
        assert "Mildly risk-on" in block

    def test_renders_the_generated_at_timestamp(self):
        block = macro_context._format_digest_block(SAMPLE_DIGEST)
        assert "2026-05-21T10:00:00+00:00" in block

    def test_includes_the_no_invention_guardrail(self):
        # Pin the prompt instruction — a regression that drops it lets
        # the model freely invent macro conditions absent from the block.
        block = macro_context._format_digest_block(SAMPLE_DIGEST)
        assert "Do NOT invent macro conditions" in block

    def test_skips_signals_without_signal_text(self):
        # A degenerate signal entry (no `signal` field) is dropped silently
        # rather than rendering a bullet with just a category tag.
        digest = {
            "summary_text": "x",
            "key_signals": [
                {"category": "rates", "signal": "", "source_url": "https://a"},
            ],
            "regime_implication": "",
            "generated_at": "2026-05-21",
        }
        block = macro_context._format_digest_block(digest)
        # Summary still renders; the empty signal is omitted.
        assert "Summary: x" in block
        assert "Key signals:" not in block

    def test_signals_without_implication_render_signal_only(self):
        # A signal that lacks an implication still appears, just without
        # the "Implication:" follow-up line.
        digest = {
            "summary_text": "",
            "key_signals": [
                {"category": "vol", "signal": "VIX +3pts",
                 "implication": "", "source_url": "https://x"},
            ],
            "regime_implication": "",
            "generated_at": "now",
        }
        block = macro_context._format_digest_block(digest)
        assert "VIX +3pts" in block
        assert "Implication" not in block


# ── get / inject ─────────────────────────────────────────────────────────────

class TestGetAndInject:
    def test_get_returns_empty_string_on_cold_cache(self):
        assert macro_context.get_macro_context() == ""

    def test_inject_is_a_noop_on_cold_cache(self):
        # A cold deploy (no digest yet) MUST render bitwise identical to
        # the pre-FEATURE-2 wire format — the agent runs text-only.
        prompt = "SYSTEM PROMPT FOR EQUITY ANALYST"
        assert macro_context.inject_macro_context(prompt) == prompt

    def test_inject_appends_when_cache_populated(self):
        macro_context._set_cache_for_test("\n=== MACRO ===\nx\n")
        out = macro_context.inject_macro_context("BASE")
        assert out.startswith("BASE")
        assert "=== MACRO ===" in out
        assert "x" in out

    def test_get_returns_set_value(self):
        macro_context._set_cache_for_test("hello")
        assert macro_context.get_macro_context() == "hello"


# ── refresh ──────────────────────────────────────────────────────────────────

class TestRefresh:
    def test_refresh_with_a_digest_populates_cache(self, monkeypatch):
        async def _stub_get_latest():
            return SAMPLE_DIGEST
        from tools import research_engine
        monkeypatch.setattr(
            research_engine, "get_latest_digest", _stub_get_latest)
        asyncio.run(macro_context.refresh_macro_context())
        ctx = macro_context.get_macro_context()
        assert "Fed paused" in ctx
        assert "Implication: IG duration tailwind." in ctx

    def test_refresh_with_no_digest_clears_cache(self, monkeypatch):
        # First populate
        macro_context._set_cache_for_test("previous content")
        async def _none():
            return None
        from tools import research_engine
        monkeypatch.setattr(research_engine, "get_latest_digest", _none)
        asyncio.run(macro_context.refresh_macro_context())
        # None → format → empty string → cache reset to empty.
        assert macro_context.get_macro_context() == ""

    def test_refresh_fails_open_keeping_previous_cache(self, monkeypatch):
        # An engine error during refresh leaves the previous cache
        # contents in place — the failure mode is "agents reason
        # against last digest", not "agents reason against nothing".
        macro_context._set_cache_for_test("kept")

        async def _boom():
            raise RuntimeError("DB down")

        from tools import research_engine
        monkeypatch.setattr(research_engine, "get_latest_digest", _boom)
        asyncio.run(macro_context.refresh_macro_context())
        assert macro_context.get_macro_context() == "kept"


class TestCitationInstruction:
    """May 23 2026 — the [Macro: <category>] citation instruction was
    REMOVED. Nothing in the rendering pipeline parses or resolves
    those tags (the frontend MacroCitation component never shipped),
    so they leaked into Bob's draft as raw text. The context block
    is now informational background only; agents weave the signals
    into prose naturally without inline tags. These tests pin the
    inverted contract so a future edit cannot quietly re-introduce
    the orphan-tag pattern."""

    def test_no_macro_tag_instruction_in_block(self):
        digest = {
            "generated_at": "2026-05-22T12:00:00Z",
            "summary_text": "Fed paused.",
            "regime_implication": "Transition to risk-on.",
            "key_signals": [
                {"category": "monetary_policy",
                 "signal": "Fed holds at 5.25-5.50%.",
                 "implication": "IG duration tailwind.",
                 "source_url": "https://federalreserve.gov/x"},
            ],
        }
        out = macro_context._format_digest_block(digest)
        assert "CITATION FORMAT" not in out
        assert "[Macro:" not in out
        # The instruction telling the model NOT to emit inline tags
        # must be present so the model knows to write prose naturally.
        assert ("do NOT emit inline tags" in out
                or "do not emit inline tags" in out.lower()
                or "do NOT emit inline markers" in out
                or "do not emit inline markers" in out.lower())

    def test_citation_instruction_omitted_on_empty_digest(self):
        # An empty digest returns "" — no instruction injected, no
        # macro context to cite. The agent's prompt is unaltered.
        out = macro_context._format_digest_block(None)
        assert out == ""
        out2 = macro_context._format_digest_block({})
        assert out2 == ""
