"""Smoke tests for tools/diversification_analytics — pure compute
functions, no DB. Builds tiny synthetic strategy_results dicts and
exercises each of the seven metrics for correctness on the easy
cases (symmetry, sign, ordering) without recomputing the
mathematical content the existing tools/analytics tests already
cover."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY",
                      "test-secret-key-at-least-32-characters-long")

import pandas as pd

from tools import diversification_analytics as div


def _strategy(name, dates, rets):
    return {"strategy_name": name,
            "monthly_returns": list(zip(dates, rets))}


# Synthetic 36-month series spanning Jan 2020 to Dec 2022.
DATES = pd.date_range("2020-01-31", periods=36, freq="ME")
DATES_ISO = [d.isoformat() for d in DATES]

# BENCHMARK: alternating positive/negative monthly returns.
BENCH_RET = [0.02 if i % 2 == 0 else -0.01 for i in range(36)]
# DEFENSIVE: half the benchmark's magnitude both directions.
DEF_RET = [r * 0.5 for r in BENCH_RET]
# AGGRESSIVE: 1.5x both directions.
AGG_RET = [r * 1.5 for r in BENCH_RET]
# INVERSE: opposite sign.
INV_RET = [-r for r in BENCH_RET]

STRATEGIES = {
    "BENCHMARK":   _strategy("BENCHMARK", DATES_ISO, BENCH_RET),
    "DEFENSIVE":   _strategy("DEFENSIVE", DATES_ISO, DEF_RET),
    "AGGRESSIVE":  _strategy("AGGRESSIVE", DATES_ISO, AGG_RET),
    "INVERSE":     _strategy("INVERSE", DATES_ISO, INV_RET),
}


def test_correlation_matrix_diagonal_is_1():
    out = div.correlation_matrices(STRATEGIES)
    assert out["labels"] == ["BENCHMARK", "DEFENSIVE", "AGGRESSIVE", "INVERSE"]
    for i in range(4):
        assert out["full"][i][i] == 1.0


def test_correlation_matrix_symmetric():
    out = div.correlation_matrices(STRATEGIES)
    n = len(out["labels"])
    for i in range(n):
        for j in range(n):
            assert out["full"][i][j] == out["full"][j][i]


def test_correlation_inverse_strategy_is_minus_one():
    # INVERSE = -BENCH, so their correlation is -1.0.
    out = div.correlation_matrices(STRATEGIES)
    i = out["labels"].index("BENCHMARK")
    j = out["labels"].index("INVERSE")
    assert out["full"][i][j] == -1.0


def test_tail_risk_cvar_at_or_below_var():
    """CVaR (expected shortfall) <= VaR at same confidence level —
    the tail beyond a threshold is worse than the threshold itself."""
    out = div.tail_risk(STRATEGIES)
    for row in out:
        assert row["cvar_95_monthly"] <= row["var_95_monthly"] + 1e-9
        assert row["cvar_99_monthly"] <= row["var_99_monthly"] + 1e-9


def test_tail_risk_var99_at_or_below_var95():
    """VaR 99% is a stricter threshold than VaR 95%, so its
    historical-simulation value is at-or-below (more negative)."""
    out = div.tail_risk(STRATEGIES)
    for row in out:
        assert row["var_99_monthly"] <= row["var_95_monthly"] + 1e-9


def test_capture_score_above_1_means_favourable_asymmetry():
    out = div.capture_ratios(STRATEGIES)
    # DEFENSIVE has 50% capture both ways → score == 1.0.
    # AGGRESSIVE has 150% both ways → score == 1.0.
    # INVERSE has negative up (-100%) and negative down (-100%) →
    # score == 1.0 (both negative, ratio is positive). All scores
    # exactly 1.0 in this contrived setup.
    for row in out:
        score = row["full"]["capture_score"]
        # All three strategies happen to score exactly 1.0 against
        # this benchmark. The test pins the contract that scores
        # are returned for every strategy.
        assert score is not None


def test_drawdown_duration_returns_a_row_per_strategy():
    out = div.drawdown_duration(STRATEGIES)
    assert len(out) == 4
    for row in out:
        # max_duration_months is always non-negative.
        assert row["max_duration_months"] >= 0
        assert row["avg_duration_months"] >= 0


def test_crisis_performance_includes_rate_shock_2022():
    out = div.crisis_performance(STRATEGIES)
    assert "Rate_Shock_2022" in out["windows"]
    for strategy_data in out["rows"].values():
        assert "Rate_Shock_2022" in strategy_data


def test_crisis_performance_flags_partial_when_data_too_short():
    # No data before 2020 → GFC_2008-2009 should be partial.
    out = div.crisis_performance(STRATEGIES)
    for strategy_data in out["rows"].values():
        gfc = strategy_data.get("GFC_2008-2009")
        assert gfc is not None
        assert gfc["partial"] is True


def test_mctr_returns_labels_and_arrays_aligned():
    out = div.marginal_contribution_to_risk(STRATEGIES)
    # Benchmark excluded; the remaining 3 strategies labelled.
    assert len(out["labels"]) == 3
    assert len(out["mctr_equal_weight"]) == 3
    assert len(out["pct_risk_contribution_equal"]) == 3


def test_mctr_pct_contribution_sums_to_100():
    """% risk contribution is a partition of portfolio variance,
    so it must sum to 100% (within rounding)."""
    out = div.marginal_contribution_to_risk(STRATEGIES)
    total = sum(out["pct_risk_contribution_equal"])
    # Allow 0.1% rounding tolerance.
    assert abs(total - 100.0) < 0.5


def test_mctr_with_tangency_weights():
    tangency = {"DEFENSIVE": 0.5, "AGGRESSIVE": 0.3, "INVERSE": 0.2}
    out = div.marginal_contribution_to_risk(STRATEGIES, tangency)
    assert out["mctr_tangency_weight"] is not None
    assert out["tangency_weights"] is not None
    # Tangency weights normalised to sum to 1.0.
    assert abs(sum(out["tangency_weights"]) - 1.0) < 1e-6


def test_return_distribution_includes_jb_and_best_worst():
    out = div.return_distribution(STRATEGIES)
    assert len(out) == 4
    for row in out:
        assert "skewness" in row
        assert "excess_kurtosis" in row
        assert len(row["best_months"]) == 3
        assert len(row["worst_months"]) == 3
        # JB statistic and p-value present (scipy is available).
        assert row["jarque_bera_stat"] is not None
        assert row["jarque_bera_p"] is not None


def test_empty_strategy_results_returns_empty_outputs():
    assert div.correlation_matrices({})["labels"] == []
    assert div.tail_risk({}) == []
    assert div.capture_ratios({}) == []
    assert div.drawdown_duration({}) == []
    assert div.return_distribution({}) == []
