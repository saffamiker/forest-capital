"""
tests/test_inprocess_caches.py

Memory-audit follow-up: two module-level in-process caches were added
to eliminate per-request reloading.

  FF factors cache (tools/data_fetcher._ff_factors_cache)
    _load_ff_factors_with_cache previously did a Postgres round-trip
    plus a ~1,197-row pandas DataFrame rebuild on every call. Since the
    warm path of get_full_history() now calls it on every request, that
    was a DB query + DataFrame construction per dashboard load. The
    cache holds one assembled DataFrame for a 1-hour TTL and is dropped
    when an incremental fetch writes new rows.

  HMM model cache (tools/regime_detector._hmm_model_cache)
    classify_hmm_regime fits a fresh 200-iteration GaussianHMM on every
    call. The cache keys the result dict on (series length, last date,
    n_states, seed) so the fit runs once per trading day instead of
    once per 15-minute regime-cache miss.

Both caches are bounded: one entry, overwritten not appended.

The autouse _clear_inprocess_caches fixture in conftest.py resets both
before each test — these tests deliberately exercise the within-test
warm path, which is unaffected by that fixture (it only runs at test
boundaries).
"""
from __future__ import annotations

import os
import sys
import time
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")


# ── FF factors in-process cache ──────────────────────────────────────────────

class TestFFFactorsInProcessCache:
    """The FF cache must (a) skip the DB round-trip on a warm hit,
    (b) return the same data, (c) drop on incremental write, and
    (d) honour the TTL."""

    @staticmethod
    def _seed_fetch(monkeypatch):
        """Stub _kenfrench_direct_fetch + an empty DB so the first
        _load_ff_factors_with_cache call populates the cache from the
        HTTP path. Returns a call-counter list for _read_ff_factors_from_db."""
        db_reads: list[int] = []

        def _count_db_read():
            db_reads.append(1)
            return []  # empty table → triggers initial fetch

        def _stub_fetch():
            return pd.DataFrame(
                {"Mkt-RF": [0.5, 0.6], "SMB": [0.1, 0.1],
                 "HML": [0.2, 0.2], "RF": [0.02, 0.02]},
                index=[202602, 202603],
            )

        monkeypatch.setattr("tools.data_fetcher._read_ff_factors_from_db", _count_db_read)
        monkeypatch.setattr("tools.data_fetcher._kenfrench_direct_fetch", _stub_fetch)
        return db_reads

    def test_second_call_skips_db_read(self, monkeypatch):
        """The headline win: a warm cache hit must not touch the DB."""
        from tools.data_fetcher import _load_ff_factors_with_cache, _ff_cache_clear
        _ff_cache_clear()
        db_reads = self._seed_fetch(monkeypatch)

        first = _load_ff_factors_with_cache()
        reads_after_first = len(db_reads)
        second = _load_ff_factors_with_cache()
        reads_after_second = len(db_reads)

        assert first is not None and not first.empty
        assert reads_after_first > 0, "First call must read the DB"
        assert reads_after_second == reads_after_first, (
            "Second call must be served from the in-process cache — "
            "_read_ff_factors_from_db must NOT be called again"
        )

    def test_warm_cache_returns_equivalent_data(self, monkeypatch):
        from tools.data_fetcher import _load_ff_factors_with_cache, _ff_cache_clear
        _ff_cache_clear()
        self._seed_fetch(monkeypatch)

        first = _load_ff_factors_with_cache()
        second = _load_ff_factors_with_cache()
        pd.testing.assert_frame_equal(first, second)

    def test_warm_cache_still_honours_start_end_slice(self, monkeypatch):
        """The cache holds the FULL frame; each caller's start/end window
        is applied on read so different callers share one cached frame."""
        from tools.data_fetcher import _load_ff_factors_with_cache, _ff_cache_clear
        _ff_cache_clear()
        self._seed_fetch(monkeypatch)

        _load_ff_factors_with_cache()  # warm the cache (full frame)
        sliced = _load_ff_factors_with_cache(start="2026-03-01", end=None)
        # Only the 202603 month-end row survives the start filter.
        assert len(sliced) == 1
        assert sliced.index[0].strftime("%Y%m") == "202603"

    def test_incremental_write_clears_cache(self, monkeypatch):
        """When an incremental fetch writes new rows, the cache must be
        dropped so the next call rebuilds with the new month."""
        from tools.data_fetcher import _ff_factors_cache, _ff_cache_clear
        _ff_cache_clear()

        # Manually warm the cache, then confirm _ff_cache_clear empties it.
        _ff_factors_cache["df"] = pd.DataFrame({"Mkt-RF": [0.5]}, index=[202603])
        _ff_factors_cache["cached_at"] = time.time()
        assert "df" in _ff_factors_cache

        _ff_cache_clear()
        assert "df" not in _ff_factors_cache
        assert _ff_factors_cache == {}

    def test_expired_ttl_triggers_rebuild(self, monkeypatch):
        """A cache entry older than _FF_CACHE_TTL_SECONDS must be ignored
        and the DB re-read."""
        from tools.data_fetcher import (
            _load_ff_factors_with_cache, _ff_cache_clear, _ff_factors_cache,
            _FF_CACHE_TTL_SECONDS,
        )
        _ff_cache_clear()
        db_reads = self._seed_fetch(monkeypatch)

        _load_ff_factors_with_cache()  # warm
        reads_after_warm = len(db_reads)

        # Backdate the cache timestamp beyond the TTL.
        _ff_factors_cache["cached_at"] = time.time() - _FF_CACHE_TTL_SECONDS - 1

        _load_ff_factors_with_cache()  # should rebuild
        assert len(db_reads) > reads_after_warm, (
            "An expired cache entry must trigger a fresh DB read"
        )

    def test_cache_holds_exactly_one_entry(self, monkeypatch):
        """The cache is bounded — repeated calls overwrite, never append.
        Memory footprint stays at one FF DataFrame."""
        from tools.data_fetcher import (
            _load_ff_factors_with_cache, _ff_cache_clear, _ff_factors_cache,
        )
        _ff_cache_clear()
        self._seed_fetch(monkeypatch)

        for _ in range(5):
            _load_ff_factors_with_cache()
        # Cache dict has exactly the two bookkeeping keys, nothing more.
        assert set(_ff_factors_cache.keys()) == {"df", "cached_at"}


# ── HMM model in-process cache ───────────────────────────────────────────────

# hmmlearn is skipped on Windows (needs C++ build tools); the whole class
# is conditionally skipped so CI on Linux still exercises it.
def _hmm_available() -> bool:
    try:
        from tools.regime_detector import _HMM_AVAILABLE
        return bool(_HMM_AVAILABLE)
    except Exception:
        return False


@pytest.mark.skipif(not _hmm_available(), reason="hmmlearn not installed (Windows)")
class TestHMMModelCache:
    """classify_hmm_regime must skip the Baum-Welch fit on a warm hit
    keyed by the input series fingerprint."""

    @staticmethod
    def _make_returns(n: int = 600, seed: int = 1) -> pd.Series:
        """Synthetic daily return series long enough for the HMM
        (classify_hmm_regime needs >= 100 obs)."""
        rng = np.random.default_rng(seed)
        idx = pd.date_range("2023-01-02", periods=n, freq="B")
        return pd.Series(rng.normal(0.0004, 0.011, n), index=idx)

    def test_second_call_same_series_skips_fit(self, monkeypatch):
        """A warm cache hit must NOT re-enter GaussianHMM.fit."""
        from tools.regime_detector import classify_hmm_regime, _hmm_cache_clear
        import tools.regime_detector as rd
        _hmm_cache_clear()

        rets = self._make_returns()

        fit_calls: list[int] = []
        orig_hmm = rd.GaussianHMM

        class _CountingHMM(orig_hmm):  # type: ignore[misc, valid-type]
            def fit(self, *a, **kw):
                fit_calls.append(1)
                return super().fit(*a, **kw)

        monkeypatch.setattr(rd, "GaussianHMM", _CountingHMM)

        classify_hmm_regime(rets)
        fits_after_first = len(fit_calls)
        classify_hmm_regime(rets)
        fits_after_second = len(fit_calls)

        assert fits_after_first >= 1, "First call must fit the HMM"
        assert fits_after_second == fits_after_first, (
            "Second call with the identical series must hit the cache — "
            "GaussianHMM.fit must NOT run again"
        )

    def test_warm_cache_returns_identical_result(self, monkeypatch):
        from tools.regime_detector import classify_hmm_regime, _hmm_cache_clear
        _hmm_cache_clear()
        rets = self._make_returns()

        first = classify_hmm_regime(rets)
        second = classify_hmm_regime(rets)
        # Same dict object returned from the cache.
        assert first is second

    def test_different_series_length_misses_cache(self, monkeypatch):
        """A series with a different length is a different fingerprint —
        it must re-fit, not return the stale cached result."""
        from tools.regime_detector import classify_hmm_regime, _hmm_cache_clear
        _hmm_cache_clear()

        first = classify_hmm_regime(self._make_returns(n=600))
        # Append one more observation → different fingerprint.
        second = classify_hmm_regime(self._make_returns(n=601))
        assert first is not second, (
            "A longer series must re-fit, not reuse the cached result"
        )

    def test_cache_holds_exactly_one_entry(self, monkeypatch):
        """Bounded — overwritten on a fingerprint change, never appended."""
        from tools.regime_detector import (
            classify_hmm_regime, _hmm_cache_clear, _hmm_model_cache,
        )
        _hmm_cache_clear()

        for n in (600, 601, 602):
            classify_hmm_regime(self._make_returns(n=n))
        assert set(_hmm_model_cache.keys()) == {"key", "result"}

    def test_clear_empties_cache(self):
        from tools.regime_detector import (
            classify_hmm_regime, _hmm_cache_clear, _hmm_model_cache,
        )
        _hmm_cache_clear()
        classify_hmm_regime(self._make_returns())
        assert "result" in _hmm_model_cache
        _hmm_cache_clear()
        assert _hmm_model_cache == {}
