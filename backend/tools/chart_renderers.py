"""
tools/chart_renderers.py — extended server-side chart renderers.

The canvas presentation editor's chart picker offers a library that
extends well beyond the five charts the deck export ships. The four
renderers in this commit (regime + factors) live here rather than in
academic_deck.py — they are canvas-only and would otherwise bloat the
deck-export path. Same matplotlib light theme as academic_deck.py
(white background, navy ink) so a render dropped into the deck slide
looks identical to one rendered for the deck export itself.

Every renderer is a function `_render_<chart_key>` that receives the
shared data bundle plus a `plt` handle, returns the PNG bytes on
success, and returns None when its source data is unavailable — same
fail-open contract as academic_deck.render_deck_charts.
render_extended_charts() is the dispatcher.

The extended charts often need data beyond what gather_document_data
returns (HMM history, raw monthly returns, ff_factors). The caller
(chart_render._render_raw) is responsible for fetching those — they
arrive on the `extras` dict, keyed by source.
"""
from __future__ import annotations

import io
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Light-mode theme — identical to academic_deck.py so a canvas-only chart
# matches a deck chart side by side in the final presentation.
_MPL_INK = "#1A1A2E"
_MPL_GREY = "#4A4A6A"
_MPL_GRID = "#E2E8F0"
_MPL_ACCENT = "#1D4ED8"
_MPL_AMBER = "#B45309"
_MPL_GREEN = "#059669"
_MPL_RED = "#DC2626"
_MPL_NAVY = "#1A2A4A"

# Regime colours — semantically aligned with the platform's regime UI:
# bull = green, transition = amber, bear = navy/red. Used by both
# regime_signals (stacked area) and regime_conditional_returns (bars).
_REGIME_COLOURS = {
    "BULL":       _MPL_GREEN,
    "TRANSITION": _MPL_AMBER,
    "BEAR":       _MPL_NAVY,
}

# A factor → colour map for factor_returns_attribution. Stable across
# years so the legend reads consistently.
_FACTOR_COLOURS = {
    "mkt_rf": _MPL_ACCENT,
    "smb":    _MPL_GREEN,
    "hml":    _MPL_AMBER,
    "mom":    "#7C3AED",
}

# Human-readable factor names used in legends.
_FACTOR_LABELS = {
    "alpha":  "α (alpha)",
    "mkt_rf": "MKT-RF",
    "smb":    "SMB",
    "hml":    "HML",
    "mom":    "MOM",
}

# The single-strategy charts (drawdown, heatmap, rolling Sharpe,
# distribution) default to the project's headline strategy and fall
# back to the first non-benchmark strategy when it is absent. This is
# the chart-vs-benchmark default; the canvas editor does not yet
# expose a strategy picker.
_DEFAULT_STRATEGY = "REGIME_SWITCHING"

# Rolling Sharpe window — 36 months matches the platform convention.
_ROLLING_SHARPE_WINDOW = 36

# Monthly-returns histogram bin count.
_DISTRIBUTION_BINS = 30

# Every chart_key the extended renderer family handles. Listed
# explicitly so chart_render can dispatch without importing
# implementation details.
# IS/OOS cutoff for oos_performance — last 60 months are OOS, matching
# the brief's decision (a five-year OOS window is the most defensible
# split to the faculty panel; an 80/20 split is less explainable).
_OOS_WINDOW_MONTHS = 60

# FDR threshold the Tier 1 gates apply to p_value_corrected.
_FDR_THRESHOLD = 0.005

# CV Stability gate threshold per CLAUDE.md Section 7.
_CV_STABILITY_THRESHOLD = 0.60

EXTENDED_KEYS: frozenset[str] = frozenset({
    "regime_signals",
    "regime_conditional_returns",
    "factor_loadings",
    "factor_returns_attribution",
    "drawdown_periods",
    "monthly_returns_heatmap",
    "rolling_sharpe",
    "return_distribution",
    "significance_journey",
    "oos_performance",
    "p_value_distribution",
})


def _style(ax) -> None:
    """Identical to academic_deck.py — keeps both renderers in lockstep."""
    ax.set_facecolor("white")
    ax.grid(True, color=_MPL_GRID, linewidth=0.7)
    ax.tick_params(colors=_MPL_GREY, labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(_MPL_GRID)
    ax.title.set_color(_MPL_INK)
    ax.xaxis.label.set_color(_MPL_GREY)
    ax.yaxis.label.set_color(_MPL_GREY)


def _finish(fig, plt) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def render_extended_charts(
    chart_key: str,
    data: dict[str, Any],
    extras: dict[str, Any] | None = None,
) -> dict[str, bytes | None]:
    """
    Renders one of the extended canvas charts to a light-mode PNG.

    Returns {chart_key: png_bytes} on success, {chart_key: None} on a
    failure (matplotlib unavailable, missing data, plotting error) —
    chart_render._render_raw then degrades to a placeholder.

    Single-chart dispatch (not the deck's "render every chart at once"
    pattern) so the canvas editor pays only for what it asks for.
    """
    extras = extras or {}
    charts: dict[str, bytes | None] = {chart_key: None}

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        log.warning("extended_charts_matplotlib_unavailable",
                    chart_key=chart_key, error=str(exc))
        return charts

    renderer = _DISPATCH.get(chart_key)
    if renderer is None:
        return charts

    try:
        png = renderer(data, extras, plt)
        charts[chart_key] = png
    except Exception as exc:  # noqa: BLE001
        log.warning("extended_chart_failed", chart_key=chart_key,
                    error=str(exc))
        charts[chart_key] = None

    return charts


# ── regime_signals — HMM posterior probability stacked area ───────────────────

def _render_regime_signals(
    data: dict[str, Any], extras: dict[str, Any], plt,
) -> bytes | None:
    """
    P(regime=BULL/TRANSITION/BEAR) over the full monthly history as a
    stacked area (the three probabilities sum to 1.0 at every t).

    extras["hmm"] is the fit_hmm_historical result — it carries
    `historical_probs` (label → list-of-probs) and `dates` (ISO).
    """
    import pandas as pd

    hmm = extras.get("hmm")
    if not isinstance(hmm, dict) or hmm.get("error"):
        return None
    probs = hmm.get("historical_probs") or {}
    dates = hmm.get("dates") or []
    if not probs or not dates:
        return None

    fig, ax = plt.subplots(figsize=(8, 4.2))
    x = [pd.to_datetime(d) for d in dates]
    # Stack BEAR (bottom) → TRANSITION → BULL (top); same vertical order
    # the platform regime UI uses.
    ordered = [(k, _REGIME_COLOURS.get(k, _MPL_GREY))
               for k in ("BEAR", "TRANSITION", "BULL") if k in probs]
    ax.stackplot(
        x,
        [probs[k] for k, _ in ordered],
        colors=[c for _, c in ordered],
        labels=[k.title() for k, _ in ordered],
        alpha=0.85,
    )
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("P(regime)")
    ax.set_title("HMM Regime Probability Over Time", fontsize=11)
    ax.legend(loc="upper left", fontsize=8, frameon=False, ncol=3)
    _style(ax)
    return _finish(fig, plt)


# ── regime_conditional_returns — mean monthly return per regime, per asset ────

def _render_regime_conditional_returns(
    data: dict[str, Any], extras: dict[str, Any], plt,
) -> bytes | None:
    """
    Grouped bar chart: x-axis = asset (Equity / IG / HY); within each
    asset, one bar per regime (BULL / TRANSITION / BEAR) showing the mean
    monthly return for that asset in that regime. Annualised to a yearly
    rate so the bars are intuitive at a glance.

    extras["hmm"] supplies historical labels; extras["monthly"] supplies
    the per-asset monthly return series.
    """
    import numpy as np
    import pandas as pd

    hmm = extras.get("hmm")
    monthly = extras.get("monthly")
    if not isinstance(hmm, dict) or hmm.get("error"):
        return None
    if not isinstance(monthly, dict):
        return None
    labels = hmm.get("labelled_series") or {}
    if not labels:
        return None

    idx = pd.to_datetime(monthly.get("dates") or [])
    if len(idx) == 0:
        return None
    label_map = {pd.to_datetime(k): v for k, v in labels.items()}
    regimes = pd.Series([label_map.get(d) for d in idx], index=idx)

    assets = [("Equity", monthly.get("equity")),
              ("IG bonds", monthly.get("ig")),
              ("HY bonds", monthly.get("hy"))]
    regime_order = ["BULL", "TRANSITION", "BEAR"]

    means: dict[str, list[float | None]] = {r: [] for r in regime_order}
    asset_names: list[str] = []
    for asset_name, values in assets:
        if not values:
            continue
        ser = pd.Series(values, index=idx)
        asset_names.append(asset_name)
        for regime in regime_order:
            mask = regimes == regime
            sub = ser[mask].dropna()
            # Annualised mean monthly return — simple multiplication × 12;
            # the chart is a comparative read, not a CAGR table.
            means[regime].append(float(sub.mean() * 12) if len(sub) else None)

    if not asset_names:
        return None

    fig, ax = plt.subplots(figsize=(8, 4.2))
    x = np.arange(len(asset_names))
    width = 0.26
    for i, regime in enumerate(regime_order):
        offset = (i - 1) * width
        vals = [v if v is not None else 0.0 for v in means[regime]]
        ax.bar(x + offset, vals, width=width,
               color=_REGIME_COLOURS[regime],
               label=regime.title())
    ax.set_xticks(x)
    ax.set_xticklabels(asset_names)
    ax.axhline(0, color=_MPL_GREY, linewidth=0.8)
    ax.set_ylabel("Annualised return")
    ax.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda v, _: f"{v * 100:.0f}%"))
    ax.set_title("Mean Annualised Return by HMM Regime", fontsize=11)
    ax.legend(fontsize=8, frameon=False, loc="upper right", ncol=3)
    _style(ax)
    return _finish(fig, plt)


# ── factor_loadings — Carhart betas with 95% CIs (horizontal bars) ────────────

def _render_factor_loadings(
    data: dict[str, Any], extras: dict[str, Any], plt,
) -> bytes | None:
    """
    Horizontal bar chart of BENCHMARK's four Carhart factor betas with
    95%-CI error bars. Single strategy keeps the chart readable on the
    canvas; a multi-strategy version would crowd the same surface.
    """
    rows = data.get("factor_loadings") or []
    if not rows:
        return None
    # Default to BENCHMARK; fall back to the first row when it is absent.
    target = next((r for r in rows if r.get("strategy") == "BENCHMARK"), rows[0])

    factors = [("mkt_rf", "MKT-RF"), ("smb", "SMB"),
               ("hml", "HML"), ("mom", "MOM")]
    names: list[str] = []
    values: list[float] = []
    los: list[float] = []
    his: list[float] = []
    sig: list[bool] = []
    for key, label in factors:
        beta = target.get(key)
        if beta is None:
            # MOM is null when the regression falls back to three-factor —
            # skip the row rather than render a misleading zero bar.
            continue
        names.append(label)
        values.append(float(beta))
        lo = target.get(f"{key}_lo")
        hi = target.get(f"{key}_hi")
        los.append(float(beta - lo) if lo is not None else 0.0)
        his.append(float(hi - beta) if hi is not None else 0.0)
        sig.append(bool(target.get(f"{key}_significant")))

    if not names:
        return None

    fig, ax = plt.subplots(figsize=(8, 4.0))
    y = list(range(len(names)))
    colours = [_MPL_ACCENT if s else _MPL_GREY for s in sig]
    ax.barh(y, values, color=colours,
            xerr=[los, his],
            error_kw={"ecolor": _MPL_GREY, "elinewidth": 1.2, "capsize": 5})
    ax.axvline(0, color=_MPL_GREY, linewidth=0.8)
    ax.set_yticks(y)
    ax.set_yticklabels(names)
    ax.invert_yaxis()  # MKT-RF at top — reading order
    ax.set_xlabel("Beta (95% CI)")
    title = f"Carhart Factor Loadings — {target.get('strategy', 'BENCHMARK')}"
    ax.set_title(title, fontsize=11)
    _style(ax)

    # Legend — solid bar = significant at p<0.05; grey = not.
    from matplotlib.patches import Patch
    legend_handles = [
        Patch(color=_MPL_ACCENT, label="Significant (p < 0.05)"),
        Patch(color=_MPL_GREY, label="Not significant"),
    ]
    ax.legend(handles=legend_handles, fontsize=8, frameon=False,
              loc="lower right")
    return _finish(fig, plt)


# ── factor_returns_attribution — per-year factor contribution (stacked) ───────

def _render_factor_returns_attribution(
    data: dict[str, Any], extras: dict[str, Any], plt,
) -> bytes | None:
    """
    Stacked bar chart per calendar year: each bar's segments are the
    return contribution of MKT-RF / SMB / HML / MOM to the BENCHMARK
    portfolio for that year. Contribution = sum over the months in the
    year of (factor_return[month] × beta[factor]).

    extras["ff_factors"] is the get_ff_factors() output. The
    factor_loadings row is read from data["factor_loadings"] so the
    betas are the same the table on Analytics shows.
    """
    import pandas as pd

    rows = data.get("factor_loadings") or []
    ff_factors = extras.get("ff_factors") or []
    if not rows or not ff_factors:
        return None

    target = next((r for r in rows if r.get("strategy") == "BENCHMARK"), rows[0])
    betas = {
        "mkt_rf": float(target.get("mkt_rf") or 0.0),
        "smb":    float(target.get("smb")    or 0.0),
        "hml":    float(target.get("hml")    or 0.0),
        "mom":    float(target.get("mom")    or 0.0) if target.get("mom") is not None else 0.0,
    }

    ff = pd.DataFrame(ff_factors)
    if "yyyymm" not in ff.columns or ff.empty:
        return None
    ff = ff.set_index("yyyymm")
    for col in ("mkt_rf", "smb", "hml", "mom"):
        if col in ff.columns:
            # ff values are stored as percent (matches analytics.factor_loadings).
            ff[col] = pd.to_numeric(ff[col], errors="coerce") / 100.0

    # Per-month contribution = beta × factor_return.
    contrib = pd.DataFrame(index=ff.index)
    for factor, beta in betas.items():
        if factor in ff.columns:
            contrib[factor] = ff[factor] * beta

    if contrib.empty:
        return None

    # Group by calendar year. yyyymm is an integer like 201907; floor-divide.
    contrib["year"] = (contrib.index.astype(int) // 100)
    yearly = contrib.groupby("year").sum()

    if yearly.empty:
        return None

    fig, ax = plt.subplots(figsize=(8, 4.2))
    years = list(yearly.index)
    # Stacked bars — positive contributions above zero, negative below.
    # Compute running positive and negative offsets per year so negative
    # contributions are stacked downward (the standard waterfall look).
    pos_bottom = [0.0] * len(years)
    neg_bottom = [0.0] * len(years)
    for factor in ("mkt_rf", "smb", "hml", "mom"):
        if factor not in yearly.columns:
            continue
        vals = yearly[factor].fillna(0.0).tolist()
        # Split each bar into the positive and negative halves so they
        # stack from the zero line in opposite directions.
        pos_vals = [v if v > 0 else 0.0 for v in vals]
        neg_vals = [v if v < 0 else 0.0 for v in vals]
        ax.bar(years, pos_vals, bottom=pos_bottom,
               color=_FACTOR_COLOURS[factor], label=_FACTOR_LABELS[factor])
        ax.bar(years, neg_vals, bottom=neg_bottom,
               color=_FACTOR_COLOURS[factor])
        pos_bottom = [a + b for a, b in zip(pos_bottom, pos_vals)]
        neg_bottom = [a + b for a, b in zip(neg_bottom, neg_vals)]

    ax.axhline(0, color=_MPL_GREY, linewidth=0.8)
    ax.set_ylabel("Annual contribution")
    ax.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda v, _: f"{v * 100:.0f}%"))
    ax.set_title(f"Factor Return Attribution by Year — "
                 f"{target.get('strategy', 'BENCHMARK')}", fontsize=11)
    ax.legend(fontsize=8, frameon=False, loc="upper left", ncol=4)
    _style(ax)
    return _finish(fig, plt)


# ── Single-strategy helpers ───────────────────────────────────────────────────

def _pick_strategy_pair(data: dict[str, Any]) -> tuple[str, dict, dict] | None:
    """Resolves (strategy_name, strategy_result, benchmark_result) from
    data["strategy_results"]. Default strategy = REGIME_SWITCHING with
    a fall-back to the first non-BENCHMARK row. Returns None when
    neither side is available."""
    results = data.get("strategy_results") or {}
    benchmark = results.get("BENCHMARK")
    if not isinstance(benchmark, dict):
        return None

    if _DEFAULT_STRATEGY in results:
        name = _DEFAULT_STRATEGY
    else:
        # Pick the first non-BENCHMARK strategy with a non-empty
        # monthly_returns list; falls through to None when only the
        # benchmark is cached.
        name = next(
            (k for k, v in results.items()
             if k != "BENCHMARK" and isinstance(v, dict)
             and v.get("monthly_returns")),
            "",
        )
        if not name:
            return None
    strategy = results.get(name)
    if not isinstance(strategy, dict):
        return None
    return name, strategy, benchmark


def _pairs_to_indexed_series(pairs: list[Any]):
    """Converts a [[iso_date, value], ...] list to a date-indexed series.
    Empty / malformed inputs return an empty series so callers can guard
    on len()==0 rather than on a missing key."""
    import pandas as pd
    if not pairs:
        return pd.Series(dtype="float64")
    try:
        dates = [pd.to_datetime(p[0]) for p in pairs]
        values = [float(p[1]) for p in pairs]
        return pd.Series(values, index=dates).dropna()
    except Exception:  # noqa: BLE001
        return pd.Series(dtype="float64")


# ── drawdown_periods — underwater equity curve, strategy vs benchmark ─────────

def _render_drawdown_periods(
    data: dict[str, Any], extras: dict[str, Any], plt,
) -> bytes | None:
    """
    Underwater curve — % below the running peak — for the strategy and
    benchmark on a single chart. The deepest drawdowns show as filled
    troughs below zero. Strategy in navy, benchmark in amber, the same
    palette the deck uses for IS-vs-OOS lines.
    """
    picked = _pick_strategy_pair(data)
    if picked is None:
        return None
    name, strategy, benchmark = picked

    s = _pairs_to_indexed_series(strategy.get("monthly_returns") or [])
    b = _pairs_to_indexed_series(benchmark.get("monthly_returns") or [])
    if s.empty or b.empty:
        return None

    # Cumulative growth → running peak → underwater %.
    def _underwater(returns):
        wealth = (1.0 + returns).cumprod()
        peak = wealth.cummax()
        return wealth / peak - 1.0

    s_dd = _underwater(s)
    b_dd = _underwater(b)

    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.fill_between(b_dd.index, b_dd.values, 0, color=_MPL_AMBER,
                    alpha=0.15)
    ax.fill_between(s_dd.index, s_dd.values, 0, color=_MPL_ACCENT,
                    alpha=0.18)
    ax.plot(s_dd.index, s_dd.values, color=_MPL_ACCENT, linewidth=1.6,
            label=name)
    ax.plot(b_dd.index, b_dd.values, color=_MPL_AMBER, linewidth=1.4,
            label="BENCHMARK")
    ax.axhline(0, color=_MPL_GREY, linewidth=0.8)
    ax.set_title(f"Drawdown — {name} vs BENCHMARK", fontsize=11)
    ax.set_ylabel("Drawdown")
    ax.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda v, _: f"{v * 100:.0f}%"))
    ax.legend(fontsize=8, frameon=False, loc="lower left")
    _style(ax)
    return _finish(fig, plt)


# ── monthly_returns_heatmap — year × month grid, strategy on top, bench below ─

def _render_monthly_returns_heatmap(
    data: dict[str, Any], extras: dict[str, Any], plt,
) -> bytes | None:
    """
    Two stacked calendar heatmaps — the strategy on top, the benchmark
    on the bottom — sharing a diverging red→white→green colormap so the
    two grids are directly comparable. Each cell is one month's return.
    """
    import numpy as np
    import pandas as pd

    picked = _pick_strategy_pair(data)
    if picked is None:
        return None
    name, strategy, benchmark = picked

    s = _pairs_to_indexed_series(strategy.get("monthly_returns") or [])
    b = _pairs_to_indexed_series(benchmark.get("monthly_returns") or [])
    if s.empty or b.empty:
        return None

    def _grid(series):
        """Pivots a date-indexed monthly series into a year × month
        2D array. Missing months render as NaN."""
        df = series.copy()
        df.index = pd.to_datetime(df.index)
        years = sorted({d.year for d in df.index})
        grid = np.full((len(years), 12), np.nan)
        year_to_row = {y: i for i, y in enumerate(years)}
        for d, v in df.items():
            grid[year_to_row[d.year], d.month - 1] = v
        return years, grid

    s_years, s_grid = _grid(s)
    b_years, b_grid = _grid(b)

    # Shared colour scale — symmetric around zero on the maximum
    # absolute return across both grids, so the same cell colour means
    # the same return on either heatmap.
    all_vals = np.concatenate([s_grid[~np.isnan(s_grid)],
                               b_grid[~np.isnan(b_grid)]])
    vmax = float(np.nanmax(np.abs(all_vals))) if len(all_vals) else 0.1
    vmax = max(vmax, 0.01)

    fig, axes = plt.subplots(2, 1, figsize=(9, 6.4),
                             gridspec_kw={"hspace": 0.30})
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    for ax, years, grid, title in (
        (axes[0], s_years, s_grid, name),
        (axes[1], b_years, b_grid, "BENCHMARK"),
    ):
        im = ax.imshow(grid, aspect="auto", cmap="RdYlGn",
                       vmin=-vmax, vmax=vmax, interpolation="nearest")
        ax.set_xticks(range(12))
        ax.set_xticklabels(months, fontsize=8)
        ax.set_yticks(range(len(years)))
        ax.set_yticklabels(years, fontsize=7)
        ax.set_title(f"Monthly Returns — {title}", fontsize=10,
                     color=_MPL_INK)
        ax.tick_params(colors=_MPL_GREY)
        for spine in ax.spines.values():
            spine.set_color(_MPL_GRID)

    cbar = fig.colorbar(im, ax=axes.ravel().tolist(), shrink=0.78,
                        pad=0.02)
    cbar.ax.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda v, _: f"{v * 100:.0f}%"))
    cbar.ax.tick_params(colors=_MPL_GREY, labelsize=8)
    return _finish(fig, plt)


# ── rolling_sharpe — 36-month rolling Sharpe, strategy vs benchmark ───────────

def _render_rolling_sharpe(
    data: dict[str, Any], extras: dict[str, Any], plt,
) -> bytes | None:
    """
    36-month rolling Sharpe ratio for the strategy and benchmark on a
    single chart. Rolling mean(excess) / rolling std(excess) × √12.
    The risk-free rate (DTB3 monthly) lives on the benchmark result's
    monthly_returns counterpart in the bundle — we read it once and
    align it to each series' index.
    """
    import numpy as np
    import pandas as pd

    picked = _pick_strategy_pair(data)
    if picked is None:
        return None
    name, strategy, benchmark = picked

    s = _pairs_to_indexed_series(strategy.get("monthly_returns") or [])
    b = _pairs_to_indexed_series(benchmark.get("monthly_returns") or [])
    if s.empty or b.empty:
        return None

    # Monthly DTB3 risk-free rate from extras (gathered by
    # chart_render._gather_extended_extras). When the cache is cold and
    # rf is unavailable, falls back to a flat zero — the result is the
    # "raw Sharpe" without rf-subtraction, still comparable strategy-
    # to-benchmark since both lines lose the same constant.
    rf_pairs = extras.get("monthly_rf") or []
    rf = _pairs_to_indexed_series(rf_pairs)

    def _rolling_sharpe(series):
        excess = series.subtract(rf.reindex(series.index).fillna(0.0))
        mean = excess.rolling(_ROLLING_SHARPE_WINDOW).mean()
        std = excess.rolling(_ROLLING_SHARPE_WINDOW).std()
        return (mean / std) * np.sqrt(12)

    s_sh = _rolling_sharpe(s).dropna()
    b_sh = _rolling_sharpe(b).dropna()
    if s_sh.empty or b_sh.empty:
        return None

    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.plot(s_sh.index, s_sh.values, color=_MPL_ACCENT, linewidth=1.6,
            label=name)
    ax.plot(b_sh.index, b_sh.values, color=_MPL_AMBER, linewidth=1.4,
            label="BENCHMARK")
    ax.axhline(0, color=_MPL_GREY, linewidth=0.8, linestyle="--")
    ax.set_title(f"{_ROLLING_SHARPE_WINDOW}-Month Rolling Sharpe — "
                 f"{name} vs BENCHMARK", fontsize=11)
    ax.set_ylabel("Sharpe ratio")
    ax.legend(fontsize=8, frameon=False)
    _style(ax)
    return _finish(fig, plt)


# ── return_distribution — histogram with normal overlay, strategy vs benchmark

def _render_return_distribution(
    data: dict[str, Any], extras: dict[str, Any], plt,
) -> bytes | None:
    """
    Overlaid monthly-return histograms — strategy and benchmark — with
    the corresponding normal-distribution curves drawn on top so the
    departure from normality is visible. Two semi-transparent histograms
    on a single axis with a shared bin grid keeps the comparison clean.
    """
    import math
    import numpy as np

    picked = _pick_strategy_pair(data)
    if picked is None:
        return None
    name, strategy, benchmark = picked

    s = _pairs_to_indexed_series(strategy.get("monthly_returns") or [])
    b = _pairs_to_indexed_series(benchmark.get("monthly_returns") or [])
    if s.empty or b.empty:
        return None

    vals_s = s.values.astype(float)
    vals_b = b.values.astype(float)
    lo = float(min(vals_s.min(), vals_b.min()))
    hi = float(max(vals_s.max(), vals_b.max()))
    bins = np.linspace(lo, hi, _DISTRIBUTION_BINS + 1)

    fig, ax = plt.subplots(figsize=(8, 4.2))
    # density=True so the y-axis is the probability density — keeps
    # the two distributions comparable when their counts differ.
    ax.hist(vals_b, bins=bins, density=True, color=_MPL_AMBER, alpha=0.40,
            label="BENCHMARK", edgecolor="white", linewidth=0.5)
    ax.hist(vals_s, bins=bins, density=True, color=_MPL_ACCENT, alpha=0.55,
            label=name, edgecolor="white", linewidth=0.5)

    # Normal-curve overlays — same colour as the matching histogram.
    xs = np.linspace(lo, hi, 200)
    for vals, colour in ((vals_b, _MPL_AMBER), (vals_s, _MPL_ACCENT)):
        mu = float(np.mean(vals))
        sd = float(np.std(vals))
        if sd > 0:
            pdf = (1.0 / (sd * math.sqrt(2 * math.pi))) \
                * np.exp(-0.5 * ((xs - mu) / sd) ** 2)
            ax.plot(xs, pdf, color=colour, linewidth=1.4)

    ax.axvline(0, color=_MPL_GREY, linewidth=0.8, linestyle="--")
    ax.set_title(f"Monthly Return Distribution — "
                 f"{name} vs BENCHMARK", fontsize=11)
    ax.set_xlabel("Monthly return")
    ax.xaxis.set_major_formatter(
        plt.FuncFormatter(lambda v, _: f"{v * 100:.0f}%"))
    ax.set_ylabel("Density")
    ax.legend(fontsize=8, frameon=False)
    _style(ax)
    return _finish(fig, plt)


# ── significance_journey — Tier 1 gate pass/fail matrix ───────────────────────

def _render_significance_journey(
    data: dict[str, Any], extras: dict[str, Any], plt,
) -> bytes | None:
    """
    A row-per-gate × column-per-strategy matrix of green (PASS) / red
    (FAIL) markers — the project's CLAUDE.md Section 7 Tier-1 gates
    applied to every strategy in the cached results.

    Reads the per-gate per-strategy fields directly off the strategy
    results cache (the backtester output), not off qa_results_cache
    (which stores the QA Agent's checklist verdict). See the chart
    library inventory at the top of chart_render.py for the source
    map.
    """
    results = data.get("strategy_results") or {}
    # Order: BENCHMARK first, then alphabetically — keeps the matrix
    # column order predictable run-to-run.
    names = []
    if "BENCHMARK" in results:
        names.append("BENCHMARK")
    names.extend(sorted(k for k in results if k != "BENCHMARK"))
    if not names:
        return None

    # Each gate is (label, predicate(strategy_dict) → bool).
    gates: list[tuple[str, Any]] = [
        ("Full-period p < 0.005",
         lambda r: float(r.get("p_value_ttest", 1.0)) < _FDR_THRESHOLD),
        ("FDR-corrected q < 0.005",
         lambda r: float(r.get("p_value_corrected", 1.0)) < _FDR_THRESHOLD),
        ("Deflated Sharpe p < 0.005",
         lambda r: float(r.get("dsr_p_value", 1.0)) < _FDR_THRESHOLD),
        ("Out-of-sample significant",
         lambda r: bool(r.get("oos_significant"))),
        (f"CV Stability ≥ {_CV_STABILITY_THRESHOLD:.2f}",
         lambda r: float(r.get("cv_stability_score", 0.0))
                   >= _CV_STABILITY_THRESHOLD),
    ]

    fig, ax = plt.subplots(figsize=(max(6, len(names) * 0.85), 4.0))
    n_rows = len(gates)
    n_cols = len(names)

    # Render each cell as a coloured dot. Scatter is simplest.
    xs = []
    ys = []
    colours = []
    for col, name in enumerate(names):
        r = results.get(name, {})
        for row, (_, predicate) in enumerate(gates):
            xs.append(col)
            ys.append(row)
            try:
                ok = bool(predicate(r))
            except Exception:  # noqa: BLE001 — a missing field is FAIL
                ok = False
            colours.append(_MPL_GREEN if ok else _MPL_RED)
    ax.scatter(xs, ys, c=colours, s=200, edgecolors="white",
               linewidths=1.5, zorder=3)

    ax.set_xticks(range(n_cols))
    ax.set_xticklabels(names, rotation=35, ha="right", fontsize=7.5)
    ax.set_yticks(range(n_rows))
    ax.set_yticklabels([g[0] for g in gates], fontsize=8)
    ax.set_xlim(-0.6, n_cols - 0.4)
    ax.set_ylim(-0.6, n_rows - 0.4)
    ax.invert_yaxis()  # Gate 1 at top — reading order
    ax.set_title("Tier 1 Significance Gates — strategy × gate matrix",
                 fontsize=11)
    _style(ax)

    # Legend — green = PASS, red = FAIL.
    from matplotlib.patches import Patch
    legend_handles = [
        Patch(color=_MPL_GREEN, label="Pass"),
        Patch(color=_MPL_RED, label="Fail"),
    ]
    ax.legend(handles=legend_handles, fontsize=8, frameon=False,
              loc="upper right", bbox_to_anchor=(1.02, 1.18), ncol=2)
    return _finish(fig, plt)


# ── oos_performance — IS vs OOS cumulative returns ─────────────────────────────

def _render_oos_performance(
    data: dict[str, Any], extras: dict[str, Any], plt,
) -> bytes | None:
    """
    Cumulative growth-of-$1 for the strategy, with the last
    _OOS_WINDOW_MONTHS coloured separately as the out-of-sample
    window. A vertical dashed line marks the IS/OOS split — the same
    line the faculty-friendly five-year-OOS framing references.

    Reads the existing data["cumulative_returns"] series, splits at a
    fixed date offset. No fresh walk-forward, no extra compute.
    """
    import pandas as pd

    cr = data.get("cumulative_returns") or {}
    points = cr.get("points") or []
    strategies = cr.get("strategies") or []
    if not points or not strategies:
        return None

    # Strategy choice: same default + fallback as the rest of the
    # extended library — REGIME_SWITCHING, then the first non-BENCHMARK
    # strategy in the cumulative_returns payload.
    name = (_DEFAULT_STRATEGY if _DEFAULT_STRATEGY in strategies else
            next((s for s in strategies if s != "BENCHMARK"), ""))
    if not name:
        return None

    dates = [pd.to_datetime(p["date"]) for p in points]
    values = [p.get(name) for p in points]
    series = pd.Series(values, index=dates).dropna()
    if len(series) <= _OOS_WINDOW_MONTHS:
        # Not enough history to define a meaningful IS window — the
        # chart would be all-OOS.
        return None

    split_idx = len(series) - _OOS_WINDOW_MONTHS
    is_part = series.iloc[:split_idx + 1]   # include split point on both
    oos_part = series.iloc[split_idx:]      # sides so the line is continuous
    split_date = series.index[split_idx]

    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.plot(is_part.index, is_part.values, color=_MPL_ACCENT,
            linewidth=1.6, label=f"In-sample ({len(is_part) - 1} mo)")
    ax.plot(oos_part.index, oos_part.values, color=_MPL_AMBER,
            linewidth=1.6,
            label=f"Out-of-sample ({_OOS_WINDOW_MONTHS} mo)")
    ax.axvline(split_date, color=_MPL_GREY, linestyle="--", linewidth=1.0)
    ax.set_title(f"In-Sample vs Out-of-Sample — {name}", fontsize=11)
    ax.set_ylabel("Growth of $1")
    ax.legend(fontsize=8, frameon=False, loc="upper left")
    _style(ax)
    return _finish(fig, plt)


# ── p_value_distribution — FDR-corrected p-values across strategies ───────────

def _render_p_value_distribution(
    data: dict[str, Any], extras: dict[str, Any], plt,
) -> bytes | None:
    """
    Bar chart of every strategy's FDR-corrected p-value with a dashed
    line at the Tier 1 threshold (0.005). Bars are coloured green when
    they clear the threshold, red when they do not. A bar that exceeds
    the y-axis ceiling is clipped to keep the threshold-crossing area
    readable — the strategy's actual p-value is annotated above the bar.
    """
    results = data.get("strategy_results") or {}
    rows = [(name, float(r.get("p_value_corrected", 1.0)))
            for name, r in results.items()]
    if not rows:
        return None
    rows.sort(key=lambda r: r[1])  # most significant left

    names = [r[0] for r in rows]
    pvals = [r[1] for r in rows]
    colours = [_MPL_GREEN if p < _FDR_THRESHOLD else _MPL_RED for p in pvals]

    # Display ceiling — the threshold needs to be visible even when
    # every p-value is far above it. Use a sensible cap.
    y_max = max(0.05, min(1.0, max(pvals) * 1.10))

    fig, ax = plt.subplots(figsize=(max(6, len(names) * 0.7), 4.2))
    x = list(range(len(names)))
    plotted = [min(p, y_max) for p in pvals]
    ax.bar(x, plotted, color=colours, edgecolor="white", linewidth=0.5)

    # Threshold line.
    ax.axhline(_FDR_THRESHOLD, color=_MPL_AMBER, linestyle="--",
               linewidth=1.4,
               label=f"FDR threshold (q = {_FDR_THRESHOLD})")

    # Annotate each bar with its actual p-value — handy when the cap
    # clips a tall bar.
    for xi, p in zip(x, pvals):
        ax.text(xi, min(p, y_max) + y_max * 0.02,
                f"{p:.3f}", ha="center", va="bottom",
                fontsize=7, color=_MPL_GREY)

    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=35, ha="right", fontsize=7.5)
    ax.set_ylim(0, y_max)
    ax.set_ylabel("FDR-corrected p-value")
    ax.set_title("FDR-Corrected p-values by Strategy", fontsize=11)
    ax.legend(fontsize=8, frameon=False, loc="upper right")
    _style(ax)
    return _finish(fig, plt)


_DISPATCH: dict[str, Any] = {
    "regime_signals":              _render_regime_signals,
    "regime_conditional_returns":  _render_regime_conditional_returns,
    "factor_loadings":             _render_factor_loadings,
    "factor_returns_attribution":  _render_factor_returns_attribution,
    "drawdown_periods":            _render_drawdown_periods,
    "monthly_returns_heatmap":     _render_monthly_returns_heatmap,
    "rolling_sharpe":              _render_rolling_sharpe,
    "return_distribution":         _render_return_distribution,
    "significance_journey":        _render_significance_journey,
    "oos_performance":             _render_oos_performance,
    "p_value_distribution":        _render_p_value_distribution,
}
