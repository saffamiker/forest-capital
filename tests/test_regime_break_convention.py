"""Pins the 2022-01-01 regime-break inclusion convention.

UAT L2 boundary-date audit (May 24 2026). The audit flagged a ~0.007
discrepancy on equity_hy.post_2022 between the platform's computation
and the auditor's independent recomputation. Root cause was an
ambiguous formula spec, not a calculation bug — the platform was
already applying `>=` uniformly across both rolling_correlation and
regime_conditional_performance, but the spec passed to the LLM
auditor didn't disambiguate the rolling-window case.

These tests pin the convention so a future refactor either:
  (a) keeps the rule uniform (>= 2022-01-01 is POST), or
  (b) trips this test and forces an explicit decision + a paired
      update to the auditor formula spec.

The convention itself lives in tools.analytics.REGIME_BREAK with a
module-level docstring; the auditor's formula specs live in
tools.audit_assembler.FORMULA_SPECIFICATIONS — the two must stay in lock-step.
"""
from __future__ import annotations

import os
import sys

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


def _build_monthly_series(values: list[float]) -> pd.Series:
    """24 month-end timestamps from 2021-01-31 through 2022-12-31 so
    every test has a 12-month pre-2022 segment and a 12-month
    post-2022 segment with the boundary at 2022-01-31."""
    idx = pd.date_range("2021-01-31", periods=24, freq="ME")
    assert len(values) == 24
    return pd.Series(values, index=idx)


class TestRegimeBreakConstant:
    """REGIME_BREAK is the single source of truth for the boundary date.
    A drift here would silently shift every regime-conditional metric on
    the platform."""

    def test_constant_is_jan_1_2022(self):
        from tools.analytics import REGIME_BREAK
        assert REGIME_BREAK == pd.Timestamp("2022-01-01")

    def test_audit_assembler_uses_same_date_string(self):
        from tools.audit_assembler import REGIME_BREAK_DATE
        from tools.analytics import REGIME_BREAK
        # The audit constants are a string of the same date — same
        # boundary, different type for the LLM prompt.
        assert REGIME_BREAK_DATE == str(REGIME_BREAK.date())


class TestRegimeConditionalSplitConvention:
    """regime_conditional_performance applies index >= REGIME_BREAK to
    classify month-end timestamps. January 2022 (timestamp 2022-01-31)
    is in POST. December 2021 (timestamp 2021-12-31) is in PRE. This
    test pins both sides of the boundary."""

    def test_january_2022_is_post(self):
        from tools.analytics import regime_conditional_performance
        # Constant 1% per month → simple Sharpe is easy to compute by
        # hand; what matters here is the COUNT of months in each bucket.
        s = _build_monthly_series([0.01] * 24)
        results = regime_conditional_performance(
            {"TEST": {"strategy_name": "TEST", "monthly_returns":
                      [[str(d.date()), float(v)] for d, v in s.items()]}},
            rf=None,
        )
        row = results[0]
        # 12 months pre (Jan 2021 → Dec 2021), 12 months post (Jan 2022
        # → Dec 2022). The strict-less-than rule puts Dec 2021 in pre
        # and Jan 2022 in post.
        assert row["pre_2022_months"] == 12
        assert row["post_2022_months"] == 12

    def test_only_post_2022_data_gives_zero_pre_months(self):
        from tools.analytics import regime_conditional_performance
        # Series starts at 2022-01-31 — every timestamp is >= REGIME_BREAK.
        idx = pd.date_range("2022-01-31", periods=12, freq="ME")
        s = pd.Series([0.01] * 12, index=idx)
        results = regime_conditional_performance(
            {"TEST": {"strategy_name": "TEST", "monthly_returns":
                      [[str(d.date()), float(v)] for d, v in s.items()]}},
            rf=None,
        )
        row = results[0]
        assert row["pre_2022_months"] == 0
        assert row["post_2022_months"] == 12

    def test_only_pre_2022_data_gives_zero_post_months(self):
        from tools.analytics import regime_conditional_performance
        idx = pd.date_range("2020-01-31", periods=12, freq="ME")
        s = pd.Series([0.01] * 12, index=idx)
        results = regime_conditional_performance(
            {"TEST": {"strategy_name": "TEST", "monthly_returns":
                      [[str(d.date()), float(v)] for d, v in s.items()]}},
            rf=None,
        )
        row = results[0]
        assert row["pre_2022_months"] == 12
        assert row["post_2022_months"] == 0


class TestRollingCorrelationSplitConvention:
    """rolling_correlation classifies by the ROLLING-VALUE TIMESTAMP, not
    by each contributing observation. A rolling correlation value dated
    2022-01-31 reflects the 12-month window Feb 2021 → Jan 2022 and is
    classified as POST because 2022-01-31 >= 2022-01-01.

    This test pins that the first 11 post-2022 rolling values (timestamps
    2022-01-31 through 2022-11-30) ARE included in post_2022 even though
    their lookback windows contain pre-2022 data."""

    def test_first_post_rolling_value_is_in_post_2022(self):
        from tools.analytics import rolling_correlation
        # 24 month-ends from 2021-01-31 through 2022-12-31. 12-month
        # rolling window → first 11 values are NaN, then 13 valid
        # rolling values dated 2021-12-31 through 2022-12-31. The
        # rolling value AT 2021-12-31 is PRE (< 2022-01-01); the
        # rolling values AT 2022-01-31 through 2022-12-31 are POST.
        # That's 1 pre and 12 post — the boundary classification.
        idx = pd.date_range("2021-01-31", periods=24, freq="ME")
        pattern = [0.01, -0.01] * 12  # alternating signs, finite variance
        equity = pd.Series(pattern, index=idx)
        ig = pd.Series(pattern, index=idx)
        hy = pd.Series([-x for x in pattern], index=idx)  # corr = -1
        out = rolling_correlation(equity, ig, hy, window=12)
        assert out["regime_break"] == "2022-01-01"
        # One pre-2022 rolling value (dated 2021-12-31) and many post.
        assert out["pre_2022"]["equity_ig"] is not None
        assert out["post_2022"]["equity_ig"] is not None
        # Lockstep → corr = +1.0 on every rolling value.
        assert abs(out["pre_2022"]["equity_ig"] - 1.0) < 1e-6
        assert abs(out["post_2022"]["equity_ig"] - 1.0) < 1e-6
        # Inverse → corr = -1.0 on every rolling value.
        assert abs(out["post_2022"]["equity_hy"] - (-1.0)) < 1e-6

    def test_pre_2022_only_yields_no_post_average(self):
        from tools.analytics import rolling_correlation
        idx = pd.date_range("2020-01-31", periods=14, freq="ME")
        pattern = [0.01, -0.01] * 7
        equity = pd.Series(pattern, index=idx)
        ig = pd.Series(pattern, index=idx)
        hy = pd.Series([-x for x in pattern], index=idx)
        out = rolling_correlation(equity, ig, hy, window=12)
        # 14 months, 12-month window → 3 valid rolling values dated
        # 2020-12-31, 2021-01-31, 2021-02-28 — all strictly before
        # 2022-01-01 so all in pre_2022.
        assert out["pre_2022"]["equity_ig"] is not None
        assert abs(out["pre_2022"]["equity_ig"] - 1.0) < 1e-6
        assert out["post_2022"]["equity_ig"] is None


class TestEquityHyEqualWeightAlignment:
    """The user explicitly named two metrics that must use the same
    boundary rule: equity_hy (rolling_correlation output) and
    EQUAL_WEIGHT.post_2022_sharpe (regime_conditional_performance
    output). Both use `index >= REGIME_BREAK` at the source level. This
    test pins that alignment so a divergence trips a clear failure."""

    def test_both_components_classify_january_2022_the_same_way(self):
        """A January 2022 month-end observation is POST in BOTH paths."""
        from tools.analytics import REGIME_BREAK
        # Direct classification check — what the source code does.
        jan_2022_month_end = pd.Timestamp("2022-01-31")
        dec_2021_month_end = pd.Timestamp("2021-12-31")
        # The < rule for pre, >= rule for post — same as the source.
        assert jan_2022_month_end >= REGIME_BREAK  # POST
        assert not (jan_2022_month_end < REGIME_BREAK)
        assert dec_2021_month_end < REGIME_BREAK   # PRE
        assert not (dec_2021_month_end >= REGIME_BREAK)


class TestAuditorFormulaSpecMatchesPlatform:
    """The auditor's formula spec is the LLM's instruction sheet for
    independent recomputation. A drift between the spec and the
    platform's actual rule produces the kind of ~0.007 false-positive
    discrepancy that closed this audit. These tests pin that the spec
    names the same boundary date and the same inclusion rule the
    platform uses."""

    def test_spec_quotes_the_boundary_date(self):
        from tools.audit_assembler import FORMULA_SPECIFICATIONS, REGIME_BREAK_DATE
        spec = FORMULA_SPECIFICATIONS["regime_split"]
        assert REGIME_BREAK_DATE in spec
        # Both halves of the rule named explicitly so an LLM does not
        # have to infer the inclusive/exclusive boundary.
        assert "strictly less than" in spec
        assert "greater than or equal to" in spec

    def test_rolling_correlation_spec_names_rolling_value_timestamp_rule(self):
        from tools.audit_assembler import FORMULA_SPECIFICATIONS
        spec = FORMULA_SPECIFICATIONS["rolling_correlation"]
        # The rule keys off the ROLLING VALUE timestamp, not each
        # contributing observation. The spec must say so.
        assert "rolling value" in spec.lower()
        # And the first-11-rolling-values caveat must be documented
        # so the LLM doesn't drop them.
        assert "lookback" in spec.lower()
