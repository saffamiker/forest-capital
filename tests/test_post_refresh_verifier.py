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


# ── Hotfix regression -- compute_implied_asset_allocation await ─


class TestRunVerificationAwaitsImpliedAllocation:
    """June 28 2026 hotfix regression pin.

    PR #460 called compute_implied_asset_allocation WITHOUT await.
    Since the helper is async, the unawaited coroutine got passed
    to get_substitution_table as the implied_allocation kwarg,
    and the first downstream .get() on it raised AttributeError
    -> the production 500 on first call after deploy.

    Pin source-inspection that the orchestrator awaits the call.
    A regression that drops the await silently would re-introduce
    the same 500."""

    def test_compute_implied_asset_allocation_is_awaited(self):
        import inspect
        from tools.post_refresh_verifier import run_verification
        src = inspect.getsource(run_verification)
        # The await keyword must precede the call. A naked
        # 'compute_implied_asset_allocation(' without await is
        # the regression we're guarding against.
        assert "await compute_implied_asset_allocation" in src, (
            "run_verification MUST await "
            "compute_implied_asset_allocation -- it is an async "
            "coroutine. Passing the unawaited coroutine to "
            "get_substitution_table as implied_allocation crashes "
            "the substitution table builder on the first .get() "
            "(production 500 on PR #460 first deploy).")

    def test_compute_implied_asset_allocation_is_actually_async(
            self):
        """Belt-and-braces: confirm the helper REALLY is async.
        If a future refactor makes it sync, this test should be
        the first to surface the change so we can also update the
        orchestrator + the source-inspection test above."""
        import asyncio
        from tools.cio_recommendation import (
            compute_implied_asset_allocation,
        )
        assert asyncio.iscoroutinefunction(
            compute_implied_asset_allocation), (
            "compute_implied_asset_allocation is expected to be "
            "async; if a future refactor makes it sync, the "
            "verifier's `await` would itself become a bug -- "
            "update both this test and run_verification together.")

    def test_get_substitution_table_called_with_hash_verified(
            self):
        """June 28 2026 hotfix pin -- the verifier MUST pass
        hash_verified=True to build_substitution_table. It
        explicitly loaded strategy_cache + historical analytics
        via the hash-aware path (get_strategy_cache(eff_hash)),
        so the audit signal that the data_hash is verified-to-
        match must be set. Without this flag,
        build_substitution_table logs the spurious 'data_hash
        supplied without hash_verified=True' warning on every
        verifier call."""
        import inspect
        from tools.post_refresh_verifier import run_verification
        src = inspect.getsource(run_verification)
        assert "hash_verified=True" in src, (
            "run_verification MUST pass hash_verified=True to "
            "get_substitution_table -- the verifier loads "
            "strategy_cache via the hash-aware path so the "
            "audit signal must be set, otherwise every verifier "
            "call logs a spurious 'data_hash supplied without "
            "hash_verified=True' warning.")
