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
    try:
        # The global QA-run guard's methodology flag is process-global —
        # clear it so a test that set it (or an endpoint that errored
        # before its finally ran) never leaves the guard stuck for the
        # next test.
        from tools.qa_guard import end_methodology
        end_methodology()
    except Exception:
        pass


@pytest.fixture
def clean_platform_users():
    """
    Reusable teardown for any test that creates platform_users rows
    through the API (e.g. POST /api/v1/admin/users).

    A true transaction rollback is not available here: the endpoints
    open their own AsyncSessionLocal and commit, so a test-side
    transaction cannot reach the committed write. Instead this fixture
    snapshots the table's emails before the test and deletes any row
    added during it — so a create-user test can run repeatedly without
    colliding on a duplicate email (409). Opt in by adding
    `clean_platform_users` to a test's signature.

    Fail-open: with no database the snapshot is empty and the teardown
    is a no-op, so the fixture is harmless in the database-free CI path.
    """
    import asyncio

    async def _emails() -> set[str]:
        try:
            from sqlalchemy import text

            from database import AsyncSessionLocal
            if AsyncSessionLocal is None:
                return set()
            async with AsyncSessionLocal() as s:
                rows = await s.execute(text("SELECT email FROM platform_users"))
                return {r[0] for r in rows.fetchall()}
        except Exception:
            return set()

    async def _delete_added(before: set[str]) -> None:
        try:
            from sqlalchemy import text

            from database import AsyncSessionLocal
            if AsyncSessionLocal is None:
                return
            async with AsyncSessionLocal() as s:
                rows = await s.execute(
                    text("SELECT id, email FROM platform_users"))
                added = [r[0] for r in rows.fetchall() if r[1] not in before]
                for row_id in added:
                    await s.execute(
                        text("DELETE FROM platform_users WHERE id = :i"),
                        {"i": row_id})
                await s.commit()
        except Exception:
            pass

    before = asyncio.run(_emails())
    yield
    asyncio.run(_delete_added(before))


@pytest.fixture
def clean_editor_drafts():
    """
    Reusable teardown for tests that create editor_drafts rows (the
    document editor). Snapshots the editor_drafts ids before the test
    and hard-deletes any added during it — editor_draft_versions rows
    cascade. Fail-open: with no database it is a no-op.
    """
    import asyncio

    async def _ids() -> set[int]:
        try:
            from sqlalchemy import text

            from database import AsyncSessionLocal
            if AsyncSessionLocal is None:
                return set()
            async with AsyncSessionLocal() as s:
                rows = await s.execute(text("SELECT id FROM editor_drafts"))
                return {r[0] for r in rows.fetchall()}
        except Exception:
            return set()

    async def _delete_added(before: set[int]) -> None:
        try:
            from sqlalchemy import text

            from database import AsyncSessionLocal
            if AsyncSessionLocal is None:
                return
            async with AsyncSessionLocal() as s:
                rows = await s.execute(text("SELECT id FROM editor_drafts"))
                added = [r[0] for r in rows.fetchall() if r[0] not in before]
                for row_id in added:
                    await s.execute(text(
                        "DELETE FROM editor_drafts WHERE id = :i"),
                        {"i": row_id})
                await s.commit()
        except Exception:
            pass

    before = asyncio.run(_ids())
    yield
    asyncio.run(_delete_added(before))


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
