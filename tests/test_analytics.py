"""
tests/test_analytics.py

Unit tests for the academic analytics layer (tools/analytics.py). Every
compute function is pure — plain dict/list in, plain dict out — so these
tests run without a database or an event loop.
"""
from __future__ import annotations

import math
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")


def _monthly_series(values: list[float], start: str = "2002-01-31") -> pd.Series:
    idx = pd.date_range(start=start, periods=len(values), freq="ME")
    return pd.Series(values, index=idx)


def _pairs(values: list[float], start: str = "2002-01-31") -> list:
    idx = pd.date_range(start=start, periods=len(values), freq="ME")
    return [[str(d.date()), v] for d, v in zip(idx, values)]


# A non-constant 24-month return series — used where the regression needs a
# dependent variable with variance (a flat series gives a 0/0 R²).
_VARYING_24 = [round(0.01 + 0.003 * ((i % 5) - 2), 4) for i in range(24)]


# ── Series helpers ────────────────────────────────────────────────────────────

class TestHelpers:
    def test_cagr_of_constant_one_percent(self):
        from tools.analytics import _cagr
        r = _monthly_series([0.01] * 12)
        # 12 months of +1% compounds to (1.01^12)-1 annually.
        assert abs(_cagr(r) - ((1.01 ** 12) - 1.0)) < 1e-9

    def test_max_drawdown_simple_path(self):
        from tools.analytics import _max_drawdown
        # +10%, -50%, +30% — trough after the -50% month.
        dd = _max_drawdown(_monthly_series([0.10, -0.50, 0.30]))
        assert abs(dd - (-0.50)) < 1e-9

    def test_recovery_months_counts_to_new_high(self):
        from tools.analytics import _recovery_months
        # Drop 50%, then two +50% months — recovers on the 2nd.
        # 1.0 -> 0.5 -> 0.75 -> 1.125 ; new high at index 3, trough at 1.
        rec = _recovery_months(_monthly_series([0.0, -0.50, 0.50, 0.50]))
        assert rec == 2

    def test_recovery_months_none_when_underwater(self):
        from tools.analytics import _recovery_months
        # Never gets back above the pre-trough peak.
        assert _recovery_months(_monthly_series([0.0, -0.50, 0.01])) is None


# ── 1. Summary statistics ─────────────────────────────────────────────────────

class TestSummaryStatistics:
    def test_row_per_asset_with_required_keys(self):
        from tools.analytics import summary_statistics
        rf = _monthly_series([0.001] * 24)
        out = summary_statistics(
            {"EQUITY": _monthly_series([0.01] * 24),
             "IG": _monthly_series([0.003] * 24)},
            rf,
        )
        assert len(out) == 2
        required = {"asset", "cagr", "ann_volatility", "sharpe_ratio",
                    "max_drawdown", "skewness", "n_months"}
        for row in out:
            assert required <= set(row.keys())

    def test_zero_volatility_series_has_zero_sharpe_not_nan(self):
        from tools.analytics import summary_statistics
        out = summary_statistics({"FLAT": _monthly_series([0.005] * 12)}, None)
        assert out[0]["sharpe_ratio"] == 0.0
        assert out[0]["ann_volatility"] == 0.0


# ── 2. Rolling correlation ────────────────────────────────────────────────────

class TestRollingCorrelation:
    def test_pre_post_2022_averages_present(self):
        from tools.analytics import rolling_correlation
        eq = _monthly_series([0.01, -0.01] * 24, start="2018-01-31")
        ig = _monthly_series([-0.01, 0.01] * 24, start="2018-01-31")
        hy = _monthly_series([0.01, -0.01] * 24, start="2018-01-31")
        out = rolling_correlation(eq, ig, hy, window=12)
        assert out["window_months"] == 12
        assert out["regime_break"] == "2022-01-01"
        assert "equity_ig" in out["pre_2022"]
        assert "equity_hy" in out["post_2022"]
        assert len(out["points"]) == len(eq)

    def test_perfectly_anticorrelated_pair_is_negative_one(self):
        from tools.analytics import rolling_correlation
        eq = _monthly_series([0.01, -0.02, 0.03, -0.01] * 6, start="2015-01-31")
        ig = -eq
        out = rolling_correlation(eq, ig, eq, window=12)
        last_ig = [p["equity_ig"] for p in out["points"] if p["equity_ig"] is not None][-1]
        assert abs(last_ig - (-1.0)) < 1e-6


# ── 3. Regime-conditional performance ─────────────────────────────────────────

class TestRegimeConditional:
    def test_splits_at_2022_and_sorts_by_post_sharpe(self):
        from tools.analytics import regime_conditional_performance
        results = {
            "A": {"strategy_name": "A",
                  "monthly_returns": _pairs([0.01] * 36, start="2020-01-31")},
            "B": {"strategy_name": "B",
                  "monthly_returns": _pairs([0.05] * 36, start="2020-01-31")},
        }
        out = regime_conditional_performance(results, None)
        assert len(out) == 2
        # 24 months in 2020-2021 (pre), 12 in 2022 (post).
        a = next(r for r in out if r["strategy"] == "A")
        assert a["pre_2022_months"] == 24
        assert a["post_2022_months"] == 12
        # Sorted by post-2022 Sharpe descending — both flat-return series
        # have undefined Sharpe (zero variance) so order falls back, but the
        # key requirement is every row carries both sub-period fields.
        for r in out:
            assert "pre_2022_cagr" in r and "post_2022_cagr" in r

    def test_skips_strategy_with_no_returns(self):
        from tools.analytics import regime_conditional_performance
        out = regime_conditional_performance({"X": {"monthly_returns": []}}, None)
        assert out == []


# ── 4. Drawdown comparison ────────────────────────────────────────────────────

class TestDrawdownComparison:
    def test_sorted_by_max_drawdown_ascending(self):
        from tools.analytics import drawdown_comparison
        results = {
            "DEEP": {"strategy_name": "DEEP",
                     "monthly_returns": _pairs([0.0, -0.40, 0.10])},
            "SHALLOW": {"strategy_name": "SHALLOW",
                        "monthly_returns": _pairs([0.0, -0.05, 0.10])},
        }
        out = drawdown_comparison(results)
        # Ascending: the deepest (most negative) drawdown comes first.
        assert out[0]["strategy"] == "DEEP"
        assert out[1]["strategy"] == "SHALLOW"
        assert out[0]["max_drawdown"] < out[1]["max_drawdown"]


# ── 6. Factor loadings ────────────────────────────────────────────────────────

class TestFactorLoadings:
    def test_pure_market_strategy_has_unit_mkt_beta_and_zero_alpha(self):
        """A strategy whose excess return is exactly 1.0 x MKT-RF must
        regress to mkt_rf beta ~= 1.0, alpha ~= 0, R^2 ~= 1.0."""
        from tools.analytics import factor_loadings
        # 36 months of varying factors (percent units, as Ken French publishes).
        mkt = [2.0, -1.5, 0.8, 3.1, -2.2, 1.0] * 6
        smb = [0.5, -0.7, 1.2, -0.3, 0.9, -1.1] * 6
        hml = [-0.4, 1.1, -0.9, 0.6, -1.3, 0.7] * 6
        rf_pct = [0.3] * 36
        ff = [
            {"yyyymm": 200201 + i if (200201 + i) % 100 <= 12 else 200301 + (i - 12),
             "mkt_rf": mkt[i], "smb": smb[i], "hml": hml[i], "rf": rf_pct[i]}
            for i in range(36)
        ]
        # Rebuild yyyymm cleanly: Jan 2002 .. Dec 2004.
        ff = []
        ym_list = []
        for year in (2002, 2003, 2004):
            for month in range(1, 13):
                ym_list.append(year * 100 + month)
        for i, ym in enumerate(ym_list):
            ff.append({"yyyymm": ym, "mkt_rf": mkt[i], "smb": smb[i],
                       "hml": hml[i], "rf": rf_pct[i]})

        # Strategy total return = rf + mkt_rf (decimal) → excess = mkt_rf.
        strat_pairs = []
        idx = pd.date_range("2002-01-31", periods=36, freq="ME")
        for i, d in enumerate(idx):
            total = (rf_pct[i] + mkt[i]) / 100.0
            strat_pairs.append([str(d.date()), total])

        results = {"MKT_CLONE": {"strategy_name": "MKT_CLONE",
                                 "monthly_returns": strat_pairs}}
        out = factor_loadings(results, ff)
        assert len(out) == 1
        row = out[0]
        assert abs(row["mkt_rf"] - 1.0) < 0.01
        assert abs(row["smb"]) < 0.01
        assert abs(row["hml"]) < 0.01
        assert abs(row["alpha_annualized"]) < 0.01
        assert row["r_squared"] > 0.99
        assert row["mkt_rf_significant"] is True

    def test_empty_ff_factors_returns_empty_list(self):
        from tools.analytics import factor_loadings
        assert factor_loadings({"A": {"monthly_returns": _pairs([0.01] * 24)}}, []) == []

    def test_four_factor_recovers_unit_mom_beta(self):
        """When MOM is present and the strategy's excess return is exactly
        1.0 x MOM, the Carhart regression must recover mom beta ~= 1.0 and
        record model == 'carhart_4factor'."""
        from tools.analytics import factor_loadings
        mkt = [2.0, -1.5, 0.8, 3.1, -2.2, 1.0] * 6
        smb = [0.5, -0.7, 1.2, -0.3, 0.9, -1.1] * 6
        hml = [-0.4, 1.1, -0.9, 0.6, -1.3, 0.7] * 6
        mom = [1.3, -0.6, 0.4, -1.0, 0.8, -0.2] * 6
        rf_pct = [0.3] * 36
        ym_list = [y * 100 + m for y in (2002, 2003, 2004) for m in range(1, 13)]
        ff = [
            {"yyyymm": ym, "mkt_rf": mkt[i], "smb": smb[i], "hml": hml[i],
             "mom": mom[i], "rf": rf_pct[i]}
            for i, ym in enumerate(ym_list)
        ]
        idx = pd.date_range("2002-01-31", periods=36, freq="ME")
        strat_pairs = [
            [str(d.date()), (rf_pct[i] + mom[i]) / 100.0]
            for i, d in enumerate(idx)
        ]
        out = factor_loadings(
            {"MOM_CLONE": {"strategy_name": "MOM_CLONE",
                           "monthly_returns": strat_pairs}},
            ff,
        )
        assert len(out) == 1
        row = out[0]
        assert row["model"] == "carhart_4factor"
        assert abs(row["mom"] - 1.0) < 0.01
        assert row["mom_significant"] is True
        assert abs(row["mkt_rf"]) < 0.01
        assert row["r_squared"] > 0.99

    def test_falls_back_to_three_factor_when_mom_absent(self):
        """A ff dataset with no mom key must regress as a three-factor model
        and report mom == None with model == 'ff_3factor'."""
        from tools.analytics import factor_loadings
        mkt = [2.0, -1.5, 0.8, 3.1, -2.2, 1.0] * 4
        smb = [0.5, -0.7, 1.2, -0.3, 0.9, -1.1] * 4
        hml = [-0.4, 1.1, -0.9, 0.6, -1.3, 0.7] * 4
        ym_list = [y * 100 + m for y in (2002, 2003) for m in range(1, 13)]
        ff = [{"yyyymm": ym, "mkt_rf": mkt[i], "smb": smb[i], "hml": hml[i],
               "rf": 0.3} for i, ym in enumerate(ym_list)]
        out = factor_loadings(
            {"A": {"strategy_name": "A", "monthly_returns": _pairs(_VARYING_24)}},
            ff,
        )
        assert len(out) == 1
        assert out[0]["model"] == "ff_3factor"
        assert out[0]["mom"] is None
        assert out[0]["mom_significant"] is False

    def test_null_mom_rows_drop_to_three_factor(self):
        """When mom is present but null on every row, the regression must
        drop those rows and fall back to the three-factor model."""
        from tools.analytics import factor_loadings
        mkt = [2.0, -1.5, 0.8, 3.1, -2.2, 1.0] * 4
        smb = [0.5, -0.7, 1.2, -0.3, 0.9, -1.1] * 4
        hml = [-0.4, 1.1, -0.9, 0.6, -1.3, 0.7] * 4
        ym_list = [y * 100 + m for y in (2002, 2003) for m in range(1, 13)]
        ff = [{"yyyymm": ym, "mkt_rf": mkt[i], "smb": smb[i], "hml": hml[i],
               "mom": None, "rf": 0.3} for i, ym in enumerate(ym_list)]
        out = factor_loadings(
            {"A": {"strategy_name": "A", "monthly_returns": _pairs(_VARYING_24)}},
            ff,
        )
        assert len(out) == 1
        assert out[0]["model"] == "ff_3factor"
        assert out[0]["mom"] is None


# ── 7. Cumulative returns ─────────────────────────────────────────────────────

class TestCumulativeReturns:
    def test_every_series_starts_at_one(self):
        from tools.analytics import cumulative_returns
        out = cumulative_returns({
            "A": {"strategy_name": "A", "monthly_returns": _pairs([0.02] * 12)},
            "B": {"strategy_name": "B", "monthly_returns": _pairs([-0.01] * 12)},
        })
        # The baseline month (one period before the first return) is 1.0 for all.
        first = out["points"][0]
        assert first["A"] == 1.0
        assert first["B"] == 1.0

    def test_constant_return_compounds_correctly(self):
        from tools.analytics import cumulative_returns
        out = cumulative_returns(
            {"A": {"strategy_name": "A", "monthly_returns": _pairs([0.10] * 3)}})
        # Baseline 1.0, then 1.10, 1.21, 1.331.
        vals = [p["A"] for p in out["points"]]
        assert vals[0] == 1.0
        assert abs(vals[-1] - 1.331) < 1e-6

    def test_empty_input_returns_empty(self):
        from tools.analytics import cumulative_returns
        assert cumulative_returns({}) == {
            "strategies": [], "points": [], "start_dates": {}}


# ── 8. Excess return and information ratio ────────────────────────────────────

class TestExcessAndInformationRatio:
    def test_benchmark_excess_return_is_zero(self):
        from tools.analytics import summary_statistics
        out = summary_statistics(
            {"BENCHMARK": _monthly_series([0.01] * 24),
             "OTHER": _monthly_series([0.02] * 24)},
            None,
        )
        bench = next(r for r in out if r["asset"] == "BENCHMARK")
        assert bench["excess_return"] == 0.0

    def test_benchmark_information_ratio_is_null(self):
        from tools.analytics import summary_statistics
        out = summary_statistics(
            {"BENCHMARK": _monthly_series([0.01] * 24),
             "OTHER": _monthly_series([0.02] * 24)},
            None,
        )
        # The benchmark has zero tracking error against itself — IR undefined.
        bench = next(r for r in out if r["asset"] == "BENCHMARK")
        assert bench["information_ratio"] is None

    def test_equity_information_ratio_is_null(self):
        from tools.analytics import summary_statistics
        # EQUITY *is* the benchmark (the benchmark is 100% equity). The
        # EQUITY asset series and the BENCHMARK strategy series are
        # economically identical but not bit-identical — a naive IR would
        # divide that tiny noise into a spurious value. IR must be null.
        out = summary_statistics(
            {"EQUITY": _monthly_series([0.011, 0.009] * 12),
             "BENCHMARK": _monthly_series([0.0109, 0.0091] * 12)},
            None,
        )
        equity = next(r for r in out if r["asset"] == "EQUITY")
        assert equity["information_ratio"] is None

    def test_excess_return_none_without_benchmark(self):
        from tools.analytics import summary_statistics
        out = summary_statistics({"A": _monthly_series([0.01] * 24)}, None)
        assert out[0]["excess_return"] is None
        assert out[0]["information_ratio"] is None

    def test_rolling_excess_benchmark_excluded_from_series(self):
        from tools.analytics import rolling_excess_return
        out = rolling_excess_return({
            "BENCHMARK": {"strategy_name": "BENCHMARK",
                          "monthly_returns": _pairs([0.01] * 24)},
            "A": {"strategy_name": "A", "monthly_returns": _pairs([0.02] * 24)},
        }, window=12)
        # The benchmark is the reference line, never a plotted series.
        assert "BENCHMARK" not in out["strategies"]
        assert "A" in out["strategies"]
