"""
tests/conftest.py

Pytest configuration for the Forest Capital test suite.

Registers custom markers so pytest does not emit PytestUnknownMarkWarning
when collecting tests that use @pytest.mark.deployment.
This file is at the tests/ root so it is always loaded regardless of which
directory pytest is invoked from or how rootdir is resolved.
"""
import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "deployment: marks tests that hit live production URLs "
        "(run with -m deployment, skipped in normal CI)",
    )
