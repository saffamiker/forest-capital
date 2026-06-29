"""tests/test_editor_export_deck_parallel.py -- bridge #86.

The presentation-deck path of _editor_export renders one PNG per
chart element. Pre-bridge-#86 this was a serial for-loop: N elements
at distinct sizes paid N x (gather_document_data + matplotlib)
wall-clock, which on Render can clip Cloudflare's 100 s gateway
timeout. Bridge #86 rewrites it to asyncio.gather so the renders
run concurrently.

These tests stub render_chart_png with a slow async coroutine that
records when each call starts and finishes; they then assert the
renders ran in parallel (max-concurrency > 1) rather than back-to-
back. No DB / matplotlib involvement -- pure async-orchestration
contract test.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")


def _deck_draft_with_n_charts(n: int) -> dict:
    """Build a fake editor-draft record with N chart elements -- the
    exact shape get_draft returns. Sizes vary so the render cache
    can't conflate the keys (a deliberately worst-case input)."""
    elements_per_slide = []
    for i in range(n):
        elements_per_slide.append({
            "id": f"el-{i}",
            "type": "chart",
            "chartKey": "rolling_correlation",
            "width": 320 + i * 10,
            "height": 200 + i * 5,
        })
    return {
        "id": 999,
        "document_type": "presentation_deck",
        "content_json": {"slides": [
            {"id": 1, "title": "Slide", "elements": elements_per_slide},
        ]},
    }


class TestEditorExportDeckParallel:

    def test_chart_renders_run_concurrently(self, monkeypatch):
        """The N renders share an event-loop window: the max-
        concurrency observed mid-call must be > 1. A serial
        implementation would show max-concurrency = 1."""
        from main import _editor_export

        n = 5
        concurrency_meter = {"current": 0, "peak": 0}
        per_call_delay = 0.05  # 50 ms each -- enough to overlap

        async def _fake_render(chart_key, theme, w, h, **_kwargs):
            # June 26 2026 -- accept **kwargs so the chart_config
            # kwarg added to render_chart_png in the
            # feat(deck) chart_config commit doesn't break the stub.
            concurrency_meter["current"] += 1
            concurrency_meter["peak"] = max(
                concurrency_meter["peak"], concurrency_meter["current"])
            await asyncio.sleep(per_call_delay)
            concurrency_meter["current"] -= 1
            return b"PNG-BYTES"

        async def _fake_get_draft(_id):
            return _deck_draft_with_n_charts(n)

        def _fake_build(_draft, chart_pngs):
            return b"PK-PPTX-BODY"

        monkeypatch.setattr(
            "tools.editor_drafts.get_draft", _fake_get_draft)
        monkeypatch.setattr(
            "tools.chart_render.render_chart_png", _fake_render)
        monkeypatch.setattr(
            "tools.chart_render.is_known_chart", lambda *_a, **_k: True)
        monkeypatch.setattr(
            "tools.academic_deck.build_editor_pptx", _fake_build)

        t0 = time.monotonic()
        resp = asyncio.run(_editor_export(999))
        elapsed = time.monotonic() - t0

        assert resp.status_code == 200
        # Parallel execution -- the peak observed concurrency MUST be
        # greater than 1 for the contract to hold. The serial
        # implementation peaked at 1; gather() peaks at N.
        assert concurrency_meter["peak"] > 1, (
            "render_chart_png calls were serial; bridge #86 contract "
            "requires asyncio.gather concurrency.")
        # Wall-clock should be closer to per_call_delay than to
        # N * per_call_delay. Allow generous slack for test overhead
        # but pin the order-of-magnitude (parallel = ~0.05 s; serial
        # = ~0.25 s for N=5).
        assert elapsed < (per_call_delay * n * 0.6), (
            f"editor_export took {elapsed:.3f}s for {n} renders; "
            f"serial would have been ~{per_call_delay * n:.3f}s. "
            "Concurrency contract not met.")

    def test_one_failing_render_doesnt_break_the_others(self, monkeypatch):
        """A single chart render that raises must NOT abort the whole
        deck export -- the builder degrades a missing chart gracefully
        to a [DATA PENDING] note. The map handed to build_editor_pptx
        carries every successful render's PNG even when one peer
        raised."""
        from main import _editor_export

        async def _fake_render(chart_key, theme, w, h, **_kwargs):
            # June 26 2026 -- accept **kwargs so the chart_config
            # kwarg added to render_chart_png doesn't break the stub.
            if w == 660:  # the 'el-1' element (size 320+10=330; *2=660)
                raise RuntimeError("matplotlib oom")
            return b"PNG-BYTES"

        captured = {"chart_pngs": None}

        async def _fake_get_draft(_id):
            return _deck_draft_with_n_charts(3)

        def _fake_build(_draft, chart_pngs):
            captured["chart_pngs"] = dict(chart_pngs)
            return b"PK"

        monkeypatch.setattr(
            "tools.editor_drafts.get_draft", _fake_get_draft)
        monkeypatch.setattr(
            "tools.chart_render.render_chart_png", _fake_render)
        monkeypatch.setattr(
            "tools.chart_render.is_known_chart", lambda *_a, **_k: True)
        monkeypatch.setattr(
            "tools.academic_deck.build_editor_pptx", _fake_build)

        resp = asyncio.run(_editor_export(999))
        assert resp.status_code == 200
        # Two of the three renders succeed; the failing one is dropped
        # from the map (the builder treats the missing key as
        # [DATA PENDING]).
        assert captured["chart_pngs"] is not None
        assert "el-0" in captured["chart_pngs"]
        assert "el-2" in captured["chart_pngs"]
        assert "el-1" not in captured["chart_pngs"]

    def test_empty_deck_with_no_chart_elements_still_succeeds(
        self, monkeypatch,
    ):
        """A deck draft with zero chart elements -- pure-prose slides
        -- must still export cleanly. The asyncio.gather over an
        empty list is a no-op."""
        from main import _editor_export

        async def _fake_get_draft(_id):
            return {
                "id": 1, "document_type": "presentation_deck",
                "content_json": {"slides": [
                    {"id": 1, "title": "Just bullets", "elements": [
                        {"id": "t", "type": "text", "text": "hi"}]}
                ]},
            }

        def _fake_build(_draft, chart_pngs):
            assert chart_pngs == {}, "no charts -> empty PNG map"
            return b"PK"

        monkeypatch.setattr(
            "tools.editor_drafts.get_draft", _fake_get_draft)
        monkeypatch.setattr(
            "tools.academic_deck.build_editor_pptx", _fake_build)

        resp = asyncio.run(_editor_export(1))
        assert resp.status_code == 200
