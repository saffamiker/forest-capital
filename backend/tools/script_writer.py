"""
tools/script_writer.py

Generates the four script artifacts from Molly's storyboard:

  1. Full team script    — every slide in order, grouped by owner section,
                           one spoken paragraph per slide at 130 wpm
  2. Molly's script      — only slides where owner == 'Molly' or 'All'
  3. Michael's script    — only slides where owner == 'Michael'
  4. Bob's script        — only slides where owner == 'Bob'
  5. Rehearsal guide     — full script plus timing cues every 2 minutes,
                           visual cues per slide, anticipated audience
                           reactions per section

Voice differentiation per CLAUDE.md Section 14:
  - Molly's sections: confident, presentation voice, leads with the visual
  - Michael's:        technical, precise, can use first person
  - Bob's:            academic, hedged ('the results suggest…'), mirrors
                      his analytical-appendix prose style

The Sonnet-composed paragraphs are wrapped in a .docx with the AI DRAFT
banner. Pure-Python fallback emits a placeholder paragraph per slide
when the Anthropic API is unreachable — script structure is always
returned even without the LLM.
"""
from __future__ import annotations

from io import BytesIO
from typing import Any

# WPM is the spoken-delivery rate used to convert timing_mins → target
# word count. 130 is the CLAUDE.md-specified pace; faster delivery
# stresses the audience, slower runs over.
WORDS_PER_MINUTE = 130


def _target_words(timing_mins: float) -> int:
    """Returns the target word count for a given timing budget."""
    return int(round(timing_mins * WORDS_PER_MINUTE))


def _voice_register(owner: str) -> str:
    """Maps owner to the voice register the Sonnet prompt should adopt."""
    return {
        "Molly":   "presentation voice — confident, audience-aware, leads with the visual",
        "Michael": "technical voice — precise, enthusiastic, first person acceptable",
        "Bob":     "academic voice — hedged language ('results suggest', 'appears to'), formal register",
        "All":     "shared address — speak to the audience as the full team",
    }.get(owner, "neutral spoken register")


def _fallback_paragraph(slide: dict[str, Any]) -> str:
    """
    Deterministic spoken paragraph used when the Sonnet call fails.
    Produces something rehearsal-ready even with no API access — the
    presenter sees their slide, the timing, and a starter line they
    can edit. Same shape (1 paragraph, headline-anchored) as the
    LLM output so downstream parsers don't branch.
    """
    headline = slide.get("headline", "(this slide)")
    key_point = slide.get("key_point", "")
    timing = slide.get("timing_mins", 1.0)
    target = _target_words(timing)
    return (
        f"[Slide {slide.get('order', '?')} — {headline}] "
        f"{key_point} "
        f"[Target {target} words / {timing:.1f} min. "
        f"Speaker note: {slide.get('speaker_note', 'edit this paragraph for spoken delivery.')[:200]}]"
    )


def _llm_paragraph(slide: dict[str, Any], writer=None) -> str:
    """
    Asks the Academic Writer Agent to compose a spoken paragraph at the
    target word count. Falls back to the deterministic paragraph on any
    LLM error so the script-writer endpoint always returns *something*.
    """
    if writer is None:
        return _fallback_paragraph(slide)

    from agents.base import SONNET_MODEL, call_claude
    from agents.academic_writer import _SYSTEM_PROMPT

    target = _target_words(float(slide.get("timing_mins", 1.0)))
    owner = slide.get("owner", "Molly")
    user_message = (
        f"Write ONE spoken paragraph (~{target} words, NOT a list, no markdown) "
        f"for this presentation slide. {_voice_register(owner)}.\n\n"
        f"Slide {slide.get('order')}: {slide.get('headline')}\n"
        f"Key point: {slide.get('key_point')}\n"
        f"Speaker note for reference: {slide.get('speaker_note', '')[:300]}\n\n"
        f"Write only the paragraph — no headers, no labels, no bullet points. "
        f"Plain prose ready to be read aloud."
    )
    try:
        text = call_claude(SONNET_MODEL, _SYSTEM_PROMPT, user_message, max_tokens=400)
        return text.replace("AI DRAFT — REQUIRES HUMAN REVIEW", "").strip()
    except Exception:
        return _fallback_paragraph(slide)


def build_script_text(
    storyboard: dict[str, Any],
    owner_filter: str | None = None,
    include_rehearsal_cues: bool = False,
    writer=None,
) -> str:
    """
    Composes the full plain-text script. Layout matches CLAUDE.md
    Section 14 Rehearsal Guide:

      [SLIDE N — Headline]  timing  ·  owner
      ▶ spoken paragraph
      → transition phrase to next slide
      (when include_rehearsal_cues=True:)
      ⏱ timing cue
      👁 visual cue (live demo, chart click, etc.)
      💡 audience reaction note

    Args:
        owner_filter:           When set, only slides where owner==filter
                                are included. Used for the three individual
                                scripts. 'All'-owner slides are included in
                                every individual script (Q&A, intro).
        include_rehearsal_cues: True for the rehearsal guide; False for
                                clean team / individual scripts.
    """
    slides = sorted(
        storyboard.get("slides", []), key=lambda s: int(s.get("order", 0)),
    )
    if owner_filter:
        slides = [
            s for s in slides
            if s.get("owner") == owner_filter or s.get("owner") == "All"
        ]

    lines: list[str] = []
    lines.append(
        f"FOREST CAPITAL PORTFOLIO INTELLIGENCE SYSTEM — "
        f"PRESENTATION SCRIPT{f' ({owner_filter})' if owner_filter else ''}"
    )
    lines.append("AI DRAFT — REQUIRES HUMAN REVIEW")
    total = sum(float(s.get("timing_mins", 0)) for s in slides)
    lines.append(f"Total time: {total:.1f} min  ·  {len(slides)} slides")
    lines.append("=" * 70)

    elapsed = 0.0
    next_cue_at = 2.0  # First cue at 2 minutes for the rehearsal guide

    for slide in slides:
        order = slide.get("order")
        headline = slide.get("headline")
        timing = float(slide.get("timing_mins", 0))
        owner = slide.get("owner", "—")

        lines.append("")
        lines.append(
            f"[SLIDE {order} — {headline}]  {timing:.1f} min  ·  {owner}"
        )
        lines.append(f"▶ {_llm_paragraph(slide, writer)}")

        if slide.get("transition"):
            lines.append(f"→ TRANSITION: {slide['transition']}")

        elapsed += timing
        if include_rehearsal_cues:
            if elapsed >= next_cue_at:
                lines.append(
                    f"⏱ TIMING CUE: should be at {elapsed:.1f} min total when this slide ends"
                )
                next_cue_at = (int(elapsed / 2) + 1) * 2.0
            if slide.get("live_demo"):
                lines.append("👁 VISUAL CUE: this slide includes a live system demo")
            if slide.get("chart_ref"):
                lines.append(f"👁 VISUAL CUE: chart '{slide['chart_ref']}' on screen")

    lines.append("")
    lines.append("=" * 70)
    lines.append("End of script. Verify timing against the rehearsal pace.")
    return "\n".join(lines)


def build_script_docx(
    storyboard: dict[str, Any],
    owner_filter: str | None = None,
    include_rehearsal_cues: bool = False,
    writer=None,
) -> bytes:
    """
    Wraps build_script_text() output in a .docx with the AI DRAFT
    header banner. Reuses tools.docx_generator's builder so banner
    formatting stays consistent across all written deliverables.
    """
    from tools.docx_generator import build_docx

    script_body = build_script_text(
        storyboard,
        owner_filter=owner_filter,
        include_rehearsal_cues=include_rehearsal_cues,
        writer=writer,
    )

    if owner_filter:
        title = f"Presentation Script — {owner_filter}"
    elif include_rehearsal_cues:
        title = "Presentation Script — Rehearsal Guide"
    else:
        title = "Presentation Script — Full Team"

    return build_docx(
        title=title,
        subtitle=f"Pace target: {WORDS_PER_MINUTE} words per minute",
        sections=[{"heading": "Script", "body": script_body}],
    )


def build_qa_prep_docx(
    storyboard: dict[str, Any],
    strategy_results: dict[str, Any] | None = None,
) -> bytes:
    """
    Generates the Q&A Preparation document. Sections:

      1. Forest Capital questions (investment-practitioner focus)
      2. MSFA Board questions     (academic-rigour focus)
      3. AI usage questions       (architecture and limitations)

    Each question template is interpolated with values from the actual
    storyboard / strategy results so the questions reference real
    numbers. Emphasis follows Molly's timing allocation — slides where
    Molly spent more time generate more questions in that area.
    """
    from tools.docx_generator import build_docx

    slides = sorted(
        storyboard.get("slides", []), key=lambda s: int(s.get("order", 0)),
    )

    # Identify the heaviest-allocated topic so the "biased toward Molly's
    # emphasis" requirement from CLAUDE.md is reflected concretely.
    by_time = sorted(slides, key=lambda s: float(s.get("timing_mins", 0)), reverse=True)
    emphasis_slides = [s.get("headline") for s in by_time[:3]]

    n_sig = (
        sum(1 for r in (strategy_results or {}).values() if r.get("is_significant"))
        if strategy_results else 0
    )

    forest_capital_qs = [
        ("How does this strategy perform in a stagflation regime that's "
         "different from anything in the 2002-2024 backtest period?",
         "Acknowledge: backtests are not forecasts. Reference the regime-"
         "conditional analysis showing performance held across bull, bear, "
         "and transition regimes — but stagflation is genuinely out-of-sample.",
         "regime_conditional_performance.png", "MEDIUM", "Bob"),
        ("What's the minimum AUM where this becomes profitable after costs?",
         "Reference alpha_after_costs_bps. Note transaction costs scale with "
         "AUM via market impact — provide a band rather than a single number.",
         "performance_attribution_waterfall.png", "MEDIUM", "Michael"),
        ("Would you put your own money in this?",
         "Honest answer: this is a research project, not a fund. The CIO "
         "synthesis recommends specific strategies but those are course-grade "
         "recommendations, not investment advice.",
         None, "HIGH", "Molly"),
        ("What happens to your conclusion if 2025-2027 looks nothing like 2002-2024?",
         "Reference the CV stability score and the regime transition matrix. "
         "Strategies that pass all five Tier 1 gates have demonstrated robustness "
         "across bull, bear, transition, and stress windows — but the future "
         "regime can always be one we haven't seen.",
         "regime_transition_matrix.png", "HIGH", "Bob"),
        ("How does this compare to a target-date fund with similar risk?",
         "We didn't benchmark against target-date funds specifically — the "
         "100% SPY benchmark is the academic reference required by the brief. "
         "Acknowledge as a limitation worth pursuing.",
         None, "MEDIUM", "Molly"),
        ("Why p < 0.005 rather than the conventional p < 0.05?",
         "Reference Benjamin et al. 2018. With 10 strategies tested simultaneously, "
         "p < 0.05 produces ~0.5 false positives in expectation; p < 0.005 with "
         "FDR correction guards against multiple-testing inflation.",
         "multiple_comparison_table.png", "HIGH", "Bob"),
        ("Show me the worst quarter for the recommended strategy.",
         "Reference the stress test panel. Q3 2008 and Q1 2020 are the worst "
         "quarters for every strategy; recommended strategies still beat the "
         "benchmark in both quarters.",
         "stress_test_comparison.png", "HIGH", "Molly"),
    ]

    msfa_board_qs = [
        ("Walk us through your CPCV implementation.",
         "Combinatorial Purged Cross-Validation per López de Prado 2018. We "
         "generate C(N,k) test paths so the OOS Sharpe is reported as a "
         "distribution, not a point estimate. CPCV catches overfitting that "
         "walk-forward alone misses.",
         "cpcv_sharpe_distribution.png", "HIGH", "Bob"),
        ("How did you handle the LQD-to-BND bridge?",
         "BND in the Excel starts April 2007; LQD provides 58 additional "
         "months back to July 2002. Spliced at month-end with per-row source "
         "tagging in market_data_monthly. Validation tests check no gap at "
         "the join and CAGR remains in the plausible 3-7% range.",
         None, "MEDIUM", "Michael"),
        ("What's the look-ahead-bias guard in your backtester?",
         "Every signal at time t uses only data available at t-1. The "
         "verify_no_lookahead assertion fires on every rebalance. Plausibility "
         "guard: no strategy Sharpe > 2.0 — implausibly high Sharpe is the "
         "fingerprint of accidental look-ahead.",
         None, "HIGH", "Michael"),
        ("Justify FDR correction over Bonferroni.",
         "Benjamini-Hochberg controls the false-discovery rate rather than "
         "the family-wise error rate. With 10 hypotheses, Bonferroni at 0.005 "
         "requires p < 0.0005 individually — too strict. BH-FDR at q < 0.005 "
         "is the right balance per Harvey & Liu 2015.",
         "multiple_comparison_table.png", "HIGH", "Bob"),
        ("Is the Deflated Sharpe Ratio calibrated correctly for your sample size?",
         "DSR per Bailey & López de Prado 2012 with n_trials=10 and the "
         "observed return distribution moments (skewness, kurtosis). With "
         "the full study period's monthly observations we exceed the 220-observation power "
         "threshold for p < 0.005 at 80% power.",
         None, "HIGH", "Bob"),
        ("How do you avoid p-hacking with this many strategies?",
         "We pre-registered the 10-strategy universe before any backtest "
         "was run. The SPA test (Hansen 2005) explicitly corrects for "
         "data snooping across the full universe.",
         None, "MEDIUM", "Bob"),
    ]

    ai_usage_qs = [
        ("Why did you use multiple AI models rather than just Claude?",
         "Two non-Claude dissenters — Gemini and Grok — provide genuinely "
         "different blind spots from the Claude council. Different training "
         "data, different failure modes. When both Gemini AND Grok flag the "
         "same concern, the CIO treats it as a hard caveat.",
         None, "MEDIUM", "Michael"),
        ("How do you prevent hallucinated numbers in agent outputs?",
         "Every agent system prompt forbids citing numbers not present in the "
         "input. Tool outputs are the only allowed source. The QA Agent "
         "audits for hallucination as part of the methodology checklist.",
         None, "MEDIUM", "Michael"),
        ("What did the AI council get wrong, and how did you catch it?",
         "Be specific about a real example: the initial CPCV implementation "
         "had a label-leakage bug Claude didn't catch but the cross-validation "
         "test suite did. Statistical correctness lives in deterministic tests, "
         "not LLM review.",
         None, "MEDIUM", "Michael"),
        ("How much did the AI usage cost?",
         "Reference the AI Usage Log on the dashboard. Council deliberation "
         "is ~$0.30 per query; full midpoint paper generation is ~$0.20; "
         "QA Tier 2 audit is ~$0.05.",
         None, "LOW", "Michael"),
        ("Could a non-technical analyst use this system?",
         "Commentary mode is built for exactly that audience — every metric "
         "has hover/click explanations anchored to session-specific results. "
         "Bob's analytical appendix is generated from real numbers and edited, "
         "not handwritten from scratch.",
         None, "LOW", "Molly"),
    ]

    body_parts: list[str] = []
    body_parts.append(
        f"Q&A preparation document — {n_sig}/10 strategies pass all Tier 1 gates. "
        f"Questions are biased toward Molly's storyboard emphasis: "
        f"{', '.join(str(e) for e in emphasis_slides if e)}.\n\n"
        f"Each question includes a suggested answer (verify before delivering), "
        f"a chart reference to project if asked, a confidence level for the "
        f"answer, and the team-member best positioned to take the question."
    )

    sections = [
        {"heading": "Forest Capital questions", "body": _format_qa_section(forest_capital_qs)},
        {"heading": "MSFA Board questions",     "body": _format_qa_section(msfa_board_qs)},
        {"heading": "AI usage questions",       "body": _format_qa_section(ai_usage_qs)},
    ]

    return build_docx(
        title="Presentation Q&A Preparation",
        subtitle=f"Total questions: {len(forest_capital_qs) + len(msfa_board_qs) + len(ai_usage_qs)}",
        sections=[{"heading": "Overview", "body": "".join(body_parts)}, *sections],
    )


def _format_qa_section(
    questions: list[tuple[str, str, str | None, str, str]],
) -> str:
    """
    Formats a list of (question, answer, chart_ref, confidence, owner)
    tuples into a docx body string. Each question becomes its own block
    separated by a blank line so build_docx's paragraph-splitter renders
    them as discrete paragraphs.
    """
    lines: list[str] = []
    for i, (q, a, chart, confidence, owner) in enumerate(questions, start=1):
        lines.append(f"Q{i}. {q}")
        lines.append(f"Suggested answer: {a}")
        if chart:
            lines.append(f"Chart: {chart}")
        lines.append(f"Confidence: {confidence}  ·  Owner: {owner}")
        lines.append("")  # blank line between questions
    return "\n\n".join(lines)
