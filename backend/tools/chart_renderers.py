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

# Charts that read solely from the gather_document_data bundle (no
# extras needed). Empty for now — all four Commit-2 charts need extras.
# Listed here so the dispatcher can decide whether to skip the extras
# fetch when the only requested chart is data-bundle-only.
EXTENDED_KEYS: frozenset[str] = frozenset({
    "regime_signals",
    "regime_conditional_returns",
    "factor_loadings",
    "factor_returns_attribution",
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


_DISPATCH: dict[str, Any] = {
    "regime_signals":              _render_regime_signals,
    "regime_conditional_returns":  _render_regime_conditional_returns,
    "factor_loadings":             _render_factor_loadings,
    "factor_returns_attribution":  _render_factor_returns_attribution,
}
