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


@pytest.fixture(autouse=True)
def _clear_inprocess_caches():
    """Resets the two module-level in-process caches before AND after
    every test.

    The FF factors loader (_load_ff_factors_with_cache) and the HMM
    classifier (classify_hmm_regime) each keep a module-level cache to
    avoid re-loading 1,197 FF rows from Postgres / re-fitting a
    200-iteration Baum-Welch HMM on every request. Those caches persist
    for the lifetime of the process — including across tests in a single
    pytest run. Without this fixture, a test that warmed either cache
    would let the next test silently skip its own monkeypatched stubs
    and assert against stale data.

    Both clear functions are no-ops if their module fails to import
    (e.g. a test that doesn't touch the data layer), so this fixture is
    safe to run autouse across the whole suite.
    """
    try:
        from tools.data_fetcher import _ff_cache_clear
        _ff_cache_clear()
    except Exception:
        pass
    try:
        from tools.regime_detector import _hmm_cache_clear
        _hmm_cache_clear()
    except Exception:
        pass

    yield

    try:
        from tools.data_fetcher import _ff_cache_clear
        _ff_cache_clear()
    except Exception:
        pass
    try:
        from tools.regime_detector import _hmm_cache_clear
        _hmm_cache_clear()
    except Exception:
        pass
