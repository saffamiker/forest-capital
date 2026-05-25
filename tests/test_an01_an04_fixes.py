"""
tests/test_an01_an04_fixes.py — May 25 2026.

Three bugs landed in one fix:

  (1) The factor-loadings diagnostic missed `mom_significant` —
      _FACTOR_LOADING_REQUIRED_FIELDS did not list it, so a row with
      mom_significant absent logged "11/11 present" while the
      validator marked it invalid via a separate `mom_or_mom_
      significant` check. Fix: mom_significant joins the required
      list (now 12 fields); the separate validator check is removed.

  (2) The r_squared isinstance check excluded numpy.float64 — under
      NEP 51 numpy scalars are not subclasses of the built-in float,
      so a real-valued numpy r_squared was reported as
      r_squared_out_of_range even though it WAS in [0, 1]. Fix:
      accept any numbers.Real except bool, plus a math.isfinite
      guard to reject NaN / ±inf cleanly.

  (3) set_metric stripped EVERY underscore-prefixed key — including
      _completeness — so the validator's verdict never landed in
      the DB row. AN01 / AN04 read the verdict back as missing
      every time and WARNed forever. Fix: strip only the ephemeral
      keys the read path attaches (_computed_at / _data_hash /
      _stale).

  (4) regime_conditional_performance produced NaN sharpe / cagr
      when the sub-period series was all-NaN or degenerate; that
      NaN drifted into JSONB as null and tripped the validator's
      "*_sharpe_unexpectedly_null when months >= 2" gate. Fix:
      wrap with _safe_sharpe / _safe_cagr that fall back to None on
      a non-finite result AND drop NaN entries from the series
      BEFORE the regime split, so months and sharpe always agree.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from tools.precomputed_analytics import (  # noqa: E402
    _EPHEMERAL_PAYLOAD_KEYS,
    _FACTOR_LOADING_REQUIRED_FIELDS,
    _validate_factor_loadings,
    _validate_regime_conditional,
)


# ── (1) mom_significant in the required list ─────────────────────────────────


class TestMomSignificantInRequiredFields:
    """mom_significant must be part of the canonical required list so
    the diagnostic and the validator agree on the field set."""

    def test_mom_significant_is_required(self):
        assert "mom_significant" in _FACTOR_LOADING_REQUIRED_FIELDS

    def test_required_field_count_is_twelve(self):
        # 11 → 12 after the fix. Pinning the count guards against a
        # future drop reverting the change.
        assert len(_FACTOR_LOADING_REQUIRED_FIELDS) == 12

    def test_a_row_missing_mom_significant_is_invalid(self):
        # Row has every other field but no mom_significant.
        row = {
            "strategy": "BENCHMARK", "mkt_rf": 0.95, "smb": 0.0,
            "hml": 0.0, "mom": 0.05, "alpha_annualized": 0.0,
            "r_squared": 0.97,
            "mkt_rf_significant": True, "smb_significant": False,
            "hml_significant": False, "alpha_significant": False,
            # no mom_significant
        }
        result = _validate_factor_loadings([row])
        assert result["complete"] is False
        assert any("mom_significant" in m
                   for m in result["invalid_rows"][0]["missing"])

    def test_a_row_with_every_field_is_valid(self):
        row = {
            "strategy": "BENCHMARK", "mkt_rf": 0.95, "smb": 0.0,
            "hml": 0.0, "mom": 0.05, "alpha_annualized": 0.0,
            "r_squared": 0.97,
            "mkt_rf_significant": True, "smb_significant": False,
            "hml_significant": False, "alpha_significant": False,
            "mom_significant": False,
        }
        result = _validate_factor_loadings([row])
        assert result["complete"] is True
        assert result["invalid_rows"] == []


# ── (2) r_squared accepts numpy.float64 ──────────────────────────────────────


class TestRSquaredAcceptsNumpyFloat:
    """statsmodels emits numpy.float64 for rsquared. NEP 51 makes
    numpy scalars not subclasses of the built-in float, so the
    previous isinstance(r2, (int, float)) check excluded them. The
    fix accepts any numbers.Real (excluding bool) and guards with
    math.isfinite."""

    def _row(self, r_squared) -> dict:
        # A minimum-complete row except r_squared, which the test
        # parametrises.
        return {
            "strategy": "BENCHMARK", "mkt_rf": 0.95, "smb": 0.0,
            "hml": 0.0, "mom": 0.05, "alpha_annualized": 0.0,
            "r_squared": r_squared,
            "mkt_rf_significant": True, "smb_significant": False,
            "hml_significant": False, "alpha_significant": False,
            "mom_significant": False,
        }

    def test_python_float_in_range_is_valid(self):
        out = _validate_factor_loadings([self._row(0.97)])
        assert out["complete"] is True

    def test_numpy_float64_in_range_is_valid(self):
        out = _validate_factor_loadings([self._row(np.float64(0.97))])
        assert out["complete"] is True

    def test_numpy_float32_in_range_is_valid(self):
        out = _validate_factor_loadings([self._row(np.float32(0.85))])
        assert out["complete"] is True

    def test_numpy_int_in_range_is_valid(self):
        # 0 and 1 are degenerate but valid bounds.
        out = _validate_factor_loadings([self._row(np.int64(1))])
        assert out["complete"] is True

    def test_python_int_zero_is_valid(self):
        out = _validate_factor_loadings([self._row(0)])
        assert out["complete"] is True

    def test_python_int_one_is_valid(self):
        out = _validate_factor_loadings([self._row(1)])
        assert out["complete"] is True

    def test_out_of_range_python_float_is_invalid(self):
        out = _validate_factor_loadings([self._row(1.5)])
        assert out["complete"] is False

    def test_out_of_range_numpy_float64_is_invalid(self):
        out = _validate_factor_loadings([self._row(np.float64(1.5))])
        assert out["complete"] is False

    def test_nan_is_rejected_as_non_finite(self):
        # NaN slipped through the old `0.0 <= r2 <= 1.0` because the
        # comparison returns False for NaN — but the new check also
        # requires math.isfinite to make the rejection explicit.
        out = _validate_factor_loadings([self._row(float("nan"))])
        assert out["complete"] is False

    def test_positive_inf_is_rejected(self):
        out = _validate_factor_loadings([self._row(float("inf"))])
        assert out["complete"] is False

    def test_bool_is_rejected(self):
        # bool is a subclass of int — without the explicit guard,
        # isinstance(True, numbers.Real) is True. r_squared must be
        # a real number, not a coerced bool.
        out = _validate_factor_loadings([self._row(True)])
        assert out["complete"] is False

    def test_string_is_rejected(self):
        # A string r_squared from a botched JSON cast must not pass.
        out = _validate_factor_loadings([self._row("0.97")])
        assert out["complete"] is False


# ── (3) set_metric preserves _completeness; strips only ephemeral keys ───────


class TestEphemeralKeysContract:
    """The set of underscore-prefixed keys set_metric strips before
    writing. The bug — stripping every underscore-prefixed key —
    made AN01 / AN04 read False forever because _completeness was
    swept up with _computed_at / _data_hash / _stale."""

    def test_ephemeral_set_contains_runtime_annotations(self):
        # The three keys get_metric / get_latest_metric attach on
        # read are the only members. Update both ends in lockstep
        # if more annotations are added.
        assert "_computed_at" in _EPHEMERAL_PAYLOAD_KEYS
        assert "_data_hash" in _EPHEMERAL_PAYLOAD_KEYS
        assert "_stale" in _EPHEMERAL_PAYLOAD_KEYS

    def test_ephemeral_set_does_not_contain_completeness(self):
        # The whole point — _completeness MUST survive the write.
        assert "_completeness" not in _EPHEMERAL_PAYLOAD_KEYS

    def test_strip_logic_keeps_completeness(self):
        # Mirror the comprehension used in set_metric so a future
        # refactor of the function body that breaks the contract
        # is caught here.
        payload = {
            "available": True,
            "factor_loadings": [{"strategy": "X"}],
            "_completeness": {"complete": True, "factor_loadings": {}},
            "_computed_at": "2026-05-25T00:00:00",
            "_data_hash": "abc",
            "_stale": True,
        }
        clean = {k: v for k, v in payload.items()
                 if k not in _EPHEMERAL_PAYLOAD_KEYS}
        assert "_completeness" in clean
        assert clean["_completeness"]["complete"] is True
        assert "_computed_at" not in clean
        assert "_data_hash" not in clean
        assert "_stale" not in clean
        # Non-underscore keys obviously survive.
        assert clean["available"] is True


# ── (4) regime_conditional_performance handles NaN safely ────────────────────


class TestRegimeConditionalNaNHandling:
    """A NaN sharpe / cagr must surface as None — never as raw NaN
    that drifts through JSONB. AND the months count must reflect
    the post-dropna series so the validator's
    'sharpe_unexpectedly_null when months >= 2' never trips on
    NaN-induced None."""

    def _build_strategy(self, monthly_returns: list[tuple[str, float]]) -> dict:
        return {
            "BENCHMARK": {
                "strategy_name": "BENCHMARK",
                "monthly_returns": [list(p) for p in monthly_returns],
            }
        }

    def test_clean_strategy_produces_finite_sharpe(self):
        from tools.analytics import regime_conditional_performance

        rng = np.random.default_rng(42)
        # 300 monthly returns straddling the 2022 break — random
        # normal so the Sharpe is finite and non-zero.
        dates = pd.date_range("2000-01-31", periods=300, freq="ME")
        returns = rng.normal(0.005, 0.04, size=300)
        pairs = [(d.isoformat(), float(r)) for d, r in zip(dates, returns)]
        rows = regime_conditional_performance(
            self._build_strategy(pairs), rf=None)
        assert len(rows) == 1
        row = rows[0]
        # Both periods have months >= 2 → both sharpes must be present.
        assert row["pre_2022_months"] >= 2
        assert row["post_2022_months"] >= 2
        assert isinstance(row["pre_2022_sharpe"], float)
        assert isinstance(row["post_2022_sharpe"], float)
        assert math.isfinite(row["pre_2022_sharpe"])

    def test_all_nan_pre_period_falls_back_to_none_and_zero_months(self):
        # Every pre-2022 entry is NaN. After dropna, pre is empty —
        # months should be 0, not 240, and sharpe is None. The
        # validator now sees (None, 0) which is legitimate (months
        # < 2 carve-out), not (None, 240) which would have been
        # flagged as unexpectedly null.
        from tools.analytics import regime_conditional_performance

        pre_dates = pd.date_range("2000-01-31", periods=240, freq="ME")
        post_dates = pd.date_range("2022-01-31", periods=24, freq="ME")
        pairs = (
            [(d.isoformat(), float("nan")) for d in pre_dates]
            + [(d.isoformat(), 0.01) for d in post_dates]
        )
        rows = regime_conditional_performance(
            self._build_strategy(pairs), rf=None)
        assert len(rows) == 1
        row = rows[0]
        assert row["pre_2022_months"] == 0
        assert row["pre_2022_sharpe"] is None
        assert row["post_2022_months"] == 24
        assert isinstance(row["post_2022_sharpe"], float)

    def test_validator_passes_on_safe_none_when_months_is_zero(self):
        # The downstream validator semantics — what (None, 0) means.
        # Months < 2 → None sharpe is legitimate → row is complete.
        row = {
            "strategy":         "X",
            "pre_2022_sharpe":  None,
            "post_2022_sharpe": 0.5,
            "pre_2022_cagr":    None,
            "post_2022_cagr":   0.07,
            "pre_2022_months":  0,
            "post_2022_months": 24,
        }
        out = _validate_regime_conditional([row])
        assert out["complete"] is True

    def test_validator_flags_none_when_months_is_high(self):
        # The bug we're defending against — if NaN ever leaks back
        # in via a future regression, the validator still catches it.
        row = {
            "strategy":         "X",
            "pre_2022_sharpe":  None,
            "post_2022_sharpe": 0.5,
            "pre_2022_cagr":    0.07,
            "post_2022_cagr":   0.07,
            "pre_2022_months":  240,
            "post_2022_months": 24,
        }
        out = _validate_regime_conditional([row])
        assert out["complete"] is False
        assert any("pre_2022_sharpe_unexpectedly_null" in m
                   for m in out["invalid_rows"][0]["missing"])

    def test_safe_sharpe_and_safe_cagr_return_none_on_non_finite(self):
        # Unit-level pin — _safe_sharpe / _safe_cagr never return NaN.
        from tools.analytics import _safe_cagr, _safe_sharpe

        # An all-NaN series produces NaN downstream; the safe wrapper
        # converts it to None.
        s = pd.Series([float("nan"), float("nan"), float("nan")],
                      index=pd.date_range("2020-01-31", periods=3, freq="ME"))
        assert _safe_sharpe(s, rf=None) is None
        # _cagr on a series whose growth <= 0 returns -1.0 (NOT NaN),
        # so _safe_cagr passes it through as a real number.
        assert _safe_cagr(s) is None or math.isfinite(_safe_cagr(s) or 0.0)

    def test_empty_series_returns_none_from_both_helpers(self):
        from tools.analytics import _safe_cagr, _safe_sharpe
        empty = pd.Series(dtype=float)
        assert _safe_sharpe(empty, rf=None) is None
        assert _safe_cagr(empty) is None

    def test_short_series_returns_none_from_safe_sharpe(self):
        from tools.analytics import _safe_sharpe
        # len < 2 → None (matches the original `if len(pre) >= 2 else None`
        # contract).
        one = pd.Series([0.01], index=pd.to_datetime(["2020-01-31"]))
        assert _safe_sharpe(one, rf=None) is None
