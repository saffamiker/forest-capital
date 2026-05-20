"""
tools/rehearsal.py

Parses a presentation_script editor draft's content_json (a TipTap doc)
into per-slide rehearsal sections — the data the GET /api/v1/documents/
rehearsal endpoint and the script editor's overlay both consume.

Section structure follows the script writer's generation contract:

  H2 node     "Slide N: Title text"          → slide_number + title
  H3 node     "Speaker: Name"                → speaker
  Paragraph   delivery prose                 → script_text (concatenated)
  Blockquote  "Transition: ..." line         → transition

A new H2 starts a new section; everything between H2 nodes attaches to
the current section. A draft that does not carry the H2/H3 convention
(an empty draft, or a freeform edit) falls back to a single section
with all paragraph text concatenated — the rehearsal still renders.
"""
from __future__ import annotations

import re
from typing import Any


# Pattern: "Slide N: Title". The N is required so a generic H2 is
# not mistaken for a slide heading.
_SLIDE_HEADING_RE = re.compile(r"^\s*Slide\s+(\d+)\s*:\s*(.*?)\s*$", re.I)

# Pattern: "Speaker: Name" — case-insensitive, the prefix is stripped.
_SPEAKER_RE = re.compile(r"^\s*Speaker\s*:\s*(.+?)\s*$", re.I)

# Pattern: "Transition: ..." inside a blockquote — the prefix is stripped.
_TRANSITION_RE = re.compile(r"^\s*Transition\s*:\s*(.+?)\s*$", re.I)


def _node_text(node: Any) -> str:
    """Flattens a TipTap node to plain text — concatenates every nested
    'text' content into a single string. Returns '' for an unknown shape."""
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return str(node.get("text") or "")
    pieces: list[str] = []
    for child in (node.get("content") or []):
        pieces.append(_node_text(child))
    return "".join(pieces)


def _word_count(text: str) -> int:
    return len([w for w in text.split() if w.strip()])


def _new_section() -> dict[str, Any]:
    return {
        "slide_number": None,
        "title":        "",
        "speaker":      None,
        "script_text":  "",
        "transition":   "",
        "word_count":   0,
    }


def parse_script_sections(content_json: Any) -> list[dict[str, Any]]:
    """
    Walks a TipTap doc and produces the per-slide section list. Always
    returns a list — never raises — so a malformed draft degrades to
    a single "everything" section rather than failing the endpoint.

    Section ordering follows the document order of H2 nodes; a draft
    without any H2 nodes returns a single section containing all the
    plain prose. Word count is the sum of script_text words across the
    section (drives the 150-wpm delivery-time estimate the endpoint
    returns).
    """
    if not isinstance(content_json, dict):
        return []
    nodes = content_json.get("content") or []
    if not isinstance(nodes, list):
        return []

    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    script_buf: list[str] = []

    def _flush() -> None:
        """Closes the current section by writing the buffered script text."""
        if current is None:
            return
        text = "\n\n".join(p.strip() for p in script_buf if p.strip())
        current["script_text"] = text
        current["word_count"] = _word_count(text)
        sections.append(current)

    for node in nodes:
        if not isinstance(node, dict):
            continue
        ntype = node.get("type")
        attrs = node.get("attrs") or {}
        text = _node_text(node)

        # New slide heading — H2 with "Slide N: Title" pattern.
        if ntype == "heading" and attrs.get("level") == 2:
            m = _SLIDE_HEADING_RE.match(text)
            if m:
                _flush()
                script_buf = []
                current = _new_section()
                current["slide_number"] = int(m.group(1))
                current["title"] = m.group(2).strip()
                continue
            # An H2 that does NOT match the slide pattern — treat it as
            # body content of the current section (a writer who inserted
            # their own heading mid-section should not lose their text).
            if current is None:
                current = _new_section()
                script_buf = []
            script_buf.append(text)
            continue

        # Speaker label — H3 with "Speaker: Name". Outside a section it
        # is ignored (a malformed document); inside, it attaches to the
        # current section.
        if ntype == "heading" and attrs.get("level") == 3:
            m = _SPEAKER_RE.match(text)
            if m and current is not None:
                current["speaker"] = m.group(1).strip()
                continue
            # An unmatched H3 falls through as body text.
            if current is None:
                current = _new_section()
                script_buf = []
            script_buf.append(text)
            continue

        # Transition — blockquote with "Transition: ..." text.
        if ntype == "blockquote":
            m = _TRANSITION_RE.match(text)
            if m and current is not None:
                current["transition"] = m.group(1).strip()
                continue
            # An untagged blockquote — fall through to the script text.
            if current is None:
                current = _new_section()
                script_buf = []
            script_buf.append(text)
            continue

        # Paragraphs and any other prose block — accumulate as script text.
        if text.strip():
            if current is None:
                current = _new_section()
                script_buf = []
            script_buf.append(text)

    _flush()
    return sections
