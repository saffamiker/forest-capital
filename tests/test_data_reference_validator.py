"""tests/test_data_reference_validator.py -- unit tests for the
data reference sheet cross-reference validator (June 22 2026).

Per-strategy validators are tested against mocked Sources so we
don't need a live DB / FRED connection. The dispatch table is
tested separately to confirm the right strategy fires for each
token shape.
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

from datetime import datetime, timedelta, timezone  # noqa: E402

from tools.data_reference_validator import (  # noqa: E402
    Sources, ValidationResult, dispatch_strategy,
    validate_reference_sheet,
    _parse_decimal, _parse_months, _parse_pct,
    _is_stale,
    _validate_locked, _validate_strategy_metric,
    _validate_regime_conditional_sharpe,
    _validate_factor_loading, _validate_net_sharpe,
    _validate_live_signal, _validate_blend_weight,
    _validate_current_asset_pct, _validate_study_months,
)


# ── Parsers ──────────────────────────────────────────────────────────


class TestParsers:

    def test_parse_decimal_handles_signed_strings(self):
        assert _parse_decimal("0.86") == 0.86
        assert _parse_decimal("-0.43") == -0.43
        assert _parse_decimal("+0.62") == 0.62

    def test_parse_decimal_returns_none_for_em_dash(self):
        assert _parse_decimal("—") is None
        assert _parse_decimal("") is None
        assert _parse_decimal(None) is None

    def test_parse_pct_handles_percent_sign(self):
        assert _parse_pct("62.0%") == 0.62
        assert abs(_parse_pct("-22.5%") - (-0.225)) < 1e-9
        assert _parse_pct("+15.0%") == 0.15

    def test_parse_pct_handles_bare_number(self):
        # Decks sometimes render the fraction directly.
        assert _parse_pct("0.62") == 0.62

    def test_parse_months_strips_suffix(self):
        assert _parse_months("8 months") == 8
        assert _parse_months("8") == 8
        # The "37 months months" bug from June 21 must round-trip.
        assert _parse_months("37 months months") == 37
        assert _parse_months("—") is None


# ── Staleness ────────────────────────────────────────────────────────


class TestStaleness:

    def test_recent_timestamp_is_not_stale(self):
        ts = (datetime.now(timezone.utc) - timedelta(
            hours=1)).isoformat()
        assert _is_stale(ts) is False

    def test_old_timestamp_is_stale(self):
        ts = (datetime.now(timezone.utc) - timedelta(
            hours=48)).isoformat()
        assert _is_stale(ts) is True

    def test_none_is_not_stale(self):
        assert _is_stale(None) is False

    def test_malformed_is_not_stale(self):
        # Defensive -- a malformed string returns False rather
        # than raising. The validator can't tell, so we don't
        # downgrade pass to warning on garbage input.
        assert _is_stale("not a timestamp") is False


# ── Locked-constant strategy ────────────────────────────────────────


class TestLockedStrategy:

    def test_locked_constant_returns_skipped(self):
        result = _validate_locked(
            "{{OOS_SHARPE_BLEND}}", "OOS Sharpe (blend)",
            "0.86", Sources())
        assert result.status == "skipped"
        assert result.note == "locked at submission"
        assert result.cache_freshness is None
        # reference and source mirror; the locked constant IS
        # the source (defended at submission, not at runtime).
        assert result.source_value == "0.86"


# ── Strategy-cache metric strategy ──────────────────────────────────


class TestStrategyMetricStrategy:

    def _sources(self, **overrides) -> Sources:
        sc = {
            "BENCHMARK": {
                "sharpe_ratio": 0.54,
                "max_drawdown": -0.34,
                "cagr": 0.082,
                "volatility": 0.151,
                "drawdown_recovery_days": 168,  # 8 months
            },
        }
        return Sources(
            strategy_cache=sc,
            strategy_cache_computed_at=(
                datetime.now(timezone.utc).isoformat()),
            **overrides,
        )

    def test_sharpe_pass_within_tolerance(self):
        result = _validate_strategy_metric(
            "{{BENCHMARK_SHARPE}}", "Benchmark Sharpe",
            "0.54", self._sources())
        assert result.status == "pass"

    def test_sharpe_fail_beyond_tolerance(self):
        result = _validate_strategy_metric(
            "{{BENCHMARK_SHARPE}}", "Benchmark Sharpe",
            "0.42", self._sources())
        assert result.status == "fail"
        assert result.delta is not None
        assert "0.12" in result.delta

    def test_pct_token_handles_percent_format(self):
        # Reference renders "-34.0%"; source has -0.34. Parser
        # converts back to fraction for comparison.
        result = _validate_strategy_metric(
            "{{BENCHMARK_MAX_DD}}", "Benchmark Max DD",
            "-34.0%", self._sources())
        assert result.status == "pass"

    def test_recovery_months_pass_when_days_round_correctly(self):
        # 168 days / 21 = 8 months exactly.
        result = _validate_strategy_metric(
            "{{BENCHMARK_RECOVERY_MONTHS}}",
            "Benchmark recovery (months)",
            "8", self._sources())
        assert result.status == "pass"
        assert result.source_value == "8 months"

    def test_recovery_months_fail_on_mismatch(self):
        result = _validate_strategy_metric(
            "{{BENCHMARK_RECOVERY_MONTHS}}",
            "Benchmark recovery (months)",
            "12", self._sources())
        assert result.status == "fail"

    def test_missing_strategy_returns_skipped(self):
        result = _validate_strategy_metric(
            "{{UNKNOWN_STRATEGY_SHARPE}}", "Unknown",
            "0.5", self._sources())
        assert result.status == "skipped"
        assert result.note == "source unavailable"

    def test_stale_source_downgrades_pass_to_warning(self):
        stale = (datetime.now(timezone.utc) - timedelta(
            hours=48)).isoformat()
        sources = self._sources()
        sources.strategy_cache_computed_at = stale
        result = _validate_strategy_metric(
            "{{BENCHMARK_SHARPE}}", "Benchmark Sharpe",
            "0.54", sources)
        assert result.status == "warning"
        assert "old" in (result.note or "")


# ── regime_conditional strategy ─────────────────────────────────────


class TestRegimeConditionalStrategy:

    def _sources(self) -> Sources:
        return Sources(
            academic_analytics={
                "regime_conditional": [
                    {"strategy": "BENCHMARK",
                     "pre_2022_sharpe": 0.72,
                     "post_2022_sharpe": 0.43},
                ],
            },
            academic_analytics_computed_at=(
                datetime.now(timezone.utc).isoformat()),
        )

    def test_pre_2022_pass(self):
        result = _validate_regime_conditional_sharpe(
            "{{BENCHMARK_PRE2022_SHARPE}}",
            "Benchmark pre-2022 Sharpe",
            "0.72", self._sources())
        assert result.status == "pass"

    def test_post_2022_fail(self):
        result = _validate_regime_conditional_sharpe(
            "{{BENCHMARK_POST2022_SHARPE}}",
            "Benchmark post-2022 Sharpe",
            "0.20", self._sources())
        assert result.status == "fail"

    def test_missing_strategy_row(self):
        result = _validate_regime_conditional_sharpe(
            "{{MISSING_POST2022_SHARPE}}", "Missing",
            "0.5", self._sources())
        assert result.status == "skipped"


# ── factor_loading strategy ─────────────────────────────────────────


class TestFactorLoadingStrategy:

    def _sources(self) -> Sources:
        return Sources(
            academic_analytics={
                "factor_loadings": [
                    {"strategy": "BENCHMARK",
                     "alpha_annualized": 0.0123,
                     "mkt_rf": 0.5500,
                     "smb": 0.1000,
                     "hml": -0.0300,
                     "r_squared": 0.9400},
                ],
            },
            academic_analytics_computed_at=(
                datetime.now(timezone.utc).isoformat()),
        )

    def test_alpha_maps_to_alpha_annualized(self):
        result = _validate_factor_loading(
            "{{BENCHMARK_ALPHA}}", "Benchmark alpha",
            "0.0123", self._sources())
        assert result.status == "pass"

    def test_beta_maps_to_mkt_rf(self):
        result = _validate_factor_loading(
            "{{BENCHMARK_BETA}}", "Benchmark market beta",
            "0.5500", self._sources())
        assert result.status == "pass"

    def test_smb_beta_maps_to_smb(self):
        result = _validate_factor_loading(
            "{{BENCHMARK_SMB_BETA}}", "Benchmark SMB",
            "0.1000", self._sources())
        assert result.status == "pass"

    def test_alpha_fail_beyond_4dp_tolerance(self):
        result = _validate_factor_loading(
            "{{BENCHMARK_ALPHA}}", "Benchmark alpha",
            "0.0200", self._sources())
        assert result.status == "fail"


# ── net_sharpe strategy ─────────────────────────────────────────────


class TestNetSharpeStrategy:

    def _sources(self) -> Sources:
        return Sources(
            oos_cost_sensitivity={
                "scenarios": [
                    {"bps": 10, "net_sharpe": 0.85},
                    {"bps": 15, "net_sharpe": 0.82},
                    {"bps": 20, "net_sharpe": 0.80},
                ],
            },
            oos_cost_sensitivity_computed_at=(
                datetime.now(timezone.utc).isoformat()),
        )

    def test_each_bps_routes_to_its_scenario(self):
        for token, ref in (
                ("{{NET_SHARPE_10BP}}", "0.85"),
                ("{{NET_SHARPE_15BP}}", "0.82"),
                ("{{NET_SHARPE_20BP}}", "0.80")):
            result = _validate_net_sharpe(
                token, "net sharpe", ref, self._sources())
            assert result.status == "pass", (
                f"{token}: {result.status} {result.delta}")

    def test_missing_bps_returns_skipped(self):
        result = _validate_net_sharpe(
            "{{NET_SHARPE_99BP}}", "Net Sharpe (99 bps)",
            "0.5", self._sources())
        assert result.status == "skipped"


# ── live_signals strategy ───────────────────────────────────────────


class TestLiveSignalStrategy:

    def _sources(self) -> Sources:
        return Sources(live_signals={
            "vix_level": 18.42,
            "yield_curve_slope": 0.34,
            "credit_spread": 3.12,
            "equity_trend": 0.062,
        })

    def test_vix_pass(self):
        result = _validate_live_signal(
            "{{VIX_CURRENT}}", "VIX", "18.42",
            self._sources())
        assert result.status == "pass"

    def test_equity_trend_uses_pct_format(self):
        # equity_trend is stored as fraction; reference renders
        # as "+6.2%". Parser handles either form.
        result = _validate_live_signal(
            "{{EQUITY_TREND_CURRENT}}", "Equity trend",
            "+6.2%", self._sources())
        assert result.status == "pass"

    def test_unknown_token_returns_skipped(self):
        result = _validate_live_signal(
            "{{NOT_A_REGIME_TOKEN}}", "?", "0.5",
            self._sources())
        assert result.status == "skipped"


# ── blend_weight strategy ───────────────────────────────────────────


class TestBlendWeightStrategy:

    def _sources(self) -> Sources:
        return Sources(
            cio_row={
                "blend_weights": {
                    "REGIME_SWITCHING": 0.50,
                    "BENCHMARK": 0.30,
                    # The CIO uses CLASSIC_60_40 with underscore;
                    # the token says CLASSIC_6040 -- the strategy
                    # maps the slug to the cio key.
                    "CLASSIC_60_40": 0.20,
                },
            },
            cio_computed_at=(
                datetime.now(timezone.utc).isoformat()),
        )

    def test_regime_switching_weight_pass(self):
        result = _validate_blend_weight(
            "{{BLEND_REGIME_SWITCHING_WT}}",
            "Regime Switching weight",
            "50.0%", self._sources())
        assert result.status == "pass"

    def test_classic_6040_slug_maps_to_classic_60_40_key(self):
        result = _validate_blend_weight(
            "{{BLEND_CLASSIC_6040_WT}}",
            "Classic 60/40 weight",
            "20.0%", self._sources())
        assert result.status == "pass"

    def test_missing_cio_row_returns_skipped(self):
        result = _validate_blend_weight(
            "{{BLEND_REGIME_SWITCHING_WT}}",
            "Regime Switching weight",
            "50.0%", Sources())
        assert result.status == "skipped"
        assert result.note == "source unavailable"


# ── current_asset_pct strategy ──────────────────────────────────────


class TestCurrentAssetPctStrategy:

    def _sources(self) -> Sources:
        return Sources(
            implied_alloc={
                "equity_pct": 0.80,
                "ig_bond_pct": 0.15,
                "hy_bond_pct": 0.05,
            },
            cio_computed_at=(
                datetime.now(timezone.utc).isoformat()),
        )

    def test_equity_pct_pass(self):
        result = _validate_current_asset_pct(
            "{{CURRENT_EQUITY_PCT}}", "Current equity %",
            "80.0%", self._sources())
        assert result.status == "pass"

    def test_missing_implied_alloc_returns_skipped(self):
        result = _validate_current_asset_pct(
            "{{CURRENT_EQUITY_PCT}}", "Current equity %",
            "80.0%", Sources())
        assert result.status == "skipped"


# ── study_months strategy ───────────────────────────────────────────


class TestStudyMonthsStrategy:

    def test_pass_when_count_matches(self):
        result = _validate_study_months(
            "{{STUDY_MONTHS}}", "Study months",
            "287", Sources(n_monthly_months=287))
        assert result.status == "pass"

    def test_fail_on_mismatch(self):
        result = _validate_study_months(
            "{{STUDY_MONTHS}}", "Study months",
            "200", Sources(n_monthly_months=287))
        assert result.status == "fail"
        assert "Δ" in (result.delta or "")

    def test_missing_source_returns_skipped(self):
        result = _validate_study_months(
            "{{STUDY_MONTHS}}", "Study months",
            "287", Sources(n_monthly_months=None))
        assert result.status == "skipped"


# ── Dispatch ────────────────────────────────────────────────────────


class TestDispatch:

    def test_locked_tokens_route_via_is_locked_flag(self):
        """June 22 2026 -- dispatcher's lock/live split now
        reads from is_locked, NOT from a hardcoded token name
        set. Same token routes to _validate_locked or to its
        pattern-based strategy depending on the flag."""
        from tools.data_reference_validator import _validate_locked
        # is_locked=True -> locked regardless of name
        for tok in (
                "{{OOS_SHARPE_BLEND}}",
                "{{OOS_WINDOW_MONTHS}}",
                "{{BENCHMARK_MAX_DD}}",
                "{{CLASSIC_6040_MAX_DD}}"):
            assert (dispatch_strategy(tok, is_locked=True)
                    is _validate_locked)

    def test_is_locked_false_routes_by_pattern(self):
        """The same {{<STRATEGY>_MAX_DD}} token that was
        hardcoded as locked in the old _LOCKED_TOKENS set now
        routes to the per-strategy metric validator when the
        catalog marks it is_locked=False (which the per-
        strategy expansion does)."""
        from tools.data_reference_validator import (
            _validate_strategy_metric,
        )
        # is_locked=False -> per-strategy metric strategy
        assert (
            dispatch_strategy(
                "{{CLASSIC_6040_MAX_DD}}", is_locked=False)
            is _validate_strategy_metric)
        assert (
            dispatch_strategy(
                "{{BENCHMARK_MAX_DD}}", is_locked=False)
            is _validate_strategy_metric)

    def test_per_strategy_metric_routes(self):
        assert (dispatch_strategy("{{BENCHMARK_SHARPE}}")
                is _validate_strategy_metric)
        assert (dispatch_strategy("{{REGIME_SWITCHING_CAGR}}")
                is _validate_strategy_metric)

    def test_recovery_suffix_routes_to_strategy_metric(self):
        """Both {{<STRATEGY>_RECOVERY}} and {{<STRATEGY>_
        RECOVERY_MONTHS}} match the per-strategy regex. The
        previous regex only matched _RECOVERY_MONTHS; the
        bare _RECOVERY fell through to passthrough."""
        assert (dispatch_strategy("{{CLASSIC_6040_RECOVERY}}")
                is _validate_strategy_metric)
        assert (
            dispatch_strategy("{{CLASSIC_6040_RECOVERY_MONTHS}}")
            is _validate_strategy_metric)

    def test_regime_conditional_routes(self):
        assert (dispatch_strategy("{{BENCHMARK_POST2022_SHARPE}}")
                is _validate_regime_conditional_sharpe)

    def test_factor_loading_routes(self):
        assert (dispatch_strategy("{{BENCHMARK_ALPHA}}")
                is _validate_factor_loading)

    def test_net_sharpe_routes(self):
        assert (dispatch_strategy("{{NET_SHARPE_15BP}}")
                is _validate_net_sharpe)

    def test_live_signal_routes(self):
        assert (dispatch_strategy("{{VIX_CURRENT}}")
                is _validate_live_signal)

    def test_blend_weight_routes(self):
        assert (dispatch_strategy("{{BLEND_REGIME_SWITCHING_WT}}")
                is _validate_blend_weight)

    def test_dd_reduction_routes_to_derived_strategy(self):
        from tools.data_reference_validator import (
            _validate_dd_reduction,
        )
        assert (
            dispatch_strategy("{{DD_REDUCTION_REGIME_SWITCHING}}")
            is _validate_dd_reduction)

    def test_oos_improvement_routes_to_derived_strategy(self):
        from tools.data_reference_validator import (
            _validate_oos_improvement_pct,
        )
        assert (
            dispatch_strategy("{{OOS_SHARPE_IMPROVEMENT_PCT}}")
            is _validate_oos_improvement_pct)
        assert (
            dispatch_strategy("{{OOS_IMPROVEMENT_PCT}}")
            is _validate_oos_improvement_pct)

    def test_unknown_token_routes_to_passthrough(self):
        """June 22 2026 -- previously this returned a skipped
        lambda for unknown tokens. The dispatcher now returns
        _validate_passthrough which reports pass for populated
        values and fail for em-dash."""
        from tools.data_reference_validator import (
            _validate_passthrough,
        )
        assert (
            dispatch_strategy("{{SOMETHING_NOVEL}}")
            is _validate_passthrough)

    def test_passthrough_pass_on_populated_value(self):
        from tools.data_reference_validator import (
            _validate_passthrough,
        )
        result = _validate_passthrough(
            "{{SOMETHING_NOVEL}}", "novel", "1.23",
            Sources())
        assert result.status == "pass"
        assert "no source-side cross-check" in (result.note or "")

    def test_passthrough_fail_on_em_dash(self):
        from tools.data_reference_validator import (
            _validate_passthrough,
        )
        result = _validate_passthrough(
            "{{SOMETHING_NOVEL}}", "novel", "—",
            Sources())
        assert result.status == "fail"
        assert "em-dash" in (result.delta or "")

    def test_locked_tokens_set_is_gone(self):
        """Revert protection: a future PR can't silently
        restore the hardcoded _LOCKED_TOKENS set (the bug
        this PR fixes)."""
        from tools import data_reference_validator as drv
        assert not hasattr(drv, "_LOCKED_TOKENS"), (
            "_LOCKED_TOKENS hardcoded set was removed; if "
            "you need to re-add lock routing, do it via the "
            "is_locked flag passed to dispatch_strategy "
            "(which reads from the catalog's entry.is_locked)")


# ── Top-level entry point ───────────────────────────────────────────


class TestValidateReferenceSheet:

    def test_walks_categories_and_aggregates(self):
        # Minimal rendered shape -- one of each token type.
        # June 22 2026 -- is_locked + source fields are
        # now consumed by the dispatcher; the locked entry
        # must carry them explicitly.
        rendered = {
            "study_period": {
                "label": "Study period",
                "entries": [
                    {"token": "{{STUDY_MONTHS}}",
                     "label": "Study months",
                     "value": "287",
                     "is_locked": False,
                     "source": "data.study_period.n_months"},
                    {"token": "{{STUDY_START}}",
                     "label": "Study start",
                     "value": "July 2002",
                     "is_locked": True,
                     "source":
                         "academic_deck (constant, July 2002)"},
                ],
            },
            "live": {
                "label": "Live",
                "entries": [
                    {"token": "{{VIX_CURRENT}}",
                     "label": "VIX",
                     "value": "18.42",
                     "is_locked": False,
                     "source": "regime_signals_cache.vix_level"},
                ],
            },
        }
        sources = Sources(
            n_monthly_months=287,
            live_signals={"vix_level": 18.42})
        report = validate_reference_sheet(
            rendered, sources, "hash_X")
        assert report.data_hash == "hash_X"
        assert report.summary["total"] == 3
        assert report.summary["passed"] == 2
        assert report.summary["skipped"] == 1
        # Every result carries cache_freshness key (null for the
        # locked one).
        for r in report.results:
            if r.token == "{{STUDY_START}}":
                assert r.cache_freshness is None
                assert r.status == "skipped"
                # June 22 2026 -- locked entries now carry the
                # structured provenance block.
                assert r.provenance is not None
                assert "lock_date" in r.provenance
                assert "method" in r.provenance

    def test_strategy_exception_becomes_skipped_with_error_note(
            self, monkeypatch):
        """If a strategy raises, the result is skipped with note
        prefixed validator_error: <type>: <msg> and the report
        still completes."""
        from tools import data_reference_validator as drv

        def _boom(*_a, **_k):
            raise RuntimeError("simulated")

        # Force every dispatch to return the boom strategy.
        # June 22 2026 -- dispatch signature is now
        # (token, is_locked=False); the stub accepts both.
        monkeypatch.setattr(
            drv, "dispatch_strategy",
            lambda _t, **_kw: _boom)
        rendered = {
            "x": {
                "label": "x",
                "entries": [
                    {"token": "{{BENCHMARK_SHARPE}}",
                     "label": "label",
                     "value": "0.5"},
                ],
            },
        }
        report = drv.validate_reference_sheet(
            rendered, Sources(), "hash_X")
        assert report.summary["total"] == 1
        assert report.summary["skipped"] == 1
        assert report.results[0].status == "skipped"
        assert (report.results[0].note or "").startswith(
            "validator_error: RuntimeError")

    def test_summary_counts_sum_to_total(self):
        """Invariant: passed + failed + warning + skipped ==
        total. Used as a smoke check both here and in the
        endpoint test."""
        rendered = {
            "live": {
                "label": "live",
                "entries": [
                    {"token": "{{VIX_CURRENT}}",
                     "label": "VIX", "value": "18.42"},
                    {"token": "{{YIELD_CURVE_CURRENT}}",
                     "label": "yield",
                     "value": "0.34"},
                    {"token": "{{SOMETHING_UNKNOWN}}",
                     "label": "??", "value": "x"},
                ],
            },
        }
        sources = Sources(live_signals={
            "vix_level": 18.42, "yield_curve_slope": 0.34})
        report = validate_reference_sheet(
            rendered, sources, "h")
        s = report.summary
        assert (s["passed"] + s["failed"] + s["warning"]
                + s["skipped"]) == s["total"]


# ── Cache freshness field (new requirement) ─────────────────────────


class TestCacheFreshnessField:
    """Every result MUST carry cache_freshness in the response.
    Locked constants and tokens with no source-side timestamp
    surface null; live tokens surface the source row's
    computed_at."""

    def test_locked_constant_freshness_is_null(self):
        result = _validate_locked(
            "{{OOS_SHARPE_BLEND}}", "OOS Sharpe blend",
            "0.86", Sources())
        assert result.cache_freshness is None

    def test_live_token_freshness_is_source_timestamp(self):
        ts = datetime.now(timezone.utc).isoformat()
        sources = Sources(
            academic_analytics={
                "regime_conditional": [
                    {"strategy": "BENCHMARK",
                     "post_2022_sharpe": 0.43,
                     "pre_2022_sharpe": 0.72},
                ],
            },
            academic_analytics_computed_at=ts)
        result = _validate_regime_conditional_sharpe(
            "{{BENCHMARK_POST2022_SHARPE}}",
            "Benchmark post-2022 Sharpe",
            "0.43", sources)
        assert result.cache_freshness == ts

    def test_freshness_serialises_to_dict(self):
        ts = datetime.now(timezone.utc).isoformat()
        sources = Sources(
            live_signals={"vix_level": 18.42},
            academic_analytics_computed_at=ts)
        report = validate_reference_sheet(
            {"live": {"label": "live",
                      "entries": [
                          {"token": "{{VIX_CURRENT}}",
                           "label": "VIX",
                           "value": "18.42"}]}},
            sources, "h")
        d = report.to_dict()
        assert "cache_freshness" in d["results"][0]


# ── Provenance field (June 22 2026) ─────────────────────────────────


class TestLockedProvenance:
    """Locked-constant validation results carry a structured
    provenance block in the ValidationResult. The frontend
    renders it as a tooltip on hover over the lock icon."""

    def test_locked_result_carries_provenance_block(self):
        from tools.data_reference_validator import _validate_locked
        result = _validate_locked(
            "{{OOS_SHARPE_BLEND}}", "OOS Sharpe blend",
            "0.86", Sources(),
            source_string="academic_deck.OOS_SHARPE_REGIME_CONDITIONAL")
        assert result.provenance is not None
        # Required keys per the LOCKED_CONSTANT_PROVENANCE schema.
        for key in (
                "lock_date", "dataset_end", "method",
                "defended", "locked_value"):
            assert key in result.provenance

    def test_locked_result_provenance_is_none_for_unknown_source(
            self):
        """A locked entry whose source string isn't in the
        provenance dict still returns a ValidationResult --
        provenance just lands as None. The frontend gracefully
        handles a None provenance by suppressing the tooltip."""
        from tools.data_reference_validator import _validate_locked
        result = _validate_locked(
            "{{SOMETHING_LOCKED}}", "unknown locked",
            "abc", Sources(),
            source_string="academic_deck.NOT_A_KNOWN_CONSTANT")
        assert result.provenance is None
        assert result.status == "skipped"

    def test_provenance_serialises_to_dict(self):
        from tools.data_reference_validator import _validate_locked
        result = _validate_locked(
            "{{OOS_SHARPE_BLEND}}", "label", "0.86",
            Sources(),
            source_string="academic_deck.OOS_SHARPE_REGIME_CONDITIONAL")
        d = result.to_dict()
        assert "provenance" in d
        assert d["provenance"]["lock_date"] == "submission lock"

    def test_every_locked_catalog_entry_has_provenance(self):
        """Coverage map: every is_locked=True entry in the
        CATALOG must have a corresponding entry in
        LOCKED_CONSTANT_PROVENANCE. A gap means the frontend
        would render the lock icon with no tooltip on that
        token -- worth surfacing as a test failure rather than
        a runtime surprise."""
        from tools.data_reference_catalog import (
            CATALOG, LOCKED_CONSTANT_PROVENANCE,
            expand_per_strategy_appendix_metrics,
            expand_per_strategy_factor_loadings,
        )
        all_locked_sources: set[str] = set()
        for _k, _label, entries in CATALOG:
            for e in entries:
                if e.is_locked:
                    all_locked_sources.add(e.source)
        # Per-strategy expansions are is_locked=False, so they
        # don't need provenance entries; not added to the set.
        _ = expand_per_strategy_appendix_metrics()
        _ = expand_per_strategy_factor_loadings()
        # Every locked source must have a provenance entry.
        missing = all_locked_sources - set(
            LOCKED_CONSTANT_PROVENANCE.keys())
        assert not missing, (
            f"Locked catalog entries with no provenance: "
            f"{sorted(missing)}")

    def test_derived_provenance_names_input_constants(self):
        """User-specified format: derived locked tokens'
        method field must name BOTH input constants and the
        closed-form computation so the derivation chain is
        fully transparent for the panel."""
        from tools.data_reference_catalog import (
            LOCKED_CONSTANT_PROVENANCE,
        )
        # OOS improvement: blend/benchmark - 1
        oos_imp = LOCKED_CONSTANT_PROVENANCE[
            "derived: blend/benchmark - 1"]
        assert "OOS_SHARPE_REGIME_CONDITIONAL" in oos_imp["method"]
        assert "OOS_SHARPE_BENCHMARK" in oos_imp["method"]
        # June 29 2026 (Issue 7) -- updated to rf-adjusted live
        # OOS Sharpe figures (0.9117 / 0.4927) sourced from the
        # corrected oos_summary cache; the retired Dec 2025 lock
        # values (0.8576 / 0.4341) could not be reproduced from
        # any documented computation.
        assert "0.9117" in oos_imp["method"]
        assert "0.4927" in oos_imp["method"]
        # DD reduction: |bench_dd| - |blend_dd|
        dd_red = LOCKED_CONSTANT_PROVENANCE[
            "derived: |bench_dd| - |blend_dd|"]
        assert "MAX_DRAWDOWN_BENCHMARK" in dd_red["method"]
        assert "MAX_DRAWDOWN_REGIME_CONDITIONAL" in dd_red["method"]


# ── Derived-token strategies (June 22 2026) ─────────────────────────


class TestDerivedTokenStrategies:
    """Two derived tokens get explicit strategies that
    recompute from the locked constants and compare against
    the reference value. Anything else still routes through
    _validate_passthrough."""

    def test_dd_reduction_pass_when_reference_matches(self):
        from tools.data_reference_validator import (
            _validate_dd_reduction,
        )
        # |52.6| - |29.7| = 22.9pp; reference renders as "+22.9%"
        result = _validate_dd_reduction(
            "{{DD_REDUCTION_REGIME_SWITCHING}}",
            "DD reduction", "22.9%", Sources())
        assert result.status == "pass"
        assert "derived" in (result.source_endpoint or "").lower()

    def test_dd_reduction_fail_on_drift(self):
        from tools.data_reference_validator import (
            _validate_dd_reduction,
        )
        result = _validate_dd_reduction(
            "{{DD_REDUCTION_REGIME_SWITCHING}}",
            "DD reduction", "10.0%", Sources())
        assert result.status == "fail"
        assert result.delta is not None

    def test_oos_improvement_pass_when_reference_matches(self):
        from tools.data_reference_validator import (
            _validate_oos_improvement_pct,
        )
        # June 29 2026 (Issue 7) -- 0.9117 / 0.4927 - 1 = 0.85024
        # = ~85.0% (rf-adjusted live OOS, replacing the retired
        # 0.8576 / 0.4341 lock).
        result = _validate_oos_improvement_pct(
            "{{OOS_SHARPE_IMPROVEMENT_PCT}}",
            "OOS improvement", "85.0%", Sources())
        assert result.status == "pass"

    def test_oos_improvement_fail_on_drift(self):
        from tools.data_reference_validator import (
            _validate_oos_improvement_pct,
        )
        result = _validate_oos_improvement_pct(
            "{{OOS_SHARPE_IMPROVEMENT_PCT}}",
            "OOS improvement", "50.0%", Sources())
        assert result.status == "fail"
