"""tests/test_post_refresh_verifier.py -- June 27 2026.

Pins the post-light-refresh verifier + rounding audit (PR for
feat/post-refresh-verification-and-rounding-audit).
"""
from __future__ import annotations

import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── classify_submission_scope -- local copy mirrors catalog ──────


class TestVerifierScopeClassifier:

    def test_full_dataset_token_set(self):
        from tools.post_refresh_verifier import (
            classify_submission_scope, SCOPE_FULL_DATASET,
        )
        for tok in (
            "{{STUDY_MONTHS}}", "{{STUDY_START}}",
            "{{STUDY_END}}", "{{PRE_2022_EQ_IG_CORR}}",
            "{{POST_2022_EQ_IG_CORR}}",
        ):
            assert classify_submission_scope(tok) == (
                SCOPE_FULL_DATASET)

    def test_live_tokens_by_name(self):
        from tools.post_refresh_verifier import (
            classify_submission_scope, SCOPE_LIVE,
        )
        for tok in (
            "{{CURRENT_REGIME}}", "{{REGIME_CONFIDENCE}}",
            "{{CURRENT_EQUITY_PCT}}", "{{VIX_CURRENT}}",
            "{{ESS_CURRENT}}",
            "{{BLEND_REGIME_SWITCHING_WT}}",
        ):
            assert classify_submission_scope(tok) == SCOPE_LIVE

    def test_locked_default(self):
        from tools.post_refresh_verifier import (
            classify_submission_scope, SCOPE_LOCKED,
        )
        # Strategy / historical analytics tokens fall through to
        # LOCKED when not flagged as constant.
        for tok in (
            "{{REGIME_SWITCHING_SHARPE}}",
            "{{BENCHMARK_MAX_DD}}",
            "{{CLASSIC_6040_RECOVERY_MONTHS}}",
        ):
            assert classify_submission_scope(tok) == SCOPE_LOCKED

    def test_is_locked_constant(self):
        from tools.post_refresh_verifier import (
            classify_submission_scope, SCOPE_CONSTANT,
        )
        # is_locked=True on a non-live token -> CONSTANT
        assert classify_submission_scope(
            "{{OOS_SHARPE_BLEND}}",
            "academic_deck.OOS_SHARPE_REGIME_CONDITIONAL",
            is_locked=True) == SCOPE_CONSTANT


# ── Rounding rules ────────────────────────────────────────────────


class TestClassifyRounding:

    def test_sharpe_rule(self):
        from tools.post_refresh_verifier import classify_rounding
        rule = classify_rounding("{{REGIME_SWITCHING_SHARPE}}")
        assert rule == {"decimals": 2, "suffix": ""}

    def test_max_dd_rule(self):
        from tools.post_refresh_verifier import classify_rounding
        rule = classify_rounding("{{BENCHMARK_MAX_DD}}")
        assert rule == {"decimals": 1, "suffix": "%"}

    def test_recovery_months_integer(self):
        from tools.post_refresh_verifier import classify_rounding
        rule = classify_rounding(
            "{{CLASSIC_6040_RECOVERY_MONTHS}}")
        assert rule == {"decimals": 0, "suffix": ""}

    def test_factor_loadings_canonical_appendix_precision(self):
        """Per operator spec: factor loadings checked at 4dp
        (appendix precision is canonical). The brief intentionally
        rounds to 2dp for readability -- the verifier MUST treat
        4dp as the correct precision, not flag 2dp as a rounding
        error in the appendix."""
        from tools.post_refresh_verifier import classify_rounding
        for tok in (
            "{{BENCHMARK_ALPHA}}", "{{BENCHMARK_BETA}}",
            "{{REGIME_SWITCHING_HML_BETA}}",
        ):
            rule = classify_rounding(tok)
            assert rule is not None
            assert rule["decimals"] == 4, (
                f"{tok} should check at appendix-canonical 4dp")

    def test_confidence_rule(self):
        from tools.post_refresh_verifier import classify_rounding
        rule = classify_rounding("{{REGIME_CONFIDENCE}}")
        assert rule == {"decimals": 1, "suffix": "%"}

    def test_watchpoints(self):
        from tools.post_refresh_verifier import classify_rounding
        rule = classify_rounding("{{VIX_CURRENT}}")
        assert rule == {"decimals": 2, "suffix": ""}
        rule = classify_rounding("{{EQUITY_TREND_CURRENT}}")
        assert rule == {"decimals": 1, "suffix": "%"}

    def test_non_numeric_tokens_skip(self):
        """Date / string / count tokens skip rounding check."""
        from tools.post_refresh_verifier import classify_rounding
        for tok in (
            "{{STUDY_MONTHS}}", "{{STUDY_END}}",
            "{{CURRENT_REGIME}}", "{{N_STRATEGIES}}",
        ):
            assert classify_rounding(tok) is None


class TestCheckRounding:

    def test_correct_2dp(self):
        from tools.post_refresh_verifier import check_rounding
        rule = {"decimals": 2, "suffix": ""}
        assert check_rounding("0.86", rule)
        assert check_rounding("+0.86", rule)
        assert check_rounding("-0.86", rule)

    def test_wrong_decimal_count(self):
        from tools.post_refresh_verifier import check_rounding
        rule = {"decimals": 2, "suffix": ""}
        assert not check_rounding("0.860", rule)
        assert not check_rounding("0.8", rule)
        assert not check_rounding("0", rule)

    def test_with_suffix(self):
        from tools.post_refresh_verifier import check_rounding
        rule = {"decimals": 1, "suffix": "%"}
        assert check_rounding("-29.7%", rule)
        assert check_rounding("+98.0%", rule)
        assert not check_rounding("-29.7", rule)  # missing %
        assert not check_rounding("-29.70%", rule)  # 2dp

    def test_integer_rule(self):
        from tools.post_refresh_verifier import check_rounding
        rule = {"decimals": 0, "suffix": ""}
        assert check_rounding("6", rule)
        assert check_rounding("287", rule)
        assert not check_rounding("6.0", rule)
        assert not check_rounding("6.5", rule)

    def test_sentinels_rejected(self):
        from tools.post_refresh_verifier import check_rounding
        rule = {"decimals": 2, "suffix": ""}
        assert not check_rounding("—", rule)
        assert not check_rounding("cache miss", rule)
        assert not check_rounding("", rule)


# ── Endpoint wiring ──────────────────────────────────────────────


class TestVerifierEndpointWired:

    def test_endpoint_imports_verifier(self):
        """Source-inspection pin -- the FastAPI endpoint must
        defer to tools.post_refresh_verifier.run_verification.
        Catches a regression that inlines verification logic in
        main.py instead of using the module."""
        import inspect
        from main import post_verify_post_refresh
        src = inspect.getsource(post_verify_post_refresh)
        assert "from tools.post_refresh_verifier" in src
        assert "run_verification" in src

    def test_endpoint_path_is_correct(self):
        """The frontend panel posts to /api/v1/data/verify-post-
        refresh. The route registration must match -- a typo
        here would silently 404 every verification request."""
        from main import app
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/api/v1/data/verify-post-refresh" in paths


# ── Plausibility helpers ──────────────────────────────────────────


class TestFullDatasetPlausibility:

    def test_study_months_in_range(self):
        from tools.post_refresh_verifier import (
            _check_full_dataset_plausibility,
        )
        status, msg = _check_full_dataset_plausibility(
            "{{STUDY_MONTHS}}", "287")
        assert status == "pass"

    def test_study_months_out_of_range(self):
        from tools.post_refresh_verifier import (
            _check_full_dataset_plausibility,
        )
        status, msg = _check_full_dataset_plausibility(
            "{{STUDY_MONTHS}}", "42")
        assert status == "fail"
        status, msg = _check_full_dataset_plausibility(
            "{{STUDY_MONTHS}}", "999")
        assert status == "fail"

    def test_correlation_in_range(self):
        from tools.post_refresh_verifier import (
            _check_full_dataset_plausibility,
        )
        for v in ("-0.05", "0.57", "1.0", "-1.0", "0"):
            status, _ = _check_full_dataset_plausibility(
                "{{PRE_2022_EQ_IG_CORR}}", v)
            assert status == "pass"

    def test_correlation_out_of_range(self):
        from tools.post_refresh_verifier import (
            _check_full_dataset_plausibility,
        )
        status, _ = _check_full_dataset_plausibility(
            "{{PRE_2022_EQ_IG_CORR}}", "1.5")
        assert status == "fail"
        status, _ = _check_full_dataset_plausibility(
            "{{POST_2022_EQ_IG_CORR}}", "-1.1")
        assert status == "fail"
