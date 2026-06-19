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
# The brief — rebuilt May 30 2026 — has six sections matching the
# spec the rubric trap demanded: lead with the Part I Static
# Recommendation, frame the 2022 break as a structural interpretation,
# explicitly name the five human-judgment decisions, introduce the
# platform AFTER human judgment as the evidence base, summarise the
# evidence, then preview Part II as the logical consequence of Part I.
# June 18 2026 -- rewritten to the FNA 670 rubric's six required
# sections in rubric order. The previous structure (The Answer / Five
# Human Decisions / Part II preview) carried non-rubric content the
# panel flagged ("next steps rather than final recommendations"). The
# section keys now match build_executive_brief's headings and the
# _generate_brief_document spec list 1:1 so a draft opened in the
# editor mirrors the generated .docx exactly.
_EXEC_BRIEF_SECTIONS = [
    ("1. Executive Summary",                "executive_summary",       None),
    ("2. Methodology Overview",             "methodology",             None),
    ("3. Key Findings and Insights",        "key_findings",            None),
    ("4. Limitations and Risks",            "limitations",             None),
    ("5. Final Recommendations",            "final_recommendations",   None),
    ("6. Visuals to Demonstrate the Insights",
                                            "visuals",                 None),
]


# Analytical Appendix — the eight evidentiary sections. The headings,
# narrative keys, and ordering mirror tools.academic_docx.
# build_analytical_appendix exactly, so a draft opened in the editor
# matches the generated .docx 1:1.
_ANALYTICAL_APPENDIX_SECTIONS = [
    ("A. Data and Methodology",          "appendix_a", None),
    ("B. Full Strategy Performance",     "appendix_b", None),
    ("C. Statistical Tests",             "appendix_c", None),
    ("D. Bootstrap Confidence Intervals", "appendix_d", None),
    ("E. Factor Loadings",               "appendix_e", None),
    ("F. Crisis Window Performance",     "appendix_f", None),
    ("G. Transaction Cost Sensitivity",  "appendix_g", None),
    ("H. Validation Audit Summary",      "appendix_h", None),
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
     "grouped as static or dynamic; long-only, fully invested; "
     "monthly evaluation; rebalance triggers when any single strategy's "
     "blend weight crosses 2 percentage points (event-driven, not "
     "calendar-driven); Carhart four-factor attribution."),
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


# Bridge (June 8 2026) -- auto-embed per-slide charts so the editor opens
# with the contextually-appropriate platform chart already on every slide
# that has one. Keys are slide_number; values are chart_key strings the
# /api/v1/charts/available endpoint recognises. Slides absent from the
# map carry NO chart element -- the slide is bullets / table only.
DECK_SLIDE_CHART_KEYS: dict[int, str] = {
    # 1: stat card only -- the answer up front, no chart.
    # 2: three-column comparison card -- table-style, no chart.
    3: "rolling_sharpe",            # risk-adjusted comparison
    4: "rolling_correlation",       # the 2022 break
    5: "cumulative_returns",        # capital preservation history
    6: "oos_performance",           # walk-forward / OOS evidence
    7: "regime_signals",            # live regime read context
    # 8: play-by-play scorecard -- table, no chart.
    # 9: transition slide -- no chart.
    # 10: AI methodology two-column bullets -- no chart.
    11: "risk_return",              # efficient-frontier proxy + live blend
}


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    """Bridge (June 8 2026) -- render a slide's table_data as a proper
    markdown table (with the `|---|` separator row) so PresentationPreview
    can detect + render it as a styled HTML table instead of raw pipe
    text. The .pptx export still consumes the structured table_data
    directly via build_presentation_deck -- this helper exists for the
    editor's text element only."""
    if not headers:
        return ""
    header_row = "| " + " | ".join(headers) + " |"
    separator = "|" + "|".join("---" for _ in headers) + "|"
    body_rows = []
    for r in rows[:12]:
        cells = [str(c) for c in r]
        # Pad / truncate to the header column count so the table stays
        # rectangular even when an LLM emits a row with the wrong arity.
        if len(cells) < len(headers):
            cells = cells + [""] * (len(headers) - len(cells))
        elif len(cells) > len(headers):
            cells = cells[: len(headers)]
        body_rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([header_row, separator, *body_rows])


def _deck_slide_with_chart(
    slide_id: int, title: str, body: str, chart_key: str | None,
) -> dict[str, Any]:
    """Bridge (June 8 2026) -- emit a deck slide with a chart element
    pre-embedded on the right half when a chart is configured for this
    slide number. When chart_key is None, the slide is bullets / table
    only and the body element spans the full content area (no narrowing).

    Layout:
      - Title: 60,40 -> 900,120 (full width).
      - With chart: bullets 60,150 -> 460,470 (left 400px),
                    chart   500,150 -> 900,470 (right 400x320).
      - Without chart: bullets 60,150 -> 900,470 (full 840px).
    """
    elements: list[dict[str, Any]] = [
        {"id": "el_001", "type": "text",
         "x": 60, "y": 40, "width": 840, "height": 80,
         "content": title, "fontSize": 36, "fontWeight": "bold",
         "fontStyle": "normal", "color": "#1B2A4A", "locked": False},
    ]
    if chart_key:
        elements.append({
            "id": "el_002", "type": "text",
            "x": 60, "y": 150, "width": 400, "height": 320,
            "content": body, "fontSize": 16, "fontWeight": "normal",
            "fontStyle": "normal", "color": "#333333", "locked": False,
        })
        elements.append({
            "id": "el_003", "type": "chart",
            "x": 500, "y": 150, "width": 400, "height": 320,
            "chartKey": chart_key, "verified": False, "locked": False,
        })
    else:
        elements.append({
            "id": "el_002", "type": "text",
            "x": 60, "y": 150, "width": 840, "height": 320,
            "content": body, "fontSize": 18, "fontWeight": "normal",
            "fontStyle": "normal", "color": "#333333", "locked": False,
        })
    return {
        "id": slide_id,
        "title": title,
        "background": "#FFFFFF",
        "speaker_notes": "",
        "elements": elements,
    }


def deck_slides_to_editor(
    slides: Any,
) -> tuple[dict[str, Any], str]:
    """Editor content for the eleven-slide deck (bridges #98 / #100,
    June 7 2026 -- rebuilt from the six-slide narrative to an eleven-
    slide academic-presentation structure covering investment
    question, evidence, regime story, OOS validation, live demo
    setup, AI methodology, and the final recommendation -- see
    academic_deck.SLIDE_TITLES).

    Maps the AI slide JSON (slide_number / title / bullets /
    table_data / speaker_notes) onto the canvas element schema
    (migration 022) so a freshly generated deck opens in the Konva
    editor. Always emits the canonical DECK_SLIDE_COUNT slides (via
    academic_deck._normalize_slides) so a missing/unparseable JSON
    still produces a complete, openable deck. The AI speaker_notes
    carry into each slide; content_text concatenates every slide for
    Academic Review.

    Bridge (June 8 2026) updates:
      * table_data is now rendered as a proper markdown table (with a
        `|---|` separator row) -- PresentationPreview detects + renders
        the markdown as a styled HTML table instead of raw pipe text.
      * Slides with an entry in DECK_SLIDE_CHART_KEYS get a chart
        element pre-embedded on the right half. The deck opens in the
        editor with charts already in place; the user no longer has
        to add them via ChartPicker.
    """
    from tools.academic_deck import SLIDE_TITLES, _normalize_slides

    norm = _normalize_slides(slides)
    canvas_slides: list[dict[str, Any]] = []
    text_lines: list[str] = []
    for i, sl in enumerate(norm, start=1):
        title = sl.get("title") or SLIDE_TITLES[i - 1]
        bullets = sl.get("bullets") or []
        body = "\n".join(f"- {b}" for b in bullets)
        td = sl.get("table_data")
        if isinstance(td, dict) and td.get("rows"):
            headers = [str(h) for h in (td.get("headers") or [])]
            rows = [
                [str(c) for c in r]
                for r in (td.get("rows") or [])
                if isinstance(r, (list, tuple))
            ]
            if headers:
                md = _markdown_table(headers, rows)
                if md:
                    body = (body + "\n\n" + md) if body else md
        chart_key = DECK_SLIDE_CHART_KEYS.get(i)
        cs = _deck_slide_with_chart(i, title, body, chart_key)
        notes = str(sl.get("speaker_notes") or "").strip()
        if notes:
            cs["speaker_notes"] = notes
        canvas_slides.append(cs)
        text_lines.append(f"Slide {i}: {title}")
        if body:
            text_lines.append(body)
        text_lines.append("")
    return {"slides": canvas_slides}, "\n".join(text_lines).strip()


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


def analytical_appendix_to_editor(
    narratives: dict[str, str],
) -> tuple[dict[str, Any], str]:
    """
    Builds the editor content for a generated analytical appendix.

    Returns (content_json, content_text). The eight evidentiary
    sections become H1 headings with the Academic Writer's intro
    paragraph beneath each. Tables are NOT embedded — the in-editor
    view is the narrative skeleton; the .docx export carries the
    full data tables alongside it. Bob edits the prose in-editor and
    the regenerated .docx includes both his edits and the live
    cached tables.
    """
    doc_content: list[dict] = []
    text_lines: list[str] = []
    for heading, key, callout in _ANALYTICAL_APPENDIX_SECTIONS:
        nodes, lines = _section_blocks(heading, narratives.get(key, ""),
                                       callout)
        doc_content.extend(nodes)
        text_lines.extend(lines)
    content_json = {"type": "doc", "content": doc_content}
    content_text = "\n".join(text_lines).strip()
    return content_json, content_text
