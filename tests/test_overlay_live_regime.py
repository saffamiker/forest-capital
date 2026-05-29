"""Regression: _overlay_live_regime call-shape contract.

The forward-projection read endpoint called the overlay with TWO
positional args (`_overlay_live_regime(proj, proj, prob_key=...)`) while
the signature accepts ONE positional plus the keyword-only prob_key. That
raised a TypeError at call binding — NOT caught by the function's internal
fail-open guard — so the endpoint's outer except swallowed it and returned
{"available": False}. A forward_projection row present in the DB therefore
read as "not computed yet" on the live tile.

These tests pin both call shapes (the CIO card's `probability` and the
forward tile's `regime_probability`) so a re-introduction of the extra
positional fails fast here instead of silently blanking a live tile.
"""
import pytest

import main


@pytest.mark.asyncio
async def test_overlay_writes_regime_probability(monkeypatch):
    """The forward-projection call shape: one positional + regime_probability."""
    monkeypatch.setattr(
        "tools.regime_detector.detect_current_regime",
        lambda: {"hmm_regime": "BULL", "hmm_probabilities": {"BULL": 0.91}},
    )
    target = {"bands": {}, "regime_probability": 0.5}
    await main._overlay_live_regime(target, prob_key="regime_probability")
    assert target["regime"] == "BULL"
    assert target["regime_probability"] == 0.91


@pytest.mark.asyncio
async def test_overlay_writes_probability_default_key(monkeypatch):
    """The CIO card call shape: one positional + default prob_key."""
    monkeypatch.setattr(
        "tools.regime_detector.detect_current_regime",
        lambda: {"hmm_regime": "BEAR", "hmm_probabilities": {"BEAR": 0.77}},
    )
    target = {"probability": 0.1}
    await main._overlay_live_regime(target, prob_key="probability")
    assert target["regime"] == "BEAR"
    assert target["probability"] == 0.77


@pytest.mark.asyncio
async def test_overlay_fail_open_leaves_target_untouched(monkeypatch):
    """A detector error must leave the cached values untouched, not raise."""
    def _boom():
        raise RuntimeError("FRED down")

    monkeypatch.setattr(
        "tools.regime_detector.detect_current_regime", _boom)
    target = {"regime": "cached", "regime_probability": 0.42}
    await main._overlay_live_regime(target, prob_key="regime_probability")
    assert target["regime"] == "cached"
    assert target["regime_probability"] == 0.42
