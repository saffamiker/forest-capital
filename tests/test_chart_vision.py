"""
tests/test_chart_vision.py

Tests for tools/chart_vision — the read-side companion to
tools/chart_snapshots. Pins the fail-open contract (missing
snapshots → None / empty list, never raise) and the Anthropic
content-block shape that callers spread into a multimodal message.

These tests are scope-bounded: get_chart_image() / get_charts_for_context()
behaviour with fixtures on disk. The end-to-end "agent generator
receives chart blocks, evaluator does not" contract is exercised in
FEATURE 1 Commit 5 after the call_claude signature change ships.
"""
from __future__ import annotations

import base64
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")


@pytest.fixture
def snapshot_dir(tmp_path, monkeypatch):
    """Redirect chart_vision and chart_snapshots to a temp directory so
    the tests can stage fake PNG files without touching the production
    /data/chart_snapshots path."""
    monkeypatch.setenv("CHART_SNAPSHOT_DIR_OVERRIDE", str(tmp_path))
    # tools.chart_vision reads CHART_SNAPSHOT_DIR from backend.config
    # at import time, so swap it on the imported module directly.
    from tools import chart_vision
    monkeypatch.setattr(chart_vision, "CHART_SNAPSHOT_DIR", str(tmp_path))
    # The cached descriptions map must reset between tests so a fixture
    # that monkeypatches AVAILABLE_CHARTS doesn't bleed into the next.
    monkeypatch.setattr(chart_vision, "_DESCRIPTIONS_CACHE", None)
    return tmp_path


def _write_fake_png(path: str, content: bytes = b"\x89PNG\r\n\x1a\nfake") -> None:
    """Write a tiny byte sequence that starts with the PNG magic bytes
    so any code probing 'is this a PNG?' sees a yes. The content is
    not a valid PNG body — chart_vision does not decode it, only
    base64-encodes it for the model."""
    with open(path, "wb") as f:
        f.write(content)


class TestGetChartImage:
    def test_returns_base64_for_an_existing_snapshot(self, snapshot_dir):
        from tools.chart_vision import get_chart_image
        png_path = os.path.join(snapshot_dir, "rolling_correlation.png")
        _write_fake_png(png_path, content=b"\x89PNG\r\n\x1a\ndata")
        result = get_chart_image("rolling_correlation")
        assert result is not None
        # Roundtrip — the encoded string must decode back to the bytes
        # we wrote. Any encoding regression (utf-8 vs ascii, padding
        # stripped, etc.) is caught here.
        assert base64.b64decode(result) == b"\x89PNG\r\n\x1a\ndata"

    def test_returns_none_for_missing_snapshot(self, snapshot_dir):
        # No PNG written. The function must return None rather than
        # raise FileNotFoundError — the cold-deploy code path depends
        # on this.
        from tools.chart_vision import get_chart_image
        assert get_chart_image("rolling_correlation") is None

    def test_returns_none_for_unknown_key(self, snapshot_dir):
        # An arbitrary chart_key not in AVAILABLE_CHARTS also returns
        # None silently (it just can't find a file with that name).
        from tools.chart_vision import get_chart_image
        assert get_chart_image("not_a_real_chart_key") is None


class TestGetChartsForContext:
    def test_returns_image_plus_caption_blocks_per_snapshot(self, snapshot_dir):
        from tools.chart_vision import get_charts_for_context
        for key in ("rolling_correlation", "cumulative_returns"):
            _write_fake_png(os.path.join(snapshot_dir, f"{key}.png"))
        blocks = get_charts_for_context(
            ["rolling_correlation", "cumulative_returns"])
        # Two charts → image + caption per chart → 4 blocks total.
        assert len(blocks) == 4
        # Order: image_0, text_0, image_1, text_1.
        assert blocks[0]["type"] == "image"
        assert blocks[1]["type"] == "text"
        assert blocks[2]["type"] == "image"
        assert blocks[3]["type"] == "text"

    def test_image_block_carries_anthropic_base64_source_shape(
        self, snapshot_dir,
    ):
        from tools.chart_vision import get_charts_for_context
        _write_fake_png(
            os.path.join(snapshot_dir, "rolling_correlation.png"))
        blocks = get_charts_for_context(["rolling_correlation"])
        img = blocks[0]
        # The exact shape Anthropic's API expects for an image block.
        assert img["type"] == "image"
        assert img["source"]["type"] == "base64"
        assert img["source"]["media_type"] == "image/png"
        assert isinstance(img["source"]["data"], str)
        assert len(img["source"]["data"]) > 0

    def test_caption_names_the_chart_key(self, snapshot_dir):
        from tools.chart_vision import get_charts_for_context
        _write_fake_png(
            os.path.join(snapshot_dir, "rolling_correlation.png"))
        blocks = get_charts_for_context(["rolling_correlation"])
        caption = blocks[1]
        assert caption["type"] == "text"
        # The caption begins with "Chart: <key>" so the model can
        # name the chart back when reasoning about it.
        assert caption["text"].startswith("Chart: rolling_correlation")

    def test_missing_snapshots_are_skipped_silently(self, snapshot_dir):
        # Two requested keys, one snapshot on disk → 2 blocks (one
        # image + caption), not 4. The missing key is logged but
        # never raises.
        from tools.chart_vision import get_charts_for_context
        _write_fake_png(
            os.path.join(snapshot_dir, "rolling_correlation.png"))
        blocks = get_charts_for_context(
            ["rolling_correlation", "cumulative_returns"])
        assert len(blocks) == 2
        assert blocks[0]["type"] == "image"
        assert "rolling_correlation" in blocks[1]["text"]

    def test_returns_empty_list_when_none_available(self, snapshot_dir):
        # No PNGs on disk. The function returns []; the caller's
        # spread (*get_charts_for_context(...)) becomes a no-op and
        # the user message proceeds with only the text block —
        # exactly the pre-vision behaviour.
        from tools.chart_vision import get_charts_for_context
        assert get_charts_for_context(
            ["rolling_correlation", "cumulative_returns"]) == []

    def test_returns_empty_list_for_empty_input(self, snapshot_dir):
        # Defensive — an empty input list returns an empty list even
        # if every requested snapshot would have been available.
        from tools.chart_vision import get_charts_for_context
        assert get_charts_for_context([]) == []


class TestChartSets:
    """Pin the three predefined chart sets so a regression that drops
    a key from one of them shows up in tests rather than at runtime."""

    def test_council_charts_contains_the_regime_break(self):
        from tools.chart_vision import COUNCIL_CHARTS
        # The 2022 break is the central finding — council MUST see it.
        assert "rolling_correlation" in COUNCIL_CHARTS

    def test_academic_review_charts_includes_significance(self):
        from tools.chart_vision import ACADEMIC_REVIEW_CHARTS
        assert "significance_journey" in ACADEMIC_REVIEW_CHARTS

    def test_document_generation_charts_includes_drawdown_and_regime(self):
        from tools.chart_vision import DOCUMENT_GENERATION_CHARTS
        for k in ("rolling_correlation", "regime_signals",
                  "drawdown_periods"):
            assert k in DOCUMENT_GENERATION_CHARTS

    def test_every_set_key_is_a_valid_available_chart(self):
        # A typo in any set would silently drop that chart at runtime
        # because get_chart_image() returns None for an unknown key.
        # Cross-reference each set against AVAILABLE_CHARTS so a
        # typo surfaces here.
        from tools.chart_render import AVAILABLE_CHARTS
        from tools.chart_vision import (
            ACADEMIC_REVIEW_CHARTS, COUNCIL_CHARTS,
            DOCUMENT_GENERATION_CHARTS,
        )
        known = {c["key"] for c in AVAILABLE_CHARTS}
        for label, chart_set in (
            ("COUNCIL_CHARTS", COUNCIL_CHARTS),
            ("ACADEMIC_REVIEW_CHARTS", ACADEMIC_REVIEW_CHARTS),
            ("DOCUMENT_GENERATION_CHARTS", DOCUMENT_GENERATION_CHARTS),
        ):
            unknown = set(chart_set) - known
            assert unknown == set(), (
                f"{label} carries unknown chart keys: {unknown}")


class TestSnapshotsDirProbe:
    def test_reports_true_when_dir_exists(self, snapshot_dir):
        from tools.chart_vision import snapshots_dir_exists
        assert snapshots_dir_exists() is True

    def test_reports_false_when_dir_missing(self, tmp_path, monkeypatch):
        # Point the dir constant at a path that doesn't exist.
        from tools import chart_vision
        monkeypatch.setattr(chart_vision, "CHART_SNAPSHOT_DIR",
                            str(tmp_path / "does_not_exist"))
        assert chart_vision.snapshots_dir_exists() is False
