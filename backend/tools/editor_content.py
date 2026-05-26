"""
tools/editor_content.py

Converts a generated deliverable into the editor's content format —
the TipTap document JSON the rich-text editor loads (content_json) and
the plain-text projection the AI and Academic Review read (content_text).

Inline working aids survive the conversion as plain text inside
paragraphs: [[VERIFY: …]] / [[VERIFY CITATION: …]] markers as the
Academic Writer emitted them. The editor frontend renders [[VERIFY]]
as an amber span.

May 26 2026 — submission night. The six [[BOB]] placeholder callouts
that previously sat below each midpoint/brief section have been
removed. The rubric requires analytical interpretation to be PRESENT,
not human-authored — the Academic Writer's section prose now stands
as the deliverable's interpretation. Bob edits the generated output
for voice; he does not write from scratch.
"""
from __future__ import annotations

from typing import Any


def _text_node(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _heading(text: str, level: int = 1) -> dict[str, Any]:
    return {"type": "heading", "attrs": {"level": level},
            "content": [_text_node(text)]}


def _paragraph(text: str) -> dict[str, Any]:
    # An empty paragraph carries no content key — TipTap treats it as a
    # blank line.
    return ({"type": "paragraph", "content": [_text_node(text)]}
            if text else {"type": "paragraph"})


def _section_blocks(
    heading: str, narrative: str, callout: str | None,
) -> tuple[list[dict], list[str]]:
    """One section → its TipTap nodes and its plain-text lines."""
    nodes: list[dict] = [_heading(heading, level=1)]
    lines: list[str] = [heading, ""]
    for block in (narrative or "").strip().split("\n\n"):
        block = block.strip()
        if not block:
            continue
        nodes.append(_paragraph(block))
        lines.append(block)
        lines.append("")
    if callout:
        nodes.append(_paragraph(f"[[BOB: {callout}]]"))
        lines.append(f"[[BOB: {callout}]]")
        lines.append("")
    return nodes, lines


# Section order for the midpoint paper — (heading, narratives key,
# trailing callout or None). All callouts removed May 26 2026; the
# third tuple element is kept as a None placeholder so the existing
# _section_to_nodes_and_lines unpacking continues to work and a future
# callout could be reintroduced without a structure change.
_MIDPOINT_SECTIONS = [
    ("1. Data and Methodology", "methodology", None),
    ("2. Preliminary Results", "results", None),
    ("3. Roles and Division of Labor", "roles", None),
    ("4. Next Steps and Open Questions", "next_steps", None),
]


# Section order for the executive brief — (heading, narratives key,
# trailing callout or None). All callouts removed May 26 2026; the
# Academic Writer produces the executive summary, limitations and
# final recommendations directly. [[VERIFY]] markers remain inline
# for any uncertain numeric values the writer flagged.
_EXEC_BRIEF_SECTIONS = [
    ("Executive Summary", "exec_summary", None),
    ("Methodology Overview", "methodology", None),
    ("Finding 1 — The 2022 Correlation Break", "finding_1", None),
    ("Finding 2 — Static Allocation Results", "finding_2", None),
    ("Finding 3 — Dynamic Allocation Results", "finding_3", None),
    ("Finding 4 — Factor Analysis", "finding_4", None),
    ("Limitations and Risks", "limitations", None),
    ("Final Recommendations", "recommendations", None),
]


# The 16 presentation-deck slides — (title, narratives key or None,
# seed content). Mirrors tools/academic_deck.build_presentation_deck's
# slide order so a deck draft opened in the editor matches the generated
# .pptx. The five narrative-keyed slides take the generated prose; the
# rest take the static seed — the deck endpoint's narratives dict only
# carries the four keyed sections, and build_presentation_deck embeds the
# other slides' bodies as inline bullet literals. The seed gives every
# editor slide-card non-empty starting content rather than a blank field.
_DECK_SLIDES: list[tuple[str, str | None, str]] = [
    ("Title", None,
     "Forest Capital Portfolio Intelligence System — does diversification "
     "across equities and fixed income improve risk-adjusted performance?"),
    ("Agenda", None,
     "The research question, the data and methodology, the static and "
     "dynamic strategy results, the 2022 regime break, and the "
     "recommendation."),
    ("The Question", "thesis",
     "The research question and why it matters for portfolio construction."),
    ("Data and Methodology", None,
     "Aligned monthly returns for equities, investment-grade and "
     "high-yield bonds over the 2002–2025 study period; ten strategies "
     "grouped as static or dynamic; long-only, fully invested, quarterly "
     "rebalancing; Carhart four-factor attribution."),
    ("The 2022 Regime Break", "thesis",
     "The 2022 equity-bond correlation break and what it means for "
     "diversification."),
    ("Static Allocation Results", None,
     "How the fixed-weight strategies — 60/40, Risk Parity, Minimum "
     "Variance, Equal Weight — performed against the 100% equity "
     "benchmark."),
    ("Dynamic Allocation Results", None,
     "How the rules-based strategies — Regime Switching, Momentum "
     "Rotation, Volatility Targeting, Black-Litterman, Max-Sharpe Rolling "
     "— performed, and their drawdown behaviour."),
    ("Cumulative Returns — Growth of $1", None,
     "Growth of $1 invested at inception across every strategy and the "
     "benchmark over the full study period."),
    ("Risk-Return Profile", None,
     "Each strategy plotted by annualised return against volatility, "
     "against the efficient frontier of static allocations."),
    ("Factor Analysis", None,
     "Carhart four-factor loadings — market, size, value and momentum — "
     "and the alpha each strategy generates beyond passive factor "
     "exposure."),
    ("Drawdown Analysis", None,
     "Peak-to-trough losses and recovery periods, comparing the "
     "strategies' worst-case behaviour against the benchmark."),
    ("Sensitivity Analysis", None,
     "How the headline results hold up when key parameters are varied — "
     "a robustness check on the conclusions."),
    ("Conclusions", "conclusions",
     "What the analysis concludes about diversification and the 2022 "
     "regime break."),
    ("Recommendations", "recommendations",
     "The strategic allocation recommendation for Forest Capital."),
    ("How We Built This", "ai_leverage",
     "How the team used AI — a multi-model council and quality harness — "
     "to build and check the analysis."),
    ("Questions and Discussion", None,
     "Closing slide — questions from Forest Capital and the MSFA panel."),
]


def _canvas_slide(slide_id: int, title: str, content: str) -> dict[str, Any]:
    """A generated deck slide in the canvas element schema (migration 022)
    — the title and body as positioned text elements on a 960x540
    canvas, matching migration 022's _slide_to_canvas mapping so a freshly
    generated deck and a migrated one open identically in the editor."""
    return {
        "id": slide_id,
        "title": title,
        "background": "#FFFFFF",
        "speaker_notes": "",
        "elements": [
            {"id": "el_001", "type": "text",
             "x": 60, "y": 40, "width": 840, "height": 80,
             "content": title, "fontSize": 36, "fontWeight": "bold",
             "fontStyle": "normal", "color": "#1B2A4A", "locked": False},
            {"id": "el_002", "type": "text",
             "x": 60, "y": 150, "width": 840, "height": 330,
             "content": content, "fontSize": 18, "fontWeight": "normal",
             "fontStyle": "normal", "color": "#333333", "locked": False},
        ],
    }


def deck_to_editor(
    narratives: dict[str, str],
) -> tuple[dict[str, Any], str]:
    """
    Builds the editor content for a generated presentation deck.

    Returns (content_json, content_text): content_json is
    {"slides": [...]} in the canvas element schema (migration 022) — each
    slide carries its title and body as positioned text elements so the
    deck opens directly in the Konva canvas editor. EVERY slide's body is
    seeded — from the generated narrative where the slide maps to one,
    otherwise from the static per-slide description in _DECK_SLIDES — so
    no slide opens blank. speaker_notes start EMPTY so Molly writes her
    own. content_text concatenates every slide for Academic Review.
    """
    slides: list[dict[str, Any]] = []
    text_lines: list[str] = []
    for i, (title, key, seed) in enumerate(_DECK_SLIDES, start=1):
        # The generated narrative when this slide maps to one, otherwise
        # the static seed — never an empty body.
        narrative = narratives.get(key, "").strip() if key else ""
        content = narrative or seed
        slides.append(_canvas_slide(i, title, content))
        text_lines.append(f"Slide {i}: {title}")
        if content:
            text_lines.append(content)
        text_lines.append("")
    content_json = {"slides": slides}
    content_text = "\n".join(text_lines).strip()
    return content_json, content_text


def _word_count_warning_block(
    validation: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Builds an editor-draft banner that surfaces midpoint paper
    word-count drift (May 25 2026). Renders as a `[[BOB: WORD COUNT
    WARNING — …]]` callout the editor styles as an amber panel — the
    same convention every other human-input prompt uses, so a user
    already familiar with the [[BOB]] callout treats this banner the
    same way: an actionable note to address before submitting.

    Returns ([] / "") when the validation is missing or in-range so the
    banner is invisible on a clean run.
    """
    if not validation or validation.get("valid"):
        return [], []
    warnings = validation.get("warnings") or []
    if not warnings:
        return [], []
    total = validation.get("total_words")
    total_target = validation.get("total_target") or [750, 900]
    detail = "; ".join(str(w) for w in warnings)
    summary = (
        f"WORD COUNT WARNING — the AI draft totals {total} words "
        f"against a {total_target[0]}-{total_target[1]} target for "
        f"a 3-page double-spaced 12-point paper. Section drift: "
        f"{detail} Adjust before submitting — the editor's section "
        f"navigator shows live word counts per section."
    )
    nodes: list[dict[str, Any]] = [_paragraph(f"[[BOB: {summary}]]")]
    lines: list[str] = [f"[[BOB: {summary}]]", ""]
    return nodes, lines


def midpoint_to_editor(
    narratives: dict[str, str],
    *,
    word_validation: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    """
    Builds the editor content for a generated midpoint paper.

    Returns (content_json, content_text): a TipTap doc and its plain-text
    projection. The four sections become H1 headings with the generated
    prose as paragraphs. May 26 2026 — the trailing [[BOB: …]] section
    callouts have been removed; the Academic Writer's prose stands as
    the deliverable's interpretation. Bob edits for voice in-editor.

    word_validation (May 25 2026): when supplied AND the validation
    failed (any section or the total outside its target range), a
    [[BOB: WORD COUNT WARNING — …]] alert is prepended to the
    document so the user sees the drift at the top of the editor
    rather than discovering it at submission time. This is a
    transient quality-check alert, NOT a content placeholder — it
    disappears when the team fixes the word counts.
    """
    doc_content: list[dict] = []
    text_lines: list[str] = []
    warn_nodes, warn_lines = _word_count_warning_block(
        word_validation or {})
    doc_content.extend(warn_nodes)
    text_lines.extend(warn_lines)
    for heading, key, callout in _MIDPOINT_SECTIONS:
        nodes, lines = _section_blocks(
            heading, narratives.get(key, ""), callout)
        doc_content.extend(nodes)
        text_lines.extend(lines)
    content_json = {"type": "doc", "content": doc_content}
    content_text = "\n".join(text_lines).strip()
    return content_json, content_text


def executive_brief_to_editor(
    narratives: dict[str, str],
) -> tuple[dict[str, Any], str]:
    """
    Builds the editor content for a generated executive brief.

    Returns (content_json, content_text): a TipTap doc and its plain-text
    projection — the same pattern as midpoint_to_editor. The eight
    sections become H1 headings with the generated prose as paragraphs.
    May 26 2026 — the trailing [[BOB: …]] callouts that previously
    sat on the Executive Summary, Limitations and Final Recommendations
    sections have been removed; the Academic Writer's prose stands as
    each section's interpretation. Bob edits for voice in-editor.
    """
    doc_content: list[dict] = []
    text_lines: list[str] = []
    for heading, key, callout in _EXEC_BRIEF_SECTIONS:
        nodes, lines = _section_blocks(heading, narratives.get(key, ""),
                                       callout)
        doc_content.extend(nodes)
        text_lines.extend(lines)
    content_json = {"type": "doc", "content": doc_content}
    content_text = "\n".join(text_lines).strip()
    return content_json, content_text
