"""tests/test_path_a_constants.py -- PR A (June 22 2026).

Path A: academic_deck.py constants locked to LIVE full-period
values from the strategy cache at hash f2e87dec7dcabe71. Brief +
appendix + deck must agree on the numbers they cite.

Tests cover four contracts:
  1. Constants in academic_deck.py have the right Path A values.
  2. validated_constants block is populated by gather_document_data.
  3. build_substitution_table accepts live_signals kwarg and
     resolves the 5 watchpoint tokens from it.
  4. _substitute_slide_content walks table_data cells (the bug
     that caused the 23 unresolved tokens in the deck).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")


# ── 1. academic_deck.py Path A values ────────────────────────────────────


class TestPathAConstantValues:
    """Pin the Path A live-full-period values. A future PR that
    drifts these silently breaks brief / appendix consistency."""

    def test_oos_sharpe_regime_conditional_matches_live_cache(self):
        from tools.academic_deck import OOS_SHARPE_REGIME_CONDITIONAL
        # REGIME_SWITCHING.sharpe_ratio from the f2e87dec7dcabe71
        # cache (Jul 2002 - May 2026, 287 monthly obs).
        assert OOS_SHARPE_REGIME_CONDITIONAL == 0.6291

    def test_oos_sharpe_benchmark_matches_live_cache(self):
        from tools.academic_deck import OOS_SHARPE_BENCHMARK
        assert OOS_SHARPE_BENCHMARK == 0.5370

    def test_oos_sharpe_equal_weight_matches_live_cache(self):
        from tools.academic_deck import OOS_SHARPE_EQUAL_WEIGHT
        assert OOS_SHARPE_EQUAL_WEIGHT == 0.5728

    def test_max_drawdown_regime_conditional_matches_live_cache(self):
        from tools.academic_deck import MAX_DRAWDOWN_REGIME_CONDITIONAL
        assert MAX_DRAWDOWN_REGIME_CONDITIONAL == -0.2974

    def test_max_drawdown_benchmark_matches_live_cache(self):
        from tools.academic_deck import MAX_DRAWDOWN_BENCHMARK
        # Cache value -0.5256 rounds to -0.526 at the 3-decimal
        # precision the constant carries.
        assert MAX_DRAWDOWN_BENCHMARK == -0.526

    def test_correlation_constants_unchanged(self):
        # Verified June 22 2026 -- match the analytics
        # rolling_correlation() methodology (12-month rolling
        # corr averaged over the pre/post window). Raw correlations
        # are different and that's documented; constants stay at
        # the rolling-method values.
        from tools.academic_deck import (
            CORRELATION_POST_2022, CORRELATION_PRE_2022,
        )
        assert CORRELATION_PRE_2022 == -0.05
        assert CORRELATION_POST_2022 == 0.57

    def test_play_by_play_constants_unchanged(self):
        from tools.academic_deck import (
            PLAY_BY_PLAY_ADD_VALUE, PLAY_BY_PLAY_EVENTS,
        )
        assert PLAY_BY_PLAY_EVENTS == 9
        assert PLAY_BY_PLAY_ADD_VALUE == 2

    def test_new_constants_present(self):
        """The three constants added in PR A. A future edit that
        removes any would break the brief prompts that now
        reference {{OOS_WINDOW_MONTHS}} / {{OOS_WINDOW_PCT_OF_STUDY}}."""
        from tools.academic_deck import (
            CURRENT_EQUITY_WEIGHT, CURRENT_REGIME,
            OOS_WINDOW_MONTHS, OOS_WINDOW_PCT_OF_STUDY,
        )
        assert OOS_WINDOW_MONTHS == 53
        assert OOS_WINDOW_PCT_OF_STUDY == 18.5
        assert CURRENT_REGIME == "BULL"
        assert CURRENT_EQUITY_WEIGHT == 0.80


# ── 2. validated_constants threading ─────────────────────────────────────


class TestValidatedConstantsBlock:
    """gather_document_data() must populate validated_constants
    in its bundle. The brief story plan resolver reads
    data.get('validated_constants') -- before PR A this was {}
    and Opus emitted null/empty numeric_anchors, which Sonnet
    rendered as '--' in the brief prose."""

    def test_bundle_carries_validated_constants_in_test_env(self):
        # In ENVIRONMENT=test the bundle short-circuits to the
        # cold-cache shape without analytics. We pin that the
        # SHAPE includes the key by checking the shipping code
        # populates it in the warm path.
        import inspect
        from tools import academic_export
        src = inspect.getsource(academic_export.gather_document_data)
        assert '"validated_constants": validated_constants' in src, (
            "gather_document_data must populate validated_constants "
            "in the bundle.update() call -- PR A scope")

    def test_validated_constants_keys_are_complete(self):
        """Source-level pin on the validated_constants block.
        Every constant the brief / appendix / deck story plan
        resolver may reference must be in this block."""
        import inspect
        from tools import academic_export
        src = inspect.getsource(academic_export.gather_document_data)
        for key in (
            "oos_sharpe_regime_conditional",
            "oos_sharpe_benchmark",
            "oos_sharpe_equal_weight",
            "correlation_pre_2022",
            "correlation_post_2022",
            "max_drawdown_benchmark",
            "max_drawdown_regime_conditional",
            "play_by_play_events",
            "play_by_play_add_value",
            "oos_window_months",
            "oos_window_pct_of_study",
            "current_regime",
            "current_equity_weight",
        ):
            assert f'"{key}"' in src, (
                f"validated_constants is missing key {key} -- "
                "the brief story plan resolver expects it")


# ── 3. build_substitution_table -- live_signals kwarg ─────────────────────


class TestBuildSubstitutionTableLiveSignals:
    """build_substitution_table now accepts a live_signals kwarg
    that the caller populates from regime_signals_cache. The 5
    watchpoint tokens (VIX, credit spread, yield curve, equity
    trend, ESS) now resolve from this kwarg instead of from
    the strategy_cache (where they never lived)."""

    def test_signature_accepts_live_signals(self):
        from tools.numeric_substitution import (
            build_substitution_table,
        )
        import inspect
        sig = inspect.signature(build_substitution_table)
        assert "live_signals" in sig.parameters

    def test_vix_resolves_from_live_signals_vix_level(self):
        """The regime_signals_cache field name is `vix_level` --
        the substitution table must read that key, not the old
        `vix_current` that the strategy_cache never carried."""
        from tools.numeric_substitution import (
            build_substitution_table,
        )
        table = build_substitution_table(
            strategy_cache={},
            cio_recommendation={},
            live_signals={
                "vix_level": 17.5,
                "credit_spread": 320,
                "yield_curve_slope": -0.45,
                "equity_trend": 0.082,
            },
        )
        assert table["{{VIX_CURRENT}}"] == "17.5"
        assert table["{{CREDIT_SPREAD_CURRENT}}"] == "320"
        assert table["{{YIELD_CURVE_CURRENT}}"] == "-0.45"
        # equity_trend renders as a percentage (format_pct).
        assert "%" in table["{{EQUITY_TREND_CURRENT}}"]

    def test_watchpoints_em_dash_when_live_signals_missing(self):
        """When live_signals is None or empty (cold cache), each
        watchpoint resolves to em-dash. The brief renders cleanly
        rather than carrying an unresolved token through."""
        from tools.numeric_substitution import (
            build_substitution_table,
        )
        table = build_substitution_table(
            strategy_cache={}, cio_recommendation={},
            live_signals=None,
        )
        assert table["{{VIX_CURRENT}}"] == "—"
        assert table["{{CREDIT_SPREAD_CURRENT}}"] == "—"
        assert table["{{YIELD_CURVE_CURRENT}}"] == "—"

    def test_ess_resolves_from_cio_confidence(self):
        """ESS is not in regime_signals_cache; it lives on the
        CIO recommendation's confidence dict. Pin that path."""
        from tools.numeric_substitution import (
            build_substitution_table,
        )
        table = build_substitution_table(
            strategy_cache={},
            cio_recommendation={
                "confidence": {"probability": 0.974, "ess": 164.5}
            },
            live_signals=None,
        )
        assert table["{{ESS_CURRENT}}"] == "164.5"

    def test_oos_window_pct_of_study_token_present(self):
        """New token added in PR A. Defaults to 18.5 (53/287) when
        the kwarg isn't supplied so a cold cache still renders
        a plausible value."""
        from tools.numeric_substitution import (
            build_substitution_table,
        )
        table = build_substitution_table(
            strategy_cache={}, cio_recommendation={},
        )
        assert table["{{OOS_WINDOW_PCT_OF_STUDY}}"] == "18.5"
        # Explicit kwarg overrides the default.
        table2 = build_substitution_table(
            strategy_cache={}, cio_recommendation={},
            oos_window_pct_of_study=20.3,
        )
        assert table2["{{OOS_WINDOW_PCT_OF_STUDY}}"] == "20.3"


# ── 4. _substitute_slide_content -- table_data walk ───────────────────────


class TestSubstituteSlideContentTableData:
    """_substitute_slide_content now walks table_data cells. Before
    PR A it only walked title / headline / speaker_notes / bullets,
    which left every {{TOKEN}} embedded in a slide table column
    unsubstituted -- the root cause of the 23 unresolved placeholders
    reported in production."""

    def test_table_data_headers_substituted(self):
        from main import _substitute_slide_content
        slide = {
            "title": "Strategy Comparison",
            "table_data": {
                "headers": [
                    "Strategy",
                    "Sharpe: {{BENCHMARK_SHARPE}}",
                    "Max DD: {{BENCHMARK_MAX_DD}}",
                ],
                "rows": [],
            },
        }
        table = {
            "{{BENCHMARK_SHARPE}}": "0.54",
            "{{BENCHMARK_MAX_DD}}": "-52.6%",
        }
        out = _substitute_slide_content(slide, table, slide_number=2)
        assert out["table_data"]["headers"][1] == "Sharpe: 0.54"
        assert out["table_data"]["headers"][2] == "Max DD: -52.6%"

    def test_table_data_rows_substituted(self):
        from main import _substitute_slide_content
        slide = {
            "title": "Performance",
            "table_data": {
                "headers": ["Strategy", "Sharpe"],
                "rows": [
                    ["Blend", "{{REGIME_SWITCHING_SHARPE}}"],
                    ["Benchmark", "{{BENCHMARK_SHARPE}}"],
                ],
            },
        }
        table = {
            "{{REGIME_SWITCHING_SHARPE}}": "0.63",
            "{{BENCHMARK_SHARPE}}": "0.54",
        }
        out = _substitute_slide_content(slide, table, slide_number=3)
        assert out["table_data"]["rows"][0][1] == "0.63"
        assert out["table_data"]["rows"][1][1] == "0.54"

    def test_table_data_non_string_cells_passthrough(self):
        """Numeric / None / nested-dict cells pass through
        untouched. The walk only substitutes string cells."""
        from main import _substitute_slide_content
        slide = {
            "title": "T",
            "table_data": {
                "headers": ["A", None, 123],
                "rows": [
                    ["{{BENCHMARK_SHARPE}}", 1.5, None],
                ],
            },
        }
        table = {"{{BENCHMARK_SHARPE}}": "0.54"}
        out = _substitute_slide_content(slide, table, slide_number=1)
        # String cell got substituted.
        assert out["table_data"]["rows"][0][0] == "0.54"
        # Non-strings passed through.
        assert out["table_data"]["rows"][0][1] == 1.5
        assert out["table_data"]["rows"][0][2] is None
        assert out["table_data"]["headers"][1] is None
        assert out["table_data"]["headers"][2] == 123

    def test_no_table_data_field_no_op(self):
        from main import _substitute_slide_content
        slide = {"title": "T", "bullets": []}
        table = {"{{BENCHMARK_SHARPE}}": "0.54"}
        out = _substitute_slide_content(slide, table, slide_number=1)
        # No table_data → no crash; slide passes through.
        assert "table_data" not in out

    def test_existing_title_headline_bullets_still_substituted(self):
        """Regression: the original substitution targets must
        continue to work. The new table_data walk is additive."""
        from main import _substitute_slide_content
        slide = {
            "title": "Sharpe: {{BENCHMARK_SHARPE}}",
            "headline": "DD: {{BENCHMARK_MAX_DD}}",
            "speaker_notes": "Cite {{REGIME_SWITCHING_SHARPE}}",
            "bullets": ["{{REGIME_SWITCHING_MAX_DD}} blend DD"],
        }
        table = {
            "{{BENCHMARK_SHARPE}}":         "0.54",
            "{{BENCHMARK_MAX_DD}}":         "-52.6%",
            "{{REGIME_SWITCHING_SHARPE}}":  "0.63",
            "{{REGIME_SWITCHING_MAX_DD}}":  "-29.7%",
        }
        out = _substitute_slide_content(slide, table, slide_number=4)
        assert out["title"] == "Sharpe: 0.54"
        assert out["headline"] == "DD: -52.6%"
        assert out["speaker_notes"] == "Cite 0.63"
        assert out["bullets"] == ["-29.7% blend DD"]
