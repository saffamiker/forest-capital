"""tests/test_three_strategy_submission_scope.py -- June 29 2026.

Pins for the three-strategy submission-scope restriction:
brief, appendix, deck, and script all operate on only
BENCHMARK / CLASSIC_60_40 / REGIME_SWITCHING.

  * Central filter helper _filter_to_submission_scope
  * SUBMISSION_STRATEGIES constant
  * STORY_PLAN_VERSION bump + cache-key versioning
  * Figure caption accuracy (especially strategy_comparison
    no longer mislabelled as OOS Sharpe)
"""
from __future__ import annotations

import inspect
import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── Filter helper ────────────────────────────────────────


class TestSubmissionScopeFilter:

    def test_constant_includes_three_strategies(self):
        from tools.academic_export import SUBMISSION_STRATEGIES
        assert SUBMISSION_STRATEGIES == frozenset({
            "BENCHMARK", "CLASSIC_60_40", "REGIME_SWITCHING"})

    def test_filter_strategy_results_dict(self):
        from tools.academic_export import (
            _filter_to_submission_scope,
        )
        bundle = {"strategy_results": {
            "BENCHMARK":           {"sharpe": 0.43},
            "CLASSIC_60_40":       {"sharpe": 0.54},
            "REGIME_SWITCHING":    {"sharpe": 0.86},
            "MIN_VARIANCE":        {"sharpe": 0.71},
            "RISK_PARITY":         {"sharpe": 0.62},
            "VOL_TARGETING":       {"sharpe": 0.58},
        }}
        out = _filter_to_submission_scope(bundle)
        assert set(out["strategy_results"].keys()) == {
            "BENCHMARK", "CLASSIC_60_40", "REGIME_SWITCHING"}

    def test_filter_list_rows_by_strategy_field(self):
        from tools.academic_export import (
            _filter_to_submission_scope,
        )
        bundle = {
            "summary_statistics": [
                {"strategy": "BENCHMARK", "x": 1},
                {"strategy": "MIN_VARIANCE", "x": 2},
                {"strategy": "CLASSIC_60_40", "x": 3},
            ],
            "factor_loadings": [
                {"strategy": "REGIME_SWITCHING", "alpha": 1.2},
                {"strategy": "MAX_SHARPE_ROLLING", "alpha": 0.5},
            ],
        }
        out = _filter_to_submission_scope(bundle)
        assert [r["x"] for r in out["summary_statistics"]] == (
            [1, 3])
        assert [r["alpha"] for r in out["factor_loadings"]] == (
            [1.2])

    def test_filter_list_rows_by_strategy_name_field(self):
        """regime_conditional uses 'strategy_name' field
        instead of 'strategy'. Filter must match both."""
        from tools.academic_export import (
            _filter_to_submission_scope,
        )
        bundle = {"regime_conditional": [
            {"strategy_name": "BENCHMARK"},
            {"strategy_name": "EQUAL_WEIGHT"},
            {"strategy_name": "REGIME_SWITCHING"},
        ]}
        out = _filter_to_submission_scope(bundle)
        names = [r["strategy_name"]
                 for r in out["regime_conditional"]]
        assert names == ["BENCHMARK", "REGIME_SWITCHING"]

    def test_filter_crisis_performance_rows_dict(self):
        from tools.academic_export import (
            _filter_to_submission_scope,
        )
        bundle = {"crisis_performance": {
            "windows": {"GFC": {}, "COVID": {}},
            "rows": {
                "BENCHMARK":           {"GFC": {}},
                "CLASSIC_60_40":       {"GFC": {}},
                "MIN_VARIANCE":        {"GFC": {}},
                "REGIME_SWITCHING":    {"GFC": {}},
                "MAX_SHARPE_ROLLING":  {"GFC": {}},
            },
        }}
        out = _filter_to_submission_scope(bundle)
        assert set(out["crisis_performance"]["rows"].keys()) == {
            "BENCHMARK", "CLASSIC_60_40", "REGIME_SWITCHING"}
        # windows is non-strategy data + passes through.
        assert "windows" in out["crisis_performance"]

    def test_filter_no_op_when_no_strategy_keys(self):
        from tools.academic_export import (
            _filter_to_submission_scope,
        )
        bundle = {"misc": "data", "n_observations": 287}
        out = _filter_to_submission_scope(bundle)
        # Bundle unchanged (no strategy-bearing surfaces).
        assert out == bundle

    def test_gather_document_data_applies_filter(self):
        """Source-pin: gather_document_data calls the filter
        at the end so every consumer sees the 3-strategy
        bundle."""
        import inspect as _i
        from tools.academic_export import gather_document_data
        src = _i.getsource(gather_document_data)
        assert "_filter_to_submission_scope(bundle)" in src

    def test_gather_analytical_appendix_data_applies_filter(
            self):
        import inspect as _i
        from tools.academic_export import (
            gather_analytical_appendix_data,
        )
        src = _i.getsource(gather_analytical_appendix_data)
        assert "_filter_to_submission_scope(bundle)" in src


# ── Story plan cache invalidation ───────────────────────


class TestStoryPlanVersionBump:

    def test_version_constant_present_and_bumped(self):
        from tools.story_plan import STORY_PLAN_VERSION
        # Bumped to v2 with this PR (3-strategy scope).
        assert STORY_PLAN_VERSION >= 2

    def test_versioned_document_type_helper(self):
        from tools.story_plan import (
            STORY_PLAN_VERSION, _versioned_document_type,
        )
        out = _versioned_document_type("brief")
        assert out == f"brief_v{STORY_PLAN_VERSION}"

    def test_cache_read_uses_versioned_key(self):
        """Source-pin: get_cached_story_plan binds the version-
        suffixed document_type when querying."""
        from tools.story_plan import get_cached_story_plan
        src = inspect.getsource(get_cached_story_plan)
        assert "_versioned_document_type(document_type)" in src

    def test_cache_write_uses_versioned_key(self):
        from tools.story_plan import persist_story_plan
        src = inspect.getsource(persist_story_plan)
        assert "_versioned_document_type(document_type)" in src


# ── Figure caption accuracy ──────────────────────────────


class TestFigureCaptionsAccurate:

    def test_strategy_comparison_no_longer_mislabelled(self):
        """The renderer pulls post_2022_sharpe, so the caption
        should say 'Post-2022 Sharpe' not 'Out-of-Sample
        Sharpe Ratio Comparison'."""
        import tools.academic_docx as _docx
        src = inspect.getsource(_docx)
        # New form is present.
        assert (
            "Post-2022 Sharpe Ratio Comparison by Strategy" in src)
        # The old mislabel is gone.
        assert (
            "Out-of-Sample Sharpe Ratio Comparison, "
            "{{OOS_WINDOW}}" not in src)

    def test_cumulative_returns_describes_growth_of_dollar(
            self):
        import tools.academic_docx as _docx
        src = inspect.getsource(_docx)
        assert "Growth of $1 invested at inception" in src

    def test_rolling_correlation_describes_axes(self):
        import tools.academic_docx as _docx
        src = inspect.getsource(_docx)
        assert (
            "Rolling 12-month correlation between monthly "
            "returns" in src)

    def test_efficient_frontier_describes_scatter(self):
        import tools.academic_docx as _docx
        src = inspect.getsource(_docx)
        assert (
            "Scatter plot of each strategy's annualised "
            "return" in src)


# ── Prompt edits reflect new scope ───────────────────────


class TestPromptsReferToThreeStrategyScope:

    def test_brief_key_findings_drops_ten_strategy_set(self):
        import main as _main
        with open(
                _main.__file__, encoding="utf-8") as f:
            src = f.read()
        # The brief_key_findings task references the new
        # three-strategy submission set.
        assert (
            "three-strategy submission set" in src
            or "BENCHMARK, CLASSIC_60_40, REGIME_SWITCHING"
            in src)

    def _runtime_main_source(self) -> str:
        """Collapse Python string-literal continuations the
        same way other prompt-text tests do (see
        test_executive_brief_structure._runtime_main_source).
        Required because the appendix framing prelude breaks
        the strategy enumeration across line continuations."""
        import re as _re
        import main as _main
        with open(
                _main.__file__, encoding="utf-8") as f:
            raw = f.read()
        return _re.sub(r'"\s*\n\s+"', '', raw)

    def test_appendix_framing_includes_scope_note(self):
        src = self._runtime_main_source()
        idx = src.find("_APPENDIX_FRAMING_PRELUDE")
        assert idx > -1
        slice_ = src[idx:idx + 2000]
        # Concatenated runtime form: the three strategies
        # named in the framing prelude prose.
        assert "BENCHMARK, CLASSIC_60_40, REGIME_SWITCHING" in (
            slice_)

    def test_appendix_placeholder_guide_lists_three_only(self):
        src = self._runtime_main_source()
        # Target the DEFINITION of the constant, not earlier
        # usages of the same name in the spec-build block above.
        idx = src.find(
            "_APPENDIX_NUMERIC_PLACEHOLDER_GUIDE_EXTENSION = (")
        assert idx > -1
        slice_ = src[idx:idx + 1500]
        assert (
            "BENCHMARK, CLASSIC_60_40, REGIME_SWITCHING"
            in slice_)
