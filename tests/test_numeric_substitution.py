"""tests/test_numeric_substitution.py -- the deterministic numeric
substitution architecture (June 21 2026).

Pins:
  * format_sharpe / format_pct / format_corr / format_months_from_days
    behave deterministically on real + edge-case inputs
  * build_substitution_table emits every required brief-side token
  * per-strategy dynamic tokens land for every strategy in the cache
  * get_substitution_table caches by data_hash and serves the same
    dict instance on a second call (the determinism guarantee
    cross-deliverable consistency relies on)
  * apply_substitutions replaces every table-known token, leaves
    unknown tokens intact (so the audit can flag them)
  * unresolved_placeholders identifies surviving tokens
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")


# ── Formatters ──────────────────────────────────────────────────────────


class TestFormatters:

    def test_format_sharpe_rounds_to_2dp(self):
        from tools.numeric_substitution import format_sharpe
        assert format_sharpe(1.2376) == "1.24"
        assert format_sharpe(0.733) == "0.73"
        assert format_sharpe(0.5) == "0.50"

    def test_format_sharpe_em_dash_on_invalid(self):
        from tools.numeric_substitution import format_sharpe
        assert format_sharpe(None) == "—"
        assert format_sharpe("abc") == "—"

    def test_format_pct_preserves_sign(self):
        from tools.numeric_substitution import format_pct
        # The drawdown case: negative magnitude must survive.
        assert format_pct(-0.526) == "-52.6%"
        assert format_pct(0.057) == "5.7%"
        assert format_pct(0) == "0.0%"

    def test_format_corr_preserves_sign(self):
        from tools.numeric_substitution import format_corr
        # The correlation regime break case: the sign IS the finding.
        assert format_corr(-0.05) == "-0.05"
        assert format_corr(0.57) == "+0.57"
        # Em dash on invalid input.
        assert format_corr(None) == "—"

    def test_format_months_from_days_em_dash_on_zero(self):
        from tools.numeric_substitution import format_months_from_days
        # 0 days isn't a real recovery; em dash rather than "0 months".
        assert format_months_from_days(0) == "—"
        # 21 days -> 1 month (the trading-days-per-month constant).
        assert format_months_from_days(21) == "1 months"
        # 105 days -> 5 months.
        assert format_months_from_days(105) == "5 months"


# ── build_substitution_table ─────────────────────────────────────────────


def _fake_cache() -> dict:
    """Strategy cache shape the substitution table expects. Three
    strategies (BENCHMARK / CLASSIC_60_40 / REGIME_SWITCHING) plus
    a fourth (MIN_VARIANCE) to exercise the per-strategy dynamic
    token loop."""
    return {
        "BENCHMARK": {
            "sharpe_ratio": 0.5374,
            "max_drawdown": -0.526,
            "drawdown_recovery_days": 1029,  # ~49 months
            "cagr": 0.084,
            "volatility": 0.155,
            "post_2022_sharpe": 0.4934,
            "pre_2022_sharpe": 0.612,
        },
        "CLASSIC_60_40": {
            "sharpe_ratio": 0.612,
            "max_drawdown": -0.301,
            "drawdown_recovery_days": 420,
            "cagr": 0.061,
            "volatility": 0.098,
            "post_2022_sharpe": 0.350,
            "pre_2022_sharpe": 0.690,
        },
        "REGIME_SWITCHING": {
            "sharpe_ratio": 0.86,
            "max_drawdown": -0.253,
            "drawdown_recovery_days": 252,  # ~12 months
            "cagr": 0.072,
            "volatility": 0.084,
            "post_2022_sharpe": 0.282,
            "pre_2022_sharpe": 0.95,
        },
        "MIN_VARIANCE": {
            "sharpe_ratio": 0.45,
            "max_drawdown": -0.18,
            "drawdown_recovery_days": 168,
            "cagr": 0.045,
            "volatility": 0.062,
        },
    }


def _fake_cio() -> dict:
    return {
        "regime": "BULL",
        "confidence": {"probability": 0.974, "ess": 164},
        "implied_equity": 0.789,
        "implied_ig": 0.184,
        "implied_hy": 0.012,
    }


class TestBuildSubstitutionTable:

    def test_brief_side_tokens_all_present(self):
        from tools.numeric_substitution import build_substitution_table
        table = build_substitution_table(
            _fake_cache(), _fake_cio(),
            data_hash="c421fb895347f924",
            oos_sharpe_blend=0.86,
            oos_sharpe_benchmark=0.43,
            pre_2022_eq_ig_correlation=-0.05,
            post_2022_eq_ig_correlation=0.57,
            study_months=287,
        )
        for token in (
            "{{OOS_SHARPE_BLEND}}",
            "{{OOS_SHARPE_BENCHMARK}}",
            "{{OOS_WINDOW}}",
            "{{OOS_WINDOW_MONTHS}}",
            "{{OOS_SHARPE_IMPROVEMENT_PCT}}",
            "{{REGIME_SWITCHING_SHARPE}}",
            "{{BENCHMARK_SHARPE}}",
            "{{CLASSIC_6040_SHARPE}}",
            "{{REGIME_SWITCHING_MAX_DD}}",
            "{{BENCHMARK_MAX_DD}}",
            "{{CLASSIC_6040_MAX_DD}}",
            "{{DD_REDUCTION_REGIME_SWITCHING}}",
            "{{REGIME_SWITCHING_RECOVERY}}",
            "{{BENCHMARK_RECOVERY}}",
            "{{PRE_2022_EQ_IG_CORR}}",
            "{{POST_2022_EQ_IG_CORR}}",
            "{{REGIME_SWITCHING_POST2022_SHARPE}}",
            "{{BENCHMARK_POST2022_SHARPE}}",
            "{{CURRENT_REGIME}}",
            "{{CURRENT_EQUITY_PCT}}",
            "{{STUDY_MONTHS}}",
            "{{DATA_HASH}}",
        ):
            assert token in table, f"missing required token: {token}"

    def test_values_use_centralised_formatters(self):
        from tools.numeric_substitution import build_substitution_table
        table = build_substitution_table(
            _fake_cache(), _fake_cio(),
            data_hash="c421fb89",
            oos_sharpe_blend=0.86, oos_sharpe_benchmark=0.43,
            pre_2022_eq_ig_correlation=-0.05,
            post_2022_eq_ig_correlation=0.57,
        )
        assert table["{{OOS_SHARPE_BLEND}}"] == "0.86"
        assert table["{{OOS_SHARPE_BENCHMARK}}"] == "0.43"
        assert table["{{PRE_2022_EQ_IG_CORR}}"] == "-0.05"
        assert table["{{POST_2022_EQ_IG_CORR}}"] == "+0.57"
        assert table["{{BENCHMARK_MAX_DD}}"] == "-52.6%"
        assert table["{{CURRENT_REGIME}}"] == "BULL"
        # OOS improvement: 0.86/0.43 - 1 = 100% (sign +)
        assert table["{{OOS_SHARPE_IMPROVEMENT_PCT}}"] == "+100%"
        # data_hash trimmed to 8 chars.
        assert table["{{DATA_HASH}}"] == "c421fb89"

    def test_em_dash_when_kwarg_missing(self):
        from tools.numeric_substitution import build_substitution_table
        table = build_substitution_table(
            _fake_cache(), _fake_cio(),
            data_hash="x",
            # oos_sharpe_blend / benchmark intentionally omitted
        )
        assert table["{{OOS_SHARPE_BLEND}}"] == "—"
        assert table["{{OOS_SHARPE_BENCHMARK}}"] == "—"
        # The derived improvement must also degrade rather than crash.
        assert table["{{OOS_SHARPE_IMPROVEMENT_PCT}}"] == "—"


class TestPerStrategyDynamicTokens:

    def test_every_cache_strategy_gets_five_tokens(self):
        from tools.numeric_substitution import get_substitution_table
        from tools.numeric_substitution import clear_substitution_cache
        clear_substitution_cache()
        table = get_substitution_table(
            "dyn_hash_1", _fake_cache(), _fake_cio())
        for strategy in (
            "BENCHMARK", "CLASSIC_60_40", "REGIME_SWITCHING",
            "MIN_VARIANCE",
        ):
            for suffix in (
                "SHARPE", "MAX_DD", "CAGR", "VOLATILITY", "RECOVERY",
            ):
                token = f"{{{{{strategy}_{suffix}}}}}"
                assert token in table, (
                    f"missing per-strategy token: {token}")
        # Spot-check that MIN_VARIANCE (not in the brief-side
        # hardcoded list) lands a real value.
        assert table["{{MIN_VARIANCE_SHARPE}}"] == "0.45"
        assert table["{{MIN_VARIANCE_MAX_DD}}"] == "-18.0%"


class TestGetSubstitutionTableCaching:

    def test_same_data_hash_returns_same_dict_instance(self):
        from tools.numeric_substitution import (
            clear_substitution_cache, get_substitution_table,
        )
        clear_substitution_cache()
        table1 = get_substitution_table(
            "h1", _fake_cache(), _fake_cio())
        table2 = get_substitution_table(
            "h1", _fake_cache(), _fake_cio())
        # Same instance -- the determinism guarantee across
        # deliverables that share one table.
        assert table1 is table2

    def test_different_data_hash_builds_new_table(self):
        from tools.numeric_substitution import (
            clear_substitution_cache, get_substitution_table,
        )
        clear_substitution_cache()
        table_a = get_substitution_table(
            "h_a", _fake_cache(), _fake_cio())
        table_b = get_substitution_table(
            "h_b", _fake_cache(), _fake_cio())
        assert table_a is not table_b
        # data_hash kwarg flowed through to {{DATA_HASH}}.
        assert table_a["{{DATA_HASH}}"] == "h_a"
        assert table_b["{{DATA_HASH}}"] == "h_b"

    def test_rebuild_true_overrides_cache_hit(self):
        from tools.numeric_substitution import (
            clear_substitution_cache, get_substitution_table,
        )
        clear_substitution_cache()
        table1 = get_substitution_table(
            "rebuild_hash", _fake_cache(), _fake_cio())
        table2 = get_substitution_table(
            "rebuild_hash", _fake_cache(), _fake_cio(),
            rebuild=True)
        assert table1 is not table2

    def test_empty_data_hash_does_not_cache(self):
        """An empty hash signals data_status unavailable -- the
        builder runs but the result is NOT cached (so the next call
        with a real hash builds afresh)."""
        from tools.numeric_substitution import (
            _substitution_cache, clear_substitution_cache,
            get_substitution_table,
        )
        clear_substitution_cache()
        get_substitution_table("", _fake_cache(), _fake_cio())
        assert "" not in _substitution_cache


# ── apply_substitutions ──────────────────────────────────────────────────


class TestApplySubstitutions:

    def test_replaces_every_table_known_token(self):
        from tools.numeric_substitution import apply_substitutions
        text = (
            "The blend achieved {{OOS_SHARPE_BLEND}} versus "
            "{{OOS_SHARPE_BENCHMARK}} for the benchmark.")
        out, replaced = apply_substitutions(
            text, {
                "{{OOS_SHARPE_BLEND}}": "1.24",
                "{{OOS_SHARPE_BENCHMARK}}": "0.73",
            })
        assert "{{" not in out
        assert "1.24" in out
        assert "0.73" in out
        assert set(replaced) == {
            "{{OOS_SHARPE_BLEND}}", "{{OOS_SHARPE_BENCHMARK}}"}

    def test_leaves_unknown_tokens_intact(self):
        """Unknown tokens must survive the substitution pass so the
        document audit's check_unresolved_placeholders can flag
        them. Silent removal would hide a writer that invented its
        own token name."""
        from tools.numeric_substitution import apply_substitutions
        out, replaced = apply_substitutions(
            "Value: {{UNKNOWN_FIGURE}}",
            {"{{OOS_SHARPE_BLEND}}": "1.24"})
        assert "{{UNKNOWN_FIGURE}}" in out
        assert replaced == []

    def test_replaces_multiple_occurrences(self):
        from tools.numeric_substitution import apply_substitutions
        text = "{{X}} and {{X}} and {{Y}}"
        out, replaced = apply_substitutions(
            text, {"{{X}}": "A", "{{Y}}": "B"})
        assert out == "A and A and B"
        # Replaced list dedups by token.
        assert set(replaced) == {"{{X}}", "{{Y}}"}

    def test_none_text_returns_empty(self):
        from tools.numeric_substitution import apply_substitutions
        out, replaced = apply_substitutions(None, {"{{X}}": "v"})
        assert out == ""
        assert replaced == []


# ── unresolved_placeholders ──────────────────────────────────────────────


class TestUnresolvedPlaceholders:

    def test_returns_sorted_unique_tokens(self):
        from tools.numeric_substitution import unresolved_placeholders
        text = "{{B}} then {{A}} then {{B}} again"
        out = unresolved_placeholders(text)
        assert out == ["{{A}}", "{{B}}"]

    def test_empty_text_returns_empty_list(self):
        from tools.numeric_substitution import unresolved_placeholders
        assert unresolved_placeholders("") == []
        assert unresolved_placeholders(None) == []

    def test_no_tokens_returns_empty_list(self):
        from tools.numeric_substitution import unresolved_placeholders
        assert unresolved_placeholders(
            "Plain prose, no placeholders.") == []
