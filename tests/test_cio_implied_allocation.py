"""Bridge #81: implied asset allocation + blend change trigger helpers
that the /api/v1/recommendation endpoint overlays onto the cached CIO
recommendation dict.

  compute_implied_asset_allocation(blend_weights)
    Pure cache read. Multiplies the live blend weights by each
    strategy's avg_equity_weight / avg_bond_weight from
    strategy_results_cache and returns the portfolio-level shares
    {equity_pct, bond_pct, cash_pct} as fractions of 1.0. None when
    the cache is empty or the blend is missing.

  compute_blend_change_trigger(regime, monthly_regime, hmm_models_agree)
    Pure synthesis. Returns one readable sentence describing what
    would shift the live blend. Always returns a string -- when the
    inputs are missing, a generic sentence backs the answer.
"""
from __future__ import annotations

import asyncio
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")


# ── compute_implied_asset_allocation ────────────────────────────────────

class TestImpliedAllocation:
    def test_returns_none_when_blend_is_empty(self):
        from tools.cio_recommendation import (
            compute_implied_asset_allocation,
        )
        assert asyncio.run(compute_implied_asset_allocation(None)) is None
        assert asyncio.run(compute_implied_asset_allocation({})) is None

    def test_returns_none_when_strategy_cache_is_empty(self, monkeypatch):
        from tools.cio_recommendation import (
            compute_implied_asset_allocation,
        )

        async def _empty_cache():
            return {}

        monkeypatch.setattr(
            "tools.cache.get_latest_strategy_cache", _empty_cache)
        out = asyncio.run(compute_implied_asset_allocation(
            {"VOL_TARGETING": 0.5, "MIN_VARIANCE": 0.5}))
        assert out is None

    def test_aggregates_per_strategy_asset_weights(self, monkeypatch):
        """A two-strategy blend with explicit equity / bond shares
        on each strategy aggregates to the weighted average."""
        from tools.cio_recommendation import (
            compute_implied_asset_allocation,
        )

        async def _cache():
            return {
                "VOL_TARGETING": {
                    "avg_equity_weight": 0.4, "avg_bond_weight": 0.6},
                "MIN_VARIANCE": {
                    "avg_equity_weight": 0.2, "avg_bond_weight": 0.8},
            }
        monkeypatch.setattr(
            "tools.cache.get_latest_strategy_cache", _cache)

        out = asyncio.run(compute_implied_asset_allocation(
            {"VOL_TARGETING": 0.5, "MIN_VARIANCE": 0.5}))
        assert out is not None
        # Equity = 0.5*0.4 + 0.5*0.2 = 0.30
        # Bond   = 0.5*0.6 + 0.5*0.8 = 0.70
        # Cash residual = 0
        assert out["equity_pct"] == pytest.approx(0.30)
        assert out["bond_pct"] == pytest.approx(0.70)
        assert out["cash_pct"] == pytest.approx(0.0)

    def test_residual_cash_when_strategies_partial_invested(
        self, monkeypatch,
    ):
        """If a strategy carries cash drag (eq + bd < 1) the helper
        surfaces the un-invested fraction as cash_pct, not silently
        rounding to zero."""
        from tools.cio_recommendation import (
            compute_implied_asset_allocation,
        )

        async def _cache():
            return {
                "VOL_TARGETING": {
                    "avg_equity_weight": 0.3, "avg_bond_weight": 0.5},
            }
        monkeypatch.setattr(
            "tools.cache.get_latest_strategy_cache", _cache)

        out = asyncio.run(compute_implied_asset_allocation(
            {"VOL_TARGETING": 1.0}))
        assert out is not None
        assert out["equity_pct"] == pytest.approx(0.30)
        assert out["bond_pct"] == pytest.approx(0.50)
        # 1.0 - 0.30 - 0.50 = 0.20 cash drag.
        assert out["cash_pct"] == pytest.approx(0.20)

    def test_skips_strategies_with_zero_weight(self, monkeypatch):
        """A strategy whose live weight is zero (or negative) does
        not contribute to the totals."""
        from tools.cio_recommendation import (
            compute_implied_asset_allocation,
        )

        async def _cache():
            return {
                "VOL_TARGETING": {
                    "avg_equity_weight": 0.5, "avg_bond_weight": 0.5},
                "BLACK_LITTERMAN": {
                    "avg_equity_weight": 1.0, "avg_bond_weight": 0.0},
            }
        monkeypatch.setattr(
            "tools.cache.get_latest_strategy_cache", _cache)

        # BLACK_LITTERMAN is in the blend dict but weighted zero --
        # the totals must reflect VOL_TARGETING alone.
        out = asyncio.run(compute_implied_asset_allocation(
            {"VOL_TARGETING": 1.0, "BLACK_LITTERMAN": 0.0}))
        assert out is not None
        assert out["equity_pct"] == pytest.approx(0.5)
        assert out["bond_pct"] == pytest.approx(0.5)


# ── IG/HY split surfaced when the new fields are present ───────────────

class TestImpliedAllocationIgHySplit:
    """June 2026 -- when strategy cache entries carry the explicit
    avg_ig_weight / avg_hy_weight split, the returned dict gains
    ig_bond_pct and hy_bond_pct. ig + hy == bond_pct (within rounding).
    When no contributing strategy carries the new fields, the dict
    falls back to the combined-bonds shape (no ig/hy keys)."""

    def test_returns_ig_hy_when_strategies_carry_the_split(
        self, monkeypatch,
    ):
        from tools.cio_recommendation import (
            compute_implied_asset_allocation,
        )

        async def _cache():
            return {
                # An IG-only strategy: avg_bond is 100% IG.
                "VOL_TARGETING": {
                    "avg_equity_weight": 0.5,
                    "avg_bond_weight":   0.5,
                    "avg_ig_weight":     0.5,
                    "avg_hy_weight":     0.0,
                },
                # A mixed-bond strategy: 50/50 IG/HY of the bond leg.
                "EQUAL_WEIGHT": {
                    "avg_equity_weight": 0.333,
                    "avg_bond_weight":   0.667,
                    "avg_ig_weight":     0.333,
                    "avg_hy_weight":     0.333,
                },
            }
        monkeypatch.setattr(
            "tools.cache.get_latest_strategy_cache", _cache)

        # 50/50 blend.
        out = asyncio.run(compute_implied_asset_allocation(
            {"VOL_TARGETING": 0.5, "EQUAL_WEIGHT": 0.5}))
        assert out is not None
        # Equity = 0.5*0.5 + 0.5*0.333 = 0.4165
        assert out["equity_pct"] == pytest.approx(0.4165, abs=1e-3)
        # IG = 0.5*0.5 + 0.5*0.333 = 0.4165
        assert out["ig_bond_pct"] == pytest.approx(0.4165, abs=1e-3)
        # HY = 0.5*0.0 + 0.5*0.333 = 0.1665
        assert out["hy_bond_pct"] == pytest.approx(0.1665, abs=1e-3)
        # ig + hy == bond_pct (within rounding tolerance).
        assert (out["ig_bond_pct"] + out["hy_bond_pct"]) == pytest.approx(
            out["bond_pct"], abs=1e-2)

    def test_omits_ig_hy_when_no_strategy_carries_the_split(
        self, monkeypatch,
    ):
        from tools.cio_recommendation import (
            compute_implied_asset_allocation,
        )

        async def _cache():
            return {
                # Pre-backfill: only the combined avg_bond_weight.
                "VOL_TARGETING": {
                    "avg_equity_weight": 0.5,
                    "avg_bond_weight":   0.5,
                },
            }
        monkeypatch.setattr(
            "tools.cache.get_latest_strategy_cache", _cache)

        out = asyncio.run(compute_implied_asset_allocation(
            {"VOL_TARGETING": 1.0}))
        assert out is not None
        assert out["equity_pct"] == pytest.approx(0.5)
        assert out["bond_pct"] == pytest.approx(0.5)
        # The frontend uses presence of ig_bond_pct as the
        # back-compat switch -- it MUST be absent on the old shape.
        assert "ig_bond_pct" not in out
        assert "hy_bond_pct" not in out


# ── compute_blend_change_trigger ────────────────────────────────────────

class TestBlendChangeTrigger:
    def test_bear_regime_uses_de_risk_sentence(self):
        from tools.cio_recommendation import (
            compute_blend_change_trigger,
        )
        s = compute_blend_change_trigger(
            regime="BEAR", monthly_regime="BEAR", hmm_models_agree=True)
        assert "de-risk" in s.lower() or "low-beta" in s.lower() \
            or "bear" in s.lower()
        assert "vix" in s.lower()

    def test_bull_regime_uses_risk_on_sentence(self):
        from tools.cio_recommendation import (
            compute_blend_change_trigger,
        )
        s = compute_blend_change_trigger(
            regime="BULL", monthly_regime="BULL", hmm_models_agree=True)
        assert "bull" in s.lower()
        assert "vix" in s.lower()

    def test_transition_regime_uses_neutral_sentence(self):
        from tools.cio_recommendation import (
            compute_blend_change_trigger,
        )
        s = compute_blend_change_trigger(
            regime="TRANSITION", monthly_regime="TRANSITION",
            hmm_models_agree=True)
        assert "transition" in s.lower() or "neutral" in s.lower()

    def test_unknown_regime_falls_back_to_generic_sentence(self):
        from tools.cio_recommendation import (
            compute_blend_change_trigger,
        )
        s = compute_blend_change_trigger(
            regime=None, monthly_regime=None, hmm_models_agree=True)
        assert s  # never empty
        assert "vix" in s.lower()

    def test_divergent_models_lead_with_divergence_sentence(self):
        """When daily HMM != monthly HMM, the trigger sentence names
        the split FIRST -- the next blend shift is whichever model
        concedes."""
        from tools.cio_recommendation import (
            compute_blend_change_trigger,
        )
        s = compute_blend_change_trigger(
            regime="BEAR", monthly_regime="BULL",
            hmm_models_agree=False)
        assert "diverge" in s.lower() or "disagree" in s.lower() \
            or ("BEAR" in s and "BULL" in s)
        # The two regime labels both appear in the sentence so the
        # reader can SEE the split.
        assert "BEAR" in s and "BULL" in s

    def test_returns_a_single_sentence_no_data_dump(self):
        """One sentence is the contract -- not a bullet list."""
        from tools.cio_recommendation import (
            compute_blend_change_trigger,
        )
        for inputs in (
            ("BEAR", "BEAR", True),
            ("BULL", "BULL", True),
            ("TRANSITION", "TRANSITION", True),
            ("BEAR", "BULL", False),
            (None, None, True),
        ):
            s = compute_blend_change_trigger(*inputs)
            # The string contains no newlines and ends with a period.
            assert "\n" not in s
            assert s.endswith(".")
            # Short enough to fit on a CIO card row (under 300 chars).
            assert len(s) < 300
