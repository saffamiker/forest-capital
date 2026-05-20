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

──────────────────────────────────────────────────────────────────────────
CHART LIBRARY INVENTORY  (audit completed May 19 2026 — Commit 1)
──────────────────────────────────────────────────────────────────────────

SHIPPED — server-renderable today (5 charts):
  rolling_correlation     regime      from data["rolling_correlation"]
  cumulative_returns      performance from data["cumulative_returns"]
  risk_return             performance from data["strategy_results"]
  sensitivity             robustness  via compute_sensitivity() (heavy)
  team_activity           process     from data["team_summary"]

PROPOSED — Commit 2 (regime + factors):
  regime_signals               regime
    Data: HMM posterior probabilities per date.
    Source: classify_hmm_regime() exposes historical_labels (label per
            date) but NOT full per-date posteriors. Two paths:
      A) Render a discrete colored regime band from historical_labels
         (BULL/TRANSITION/BEAR bands) — works today with no detector
         change. Falls short of "stacked area" but is the same signal.
      B) Extend classify_hmm_regime to also return historical_probs
         (posteriors[:] indexed by date) — small addition (~5 lines)
         since posteriors are already computed internally.
    Recommendation: A (band) for Commit 2 — adequate for the canvas
    editor's grid; B can ship later if a true stacked-area is wanted.

  regime_conditional_returns   regime
    Data: regime label per month × monthly returns.
    Source: historical_labels from classify_hmm_regime + monthly returns
            from get_monthly_returns() OR per-strategy monthly_returns
            from get_latest_strategy_cache(). Group returns by regime
            label, render bar of mean monthly return per regime
            (optionally per asset or top-strategies).
    Ready today.

  factor_loadings              factors
    Data: Carhart betas with 95% confidence intervals.
    Source: analytics.factor_loadings() returns betas + significance
            flags but NOT CIs. statsmodels exposes model.conf_int(0.05)
            for lower/upper bounds. Small extension to factor_loadings
            (or compute in the renderer from the same regression).
    Recommendation: extend analytics.factor_loadings to expose conf_int
    lower/upper per coefficient (preserves a single source of truth).

  factor_returns_attribution   factors
    Data: Carhart factor returns × portfolio betas, summed per year.
    Source: ff_factors_monthly (get_ff_factors) for factor returns;
            per-strategy betas from analytics.factor_loadings; multiply
            monthly to get per-factor monthly contribution, sum per
            calendar year. Default strategy: BENCHMARK.
    Ready today (pure compute in the renderer).

PROPOSED — Commit 3 (performance + risk):
  drawdown_periods             risk
    Data: cumulative returns → running peak → underwater %.
    Source: data["cumulative_returns"]["points"] already contains
            growth-of-$1 per strategy. Pure derived compute.
    Ready today.

  monthly_returns_heatmap      performance
    Data: monthly returns indexed by (year, month).
    Source: a strategy's monthly_returns list — default BENCHMARK from
            get_latest_strategy_cache(), or an asset series from
            get_monthly_returns(). Pivot into a year × month grid.
    Ready today.

  rolling_sharpe               performance
    Data: monthly returns + risk-free rate, 36-month rolling.
    Source: get_monthly_returns() (asset rf) or a strategy's monthly
            returns. rolling mean(excess) / rolling std(excess) × √12.
    Ready today.

  return_distribution          performance
    Data: monthly returns series + normal overlay (mean, std).
    Source: any strategy's monthly_returns. Default BENCHMARK.
    Ready today.

PROPOSED — Commit 4 (significance):
  significance_journey         significance
    Data: per-strategy Tier 1 gate results.
    Source: get_latest_strategy_cache() — each strategy result already
            carries p_value_ttest, p_value_corrected, dsr_p_value,
            oos_significant, cv_stability_score, tier1_gates_passed.
            Compose a row-per-gate × column-per-strategy matrix.
    Ready today. Independent of qa_results_cache (which stores the
    QA Agent's verdict, not the per-gate per-strategy data).

  oos_performance              significance
    Data: in-sample vs out-of-sample cumulative returns per strategy.
    Source: PARTIAL — strategies carry oos_sharpe / oos_cagr (aggregate
            stats) and walk_forward_test in backtester yields
            oos_sharpe_mean/std/min/max across folds — but NEITHER
            produces a per-date IS/OOS-split cumulative series. Two
            paths:
      A) Split data["cumulative_returns"] at a fixed cutoff (e.g. 80%
         of the date range or the train_end constant from config) and
         color the two halves IS vs OOS. Same data we already have,
         renders today.
      B) Run a fresh walk-forward and assemble a stitched OOS path.
         Heavy — would need to be cached. Not worth Commit 4.
    Recommendation: A for Commit 4. Add the IS/OOS cutoff as a
    constant inside the renderer.

  p_value_distribution         significance
    Data: per-strategy p_value_corrected (FDR-corrected).
    Source: get_latest_strategy_cache() — every strategy result has
            p_value_corrected. Bar chart, bars colored pass/fail at
            the 0.005 FDR threshold, dashed line at 0.005.
    Ready today.

──────────────────────────────────────────────────────────────────────────
DATA-AVAILABILITY SUMMARY
──────────────────────────────────────────────────────────────────────────
  Ready today (no upstream change): 9 of 11 new charts
  Needs a small upstream addition: 2 of 11 —
    * regime_signals — expose HMM per-date posteriors (small detector
      change) for the true stacked-area; the colored-band fallback
      works today without any change
    * factor_loadings — expose statsmodels conf_int() in
      analytics.factor_loadings (single-source-of-truth principle)

  Charts requiring the QA cache: NONE.
    The significance charts read per-strategy fields from the strategy
    results cache (the backtester output), not from qa_results_cache
    (which stores the QA Agent's checklist verdict).

  Charts requiring regime_signals_cache: NONE for time-series renders.
    regime_signals_cache stores only the current/latest regime reading
    (a single row, 15-minute TTL) — never a historical series. Charts
    that need regime-over-time read from classify_hmm_regime's
    historical_labels (run on the full monthly series) instead.

  Heaviest compute paths to guard:
    sensitivity (existing): already on its own opt-in path
    regime_signals + regime_conditional_returns: classify_hmm_regime
      fits a Baum-Welch HMM (~200 iters). Has an in-process cache; the
      first render after a cold start takes ~1-2 s, then cached.
    factor_loadings + factor_returns_attribution: a statsmodels OLS
      per strategy — fast (<200 ms total).

──────────────────────────────────────────────────────────────────────────
GROUP 3A AUDIT  (May 19 2026 — Commit 1)
──────────────────────────────────────────────────────────────────────────
Cross-reference between the Analytics page (frontend/src/pages/
AcademicAnalytics.tsx) and AVAILABLE_CHARTS. The page renders four
Recharts components in this order:

  CumulativeReturnChart        → cumulative_returns       ✓ shipped
  RollingCorrelationChart      → rolling_correlation      ✓ shipped
  RollingExcessReturnChart     → NOT IN AVAILABLE_CHARTS  ← gap
  SensitivityAnalysis          → sensitivity              ✓ shipped

(Tables on the same page — SummaryStatistics, RegimeConditional,
DrawdownComparison, FactorLoadings, StrategyMethodology — already
have chart-form counterparts in AVAILABLE_CHARTS where appropriate;
they are not chart components themselves so not part of this gap.)

THE ONE GAP — rolling_excess_return

  What it shows: 12-month rolling total return of each strategy
    minus the 100% equity benchmark, plotted per month. Above-zero
    half-plane shading marks periods of outperformance, below-zero
    underperformance. A vertical regime-break marker on the first
    plotted month at or after 2022-01-01 anchors the central project
    finding to the same chart.

  Data needed: per-strategy 12-month trailing-compound return minus
    the benchmark's same-window trailing-compound return, by month.
    Already shaped by analytics.rolling_excess_return(strategy_
    results, window=12) — returns {strategies, points[{date, ...}],
    window_months}.

  Data availability: READY TODAY — no upstream change.
    strategy_results (with monthly_returns lists per strategy) is
    already in the gather_document_data bundle as data["strategy_
    results"]. The renderer can call analytics.rolling_excess_return
    directly. No new endpoint, no cache miss, no run_all_strategies()
    recompute.

  Render complexity: LOW.
    Single-axis matplotlib line plot, one line per strategy, two
    half-plane fills (axhspan above/below zero), a vertical
    regime-break line at the post-2022 anchor. Same shape family as
    rolling_sharpe and rolling_correlation (already written).
    Estimated <80 lines in chart_renderers.py.

  Category: "performance" — alongside cumulative_returns and the
    rolling-* family.

OTHER PAGES — out of scope for GROUP 3A but noted for completeness:
  Recharts components also live in components/charts (the
  Statistical Evidence and Regime Analysis dashboards), Dashboard
  (cumulative + efficient frontier), TeamActivityCharts, and
  ActivityBreakdownPanel (Settings → Users). The corresponding
  canvas charts already exist in AVAILABLE_CHARTS
  (significance_journey, regime_*, factor_*, p_value_distribution,
  team_activity, etc.); EfficientFrontier is the Dashboard-only
  scatter we deliberately don't ship as a canvas chart. Only the
  Analytics page had a missing chart-component-to-AVAILABLE_CHARTS
  gap.

──────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import io
import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Server-renderable canvas charts. Two backing renderer families:
#   - The "deck" five (academic_deck.render_deck_charts) — the same ones
#     the .pptx export ships.
#   - The "extended" set (chart_renderers.render_extended_charts) — canvas-
#     only, added by the chart-library expansion (commits 2-4 of that build).
#
# Order — and the `category` field — is the canvas chart picker's display
# grouping (canvas editor Commit 5/7). The picker reads first-seen order
# from this list and renders one section per category. rolling_correlation
# is grouped with the time-series performance charts the user navigates to
# from the central finding; risk_return and sensitivity are grouped under
# "risk" because that is how a faculty panel reads them.
AVAILABLE_CHARTS: list[dict[str, str]] = [
    # ── Regime Analysis ───────────────────────────────────────────────────
    {"key": "regime_signals",
     "label": "Regime Probability Over Time",
     "description": "HMM posterior probability of BULL / TRANSITION / "
                    "BEAR regime over the full monthly history.",
     "category": "regime"},
    {"key": "regime_conditional_returns",
     "label": "Returns by Regime",
     "description": "Mean annualised return per asset class, split by "
                    "HMM regime state.",
     "category": "regime"},
    # ── Factors ───────────────────────────────────────────────────────────
    {"key": "factor_loadings",
     "label": "Carhart Factor Loadings",
     "description": "Four-factor betas (MKT-RF, SMB, HML, MOM) with "
                    "95% confidence intervals, BENCHMARK portfolio.",
     "category": "factors"},
    {"key": "factor_returns_attribution",
     "label": "Factor Return Attribution",
     "description": "Stacked yearly breakdown of factor contributions "
                    "to the portfolio's annual return.",
     "category": "factors"},
    # ── Performance ───────────────────────────────────────────────────────
    {"key": "rolling_correlation",
     "label": "Rolling Correlation",
     "description": "Equity-bond rolling correlation with the 2022 "
                    "regime-break marker — the project's central finding.",
     "category": "performance"},
    {"key": "cumulative_returns",
     "label": "Cumulative Returns",
     "description": "Growth of $1 across every strategy and the "
                    "benchmark over the full study period.",
     "category": "performance"},
    {"key": "rolling_sharpe",
     "label": "Rolling Sharpe",
     "description": "36-month rolling Sharpe ratio for the strategy and "
                    "the benchmark, with a zero reference line.",
     "category": "performance"},
    {"key": "rolling_excess_return",
     "label": "Rolling Excess Return",
     "description": "12-month rolling total return of each strategy "
                    "minus the 100% equity benchmark, with the 2022 "
                    "regime-break marker.",
     "category": "performance"},
    {"key": "return_distribution",
     "label": "Return Distribution",
     "description": "Histogram of monthly returns with a normal-curve "
                    "overlay — strategy vs benchmark.",
     "category": "performance"},
    {"key": "monthly_returns_heatmap",
     "label": "Monthly Returns Heatmap",
     "description": "Calendar heatmap of monthly returns — strategy on "
                    "top, benchmark below, shared diverging colour scale.",
     "category": "performance"},
    # ── Risk ──────────────────────────────────────────────────────────────
    {"key": "drawdown_periods",
     "label": "Drawdown",
     "description": "Underwater equity curve — % below the running "
                    "peak — for the strategy and the benchmark.",
     "category": "risk"},
    {"key": "risk_return",
     "label": "Risk vs Return",
     "description": "Each strategy plotted by annualised return against "
                    "volatility.",
     "category": "risk"},
    {"key": "sensitivity",
     "label": "Sensitivity Analysis",
     "description": "How the headline results hold up when key "
                    "parameters are varied — a robustness check.",
     "category": "risk"},
    # ── Significance ──────────────────────────────────────────────────────
    {"key": "significance_journey",
     "label": "Significance Journey",
     "description": "Row per Tier 1 gate, column per strategy — green "
                    "PASS / red FAIL for each of the five gates.",
     "category": "significance"},
    {"key": "oos_performance",
     "label": "In-Sample vs Out-of-Sample",
     "description": "Cumulative growth-of-$1 for the strategy with the "
                    "last 60 months coloured as the OOS window.",
     "category": "significance"},
    {"key": "p_value_distribution",
     "label": "p-value Distribution",
     "description": "FDR-corrected p-value per strategy with the "
                    "0.005 Tier 1 threshold marked.",
     "category": "significance"},
    # ── Activity ──────────────────────────────────────────────────────────
    {"key": "team_activity",
     "label": "Team Activity",
     "description": "The project build timeline — commits, council runs "
                    "and reviews per team member.",
     "category": "activity"},
]

# Charts backed by the deck renderer (academic_deck.render_deck_charts).
# Every other key on AVAILABLE_CHARTS is routed to the extended renderer.
_DECK_KEYS = frozenset({
    "rolling_correlation", "cumulative_returns", "risk_return",
    "sensitivity", "team_activity",
})

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
    """The raw chart PNG from whichever renderer family backs this key,
    or None when its source data is unavailable (cold caches / the test
    environment). Dispatches between the deck renderer (the five charts
    the .pptx export ships) and the extended renderer (canvas-only)."""
    import asyncio

    from tools.academic_export import gather_document_data

    data = await gather_document_data()

    if chart_key in _DECK_KEYS:
        from tools.academic_deck import render_deck_charts
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

    # Extended renderers — gather the per-chart extras (HMM history, raw
    # monthly returns, ff_factors) before crossing into the thread.
    from tools.chart_renderers import render_extended_charts
    extras = await _gather_extended_extras(chart_key)
    charts = await asyncio.to_thread(
        render_extended_charts, chart_key, data, extras)
    return charts.get(chart_key)


async def _gather_extended_extras(chart_key: str) -> dict[str, Any]:
    """Per-chart extras the extended renderers need beyond the data
    bundle. Each branch is fail-open — a missing extra produces a None
    PNG, which the caller turns into the placeholder.

    Heavy work — the HMM fit and the FF read — is paid only for the
    charts that consume it, not on every extended-chart render.
    """
    import asyncio

    extras: dict[str, Any] = {}

    if chart_key in {"regime_signals", "regime_conditional_returns"}:
        # HMM on the monthly equity series. The detector has its own
        # in-process cache keyed by series fingerprint — a second chart
        # request in the same trading day hits that cache and skips the
        # Baum-Welch fit.
        try:
            from tools.cache import get_monthly_returns
            monthly = await get_monthly_returns()
            extras["monthly"] = monthly
            if monthly:
                import pandas as pd
                from tools.regime_detector import fit_hmm_historical
                idx = pd.to_datetime(monthly["dates"])
                equity = pd.Series(monthly["equity"], index=idx)
                extras["hmm"] = await asyncio.to_thread(
                    fit_hmm_historical, equity)
        except Exception as exc:  # noqa: BLE001
            log.warning("chart_render_hmm_unavailable",
                        chart_key=chart_key, error=str(exc))

    if chart_key == "factor_returns_attribution":
        try:
            from tools.cache import get_ff_factors
            extras["ff_factors"] = await get_ff_factors()
        except Exception as exc:  # noqa: BLE001
            log.warning("chart_render_ff_unavailable",
                        chart_key=chart_key, error=str(exc))

    if chart_key == "rolling_sharpe":
        # Excess-return Sharpe needs the monthly DTB3 risk-free rate
        # alongside each series. The raw values live in get_monthly_returns
        # under the "rf" key; we surface them as a [[iso_date, value]]
        # list so the renderer can _pairs_to_indexed_series them like
        # every other monthly series.
        try:
            from tools.cache import get_monthly_returns
            monthly = await get_monthly_returns()
            if monthly:
                dates = monthly.get("dates") or []
                rf = monthly.get("rf") or []
                extras["monthly_rf"] = [
                    [d, v] for d, v in zip(dates, rf) if v is not None
                ]
        except Exception as exc:  # noqa: BLE001
            log.warning("chart_render_rf_unavailable",
                        chart_key=chart_key, error=str(exc))

    return extras


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
