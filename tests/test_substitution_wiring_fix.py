"""tests/test_substitution_wiring_fix.py -- June 22 2026.

Pins the four wiring fixes in build_substitution_table():

  Gap 1 -- pre/post 2022 sub-period Sharpes resolve from the
           `regime_conditional` kwarg (the academic_analytics
           payload's regime_conditional list), NOT from
           strategy_cache[name].post_2022_sharpe.
  Gap 2 -- per-strategy Carhart factor-loading tokens emit
           when factor_loadings list is supplied to
           get_substitution_table / _append_per_strategy_tokens.
  Gap 3 -- net Sharpe @ 10/15/20 bps tokens resolve from the
           `cost_sensitivity` kwarg's scenarios list, NOT
           from strategy_cache.get("net_sharpe_*bp") which
           never carried those fields. Turnover resolves
           from the regime_conditional row's
           annualized_turnover field.
  Gap 4 -- already wired in PR #370 + #371; not retested here.

  Plus an integration test of
  tools.academic_export.load_substitution_metric_sources -- the
  helper the brief / appendix / deck / data-reference-sheet
  callsites use to fetch the three metric payloads in one shot.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")

import pytest


@pytest.fixture(autouse=True)
def _clear_substitution_cache():
    """get_substitution_table uses a process-wide cache keyed by
    data_hash. Tests in this module reuse the "test_hash" key, so
    a previous test's table would survive into the next test and
    break the negative-case ("token NOT in table") assertions.
    Clear before and after every test."""
    from tools.numeric_substitution import clear_substitution_cache
    clear_substitution_cache()
    yield
    clear_substitution_cache()


# ── Signature + helper unit tests ────────────────────────────────────────


class TestBuildSubstitutionTableSignature:

    def test_three_new_kwargs_present(self):
        import inspect
        from tools.numeric_substitution import build_substitution_table
        sig = inspect.signature(build_substitution_table)
        for kwarg in (
            "regime_conditional",
            "factor_loadings",
            "cost_sensitivity",
        ):
            assert kwarg in sig.parameters, (
                f"build_substitution_table missing kwarg: {kwarg}")

    def test_index_by_strategy_helper(self):
        from tools.numeric_substitution import _index_by_strategy
        rows = [
            {"strategy": "BENCHMARK", "post_2022_sharpe": 0.50},
            {"strategy": "REGIME_SWITCHING", "post_2022_sharpe": 0.29},
            "not a dict",  # ignored
            {"strategy": None},  # ignored
        ]
        out = _index_by_strategy(rows)
        assert out["BENCHMARK"]["post_2022_sharpe"] == 0.50
        assert out["REGIME_SWITCHING"]["post_2022_sharpe"] == 0.29
        # Bad rows excluded.
        assert len(out) == 2

    def test_index_by_strategy_accepts_strategy_name_alias(self):
        """The regime_conditional rows from analytics carry the
        name in `strategy`; some other analytics outputs use
        `strategy_name`. The helper accepts both."""
        from tools.numeric_substitution import _index_by_strategy
        out = _index_by_strategy([
            {"strategy_name": "BENCHMARK", "post_2022_sharpe": 0.50},
        ])
        assert out["BENCHMARK"]["post_2022_sharpe"] == 0.50

    def test_cost_scenario_lookup(self):
        from tools.numeric_substitution import _cost_scenario
        payload = {
            "scenarios": [
                {"bps": 10, "net_sharpe": 0.85},
                {"bps": 15, "net_sharpe": 0.82},
                {"bps": 20, "net_sharpe": 0.79},
            ],
        }
        assert _cost_scenario(payload, 10)["net_sharpe"] == 0.85
        assert _cost_scenario(payload, 15)["net_sharpe"] == 0.82
        assert _cost_scenario(payload, 20)["net_sharpe"] == 0.79
        # Missing bps falls back to {}.
        assert _cost_scenario(payload, 30) == {}
        # None / wrong shape stays safe.
        assert _cost_scenario(None, 10) == {}
        assert _cost_scenario({}, 10) == {}


# ── Gap 1 -- pre/post 2022 Sharpes resolve from kwarg ────────────────────


class TestGap1PrePost2022Sharpe:

    def test_post2022_resolves_from_regime_conditional_kwarg(self):
        from tools.numeric_substitution import build_substitution_table
        rc_rows = [
            {"strategy": "REGIME_SWITCHING", "post_2022_sharpe": 0.29,
             "pre_2022_sharpe": 0.95},
            {"strategy": "BENCHMARK", "post_2022_sharpe": 0.50,
             "pre_2022_sharpe": 0.55},
            {"strategy": "CLASSIC_60_40", "post_2022_sharpe": 0.22},
        ]
        table = build_substitution_table(
            strategy_cache={}, cio_recommendation={},
            regime_conditional=rc_rows)
        assert table["{{REGIME_SWITCHING_POST2022_SHARPE}}"] == "0.29"
        assert table["{{BENCHMARK_POST2022_SHARPE}}"] == "0.50"
        assert table["{{CLASSIC_6040_POST2022_SHARPE}}"] == "0.22"
        assert table["{{REGIME_SWITCHING_PRE2022_SHARPE}}"] == "0.95"
        assert table["{{BENCHMARK_PRE2022_SHARPE}}"] == "0.55"

    def test_post2022_em_dash_when_kwarg_missing(self):
        """Without regime_conditional, the tokens resolve to
        em-dash (NOT from strategy_cache, which was the broken
        path before this fix)."""
        from tools.numeric_substitution import build_substitution_table
        # strategy_cache deliberately carries fake post_2022 fields
        # to prove the new resolver ignores them.
        cache = {
            "BENCHMARK": {
                "sharpe_ratio": 0.5,
                "post_2022_sharpe": 999.0,  # ignored
            },
        }
        table = build_substitution_table(
            strategy_cache=cache, cio_recommendation={},
            regime_conditional=None)
        assert table["{{BENCHMARK_POST2022_SHARPE}}"] == "—"

    def test_ignores_strategy_cache_post2022_field(self):
        """The CORE wiring contract: regime_conditional kwarg is
        the AUTHORITATIVE source. If a strategy_cache entry
        happens to carry post_2022_sharpe (e.g. from a stale
        merge in gather_document_data), the substitution table
        must NOT read from it."""
        from tools.numeric_substitution import build_substitution_table
        cache = {
            "BENCHMARK": {"sharpe_ratio": 0.5,
                          "post_2022_sharpe": 999.0},
        }
        rc_rows = [
            {"strategy": "BENCHMARK", "post_2022_sharpe": 0.50},
        ]
        table = build_substitution_table(
            strategy_cache=cache, cio_recommendation={},
            regime_conditional=rc_rows)
        # Reads from rc_rows (0.50), not strategy_cache (999).
        assert table["{{BENCHMARK_POST2022_SHARPE}}"] == "0.50"


# ── Gap 2 -- factor loadings tokens emit when kwarg supplied ─────────────


class TestGap2FactorLoadings:

    def test_per_strategy_factor_tokens_emitted(self):
        """Supplying factor_loadings via get_substitution_table
        emits per-strategy ALPHA / BETA / SMB_BETA / HML_BETA /
        R_SQUARED tokens. The appendix Section E table cites
        these directly."""
        from tools.numeric_substitution import get_substitution_table
        fl_rows = [
            {
                "strategy": "REGIME_SWITCHING",
                "alpha": 0.0045, "beta": 0.6669,
                "smb_beta": 0.1234, "hml_beta": -0.0567,
                "r_squared": 0.9328,
            },
            {
                "strategy": "BENCHMARK",
                "alpha": 0.0, "beta": 1.0,
                "smb_beta": 0.0, "hml_beta": 0.0,
                "r_squared": 1.0,
            },
        ]
        # Need strategy_cache with sharpe_ratio so
        # _append_per_strategy_tokens iterates the strategies.
        cache = {
            "REGIME_SWITCHING": {"sharpe_ratio": 0.63},
            "BENCHMARK": {"sharpe_ratio": 0.54},
        }
        table = get_substitution_table(
            "test_hash", cache, {},
            factor_loadings=fl_rows)
        # 4dp formatting on factor metrics.
        assert table["{{REGIME_SWITCHING_ALPHA}}"] == "0.0045"
        assert table["{{REGIME_SWITCHING_BETA}}"] == "0.6669"
        assert table["{{REGIME_SWITCHING_SMB_BETA}}"] == "0.1234"
        assert table["{{REGIME_SWITCHING_HML_BETA}}"] == "-0.0567"
        assert table["{{REGIME_SWITCHING_R_SQUARED}}"] == "0.9328"
        assert table["{{BENCHMARK_ALPHA}}"] == "0.0000"
        assert table["{{BENCHMARK_BETA}}"] == "1.0000"
        assert table["{{BENCHMARK_R_SQUARED}}"] == "1.0000"

    def test_no_factor_tokens_when_kwarg_omitted(self):
        """Without factor_loadings, the per-strategy factor
        tokens are NOT in the table. This preserves the
        previous behaviour for callers that don't yet pass
        the kwarg."""
        from tools.numeric_substitution import get_substitution_table
        cache = {
            "REGIME_SWITCHING": {"sharpe_ratio": 0.63},
        }
        table = get_substitution_table("test_hash", cache, {})
        # Per-strategy non-factor tokens are still there.
        assert "{{REGIME_SWITCHING_SHARPE}}" in table
        # But factor tokens are NOT.
        assert "{{REGIME_SWITCHING_ALPHA}}" not in table
        assert "{{REGIME_SWITCHING_BETA}}" not in table


# ── Gap 3 -- net Sharpe + turnover from cost_sensitivity / rc ────────────


class TestGap3CostSensitivity:

    def test_net_sharpe_tokens_resolve_from_cost_sensitivity(self):
        from tools.numeric_substitution import build_substitution_table
        cs_payload = {
            "n_rebalances": 30,
            "gross_sharpe": 0.91,
            "scenarios": [
                {"bps": 10, "net_sharpe": 0.85},
                {"bps": 15, "net_sharpe": 0.82},
                {"bps": 20, "net_sharpe": 0.79},
            ],
        }
        table = build_substitution_table(
            strategy_cache={}, cio_recommendation={},
            cost_sensitivity=cs_payload)
        assert table["{{NET_SHARPE_10BP}}"] == "0.85"
        assert table["{{NET_SHARPE_15BP}}"] == "0.82"
        assert table["{{NET_SHARPE_20BP}}"] == "0.79"

    def test_net_sharpe_em_dash_when_cost_sensitivity_missing(self):
        """Without the kwarg, the tokens degrade gracefully."""
        from tools.numeric_substitution import build_substitution_table
        table = build_substitution_table(
            strategy_cache={}, cio_recommendation={},
            cost_sensitivity=None)
        assert table["{{NET_SHARPE_10BP}}"] == "—"
        assert table["{{NET_SHARPE_15BP}}"] == "—"
        assert table["{{NET_SHARPE_20BP}}"] == "—"

    def test_turnover_token_resolves_from_regime_conditional(self):
        """Annualized turnover for REGIME_SWITCHING lives on
        the regime_conditional row, NOT on the strategy_cache.
        The token reads from rc_by_strategy now."""
        from tools.numeric_substitution import build_substitution_table
        rc_rows = [
            {"strategy": "REGIME_SWITCHING",
             "annualized_turnover": 0.50},
        ]
        table = build_substitution_table(
            strategy_cache={}, cio_recommendation={},
            regime_conditional=rc_rows)
        # 1dp percent format.
        assert table["{{REGIME_SWITCHING_TURNOVER}}"] == "50.0%"

    def test_turnover_em_dash_when_regime_conditional_missing(self):
        from tools.numeric_substitution import build_substitution_table
        table = build_substitution_table(
            strategy_cache={}, cio_recommendation={},
            regime_conditional=None)
        assert table["{{REGIME_SWITCHING_TURNOVER}}"] == "—"


# ── Integration: load_substitution_metric_sources helper ─────────────────


class TestLoadSubstitutionMetricSources:

    def test_returns_three_element_tuple(self):
        """ENVIRONMENT=test short-circuits the DB; the helper
        must still return the three-element tuple shape rather
        than raising."""
        import asyncio
        from tools.academic_export import (
            load_substitution_metric_sources,
        )
        result = asyncio.run(load_substitution_metric_sources())
        assert isinstance(result, tuple)
        assert len(result) == 3
        rc, fl, cs = result
        # In test env without a warm DB, all three default to
        # empty values.
        assert isinstance(rc, list)
        assert isinstance(fl, list)
        # cost_sensitivity can be None or dict.
        assert cs is None or isinstance(cs, dict)

    def test_handles_get_latest_metric_failure(self, monkeypatch):
        """Any exception in get_latest_metric should be swallowed
        and the helper should still return the empty-default
        tuple."""
        import asyncio
        from tools import academic_export

        async def _broken(*_a, **_k):
            raise RuntimeError("simulated cache failure")

        monkeypatch.setattr(
            "tools.precomputed_analytics.get_latest_metric",
            _broken)
        rc, fl, cs = asyncio.run(
            academic_export.load_substitution_metric_sources())
        assert rc == []
        assert fl == []
        assert cs is None
