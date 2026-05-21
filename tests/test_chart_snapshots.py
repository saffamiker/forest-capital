"""
tests/test_chart_snapshots.py

Tests for tools/chart_snapshots — the hash-gated chart-snapshot writer
that powers the chart-vision feature. Focused tests for the
skip-on-unchanged-hash guard added after the Commit-1 review found
that snapshots could re-render unnecessarily on a Render redeploy
when the persistent disk's previous snapshots are still current.

The full per-chart render path is exercised in test_charts.py against
the same render_chart_png() function this module calls. Here we pin
the guard contract: skip when the manifest hash matches AND every
chart has a PNG; render otherwise.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def snapshot_dir(tmp_path, monkeypatch):
    """Redirect chart_snapshots' on-disk path to a tmp directory so
    each test gets a clean slate."""
    from tools import chart_snapshots
    monkeypatch.setattr(chart_snapshots, "CHART_SNAPSHOT_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def two_chart_registry(monkeypatch):
    """Stub AVAILABLE_CHARTS down to two keys so the tests are fast
    and the manifest/disk coverage check has a small, predictable
    set. The real registry has 17 charts."""
    fake = [
        {"key": "rolling_correlation", "label": "Rolling Correlation",
         "description": "d1", "category": "performance"},
        {"key": "cumulative_returns", "label": "Cumulative Returns",
         "description": "d2", "category": "performance"},
    ]
    from tools import chart_snapshots
    # Patch the source-of-truth that chart_snapshots imports lazily.
    monkeypatch.setattr("tools.chart_render.AVAILABLE_CHARTS", fake)
    # Also stub render_chart_png so the test never touches matplotlib.
    async def _fake_render(key, theme, w, h):
        return b"\x89PNG\r\n\x1a\nfake-" + key.encode()
    monkeypatch.setattr("tools.chart_render.render_chart_png", _fake_render)
    return fake


@pytest.fixture
def stub_hash(monkeypatch):
    """Pins current_data_hash() to a deterministic value so the test
    can compose manifest fixtures with the same hash."""
    async def _fake_hash():
        return "abcdef1234567890abcdef1234567890"
    monkeypatch.setattr(
        "tools.audit_assembler.current_data_hash", _fake_hash)
    return "abcdef1234567890abcdef1234567890"


def _write_manifest(dir_path: str, hash_value: str, keys: list[str]) -> None:
    """Write a manifest.json mimicking a previous render."""
    manifest = {
        "hash": hash_value,
        "rendered_at": "2026-05-20T00:00:00Z",
        "charts": [
            {"key": k, "path": os.path.join(dir_path, f"{k}.png"),
             "size_kb": 12, "category": "performance"}
            for k in keys
        ],
    }
    with open(os.path.join(dir_path, "manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(manifest, f)


def _touch_png(dir_path: str, key: str) -> None:
    with open(os.path.join(dir_path, f"{key}.png"), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\nstale")


class TestSnapshotHashGuard:
    def test_skips_when_manifest_hash_matches_and_all_pngs_present(
        self, snapshot_dir, two_chart_registry, stub_hash,
    ):
        # Manifest's stored hash matches current_data_hash and every
        # AVAILABLE_CHARTS key has a PNG on disk → skip the render.
        from tools.chart_snapshots import render_all_chart_snapshots
        _write_manifest(str(snapshot_dir), stub_hash,
                         ["rolling_correlation", "cumulative_returns"])
        _touch_png(str(snapshot_dir), "rolling_correlation")
        _touch_png(str(snapshot_dir), "cumulative_returns")
        result = _run(render_all_chart_snapshots())
        assert result["skipped"] is True
        assert result["n_rendered"] == 0

    def test_renders_when_manifest_hash_differs_from_current(
        self, snapshot_dir, two_chart_registry, stub_hash,
    ):
        # Manifest says hash 'old'; current data hash differs → render.
        from tools.chart_snapshots import render_all_chart_snapshots
        _write_manifest(str(snapshot_dir), "different_hash_aaaa",
                         ["rolling_correlation", "cumulative_returns"])
        _touch_png(str(snapshot_dir), "rolling_correlation")
        _touch_png(str(snapshot_dir), "cumulative_returns")
        result = _run(render_all_chart_snapshots())
        assert result["skipped"] is False
        assert result["n_rendered"] == 2

    def test_renders_when_hash_matches_but_a_png_is_missing(
        self, snapshot_dir, two_chart_registry, stub_hash,
    ):
        # Manifest hash matches BUT only one of the two expected PNGs
        # exists on disk → render. This is the path that handles a
        # code deploy adding a new chart key (the new key has no PNG,
        # the guard fails, the new chart is produced).
        from tools.chart_snapshots import render_all_chart_snapshots
        _write_manifest(str(snapshot_dir), stub_hash,
                         ["rolling_correlation"])
        _touch_png(str(snapshot_dir), "rolling_correlation")
        # cumulative_returns.png deliberately absent.
        result = _run(render_all_chart_snapshots())
        assert result["skipped"] is False
        assert result["n_rendered"] == 2

    def test_renders_when_no_manifest_exists(
        self, snapshot_dir, two_chart_registry, stub_hash,
    ):
        # First-ever run for this disk — no manifest → render.
        from tools.chart_snapshots import render_all_chart_snapshots
        result = _run(render_all_chart_snapshots())
        assert result["skipped"] is False
        assert result["n_rendered"] == 2

    def test_renders_when_current_hash_unavailable(
        self, snapshot_dir, two_chart_registry, monkeypatch,
    ):
        # current_data_hash() raises — we cannot decide the skip is
        # safe, so we render. Same fail-open behaviour as before.
        from tools.chart_snapshots import render_all_chart_snapshots
        async def _bad_hash():
            raise RuntimeError("assembler down")
        monkeypatch.setattr(
            "tools.audit_assembler.current_data_hash", _bad_hash)
        _write_manifest(str(snapshot_dir), "some_hash",
                         ["rolling_correlation", "cumulative_returns"])
        _touch_png(str(snapshot_dir), "rolling_correlation")
        _touch_png(str(snapshot_dir), "cumulative_returns")
        result = _run(render_all_chart_snapshots())
        assert result["skipped"] is False
        assert result["n_rendered"] == 2

    def test_manifest_written_after_render_includes_current_hash(
        self, snapshot_dir, two_chart_registry, stub_hash,
    ):
        # Confirms the next run can use the manifest the previous run
        # wrote. End-to-end: first call renders + writes manifest;
        # second call sees the manifest and skips.
        from tools.chart_snapshots import render_all_chart_snapshots
        r1 = _run(render_all_chart_snapshots())
        assert r1["skipped"] is False
        with open(os.path.join(snapshot_dir, "manifest.json"),
                  encoding="utf-8") as f:
            manifest = json.load(f)
        assert manifest["hash"] == stub_hash
        r2 = _run(render_all_chart_snapshots())
        assert r2["skipped"] is True
