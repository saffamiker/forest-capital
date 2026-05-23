"""Coverage for tools/strategy_context (item 9 commit 5).

May 22 2026. Verifies the per-strategy agent prompt injection layer:

  detect_strategies_in_query   regex matches all 10 known ids in
                               snake_case / spaced / hyphenated /
                               case-insensitive forms; substrings
                               inside other words are rejected.
  inject_strategy_context      formatted block appended; no-op on
                               empty list or unknown strategy_id;
                               renders construction / behavioural
                               profile / regime sensitivity /
                               portfolio characteristics / tag.
  set_active_strategies        ContextVar round-trip across nested
                               function calls inside a copied
                               context (the council ThreadPool path).
  _with_strategy_context       call_claude wrapper picks up the
                               ContextVar fallback when no explicit
                               override is passed.
"""
import contextvars
import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")


# ── detect_strategies_in_query ──────────────────────────────────────────────


class TestDetectStrategiesInQuery:
    def test_empty_query(self):
        from tools.strategy_context import detect_strategies_in_query
        assert detect_strategies_in_query("") == []
        assert detect_strategies_in_query(None) == []  # type: ignore[arg-type]

    def test_no_strategy_mentioned(self):
        from tools.strategy_context import detect_strategies_in_query
        assert detect_strategies_in_query(
            "What is the Sharpe ratio over 2002-2025?") == []

    def test_single_snake_case(self):
        from tools.strategy_context import detect_strategies_in_query
        out = detect_strategies_in_query(
            "Tell me about REGIME_SWITCHING vs BENCHMARK")
        assert out == ["REGIME_SWITCHING", "BENCHMARK"]

    def test_spaced_form(self):
        from tools.strategy_context import detect_strategies_in_query
        out = detect_strategies_in_query(
            "How does regime switching compare?")
        assert "REGIME_SWITCHING" in out

    def test_hyphenated_form(self):
        from tools.strategy_context import detect_strategies_in_query
        out = detect_strategies_in_query(
            "vol-targeting vs equal-weight blend?")
        assert "VOL_TARGETING" in out
        assert "EQUAL_WEIGHT" in out

    def test_case_insensitive(self):
        from tools.strategy_context import detect_strategies_in_query
        out = detect_strategies_in_query(
            "regime switching and Black-Litterman options")
        assert "REGIME_SWITCHING" in out
        assert "BLACK_LITTERMAN" in out

    def test_first_mention_order_preserved(self):
        from tools.strategy_context import detect_strategies_in_query
        out = detect_strategies_in_query(
            "VOL_TARGETING is better than BENCHMARK and "
            "REGIME_SWITCHING")
        assert out == ["VOL_TARGETING", "BENCHMARK", "REGIME_SWITCHING"]

    def test_dedupes_repeated_mentions(self):
        from tools.strategy_context import detect_strategies_in_query
        out = detect_strategies_in_query(
            "BENCHMARK BENCHMARK benchmark Benchmark")
        assert out == ["BENCHMARK"]

    def test_word_boundary_rejects_substring(self):
        """The regex anchors strategy ids on word boundaries — a
        substring inside another word doesn't match."""
        from tools.strategy_context import detect_strategies_in_query
        # 'benchmarking' should not match BENCHMARK.
        out = detect_strategies_in_query(
            "We are benchmarking against the benchmark.")
        # 'benchmark' (standalone) matches; 'benchmarking' does not.
        assert out == ["BENCHMARK"]


# ── inject_strategy_context (cache-aware) ──────────────────────────────────


class TestInjectStrategyContext:
    def setup_method(self):
        from tools.strategy_context import _set_cache_for_tests
        _set_cache_for_tests({
            "REGIME_SWITCHING": {
                "strategy_id": "REGIME_SWITCHING",
                "construction_summary": (
                    "Allocates between equity and bonds based on a "
                    "macroeconomic regime classifier."),
                "behavioural_profile": {
                    "drawdown_profile": "shallow",
                    "tail_risk": "low",
                },
                "regime_sensitivity": (
                    "Performs best in regime transitions; underperforms "
                    "in stable bull markets."),
                "behavioural_tag": "Macro-aware tail-risk hedge",
                "portfolio_characteristics": {
                    "avg_holdings": 3,
                    "avg_turnover_pct": 0.28,
                },
            },
            "BENCHMARK": {
                "strategy_id": "BENCHMARK",
                "construction_summary": (
                    "100% S&P 500 — pure equity benchmark."),
                "behavioural_profile": {},
                "regime_sensitivity": (
                    "Concentrated equity exposure with no hedge."),
                "behavioural_tag": "Pure equity benchmark",
                "portfolio_characteristics": {"avg_holdings": 1},
            },
        })

    def teardown_method(self):
        from tools.strategy_context import _set_cache_for_tests
        _set_cache_for_tests({})

    def test_inject_single_strategy(self):
        from tools.strategy_context import inject_strategy_context
        prompt = "You are an equity analyst."
        out = inject_strategy_context(prompt, ["REGIME_SWITCHING"])
        assert prompt in out
        assert "STRATEGY CONTEXT: REGIME_SWITCHING" in out
        assert "Macro-aware tail-risk hedge" in out
        assert "Macroeconomic regime classifier" in out or (
            "macroeconomic regime classifier" in out.lower())
        assert "shallow" in out
        assert "Reason from these specific strategy characteristics" in out

    def test_inject_multiple_strategies_in_order(self):
        from tools.strategy_context import inject_strategy_context
        out = inject_strategy_context(
            "system", ["REGIME_SWITCHING", "BENCHMARK"])
        assert out.find("REGIME_SWITCHING") < out.find("BENCHMARK")

    def test_inject_empty_list_is_noop(self):
        from tools.strategy_context import inject_strategy_context
        prompt = "You are an equity analyst."
        assert inject_strategy_context(prompt, []) == prompt
        assert inject_strategy_context(prompt, None) == prompt

    def test_inject_unknown_strategy_is_noop(self):
        from tools.strategy_context import inject_strategy_context
        prompt = "You are an equity analyst."
        # Empty cache for the unknown id → no block appended.
        assert inject_strategy_context(prompt, ["NEVER_HEARD_OF"]) == prompt

    def test_inject_lowercase_normalises_to_upper(self):
        from tools.strategy_context import inject_strategy_context
        out = inject_strategy_context("sys", ["regime_switching"])
        assert "REGIME_SWITCHING" in out

    def test_block_omits_empty_fields(self):
        """A row with empty fields should not render placeholder
        prompts — the agent's context block stays clean."""
        from tools.strategy_context import (
            inject_strategy_context, _set_cache_for_tests,
        )
        _set_cache_for_tests({
            "MIN_VARIANCE": {
                "strategy_id": "MIN_VARIANCE",
                "construction_summary": "",
                "behavioural_profile": {},
                "regime_sensitivity": "",
                "behavioural_tag": "Variance minimiser",
                "portfolio_characteristics": {},
            },
        })
        out = inject_strategy_context("sys", ["MIN_VARIANCE"])
        assert "Variance minimiser" in out
        # No empty sections rendered.
        assert "Construction:" not in out
        assert "Regime sensitivity:" not in out


# ── ContextVar — set / get / clear ─────────────────────────────────────────


class TestActiveStrategiesContextVar:
    def test_default_is_empty_list(self):
        from tools.strategy_context import (
            get_active_strategies, clear_active_strategies,
        )
        clear_active_strategies()
        assert get_active_strategies() == []

    def test_set_then_get(self):
        from tools.strategy_context import (
            set_active_strategies, get_active_strategies,
            clear_active_strategies,
        )
        try:
            set_active_strategies(["BENCHMARK", "REGIME_SWITCHING"])
            assert get_active_strategies() == [
                "BENCHMARK", "REGIME_SWITCHING"]
        finally:
            clear_active_strategies()

    def test_clear_resets_to_empty(self):
        from tools.strategy_context import (
            set_active_strategies, get_active_strategies,
            clear_active_strategies,
        )
        set_active_strategies(["BENCHMARK"])
        clear_active_strategies()
        assert get_active_strategies() == []

    def test_copy_context_propagates_to_thread(self):
        """The council's ThreadPoolExecutor uses
        contextvars.copy_context().run() so the per-request
        strategy id list reaches the specialist worker threads."""
        import concurrent.futures
        from tools.strategy_context import (
            set_active_strategies, get_active_strategies,
            clear_active_strategies,
        )

        clear_active_strategies()
        set_active_strategies(["VOL_TARGETING"])
        try:
            def _worker() -> list[str]:
                return get_active_strategies()

            ctx = contextvars.copy_context()
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=1) as pool:
                future = pool.submit(ctx.run, _worker)
                got = future.result()
            assert got == ["VOL_TARGETING"]
        finally:
            clear_active_strategies()


# ── _with_strategy_context (call_claude wrapper) ───────────────────────────


class TestWithStrategyContext:
    def setup_method(self):
        from tools.strategy_context import _set_cache_for_tests
        _set_cache_for_tests({
            "BENCHMARK": {
                "strategy_id": "BENCHMARK",
                "construction_summary": "100% SPY.",
                "behavioural_profile": {},
                "regime_sensitivity": "Pure equity.",
                "behavioural_tag": "Pure equity benchmark",
                "portfolio_characteristics": {},
            },
        })

    def teardown_method(self):
        from tools.strategy_context import (
            _set_cache_for_tests, clear_active_strategies,
        )
        _set_cache_for_tests({})
        clear_active_strategies()

    def test_explicit_override_wins(self):
        from agents.base import _with_strategy_context
        out = _with_strategy_context("sys", ["BENCHMARK"])
        assert "STRATEGY CONTEXT: BENCHMARK" in out
        assert "100% SPY" in out

    def test_contextvar_fallback(self):
        from agents.base import _with_strategy_context
        from tools.strategy_context import (
            set_active_strategies, clear_active_strategies,
        )
        clear_active_strategies()
        out_before = _with_strategy_context("sys", None)
        assert out_before == "sys"
        set_active_strategies(["BENCHMARK"])
        try:
            out_after = _with_strategy_context("sys", None)
            assert "STRATEGY CONTEXT: BENCHMARK" in out_after
        finally:
            clear_active_strategies()

    def test_both_empty_is_noop(self):
        from agents.base import _with_strategy_context
        from tools.strategy_context import clear_active_strategies
        clear_active_strategies()
        assert _with_strategy_context("sys", None) == "sys"
        assert _with_strategy_context("sys", []) == "sys"


# ── Cache refresh ──────────────────────────────────────────────────────────


def test_refresh_strategy_context_cache_safe_when_db_unavailable():
    """In the test environment AsyncSessionLocal is None — the
    refresh must complete without raising and leave the cache empty."""
    import asyncio
    from tools.strategy_context import (
        refresh_strategy_context_cache, _set_cache_for_tests,
        get_strategy_context,
    )
    _set_cache_for_tests({})
    asyncio.run(refresh_strategy_context_cache())
    assert get_strategy_context("BENCHMARK") == ""


# ── Smoke test — module is importable from main.py side ────────────────────


def test_module_imports_cleanly():
    import tools.strategy_context as sc
    for name in (
        "detect_strategies_in_query", "inject_strategy_context",
        "set_active_strategies", "get_active_strategies",
        "clear_active_strategies", "get_strategy_context",
        "refresh_strategy_context_cache", "known_strategy_ids",
        "_set_cache_for_tests",
    ):
        assert hasattr(sc, name), f"missing helper: {name}"

    ids = sc.known_strategy_ids()
    assert "BENCHMARK" in ids
    assert "REGIME_SWITCHING" in ids
    assert "VOL_TARGETING" in ids
    assert len(ids) == 10
