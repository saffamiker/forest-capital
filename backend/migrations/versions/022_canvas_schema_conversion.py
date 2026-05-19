"""Convert presentation_deck editor_drafts to the canvas element schema.

The presentation-deck editor moves from a fixed slide-card layout to a
free-form Konva canvas. A presentation_deck draft's content_json changes
from the slide-card shape —

  {slides: [{id, title, content, data_points, speaker_notes,
             verified, notes_written}]}

— to the canvas shape, where each slide carries a background, top-level
speaker_notes, and an `elements` array of positioned text/chart elements
on a 960x540 (16:9) canvas:

  {slides: [{id, title, background, speaker_notes,
             elements: [{id, type, x, y, width, height, ...}]}]}

This is a DATA migration — no schema change. Only editor_drafts rows
with document_type='presentation_deck' are touched. The conversion is
idempotent (a slide already carrying an `elements` array is left
unchanged) and the downgrade restores the slide-card shape.

Field mapping (slide-card → canvas elements):
  title        → text element  x:60  y:40   w:840 h:80  36pt bold #1B2A4A
  content      → text element  x:60  y:140  w:500 h:280 18pt      #333333
  data_points  → text element  x:60  y:440  w:840 h:60  14pt      #B45309
                 (only when non-empty)
  speaker_notes→ slide-level speaker_notes (unchanged)
  verified, notes_written → dropped (per-element `verified` replaces them
                 on chart elements). The downgrade restores the keys with
                 verified=False and notes_written = (speaker_notes set).

Revision ID: 022
Revises: 021
Create Date: 2026-05-19
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from alembic import op
import sqlalchemy as sa

revision: str = "022"
down_revision: str | None = "021"
branch_labels: str | None = None
depends_on: str | None = None


def _slide_to_canvas(slide: dict[str, Any]) -> dict[str, Any]:
    """Slide-card slide → canvas slide. Idempotent — a slide already in
    canvas shape (has an `elements` list) is returned unchanged."""
    if isinstance(slide.get("elements"), list):
        return slide

    title = str(slide.get("title", "") or "")
    content = str(slide.get("content", "") or "")
    data_points = slide.get("data_points") or []

    elements: list[dict[str, Any]] = [
        {"id": "el_001", "type": "text",
         "x": 60, "y": 40, "width": 840, "height": 80,
         "content": title, "fontSize": 36, "fontWeight": "bold",
         "color": "#1B2A4A", "locked": False},
        {"id": "el_002", "type": "text",
         "x": 60, "y": 140, "width": 500, "height": 280,
         "content": content, "fontSize": 18, "fontWeight": "normal",
         "color": "#333333", "locked": False},
    ]
    if data_points:
        elements.append({
            "id": "el_003", "type": "text",
            "x": 60, "y": 440, "width": 840, "height": 60,
            "content": "\n".join(str(d) for d in data_points),
            "fontSize": 14, "fontWeight": "normal",
            "color": "#B45309", "locked": False})

    return {
        "id": slide.get("id"),
        "title": title,
        "background": "#FFFFFF",
        "speaker_notes": slide.get("speaker_notes", "") or "",
        "elements": elements,
    }


def _slide_to_cards(slide: dict[str, Any]) -> dict[str, Any]:
    """Canvas slide → slide-card slide (downgrade). A slide already in
    card shape (no `elements` list) is returned unchanged."""
    elements = slide.get("elements")
    if not isinstance(elements, list):
        return slide

    def _text_at(y: int) -> str:
        for el in elements:
            if (isinstance(el, dict) and el.get("type") == "text"
                    and el.get("y") == y):
                return str(el.get("content", "") or "")
        return ""

    title = str(slide.get("title", "") or "") or _text_at(40)
    data_text = _text_at(440)
    notes = str(slide.get("speaker_notes", "") or "")
    return {
        "id": slide.get("id"),
        "title": title,
        "content": _text_at(140),
        "data_points": data_text.split("\n") if data_text else [],
        "speaker_notes": notes,
        "verified": False,
        "notes_written": bool(notes.strip()),
    }


def _convert(transform: Callable[[dict], dict]) -> None:
    """Applies `transform` to every slide of every presentation_deck
    editor_drafts row. Robust to content_json arriving as a JSON string
    (asyncpg's default jsonb decoding) or an already-parsed dict."""
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, content_json FROM editor_drafts "
        "WHERE document_type = 'presentation_deck'")).fetchall()
    for row_id, content_json in rows:
        if isinstance(content_json, str):
            try:
                content_json = json.loads(content_json)
            except (ValueError, TypeError):
                continue
        if not isinstance(content_json, dict):
            continue
        slides = content_json.get("slides")
        if not isinstance(slides, list):
            continue
        new_json = {
            **content_json,
            "slides": [transform(s) for s in slides
                       if isinstance(s, dict)],
        }
        bind.execute(
            sa.text("UPDATE editor_drafts "
                    "SET content_json = CAST(:cj AS jsonb), "
                    "updated_at = now() WHERE id = :id"),
            {"cj": json.dumps(new_json), "id": row_id})


def upgrade() -> None:
    _convert(_slide_to_canvas)

    # Changelog contract — every migration inserts at least one row.
    changelog = sa.table(
        "changelog",
        sa.column("version", sa.Integer),
        sa.column("released_at", sa.TIMESTAMP(timezone=True)),
        sa.column("title", sa.String),
        sa.column("description", sa.Text),
        sa.column("academic_rationale", sa.Text),
        sa.column("tour_step_id", sa.String),
    )
    op.bulk_insert(changelog, [{
        "version": 41,
        "released_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        "title": "Canvas Presentation Editor",
        "description": (
            "The presentation-deck editor moves from fixed slide cards to "
            "a free-form 960x540 canvas — drag, resize and style text, and "
            "drop in live charts rendered straight from the Analytics "
            "page."
        ),
        "academic_rationale": (
            "A canvas editor lets the team lay the final presentation out "
            "exactly as it will be shown, with live platform charts "
            "embedded directly — so the deck the panel sees is built from "
            "the same independently verified data as the analysis."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    _convert(_slide_to_cards)
    op.execute("DELETE FROM changelog WHERE version = 41")
