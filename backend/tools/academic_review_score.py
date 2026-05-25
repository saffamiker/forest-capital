"""
tools/academic_review_score.py — May 25 2026.

Maps the Academic Review arbiter's section ratings to a single
numerical score on a 0-10 scale, and surfaces an overall categorical
rating. Used by the auto-fire on document generation to populate the
editor-header indicator and the midpoint advisory banner.

The arbiter writes five `### N. Section Heading\\n**Rating:**
Strong | Developing | Needs Work` blocks. We parse all five, map each
to a numeric point on a deliberately conservative scale, and average.

Score map:
  Strong         8.5    a clearly-credible section
  Developing     6.0    visibly weaker but not failing — the midpoint
                        advisory threshold sits here for that reason
  Needs Work     3.5    blocking concern in this section
  unknown        skipped (e.g. malformed or `Incomplete`)

The 6.0 threshold the editor uses is anchored to "all Developing"
on this scale. A paper whose sections all rate Developing scores
exactly 6.0 — i.e. no Needs Work anywhere — and is not advisory.
A single Needs Work pulls the average below 6.0 and trips the
banner. This keeps the threshold's meaning legible: "at least one
section that needs work".
"""
from __future__ import annotations

import re
from typing import Any

# The standard five sections — the arbiter is instructed to emit them
# in this order and the evaluator scores presence/order. The keys are
# stable identifiers; the labels render in the UI when surfaced.
_SECTION_KEYS = (
    ("data_sufficiency",  "Data Sufficiency and Methodology"),
    ("requirements",      "Requirements and Rubric Alignment"),
    ("deliverable",       "Deliverable Quality"),
    ("investigation",     "Priority Areas for Further Investigation"),
    ("readiness",         "Overall Academic Readiness"),
)

_RATING_POINTS: dict[str, float] = {
    "Strong":     8.5,
    "Developing": 6.0,
    # The standard rubric uses "Needs Work"; we normalise across the
    # script rubric's variants below.
    "Needs Work": 3.5,
}

# Bands the frontend reads for legend/colour mapping. The midpoint
# advisory banner fires below 6.0 — anchored to "all Developing".
ADVISORY_THRESHOLD: float = 6.0


def _normalise_rating(raw: str | None) -> str | None:
    """Trim, title-case-ish normalise the rating token. The arbiter's
    output sometimes drifts to 'Needs work' lowercase or to 'Needs-Work'
    hyphenated — we accept those as the same rating."""
    if not raw:
        return None
    cleaned = re.sub(r"[-_]+", " ", raw).strip()
    lower = cleaned.lower()
    if lower == "strong":
        return "Strong"
    if lower == "developing":
        return "Developing"
    if lower in ("needs work", "needswork"):
        return "Needs Work"
    return None


def _parse_section_ratings(verdict: str | None) -> dict[str, str]:
    """Returns {section_key: rating_string} for every section that
    has a recognisable rating. Missing or malformed sections are
    silently skipped — the score averages over whatever is present.

    Walks the verdict header-by-header so each section's rating
    search is bounded to that section's body — no risk of section
    N's regex greedily matching section N+1's rating. The header
    line shape is `### N. Heading` or `## N. Heading`; the rating
    inside the body is `**Rating:** Strong` or `Rating: Strong`
    (case-insensitive; bold optional; hyphens / lowercase accepted
    in the rating token).
    """
    if not verdict:
        return {}

    # First pass — find every numbered section heading and the slice
    # of text from this heading to the next. Bounding the search
    # window per section keeps a missing rating in one section from
    # bleeding into the next section's match.
    heading_re = re.compile(
        r"^#{1,3}\s*(\d+)\.[^\n]*$", re.MULTILINE)
    matches = list(heading_re.finditer(verdict))
    bounds: dict[int, tuple[int, int]] = {}
    for idx, m in enumerate(matches):
        try:
            num = int(m.group(1))
        except ValueError:
            continue
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(verdict)
        bounds[num] = (m.end(), end)

    rating_re = re.compile(
        r"(?:\*\*)?\s*Rating\s*:\s*(?:\*\*)?\s*"
        r"(Strong|Developing|Needs[\s-]?Work|Incomplete)",
        re.IGNORECASE,
    )

    out: dict[str, str] = {}
    for i, (key, _label) in enumerate(_SECTION_KEYS, start=1):
        slice_bounds = bounds.get(i)
        if slice_bounds is None:
            continue
        start, end = slice_bounds
        body = verdict[start:end]
        m = rating_re.search(body)
        if not m:
            continue
        norm = _normalise_rating(m.group(1))
        if norm is not None:
            out[key] = norm
    return out


def compute_review_score(verdict: str | None) -> dict[str, Any]:
    """Parses the arbiter verdict and returns:
      {
        score:           7.2     # 0-10, averaged over rated sections
                                 # (None when nothing parseable)
        rating:          "Developing"  # section-5 overall, when present
        section_ratings: {section_key: rating_string}
        advisory:        True    # score is below 6.0 — the midpoint
                                 # advisory banner threshold
        sections_rated:  4       # how many of 5 the parser found
      }

    Always returns the shape — the caller treats `score is None` as
    "could not derive a score" (the arbiter returned malformed
    markdown) rather than as a failing paper.
    """
    section_ratings = _parse_section_ratings(verdict)
    points = [
        _RATING_POINTS[v]
        for v in section_ratings.values()
        if v in _RATING_POINTS
    ]
    score: float | None
    if points:
        score = round(sum(points) / len(points), 1)
    else:
        score = None
    overall = section_ratings.get("readiness")
    advisory = score is not None and score < ADVISORY_THRESHOLD
    return {
        "score": score,
        "rating": overall,
        "section_ratings": section_ratings,
        "advisory": advisory,
        "sections_rated": len(section_ratings),
    }
