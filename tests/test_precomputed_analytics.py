"""Minimal smoke tests for precomputed_analytics — the fail-open
contract on read/write and the migration loads cleanly."""
import os
import sys
import importlib.util

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")


def test_migration_028_loads():
    spec = importlib.util.spec_from_file_location(
        "mig_028",
        os.path.join(os.path.dirname(__file__), "..", "backend",
                     "migrations", "versions",
                     "028_analytics_metrics_cache.py"),
    )
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.revision == "028"
    assert m.down_revision == "027"
    assert callable(m.upgrade)
    assert callable(m.downgrade)


def test_get_metric_returns_none_without_db():
    import asyncio
    from tools import precomputed_analytics as pa
    # Without a real database, every read returns None — the
    # endpoint's inline-fallback path handles this.
    out = asyncio.run(pa.get_metric("any_hash", "academic_analytics"))
    assert out is None


def test_set_metric_no_op_without_db():
    import asyncio
    from tools import precomputed_analytics as pa
    # set_metric is fail-open — never raises, even with no DB.
    asyncio.run(pa.set_metric("any_hash", "test_metric",
                              {"foo": "bar"},
                              source="unit_test"))


def test_get_latest_metric_returns_none_without_db():
    import asyncio
    from tools import precomputed_analytics as pa
    out = asyncio.run(pa.get_latest_metric("academic_analytics"))
    assert out is None


def test_trigger_refresh_async_off_loop_does_not_crash():
    from tools import precomputed_analytics as pa
    # When called off-loop (test env, no running event loop),
    # the trigger spawns a daemon thread. The thread fail-opens
    # internally so the caller does not see an exception.
    pa.trigger_refresh_async("smoketest_hash")
    # The function returned — no crash. The spawned thread runs
    # asyncio.run() which fails open inside refresh_all_analytics.
