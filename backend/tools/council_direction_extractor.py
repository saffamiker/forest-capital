"""tools/council_direction_extractor.py — extract recommendation
direction from council synthesis prose.

The cio_recommendations row stores signal / recommendation /
dissenting_view / key_risk / confidence / limitations — none of
those is a directional label. The council_query_metrics table needs
one (`risk_on` / `defensive` / `balanced`) to compute the HMM
alignment score.

Keyword extractor — the same simplicity bar as the question
classifier. A small list of phrases per direction is matched against
the synthesis text; the direction with the most matches wins. Ties
or no matches → "balanced" (the neutral fallback per the June 3 2026
finding's Q4 default).

DELIBERATELY KEYWORD-BASED, NOT LLM-CALL-BASED
  The alternative was a structured `direction` field on the CIO's
  output schema. That would have been cleaner long-term but touches
  the CIO prompt + output parsing + every test that asserts on the
  synthesis shape. Keyword extraction is ~40 LoC, no LLM cost, no
  prompt drift, easy to test deterministically.

REFINED ALIGNMENT SCORE
  The June 3 2026 amendment replaces the original 0 / 0.5 / 1.0
  ladder with a continuous formula:

    final_score = base_score (0|1) * hmm_confidence (0.0-1.0)

  A correct recommendation in a 95%-confidence BULL regime scores
  0.95; the same recommendation in a 51%-confidence TRANSITION
  scores 0.51. Misalignment is penalised proportionally to
  confidence.
"""
from __future__ import annotations

import re

import structlog

log = structlog.get_logger(__name__)


DIRECTION_RISK_ON = "risk_on"
DIRECTION_DEFENSIVE = "defensive"
DIRECTION_BALANCED = "balanced"

ALL_DIRECTIONS = (DIRECTION_RISK_ON, DIRECTION_DEFENSIVE, DIRECTION_BALANCED)


# Direction → key phrases. Multi-word phrases match anywhere they
# appear; single-word phrases match on word boundaries. The lists
# are intentionally short — the goal is "good enough to distinguish
# clearly directional advice from neutral synthesis", not exhaustive.
_DIRECTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    DIRECTION_RISK_ON: (
        "overweight equity", "risk on", "risk-on", "add equity",
        "increase equity", "lean into momentum", "lean equity",
        "tilt to equity", "overweight risk",
    ),
    DIRECTION_DEFENSIVE: (
        "underweight equity", "shift to bonds", "shift to bond",
        "reduce risk", "reduce equity", "raise cash",
        "increase bond", "increase bonds", "defensive",
        "tilt defensive", "rotate to defence", "rotate to defense",
        "tilt to bonds",
    ),
    DIRECTION_BALANCED: (
        "balanced allocation", "stay balanced", "maintain balance",
        "neutral stance", "hold current", "no change",
        "current weights are appropriate",
    ),
}


def _compile_patterns(phrases: tuple[str, ...]) -> list[re.Pattern[str]]:
    out: list[re.Pattern[str]] = []
    for phrase in phrases:
        if " " in phrase or "-" in phrase:
            pat = re.escape(phrase).replace(r"\ ", r"\s+")
            out.append(re.compile(pat, re.IGNORECASE))
        else:
            out.append(re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE))
    return out


_DIRECTION_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    d: _compile_patterns(kws) for d, kws in _DIRECTION_KEYWORDS.items()
}


def extract_direction(synthesis_text: str | None) -> str:
    """Returns one of risk_on / defensive / balanced. Never raises;
    a missing / non-string synthesis returns 'balanced'."""
    if not synthesis_text or not isinstance(synthesis_text, str):
        return DIRECTION_BALANCED
    scores: dict[str, int] = {}
    for direction, patterns in _DIRECTION_PATTERNS.items():
        n = 0
        for p in patterns:
            if p.search(synthesis_text):
                n += 1
        if n > 0:
            scores[direction] = n
    if not scores:
        return DIRECTION_BALANCED
    top = max(scores.items(), key=lambda kv: kv[1])
    top_dir, top_count = top
    # On a tie between risk_on and defensive (rare but possible in
    # a "consider rotating to bonds while staying tilted to momentum"
    # sentence), prefer balanced — the synthesis is hedging, not
    # taking a side.
    competitors = [d for d, n in scores.items()
                   if n == top_count and d != top_dir]
    if competitors and DIRECTION_BALANCED not in (top_dir, *competitors):
        return DIRECTION_BALANCED
    return top_dir


# ── Alignment score ──────────────────────────────────────────────────────


def alignment_score(
    direction: str | None,
    hmm_state: str | None,
    hmm_confidence: float | None,
) -> float:
    """The refined alignment score per the June 3 2026 amendment:

        base = 1 when direction matches hmm_state's natural posture
        base = 0 on a clear mismatch (risk_on in a BEAR regime, etc.)
        base = 0.5 when direction is balanced or hmm_state is
               TRANSITION (no clear alignment to score against)
        final = base * (hmm_confidence or 0.5)

    `balanced` paired with any regime returns 0.5 * confidence —
    treats a neutral recommendation as halfway-correct regardless
    of regime, so a low-confidence regime can still log a high-
    confidence neutral stance reasonably.

    `hmm_confidence` of None falls back to 0.5 so missing data is
    treated as low-confidence rather than zero — same convention
    detect_current_regime() applies for missing inputs.
    """
    if direction is None:
        direction = DIRECTION_BALANCED
    state = (hmm_state or "").upper()
    conf = (float(hmm_confidence) if hmm_confidence is not None else 0.5)
    # Clamp confidence to [0, 1] in case of upstream data drift.
    conf = max(0.0, min(1.0, conf))

    if direction == DIRECTION_BALANCED or state in ("TRANSITION", ""):
        # Neutral recommendation, OR no clear regime to align to.
        base = 0.5
    elif state == "BULL" and direction == DIRECTION_RISK_ON:
        base = 1.0
    elif state == "BEAR" and direction == DIRECTION_DEFENSIVE:
        base = 1.0
    elif state == "BULL" and direction == DIRECTION_DEFENSIVE:
        base = 0.0
    elif state == "BEAR" and direction == DIRECTION_RISK_ON:
        base = 0.0
    else:
        # Unknown regime label — treat as neutral.
        base = 0.5

    return round(base * conf, 4)
