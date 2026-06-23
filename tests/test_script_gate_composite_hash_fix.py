"""tests/test_script_gate_composite_hash_fix.py -- June 22 2026.

Pins the fix for the script-card gate bug discovered after
PR #389: the gate was querying get_cached_story_plan(data_hash,
"deck") with the BARE current_data_hash, but refresh_story_plan
PERSISTS deck rows under a COMPOSITE storage_hash
(cache_key_with_brief_and_appendix:
"<data_hash>|<brief_hash>|<appendix_hash>"). The exact-match
query missed every real deck row -- the script card stayed
locked even when a fresh deck plan with a populated full_script
was sitting in the table.

Fix: new get_latest_story_plan(document_type, *,
exclude_fallback=True) helper queries by document_type only,
ordered by computed_at DESC LIMIT 1, with a SQL-level filter
on model != 'deterministic_fallback'. _deck_story_plan_status
now uses the new helper; the bare-hash query is gone.

These tests pin:
  1. The new helper exists with the documented signature
  2. The gate uses the new helper (not the old bare-hash path)
  3. The exclude_fallback filter is at the SQL level
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault(
    "SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")


# ── Helper signature + existence ────────────────────────────────────


class TestGetLatestStoryPlanHelperExists:

    def test_helper_is_importable(self):
        from tools.story_plan import get_latest_story_plan
        assert callable(get_latest_story_plan)

    def test_helper_signature(self):
        import inspect
        from tools.story_plan import get_latest_story_plan
        sig = inspect.signature(get_latest_story_plan)
        assert "document_type" in sig.parameters
        # exclude_fallback is keyword-only with default True
        assert "exclude_fallback" in sig.parameters
        param = sig.parameters["exclude_fallback"]
        assert param.default is True
        assert param.kind == inspect.Parameter.KEYWORD_ONLY


# ── Gate wiring ─────────────────────────────────────────────────────


class TestGateUsesLatestStoryPlanHelper:
    """Source-level pin: _deck_story_plan_status must import +
    call get_latest_story_plan, NOT get_cached_story_plan. A
    regression that reverts to the bare-hash query would re-
    introduce the script-card-locked bug."""

    def test_gate_imports_latest_helper(self):
        import inspect
        import main
        src = inspect.getsource(main._deck_story_plan_status)
        assert "get_latest_story_plan" in src, (
            "_deck_story_plan_status must use "
            "get_latest_story_plan -- the bare-hash query via "
            "get_cached_story_plan misses composite-key rows")

    def test_gate_does_not_use_bare_hash_get_cached(self):
        """The old get_cached_story_plan(data_hash, 'deck')
        call must be gone from the function BODY. Mentions
        in the docstring (explaining the bug) are fine."""
        import inspect
        import main
        src = inspect.getsource(main._deck_story_plan_status)
        # Strip the docstring so the assertion targets the
        # body only. Function body starts after the closing
        # triple-quote of the docstring.
        body_start = src.find('"""', src.find('"""') + 3) + 3
        body = src[body_start:]
        # The gate body must not call get_cached_story_plan.
        assert "get_cached_story_plan(" not in body, (
            "_deck_story_plan_status body must NOT call "
            "get_cached_story_plan with a bare hash -- "
            "refresh_story_plan persists deck rows under a "
            "composite hash so the exact-match query misses")
        # Also: no bare-hash data_hash variable referenced
        # in a call -- the gate's old shape was
        # `data_hash = await current_data_hash()` then
        # `get_cached_story_plan(data_hash, "deck")`. Both
        # of those patterns must be gone.
        assert "current_data_hash()" not in body, (
            "_deck_story_plan_status body must NOT call "
            "current_data_hash() -- the new helper queries "
            "by document_type only, no hash lookup needed")

    def test_gate_passes_exclude_fallback_true(self):
        import inspect
        import main
        src = inspect.getsource(main._deck_story_plan_status)
        assert "exclude_fallback=True" in src, (
            "_deck_story_plan_status must pass "
            "exclude_fallback=True so deterministic_fallback "
            "rows are filtered at the SQL layer; the gate's "
            "intent is to never serve a script built from a "
            "degraded outline")


# ── SQL-level filter ────────────────────────────────────────────────


class TestExcludeFallbackFiltersAtSqlLayer:
    """The exclude_fallback filter must land in the SQL WHERE
    clause -- a Python-side filter would still fetch the row,
    waste a round-trip, and (more importantly) miss the
    intended semantic: a fallback row with newer computed_at
    than a real row would short-circuit the latest-row query
    AT THE SQL LAYER and prevent the real row from being
    returned. SQL-layer filtering is the only correct shape."""

    def test_helper_source_filters_fallback_in_where_clause(
            self):
        import inspect
        from tools import story_plan
        src = inspect.getsource(story_plan.get_latest_story_plan)
        # The filter clause must be on `model` and reject
        # the fallback string at the SQL layer.
        assert "deterministic_fallback" in src
        # The filter must be inside a WHERE clause structure
        # (the helper builds where_clauses).
        assert "where_clauses" in src
        # And the SQL string must include the joined WHERE.
        assert "WHERE" in src
        assert "document_type = :t" in src


# ── End-to-end gate behaviour with mocked DB ────────────────────────


class TestGateBehaviourWithMockedHelper:
    """Functional test: when get_latest_story_plan returns a
    real deck plan with a populated full_script, the gate
    returns (True, True). When it returns None, (False, False).
    When it returns a deterministic_fallback row (defensive
    case -- shouldn't happen with exclude_fallback=True but
    we test the recheck), (False, False)."""

    def test_gate_returns_true_true_when_helper_returns_real_plan(
            self, monkeypatch):
        import asyncio
        import main

        async def _fake_helper(doc_type, *, exclude_fallback=True):
            assert doc_type == "deck"
            assert exclude_fallback is True
            return {
                "_model": "claude-opus-4-7",
                "full_script": "FULL_SCRIPT_PROSE_13KB",
                "central_argument": "test",
            }

        monkeypatch.setattr(
            "tools.story_plan.get_latest_story_plan",
            _fake_helper)
        plan_avail, script_avail = asyncio.run(
            main._deck_story_plan_status())
        assert plan_avail is True
        assert script_avail is True

    def test_gate_returns_false_false_when_helper_returns_none(
            self, monkeypatch):
        import asyncio
        import main

        async def _fake_helper(_doc_type, *, exclude_fallback=True):
            return None

        monkeypatch.setattr(
            "tools.story_plan.get_latest_story_plan",
            _fake_helper)
        plan_avail, script_avail = asyncio.run(
            main._deck_story_plan_status())
        assert plan_avail is False
        assert script_avail is False

    def test_gate_returns_true_false_when_full_script_empty(
            self, monkeypatch):
        """A real deck plan with no full_script (Pass-1a
        succeeded, Pass-2 failed) means the deck is generable
        but the script is not. Gate must distinguish the two."""
        import asyncio
        import main

        async def _fake_helper(_doc_type, *, exclude_fallback=True):
            return {
                "_model": "claude-opus-4-7",
                "full_script": "",  # Pass-2 didn't land
                "central_argument": "test",
            }

        monkeypatch.setattr(
            "tools.story_plan.get_latest_story_plan",
            _fake_helper)
        plan_avail, script_avail = asyncio.run(
            main._deck_story_plan_status())
        assert plan_avail is True
        assert script_avail is False

    def test_gate_defensive_recheck_blocks_fallback_with_full_script(
            self, monkeypatch):
        """Defensive case: if a fallback row somehow leaks
        past exclude_fallback (e.g. model=NULL future schema),
        the defensive recheck at plan_available =
        plan.get('_model') != 'deterministic_fallback' must
        still block. We can't trigger this with
        exclude_fallback=True easily, so we patch the helper
        to bypass and confirm the recheck fires."""
        import asyncio
        import main

        async def _fake_helper(_doc_type, *, exclude_fallback=True):
            return {
                "_model": "deterministic_fallback",
                "full_script": "fallback_text",
            }

        monkeypatch.setattr(
            "tools.story_plan.get_latest_story_plan",
            _fake_helper)
        plan_avail, script_avail = asyncio.run(
            main._deck_story_plan_status())
        assert plan_avail is False
        assert script_avail is False

    def test_gate_fail_open_when_helper_raises(self, monkeypatch):
        import asyncio
        import main

        async def _fake_helper(*_a, **_kw):
            raise RuntimeError("simulated db failure")

        monkeypatch.setattr(
            "tools.story_plan.get_latest_story_plan",
            _fake_helper)
        plan_avail, script_avail = asyncio.run(
            main._deck_story_plan_status())
        # Fail-open: errors return (False, False) -- the
        # script card stays disabled rather than the readiness
        # endpoint 500ing.
        assert plan_avail is False
        assert script_avail is False
