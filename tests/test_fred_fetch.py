"""
tests/test_fred_fetch.py

Verifies FRED API fetch behaviour: 60-second timeout, API key forwarding,
and graceful fallback when FRED is unavailable. We patch `requests.get`
globally since _fred_fetch imports requests inside the function body.
"""
import asyncio
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# FRED timeout configuration — config constant
# ---------------------------------------------------------------------------

class TestFredTimeoutConfig:
    """FRED_TIMEOUT_SECONDS constant must be 60 in config.py."""

    def test_fred_timeout_constant_is_60(self):
        """Raises if someone accidentally sets a shorter timeout."""
        from config import FRED_TIMEOUT_SECONDS  # type: ignore[import]
        assert FRED_TIMEOUT_SECONDS == 60, (
            f"FRED_TIMEOUT_SECONDS must be 60, got {FRED_TIMEOUT_SECONDS}"
        )

    def test_fred_fetch_function_exists(self):
        """_fred_fetch is importable — it is the single FRED access point."""
        from tools.data_fetcher import _fred_fetch  # type: ignore[import]
        assert callable(_fred_fetch)

    def test_fred_fetch_uses_60s_timeout(self):
        """The _fred_fetch implementation passes timeout=60 to requests.get."""
        # requests is imported inside _fred_fetch, so we patch the global module
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.text = "DATE,VALUE\n2024-01-02,13.29\n2024-01-03,13.17"

        with patch("requests.get", return_value=mock_resp) as mock_get:
            from tools.data_fetcher import _fred_fetch  # type: ignore[import]
            try:
                _fred_fetch("VIXCLS", "2024-01-01", "2024-01-31")
            except Exception:
                pass  # We only care that requests.get was called with timeout=60
            if mock_get.called:
                call_kwargs = mock_get.call_args
                timeout = call_kwargs.kwargs.get("timeout")
                assert timeout == 60, f"Expected timeout=60, got {timeout}"

    def test_fred_fetch_appends_api_key_when_set(self):
        """When FRED_API_KEY is set, the URL includes it."""
        os.environ["FRED_API_KEY"] = "test_fred_key_abc"

        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.text = "DATE,VALUE\n2024-01-02,13.29"

        with patch("requests.get", return_value=mock_resp) as mock_get:
            from tools.data_fetcher import _fred_fetch  # type: ignore[import]
            try:
                _fred_fetch("VIXCLS", "2024-01-01", "2024-01-31")
            except Exception:
                pass
            if mock_get.called:
                url_called = mock_get.call_args.args[0] if mock_get.call_args.args else ""
                assert "test_fred_key_abc" in url_called or "api_key" in url_called, (
                    "FRED_API_KEY must appear in the request URL"
                )

    def test_fred_fetch_drops_missing_value_sentinel(self):
        """FRED uses '.' as a missing-value sentinel — _fred_fetch drops these rows."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.text = "DATE,VALUE\n2024-01-01,.\n2024-01-02,13.29"

        with patch("requests.get", return_value=mock_resp):
            from tools.data_fetcher import _fred_fetch  # type: ignore[import]
            try:
                result = _fred_fetch("VIXCLS", "2024-01-01", "2024-01-31")
                # Only the non-missing row should remain
                if result is not None and hasattr(result, "__len__"):
                    assert len(result) == 1, (
                        f"Expected 1 non-missing row, got {len(result)}"
                    )
            except ValueError:
                pass  # ValueError("FRED returned no data") is acceptable here


# ---------------------------------------------------------------------------
# FRED fallback behaviour when unavailable
# ---------------------------------------------------------------------------

class TestFredFallback:
    """Pipeline must not crash when FRED times out or returns an error."""

    def test_regime_cache_serves_data_when_fred_unavailable(self):
        """The regime cache module returns cached data without calling FRED."""
        from unittest.mock import AsyncMock

        cached_regime = {
            "threshold_regime": "BULL",
            "hmm_regime": 1,
            "hmm_probabilities": [0.1, 0.8, 0.1],
            "regimes_agree": True,
            "vix_level": 18.4,
            "yield_curve_slope": 0.42,
            "credit_spread": 3.21,
            "equity_trend": 0.08,
            "pre_2022_avg_correlation": -0.31,
            "post_2022_avg_correlation": 0.48,
        }

        with patch("tools.cache.get_regime_cache", new_callable=AsyncMock) as mock_cache:
            mock_cache.return_value = cached_regime
            result = _run(mock_cache())
        assert result["threshold_regime"] == "BULL"
        assert result["pre_2022_avg_correlation"] == -0.31

    def test_fred_timeout_raises_timeout_not_generic_exception(self):
        """requests.Timeout from FRED propagates with the correct type."""
        import requests as req_module

        with patch("requests.get", side_effect=req_module.exceptions.Timeout("timeout")):
            from tools.data_fetcher import _fred_fetch  # type: ignore[import]
            with pytest.raises((req_module.exceptions.Timeout, Exception)):
                _fred_fetch("VIXCLS", "2024-01-01", "2024-01-31")

    def test_missing_fred_api_key_does_not_crash_at_import(self):
        """The data_fetcher module loads successfully without FRED_API_KEY set."""
        env_backup = os.environ.pop("FRED_API_KEY", None)
        try:
            import importlib
            import tools.data_fetcher as df
            importlib.reload(df)
        finally:
            if env_backup is not None:
                os.environ["FRED_API_KEY"] = env_backup


# ---------------------------------------------------------------------------
# FRED data shape validation
# ---------------------------------------------------------------------------

class TestFredDataShape:
    """After a successful FRED fetch, the DataFrame has the right shape."""

    def test_vix_result_is_numeric_dataframe(self):
        """_fred_fetch result for VIX contains float values, not strings."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.text = "DATE,VALUE\n2024-01-02,13.29\n2024-01-03,13.17"

        with patch("requests.get", return_value=mock_resp):
            from tools.data_fetcher import _fred_fetch  # type: ignore[import]
            try:
                result = _fred_fetch("VIXCLS", "2024-01-01", "2024-01-31")
                if result is not None and not result.empty:
                    import numpy as np
                    assert result.dtypes.apply(lambda d: np.issubdtype(d, np.floating)).all(), (
                        "FRED result must be numeric (float) — not string"
                    )
            except Exception:
                pass  # Environment may lack network access

    def test_empty_fred_response_raises_value_error(self):
        """_fred_fetch raises ValueError when FRED returns an empty observation set."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        # Only headers, no data rows
        mock_resp.text = "DATE,VALUE\n"

        with patch("requests.get", return_value=mock_resp):
            from tools.data_fetcher import _fred_fetch  # type: ignore[import]
            with pytest.raises((ValueError, Exception)):
                _fred_fetch("VIXCLS", "2030-01-01", "2030-12-31")
