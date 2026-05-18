"""
tools/editor_content.py

Converts a generated deliverable into the editor's content format —
the TipTap document JSON the rich-text editor loads (content_json) and
the plain-text projection the AI and Academic Review read (content_text).

Inline working aids survive the conversion as plain text inside
paragraphs: [[VERIFY: …]] / [[VERIFY CITATION: …]] markers as the
Academic Writer emitted them, and a [[BOB: …]] callout per section that
needs the author's own input. The editor frontend renders [[VERIFY]] as
an amber span and [[BOB]] as a full-width amber panel.
"""
from __future__ import annotations

from typing import Any

# The section-specific human-input callouts, embedded as [[BOB: …]]
# markers so the editor renders them as amber panels and section
# progress can track whether each has been resolved.
_ROLES_CALLOUT = (
    "BOB — PERSONALISE THIS SECTION: the draft above is pre-seeded with "
    "your actual platform activity data. Confirm the numbers, add "
    "specific examples of your analytical contributions, rewrite it in "
    "your own voice, and add anything the platform data does not capture "
    "(literature review, offline analysis, team discussions)."
)
_NEXT_STEPS_CALLOUT = (
    "BOB — REVIEW AND REFINE: edit the draft to reflect your own "
    "analytical priorities — what would you investigate next given these "
    "findings? That is what belongs here, not an engineering roadmap."
)


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
# trailing [[BOB]] callout or None).
_MIDPOINT_SECTIONS = [
    ("1. Data and Methodology", "methodology", None),
    ("2. Preliminary Results", "results", None),
    ("3. Roles and Division of Labor", "roles", _ROLES_CALLOUT),
    ("4. Next Steps and Open Questions", "next_steps", _NEXT_STEPS_CALLOUT),
]


# The 16 presentation-deck slides — (title, narratives key or None).
# Mirrors tools/academic_deck.build_presentation_deck's slide order so a
# deck draft opened in the editor matches the generated .pptx.
_DECK_SLIDES: list[tuple[str, str | None]] = [
    ("Title", None),
    ("Agenda", None),
    ("The Question", "thesis"),
    ("Data and Methodology", None),
    ("The 2022 Regime Break", "thesis"),
    ("Static Allocation Results", None),
    ("Dynamic Allocation Results", None),
    ("Cumulative Returns — Growth of $1", None),
    ("Risk-Return Profile", None),
    ("Factor Analysis", None),
    ("Drawdown Analysis", None),
    ("Sensitivity Analysis", None),
    ("Conclusions", "conclusions"),
    ("Recommendations", "recommendations"),
    ("How We Built This", "ai_leverage"),
    ("Questions and Discussion", None),
]


def deck_to_editor(
    narratives: dict[str, str],
) -> tuple[dict[str, Any], str]:
    """
    Builds the editor content for a generated presentation deck.

    Returns (content_json, content_text): content_json is
    {"slides": [...]} — one slide object per deck slide, the editor's
    slide-card format. Slide content is seeded from the generated
    narratives where one applies; speaker_notes start EMPTY so Molly
    writes her own (the slide-card Generate Talking Points helper offers
    a starting point). content_text concatenates every slide for
    Academic Review.
    """
    slides: list[dict[str, Any]] = []
    text_lines: list[str] = []
    for i, (title, key) in enumerate(_DECK_SLIDES, start=1):
        content = (narratives.get(key, "").strip() if key else "")
        slides.append({
            "id": i,
            "title": title,
            "content": content,
            "data_points": [],
            "speaker_notes": "",
            "verified": False,
            "notes_written": False,
        })
        text_lines.append(f"Slide {i}: {title}")
        if content:
            text_lines.append(content)
        text_lines.append("")
    content_json = {"slides": slides}
    content_text = "\n".join(text_lines).strip()
    return content_json, content_text


def midpoint_to_editor(
    narratives: dict[str, str],
) -> tuple[dict[str, Any], str]:
    """
    Builds the editor content for a generated midpoint paper.

    Returns (content_json, content_text): a TipTap doc and its plain-text
    projection. The four sections become H1 headings with the generated
    prose as paragraphs; the Roles and Next Steps sections each carry a
    trailing [[BOB: …]] callout.
    """
    doc_content: list[dict] = []
    text_lines: list[str] = []
    for heading, key, callout in _MIDPOINT_SECTIONS:
        nodes, lines = _section_blocks(
            heading, narratives.get(key, ""), callout)
        doc_content.extend(nodes)
        text_lines.extend(lines)
    content_json = {"type": "doc", "content": doc_content}
    content_text = "\n".join(text_lines).strip()
    return content_json, content_text
