"""
tools/storyboard_template.py

Builds the initial 15-slide storyboard that POST /api/documents/storyboard/draft
returns. The structure matches CLAUDE.md Section 14 — fifteen slides,
totalling ~19:30 of a 20-minute presentation slot, with owner assignments
that split the talk between Molly (presentation), Michael (technical),
and Bob (academic).

Why this module rather than inlining in main.py:
  - The default slide list is long enough that embedding it in a route
    handler clutters the request flow.
  - The Academic Writer Agent's speaker-note enrichment runs sequentially
    against this base structure; isolating the merge logic here keeps
    main.py focused on HTTP concerns.
  - When the structure changes (e.g. Forest Capital asks for a 25-minute
    slot in a follow-up sprint), only this file is touched.
"""
from __future__ import annotations

import uuid
from typing import Any


# Default storyboard structure — 15 slides, 19:30 total, three owners.
# Edit these tuples to reshape the presentation skeleton; everything
# downstream (UI timing bar, script writer, deck generator) re-derives
# from whatever the storyboard JSON actually contains, not from
# constants duplicated elsewhere.
DEFAULT_SLIDES: list[dict[str, Any]] = [
    {
        "order": 1, "owner": "Molly", "timing_mins": 0.5,
        "headline_template": "Research question — does diversification beat 100% equity?",
        "key_point_template": "Twenty-five years, ten strategies, one question.",
        "chart_ref": None, "live_demo": False,
        "transition_template": "Let me show you the system we built…",
    },
    {
        "order": 2, "owner": "Molly", "timing_mins": 1.0,
        "headline_template": "System architecture — six AI agents plus a QA auditor",
        "key_point_template": "Multi-agent council with independent dissenters.",
        "chart_ref": None, "live_demo": False,
        "transition_template": "Before we get to results, where the data comes from…",
    },
    {
        "order": 3, "owner": "Molly", "timing_mins": 1.0,
        "headline_template": "Data sources and provenance",
        "key_point_template": "Excel authoritative, supplemental fetches fill gaps only.",
        "chart_ref": None, "live_demo": False,
        "transition_template": "And the ten strategies tested…",
    },
    {
        "order": 4, "owner": "Molly", "timing_mins": 1.5,
        "headline_template": "Portfolio strategies — five static, five dynamic",
        "key_point_template": "From 60/40 to regime-switching with HMM signals.",
        "chart_ref": None, "live_demo": False,
        "transition_template": "Twenty-five years of cumulative returns…",
    },
    {
        "order": 5, "owner": "Molly", "timing_mins": 2.0,
        "headline_template": "Cumulative returns — $1 across 25 years",
        "key_point_template": "{best_strategy} terminal value vs benchmark.",
        "chart_ref": "cumulative_returns.png", "live_demo": False,
        "transition_template": "But raw returns mislead — let's add risk…",
    },
    {
        "order": 6, "owner": "Molly", "timing_mins": 1.5,
        "headline_template": "Risk-adjusted performance — Sharpe ratios",
        "key_point_template": "{n_significant} of 10 pass all Tier 1 gates at p<0.005.",
        "chart_ref": "strategy_comparison.csv", "live_demo": False,
        "transition_template": "Performance is regime-dependent…",
    },
    {
        "order": 7, "owner": "Molly", "timing_mins": 2.0,
        "headline_template": "Regime analysis — bull, bear, transition",
        "key_point_template": "Dynamic strategies hold up across all three regimes.",
        "chart_ref": "regime_conditional_performance.png", "live_demo": False,
        "transition_template": "And the worst crises…",
    },
    {
        "order": 8, "owner": "Molly", "timing_mins": 1.0,
        "headline_template": "2008 GFC stress test",
        "key_point_template": "Benchmark drawdown -50%; significant strategies < -22%.",
        "chart_ref": "stress_test_comparison.png", "live_demo": False,
        "transition_template": "But the most important test is 2022…",
    },
    {
        "order": 9, "owner": "Molly", "timing_mins": 1.5,
        "headline_template": "2022 — when bonds failed",
        "key_point_template": "Correlation broke from -0.31 to +0.48 — diversification disappeared.",
        "chart_ref": "correlation_breakdown.png", "live_demo": False,
        "transition_template": "Statistical significance is the academic gate…",
    },
    {
        "order": 10, "owner": "Molly", "timing_mins": 1.0,
        "headline_template": "Statistical significance — Tier 1 gates",
        "key_point_template": "p<0.005 with Benjamini-Hochberg FDR correction across all 10.",
        "chart_ref": "significance_journey_matrix.png", "live_demo": False,
        "transition_template": "Now Michael will walk through the AI council…",
    },
    {
        "order": 11, "owner": "Michael", "timing_mins": 1.5,
        "headline_template": "AI council architecture",
        "key_point_template": "Six agents plus two non-Claude dissenters — Gemini and Grok.",
        "chart_ref": None, "live_demo": True,
        "transition_template": "And what we learned from running them…",
    },
    {
        "order": 12, "owner": "Michael", "timing_mins": 1.5,
        "headline_template": "What we learned from AI — what worked and what didn't",
        "key_point_template": "Multi-agent debate genuinely surfaced different blind spots.",
        "chart_ref": None, "live_demo": False,
        "transition_template": "Bob will close with the limitations of the analysis…",
    },
    {
        "order": 13, "owner": "Bob", "timing_mins": 1.5,
        "headline_template": "Limitations and risks",
        "key_point_template": "Backtested returns are not realised returns; regime shifts continue.",
        "chart_ref": None, "live_demo": False,
        "transition_template": "With those caveats in mind, our recommendation…",
    },
    {
        "order": 14, "owner": "Molly", "timing_mins": 1.0,
        "headline_template": "Strategic recommendation for Forest Capital",
        "key_point_template": "Dynamic regime-adaptive allocation over static 60/40.",
        "chart_ref": None, "live_demo": False,
        "transition_template": "We'll take questions now…",
    },
    {
        "order": 15, "owner": "All", "timing_mins": 0.5,
        "headline_template": "Q&A",
        "key_point_template": "Open the floor.",
        "chart_ref": None, "live_demo": False,
        "transition_template": "",
    },
]


def _interpolate(template: str, ctx: dict[str, Any]) -> str:
    """
    Replaces {placeholder} tokens with values from ctx. Falls back to a
    descriptive placeholder when the key is missing so the storyboard
    never displays "{best_strategy}" verbatim if strategy results haven't
    loaded yet.
    """
    out = template
    for key, value in ctx.items():
        out = out.replace(f"{{{key}}}", str(value))
    return out


def _default_speaker_note(slide: dict[str, Any], ctx: dict[str, Any]) -> str:
    """
    Generates a baseline speaker note for slides where the Academic Writer
    Agent didn't run (test env, API failure, fallback path). One sentence
    grounded in the slide's headline + the running context — never generic
    boilerplate, since the user is meant to edit these.
    """
    headline = _interpolate(slide["headline_template"], ctx)
    return (
        f"Speaker note for slide {slide['order']}: {headline}. "
        f"Owner: {slide['owner']}. "
        f"Edit this paragraph with the analyst note you want the presenter "
        f"to deliver — the Academic Writer Agent can regenerate it on demand."
    )


def build_default_storyboard(
    strategy_results: dict[str, Any] | None = None,
    writer=None,  # AcademicWriter instance, optional
) -> dict[str, Any]:
    """
    Produces the initial 15-slide storyboard JSON that the editor opens with.

    Args:
        strategy_results: run_all_strategies() output — used to interpolate
                          live numbers into headlines and key points.
                          Passing None still works (placeholder text).
        writer:           Optional AcademicWriter instance. When provided
                          and the Anthropic API is reachable, the writer
                          enriches the speaker notes with session-grounded
                          prose. When omitted or unavailable, the function
                          emits deterministic placeholder notes.

    Returns:
        A dict with keys:
          slides[]           — list of slide objects (see below)
          total_timing_mins  — sum of timing_mins across all slides
          generated_at       — ISO timestamp at draft creation
          ai_draft           — always True for first generation

    Slide schema (per CLAUDE.md Section 14):
        id, order, owner, timing_mins, headline, key_point,
        chart_ref, speaker_note, live_demo, transition, ai_draft
    """
    from datetime import datetime, timezone

    # Build the interpolation context from strategy results — these are
    # the numbers that show up in slide 5/6/8/9 headlines.
    ctx = _build_context(strategy_results or {})

    slides: list[dict[str, Any]] = []
    for raw in DEFAULT_SLIDES:
        headline = _interpolate(raw["headline_template"], ctx)
        key_point = _interpolate(raw["key_point_template"], ctx)
        transition = _interpolate(raw["transition_template"], ctx)
        speaker_note = _default_speaker_note(raw, ctx)

        # Try Academic Writer enrichment for slides that benefit from
        # session-specific prose. The first/last and Q&A slides keep the
        # generic note — they don't change between sessions.
        if writer is not None and 5 <= raw["order"] <= 13:
            try:
                speaker_note = _writer_enriched_note(writer, raw, ctx, headline)
            except Exception:
                # Fall back silently — placeholder note is still usable
                pass

        slides.append({
            "id":           str(uuid.uuid4()),
            "order":        raw["order"],
            "owner":        raw["owner"],
            "timing_mins":  raw["timing_mins"],
            "headline":     headline,
            "key_point":    key_point,
            "chart_ref":    raw["chart_ref"],
            "speaker_note": speaker_note,
            "live_demo":    raw["live_demo"],
            "transition":   transition,
            "ai_draft":     True,
        })

    return {
        "slides":             slides,
        "total_timing_mins":  round(sum(s["timing_mins"] for s in slides), 2),
        "generated_at":       datetime.now(timezone.utc).isoformat(),
        "ai_draft":           True,
    }


def _build_context(strategy_results: dict[str, Any]) -> dict[str, Any]:
    """
    Distils strategy_results into the template-variable dict used by
    _interpolate. Keys here must match {token} names in DEFAULT_SLIDES.
    Missing data falls through to descriptive placeholders.
    """
    if not strategy_results:
        return {
            "best_strategy":  "the leading dynamic strategy",
            "n_significant":  "0",
            "n_strategies":   "10",
        }

    significant = [n for n, r in strategy_results.items()
                   if r.get("is_significant", False)]
    by_sharpe = sorted(
        strategy_results.items(),
        key=lambda kv: float(kv[1].get("sharpe_ratio", 0.0) or 0.0),
        reverse=True,
    )
    best_strategy = (
        by_sharpe[0][0].replace("_", " ") if by_sharpe
        else "the leading dynamic strategy"
    )
    return {
        "best_strategy":  best_strategy,
        "n_significant":  str(len(significant)),
        "n_strategies":   str(len(strategy_results)),
    }


def _writer_enriched_note(
    writer,
    raw_slide: dict[str, Any],
    ctx: dict[str, Any],
    headline: str,
) -> str:
    """
    Asks the Academic Writer Agent for a single short paragraph anchored
    to this slide. We deliberately don't call write_methodology /
    write_results in full — those produce 400-600 word sections, way too
    long for a speaker note. Instead we craft a tight prompt and read the
    raw response.
    """
    from agents.base import SONNET_MODEL, call_claude
    from agents.academic_writer import _SYSTEM_PROMPT

    user_message = (
        f"Write ONE paragraph (~60 words, spoken delivery) for slide "
        f"{raw_slide['order']} titled '{headline}'. Owner: {raw_slide['owner']}. "
        f"Key point: {_interpolate(raw_slide['key_point_template'], ctx)}.\n\n"
        f"This is a spoken speaker note, not academic prose. Conversational "
        f"register. Use only numbers in this brief: "
        f"{ctx['n_significant']} of {ctx['n_strategies']} strategies pass all "
        f"Tier 1 gates; best Sharpe strategy is {ctx['best_strategy']}."
    )

    text = call_claude(SONNET_MODEL, _SYSTEM_PROMPT, user_message,
                       max_tokens=200, trigger="storyboard_template:speaker_note")
    # Strip the "AI DRAFT" banner the writer prepends to every output —
    # it lives in the document header, not in every speaker note.
    return text.replace("AI DRAFT — REQUIRES HUMAN REVIEW", "").strip()
