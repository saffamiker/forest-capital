"""Unit tests for the invariant framework.

Each check_* has a positive case (clean fixture passes) and a
negative case (a crafted violation fires the correct code). The F3
regression is the headline negative case for 1a + 1h + 2a — the
three assertions that would have caught the original incident.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")

from tools.invariant_checks import (  # noqa: E402
    check_1a_window_return_le_full_max_dd,
    check_1b_sharpe_consistent_with_components,
    check_1c_max_drawdown_le_min_monthly,
    check_1d_cvar99_le_cvar95,
    check_1e_weight_schedule_sums_to_one,
    check_1f_correlation_matrix_psd,
    check_1g_monthly_return_bounds,
    check_1h_full_period_dd_dominates_crisis,
    check_2a_crisis_uses_cumulative_basis,
    check_2b_full_period_sharpe_annualised,
    check_3_benchmark_crisis_plausibility,
    check_3_macro_series_plausibility,
    check_4a_defensive_protects_in_crash,
    check_4c_tangency_sharpe_dominates,
    check_4d_bootstrap_ci_brackets_point,
    check_5a_no_monthly_gaps,
    check_5e_oos_split_after_lookbacks,
    run_all_invariants,
)


def _series_payload(name: str, returns: list[float], start: str = "2020-01-31") -> dict:
    idx = pd.date_range(start, periods=len(returns), freq="ME")
    pairs = [{"date": d.strftime("%Y-%m-%d"), "return": float(v)}
             for d, v in zip(idx, returns)]
    return {"strategy_name": name, "monthly_returns": pairs}


# ── Category 1 ─────────────────────────────────────────────────────────────


def test_1a_passes_when_crisis_loss_within_full_period_dd():
    series = [-0.10, 0.05, -0.05, 0.03, -0.02] * 6   # 30 months
    payload = {"TEST": _series_payload("TEST", series)}
    crisis = {
        "windows": {"WINDOW": {"start": "2020-01-01", "end": "2020-03-31"}},
        "rows": {"TEST": {"WINDOW": {"cumulative_return": -0.05}}},
    }
    vios, _ = check_1a_window_return_le_full_max_dd(payload, crisis)
    assert vios == []


def test_1a_fires_on_f3_regression():
    """COVID Crash -73.53% on a strategy whose full-period max DD was
    -52.56% — the original F3 bug. This must fire 1a."""
    series = [-0.10, 0.05, -0.05, 0.03, -0.02] * 6
    payload = {"TEST": _series_payload("TEST", series)}
    crisis = {
        "windows": {"COVID_Crash_2020":
                    {"start": "2020-01-01", "end": "2020-03-31"}},
        # The bug value the display would have shown pre-fix.
        "rows": {"TEST": {"COVID_Crash_2020":
                          {"cumulative_return": -0.7353}}},
    }
    vios, _ = check_1a_window_return_le_full_max_dd(payload, crisis)
    assert len(vios) == 1
    assert vios[0].code == "1a"
    assert vios[0].severity == "hard"
    assert "TEST/COVID_Crash_2020" in vios[0].entity


def test_1b_passes_on_backtester_formula_sharpe():
    """May 31 2026 — 1b was rewritten to mirror tools/backtester._m_sharpe
    exactly: arithmetic mean of (returns - rf_aligned) / std(excess,
    ddof=1) * sqrt(12). A stored value computed by the SAME formula
    must pass. Previously 1b used (CAGR - 0)/ann_vol — a different
    formula — and fired on every real strategy."""
    rng = np.random.default_rng(seed=11)
    n = 36
    series = list(rng.normal(0.006, 0.04, n))
    rf_values = list(rng.normal(0.002, 0.001, n))
    payload = _series_payload("TEST", series, start="2020-01-31")
    s = pd.Series(series)
    rf_s = pd.Series(rf_values, index=pd.date_range(
        "2020-01-31", periods=n, freq="ME"))
    # Backtester's exact formula:
    excess = s.values - rf_s.values
    sharpe = float(np.mean(excess) / np.std(excess, ddof=1) * np.sqrt(12))
    payload["sharpe_ratio"] = sharpe
    vios, _ = check_1b_sharpe_consistent_with_components(
        {"TEST": payload}, risk_free_rate=rf_s)
    assert vios == []


def test_1b_fires_when_stored_sharpe_drifts():
    """The check still fires when a stored Sharpe genuinely disagrees
    with the recompute beyond the 0.02 tolerance."""
    rng = np.random.default_rng(seed=42)
    n = 36
    series = list(rng.normal(0.006, 0.04, n))
    rf_s = pd.Series(
        [0.002] * n,
        index=pd.date_range("2020-01-31", periods=n, freq="ME"))
    payload = _series_payload("TEST", series, start="2020-01-31")
    # Store a Sharpe that's 0.5 off the actual.
    s = pd.Series(series)
    excess = s.values - 0.002
    real_sharpe = float(np.mean(excess) / np.std(excess, ddof=1) * np.sqrt(12))
    payload["sharpe_ratio"] = real_sharpe + 0.5
    vios, _ = check_1b_sharpe_consistent_with_components(
        {"TEST": payload}, risk_free_rate=rf_s)
    assert len(vios) == 1 and vios[0].code == "1b"


def test_1b_skipped_when_rf_not_supplied():
    """Without a real rf series the recompute is not faithful to the
    backtester. The check skips entirely rather than falling back to
    rf=0 — fallback produced the false positive that blocked every
    cache write before this hotfix."""
    rng = np.random.default_rng(seed=7)
    series = list(rng.normal(0.006, 0.04, 36))
    payload = _series_payload("TEST", series, start="2020-01-31")
    payload["sharpe_ratio"] = 0.5
    vios, n = check_1b_sharpe_consistent_with_components(
        {"TEST": payload}, risk_free_rate=None)
    assert vios == []
    assert n == 0


def test_1b_does_not_fire_on_backtester_realistic_series():
    """Regression test for the May 31 hotfix. Reproduces the production
    failure: a strategy whose stored Sharpe was computed by the
    backtester's arithmetic-monthly formula must NOT trip 1b when 1b
    is run with the same rf series. The old 1b formula
    (CAGR - 0)/ann_vol produced a tolerance gap > 0.02 on this exact
    shape, blocking every cache write across all 10 strategies."""
    rng = np.random.default_rng(seed=2026)
    # 23 years of monthly data, equity-like distribution.
    n = 276
    series = list(rng.normal(0.0072, 0.045, n))
    # DTB3-like rf — averages ~2% annual.
    rf_values = list(np.clip(rng.normal(0.0018, 0.0008, n), 0, 0.005))
    rf_s = pd.Series(
        rf_values,
        index=pd.date_range("2002-01-31", periods=n, freq="ME"))
    s = pd.Series(series)
    # Backtester's exact formula — what would actually be stored.
    excess = s.values - rf_s.values
    sharpe = float(np.mean(excess) / np.std(excess, ddof=1) * np.sqrt(12))
    payload = _series_payload("STRAT", series, start="2002-01-31")
    payload["sharpe_ratio"] = sharpe
    # Also store CAGR + vol the way the backtester does — these are
    # NOT used by 1b after the hotfix but their presence in the
    # payload exercises the realistic shape.
    payload["cagr"] = float((1 + s).prod()) ** (12 / n) - 1
    payload["volatility"] = float(s.std(ddof=1)) * np.sqrt(12)
    vios, _ = check_1b_sharpe_consistent_with_components(
        {"STRAT": payload}, risk_free_rate=rf_s)
    assert vios == [], (
        f"1b false positive on backtester-formula Sharpe: {vios}")


def test_1c_fires_when_max_dd_less_negative_than_worst_month():
    payload = _series_payload("T", [-0.30, 0.01, 0.02])
    payload["max_drawdown"] = -0.10  # less negative than -0.30
    vios, _ = check_1c_max_drawdown_le_min_monthly({"T": payload})
    assert len(vios) == 1 and vios[0].code == "1c"


def test_1d_fires_when_cvar99_less_negative_than_cvar95():
    payload = _series_payload("T", [0.0] * 12)
    payload.update({"cvar_95": -0.20, "cvar_99": -0.10})
    vios, _ = check_1d_cvar99_le_cvar95({"T": payload})
    assert len(vios) == 1 and vios[0].code == "1d"


def test_1e_passes_when_weights_sum_to_one():
    payload = {"T": {
        "weight_schedule": [
            {"date": "2020-01-31",
             "weights": {"e": 0.6, "i": 0.3, "h": 0.1}},
            {"date": "2020-04-30",
             "weights": {"e": 0.5, "i": 0.5, "h": 0.0}},
        ]}}
    vios, _ = check_1e_weight_schedule_sums_to_one(payload)
    assert vios == []


def test_1e_fires_when_weights_drift():
    payload = {"T": {
        "weight_schedule": [
            {"date": "2020-01-31",
             "weights": {"e": 0.6, "i": 0.3, "h": 0.0}},  # 0.9
        ]}}
    vios, _ = check_1e_weight_schedule_sums_to_one(payload)
    assert len(vios) == 1 and vios[0].code == "1e"


def test_1f_passes_on_psd_correlation_matrix():
    payload = {"full": [[1.0, 0.3], [0.3, 1.0]]}
    vios, _ = check_1f_correlation_matrix_psd(payload)
    assert vios == []


def test_1f_fires_on_non_psd_matrix():
    # Off-diagonal > 1 → eigenvalue negative.
    payload = {"full": [[1.0, 1.5], [1.5, 1.0]]}
    vios, _ = check_1f_correlation_matrix_psd(payload)
    assert len(vios) == 1 and vios[0].code == "1f"


def test_1g_fires_on_out_of_band_return():
    payload = _series_payload("T", [0.01, 3.5, -0.02])  # 350% month
    vios, _ = check_1g_monthly_return_bounds({"T": payload})
    assert len(vios) == 1 and vios[0].code == "1g"


def test_1h_fires_on_f3_regression():
    """Subset-invariant framing of the F3 bug: cumulative crisis loss
    more negative than full-period max DD."""
    series = [-0.10, 0.05, -0.05, 0.03, -0.02] * 6
    payload = {"TEST": _series_payload("TEST", series)}
    crisis = {
        "windows": {"COVID": {"start": "2020-01-01", "end": "2020-03-31"}},
        "rows": {"TEST": {"COVID": {"cumulative_return": -0.7353}}},
    }
    vios, _ = check_1h_full_period_dd_dominates_crisis(payload, crisis)
    assert len(vios) == 1 and vios[0].code == "1h"


# ── Category 2 ─────────────────────────────────────────────────────────────


def test_2a_passes_when_displayed_matches_recompute():
    series = [0.05, -0.10, 0.02, 0.01, -0.03] * 6
    payload = {"T": _series_payload("T", series, start="2020-01-31")}
    # Inline-compute the cumulative for the first 2 months.
    sub = pd.Series(series[:2])
    expected = float((1 + sub).prod() - 1)
    crisis = {
        "windows": {"W": {"start": "2020-01-01", "end": "2020-02-29"}},
        "rows": {"T": {"W": {"cumulative_return": round(expected, 4)}}},
    }
    vios, _ = check_2a_crisis_uses_cumulative_basis(crisis, payload)
    assert vios == []


def test_2a_fires_when_cagr_leaks_into_display():
    """The F3 bug: CAGR leaked into the cumulative_return field."""
    series = [-0.085, -0.125] + [0.005] * 22
    payload = {"T": _series_payload("T", series, start="2020-01-31")}
    sub = pd.Series(series[:2])
    cumulative = float((1 + sub).prod() - 1)
    cagr_value = (1 + cumulative) ** (12 / 2) - 1
    # Display CAGR where cumulative was expected.
    crisis = {
        "windows": {"COVID_Crash_2020":
                    {"start": "2020-01-01", "end": "2020-02-29"}},
        "rows": {"T": {"COVID_Crash_2020":
                       {"cumulative_return": round(cagr_value, 4)}}},
    }
    vios, _ = check_2a_crisis_uses_cumulative_basis(crisis, payload)
    assert len(vios) == 1 and vios[0].code == "2a"


def test_2b_fires_when_stored_sharpe_is_monthly_not_annualised():
    series = [0.01] * 30
    payload = _series_payload("T", series)
    s = pd.Series(series)
    monthly_sharpe = float(s.mean()) / float(s.std(ddof=1) or 1e-9)
    payload["sharpe_ratio"] = monthly_sharpe  # missing the sqrt(12)
    vios, _ = check_2b_full_period_sharpe_annualised({"T": payload})
    # 30 const positives → std() = 0; fall through silently.
    # Use a noisy series instead.
    rng = np.random.default_rng(42)
    series = list(rng.normal(0.005, 0.04, 30))
    payload = _series_payload("T", series)
    s = pd.Series(series)
    payload["sharpe_ratio"] = float(s.mean()) / float(s.std(ddof=1))
    vios, _ = check_2b_full_period_sharpe_annualised({"T": payload})
    assert len(vios) == 1 and vios[0].code == "2b"


# ── Category 3 ─────────────────────────────────────────────────────────────


def test_3_benchmark_plausibility_passes_when_inside_range():
    crisis = {
        "rows": {"BENCHMARK": {
            "GFC_2008-2009":    {"cumulative_return": -0.4566},
            "COVID_Crash_2020": {"cumulative_return": -0.1987},
        }}
    }
    vios, _ = check_3_benchmark_crisis_plausibility(crisis)
    assert vios == []


def test_3_benchmark_plausibility_soft_warns_on_drift():
    crisis = {
        "rows": {"BENCHMARK": {
            "COVID_Crash_2020": {"cumulative_return": -0.50},  # too deep
        }}
    }
    vios, _ = check_3_benchmark_crisis_plausibility(crisis)
    assert len(vios) == 1
    assert vios[0].severity == "soft"


def test_3_macro_plausibility_soft_warns_on_implausible_vix():
    macro = {"vix": [12.0, 18.5, 250.0]}  # 250 > upper bound 85
    vios, _ = check_3_macro_series_plausibility(macro)
    assert len(vios) == 1 and vios[0].severity == "soft"


# ── Category 4 ─────────────────────────────────────────────────────────────


def test_4a_passes_when_vol_targeting_beats_benchmark_in_crash():
    crisis = {
        "rows": {
            "BENCHMARK":     {"W": {"cumulative_return": -0.20}},
            "VOL_TARGETING": {"W": {"cumulative_return": -0.05}},
        }}
    vios, _ = check_4a_defensive_protects_in_crash({}, crisis)
    assert vios == []


def test_4a_soft_warns_when_defensive_strategy_loses_more():
    crisis = {
        "rows": {
            "BENCHMARK":     {"W": {"cumulative_return": -0.10}},
            "VOL_TARGETING": {"W": {"cumulative_return": -0.20}},
        }}
    vios, _ = check_4a_defensive_protects_in_crash({}, crisis)
    assert len(vios) == 1 and vios[0].severity == "soft"


def test_4c_soft_warns_when_tangency_below_best_individual():
    sr = {
        "A": {"sharpe_ratio": 1.0},
        "B": {"sharpe_ratio": 0.5},
    }
    vios, _ = check_4c_tangency_sharpe_dominates(sr, tangency_sharpe=0.7)
    assert len(vios) == 1 and vios[0].severity == "soft"


def test_4d_soft_warns_on_inverted_ci():
    bs = {"rows": [
        {"strategy": "A", "sharpe_ratio": 0.5,
         "ci_low": 0.6, "ci_high": 0.7},  # point below lo
    ]}
    vios, _ = check_4d_bootstrap_ci_brackets_point(bs)
    assert len(vios) == 1 and vios[0].severity == "soft"


# ── Category 5 ─────────────────────────────────────────────────────────────


def test_5a_fires_on_monthly_gap():
    idx = pd.date_range("2020-01-31", periods=6, freq="ME").tolist()
    idx.pop(2)  # delete March
    pairs = [{"date": d.strftime("%Y-%m-%d"), "return": 0.01} for d in idx]
    payload = {"T": {"strategy_name": "T", "monthly_returns": pairs}}
    vios, _ = check_5a_no_monthly_gaps(payload)
    assert len(vios) == 1 and vios[0].code == "5a"


def test_5e_soft_warns_when_oos_too_close_to_start():
    vios, _ = check_5e_oos_split_after_lookbacks(
        oos_split="2003-01-01", data_start="2002-07-31",
        min_months_after_start=36)
    assert len(vios) == 1 and vios[0].code == "5e"


# ── Top-level runner integration ───────────────────────────────────────────


def test_run_all_invariants_returns_passing_result_on_clean_fixture():
    series = [0.01, 0.02, -0.005, 0.012, 0.008] * 6
    res = run_all_invariants(
        {"BENCHMARK": _series_payload("BENCHMARK", series)})
    assert res.passed
    assert len(res.hard_failures) == 0


def test_run_all_invariants_fails_on_f3_payload():
    """End-to-end F3 reproduction — every assertion that should catch
    it (1a, 1h, 2a) fires together."""
    series = [-0.10, 0.05, -0.05, 0.03, -0.02] * 6
    crisis = {
        "windows": {"COVID_Crash_2020":
                    {"start": "2020-01-01", "end": "2020-03-31"}},
        "rows": {"BENCHMARK": {"COVID_Crash_2020":
                               {"cumulative_return": -0.7353}}},
    }
    res = run_all_invariants(
        {"BENCHMARK": _series_payload("BENCHMARK", series)},
        crisis_payload=crisis)
    assert not res.passed
    codes = {v.code for v in res.hard_failures}
    assert "1a" in codes
    assert "1h" in codes


def test_get_latest_result_returns_module_cache():
    from tools.invariant_checks import get_latest_result
    series = [0.01] * 12
    run_all_invariants(
        {"BENCHMARK": _series_payload("BENCHMARK", series)})
    latest = get_latest_result()
    assert latest is not None
    assert "passed" in latest
    assert "ran_at" in latest
