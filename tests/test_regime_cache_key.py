"""Regime-aware recommendation cache key — unit tests.

Covers the four behavioural assertions from the spec — same key = hit, same
hash with a regime flip = miss, same hash with confidence crossing a bucket
boundary = miss, same hash with within-bucket noise = hit — plus the
underlying bucket / key / miss-reason helpers.

All tests are deterministic: detect_current_regime, _current_data_hash, and
get_cached_for_hash are monkeypatched per case, and the background
schedule_regime_aware_refresh is intercepted so no LLM ever fires.
"""
import pytest

from tools import cio_recommendation as r


# ── helpers ────────────────────────────────────────────────────────────────

def test_confidence_bucket_round_half_up():
    assert r._confidence_bucket(0.89) == 90
    assert r._confidence_bucket(0.84) == 80
    assert r._confidence_bucket(0.85) == 90  # round-half-up, not banker's
    assert r._confidence_bucket(0.92) == 90
    assert r._confidence_bucket(1.0) == 100
    assert r._confidence_bucket(0.0) == 0
    assert r._confidence_bucket(None) is None


def test_regime_cache_key_formatting():
    assert r.regime_cache_key("32862b2a", "TRANSITION", 0.89) \
        == "32862b2a_TRANSITION_90"
    assert r.regime_cache_key("32862b2a", "TRANSITION", 0.84) \
        == "32862b2a_TRANSITION_80"
    assert r.regime_cache_key("32862b2a", "BULL", 0.92) \
        == "32862b2a_BULL_90"
    # Falls back to the bare hash when regime / confidence is missing.
    assert r.regime_cache_key("32862b2a", None, 0.5) == "32862b2a"
    assert r.regime_cache_key("32862b2a", "BULL", None) == "32862b2a"


def test_miss_reason_classifies_change():
    assert r._miss_reason(
        "h_BULL_90", "h_TRANSITION_90", "h", "BULL", 0.92) == "regime_shift"
    assert r._miss_reason(
        "h_TRANSITION_80", "h_TRANSITION_90", "h", "TRANSITION", 0.84
    ) == "confidence_shift"
    assert r._miss_reason(
        "NEW_TRANSITION_90", "OLD_TRANSITION_90", "NEW", "TRANSITION", 0.89
    ) == "data_hash_change"
    assert r._miss_reason(
        "h_TRANSITION_90", None, "h", "TRANSITION", 0.89
    ) == "no_cached_recommendation"


# ── the four behavioural assertions ────────────────────────────────────────

def _wire_endpoint(monkeypatch, *, live_regime, live_confidence,
                   data_hash, cached_rows):
    """Set up get_endpoint_recommendation with deterministic inputs:
      - detect_current_regime returns {hmm_regime, hmm_probabilities},
      - _current_data_hash returns `data_hash`,
      - get_cached_for_hash serves rows out of `cached_rows` (key->dict),
      - schedule_regime_aware_refresh is intercepted into `scheduled`.
    Returns the `scheduled` list so the test can assert miss vs hit."""
    monkeypatch.setattr(
        "tools.regime_detector.detect_current_regime",
        lambda: {
            "hmm_regime": live_regime,
            "hmm_probabilities": (
                {live_regime: live_confidence} if live_regime else {}),
        })

    async def _hash():
        return data_hash
    monkeypatch.setattr(r, "_current_data_hash", _hash)

    async def _get_cached(key):
        return cached_rows.get(key)
    monkeypatch.setattr(r, "get_cached_for_hash", _get_cached)

    async def _latest():
        # Newest by computed_at — pick the last-written row.
        return list(cached_rows.values())[-1] if cached_rows else None
    monkeypatch.setattr(r, "get_latest_recommendation", _latest)

    scheduled: list[dict] = []

    def _sched(dh, regime, conf, *, reason):
        scheduled.append({"data_hash": dh, "regime": regime,
                          "confidence": conf, "reason": reason})
    monkeypatch.setattr(r, "schedule_regime_aware_refresh", _sched)
    return scheduled


@pytest.mark.asyncio
async def test_same_hash_same_regime_same_bucket_is_a_hit(monkeypatch):
    """Same data_hash + same regime + same bucket -> cache hit (no LLM)."""
    cached = {
        "32862b2a_TRANSITION_90": {
            "signal": "cached prose for TRANSITION @ ~90%",
            "data_hash": "32862b2a_TRANSITION_90"},
    }
    scheduled = _wire_endpoint(
        monkeypatch, live_regime="TRANSITION", live_confidence=0.89,
        data_hash="32862b2a", cached_rows=cached)
    rec = await r.get_endpoint_recommendation()
    assert rec is cached["32862b2a_TRANSITION_90"]
    # Within the same composite key — no regenerate fired.
    assert scheduled == []


@pytest.mark.asyncio
async def test_same_hash_regime_change_is_a_miss(monkeypatch):
    """Same data_hash, regime flips TRANSITION->BULL -> miss + regenerate."""
    cached = {
        "32862b2a_TRANSITION_90": {
            "signal": "TRANSITION prose",
            "data_hash": "32862b2a_TRANSITION_90"},
    }
    scheduled = _wire_endpoint(
        monkeypatch, live_regime="BULL", live_confidence=0.92,
        data_hash="32862b2a", cached_rows=cached)
    rec = await r.get_endpoint_recommendation()
    # Served the latest cached (the TRANSITION row) as the bridge.
    assert rec is cached["32862b2a_TRANSITION_90"]
    # And a regenerate under the BULL key was scheduled.
    assert len(scheduled) == 1
    assert scheduled[0]["regime"] == "BULL"
    assert scheduled[0]["reason"] == "regime_shift"


@pytest.mark.asyncio
async def test_same_hash_confidence_crosses_bucket_is_a_miss(monkeypatch):
    """Confidence drops 89% -> 84% -> bucket 90 -> 80 -> miss + regenerate."""
    cached = {
        "32862b2a_TRANSITION_90": {
            "signal": "TRANSITION @ ~90% prose",
            "data_hash": "32862b2a_TRANSITION_90"},
    }
    scheduled = _wire_endpoint(
        monkeypatch, live_regime="TRANSITION", live_confidence=0.84,
        data_hash="32862b2a", cached_rows=cached)
    rec = await r.get_endpoint_recommendation()
    assert rec is cached["32862b2a_TRANSITION_90"]
    assert len(scheduled) == 1
    assert scheduled[0]["reason"] == "confidence_shift"
    assert r._confidence_bucket(scheduled[0]["confidence"]) == 80


@pytest.mark.asyncio
async def test_same_hash_within_bucket_noise_is_a_hit(monkeypatch):
    """Cached at 89% (bucket 90); live drifts to 92% (still bucket 90)
    -> cache HIT, no regenerate. This is the whole point: daily fluctuations
    inside a bucket should NOT burn an LLM call."""
    cached = {
        "32862b2a_TRANSITION_90": {
            "signal": "TRANSITION @ ~90% prose",
            "data_hash": "32862b2a_TRANSITION_90"},
    }
    scheduled = _wire_endpoint(
        monkeypatch, live_regime="TRANSITION", live_confidence=0.92,
        data_hash="32862b2a", cached_rows=cached)
    rec = await r.get_endpoint_recommendation()
    assert rec is cached["32862b2a_TRANSITION_90"]
    assert scheduled == []
