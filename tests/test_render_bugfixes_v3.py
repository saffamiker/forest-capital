"""
tests/test_render_bugfixes_v3.py

Third wave of Render production bugfixes:

  Fix 1 — Performance Attribution Waterfall rendered all zeros.
          tools/chart_data.py _compute_attribution had two real bugs:
            (a) `interaction = active_return - allocation - selection`
                where `selection = active_return - allocation` reduces
                to `interaction = 0` algebraically, on every input.
            (b) The allocation effect was a synthetic guess
                (`avg_bond_wt × benchmark_mean × -0.4`) instead of using
                the actual bond return series.
          The fix passes `ig_monthly` through from compute_chart_data,
          computes allocation = w_bond × (R_bond - R_benchmark), and
          documents interaction = 0 explicitly (full BHB requires
          per-asset-class component returns we don't track at this layer).

  Fix 2 — Explainer Grok via OpenRouter emits JSON with missing
          commas between key/value pairs. _safe_json_parse now repairs
          three common malformations (missing comma between pairs,
          trailing comma before } or ], single-quoted keys) before the
          second-pass parse. The per-request "json_parse_failed" warning
          was removed — it floods Render logs when a model regression
          hits every chart hover; the silent fallback IS the signal.

  Fix 3 — FF factors incremental fetch fired on every request after
          the initial load, even when no new month was available
          upstream. The staleness check used (today - first_of_next_month)
          which crosses 35 days mid-month — but Ken French only
          publishes new months around the 15th. The new rule computes
          months_behind directly in YYYYMM space and only fetches when
          the gap exceeds 35 days (months_behind >= 2).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date as _date
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


# ── Fix 1: Brinson attribution ───────────────────────────────────────────────

class TestBrinsonAttribution:
    """The previous _compute_attribution produced all-zero rows in
    production. These tests pin the failure modes that produced that
    symptom AND the new behaviour."""

    def test_returns_zero_when_aligned_series_too_short(self):
        """Existing contract: < 12 aligned months → zeros across the board."""
        from tools.chart_data import _compute_attribution
        idx = pd.date_range("2024-01-31", periods=6, freq="ME")
        s = pd.Series([0.01] * 6, index=idx)
        b = pd.Series([0.005] * 6, index=idx)
        bond = pd.Series([0.002] * 6, index=idx)

        out = _compute_attribution(s, b, bond, avg_eq_wt=0.6, avg_bond_wt=0.4)
        assert out == {"allocation": 0.0, "selection": 0.0,
                       "interaction": 0.0, "total_active": 0.0}

    def test_non_zero_allocation_when_bond_outperforms_benchmark(self):
        """The headline fix: with a bond series passed in, allocation
        should reflect w_bond × (R_bond - R_benchmark). Previously the
        function ignored the actual bond returns and produced ~0."""
        from tools.chart_data import _compute_attribution

        idx = pd.date_range("2024-01-31", periods=24, freq="ME")
        # Benchmark mean: 1.0% monthly = 12% annualised
        bm = pd.Series([0.01] * 24, index=idx)
        # Bond mean: 0.3% monthly = 3.6% annualised → R_bond - R_bm = -8.4%
        bond = pd.Series([0.003] * 24, index=idx)
        # Strategy is 60/40 → mean roughly 0.6×1.0% + 0.4×0.3% = 0.72%
        strat = pd.Series([0.0072] * 24, index=idx)

        out = _compute_attribution(
            strat, bm, bond, avg_eq_wt=0.6, avg_bond_wt=0.4, strategy_name="TEST",
        )

        # Expected allocation: 0.4 × (0.036 - 0.12) = -0.0336
        assert abs(out["allocation"] - (-0.0336)) < 0.001, (
            f"Allocation should be w_bond × (R_bond - R_bm); got {out['allocation']}"
        )
        # active_return = mean(strat - bm) × 12 = mean(-0.28%) × 12 = -0.0336
        assert abs(out["total_active"] - (-0.0336)) < 0.001
        # Selection is the residual — for this exact 60/40 case where
        # the strategy IS the weighted average, residual is ~0.
        assert abs(out["selection"]) < 0.001
        # Interaction is documented as 0 in the simplified two-asset model.
        assert out["interaction"] == 0.0

    def test_non_zero_selection_when_strategy_outperforms_weighted_components(self):
        """A strategy beating the weighted-average of its asset-class
        proxies has a non-zero selection effect (the residual). This
        test pins that selection is non-zero for the case where the
        backtester finds alpha beyond pure allocation."""
        from tools.chart_data import _compute_attribution

        idx = pd.date_range("2024-01-31", periods=24, freq="ME")
        bm = pd.Series([0.01] * 24, index=idx)
        bond = pd.Series([0.003] * 24, index=idx)
        # Strategy returns 1.0% monthly — better than the 0.72% a 60/40
        # mix of bm + bond would produce. The excess shows up as selection.
        strat = pd.Series([0.01] * 24, index=idx)

        out = _compute_attribution(
            strat, bm, bond, avg_eq_wt=0.6, avg_bond_wt=0.4,
        )
        # active_return = 0 (matches benchmark exactly)
        # allocation = 0.4 × (0.036 - 0.12) = -0.0336
        # selection = 0 - (-0.0336) = +0.0336
        assert abs(out["selection"] - 0.0336) < 0.001
        assert out["total_active"] == 0.0

    def test_handles_missing_bond_series_gracefully(self):
        """When bond_returns is None (or empty), allocation falls back
        to 0 — preserves the old "no crash" contract."""
        from tools.chart_data import _compute_attribution

        idx = pd.date_range("2024-01-31", periods=24, freq="ME")
        bm = pd.Series([0.01] * 24, index=idx)
        strat = pd.Series([0.012] * 24, index=idx)

        out = _compute_attribution(
            strat, bm, bond_returns=None,
            avg_eq_wt=1.0, avg_bond_wt=0.0,
        )
        assert out["allocation"] == 0.0
        # Selection takes the full active return when no allocation
        # contribution can be computed.
        assert abs(out["selection"] - (0.002 * 12)) < 0.001

    def test_diagnostic_log_fires_with_raw_attribution_values(self, capsys, caplog):
        """Production failure mode: the waterfall renders blank but
        we can't tell whether (a) inputs were empty or (b) the math
        zeroed out. The new diagnostic log surfaces both.

        Checks both capsys and caplog: structlog routes to stdout
        when run in isolation and through stdlib logging when another
        test in the suite has configured the chain first."""
        import logging
        from tools.chart_data import _compute_attribution

        idx = pd.date_range("2024-01-31", periods=24, freq="ME")
        bm = pd.Series([0.01] * 24, index=idx)
        bond = pd.Series([0.003] * 24, index=idx)
        strat = pd.Series([0.011] * 24, index=idx)

        with caplog.at_level(logging.INFO, logger="tools.chart_data"):
            _compute_attribution(
                strat, bm, bond, avg_eq_wt=0.6, avg_bond_wt=0.4, strategy_name="DIAG",
            )

        captured = capsys.readouterr()
        stream_text = captured.out + captured.err
        record_text = " ".join(str(r.__dict__) + " " + str(r.msg) for r in caplog.records)
        log_text = stream_text + record_text
        assert "attribution_computed" in log_text
        assert "DIAG" in log_text


# ── Fix 2: Lenient JSON parsing ──────────────────────────────────────────────

class TestJsonRepair:
    """The Grok-3-mini-via-OpenRouter JSON often has a missing comma
    between key/value pairs. _safe_json_parse must repair that before
    the second parse attempt."""

    def test_missing_comma_between_pairs_recovered(self):
        from agents.explainer_agent import _safe_json_parse
        # Real shape from Render logs: missing comma after "value1"
        malformed = (
            '{\n'
            '  "first": "value1"\n'
            '  "second": "value2"\n'
            '}'
        )
        out = _safe_json_parse(malformed, fallback={})
        assert out == {"first": "value1", "second": "value2"}

    def test_missing_comma_with_nested_object(self):
        from agents.explainer_agent import _safe_json_parse
        malformed = (
            '{\n'
            '  "outer": {"a": 1, "b": 2}\n'
            '  "next": "x"\n'
            '}'
        )
        out = _safe_json_parse(malformed, fallback={})
        assert out == {"outer": {"a": 1, "b": 2}, "next": "x"}

    def test_trailing_comma_before_close_brace(self):
        from agents.explainer_agent import _safe_json_parse
        malformed = '{"a": 1, "b": 2,}'
        out = _safe_json_parse(malformed, fallback={})
        assert out == {"a": 1, "b": 2}

    def test_single_quoted_keys_recovered(self):
        from agents.explainer_agent import _safe_json_parse
        malformed = "{'a': 1, 'b': 2}"
        out = _safe_json_parse(malformed, fallback={})
        assert out == {"a": 1, "b": 2}

    def test_silent_fallback_when_unrecoverable(self):
        """Truly malformed JSON (no closing brace) falls back silently —
        no warning log line, no exception. The Explainer fires on every
        chart hover; logging on each failure would flood Render."""
        from agents.explainer_agent import _safe_json_parse
        malformed = '{"this is": "totally broken'

        fallback = {"empty": True}
        out = _safe_json_parse(malformed, fallback=fallback)
        assert out == fallback

    def test_no_warning_log_on_parse_failure(self, capsys):
        """The 'explainer_json_parse_failed' warning was removed because
        a Grok regression that breaks JSON on every request would flood
        Render logs. This test pins that silence."""
        from agents.explainer_agent import _safe_json_parse
        _safe_json_parse("not valid json at all", fallback={})
        captured = capsys.readouterr()
        assert "explainer_json_parse_failed" not in captured.out
        assert "explainer_json_parse_failed" not in captured.err

    def test_happy_path_still_works(self):
        from agents.explainer_agent import _safe_json_parse
        assert _safe_json_parse('{"a": 1}', fallback={}) == {"a": 1}
        assert _safe_json_parse('```json\n{"a": 1}\n```', fallback={}) == {"a": 1}


# ── Fix 3: FF staleness window ───────────────────────────────────────────────

class TestFFStalenessWindow:
    """The fetch was firing every request once db_last was a couple of
    weeks past the next month boundary. New rule: months_behind in
    YYYYMM space, only fetch when the gap > 35 days approx."""

    @staticmethod
    def _seed_db(monkeypatch, db_last_yyyymm: int):
        """Stub _read_ff_factors_from_db to return one row at the given month."""
        rows = [(db_last_yyyymm, 0.5, 0.1, 0.2, 0.02)]
        monkeypatch.setattr(
            "tools.data_fetcher._read_ff_factors_from_db", lambda: rows,
        )
        return rows

    @staticmethod
    def _spy_fetch(monkeypatch):
        calls: list[int] = []

        def _stub() -> pd.DataFrame:
            calls.append(1)
            # Return whatever's in the DB unchanged so the writer is a no-op.
            return pd.DataFrame(
                {"Mkt-RF": [0.5], "SMB": [0.1], "HML": [0.2], "RF": [0.02]},
                index=[202603],
            )

        monkeypatch.setattr("tools.data_fetcher._kenfrench_direct_fetch", _stub)
        return calls

    def test_one_month_behind_does_not_fetch(self, monkeypatch):
        """db_last=202603 (March), today=202604 (April).
        months_behind=1 → ~30 days → no fetch. Prevents the bug where
        a fetch fired every April request even though KF wouldn't have
        published April data yet."""
        from tools.data_fetcher import _load_ff_factors_with_cache

        self._seed_db(monkeypatch, 202603)
        calls = self._spy_fetch(monkeypatch)

        with _freeze_today(monkeypatch, 2026, 4, 20):
            _load_ff_factors_with_cache()

        assert len(calls) == 0, (
            f"With db_last=202603 and today=2026-04-20 (1 month behind), "
            f"the fetch must NOT fire; got {len(calls)} calls"
        )

    def test_two_months_behind_fetches(self, monkeypatch):
        """db_last=202603, today=2026-05-14 → months_behind=2 → ~60
        days → fetch. By mid-May, FF should have April data."""
        from tools.data_fetcher import _load_ff_factors_with_cache

        self._seed_db(monkeypatch, 202603)
        calls = self._spy_fetch(monkeypatch)

        with _freeze_today(monkeypatch, 2026, 5, 14):
            _load_ff_factors_with_cache()

        assert len(calls) == 1, (
            f"With db_last=202603 and today=2026-05-14 (2 months behind), "
            f"the fetch must fire exactly once; got {len(calls)} calls"
        )

    def test_same_month_does_not_fetch(self, monkeypatch):
        """db_last=202604, today=2026-04-30 → months_behind=0 → no fetch.
        The bug we're guarding against: previous code computed 'first of
        next month' = 2026-05-01, days_stale=-1, but signed math could
        misbehave on edge cases."""
        from tools.data_fetcher import _load_ff_factors_with_cache

        self._seed_db(monkeypatch, 202604)
        calls = self._spy_fetch(monkeypatch)

        with _freeze_today(monkeypatch, 2026, 4, 30):
            _load_ff_factors_with_cache()

        assert len(calls) == 0

    def test_year_boundary_handled(self, monkeypatch):
        """db_last=202512 (Dec 2025), today=2026-02-15 → months_behind=2 → fetch.
        Verifies YYYYMM arithmetic handles year rollover correctly."""
        from tools.data_fetcher import _load_ff_factors_with_cache

        self._seed_db(monkeypatch, 202512)
        calls = self._spy_fetch(monkeypatch)

        with _freeze_today(monkeypatch, 2026, 2, 15):
            _load_ff_factors_with_cache()

        assert len(calls) == 1


import contextlib
import datetime as _dt


@contextlib.contextmanager
def _freeze_today(monkeypatch, year: int, month: int, day: int):
    """Patches datetime.date.today so the FF staleness check sees a
    deterministic 'today'. The staleness logic imports `date` locally
    via `from datetime import date as _date`, so patching the
    `datetime.date` class directly catches that import."""
    target = _dt.date(year, month, day)

    class _FrozenDate(_dt.date):
        @classmethod
        def today(cls):
            return target

    monkeypatch.setattr(_dt, "date", _FrozenDate)
    try:
        yield target
    finally:
        # monkeypatch handles teardown — nothing to do here
        pass
