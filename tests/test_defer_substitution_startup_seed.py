"""tests/test_defer_substitution_startup_seed.py -- June 28 2026.

Source-inspection pins for the DEFER_SUBSTITUTION_TO_EXPORT
platform_config startup seed.

Operator confirmed the row keeps disappearing on Render
restarts. Without the row, _read_flag returns its default False,
and Phase 2 deferral silently no-ops on every brief generation
(drafts 74, 77 reproduced this).

The seed is idempotent (INSERT ... ON CONFLICT DO NOTHING) so
existing operator-set values (true OR false) are preserved.
"""
from __future__ import annotations

import inspect
import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


class TestStartupSeedWired:

    def test_lifespan_contains_defer_substitution_seed(self):
        """The lifespan handler must contain the seed block so
        it fires on every app boot."""
        from main import lifespan
        src = inspect.getsource(lifespan)
        # Seed event name must appear in the log call.
        assert (
            "platform_config_defer_substitution_seed" in src), (
            "lifespan must emit platform_config_"
            "defer_substitution_seed event after the seed runs")
        # Must use INSERT ... ON CONFLICT DO NOTHING (idempotent
        # contract preserving operator overrides).
        assert "ON CONFLICT (key) DO NOTHING" in src
        assert "defer_substitution_to_export" in src
        # Value JSON must match the platform_flags reader's
        # expected shape ({"enabled": true}).
        assert '\'{"enabled": true}\'' in src

    def test_seed_runs_in_try_except_fail_open(self):
        """The seed must be wrapped in try/except so a DB write
        failure never blocks app startup. Failure path emits a
        warning + continues; flag defaults to OFF in that case
        (legacy behaviour preserved)."""
        from main import lifespan
        src = inspect.getsource(lifespan)
        # The seed block has a paired except that logs the
        # failure event.
        assert (
            "platform_config_defer_substitution_seed_failed"
            in src)

    def test_seed_uses_jsonb_cast(self):
        """The value column is JSONB. The INSERT must CAST the
        string to JSONB so the row stores as a JSON object,
        not a text-string-of-JSON. The reader (_read_flag)
        parses value.get('enabled') and would fail if value
        were a string."""
        from main import lifespan
        src = inspect.getsource(lifespan)
        assert "CAST(:v AS JSONB)" in src

    def test_seed_runs_only_in_non_test_env(self):
        """Seed is inside the `if ENVIRONMENT != 'test':` guard
        like every other startup hook -- tests don't write to a
        production-like DB at boot."""
        from main import lifespan
        src = inspect.getsource(lifespan)
        # The seed log event must appear AFTER the
        # ENVIRONMENT != "test" guard.
        guard_idx = src.find('if ENVIRONMENT != "test":')
        seed_idx = src.find(
            "platform_config_defer_substitution_seed")
        assert guard_idx > -1
        assert seed_idx > -1
        assert guard_idx < seed_idx, (
            "Seed must be inside the non-test guard so "
            "pytest collection doesn't touch the DB on boot")
