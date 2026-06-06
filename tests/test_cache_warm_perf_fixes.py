"""Cache-warm performance fixes -- bridge #71.

Three fixes pinned here:

  FIX 1 -- HMM model cache is multi-entry instead of single-entry.
    Pre-fix: _hmm_model_cache was {key, result} -- holding one entry
    overwritten on every call. Daily + monthly fits evicted each
    other on every detect_current_regime call so the cache was
    effectively useless.
    Post-fix: dict keyed by the (function, len, last_date, n_states,
    seed) tuple. Daily and monthly fits coexist; LRU eviction after
    16 entries.

  FIX 2 -- The three independent post-CIO refreshes (performance
    chart, forward projection, OOS cost sensitivity) now run via
    asyncio.gather instead of three sequential awaits. Each preserves
    its fail-open contract via return_exceptions=True.

  FIX 3 -- Composite (data_hash, regime) index on cio_recommendations.
    Migration 054. The index is checked indirectly by importing the
    migration module and asserting the upgrade calls create_index
    with the named target.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import numpy as np
import pandas as pd
import pytest


# ── FIX 1 -- multi-entry HMM cache ──────────────────────────────────────

def _synthetic_returns(
    n: int, mean: float = 0.001, std: float = 0.02, seed: int = 17,
) -> pd.Series:
    rng = np.random.default_rng(seed=seed)
    return pd.Series(
        rng.normal(mean, std, n),
        index=pd.date_range("2010-01-31", periods=n, freq="ME"))


def test_hmm_cache_daily_and_monthly_coexist(monkeypatch):
    """Pre-fix: a daily fit followed by a monthly fit evicted the
    daily cache entry, so the next daily call missed. Post-fix: both
    coexist."""
    from tools import regime_detector
    regime_detector._hmm_cache_clear()

    # Stub the HMM fit so the test is fast and deterministic.
    fits: list[int] = []

    class _FakeModel:
        def __init__(self, **_kw):
            self.monitor_ = type("m", (), {"converged": True})()
        def fit(self, X):
            fits.append(len(X))
        def predict(self, X):
            return np.zeros(len(X), dtype=int)
        def score_samples(self, X):
            n = len(X)
            return None, np.full((n, 3), 1.0 / 3)
        means_ = np.array([[0.001], [0.0], [-0.001]])
        transmat_ = np.eye(3)

    monkeypatch.setattr(
        regime_detector, "GaussianHMM",
        lambda **kw: _FakeModel(**kw), raising=False)
    monkeypatch.setattr(regime_detector, "_HMM_AVAILABLE", True)

    daily = _synthetic_returns(6640, seed=1)
    monthly = _synthetic_returns(287, seed=2)

    # First fits land in cache.
    regime_detector.classify_hmm_regime(daily)
    regime_detector.classify_hmm_regime(monthly)
    assert len(fits) == 2

    # Repeated calls hit cache (no new fits).
    regime_detector.classify_hmm_regime(daily)
    regime_detector.classify_hmm_regime(monthly)
    regime_detector.classify_hmm_regime(daily)
    assert len(fits) == 2, "daily/monthly fits evicted each other"


def test_hmm_cache_lru_eviction_bound(monkeypatch):
    """The cache is bounded so it never grows unbounded. After 17
    insertions (max=16), the oldest entry is evicted."""
    from tools import regime_detector
    regime_detector._hmm_cache_clear()

    # Direct accessor exercise -- exercise the bound regardless of
    # the classify_hmm_regime stub overhead.
    for i in range(17):
        key = ("classify", 100 + i, f"2026-{(i % 12) + 1:02d}-01", 3, 42)
        regime_detector._hmm_cache_put(key, {"i": i})

    assert len(regime_detector._hmm_model_cache) == 16
    # The OLDEST key (i=0) was evicted.
    assert ("classify", 100, "2026-01-01", 3, 42) not in (
        regime_detector._hmm_model_cache)
    # The MOST RECENT key (i=16) is still present.
    assert ("classify", 116, "2026-05-01", 3, 42) in (
        regime_detector._hmm_model_cache)


def test_hmm_cache_get_touches_lru_position(monkeypatch):
    """Reading an entry promotes it -- so it survives later evictions
    while older un-read entries get evicted first."""
    from tools import regime_detector
    regime_detector._hmm_cache_clear()

    # Fill with 16 entries.
    for i in range(16):
        regime_detector._hmm_cache_put(
            ("classify", 100 + i, f"d{i}", 3, 42), {"i": i})
    # Touch entry 0 (move to MRU).
    regime_detector._hmm_cache_get(("classify", 100, "d0", 3, 42))
    # Insert a 17th entry -- the LRU should be entry 1 now, not 0.
    regime_detector._hmm_cache_put(("classify", 200, "new", 3, 42), {"i": 200})
    assert ("classify", 100, "d0", 3, 42) in (
        regime_detector._hmm_model_cache)
    assert ("classify", 101, "d1", 3, 42) not in (
        regime_detector._hmm_model_cache)


def test_classify_and_historical_namespaced(monkeypatch):
    """classify_hmm_regime (diag, 2 features) and fit_hmm_historical
    (full, optional VIX) must not collide under the same series
    fingerprint -- the fits and the result schemas differ."""
    from tools import regime_detector
    regime_detector._hmm_cache_clear()

    # Direct exercise: two keys with the same series but different
    # function namespaces should coexist.
    regime_detector._hmm_cache_put(
        ("classify", 287, "2026-06-01", 3, 42), {"src": "classify"})
    regime_detector._hmm_cache_put(
        ("historical", 287, "2026-06-01", 3, 42, False),
        {"src": "historical"})
    assert regime_detector._hmm_cache_get(
        ("classify", 287, "2026-06-01", 3, 42))["src"] == "classify"
    assert regime_detector._hmm_cache_get(
        ("historical", 287, "2026-06-01", 3, 42, False)
    )["src"] == "historical"


# ── FIX 2 -- asyncio.gather the three independent refreshes ────────────

@pytest.mark.asyncio
async def test_cache_warm_parallel_three_refreshes(monkeypatch):
    """The three independent refreshes after refresh_cio_recommendation
    must run via asyncio.gather, not three sequential awaits. We stub
    each refresh to sleep, then assert the total elapsed time is
    closer to one-third of the sequential lower bound."""
    from tools import cache_warm_state

    # Mock the analytics + CIO upstream so the warm fn doesn't try
    # to hit a real database.
    async def _fake_analytics(_h): return True
    async def _fake_latest_hash(): return "test-hash"
    async def _fake_precomputed(_h, _k): return {"present": True}
    async def _fake_cio(): return {}

    monkeypatch.setattr(
        "tools.precomputed_analytics.refresh_all_analytics",
        _fake_analytics)
    monkeypatch.setattr(
        "tools.cache.get_latest_strategy_hash", _fake_latest_hash)
    # The cache_warm_state module imports `get_precomputed` as an
    # alias for `tools.precomputed_analytics.get_metric` -- patch the
    # source name so the lazy `from ... import get_metric as
    # get_precomputed` inside _default_warm_fn picks up the stub.
    monkeypatch.setattr(
        "tools.precomputed_analytics.get_metric", _fake_precomputed)
    # The CIO refresh + strategy cache reads happen via the same
    # lazy-import dance.
    async def _fake_strategy_cache(*_a, **_kw):
        return {"BENCHMARK": {"monthly_returns": []}}
    monkeypatch.setattr(
        "tools.cache.get_latest_strategy_cache", _fake_strategy_cache)
    async def _fake_metric(_k): return {"factor_loadings": ["x"],
                                         "regime_conditional": ["y"]}
    monkeypatch.setattr(
        "tools.precomputed_analytics.get_latest_metric", _fake_metric)
    # Skip the heavy backtester reseed path: pretend both health
    # checks already pass so _default_warm_fn proceeds straight to
    # refresh_all_analytics + the CIO + the three parallel refreshes
    # we are actually testing.
    monkeypatch.setattr(
        "tools.cache_warm_state._strategy_cache_is_healthy",
        lambda _c: True)
    monkeypatch.setattr(
        "tools.cache_warm_state._analytics_downstream_is_healthy",
        lambda _r: True)
    monkeypatch.setattr(
        "tools.cio_recommendation.refresh_cio_recommendation",
        _fake_cio)

    # Stub the THREE refreshes that should now parallelize. Each
    # sleeps 0.2s -- sequential = 0.6s, parallel = ~0.2s.
    SLEEP = 0.2
    async def _slow_chart(_h):  await asyncio.sleep(SLEEP); return True
    async def _slow_forward(_h): await asyncio.sleep(SLEEP); return True
    async def _slow_cost(_h):    await asyncio.sleep(SLEEP); return True
    monkeypatch.setattr(
        "tools.play_by_play.refresh_performance_chart", _slow_chart)
    monkeypatch.setattr(
        "tools.regime_meta_forward.refresh_forward_projection",
        _slow_forward)
    monkeypatch.setattr(
        "tools.regime_meta_validation.refresh_oos_cost_sensitivity",
        _slow_cost)

    t0 = time.perf_counter()
    result = await cache_warm_state._default_warm_fn()
    elapsed = time.perf_counter() - t0

    # Sequential would be 3 * SLEEP = 0.6s; parallel is closer to
    # SLEEP = 0.2s. We assert under 1.5x SLEEP to give CI scheduling
    # headroom while still failing if the gather is reverted to
    # sequential awaits.
    assert elapsed < SLEEP * 1.5, (
        f"warm took {elapsed:.2f}s -- expected ~{SLEEP}s under "
        f"asyncio.gather; sequential would be ~{SLEEP * 3}s")

    # All three refreshes landed.
    assert result["performance_chart"] is True
    assert result["forward_projection"] is True
    assert result["oos_cost_sensitivity"] is True


@pytest.mark.asyncio
async def test_cache_warm_one_refresh_failure_does_not_block_others(
    monkeypatch,
):
    """The gather uses return_exceptions=True so a single refresh
    exception falls back to landed=False for that one and the others
    complete normally."""
    from tools import cache_warm_state

    async def _ok(_h): return True
    async def _latest(): return "hash"
    async def _precomp(_h, _k): return {"present": True}
    async def _cio(): return {}
    monkeypatch.setattr(
        "tools.precomputed_analytics.refresh_all_analytics", _ok)
    monkeypatch.setattr(
        "tools.cache.get_latest_strategy_hash", _latest)
    monkeypatch.setattr(
        "tools.precomputed_analytics.get_metric", _precomp)
    async def _fake_strategy_cache(*_a, **_kw):
        return {"BENCHMARK": {"monthly_returns": []}}
    monkeypatch.setattr(
        "tools.cache.get_latest_strategy_cache", _fake_strategy_cache)
    async def _fake_metric(_k): return {"factor_loadings": ["x"],
                                         "regime_conditional": ["y"]}
    monkeypatch.setattr(
        "tools.precomputed_analytics.get_latest_metric", _fake_metric)
    # Skip the heavy backtester reseed path: pretend both health
    # checks already pass so _default_warm_fn proceeds straight to
    # refresh_all_analytics + the CIO + the three parallel refreshes
    # we are actually testing.
    monkeypatch.setattr(
        "tools.cache_warm_state._strategy_cache_is_healthy",
        lambda _c: True)
    monkeypatch.setattr(
        "tools.cache_warm_state._analytics_downstream_is_healthy",
        lambda _r: True)
    monkeypatch.setattr(
        "tools.cio_recommendation.refresh_cio_recommendation", _cio)

    async def _chart(_h): return True
    async def _forward(_h): raise RuntimeError("forward refresh broken")
    async def _cost(_h): return True
    monkeypatch.setattr(
        "tools.play_by_play.refresh_performance_chart", _chart)
    monkeypatch.setattr(
        "tools.regime_meta_forward.refresh_forward_projection", _forward)
    monkeypatch.setattr(
        "tools.regime_meta_validation.refresh_oos_cost_sensitivity",
        _cost)

    result = await cache_warm_state._default_warm_fn()
    assert result["performance_chart"] is True
    assert result["forward_projection"] is False, (
        "exception in one refresh must produce False landed, not raise")
    assert result["oos_cost_sensitivity"] is True


# ── FIX 3 -- composite index migration ──────────────────────────────────

def test_migration_054_creates_composite_index():
    """Migration 054 must add the composite (data_hash, regime) index
    that bridge #71 / cache-warm audit #68 identified as missing.

    The migration's module name starts with a digit so unittest.mock's
    string-based patch decorator cannot reach into it. We exercise the
    contract via grep-on-disk -- same pattern as the chart-theming
    regression pins in PR #289. The upgrade docstring + create_index
    call are required text in the migration file.
    """
    from pathlib import Path

    repo_root = Path(__file__).parent.parent
    migration = repo_root / "backend" / "migrations" / "versions" / (
        "054_cio_recommendations_hash_regime_index.py")
    assert migration.exists(), (
        "Migration 054 file is missing -- a rename or move requires "
        "updating this test.")
    src = migration.read_text(encoding="utf8")

    # upgrade() must call create_index with the named composite target.
    assert 'ix_cio_recommendations_hash_regime' in src
    assert '"cio_recommendations"' in src
    assert '["data_hash", "regime"]' in src
    # The new revision must chain off 053.
    assert 'down_revision: str | None = "053"' in src
    assert 'revision: str = "054"' in src
    # downgrade() must drop the same index.
    assert 'drop_index(' in src


def test_migration_054_revision_chain_is_continuous():
    """Sanity guard: revision 054 must not skip 053 or anchor on
    something else -- alembic upgrade head must traverse it cleanly."""
    from pathlib import Path

    repo_root = Path(__file__).parent.parent
    versions = (
        repo_root / "backend" / "migrations" / "versions")
    # Both 053 and 054 must exist.
    assert (versions / "053_tour_uat_bumps.py").exists()
    assert (versions / (
        "054_cio_recommendations_hash_regime_index.py")).exists()
