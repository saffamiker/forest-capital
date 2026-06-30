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


# ── get_latest_metric: _stale is a REAL hash comparison ─────────────────
# June 30 2026 -- prior to this PR, get_latest_metric unconditionally
# stamped _stale=True on every returned row, so consumers (the
# diversification metrics tile) always rendered "stale" even
# immediately after a successful refresh. These three tests pin the
# new conditional behaviour: True iff row_hash != current_hash, fail-
# open to True when the current hash is unavailable.


class _FakeRow(tuple):
    """A 3-tuple shaped like the SELECT (payload, computed_at, data_hash)
    row used by get_latest_metric."""

    def __new__(cls, payload, computed_at, data_hash):
        return super().__new__(cls, (payload, computed_at, data_hash))


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeSession:
    def __init__(self, row):
        self._row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, *_args, **_kwargs):
        return _FakeResult(self._row)


def _install_fake_db(monkeypatch, row):
    """Wire a fake AsyncSessionLocal that returns the supplied row."""
    import sys
    import types

    fake_module = types.ModuleType("database")
    fake_module.AsyncSessionLocal = lambda: _FakeSession(row)  # noqa: E731
    monkeypatch.setitem(sys.modules, "database", fake_module)


def _install_current_hash(monkeypatch, current_hash):
    """Patch tools.cache.get_latest_strategy_hash to return a fixed
    value (or None / raise) for the duration of the test."""
    import sys
    import types

    fake_cache = types.ModuleType("tools.cache")

    async def _hash():
        return current_hash

    fake_cache.get_latest_strategy_hash = _hash
    monkeypatch.setitem(sys.modules, "tools.cache", fake_cache)


def _install_current_hash_raising(monkeypatch):
    """Patch tools.cache.get_latest_strategy_hash to raise -- exercises
    the fail-open path."""
    import sys
    import types

    fake_cache = types.ModuleType("tools.cache")

    async def _boom():
        raise RuntimeError("db unavailable")

    fake_cache.get_latest_strategy_hash = _boom
    monkeypatch.setitem(sys.modules, "tools.cache", fake_cache)


def test_get_latest_metric_stale_false_when_hashes_match(monkeypatch):
    """row_hash == current_hash -- _stale must be False."""
    import asyncio
    import datetime as _dt
    from tools import precomputed_analytics as pa

    row = _FakeRow(
        {"diversification_ratio": 1.34},
        _dt.datetime(2026, 6, 30, tzinfo=_dt.timezone.utc),
        "deadbeefcafe1234",
    )
    _install_fake_db(monkeypatch, row)
    _install_current_hash(monkeypatch, "deadbeefcafe1234")
    out = asyncio.run(pa.get_latest_metric("diversification"))
    assert out is not None
    assert out["_stale"] is False
    assert out["_data_hash"] == "deadbeefcafe1234"


def test_get_latest_metric_stale_true_when_hashes_differ(monkeypatch):
    """row_hash != current_hash -- _stale must be True."""
    import asyncio
    import datetime as _dt
    from tools import precomputed_analytics as pa

    row = _FakeRow(
        {"diversification_ratio": 1.34},
        _dt.datetime(2026, 6, 30, tzinfo=_dt.timezone.utc),
        "old_hash_aaaaaaaa",
    )
    _install_fake_db(monkeypatch, row)
    _install_current_hash(monkeypatch, "new_hash_bbbbbbbb")
    out = asyncio.run(pa.get_latest_metric("diversification"))
    assert out is not None
    assert out["_stale"] is True
    assert out["_data_hash"] == "old_hash_aaaaaaaa"


def test_get_latest_metric_stale_true_on_current_hash_none(monkeypatch):
    """get_latest_strategy_hash returns None -- _stale fail-opens True."""
    import asyncio
    import datetime as _dt
    from tools import precomputed_analytics as pa

    row = _FakeRow(
        {"diversification_ratio": 1.34},
        _dt.datetime(2026, 6, 30, tzinfo=_dt.timezone.utc),
        "deadbeefcafe1234",
    )
    _install_fake_db(monkeypatch, row)
    _install_current_hash(monkeypatch, None)
    out = asyncio.run(pa.get_latest_metric("diversification"))
    assert out is not None
    assert out["_stale"] is True


def test_get_latest_metric_stale_true_on_current_hash_error(monkeypatch):
    """get_latest_strategy_hash raises -- _stale fail-opens True."""
    import asyncio
    import datetime as _dt
    from tools import precomputed_analytics as pa

    row = _FakeRow(
        {"diversification_ratio": 1.34},
        _dt.datetime(2026, 6, 30, tzinfo=_dt.timezone.utc),
        "deadbeefcafe1234",
    )
    _install_fake_db(monkeypatch, row)
    _install_current_hash_raising(monkeypatch)
    out = asyncio.run(pa.get_latest_metric("diversification"))
    assert out is not None
    assert out["_stale"] is True
