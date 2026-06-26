"""
backend/tools/chart_config_defaults.py

Generation-time prepopulation of ChartConfig + TableConfig for
deck canvas elements (see tools/canvas_schema.py).

The renderer functions in chart_render.py + chart_renderers.py
have ALL their visual decisions (title, color, axis labels,
window length, regime-break date) hardcoded as constants in
each function body. To let the slide editor surface those
decisions as editable knobs, we extract them here at GENERATION
time and stamp them into the slide element's chart_config /
table_config. The renderer then reads chart_config back from
the element at EXPORT time and uses its values to override the
hardcoded defaults (commit 3).

The chartKey -> ChartConfig table below mirrors the renderer
constants verbatim so an out-of-the-box generated deck renders
byte-identical to the pre-config path. Editor changes are layered
on top by replacing fields in the persisted chart_config dict.

DATA SOURCING

  The series list is built from the strategy_results_cache shape
  -- one entry per strategy, in cache order, all visible by
  default. Pulled out of build_chart_config_for_key so the
  generator can pass the live strategy list once + we don't
  re-query the cache per slide.

DEFAULT COLORS

  The renderer modules carry the deck color palette as private
  _DECK_* constants. We re-export the names we need here as
  module-level constants so the editor's color pickers can show
  a 'reset to default' value. Keep this list in sync with
  chart_render.py and chart_renderers.py -- a renderer-side
  palette change without a corresponding update here will leave
  newly-generated decks at the OLD default until the renderer's
  fallback fires.
"""
from __future__ import annotations

from typing import Any

from tools.canvas_schema import (
    ChartConfig, ChartSeriesEntry, TableConfig,
)


# ── Default deck color palette ────────────────────────────────────────────
#
# Mirrors chart_render.py:_DECK_* + chart_renderers.py:_MPL_*.
# Surfaced here as a stable contract for the editor; do not import
# from the renderer modules directly to avoid pulling matplotlib
# into the schema layer (which the editor-content path uses).

DEFAULT_PRIMARY   = "#1D4ED8"  # _DECK_ACCENT  (blue)
DEFAULT_SECONDARY = "#059669"  # _DECK_GREEN
DEFAULT_BENCHMARK = "#B45309"  # _DECK_AMBER
DEFAULT_ACCENT    = "#7C3AED"  # purple (used for accent series)

# 10-color cycle for per-series defaults. Order matters: editor
# colour pickers will offer this palette as the "Default" set.
DEFAULT_SERIES_PALETTE: list[str] = [
    "#1D4ED8", "#059669", "#B45309", "#7C3AED", "#DB2777",
    "#0891B2", "#CA8A04", "#15803D", "#9333EA", "#374151",
]

# Regime-break date the deck renderers axvline by default.
# Editor's date_range presets read this so 'post_2022' lines up.
DEFAULT_REGIME_BREAK = "2022-01-01"


# ── chartKey -> ChartConfig defaults ──────────────────────────────────────
#
# One entry per chartKey used in DECK_SLIDE_CHART_KEYS. Each entry
# captures the renderer's hardcoded title, axis labels, color
# choices, date-range default, and the regime-break / benchmark
# toggle defaults. The series list is appended per-call by
# build_chart_config_for_key from the live strategy_results cache.


def _color_scheme_default() -> dict[str, str]:
    """The shared default color scheme. Returned as a fresh dict
    per call so callers can mutate without affecting other configs."""
    return {
        "primary":   DEFAULT_PRIMARY,
        "secondary": DEFAULT_SECONDARY,
        "benchmark": DEFAULT_BENCHMARK,
        "accent":    DEFAULT_ACCENT,
    }


_CHART_KEY_DEFAULTS: dict[str, dict[str, Any]] = {
    "rolling_correlation": {
        "chart_type": "line",
        "title": "Rolling 12-Month Equity-Bond Correlation",
        "axis": {"x_label": "", "y_label": "Correlation",
                 "x_min": None, "x_max": None,
                 "y_min": None, "y_max": None},
        "date_range": {"start": None, "end": None,
                       "preset": "full"},
        "highlight_regime_breaks": True,
        "show_benchmark": False,  # not applicable -- two-line chart
    },
    "cumulative_returns": {
        "chart_type": "line",
        "title": "Cumulative Total Return - Growth of $1",
        "axis": {"x_label": "", "y_label": "Growth of $1",
                 "x_min": None, "x_max": None,
                 "y_min": None, "y_max": None},
        "date_range": {"start": None, "end": None,
                       "preset": "full"},
        "highlight_regime_breaks": False,
        "show_benchmark": True,
    },
    "strategy_comparison_oos_sharpe": {
        "chart_type": "bar",
        "title": "Post-2022 Sharpe - Dynamic Strategies",
        "axis": {"x_label": "", "y_label": "Post-2022 Sharpe",
                 "x_min": None, "x_max": None,
                 "y_min": None, "y_max": None},
        "date_range": {"start": None, "end": None,
                       "preset": "post_2022"},
        "highlight_regime_breaks": False,
        "show_benchmark": True,
    },
    "efficient_frontier": {
        "chart_type": "scatter",
        "title": "Strategy Risk-Return Frontier",
        "axis": {"x_label": "Annualised volatility (%)",
                 "y_label": "CAGR (%)",
                 "x_min": None, "x_max": None,
                 "y_min": None, "y_max": None},
        "date_range": {"start": None, "end": None,
                       "preset": "full"},
        "highlight_regime_breaks": False,
        "show_benchmark": True,
    },
    # The deck's editor schema (DECK_SLIDE_CHART_KEYS) uses
    # 'risk_return' for the efficient frontier slot. Same defaults
    # as efficient_frontier; the key alias is acknowledged in
    # chart_render.py:_DECK_KEYS.
    "risk_return": {
        "chart_type": "scatter",
        "title": "Strategy Risk-Return Frontier",
        "axis": {"x_label": "Annualised volatility (%)",
                 "y_label": "CAGR (%)",
                 "x_min": None, "x_max": None,
                 "y_min": None, "y_max": None},
        "date_range": {"start": None, "end": None,
                       "preset": "full"},
        "highlight_regime_breaks": False,
        "show_benchmark": True,
    },
    "rolling_sharpe": {
        "chart_type": "line",
        "title": "36-Month Rolling Sharpe - Strategy vs BENCHMARK",
        "axis": {"x_label": "", "y_label": "Sharpe ratio",
                 "x_min": None, "x_max": None,
                 "y_min": None, "y_max": None},
        "date_range": {"start": None, "end": None,
                       "preset": "full"},
        "highlight_regime_breaks": False,
        "show_benchmark": True,
    },
    "oos_performance": {
        "chart_type": "line",
        "title": "In-Sample vs Out-of-Sample",
        "axis": {"x_label": "", "y_label": "Growth of $1",
                 "x_min": None, "x_max": None,
                 "y_min": None, "y_max": None},
        "date_range": {"start": None, "end": None,
                       "preset": "full"},
        "highlight_regime_breaks": True,
        "show_benchmark": True,
    },
    "regime_signals": {
        "chart_type": "line",
        "title": "HMM Regime Probability Over Time",
        "axis": {"x_label": "", "y_label": "P(regime)",
                 "x_min": None, "x_max": None,
                 "y_min": 0.0, "y_max": 1.0},
        "date_range": {"start": None, "end": None,
                       "preset": "full"},
        "highlight_regime_breaks": False,
        "show_benchmark": False,
    },
}


# Charts that should DEFAULT to the post-2022 preset (per spec:
# "date_range: default to 'full' for most charts, 'post_2022' for
# OOS Sharpe comparison"). Already encoded in _CHART_KEY_DEFAULTS
# above; this set is a quick lookup for the few charts that
# diverge from 'full' so the rule is documented in one place.
POST_2022_DEFAULT_KEYS: frozenset[str] = frozenset({
    "strategy_comparison_oos_sharpe",
})


def _series_label(name: str) -> str:
    """Convert a strategy id ('REGIME_SWITCHING') to a presentable
    label ('Regime Switching'). Editor-side display only -- the
    cache lookup still uses the raw id."""
    return name.replace("_", " ").title()


def _build_series_entries(
    strategy_names: list[str],
) -> list[ChartSeriesEntry]:
    """Build one ChartSeriesEntry per strategy in cache order, all
    visible by default, each color picked from
    DEFAULT_SERIES_PALETTE in order (cycles after 10)."""
    out: list[ChartSeriesEntry] = []
    for i, name in enumerate(strategy_names):
        out.append({
            "key":     name,
            "label":   _series_label(name),
            "visible": True,
            "color":
                DEFAULT_SERIES_PALETTE[
                    i % len(DEFAULT_SERIES_PALETTE)],
        })
    return out


def build_chart_config_for_key(
    chart_key: str,
    strategy_names: list[str] | None = None,
) -> ChartConfig:
    """Returns a prepopulated ChartConfig for the given chartKey,
    mirroring the renderer's hardcoded defaults verbatim. The
    series list is sourced from the supplied strategy_names (one
    entry per strategy in cache order, all visible) -- pass [] or
    None for charts that don't have a per-strategy series concept.

    Unknown chartKey -> returns a minimal config with renderer_key
    populated; the renderer will fall back to its hardcoded
    defaults for every other field (the optional-fields contract)."""
    base = dict(_CHART_KEY_DEFAULTS.get(chart_key, {}))
    cfg: ChartConfig = {
        "renderer_key": chart_key,
        "color_scheme": _color_scheme_default(),
        **base,
    }
    if strategy_names:
        cfg["series"] = _build_series_entries(strategy_names)
    return cfg


# ── Table type defaults ──────────────────────────────────────────────────

# Default column set per table_type. The editor's column picker
# offers these as the starting selection; users can add / remove
# columns from the cache's full metric set.
_TABLE_TYPE_DEFAULT_COLUMNS: dict[str, list[str]] = {
    "performance": [
        "sharpe_ratio", "cagr", "volatility",
        "max_drawdown", "sortino_ratio", "calmar_ratio",
    ],
    "correlation": [
        "equity_corr", "ig_corr", "hy_corr",
    ],
    "factor_loadings": [
        "alpha", "mkt_rf", "smb", "hml", "mom", "r_squared",
    ],
    "drawdown": [
        "max_drawdown", "recovery_months", "peak_date",
        "trough_date",
    ],
}


def build_table_config(
    table_type: str = "performance",
    strategy_names: list[str] | None = None,
    title: str | None = None,
) -> TableConfig:
    """Returns a prepopulated TableConfig for the given table_type
    + strategy list. Rows = all strategies in cache order; columns
    = the table_type's default column set (see
    _TABLE_TYPE_DEFAULT_COLUMNS); highlight_best/worst on;
    decimal_places=2."""
    cfg: TableConfig = {
        "table_type":      table_type,  # type: ignore[typeddict-item]
        "rows":            list(strategy_names or []),
        "columns": list(
            _TABLE_TYPE_DEFAULT_COLUMNS.get(table_type, [])),
        "highlight_best":  True,
        "highlight_worst": True,
        "decimal_places":  2,
    }
    if title:
        cfg["title"] = title
    return cfg


def default_strategy_names_from_cache(
    strategy_results: dict[str, Any] | None,
) -> list[str]:
    """Strategy ids from a strategy_results_cache snapshot, in
    cache iteration order. Returns [] when the cache is empty or
    missing. Idempotent: handles {strategy_name: {metrics}} +
    {strategy_name: any_value} shapes."""
    if not isinstance(strategy_results, dict):
        return []
    return [str(k) for k in strategy_results.keys()]
