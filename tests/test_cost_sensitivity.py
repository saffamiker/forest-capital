"""Transaction-cost sensitivity — pure-math tests.

Covers count_material_rebalances, compute_cost_sensitivity, and the
net-of-cost cumulative blend series compute_performance_chart emits for
the chart. All deterministic; no HMM fit, no DB, no network.
"""
import pytest

from tools.regime_meta_validation import (
    build_rebalance_events, compute_cost_sensitivity, count_material_rebalances,
)


def test_build_rebalance_events_rows():
    weights = [
        {"MIN_VARIANCE": 0.40, "RISK_PARITY": 0.40, "EQUAL_WEIGHT": 0.20},
        {"MIN_VARIANCE": 0.50, "RISK_PARITY": 0.34, "EQUAL_WEIGHT": 0.16},
        {"MIN_VARIANCE": 0.50, "RISK_PARITY": 0.34, "EQUAL_WEIGHT": 0.16},
    ]
    dates = ["2022-01-31", "2022-02-28", "2022-03-31"]
    regimes = ["BULL", "TRANSITION", "TRANSITION"]
    events = build_rebalance_events(weights, dates, regimes)
    # First month seeds; month 3 unchanged -> exactly one event (month 2).
    assert len(events) == 1
    ev = events[0]
    assert ev["date"] == "2022-02-28"
    assert ev["regime"] == "TRANSITION"
    assert ev["weights"]["MIN_VARIANCE"] == 0.50
    # total shift = |0.10| + |-0.06| + |-0.04| = 0.20.
    assert ev["total_shift"] == pytest.approx(0.20, abs=1e-6)


def test_build_rebalance_events_threshold_and_empty():
    # A 2.0% shift is not > 2% -> no event.
    weights = [{"A": 0.40}, {"A": 0.42}]
    assert build_rebalance_events(weights, ["d0", "d1"], [None, None]) == []
    assert build_rebalance_events([], [], []) == []


def test_count_material_rebalances_first_month_seeds():
    # t1 no change; t2 shifts 0.40 -> 0.50 (>2%) -> exactly one rebalance.
    weights = [{"A": 0.4, "B": 0.6}, {"A": 0.4, "B": 0.6}, {"A": 0.5, "B": 0.5}]
    assert count_material_rebalances(weights) == 1


def test_count_material_rebalances_threshold_is_strict():
    # A 2.0% shift is NOT > 2% (not counted); 2.1% is.
    assert count_material_rebalances([{"A": 0.40}, {"A": 0.42}]) == 0
    assert count_material_rebalances([{"A": 0.40}, {"A": 0.421}]) == 1


def test_count_material_rebalances_empty():
    assert count_material_rebalances([]) == 0
    assert count_material_rebalances([{"A": 1.0}]) == 0


def test_compute_cost_sensitivity_exact():
    # Two rebalances over the window.
    weights = [{"A": 0.4, "B": 0.6}, {"A": 0.5, "B": 0.5},
               {"A": 0.5, "B": 0.5}, {"A": 0.6, "B": 0.4}]
    out = compute_cost_sensitivity(
        blend_weights_monthly=weights, gross_sharpe=0.86, oos_vol=0.10,
        benchmark_sharpe=0.43, n_test_months=40, cost_bps=(10,))
    assert out["n_rebalances"] == 2
    s = out["scenarios"][0]
    assert s["bps"] == 10
    # total drag = 2 * 10 * 1e-4 = 0.002; annualised = 0.002 / (40/12);
    # net Sharpe = 0.86 - annualised/0.10 = 0.854.
    assert s["net_sharpe"] == pytest.approx(0.854, abs=1e-3)
    # vs benchmark = 0.854 / 0.43 - 1 ≈ 0.986.
    assert s["vs_benchmark_pct"] == pytest.approx(0.986, abs=1e-3)


def test_compute_cost_sensitivity_fail_open_on_missing_inputs():
    out = compute_cost_sensitivity(
        blend_weights_monthly=[{"A": 1.0}], gross_sharpe=None, oos_vol=None,
        benchmark_sharpe=None, n_test_months=0)
    # No gross/vol -> net_sharpe None, but the shape is still well-formed.
    assert out["scenarios"][0]["net_sharpe"] is None
    assert out["scenarios"][0]["vs_benchmark_pct"] is None


def test_performance_chart_net_series_applies_drag_at_rebalance(monkeypatch):
    """compute_performance_chart subtracts the per-bps drag only in months
    whose weights shifted materially, and leaves a no-rebalance month equal
    to the gross path."""
    from tools import play_by_play

    def fake_oos(strategy_results, hmm_result, *, split_date, return_series):
        return {
            "test_dates": ["2022-01-31", "2022-02-28", "2022-03-31"],
            "blend_monthly": [0.01, 0.02, 0.00],
            # t1: 0.5 -> 0.6 (rebalance); t2: unchanged (no rebalance).
            "blend_weights_monthly": [
                {"A": 0.5, "B": 0.5}, {"A": 0.6, "B": 0.4}, {"A": 0.6, "B": 0.4},
            ],
            "oos": {},
        }
    monkeypatch.setattr(
        "tools.regime_meta_validation.out_of_sample_validation", fake_oos)

    chart = play_by_play.compute_performance_chart({}, {})
    series = chart["series"]
    # Month 0 seeds the position (no rebalance) -> net == gross.
    assert series[0]["blend_net_10"] == pytest.approx(series[0]["regime_conditional"])
    # Month 1 is a rebalance -> net is strictly below gross, and more so at
    # higher cost assumptions.
    assert series[1]["blend_net_10"] < series[1]["regime_conditional"]
    assert series[1]["blend_net_20"] < series[1]["blend_net_10"]
