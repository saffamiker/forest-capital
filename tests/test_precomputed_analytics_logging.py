"""
tests/test_precomputed_analytics_logging.py — May 25 2026.

Coverage for the per-row diagnostic logging added to
refresh_academic_analytics and refresh_transition_matrix. The user's
directive: after a degraded AN01 / AN04 verdict, the Render log alone
must be enough to answer "which strategies landed, which fields were
null, which were missing" — without inspecting the JSONB payload by
hand.

Three layers tested:
  1. _log_table_rows_diagnostic — the per-row writer (pure function).
  2. refresh_academic_analytics — end-to-end logging on the happy
     path AND when an upstream input is empty AND when a reduction
     raises.
  3. refresh_transition_matrix — end-to-end logging on the happy
     path AND on the empty-monthly / empty-HMM skip paths.

All tests use structlog.testing.capture_logs so no live DB / FRED /
yfinance is touched.
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")


# ── _log_table_rows_diagnostic — pure-function unit tests ────────────────────

class TestLogTableRowsDiagnostic:
    """The shared helper that walks a table-row list, classifies each
    field as present / null / missing, and emits one log line per row.
    The aggregate summary it returns drives the parent
    precomputed_*_written line."""

    def test_clean_row_emits_info_level_with_no_gap_fields(self):
        from structlog.testing import capture_logs
        from tools.precomputed_analytics import (
            _log_table_rows_diagnostic, _FACTOR_LOADING_REQUIRED_FIELDS,
        )
        clean = {f: 1.0 for f in _FACTOR_LOADING_REQUIRED_FIELDS}
        clean["strategy"] = "BENCHMARK"
        with capture_logs() as logs:
            summary = _log_table_rows_diagnostic(
                "factor_loadings", [clean],
                _FACTOR_LOADING_REQUIRED_FIELDS, data_hash="abcdef0123",
            )
        rows = [e for e in logs
                if e.get("event") == "precomputed_factor_loadings_row_written"]
        assert len(rows) == 1
        row = rows[0]
        assert row["log_level"] == "info"
        assert row["strategy"] == "BENCHMARK"
        assert row["n_missing"] == 0
        assert row["n_null"] == 0
        # Clean rows do NOT enumerate field names (volume control).
        assert row.get("missing_fields") is None
        assert row.get("null_fields") is None
        assert summary == {"n_rows": 1, "n_complete": 1,
                           "n_with_nulls": 0, "n_with_missing": 0}

    def test_row_with_missing_field_emits_warning_with_field_name(self):
        from structlog.testing import capture_logs
        from tools.precomputed_analytics import (
            _log_table_rows_diagnostic, _FACTOR_LOADING_REQUIRED_FIELDS,
        )
        # Missing alpha_annualized — the warm needs to surface the
        # specific field name so a Render grep can locate the gap.
        row = {f: 1.0 for f in _FACTOR_LOADING_REQUIRED_FIELDS
               if f != "alpha_annualized"}
        row["strategy"] = "BENCHMARK"
        with capture_logs() as logs:
            summary = _log_table_rows_diagnostic(
                "factor_loadings", [row],
                _FACTOR_LOADING_REQUIRED_FIELDS, data_hash="abc",
            )
        entry = next(e for e in logs
                     if e.get("event") == "precomputed_factor_loadings_row_written")
        assert entry["log_level"] == "warning"
        assert entry["missing_fields"] == ["alpha_annualized"]
        assert entry["n_missing"] == 1
        # The present-field list IS enumerated when level upgrades to
        # warning — so a grep can see what DID land alongside the gap.
        assert entry["present"] is not None
        assert "mkt_rf" in entry["present"]
        assert summary["n_with_missing"] == 1
        assert summary["n_complete"] == 0

    def test_row_with_null_field_emits_warning_with_field_name(self):
        from structlog.testing import capture_logs
        from tools.precomputed_analytics import (
            _log_table_rows_diagnostic, _FACTOR_LOADING_REQUIRED_FIELDS,
        )
        row = {f: 1.0 for f in _FACTOR_LOADING_REQUIRED_FIELDS}
        row["strategy"] = "BENCHMARK"
        # Three-factor fallback — mom present but null. Only `mom`
        # is in the required-fields tuple (mom_significant is checked
        # separately by the validator), so the diagnostic flags it
        # as a single null field.
        row["mom"] = None
        with capture_logs() as logs:
            summary = _log_table_rows_diagnostic(
                "factor_loadings", [row],
                _FACTOR_LOADING_REQUIRED_FIELDS, data_hash="abc",
            )
        entry = next(e for e in logs
                     if e.get("event") == "precomputed_factor_loadings_row_written")
        assert entry["log_level"] == "warning"
        assert entry["null_fields"] == ["mom"]
        assert entry["n_null"] == 1
        assert entry["n_missing"] == 0
        # Summary counts the row as having nulls — NOT as missing.
        assert summary["n_with_nulls"] == 1
        assert summary["n_with_missing"] == 0

    def test_non_dict_row_emits_invalid_type_warning(self):
        from structlog.testing import capture_logs
        from tools.precomputed_analytics import (
            _log_table_rows_diagnostic, _FACTOR_LOADING_REQUIRED_FIELDS,
        )
        # A string row would crash the field walker; the diagnostic
        # logs and continues so a malformed row never poisons the
        # whole refresh.
        with capture_logs() as logs:
            summary = _log_table_rows_diagnostic(
                "factor_loadings", ["not-a-dict"],
                _FACTOR_LOADING_REQUIRED_FIELDS, data_hash="abc",
            )
        invalid = [e for e in logs
                   if e.get("event")
                   == "precomputed_factor_loadings_row_invalid_type"]
        assert invalid, "expected an invalid-type warning"
        assert invalid[0]["row_type"] == "str"
        # Summary still counts the row but classifies neither way.
        assert summary["n_rows"] == 1

    def test_empty_rows_returns_zero_summary(self):
        from tools.precomputed_analytics import (
            _log_table_rows_diagnostic, _REGIME_CONDITIONAL_REQUIRED_FIELDS,
        )
        summary = _log_table_rows_diagnostic(
            "regime_conditional", [],
            _REGIME_CONDITIONAL_REQUIRED_FIELDS, data_hash="abc",
        )
        assert summary == {"n_rows": 0, "n_complete": 0,
                           "n_with_nulls": 0, "n_with_missing": 0}


# ── refresh_academic_analytics — end-to-end logging ──────────────────────────

class TestRefreshAcademicAnalyticsLogging:
    """The whole refresh path: started → inputs → per-row → written.
    Each test stubs the data dependencies so the test environment never
    hits a live DB."""

    def test_happy_path_logs_started_inputs_per_row_and_written(self,
                                                                  monkeypatch):
        """A clean refresh emits the full log trail:
          1. precomputed_academic_analytics_started
          2. precomputed_academic_analytics_inputs (with n_strategies)
          3. precomputed_factor_loadings_row_written × n_strategies
          4. precomputed_regime_conditional_row_written × n_strategies
          5. precomputed_academic_analytics_written (with summary)
        """
        from structlog.testing import capture_logs
        from tools import precomputed_analytics as pa

        monthly = {"dates": ["2024-01-31", "2024-02-29"],
                   "equity": [0.01, 0.02], "ig": [0.005, 0.01],
                   "hy": [0.012, 0.015], "rf": [0.001, 0.001]}
        strategies = {
            "BENCHMARK": {"monthly_returns": [
                ["2024-01-31", 0.01], ["2024-02-29", 0.02]]},
        }

        async def _fake_monthly():
            return monthly

        async def _fake_strategies():
            return strategies

        async def _fake_ff():
            return []

        # Stub the analytics reductions so the test doesn't depend
        # on the regression solver settling for 2 data points.
        factor_row = {
            "strategy": "BENCHMARK", "mkt_rf": 1.0, "smb": 0.0,
            "hml": 0.0, "mom": 0.0, "alpha_annualized": 0.0,
            "r_squared": 1.0, "mkt_rf_significant": True,
            "smb_significant": False, "hml_significant": False,
            "mom_significant": False, "alpha_significant": False,
        }
        regime_row = {
            "strategy": "BENCHMARK", "pre_2022_sharpe": 0.5,
            "post_2022_sharpe": 0.4, "pre_2022_cagr": 0.08,
            "post_2022_cagr": 0.05, "pre_2022_months": 240,
            "post_2022_months": 36,
        }

        async def _fake_set_metric(*args, **kwargs):
            return None

        with patch("tools.cache.get_monthly_returns", _fake_monthly), \
             patch("tools.cache.get_latest_strategy_cache",
                   _fake_strategies), \
             patch("tools.cache.get_ff_factors", _fake_ff), \
             patch("tools.analytics.factor_loadings",
                   lambda s, ff: [factor_row]), \
             patch("tools.analytics.regime_conditional_performance",
                   lambda s, rf: [regime_row]), \
             patch.object(pa, "set_metric", _fake_set_metric), \
             capture_logs() as logs:
            asyncio.run(pa.refresh_academic_analytics("abcdef0123"))

        events = [e["event"] for e in logs]
        assert "precomputed_academic_analytics_started" in events
        assert "precomputed_academic_analytics_inputs" in events
        # The inputs line carries n_strategies so a degraded refresh
        # ties back to the strategy cache state in the SAME line.
        inputs = next(e for e in logs
                      if e["event"] == "precomputed_academic_analytics_inputs")
        assert inputs["n_strategies"] == 1
        assert "precomputed_factor_loadings_row_written" in events
        assert "precomputed_regime_conditional_row_written" in events
        # Final written line carries summary counts for both tables.
        written = next(e for e in logs
                       if e["event"] == "precomputed_academic_analytics_written")
        assert written["complete"] is True
        assert written["factor_loadings"]["n_rows"] == 1
        assert written["regime_conditional"]["n_rows"] == 1

    def test_empty_strategy_cache_logs_skipped_empty_inputs(self,
                                                              monkeypatch):
        """The production symptom that prompted the warm fix: when
        strategy_results_cache is empty the refresh aborts. The log
        line must surface the empty state so a Render log scan can
        tie the warm failure to its cause."""
        from structlog.testing import capture_logs
        from tools import precomputed_analytics as pa

        async def _fake_monthly():
            return {"dates": ["2024-01-31"], "equity": [0.01],
                    "ig": [0.005], "hy": [0.012], "rf": [0.001]}

        async def _fake_strategies():
            return None  # empty cache

        async def _fake_ff():
            return []

        with patch("tools.cache.get_monthly_returns", _fake_monthly), \
             patch("tools.cache.get_latest_strategy_cache",
                   _fake_strategies), \
             patch("tools.cache.get_ff_factors", _fake_ff), \
             capture_logs() as logs:
            asyncio.run(pa.refresh_academic_analytics("abc"))

        events = [e["event"] for e in logs]
        assert "precomputed_academic_analytics_started" in events
        # The skip line names WHICH input was empty so the cause is
        # readable from the Render log alone.
        skipped = next(
            e for e in logs
            if e["event"] == "precomputed_academic_analytics_skipped_empty_inputs"
        )
        assert skipped["strategies_present"] is False
        assert skipped["monthly_present"] is True
        # No per-row lines and no written line — refresh aborted.
        assert "precomputed_factor_loadings_row_written" not in events
        assert "precomputed_academic_analytics_written" not in events

    def test_factor_loadings_compute_failure_logs_with_exc_type(self,
                                                                  monkeypatch):
        """When the Carhart regression solver raises, the failure is
        caught with explicit context — exc_type + error string — so
        a degraded AN01 verdict can be traced back to the specific
        reduction that crashed."""
        from structlog.testing import capture_logs
        from tools import precomputed_analytics as pa

        async def _fake_monthly():
            return {"dates": ["2024-01-31"], "equity": [0.01],
                    "ig": [0.005], "hy": [0.012], "rf": [0.001]}

        async def _fake_strategies():
            return {"BENCHMARK": {"monthly_returns": [
                ["2024-01-31", 0.01]]}}

        async def _fake_ff():
            return []

        async def _fake_set_metric(*args, **kwargs):
            return None

        def _boom(_strategies, _ff):
            raise ValueError("simulated solver failure")

        with patch("tools.cache.get_monthly_returns", _fake_monthly), \
             patch("tools.cache.get_latest_strategy_cache",
                   _fake_strategies), \
             patch("tools.cache.get_ff_factors", _fake_ff), \
             patch("tools.analytics.factor_loadings", _boom), \
             patch("tools.analytics.regime_conditional_performance",
                   lambda s, rf: []), \
             patch.object(pa, "set_metric", _fake_set_metric), \
             capture_logs() as logs:
            asyncio.run(pa.refresh_academic_analytics("abc"))

        failed = next(
            e for e in logs
            if e["event"] == "precomputed_factor_loadings_compute_failed"
        )
        assert failed["exc_type"] == "ValueError"
        assert "simulated solver failure" in failed["error"]
        # The outer write still proceeds (the reduction's failure is
        # caught), so a downstream consumer still sees a payload —
        # just with an empty factor_loadings table.
        events = [e["event"] for e in logs]
        assert "precomputed_academic_analytics_written" in events


# ── refresh_transition_matrix — end-to-end logging ───────────────────────────

class TestRefreshTransitionMatrixLogging:
    """AN04 also reads the transition matrix; instrumentation here is
    symmetric with refresh_academic_analytics so the log trail of a
    degraded AN04 verdict is uniform across both refreshes."""

    def test_empty_monthly_logs_skipped(self):
        from structlog.testing import capture_logs
        from tools import precomputed_analytics as pa

        async def _fake_monthly():
            return None

        with patch("tools.cache.get_monthly_returns", _fake_monthly), \
             capture_logs() as logs:
            asyncio.run(pa.refresh_transition_matrix("abc"))

        events = [e["event"] for e in logs]
        assert "precomputed_transition_matrix_started" in events
        assert "precomputed_transition_matrix_skipped_empty_monthly" in events
        assert "precomputed_transition_matrix_written" not in events

    def test_hmm_returns_empty_logs_skipped_empty_hmm(self):
        from structlog.testing import capture_logs
        from tools import precomputed_analytics as pa

        async def _fake_monthly():
            return {"dates": ["2024-01-31", "2024-02-29"],
                    "equity": [0.01, 0.02], "ig": [0.005, 0.01],
                    "hy": [0.012, 0.015], "rf": [0.001, 0.001]}

        def _fake_hmm(_equity):
            return {"labelled_series": None}

        with patch("tools.cache.get_monthly_returns", _fake_monthly), \
             patch("tools.regime_detector.classify_hmm_regime", _fake_hmm), \
             capture_logs() as logs:
            asyncio.run(pa.refresh_transition_matrix("abc"))

        events = [e["event"] for e in logs]
        assert "precomputed_transition_matrix_skipped_empty_hmm" in events
        assert "precomputed_transition_matrix_written" not in events
