"""
tests/test_audit_layer2_deterministic.py — May 25 2026.

Pins the Layer 2 deterministic recompute path (replaces the LLM
arithmetic that used to be on this hot path). Five surfaces:

  1. _compare — the comparison primitive. Pass / warning / fail
     threshold semantics, sign-mismatch fail-fast, missing-value
     handling, non-finite guard.
  2. recompute_summary_statistics — 7 metrics per asset, computed via
     tools.analytics. The cached platform values are confirmed against
     a fresh Python recompute over raw_data.asset_returns.
  3. recompute_efficient_frontier — mu @ w and sqrt(w · cov · w) for
     the platform's max-Sharpe weights, against the aligned subset
     surfaced under platform_computed.efficient_frontier.aligned_returns.
  4. recompute_regime_split — pre/post-2022 Sharpe + CAGR per strategy,
     using analytics._safe_sharpe / _safe_cagr (the SAME helpers the
     platform's regime_conditional_performance uses).
  5. recompute_rolling_correlation — 12-month rolling correlation
     equity-vs-IG and equity-vs-HY, averaged pre/post-2022.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")


# ── _compare — the comparison primitive ──────────────────────────────────────


class TestCompare:
    """Pass / warning / fail threshold semantics + fail-open paths."""

    def test_exact_match_is_pass(self):
        from tools.audit_layer2_deterministic import _compare
        out = _compare("m", 0.05, 0.05)
        assert out["status"] == "pass"
        assert out["platform_value"] == 0.05
        assert out["auditor_value"] == 0.05
        assert out["discrepancy_pct"] == 0.0
        assert out["flag"] == ""

    def test_within_tolerance_pass_is_pass(self):
        # 0.005% difference is well within TOLERANCE_PASS=0.01%.
        from tools.audit_layer2_deterministic import _compare
        out = _compare("m", 1.0, 1.00005)
        assert out["status"] == "pass"

    def test_in_warning_band_is_warning(self):
        # 0.05% — between PASS (0.01%) and FAIL (0.1%).
        from tools.audit_layer2_deterministic import _compare
        out = _compare("m", 1.0, 1.0005)
        assert out["status"] == "warning"
        assert out["flag"] == "discrepancy"

    def test_beyond_fail_threshold_is_fail(self):
        # 0.2% — beyond FAIL (0.1%).
        from tools.audit_layer2_deterministic import _compare
        out = _compare("m", 1.0, 1.002)
        assert out["status"] == "fail"

    def test_sign_disagreement_is_always_fail(self):
        # Wrong-direction Sharpe is a real bug, not a rounding gap.
        from tools.audit_layer2_deterministic import _compare
        out = _compare("sharpe", 0.5, -0.5)
        assert out["status"] == "fail"
        assert "Sign disagreement" in out["reasoning"]

    def test_sign_disagreement_near_zero_is_NOT_failed_on_sign(self):
        # Tiny values on either side of zero (e.g. ±0.00001) shouldn't
        # be flagged as a sign mismatch — the magnitude-gate is 1e-4.
        from tools.audit_layer2_deterministic import _compare
        out = _compare("m", 0.00001, -0.00001)
        # The values are both essentially zero; sign rule doesn't fire.
        # The result depends on the absolute-difference formula for
        # near-zero platform values — which scales as percent of value.
        # The point of THIS test is: the sign-disagreement message is
        # NOT in the reasoning.
        assert "Sign disagreement" not in out["reasoning"]

    def test_platform_none_yields_warning(self):
        # ONE side None is missing_value WARN — the two sides disagree
        # about whether the value is computable.
        from tools.audit_layer2_deterministic import _compare
        out = _compare("m", None, 0.5)
        assert out["status"] == "warning"
        assert out["flag"] == "missing_value"

    def test_auditor_none_yields_warning(self):
        from tools.audit_layer2_deterministic import _compare
        out = _compare("m", 0.5, None)
        assert out["status"] == "warning"
        assert out["flag"] == "missing_value"

    def test_both_none_is_pass_with_agreement_reasoning(self):
        """Both sides agree the value is not computable — PASS, not
        missing_value WARN. May 25 2026 fix: the previous behaviour
        flagged the agreement as a defect, surfacing dozens of
        spurious warnings for EQUITY/BENCHMARK information_ratio,
        short-history regime sub-periods, and MOM-factor fallbacks."""
        from tools.audit_layer2_deterministic import _compare
        out = _compare("m", None, None)
        assert out["status"] == "pass"
        # The reasoning string explicitly tells the operator this is
        # an agreement, not a missing-data failure.
        assert "agreement" in out["reasoning"].lower()
        assert "not computable" in out["reasoning"].lower()
        # discrepancy_pct is 0 (perfect agreement) and flag is empty
        # so the finding shape is consistent with other PASS cases.
        assert out["discrepancy_pct"] == 0.0
        assert out["flag"] == ""

    def test_nan_auditor_yields_warning(self):
        from tools.audit_layer2_deterministic import _compare
        out = _compare("m", 0.5, float("nan"))
        assert out["status"] == "warning"
        assert out["flag"] == "non_finite_value"

    def test_non_numeric_yields_warning(self):
        from tools.audit_layer2_deterministic import _compare
        out = _compare("m", "not a number", 0.5)
        assert out["status"] == "warning"
        assert out["flag"] == "non_numeric_value"

    def test_near_zero_platform_uses_absolute_discrepancy(self):
        # When the platform value is ~0, a small auditor difference
        # should NOT compute as a huge percent miss — the formula
        # switches to absolute-difference-as-percent for |platform|<1e-6.
        from tools.audit_layer2_deterministic import _compare
        # platform=0, auditor=0.0001 → abs(0.0001) * 100 = 0.01% pass.
        out = _compare("m", 0.0, 0.0001)
        # Discrepancy_pct from abs(0.0001-0)*100 = 0.01 → status pass.
        assert out["discrepancy_pct"] == 0.01
        assert out["status"] == "pass"


# ── recompute_summary_statistics ──────────────────────────────────────────────


class TestRecomputeSummaryStatistics:
    """Compares the recompute (via tools.analytics) against the platform's
    cached values for one asset. A correctly-cached value lands as pass."""

    def _payload(self, asset_returns: dict, platform: dict) -> dict:
        return {
            "raw_data": {"asset_returns": asset_returns},
            "metadata": {"risk_free_rate": {"value": 0.0}},
            "platform_computed": {"summary_statistics": {"IG": platform}},
        }

    def test_recompute_matches_platform_value_for_clean_series(self):
        # Build a deterministic returns series; compute the seven
        # metrics via analytics directly; assert recompute matches.
        # IMPORTANT — the platform's analytics.summary_statistics()
        # stores volatility under `ann_volatility` and Sharpe under
        # `sharpe_ratio`. The recompute MUST read those keys; reading
        # `volatility` / `sharpe` returns None and surfaces a spurious
        # missing_value WARN (the May 25 2026 bug this test pins).
        from tools import analytics as an
        from tools.audit_layer2_deterministic import (
            recompute_summary_statistics,
        )

        idx = pd.date_range("2020-01-31", periods=24, freq="ME")
        rng = np.random.default_rng(42)
        equity = rng.normal(0.008, 0.04, 24).tolist()
        ig     = rng.normal(0.003, 0.02, 24).tolist()
        hy     = rng.normal(0.005, 0.03, 24).tolist()
        rf_arr = [0.001] * 24

        # Platform-computed values via the same primitives — these are
        # what the analytics layer stores, under its native field names.
        rf = pd.Series(rf_arr, index=idx)
        ig_series = pd.Series(ig, index=idx)
        platform = {
            "cagr":           round(an._cagr(ig_series), 4),
            "ann_volatility": round(an._ann_vol(ig_series), 4),
            "sharpe_ratio":   round(an._sharpe(ig_series, rf), 4),
            "max_drawdown":   round(an._max_drawdown(ig_series), 4),
            "skewness":      None,    # tested independently
            "excess_return": None,
            "information_ratio": None,
        }

        payload = self._payload(
            {"equity": equity, "ig": ig, "hy": hy, "rf": rf_arr,
             "dates": [d.isoformat() for d in idx]},
            platform,
        )
        result = recompute_summary_statistics("IG", payload, platform)
        # Every check on a non-None platform value must be PASS — the
        # recompute uses the SAME analytics primitives the platform
        # stored, so the values agree to the round(_, 4) precision.
        statuses = {c["metric"]: c["status"] for c in result["checks"]}
        for metric in ("IG.cagr", "IG.volatility", "IG.sharpe",
                       "IG.max_drawdown"):
            assert statuses[metric] == "pass", (
                f"{metric}: expected pass, got "
                f"{statuses[metric]} ({result['checks']})"
            )

    def test_recompute_uses_analytics_layer_field_names(self):
        """Regression pin for the May 25 2026 bug — every platform
        summary-statistics row uses the analytics layer's native
        field names (ann_volatility / sharpe_ratio), NOT the natural
        metric names (volatility / sharpe). Reading the wrong key
        returns None and surfaces a spurious missing_value WARN on
        46 checks across the four assets (EQUITY / IG / HY /
        BENCHMARK × 7 metrics).

        The bug was the recomputer asking for platform.get("volatility"),
        which the analytics layer never sets. This test asserts:
          (1) When the platform dict ONLY carries the canonical field
              names, volatility and sharpe checks PASS — the recomputer
              found them.
          (2) When the platform dict carries the wrong-named fields
              (volatility / sharpe), the recomputer falls through to
              the canonical names and STILL reports missing_value —
              proving the lookup keys are exactly the canonical ones.
        """
        from tools.audit_layer2_deterministic import (
            recompute_summary_statistics,
        )

        idx = pd.date_range("2020-01-31", periods=12, freq="ME")
        equity = [0.01] * 12
        ig     = [0.005] * 12
        hy     = [0.008] * 12
        rf_arr = [0.0] * 12
        payload = self._payload(
            {"equity": equity, "ig": ig, "hy": hy, "rf": rf_arr,
             "dates": [d.isoformat() for d in idx]},
            {},
        )

        # (1) Canonical field names — checks find the value.
        canonical_platform = {
            "cagr":           0.0617,
            "ann_volatility": 0.0,    # constant series → zero vol
            "sharpe_ratio":   0.0,    # zero vol → zero sharpe
            "max_drawdown":   0.0,
            "skewness":       0.0,
            "excess_return":  0.0,
            "information_ratio": None,
        }
        result = recompute_summary_statistics(
            "IG", payload, canonical_platform)
        by_metric = {c["metric"]: c for c in result["checks"]}
        # No missing-value flags on ann_volatility / sharpe_ratio reads
        # — proves the recompute uses the right keys.
        assert by_metric["IG.volatility"]["flag"] != "missing_value", \
            by_metric["IG.volatility"]
        assert by_metric["IG.sharpe"]["flag"] != "missing_value", \
            by_metric["IG.sharpe"]

        # (2) Wrong field names — checks fall through to missing_value.
        wrong_platform = {
            "cagr":         0.0617,
            "volatility":   0.0,   # WRONG key — analytics doesn't set this
            "sharpe":       0.0,   # WRONG key — analytics doesn't set this
            "max_drawdown": 0.0,
            "skewness":     0.0,
            "excess_return": 0.0,
            "information_ratio": None,
        }
        result = recompute_summary_statistics("IG", payload, wrong_platform)
        by_metric = {c["metric"]: c for c in result["checks"]}
        # With the wrong-named fields, the recompute reports
        # missing_value — proves it's reading the canonical names.
        assert by_metric["IG.volatility"]["flag"] == "missing_value"
        assert by_metric["IG.sharpe"]["flag"] == "missing_value"

    def test_missing_asset_returns_warning(self):
        from tools.audit_layer2_deterministic import (
            recompute_summary_statistics,
        )
        payload = self._payload(
            {"dates": ["2024-01-31", "2024-02-29"],
             "equity": [0.01, 0.02], "rf": [0.001, 0.001]},
            {"cagr": 0.05},
        )
        # No 'ig' field at all → recompute can't run → one WARN check.
        result = recompute_summary_statistics("IG", payload, {})
        assert result["strategy"] == "IG"
        assert len(result["checks"]) == 1
        assert result["checks"][0]["status"] == "warning"
        assert result["checks"][0]["flag"] == "no_data"


# ── recompute_efficient_frontier ─────────────────────────────────────────────


class TestRecomputeEfficientFrontier:
    """The recompute verifies mu @ w and sqrt(w · cov · w) against the
    platform's max-Sharpe σ and μ, using the SAME aligned subset the
    platform's frontier was built against."""

    def test_recompute_matches_platform_arithmetic(self):
        from tools.audit_layer2_deterministic import (
            recompute_efficient_frontier,
        )
        rng = np.random.default_rng(7)
        eq = rng.normal(0.008, 0.04, 60).tolist()
        ig = rng.normal(0.003, 0.02, 60).tolist()
        hy = rng.normal(0.005, 0.03, 60).tolist()
        # Some weights — doesn't need to be tangency for this test;
        # we're verifying recompute matches mu @ w / sqrt(w · cov · w).
        weights = {"EQUITY": 0.4, "IG": 0.3, "HY": 0.3}
        monthly = np.array([eq, ig, hy]).T
        mu = monthly.mean(axis=0) * 12
        cov = np.cov(monthly, rowvar=False, ddof=1) * 12
        w_arr = np.array([weights["EQUITY"], weights["IG"], weights["HY"]])
        platform_mu = float(mu @ w_arr)
        platform_sigma = float(np.sqrt(w_arr @ cov @ w_arr))
        platform_sharpe = platform_mu / platform_sigma

        payload = {
            "platform_computed": {"efficient_frontier": {
                "max_sharpe_point": {
                    "mu":     round(platform_mu, 6),
                    "sigma":  round(platform_sigma, 6),
                    "sharpe": round(platform_sharpe, 6),
                    "weights": weights,
                },
                "aligned_returns": {
                    "equity": eq, "ig": ig, "hy": hy,
                    "rf": [0.0] * 60, "rf_annual": 0.0, "n_obs": 60,
                    "dates": [],
                },
            }},
        }
        result = recompute_efficient_frontier(payload)
        statuses = {c["metric"]: c["status"] for c in result["checks"]}
        assert statuses["max_sharpe.mu"] == "pass"
        assert statuses["max_sharpe.sigma"] == "pass"
        assert statuses["max_sharpe.sharpe"] == "pass"

    def test_missing_aligned_returns_yields_warning(self):
        from tools.audit_layer2_deterministic import (
            recompute_efficient_frontier,
        )
        payload = {
            "platform_computed": {"efficient_frontier": {
                "max_sharpe_point": {"mu": 0.07, "sigma": 0.08,
                                     "sharpe": 0.875,
                                     "weights": {"EQUITY": 1.0}},
            }},
        }
        result = recompute_efficient_frontier(payload)
        assert len(result["checks"]) == 1
        assert result["checks"][0]["status"] == "warning"
        assert result["checks"][0]["flag"] == "no_data"

    def test_recompute_detects_intentionally_wrong_platform_value(self):
        """A platform that reports a μ that doesn't match mu @ w — the
        bug class the user reported. The recompute should FAIL the
        check, not just record it as pass."""
        from tools.audit_layer2_deterministic import (
            recompute_efficient_frontier,
        )
        rng = np.random.default_rng(7)
        eq = rng.normal(0.008, 0.04, 60).tolist()
        ig = rng.normal(0.003, 0.02, 60).tolist()
        hy = rng.normal(0.005, 0.03, 60).tolist()
        weights = {"EQUITY": 0.08, "IG": 0.0, "HY": 0.92}
        payload = {
            "platform_computed": {"efficient_frontier": {
                "max_sharpe_point": {
                    "mu":     0.0773,  # the user-reported wrong value
                    "sigma":  0.089,
                    "sharpe": 0.682,
                    "weights": weights,
                },
                "aligned_returns": {
                    "equity": eq, "ig": ig, "hy": hy,
                    "rf": [0.0] * 60, "rf_annual": 0.0, "n_obs": 60,
                    "dates": [],
                },
            }},
        }
        result = recompute_efficient_frontier(payload)
        # Whatever μ the recompute lands on (different from 0.0773
        # because the data is random), the check status for mu must
        # not be "pass" — they disagree.
        mu_check = next(c for c in result["checks"]
                        if c["metric"] == "max_sharpe.mu")
        assert mu_check["status"] != "pass"


# ── recompute_regime_split ───────────────────────────────────────────────────


class TestRecomputeRegimeSplit:
    """pre/post-2022 Sharpe + CAGR per strategy. Uses analytics'
    _safe_sharpe / _safe_cagr so the recompute matches what the
    platform's regime_conditional_performance computes."""

    def test_recompute_matches_platform_value(self):
        from tools.audit_layer2_deterministic import (
            recompute_regime_split,
        )
        from tools.analytics import _safe_cagr, _safe_sharpe, REGIME_BREAK

        rng = np.random.default_rng(11)
        idx = pd.date_range("2018-01-31", periods=72, freq="ME")
        vals = rng.normal(0.008, 0.04, 72)
        dates = [d.isoformat() for d in idx]
        series = pd.Series(vals, index=idx)
        pre = series[series.index < REGIME_BREAK]
        post = series[series.index >= REGIME_BREAK]

        platform_regime = {
            "TEST_STRAT": {
                "pre_2022_sharpe":  _safe_sharpe(pre,  None),
                "post_2022_sharpe": _safe_sharpe(post, None),
                "pre_2022_cagr":    _safe_cagr(pre),
                "post_2022_cagr":   _safe_cagr(post),
            },
        }
        payload = {
            "raw_data": {
                "strategy_returns": {"TEST_STRAT": vals.tolist()},
                "asset_returns": {"dates": dates, "rf": [0.0] * 72},
            },
            "platform_computed": {"regime_conditional": platform_regime},
        }
        result = recompute_regime_split(["TEST_STRAT"], payload)
        # Four checks per strategy; all should pass against the same
        # analytics primitives.
        assert len(result["checks"]) == 4
        for c in result["checks"]:
            assert c["status"] == "pass", c

    def test_unknown_strategy_yields_alignment_warning(self):
        from tools.audit_layer2_deterministic import (
            recompute_regime_split,
        )
        payload = {
            "raw_data": {
                "strategy_returns": {},
                "asset_returns": {"dates": [], "rf": []},
            },
            "platform_computed": {"regime_conditional": {}},
        }
        result = recompute_regime_split(["MISSING"], payload)
        assert len(result["checks"]) == 1
        assert result["checks"][0]["status"] == "warning"
        assert result["checks"][0]["flag"] == "alignment_error"

    def test_short_history_strategy_recomputes_on_pair_shape(self):
        """The actual bug the user reported: MOMENTUM_ROTATION starts
        ~2003-07 (270 months) while the global asset_returns dates
        index has 282 months. The flat-values shape couldn't align —
        my length-mismatch guard skipped these strategies, surfacing
        every coef as missing_value WARN. With pair-shape input each
        strategy carries its own dates and aligns correctly."""
        from tools.audit_layer2_deterministic import (
            recompute_regime_split,
        )

        # Global index: 36 months starting 2018-01-31.
        global_idx = pd.date_range("2018-01-31", periods=36, freq="ME")
        # SHORT-HISTORY strategy: only 24 months (starts 2020-01),
        # 12 months shorter than the global index.
        rng = np.random.default_rng(13)
        short_dates = pd.date_range(
            "2020-01-31", periods=24, freq="ME")
        short_vals = rng.normal(0.008, 0.04, 24).tolist()
        short_pairs = [[d.isoformat(), v]
                       for d, v in zip(short_dates, short_vals)]

        payload = {
            "raw_data": {
                # Pair shape carries the strategy's own dates.
                "strategy_returns": {"SHORT_STRAT": short_pairs},
                "asset_returns": {
                    "dates": [d.isoformat() for d in global_idx],
                    "rf": [0.0] * 36,
                },
            },
            "platform_computed": {"regime_conditional": {
                "SHORT_STRAT": {
                    "pre_2022_sharpe": None,    # < 24 mo pre-2022
                    "post_2022_sharpe": 0.5,
                    "pre_2022_cagr": None,
                    "post_2022_cagr": 0.06,
                    "pre_2022_months": 24,
                    "post_2022_months": 0,
                },
            }},
        }
        result = recompute_regime_split(["SHORT_STRAT"], payload)
        # NO alignment_error WARN — the pair shape lets the recompute
        # build the series on the strategy's own timeline. 4 checks
        # (pre/post × sharpe/cagr).
        assert len(result["checks"]) == 4
        flags = {c["flag"] for c in result["checks"]}
        assert "alignment_error" not in flags

    def test_pair_shape_for_full_history_strategy_also_works(self):
        """A full-history strategy in pair shape recomputes identically
        to the equivalent flat-values input — the pair path is a
        SUPERSET of the legacy path."""
        from tools.audit_layer2_deterministic import (
            recompute_regime_split,
        )

        rng = np.random.default_rng(17)
        idx = pd.date_range("2018-01-31", periods=72, freq="ME")
        vals = rng.normal(0.008, 0.04, 72).tolist()
        pairs = [[d.isoformat(), v] for d, v in zip(idx, vals)]

        payload = {
            "raw_data": {
                "strategy_returns": {"FULL_STRAT": pairs},
                "asset_returns": {
                    "dates": [d.isoformat() for d in idx],
                    "rf": [0.0] * 72,
                },
            },
            "platform_computed": {"regime_conditional": {
                "FULL_STRAT": {
                    "pre_2022_sharpe": 0.5, "post_2022_sharpe": 0.4,
                    "pre_2022_cagr": 0.07, "post_2022_cagr": 0.05,
                    "pre_2022_months": 48, "post_2022_months": 24,
                },
            }},
        }
        result = recompute_regime_split(["FULL_STRAT"], payload)
        assert len(result["checks"]) == 4
        # No alignment_error — the pair shape resolves cleanly.
        assert not any(c["flag"] == "alignment_error"
                       for c in result["checks"])

    def test_flat_values_short_history_falls_through_as_alignment_error(
        self,
    ):
        """A LEGACY flat-values payload with a short-history strategy
        can't be aligned (no per-strategy dates available). Drop to
        the alignment_error path so the operator sees the actual
        cause rather than a silent zero-output for that strategy.
        This is the pre-fix behaviour preserved for legacy data."""
        from tools.audit_layer2_deterministic import (
            recompute_regime_split,
        )

        global_idx = pd.date_range("2018-01-31", periods=36, freq="ME")
        # Flat values — 24 entries (shorter than the 36-month index).
        short_vals = [0.01] * 24
        payload = {
            "raw_data": {
                "strategy_returns": {"SHORT_STRAT": short_vals},
                "asset_returns": {
                    "dates": [d.isoformat() for d in global_idx],
                    "rf": [0.0] * 36,
                },
            },
            "platform_computed": {"regime_conditional": {
                "SHORT_STRAT": {"pre_2022_sharpe": 0.5,
                                "post_2022_sharpe": 0.5,
                                "pre_2022_cagr": 0.05,
                                "post_2022_cagr": 0.05,
                                "pre_2022_months": 24,
                                "post_2022_months": 0},
            }},
        }
        result = recompute_regime_split(["SHORT_STRAT"], payload)
        # Single alignment_error WARN — the legacy flat-values path
        # can't recover the dates without per-strategy timeline.
        assert any(c["flag"] == "alignment_error"
                   for c in result["checks"])


# ── recompute_factor_loadings — short-history pair-shape support ────────────


class TestRecomputeFactorLoadings:
    """A short-history strategy (MOMENTUM_ROTATION starts ~2003-07 vs
    the global asset_returns 2002-07 index) was the user-reported
    cause of 30+ missing_value warnings in factor_loadings: the
    audit payload's flat-values strategy_returns couldn't pair the
    270-entry strategy values to the 282-entry global dates index,
    so the recomputer's length-mismatch guard skipped the strategy
    and every coef came back None on the auditor side."""

    def _ff_rows(self, n: int = 60) -> dict[str, list]:
        # Construct Fama-French factor rows from yyyymm dates.
        start = pd.Timestamp("2018-01-01")
        return {
            "dates":   [(start + pd.DateOffset(months=i)).strftime("%Y-%m")
                        for i in range(n)],
            "mkt_rf":  [0.5 + 0.01 * i for i in range(n)],
            "smb":     [0.1] * n,
            "hml":     [0.05] * n,
            "mom":     [0.02] * n,
            "rf":      [0.001] * n,
        }

    def test_pair_shape_for_short_history_strategy_recomputes(self):
        """The bug: short-history strategy in flat-values form skipped
        on length mismatch. With pair shape the strategy carries its
        own dates and resolves on its own timeline."""
        from tools.audit_layer2_deterministic import (
            recompute_factor_loadings,
        )

        rng = np.random.default_rng(101)
        # Global index: 60 months. Strategy has 36 months (short).
        global_idx = pd.date_range("2018-01-31", periods=60, freq="ME")
        short_idx = pd.date_range("2020-07-31", periods=36, freq="ME")
        short_vals = rng.normal(0.008, 0.04, 36).tolist()
        # Pair shape — carries the strategy's own dates.
        short_pairs = [[d.isoformat(), v]
                       for d, v in zip(short_idx, short_vals)]

        ff = self._ff_rows(60)
        payload = {
            "raw_data": {
                "strategy_returns": {"SHORT_STRAT": short_pairs},
                "asset_returns": {
                    "dates": [d.isoformat() for d in global_idx],
                    "rf": [0.0] * 60,
                },
                "ff_factors": ff,
            },
            # Platform values: pretend the regression ran successfully.
            "platform_computed": {"factor_loadings": {"SHORT_STRAT": {
                "model": "carhart_4factor",
                "mkt_rf": 0.85, "smb": 0.0, "hml": 0.0, "mom": -0.02,
                "alpha_annualized": 0.01, "r_squared": 0.7,
                "mkt_rf_significant": True, "smb_significant": False,
                "hml_significant": False, "mom_significant": False,
                "alpha_significant": False,
            }}},
        }
        result = recompute_factor_loadings(["SHORT_STRAT"], payload)
        # The recompute landed — not the "no_data" fall-through.
        assert result["strategy"] == "factor_loadings"
        assert len(result["checks"]) >= 1
        # At least one check has an auditor_value populated; the
        # regression ran. The pre-fix behaviour would have surfaced
        # one "no_data" WARN and zero recomputed values.
        flags = [c.get("flag") for c in result["checks"]]
        assert flags.count("no_data") == 0, (
            f"recompute fell through to no_data — pair shape not "
            f"resolving short-history strategy. checks: {result['checks']}"
        )

    def test_full_history_pair_shape_recomputes(self):
        """Sanity — full-history strategy in pair shape also works
        (regression guard so a future refactor doesn't break the
        common case while fixing the short-history one)."""
        from tools.audit_layer2_deterministic import (
            recompute_factor_loadings,
        )

        rng = np.random.default_rng(103)
        idx = pd.date_range("2018-01-31", periods=60, freq="ME")
        vals = rng.normal(0.008, 0.04, 60).tolist()
        pairs = [[d.isoformat(), v] for d, v in zip(idx, vals)]
        ff = self._ff_rows(60)
        payload = {
            "raw_data": {
                "strategy_returns": {"FULL_STRAT": pairs},
                "asset_returns": {
                    "dates": [d.isoformat() for d in idx],
                    "rf": [0.0] * 60,
                },
                "ff_factors": ff,
            },
            "platform_computed": {"factor_loadings": {"FULL_STRAT": {
                "model": "carhart_4factor",
                "mkt_rf": 0.0, "smb": 0.0, "hml": 0.0, "mom": 0.0,
                "alpha_annualized": 0.0, "r_squared": 0.0,
            }}},
        }
        result = recompute_factor_loadings(["FULL_STRAT"], payload)
        assert result["strategy"] == "factor_loadings"
        # No "no_data" WARN — the pair shape resolved.
        flags = [c.get("flag") for c in result["checks"]]
        assert flags.count("no_data") == 0


# ── recompute_rolling_correlation ────────────────────────────────────────────


class TestRecomputeRollingCorrelation:
    """Rolling 12-month correlation of equity vs IG / HY, averaged
    pre/post-2022."""

    def test_recompute_returns_four_checks(self):
        from tools.audit_layer2_deterministic import (
            recompute_rolling_correlation,
        )
        rng = np.random.default_rng(31)
        n = 60
        idx = pd.date_range("2019-01-31", periods=n, freq="ME")
        eq = rng.normal(0.008, 0.04, n).tolist()
        ig = rng.normal(0.003, 0.02, n).tolist()
        hy = rng.normal(0.005, 0.03, n).tolist()
        payload = {
            "raw_data": {"asset_returns": {
                "equity": eq, "ig": ig, "hy": hy,
                "rf": [0.0] * n,
                "dates": [d.isoformat() for d in idx],
            }},
            # Platform values undefined — the recompute should still
            # produce four auditor values, just with WARN status from
            # the missing-value path.
            "platform_computed": {"rolling_correlation": {}},
        }
        result = recompute_rolling_correlation(payload)
        # Four checks: equity_ig pre/post + equity_hy pre/post.
        metrics = {c["metric"] for c in result["checks"]}
        assert metrics == {
            "equity_ig.pre_2022", "equity_ig.post_2022",
            "equity_hy.pre_2022", "equity_hy.post_2022",
        }

    def test_missing_asset_series_yields_warning(self):
        from tools.audit_layer2_deterministic import (
            recompute_rolling_correlation,
        )
        payload = {
            "raw_data": {"asset_returns": {"dates": [], "rf": []}},
            "platform_computed": {"rolling_correlation": {}},
        }
        result = recompute_rolling_correlation(payload)
        assert len(result["checks"]) == 1
        assert result["checks"][0]["status"] == "warning"
        assert result["checks"][0]["flag"] == "no_data"

    def test_nested_platform_shape_resolves_correctly(self):
        """Canonical shape: {pre_2022: {equity_ig, equity_hy}, ...}."""
        from tools.audit_layer2_deterministic import (
            recompute_rolling_correlation,
        )
        n = 30
        idx = pd.date_range("2019-01-31", periods=n, freq="ME")
        rng = np.random.default_rng(99)
        eq = rng.normal(0.008, 0.04, n).tolist()
        ig = rng.normal(0.003, 0.02, n).tolist()
        hy = rng.normal(0.005, 0.03, n).tolist()
        payload = {
            "raw_data": {"asset_returns": {
                "equity": eq, "ig": ig, "hy": hy,
                "rf": [0.0] * n,
                "dates": [d.isoformat() for d in idx],
            }},
            "platform_computed": {"rolling_correlation": {
                # The canonical shape analytics emits.
                "pre_2022":  {"equity_ig": 0.123, "equity_hy": 0.456},
                "post_2022": {"equity_ig": 0.789, "equity_hy": 0.234},
            }},
        }
        result = recompute_rolling_correlation(payload)
        # Each check's platform_value comes back as the float from the
        # nested payload (rounded by _compare). No missing_value.
        by_metric = {c["metric"]: c for c in result["checks"]}
        for metric in ("equity_ig.pre_2022", "equity_ig.post_2022",
                       "equity_hy.pre_2022", "equity_hy.post_2022"):
            assert by_metric[metric]["platform_value"] is not None, (
                f"{metric}: platform_value should resolve via nested "
                f"lookup, got {by_metric[metric]}"
            )

    def test_flat_key_fallback_resolves_period_dot_pair(self):
        """Legacy/alternative flat-key shape: 'pre_2022.equity_ig'."""
        from tools.audit_layer2_deterministic import (
            recompute_rolling_correlation,
        )
        n = 30
        idx = pd.date_range("2019-01-31", periods=n, freq="ME")
        rng = np.random.default_rng(101)
        eq = rng.normal(0.008, 0.04, n).tolist()
        ig = rng.normal(0.003, 0.02, n).tolist()
        hy = rng.normal(0.005, 0.03, n).tolist()
        payload = {
            "raw_data": {"asset_returns": {
                "equity": eq, "ig": ig, "hy": hy,
                "rf": [0.0] * n,
                "dates": [d.isoformat() for d in idx],
            }},
            "platform_computed": {"rolling_correlation": {
                # Flat-key shape — fallback path.
                "pre_2022.equity_ig": 0.111,
                "pre_2022.equity_hy": 0.222,
                "post_2022.equity_ig": 0.333,
                "post_2022.equity_hy": 0.444,
            }},
        }
        result = recompute_rolling_correlation(payload)
        by_metric = {c["metric"]: c for c in result["checks"]}
        # All four checks resolve via the flat-key fallback.
        for metric in ("equity_ig.pre_2022", "equity_ig.post_2022",
                       "equity_hy.pre_2022", "equity_hy.post_2022"):
            assert by_metric[metric]["platform_value"] is not None, (
                f"{metric}: flat-key fallback should resolve, "
                f"got {by_metric[metric]}"
            )


# ── Hash check — TOLERANCE constants are reasonable ──────────────────────────


class TestToleranceConstants:
    """Sanity-pin the threshold constants so an accidental change
    (someone bumps TOLERANCE_PASS to 5%) lights up in review."""

    def test_pass_threshold_is_0_01_percent(self):
        from tools.audit_layer2_deterministic import TOLERANCE_PASS
        assert TOLERANCE_PASS == 0.01

    def test_fail_threshold_is_0_1_percent(self):
        from tools.audit_layer2_deterministic import TOLERANCE_FAIL
        assert TOLERANCE_FAIL == 0.1

    def test_pass_threshold_is_below_fail_threshold(self):
        from tools.audit_layer2_deterministic import (
            TOLERANCE_FAIL, TOLERANCE_PASS,
        )
        assert TOLERANCE_PASS < TOLERANCE_FAIL
