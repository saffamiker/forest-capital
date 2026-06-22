"""tools/brief_grounding.py -- the brief-as-anchor architecture.

The executive brief is the anchor document for the final FNA 670
submission. The presentation deck and analytical appendix MUST
align with it: same numeric values (already guaranteed by the
shared substitution table), AND same central argument, same
honest-acknowledgment framing, same recommendation language.

Before this module, the deck and appendix generators read raw
cache data and independently produced their own story plans.
Numbers matched (substitution table) but framing could drift.
This module is the shared infrastructure for grounding both
secondary documents in the brief's finalized text.

CONTRACT

  brief_content_hash(content_text) -> str
    SHA256 prefix of the brief's content_text, used to extend
    the (data_hash, document_type) cache key for deck and
    appendix story plans. A brief regeneration changes this
    hash, which auto-invalidates the deck + appendix story
    plans on their next generation.

  get_brief_for_grounding(email) -> dict | None
    Fetches the user's CURRENT executive_brief editor draft.
    Returns {"content_text": str, "content_hash": str} or None
    when no brief draft exists. The gate at the top of the deck
    and appendix generators uses None as the signal to raise
    HTTPException(409, "Generate the executive brief first").

  brief_section_excerpt(brief_text, section_name) -> str
    Returns just the body of one brief section. Used by per-
    slide and per-section writers in the deck and appendix --
    threading the FULL brief into every writer would bloat
    input tokens. The Pass-1 Opus arbiter (the structural
    layer) sees the full brief; per-slide / per-section
    writers see only the relevant excerpt.

  SLIDE_TO_BRIEF_SECTION
    Mapping of deck slide_number -> brief canonical section
    name. Slides that have no brief counterpart (slide 9 live
    demo, slide 10 AI methodology) map to None and the
    per-slide writer receives no excerpt.

  APPENDIX_TO_BRIEF_SECTION
    Mapping of appendix section_key -> brief canonical section
    name. Appendix sections without a clear brief counterpart
    (full 10-strategy performance table, factor loadings) map
    to None.

FAIL-OPEN POSTURE

Every helper is wrapped: a missing draft, a malformed brief,
a section splitter that returns nothing -- each path returns
None / "" rather than raising. The grounding is enrichment, not
a precondition for individual writes (the gate is the only
precondition; once past the gate, missing-excerpt cases
degrade gracefully).
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# Slides explicitly EXCLUDED from brief excerpt threading. Named
# constant rather than an implicit None lookup so the exclusion
# is grep-able and any future PR that accidentally re-includes a
# slide breaks a pinned test. Slide 9 is the LIVE_DEMO_SEQUENCE
# (platform UX walkthrough); slide 10 is AI methodology /
# platform meta-narrative. Neither has a brief counterpart and
# threading brief text into either would dilute their function.
SLIDES_EXCLUDED_FROM_BRIEF_GROUNDING: frozenset[int] = frozenset({9, 10})


# Deck slide -> brief section, for slides that DO map to a brief
# section. Slides in SLIDES_EXCLUDED_FROM_BRIEF_GROUNDING above
# are NOT listed here; the dispatcher checks the exclusion set
# first before consulting this map.
SLIDE_TO_BRIEF_SECTION: dict[int, str] = {
    1: "Executive Summary",         # opener
    2: "Methodology Overview",      # three-strategy frame setup
    3: "Methodology Overview",      # HMM + OOS window
    4: "Key Findings and Insights",  # cumulative return
    5: "Key Findings and Insights",  # regime correlation break
    6: "Key Findings and Insights",  # play-by-play
    7: "Key Findings and Insights",  # post-2022 OOS Sharpe
    8: "Limitations and Risks",     # honest acknowledgments
    # 9 -- EXCLUDED (LIVE_DEMO_SEQUENCE; see exclusion set above)
    # 10 -- EXCLUDED (AI methodology; see exclusion set above)
    11: "Final Recommendations",    # closing investable conclusion
}


def brief_section_for_slide(slide_number: int) -> str | None:
    """Returns the brief section name to thread into the slide's
    per-slide writer, or None when the slide is explicitly
    excluded from brief grounding.

    This helper is the SINGLE dispatch point for slide-to-brief
    mapping. Per-slide writers MUST call this rather than
    looking up SLIDE_TO_BRIEF_SECTION directly so the exclusion
    set is honoured consistently. A future caller that bypasses
    this helper and reads the map directly would re-include the
    excluded slides -- the pinned test
    test_excluded_slides_return_none_from_dispatcher catches
    that regression class.
    """
    if slide_number in SLIDES_EXCLUDED_FROM_BRIEF_GROUNDING:
        return None
    return SLIDE_TO_BRIEF_SECTION.get(slide_number)


# Appendix section_key -> brief canonical section. The appendix
# is scope-wider than the brief (covers all 10 strategies vs.
# the brief's three-strategy lens), so several appendix sections
# have no direct brief counterpart -- they map to None and the
# writer proceeds without an excerpt.
APPENDIX_TO_BRIEF_SECTION: dict[str, str | None] = {
    # Data + Methodology aligns with brief's Methodology Overview.
    "appendix_data_sources": "Methodology Overview",
    "appendix_methodology": "Methodology Overview",
    # Portfolio construction is appendix-specific (full 10-strategy
    # detail; the brief's Methodology only covers the three-
    # strategy lens). No brief alignment.
    "appendix_portfolio_construction": None,
    # Calculations + Models -- appendix-specific full-strategy
    # detail.
    "appendix_calculations": None,
    # Performance + Visualizations partially aligns with brief
    # Key Findings (the three-strategy comparison) but covers
    # broader ground. Pass brief Key Findings as alignment
    # context so the three-strategy framing remains consistent.
    "appendix_performance": "Key Findings and Insights",
    # Sensitivity + Robustness aligns with brief's Limitations
    # (transaction cost sensitivity, sample-size caveats).
    "appendix_sensitivity": "Limitations and Risks",
}


# The canonical brief section headings the splitter recognises.
# Mirrors _BRIEF_SECTION_WORD_TARGETS in tools/document_audit.py
# but kept local so this module doesn't take a hard dependency
# on the audit module's internals.
_BRIEF_SECTION_NAMES: tuple[str, ...] = (
    "Executive Summary",
    "Methodology Overview",
    "Key Findings and Insights",
    "Limitations and Risks",
    "Final Recommendations",
    "Visuals",
)


def brief_content_hash(content_text: str | None) -> str:
    """SHA256 prefix of the brief content_text. Used to extend
    the deck + appendix story-plan cache key from (data_hash,
    document_type) to (data_hash|brief_content_hash,
    document_type).

    Returns the empty string when content_text is None / empty
    -- the caller treats empty as "no brief grounding available"
    and uses the data_hash alone as the cache key (degrades to
    pre-grounding behaviour).
    """
    if not content_text:
        return ""
    digest = hashlib.sha256(
        content_text.encode("utf-8", errors="replace")).hexdigest()
    return digest[:16]


def cache_key_with_brief(
    data_hash: str, brief_hash: str | None,
) -> str:
    """Combine data_hash + brief_hash into the extended cache
    key string. Returns data_hash alone when brief_hash is
    empty / None -- preserves the legacy cache-hit path for any
    callers that haven't (yet) wired brief grounding.

    The pipe separator is illegal in SHA256 hex output so the
    two halves never ambiguously merge."""
    if not brief_hash:
        return data_hash
    return f"{data_hash}|{brief_hash}"


async def get_brief_for_grounding(
    email: str,
) -> dict[str, Any] | None:
    """Fetches the user's current executive_brief editor draft.
    Returns {content_text, content_hash, draft_id} when a non-
    empty draft exists, None otherwise.

    None is the signal the deck + appendix gates use to raise
    HTTPException(409, "Generate the executive brief first").
    Fail-open at the read level: a DB error returns None and the
    caller treats it as no brief available."""
    try:
        from tools.editor_drafts import get_current_draft
        draft = await get_current_draft(email, "executive_brief")
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "brief_grounding_draft_read_failed", error=str(exc))
        return None
    if not draft:
        return None
    content_text = (draft.get("content_text") or "").strip()
    if not content_text:
        return None
    return {
        "content_text": content_text,
        "content_hash": brief_content_hash(content_text),
        "draft_id": draft.get("id"),
    }


def _split_brief_by_section(text: str) -> dict[str, str]:
    """Match each canonical brief section heading case-
    insensitively and extract the body up to the next
    recognised heading. Tolerates the same heading shapes the
    brief's audit splitter tolerates:
      - markdown:        '## Methodology'
      - numbered:        '1. Methodology' / '2. Methodology Overview'
      - plain heading:   'Methodology' on a line of its own
    """
    if not text:
        return {}
    name_alt = "|".join(sorted(
        (re.escape(n) for n in _BRIEF_SECTION_NAMES),
        key=len, reverse=True))
    pattern = (
        r"(?:^|\n)\s*"
        r"(?:#+\s*)?"
        r"(?:\d+\.?\s*)?"
        r"(" + name_alt + r")\b[^\n]*\n")
    out: dict[str, str] = {}
    matches = list(re.finditer(pattern, text, re.IGNORECASE))
    for i, m in enumerate(matches):
        matched_heading = m.group(1)
        canonical = next(
            (n for n in _BRIEF_SECTION_NAMES
             if n.lower() == matched_heading.lower()),
            None)
        if canonical is None:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out[canonical] = text[start:end].strip()
    return out


def brief_section_excerpt(
    brief_text: str | None, section_name: str | None,
) -> str:
    """Returns the body of one brief section. Used by per-slide
    and per-section writers in the deck and appendix to align
    framing without dragging the entire brief into every Sonnet
    call. Returns the empty string when section_name is None
    (slide 9, slide 10, certain appendix sections), when
    brief_text is empty, or when the splitter doesn't find the
    section heading. Fail-open: the writer continues without
    the excerpt; the deeper Pass-1 grounding (full brief in the
    arbiter call) is the structural alignment, the per-writer
    excerpt is reinforcement."""
    if not brief_text or not section_name:
        return ""
    try:
        sections = _split_brief_by_section(brief_text)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "brief_grounding_section_split_failed", error=str(exc))
        return ""
    return sections.get(section_name, "")


def brief_grounding_block(
    brief_text: str | None,
) -> str:
    """Renders the full brief as a prompt block suitable for
    threading into a Pass-1 Opus call. The block carries a
    clear header so the model recognises the grounding context;
    when brief_text is empty, returns the empty string (the
    caller's prompt assembly skips the empty block via the
    standard `+ block` no-op pattern).

    Used by:
      - generate_deck_story_plan(brief_text=...) for the deck
        Pass-1 arbiter
      - generate_appendix_section_plan(brief_text=...) for the
        appendix's lightweight section-plan pass

    Conservative budget -- the full brief is typically 2-3k
    words ~= 3-4k tokens. Added once per Pass-1 call (NOT once
    per slide / per section), so the input-context cost is
    bounded."""
    if not brief_text:
        return ""
    return (
        "\n\nBRIEF GROUNDING CONTEXT (this document must align "
        "with the brief's framing):\n"
        "The executive brief is the anchor document for this "
        "submission. The brief's central argument, honest "
        "acknowledgments, and recommendation framing are LOCKED "
        "by the time this generation runs. Your job is to "
        "amplify and visualize what the brief argues, NOT to "
        "re-derive your own conclusions. Match the brief's "
        "framing, terminology, and recommendation language "
        "exactly where the section topic overlaps.\n\n"
        "=== FINALIZED EXECUTIVE BRIEF ===\n"
        + brief_text.strip()
        + "\n=== END OF BRIEF ===\n"
    )


def brief_section_block(
    section_excerpt: str, section_name: str | None,
) -> str:
    """Renders a per-section brief excerpt as a prompt block for
    per-slide / per-section writers. Returns the empty string
    when there's no excerpt to inject (slide 9, slide 10,
    appendix sections without a brief counterpart, or a missing
    section in the splitter output). Fail-open: an empty block
    leaves the writer's prompt unchanged."""
    if not section_excerpt or not section_name:
        return ""
    return (
        "\n\nBRIEF ALIGNMENT EXCERPT (your section should "
        f"amplify the brief's {section_name} section):\n"
        "=== EXCERPT ===\n"
        + section_excerpt.strip()
        + "\n=== END EXCERPT ===\n"
        "Match the framing and recommendation language in this "
        "excerpt where the section topic overlaps. Do NOT "
        "contradict the brief's conclusions; do NOT introduce "
        "claims absent from the brief.\n"
    )
