"""
backend/tools/canvas_schema.py

Single source of truth for the deck-canvas element schema --
TypedDict mirrors of the TypeScript shapes in
frontend/src/types/editor.ts. Used by:

  * tools/editor_content.py -- deck_slides_to_editor + _deck_slide_with_chart
    construct canvas elements that conform to these shapes.
  * tools/academic_deck.py -- build_editor_pptx reads chart_config /
    table_config off elements and threads them into the chart renderer
    + the table renderer respectively.
  * scripts/backfill_chart_config.py (post-merge) -- backfills the
    chart_config field on existing deck drafts.

DESIGN NOTES (June 26 2026)

  * chart_config and table_config are BOTH OPTIONAL on their host
    element. Absence preserves current rendering -- legacy drafts
    that pre-date this schema rev open + export unchanged. The
    renderers read the config and fall back to their hardcoded
    defaults for any missing field.

  * The new 'table' element type is a first-class canvas element
    (not a markdown-pipe block inside a text element). It carries
    its own bounding box + an optional table_config. The PPTX
    exporter renders it as a real <a:tbl> shape; the editor renders
    it as a Konva group placeholder + a real HTML table preview
    (mirrors the chart element's PNG-from-API pattern).

  * Configs are PRESENTATION ONLY. Data sources stay the verified
    analytics cache -- a user editing color_scheme can change the
    color of a series but can NOT change which strategies the cache
    returns or which numeric values the renderer reads. The data
    contract stays platform-controlled; the visual contract opens
    up to the editor.
"""
from __future__ import annotations

from typing import Literal, TypedDict, Union


# ── Chart config ──────────────────────────────────────────────────────────

ChartType = Literal["line", "bar", "scatter", "waterfall", "table"]

DateRangePreset = Literal["full", "post_2022", "oos_only", "custom"]


class ChartColorScheme(TypedDict, total=False):
    primary:   str
    secondary: str
    benchmark: str
    accent:    str


class ChartAxisConfig(TypedDict, total=False):
    x_label: str
    y_label: str
    x_min:   float | None
    x_max:   float | None
    y_min:   float | None
    y_max:   float | None


class ChartSeriesEntry(TypedDict, total=False):
    key:     str
    label:   str
    visible: bool
    color:   str


class ChartDateRange(TypedDict, total=False):
    start:  str | None
    end:    str | None
    preset: DateRangePreset


class ChartConfig(TypedDict, total=False):
    """All fields are OPTIONAL -- the renderer falls back to its
    hardcoded defaults for anything missing. Mirrors the
    ChartConfig interface in frontend/src/types/editor.ts."""
    chart_type:   ChartType
    title:        str
    caption:      str
    renderer_key: str
    color_scheme: ChartColorScheme
    axis:         ChartAxisConfig
    series:       list[ChartSeriesEntry]
    date_range:   ChartDateRange
    highlight_regime_breaks: bool
    show_benchmark:          bool


# ── Table config ──────────────────────────────────────────────────────────

TableType = Literal[
    "performance", "correlation", "factor_loadings", "drawdown",
]


class TableConfig(TypedDict, total=False):
    """All fields are OPTIONAL. table_type drives the default
    column set and the cache the rows are pulled from. Mirrors
    the TableConfig interface in frontend/src/types/editor.ts."""
    table_type:      TableType
    title:           str
    caption:         str
    rows:            list[str]
    columns:         list[str]
    highlight_best:  bool
    highlight_worst: bool
    decimal_places:  int


# ── Canvas elements ───────────────────────────────────────────────────────


class CanvasElementBase(TypedDict, total=False):
    id:     str
    type:   str
    x:      float
    y:      float
    width:  float
    height: float
    locked: bool


class CanvasTextElement(CanvasElementBase, total=False):
    content:    str
    fontSize:   int
    fontWeight: Literal["normal", "bold"]
    fontStyle:  Literal["normal", "italic"]
    color:      str


class CanvasChartElement(CanvasElementBase, total=False):
    chartKey:     str
    verified:     bool
    chart_config: ChartConfig


class CanvasTableElement(CanvasElementBase, total=False):
    """A native table element -- promoted to a first-class canvas
    element type June 26 2026. The PPTX exporter renders it as a
    real <a:tbl> shape via the table_config's table_type +
    column set."""
    table_config: TableConfig


CanvasElement = Union[
    CanvasTextElement, CanvasChartElement, CanvasTableElement]


class CanvasSlide(TypedDict, total=False):
    id:            int
    title:         str
    background:    str
    speaker_notes: str
    elements:      list[CanvasElement]
    speaker:       str | None


class CanvasDeck(TypedDict, total=False):
    slides: list[CanvasSlide]
