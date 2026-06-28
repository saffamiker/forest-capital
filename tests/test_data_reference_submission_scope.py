"""tests/test_data_reference_submission_scope.py -- June 27 2026.

Pins the submission-scope classification + the live CIO
override on the Data Reference Sheet endpoint (Tasks 1 + 4).

Task 1 -- {{REGIME_CONFIDENCE}} reads from cio_row directly,
bypassing the substitution cache. Same for {{CURRENT_REGIME}}.

Task 4 -- every row carries a submission_scope field derived by
classify_submission_scope(). The endpoint response includes a
submission_scope_summary count + a legend block + the freeze
status fields at the top level.
"""
from __future__ import annotations

import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── Task 4 -- scope classifier ────────────────────────────────────


class TestClassifySubmissionScope:

    def test_full_dataset_tokens_classified_correctly(self):
        from tools.data_reference_catalog import (
            classify_submission_scope, SCOPE_FULL_DATASET,
        )
        for token in (
            "{{STUDY_MONTHS}}", "{{STUDY_START}}",
            "{{STUDY_END}}", "{{PRE_2022_EQ_IG_CORR}}",
            "{{POST_2022_EQ_IG_CORR}}",
        ):
            # The is_locked + source values are irrelevant for
            # FULL_DATASET tokens -- the literal token name is
            # the only signal.
            assert classify_submission_scope(
                token, "any.source", False) == SCOPE_FULL_DATASET
            assert classify_submission_scope(
                token, "any.source", True) == SCOPE_FULL_DATASET

    def test_live_source_prefixes_classified_live(self):
        from tools.data_reference_catalog import (
            classify_submission_scope, SCOPE_LIVE,
        )
        for source in (
            "cio_recommendation.confidence",
            "cio_recommendation.regime",
            "regime_signals_cache.vix_level",
            "data.live_signals.credit_spread",
            "data.cio_row.blend_weights",
        ):
            assert classify_submission_scope(
                "{{ANY_TOKEN}}", source, False) == SCOPE_LIVE

    def test_locked_constant_classified(self):
        from tools.data_reference_catalog import (
            classify_submission_scope, SCOPE_CONSTANT,
        )
        # is_locked=True + non-live source -> CONSTANT
        # (the academic_deck.py hardcoded methodology constants)
        assert classify_submission_scope(
            "{{OOS_SHARPE_BLEND}}",
            "academic_deck.OOS_SHARPE_REGIME_CONDITIONAL",
            True) == SCOPE_CONSTANT

    def test_strategy_cache_falls_through_to_locked(self):
        from tools.data_reference_catalog import (
            classify_submission_scope, SCOPE_LOCKED,
        )
        # is_locked=False + non-live source -> LOCKED
        # (strategy cache, historical analytics, factor loadings)
        for source in (
            "strategy_cache.BENCHMARK.sharpe_ratio",
            "data.regime_conditional.BENCHMARK.sharpe",
            "data.factor_loadings.BENCHMARK.alpha",
            "data.crisis_performance.gfc_drawdown",
        ):
            assert classify_submission_scope(
                "{{ANY_TOKEN}}", source, False) == SCOPE_LOCKED

    def test_resolution_order_live_beats_locked(self):
        """A live-prefix source on an is_locked=True entry
        resolves to LIVE -- the is_locked flag is overruled by
        the explicit live source. Defends against a future
        catalog edit that flips is_locked=True for a live row."""
        from tools.data_reference_catalog import (
            classify_submission_scope, SCOPE_LIVE,
        )
        assert classify_submission_scope(
            "{{CURRENT_REGIME}}",
            "cio_recommendation.regime",
            True) == SCOPE_LIVE

    def test_full_dataset_beats_live(self):
        """STUDY_* + rolling correlation tokens win against the
        live source check -- the literal token name is the most
        specific signal."""
        from tools.data_reference_catalog import (
            classify_submission_scope, SCOPE_FULL_DATASET,
        )
        assert classify_submission_scope(
            "{{STUDY_END}}",
            "cio_recommendation.something",  # would be LIVE
            False) == SCOPE_FULL_DATASET


# ── Task 4 -- legend shape ────────────────────────────────────────


class TestScopeLegendShape:

    def test_legend_carries_all_four_scopes(self):
        from tools.data_reference_catalog import (
            SCOPE_LEGEND, SCOPE_LOCKED, SCOPE_CONSTANT,
            SCOPE_FULL_DATASET, SCOPE_LIVE,
        )
        for scope in (SCOPE_LOCKED, SCOPE_CONSTANT,
                      SCOPE_FULL_DATASET, SCOPE_LIVE):
            assert scope in SCOPE_LEGEND
            entry = SCOPE_LEGEND[scope]
            assert "label" in entry
            assert "description" in entry
            assert "applies_to" in entry

    def test_legend_labels_match_spec(self):
        from tools.data_reference_catalog import SCOPE_LEGEND
        assert SCOPE_LEGEND["IN_SCOPE_LOCKED"]["label"] == (
            "IN SCOPE -- LOCKED")
        assert SCOPE_LEGEND["IN_SCOPE_CONSTANT"]["label"] == (
            "IN SCOPE -- CONSTANT")
        assert SCOPE_LEGEND["IN_SCOPE_FULL_DATASET"]["label"] == (
            "IN SCOPE -- FULL DATASET")
        assert SCOPE_LEGEND["OUT_OF_SCOPE_LIVE"]["label"] == (
            "OUT OF SCOPE -- LIVE")


# ── Task 1 -- live CIO override in _resolve_value ────────────────


class TestDataReferenceLiveCioOverride:

    def test_resolve_value_source_inspection(self):
        """Source-inspection pin -- the data-reference endpoint
        must special-case {{REGIME_CONFIDENCE}} and
        {{CURRENT_REGIME}} to read from cio_row directly. The
        substitution table cache can hold stale values; the
        diagnostic sheet must always reflect the live CIO row."""
        import inspect
        from main import get_data_reference_sheet
        src = inspect.getsource(get_data_reference_sheet)
        # Both tokens must be special-cased in _resolve_value.
        assert 'if token == "{{REGIME_CONFIDENCE}}"' in src, (
            "data-reference endpoint must special-case "
            "{{REGIME_CONFIDENCE}} to read live from cio_row")
        assert 'if token == "{{CURRENT_REGIME}}"' in src, (
            "data-reference endpoint must special-case "
            "{{CURRENT_REGIME}} to read live from cio_row")
        # The override must read from cio_row (not table cache)
        # and stamp last_verified as 'live (from
        # cio_recommendation)' so the reader knows it's not
        # cached.
        assert "live (from cio_recommendation)" in src, (
            "live override must stamp last_verified as "
            "'live (from cio_recommendation)' so the reader "
            "knows the value bypasses the substitution cache")

    def test_endpoint_returns_submission_scope_fields(self):
        """Source-inspection pin -- the endpoint return block
        must carry the new Task 4 top-level fields."""
        import inspect
        from main import get_data_reference_sheet
        src = inspect.getsource(get_data_reference_sheet)
        for field in (
            "freeze_active", "freeze_hash",
            "submission_scope_summary",
            "submission_scope_legend",
        ):
            assert f'"{field}"' in src, (
                f"endpoint response missing Task 4 field: {field}")
