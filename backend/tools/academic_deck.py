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

# ── Validated production constants (June 22 2026 -- Path A live values) ──
# These are the locked submission figures threaded through every document
# generator (brief, appendix, deck, script). They backstop the AI prompts
# so the writer never invents a number, and they flow through the
# substitution layer (tools/numeric_substitution.build_substitution_table)
# so every {{TOKEN}} in the generated prose resolves deterministically.
#
# PATH A -- LIVE FULL-PERIOD VALUES FROM THE STRATEGY CACHE
# Locked to the strategy_results_cache at hash f2e87dec7dcabe71 (the
# canonical 287-monthly-observation freeze, Jul 2002 - May 2026).
# These match the appendix-table values so the brief and the appendix
# cite identical Sharpe figures. The earlier December 2025 lock values
# (0.8576 / 0.4341 / 0.1264 / -0.253) drifted from the live cache when
# the data window extended through May 2026; the May 31 2026 directive
# to "DO NOT update" was reversed June 22 2026 after the live-vs-locked
# disagreement between the brief narrative and the appendix tables was
# diagnosed as a submission integrity issue.
#
# DO NOT manually edit these to match an earlier dataset state without
# regenerating the brief + appendix + deck against the same cache --
# the three documents must always agree on the numbers they cite.
OOS_SHARPE_REGIME_CONDITIONAL = 0.6291  # REGIME_SWITCHING.sharpe_ratio
OOS_SHARPE_BENCHMARK = 0.5370          # BENCHMARK.sharpe_ratio
OOS_SHARPE_EQUAL_WEIGHT = 0.5728       # EQUAL_WEIGHT.sharpe_ratio
CORRELATION_PRE_2022 = -0.05           # avg of 12m rolling corr pre-2022
CORRELATION_POST_2022 = 0.57           # avg of 12m rolling corr post-2022
PLAY_BY_PLAY_EVENTS = 9                # rebalance_events.csv row count

# Max drawdown over the full study period (Jul 2002 - May 2026).
# BENCHMARK matches the cache exactly (rounds from -0.5256 to -0.526);
# REGIME_SWITCHING bumped from -0.253 to -0.2974 to match the live
# cache REGIME_SWITCHING.max_drawdown under Path A.
MAX_DRAWDOWN_BENCHMARK = -0.526
MAX_DRAWDOWN_REGIME_CONDITIONAL = -0.2974
PLAY_BY_PLAY_ADD_VALUE = 2

# June 22 2026 (Path A scope) -- locked submission figures threaded
# through validated_constants so the brief story plan resolver, the
# deck/appendix substitution tables, and the per-section LLM prompts
# all see them. Prompts that previously hardcoded the literal "40
# months" / "14%" / etc. have been replaced with {{OOS_WINDOW_MONTHS}}
# and {{OOS_WINDOW_PCT_OF_STUDY}} placeholders that resolve here.
OOS_WINDOW_MONTHS = 53                  # Feb 2022 - May 2026 inclusive
OOS_WINDOW_PCT_OF_STUDY = 18.5          # 53 / 287 ≈ 18.5% of full window
CURRENT_REGIME = "BULL"                 # live HMM read at submission lock
CURRENT_EQUITY_WEIGHT = 0.80            # BULL regime equity from
                                        # REGIME_WEIGHTS in
                                        # tools/backtester.py:888
                                        # (BULL = 80% eq / 20% IG)

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
    "Does Diversification Beat 100% Equity?",                 # 1
    "Static, Dynamic, or Benchmark?",                         # 2
    "Risk-Adjusted Outperformance: The Numbers",              # 3
    "The 2022 Break: Why Static Allocation Failed",           # 4
    "Capital Preservation in Bear Regimes",                   # 5
    "Does It Hold Up Out-of-Sample?",                         # 6
    "Macro Context: Why Now Is a BEAR Regime",                # 7
    "What the Model Gets Wrong",                              # 8
    "AnalyticsDesk: The Platform Behind the Analysis",        # 9
    "How We Used AI: What Worked and What Didn't",            # 10
    "The Answer: Yes, With Conditions",                       # 11
]
DECK_SLIDE_COUNT = len(SLIDE_TITLES)

# slide_number → chart role. The role string is resolved to a chart_render
# renderer + arguments in main._render_deck_slide_charts. Slides not listed
# carry no chart -- the slide body renders bullets + optional table only.
#
# Bridge #98/#100 (June 7 2026) -- eleven-slide rebuild. Chart slots map to
# the existing platform renderers where the shape matches:
#   slide 4 -- rolling correlation (the 2022 break)
#   slide 5 -- strategy comparison bars (cumulative returns proxy)
#   slide 11 -- efficient frontier with the live blend marker
# Other slides carry stat cards, tables, or pure prose -- the builder
# renders those from the slide body without a matplotlib pass.
SLIDE_CHARTS: dict[int, str] = {
    4: "rolling_correlation",
    5: "strategy_comparison_oos_sharpe",
    11: "efficient_frontier",
}


# ── Generation prompt (the per-slide framing) ─────────────────────────────────
# DECK_GENERATION_PROMPT is the preamble carrying the project framing + the
# audience + the story-arc seed. slide_generation_prompt() slices ONE slide's
# spec out of SLIDE_SPECIFICATIONS, prepends the preamble (with the story-arc
# seed parameterised for that slide), and asks for a single-object JSON
# response. Bridge #98 / #100 (June 7 2026) rebuilt the deck from 6 slides
# to 11 and switched generation from the academic-writer harness to a single
# direct Sonnet call per slide (no peer-discussant evaluator, no Gemini, no
# Opus arbiter -- those were leaking peer-review text into the slide output).
DECK_GENERATION_PROMPT = """\
You are generating slide content for an 18-20 minute final investment \
presentation to an academic panel for FNA 670 (Financial Strategies and \
Analytics) at the McColl School of Business, Queens University of Charlotte. \
The audience is Dr. Katerina Panttser and a graduate-level industry panel.

The central question of the project is: \
"Does diversification outperform 100% equity?" Every slide must contribute \
to answering that question with the investable conclusion front and center.

Midpoint feedback was explicit: (a) lead with the answer, not the methodology; \
(b) simplify the strategy set to three (Static / Dynamic / Benchmark) for the \
panel; (c) emphasise the economic story around regime-switching, not the HMM \
math; (d) keep the framing at the executive level; (e) discuss AI use \
explicitly. Forest Capital purchases individual stocks and bonds; ETF \
proxies in this analysis represent asset-class signals only.

The team is:
Bob Thao -- quantitative analysis, regime hypothesis, economic significance \
threshold.
Michael Ruurds -- platform engineering, architecture, OOS window design, \
asset scope decisions.
Molly Murdock -- presentation, validation framework, 9-event play-by-play \
scorecard, peer review.

Using ONLY the data provided in the context block, generate the slide \
content. Do not invent numbers. If a value is missing from context, write \
[DATA PENDING].

CRITICAL: All numeric values in your response must come exactly from the \
data context provided. Do not estimate, interpolate, round beyond two \
decimal places, or substitute figures from prior knowledge. Sharpe ratios, \
max drawdowns, CAGR figures, and correlation coefficients MUST be quoted \
from the data block verbatim or else replaced with [DATA PENDING]. Every \
numeric attribution is verified against the source cache by a post-\
generation audit; a Sharpe attributed to the wrong strategy or a drawdown \
that is not the cache's figure will fail the numeric cross-reference \
check and flag in the audit panel. Numeric accuracy is non-negotiable."""


# Story-arc seed: every per-slide prompt prepends this with the slide-N
# value substituted in. The seed tells the model where it is in the overall
# narrative -- critical for keeping slide-by-slide tone consistent without
# re-reading every other slide's content.
STORY_ARC_SEED = """\
This is slide {slide_number} of {total_slides} in the final FNA 670 \
investment presentation. The story arc is:
  1. The investment question and direct answer.
  2. Three strategies, one comparison (Static / Dynamic / Benchmark).
  3. Risk-adjusted outperformance -- the numbers.
  4. Why regime-switching works (the 2022 correlation break).
  5. Capital preservation in bear regimes.
  6. Out-of-sample validation (resolves the overfitting concern).
  7. Macro context -- why the live signal is BEAR right now.
  8. Limitations and honest assessment (the 3/9 play-by-play).
  9. Live platform demo setup.
  10. AI methodology -- what worked, what didn't.
  11. Final recommendation.

You are generating slide {slide_number}: "{slide_title}". Stay in arc \
position -- do not anticipate slides ahead or re-state slides already \
covered. The panel reads the deck in order."""

# The slide specifications. Constants are interpolated; performance numbers for
# the strategy tables are deliberately left for gather_document_data() to
# supply (the prompt instructs the model to pull them from context, never
# hardcode them). Strategy names are the real platform names.
SLIDE_SPECIFICATIONS = f"""\
SLIDE SPECIFICATIONS

Bridge #98/#100 (June 7 2026) -- eleven-slide rebuild. The deck opens
with the answer, lays evidence in slides 2-8, sets up the live demo on
slide 9, addresses AI use explicitly on slide 10, and closes with the
investable recommendation on slide 11.

Slide 1 -- Does Diversification Beat 100% Equity?
Message: The regime-conditional blend outperforms the 100% equity \
benchmark on a risk-adjusted basis in the out-of-sample period.
Required bullets (no more than 3, answer-first):
- Yes. The dynamic regime-conditional blend beats the 100% equity \
benchmark on out-of-sample Sharpe.
- OOS Sharpe of the blend: {{strategy_performance.regime_conditional.oos_sharpe}}. \
OOS Sharpe of the benchmark: {{strategy_performance.benchmark.oos_sharpe}}. \
Pull both numbers from context -- do NOT invent.
- Current regime context: state the live regime label from \
current_regime as a single word (BEAR / BULL / TRANSITION).
Required table: none. Single large stat card -- the OOS Sharpe of the \
blend, the benchmark Sharpe, and the percentage advantage.
Chart: none -- the answer is the data point.
Speaker notes: "Before we get to methodology, here is the answer. \
The regime-conditional blend outperforms 100% equity on risk-adjusted \
returns in the out-of-sample period. The rest of the deck is the evidence \
behind that answer." Time: ~1 minute.
Guardrails: No more than 3 data points. Do not mention HMM, strategy \
codes, or factor loadings on this slide. Visible answer in 5 seconds.

Slide 2 -- Static, Dynamic, or Benchmark?
Message: Simplify the strategy set to three categories for the panel. \
Static = Classic 60/40. Dynamic = Regime-conditional blend. Benchmark = \
100% S&P 500. Everything else is supporting evidence.
Required bullets:
- Static (Classic 60/40): a fixed 60% equity / 40% bond mix, rebalanced \
monthly. The traditional diversification answer.
- Dynamic (Regime-Conditional Blend): allocation shifts with the live \
regime read -- the platform's recommendation.
- Benchmark (100% S&P 500): the question's baseline -- does \
diversification beat this?
Required table:
Headers: Strategy | Description | IS Sharpe | OOS Sharpe
Rows: Three rows in this exact order: Dynamic Blend, Classic 60/40, \
100% Equity Benchmark. Pull Sharpe figures from context.summary_statistics \
and context.strategy_performance. Use plain English strategy names on \
this slide.
Chart: none -- the three-column comparison is the visual.
Speaker notes: "We tested ten strategies. For this panel we simplify to \
three categories: the static answer, the dynamic answer, and the \
benchmark. Everything else is supporting evidence and lives in the \
analytical appendix." Time: ~1.5 minutes.
Guardrails: Do NOT list all 10 strategies. Do NOT use codes \
(MIN_VARIANCE, VOL_TARGETING). Use plain English on this slide only.

Slide 3 -- Risk-Adjusted Outperformance: The Numbers
Message: The dynamic strategy beats the benchmark on every risk-adjusted \
metric in the out-of-sample period. Static diversification also \
outperforms but by less.
Required bullets:
- Out-of-sample period: {{OOS_WINDOW_MONTHS}}+ months post-2022 correlation break. The \
window the hypothesis addresses.
- Dynamic Blend wins on every risk-adjusted metric vs the benchmark.
- Classic 60/40 also outperforms the benchmark, but the dynamic edge is \
larger.
Required table:
Headers: Strategy | OOS Sharpe | Max Drawdown | Volatility | Total Return
Rows: Dynamic Blend, Classic 60/40, 100% Equity Benchmark. Pull every \
figure from context.strategy_performance. Color-code in the bullets: \
green = dynamic beats benchmark, amber = 60/40 beats benchmark.
Footnote: "Figures based on December 2025 data lock. Academic submission \
figures."
Chart: none -- the performance table IS the slide.
Speaker notes: "Out-of-sample numbers across our three categories. The \
dynamic blend leads on Sharpe and drawdown. Classic 60/40 helps but the \
edge is smaller. Benchmark is third on every risk-adjusted metric." \
Time: ~2 minutes.
Guardrails: OOS period only. Do NOT mix IS and OOS in the same table.

Slide 4 -- The 2022 Break: Why Static Allocation Failed
Message: Pre-2022 equities and bonds were negatively correlated -- \
diversification was a free hedge. Post-2022 the correlation flipped to \
+0.68. Regime-conditional allocation adapts; static does not.
Required bullets:
- Equity-bond correlation pre-2022: {CORRELATION_PRE_2022:+.2f} \
(negative -- bonds hedged equity falls).
- Post-2022: {CORRELATION_POST_2022:+.2f} (structural inversion). Bonds \
and equities now fall together.
- Static 60/40 was designed for the pre-2022 regime. It cannot adapt to \
the new correlation environment.
- Dynamic regime-conditional allocation shifts when the regime shifts.
Required table: none -- the chart carries the slide.
Chart: rolling_correlation -- twelve-month equity-bond correlation \
2002-2026, vertical line at Jan 2022, annotation showing the +0.68 \
post-2022 figure. Right-panel bar chart NOT required in the JSON; the \
builder renders the canonical rolling-correlation matplotlib output.
Speaker notes: "Pre-2022 the equity-bond correlation was {CORRELATION_PRE_2022:+.2f}. \
That negative correlation was the diversification answer. In 2022 it \
flipped to {CORRELATION_POST_2022:+.2f}. The static 60/40 answer was \
designed for the old regime. The dynamic blend adapts." Time: ~2 minutes.
Guardrails: Causal story, not just numbers. No HMM technical detail. \
Just the economic intuition: correlation broke, static suffered, regime \
detection adapted.

Slide 5 -- Capital Preservation in Bear Regimes
Message: The platform's edge is capital preservation when it matters most, \
not bull-market outperformance.
Required bullets:
- The blend's drawdown profile is meaningfully better in bear regimes; \
the benchmark and 60/40 are both punished in 2008-2009 and 2022.
- Max drawdown: blend vs benchmark vs 60/40 -- pull from context.drawdown_comparison.
- Honest caveat: the council MISSED the April 2025 Liberation Day \
V-shaped recovery. Capital preservation is the edge, not crisis \
prediction.
Required table:
Headers: Strategy | Max Drawdown | 2008-09 DD | 2022 DD
Rows: Dynamic Blend, Classic 60/40, 100% Equity Benchmark. Pull figures \
from context.drawdown_comparison. If a value is missing, write \
[DATA PENDING] in that cell.
Chart: strategy_comparison_oos_sharpe -- cumulative-returns proxy, \
three lines, 2008-09 and 2022 bear regions shaded.
Speaker notes: "Where does the dynamic blend earn its Sharpe? In bear \
regimes. The blend cuts the worst drawdown roughly in half versus the \
benchmark. Liberation Day in April 2025 was a miss for us -- we did not \
call the V-shaped recovery. Our edge is sustained directional regimes, \
not sharp reversals." Time: ~2 minutes.
Guardrails: Be honest about the April 2025 miss. Cumulative returns \
chart starts at $1.00 in 2002.

Slide 6 -- Does It Hold Up Out-of-Sample?
Message: Strategy was designed on pre-2022 data and tested on \
{{OOS_WINDOW_MONTHS}}+ months \
of post-2022 data it never saw. It beats the benchmark OOS -- this \
addresses the overfitting concern directly.
Required bullets:
- Design window (in-sample): 2002 through 2021-end. The strategy was \
calibrated on this period.
- Test window (out-of-sample): January 2022 through the May 2026 \
data lock -- ~{{OOS_WINDOW_MONTHS}} months the strategy never saw.
- Dynamic Blend OOS Sharpe: {{strategy_performance.regime_conditional.oos_sharpe}}.
- Benchmark OOS Sharpe: {{strategy_performance.benchmark.oos_sharpe}}.
- OOS performance is genuine -- the strategy parameters were NOT tuned \
on post-2022 data.
Required table:
Headers: Strategy | IS Sharpe (pre-2022) | OOS Sharpe (post-2022)
Rows: Dynamic Blend, Classic 60/40, 100% Equity Benchmark. Pull both \
columns from context.strategy_performance.
Chart: none -- the comparison table IS the slide.
Speaker notes: "The overfitting concern: did we tune on the same data we \
report on? No. Pre-2022 is the design window. Post-2022 is \
{{OOS_WINDOW_MONTHS}}+ months of \
genuine out-of-sample. The dynamic blend beats the benchmark on both. \
The static 60/40 wins IS, ties OOS." Time: ~2 minutes.
Guardrails: Address overfitting head-on. Panel WILL ask.

Slide 7 -- Macro Context: Why Now Is a BEAR Regime
Message: Connect the live platform regime signal to real macroeconomic \
conditions. The current BEAR signal reflects specific observable factors. \
Not a black box.
Required bullets:
- Live regime: {{current_regime}} ({{regime_confidence}}). The signal \
updates as macro conditions shift.
- VIX, 10Y-2Y spread, HY credit spread, equity trend -- five watch-tiles \
showing what the live signal is reading right now.
- This is the only slide with live data; everything else is the locked \
academic figures from December 2025.
Required table:
Headers: Watchpoint | Current | Direction | Threshold
Rows: five watch-tiles -- pull from context.live_regime_signals. If the \
context lacks a watch-tile, write [DATA PENDING] in that row.
Chart: none -- the watchpoint tile grid carries the slide.
Speaker notes: "The live regime read updates as macro conditions move. \
Here is what the platform is reading right now. {{current_regime}} -- \
{{regime_confidence}}. This is the only slide in the deck with live data; \
the rest are the December 2025 academic-submission figures." \
Time: ~2 minutes.
Guardrails: This slide is FOR DISCUSSION ONLY. Add the label: \
"Live signal as of [generation date] -- for discussion, not academic \
submission figures."

Slide 8 -- What the Model Gets Wrong
Message: Intellectual honesty is a strength. Edge is capital preservation \
in sustained bear regimes. NOT designed to call sharp V-shaped reversals. \
Acknowledge explicitly.
Required bullets:
- We tested the council against {PLAY_BY_PLAY_EVENTS} named market events \
committed to the database BEFORE the council was asked about them.
- Honest result: value added in {PLAY_BY_PLAY_ADD_VALUE} of \
{PLAY_BY_PLAY_EVENTS} events.
- Misses include April 2025 Liberation Day (V-shaped recovery -- \
council was risk-off, missed the bounce).
- The regime filter is optimised for SUSTAINED directional regimes, not \
short-duration reversals.
Required table:
Headers: Event | Date | Council Signal | Outcome
Rows: every event in context.play_by_play_events. One row per event. \
Event names only, no quantitative outcome scores -- the slide is a \
scorecard not a P&L attribution.
Chart: none -- the table IS the scorecard.
Speaker notes: "{PLAY_BY_PLAY_ADD_VALUE} of {PLAY_BY_PLAY_EVENTS}. We \
are not selling crisis prediction. We are selling allocation discipline \
that compounds. The model's edge is sustained bear regimes -- not sharp \
reversals like April 2025 Liberation Day." Time: ~1.5 minutes.
Guardrails: Do NOT spin the misses. {PLAY_BY_PLAY_ADD_VALUE}/{PLAY_BY_PLAY_EVENTS} \
honest framing is academically stronger than cherry-picking wins.

Slide 9 -- AnalyticsDesk: The Platform Behind the Analysis
Message: Transition slide before the live demo. Set up what the panel is \
about to see. Three things: live regime + CIO recommendation, council \
output with dissenting view, document generation.
Required bullets:
- Live regime detection + CIO recommendation (Investment Outlook page).
- Council output -- five agents, generator-evaluator harness, dissenting \
view from the Risk Manager.
- Document generation -- this deck, the executive brief, the analytical \
appendix all from the same data layer.
- URL for the panel: analyticsdesk.app.
Required table: none.
Chart: none -- this is a transition slide. The live browser demo follows.
Speaker notes: "The platform is the analytical engine behind everything \
you have seen so far. Three things to show in the live demo: regime \
detection, the council with its dissenting view, and document \
generation. URL is analyticsdesk.app." Time: 30 seconds (setup only -- \
the demo itself happens on the live site).
Guardrails: Setup slide. NO data tables. Brief. The demo is the live \
browser pivot.

Slide 10 -- How We Used AI: What Worked and What Didn't
Message: Rubric explicitly requires discussion of AI use. Be direct. \
The generator-evaluator council with dissenting agents is the platform \
differentiator. Honest about what failed.
Required bullets (two columns -- what worked + what we learned):
- What worked: multi-model validation (Sonnet + Opus + Gemini + Grok), \
regime-keyed caching, harness evaluation with the dissenting Risk Manager.
- What worked: deterministic Python recomputation of every reported \
number -- the LLMs draft prose, Python computes the numbers.
- What worked: documentation generation -- this deck, the brief, the \
appendix all from the same data layer.
- What we learned: LLM arithmetic is unreliable -- replaced every \
numerical computation with deterministic Python.
- What we learned: early prompts produced sycophantic outputs -- the \
dissenting Risk Manager agent specifically counters this.
- What we learned: the council is the analytical engine, not just a \
writing tool.
Required table: none -- two-column bullets carry the slide.
Chart: none.
Speaker notes: "The rubric asks about AI use. We used AI critically, \
not blindly. What worked: multi-model validation, deterministic \
recomputation, dissenting agents that argue back. What we learned: LLM \
arithmetic is unreliable, early prompts were sycophantic, and the \
council is the analytical engine -- not just a writing helper." \
Time: ~2 minutes.
Guardrails: Honest and reflective, not promotional. Do NOT list every \
AI model used. The callout: "Every number in this presentation was \
verified by deterministic Python recomputation, not LLM arithmetic."

Slide 11 -- The Answer: Yes, With Conditions
Message: Return to the central question with a direct investable answer. \
Yes diversification outperforms 100% equity -- but only when allocation \
is regime-conditional. Static helps but is insufficient post-2022. \
Recommendation for Forest Capital is the current BEAR regime blend.
Required bullets (no more than 4 -- the conclusion is the slide):
- Question restated: "Does diversification outperform 100% equity?"
- Answer: Yes -- regime-conditional diversification outperforms; static \
helps but is insufficient post-2022.
- Current recommended blend (live, refreshes on regeneration): pull \
{{blend_weights}} from context. Express as implied asset allocation \
(equities X%, bonds Y%) using compute_implied_asset_allocation on the \
weights.
- These are asset-class signals -- Forest Capital purchases individual \
stocks and bonds; ETF proxies represent asset-class direction only.
Required table:
Headers: Asset Class | Allocation
Rows: Equities (compute from blend_weights), Bonds (compute), Cash \
residual if any. Two-decimal percentage values.
Chart: efficient_frontier with the live blend point marked.
Speaker notes: "The answer to the panel's question: yes, diversification \
outperforms 100% equity -- when the allocation is regime-conditional. \
Static 60/40 helps but is not sufficient post-2022. The current BEAR \
regime recommendation is {{blend_weights}}, which works out to roughly \
[implied asset allocation]. Forest Capital purchases individual stocks \
and bonds -- these are asset-class signals." Time: 1 minute (close).
Guardrails: End on the investable conclusion, NOT on limitations or \
caveats. Final image the panel sees should be the answer to the \
question. Academic disclaimer present but visually subordinate.

"""


def deck_generation_prompt() -> str:
    """The full generation task text — preamble plus slide specifications —
    handed to harness_narrative() as the deck-generation instruction."""
    return f"{DECK_GENERATION_PROMPT}\n\n{SLIDE_SPECIFICATIONS}"


def _slice_slide_spec(slide_number: int) -> str:
    """Returns the slide-N section out of SLIDE_SPECIFICATIONS.

    SLIDE_SPECIFICATIONS is a single block of text with "Slide N --"
    headers. We split on those headers and return the requested slide's
    body. Bridge #95 -- per-slide generation needs ONE slide's spec at
    a time so the LLM doesn't have to hold all six in working memory
    AND so each call's output fits comfortably under max_tokens.
    """
    if not (1 <= slide_number <= DECK_SLIDE_COUNT):
        raise ValueError(
            f"slide_number must be 1..{DECK_SLIDE_COUNT}; got {slide_number}")
    text = SLIDE_SPECIFICATIONS
    marker = f"Slide {slide_number} --"
    start = text.find(marker)
    if start == -1:
        raise ValueError(
            f"Slide {slide_number} spec missing from SLIDE_SPECIFICATIONS")
    # The next slide's marker terminates this slide's body. The last
    # slide runs to end-of-string.
    if slide_number < DECK_SLIDE_COUNT:
        end = text.find(f"Slide {slide_number + 1} --", start)
        if end == -1:
            end = len(text)
    else:
        end = len(text)
    return text[start:end].rstrip()


def slide_generation_prompt(slide_number: int) -> str:
    """Bridge #98 / #100 -- per-slide generation prompt. Returns a prompt
    that asks the model to emit a SINGLE slide's JSON object for slide
    {slide_number}. The prompt is sent directly to Sonnet (no harness,
    no evaluator, no Gemini, no Opus arbiter -- those were leaking
    peer-review text into the slide output via the academic-review
    evaluator's feedback retry loop).

    Structure:
      1. DECK_GENERATION_PROMPT preamble (project framing, audience,
         midpoint feedback, team roles).
      2. STORY_ARC_SEED with slide_number / total_slides / slide_title
         substituted. Tells the model where it sits in the 11-slide arc.
      3. The slide's spec block from SLIDE_SPECIFICATIONS.
      4. Explicit single-object JSON contract -- no wrapped list, no
         markdown fences, no preamble.

    Token budget: with one slide's content the response stays at
    ~500-1200 tokens, comfortably under a 2000 max_tokens cap.
    """
    spec = _slice_slide_spec(slide_number)
    title = SLIDE_TITLES[slide_number - 1]
    arc_seed = STORY_ARC_SEED.format(
        slide_number=slide_number,
        total_slides=DECK_SLIDE_COUNT,
        slide_title=title)
    return (
        f"{DECK_GENERATION_PROMPT}\n\n"
        f"{arc_seed}\n\n"
        f"SLIDE SPEC:\n{spec}\n\n"
        f"Output ONLY a JSON object with these keys (no preamble, no "
        f"markdown fences, no wrapping list, no commentary):\n"
        f'{{\n'
        f'  "slide_number": {slide_number},\n'
        f'  "title": "{title}",\n'
        f'  "bullets": ["...", "..."],\n'
        f'  "table_data": null or {{"headers": [...], "rows": [[...], ...]}},\n'
        f'  "speaker_notes": "..."\n'
        f'}}'
    )


# LLM preamble patterns that should never appear in a slide bullet.
# When the model emits a short conversational opener before the JSON
# (which the JSON-extraction step strips) the same conversational tone
# sometimes bleeds into a bullet -- "I cannot generate the requested
# slide", "Note: ...", "Sorry, ...". A short prefix scan after parsing
# replaces those with a clearly-flagged regen marker so the deck never
# carries raw apology text to the audience.
_BULLET_PREAMBLE_PATTERNS = (
    "i ", "i'm ", "i am ", "note:", "sorry", "as an ai",
    "i apologize", "i cannot", "i can't", "unfortunately",
)
_BULLET_PREAMBLE_REPLACEMENT = (
    "[Content generation error -- regenerate deck]")


def _bullet_looks_like_preamble(bullet: str) -> bool:
    """A bullet that opens with a known LLM preamble pattern is the
    model talking ABOUT the slide rather than producing slide content.
    Case-insensitive prefix check; the leading bullet glyph (if any)
    is stripped before the comparison."""
    text = (bullet or "").lstrip("-•▪ \t").lower()
    return any(text.startswith(p) for p in _BULLET_PREAMBLE_PATTERNS)


def _scrub_bullet_preambles(obj: dict) -> dict:
    """Replace any bullet that opens with a known LLM preamble pattern
    with the regen marker. Logs each substitution so a deck with a
    polluted prose surface is visible in Render logs without having to
    open the .pptx.
    """
    bullets = obj.get("bullets")
    if not isinstance(bullets, list):
        return obj
    cleaned: list[str] = []
    for b in bullets:
        if isinstance(b, str) and _bullet_looks_like_preamble(b):
            log.warning(
                "deck_bullet_preamble_replaced",
                bullet_preview=str(b)[:120])
            cleaned.append(_BULLET_PREAMBLE_REPLACEMENT)
        else:
            cleaned.append(b if isinstance(b, str) else str(b))
    obj["bullets"] = cleaned
    return obj


def parse_single_slide_json(raw: str) -> dict | None:
    """Bridge #95 -- parse a per-slide LLM response into a single slide
    dict, or None on any failure (the caller writes a [DATA PENDING]
    placeholder for that slide). Mirrors _parse_deck_slides's tolerance
    of markdown fences and leading/trailing prose, but pulls the
    SINGLE object instead of the wrapped list.

    Hardened June 18 2026:
      * any preamble text before the first '{' is discarded entirely
        before parsing (the JSON-object extraction was lenient enough
        that a stray "Here is the slide:" prefix could survive when
        paired with an unusually-shaped body),
      * on parse failure we log the raw response truncated to 500
        chars at WARNING level so a regression is diagnosable without
        having to reproduce locally,
      * post-parse we scrub any bullet that opens with an LLM
        apology / preamble pattern -- those are the model talking
        ABOUT the slide rather than producing slide content and they
        otherwise leak verbatim into the final .pptx.
    """
    import json
    text = (raw or "").strip()
    if "{" not in text:
        return None
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    # Discard any preamble BEFORE the first opening brace. This is
    # belt-and-braces -- the slice-by-find below already trims it
    # implicitly -- but doing it explicitly here makes the intent
    # visible at the entry point.
    first_brace = text.find("{")
    if first_brace > 0:
        text = text[first_brace:]
    try:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end < start:
            log.warning(
                "deck_slide_parse_no_object_braces",
                raw_preview=(raw or "")[:500])
            return None
        obj = json.loads(text[start:end + 1])
        if not isinstance(obj, dict):
            log.warning(
                "deck_slide_parse_non_object",
                raw_preview=(raw or "")[:500])
            return None
        return _scrub_bullet_preambles(obj)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "deck_slide_parse_failed",
            error=str(exc),
            raw_preview=(raw or "")[:500])
        return None


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
    """Places a PNG, or a clearly-marked unavailable note when the
    chart renderer returned None.

    Hardened June 18 2026: every silent None now emits a WARNING log
    naming the chart slot, so a deck that came back with a missing
    chart is diagnosable from Render logs alone (previously the
    [DATA PENDING] text was the ONLY signal, requiring the operator
    to open the .pptx to know a chart had been skipped). The
    placeholder text was also reworded to name the chart slot
    explicitly -- "[Chart unavailable: rolling_correlation -- ..."
    is more actionable than the old "[DATA PENDING] -- chart
    unavailable" line which gave no slot context.
    """
    if png:
        slide.shapes.add_picture(io.BytesIO(png), left, top, width=width)
        return
    log.warning("deck_chart_slot_unavailable", chart=str(fallback))
    _textbox(slide, left, top, width, Inches(1.0),
             f"[Chart unavailable: {fallback} -- ensure caches are warm "
             "before generating the deck]",
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
