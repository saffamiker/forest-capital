"""
tools/academic_deck.py

Assembles the 10-slide final presentation deck (.pptx) from AI-generated
slide JSON and light-mode chart images.

The deck rebuild (May 28 2026) is JSON-driven: a single Academic Writer
call (deck_generation_prompt() through harness_narrative()) returns
content for all ten slides — {slide_number, title, bullets, table_data,
speaker_notes} — and build_presentation_deck() lays them out. The ten
canonical SLIDE_TITLES and per-slide chart roles (SLIDE_CHARTS) are
fixed; the AI fills the content from the live data context.

  render_deck_charts()  — renders the legacy five canvas/deck charts as
    light-mode PNGs with matplotlib (white background, navy ink — NOT the
    platform's dark theme). Kept because chart_render.py's canvas-render
    path depends on it; the rebuilt deck renders its per-slide charts
    through the chart_render deck renderers instead. matplotlib is lazy
    and guarded — unavailable matplotlib / missing data returns None and
    the slide degrades to a [DATA PENDING] note.

  build_presentation_deck(slides, charts)  — lays out the ten slides in a
    professional navy/white theme and returns the .pptx bytes. Pure
    assembly: no LLM calls, no database reads. Always emits exactly ten
    slides with the canonical titles; a missing slide / table / chart
    degrades to a [DATA PENDING] note rather than failing.

Every slide carries the AI DRAFT footer and a speaker-notes verification
caveat — the deck is a first draft for the team to refine before the
July 1 presentation.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any

import structlog
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

from tools.academic_export import (
    table_drawdown, table_factor_loadings, table_regime_conditional,
    table_summary_statistics,
)

log = structlog.get_logger(__name__)

# ── Validated production constants (May 28 2026 deck rebuild) ─────────────────
# These are the validated, authoritative figures for the 10-slide final deck.
# They are injected into the generation context block so the AI never has to
# invent them, and they backstop the slide-spec text. Performance numbers for
# the strategy tables are NOT here — those come live from gather_document_data().
#
# ACADEMIC SUBMISSION FIGURES — locked to the December 2025 data lock.
# DO NOT update to a more-recent dataset for any reason. The June 3
# cohort peer review and the July 1 panel defend the submitted record;
# replacing these figures with live performance breaks that record. The
# live figures (Jan-May 2026 included) are surfaced separately on the
# Performance Record page (Live Figure row); the brief and the final
# presentation continue to quote the locked academic figures below.
# (User directive, May 31 2026.)
OOS_SHARPE_REGIME_CONDITIONAL = 0.8576
OOS_SHARPE_BENCHMARK = 0.4341
OOS_SHARPE_EQUAL_WEIGHT = 0.1264
CORRELATION_PRE_2022 = -0.05
CORRELATION_POST_2022 = 0.57
PLAY_BY_PLAY_EVENTS = 9

# June 6 2026 — additional locked figures for the 6-slide rewrite.
# The drawdown numbers are the academic-submission max drawdowns (over
# the full study period, December 2025 lock), benchmark vs the regime-
# conditional blend. PLAY_BY_PLAY_ADD_VALUE is the council's value-add
# count out of the {PLAY_BY_PLAY_EVENTS} named market events tested.
# These are stated in the slide content directly (slide 1, slide 4)
# and locked against the same December 2025 dataset as the Sharpe
# figures above — DO NOT update for the panel defense.
MAX_DRAWDOWN_BENCHMARK = -0.526
MAX_DRAWDOWN_REGIME_CONDITIONAL = -0.253
PLAY_BY_PLAY_ADD_VALUE = 2

# Regime state is LIVE-SOURCED, never a constant. The current regime, its HMM
# posterior confidence, and the live blend weights drift as new monthly data
# lands, so they would go stale before the presentation. They are wired into
# the deck context block at generation time from detect_current_regime() (the
# same source the Forward Projection tile and CIO card use). Slide 6's spec
# below instructs the model to read current_regime / regime_confidence /
# blend_weights from the context rather than any baked-in value.

# The real platform universe — confirmed May 28 2026. The earlier-draft
# TLT / DJP / Trend Following / Dynamic Risk Parity names are NOT used.
_STATIC_STRATEGIES = ["CLASSIC_60_40", "EQUAL_WEIGHT", "RISK_PARITY",
                      "BLACK_LITTERMAN"]
_DYNAMIC_STRATEGIES = ["MOMENTUM_ROTATION", "REGIME_SWITCHING", "VOL_TARGETING",
                       "MIN_VARIANCE", "MAX_SHARPE_ROLLING"]

# June 6 2026 — Six-slide rewrite. The previous 10-slide structure split
# the answer across an introduction (slides 1-2), evidence (3-4), method
# (5-6), validation (7-8) and limitations + conclusions (9-10) — a
# structure that delayed the verdict and read as a paper, not a pitch.
# The new structure leads with the answer (slide 1), establishes the
# practical problem (slide 2), shows the evidence (slide 3), discloses
# the honest validation result (slide 4), names the five team-only
# decisions that the platform could not make (slide 5, addresses the
# 3/5 division-of-labor score from the midpoint), and closes with the
# live recommendation (slide 6, the only slide that pulls live data
# every regeneration — regime + blend updated on presentation morning).
#
# The builder always emits exactly these six slides; when the AI JSON
# is missing or unparseable (the test environment, or an LLM outage)
# the slide still appears with its canonical title and a [DATA PENDING]
# body. SLIDE_CHARTS maps a slide number to the deck-chart role the
# builder embeds on it.
SLIDE_TITLES = [
    "Does holding only equities cost you?",                # 1
    "Why this question got harder in 2022",                # 2
    "Three strategies, one comparison",                    # 3
    "We tried to break our own answer",                    # 4
    "Five decisions only the team made",                   # 5
    "The answer — updated on presentation morning",        # 6
]
DECK_SLIDE_COUNT = len(SLIDE_TITLES)

# slide_number → chart role. The role string is resolved to a chart_render
# renderer + arguments in main._render_deck_slide_charts. Slides not listed
# carry no chart. The six-slide rewrite (June 6 2026) reuses the existing
# chart renderers where the shape matches — rolling correlation for slide
# 2, strategy_comparison bars for slide 3 (OOS Sharpe comparison across
# three series), and efficient_frontier with the live blend position
# marked for slide 6. Slide 1's two-bar drawdown chart, slide 4's
# nine-event scorecard, and slide 5 (no chart) all render directly from
# the slide body tables — no custom matplotlib renderer needed.
SLIDE_CHARTS: dict[int, str] = {
    2: "rolling_correlation",
    3: "strategy_comparison_oos_sharpe",
    6: "efficient_frontier",
}


# ── Generation prompt (passed verbatim to harness_narrative) ──────────────────
# DECK_GENERATION_PROMPT is the preamble; SLIDE_SPECIFICATIONS lists the ten
# slides' required content. deck_generation_prompt() concatenates them — the
# full text handed to harness_narrative() as the generation task.
DECK_GENERATION_PROMPT = """\
You are generating content for a 6-slide investment research presentation \
for FNA 670 (Financial Strategies and Analytics). The audience is Dr. \
Katerina Panttser and peer reviewers. Runtime is approximately 8-10 \
minutes — slides 1-4 land in 4-5 minutes total, slide 5 in 1.5 minutes, \
slide 6 in 3 minutes including the live regime read.

The central question of the project is: \
"Does diversification outperform 100% equity?" Every slide must \
contribute to answering that question. The title slide states the \
question; every speaker note references it explicitly. The answer \
appears on slide 1 (the drawdown answer) and again on slide 6 (the \
live blend recommendation). The slides in between are evidence.

The team is:
Bob Thao -- quantitative analysis, regime hypothesis, economic \
significance threshold
Michael Ruurds -- platform engineering, architecture, OOS window \
design, asset scope decisions
Molly Murdock -- presentation, validation framework, 9-event \
play-by-play, peer review

Using ONLY the data provided in the context block, generate structured slide \
content for all 6 slides. Do not invent numbers. If a value is missing from \
context, write [DATA PENDING].

For each slide return:
- title (use the exact canonical title from the spec below)
- 3-5 bullet points (factual, specific, no filler phrases)
- table_data (if applicable): column headers + row data as JSON
- speaker_notes (the full spoken script from the spec below — paraphrase \
in your own voice but cover every required point)

Respond only in JSON. No preamble, no markdown fences. Structure:
{
  "slides": [
    {
      "slide_number": 1,
      "title": "...",
      "bullets": ["...", "..."],
      "table_data": null or {
        "headers": [...],
        "rows": [[...], ...]
      },
      "speaker_notes": "..."
    }
  ]
}
"""

# The slide specifications. Constants are interpolated; performance numbers for
# the strategy tables are deliberately left for gather_document_data() to
# supply (the prompt instructs the model to pull them from context, never
# hardcode them). Strategy names are the real platform names.
SLIDE_SPECIFICATIONS = f"""\
SLIDE SPECIFICATIONS

Title slide convention: every deck opens on slide 1 with the central
question stated explicitly: "Does diversification outperform 100%
equity?" Every speaker note references the central question — it is
the thread the panel follows from slide 1 to slide 6.

Slide 1 -- Does holding only equities cost you?
Key number: {MAX_DRAWDOWN_BENCHMARK:.1%} vs {MAX_DRAWDOWN_REGIME_CONDITIONAL:.1%} \
(max drawdown, benchmark vs the regime-conditional blend)
Required bullets:
- The answer is yes. A 100%-equity allocation cost 27 points of additional \
drawdown over the study period.
- Benchmark max drawdown: {MAX_DRAWDOWN_BENCHMARK:.1%} (peak-to-trough).
- Regime-conditional blend max drawdown: \
{MAX_DRAWDOWN_REGIME_CONDITIONAL:.1%} — less than half.
- The rest of the deck is the evidence behind this number.
Required table: none
Chart: two-bar drawdown comparison (benchmark red, blend green) — no other
strategies on this slide. (Renders via strategy_comparison filtered to
benchmark + blend.)
Speaker notes: "Before we show you anything else — here is the answer. \
A diversified, regime-aware blend would have cut your worst drawdown in \
half. Everything we show you for the next 8-10 minutes is the evidence \
behind that number." Time: ~1 minute.

Slide 2 -- Why this question got harder in 2022
Key number: equity-bond correlation moved from {CORRELATION_PRE_2022:+.2f} \
pre-2022 to {CORRELATION_POST_2022:+.2f} post-2022
Required bullets:
- The traditional 60/40 diversification answer broke in 2022.
- Equity-bond correlation moved from {CORRELATION_PRE_2022:+.2f} pre-2022 to \
{CORRELATION_POST_2022:+.2f} post-2022 — a structural inversion, not a \
short-term anomaly.
- Bonds stopped cushioning equity falls; both fell together.
- The diversification question now depends on KNOWING WHAT REGIME YOU ARE IN, \
not on a fixed mix.
Required table: none
Chart: rolling 12-month equity-bond correlation 2002-2026, single line, zero
axis highlighted. (Renders via rolling_correlation.)
Speaker notes: "The traditional diversification answer broke in 2022. Bonds \
stopped cushioning equity falls — they fell together. The old playbook \
failed. You need to know what regime you are in." Time: ~1 minute.

Slide 3 -- Three strategies, one comparison
Key number: {OOS_SHARPE_REGIME_CONDITIONAL} vs {OOS_SHARPE_BENCHMARK} (OOS \
Sharpe, regime-conditional blend vs the 100%-equity benchmark)
Required bullets:
- We tested 10 strategies; three are what matter for the recommendation.
- 100% equity benchmark — the baseline the question asks about.
- Best static diversifier — the strongest fixed-mix alternative.
- Dynamic regime-aware blend — the recommendation.
- The blend wins on risk-adjusted return AND on drawdown.
- Test window: the 40 months AFTER the 2022 correlation break, not before. \
The OOS evidence reflects the environment the hypothesis addresses.
Required table:
Headers: Strategy | OOS Sharpe | OOS Return | OOS Volatility | Max DD
Rows: BENCHMARK, the best static strategy (pull from context), and the \
regime-conditional blend. Pull every performance figure from the \
strategy_performance section of the context — do not invent any.
Chart: three-bar OOS Sharpe comparison (benchmark / best static / dynamic
blend). Other 7 strategies dropped from the body — they live in the
Analytical Appendix. (Renders via strategy_comparison_oos_sharpe.)
Speaker notes: "We tested 10 strategies. Three are what matter: 100% \
equity as the baseline, the best static diversifier, and the dynamic \
regime-aware blend. The blend wins on risk-adjusted return AND drawdown — \
in the 40-month window after the correlation break, not before it." \
Time: ~1.5 minutes.

Slide 4 -- We tried to break our own answer
Key number: {PLAY_BY_PLAY_ADD_VALUE} of {PLAY_BY_PLAY_EVENTS} (events where \
the council added value, on shock days)
Required bullets:
- We tested the council against {PLAY_BY_PLAY_EVENTS} named market events \
we did not choose — the events were committed to the database BEFORE the \
council was asked about them.
- Honest result: {PLAY_BY_PLAY_ADD_VALUE} of {PLAY_BY_PLAY_EVENTS} events \
where the council added value.
- We are NOT selling a crisis-prediction engine.
- We ARE selling allocation discipline that compounds over a full cycle.
- The cumulative outperformance comes from systematic regime weighting \
across every month, not from calling individual crises.
Required table:
Headers: Event | Date | Council Signal | Outcome
Rows: all {PLAY_BY_PLAY_EVENTS} events from the play_by_play_events context \
section. Event names only, no quantitative outcome scores — the slide is a \
scorecard, not a P&L attribution.
Chart: none — the table is the scorecard.
Speaker notes: "We tested against {PLAY_BY_PLAY_EVENTS} named market events \
we did not choose. Honest result: value added in {PLAY_BY_PLAY_ADD_VALUE} \
of {PLAY_BY_PLAY_EVENTS}. We are not selling a crisis-prediction engine. \
We are selling allocation discipline that compounds over a full cycle. The \
Sharpe advantage comes from being correctly positioned across all months, \
not from calling individual shocks." Time: ~1 minute.

Slide 5 -- Five decisions only the team made
Required bullets (five numbered, each one short paragraph):
1. Regime hypothesis -- Bob Thao. Chose to test whether the 2022 correlation \
inversion creates distinct regimes requiring different allocation rules. \
The platform cannot hypothesise; it tests.
2. Economic significance threshold -- Bob Thao. Set the bar at drawdown \
reduction and capital preservation rather than statistical Sharpe edge. \
No strategy clears p < 0.005; framing the case on economic magnitude was \
a deliberate analytical choice.
3. Out-of-sample window design -- Michael Ruurds. Chose to begin the OOS \
period AFTER the 2022 break, not before. The evidence reflects the \
environment the hypothesis addresses.
4. Asset scope -- Michael Ruurds. Three asset classes was the graduate \
project boundary. Designed the architecture to be extensible — HMM and \
transition matrix work with any return series; the scope is a constraint, \
not an architectural limit.
5. Validation framework -- Molly Murdock. Designed the \
{PLAY_BY_PLAY_EVENTS}-event play-by-play scorecard and the 4-layer data \
integrity framework, including the honest \
{PLAY_BY_PLAY_ADD_VALUE}-of-{PLAY_BY_PLAY_EVENTS} result. Chose to surface \
the limitation rather than omit it.
Required table: none — the bullets carry the section.
Chart: none.
Speaker notes: "The platform runs the computation. Five decisions required \
human judgment. Each was a deliberate choice — not a platform output. \
Without these five decisions, the platform produces tables of numbers \
without a thesis." Time: ~1.5 minutes.

Slide 6 -- The answer — updated on presentation morning
Key number: LIVE — pulled from detect_current_regime() on the day of \
generation. The regime label, the confidence percentage, and the blend \
weights all come from the context block; do NOT hardcode any of them.
Required bullets:
- Yes. Diversification outperforms 100% equity over the study period.
- In the CURRENT regime: state the regime (current_regime), the confidence \
(regime_confidence as a percentage), and the per-strategy live blend \
weights from the context.
- These are ASSET-CLASS WEIGHTS for a portfolio of individual stocks and \
bonds — Forest Capital fills each envelope with its own security selection.
- The project scope was three asset classes. The architecture has no such \
limit.
- The blend updates as the regime read changes — refresh this slide on \
presentation morning.
Required table:
Headers: Regime | Equity | IG Bonds | HY Bonds | Notes
Rows: BULL (from regime_blends.BULL aggregated to asset class), BEAR (from \
regime_blends.BEAR), TRANSITION (from regime_blends.TRANSITION). Three rows \
showing what the blend SHIFTS TO on a regime flip — the table is a watch \
list, not a recommendation. The CURRENT row (whichever matches the live \
regime) is highlighted in the speaker notes; the table itself shows all \
three for context.
Chart: efficient_frontier with the current live blend position marked.
(Renders via efficient_frontier with blend_weights from context.)
Speaker notes: "Yes. Diversification outperforms 100% equity. In the \
current [LIVE REGIME] regime: [LIVE BLEND]. Asset class weights for a \
portfolio of individual stocks and bonds. The project scope was three \
asset classes. The architecture has no such limit." Time: ~3 minutes \
including the live regime read.
"""


def deck_generation_prompt() -> str:
    """The full generation task text — preamble plus slide specifications —
    handed to harness_narrative() as the deck-generation instruction."""
    return f"{DECK_GENERATION_PROMPT}\n\n{SLIDE_SPECIFICATIONS}"


# ── Professional navy/white theme — deliberately NOT the platform dark UI ─────
_NAVY = RGBColor(0x1A, 0x2A, 0x4A)
_NAVY_SOFT = RGBColor(0x2D, 0x4A, 0x6B)
_WHITE = RGBColor(0xFF, 0xFF, 0xFF)
_INK = RGBColor(0x1A, 0x1A, 0x2E)
_GREY = RGBColor(0x4A, 0x4A, 0x6A)
_ACCENT = RGBColor(0x1D, 0x4E, 0xD8)
_AMBER = RGBColor(0xB4, 0x53, 0x09)

_SLIDE_W = Inches(13.333)
_SLIDE_H = Inches(7.5)

_AI_DRAFT_FOOTER = ("AI DRAFT — REQUIRES HUMAN REVIEW · "
                    "first-draft deck; verify every figure before presenting")

# CAVEAT 4 — the verification note added to every slide's speaker notes.
_MOLLY_VERIFY_NOTE = (
    "[MOLLY — VERIFY BEFORE PRESENTING: Confirm the data on this slide "
    "matches the current platform output. Rewrite the talking points in "
    "your own voice. Do not read AI-generated text verbatim during the "
    "presentation.]"
)

# CAVEAT 1 + CAVEAT 5 — the review warning and submission checklist,
# carried in the title slide's speaker notes (the deck's equivalent of
# the .docx header warning box and end-of-document checklist).
_DECK_TITLE_NOTE = (
    "AI DRAFT — REVIEW REQUIRED. This deck was generated by AI from "
    "platform data. Before presenting:\n"
    "CITATIONS: verify every cited source exists and says what is "
    "claimed.\n"
    "STATISTICS: confirm every number against the platform Analytics "
    "page — if a value differs from the screen, the screen wins.\n"
    "YOUR VOICE: rewrite every talking point in your own words; do not "
    "read AI prose verbatim.\n"
    "HALLUCINATIONS: AI can produce plausible but incorrect claims — "
    "read every slide critically.\n\n"
    "SUBMISSION CHECKLIST — before the final deck:\n"
    "- All figures confirmed against the Analytics page\n"
    "- All [[VERIFY]] markers resolved and removed\n"
    "- All speaker notes rewritten in the presenter's own voice\n"
    "- AI DRAFT footer removed from the final version\n"
    "- Academic Review run against the final deck\n\n"
    + _MOLLY_VERIFY_NOTE
)

# matplotlib hex equivalents of the theme — light mode for print/projection.
_MPL_INK = "#1A1A2E"
_MPL_GREY = "#4A4A6A"
_MPL_GRID = "#E2E8F0"
_MPL_ACCENT = "#1D4ED8"
_MPL_SERIES = ["#1D4ED8", "#059669", "#B45309", "#7C3AED", "#DB2777",
               "#0891B2", "#CA8A04", "#15803D", "#9333EA", "#374151"]


# ── Chart rendering (matplotlib, light mode) ──────────────────────────────────

def render_deck_charts(
    data: dict[str, Any], sensitivity: dict[str, Any] | None = None,
) -> dict[str, bytes | None]:
    """
    Renders every deck chart to a light-mode PNG. Returns a dict keyed by
    slide role — rolling_correlation, cumulative_returns, risk_return,
    sensitivity, team_activity — with PNG bytes or None when the chart
    cannot be drawn (matplotlib missing, or no source data).
    """
    charts: dict[str, bytes | None] = {
        "rolling_correlation": None, "cumulative_returns": None,
        "risk_return": None, "sensitivity": None, "team_activity": None,
    }
    try:
        import matplotlib
        matplotlib.use("Agg")  # headless — no display on the server
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        log.warning("deck_charts_matplotlib_unavailable", error=str(exc))
        return charts

    def _finish(fig) -> bytes:
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                    facecolor="white")
        plt.close(fig)
        return buf.getvalue()

    def _style(ax) -> None:
        ax.set_facecolor("white")
        ax.grid(True, color=_MPL_GRID, linewidth=0.7)
        ax.tick_params(colors=_MPL_GREY, labelsize=9)
        for spine in ax.spines.values():
            spine.set_color(_MPL_GRID)
        ax.title.set_color(_MPL_INK)
        ax.xaxis.label.set_color(_MPL_GREY)
        ax.yaxis.label.set_color(_MPL_GREY)

    # ── Rolling correlation — the 2022 regime break ───────────────────────
    try:
        rc = data.get("rolling_correlation") or {}
        pts = rc.get("points") or []
        if pts:
            import pandas as pd
            dates = [pd.to_datetime(p["date"]) for p in pts]
            fig, ax = plt.subplots(figsize=(8, 4.2))
            ax.plot(dates, [p.get("equity_ig") for p in pts],
                    color=_MPL_ACCENT, linewidth=1.6, label="Equity vs IG bonds")
            ax.plot(dates, [p.get("equity_hy") for p in pts],
                    color="#059669", linewidth=1.6, label="Equity vs HY bonds")
            ax.axhline(0, color=_MPL_GREY, linewidth=0.8)
            if rc.get("regime_break"):
                ax.axvline(pd.to_datetime(rc["regime_break"]), color=_AMBER_HEX,
                           linestyle="--", linewidth=1.4, label="2022 regime break")
            ax.set_title("Rolling 12-Month Equity–Bond Correlation", fontsize=11)
            ax.set_ylabel("Correlation")
            ax.legend(fontsize=8, frameon=False)
            _style(ax)
            charts["rolling_correlation"] = _finish(fig)
    except Exception as exc:  # noqa: BLE001
        log.warning("deck_chart_rolling_correlation_failed", error=str(exc))

    # ── Cumulative returns — growth of $1 ─────────────────────────────────
    try:
        cr = data.get("cumulative_returns") or {}
        pts = cr.get("points") or []
        strategies = cr.get("strategies") or []
        if pts and strategies:
            import pandas as pd
            dates = [pd.to_datetime(p["date"]) for p in pts]
            fig, ax = plt.subplots(figsize=(8, 4.2))
            for i, name in enumerate(strategies):
                ax.plot(dates, [p.get(name) for p in pts],
                        color=_MPL_SERIES[i % len(_MPL_SERIES)],
                        linewidth=1.3, label=name)
            ax.set_title("Cumulative Total Return — Growth of $1", fontsize=11)
            ax.set_ylabel("Growth of $1")
            ax.legend(fontsize=6.5, frameon=False, ncol=2)
            _style(ax)
            charts["cumulative_returns"] = _finish(fig)
    except Exception as exc:  # noqa: BLE001
        log.warning("deck_chart_cumulative_returns_failed", error=str(exc))

    # ── Risk-return scatter — strategy volatility vs CAGR ──────────────────
    try:
        results = data.get("strategy_results") or {}
        xs, ys, labels, best = [], [], [], None
        best_sharpe = float("-inf")
        for name, r in results.items():
            vol, cagr = r.get("volatility"), r.get("cagr")
            if isinstance(vol, (int, float)) and isinstance(cagr, (int, float)):
                xs.append(vol * 100)
                ys.append(cagr * 100)
                labels.append(r.get("strategy_name") or name)
                sh = r.get("sharpe_ratio")
                if isinstance(sh, (int, float)) and sh > best_sharpe:
                    best_sharpe, best = sh, len(xs) - 1
        if xs:
            fig, ax = plt.subplots(figsize=(8, 4.4))
            ax.scatter(xs, ys, color=_MPL_ACCENT, s=70, zorder=3)
            for i, lab in enumerate(labels):
                ax.annotate(lab, (xs[i], ys[i]), fontsize=6.5,
                            color=_MPL_GREY, xytext=(4, 4),
                            textcoords="offset points")
            if best is not None:
                ax.scatter([xs[best]], [ys[best]], color=_AMBER_HEX, s=130,
                           zorder=4, label="Highest Sharpe")
                ax.legend(fontsize=8, frameon=False)
            ax.set_title("Risk–Return Profile by Strategy", fontsize=11)
            ax.set_xlabel("Annualised volatility (%)")
            ax.set_ylabel("CAGR (%)")
            _style(ax)
            charts["risk_return"] = _finish(fig)
    except Exception as exc:  # noqa: BLE001
        log.warning("deck_chart_risk_return_failed", error=str(exc))

    # ── Sensitivity — parameter robustness ────────────────────────────────
    try:
        strat_sens = (sensitivity or {}).get("strategies") or []
        if strat_sens:
            fig, ax = plt.subplots(figsize=(8, 4.2))
            for i, s in enumerate(strat_sens[:4]):
                xs = [p.get("value") for p in s.get("points", [])]
                ys = [p.get("sharpe") for p in s.get("points", [])]
                if xs and ys:
                    ax.plot(xs, ys, marker="o", markersize=3,
                            color=_MPL_SERIES[i % len(_MPL_SERIES)],
                            linewidth=1.4, label=s.get("strategy", f"Strategy {i+1}"))
            ax.set_title("Parameter Sensitivity — Sharpe vs Key Parameter",
                         fontsize=11)
            ax.set_ylabel("Sharpe ratio")
            ax.legend(fontsize=7, frameon=False)
            _style(ax)
            charts["sensitivity"] = _finish(fig)
    except Exception as exc:  # noqa: BLE001
        log.warning("deck_chart_sensitivity_failed", error=str(exc))

    # ── Team activity — per-member contribution ───────────────────────────
    try:
        members = (data.get("team_summary") or {}).get("per_member") or []
        if members:
            names = [m.get("user_name") or m.get("user", "—") for m in members]
            councils = [m.get("council_interactions", 0) for m in members]
            reviews = [m.get("academic_review_sessions", 0) for m in members]
            views = [m.get("page_views", 0) for m in members]
            fig, ax = plt.subplots(figsize=(7.5, 4.0))
            import numpy as np
            x = np.arange(len(names))
            ax.bar(x - 0.25, councils, 0.25, color=_MPL_ACCENT, label="Council")
            ax.bar(x, reviews, 0.25, color="#059669", label="Academic reviews")
            ax.bar(x + 0.25, views, 0.25, color="#B45309", label="Page views")
            ax.set_xticks(x)
            ax.set_xticklabels(names, fontsize=8)
            ax.set_title("Team Platform Engagement", fontsize=11)
            ax.legend(fontsize=8, frameon=False)
            _style(ax)
            charts["team_activity"] = _finish(fig)
    except Exception as exc:  # noqa: BLE001
        log.warning("deck_chart_team_activity_failed", error=str(exc))

    return charts


# matplotlib needs a hex string for the amber accent.
_AMBER_HEX = "#B45309"


# ── Slide primitives ──────────────────────────────────────────────────────────

def _blank(prs: Presentation):
    """A fully blank slide — every element is drawn manually for theme control."""
    return prs.slides.add_slide(prs.slide_layouts[6])


def _bg(slide, color: RGBColor) -> None:
    """Fills the whole slide background with a solid colour."""
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, 0, 0, _SLIDE_W, _SLIDE_H)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    shape.shadow.inherit = False
    slide.shapes._spTree.remove(shape._element)
    slide.shapes._spTree.insert(2, shape._element)


def _textbox(slide, left, top, width, height, text, *, size=18, bold=False,
             color=_INK, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    """Adds a single-paragraph textbox."""
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    return box


def _title_bar(slide, text: str) -> None:
    """A navy band across the top of a content slide with the slide title."""
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, _SLIDE_W, Inches(1.0))
    bar.fill.solid()
    bar.fill.fore_color.rgb = _NAVY
    bar.line.fill.background()
    bar.shadow.inherit = False
    tf = bar.text_frame
    tf.margin_left = Inches(0.5)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = text
    run.font.size = Pt(26)
    run.font.bold = True
    run.font.color.rgb = _WHITE


def _bullets(slide, items: list[str], *, left=Inches(0.7), top=Inches(1.4),
             width=Inches(12.0), height=Inches(5.4), size=18) -> None:
    """A bulleted list. Items already prefixed with '- ' are de-prefixed."""
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    for i, item in enumerate(items):
        text = item.lstrip("-• ").strip()
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(10)
        bullet = p.add_run()
        bullet.text = "▪  "
        bullet.font.size = Pt(size)
        bullet.font.color.rgb = _ACCENT
        run = p.add_run()
        run.text = text
        run.font.size = Pt(size)
        run.font.color.rgb = _INK


def _image(slide, png: bytes | None, *, left, top, width, fallback="chart"):
    """Places a PNG, or a [DATA PENDING] note when the render was unavailable."""
    if png:
        slide.shapes.add_picture(io.BytesIO(png), left, top, width=width)
    else:
        _textbox(slide, left, top, width, Inches(1.0),
                 f"[DATA PENDING] — {fallback} chart unavailable. "
                 "Warm the analytics caches, then regenerate the deck.",
                 size=14, color=_AMBER, anchor=MSO_ANCHOR.MIDDLE)


def _callout(slide, left, top, width, height, heading, value, *,
             color=_ACCENT) -> None:
    """A small coloured callout box — a label over a large value."""
    box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                 left, top, width, height)
    box.fill.solid()
    box.fill.fore_color.rgb = color
    box.line.fill.background()
    box.shadow.inherit = False
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p1 = tf.paragraphs[0]
    p1.alignment = PP_ALIGN.CENTER
    r1 = p1.add_run()
    r1.text = heading
    r1.font.size = Pt(12)
    r1.font.color.rgb = _WHITE
    p2 = tf.add_paragraph()
    p2.alignment = PP_ALIGN.CENTER
    r2 = p2.add_run()
    r2.text = value
    r2.font.size = Pt(24)
    r2.font.bold = True
    r2.font.color.rgb = _WHITE


def _table(slide, headers: list[str], rows: list[list[str]], *,
           left=Inches(0.7), top=Inches(1.4), width=Inches(12.0),
           max_rows=11) -> None:
    """A light-styled native PowerPoint table. [DATA PENDING] when empty."""
    if not rows:
        _textbox(slide, left, top, width, Inches(1.0),
                 "[DATA PENDING] — table data unavailable. Warm the "
                 "analytics caches, then regenerate the deck.",
                 size=16, color=_AMBER)
        return
    rows = rows[:max_rows]
    n_rows, n_cols = len(rows) + 1, len(headers)
    height = Inches(0.4 * n_rows)
    tbl_shape = slide.shapes.add_table(n_rows, n_cols, left, top, width, height)
    table = tbl_shape.table
    for c, label in enumerate(headers):
        cell = table.cell(0, c)
        cell.fill.solid()
        cell.fill.fore_color.rgb = _NAVY
        para = cell.text_frame.paragraphs[0]
        run = para.add_run()
        run.text = label
        run.font.size = Pt(11)
        run.font.bold = True
        run.font.color.rgb = _WHITE
    for r, row in enumerate(rows, start=1):
        for c, value in enumerate(row):
            cell = table.cell(r, c)
            cell.fill.solid()
            cell.fill.fore_color.rgb = (
                _WHITE if r % 2 else RGBColor(0xF1, 0xF5, 0xF9))
            para = cell.text_frame.paragraphs[0]
            run = para.add_run()
            run.text = str(value)
            run.font.size = Pt(10)
            run.font.color.rgb = _INK


def _footer(slide, idx: int, total: int) -> None:
    """The AI DRAFT footer plus a slide counter, on every slide."""
    box = slide.shapes.add_textbox(Inches(0.4), Inches(7.05),
                                   Inches(12.5), Inches(0.35))
    p = box.text_frame.paragraphs[0]
    run = p.add_run()
    run.text = f"{_AI_DRAFT_FOOTER}   ·   Slide {idx}/{total}"
    run.font.size = Pt(8)
    run.font.italic = True
    run.font.color.rgb = _AMBER


# ── Deck builder ──────────────────────────────────────────────────────────────

_DATA_PENDING_BULLET = (
    "[DATA PENDING] — generated from live analytics; warm the caches "
    "and regenerate the deck.")


def _normalize_slides(raw_slides: Any) -> list[dict[str, Any]]:
    """Map the AI's slide list onto exactly DECK_SLIDE_COUNT slides indexed
    1..10, each with a title. A missing or invalid slide keeps its canonical
    title and a [DATA PENDING] body, so the deck always has all ten slides
    even when the generation JSON is absent (test env / LLM outage)."""
    by_num: dict[int, dict[str, Any]] = {}
    for s in (raw_slides or []):
        if not isinstance(s, dict):
            continue
        n = s.get("slide_number")
        if isinstance(n, int) and 1 <= n <= DECK_SLIDE_COUNT and n not in by_num:
            by_num[n] = s
    out: list[dict[str, Any]] = []
    for n in range(1, DECK_SLIDE_COUNT + 1):
        s = dict(by_num.get(n) or {})
        s["slide_number"] = n
        if not str(s.get("title") or "").strip():
            s["title"] = SLIDE_TITLES[n - 1]
        bullets = [str(b).strip() for b in (s.get("bullets") or [])
                   if str(b).strip()]
        s["bullets"] = bullets or [_DATA_PENDING_BULLET]
        out.append(s)
    return out


def _slide_table(sl: dict[str, Any]) -> tuple[list[str], list[list[str]]]:
    """Extract (headers, rows-of-strings) from a slide's table_data, or
    ([], []) when the slide carries no usable table."""
    td = sl.get("table_data")
    if not isinstance(td, dict):
        return [], []
    headers = [str(h) for h in (td.get("headers") or [])]
    rows = [[str(c) for c in r] for r in (td.get("rows") or [])
            if isinstance(r, (list, tuple))]
    return (headers, rows) if headers and rows else ([], [])


def _render_content_slide(prs, sl, chart_png, idx, total) -> None:
    """Lay out one content slide: title bar, bullets, optional table, optional
    chart. Adds exactly one slide. Slides in SLIDE_CHARTS always reserve a
    chart slot — a None PNG degrades to a [DATA PENDING] note. Fully guarded so
    a malformed slide never raises out of the builder."""
    s = _blank(prs)
    _bg(s, _WHITE)
    title = sl.get("title") or SLIDE_TITLES[idx - 1]
    try:
        _title_bar(s, title)
        bullets = sl.get("bullets") or [_DATA_PENDING_BULLET]
        headers, rows = _slide_table(sl)
        has_table = bool(headers and rows)
        has_chart = idx in SLIDE_CHARTS
        role = SLIDE_CHARTS.get(idx, "chart")

        if has_chart and has_table:
            _bullets(s, bullets, left=Inches(0.6), top=Inches(1.15),
                     width=Inches(12.1), height=Inches(1.55), size=13)
            _table(s, headers, rows, left=Inches(0.6), top=Inches(2.85),
                   width=Inches(6.1), max_rows=11)
            _image(s, chart_png, left=Inches(7.0), top=Inches(2.85),
                   width=Inches(5.8), fallback=role)
        elif has_chart:
            _bullets(s, bullets, left=Inches(0.6), top=Inches(1.4),
                     width=Inches(4.9), height=Inches(5.2), size=15)
            _image(s, chart_png, left=Inches(5.7), top=Inches(1.5),
                   width=Inches(7.1), fallback=role)
        elif has_table:
            _bullets(s, bullets, left=Inches(0.6), top=Inches(1.25),
                     width=Inches(12.1), height=Inches(1.95), size=14)
            _table(s, headers, rows, left=Inches(0.6), top=Inches(3.35),
                   width=Inches(12.1), max_rows=12)
        else:
            _bullets(s, bullets, left=Inches(0.7), top=Inches(1.7),
                     width=Inches(11.9), height=Inches(5.0), size=18)
    except Exception as exc:  # noqa: BLE001 — one bad slide never fails the deck
        log.warning("deck_slide_body_failed", slide=idx, error=str(exc))

    _footer(s, idx, total)


def build_presentation_deck(
    slides: Any,
    charts: dict[int, bytes | None] | None = None,
) -> bytes:
    """
    Lays out the 10-slide final presentation deck and returns the .pptx bytes.

    `slides` is the parsed AI JSON — a list of
    {slide_number, title, bullets, table_data, speaker_notes} dicts produced by
    deck_generation_prompt() through harness_narrative(). `charts` maps a slide
    number to its pre-rendered light-mode PNG (main renders them with the
    chart_render deck renderers, since that path is async — the sync builder
    only embeds the bytes).

    The builder always emits exactly ten slides with the canonical SLIDE_TITLES:
    a missing slide, a missing table, or a missing chart each degrade to a
    [DATA PENDING] note, never an exception. Every slide carries the AI DRAFT
    footer and a speaker-notes verification caveat; slide 1 additionally carries
    the review warning + submission checklist.
    """
    charts = charts or {}
    prs = Presentation()
    prs.slide_width = _SLIDE_W
    prs.slide_height = _SLIDE_H
    total = DECK_SLIDE_COUNT

    norm = _normalize_slides(slides)
    for i, sl in enumerate(norm):
        _render_content_slide(prs, sl, charts.get(i + 1), i + 1, total)

    # Speaker notes — the AI narrative, prefixed with the verification caveat.
    # Slide 1 carries the full review warning + submission checklist (CAVEAT
    # 1 + 5); every other slide carries the per-slide verify note (CAVEAT 4).
    # The caveat is always non-empty, so every slide has speaker notes.
    for i, slide in enumerate(prs.slides):
        ai_notes = str(norm[i].get("speaker_notes") or "").strip()
        caveat = _DECK_TITLE_NOTE if i == 0 else _MOLLY_VERIFY_NOTE
        text = caveat + (f"\n\n{ai_notes}" if ai_notes else "")
        try:
            slide.notes_slide.notes_text_frame.text = text
        except Exception:  # noqa: BLE001 — a notes failure never fails the deck
            pass

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ── Editor-content export ─────────────────────────────────────────────────────

# The editor canvas is a fixed 960x540 (16:9) space (migration 022). The
# exported deck uses the 10in x 5.625in 16:9 slide size, so the canvas
# maps onto the slide 1:1 — every element coordinate scales by a single
# EMU factor per axis, and the export matches the editor pixel-for-pixel.
_CANVAS_W = 960
_CANVAS_H = 540
_EDITOR_SLIDE_W = 9144000   # EMU — 10 in
_EDITOR_SLIDE_H = 5143500   # EMU — 5.625 in
# 960 canvas px span a 720 pt-wide slide — font sizes scale by 0.75.
_CANVAS_PT_FACTOR = 720.0 / _CANVAS_W


def _emu_x(x: float) -> int:
    """A canvas x-coordinate (or width), in px, to EMU."""
    return round(float(x) / _CANVAS_W * _EDITOR_SLIDE_W)


def _emu_y(y: float) -> int:
    """A canvas y-coordinate (or height), in px, to EMU."""
    return round(float(y) / _CANVAS_H * _EDITOR_SLIDE_H)


def _canvas_color(value: Any, default: RGBColor) -> RGBColor:
    """Parses a '#RRGGBB' canvas colour, falling back to `default`."""
    try:
        s = str(value or "").lstrip("#")
        if len(s) == 6:
            return RGBColor.from_string(s.upper())
    except Exception:  # noqa: BLE001 — a bad colour never fails the export
        pass
    return default


def _canvas_text(slide, el: dict[str, Any], left, top, width, height) -> None:
    """Places a canvas text element — its content, font size, weight,
    style and colour mapped onto a pptx textbox."""
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    size_pt = max(1, round(float(el.get("fontSize", 18)) * _CANVAS_PT_FACTOR))
    bold = str(el.get("fontWeight", "")) == "bold"
    italic = str(el.get("fontStyle", "")) == "italic"
    color = _canvas_color(el.get("color"), _INK)
    # Konva wraps a single string; explicit newlines become paragraphs.
    for i, line in enumerate(str(el.get("content") or "").split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = p.add_run()
        run.text = line
        run.font.size = Pt(size_pt)
        run.font.bold = bold
        run.font.italic = italic
        run.font.color.rgb = color


def build_editor_pptx(
    draft: dict[str, Any],
    chart_pngs: dict[str, bytes] | None = None,
) -> bytes:
    """
    Renders a presentation_deck editor draft to a .pptx straight from its
    canvas content_json (migration 022). Each slide's positioned text and
    chart elements are mapped onto a 10x5.625in 16:9 slide so the export
    matches the editor canvas 1:1.

    `chart_pngs` maps a chart element id to its server-rendered PNG — the
    caller renders them, since that path is async — and a missing PNG
    degrades to a [DATA PENDING] note rather than failing the export.

    The presenter's speaker notes are carried into each slide's notes,
    prefixed with the AI-draft verification reminder (the editor export
    is a faithful WYSIWYG render, so it carries no on-slide chrome).
    """
    from pptx import Presentation
    from pptx.util import Emu

    pngs = chart_pngs or {}
    prs = Presentation()
    prs.slide_width = Emu(_EDITOR_SLIDE_W)
    prs.slide_height = Emu(_EDITOR_SLIDE_H)

    content = draft.get("content_json") or {}
    slides = content.get("slides", []) if isinstance(content, dict) else []

    for sl in slides:
        if not isinstance(sl, dict):
            continue
        s = _blank(prs)
        # Slide background — the canvas background colour, full bleed.
        bg = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0,
                                Emu(_EDITOR_SLIDE_W), Emu(_EDITOR_SLIDE_H))
        bg.fill.solid()
        bg.fill.fore_color.rgb = _canvas_color(sl.get("background"), _WHITE)
        bg.line.fill.background()
        bg.shadow.inherit = False
        s.shapes._spTree.remove(bg._element)
        s.shapes._spTree.insert(2, bg._element)

        for el in (sl.get("elements") or []):
            if not isinstance(el, dict):
                continue
            left = Emu(_emu_x(el.get("x", 0)))
            top = Emu(_emu_y(el.get("y", 0)))
            width = Emu(_emu_x(el.get("width", 0)))
            height = Emu(_emu_y(el.get("height", 0)))
            if el.get("type") == "text":
                _canvas_text(s, el, left, top, width, height)
            elif el.get("type") == "chart":
                png = pngs.get(str(el.get("id")))
                if png:
                    s.shapes.add_picture(io.BytesIO(png), left, top,
                                         width=width, height=height)
                else:
                    _textbox(s, left, top, width, height,
                             "[DATA PENDING] — chart unavailable. Warm the "
                             "analytics caches, then re-export.",
                             size=12, color=_AMBER, anchor=MSO_ANCHOR.MIDDLE)

        # Speaker notes — the verify reminder, then the presenter's notes.
        try:
            notes = str(sl.get("speaker_notes") or "").strip()
            s.notes_slide.notes_text_frame.text = (
                _MOLLY_VERIFY_NOTE + ("\n\n" + notes if notes else ""))
        except Exception:  # noqa: BLE001 — a notes failure never fails export
            pass

    if not slides:
        s = _blank(prs)
        _textbox(s, Emu(_emu_x(120)), Emu(_emu_y(230)),
                 Emu(_emu_x(720)), Emu(_emu_y(80)),
                 "This deck draft has no slides yet.", size=18)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
