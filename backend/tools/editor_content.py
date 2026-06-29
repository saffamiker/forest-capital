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

import structlog
from typing import Any


log = structlog.get_logger(__name__)


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


# PR-B (June 2026) -- the _MIDPOINT_SECTIONS list + midpoint_to_editor
# adapter were deleted alongside the midpoint endpoint retirement.
# Historical midpoint editor drafts still open via build_editor_docx
# (it reads from the TipTap content_json directly, not from this
# section list).


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
    # June 28 2026 -- Section 6 'Visuals' removed. The
    # brief_visuals agent was deleted June 26 2026 + the DOCX
    # builder's orphan heading block was removed June 28 2026.
    # _EXEC_BRIEF_SECTIONS now mirrors the live spec list so
    # executive_brief_to_editor doesn't render a phantom
    # Section 6 in the editor either.
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
            {"id": f"s{slide_id}_title", "type": "text",
             "x": 60, "y": 40, "width": 840, "height": 80,
             "content": title, "fontSize": 36, "fontWeight": "bold",
             "fontStyle": "normal", "color": "#1B2A4A", "locked": False},
            {"id": f"s{slide_id}_body", "type": "text",
             "x": 60, "y": 150, "width": 840, "height": 330,
             "content": content, "fontSize": 18, "fontWeight": "normal",
             "fontStyle": "normal", "color": "#333333", "locked": False},
        ],
    }


def deck_to_editor(
    narratives: dict[str, str],
    *,
    substitution_table: dict[str, str] | None = None,
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

    June 28 2026 (Phase 2 substitution-deferral audit) -- the
    deck's canvas-element content_json schema is structurally
    incompatible with the dual-mode token_value architecture
    (Konva positioned elements, not TipTap inline nodes). The
    upgrade pass does not walk canvas elements; the NodeView
    only renders inside TipTap. So the deck NEVER benefits from
    deferring substitution -- the only consequence under
    DEFER_SUBSTITUTION_TO_EXPORT=ON would be that the operator
    sees literal `{{OOS_SHARPE_BLEND}}` strings in the Konva
    canvas editor (bad UX, zero upside).

    Recommendation per audit: substitute at this boundary
    REGARDLESS of the deferral flag's state. The PPTX export
    pipeline's _substitute_pptx_text post-build sweep remains
    a no-op safety net (nothing left to substitute).
    """
    slides: list[dict[str, Any]] = []
    text_lines: list[str] = []
    for i, (title, key, seed) in enumerate(_DECK_SLIDES, start=1):
        # The generated narrative when this slide maps to one, otherwise
        # the static seed — never an empty body.
        narrative = narratives.get(key, "").strip() if key else ""
        content = narrative or seed
        # June 28 2026 -- substitute at this boundary so the
        # canvas editor never displays raw {{TOKEN}}. No-op when
        # no substitution_table OR no tokens to replace.
        if substitution_table:
            content = _apply_subs(content, substitution_table)
            title = _apply_subs(title, substitution_table)
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
#
# June 22 2026 -- shifted for the 12-slide deck (agenda inserted at
# position 2 + AI methodology / live demo flipped to slides 10/11). Each
# chart slide bumped by +1 due to the agenda; the closing-slide chart
# bumped by +1 for the same reason.
DECK_SLIDE_CHART_KEYS: dict[int, str] = {
    # June 27 2026 -- collapsed to 11 slides + remapped to match
    # Molly's reference deck. Slides 3 + 4 of the old 12-slide
    # deck (Three Strategies setup + The Numbers OOS verdict)
    # merged into one Investment Case split-panel slide; chart
    # slots downstream all shifted up by -1. After the collapse
    # only slide 4 (Why Static Allocation Failed in 2022)
    # carries a chart; the other slides become tables / cards /
    # verdict panels rendered via the specialized layouts that
    # land in PR B.
    #
    # Slide map below mirrors Molly's deck 1:1:
    #   1  Title                       -- title chrome (PR B)
    #   2  Agenda                      -- structural, no chart
    #   3  Investment Case             -- split panel (PR B)
    #   4  Why Static Failed 2022      -- cards left, chart right
    #   5  Capital Preservation        -- strategy table (PR B)
    #   6  Does It Hold Up OOS         -- window cards + IS/OOS
    #   7  Live Regime Signal          -- context cards + table
    #   8  What Model Gets Wrong       -- scorecard table (PR B)
    #   9  How We Used AI              -- card grid (PR B)
    #  10  Live Demo                   -- feature rows (PR B)
    #  11  The Answer                  -- verdict cards + alloc
    4: "rolling_correlation",
}


def _assert_chart_maps_match() -> None:
    """June 27 2026 -- guards the SLIDE_CHARTS (academic_deck.py,
    generation-time PPTX export) vs DECK_SLIDE_CHART_KEYS
    (editor canvas) invariant.

    Both maps must reference the SAME chart role on the SAME slide
    number; if they drift, the editor canvas would show one chart
    while the exported PPTX rendered a different one -- the symptom
    that prompted this reconciliation (slide 6 had two different
    chart roles before June 27 2026: 'strategy_comparison_oos_sharpe'
    in SLIDE_CHARTS vs 'cumulative_returns' in
    DECK_SLIDE_CHART_KEYS).

    Fires at module load; raises RuntimeError with a precise
    diff when the maps disagree so the regression is caught by
    every test that imports this module + by application boot.

    Both maps must agree on:
      * the SET of slide numbers carrying a chart
      * the chart role assigned to each slide
    """
    from tools.academic_deck import SLIDE_CHARTS  # local-import to
    # avoid the editor_content -> academic_deck circular at import-
    # time top-of-module load.

    canvas_keys = {
        k: v for k, v in DECK_SLIDE_CHART_KEYS.items()
        if v is not None
    }
    gen_keys = dict(SLIDE_CHARTS)
    if canvas_keys != gen_keys:
        only_in_canvas = sorted(
            set(canvas_keys) - set(gen_keys))
        only_in_gen = sorted(
            set(gen_keys) - set(canvas_keys))
        role_mismatches = sorted(
            (k, canvas_keys[k], gen_keys[k])
            for k in set(canvas_keys) & set(gen_keys)
            if canvas_keys[k] != gen_keys[k])
        raise RuntimeError(
            "Deck chart map drift detected -- SLIDE_CHARTS "
            "(generation-time PPTX export) and "
            "DECK_SLIDE_CHART_KEYS (editor canvas) must agree on "
            "every slide. Only-in-canvas: "
            f"{only_in_canvas}. Only-in-generation: "
            f"{only_in_gen}. Role mismatches "
            f"[(slide, canvas, generation)]: {role_mismatches}.")


_assert_chart_maps_match()


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


def _deck_slide_title_only(
    slide_id: int, title: str,
) -> dict[str, Any]:
    """June 27 2026 (PR B canvas mirror) -- title-chrome slide for
    slide 1. The PPTX export draws a navy header band + teal accent
    rule + subtitle + presenter line. The canvas can't paint shape
    backgrounds at the element level, so this produces three
    structurally-faithful text elements (title large + subtitle +
    presenter) on the standard white slide background. The exported
    PPTX gets the full title chrome via _render_title_slide."""
    elements: list[dict[str, Any]] = [
        {"id": f"s{slide_id}_title", "type": "text",
         "x": 60, "y": 180, "width": 840, "height": 140,
         "content": title,
         "fontSize": 36, "fontWeight": "bold",
         "fontStyle": "normal", "color": "#1E2761",
         "locked": False},
        {"id": f"s{slide_id}_subtitle", "type": "text",
         "x": 60, "y": 340, "width": 840, "height": 60,
         "content": "Forest Capital  /  McColl School of Business",
         "fontSize": 20, "fontWeight": "normal",
         "fontStyle": "normal", "color": "#1E2761",
         "locked": False},
        {"id": f"s{slide_id}_presenters", "type": "text",
         "x": 60, "y": 420, "width": 840, "height": 60,
         "content": "Group 1: Bob Thao, Michael Ruurds, Molly Murdock",
         "fontSize": 16, "fontWeight": "normal",
         "fontStyle": "normal", "color": "#1A1A2E",
         "locked": False},
    ]
    return {
        "id": slide_id,
        "title": title,
        "background": "#FFFFFF",
        "speaker_notes": "",
        "elements": elements,
    }


def _deck_slide_split_panel(
    slide_id: int, title: str, body: str,
    *, strategy_names: list[str] | None = None,
    table_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """June 27 2026 (PR B canvas mirror) -- split-panel slide for
    slide 3. The PPTX export draws three stat callout cards on the
    left + an OOS results table on the right. The canvas produces a
    structurally-equivalent layout: left text element with the
    strategy bullets + right table element with the OOS verdict
    table.

    When table_spec is missing, falls back to the default chartless
    body-only layout so a draft without an OOS verdict table still
    renders cleanly (the right half just stays empty)."""
    from tools.chart_config_defaults import build_table_config

    elements: list[dict[str, Any]] = [
        {"id": f"s{slide_id}_title", "type": "text",
         "x": 60, "y": 40, "width": 840, "height": 80,
         "content": title, "fontSize": 36, "fontWeight": "bold",
         "fontStyle": "normal", "color": "#1B2A4A", "locked": False},
        {"id": f"s{slide_id}_body", "type": "text",
         "x": 60, "y": 150,
         "width": 420 if table_spec else 840,
         "height": 380,
         "content": body, "fontSize": 16, "fontWeight": "normal",
         "fontStyle": "normal", "color": "#333333", "locked": False},
    ]
    if table_spec:
        table_type = str(
            table_spec.get("table_type", "performance"))
        rows = (
            table_spec.get("rows")
            or list(strategy_names or []))
        table_el: dict[str, Any] = {
            "id":     f"s{slide_id}_table",
            "type":   "table",
            "x":      500,
            "y":      150,
            "width":  400,
            "height": 30,  # placeholder; renderer auto-grows
            "locked": False,
            "table_config": build_table_config(
                table_type=table_type,
                strategy_names=rows,
                title=table_spec.get("title")),
        }
        if table_spec.get("columns"):
            table_el["table_config"]["columns"] = list(
                table_spec["columns"])
        elements.append(table_el)
    return {
        "id": slide_id,
        "title": title,
        "background": "#FFFFFF",
        "speaker_notes": "",
        "elements": elements,
    }


def _deck_slide_with_chart(
    slide_id: int, title: str, body: str, chart_key: str | None,
    *, strategy_names: list[str] | None = None,
    table_spec: dict[str, Any] | None = None,
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

    June 26 2026 (PR #441) -- element ids namespaced by slide_id
    ('s4_title', 's4_body', 's4_chart' etc.) so chart_pngs lookups
    don't collapse across slides.

    June 26 2026 (chart_config feature) -- two new optional kwargs:
      * strategy_names: list of cache strategy ids to prepopulate
        the chart_config's series list (one entry per strategy,
        all visible by default). Pass [] / None for unknown set.
      * table_spec: {headers, rows, table_type?, title?} to emit a
        NEW first-class type='table' canvas element on the slide
        below the body. Previously the same data was concatenated
        into the body text as a markdown table; the new element
        carries a table_config that the editor's Configure panel
        operates on and the PPTX exporter renders as a real
        <a:tbl> shape. Pass None to skip (preserves existing
        behaviour for bullet-only slides)."""
    from tools.chart_config_defaults import (
        build_chart_config_for_key, build_table_config,
    )

    elements: list[dict[str, Any]] = [
        {"id": f"s{slide_id}_title", "type": "text",
         "x": 60, "y": 40, "width": 840, "height": 80,
         "content": title, "fontSize": 36, "fontWeight": "bold",
         "fontStyle": "normal", "color": "#1B2A4A", "locked": False},
    ]
    if chart_key:
        elements.append({
            "id": f"s{slide_id}_body", "type": "text",
            "x": 60, "y": 150, "width": 400, "height": 320,
            "content": body, "fontSize": 16, "fontWeight": "normal",
            "fontStyle": "normal", "color": "#333333", "locked": False,
        })
        chart_el: dict[str, Any] = {
            "id": f"s{slide_id}_chart", "type": "chart",
            "x": 500, "y": 150, "width": 400, "height": 320,
            "chartKey": chart_key, "verified": False, "locked": False,
            # June 26 2026 -- chart_config prepopulated from the
            # renderer's hardcoded defaults so the editor's
            # Configure panel opens to the current visual state.
            # Absence preserves legacy behaviour; presence layers
            # on top of the renderer's fallback path.
            "chart_config": build_chart_config_for_key(
                chart_key, strategy_names),
        }
        elements.append(chart_el)
    else:
        elements.append({
            "id": f"s{slide_id}_body", "type": "text",
            "x": 60, "y": 150, "width": 840, "height": 320,
            "content": body, "fontSize": 18, "fontWeight": "normal",
            "fontStyle": "normal", "color": "#333333", "locked": False,
        })

    # June 26 2026 -- new first-class type='table' element. Emitted
    # below the body / chart area when the caller passes table_spec.
    # The element's table_config carries the rows/columns + style
    # the editor can edit; the PPTX exporter renders it as a real
    # <a:tbl> shape via the table_config's column set.
    if table_spec:
        table_type = str(table_spec.get("table_type", "performance"))
        rows = (
            table_spec.get("rows")
            or list(strategy_names or []))
        table_el: dict[str, Any] = {
            "id":     f"s{slide_id}_table",
            "type":   "table",
            "x":      60,
            "y":      490 if chart_key else 470,
            "width":  840 if not chart_key else 400,
            "height": 30,  # placeholder height; renderer auto-grows
            "locked": False,
            "table_config": build_table_config(
                table_type=table_type,
                strategy_names=rows,
                title=table_spec.get("title")),
        }
        # Allow caller to override the default column set.
        if table_spec.get("columns"):
            table_el["table_config"]["columns"] = list(
                table_spec["columns"])
        elements.append(table_el)

    return {
        "id": slide_id,
        "title": title,
        "background": "#FFFFFF",
        "speaker_notes": "",
        "elements": elements,
    }


def deck_slides_to_editor(
    slides: Any,
    *,
    strategy_names: list[str] | None = None,
    substitution_table: dict[str, str] | None = None,
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

    # June 28 2026 (Phase 2 substitution-deferral audit) -- per
    # the deck audit, the deck content_json schema (canvas
    # elements) is structurally incompatible with the dual-mode
    # token_value architecture. So the deck ALWAYS substitutes
    # at this boundary regardless of DEFER_SUBSTITUTION_TO_EXPORT
    # state -- otherwise the Konva canvas editor would display
    # literal {{OOS_SHARPE_BLEND}} strings in slide content with
    # no NodeView affordance to resolve them. The PPTX export's
    # _substitute_pptx_text post-build pass remains a no-op
    # safety net (nothing left to substitute after this).
    def _sub(s: str) -> str:
        if not substitution_table or not isinstance(s, str):
            return s
        return _apply_subs(s, substitution_table)

    norm = _normalize_slides(slides)
    canvas_slides: list[dict[str, Any]] = []
    text_lines: list[str] = []
    for i, sl in enumerate(norm, start=1):
        title = _sub(sl.get("title") or SLIDE_TITLES[i - 1])
        bullets = [_sub(b) for b in (sl.get("bullets") or [])]
        body = "\n".join(f"- {b}" for b in bullets)
        # June 29 2026 (deck-export fix) -- defensive body
        # fallback. When the AI deck JSON came back with no
        # bullets (parse failure, mid-generation truncation,
        # or a slide spec the LLM didn't fill), the canvas
        # body element was being persisted with content=""
        # which renders as an EMPTY textbox -> the editor +
        # PPTX export both show "titles only" for that slide.
        # Fall back to the AI's speaker_notes (the first 400
        # chars) so the slide carries something visible. The
        # speaker_notes always have content even when bullets
        # are empty (the LLM prompt requires them per slide).
        if not body:
            _notes_fallback = str(
                sl.get("speaker_notes") or "").strip()
            if _notes_fallback:
                body = _sub(_notes_fallback[:400])
                log.warning(
                    "deck_slide_body_empty_fallback_to_notes",
                    slide_id=i,
                    notes_chars=len(_notes_fallback))

        # June 26 2026 -- table_data now flows into a separate
        # first-class type='table' canvas element (via table_spec)
        # so the editor can edit row/column selection in a
        # Configure panel. The legacy markdown-pipe concatenation
        # into the body text is preserved as a fallback when no
        # structured headers + rows are present, so prose-only
        # slides + drafts that pre-date the structured table
        # contract still render the same way.
        table_spec: dict[str, Any] | None = None
        td = sl.get("table_data")
        if isinstance(td, dict) and td.get("rows"):
            headers = [_sub(str(h)) for h in (td.get("headers") or [])]
            rows = [
                [_sub(str(c)) for c in r]
                for r in (td.get("rows") or [])
                if isinstance(r, (list, tuple))
            ]
            # June 26 2026 -- normalise each row to the header
            # column count (pad short, truncate long). Ports the
            # row-shape contract previously enforced by
            # _markdown_table so a downstream LLM off-by-one doesn't
            # produce a ragged table in the new type='table' element.
            if headers and rows:
                rows = [
                    (r + [""] * (len(headers) - len(r)))[:len(headers)]
                    for r in rows
                ]
                table_spec = {
                    "headers":  headers,
                    "rows":     rows,
                    "title":    sl.get("table_title"),
                    "columns":  headers,
                    "table_type": (
                        sl.get("table_type") or "performance"),
                }
        chart_key = DECK_SLIDE_CHART_KEYS.get(i)
        # June 27 2026 (PR B canvas mirror) -- dispatch slides 1
        # and 3 to specialized canvas layouts that match the new
        # PPTX renderers (title chrome + split panel respectively).
        # Slides 4 / 6 / 7 / 8 / 9 / 10 / 11 continue through the
        # generic _deck_slide_with_chart path because the existing
        # text + optional chart + optional table element shape
        # already produces a structurally-equivalent canvas layout
        # for those slide types; the PPTX export gets the extra
        # styling (cards, badges, feature rows) via the dispatch
        # in academic_deck._render_content_slide.
        if i == 1:
            cs = _deck_slide_title_only(i, title)
        elif i == 3:
            cs = _deck_slide_split_panel(
                i, title, body,
                strategy_names=strategy_names,
                table_spec=table_spec)
        else:
            cs = _deck_slide_with_chart(
                i, title, body, chart_key,
                strategy_names=strategy_names,
                table_spec=table_spec)
        notes = _sub(str(sl.get("speaker_notes") or "").strip())
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



def executive_brief_to_editor(
    narratives: dict[str, str],
    *,
    substitution_table: dict[str, str] | None = None,
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

    June 28 2026 (Phase 2 substitution deferral): when
    substitution_table is supplied AND
    DEFER_SUBSTITUTION_TO_EXPORT is on at the caller, narratives
    carry raw {{TOKEN}} placeholders. content_json is built with
    the tokens INTACT (the dual-mode upgrade pass then converts
    them to token_value nodes; the in-editor render resolves
    them via the NodeView). content_text is built from the
    SUBSTITUTED projection so full-text search + word counts
    see the rendered values, matching what a human reader sees.
    When substitution_table is None (legacy generation OR flag
    OFF) both columns hold whatever narratives already carry.
    """
    doc_content: list[dict] = []
    text_lines: list[str] = []
    text_lines_substituted: list[str] = []
    for heading, key, callout in _EXEC_BRIEF_SECTIONS:
        narrative = narratives.get(key, "")
        nodes, lines = _section_blocks(heading, narrative, callout)
        doc_content.extend(nodes)
        text_lines.extend(lines)
        # June 28 2026 -- parallel substituted projection for
        # content_text only. The narrative text inside each
        # section block is substituted; the heading + callout
        # markers are token-free already so a straight string
        # replace on the raw narrative produces the right
        # parallel `lines` shape.
        if substitution_table:
            sub_narrative = _apply_subs(narrative, substitution_table)
            _, sub_lines = _section_blocks(
                heading, sub_narrative, callout)
            text_lines_substituted.extend(sub_lines)
    content_json = {"type": "doc", "content": doc_content}
    if substitution_table and text_lines_substituted:
        content_text = (
            "\n".join(text_lines_substituted).strip())
    else:
        content_text = "\n".join(text_lines).strip()
    return content_json, content_text


def analytical_appendix_to_editor(
    narratives: dict[str, str],
    *,
    substitution_table: dict[str, str] | None = None,
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

    June 28 2026 (Phase 2 substitution deferral): same contract
    as executive_brief_to_editor -- content_json preserves
    {{TOKEN}} placeholders when substitution_table is supplied;
    content_text is derived from the substituted projection.
    """
    doc_content: list[dict] = []
    text_lines: list[str] = []
    text_lines_substituted: list[str] = []
    for heading, key, callout in _ANALYTICAL_APPENDIX_SECTIONS:
        narrative = narratives.get(key, "")
        nodes, lines = _section_blocks(heading, narrative, callout)
        doc_content.extend(nodes)
        text_lines.extend(lines)
        if substitution_table:
            sub_narrative = _apply_subs(narrative, substitution_table)
            _, sub_lines = _section_blocks(
                heading, sub_narrative, callout)
            text_lines_substituted.extend(sub_lines)
    content_json = {"type": "doc", "content": doc_content}
    if substitution_table and text_lines_substituted:
        content_text = (
            "\n".join(text_lines_substituted).strip())
    else:
        content_text = "\n".join(text_lines).strip()
    return content_json, content_text


def _apply_subs(text: str, table: dict[str, str]) -> str:
    """June 28 2026 -- thin wrapper around
    numeric_substitution.apply_substitutions for use by the
    editor-content builders. Returns text with {{TOKEN}}
    replaced by table values. Fail-open: any error returns the
    input unchanged (the caller falls through to the original
    narrative for content_text -- both states are correct, just
    one carries tokens)."""
    if not text or not table:
        return text
    try:
        from tools.numeric_substitution import apply_substitutions
        substituted, _ = apply_substitutions(text, table)
        return substituted
    except Exception:
        return text
