"""tools/citation_sourcing.py — multi-layered citation sourcing foundation.

May 24 2026. Pure helpers + a single LLM wrapper. NO pipeline wiring;
this module is intentionally NOT imported by source_citations or any
other existing pipeline step. A follow-up PR adds the wiring.

WHAT THIS MODULE PROVIDES:

  CITATION_TYPES — the canonical 4-tag taxonomy enum:
    theoretical / empirical / methodological / practitioner

  TRUST_FLAGS — the canonical 5-flag taxonomy enum:
    verified / unverified / paywalled / stale / mismatch

  generate_queries(finding) → dict[str, str]
    LLM-driven query generator. Takes a finding payload (the same
    shape backend/tools/analytical_findings emits) and returns four
    search queries (one per citation_type). Handles missing /
    null fields gracefully — falls back to title-only queries when
    the finding payload is incomplete. Fail-open: LLM error or
    timeout returns {} (the caller treats absence as "no search
    runs for this finding").

  score_citation(citation, finding_context) → dict
    PURE deterministic scoring. No LLM call. Given a citation
    metadata dict + the finding it's meant to support, returns:
      {
        confidence_score: float in [0.0, 1.0],   ← clamped at boundaries
        trust_flag: str (one of TRUST_FLAGS),    ← enum-restricted
        scoring_rationale: str,                  ← human-readable one-liner
      }
    The weighted-component formula (40% publication / 35% relevance /
    15% recency / 10% verifiability) is encoded as four pure
    sub-scorers. Same inputs always produce the same output.

  LLM_TIMEOUT_SECONDS — every LLM call wraps in this timeout
    so a hung Anthropic call never blocks the request thread.

  FALLBACK_SCORE — the conservative score every fail-open path
    returns: 0.0 confidence + 'unverified' trust flag + a note.

DESIGN INVARIANTS (enforced by tests):
  - Trust flag is always one of TRUST_FLAGS (enum-restricted).
  - confidence_score is always in [0.0, 1.0] (clamped).
  - generate_queries never raises on malformed input — returns {}.
  - score_citation never raises on missing fields — substitutes
    safe defaults and proceeds.
"""
from __future__ import annotations

import json
from typing import Any, Final

import structlog

log = structlog.get_logger(__name__)


# ── Enums ─────────────────────────────────────────────────────────────────────

CITATION_TYPE_THEORETICAL:    Final[str] = "theoretical"
CITATION_TYPE_EMPIRICAL:      Final[str] = "empirical"
CITATION_TYPE_METHODOLOGICAL: Final[str] = "methodological"
CITATION_TYPE_PRACTITIONER:   Final[str] = "practitioner"

CITATION_TYPES: Final[frozenset[str]] = frozenset([
    CITATION_TYPE_THEORETICAL,
    CITATION_TYPE_EMPIRICAL,
    CITATION_TYPE_METHODOLOGICAL,
    CITATION_TYPE_PRACTITIONER,
])

TRUST_FLAG_VERIFIED:   Final[str] = "verified"
TRUST_FLAG_UNVERIFIED: Final[str] = "unverified"
TRUST_FLAG_PAYWALLED:  Final[str] = "paywalled"
TRUST_FLAG_STALE:      Final[str] = "stale"
TRUST_FLAG_MISMATCH:   Final[str] = "mismatch"

TRUST_FLAGS: Final[frozenset[str]] = frozenset([
    TRUST_FLAG_VERIFIED,
    TRUST_FLAG_UNVERIFIED,
    TRUST_FLAG_PAYWALLED,
    TRUST_FLAG_STALE,
    TRUST_FLAG_MISMATCH,
])


# ── Constants ────────────────────────────────────────────────────────────────

# Every LLM call wraps in this timeout so a hung Anthropic call
# never blocks a request thread. 30s matches the frontend's axios
# default and the Render gateway's request timeout — a longer
# value would be silently terminated upstream.
LLM_TIMEOUT_SECONDS: Final[float] = 30.0

# The conservative score returned by every fail-open path. A citation
# with this score will NOT cross the 0.50 surface threshold the
# Citation Review panel applies — so a failed scoring call is
# silently dropped rather than surfaced as a low-quality option.
FALLBACK_SCORE: Final[dict[str, Any]] = {
    "confidence_score": 0.0,
    "trust_flag": TRUST_FLAG_UNVERIFIED,
    "scoring_rationale": (
        "Scoring unavailable — citation surfaced with a "
        "conservative default. Manual review recommended."),
}

# Weighted components per the spec (sum = 1.0). Encoded here so a
# future tuning lands in one place rather than scattered across the
# four sub-scorers.
WEIGHT_PUBLICATION:   Final[float] = 0.40
WEIGHT_RELEVANCE:     Final[float] = 0.35
WEIGHT_RECENCY:       Final[float] = 0.15
WEIGHT_VERIFIABILITY: Final[float] = 0.10

# Top-tier journals — bonus on publication score.
TOP_TIER_JOURNALS: Final[frozenset[str]] = frozenset([
    "journal of finance",
    "review of financial studies",
    "journal of portfolio management",
    "financial analysts journal",
    "journal of financial economics",
    "journal of asset management",
])

# Practitioner-allowed source identifiers (case-insensitive substring
# match on the source field). Used by the publication scorer.
PRACTITIONER_PUBLISHERS: Final[frozenset[str]] = frozenset([
    "aqr", "cfa institute", "msci", "jpmorgan", "j.p. morgan",
    "vanguard", "blackrock", "state street", "morningstar",
    "federal reserve", "bis", "imf",
])


# ── Pure helpers ─────────────────────────────────────────────────────────────

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp a float to [lo, hi] — the confidence_score guarantee."""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _safe_str(value: Any) -> str:
    """Coerce a possibly-None / non-string value to a stripped string."""
    if value is None:
        return ""
    try:
        return str(value).strip()
    except Exception:  # noqa: BLE001
        return ""


def _safe_lower(value: Any) -> str:
    return _safe_str(value).lower()


# ── Sub-scorers — each returns a value in [0.0, 1.0] ─────────────────────────

def _score_publication(citation: dict[str, Any]) -> float:
    """Weight: 40%. Source quality.
      - Peer-reviewed journal with DOI: 1.0 (top-tier journals +0.1)
      - Working paper SSRN/arXiv: 0.75 if cited > 50, else 0.60
      - Practitioner report: 0.65
      - Industry blog / explainer: 0.30
    """
    journal = _safe_lower(citation.get("journal_or_institution"))
    url = _safe_lower(citation.get("url"))
    has_doi = "doi.org" in url or _safe_str(citation.get("doi")) != ""
    citation_count = citation.get("citation_count")
    try:
        citation_count = int(citation_count) if citation_count is not None else 0
    except (TypeError, ValueError):
        citation_count = 0

    # Top-tier peer-reviewed with DOI.
    if has_doi:
        base = 1.0
        if any(j in journal for j in TOP_TIER_JOURNALS):
            base = _clamp(base + 0.1)
        return _clamp(base)
    # SSRN / arXiv working paper.
    if "ssrn" in url or "arxiv" in url:
        return 0.75 if citation_count > 50 else 0.60
    # Practitioner report — match the publisher list.
    if any(p in journal or p in url for p in PRACTITIONER_PUBLISHERS):
        return 0.65
    # Anything else — treat as industry blog / explainer.
    return 0.30


def _score_relevance(
    citation: dict[str, Any],
    finding_context: dict[str, Any],
) -> float:
    """Weight: 35%. Closeness of the source's hypothesis to the finding.

    The relevance assessment is supplied by the caller as a single
    field on `finding_context['relevance_tier']`. The LLM that did
    the source-finding pairing should classify each candidate into
    one of four tiers; this function maps that tier to a score:
      - same_hypothesis_same_asset_class: 1.0
      - same_hypothesis_different_asset_class: 0.75
      - related_hypothesis: 0.50
      - tangential: 0.25
    A missing or unknown tier conservatively defaults to 0.25 so a
    low-confidence pairing never silently scores high.
    """
    tier = _safe_lower(finding_context.get("relevance_tier"))
    return {
        "same_hypothesis_same_asset_class": 1.0,
        "same_hypothesis_different_asset_class": 0.75,
        "related_hypothesis": 0.50,
        "tangential": 0.25,
    }.get(tier, 0.25)


def _score_recency(
    citation: dict[str, Any],
    citation_type: str,
) -> float:
    """Weight: 15%. Recency banding.
      - 2022-present: 1.0
      - 2018-2021: 0.80
      - 2015-2017: 0.65
      - Pre-2015: 0.50 — acceptable for theoretical type ONLY
    """
    year_raw = citation.get("year")
    try:
        year = int(_safe_str(year_raw)[:4])
    except (TypeError, ValueError):
        return 0.50  # Unknown year — conservative midpoint.
    if year >= 2022:
        return 1.0
    if year >= 2018:
        return 0.80
    if year >= 2015:
        return 0.65
    # Pre-2015 — only theoretical retains a score, others penalised.
    if citation_type == CITATION_TYPE_THEORETICAL:
        return 0.50
    return 0.20


def _score_verifiability(citation: dict[str, Any]) -> float:
    """Weight: 10%. Whether the link resolves.
      - DOI resolves: 1.0
      - URL live + content confirmed: 0.80
      - URL paywalled / abstract only: 0.60
      - URL not confirmed: 0.20

    Inputs:
      citation['doi_resolved'] (bool) — set by the verifier helper
      citation['url_status'] (str)    — 'live' | 'paywalled' | None
    """
    if citation.get("doi_resolved") is True:
        return 1.0
    url_status = _safe_lower(citation.get("url_status"))
    if url_status == "live":
        return 0.80
    if url_status == "paywalled":
        return 0.60
    return 0.20


# ── Trust-flag classifier ────────────────────────────────────────────────────

def _classify_trust_flag(
    citation: dict[str, Any],
    citation_type: str,
    relevance_tier: str,
) -> str:
    """Returns one of TRUST_FLAGS. Order matters — first match wins,
    and the ordering reflects severity (mismatch is the strongest
    signal, verified the weakest in the sense that any concern
    overrides it).

      mismatch     — relevance tier is 'tangential' OR the LLM's
                     pairing returned an explicit mismatch signal.
                     The Citation Review panel will NOT surface
                     mismatch-flagged citations.
      stale        — non-theoretical citation from pre-2015. The
                     spec says pre-2015 is acceptable for
                     theoretical only.
      paywalled    — URL is reachable but content is behind a wall.
      verified     — DOI resolved or content confirmed.
      unverified   — fallback when no stronger signal applies.
    """
    if _safe_lower(relevance_tier) == "tangential":
        return TRUST_FLAG_MISMATCH
    if citation.get("explicit_mismatch") is True:
        return TRUST_FLAG_MISMATCH
    try:
        year = int(_safe_str(citation.get("year"))[:4])
    except (TypeError, ValueError):
        year = 0
    if year and year < 2015 and citation_type != CITATION_TYPE_THEORETICAL:
        return TRUST_FLAG_STALE
    if _safe_lower(citation.get("url_status")) == "paywalled":
        return TRUST_FLAG_PAYWALLED
    if (citation.get("doi_resolved") is True
            or _safe_lower(citation.get("url_status")) == "live"):
        return TRUST_FLAG_VERIFIED
    return TRUST_FLAG_UNVERIFIED


# ── Public scoring function ──────────────────────────────────────────────────

def score_citation(
    citation: dict[str, Any],
    finding_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """PURE deterministic scoring. Same inputs always produce the
    same output. Never raises — every code path returns a valid
    {confidence_score, trust_flag, scoring_rationale} dict.

    Args:
      citation — the citation metadata. Recognised fields:
        url, journal_or_institution, year, doi, doi_resolved,
        url_status, citation_count, explicit_mismatch.
      finding_context — the per-finding context the relevance
        scorer needs. Recognised field: relevance_tier (one of
        'same_hypothesis_same_asset_class' /
        'same_hypothesis_different_asset_class' /
        'related_hypothesis' / 'tangential').

    Returns:
      {
        confidence_score: float in [0.0, 1.0],
        trust_flag: str (one of TRUST_FLAGS),
        scoring_rationale: str (human-readable one-liner),
      }
    """
    # Defensive — None inputs degrade to empty dicts so the
    # sub-scorers' .get() calls never blow up.
    citation = citation or {}
    finding_context = finding_context or {}

    citation_type = _safe_lower(
        citation.get("citation_type") or finding_context.get("citation_type"))
    if citation_type not in CITATION_TYPES:
        citation_type = CITATION_TYPE_THEORETICAL  # safest default

    relevance_tier = _safe_lower(finding_context.get("relevance_tier"))

    pub = _score_publication(citation)
    rel = _score_relevance(citation, finding_context)
    rec = _score_recency(citation, citation_type)
    ver = _score_verifiability(citation)

    raw = (
        WEIGHT_PUBLICATION   * pub +
        WEIGHT_RELEVANCE     * rel +
        WEIGHT_RECENCY       * rec +
        WEIGHT_VERIFIABILITY * ver
    )
    score = _clamp(raw)

    trust_flag = _classify_trust_flag(citation, citation_type, relevance_tier)

    # The rationale names the four sub-scores so a reviewer reading
    # the Citation Review panel can see WHY a citation scored where
    # it did. Format: 4 numbers + a one-sentence summary keyed off
    # the dominant signal.
    dominant = max(
        ("publication", pub),
        ("relevance",   rel),
        ("recency",     rec),
        ("verifiability", ver),
        key=lambda x: x[1],
    )
    rationale = (
        f"Score {score:.2f} (pub={pub:.2f} rel={rel:.2f} "
        f"rec={rec:.2f} ver={ver:.2f}). "
        f"Dominant signal: {dominant[0]} at {dominant[1]:.2f}. "
        f"Type={citation_type}, trust={trust_flag}."
    )

    return {
        "confidence_score": score,
        "trust_flag": trust_flag,
        "scoring_rationale": rationale,
    }


# ── LLM query generator ──────────────────────────────────────────────────────

def _fallback_queries(finding: dict[str, Any]) -> dict[str, str]:
    """Title-only fallback when the finding payload is incomplete or
    the LLM call fails. One query per citation_type, all keyed off
    whatever title text we can salvage. Returns {} when even the
    title is unavailable — the caller treats absence as "no search
    runs for this finding."""
    title = _safe_str(finding.get("title") if finding else "")
    if not title:
        return {}
    base = title.lower()
    return {
        CITATION_TYPE_THEORETICAL: (
            f"{base} foundational theory portfolio management"),
        CITATION_TYPE_EMPIRICAL: (
            f"{base} empirical evidence portfolio 2018 2024"),
        CITATION_TYPE_METHODOLOGICAL: (
            f"{base} methodology validation finance"),
        CITATION_TYPE_PRACTITIONER: (
            f"{base} institutional investor portfolio implications"),
    }


def _build_query_prompt(finding: dict[str, Any]) -> str:
    """Builds the user prompt passed to Sonnet. The finding payload
    is JSON-serialised (default=str so any non-serialisable value
    coerces cleanly) and wrapped in the spec's prompt template."""
    finding_json = json.dumps(finding or {}, default=str, indent=2)
    return (
        f"Given this portfolio finding:\n{finding_json}\n\n"
        "Generate 4 search queries (empirical, theoretical, "
        "methodological, practitioner) that would find the best "
        "academic and practitioner citations to support it. Each "
        "query should be 6-10 words, specific, and include a date "
        "range for empirical. Return ONLY a JSON object with the "
        "keys 'empirical', 'theoretical', 'methodological', "
        "'practitioner' — each value a single string query. No "
        "preamble, no markdown code fence."
    )


def _parse_query_response(raw: str) -> dict[str, str]:
    """Parses the LLM response. Returns an empty dict on any parse
    failure — the caller's fallback path takes over."""
    if not raw:
        return {}
    text = raw.strip()
    # Strip a markdown code fence if the model added one despite the
    # instruction.
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rsplit("```", 1)[0]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    # Restrict to the four valid keys; coerce values to stripped strings.
    out: dict[str, str] = {}
    for k in (CITATION_TYPE_THEORETICAL, CITATION_TYPE_EMPIRICAL,
              CITATION_TYPE_METHODOLOGICAL, CITATION_TYPE_PRACTITIONER):
        v = _safe_str(parsed.get(k))
        if v:
            out[k] = v
    return out


def generate_queries(finding: dict[str, Any]) -> dict[str, str]:
    """LLM-driven query generator. Returns one search query per
    citation_type for the given finding payload.

    Guardrails (per the spec):
      - Handles missing / null fields gracefully — falls back to a
        title-only query when the payload is incomplete.
      - Wraps the LLM call in LLM_TIMEOUT_SECONDS; on timeout or
        any other error, returns the fallback queries.
      - Returns an EMPTY dict only when even the title is
        unavailable (the caller skips the finding cleanly).
      - Never raises.

    Returns:
      {'theoretical': str, 'empirical': str, 'methodological': str,
       'practitioner': str}  — possibly missing some keys when the
                                LLM returned fewer than four queries.
    """
    # Fast bail-out — empty / None finding.
    if not finding or not isinstance(finding, dict):
        return {}

    # Test environment / no API key → the LLM cannot run. Fall back
    # to title-only queries so tests have a deterministic surface.
    import os
    if os.getenv("ENVIRONMENT", "").lower() == "test":
        return _fallback_queries(finding)
    if not os.getenv("ANTHROPIC_API_KEY"):
        return _fallback_queries(finding)

    # Lazy import so the test environment (no anthropic SDK
    # available) can still import this module.
    try:
        from agents.base import call_claude, SONNET_MODEL
    except Exception as exc:  # noqa: BLE001
        log.warning("citation_query_generator_import_failed", error=str(exc))
        return _fallback_queries(finding)

    sys_prompt = (
        "You are a research librarian generating academic search "
        "queries. Output JSON only — no markdown, no preamble.")
    user_message = _build_query_prompt(finding)

    try:
        # call_claude is sync; wrap in a timeout via threads if
        # needed. The Anthropic SDK already respects its own request
        # timeout, but we re-impose ours as defence in depth.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(
                call_claude, SONNET_MODEL, sys_prompt, user_message,
                max_tokens=400,
            )
            raw = future.result(timeout=LLM_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        log.warning("citation_query_generator_timeout",
                    finding_title=_safe_str(finding.get("title"))[:80])
        return _fallback_queries(finding)
    except Exception as exc:  # noqa: BLE001
        log.warning("citation_query_generator_failed",
                    finding_title=_safe_str(finding.get("title"))[:80],
                    error=str(exc))
        return _fallback_queries(finding)

    parsed = _parse_query_response(raw)
    if not parsed:
        # Empty parse — fall back rather than skip so the pipeline
        # still has something to search on.
        return _fallback_queries(finding)
    return parsed
