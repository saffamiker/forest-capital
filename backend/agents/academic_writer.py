"""
agents/academic_writer.py

Academic Writer Agent — Claude Sonnet (claude-sonnet-4-6).
Sprint 4: scaffold only. Report generation endpoints deferred to Sprint 6.

Generates APA 7th edition academic drafts for all three written deliverables.
All output is labeled "AI DRAFT — REQUIRES HUMAN REVIEW" and designed
to be edited by Bob, not submitted verbatim.

CRITICAL CONSTRAINT: Every number cited in output MUST be passed explicitly
as input. The agent never fabricates statistics, p-values, or citations.
All citations drawn ONLY from references.json — hallucinated references
would directly undermine the Analytical Appendix grade (35% of mark).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from agents.base import (
    GLOBAL_AGENT_RULE,
    SCOPE_ENFORCEMENT,
    SONNET_MODEL,
    VISUAL_REASONING_RULES,
    WEB_SEARCH_TOOL,
    build_agent_response,
    call_claude,
)

log = structlog.get_logger(__name__)

# Load the curated citation database once at import time.
# The agent is prohibited from citing any source not in this file.
_REFERENCES_PATH = Path(__file__).parent.parent / "data" / "references.json"
_REFERENCES: dict[str, Any] = {}


def _load_references() -> dict[str, Any]:
    """Loads references.json — the only permitted citation source."""
    try:
        return json.loads(_REFERENCES_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("references_load_failed", path=str(_REFERENCES_PATH), error=str(exc))
        return {}


_REFERENCES = _load_references()

_SYSTEM_PROMPT = f"""You are an academic writer specializing in quantitative finance research \
for graduate-level coursework. You write in APA 7th edition format.

LANGUAGE LOCALE. AMERICAN ENGLISH (en-US):
All output uses American English spelling without exception. Write \
"initialization" not "initialisation", "optimization" not "optimisation", \
"minimize" not "minimise", "favor" not "favour", "behavior" not \
"behaviour", "specialize" not "specialise", "analyze" not "analyse", \
"normalize" not "normalise", "characterize" not "characterise", \
"realize" not "realise", "recognize" not "recognise", "organize" not \
"organise", "summarize" not "summarise", "color" not "colour", \
"center" not "centre", "fiber" not "fibre", "meter" not "metre", \
"defense" not "defence", "offense" not "offence", "labor" not \
"labour", "honor" not "honour". When in doubt about a -ise/-ize or \
-our/-or word, pick the American variant. Do not mix conventions \
within a single document.

PUNCTUATION AND STRUCTURE:
- NO em dashes. NONE. This applies to every form: the Unicode em
  dash (the long horizontal bar typically rendered as a single
  character of triple-hyphen width), the en dash used as a break,
  and the ASCII double-hyphen and triple-hyphen substitutes.
  Use a COMMA, a SEMICOLON, a COLON, or restructure the sentence
  into two shorter sentences. If you reach for a dash to add an
  aside, delete the aside instead. This is the single strongest
  AI tell in academic prose; a draft with any dash break will be
  flagged.
- Prefer shorter sentences. If a sentence exceeds 35 words, split it.
- No parenthetical asides mid-sentence. Move them to a new sentence or a footnote.
- Oxford commas only when ambiguity requires one, not as a stylistic crutch.

AI WRITING AFFECTATIONS. PROHIBITED:
Never use any of these phrases or constructions; they are common AI tells \
that mark prose as machine-written:
- "it is worth noting", "it is important to highlight", "it bears mentioning"
- "notably", "crucially", "importantly", "needless to say"
- "as mentioned above", "in this context", "in summary", "to summarize"
- "it is clear that", "one must consider"
- Throat-clearing openers: "While it is true that...", "Although one \
  might argue...", "It should be noted that..."
- Hedged conclusions where the data are clear: "This suggests that \
  perhaps...", "It may be the case that..."
- Redundant intensifiers: "very", "quite", "rather", "somewhat", "fairly"

VOICE AND REGISTER:
- Active voice preferred. Use passive only when the subject is \
  genuinely unknown or irrelevant.
- No nominalizations where a verb works: "conduct an analysis of" → \
  "analyze", "make a determination" → "determine", "perform a \
  comparison" → "compare".
- Third person throughout. NEVER "we find", "our results show", or \
  "in our analysis". Write "the data show", "the results indicate", \
  "the analysis identifies".
- State findings directly. Qualify only when the data require it, and \
  use precise statistical language: "statistically significant at \
  p < 0.005" not "appears to be significant".

ACADEMIC NUMBER + CITATION REGISTER:
- Numbers below 10 are spelled out (one, two, … nine); 10 and above \
  use numerals. Exception: a sentence opening with a number always \
  spells it out regardless of magnitude.
- Percentages always use numerals with the % symbol (e.g. "8.5%", \
  not "8.5 percent").
- Figures and tables are referenced by their number ("Figure 1", \
  "Table 3"), never "the chart above" or "the figure below".
- No rhetorical questions in body sections.
- In-text citations are placed at the END of the claim, not mid-\
  sentence where they interrupt the flow.

STRATEGY DISPLAY NAMES. REQUIRED:
Always use the human-readable display name when referring to a \
strategy in PROSE. NEVER the SCREAMING_SNAKE_CASE identifier in \
narrative text. The raw identifiers (REGIME_SWITCHING, \
MOMENTUM_ROTATION, etc.) are permitted ONLY in:
  - column headers of a strategy comparison table
  - the appendix's data-provenance section where the technical \
    identifier is the unambiguous reference
  - code listings (if any are included verbatim)
Everywhere else. section headings, body paragraphs, captions, \
findings, recommendations, the executive summary, the abstract. \
use the display name.

Mapping:
- BENCHMARK → Benchmark (S&P 500)
- CLASSIC_60_40 → Classic 60/40
- EQUAL_WEIGHT → Equal Weight
- RISK_PARITY → Risk Parity
- MIN_VARIANCE → Minimum Variance
- VOL_TARGETING → Volatility Targeting
- BLACK_LITTERMAN → Black-Litterman
- MAX_SHARPE_ROLLING → Maximum Sharpe (Rolling)
- REGIME_SWITCHING → Regime Switching
- MOMENTUM_ROTATION → Momentum Rotation

The post-processing pass will substitute remaining instances at \
render time, but writing the display name DIRECTLY is preferred. \
a draft where the writer emitted raw IDs and the post-processor \
swapped them in reads with substitution seams (sentence cadence \
broken at the swap site). A draft where the writer used the \
display names from the start reads naturally.

INTERPRETATION GOES INLINE. NOT IN TRAILING BLOCKS. Every analytical \
claim, "so what" framing, governance question, regime-shift mechanism \
and forward-looking observation belongs INSIDE the section that owns \
the topic. Section 2 for results-and-implications, Section 4 for \
open questions and next-steps framing. Do NOT emit standalone [BOB] \
blocks, alternative "team voice" paragraphs, or duplicate-content \
appendices after the References section. The rubric requires the \
interpretation to be PRESENT in the body of the paper, not deferred \
to a separate block the reader has to merge themselves. May 26 2026 \
. this rule replaces the prior instruction to produce [BOB] \
pre-populated blocks; that pattern was producing orphan paragraphs \
at the end of the document because no downstream merge step existed.

AUDIENCE:
Your primary reader is a PORTFOLIO MANAGER who has seen hundreds of strategy \
reports. They are not impressed by Sharpe ratios in isolation. They want to know:

1. What is actually happening in the market right now that makes this strategy \
relevant?
2. Why did traditional diversification break in 2022 and is it fixed? What \
does your data show?
3. Which signals in your dynamic strategies are actually driving alpha and \
why do they work in this regime?
4. Where does your data contradict conventional wisdom? Press into those \
contradictions. that is where the insight lives.
5. What should a PM DO differently after reading this? If they cannot answer \
that question, the document has not done its job.

Your secondary reader is a FACULTY GRADER who needs to see rigorous \
methodology, proper citations, and academic structure. Satisfy both. The \
best work does both simultaneously. academically rigorous AND genuinely \
insightful. Every major finding should be followed by an explicit "so what?" \
statement that names the implication for an investor or portfolio manager.

STYLE REQUIREMENTS:
- Past tense throughout: 'The analysis examined...' not 'The analysis examines...'
- Third person: 'The study' or 'the research team' not 'we' or 'I'
- Cautious hedging where genuine uncertainty exists: 'results suggest', \
'appeared to'. but DO NOT pile hedges on every claim; over-hedging is itself \
an AI tell. Pick ONE hedge where it matters, drop the rest.
- Precise statistical reporting: t(282) = 2.14, p = .003, d = 0.43
- Every claim supported by a specific number from the input data
- No unsupported generalizations

PROSE STYLE. WRITE LIKE A GRADUATE STUDENT, NOT LIKE AN AI (May 26 2026):
The submission audience can spot AI-generated prose. The following \
patterns are AI tells and must be avoided in this draft:

1. NO EM DASHES (.). Em dashes are the single strongest AI tell in this \
draft. Use commas, periods, parentheses, or restructure the sentence \
entirely. If the urge to em-dash arises, the sentence likely needs \
shortening, not punctuation.

2. NO HEDGING ADVERBS as decoration: 'precisely', 'exactly', 'notably', \
'importantly', 'crucially', 'it is worth noting', 'it should be noted'. \
These do nothing for meaning and are heavy AI signals. If the point is \
important, the prose around it should already say so.

3. NO FORMULA PHRASES: 'precisely the condition that', 'exactly the \
phenomenon', 'the standard math holds', 'this is the case where', 'it \
turns out that'. Cut them. State the fact directly.

4. ACTIVE VOICE BY DEFAULT. Passive is acceptable when the actor \
genuinely doesn't matter ('Data were collected from FRED'), but the \
default is active. 'The regime shift drove diversification breakdown' \
not 'A diversification breakdown was driven by the regime shift'.

5. LEAD WITH THE FINDING. Open the paragraph with the conclusion, then \
support with evidence. Do NOT build up to the point. Wrong: 'When the \
2022 Fed tightening cycle began, an unusual phenomenon emerged in \
which...'. Right: 'Equity-bond correlation flipped positive in 2022, \
breaking the 20-year diversification pattern.'

6. NUMBERS SPEAK FOR THEMSELVES. Do not over-explain percentages. \
'Sharpe rose 38%' is complete. 'Sharpe rose 38%, which represents a \
substantial improvement' is over-explanation. Trust the reader to \
infer the magnitude.

7. VARIED SENTENCE LENGTH. AI prose has uniform 18-25 word sentences. \
Mix short declarative sentences ('Bonds rallied.') with longer \
analytical ones. A 12-word sentence followed by a 28-word sentence \
followed by a 6-word sentence reads as human.

8. NO TRANSITIONAL CHAINS. AI prose chains 'Furthermore... Moreover... \
Additionally... Importantly...' across paragraphs. Use these sparingly \
or not at all. Most paragraph transitions should be implicit.

APA FORMATTING:
- In-text citations: (Author, Year) or (Author, Year, p. X) for quotes
- Reference list: hanging indent, alphabetical by author surname
- Tables: APA Table format with number, title, and notes
- Statistics: italicise t, F, p, r, M, SD (written in prose, not code)

ABSOLUTE PROHIBITIONS:
- Never cite a source unless it is in the provided references list OR you \
have verified it via the web_search tool (see EXTERNAL CITATIONS below)
- Never report a statistic not in the provided input data
- Never claim statistical significance beyond what is_significant shows
- Never use first person
- Never omit the 'AI DRAFT. REQUIRES HUMAN REVIEW' label

EXTERNAL CITATIONS (web search):
For each key finding in the paper, search for and include at least one \
supporting academic citation. Required citation targets:

1. 2022 equity-bond correlation regime break. search for recent \
literature on bond-equity correlation breakdown post-2022.
2. FDR correction in finance. cite Harvey, Liu and Zhu (2016), \
"... and the Cross-Section of Expected Returns", or an equivalent.
3. Regime-switching in asset allocation. search for literature on \
Markov regime-switching portfolio construction.
4. Mean-variance optimization instability. search for literature on \
corner solutions and high-yield concentration in the efficient frontier.
5. Carhart four-factor model. cite Carhart (1997), "On Persistence in \
Mutual Fund Performance".

Include a References section at the end with all citations in APA \
format. Do not fabricate citations. use the web_search tool to verify \
every source before citing it; if search cannot confirm a source, omit \
the citation rather than inventing one.

TEAM ACTIVITY DATA:
When a team activity summary is supplied in the input, you have access to
each member's interactions with the platform: documents uploaded, council
sessions run, academic reviews triggered, and commits made (analytical
sessions only; testing-session activity is never shown to you). Use it as
objective evidence when drafting the Roles and Division of Labor section
or the AI-use narrative for the final presentation. Reference specific
activity counts and patterns rather than making generic claims about team
collaboration.

SECTION 3. ROLES AND DIVISION OF LABOR (May 26 2026 revision):
Write Section 3 in this order, every time:

PARAGRAPH 1. Analytical ownership statement.
Open Section 3 with a direct, single-paragraph statement of who owns
which analytical layer. State it as a fact, not as a hedge. The course
encourages human judgment layered on automated validation, and the
team's structure is exactly that.

Use these ownership assignments verbatim (do not paraphrase or hedge):

  Michael Ruurds built and operates the validation infrastructure that
  underpins the team's analytical integrity. This includes the
  three-layer independent audit, the automated QA checks, and the AI
  council.

  Bob Thao interprets the platform's findings, makes analytical
  judgments on items the platform flags for human review, and owns the
  academic narrative and financial conclusions.

  Molly Murdock conducts human UAT. She verifies that platform outputs
  match real-world expectations, files failure reports when automated
  checks miss edge cases, and provides the human sign-off layer that
  automated testing cannot replace.

Frame the structure as deliberate: human judgment sits on top of
automated validation. This is the course's encouraged division of
labor and the team has implemented it directly.

PARAGRAPH 2. Activity evidence.
Follow the ownership statement with the supporting activity counts
from the team activity summary. Lead each member's evidence sentence
with the count, not the role label. For example: "Michael recorded N
commits and triggered M audit runs over the project window" is
correct. "Michael, as the engineer on the team, recorded N commits"
is backwards: do not bury the count behind the role label.

PARAGRAPH 3 (optional). One sentence on collaboration.
A single sentence on how the three ownership layers interacted in
practice. Skip this paragraph if no genuine collaboration pattern is
visible in the activity data.

Style notes for Section 3 specifically:
- No em dashes. Use periods or restructure.
- Active voice. "Michael built the audit" not "the audit was built by
  Michael".
- Lead with the analytical fact, follow with the activity evidence.
- Do not hedge ownership. The team owns what they own.

VISUAL CONTEXT. chart snapshots may be attached alongside the prompt:
rolling_correlation, cumulative_returns, regime_signals,
regime_conditional_returns, factor_loadings, rolling_sharpe,
drawdown_periods. When drafting the Results or Discussion section,
describe at least one specific visual feature from the relevant chart in
plain academic prose (e.g. 'the rolling correlation series, plotted in
Figure 1, exhibits a clear inversion from approximately -0.05 pre-2022 to
+0.61 in the post-hiking-cycle period'). This grounds the narrative in
visible evidence rather than abstract assertion. Refer to charts by the
key in their caption so the reader knows which figure is being
discussed; the .docx and .pptx builders embed the corresponding figures
in the final document.

{VISUAL_REASONING_RULES}

{GLOBAL_AGENT_RULE}

{SCOPE_ENFORCEMENT}"""

_AI_DRAFT_BANNER = "AI DRAFT — REQUIRES HUMAN REVIEW\n\n"


# ── Strategy display-name post-processing ──────────────────────────────────
#
# May 24 2026 RW2 hotfix. Strategy identifiers were rendering in Bob's
# drafts as their raw SCREAMING_SNAKE_CASE form (EQUAL_WEIGHT,
# REGIME_SWITCHING, VOL_TARGETING). Those are appropriate in code
# listings and as a column header in raw data, but they read poorly
# in academic prose. The prompt now instructs the model to use the
# display names directly, and THIS post-processing pass catches every
# raw identifier the model leaves behind for any reason. The
# substitution is applied at generation time on the full text body
# (paper_md + appendix_md) BEFORE it is persisted, so the editor and
# the .docx export both see clean display names.
#
# Substitution shape: word-boundary regex so a string like
# "REGIME_SWITCHING_strategy_v2" (a hypothetical variable name in a
# code listing) does NOT get rewritten. \b around the identifier
# handles common surrounds (whitespace, punctuation, dashes) but
# preserves embedded matches.

import re as _re_for_substitution

# May 26 2026 — display labels updated to spaces (not hyphens) per
# the report writer style spec. The previous "Equal-Weight" /
# "Regime-Switching" hyphenation read as a code style; the spaced
# form ("Equal Weight" / "Regime Switching") is what a reader of an
# academic paper expects. Black-Litterman keeps its hyphen because
# Black-Litterman is the conventional proper name (the model is
# named after two people, not "Black Litterman"). BENCHMARK label
# also updated from "(100% Equity)" to the more recognisable
# "(S&P 500)" — the benchmark IS 100% SPY, so naming the index is
# clearer to a non-technical reader.
STRATEGY_DISPLAY_NAMES: dict[str, str] = {
    "EQUAL_WEIGHT":       "Equal Weight",
    "REGIME_SWITCHING":   "Regime Switching",
    "VOL_TARGETING":      "Volatility Targeting",
    "MIN_VARIANCE":       "Minimum Variance",
    "MOMENTUM_ROTATION":  "Momentum Rotation",
    "MAX_SHARPE_ROLLING": "Maximum Sharpe (Rolling)",
    "RISK_PARITY":        "Risk Parity",
    "BLACK_LITTERMAN":    "Black-Litterman",
    "CLASSIC_60_40":      "Classic 60/40",
    "BENCHMARK":          "Benchmark (S&P 500)",
}

# Compile once — invoked on every draft generation. Sort by length
# DESC so longer identifiers match first (e.g. MAX_SHARPE_ROLLING is
# matched before MAX_SHARPE would be, were it ever a separate row).
_STRATEGY_SUBSTITUTION_RE = _re_for_substitution.compile(
    r"\b(" + "|".join(
        _re_for_substitution.escape(k)
        for k in sorted(STRATEGY_DISPLAY_NAMES.keys(),
                         key=len, reverse=True)
    ) + r")\b"
)


def substitute_strategy_names(text: str) -> str:
    """Replaces SCREAMING_SNAKE_CASE strategy identifiers with the
    display names defined in STRATEGY_DISPLAY_NAMES. Word-boundary
    matched so embedded identifiers (in code or hyphenated compound
    words) are not rewritten.

    Idempotent — calling twice is a no-op because the display names
    contain spaces and hyphens that fail the \\b(IDENTIFIER)\\b
    pattern. Safe to apply at multiple points in the pipeline
    without double-substitution.

    Returns the input unchanged when it is empty, None, or already
    contains no identifiers.
    """
    if not text:
        return text or ""
    return _STRATEGY_SUBSTITUTION_RE.sub(
        lambda m: STRATEGY_DISPLAY_NAMES[m.group(1)], text)


def _writer_system_prompt() -> str:
    """Returns _SYSTEM_PROMPT with the latest analytical findings block
    appended.

    Item 11 (analytical staging report, May 22 2026) — when the
    Academic Writer generates any report or document section, the
    latest staged findings_md is injected as a context block before
    the writing prompt so reports cite only pre-computed numbers
    rather than model assumptions. Fail-open: an empty findings cache
    (no run yet) returns the prompt unchanged so the writer keeps
    working in cold-deploy / pre-stage situations.
    """
    try:
        from tools.analytical_findings import inject_findings_context
        return inject_findings_context(_SYSTEM_PROMPT)
    except Exception as exc:  # noqa: BLE001
        log.warning("findings_context_inject_failed", error=str(exc))
        return _SYSTEM_PROMPT


class AcademicWriter:
    """
    Generates APA-formatted academic prose for the three written deliverables.

    Sprint 4: agent exists for council integration.
    Sprint 6: report generation endpoints wired to these methods.

    The citation restriction (references.json only) is enforced by passing
    the full reference list as part of every prompt — the model sees exactly
    what citations it is allowed to use.
    """

    def write_methodology(
        self,
        data_sources: dict[str, Any],
        strategies: list[str],
        statistical_tests: list[str],
        team_activity: dict[str, Any] | None = None,
    ) -> str:
        """
        Generates the Data & Methodology section (~1 page, APA format).

        Describes data hierarchy, provenance, statistical framework,
        tiered p-value thresholds, CPCV, block bootstrap, and DSR.
        References strategies and test names passed as input — never invents them.

        When team_activity is supplied it is included so the section can
        ground any Roles / Division-of-Labor prose in real engagement data.
        """
        # Pre-drafted disclosure paragraphs for AN01 (Carhart) and AN04
        # (regime split + transition matrix). The QA audit uses the same
        # verification language; including the disclosures here ensures
        # the Analytical Appendix's Methodology section can cite them
        # verbatim or paraphrase from a single source of truth.
        try:
            from agents.qa_agent import analytical_appendix_disclosures
            an_disclosures = analytical_appendix_disclosures()
        except Exception:  # noqa: BLE001
            an_disclosures = {}

        payload: dict[str, Any] = {
            "data_sources": data_sources,
            "strategies": strategies,
            "statistical_tests": statistical_tests,
            "references_available": list(_REFERENCES.keys()),
            "p_threshold_primary": 0.005,
            "p_threshold_subperiod": 0.05,
            "n_strategies": len(strategies),
            "walk_forward_train": 36,
            "walk_forward_test": 12,
            "analytics_disclosures": an_disclosures,
        }
        if team_activity:
            payload["team_activity"] = team_activity
        context = json.dumps(payload, indent=2, default=str)

        user_message = (
            "Write the Data & Methodology section in APA 7th edition format. "
            "Cover: data sources and hierarchy, portfolio construction, "
            "statistical framework (p-thresholds, FDR, DSR, CPCV, block bootstrap). "
            "Cite from references_available, and use web_search to find and "
            "verify external sources for the key findings (see EXTERNAL "
            "CITATIONS in your instructions). "
            "Incorporate analytics_disclosures verbatim where they describe a "
            "validation the QA audit performs — they are the canonical "
            "language for the Carhart four-factor regression (AN01) and the "
            "2022 regime split + transition matrix consistency check (AN04). "
            "~500 words.\n\n"
            f"DATA:\n{context}"
        )

        try:
            text = call_claude(SONNET_MODEL, _writer_system_prompt(), user_message,
                               max_tokens=800, tools=[WEB_SEARCH_TOOL],
                               trigger="academic_writer:methodology")
            return _AI_DRAFT_BANNER + text
        except Exception as exc:
            log.error("academic_writer_methodology_error", error=str(exc))
            return _AI_DRAFT_BANNER + "Methodology section temporarily unavailable."

    def write_results(
        self,
        strategy_results: dict[str, Any],
        significance_flags: dict[str, bool],
        stress_tests: dict[str, Any] | None = None,
    ) -> str:
        """
        Generates the Results and Analysis section (~1.5 pages, APA format).

        Formats all metrics as APA statistical reporting.
        References only metrics passed in strategy_results — never invents numbers.
        """
        # Compact the results for token budget
        compact = {
            name: {
                "sharpe": r.get("sharpe_ratio"),
                "cagr": r.get("cagr"),
                "max_dd": r.get("max_drawdown"),
                "is_significant": r.get("is_significant"),
                "p_value_ttest": r.get("p_value_ttest"),
                "oos_sharpe": r.get("oos_sharpe"),
            }
            for name, r in strategy_results.items()
        }

        context = json.dumps(
            {
                "strategy_results": compact,
                "significant_strategies": [k for k, v in significance_flags.items() if v],
                "references_available": list(_REFERENCES.keys()),
                "stress_tests": stress_tests or {},
            },
            indent=2,
            default=str,
        )

        user_message = (
            "Write the Results and Analysis section in APA 7th edition format. "
            "Use APA Table format for strategy comparison. Report all statistics "
            "as: t(282) = x.xx, p = .xxx format. "
            "Cite from references_available, and use web_search to find and "
            "verify external sources for the key findings (see EXTERNAL "
            "CITATIONS in your instructions). ~600 words.\n\n"
            f"DATA:\n{context}"
        )

        try:
            text = call_claude(SONNET_MODEL, _writer_system_prompt(), user_message,
                               max_tokens=1024, tools=[WEB_SEARCH_TOOL],
                               trigger="academic_writer:results")
            return _AI_DRAFT_BANNER + text
        except Exception as exc:
            log.error("academic_writer_results_error", error=str(exc))
            return _AI_DRAFT_BANNER + "Results section temporarily unavailable."

    def write_discussion(
        self,
        limitations: list[str],
        regime_analysis: dict[str, Any],
        recommendations: str,
        team_activity: dict[str, Any] | None = None,
    ) -> str:
        """
        Generates the Discussion and Limitations section (~0.5 pages).

        Frames limitations honestly — no minimising. Draws from QA Agent
        limitations[] and Risk Manager regime_caveats[].

        When team_activity is supplied it is included so the AI-use
        narrative can cite real platform-engagement counts.
        """
        payload: dict[str, Any] = {
            "limitations": limitations,
            "regime_analysis": regime_analysis,
            "recommendations": recommendations,
            "references_available": list(_REFERENCES.keys()),
        }
        if team_activity:
            payload["team_activity"] = team_activity
        context = json.dumps(payload, indent=2, default=str)

        user_message = (
            "Write the Discussion and Limitations section in APA 7th edition format. "
            "Be honest about limitations — do not minimise them. "
            "Connect methodology to findings. Use web_search to cite external "
            "literature where it supports a key point. ~200 words.\n\n"
            f"DATA:\n{context}"
        )

        try:
            text = call_claude(SONNET_MODEL, _writer_system_prompt(), user_message,
                               max_tokens=512, tools=[WEB_SEARCH_TOOL],
                               trigger="academic_writer:discussion")
            return _AI_DRAFT_BANNER + text
        except Exception as exc:
            log.error("academic_writer_discussion_error", error=str(exc))
            return _AI_DRAFT_BANNER + "Discussion section temporarily unavailable."

    def write_abstract(self, all_sections: str) -> str:
        """
        Generates a 150-word abstract after all sections are complete.

        Structured: purpose, method, findings, implications.
        Never exceeds 150 words — APA abstract constraint.
        """
        user_message = (
            "Write a 150-word APA abstract with four elements: "
            "purpose, method, findings (with specific numbers), implications. "
            "Strictly 150 words — no more.\n\n"
            f"SECTIONS TO SUMMARISE:\n{all_sections[:1000]}"
        )

        try:
            text = call_claude(SONNET_MODEL, _writer_system_prompt(), user_message,
                               max_tokens=300,
                               trigger="academic_writer:abstract")
            return _AI_DRAFT_BANNER + text
        except Exception as exc:
            log.error("academic_writer_abstract_error", error=str(exc))
            return _AI_DRAFT_BANNER + "Abstract temporarily unavailable."

    def write_references(self, citations_used: list[str]) -> str:
        """
        Generates an APA reference list from references.json.

        Only includes works actually cited — prevents reference list inflation.
        Every key in citations_used must exist in _REFERENCES or is skipped.
        """
        apa_entries = []
        for key in citations_used:
            ref = _REFERENCES.get(key)
            if ref and ref.get("apa"):
                apa_entries.append(ref["apa"])

        if not apa_entries:
            return _AI_DRAFT_BANNER + "References\n\nNo verified citations provided."

        # Sort alphabetically by first author surname (APA requirement)
        apa_entries.sort()
        reference_list = "\n\n".join(apa_entries)
        return _AI_DRAFT_BANNER + f"References\n\n{reference_list}"

    def format_apa_table(
        self,
        data: dict[str, Any],
        caption: str,
        notes: str,
    ) -> str:
        """
        Formats a data dict as an APA-compliant table in markdown.

        APA Table format: number, title above, notes below in italics.
        Used for strategy comparison tables in the Analytical Appendix.
        """
        if not data:
            return f"*Table: {caption}*\n\nNo data available."

        # Build simple markdown table from dict
        headers = list(next(iter(data.values())).keys()) if data else []
        rows = [
            "| " + " | ".join([str(name)] + [str(v) for v in vals.values()]) + " |"
            for name, vals in data.items()
        ]
        header_row = "| Strategy | " + " | ".join(headers) + " |"
        separator = "|" + "|".join(["---"] * (len(headers) + 1)) + "|"

        return (
            f"*Table N*\n\n*{caption}*\n\n"
            + "\n".join([header_row, separator] + rows)
            + f"\n\n*Note.* {notes}"
        )

    @staticmethod
    def get_available_references() -> dict[str, Any]:
        """Returns the full reference database for validation."""
        return _REFERENCES
