"""
tests/conftest.py

Pytest configuration for the Forest Capital test suite.

Registers custom markers so pytest does not emit PytestUnknownMarkWarning
when collecting tests that use @pytest.mark.deployment.
This file is at the tests/ root so it is always loaded regardless of which
directory pytest is invoked from or how rootdir is resolved.
"""
import os
import sys

import pytest

# Ensure backend/ is importable for the cache-clearing fixture below.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "deployment: marks tests that hit live production URLs "
        "(run with -m deployment, skipped in normal CI)",
    )


def _reset_all_inprocess_caches() -> None:
    """Clears every module-level in-process cache. Each clear is wrapped
    so a module that fails to import (a test that doesn't touch the data
    layer) doesn't break the fixture."""
    try:
        from tools.data_fetcher import _ff_cache_clear
        _ff_cache_clear()
    except Exception:
        pass
    try:
        from tools.data_fetcher import _history_memo_clear
        _history_memo_clear()
    except Exception:
        pass
    try:
        from tools.regime_detector import _hmm_cache_clear
        _hmm_cache_clear()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _clear_inprocess_caches():
    """Resets every module-level in-process cache before AND after each test.

    Three caches persist for the lifetime of the process — and therefore
    across tests in a single pytest run:
      - FF factors  (data_fetcher._ff_factors_cache) — avoids re-loading
        1,197 rows from Postgres on every request.
      - HMM model   (regime_detector._hmm_model_cache) — avoids re-fitting
        a 200-iteration Baum-Welch on every request.
      - History memo (data_fetcher._history_memo) — 30-second TTL memo of
        get_full_history() that collapses the QA-badge poll storm.

    Without this fixture, a test that warmed any of them would let the
    next test silently skip its monkeypatched stubs and assert against
    stale data.
    """
    _reset_all_inprocess_caches()
    yield
    _reset_all_inprocess_caches()
