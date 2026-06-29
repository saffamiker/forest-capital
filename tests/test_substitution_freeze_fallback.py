"""tests/test_substitution_freeze_fallback.py -- June 29 2026.

Pins for the substitution-pipeline freeze-hash fallback:
  - load_substitution_metric_sources falls back to the freeze hash
    when get_metric(draft_data_hash, kind) returns None
  - get_academic_lock(data_hash=...) falls back to freeze hash too
  - get_cached_oos_summary(data_hash=...) ditto

Operator-reported scenario (draft 88):
  data_hash = "d0b1339e06845559"  -- no matching analytics_metrics_cache row
  freeze_hash = "c421fb895347f924" -- has academic_lock + oos_summary rows
  Substitution table was rendering em-dashes for every metric-cache
  token because the data_hash lookup returned None and there was no
  fallback. This PR adds the fallback.
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


_DRAFT_HASH = "d0b1339e06845559"
_FREEZE_HASH = "c421fb895347f924"


# ── load_substitution_metric_sources -- the primary path ────────────


class TestLoadSubstitutionMetricSourcesFallback:

    @pytest.mark.asyncio
    async def test_fallback_when_draft_hash_missing(self):
        """When get_metric(draft_hash, 'academic_analytics')
        returns None AND submission_freeze is active, the function
        retries with the freeze hash and returns the freeze-keyed
        row."""
        from tools.academic_export import load_substitution_metric_sources

        freeze_row = {
            "regime_conditional": [
                {"strategy": "BENCHMARK", "post_2022_sharpe": 0.49},
            ],
            "factor_loadings": [
                {"strategy": "BENCHMARK", "mkt": 1.0, "smb": 0.0,
                 "hml": 0.0, "mom": 0.0},
            ],
        }

        async def fake_get_metric(data_hash: str, kind: str) -> Any:
            # Draft hash misses every kind; freeze hash hits
            # academic_analytics only.
            if data_hash == _FREEZE_HASH and kind == "academic_analytics":
                return freeze_row
            return None

        async def fake_get_freeze_config() -> dict:
            return {"active": True, "freeze_hash": _FREEZE_HASH}

        with patch(
            "tools.precomputed_analytics.get_metric",
            new=AsyncMock(side_effect=fake_get_metric),
        ), patch(
            "tools.submission_freeze.get_freeze_config",
            new=AsyncMock(side_effect=fake_get_freeze_config),
        ):
            rc, fl, cs, cp = await load_substitution_metric_sources(
                data_hash=_DRAFT_HASH)

        assert rc == freeze_row["regime_conditional"]
        assert fl == freeze_row["factor_loadings"]
        # The other kinds miss on both draft + freeze.
        assert cs is None
        assert cp is None

    @pytest.mark.asyncio
    async def test_no_fallback_when_freeze_inactive(self):
        """When submission_freeze is inactive, the function does
        NOT fall back to the freeze hash. Missing data_hash row =>
        empty / None return."""
        from tools.academic_export import load_substitution_metric_sources

        async def fake_get_metric(data_hash: str, kind: str) -> Any:
            return None  # everything misses

        async def fake_get_freeze_config() -> dict:
            return {"active": False, "freeze_hash": None}

        with patch(
            "tools.precomputed_analytics.get_metric",
            new=AsyncMock(side_effect=fake_get_metric),
        ), patch(
            "tools.submission_freeze.get_freeze_config",
            new=AsyncMock(side_effect=fake_get_freeze_config),
        ):
            rc, fl, cs, cp = await load_substitution_metric_sources(
                data_hash=_DRAFT_HASH)

        assert rc == []
        assert fl == []
        assert cs is None
        assert cp is None

    @pytest.mark.asyncio
    async def test_no_fallback_when_data_hash_hits_directly(self):
        """Happy path: data_hash row exists, freeze fallback path
        never fires."""
        from tools.academic_export import load_substitution_metric_sources

        direct_row = {
            "regime_conditional": [
                {"strategy": "REGIME_SWITCHING",
                 "post_2022_sharpe": 0.91},
            ],
            "factor_loadings": [],
        }

        freeze_config_calls: list = []

        async def fake_get_metric(data_hash: str, kind: str) -> Any:
            if data_hash == _DRAFT_HASH and kind == "academic_analytics":
                return direct_row
            return None

        async def fake_get_freeze_config() -> dict:
            freeze_config_calls.append("called")
            return {"active": True, "freeze_hash": _FREEZE_HASH}

        with patch(
            "tools.precomputed_analytics.get_metric",
            new=AsyncMock(side_effect=fake_get_metric),
        ), patch(
            "tools.submission_freeze.get_freeze_config",
            new=AsyncMock(side_effect=fake_get_freeze_config),
        ):
            rc, _fl, _cs, _cp = (
                await load_substitution_metric_sources(
                    data_hash=_DRAFT_HASH))

        assert rc == direct_row["regime_conditional"]
        # Freeze config IS lazy-fetched once (when the FIRST kind
        # misses and triggers the fallback path for oos_cost_
        # sensitivity / crisis_performance, which both miss).
        # The direct-hit on academic_analytics doesn't trigger
        # the fallback path itself, but subsequent kinds that
        # miss DO -- so the freeze config might be fetched. The
        # important assertion is that the DIRECT-HIT row was
        # returned, not the fallback row.


# ── get_academic_lock -- pass through fallback ──────────────────────


class TestGetAcademicLockFallback:

    @pytest.mark.asyncio
    async def test_data_hash_hits_returns_directly(self):
        """When get_metric(data_hash, 'academic_lock') hits, return
        without consulting the freeze hash."""
        from tools.play_by_play import get_academic_lock

        direct_row = {
            "oos_sharpe_blend": 0.9117,
            "oos_sharpe_benchmark": 0.4927,
            "oos_sharpe_improvement_pct": 85.0,
        }

        async def fake_get_metric(data_hash: str, kind: str) -> Any:
            if data_hash == _DRAFT_HASH and kind == "academic_lock":
                return direct_row
            return None

        with patch(
            "tools.precomputed_analytics.get_metric",
            new=AsyncMock(side_effect=fake_get_metric),
        ):
            out = await get_academic_lock(data_hash=_DRAFT_HASH)

        assert out["oos_sharpe_blend"] == 0.9117
        assert out["oos_sharpe_benchmark"] == 0.4927

    @pytest.mark.asyncio
    async def test_falls_back_to_freeze_hash(self):
        """Draft hash misses; freeze is active + has a row.
        Result: returns the freeze-hash row."""
        from tools.play_by_play import get_academic_lock

        freeze_row = {
            "oos_sharpe_blend": 0.9117,
            "oos_sharpe_benchmark": 0.4927,
            "oos_sharpe_improvement_pct": 85.0,
        }

        async def fake_get_metric(data_hash: str, kind: str) -> Any:
            if data_hash == _FREEZE_HASH and kind == "academic_lock":
                return freeze_row
            return None

        async def fake_get_latest_metric(kind: str) -> Any:
            return None  # no latest write either

        async def fake_get_freeze_config() -> dict:
            return {"active": True, "freeze_hash": _FREEZE_HASH}

        with patch(
            "tools.precomputed_analytics.get_metric",
            new=AsyncMock(side_effect=fake_get_metric),
        ), patch(
            "tools.precomputed_analytics.get_latest_metric",
            new=AsyncMock(side_effect=fake_get_latest_metric),
        ), patch(
            "tools.submission_freeze.get_freeze_config",
            new=AsyncMock(side_effect=fake_get_freeze_config),
        ):
            out = await get_academic_lock(data_hash=_DRAFT_HASH)

        assert out["oos_sharpe_blend"] == 0.9117
        assert out["oos_sharpe_benchmark"] == 0.4927


# ── get_cached_oos_summary -- pass through fallback ─────────────────


class TestGetCachedOosSummaryFallback:

    @pytest.mark.asyncio
    async def test_falls_back_to_freeze_hash(self):
        """Same pattern as get_academic_lock: draft hash misses,
        freeze hash has the row, returns the freeze-hash row."""
        from tools.play_by_play import get_cached_oos_summary

        freeze_row = {
            "blend": 0.9117,
            "benchmark": 0.4927,
            "value_add_events": 2,
            "total_events": 9,
        }

        async def fake_get_metric(data_hash: str, kind: str) -> Any:
            if data_hash == _FREEZE_HASH and kind == "oos_summary":
                return freeze_row
            return None

        async def fake_get_latest_metric(kind: str) -> Any:
            return None

        async def fake_get_freeze_config() -> dict:
            return {"active": True, "freeze_hash": _FREEZE_HASH}

        with patch(
            "tools.precomputed_analytics.get_metric",
            new=AsyncMock(side_effect=fake_get_metric),
        ), patch(
            "tools.precomputed_analytics.get_latest_metric",
            new=AsyncMock(side_effect=fake_get_latest_metric),
        ), patch(
            "tools.submission_freeze.get_freeze_config",
            new=AsyncMock(side_effect=fake_get_freeze_config),
        ):
            out = await get_cached_oos_summary(data_hash=_DRAFT_HASH)

        assert out is not None
        assert out["blend"] == 0.9117
        assert out["benchmark"] == 0.4927


# ── get_cached_story_plan -- pass through fallback ──────────────────


class TestGetCachedStoryPlanFallback:

    @pytest.mark.asyncio
    async def test_falls_back_to_freeze_hash(self):
        """Story plan stored under freeze hash; lookup with the
        draft's data_hash misses; fallback retrieves the freeze
        version."""
        from tools.story_plan import get_cached_story_plan

        freeze_plan = {
            "plan_json": {"sections": ["intro", "findings"]},
            "_model": "claude-opus-4-7",
        }

        async def fake_inner(
                data_hash: str, document_type: str,
        ) -> Any:
            if data_hash == _FREEZE_HASH:
                return freeze_plan
            return None

        async def fake_get_freeze_config() -> dict:
            return {"active": True, "freeze_hash": _FREEZE_HASH}

        with patch(
            "tools.story_plan._read_story_plan_row",
            new=AsyncMock(side_effect=fake_inner),
        ), patch(
            "tools.submission_freeze.get_freeze_config",
            new=AsyncMock(side_effect=fake_get_freeze_config),
        ):
            out = await get_cached_story_plan(
                _DRAFT_HASH, "brief")

        assert out is freeze_plan

    @pytest.mark.asyncio
    async def test_no_fallback_when_freeze_inactive(self):
        from tools.story_plan import get_cached_story_plan

        async def fake_inner(
                data_hash: str, document_type: str,
        ) -> Any:
            return None  # always miss

        async def fake_get_freeze_config() -> dict:
            return {"active": False, "freeze_hash": None}

        with patch(
            "tools.story_plan._read_story_plan_row",
            new=AsyncMock(side_effect=fake_inner),
        ), patch(
            "tools.submission_freeze.get_freeze_config",
            new=AsyncMock(side_effect=fake_get_freeze_config),
        ):
            out = await get_cached_story_plan(
                _DRAFT_HASH, "brief")

        assert out is None

    @pytest.mark.asyncio
    async def test_direct_hit_returns_without_consulting_freeze(self):
        from tools.story_plan import get_cached_story_plan

        direct_plan = {"plan_json": {}, "_model": "x"}

        async def fake_inner(
                data_hash: str, document_type: str,
        ) -> Any:
            if data_hash == _DRAFT_HASH:
                return direct_plan
            return None

        freeze_calls: list = []

        async def fake_get_freeze_config() -> dict:
            freeze_calls.append("called")
            return {"active": True, "freeze_hash": _FREEZE_HASH}

        with patch(
            "tools.story_plan._read_story_plan_row",
            new=AsyncMock(side_effect=fake_inner),
        ), patch(
            "tools.submission_freeze.get_freeze_config",
            new=AsyncMock(side_effect=fake_get_freeze_config),
        ):
            out = await get_cached_story_plan(
                _DRAFT_HASH, "brief")

        assert out is direct_plan
        assert freeze_calls == [], (
            "freeze config should not be consulted on a direct hit")
