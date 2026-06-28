"""tools/untoken_numeric_check.py -- June 28 2026.

Hard-lock numeric guardrail for executive_brief +
analytical_appendix generation.

After every Sonnet response (which may contain {{TOKEN}}
placeholders + plain prose), this scanner flags any free-text
numeric that is NOT supported by the substitution layer. The
harness loop in harness_narrative then routes the response back
to the LLM with feedback identifying the offenders + asks for
rephrasing OR replacement with a supported {{TOKEN}}.

The scan is conservative -- it deliberately allows:
  - Years (1900-2099) -- citation dates, dataset eras
  - Numbers inside parentheses that look like citations
    (e.g. "Smith (2020)")
  - Numbers attached to standard units that aren't numeric
    findings (bps, n=, p-values written as "p < 0.05")
  - Numbers inside table-formatting context (% column widths)
  - Numbers that match a value the substitution_table CAN
    produce (so an LLM that happened to type "0.86" instead of
    "{{OOS_SHARPE_BLEND}}" is flagged as the "missing token"
    feedback rather than rejected as truly-unsupported)

A flagged violation carries:
  - the offending numeric string
  - the sentence containing it (up to 200 chars of context)
  - the suggested {{TOKEN}} when the value matches a known
    substitution-table output (lets the LLM swap rather than
    delete)
  - severity: "unsupported" (no matching token; must rephrase)
              "token_available" (value matches; must swap)

The harness loop uses this to construct correction feedback for
the next iteration.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

try:
    import structlog
    log = structlog.get_logger(__name__)
except ImportError:
    log = logging.getLogger(__name__)  # type: ignore[assignment]


# ── Numeric regex + allowlist patterns ─────────────────────────


# A "candidate numeric" is any decimal / integer / percentage /
# signed value that appears as a standalone token in the text.
# We use a broad capture + filter via the allowlist patterns
# below.
_NUMERIC_PATTERN = re.compile(
    r"""
    (?:^|(?<=[\s\(\[\{\"\'>,]))   # word boundary at start
    (
      [+\-]?              # optional sign
      \d{1,4}             # 1-4 leading digits
      (?:,\d{3})*         # optional thousands separators
      (?:\.\d+)?          # optional decimal
      %?                  # optional percent
    )
    (?=$|[\s\.\,\;\:\!\?\)\]\}\"\'<])  # word boundary at end
    """,
    re.VERBOSE,
)

# Patterns that intentionally allow some numeric strings without
# requiring a token backing.
_ALLOWLIST_PATTERNS: list[tuple[re.Pattern, str]] = [
    # 4-digit years (1900-2099) -- citation / era references.
    (re.compile(r"^[12]\d{3}$"), "year"),
    # Years inside parentheses (citation form): "(2020)" etc.
    (re.compile(r"^\([12]\d{3}\)$"), "year_paren"),
    # Standalone single digits in narrative ("3 strategies")
    # -- noisy but uncatchable without semantic context. The
    # scanner conservatively ALLOWS bare single digits because
    # the operator's directive says rephrase OR swap; for "3
    # strategies" the right answer is usually neither. Long-term:
    # the prompt should ask the LLM to spell out small integers.
    (re.compile(r"^[0-9]$"), "single_digit"),
    # P-values written as "p < 0.05" / "p = 0.001" -- the
    # numeric tail is conventional notation, not a finding.
    # The surrounding context check (sentence_contains_p_value)
    # handles these.
]


# June 28 2026 -- structural-prose patterns that anchor a
# numeric to a non-data-driven context. The numeric is part of
# a recognised structural phrase (index name, definitional
# allocation, strategy reference, statistical threshold) rather
# than a substitution-eligible value. These patterns short-
# circuit the violation classification BEFORE the
# substitution-table check -- BUT a value that IS in the
# substitution table never gets exempted (the operator's
# constraint: "Do not exempt any value that appears in the
# substitution table").
#
# Each pattern matches against a (value, surrounding_window)
# tuple where surrounding_window is ~40 chars of text centred on
# the value's position. The window-based match avoids false
# positives from coincidental substrings elsewhere in the
# sentence.
_STRUCTURAL_PATTERNS: list[tuple[re.Pattern, str]] = [
    # "S&P 500" / "S&P 100" / "S&P 1500" -- index name. The
    # numeric is part of the proper noun, not a finding.
    (re.compile(r"S&P\s*\d{2,4}", re.IGNORECASE), "sp_index"),
    # "100% equity" / "100% allocation" / "100% bond" /
    # "100% bonds" -- definitional allocation prose ("the
    # benchmark holds 100% equity"). The "100%" is structural,
    # not a substitution-eligible value.
    (re.compile(
        r"100%\s+(equity|equities|bond|bonds|allocation|"
        r"cash|stocks|stock)",
        re.IGNORECASE), "definitional_100pct"),
    # "60/40", "70/30", etc -- strategy name references
    # (canonical balanced-portfolio shorthand). The numeric pair
    # IS the strategy identifier, not a substitutable value.
    (re.compile(r"\d{2,3}/\d{2,3}"), "balanced_portfolio_ref"),
    # Statistical-threshold prose: "p < 0.05" / "p = 0.001" /
    # "p <= 0.005" / "p > 0.10" / "alpha = 0.05". The numeric
    # tail is conventional statistical notation, not a data
    # finding from the cache.
    (re.compile(
        r"\b(?:p|alpha|significance)\s*[<>=≤≥]+\s*0?\.\d+",
        re.IGNORECASE), "stat_threshold"),
]


def _matches_structural_pattern(
    value: str,
    text: str,
    span: tuple[int, int],
) -> str | None:
    """Returns the structural-pattern name when `value` sits
    inside a recognised structural phrase, else None. Match
    window is 40 chars before + 40 chars after the value's
    span to keep the regex cost bounded + avoid matching
    patterns elsewhere in the sentence.

    Important: this does NOT consult the substitution table --
    the caller (find_untoken_backed_numerics) applies the
    "never exempt a substitution-table value" rule by checking
    the inverse table BEFORE invoking this helper."""
    start, end = span
    window_start = max(0, start - 40)
    window_end = min(len(text), end + 40)
    window = text[window_start:window_end]
    for pattern, name in _STRUCTURAL_PATTERNS:
        for m in pattern.finditer(window):
            m_start = window_start + m.start()
            m_end = window_start + m.end()
            # The value's span must sit WITHIN the structural
            # match.
            if m_start <= start and end <= m_end:
                return name
    return None


@dataclass
class NumericViolation:
    """One untoken-backed numeric found in the text."""
    raw_value:   str          # the matched numeric string
    sentence:    str          # surrounding sentence (200-char cap)
    suggested_token: str | None = None  # matching token if any
    severity:    str = "unsupported"
    span:        tuple[int, int] = (0, 0)

    def to_feedback_line(self) -> str:
        if self.suggested_token:
            return (
                f"  - '{self.raw_value}' in: "
                f"'{self.sentence[:200]}...' -- "
                f"REPLACE with {self.suggested_token}")
        return (
            f"  - '{self.raw_value}' in: "
            f"'{self.sentence[:200]}...' -- "
            "REPHRASE without this number (no matching token)")


# ── Helpers ────────────────────────────────────────────────────


def _sentence_containing(text: str, span: tuple[int, int]) -> str:
    """Returns the sentence (up to 200 chars) containing the
    span. Uses simple terminator splitting -- not perfect for
    citations like 'Smith et al.' but good enough for feedback
    construction."""
    start, end = span
    # Walk backwards for sentence start.
    s = start
    while s > 0 and text[s - 1] not in ".!?\n":
        s -= 1
    # Walk forward for sentence end.
    e = end
    while e < len(text) and text[e] not in ".!?\n":
        e += 1
    snippet = text[s:e + 1].strip()
    return snippet[:200]


def _is_inside_token(text: str, span: tuple[int, int]) -> bool:
    """True when the matched numeric sits inside a {{TOKEN}}
    placeholder (e.g. a token whose name happens to contain
    digits). Defensive -- the broad numeric regex shouldn't
    match inside word-character runs but this check is cheap
    insurance."""
    start, end = span
    # Look for an unclosed {{ before the match.
    before = text[:start]
    last_open = before.rfind("{{")
    if last_open == -1:
        return False
    last_close = before.rfind("}}")
    if last_close > last_open:
        return False
    # Open brace found with no closing before our match. Check
    # for closing }} after our match within reasonable bounds.
    after = text[end:end + 100]
    return "}}" in after


def _sentence_is_citation(sentence: str) -> bool:
    """True when the sentence looks like a citation-only line --
    no semantic numeric claim. Conservative: only matches when
    the entire 'sentence' is a parenthesised author-year form
    or a reference-list entry pattern."""
    s = sentence.strip()
    if not s:
        return False
    # Pure parenthesised year inside a citation.
    if re.fullmatch(r"\([A-Za-z][\w\.\s,&]+\s*\d{4}\)", s):
        return True
    # Reference-list entry: starts with an author name + year
    # OR ends in a DOI / URL.
    if re.search(r"https?://", s) or re.search(
            r"\bdoi:\s*10\.", s, re.IGNORECASE):
        return True
    return False


# ── Token-value index ──────────────────────────────────────────


def _build_token_index(
    substitution_table: dict[str, str],
) -> dict[str, str]:
    """Inverts the substitution table to value -> token. Used to
    suggest a swap when the LLM types a numeric that happens to
    match a substitution output (so the feedback can be 'use the
    {{TOKEN}} instead' rather than 'rephrase')."""
    out: dict[str, str] = {}
    if not substitution_table:
        return out
    for token, value in substitution_table.items():
        if not isinstance(token, str) or not isinstance(value, str):
            continue
        if not (token.startswith("{{") and token.endswith("}}")):
            continue
        if not value or value == "—":
            continue
        # Last-write-wins on collision (same value from two
        # tokens). The suggestion is informational; the operator
        # ultimately reviews via the value_manifest audit.
        out[value] = token
    return out


def _is_value_supported_by_substitution(
    value: str,
    value_to_token: dict[str, str],
) -> str | None:
    """Returns the suggested token if `value` matches a
    substitution-table output. Tries exact match first, then a
    trimmed variant (e.g. '0.86' matches when table has
    '0.86%')."""
    if value in value_to_token:
        return value_to_token[value]
    # Trim trailing % for comparison.
    if value.endswith("%") and value[:-1] in value_to_token:
        return value_to_token[value[:-1]]
    if not value.endswith("%") and (value + "%") in value_to_token:
        return value_to_token[value + "%"]
    return None


# ── Public scanner ─────────────────────────────────────────────


def find_untoken_backed_numerics(
    text: str,
    substitution_table: dict[str, str] | None = None,
    numeric_anchors: dict[str, Any] | None = None,
) -> list[NumericViolation]:
    """Scan `text` for free-text numerics that lack token
    backing. Returns a list of NumericViolation entries; empty
    list when the text is clean.

    substitution_table -- the full token-to-value mapping from
    get_substitution_table; used both to skip numerics that
    appear inside {{TOKEN}} markers AND to suggest a swap when
    an unsupported numeric matches a known substitution value.

    numeric_anchors -- per-section locked anchor values from the
    story plan. A numeric matching any anchor value is allowed
    even without a token wrapper (anchors are deliberately
    inline -- they're the section's authoritative numeric
    claims).
    """
    if not text:
        return []
    value_to_token = _build_token_index(substitution_table or {})

    # Anchor values, normalised for comparison.
    anchor_values: set[str] = set()
    if numeric_anchors:
        for v in numeric_anchors.values():
            try:
                fv = float(v)
                anchor_values.add(f"{fv:g}")
                anchor_values.add(f"{fv:.1f}")
                anchor_values.add(f"{fv:.2f}")
                anchor_values.add(str(int(fv))
                                  if fv.is_integer() else f"{fv:.2f}")
                # Percentage formats for fraction-style anchors.
                if abs(fv) < 1:
                    anchor_values.add(f"{fv * 100:.1f}%")
                    anchor_values.add(f"{fv * 100:.2f}%")
            except (TypeError, ValueError):
                anchor_values.add(str(v))

    violations: list[NumericViolation] = []
    for m in _NUMERIC_PATTERN.finditer(text):
        raw = m.group(1)
        if not raw:
            continue
        span = m.span(1)

        # Skip if inside a {{TOKEN}}.
        if _is_inside_token(text, span):
            continue

        # Skip allowlisted shapes (years, single digits).
        if any(p.match(raw) for p, _ in _ALLOWLIST_PATTERNS):
            continue

        # Skip if this exact value is a numeric_anchor.
        if raw in anchor_values:
            continue
        if raw.rstrip("%") in anchor_values:
            continue

        # Either supported (swap) or unsupported (rephrase).
        suggested = _is_value_supported_by_substitution(
            raw, value_to_token)

        # June 28 2026 -- structural-prose exemption.
        # When the numeric sits inside a structural phrase
        # (S&P 500 / 100% equity / 60/40 / p < 0.005) AND the
        # value is NOT in the substitution table, skip the
        # violation. The "not in substitution table" guard
        # enforces the operator constraint: "Do not exempt any
        # value that appears in the substitution table." A
        # substitution-table value that ALSO happens to land
        # inside a structural pattern still flags as
        # token_available so the LLM swaps it for the token.
        if suggested is None:
            structural_name = _matches_structural_pattern(
                raw, text, span)
            if structural_name is not None:
                log.info(
                    "untoken_numeric_check_structural_exempt",
                    value=raw, pattern=structural_name)
                continue

        # Skip if the surrounding sentence looks like a citation.
        sentence = _sentence_containing(text, span)
        if _sentence_is_citation(sentence):
            continue

        violations.append(NumericViolation(
            raw_value=raw,
            sentence=sentence,
            suggested_token=suggested,
            severity=("token_available"
                      if suggested else "unsupported"),
            span=span,
        ))

    return violations


# ── Feedback construction ──────────────────────────────────────


def build_correction_prompt(
    original_prompt: str,
    violations: list[NumericViolation],
    iteration: int,
) -> str:
    """Construct a correction prompt for the next harness
    iteration. The LLM gets the original task back plus an
    explicit list of offending numerics with per-line guidance."""
    swap_lines = [
        v.to_feedback_line() for v in violations
        if v.suggested_token]
    rephrase_lines = [
        v.to_feedback_line() for v in violations
        if not v.suggested_token]

    sections: list[str] = [
        original_prompt,
        "",
        "REGENERATION FEEDBACK -- UNTOKEN-BACKED NUMERICS "
        f"(pass {iteration}/3):",
        "",
        "Your previous draft emitted numeric values that are "
        "not supported by the substitution layer. Every "
        "numeric finding in the prose MUST be one of:",
        "  1. A {{TOKEN}} placeholder from the substitution "
        "table (renders the live value at generation time)",
        "  2. A year (1900-2099) used as a citation date or "
        "era reference",
        "  3. A value from this section's locked "
        "numeric_anchors list",
        "",
    ]
    if swap_lines:
        sections.append(
            f"REPLACE these numerics with the matching "
            f"{{{{TOKEN}}}} placeholder:")
        sections.extend(swap_lines)
        sections.append("")
    if rephrase_lines:
        sections.append(
            "REPHRASE these sentences to remove the "
            "unsupported numeric entirely (no matching token "
            "in the substitution table):")
        sections.extend(rephrase_lines)
        sections.append("")
    sections.append(
        "Regenerate the section in full. Every other "
        "constraint from the original prompt still applies.")
    return "\n".join(sections)


# ── Persistence-failure error ──────────────────────────────────


class UntokenNumericLockError(RuntimeError):
    """Raised by the harness loop when 3 correction iterations
    fail to eliminate all untoken-backed numerics. The error
    payload carries the surviving violations so the operator can
    see exactly what the LLM couldn't fix -- usually a missing
    token in the substitution table that needs to be added.

    Catch + translate to HTTPException 500 at the generator
    endpoint so the user-facing failure surfaces the list."""

    def __init__(
        self, document_type: str,
        agent_id: str,
        violations: list[NumericViolation],
    ):
        offenders = "\n".join(
            v.to_feedback_line() for v in violations[:20])
        super().__init__(
            f"{document_type} generation failed: "
            f"{len(violations)} untoken-backed numeric(s) "
            f"survived 3 correction passes in section "
            f"'{agent_id}'. Operator likely needs to add a "
            f"missing {{{{TOKEN}}}} to the substitution table.\n"
            f"\nRemaining offenders:\n{offenders}")
        self.document_type = document_type
        self.agent_id = agent_id
        self.violations = violations
