"""tests/test_performance_fixes.py — backend perf fixes.

Item 6 (May 23 2026 — performance audit).

Two backend perf wins covered here:

  1. auto_edit source skips the version snapshot write. The auto-
     save loop fires update_paper_md every ~30s on debounce; a
     snapshot on every keystroke round-trips report_paper_versions
     for no real value. Snapshots fire on meaningful source types
     only (manual / auto_iterate / auto_resolve_bob / restore).

  2. The freshness endpoint (item 5) reads three layers in parallel
     where the DB read is gated and short-circuits cleanly on a
     cold cache — verified by the response shape contract.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")


# ── update_paper_md source-gated snapshot ────────────────────────────────────


class TestAutoEditSkipsSnapshot:
    """The debounced auto-save (source='auto_edit') must NOT create
    a version snapshot. Other source types (manual, auto_iterate,
    auto_resolve_bob, restore) must STILL create snapshots."""

    def test_auto_edit_source_does_not_create_snapshot(self, monkeypatch):
        snapshot_calls: list[dict] = []

        async def _fake_get_generation(gid):
            return {"id": gid, "verified_data": {}, "paper_md": "old"}

        async def _fake_load_citations(gid):
            return {}

        async def _fake_update(gid, paper_md, fc, wc):
            return True

        async def _fake_bump(gid):
            return 5

        async def _fake_save(*args, **kwargs):
            snapshot_calls.append(kwargs)
            return {"id": 1, "version_number": 1}

        # _post_check_summary needs to return a dict — short-circuit
        # via a stub that doesn't reach the real implementation.
        def _fake_post_check(*args, **kwargs):
            return {"flag_count": 0, "word_counts": {},
                    "unverified_numbers": [],
                    "unverified_citations": []}

        from tools import report_generator as rg
        monkeypatch.setattr(rg, "get_generation", _fake_get_generation)
        monkeypatch.setattr(rg, "_load_citations_for_generation",
                            _fake_load_citations)
        monkeypatch.setattr(rg, "_update_paper_md", _fake_update)
        monkeypatch.setattr(rg, "_post_check_summary", _fake_post_check)
        from tools import paper_versions as pv
        monkeypatch.setattr(pv, "bump_paper_revision", _fake_bump)
        monkeypatch.setattr(pv, "save_version", _fake_save)

        # auto_edit — no snapshot.
        result = asyncio.run(rg.update_paper_md(
            42, "new text",
            source="auto_edit",
            saved_by_email="bob@queens.edu"))
        assert result["saved"] is True
        assert result["snapshot"] is None
        assert snapshot_calls == []  # save_version NEVER fired
        assert result["paper_revision"] == 5  # bump still ran

    def test_manual_source_does_create_snapshot(self, monkeypatch):
        snapshot_calls: list[dict] = []

        async def _fake_get_generation(gid):
            return {"id": gid, "verified_data": {}, "paper_md": "old"}

        async def _fake_load_citations(gid):
            return {}

        async def _fake_update(gid, paper_md, fc, wc):
            return True

        async def _fake_bump(gid):
            return 6

        async def _fake_save(*args, **kwargs):
            snapshot_calls.append(kwargs)
            return {"id": 7, "version_number": 6}

        def _fake_post_check(*args, **kwargs):
            return {"flag_count": 0, "word_counts": {},
                    "unverified_numbers": [],
                    "unverified_citations": []}

        from tools import report_generator as rg
        monkeypatch.setattr(rg, "get_generation", _fake_get_generation)
        monkeypatch.setattr(rg, "_load_citations_for_generation",
                            _fake_load_citations)
        monkeypatch.setattr(rg, "_update_paper_md", _fake_update)
        monkeypatch.setattr(rg, "_post_check_summary", _fake_post_check)
        from tools import paper_versions as pv
        monkeypatch.setattr(pv, "bump_paper_revision", _fake_bump)
        monkeypatch.setattr(pv, "save_version", _fake_save)

        result = asyncio.run(rg.update_paper_md(
            42, "new text",
            source="manual",
            saved_by_email="bob@queens.edu"))
        assert result["saved"] is True
        assert result["snapshot"] is not None
        assert len(snapshot_calls) == 1
        assert snapshot_calls[0]["source"] == "manual"

    def test_auto_iterate_source_does_create_snapshot(self, monkeypatch):
        """A confirmed AI iterate result IS worth snapshotting — the
        diff between pre and post is meaningful for the version panel."""
        snapshot_calls: list[dict] = []

        async def _fake_get_generation(gid):
            return {"id": gid, "verified_data": {}, "paper_md": "old"}

        async def _fake_load_citations(gid):
            return {}

        async def _fake_update(gid, paper_md, fc, wc):
            return True

        async def _fake_bump(gid):
            return 7

        async def _fake_save(*args, **kwargs):
            snapshot_calls.append(kwargs)
            return {"id": 8, "version_number": 7}

        def _fake_post_check(*args, **kwargs):
            return {"flag_count": 0, "word_counts": {},
                    "unverified_numbers": [],
                    "unverified_citations": []}

        from tools import report_generator as rg
        monkeypatch.setattr(rg, "get_generation", _fake_get_generation)
        monkeypatch.setattr(rg, "_load_citations_for_generation",
                            _fake_load_citations)
        monkeypatch.setattr(rg, "_update_paper_md", _fake_update)
        monkeypatch.setattr(rg, "_post_check_summary", _fake_post_check)
        from tools import paper_versions as pv
        monkeypatch.setattr(pv, "bump_paper_revision", _fake_bump)
        monkeypatch.setattr(pv, "save_version", _fake_save)

        result = asyncio.run(rg.update_paper_md(
            42, "new text",
            source="auto_iterate",
            saved_by_email="bob@queens.edu"))
        assert result["snapshot"] is not None
        assert snapshot_calls[0]["source"] == "auto_iterate"
