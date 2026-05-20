"""
tools/speaker_colours.py

The single source of truth for per-speaker colour assignment. The script
editor (frontend) and the script DOCX export (backend) both consume it,
so a presenter scanning the exported document sees the same colour the
editor showed them. The frontend mirrors this palette and resolver in
lib/speakerColours.ts — if either side changes, both must.

Palette is fixed at five colours and cycles for a sixth speaker (in
practice the team has three). The colours are distinct from the
agent-accent colours used elsewhere so a speaker's name never reads as
a system-level role.
"""
from __future__ import annotations

# Five distinct, accessible colours. Index 0 = navy (Speaker 1),
# index 4 = red (Speaker 5). A sixth speaker cycles back to navy.
SPEAKER_COLOURS: tuple[str, ...] = (
    "#1B2A4A",  # navy
    "#B45309",  # amber
    "#059669",  # green
    "#7C3AED",  # purple
    "#DC2626",  # red
)


def get_speaker_colour(speaker_name: str, all_speakers: list[str]) -> str:
    """
    Resolves a speaker's stable colour from the document's full
    first-seen speaker list. A name not present in all_speakers maps to
    Speaker 1 (navy) — defensive fallback, never raises.
    """
    if speaker_name in all_speakers:
        idx = all_speakers.index(speaker_name)
    else:
        idx = 0
    return SPEAKER_COLOURS[idx % len(SPEAKER_COLOURS)]
