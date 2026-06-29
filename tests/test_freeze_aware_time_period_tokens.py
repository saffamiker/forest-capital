"""tests/test_freeze_aware_time_period_tokens.py -- June 29 2026.

Pins for the freeze-aware time-period token derivation:
  - get_strategy_cache projects n_observations into the
    returned dict as _n_observations
  - compute_freeze_aware_window_metrics derives OOS_WINDOW_*,
    STUDY_END, STUDY_MONTHS from cache payload + monthly_returns
  - build_substitution_table picks up the derived values
    automatically when callers don't override
  - _pre_2022_months accepts post_2022_months kwarg
"""
from __future__ import annotations

import inspect
import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── compute_freeze_aware_window_metrics ──────────────────


class TestComputeFreezeAwareWindowMetrics:

    def test_derives_freeze_values_from_281_obs_payload(self):
        """281 months from Jul 2002 ends Nov 2025; OOS window
        Jan 2022 -> Nov 2025 = 47 months, pct = 16.7%.

        Operator's spec stated the freeze freeze-end date as
        "December 2025" (the freeze activation calendar date)
        but the actual frozen data ends 281 months after the
        Jul 2002 study start, which is Nov 2025. The
        derivation reads the actual end date from the cache
        payload, so the test fixture's last entry must
        match -- otherwise the math diverges by one month."""
        from tools.academic_export import (
            compute_freeze_aware_window_metrics,
        )
        strategy_results = {
            "_n_observations": 281,
            "BENCHMARK": {
                "monthly_returns": [
                    ["2002-07-31", 0.01],
                    # ... 279 intermediate entries omitted ...
                    ["2025-10-31", 0.01],
                    ["2025-11-30", 0.02],
                ],
            },
        }
        out = compute_freeze_aware_window_metrics(
            strategy_results)
        assert out is not None
        assert out["study_months"] == 281
        assert out["study_end"] == "November 2025"
        assert out["oos_window_months"] == 47
        assert out["oos_window_pct_of_study"] == 16.7
        assert out["post_2022_months"] == 47
        assert out["last_year"] == 2025
        assert out["last_month"] == 11

    def test_derives_live_may_2026_values(self):
        """Live (May 2026): 287 months total, OOS window
        Jan 2022 -> May 2026 = 53 months, pct = 18.5%."""
        from tools.academic_export import (
            compute_freeze_aware_window_metrics,
        )
        strategy_results = {
            "_n_observations": 287,
            "CLASSIC_60_40": {
                "monthly_returns": [
                    ["2002-07-31", 0.01],
                    ["2026-05-31", 0.02],
                ],
            },
        }
        out = compute_freeze_aware_window_metrics(
            strategy_results)
        assert out is not None
        assert out["study_months"] == 287
        assert out["study_end"] == "May 2026"
        assert out["oos_window_months"] == 53
        assert out["oos_window_pct_of_study"] == 18.5

    def test_returns_none_on_missing_n_observations(self):
        from tools.academic_export import (
            compute_freeze_aware_window_metrics,
        )
        out = compute_freeze_aware_window_metrics({
            "BENCHMARK": {"monthly_returns": [
                ["2025-12-31", 0.01]]},
        })
        assert out is None

    def test_returns_none_on_empty_strategy_results(self):
        from tools.academic_export import (
            compute_freeze_aware_window_metrics,
        )
        assert compute_freeze_aware_window_metrics(None) is None
        assert compute_freeze_aware_window_metrics({}) is None
        assert compute_freeze_aware_window_metrics(
            {"_n_observations": 281}) is None

    def test_skips_underscore_keys_when_finding_last_date(
            self):
        """The _n_observations field is at the same dict
        level as strategy entries; underscore-prefixed keys
        must be skipped when looking for the monthly_returns
        date axis."""
        from tools.academic_export import (
            compute_freeze_aware_window_metrics,
        )
        strategy_results = {
            "_n_observations": 281,
            "_some_other_metadata": {"monthly_returns": "ignore"},
            "BENCHMARK": {
                "monthly_returns": [["2025-12-31", 0.01]],
            },
        }
        out = compute_freeze_aware_window_metrics(
            strategy_results)
        assert out is not None
        assert out["study_end"] == "December 2025"


# ── _pre_2022_months freeze-aware ──────────────────────


class TestPre2022MonthsFreezeAware:

    def test_accepts_post_2022_months_kwarg(self):
        from tools.numeric_substitution import _pre_2022_months
        sig = inspect.signature(_pre_2022_months)
        assert "post_2022_months" in sig.parameters

    def test_freeze_value_47_yields_correct_pre_count(self):
        """Under Dec 2025 freeze: 281 - 47 = 234 months
        before 2022."""
        from tools.numeric_substitution import _pre_2022_months
        out = _pre_2022_months(
            study_months=281,
            strategy_cache=None,
            post_2022_months=47)
        assert out == "234"

    def test_live_value_53_yields_correct_pre_count(self):
        """Under live: 287 - 53 = 234 months."""
        from tools.numeric_substitution import _pre_2022_months
        out = _pre_2022_months(
            study_months=287,
            strategy_cache=None,
            post_2022_months=53)
        assert out == "234"

    def test_legacy_call_falls_back_to_53(self):
        """Without the new kwarg, helper uses live constant
        53 (back-compat)."""
        from tools.numeric_substitution import _pre_2022_months
        out = _pre_2022_months(287, None)
        assert out == "234"  # 287 - 53


# ── get_strategy_cache projects n_observations ──────────


class TestGetStrategyCacheProjectsNObservations:

    def test_source_pin_projects_n_observations(self):
        """The SQL now SELECTs n_observations alongside
        results_json + the dict construction adds
        _n_observations to the returned payload."""
        from tools.cache import get_strategy_cache
        src = inspect.getsource(get_strategy_cache)
        assert "SELECT results_json, n_observations" in src
        assert '"_n_observations"' in src


# ── build_substitution_table auto-derives ────────────────


class TestBuildSubstitutionTableAutoDerives:

    def test_imports_and_calls_compute_helper(self):
        """Source-pin: build_substitution_table imports
        compute_freeze_aware_window_metrics + calls it on
        strategy_cache to derive defaults that callers
        didn't supply."""
        from tools.numeric_substitution import (
            build_substitution_table,
        )
        src = inspect.getsource(build_substitution_table)
        assert "compute_freeze_aware_window_metrics" in src
        assert "_derived" in src

    def test_freeze_aware_oos_window_in_table(self):
        """End-to-end: pass a 281-obs strategy_cache (Nov 2025
        end) -> OOS_WINDOW_MONTHS resolves to 47, STUDY_END
        to 'November 2025'."""
        from tools.numeric_substitution import (
            get_substitution_table,
        )
        sc = {
            "_n_observations": 281,
            "BENCHMARK": {
                "monthly_returns": [
                    ["2002-07-31", 0.01],
                    ["2025-11-30", 0.02],
                ],
                "sharpe_ratio": 0.43,
            },
        }
        t = get_substitution_table(
            "test_freeze_281_v1", sc, None,
            hash_verified=True)
        assert t["{{OOS_WINDOW_MONTHS}}"] == "47"
        assert t["{{STUDY_END}}"] == "November 2025"
        assert t["{{STUDY_MONTHS}}"] == "281"

    def test_live_oos_window_in_table(self):
        """End-to-end: 287-obs strategy_cache (live) ->
        OOS_WINDOW_MONTHS resolves to 53, STUDY_END to
        'May 2026'."""
        from tools.numeric_substitution import (
            get_substitution_table,
        )
        sc = {
            "_n_observations": 287,
            "BENCHMARK": {
                "monthly_returns": [
                    ["2002-07-31", 0.01],
                    ["2026-05-31", 0.02],
                ],
            },
        }
        t = get_substitution_table(
            "test_live_287_v1", sc, None,
            hash_verified=True)
        assert t["{{OOS_WINDOW_MONTHS}}"] == "53"
        assert t["{{STUDY_END}}"] == "May 2026"
        assert t["{{STUDY_MONTHS}}"] == "287"

    def test_explicit_kwarg_overrides_derivation(self):
        """An operator-supplied study_end kwarg wins over
        the cache-derived value."""
        from tools.numeric_substitution import (
            get_substitution_table,
        )
        sc = {
            "_n_observations": 281,
            "BENCHMARK": {"monthly_returns": [
                ["2025-12-31", 0.01]]},
        }
        t = get_substitution_table(
            "test_explicit_override_v1", sc, None,
            study_end="Custom End Date",
            hash_verified=True)
        # The explicit kwarg respected.
        assert t["{{STUDY_END}}"] == "Custom End Date"
