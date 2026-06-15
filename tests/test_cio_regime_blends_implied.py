"""tests/test_cio_regime_blends_implied.py -- June 8 2026.

Per-regime blend-shift implied splits + delta from the live portfolio.

The /api/v1/recommendation endpoint overlays compute_regime_blends_
implied(regime_blends, current_implied) onto the recommendation
payload so the CIO card and the daily digest render the BULL / BEAR
/ TRANSITION blend shifts with their equity/bond translation AND the
delta-from-current-portfolio (in percentage points), without re-
computing client-side.

These tests pin the helper's contract.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")


class TestComputeRegimeBlendsImplied:

    def test_returns_none_when_regime_blends_missing(self):
        from tools.cio_recommendation import compute_regime_blends_implied
        assert asyncio.run(compute_regime_blends_implied(None, None)) is None
        assert asyncio.run(compute_regime_blends_implied({}, None)) is None

    def test_returns_none_when_strategy_cache_empty(self, monkeypatch):
        from tools.cio_recommendation import compute_regime_blends_implied

        async def _empty_cache():
            return {}

        monkeypatch.setattr(
            "tools.cache.get_latest_strategy_cache", _empty_cache)
        out = asyncio.run(compute_regime_blends_implied(
            {"BULL": {"VOL_TARGETING": 1.0}}, None))
        assert out is None

    def test_computes_implied_split_per_regime(self, monkeypatch):
        """Two strategies with KNOWN per-strategy equity/bond weights:
        confirm the regime-level implied split is the weighted average
        of those per-strategy weights."""
        from tools.cio_recommendation import compute_regime_blends_implied

        async def _cache():
            return {
                "VOL_TARGETING":  {"avg_equity_weight": 0.40,
                                   "avg_bond_weight":   0.60},
                "MIN_VARIANCE":   {"avg_equity_weight": 0.20,
                                   "avg_bond_weight":   0.80},
            }
        monkeypatch.setattr(
            "tools.cache.get_latest_strategy_cache", _cache)

        regime_blends = {
            # BULL is 50/50 -> equity = 0.5*0.4 + 0.5*0.2 = 0.30,
            #                  bond   = 0.5*0.6 + 0.5*0.8 = 0.70
            "BULL": {"VOL_TARGETING": 0.5, "MIN_VARIANCE": 0.5},
            # BEAR leans defensive -> 25/75
            #   equity = 0.25*0.4 + 0.75*0.2 = 0.10 + 0.15 = 0.25,
            #   bond   = 0.25*0.6 + 0.75*0.8 = 0.15 + 0.60 = 0.75
            "BEAR": {"VOL_TARGETING": 0.25, "MIN_VARIANCE": 0.75},
        }
        out = asyncio.run(compute_regime_blends_implied(
            regime_blends, None))
        assert out is not None
        assert out["BULL"]["equity_pct"] == 0.30
        assert out["BULL"]["bond_pct"] == 0.70
        assert out["BEAR"]["equity_pct"] == 0.25
        assert out["BEAR"]["bond_pct"] == 0.75
        # Without current_implied, the delta fields are absent (the
        # frontend / digest will omit the delta line).
        assert "equity_delta_pp" not in out["BULL"]

    def test_delta_uses_percentage_points_not_fractions(self, monkeypatch):
        """The delta MUST be in percentage points (pp) so the UI can
        render "+35.6pp" without re-multiplying by 100. The frontend
        does NOT multiply this field; it formats it verbatim."""
        from tools.cio_recommendation import compute_regime_blends_implied

        async def _cache():
            return {
                "DYN":  {"avg_equity_weight": 0.80,
                         "avg_bond_weight":   0.20},
            }
        monkeypatch.setattr(
            "tools.cache.get_latest_strategy_cache", _cache)

        regime_blends = {"BULL": {"DYN": 1.0}}
        # Current portfolio sits at 32% equity / 68% bonds.
        current = {"equity_pct": 0.324, "bond_pct": 0.676}
        out = asyncio.run(compute_regime_blends_implied(
            regime_blends, current))
        assert out is not None
        # Regime equity = 0.80; current = 0.324
        # Delta = (0.80 - 0.324) * 100 = 47.6 pp
        assert out["BULL"]["equity_delta_pp"] == 47.6
        # Regime bond = 0.20; current = 0.676
        # Delta = (0.20 - 0.676) * 100 = -47.6 pp
        assert out["BULL"]["bond_delta_pp"] == -47.6

    def test_passes_through_weights_for_render(self, monkeypatch):
        """The helper passes the per-regime strategy weights through
        unchanged so the frontend can render the same '<STRAT> %' list
        the existing digest path renders. No mutation, no truncation."""
        from tools.cio_recommendation import compute_regime_blends_implied

        async def _cache():
            return {
                "A": {"avg_equity_weight": 0.5, "avg_bond_weight": 0.5},
                "B": {"avg_equity_weight": 0.5, "avg_bond_weight": 0.5},
                "C": {"avg_equity_weight": 0.5, "avg_bond_weight": 0.5},
            }
        monkeypatch.setattr(
            "tools.cache.get_latest_strategy_cache", _cache)

        regime_blends = {
            "BULL": {"A": 0.40, "B": 0.40, "C": 0.20},
        }
        out = asyncio.run(compute_regime_blends_implied(
            regime_blends, None))
        assert out is not None
        assert out["BULL"]["weights"] == {"A": 0.40, "B": 0.40, "C": 0.20}

    def test_skips_regime_with_empty_weights(self, monkeypatch):
        """A regime whose blend is empty (or all-zero weights) is
        dropped from the output so the caller renders only regimes
        with usable data."""
        from tools.cio_recommendation import compute_regime_blends_implied

        async def _cache():
            return {
                "A": {"avg_equity_weight": 0.6, "avg_bond_weight": 0.4},
            }
        monkeypatch.setattr(
            "tools.cache.get_latest_strategy_cache", _cache)

        regime_blends = {
            "BULL":       {"A": 1.0},
            "BEAR":       {},                 # empty -> dropped
            "TRANSITION": {"A": 0.0},         # all-zero -> dropped
        }
        out = asyncio.run(compute_regime_blends_implied(
            regime_blends, None))
        assert out is not None
        assert set(out.keys()) == {"BULL"}

    def test_returns_none_when_no_regime_produces_a_valid_blend(
        self, monkeypatch,
    ):
        from tools.cio_recommendation import compute_regime_blends_implied

        async def _cache():
            return {"A": {"avg_equity_weight": 0.5, "avg_bond_weight": 0.5}}
        monkeypatch.setattr(
            "tools.cache.get_latest_strategy_cache", _cache)
        # Every regime has zero-weight blends -> compute_regime_blends_implied
        # drops them all and returns None so the caller omits the section.
        out = asyncio.run(compute_regime_blends_implied(
            {"BULL": {"A": 0.0}, "BEAR": {}}, None))
        assert out is None
