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

# The brief rubric's six sections (PR — academic review brief-specific
# rubric). Distinct keys so a brief verdict and a midpoint verdict
# can sit side-by-side in storage without collisions, and so the
# weighted-average aggregator can map weights to keys positionally.
_BRIEF_SECTION_KEYS = (
    ("executive_summary",     "Executive Summary"),
    ("methodology",           "Methodology Overview"),
    ("key_findings",          "Key Findings and Insights"),
    ("limitations",           "Limitations and Risks"),
    ("final_recommendations", "Final Recommendations"),
    ("visuals",               "Visuals"),
)

# Per-mode section weights. The midpoint rubric averages equally over
# whatever sections parse (legacy default — backward compatible). The
# brief rubric weights its six sections to match the brief's rubric
# (Executive Summary 15%, Methodology 20%, Key Findings 25%,
# Limitations 15%, Final Recommendations 20%, Visuals 5%) so a weak
# Visuals section doesn't tank the score and Key Findings carries it.
_BRIEF_WEIGHTS: tuple[float, ...] = (0.15, 0.20, 0.25, 0.15, 0.20, 0.05)

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


def _parse_section_ratings(
    verdict: str | None,
    section_keys: tuple[tuple[str, str], ...] = _SECTION_KEYS,
) -> dict[str, str]:
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

    section_keys — the (stable_id, label) tuple sequence to map
    section numbers 1..N to. Defaults to the standard five midpoint
    sections; brief mode passes the six brief sections.
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
    for i, (key, _label) in enumerate(section_keys, start=1):
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


def compute_review_score(
    verdict: str | None,
    mode: str = "midpoint",
) -> dict[str, Any]:
    """Parses the arbiter verdict and returns:
      {
        score:           7.2     # 0-10, averaged over rated sections
                                 # (None when nothing parseable)
        rating:          "Developing"  # overall rating (last section
                                 # in the rubric — section 5 for the
                                 # midpoint rubric, section 5 "Final
                                 # Recommendations" for the brief
                                 # rubric, when present)
        section_ratings: {section_key: rating_string}
        advisory:        True    # score is below 6.0 — the midpoint
                                 # advisory banner threshold
        sections_rated:  4       # how many of 5/6 the parser found
        parse_error:     False   # True only when the verdict carried
                                 # non-trivial text but zero sections
                                 # parsed — distinguishes "arbiter
                                 # response broken" from "partial
                                 # truncation" downstream (bridge #82).
      }

    mode — "midpoint" (default) uses the five-section midpoint rubric
    keys and equal-weighted averaging (legacy behaviour, every
    existing caller continues to work). "brief_review" uses the six-
    section brief rubric keys and WEIGHTED averaging by
    _BRIEF_WEIGHTS (15/20/25/15/20/5). If the brief verdict parses to
    a section count other than 6 (partial responses, drift), the
    weighted path falls back to equal-weighted averaging over whatever
    is present — defensive so a truncated brief response still
    produces a score rather than crashing.

    Always returns the shape — the caller treats `score is None` as
    "could not derive a score" (the arbiter returned malformed
    markdown) rather than as a failing paper.

    Partial responses are kept verbatim. If the parser recognises 3 of
    5 sections (or 4 of 6 in brief mode), sections_rated is 3/4 and
    section_ratings carries those entries; the caller decides whether
    to treat a partial result as advisory or to re-run the review.
    """
    is_brief = mode == "brief_review"
    section_keys = _BRIEF_SECTION_KEYS if is_brief else _SECTION_KEYS
    section_ratings = _parse_section_ratings(verdict, section_keys)

    # Weighted average for brief mode when all six sections parsed;
    # equal-weighted fallback otherwise (defensive — partial brief
    # responses must not crash and a midpoint verdict is always
    # equal-weighted).
    score: float | None
    if is_brief and len(section_ratings) == len(_BRIEF_WEIGHTS):
        weighted = 0.0
        weight_sum = 0.0
        for (key, _label), w in zip(section_keys, _BRIEF_WEIGHTS):
            rating = section_ratings.get(key)
            if rating is None or rating not in _RATING_POINTS:
                continue
            weighted += _RATING_POINTS[rating] * w
            weight_sum += w
        score = round(weighted / weight_sum, 1) if weight_sum else None
    else:
        points = [
            _RATING_POINTS[v]
            for v in section_ratings.values()
            if v in _RATING_POINTS
        ]
        score = round(sum(points) / len(points), 1) if points else None

    # Overall rating — the last keyed section (section 5 in midpoint,
    # section 5 "Final Recommendations" in brief, NOT section 6
    # "Visuals" which is a 5%-weight ancillary). Preserves the
    # historical "rating" surfacing: the editor pill's title attribute
    # reads the closing rubric verdict, not the smallest-weight section.
    overall_key = "readiness" if not is_brief else "final_recommendations"
    overall = section_ratings.get(overall_key)
    advisory = score is not None and score < ADVISORY_THRESHOLD
    # Bridge #82 — when the verdict is non-empty but zero sections
    # parsed, surface it as a parse error rather than reporting it as
    # a clean zero-section result. The arbiter may have refused, the
    # heading syntax may have drifted, or the response may have been
    # truncated mid-prefix; in every case the IN02 finding should
    # describe the broken response, not pretend the review delivered
    # zero ratings.
    stripped = (verdict or "").strip()
    parse_error = bool(stripped) and not section_ratings
    return {
        "score": score,
        "rating": overall,
        "section_ratings": section_ratings,
        "advisory": advisory,
        "sections_rated": len(section_ratings),
        "parse_error": parse_error,
    }
