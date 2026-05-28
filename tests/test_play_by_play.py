"""Tests for tools/play_by_play.py. The pure forward-performance maths
and the deterministic recommendation are fully covered here; the
point-in-time HMM orchestration (point_in_time_blend / evaluate_event)
needs hmmlearn + a live equity fit and is exercised on Render."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")

import pandas as pd  # noqa: E402

from tools import play_by_play as pbp  # noqa: E402


def _dates(n: int, start: str = "2022-01-31") -> list[str]:
    return [d.date().isoformat()
            for d in pd.date_range(start, periods=n, freq="ME")]


def _results(series: dict[str, list[float]], start: str = "2022-01-31") -> dict:
    """strategy_results from explicit per-month value lists (all the same
    length, aligned to a shared month-end index)."""
    n = len(next(iter(series.values())))
    ds = _dates(n, start)
    return {name: {"monthly_returns": [[ds[t], vals[t]] for t in range(n)]}
            for name, vals in series.items()}


class TestEventsRegistry:

    def test_nine_events_well_formed(self):
        assert len(pbp.EVENTS) == 9
        ids = [e["event_id"] for e in pbp.EVENTS]
        assert len(set(ids)) == 9
        for e in pbp.EVENTS:
            assert e["event_id"] and e["label"] and e["trigger"]
            # date parses to a month-end
            d = pd.Timestamp(e["event_date"])
            assert d == d + pd.offsets.MonthEnd(0)
            # one factual sentence, no em dashes (project prose rule)
            assert "—" not in e["trigger"]


class TestComputeEventPerformance:

    def test_forward_window_and_weighting(self):
        # 12 months from 2022-01; event at 2022-06-30 -> forward months
        # are 2022-07..2022-09 for the 90-day horizon (and a 4th month
        # that must NOT be included).
        res = _results({
            "STRAT_A": [0.0] * 6 + [0.03, -0.01, 0.02, 0.05, 0.0, 0.0],
            "STRAT_B": [0.0] * 6 + [0.01, 0.00, 0.01, 0.05, 0.0, 0.0],
            "BENCHMARK": [0.0] * 6 + [0.02, 0.01, -0.01, 0.05, 0.0, 0.0],
            "CLASSIC_60_40": [0.0] * 6 + [0.012, 0.006, -0.004, 0.0, 0.0, 0.0],
        })
        out = pbp.compute_event_performance(
            res, {"STRAT_A": 0.5, "STRAT_B": 0.5}, "2022-06-30")
        perf = out["performance"]
        # blend monthly = 0.5*A + 0.5*B over the three forward months
        blend = np.array([0.02, -0.005, 0.015])
        assert perf["blend"]["d30"] == pytest.approx(
            float(np.prod(1 + blend[:1]) - 1), abs=1e-6)
        assert perf["blend"]["d60"] == pytest.approx(
            float(np.prod(1 + blend[:2]) - 1), abs=1e-6)
        assert perf["blend"]["d90"] == pytest.approx(
            float(np.prod(1 + blend[:3]) - 1), abs=1e-6)
        # benchmark forward = [0.02, 0.01, -0.01]
        assert perf["benchmark"]["d90"] == pytest.approx(
            float(np.prod(1 + np.array([0.02, 0.01, -0.01])) - 1), abs=1e-6)
        # the 4th forward month (0.05) must not leak into d90
        assert perf["blend"]["d90"] < 0.05

    def test_value_added_and_verdict(self):
        res = _results({
            "STRAT_A": [0.0] * 6 + [0.03, -0.01, 0.02],
            "BENCHMARK": [0.0] * 6 + [0.00, 0.00, -0.02],
            "CLASSIC_60_40": [0.0] * 6 + [0.01, 0.00, 0.00],
        })
        out = pbp.compute_event_performance(
            res, {"STRAT_A": 1.0}, "2022-06-30")
        # blend beats benchmark over 90d -> verdict says added value
        assert out["value_added_sharpe"] is not None
        assert "added value" in out["verdict"]
        assert "directional" in out["verdict"]

    def test_short_forward_window_yields_none(self):
        # Event one month before the data ends: only d30 is available.
        res = _results({
            "STRAT_A": [0.0] * 10 + [0.02, 0.0],
            "BENCHMARK": [0.0] * 10 + [0.01, 0.0],
            "CLASSIC_60_40": [0.0] * 10 + [0.005, 0.0],
        })
        # data ends at month 12; event at month 11 -> 1 forward month.
        ed = _dates(12)[10]
        out = pbp.compute_event_performance(res, {"STRAT_A": 1.0}, ed)
        assert out["performance"]["blend"]["d30"] is not None
        assert out["performance"]["blend"]["d90"] is None
        assert out["value_added_sharpe"] is None  # <2 obs

    def test_bad_date_is_fail_open(self):
        out = pbp.compute_event_performance({}, {"X": 1.0}, "not-a-date")
        assert out["value_added_sharpe"] is None
        assert out["performance"] == {}


class TestEventRecommendation:

    def test_recommendation_and_named_dissent(self):
        rec = pbp.event_recommendation(
            "BEAR", {"bull": 0.1, "bear": 0.7, "transition": 0.2},
            {"MIN_VARIANCE": 0.4, "VOL_TARGETING": 0.3, "RISK_PARITY": 0.3})
        assert "BEAR" in rec["recommendation"]
        assert "MIN_VARIANCE" in rec["recommendation"]
        # dissent names a specific limitation, not a generic hedge
        assert ("point-in-time" in rec["dissenting_view"]
                and "training window" in rec["dissenting_view"])
        # project prose rule
        assert "—" not in rec["recommendation"]
        assert "—" not in rec["dissenting_view"]

    def test_handles_missing_regime(self):
        rec = pbp.event_recommendation(None, None, {})
        assert isinstance(rec["recommendation"], str)
        assert isinstance(rec["dissenting_view"], str)


class TestHelpers:

    def test_compound_and_sharpe_edge_cases(self):
        assert pbp._compound(np.empty(0)) is None
        assert pbp._compound(np.array([0.1, 0.1])) == pytest.approx(0.21)
        assert pbp._annualised_sharpe(np.array([0.01])) is None  # <2 obs
        assert pbp._annualised_sharpe(np.array([0.01, 0.01])) is None  # 0 var
        assert pbp._annualised_sharpe(
            np.array([0.02, -0.01, 0.03])) is not None


class TestCacheAndPersistence:

    def test_run_skips_existing_event_ids(self):
        res = _results({
            "STRAT_A": [0.01] * 12, "BENCHMARK": [0.01] * 12,
            "CLASSIC_60_40": [0.005] * 12})
        eq = pd.Series([0.01] * 12,
                       index=pd.to_datetime(_dates(12)))
        all_ids = {e["event_id"] for e in pbp.EVENTS}
        # Every event cached -> nothing recomputed.
        assert pbp.run_play_by_play(
            res, eq, existing_event_ids=all_ids) == []
        # All but one cached -> exactly the one uncached event is attempted.
        one_left = all_ids - {pbp.EVENTS[0]["event_id"]}
        out = pbp.run_play_by_play(res, eq, existing_event_ids=one_left)
        assert len(out) == 1
        assert out[0]["event_id"] == pbp.EVENTS[0]["event_id"]

    def test_is_persistable_gate(self):
        complete = {"regime": "BEAR",
                    "performance": {"blend": {"d30": 0.01, "d60": 0.02,
                                              "d90": 0.03}}}
        assert pbp.is_persistable(complete) is True
        assert pbp.is_persistable({"error": "x"}) is False
        assert pbp.is_persistable({"regime": None}) is False
        # Forward window not yet complete -> not frozen.
        assert pbp.is_persistable(
            {"regime": "BULL",
             "performance": {"blend": {"d30": 0.01, "d90": None}}}) is False

    def test_evaluate_event_uses_recommend_fn(self, monkeypatch):
        # Stub the Render-side HMM step so evaluate_event reaches the
        # recommendation; verify the injected recommend_fn is used and
        # receives the event.
        monkeypatch.setattr(pbp, "point_in_time_blend", lambda *a, **k: {
            "regime": "BEAR",
            "posterior": {"bull": 0.1, "bear": 0.7, "transition": 0.2},
            "blend_weights": {"STRAT_A": 1.0},
            "n_train_months": 100})
        res = _results({
            "STRAT_A": [0.0] * 6 + [0.02, 0.01, 0.0],
            "BENCHMARK": [0.0] * 6 + [0.0, 0.0, 0.0],
            "CLASSIC_60_40": [0.0] * 6 + [0.0, 0.0, 0.0]})
        seen = {}

        def fake_rec(event, regime, posterior, blend_weights):
            seen["event_id"] = event["event_id"]
            seen["regime"] = regime
            return {"recommendation": "REC", "dissenting_view": "DIS"}

        row = pbp.evaluate_event(
            pbp.EVENTS[0], res, pd.Series(dtype=float),
            recommend_fn=fake_rec)
        assert row["recommendation"] == "REC"
        assert row["dissenting_view"] == "DIS"
        assert seen["event_id"] == pbp.EVENTS[0]["event_id"]
        assert seen["regime"] == "BEAR"

    def test_llm_recommendation_fails_open_to_deterministic(self):
        # No API key in the test environment -> call_claude raises ->
        # falls back to the deterministic recommendation.
        out = pbp.llm_event_recommendation(
            pbp.EVENTS[0], "BEAR",
            {"bull": 0.1, "bear": 0.7, "transition": 0.2},
            {"MIN_VARIANCE": 0.5, "VOL_TARGETING": 0.5})
        assert out["recommendation"] and out["dissenting_view"]
        assert "—" not in out["recommendation"]
        assert "—" not in out["dissenting_view"]
