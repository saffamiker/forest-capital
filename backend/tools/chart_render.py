"""
tools/chart_render.py — server-side chart PNGs for the canvas editor.

The Konva presentation canvas embeds live platform charts as images.
This module exposes the charts that can be rendered server-side and a
cached render path that reuses academic_deck.render_deck_charts() — the
same matplotlib renderers the PPTX export package uses.

render_deck_charts() draws five fixed-size, light-mode charts. The
canvas editor asks for arbitrary width/height (and a theme): the raw
PNG is resized to the requested dimensions with Pillow, and `theme=dark`
falls back to the light render (the matplotlib renderers are light-only).
A 5-minute per-(chart_key, theme, width, height) cache keeps repeated
requests — thumbnails, re-fetches — off the render path.
"""
from __future__ import annotations

import io
import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# The charts academic_deck.render_deck_charts() can produce server-side.
# `key` must match a key of that function's return dict.
AVAILABLE_CHARTS: list[dict[str, str]] = [
    {"key": "rolling_correlation",
     "label": "Rolling Correlation",
     "description": "Equity-bond rolling correlation with the 2022 "
                    "regime-break marker — the project's central finding.",
     "category": "regime"},
    {"key": "cumulative_returns",
     "label": "Cumulative Returns",
     "description": "Growth of $1 across every strategy and the "
                    "benchmark over the full study period.",
     "category": "performance"},
    {"key": "risk_return",
     "label": "Risk vs Return",
     "description": "Each strategy plotted by annualised return against "
                    "volatility.",
     "category": "performance"},
    {"key": "sensitivity",
     "label": "Sensitivity Analysis",
     "description": "How the headline results hold up when key "
                    "parameters are varied — a robustness check.",
     "category": "robustness"},
    {"key": "team_activity",
     "label": "Team Activity",
     "description": "The project build timeline — commits, council runs "
                    "and reviews per team member.",
     "category": "process"},
]

_CHART_KEYS = frozenset(c["key"] for c in AVAILABLE_CHARTS)
_CACHE_TTL_SECONDS = 300  # 5 minutes

# {(chart_key, theme, width, height): (png_bytes, cached_at)}
_render_cache: dict[tuple[str, str, int, int], tuple[bytes, float]] = {}


def is_known_chart(chart_key: str) -> bool:
    """True when chart_key is server-renderable."""
    return chart_key in _CHART_KEYS


def _prune_expired(now: float) -> None:
    """Drops cache entries past the TTL — keeps the dict bounded."""
    stale = [k for k, (_, ts) in _render_cache.items()
             if now - ts >= _CACHE_TTL_SECONDS]
    for k in stale:
        _render_cache.pop(k, None)


def _placeholder(width: int, height: int) -> bytes:
    """A light placeholder PNG — used when a chart has no source data
    (a cold analytics cache, the test environment) so the canvas always
    receives a valid image."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (width, height), (244, 244, 246))
    draw = ImageDraw.Draw(img)
    msg = "Chart preview unavailable"
    try:
        bbox = draw.textbbox((0, 0), msg)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:  # noqa: BLE001 — older Pillow
        tw, th = len(msg) * 6, 11
    draw.text(((width - tw) / 2, (height - th) / 2), msg,
              fill=(120, 120, 130))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _resize(png: bytes, width: int, height: int) -> bytes:
    """Resizes a rendered chart PNG to the requested dimensions."""
    from PIL import Image
    img = Image.open(io.BytesIO(png)).convert("RGB")
    img = img.resize((width, height))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


async def _render_raw(chart_key: str) -> bytes | None:
    """The raw chart PNG from render_deck_charts, or None when its
    source data is unavailable (cold caches / the test environment)."""
    import asyncio

    from tools.academic_deck import render_deck_charts
    from tools.academic_export import gather_document_data

    data = await gather_document_data()
    sensitivity: dict[str, Any] | None = None
    if chart_key == "sensitivity":
        # Sensitivity is a heavier compute — only paid for its own chart.
        try:
            from tools.data_fetcher import get_full_history
            from tools.sensitivity import compute_sensitivity
            sensitivity = await asyncio.to_thread(
                lambda: compute_sensitivity(get_full_history()))
        except Exception as exc:  # noqa: BLE001
            log.warning("chart_render_sensitivity_unavailable", error=str(exc))

    charts = await asyncio.to_thread(render_deck_charts, data, sensitivity)
    return charts.get(chart_key)


async def render_chart_png(
    chart_key: str, theme: str, width: int, height: int,
) -> bytes:
    """
    Returns the chart as a PNG sized to width x height. Cached for five
    minutes per (chart_key, theme, width, height). `theme=dark` falls
    back to the light render — the matplotlib renderers are light-only.

    A chart whose source data is unavailable degrades to a placeholder
    PNG rather than an error, so the canvas always receives an image.
    """
    now = time.time()
    _prune_expired(now)
    cache_key = (chart_key, theme, width, height)
    hit = _render_cache.get(cache_key)
    if hit is not None and now - hit[1] < _CACHE_TTL_SECONDS:
        return hit[0]

    try:
        raw = await _render_raw(chart_key)
        png = _resize(raw, width, height) if raw else _placeholder(width, height)
    except Exception as exc:  # noqa: BLE001 — never 500 the canvas
        log.warning("chart_render_failed", chart_key=chart_key, error=str(exc))
        png = _placeholder(width, height)

    _render_cache[cache_key] = (png, now)
    return png
