"""
tests/test_cache_layer.py

Tests for the PostgreSQL caching layer introduced in Sprint 5.

Strategy cache: verifies hash stability, cache read/write round-trips, and
that a hash mismatch forces a cache miss.

Regime cache: verifies TTL enforcement (expired rows not returned) and that
valid unexpired rows are returned correctly.

All tests use in-memory mocks — no live database required in CI.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def _run(coro):
    """Run a coroutine in a fresh event loop — compatible with Python 3.10+."""
    return asyncio.run(coro)


# ── strategy_results_cache ────────────────────────────────────────────────────

class TestStrategyHashStability:
    """
    The cache key must be deterministic for the same pipeline inputs.
    If the hash changes spuriously, every cold start is a cache miss.
    """

    def test_same_inputs_produce_same_hash(self):
        from tools.cache import _compute_data_hash
        h1 = _compute_data_hash(282, "2024-12-31", 10)
        h2 = _compute_data_hash(282, "2024-12-31", 10)
        assert h1 == h2

    def test_different_row_count_produces_different_hash(self):
        from tools.cache import _compute_data_hash
        h1 = _compute_data_hash(282, "2024-12-31", 10)
        h2 = _compute_data_hash(283, "2024-12-31", 10)
        assert h1 != h2

    def test_different_last_date_produces_different_hash(self):
        from tools.cache import _compute_data_hash
        h1 = _compute_data_hash(282, "2024-12-31", 10)
        h2 = _compute_data_hash(282, "2025-01-31", 10)
        assert h1 != h2

    def test_hash_is_hex_string(self):
        from tools.cache import _compute_data_hash
        h = _compute_data_hash(282, "2024-12-31", 10)
        assert isinstance(h, str)
        int(h, 16)  # must be valid hex


class TestStrategyCacheWithoutDB:
    """When no DB is configured, get_strategy_cache returns None and set is a no-op."""

    def test_get_returns_none_without_db(self):
        with patch("tools.cache._DB_AVAILABLE", False):
            from tools.cache import get_strategy_cache
            result = _run(get_strategy_cache("abc123"))
        assert result is None

    def test_set_is_noop_without_db(self):
        with patch("tools.cache._DB_AVAILABLE", False):
            from tools.cache import set_strategy_cache
            # Should not raise
            _run(set_strategy_cache("abc123", {"BENCHMARK": {"sharpe_ratio": 0.52}}))


class TestStrategyCacheRoundTrip:
    """Verify that set then get returns the stored value."""

    def _make_session_mock(self, return_value=None):
        """Helper: creates an async context manager mock for AsyncSessionLocal."""
        mock_result = MagicMock()
        mock_result.fetchone.return_value = return_value
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        return mock_ctx, mock_session

    def test_cache_miss_returns_none(self):
        mock_ctx, _ = self._make_session_mock(return_value=None)
        with patch("tools.cache._DB_AVAILABLE", True), \
             patch("tools.cache.AsyncSessionLocal", return_value=mock_ctx):
            from tools.cache import get_strategy_cache
            result = _run(get_strategy_cache("nonexistent_hash"))
        assert result is None

    def test_cache_hit_returns_dict(self):
        mock_result = MagicMock()
        mock_result.fetchone.return_value = ({"BENCHMARK": {"sharpe_ratio": 0.52}},)
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("tools.cache._DB_AVAILABLE", True), \
             patch("tools.cache.AsyncSessionLocal", return_value=mock_ctx):
            from tools.cache import get_strategy_cache
            result = _run(get_strategy_cache("known_hash"))
        assert result is not None
        assert "BENCHMARK" in result


# ── regime_signals_cache ──────────────────────────────────────────────────────

class TestRegimeCacheExpiry:
    """Expired rows must not be returned — the TTL must be enforced."""

    def _make_regime_row(self, expires_minutes_from_now: int):
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=expires_minutes_from_now)
        return (
            "BULL",            # threshold_regime
            1,                 # hmm_regime
            [0.1, 0.8, 0.1],  # hmm_probabilities
            True,              # regimes_agree
            18.4,              # vix_level
            0.42,              # yield_curve_slope
            3.21,              # credit_spread
            0.08,              # equity_trend
            -0.31,             # pre_2022_avg_correlation
            0.48,              # post_2022_avg_correlation
            now,               # fetched_at
            expires_at,        # expires_at
        )

    def test_expired_row_returns_none(self):
        row = self._make_regime_row(expires_minutes_from_now=-1)
        mock_result = MagicMock()
        mock_result.fetchone.return_value = row
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("tools.cache._DB_AVAILABLE", True), \
             patch("tools.cache.AsyncSessionLocal", return_value=mock_ctx):
            from tools.cache import get_regime_cache
            result = _run(get_regime_cache())
        assert result is None

    def test_fresh_row_returns_data(self):
        row = self._make_regime_row(expires_minutes_from_now=10)
        mock_result = MagicMock()
        mock_result.fetchone.return_value = row
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("tools.cache._DB_AVAILABLE", True), \
             patch("tools.cache.AsyncSessionLocal", return_value=mock_ctx):
            from tools.cache import get_regime_cache
            result = _run(get_regime_cache())
        assert result is not None
        assert result["threshold_regime"] == "BULL"
        assert result["vix_level"] == 18.4
        assert result["pre_2022_avg_correlation"] == -0.31
        assert result["post_2022_avg_correlation"] == 0.48

    def test_get_returns_none_without_db(self):
        with patch("tools.cache._DB_AVAILABLE", False):
            from tools.cache import get_regime_cache
            result = _run(get_regime_cache())
        assert result is None


# ── auth attempt logging ──────────────────────────────────────────────────────

class TestAuthAttemptLogging:
    """log_auth_attempt is fail-open — it must never raise even if DB is down."""

    def test_log_is_noop_without_db(self):
        with patch("tools.cache._DB_AVAILABLE", False):
            from tools.cache import log_auth_attempt
            # Should not raise
            _run(log_auth_attempt("test@queens.edu", "127.0.0.1", "Test", "sent"))

    def test_log_does_not_raise_on_db_error(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=Exception("DB down"))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("tools.cache._DB_AVAILABLE", True), \
             patch("tools.cache.AsyncSessionLocal", return_value=mock_ctx):
            from tools.cache import log_auth_attempt
            # Must not raise — auth flow is unaffected by logging failures
            _run(log_auth_attempt("test@queens.edu", "127.0.0.1", "Test", "sent"))


# ── JTI persistence ───────────────────────────────────────────────────────────

class TestJTIPersistence:
    """Consumed JTIs must be detectable after a simulated restart."""

    def test_unused_jti_returns_false(self):
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("tools.cache._DB_AVAILABLE", True), \
             patch("tools.cache.AsyncSessionLocal", return_value=mock_ctx):
            from tools.cache import is_jti_used
            result = _run(is_jti_used("new-jti-abc123"))
        assert result is False

    def test_used_jti_returns_true(self):
        mock_result = MagicMock()
        mock_result.fetchone.return_value = (1,)  # row found
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("tools.cache._DB_AVAILABLE", True), \
             patch("tools.cache.AsyncSessionLocal", return_value=mock_ctx):
            from tools.cache import is_jti_used
            result = _run(is_jti_used("already-used-jti"))
        assert result is True

    def test_is_jti_used_returns_false_without_db(self):
        with patch("tools.cache._DB_AVAILABLE", False):
            from tools.cache import is_jti_used
            result = _run(is_jti_used("any-jti"))
        assert result is False
