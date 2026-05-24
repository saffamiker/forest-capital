"""tests/test_dashboard_hotfixes_may24.py — pre-3pm dashboard hotfixes.

P0: analytics cache refresh trigger now fires unconditionally on
    cold cache + during startup hook, with a BOOT-WARM sentinel
    hash so the row always lands somewhere get_latest_metric can
    find it. The pre-existing `if latest_hash:` gate meant a fresh
    Render restart with an empty strategy_results_cache never
    triggered the background sweep — the dashboard saw a 30s
    timeout on every load.

RW2: strategy SCREAMING_SNAKE_CASE identifiers in generated drafts
     are now substituted with display names. Both the prompt
     instructs the writer to use display names AND a post-
     processing pass catches anything the model leaves behind.
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
    "ALLOWED_EMAILS", "ruurdsm@queens.edu,thaob@queens.edu")


# ── P0 ─────────────────────────────────────────────────────────────────────


class TestP0SentinelHash:
    """refresh_all_analytics now accepts an empty string / None
    data_hash and substitutes a BOOT-WARM sentinel so a Render
    restart with an empty strategy_results_cache still produces a
    row that get_latest_metric can find."""

    def test_refresh_accepts_empty_string(self):
        import asyncio
        from tools.precomputed_analytics import refresh_all_analytics
        # Must not raise. The refresh itself fails-open on every
        # downstream compute, so we just exercise the entry point.
        asyncio.run(refresh_all_analytics(""))

    def test_refresh_accepts_none_via_trigger(self):
        # trigger_refresh_async accepts (str). The endpoint code
        # passes `latest_hash or ""` — confirm that exact shape
        # threads through without raising.
        import asyncio
        from tools.precomputed_analytics import refresh_all_analytics
        latest_hash = None
        asyncio.run(refresh_all_analytics(latest_hash or ""))


# ── RW2 ────────────────────────────────────────────────────────────────────


class TestRW2StrategyNameSubstitution:

    def test_every_strategy_substituted(self):
        from agents.academic_writer import (
            substitute_strategy_names, STRATEGY_DISPLAY_NAMES,
        )
        for raw, display in STRATEGY_DISPLAY_NAMES.items():
            out = substitute_strategy_names(
                f"The {raw} strategy achieved Sharpe 0.6.")
            assert display in out, (
                f"Expected '{display}' in output for {raw}, "
                f"got: {out}")
            assert raw not in out, (
                f"Raw identifier {raw} still present in: {out}")

    def test_substitution_is_idempotent(self):
        from agents.academic_writer import substitute_strategy_names
        once = substitute_strategy_names(
            "REGIME_SWITCHING beat BENCHMARK.")
        twice = substitute_strategy_names(once)
        assert once == twice

    def test_word_boundary_preserves_embedded_identifiers(self):
        # A hypothetical variable name embedded in a longer token
        # must NOT be rewritten — only standalone identifiers are.
        from agents.academic_writer import substitute_strategy_names
        out = substitute_strategy_names(
            "see function REGIME_SWITCHING_test_helper above")
        assert "REGIME_SWITCHING_test_helper" in out

    def test_empty_and_none_are_safe(self):
        from agents.academic_writer import substitute_strategy_names
        assert substitute_strategy_names("") == ""
        assert substitute_strategy_names(None) == ""

    def test_max_sharpe_rolling_matches_full_identifier(self):
        # MAX_SHARPE_ROLLING is the longest identifier — sort-by-
        # length-DESC in the regex must catch it before any shorter
        # would match. Defensive regression for the sort order.
        from agents.academic_writer import substitute_strategy_names
        out = substitute_strategy_names(
            "MAX_SHARPE_ROLLING delivered alpha.")
        assert "Maximum Sharpe (Rolling)" in out
        assert "MAX_SHARPE_ROLLING" not in out

    def test_classic_60_40_with_underscore_numeric(self):
        # CLASSIC_60_40 has digits in it — the regex must still
        # word-boundary match. Belt-and-braces.
        from agents.academic_writer import substitute_strategy_names
        out = substitute_strategy_names(
            "CLASSIC_60_40 underperformed the dynamic sleeves.")
        assert "Classic 60/40" in out

    def test_prompt_lists_every_display_name(self):
        # The academic_writer system prompt must name each
        # display-name substitution verbatim so the model has the
        # canonical form in front of it during generation. A
        # regression that drops one would let the raw identifier
        # leak through even when the post-processing pass is
        # idempotent (the model would generate the raw form).
        from agents.academic_writer import _SYSTEM_PROMPT
        assert "STRATEGY DISPLAY NAMES" in _SYSTEM_PROMPT
        for display in (
            "Equal-Weight", "Regime-Switching", "Volatility-Targeting",
            "Minimum-Variance", "Momentum-Rotation",
            "Maximum Sharpe (Rolling)", "Risk-Parity", "Black-Litterman",
            "Classic 60/40", "Benchmark (100% Equity)",
        ):
            assert display in _SYSTEM_PROMPT, (
                f"'{display}' missing from prompt — model needs it "
                f"to be present so generation uses the display "
                f"form natively rather than relying on the post-"
                f"processing pass alone.")
