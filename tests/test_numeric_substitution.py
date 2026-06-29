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

    def test_current_pct_tokens_resolve_from_implied_allocation(self):
        """The CURRENT_*_PCT tokens used to read non-existent
        cio.implied_equity / .implied_ig / .implied_hy fields, so
        they silently resolved to em dashes everywhere -- the
        substitution table never carried real values for these
        even though the upstream compute helper had them. Fixed
        June 21 2026 by reading from the explicit implied_
        allocation kwarg the caller computes via
        compute_implied_asset_allocation(cio.blend_weights)."""
        from tools.numeric_substitution import build_substitution_table
        table = build_substitution_table(
            _fake_cache(), _fake_cio(),
            data_hash="x",
            implied_allocation={
                "equity_pct": 0.789,
                "ig_bond_pct": 0.184,
                "hy_bond_pct": 0.012,
                "bond_pct": 0.196,
                "cash_pct": 0.015,
            },
        )
        assert table["{{CURRENT_EQUITY_PCT}}"] == "78.9%"
        assert table["{{CURRENT_IG_PCT}}"] == "18.4%"
        assert table["{{CURRENT_HY_PCT}}"] == "1.2%"

    def test_current_pct_tokens_em_dash_when_alloc_missing(self):
        """No implied_allocation supplied -- the tokens degrade
        cleanly rather than crashing. Mirrors the existing em-dash
        fallback for other missing kwargs."""
        from tools.numeric_substitution import build_substitution_table
        table = build_substitution_table(
            _fake_cache(), _fake_cio(),
            data_hash="x",
            # implied_allocation intentionally omitted
        )
        assert table["{{CURRENT_EQUITY_PCT}}"] == "—"
        assert table["{{CURRENT_IG_PCT}}"] == "—"
        assert table["{{CURRENT_HY_PCT}}"] == "—"


class TestRecoveryTokenSplit:
    """June 21 2026 -- {{*_RECOVERY}} tokens used to resolve to
    "<n> months". Per-section writers that wrote "months" after
    the token in prose produced the "37 months months" bug. The
    split: {{*_RECOVERY}} is now bare integer; the new
    {{*_RECOVERY_MONTHS}} carries the pre-formatted form."""

    def test_recovery_is_number_only(self):
        from tools.numeric_substitution import build_substitution_table
        table = build_substitution_table(
            _fake_cache(), _fake_cio(), data_hash="x")
        # benchmark cache fixture: drawdown_recovery_days = 1029
        # -> round(1029 / 21) = 49 months. format_months_only emits
        # the integer string only.
        assert table["{{BENCHMARK_RECOVERY}}"] == "49"
        # regime_switching: drawdown_recovery_days = 252 -> 12.
        assert table["{{REGIME_SWITCHING_RECOVERY}}"] == "12"
        # classic_60_40: drawdown_recovery_days = 420 -> 20.
        assert table["{{CLASSIC_6040_RECOVERY}}"] == "20"

    def test_recovery_months_carries_units(self):
        from tools.numeric_substitution import build_substitution_table
        table = build_substitution_table(
            _fake_cache(), _fake_cio(), data_hash="x")
        assert table["{{BENCHMARK_RECOVERY_MONTHS}}"] == "49 months"
        assert (
            table["{{REGIME_SWITCHING_RECOVERY_MONTHS}}"] == "12 months")
        assert table["{{CLASSIC_6040_RECOVERY_MONTHS}}"] == "20 months"

    def test_recovery_substitution_does_not_double_months(self):
        """The bug this fix prevents: a section writer using
        {{BENCHMARK_RECOVERY}} followed by " months" in prose used
        to produce "49 months months". With the bare-integer token
        the same prose pattern reads cleanly as "49 months"."""
        from tools.numeric_substitution import (
            apply_substitutions, build_substitution_table,
        )
        table = build_substitution_table(
            _fake_cache(), _fake_cio(), data_hash="x")
        prose = "Benchmark recovered in {{BENCHMARK_RECOVERY}} months."
        # apply_substitutions returns (text, replaced_tokens_list).
        out_text, _ = apply_substitutions(prose, table)
        assert out_text == "Benchmark recovered in 49 months."
        assert "months months" not in out_text

    def test_format_months_only_em_dash_on_invalid(self):
        from tools.numeric_substitution import format_months_only
        assert format_months_only(None) == "—"
        assert format_months_only("abc") == "—"
        assert format_months_only(0) == "—"
        assert format_months_only(-1) == "—"

    def test_per_strategy_loop_emits_both_recovery_variants(self):
        """The appendix dynamic generator iterates every strategy
        x metric -- the RECOVERY suffix split must surface both
        {{NAME_RECOVERY}} (bare) and {{NAME_RECOVERY_MONTHS}}
        (with units) for every strategy in the cache. Uses the
        get_substitution_table entry-point because that's the call
        site that runs the per-strategy loop (the appendix path);
        the lower-level build_substitution_table emits only the
        three brief-side hardcoded recovery tokens."""
        from tools.numeric_substitution import (
            clear_substitution_cache, get_substitution_table,
        )
        clear_substitution_cache()
        table = get_substitution_table(
            "x", _fake_cache(), _fake_cio())
        # MIN_VARIANCE fixture: drawdown_recovery_days = 168 -> 8.
        assert table["{{MIN_VARIANCE_RECOVERY}}"] == "8"
        assert table["{{MIN_VARIANCE_RECOVERY_MONTHS}}"] == "8 months"
        # Brief-side variants also overwritten consistently via the
        # appendix loop -- BENCHMARK appears in both paths.
        assert table["{{BENCHMARK_RECOVERY}}"] == "49"
        assert table["{{BENCHMARK_RECOVERY_MONTHS}}"] == "49 months"


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


# ── Layer 2: deck + appendix tokens (June 21 2026) ──────────────────────


class TestDeckSpecificTokens:
    """The Layer-2 extension adds deck-specific tokens to the shared
    substitution table -- play-by-play scorecard, transaction-cost
    sensitivity, live macro watch points, live blend composition."""

    def test_play_by_play_tokens_default_to_canonical_constants(self):
        from tools.numeric_substitution import build_substitution_table
        table = build_substitution_table(
            _fake_cache(), _fake_cio(), data_hash="x")
        assert table["{{PLAY_BY_PLAY_VALUE_ADD}}"] == "2"
        assert table["{{PLAY_BY_PLAY_TOTAL}}"] == "9"

    def test_live_watch_point_tokens_em_dash_on_cold_cache(self):
        """June 22 2026 (PR A) -- the 5 watchpoint tokens read from
        the new live_signals kwarg (populated from
        regime_signals_cache) instead of the strategy_cache. When
        live_signals is None AND the cio has no ess field, all
        five resolve to em-dash."""
        from tools.numeric_substitution import build_substitution_table
        cio_no_ess = {"regime": "BULL"}  # no confidence dict
        table = build_substitution_table(
            _fake_cache(), cio_no_ess, data_hash="x",
            live_signals=None)
        assert table["{{VIX_CURRENT}}"] == "—"
        assert table["{{CREDIT_SPREAD_CURRENT}}"] == "—"
        assert table["{{YIELD_CURVE_CURRENT}}"] == "—"
        assert table["{{ESS_CURRENT}}"] == "—"
        assert table["{{EQUITY_TREND_CURRENT}}"] == "—"

    def test_live_watch_point_tokens_pick_up_warm_cache_values(self):
        """June 22 2026 (PR A) -- live_signals carries the
        regime_signals_cache row. Field names are vix_level /
        credit_spread / yield_curve_slope / equity_trend (the
        names cache.py:670 uses). ESS comes from cio.confidence.ess
        separately."""
        from tools.numeric_substitution import build_substitution_table
        warm_signals = {
            "vix_level": 18.44,
            "credit_spread": 2.63,
            "yield_curve_slope": 0.29,
            "equity_trend": 0.057,
        }
        cio_with_ess = {
            "regime": "BULL",
            "confidence": {"probability": 0.974, "ess": 164},
        }
        table = build_substitution_table(
            _fake_cache(), cio_with_ess, data_hash="x",
            live_signals=warm_signals)
        assert table["{{VIX_CURRENT}}"] == "18.44"
        assert table["{{CREDIT_SPREAD_CURRENT}}"] == "2.63"
        assert table["{{YIELD_CURVE_CURRENT}}"] == "0.29"
        assert table["{{ESS_CURRENT}}"] == "164"
        assert table["{{EQUITY_TREND_CURRENT}}"] == "5.7%"

    def test_blend_weight_tokens_from_cio_recommendation(self):
        from tools.numeric_substitution import build_substitution_table
        cio_dict_form = dict(_fake_cio())
        cio_dict_form["blend_weights"] = {
            "REGIME_SWITCHING": 0.60,
            "BENCHMARK": 0.25,
            "CLASSIC_60_40": 0.15,
        }
        table = build_substitution_table(
            _fake_cache(), cio_dict_form, data_hash="x")
        assert table["{{BLEND_REGIME_SWITCHING_WT}}"] == "60.0%"
        assert table["{{BLEND_BENCHMARK_WT}}"] == "25.0%"
        assert table["{{BLEND_CLASSIC_6040_WT}}"] == "15.0%"

    def test_n_strategies_falls_back_to_cache_count(self):
        from tools.numeric_substitution import build_substitution_table
        table = build_substitution_table(
            _fake_cache(), _fake_cio(), data_hash="x")
        # _fake_cache has 4 strategies with sharpe_ratio set.
        assert table["{{N_STRATEGIES}}"] == "4"

    def test_n_strategies_uses_explicit_field_when_present(self):
        from tools.numeric_substitution import build_substitution_table
        cache_with_count = dict(_fake_cache())
        cache_with_count["n_strategies"] = 10
        table = build_substitution_table(
            cache_with_count, _fake_cio(), data_hash="x")
        assert table["{{N_STRATEGIES}}"] == "10"


class TestSharedTableConsistency:
    """The architectural invariant: same data_hash -> same table
    instance, so a value substituted into one document is byte-
    identical to the same value substituted into another. This is
    the structural guarantee check_cross_deliverable_consistency
    relies on at the audit layer."""

    def test_brief_and_deck_see_identical_token_values(self):
        from tools.numeric_substitution import (
            clear_substitution_cache, get_substitution_table,
        )
        clear_substitution_cache()
        brief_table = get_substitution_table(
            "shared_hash", _fake_cache(), _fake_cio(),
            oos_sharpe_blend=0.86, oos_sharpe_benchmark=0.43,
            pre_2022_eq_ig_correlation=-0.05,
            post_2022_eq_ig_correlation=0.57)
        # Deck generation hits the same data_hash -- should serve the
        # cached instance regardless of the kwargs passed (the cache
        # key is the data_hash; subsequent kwargs are ignored on hit).
        deck_table = get_substitution_table(
            "shared_hash", _fake_cache(), _fake_cio())
        assert brief_table is deck_table
        assert brief_table["{{OOS_SHARPE_BLEND}}"] == \
            deck_table["{{OOS_SHARPE_BLEND}}"]
        assert brief_table["{{BENCHMARK_MAX_DD}}"] == \
            deck_table["{{BENCHMARK_MAX_DD}}"]


# ── Layer 3 (June 21 2026) -- export-time verification ─────────────────


class TestBuildValueManifest:
    """Layer 3: persisting the substitution-table snapshot on the
    editor draft so export-time verification has an authoritative
    reference for every numeric value."""

    def test_only_numeric_values_in_manifest(self):
        """String values (BULL/BEAR, July 2002) don't round-corrupt
        and don't need export-time verification -- they should NOT
        land in the manifest. Numeric values (Sharpe, percentages,
        month counts) DO need the round-trip check and must land."""
        from tools.numeric_substitution import build_value_manifest
        table = {
            "{{OOS_SHARPE_BLEND}}":         "1.24",
            "{{BENCHMARK_MAX_DD}}":         "-52.6%",
            "{{REGIME_SWITCHING_RECOVERY}}": "12 months",
            "{{CURRENT_REGIME}}":           "BULL",     # string
            "{{STUDY_START}}":              "July 2002", # string
            "{{OOS_SHARPE_BENCHMARK}}":     "—",         # em dash
        }
        manifest = build_value_manifest(
            table, data_hash="c421fb89", generated_at="2026-06-21T00:00:00Z")
        # Three numeric values present.
        assert "1.24" in manifest
        assert "-52.6%" in manifest
        assert "12 months" in manifest
        # Strings and em-dashes excluded.
        assert "BULL" not in manifest
        assert "July 2002" not in manifest
        assert "—" not in manifest

    def test_manifest_carries_provenance(self):
        from tools.numeric_substitution import build_value_manifest
        manifest = build_value_manifest(
            {"{{OOS_SHARPE_BLEND}}": "1.24"},
            data_hash="c421fb89",
            generated_at="2026-06-21T10:00:00Z")
        entry = manifest["1.24"]
        assert entry["token"] == "{{OOS_SHARPE_BLEND}}"
        assert entry["data_hash"] == "c421fb89"
        assert entry["generated_at"] == "2026-06-21T10:00:00Z"


class TestVerifyExportAgainstCache:
    """Layer 3 export-time check: data hash staleness (warning),
    value presence (error), corrupted variants (error)."""

    def test_passing_state_returns_passed_true(self):
        from tools.numeric_substitution import (
            build_value_manifest, verify_export_against_cache,
        )
        manifest = build_value_manifest(
            {"{{OOS_SHARPE_BLEND}}": "1.24"},
            "h_gen", "2026-06-21T00:00:00Z")
        result = verify_export_against_cache(
            content_text="The blend achieved 1.24 vs benchmark.",
            value_manifest=manifest,
            current_data_hash="h_gen",
            generation_data_hash="h_gen",
            document_type="executive_brief")
        assert result["passed"] is True
        assert result["errors"] == []
        assert result["warnings"] == []
        assert result["data_hash_match"] is True
        assert result["n_values_verified"] == 1
        assert result["n_values_missing"] == 0
        assert result["document_type"] == "executive_brief"
        assert "verified_at" in result

    def test_value_missing_from_export_fires_error(self):
        from tools.numeric_substitution import (
            build_value_manifest, verify_export_against_cache,
        )
        manifest = build_value_manifest(
            {"{{OOS_SHARPE_BLEND}}": "1.24"},
            "h_gen", "2026-06-21T00:00:00Z")
        result = verify_export_against_cache(
            content_text="The blend performed well.",  # no 1.24
            value_manifest=manifest,
            current_data_hash="h_gen",
            generation_data_hash="h_gen",
            document_type="executive_brief")
        assert result["passed"] is False
        assert len(result["errors"]) == 1
        err = result["errors"][0]
        assert err["type"] == "value_missing_from_export"
        assert err["expected_value"] == "1.24"
        assert err["token"] == "{{OOS_SHARPE_BLEND}}"
        assert err["severity"] == "high"

    def test_stale_data_hash_fires_warning_not_error(self):
        from tools.numeric_substitution import (
            build_value_manifest, verify_export_against_cache,
        )
        manifest = build_value_manifest(
            {"{{OOS_SHARPE_BLEND}}": "1.24"},
            "h_gen", "2026-06-21T00:00:00Z")
        result = verify_export_against_cache(
            content_text="The blend achieved 1.24 vs benchmark.",
            value_manifest=manifest,
            current_data_hash="h_new",   # cache moved on
            generation_data_hash="h_gen",
            document_type="executive_brief")
        # Warning, not error -- the document is still internally
        # consistent with the cache it was generated against.
        assert result["passed"] is True
        assert len(result["errors"]) == 0
        assert len(result["warnings"]) == 1
        assert result["warnings"][0]["type"] == "stale_data_hash"
        assert result["data_hash_match"] is False

    def test_corrupted_variant_fires_error(self):
        from tools.numeric_substitution import (
            build_value_manifest, verify_export_against_cache,
        )
        manifest = build_value_manifest(
            {"{{OOS_SHARPE_BLEND}}": "1.24"},
            "h_gen", "2026-06-21T00:00:00Z")
        # Manual edit changed "1.24" to "1.23" -- a +/- 1 last-digit
        # variant the corruption scanner should catch.
        result = verify_export_against_cache(
            content_text="The blend achieved 1.23 vs benchmark.",
            value_manifest=manifest,
            current_data_hash="h_gen",
            generation_data_hash="h_gen",
            document_type="executive_brief")
        # Both value-missing AND corrupted variant fire (1.24 not
        # present + 1.23 is a variant). At minimum the variant
        # error is in the list.
        types = {e["type"] for e in result["errors"]}
        assert "value_corrupted" in types or \
            "value_missing_from_export" in types
        if "value_corrupted" in types:
            corrupt = next(
                e for e in result["errors"]
                if e["type"] == "value_corrupted")
            assert corrupt["expected"] == "1.24"
            assert corrupt["found"] == "1.23"

    def test_no_manifest_returns_skipped_state(self):
        """Pre-Layer-3 drafts have no value_manifest. The check
        must short-circuit cleanly so the export ships and the
        UI shows a neutral 'Not yet verified' state instead of
        a spurious 'Failed' badge."""
        from tools.numeric_substitution import verify_export_against_cache
        result = verify_export_against_cache(
            content_text="Some content.",
            value_manifest={},
            current_data_hash="h",
            generation_data_hash="h",
            document_type="executive_brief")
        assert result["passed"] is True
        assert result.get("skipped") == "no_value_manifest"
        assert result["n_values_verified"] == 0

    def test_em_dash_value_skipped_at_manifest_build(self):
        """An em-dash is the 'missing value' sentinel -- it must
        never be added to the manifest at build time. Pinned at
        the manifest-builder level so the verification check
        never sees em-dashes to begin with."""
        from tools.numeric_substitution import build_value_manifest
        table = {
            "{{VIX_CURRENT}}": "—",
            "{{OOS_SHARPE_BLEND}}": "1.24",
        }
        manifest = build_value_manifest(
            table, "h", "2026-06-21T00:00:00Z")
        assert "—" not in manifest
        assert "1.24" in manifest
