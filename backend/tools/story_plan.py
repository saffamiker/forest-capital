"""tools/story_plan.py -- the locked story arc that backs the deck and brief.

Each slide of the presentation deck and each section of the executive
brief is rendered today by an independent LLM call. The June 18 deck
audit panel surfaced what that independence costs: 48 numeric flags
and 7 cross-section consistency flags where the same metric appeared
at different values across slides because each slide's LLM call made
its own substitution from prior knowledge instead of the locked cache.

The fix is structural. This module generates a STORY PLAN once per
data_hash: a structured outline with locked numeric anchors per slide
(deck) or per section (brief), a presenter script (deck), Grok's
anticipated committee questions, and Gemini's honest dissent. The
per-slide / per-section rendering pass then reads the locked anchors
and writes prose around them; it never substitutes its own figures.

Architecture mirrors tools/cio_recommendation.py (PR #324):

  * The expensive multi-pass generation runs ONCE per data_hash,
    persisted to story_plans (migration 056) and served from cache
    on every subsequent read until the underlying market data ticks
    over and a new hash is computed.
  * The persistence is a GUARDED UPSERT: a real LLM plan overwrites
    a previously stored deterministic_fallback row at the same
    (data_hash, document_type); a fallback never overwrites a real
    plan. The reverse-of-PR-324 contract.
  * Every LLM call is fail-open: an exception logs and falls through
    to a deterministic structured plan so the deck and brief always
    have something to render.

Four-pass pipeline (per document type):

  PASS 1  Opus arbiter (claude-opus-4-7) wrapped in
          GeneratorEvaluatorHarness with a story-plan-specific
          evaluator rubric. The harness retries up to 2 times if the
          plan scores below 7.0 against the rubric. Generates the
          slide_plan / section_plan + central_argument.

  PASS 2  Opus arbiter, direct call. Generates the full word-for-
          word presenter script (deck only). Conditioned on Pass 1
          output so the script and the plan never diverge.

  PASS 3  Grok contrarian (xAI). Generates anticipated_questions --
          the hardest committee questions the team will face,
          grounded in known weak points (53-month sample, 2-of-9
          play-by-play, Liberation Day miss).

  PASS 4  Gemini independent (Google). Generates dissenting_view +
          limitations_to_surface -- the blind-spot pass that names
          what the plan is NOT saying that it should be.

Passes 3 and 4 are INDEPENDENT of Pass 1 / Pass 2 -- a failure in
either does not block the slide_plan or full_script from being
persisted. The deck / brief consumer reads whatever is present.

Caching:
  get_cached_story_plan(data_hash, document_type) reads the most
  recent row for (data_hash, document_type) and returns it as a
  dict. None on cold cache or DB unavailability.
"""
from __future__ import annotations

import json
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_DB_AVAILABLE = False
try:  # pragma: no cover -- environment dependent
    from database import AsyncSessionLocal
    _DB_AVAILABLE = AsyncSessionLocal is not None
except Exception:  # noqa: BLE001
    AsyncSessionLocal = None  # type: ignore[assignment]

_DETERMINISTIC = "deterministic_fallback"


# ── Shared framing -- midpoint-feedback constraints (June 19 2026) ──────
#
# Three constants threaded into every Pass-1 system prompt below so the
# brief, deck, and script all open from the same authoritative frame.
# Pinned by tests so a future refactor cannot drop any one of them.
# The midpoint panel's directive (Dr. Panttser): too academic, lacking
# an investable conclusion, the central question is buried, the
# strategy set is too complex for a senior-audience presentation. The
# constants below address each criticism structurally.

THREE_STRATEGY_FRAME = """\
PRESENTATION FRAME: All outputs use a three-strategy lens regardless \
of how many strategies exist in the analytical cache:
  - Benchmark: S&P 500 (100% equity, no diversification)
  - Static blend: Classic 60/40 (fixed allocation)
  - Dynamic blend: regime-conditional (HMM-driven)
The 10-strategy analytical engine is appendix material. Presentation, \
brief, and script communicate through these three strategies only."""


CENTRAL_QUESTION_AND_ANSWER = """\
CENTRAL QUESTION + ANSWER (front-load in every deliverable):
The central question: does diversification improve risk-adjusted \
performance vs 100% equity?

PRIMARY PROOF POINT (the answer -- lead with this):
YES. OOS Sharpe 0.86 (blend) vs 0.43 (benchmark) over 53 months of \
genuinely unseen post-2022 data -- a 98% improvement in risk-adjusted \
return on data the model never trained on. These are the December \
2025 academic submission lock figures (the panel-defense record). \
The platform's Performance Record carries a matched Live Figure row \
(1.24 vs 0.73, +70%) but the brief / deck / appendix use the \
conservative submission values; the academic record stands.

SECONDARY REINFORCEMENT (capital preservation):
The blend reduced peak drawdown from -52.6% (benchmark) to -29.7% and \
recovered in 32 trading-day months vs 71 for the benchmark. The risk-\
adjusted advantage is not paper gain -- it is measurable capital \
preservation through equity bear regimes.

HONEST LIMITATION (do not bury -- foreground in brief section 3 and \
deck slide 8):
The council added value in 2 of 9 named market events (play-by-play \
scorecard). The edge is capital preservation in sustained bear \
regimes, NOT crisis prediction or market timing. The 2025-04 \
"Liberation Day" reversal is a documented miss -- surface it rather \
than hiding it.

STORY ARC HIERARCHY (apply to every deliverable):
  1. Lead with the OOS proof point. 0.86 vs 0.43 over 53 months of \
     unseen data is THE answer to the research question. It belongs \
     in the first sentence of the brief executive summary and on \
     slide 1 of the deck. NOT the full-period 0.63 vs 0.54 from the \
     live cache.
  2. Reinforce with drawdown numbers. -29.7% vs -52.6% peak loss, \
     32 vs 71 trading-day months recovery.
  3. Disclose limitation honestly. 2 of 9 play-by-play value-add; \
     capital preservation not market timing.
  4. Close with the investment conclusion. Regime-conditional \
     construction, retain bond sleeve, monitor monthly.

TOKEN MAPPING (be explicit about which token carries which figure):
  - {{OOS_SHARPE_BLEND}}        -> "0.86"  (December 2025 lock)
  - {{OOS_SHARPE_BENCHMARK}}    -> "0.43"  (December 2025 lock)
  - {{REGIME_SWITCHING_SHARPE}} -> "0.63"  (live full-period; ONLY in
                                            per-strategy tables,
                                            NEVER in the OOS headline)
  - {{BENCHMARK_SHARPE}}        -> "0.54"  (live full-period;
                                            same restriction)
  Use OOS tokens for the headline; use per-strategy tokens in the \
  appendix Section B comparison table only.

TOKEN MIXING PROHIBITION (non-negotiable):
  NEVER place {{OOS_SHARPE_BLEND}} (0.86) and \
  {{REGIME_SWITCHING_SHARPE}} (0.63) in the same sentence, bullet, or \
  table row without a clear label distinguishing OOS from full-\
  period. A reader seeing "0.86 vs 0.63" without labels cannot tell \
  which is the headline figure. Same applies to \
  {{OOS_SHARPE_BENCHMARK}} (0.43) and {{BENCHMARK_SHARPE}} (0.54). \
  In the brief: OOS figures belong in section 1, the section 3 \
  lead, and section 5. Full-period figures belong in section 3 \
  supporting context and the section 6 visuals table only. In the \
  deck: OOS figures on slides 1, 4, 7, 12 (the OOS headline, the \
  risk-adjusted numbers slide, the OOS validation slide, and the \
  closing answer). Full-period figures in the per-strategy \
  comparison table on slide 3 only, clearly labeled \
  "Full-Period Sharpe"."""


INVESTABLE_CONCLUSION_GUARD = """\
INVESTABLE CONCLUSION (non-negotiable):
The audience includes Forest Capital representatives who need an \
investable conclusion, not a literature review. Lead with the \
recommendation in the language of a CIO memo, not an academic paper. \
Do not use language like "future research suggests" or "further study \
would benefit from" -- these are academic hedges that undermine an \
investable conclusion. The final recommendations section must be \
written as an investment committee conclusion. Close with one \
sentence on the conditions under which the recommendation would be \
revisited (e.g. sustained ESS below the reliable threshold, or data \
hash change indicating structural market shift)."""


STATIC_ALLOCATION_JUSTIFICATION = """\
STATIC ALLOCATION REQUIREMENT (Part I rubric):
The static blend (Classic 60/40) is a first-class deliverable in its \
own right, not merely a comparison point for the dynamic blend. The \
brief and deck must:
  1. State the proposed fixed allocation: 60% S&P 500, 40% \
investment-grade bonds (Classic 60/40).
  2. Justify it using Markowitz (1952) mean-variance theory: the \
bond allocation reduces portfolio variance through imperfect \
correlation with equities, improving the risk-return tradeoff even \
if expected return is lower than 100% equity.
  3. Cite the historical case: the 60/40 portfolio has been the \
institutional standard for decades because equity-bond correlation \
was reliably negative during 2000-2020, providing drawdown \
protection.
  4. Acknowledge the 2022 limitation honestly: rising rates caused \
positive equity-bond correlation, which is why the static blend \
underperformed in 2022 and why the dynamic extension adds value.
The static blend earns its own evaluation on Part I metrics (total \
return, Sharpe, max drawdown, excess return vs benchmark) before the \
dynamic comparison."""


# Verified primary-source citations for the seven academic references
# the brief is required to ground its methodology section in. Sourced
# from the original journals + cross-checked against the publisher
# DOI registry; hardcoded rather than resolved at generation time so
# the brief LLM does not have to web-search and cannot drift to
# similar-but-different citations. The brief Pass-1 prompt instructs
# the model to USE THESE EXACTLY AS PROVIDED.
VERIFIED_CITATIONS = {
    "hamilton_1989": (
        "Hamilton, J. D. (1989). A new approach to the economic "
        "analysis of nonstationary time series and the business cycle. "
        "Econometrica, 57(2), 357-384. "
        "https://doi.org/10.2307/1912559"),
    "ang_bekaert_2002": (
        "Ang, A., & Bekaert, G. (2002). International asset allocation "
        "with regime shifts. The Review of Financial Studies, 15(4), "
        "1137-1187. "
        "https://doi.org/10.1093/rfs/15.4.1137"),
    "markowitz_1952": (
        "Markowitz, H. (1952). Portfolio selection. The Journal of "
        "Finance, 7(1), 77-91. "
        "https://doi.org/10.1111/j.1540-6261.1952.tb01525.x"),
    "carhart_1997": (
        "Carhart, M. M. (1997). On persistence in mutual fund "
        "performance. The Journal of Finance, 52(1), 57-82. "
        "https://doi.org/10.1111/j.1540-6261.1997.tb03808.x"),
    "sharpe_1994": (
        "Sharpe, W. F. (1994). The Sharpe ratio. The Journal of "
        "Portfolio Management, 21(1), 49-58. "
        "https://doi.org/10.3905/jpm.1994.409501"),
    "fama_french_1993": (
        "Fama, E. F., & French, K. R. (1993). Common risk factors in "
        "the returns on stocks and bonds. Journal of Financial "
        "Economics, 33(1), 3-56. "
        "https://doi.org/10.1016/0304-405X(93)90023-5"),
    "lo_2002": (
        "Lo, A. W. (2002). The statistics of Sharpe ratios. Financial "
        "Analysts Journal, 58(4), 36-52. "
        "https://doi.org/10.2469/faj.v58.n4.2453"),
}


ACADEMIC_GROUNDING_REQUIREMENT = """\
ACADEMIC CITATION REQUIREMENT:
The brief must cite primary academic sources throughout. The \
following seven citations have been verified from primary sources \
and must be used exactly as provided. Do not paraphrase, abbreviate, \
or alter any bibliographic detail. Do not substitute similar \
citations.

VERIFIED REFERENCES (use verbatim in References section):

""" + "\n\n".join(VERIFIED_CITATIONS.values()) + """

IN-TEXT CITATION RULES (APA 7th edition author-year format):

1. Hamilton (1989): Cite when introducing the Hidden Markov Model \
regime detection methodology. Example: "Following Hamilton (1989), \
the platform models market states as latent variables governed by a \
time-varying transition matrix."

2. Ang and Bekaert (2002): Cite when discussing regime-conditional \
asset allocation -- the direct academic precedent for the dynamic \
blend approach. Example: "Consistent with Ang and Bekaert (2002), the \
portfolio allocates differently across BULL, BEAR, and TRANSITION \
regimes."

3. Markowitz (1952): Cite when justifying the static blend (Classic \
60/40) using mean-variance theory. Example: "The static allocation \
follows Markowitz (1952) in seeking the efficient frontier portfolio \
that maximises return per unit of variance."

4. Fama and French (1993): Cite when introducing factor loading \
attribution analysis. Example: "Factor exposures are estimated using \
the Fama and French (1993) three-factor model extended by Carhart \
(1997)."

5. Carhart (1997): Cite alongside Fama and French (1993) for the \
four-factor attribution. Example: "The Carhart (1997) momentum \
factor is included to capture the one-year return continuation \
documented in momentum strategies."

6. Sharpe (1994): Cite when presenting the Sharpe ratio as a \
performance measure. Example: "Risk-adjusted performance is \
evaluated using the Sharpe ratio (Sharpe, 1994), defined as excess \
return per unit of total volatility."

7. Lo (2002): Cite when presenting the Deflated Sharpe Ratio or \
discussing statistical reliability of the OOS Sharpe result. \
Example: "To correct for multiple testing bias and non-normal return \
distributions, the platform computes the Deflated Sharpe Ratio \
following Lo (2002)."

MANDATORY REQUIREMENTS:
- Every citation used in-text must appear in References.
- Every paper in References must be cited in-text at least once.
- Do not add citations not in this list.
- Do not cite papers the platform did not use.
- Place the References section at the end of the brief, formatted \
as a hanging-indent list in APA 7th edition.
- Minimum three in-text citations required in the Methodology \
section alone (Hamilton 1989, Markowitz 1952, and Sharpe 1994 at \
minimum)."""


# June 21 2026 -- the FNA 670 rubric describes the executive brief as
# "a written report intended for a senior investment audience." Dr.
# Panttser's midpoint feedback flagged the output as "too academic"
# and lacking "investable conclusions". The story plan Pass 1 (Opus)
# produces a locked section plan with the right argument, but the six
# per-section Sonnet calls write academically by default unless
# explicitly instructed otherwise. This constant threads into both
# the Pass-1 prompt (so the locked plan itself uses executive framing
# in key_message) AND the per-section Sonnet prompts (so the rendered
# prose holds the same voice). NOT applied to the deck -- the deck has
# its own audience calibration via _SCRIPT_AUDIENCE_CALIBRATION.
EXECUTIVE_VOICE_REQUIREMENT = """\
VOICE AND AUDIENCE REQUIREMENT:
This brief is written for Forest Capital executives and the FNA 670 \
academic panel. Write in the voice of a senior investment \
professional addressing a CIO, not a student addressing a professor.

EXECUTIVE MEMO VOICE RULES:
1. Lead every section with the conclusion, not the methodology. A \
C-suite reader decides in the first sentence whether to keep reading.
   WRONG: "This section examines whether diversification improves \
risk-adjusted returns by analyzing..."
   RIGHT: "Diversification works. The regime-conditional blend \
outperforms 100% equity on every risk-adjusted metric across the \
full sample and the out-of-sample validation period."

2. Translate every metric into a business consequence.
   WRONG: "The OOS Sharpe ratio of 1.24 exceeds the benchmark Sharpe \
of 0.73."
   RIGHT: "The blend generates 70% more return per unit of risk than \
the S&P 500 in the out-of-sample period -- a period the model never \
saw during training."

3. Use the 2022 drawdown as the emotional anchor. Forest Capital \
executives lived through a year where 100% equity lost 52.6% at its \
worst. The brief should reference this directly: "In 2022, a 100% \
equity portfolio lost more than half its value at peak drawdown. The \
regime-conditional blend reduced that exposure by 51% through \
systematic defensive repositioning."

4. Make the recommendation unambiguous. Do not hedge the conclusion \
with "subject to further analysis" or "pending additional data." The \
analysis is complete. The recommendation is clear.
   RIGHT: "We recommend adopting regime-conditional dynamic allocation \
as Forest Capital's core portfolio framework, subject to the constraint \
relaxation noted in the limitations section."

5. Keep sentences short. One idea per sentence. Executive readers \
scan. Dense paragraphs with subordinate clauses lose them.

6. Never use passive voice in the recommendations section. "It was \
found that..." is academic. "The analysis demonstrates..." is \
executive.

PROHIBITED PHRASES (replace with direct alternatives):
   "It is worth noting that..." -> state it directly
   "Further research would benefit from..." -> omit
   "The results suggest..." -> "The results show..."
   "It could be argued that..." -> "The evidence shows..."
   "One limitation is that..." -> "The model cannot..."

LOSS METRIC LANGUAGE:
CVaR and VaR are LOSS metrics -- a more-negative value means a \
larger loss. The English superlatives "highest" / "lowest" / "best" \
/ "worst" are AMBIGUOUS when paired with these metrics ("highest \
CVaR" could mean the largest-magnitude loss OR the least-negative \
value; the reader cannot tell). The post-generation document audit \
flags any superlative ("highest", "lowest", "best", "worst", "most \
severe", "least severe") paired with a loss metric -- so use \
magnitude language instead.
   PROHIBITED: "the highest CVaR among the strategies"
   PROHIBITED: "the best VaR in the cohort"
   PROHIBITED: "the lowest tail risk" (ambiguous: smallest-magnitude
                or most-negative?)
   PROHIBITED: "the worst CVaR in the cohort" (also flags -- "worst"
                is in the superlative set the audit scans for)
   RIGHT for the most-negative value: "the largest tail risk", \
                                       "the largest CVaR magnitude"
   RIGHT for the least-negative value: "the smallest tail risk", \
                                        "the smallest CVaR magnitude"
The same guidance applies to "drawdown" and "volatility" -- they're \
loss-magnitude metrics; describe direction in magnitude language \
("largest drawdown") rather than superlative language ("highest \
drawdown") to avoid the audit flag.

NATURAL WRITING REQUIREMENT:
The brief must read as written by a senior analyst, not generated \
by a language model. Avoid patterns that signal AI authorship to an \
experienced reader.

PROHIBITED AI WRITING PATTERNS:

1. Hollow openers -- never start a section or paragraph with a \
content-free setup sentence:
   PROHIBITED: "This section examines the performance of our \
portfolio strategies across multiple dimensions."
   RIGHT: Start with the finding itself.

2. Parallel list structures -- avoid three-item parallel \
constructions that read like bullet points converted to prose:
   PROHIBITED: "The blend is robust, comprehensive, and \
analytically rigorous."
   RIGHT: Vary sentence structure. Not every point needs a sibling.

3. Transition signposting -- avoid mechanical transitions:
   PROHIBITED: "Having established X, we now turn to Y."
   PROHIBITED: "Furthermore," / "Moreover," / "Additionally," at the \
start of consecutive paragraphs.
   RIGHT: Let the logic connect the paragraphs. If the connection \
needs to be stated, state it in one clause, not a full sentence.

4. Adjective stacking -- one precise adjective beats three vague \
ones:
   PROHIBITED: "robust, comprehensive, and rigorous"
   PROHIBITED: "significant, meaningful, and material"
   RIGHT: Pick the one word that is accurate.

5. Prohibited words and phrases -- do not use:
   "leverage" (as a verb -- use "use" or "apply")
   "delve into"
   "it is worth noting"
   "importantly,"
   "notably,"
   "in conclusion," (at the start of the conclusion)
   "the results suggest" (use "the results show")
   "a testament to"
   "game-changing"
   "cutting-edge"
   "in today's environment"
   "it goes without saying"

6. Suspiciously perfect structure -- real analysts do not write \
five paragraphs of exactly equal length with exactly one main point \
each. Vary paragraph length. Some points deserve one sentence. Some \
deserve four.

7. Numeric padding -- do not restate a number in words immediately \
after stating it in digits:
   PROHIBITED: "an OOS Sharpe of 1.24 -- a 70 percent improvement \
-- representing nearly three quarters of additional risk-adjusted \
return..."
   RIGHT: State the number once, state its implication once, move on.

8. Voice consistency -- write in first person plural throughout \
("we recommend", "our analysis shows", "the blend we constructed"). \
Do not switch between first person and passive voice within a \
section.

THE TEST: read each paragraph aloud. If it sounds like a press \
release or a consulting deck, rewrite it. If it sounds like a senior \
analyst explaining something to a colleague, it is right.
"""


# ── Evaluator prompts (Pass 1, both document types) ──────────────────────

STORY_PLAN_EVALUATOR_PROMPT = """\
You are evaluating a presentation story plan for a finance practicum \
final presentation (18-20 minutes). Score the plan against these five \
criteria, 0-2 points each:

1. CENTRAL ARGUMENT (0-2)
   Does the plan open with the diversification question and resolve it \
with the OOS Sharpe result as the primary evidence? The conclusion must \
be explicit and quantified, not implied. A plan that buries the OOS \
Sharpe result below slide 6 scores 0.

2. NARRATIVE ARC (0-2)
   Does the flow move cleanly from: problem statement -> methodology -> \
in-sample evidence -> OOS validation -> conclusion? Regime detection \
and the multi-agent council must appear as core methodological \
differentiators, not as appendix material. A plan that front-loads \
results before methodology scores 1 maximum.

3. NUMERIC DISCIPLINE (0-2)
   Are all key figures explicitly present in numeric_anchors per slide? \
Required figures: OOS Sharpe blend 1.24 vs benchmark 0.73, max drawdown \
reduction vs benchmark, CAGR for each strategy, regime confidence level. \
Any slide that discusses performance without numeric_anchors scores 0 \
for this criterion.

4. SLIDE ECONOMY (0-2)
   Are slides lean -- one headline assertion, one visual reference, zero \
to two bullets maximum? A plan where slide_bullets contains more than \
two items per slide scores 1 maximum. Slides carry the assertion; the \
script carries the argument.

NOTE on speaker_notes: a separate Pass 1b generates the per-slide \
speaker_notes conditioned on this slide_plan. The plan you are \
scoring here does NOT contain speaker_notes -- do not penalise their \
absence. Score against the structural fields the plan does carry: \
headline, key_visual, numeric_anchors, slide_bullets, \
transition_to_next.

5. HONEST LIMITATIONS (0-2)
   Does the plan surface the 2-of-9 value-add events result and the \
Liberation Day underperformance directly, with a prepared response? A \
plan that omits known weaknesses scores 0 -- academic panels penalize \
evasion more than honest limitations.

6. INVESTABLE CONCLUSION (0-2)
   Does every section move toward a clear, actionable investment \
recommendation? Could a non-technical decision-maker (a Forest Capital \
representative who has not seen the analytical work) read this plan \
and understand what to do with the information? A technically correct \
output that fails to communicate an investable conclusion scores 0 on \
this criterion regardless of analytical quality. The midpoint panel \
flagged this explicitly: "too academic, lacking an investable \
conclusion".

The rubric now has six criteria for a maximum of 12 points. The \
acceptance threshold is 8.4 (70% equivalent, same proportional \
contract as the original 7.0/10 threshold).

Return ONLY valid JSON with no preamble or markdown fences:
{"overall": <float 0-12>, "feedback": "<one paragraph of specific, \
actionable improvement notes>"}

If overall >= 8.4 the plan is accepted. If < 8.4 your feedback will be \
injected into the next generation attempt. Be specific: identify which \
slides fail which criteria and what must change."""


BRIEF_PLAN_EVALUATOR_PROMPT = """\
You are evaluating an executive brief section plan for a finance \
practicum submission (5 pages, double-spaced, senior investment \
audience, FNA 670 rubric).

Score against these five criteria, 0-2 points each:

1. RUBRIC COMPLIANCE (0-2)
   Does the plan contain exactly these six sections and no others: \
executive summary, methodology overview, key findings and insights, \
limitations and risks, final recommendations, visuals? Any extra \
section (next steps, future work, appendix) scores 0.

2. RECOMMENDATIONS FRAMING (0-2)
   Are final recommendations framed as investment conclusions drawn \
from quantitative results -- not action items, next steps, or future \
research? The recommendations section must reference OOS Sharpe 1.24 \
vs 0.73 as the primary evidentiary basis. A section plan that frames \
recommendations as "we suggest further study" scores 0.

3. NUMERIC DISCIPLINE (0-2)
   Are key figures present in numeric_anchors for each section? Key \
findings section must anchor OOS Sharpe, Sharpe ratio full-sample, max \
drawdown reduction, CAGR. Methodology section must anchor sample \
period, number of strategies, rebalancing frequency.

4. SENIOR AUDIENCE CALIBRATION (0-2)
   Is the language calibrated for senior investment professionals -- \
direct, evidence-based, not over-hedged? A plan whose key_message \
fields read like a student report rather than an investment memo \
scores 1 maximum.
   Check for prohibited academic phrases: "it is worth noting", \
"further research", "results suggest", "it could be argued", "one \
limitation is". Each prohibited phrase found in the section plan's \
key_message fields reduces this criterion by 0.5 points. A \
key_message that leads with methodology rather than conclusion \
scores 0 on this criterion.
   Also flag AI writing patterns in key_message fields: parallel \
three-item lists, hollow opener sentences, adjective stacking, the \
words "leverage" (as verb), "delve", "importantly", "notably". \
Each pattern found reduces criterion 4 by 0.5 points.

5. LIMITATIONS HONESTY (0-2)
   Does the limitations section plan surface the 2-of-9 value-add \
events and Liberation Day underperformance directly? Does it frame \
them as known, bounded risks rather than hiding them?

6. INVESTABLE CONCLUSION (0-2)
   Does every section move toward a clear, actionable investment \
recommendation? Could a non-technical decision-maker (a Forest Capital \
representative) read this brief and understand what to do with the \
information? A technically correct output that fails to communicate an \
investable conclusion scores 0 on this criterion regardless of \
analytical quality. The midpoint panel flagged this explicitly: "too \
academic, lacking an investable conclusion".

7. ACADEMIC GROUNDING (0-2)
   Does the section plan cite at least five of the seven required \
papers from VERIFIED_CITATIONS (Hamilton 1989, Ang and Bekaert 2002, \
Markowitz 1952, Carhart 1997, Sharpe 1994, Fama and French 1993, Lo \
2002)? Are citations placed correctly -- Hamilton (1989) in \
methodology, Markowitz (1952) in static allocation justification, \
Sharpe (1994) when presenting the Sharpe ratio result, Lo (2002) \
with the Deflated Sharpe Ratio, Fama and French (1993) and Carhart \
(1997) in factor attribution? Is a References section planned at the \
end of the brief?
  - 2 points: five or more citations correctly placed with a planned
    References section.
  - 1 point: three or four citations present.
  - 0 points: fewer than three, or citations present but not placed
    in contextually correct sections.

The rubric now has seven criteria for a maximum of 14 points. The \
acceptance threshold is 9.8 (70% equivalent).

Return ONLY valid JSON:
{"overall": <float 0-14>, "feedback": "<specific actionable notes on \
which sections fail which criteria>"}"""


# ── Pass 1 generator system prompts ──────────────────────────────────────

_DECK_ECONOMIC_STORYTELLING = """\
ECONOMIC STORYTELLING REQUIREMENT: The presentation must explain not \
just WHAT the HMM finds but WHY regime detection improves outcomes and \
WHEN it works. The speaker notes for the methodology slide must \
include:
  - WHY: HMM identifies structural market state changes that persist \
for months (average regime duration from the transition matrix). This \
persistence means the portfolio can reposition before drawdowns \
materialize, unlike momentum signals which are reactive.
  - WHEN: Concrete examples from the play-by-play -- the 2022 BEAR \
call that reduced equity exposure before the drawdown, the TRANSITION \
call before Liberation Day (even if the timing wasn't perfect -- be \
honest about this).
Grok's anticipated questions will likely probe this layer -- the \
script must have prepared answers."""


_DECK_STORY_PLAN_BODY = """\
You design the structural plan for an 18-20 minute investment \
presentation. Your output is consumed by a downstream code path that \
renders each slide and a separate full-script pass: produce ONLY \
strict JSON, no markdown fences, no commentary, no preamble.

The plan is the LOCKED source of truth for every numeric value in the \
deck. Each slide's numeric_anchors are quoted verbatim by the per-slide \
LLM pass; any number not in numeric_anchors must NOT appear on that \
slide. The slide rendering pass writes prose around the anchors but \
never substitutes its own figures.

Audience: senior investment professionals and the FNA 670 academic \
panel (Dr. Katerina Panttser). Tone: confident, evidence-based, not \
over-hedged. Slides are executive quality: one headline assertion, one \
visual, minimal bullets (0-2 maximum per slide). Speaker notes carry \
the full argument; slides carry only the assertion and the visual \
anchor. This is an executive presentation, not a report.

The central question: does diversification outperform 100% equity on a \
risk-adjusted basis? The answer must be grounded in the December 2025 \
academic submission OOS Sharpe figures from validated_constants: 0.86 \
(blend) vs 0.43 (benchmark) over the 53-month post-2022 window. These \
are the LOCKED submission figures the panel defense uses. The live \
figures (1.24 vs 0.73) appear on the platform's Performance Record \
but are NOT used in the deck -- the academic submission record stands.

SLIDE 1 -- NON-NEGOTIABLE OPENING:
  Slide 1's headline MUST be the OOS proof point in one visual punch. \
  Required numeric_anchors for slide 1:
    oos_sharpe_blend:           0.86
    oos_sharpe_benchmark:       0.43
    oos_sharpe_improvement_pct: 98
    oos_window_months:          53
  The headline assertion is the +98% risk-adjusted advantage in \
  genuinely unseen data. Do NOT open with a methodology overview. Do \
  NOT bury the headline. Do NOT substitute the full-period Sharpe \
  (0.63 / 0.54) for the OOS pair on this slide. The audience sees \
  the answer and the proof point in the first visual.

PRESENTATION DISCIPLINE (applies to every slide):
Each slide has exactly ONE job stated in its title. Prove that job \
with one number, one table, or one chart reference. Bullets interpret \
the evidence -- they never repeat the title or re-describe the table. \
A panel audience reads a slide in 8 seconds. Every bullet must \
survive that constraint.

The Sonnet writer for each slide receives these constraints. Your \
story plan must respect them -- do not specify more than 3 key points \
per slide in the slide_plan, and set max_bullets explicitly per slide \
(see schema below).

SO WHAT FRAMING (the single most important slide design principle):

Every slide title must answer one of two questions:
- "What does this prove?" (answer slides: state the finding)
- "What is the question?" (setup slides: state what the next slide answers)

The body of the slide proves the title with ONE number, ONE table, \
or ONE chart. The bullets explain WHY that number matters -- never \
what it is (the title already said it).

Required title framing per slide (LOCKED -- the Sonnet writer must \
use these verbatim or as the token-resolved equivalent; do not \
generate alternative titles):
- Slide 1:  "Yes -- Regime-Conditional Beats 100% Equity Out-of-Sample"
- Slide 2:  "Agenda" (structural -- no so-what needed)
- Slide 3:  "Three Strategies, One Question"
- Slide 4:  "The Numbers: 0.86 vs 0.43, 53 Months of Unseen Data"
- Slide 5:  "Why Static Allocation Failed in 2022"
- Slide 6:  "Capital Preservation: Half the Drawdown, Half the Recovery Time"
- Slide 7:  "Does It Hold Up Out-of-Sample? Yes."
- Slide 8:  "Live Regime Signal: {{CURRENT_REGIME}} at {{REGIME_CONFIDENCE}} Confidence"
- Slide 9:  "What the Model Gets Wrong: 2 of 9"
- Slide 10: "How We Used AI: What Worked and What We Learned"
- Slide 11: "Live Demo -- analyticsdesk.app"
- Slide 12: "The Answer: Yes, With Conditions"

BULLET DISCIPLINE (June 22 2026 supplement):
Target 2-3 bullets per slide. Hard ceiling: never exceed 3 (or 2 for \
table-heavy slides 4, 6, 7, 8, 9, 12 where the table carries the \
evidence). Floor is zero -- if you cannot write a bullet that adds \
meaning beyond what the title and table already state, do not write \
it. One strong bullet beats two weak ones. Silence beats padding. \
The max_bullets field in slide_plan is a CEILING, not a target -- \
max_bullets=2 means "no more than 2", never "write exactly 2".

BULLET WRITING RULE:
A bullet is a "because" or a "which means" -- never a "what".
Wrong: "The blend achieved an OOS Sharpe of 0.86" (that is the \
title's job).
Right: "Nearly double the benchmark's risk-adjusted return on data \
the model never trained on".
Wrong: "Maximum drawdown was -29.7% for the blend versus -52.6% \
for the benchmark".
Right: "32 months to recover versus 71 -- that is four years of \
reinvestment opportunity".

The brief and analytical appendix provide the full analytical \
grounding. The deck's only job is to communicate the SO WHAT to a \
panel audience in 18 minutes. Strip everything that does not serve \
that job.

PRESENTATION ARC (lock in the central_argument + presentation_arc \
fields of the JSON output):
  Slide 1:    Answer + OOS proof point (0.86 vs 0.43)
  Slide 2:    Agenda (structural, no data, no chart)
  Slides 3-4: Three-strategy framing + the numbers
  Slides 5-6: Why it works (correlation break, drawdown)
  Slide 7:    Full out-of-sample treatment (in-sample vs OOS)
  Slides 8-9: Macro context + honest limitation (2 of 9)
  Slide 10:   AI methodology (BEFORE the demo -- panel needs
              context on how the council works before watching
              it operate live)
  Slide 11:   AnalyticsDesk live demo
  Slide 12:   Recommendation

CRITICAL: All numeric values in numeric_anchors must come EXACTLY from \
the validated_constants block in context. Do not estimate, interpolate, \
round beyond two decimal places, or substitute figures from prior \
knowledge. Numeric accuracy will be verified against the source cache \
after generation.

Output schema (return ONLY this JSON object). NOTE: speaker_notes \
are intentionally OMITTED from this schema. A subsequent Opus pass \
generates the per-slide speaker_notes conditioned on this locked \
slide_plan -- splitting the work keeps this pass under the token \
ceiling and lets the speaker_notes pass spend more tokens per slide \
on prose quality.

The max_bullets field is a CEILING ("no more than N"), not a target \
("write exactly N"). Set max_bullets=2 for slides 4, 6, 7, 8, 9, 12 \
(table-heavy -- the table is the evidence). Set max_bullets=3 for \
slides 1, 2, 3, 5, 10, 11. The per-slide Sonnet writer reads this \
field and refuses to emit more bullets than the cap.
{
  "central_argument": "<one-sentence thesis>",
  "presentation_arc": "<two-to-three-sentence narrative thread>",
  "slide_plan": [
    {
      "slide_number": <int>,
      "title": "<slide title>",
      "headline": "<one assertion that appears in large text on the slide>",
      "key_visual": "<chart or table name -- one of the platform visuals>",
      "numeric_anchors": {"metric_name": <value>, ...},
      "slide_bullets": ["<bullet>", ...],
      "max_bullets": <int -- CEILING; 2 for slides 4/6/7/8/9/12; 3 otherwise>,
      "transition_to_next": "<one sentence linking to next slide>"
    }
  ]
}"""


# Pass 1b -- speaker_notes generation. June 21 2026 split.
# Pass 1 used to emit speaker_notes inline in every slide entry; the
# 11 x ~200-280-word notes consumed 3000-4000 tokens by themselves and
# reliably pushed the Pass 1 JSON output past the 8000-token ceiling
# (truncating mid-object and falling back to the deterministic plan).
# The split lets Pass 1a stay lean (slide_plan structure) and lets
# Pass 1b spend its full budget on prose quality for the speaker notes
# without competing with the structural schema for tokens.
LIVE_DEMO_SEQUENCE = """\
Slide 11 is the AnalyticsDesk live demo. The speaker_notes for slide 11 \
must structure the 3.5-minute demo as a four-step sequence:

  Step 1 -- Open analyticsdesk.app and navigate to the Investment \
Outlook page. Show the live regime detection (BULL / TRANSITION / \
BEAR) with the HMM probability, the current blend recommendation, \
and the "why this regime" explanation. Time: ~50 seconds.

  Step 2 -- Open the Council Output panel. Show the five council \
agents (Strategy / Risk Manager / Quant / Historian / Devil's \
Advocate), the generator-evaluator harness, and call out the \
Risk Manager's dissenting view on the current recommendation. \
Time: ~50 seconds.

  Step 3 -- Navigate to the Reports page. Show the document \
generation cards (Executive Brief, Presentation Deck, Analytical \
Appendix) and demonstrate that this deck, the brief on the panel's \
desk, and the appendix all derive from the same numeric cache. \
Time: ~50 seconds.

  Step 4 -- Hand-off back to the deck. Say "URL is \
analyticsdesk.app, the panel has access during deliberation." \
Time: ~20 seconds.

Tone: confident, conversational, evidence-based. Do not over-hedge \
the live demo -- if a step fails on the live site, acknowledge \
briefly and move on rather than abandoning the demo."""


_DECK_SPEAKER_NOTES_BODY = """\
You write the per-slide speaker_notes for an 18-20 minute investment \
presentation, conditioned on a pre-locked slide_plan. Output ONLY \
strict JSON, no markdown fences, no preamble.

The slide_plan structure -- headlines, key_visuals, numeric_anchors, \
slide_bullets, transition_to_next -- is LOCKED. Your job is to write \
the spoken-word notes the presenter delivers while each slide is on \
screen. Notes target 200-280 words per slide = ~90-120 seconds of \
spoken content; the 11 slides must sum to 18-20 minutes of speaking \
time when totalled.

Each slide's speaker_notes must:
  * Lead with the slide's headline assertion expressed in spoken form
  * Walk the audience through the key_visual (what the chart shows, \
what the numbers anchor)
  * Bridge to the next slide via the transition_to_next sentence
  * Use ONLY values present in the slide's numeric_anchors -- no \
new figures introduced
  * Cite academic grounding verbally where appropriate (Hamilton \
1989 for HMM; Ang and Bekaert 2002 for regime-conditional \
allocation) on the methodology slide

CRITICAL: numeric_anchors are the ONLY numbers permitted in the \
speaker_notes. Quoting a figure not in the anchors block for a slide \
is a contract violation.

Output schema (return ONLY this JSON object). Keys are slide_number \
as strings, "1" through the total slide count. Every slide in the \
input slide_plan must have a corresponding key in the output:
{
  "speaker_notes": {
    "1": "<200-280 word speaker notes for slide 1>",
    "2": "<200-280 word speaker notes for slide 2>",
    ...
  }
}"""


ORAL_PRESENTATION_CONTEXT = """\
ORAL PRESENTATION CONTEXT (speaker notes only -- NEVER on a written \
slide or in any brief paragraph):
  The December 2025 submission figures (OOS Sharpe 0.86 vs 0.43) are \
  the CONSERVATIVE locked record. The platform's Live Figure row on \
  the Council Performance Record shows 1.24 vs 0.73 (+70%) through \
  May 2026 -- the submission UNDERSTATES current performance.

  For slide 1 AND slide 7, briefly note this context verbally. Frame \
  it as "the submission record is locked at the December figures; \
  the live figure has since improved." Do NOT say "we updated the \
  numbers" or "the figures are now better" -- the academic \
  submission record stands as the record. The live-figure context \
  exists only in spoken delivery; it does NOT appear on any slide \
  bullet, in any brief paragraph, or in the appendix. The slide \
  headline carries the 0.86 vs 0.43 submission figures."""


_DECK_SPEAKER_NOTES_SYSTEM_PROMPT = (
    THREE_STRATEGY_FRAME + "\n\n"
    + CENTRAL_QUESTION_AND_ANSWER + "\n\n"
    + INVESTABLE_CONCLUSION_GUARD + "\n\n"
    + STATIC_ALLOCATION_JUSTIFICATION + "\n\n"
    + LIVE_DEMO_SEQUENCE + "\n\n"
    + ORAL_PRESENTATION_CONTEXT + "\n\n"
    + _DECK_SPEAKER_NOTES_BODY)


# Composite deck Pass-1 system prompt -- midpoint-feedback constraints
# threaded ahead of the original schema body. The base body still
# defines the JSON output contract; the framing constants establish
# the rubric that the Opus arbiter and the downstream evaluator both
# enforce.
_DECK_ACADEMIC_VERBAL_GROUNDING = """\
ACADEMIC GROUNDING (deck speaker notes):
The deck does not require a formal References section -- slides are \
not the place for full bibliographic entries. But the speaker notes \
for the methodology slide MUST mention the academic grounding \
verbally: name Hamilton (1989) when introducing the Hidden Markov \
Model and name Ang and Bekaert (2002) when introducing regime-\
conditional allocation. A panel member who asks "what's the academic \
precedent for this?" must not catch the presenter empty-handed."""


_DECK_STORY_PLAN_SYSTEM_PROMPT = (
    THREE_STRATEGY_FRAME + "\n\n"
    + CENTRAL_QUESTION_AND_ANSWER + "\n\n"
    + INVESTABLE_CONCLUSION_GUARD + "\n\n"
    + STATIC_ALLOCATION_JUSTIFICATION + "\n\n"
    + _DECK_ACADEMIC_VERBAL_GROUNDING + "\n\n"
    + _DECK_ECONOMIC_STORYTELLING + "\n\n"
    + _DECK_STORY_PLAN_BODY)


_DECK_FULL_SCRIPT_BODY = """\
You write the word-for-word presenter script for an 18-20 minute \
investment presentation, conditioned on a pre-locked slide plan. \
Output ONLY strict JSON, no markdown fences, no preamble.

Each [SLIDE N: title] section must contain 150-200 words of spoken \
script. With 11 slides at 150-200 words each, the total script runs \
1650-2200 words, corresponding to 18-20 minutes at a measured pace. \
Do not over-allocate to opening slides. The AI methodology slide \
(slide 10) and conclusion (slide 11) each deserve full allocations \
-- they are not afterthoughts.

The script elaborates each slide's speaker_notes into full spoken \
paragraphs. Each slide section is delimited by [SLIDE N: {title}]. \
Transitions between slides are explicit. Tone: confident, evidence-\
based, not over-hedged. Acknowledge the 2-of-9 value-add events \
limitation honestly when relevant -- the committee will ask about it.

CRITICAL: All numeric values in the script must come EXACTLY from the \
locked numeric_anchors of the corresponding slide. Do not introduce \
new figures, do not round, do not substitute from prior knowledge.

Output schema:
{
  "full_script": "<word-for-word script with [SLIDE N: title] \
delimiters>",
  "estimated_duration_minutes": <float 18-20>
}"""


_SCRIPT_AUDIENCE_CALIBRATION = """\
AUDIENCE CALIBRATION: This script will be delivered to a mixed \
audience including Dr. Panttser (academic) and Forest Capital \
representatives (investment professionals). The academic audience \
wants to see methodological rigor and statistical validity. The \
investment audience wants to know: should I use this approach to \
manage money?

For every technical result mentioned in the script, the next \
sentence must translate it to an investment implication. Example \
patterns:
  - OOS Sharpe 1.24 vs 0.73 -> "In practical terms, this means the \
blend generated more return per unit of risk than simply holding the \
S&P 500 -- a result that holds in the post-training period where the \
model had no look-ahead advantage."
  - Max drawdown reduced 51% -> "A portfolio that lost half as much \
in the 2022 drawdown allowed investors to stay invested rather than \
panic-selling at the bottom."

Each translation should take no more than one sentence. The goal is \
that a Forest Capital representative who knows nothing about HMM can \
leave the room understanding why this approach is worth considering."""


# Composite script Pass-2 system prompt -- audience calibration ahead
# of the original body. The base body still defines the JSON output
# contract; the audience calibration layer translates every technical
# claim into an investment implication so a Forest Capital decision-
# maker leaves with an actionable takeaway.
_DECK_FULL_SCRIPT_SYSTEM_PROMPT = (
    THREE_STRATEGY_FRAME + "\n\n"
    + CENTRAL_QUESTION_AND_ANSWER + "\n\n"
    + _SCRIPT_AUDIENCE_CALIBRATION + "\n\n"
    + _DECK_FULL_SCRIPT_BODY)


_BRIEF_SECTION_PLAN_BODY = """\
You design the section plan for a 5-page executive brief for a finance \
practicum submission. Your output is consumed by a downstream code path \
that renders each section's prose: produce ONLY strict JSON, no markdown \
fences, no commentary, no preamble.

The brief follows the FNA 670 rubric exactly. The six sections, in \
order:
  1. executive_summary
  2. methodology
  3. key_findings
  4. limitations_and_risks
  5. final_recommendations
  6. visuals

Do NOT add a "next_steps" or "future_work" or "part_ii_preview" section \
-- those are explicitly excluded by the rubric. final_recommendations \
must be framed as INVESTMENT CONCLUSIONS drawn from the quantitative \
results, not action items, next steps, or future research.

Audience: senior investment professionals. Tone: direct, evidence-\
based. The plan is the LOCKED source of truth for every numeric value \
in the brief; each section's numeric_anchors are quoted verbatim by \
the per-section rendering pass.

CRITICAL: All numeric values in numeric_anchors must come EXACTLY from \
the validated_constants block in context. Numeric accuracy is verified \
against the source cache after generation.

EXECUTIVE SUMMARY OPENING SENTENCE (non-negotiable):
  The executive_summary section's key_message MUST open with a \
  sentence anchoring on the OOS proof point. Use this exact template:
    "A regime-conditional diversified blend outperforms a 100% \
     equity allocation on a risk-adjusted basis: out-of-sample \
     Sharpe {{OOS_SHARPE_BLEND}} versus {{OOS_SHARPE_BENCHMARK}} \
     over {{OOS_WINDOW_MONTHS}} months of post-2022 data the model \
     did not see during construction."
  Required numeric_anchors for executive_summary:
    oos_sharpe_blend:                  0.86
    oos_sharpe_benchmark:              0.43
    oos_window_months:                 53
    max_drawdown_regime_conditional:   -0.2974
    max_drawdown_benchmark:            -0.526

KEY FINDINGS (section 3) STRUCTURE:
  Finding 1 -- THE OOS proof point. 0.86 vs 0.43 over the 53-month \
              post-2022 window. +98% risk-adjusted advantage on data \
              the model did not see during construction. THIS IS THE \
              LEAD finding; it appears first in section 3's prose.
  Finding 2 -- Drawdown reduction. -29.7% (blend) vs -52.6% \
              (benchmark) peak, 32 vs 71 trading-day months recovery.
  Finding 3 -- Honest limitation. 2 of 9 play-by-play value-add \
              events; edge is capital preservation, not market timing.
  Finding 4 (optional, last) -- Full-period Sharpe comparison \
              (0.63 vs 0.54 over Jul 2002 - May 2026) as context \
              for the long-run record. NOT the lead.
  ORDER MATTERS: a reader scanning section 3 must see OOS first, then \
  drawdown, then limitation, then full-period as supporting context. \
  Anchoring the lead finding on the full-period figure buries the \
  headline.

Output schema:
{
  "central_argument": "<one-sentence thesis>",
  "section_plan": {
    "executive_summary": {
      "key_message": "<one-paragraph anchor message>",
      "numeric_anchors": {"metric": <value>, ...},
      "target_length_words": <int>
    },
    "methodology": { ... },
    "key_findings": { ... },
    "limitations_and_risks": { ... },
    "final_recommendations": { ... },
    "visuals": { ... }
  }
}"""


# Composite brief Pass-1 system prompt -- midpoint-feedback constraints
# threaded ahead of the original schema body. Same composition shape
# as the deck Pass-1 prompt above; the framing constants establish the
# rubric the Opus arbiter and the downstream BRIEF_PLAN_EVALUATOR_PROMPT
# both enforce.
#
# EXECUTIVE_VOICE_REQUIREMENT (June 21 2026) is composed in so the
# locked section_plan's key_message fields themselves carry the memo
# voice. Without this, the Sonnet downstream pass receives a key_message
# written in academic register and just paraphrases it -- the per-section
# voice injection alone can't fully recover.
_BRIEF_SECTION_PLAN_SYSTEM_PROMPT = (
    THREE_STRATEGY_FRAME + "\n\n"
    + CENTRAL_QUESTION_AND_ANSWER + "\n\n"
    + INVESTABLE_CONCLUSION_GUARD + "\n\n"
    + STATIC_ALLOCATION_JUSTIFICATION + "\n\n"
    + ACADEMIC_GROUNDING_REQUIREMENT + "\n\n"
    + EXECUTIVE_VOICE_REQUIREMENT + "\n\n"
    + _BRIEF_SECTION_PLAN_BODY)


# ── Pass 3 / Pass 4 system prompts ───────────────────────────────────────

_GROK_ANTICIPATED_QUESTIONS_PROMPT = """\
You are a sceptical academic panellist. Given a presentation story \
plan, generate the HARDEST questions the panel is likely to ask -- \
not softballs. The team will rehearse against your questions, so \
honesty about weaknesses matters more than politeness.

Known weak points to probe:
  * OOS window only 53 months -- limited sample for statistical claims
  * 2 of 9 rebalance events added value (play-by-play scorecard)
  * HMM convergence warnings in sensitivity analysis
  * Liberation Day (April 2025) underperformance the council missed

Output ONLY JSON. Min 5, max 10 questions. Mark "hard" for questions \
the team should rehearse carefully; "medium" for foreseeable but more \
routine questions.

Schema:
{
  "anticipated_questions": [
    {
      "question": "<the question, exactly as a panellist would ask it>",
      "difficulty": "hard" | "medium",
      "suggested_answer": "<concise, evidence-based answer the team \
should prepare>"
    }
  ]
}"""


_GEMINI_BLIND_SPOTS_PROMPT = """\
You are an independent reviewer of a presentation story plan. Your job \
is to identify what the plan is NOT saying that it SHOULD be -- gaps \
in the argument, missing caveats, claims that need more support, \
limitations the plan glosses over.

You are not scoring quality; you are surfacing blind spots. Be \
specific. Cite slide numbers or section names when possible.

Output ONLY JSON:
{
  "dissenting_view": "<one paragraph stating the strongest honest \
counter-argument to the plan's central thesis>",
  "limitations_to_surface": [
    "<a specific limitation the plan should disclose>",
    ...
  ],
  "blind_spots": [
    "<a specific gap in the argument that a sceptical reader would \
spot>",
    ...
  ]
}"""


# ── Deterministic fallback (per document type) ───────────────────────────


def _deterministic_deck_plan(deck_context: dict[str, Any]) -> dict[str, Any]:
    """A structured fallback plan built without the LLM. Always renders
    a valid (slide_plan, full_script, anticipated_questions,
    dissenting_view) tuple so the downstream deck rendering never sees
    a missing field. Uses the validated_constants from context so the
    anchors are still the locked academic figures.

    GATE BEHAVIOUR: this fallback's `_model` field is set to
    `deterministic_fallback`. The script unlock gate
    (_deck_story_plan_status at main.py:8261-8304) explicitly checks
    `plan.get("_model") != "deterministic_fallback"` and returns
    (False, False) when the cached row carries this tag. Result: a
    fallback plan IS cached but the script card stays in the
    "Generate Deck First" state until a real Opus pass succeeds.

    Any future improvement to this fallback (e.g. expanding to all
    12 slides to give a more useful preview) MUST consider whether
    to also change the gate logic OR change this fallback's _model
    tag -- otherwise the improved fallback still won't unlock the
    script card. Today's behaviour is intentional: the gate
    deliberately blocks document generation against a fallback row
    so the user doesn't ship a deck built from incomplete plan
    data. June 22 2026."""
    constants = (deck_context or {}).get("validated_constants") or {}
    blend = constants.get("oos_sharpe_regime_conditional")
    bench = constants.get("oos_sharpe_benchmark")
    return {
        "central_argument": (
            "A regime-conditional diversified blend outperforms a 100% "
            "equity allocation on a risk-adjusted basis in the post-"
            "2022 out-of-sample window."),
        "presentation_arc": (
            "Open with the question and the answer. Establish the "
            "methodology. Show the OOS evidence. Address the limitations "
            "honestly. Close with the recommendation."),
        "slide_plan": [
            {
                "slide_number": 1,
                "title": "Does Diversification Beat 100% Equity?",
                "headline": (
                    f"Yes -- OOS Sharpe {blend} vs benchmark {bench}."
                    if blend is not None and bench is not None
                    else "Yes -- the OOS Sharpe evidence is the answer."),
                "key_visual": "cumulative_return_post_2022",
                "numeric_anchors": {
                    "oos_sharpe_blend": blend,
                    "oos_sharpe_benchmark": bench,
                },
                "slide_bullets": [],
                "speaker_notes": (
                    "[DATA PENDING] -- LLM unavailable; deterministic "
                    "fallback plan in use. Regenerate the story plan "
                    "once the council models are reachable."),
                "transition_to_next": (
                    "Now the methodology that produced this result."),
            },
        ],
        "_model": _DETERMINISTIC,
    }


def _deterministic_brief_plan(
    brief_context: dict[str, Any],
) -> dict[str, Any]:
    """A structured brief section plan fallback."""
    constants = (brief_context or {}).get("validated_constants") or {}
    blend = constants.get("oos_sharpe_regime_conditional")
    bench = constants.get("oos_sharpe_benchmark")
    section_skeleton = {
        "key_message": (
            "[DATA PENDING] -- deterministic fallback plan in use."),
        "numeric_anchors": {
            "oos_sharpe_blend": blend,
            "oos_sharpe_benchmark": bench,
        },
        "target_length_words": 300,
    }
    return {
        "central_argument": (
            "A regime-conditional diversified blend outperforms a 100% "
            "equity allocation on a risk-adjusted basis."),
        "section_plan": {
            "executive_summary":      dict(section_skeleton),
            "methodology":            dict(section_skeleton),
            "key_findings":           dict(section_skeleton),
            "limitations_and_risks":  dict(section_skeleton),
            "final_recommendations":  dict(section_skeleton),
            "visuals":                dict(section_skeleton),
        },
        "_model": _DETERMINISTIC,
    }


# ── Pass 1: harness-wrapped Opus arbiter ─────────────────────────────────


def _build_pass1_generator(model: str, max_tokens: int):
    """Returns a generator_fn closure compatible with the
    GeneratorEvaluatorHarness shape: (prompt) -> response_text."""
    def _gen(prompt: str) -> str:
        from agents.base import call_claude
        # The harness passes the FULL accumulated prompt as `prompt`
        # (including any retry feedback prefix). The system prompt
        # is the document-type-specific Pass 1 instruction.
        return call_claude(
            model, prompt, "",
            max_tokens=max_tokens,
            trigger="story_plan_pass1")
    return _gen


def _run_pass1_with_harness(
    *,
    system_prompt: str,
    user_prompt: str,
    evaluator_prompt: str,
    agent_id: str,
    max_tokens: int,
) -> tuple[str, float, int]:
    """Runs Pass 1 through GeneratorEvaluatorHarness. Returns
    (best_response_text, final_score, attempts) so the caller can log
    evaluator performance per data_hash."""
    from agents.base import call_claude, OPUS_MODEL
    from agents.harness import GeneratorEvaluatorHarness

    # The harness retries the SAME generator_fn with the evaluator's
    # feedback prepended to the prompt -- so the generator_fn must
    # know the SYSTEM prompt to send to call_claude. Bind it via
    # closure here rather than threading through the harness.
    def _gen(prompt: str) -> str:
        return call_claude(
            OPUS_MODEL, system_prompt, prompt,
            max_tokens=max_tokens,
            trigger=f"story_plan:{agent_id}")

    harness = GeneratorEvaluatorHarness()
    result = harness.run(
        generator_fn=_gen,
        evaluator_prompt=evaluator_prompt,
        generator_prompt=user_prompt,
        context=user_prompt,
        agent_id=agent_id)
    return result.response, result.final_score, result.attempts


# ── JSON parsing (mirrors cio_recommendation hardening) ─────────────────


def _parse_plan_json(raw: str | None, *, log_key: str) -> dict | None:
    """Robust JSON parsing for LLM responses. Returns None on any
    failure (the caller falls open to the deterministic plan).

    Mirrors the defensive parsing pattern from
    cio_recommendation._parse_recommendation_json (PR #328):

      * Regex-anchored fence strip ([```json or ```](json or ` ``` `)
        only at leading/trailing positions -- backticks inside the
        body are never touched.
      * find('{') / rfind('}') extraction skips any preamble or
        trailing prose around the JSON object.
      * When the fence-stripped text starts with '{' but has no
        closing brace, the response was almost certainly truncated
        by the model's output-token ceiling. We surface that
        diagnostic ('story_plan_*_truncated') separately from the
        generic no-object case so a 'consider raising max_tokens'
        signal is immediately legible in the Render logs -- rather
        than the cryptic "Expecting ',' delimiter: line 142 column
        8" that an unclosed string produces inside json.loads.
      * raw_preview rides on every failure log so an operator can
        see the response shape without re-running the pass.
    """
    import re
    if not raw:
        return None
    text = raw.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*```$', '', text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        # Distinguish "model never returned JSON" from "model began
        # JSON and ran out of tokens before the closing brace". The
        # latter is an operator action item (raise max_tokens); the
        # former is usually a system-prompt or model-availability
        # issue.
        if text.startswith("{") and "}" not in text:
            log.warning(
                f"{log_key}_truncated",
                hint="consider raising max_tokens",
                raw_preview=(raw or "")[:500],
                raw_length=len(raw or ""))
        else:
            log.warning(f"{log_key}_no_object",
                        raw_preview=(raw or "")[:500])
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except Exception as exc:  # noqa: BLE001
        # Same truncation diagnostic at the parse layer: the model
        # may have emitted enough text for a stray '}' to match
        # earlier in the body even though the outer object never
        # closed -- json.loads then raises a delimiter error mid-
        # object. The raw_length signal helps the operator decide
        # whether the response was anywhere near the token ceiling.
        log.warning(f"{log_key}_parse_failed",
                    error=str(exc),
                    hint=(
                        "consider raising max_tokens (likely truncation)"
                        if len(raw or "") > 4500
                        else "JSON malformed"),
                    raw_preview=(raw or "")[:500],
                    raw_length=len(raw or ""))
        return None
    if not isinstance(obj, dict):
        log.warning(f"{log_key}_not_object")
        return None
    return obj


# ── Pass 1b: Opus speaker_notes (June 21 2026 split) ─────────────────────


def _generate_deck_speaker_notes(
    *,
    slide_plan: list[dict],
    central_argument: str,
    deck_context: dict[str, Any],
    duration_minutes: int = 19,
) -> dict[str, Any]:
    """Pass 1b -- generates the per-slide speaker_notes conditioned
    on the locked Pass 1a slide_plan. Returns
    {"speaker_notes": {"1": "...", "2": "..."}} on success or
    {"speaker_notes": {}} on any failure. Fail-open: the caller
    merges what's returned, defaulting any missing slide to an
    empty string."""
    if not slide_plan:
        return {"speaker_notes": {}}
    try:
        from agents.base import OPUS_MODEL, call_claude
        user_prompt = (
            "LOCKED SLIDE PLAN:\n"
            f"{json.dumps(slide_plan, indent=2, default=str)}\n\n"
            f"CENTRAL ARGUMENT: {central_argument}\n"
            f"DURATION: {duration_minutes} minutes\n\n"
            "DECK CONTEXT (validated constants the speaker notes "
            "must source numeric anchors from):\n"
            f"{json.dumps(deck_context, indent=2, default=str)}\n\n"
            "Produce per-slide speaker_notes as the strict JSON "
            "object specified. Every slide_number present in the "
            "LOCKED SLIDE PLAN must have a corresponding key in "
            "the output (as a string, e.g. \"1\", \"2\", ...).")
        raw = call_claude(
            OPUS_MODEL, _DECK_SPEAKER_NOTES_SYSTEM_PROMPT,
            user_prompt,
            max_tokens=5000,
            trigger="story_plan_deck_pass1b")
        parsed = _parse_plan_json(
            raw, log_key="story_plan_deck_pass1b")
        if not parsed or not parsed.get("speaker_notes"):
            log.warning("story_plan_deck_pass1b_invalid",
                        raw_preview=(raw or "")[:500])
            return {"speaker_notes": {}}
        return parsed
    except Exception as exc:  # noqa: BLE001
        log.warning("story_plan_deck_pass1b_call_failed",
                    error=str(exc))
        return {"speaker_notes": {}}


# ── Pass 3: Grok contrarian -- anticipated questions ─────────────────────


def _generate_anticipated_questions(plan_summary: str) -> list[dict]:
    """Calls Grok directly (mirrors ContrarianAnalyst's HTTP pattern)
    with a story-plan-specific prompt. Returns the parsed
    anticipated_questions list, or an empty list on any failure."""
    import os

    import httpx

    from agents.contrarian_analyst import (
        XAI_TIMEOUT_SECONDS, build_headers, resolve_xai_config,
    )

    environment = os.getenv("ENVIRONMENT", "development")
    xai = resolve_xai_config()
    if environment == "test" or xai is None:
        return []

    user_prompt = (
        "STORY PLAN SUMMARY:\n"
        f"{plan_summary}\n\n"
        "Generate the hardest questions the panel will ask. "
        "Return only JSON.")
    try:
        with httpx.Client(timeout=XAI_TIMEOUT_SECONDS) as client:
            resp = client.post(
                xai.chat_url,
                headers=build_headers(xai.api_key, xai.provider),
                json={
                    "model": xai.model,
                    "messages": [
                        {"role": "system",
                         "content": _GROK_ANTICIPATED_QUESTIONS_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": 3000,
                    "temperature": 0.7,
                })
            resp.raise_for_status()
            data = resp.json()
        raw = data["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        log.warning("story_plan_grok_failed", error=str(exc))
        return []
    obj = _parse_plan_json(raw, log_key="story_plan_grok")
    if not obj:
        return []
    questions = obj.get("anticipated_questions") or []
    return questions if isinstance(questions, list) else []


# ── Pass 4: Gemini independent -- blind spots ────────────────────────────


def _generate_blind_spots(plan_summary: str) -> dict[str, Any]:
    """Calls Gemini directly with a story-plan-specific prompt.
    Returns {dissenting_view, limitations_to_surface, blind_spots}
    or empty defaults on any failure."""
    import os

    empty = {
        "dissenting_view": "",
        "limitations_to_surface": [],
        "blind_spots": [],
    }
    api_key = os.getenv("GOOGLE_API_KEY", "")
    environment = os.getenv("ENVIRONMENT", "development")
    if environment == "test" or not api_key:
        return empty

    try:
        from agents.base import GEMINI_MODEL, call_gemini
        user_prompt = (
            "STORY PLAN SUMMARY:\n"
            f"{plan_summary}\n\n"
            "Identify what the plan is NOT saying that it should be. "
            "Return only JSON.")
        raw = call_gemini(
            GEMINI_MODEL, _GEMINI_BLIND_SPOTS_PROMPT, user_prompt,
            trigger="story_plan_gemini")
    except Exception as exc:  # noqa: BLE001
        log.warning("story_plan_gemini_failed", error=str(exc))
        return empty
    obj = _parse_plan_json(raw, log_key="story_plan_gemini")
    if not obj:
        return empty
    return {
        "dissenting_view": obj.get("dissenting_view") or "",
        "limitations_to_surface":
            obj.get("limitations_to_surface") or [],
        "blind_spots": obj.get("blind_spots") or [],
    }


# ── Top-level generators ────────────────────────────────────────────────


def generate_deck_story_plan(
    deck_context: dict[str, Any],
    slide_titles: list[str],
    *,
    audience: str = (
        "senior investment professionals and the FNA 670 academic panel"),
    duration_minutes: int = 19,
    brief_text: str | None = None,
    appendix_text: str | None = None,
) -> dict[str, Any]:
    """The four-pass deck story plan generator. Fail-open at every
    pass: an unreachable LLM degrades to deterministic fallback but
    the deck still has SOMETHING to render.

    June 21 2026 brief-as-anchor -- the deck Pass-1 Opus arbiter
    sees THREE grounding blocks composed onto the system prompt
    in this order:
      1. BRIEF GROUNDING CONTEXT (narrative anchor)
      2. APPENDIX GROUNDING CONTEXT (evidentiary backing)
      3. GENERATION RULES (slide 9 + 10 exclusion, traceability)
    Both upstream documents must exist before the deck generates
    (enforced by the 409 gate in main.py::_generate_deck_document);
    empty brief_text / appendix_text leaves the corresponding
    block as a no-op string for any legacy / test caller that
    bypasses the gate."""
    # Compose the three grounding blocks onto the base system
    # prompt. Each block returns "" when its input is empty so
    # the composition pattern is a no-op for that segment.
    from tools.brief_grounding import (
        appendix_grounding_block, brief_grounding_block,
        deck_generation_rules_block,
    )
    grounded_system_prompt = (
        _DECK_STORY_PLAN_SYSTEM_PROMPT
        + brief_grounding_block(brief_text)
        + appendix_grounding_block(appendix_text)
        + (deck_generation_rules_block()
           if (brief_text or appendix_text) else ""))

    user_prompt = (
        "CONTEXT (validated constants + per-slide data):\n"
        f"{json.dumps(deck_context, indent=2, default=str)}\n\n"
        f"SLIDE TITLES (n={len(slide_titles)}):\n"
        + "\n".join(
            f"  {i + 1}. {t}" for i, t in enumerate(slide_titles))
        + "\n\n"
        f"AUDIENCE: {audience}\n"
        f"DURATION: {duration_minutes} minutes\n\n"
        "Produce the story plan as the strict JSON object specified.")

    # Pass 1a -- Opus arbiter wrapped in harness. June 21 2026 split.
    # Pass 1 used to emit speaker_notes inline in every slide entry;
    # the 11 x ~200-280-word notes consumed 3000-4000 tokens by
    # themselves and reliably pushed the JSON output past the
    # 8000-token ceiling (truncating mid-object: "Expecting ','
    # delimiter: line 183"). Pass 1a now emits the lean slide_plan
    # structure only (no speaker_notes); Pass 1b below generates
    # the per-slide speaker_notes conditioned on the locked
    # slide_plan. Pass 1a's lean schema comfortably fits in 4000
    # tokens for 11 slides.
    try:
        # June 22 2026 -- raised from 4000 to 6000. The deck has 11
        # slides vs the brief's 6 sections; an 11-slide JSON plan was
        # routinely truncating at 4000 tokens. Production log signal:
        # story_plan_deck_pass1_parse_failed with hint "consider
        # raising max_tokens (likely truncation)" -- the Opus output
        # hit 4000 mid-slide and the JSON parse failed. System fell
        # back to _deterministic_deck_plan, which means the deck
        # story plan was never cached, which means the script gate
        # checking for a valid cached plan rejected the deck draft
        # ("Generate Deck First" symptom on the script card even
        # though a deck draft existed).
        #
        # 6000 matched the 11-slide deck with headroom for the
        # locked numeric anchors. The 12-slide structure (PR #375)
        # plus the new SO WHAT framing + max_bullets field per
        # slide (June 22 2026) push the JSON output close to the
        # boundary; 6000 is borderline and a transient overrun
        # truncates the JSON mid-slide, fails the parse, and
        # falls back to _deterministic_deck_plan -- which the
        # script gate rejects (see comment at
        # _deterministic_deck_plan). 8000 removes the risk for
        # the foreseeable future.
        raw, score, attempts = _run_pass1_with_harness(
            system_prompt=grounded_system_prompt,
            user_prompt=user_prompt,
            evaluator_prompt=STORY_PLAN_EVALUATOR_PROMPT,
            agent_id="story_plan_deck",
            max_tokens=8000)
    except Exception as exc:  # noqa: BLE001
        log.warning("story_plan_deck_pass1_failed", error=str(exc))
        return _deterministic_deck_plan(deck_context)

    pass1 = _parse_plan_json(raw, log_key="story_plan_deck_pass1")
    if not pass1 or not pass1.get("slide_plan"):
        log.warning("story_plan_deck_pass1_invalid",
                    raw_preview=(raw or "")[:500])
        return _deterministic_deck_plan(deck_context)
    log.info("story_plan_evaluated",
             document_type="deck",
             attempts=attempts,
             final_score=score)
    pass1["_model"] = "claude-opus-4-7"

    # Pass 1b -- Opus speaker_notes generation conditioned on Pass 1a.
    # Fail-open: on any failure the speaker_notes default to empty
    # strings and the slide rendering pass continues. The per-slide
    # Sonnet writers downstream will still produce serviceable
    # speaker notes from context; missing story-plan notes degrade
    # quality but do not block deck generation.
    try:
        notes_obj = _generate_deck_speaker_notes(
            slide_plan=pass1.get("slide_plan") or [],
            central_argument=pass1.get("central_argument") or "",
            deck_context=deck_context,
            duration_minutes=duration_minutes)
        speaker_notes_by_slide = (notes_obj or {}).get(
            "speaker_notes") or {}
        # Merge speaker_notes back into the slide_plan so the cached
        # plan_json shape is identical to the pre-split layout --
        # downstream consumers (per-slide Sonnet injection, PPTX
        # speaker-notes writer) require no changes.
        for slide in pass1.get("slide_plan") or []:
            sn_key = str(slide.get("slide_number"))
            slide["speaker_notes"] = speaker_notes_by_slide.get(
                sn_key, "")
        log.info("story_plan_deck_pass1b_merged",
                 n_slides=len(pass1.get("slide_plan") or []),
                 n_notes=len(speaker_notes_by_slide))
    except Exception as exc:  # noqa: BLE001
        log.warning("story_plan_deck_pass1b_failed",
                    error=str(exc))
        for slide in pass1.get("slide_plan") or []:
            slide.setdefault("speaker_notes", "")

    # Pass 2 -- Opus full script.
    try:
        from agents.base import OPUS_MODEL, call_claude
        script_user_prompt = (
            "LOCKED SLIDE PLAN:\n"
            f"{json.dumps(pass1.get('slide_plan'), indent=2, default=str)}\n\n"
            f"CENTRAL ARGUMENT: {pass1.get('central_argument')}\n"
            f"DURATION: {duration_minutes} minutes\n\n"
            "Produce the full presenter script as the strict JSON "
            "object specified.")
        script_raw = call_claude(
            OPUS_MODEL, _DECK_FULL_SCRIPT_SYSTEM_PROMPT,
            script_user_prompt,
            max_tokens=5000,
            trigger="story_plan_deck_pass2")
        script_obj = _parse_plan_json(
            script_raw, log_key="story_plan_deck_pass2")
        if script_obj and script_obj.get("full_script"):
            pass1["full_script"] = script_obj["full_script"]
            pass1["estimated_duration_minutes"] = (
                script_obj.get("estimated_duration_minutes"))
    except Exception as exc:  # noqa: BLE001
        log.warning("story_plan_deck_pass2_failed", error=str(exc))
        # Pass 2 failure does NOT fail the whole plan -- the slide
        # plan is still useful even without the script.
        pass1["full_script"] = None

    # Pass 3 -- Grok contrarian (independent of Pass 1/2 success).
    plan_summary = (
        f"Central argument: {pass1.get('central_argument')}\n"
        f"Slide count: {len(pass1.get('slide_plan') or [])}\n"
        f"First slide headline: "
        f"{(pass1.get('slide_plan') or [{}])[0].get('headline', '')}"
    )
    pass1["anticipated_questions"] = _generate_anticipated_questions(
        plan_summary)

    # Pass 4 -- Gemini blind spots (independent).
    blind = _generate_blind_spots(plan_summary)
    pass1["dissenting_view"] = blind.get("dissenting_view") or ""
    pass1["limitations_surfaced"] = (
        blind.get("limitations_to_surface", [])
        + blind.get("blind_spots", []))
    return pass1


def generate_brief_section_plan(
    brief_context: dict[str, Any],
    rubric_sections: list[str],
    *,
    audience: str = "senior investment professionals",
) -> dict[str, Any]:
    """The four-pass brief section plan generator. Same fail-open
    contract as the deck plan; structure mirrors it."""
    user_prompt = (
        "CONTEXT (validated constants + per-section data):\n"
        f"{json.dumps(brief_context, indent=2, default=str)}\n\n"
        f"RUBRIC SECTIONS (n={len(rubric_sections)}):\n"
        + "\n".join(
            f"  {i + 1}. {s}" for i, s in enumerate(rubric_sections))
        + "\n\n"
        f"AUDIENCE: {audience}\n\n"
        "Produce the section plan as the strict JSON object "
        "specified.")

    try:
        raw, score, attempts = _run_pass1_with_harness(
            system_prompt=_BRIEF_SECTION_PLAN_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            evaluator_prompt=BRIEF_PLAN_EVALUATOR_PROMPT,
            agent_id="story_plan_brief",
            max_tokens=6000)
    except Exception as exc:  # noqa: BLE001
        log.warning("story_plan_brief_pass1_failed", error=str(exc))
        return _deterministic_brief_plan(brief_context)

    pass1 = _parse_plan_json(raw, log_key="story_plan_brief_pass1")
    if not pass1 or not pass1.get("section_plan"):
        log.warning("story_plan_brief_pass1_invalid",
                    raw_preview=(raw or "")[:500])
        return _deterministic_brief_plan(brief_context)
    log.info("story_plan_evaluated",
             document_type="brief",
             attempts=attempts,
             final_score=score)
    pass1["_model"] = "claude-opus-4-7"

    # Briefs do NOT get a Pass 2 full-script (a written document does
    # not need a spoken script). Passes 3 and 4 still apply -- the
    # brief benefits from anticipated audience questions and the
    # blind-spot pass for its limitations section.
    plan_summary = (
        f"Central argument: {pass1.get('central_argument')}\n"
        f"Section count: {len(pass1.get('section_plan') or {})}")
    pass1["anticipated_questions"] = _generate_anticipated_questions(
        plan_summary)
    blind = _generate_blind_spots(plan_summary)
    pass1["dissenting_view"] = blind.get("dissenting_view") or ""
    pass1["limitations_surfaced"] = (
        blind.get("limitations_to_surface", [])
        + blind.get("blind_spots", []))
    return pass1


# ── Persistence + cache reader ──────────────────────────────────────────


async def get_cached_story_plan(
    data_hash: str, document_type: str,
) -> dict[str, Any] | None:
    """Read the cached story plan for (data_hash, document_type).
    Returns the plan dict on cache hit, None on cold cache or DB
    unavailable. Fail-open."""
    if not _DB_AVAILABLE or not data_hash:
        return None
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            row = await session.execute(
                text("SELECT plan_json, full_script, "
                     "anticipated_questions, dissenting_view, "
                     "limitations_surfaced, model, computed_at "
                     "FROM story_plans "
                     "WHERE data_hash = :h "
                     "  AND document_type = :t "
                     "ORDER BY computed_at DESC LIMIT 1"),
                {"h": data_hash, "t": document_type})
            r = row.fetchone()
            if not r:
                return None
            plan = dict(r[0]) if r[0] else {}
            plan["full_script"] = r[1]
            plan["anticipated_questions"] = (
                list(r[2]) if r[2] else [])
            plan["dissenting_view"] = r[3] or ""
            plan["limitations_surfaced"] = (
                list(r[4]) if r[4] else [])
            plan["_model"] = r[5]
            plan["computed_at"] = str(r[6])
            return plan
    except Exception as exc:  # noqa: BLE001
        log.warning("story_plan_read_error", error=str(exc))
        return None


async def persist_story_plan(
    data_hash: str, document_type: str, plan: dict[str, Any],
) -> None:
    """Persist the plan under (data_hash, document_type). Guarded
    UPSERT: a real LLM plan overwrites a previously stored
    deterministic_fallback row; a fallback never overwrites a real
    plan (mirrors PR #324's cio_recommendations recovery contract)."""
    if not _DB_AVAILABLE or not data_hash:
        return
    try:
        from sqlalchemy import text
        # Split the plan dict into the flat columns + the residual
        # JSON blob so the indexed surfaces (model, computed_at) are
        # cheap to query.
        plan_json = dict(plan)
        full_script = plan_json.pop("full_script", None)
        anticipated = plan_json.pop("anticipated_questions", None)
        dissenting = plan_json.pop("dissenting_view", "") or None
        limitations = plan_json.pop("limitations_surfaced", None)
        model = plan_json.pop("_model", None)
        central = plan.get("central_argument")

        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            await session.execute(
                text(
                    "INSERT INTO story_plans "
                    "(data_hash, document_type, central_argument, "
                    " plan_json, full_script, anticipated_questions, "
                    " dissenting_view, limitations_surfaced, model) "
                    "VALUES (:h, :t, :c, "
                    " CAST(:pj AS JSONB), :fs, "
                    " CAST(:aq AS JSONB), "
                    " :dv, CAST(:ls AS JSONB), :m) "
                    "ON CONFLICT (data_hash, document_type) "
                    "DO UPDATE SET "
                    "  central_argument = EXCLUDED.central_argument, "
                    "  plan_json = EXCLUDED.plan_json, "
                    "  full_script = EXCLUDED.full_script, "
                    "  anticipated_questions = "
                    "      EXCLUDED.anticipated_questions, "
                    "  dissenting_view = EXCLUDED.dissenting_view, "
                    "  limitations_surfaced = "
                    "      EXCLUDED.limitations_surfaced, "
                    "  model = EXCLUDED.model, "
                    "  computed_at = now() "
                    "WHERE story_plans.model "
                    "      = 'deterministic_fallback' "
                    "  AND EXCLUDED.model "
                    "      IS DISTINCT FROM 'deterministic_fallback'"),
                {
                    "h": data_hash,
                    "t": document_type,
                    "c": central,
                    "pj": json.dumps(plan_json),
                    "fs": full_script,
                    "aq": (json.dumps(anticipated)
                           if anticipated is not None else None),
                    "dv": dissenting,
                    "ls": (json.dumps(limitations)
                           if limitations is not None else None),
                    "m": model,
                })
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("story_plan_persist_error", error=str(exc))


async def refresh_story_plan(
    data_hash: str,
    document_type: str,
    *,
    deck_context: dict[str, Any] | None = None,
    brief_context: dict[str, Any] | None = None,
    slide_titles: list[str] | None = None,
    rubric_sections: list[str] | None = None,
    brief_text: str | None = None,
    brief_hash: str | None = None,
    appendix_text: str | None = None,
    appendix_hash: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Hash-pipeline entry point. Serves from cache when a non-
    fallback row exists at the extended storage_hash; otherwise
    runs the four-pass generator and persists. Fail-open per pass.

    June 21 2026 brief-as-anchor -- the storage cache key is
    extended depending on document type:
      - brief: data_hash alone (the brief is the anchor)
      - appendix: data_hash | brief_hash (depends on brief)
      - deck: data_hash | brief_hash | appendix_hash (depends
              on both)
    A refresh of any upstream document changes its hash and
    auto-invalidates the cached downstream plans.

    brief_text + appendix_text are forwarded to the deck
    generator as the BRIEF GROUNDING (narrative anchor) +
    APPENDIX GROUNDING (evidentiary backing) blocks in its
    Pass-1 Opus system prompt. Empty / None for either leaves
    the corresponding block as a no-op string.

    June 22 2026 -- `force` kwarg. When True, skip the cache
    READ and always run the four-pass generator. The write
    still happens after generation so subsequent non-forced
    calls (warm pipeline, other reads) see the fresh row.

    The deck Regenerate path passes force=True unconditionally
    so Molly can iterate on slide guidance / locked-title
    edits without needing the underlying data_hash /
    brief_hash / appendix_hash to change. Brief + appendix
    still default to force=False -- their regen surfaces are
    rarer and the cache hit is the right behaviour when the
    upstream data hasn't moved."""
    if not data_hash:
        return {"error": "no_data_hash"}
    # Per-document cache key extension. Order matters: the brief
    # uses its own data_hash; the appendix depends on the brief
    # but not on itself; the deck depends on both upstream
    # documents.
    if document_type == "brief":
        storage_hash = data_hash
    elif document_type == "deck":
        from tools.brief_grounding import (
            cache_key_with_brief_and_appendix,
        )
        storage_hash = cache_key_with_brief_and_appendix(
            data_hash, brief_hash, appendix_hash)
    else:
        from tools.brief_grounding import cache_key_with_brief
        storage_hash = cache_key_with_brief(data_hash, brief_hash)
    if not force:
        cached = await get_cached_story_plan(
            storage_hash, document_type)
        if cached and cached.get("_model") != _DETERMINISTIC:
            cached["cache"] = "hit"
            return cached
    if document_type == "deck":
        plan = generate_deck_story_plan(
            deck_context or {}, slide_titles or [],
            brief_text=brief_text,
            appendix_text=appendix_text)
    elif document_type == "brief":
        plan = generate_brief_section_plan(
            brief_context or {}, rubric_sections or [])
    else:
        # Appendix does NOT take a story-plan Opus pass -- the
        # appendix is workbook-shaped (8 independent sections,
        # no narrative arc to lock). Brief grounding for the
        # appendix is wired directly into per-section task
        # prompts in main.py::_generate_appendix_document via
        # APPENDIX_TO_BRIEF_SECTION + brief_section_excerpt. The
        # storage_hash extension above still gives appendix
        # callers cache-aware retrieval; the value returned here
        # is the legacy "unknown_document_type" branch so any
        # future appendix story-plan addition is opt-in.
        return {"error": f"unknown_document_type:{document_type}"}
    await persist_story_plan(storage_hash, document_type, plan)
    plan["cache"] = "miss"
    return plan
