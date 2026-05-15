"""
tests/test_render_bugfixes_v2.py

Second-wave production bug pins (after the Phase 13 hotfix landed):

  Bug 1 — FF factors load was gated behind fetch_supplemental_data,
          which is itself skipped on the warm-DB-cache path. Result:
          ff_factors was always None on every request after the first
          cold start, and the Factor Exposure Heatmap rendered blank.
          Fix: _read_history_from_db now calls _load_ff_factors_with_cache
          directly so the FF table is part of the DB-cache contract.

  Bug 2 — regime_signals_cache.hmm_probabilities was declared as
          ARRAY(Float) in migration 002, but the detector emits a DICT
          (e.g. {"BULL": 0.82, "BEAR": 0.12, "TRANSITION": 0.06}).
          asyncpg raised "sized iterable expected got dict" on every
          set_regime_cache call. Fix: migration 007 alters the column
          to JSONB, and set_regime_cache serialises via json.dumps with
          a CAST(:hp AS JSONB) bind. The reader defensively decodes
          when asyncpg returns the column as a string.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


# ── Bug 1: FF load on warm-cache path ────────────────────────────────────────

class TestFFFactorsOnWarmCachePath:
    """_read_history_from_db must populate ff_factors via
    _load_ff_factors_with_cache. Previously the field was hardcoded to
    None, so the Factor Exposure Heatmap rendered blank on every repeat
    request once market_data_monthly was populated."""

    def test_read_history_calls_ff_factors_loader(self, monkeypatch):
        """End-to-end: _read_history_from_db's return dict must come from
        _load_ff_factors_with_cache, not the legacy hardcoded None."""
        import pandas as pd
        import tools.data_fetcher as df_mod

        # Stub the FF loader so we can assert it was called and shape-check
        # the result without hitting Dartmouth.
        ff_df = pd.DataFrame(
            {"Mkt-RF": [0.01, 0.02], "SMB": [0.0, 0.001], "HML": [0.0, 0.0],
             "RF": [0.0, 0.0]},
            index=pd.to_datetime(["2024-01-31", "2024-02-29"]),
        )
        called = []

        def _stub_ff(start=None, end=None):
            called.append((start, end))
            return ff_df

        monkeypatch.setattr(df_mod, "_load_ff_factors_with_cache", _stub_ff)

        # Stub the DB query so we don't need a real Postgres.
        async def _fake_read():
            return {
                "monthly": [
                    {"date": "2024-01-31", "equity_return": 0.01, "ig_return": 0.0,
                     "hy_return": 0.001, "risk_free_rate": 0.0003,
                     "vix_month_avg": 18.0, "yield_curve": 0.5, "hy_spread": 4.0,
                     "ig_spread": 1.5, "gdp_growth": 0.025, "pe_ratio": 22.5},
                    {"date": "2024-02-29", "equity_return": 0.02, "ig_return": 0.001,
                     "hy_return": 0.0, "risk_free_rate": 0.0003,
                     "vix_month_avg": 17.0, "yield_curve": 0.4, "hy_spread": 3.8,
                     "ig_spread": 1.4, "gdp_growth": 0.025, "pe_ratio": 22.6},
                ],
                "daily": [],
            }

        # Patch the inner thread-pool path to return our stub directly.
        class FakeFuture:
            def __init__(self, value): self._v = value
            def result(self, timeout=None): return self._v

        class FakePool:
            def __init__(self, *a, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def submit(self, fn):
                import asyncio
                return FakeFuture(asyncio.run(_fake_read()))

        monkeypatch.setattr("database.DATABASE_URL", "postgresql+asyncpg://stub")
        monkeypatch.setattr("concurrent.futures.ThreadPoolExecutor", FakePool)

        result = df_mod._read_history_from_db()

        # Critical: FF loader was called exactly once with no arguments
        # (the function determines its own range from the DB).
        assert len(called) == 1
        assert called[0] == (None, None)

        # FF factors propagate to the caller via the unified history dict.
        assert "ff_factors" in result
        assert result["ff_factors"] is not None
        assert not result["ff_factors"].empty


# ── Bug 2: Migration 007 + JSONB cache writer ────────────────────────────────

class TestMigration007:
    """Migration 007 widens hmm_probabilities to JSONB. The Alembic file
    must load cleanly, chain off 006, and expose both upgrade/downgrade."""

    def test_migration_imports_cleanly(self):
        import importlib.util
        path = os.path.join(
            os.path.dirname(__file__), "..", "backend", "migrations",
            "versions", "007_alter_hmm_probabilities_to_jsonb.py",
        )
        spec = importlib.util.spec_from_file_location("m007", path)
        assert spec is not None and spec.loader is not None
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        assert m.revision == "007"
        assert m.down_revision == "006"
        assert callable(m.upgrade)
        assert callable(m.downgrade)

    def test_migration_body_uses_jsonb(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "backend", "migrations",
            "versions", "007_alter_hmm_probabilities_to_jsonb.py",
        )
        with open(path, encoding="utf-8") as f:
            source = f.read()
        assert "alter_column" in source
        assert "hmm_probabilities" in source
        assert "JSONB" in source


class TestRegimeCacheJsonSerialization:
    """The cache writer must serialise hmm_probabilities via json.dumps
    so a dict-shaped value lands in the JSONB column without asyncpg
    raising 'sized iterable expected got dict'."""

    @pytest.mark.asyncio
    async def test_set_regime_cache_serializes_dict_via_json_dumps(self, monkeypatch):
        """Captures the SQL parameters set_regime_cache binds. The
        hmm_probabilities field must arrive as a JSON string, not as the
        raw dict (which is what triggered the asyncpg error)."""
        import tools.cache as cache_mod

        captured: dict = {}

        class FakeSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def execute(self, query, params=None):
                captured["query"] = str(query)
                captured["params"] = params
                return None
            async def commit(self): pass

        def fake_session_factory():
            return FakeSession()

        monkeypatch.setattr(cache_mod, "_DB_AVAILABLE", True)
        monkeypatch.setattr(cache_mod, "AsyncSessionLocal", fake_session_factory)

        regime_data = {
            "threshold_regime": "BULL",
            "hmm_regime": "BULL",
            "hmm_probabilities": {"BULL": 0.82, "BEAR": 0.12, "TRANSITION": 0.06},
            "regimes_agree": True,
            "vix_level": 18.0,
            "yield_curve_slope": 0.5,
            "credit_spread": 4.0,
            "equity_trend": 0.02,
            "pre_2022_avg_correlation": -0.31,
            "post_2022_avg_correlation": 0.48,
        }

        await cache_mod.set_regime_cache(regime_data, ttl_minutes=15)

        # The "hp" bind parameter must be a JSON string, not the raw dict.
        hp_param = captured["params"]["hp"]
        assert isinstance(hp_param, str), (
            f"hmm_probabilities must be json.dumps'd to a string before "
            f"binding to a JSONB column; got {type(hp_param).__name__}"
        )
        # And the string must parse back to the original dict.
        assert json.loads(hp_param) == regime_data["hmm_probabilities"]

        # The SQL must CAST :hp to JSONB so the column-type contract is
        # explicit on the SQL side (defends against migration-not-yet-run
        # in production).
        assert "CAST(:hp AS JSONB)" in captured["query"]

    @pytest.mark.asyncio
    async def test_set_regime_cache_handles_none_hmm_probabilities(self, monkeypatch):
        """When the detector skips HMM (e.g. hmmlearn unavailable on
        Windows), hmm_probabilities is None. The writer must bind NULL,
        not the string "null"."""
        import tools.cache as cache_mod

        captured: dict = {}

        class FakeSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def execute(self, query, params=None):
                captured["params"] = params
                return None
            async def commit(self): pass

        monkeypatch.setattr(cache_mod, "_DB_AVAILABLE", True)
        monkeypatch.setattr(cache_mod, "AsyncSessionLocal", lambda: FakeSession())

        await cache_mod.set_regime_cache({"threshold_regime": "BULL"})
        assert captured["params"]["hp"] is None


class TestRegimeCacheReadDecodesJSON:
    """The reader must return hmm_probabilities as a Python dict whether
    asyncpg surfaces JSONB as a dict (the typical path) or as a string
    (some asyncpg + SQLAlchemy configurations)."""

    @pytest.mark.asyncio
    async def test_returns_dict_when_db_gives_dict(self, monkeypatch):
        import tools.cache as cache_mod
        from datetime import datetime, timezone, timedelta

        future = datetime.now(timezone.utc) + timedelta(minutes=10)
        # result tuple order matches the SELECT in get_regime_cache.
        row = (
            "BULL", "BULL",
            {"BULL": 0.82, "BEAR": 0.12, "TRANSITION": 0.06},
            True, 18.0, 0.5, 4.0, 0.02, -0.31, 0.48, datetime.now(timezone.utc), future,
        )
        await _run_get_cache_with_row(monkeypatch, row, cache_mod)
        result = await cache_mod.get_regime_cache()
        assert isinstance(result["hmm_probabilities"], dict)
        assert result["hmm_probabilities"]["BULL"] == 0.82

    @pytest.mark.asyncio
    async def test_returns_dict_when_db_gives_string(self, monkeypatch):
        """Some asyncpg configs return JSONB as a raw JSON string. The
        reader must defensively json.loads it back to a dict."""
        import tools.cache as cache_mod
        from datetime import datetime, timezone, timedelta

        future = datetime.now(timezone.utc) + timedelta(minutes=10)
        row = (
            "BULL", "BULL",
            '{"BULL": 0.82, "BEAR": 0.12, "TRANSITION": 0.06}',  # ← string, not dict
            True, 18.0, 0.5, 4.0, 0.02, -0.31, 0.48, datetime.now(timezone.utc), future,
        )
        await _run_get_cache_with_row(monkeypatch, row, cache_mod)
        result = await cache_mod.get_regime_cache()
        assert isinstance(result["hmm_probabilities"], dict)
        assert result["hmm_probabilities"]["BULL"] == 0.82

    @pytest.mark.asyncio
    async def test_returns_none_when_db_gives_malformed_string(self, monkeypatch):
        """A malformed JSON string is not the writer's fault but the
        reader must not raise — the cache hit should still be served
        with hmm_probabilities = None so the frontend degrades gracefully."""
        import tools.cache as cache_mod
        from datetime import datetime, timezone, timedelta

        future = datetime.now(timezone.utc) + timedelta(minutes=10)
        row = (
            "BULL", "BULL",
            "this is not json",
            True, 18.0, 0.5, 4.0, 0.02, -0.31, 0.48, datetime.now(timezone.utc), future,
        )
        await _run_get_cache_with_row(monkeypatch, row, cache_mod)
        result = await cache_mod.get_regime_cache()
        assert result is not None
        assert result["hmm_probabilities"] is None


async def _run_get_cache_with_row(monkeypatch, row, cache_mod):
    """Wires up a fake AsyncSessionLocal that returns `row` from the
    cache's SELECT — shared helper for the three read tests."""

    class FakeResult:
        def __init__(self, r): self._row = r
        def fetchone(self): return self._row

    class FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def execute(self, query, params=None):
            return FakeResult(row)
        async def commit(self): pass

    monkeypatch.setattr(cache_mod, "_DB_AVAILABLE", True)
    monkeypatch.setattr(cache_mod, "AsyncSessionLocal", lambda: FakeSession())
