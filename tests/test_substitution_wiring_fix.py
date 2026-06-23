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
        these directly.

        Field names in the input rows match what
        tools.analytics.factor_loadings actually writes
        (alpha_annualized / mkt_rf / smb / hml / r_squared --
        the raw statsmodels OLS param names with _annualized
        applied to alpha). The token resolver was previously
        reading the conceptual names (alpha / beta / smb_beta /
        hml_beta) which never matched real analytics output."""
        from tools.numeric_substitution import get_substitution_table
        fl_rows = [
            {
                "strategy": "REGIME_SWITCHING",
                "alpha_annualized": 0.0045, "mkt_rf": 0.6669,
                "smb": 0.1234, "hml": -0.0567,
                "r_squared": 0.9328,
            },
            {
                "strategy": "BENCHMARK",
                "alpha_annualized": 0.0, "mkt_rf": 1.0,
                "smb": 0.0, "hml": 0.0,
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

    def test_turnover_token_resolves_from_strategy_cache(self):
        """June 22 2026 -- annualized turnover for REGIME_SWITCHING
        lives on the strategy_cache row's true_turnover field (the
        backtester's _true_turnover -- "Genuine annualised
        portfolio turnover"). The previous source
        regime_conditional.annualized_turnover pointed at a field
        analytics.regime_conditional_performance never writes, so
        the token rendered em-dash."""
        from tools.numeric_substitution import build_substitution_table
        strategy_cache = {
            "REGIME_SWITCHING": {
                "strategy_name": "REGIME_SWITCHING",
                "true_turnover": 0.50,
            },
        }
        table = build_substitution_table(
            strategy_cache=strategy_cache, cio_recommendation={})
        # 1dp percent format.
        assert table["{{REGIME_SWITCHING_TURNOVER}}"] == "50.0%"

    def test_turnover_em_dash_when_strategy_cache_missing_turnover(
            self):
        from tools.numeric_substitution import build_substitution_table
        table = build_substitution_table(
            strategy_cache={"REGIME_SWITCHING": {}},
            cio_recommendation={})
        assert table["{{REGIME_SWITCHING_TURNOVER}}"] == "—"

    def test_turnover_em_dash_when_regime_switching_absent(self):
        from tools.numeric_substitution import build_substitution_table
        table = build_substitution_table(
            strategy_cache={},
            cio_recommendation={})
        assert table["{{REGIME_SWITCHING_TURNOVER}}"] == "—"


class TestCvar99BenchmarkRemoved:
    """June 22 2026 -- {{CVAR_99_BENCHMARK}} was advertised in
    the deck placeholder guide but cited by zero slide specs;
    the resolver pointed at a field the strategy_cache never
    carries. Removed entirely from catalog + placeholder guide
    + resolver. These tests pin the removal so a future PR can't
    silently restore the broken wiring."""

    def test_token_not_in_substitution_table(self):
        from tools.numeric_substitution import build_substitution_table
        table = build_substitution_table(
            strategy_cache={"BENCHMARK": {}},
            cio_recommendation={})
        assert "{{CVAR_99_BENCHMARK}}" not in table

    def test_token_not_in_catalog(self):
        from tools.data_reference_catalog import CATALOG
        flat = {
            e.token for _, _, entries in CATALOG for e in entries
        }
        assert "{{CVAR_99_BENCHMARK}}" not in flat

    def test_tail_risk_category_removed_from_catalog(self):
        """The catalog's tail_risk category previously held only
        the CVAR token. Removing the entry left an empty
        category, which the catalog-walker would skip but
        looks like a structural defect -- the category itself
        should be gone."""
        from tools.data_reference_catalog import CATALOG
        categories = {k for k, _, _ in CATALOG}
        assert "tail_risk" not in categories

    def test_token_not_in_deck_placeholder_guide(self):
        """The deck per-slide writer reads
        _DECK_NUMERIC_PLACEHOLDER_GUIDE_EXTENSION; the token
        must not be advertised to it."""
        import inspect
        import main
        src = inspect.getsource(main)
        # The guide constant must not list CVAR_99_BENCHMARK as
        # one of the available tokens. We use a more specific
        # match than "in src" to avoid matching the comment
        # block explaining the removal.
        lines = src.split("\n")
        offending = [
            line for line in lines
            if "{{CVAR_99_BENCHMARK}}" in line
            and "removed" not in line.lower()
            and not line.strip().startswith("#")
        ]
        assert offending == [], (
            "CVAR_99_BENCHMARK still advertised in a non-comment "
            f"line: {offending}")


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


# ── Cache-key bool fingerprint (the actual root-cause fix) ──────────────


class TestGetSubstitutionTableCacheKey:
    """Pre-fix, _substitution_cache was keyed on data_hash alone.
    A pre-PR-#374 caller that hit get_substitution_table BEFORE
    the new rc/fl/cs kwargs became available would cache a
    token-less table. Subsequent callers passing the new kwargs
    would hit that stale entry and get the old table back -- the
    new kwargs were silently ignored, and tokens like
    {{BENCHMARK_POST2022_SHARPE}}, {{BENCHMARK_ALPHA}}, and
    {{NET_SHARPE_15BP}} stayed unresolved on the documents.

    The fix extends the cache key to
    (_CACHE_VERSION, data_hash,
     bool(regime_conditional), bool(factor_loadings),
     bool(cost_sensitivity))
    so a call with the new kwargs MISSES any entry built without
    them and rebuilds fresh."""

    def test_call_with_new_kwargs_rebuilds_after_call_without(self):
        """The canonical regression: pre-fix this test would have
        failed because the second call would return the cached
        table from the first call.

        The token actually checked here is the per-strategy
        BENCHMARK_POST2022_SHARPE emitted by
        _append_per_strategy_tokens from the regime_conditional
        rows. When the kwarg is absent the per-strategy helper
        gets factor_loadings=None and emits an em-dash; when
        present it emits the formatted Sharpe."""
        from tools.numeric_substitution import (
            clear_substitution_cache, get_substitution_table,
        )
        clear_substitution_cache()
        # 1. First call WITHOUT the new kwargs -- caches an entry
        # whose BENCHMARK_POST2022_SHARPE is the em-dash fallback.
        table_no_kwargs = get_substitution_table(
            "hash-X", {"BENCHMARK": {}}, None,
            oos_sharpe_blend=0.86)
        assert table_no_kwargs.get(
            "{{BENCHMARK_POST2022_SHARPE}}") == "—"
        # 2. Second call WITH the new kwargs at SAME data_hash.
        # Pre-fix: cache hit, returns the em-dash table.
        # Post-fix: cache miss (bool fingerprint differs), rebuilds.
        table_with_kwargs = get_substitution_table(
            "hash-X", {
                "BENCHMARK": {
                    "strategy_name": "BENCHMARK",
                    "monthly_returns": [
                        ["2022-01-31", 0.01], ["2022-02-28", 0.02]],
                },
            }, None,
            oos_sharpe_blend=0.86,
            regime_conditional=[{
                "strategy": "BENCHMARK",
                "post_2022_sharpe": 0.43,
                "pre_2022_sharpe": 0.72,
            }])
        assert table_with_kwargs.get(
            "{{BENCHMARK_POST2022_SHARPE}}") == "0.43"
        assert table_with_kwargs is not table_no_kwargs

    def test_factor_loadings_kwarg_invalidates_independently(self):
        """factor_loadings has its own slot in the cache key
        fingerprint, so a call WITH factor_loadings must rebuild
        even if regime_conditional is unchanged.

        The token actually checked here is BENCHMARK_ALPHA, one
        of the Carhart-loading tokens
        _append_per_strategy_tokens emits when factor_loadings
        is supplied."""
        from tools.numeric_substitution import (
            clear_substitution_cache, get_substitution_table,
        )
        clear_substitution_cache()
        strategy_cache = {
            "BENCHMARK": {
                "strategy_name": "BENCHMARK",
                "monthly_returns": [
                    ["2022-01-31", 0.01], ["2022-02-28", 0.02]],
            },
        }
        table_a = get_substitution_table(
            "hash-Y", strategy_cache, None,
            regime_conditional=[{
                "strategy": "BENCHMARK",
                "post_2022_sharpe": 0.43}])
        # Without factor_loadings kwarg, the Carhart tokens are
        # absent from the table entirely -- a pre-fix cache hit
        # on the second call would inherit the same missing-token
        # state.
        assert "{{BENCHMARK_ALPHA}}" not in table_a
        table_b = get_substitution_table(
            "hash-Y", strategy_cache, None,
            regime_conditional=[{
                "strategy": "BENCHMARK",
                "post_2022_sharpe": 0.43}],
            factor_loadings=[{
                "strategy": "BENCHMARK",
                "alpha_annualized": 0.012,
                "mkt_rf": 0.55,
                "smb": 0.10,
                "hml": -0.03,
                "r_squared": 0.94}])
        assert "{{BENCHMARK_ALPHA}}" in table_b
        assert table_b is not table_a

    def test_cost_sensitivity_kwarg_invalidates_independently(self):
        """cost_sensitivity has its own slot in the cache key
        fingerprint. The token NET_SHARPE_15BP comes from the
        cost_sensitivity scenarios payload."""
        from tools.numeric_substitution import (
            clear_substitution_cache, get_substitution_table,
        )
        clear_substitution_cache()
        table_a = get_substitution_table(
            "hash-Z", {"BENCHMARK": {}}, None)
        assert table_a.get("{{NET_SHARPE_15BP}}") == "—"
        table_b = get_substitution_table(
            "hash-Z", {"BENCHMARK": {}}, None,
            cost_sensitivity={
                "scenarios": [
                    {"bps": 10, "net_sharpe": 0.84,
                     "vs_benchmark_pct": 0.95},
                    {"bps": 15, "net_sharpe": 0.82,
                     "vs_benchmark_pct": 0.91},
                    {"bps": 20, "net_sharpe": 0.80,
                     "vs_benchmark_pct": 0.86},
                ],
            })
        assert table_b.get("{{NET_SHARPE_15BP}}") == "0.82"
        assert table_b is not table_a

    def test_same_kwargs_shape_still_hits_cache(self):
        """The fix must not break the cache's reason-for-being.
        Two calls with the SAME data_hash and the SAME kwarg
        presence pattern should return the same dict instance --
        that's how cross_deliverable_consistency_check sees
        byte-identical tokens across brief / appendix / deck."""
        from tools.numeric_substitution import (
            clear_substitution_cache, get_substitution_table,
        )
        clear_substitution_cache()
        rc_rows = [{
            "strategy": "BENCHMARK", "post_2022_sharpe": 0.43}]
        table_1 = get_substitution_table(
            "hash-W", {"BENCHMARK": {}}, None,
            regime_conditional=rc_rows)
        table_2 = get_substitution_table(
            "hash-W", {"BENCHMARK": {}}, None,
            regime_conditional=rc_rows)
        assert table_1 is table_2

    def test_cache_version_constant_is_exported(self):
        """_CACHE_VERSION should be module-level so a follow-up PR
        bumping it is a single-line diff. Pin it >= 2 so a future
        revert doesn't silently re-introduce the bug."""
        from tools import numeric_substitution
        assert hasattr(numeric_substitution, "_CACHE_VERSION")
        assert numeric_substitution._CACHE_VERSION >= 2

    def test_factor_loadings_field_names_match_analytics_output(self):
        """Pre-fix, _append_per_strategy_tokens read row.get('alpha'),
        row.get('beta'), row.get('smb_beta'), row.get('hml_beta').
        But analytics.factor_loadings writes the raw statsmodels OLS
        param names plus _annualized for alpha:
            alpha_annualized, mkt_rf, smb, hml, r_squared
        Only r_squared matched -- the other four tokens rendered as
        em-dash on every document even after the cache-key fix.

        This test pins the field-name contract against the actual
        analytics output shape so a future rename in either direction
        breaks this test rather than silently rendering em-dashes."""
        from tools.numeric_substitution import (
            clear_substitution_cache, get_substitution_table,
        )
        clear_substitution_cache()
        # Use the exact shape that tools.analytics.factor_loadings
        # emits (see backend/tools/analytics.py:697-728).
        strategy_cache = {
            "BENCHMARK": {
                "strategy_name": "BENCHMARK",
                "sharpe_ratio": 0.5,
            },
        }
        table = get_substitution_table(
            "h", strategy_cache, None,
            factor_loadings=[{
                "strategy": "BENCHMARK",
                "model": "carhart_4factor",
                "alpha_annualized": 0.0123,
                "mkt_rf": 0.5500,
                "smb": 0.1000,
                "hml": -0.0300,
                "r_squared": 0.9400,
                "n_months": 287,
            }])
        # All five tokens should resolve to formatted 4dp decimals,
        # NOT em-dash. Pre-fix the first four would have been em-dash.
        assert table["{{BENCHMARK_ALPHA}}"] == "0.0123"
        assert table["{{BENCHMARK_BETA}}"] == "0.5500"
        assert table["{{BENCHMARK_SMB_BETA}}"] == "0.1000"
        assert table["{{BENCHMARK_HML_BETA}}"] == "-0.0300"
        assert table["{{BENCHMARK_R_SQUARED}}"] == "0.9400"

    def test_cache_key_helper_includes_version_and_fingerprint(self):
        """_cache_key returns a tuple whose first element is the
        version constant and whose tail is the three bools."""
        from tools.numeric_substitution import _cache_key
        key_empty = _cache_key("hash-A", {})
        assert key_empty == (2, "hash-A", False, False, False)
        key_all = _cache_key("hash-A", {
            "regime_conditional": [{"strategy": "BENCHMARK"}],
            "factor_loadings": [{"strategy": "BENCHMARK"}],
            "cost_sensitivity": {"scenarios": [{"bps": 10}]},
        })
        assert key_all == (2, "hash-A", True, True, True)
        # An empty list / empty dict counts as falsy -- that's
        # intentional, the data wasn't really there.
        key_empty_lists = _cache_key("hash-A", {
            "regime_conditional": [],
            "factor_loadings": [],
            "cost_sensitivity": {},
        })
        assert key_empty_lists == (2, "hash-A", False, False, False)
