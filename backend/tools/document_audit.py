"""tools/document_audit.py — deterministic post-generation audit.

Runs AFTER the LLM produces a generated document (executive brief or
presentation deck) and BEFORE the draft lands in editor_drafts. Pure-
Python, no LLM in any detection path — the spec's whole point was to
remove the LLM from its own quality-control loop.

Four checks:

  CHECK 1 — Numeric cross-reference
    Extract (strategy, metric, value) TUPLES from the text. Look up
    each against the authoritative cache (strategy_results_cache /
    academic_analytics / oos_summary / crisis_performance). Flag any
    value that disagrees with cache by more than 0.005 absolute.

  CHECK 2 — Label direction
    Loss metrics (CVaR, drawdown, volatility, max_drawdown, tail_risk)
    have natural lower-is-worse semantics. Any superlative
    (best / worst / highest / lowest / most severe / least severe)
    paired with a loss metric is ambiguous (could mean closest-to-zero
    or most-negative). Flag for human review.

  CHECK 3 — Cross-section consistency
    Group every extracted (strategy, metric, value) tuple. Flag any
    pair that appears with values differing by more than 0.05 across
    sections. The human resolves by adding explicit window disclosure
    when the divergence is window-driven (full-sample vs post-2022 vs
    crisis-window).

  CHECK 4 — Citation completeness
    Extract every Author (Year) / Author et al. (Year) citation.
    Cross-check against the document's own References section. Flag
    any cited author not present in the reference list.

DESIGN PRINCIPLES

  - **Never blocks the document write.** Every check raises only on
    truly malformed input; the dispatcher catches all exceptions
    inside any individual check and degrades that check to "skipped"
    in the result. The wider generator path is fail-open too.
  - **Strategy-name normalisation.** strategy_results_cache keys are
    display labels ("Regime Switching"); the generated text may use
    different spellings ("regime-switching", "REGIME_SWITCHING").
    Both sides normalise via _normalise_strategy_name so the lookup
    is robust.
  - **Tight extraction beats broad extraction.** Orphan numbers
    ("0.63 in the post-2022 window") are skipped — too noisy. Only
    numbers with an unambiguous (strategy, metric) attribution feed
    Check 1 and Check 3.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

import structlog

log = structlog.get_logger(__name__)


# ── Metric name registry ──────────────────────────────────────────────────
#
# Maps the colloquial metric names a generator might write into the
# canonical cache field name. Two parallel tables — loss vs gain —
# also drive Check 2's direction logic.

_GAIN_METRIC_NAMES: dict[str, str] = {
    "sharpe":          "sharpe_ratio",
    "sharpe ratio":    "sharpe_ratio",
    "sharpe_ratio":    "sharpe_ratio",
    "cagr":            "cagr",
    "annualised return": "cagr",
    "annualized return": "cagr",
    "alpha":           "alpha",
    "sortino":         "sortino_ratio",
    "sortino ratio":   "sortino_ratio",
    "calmar":          "calmar_ratio",
    "calmar ratio":    "calmar_ratio",
}

_LOSS_METRIC_NAMES: dict[str, str] = {
    "cvar":            "cvar_95",
    "cvar 95":         "cvar_95",
    "drawdown":        "max_drawdown",
    "max drawdown":    "max_drawdown",
    "max dd":          "max_drawdown",
    "maximum drawdown": "max_drawdown",
    "volatility":      "volatility",
    "ann volatility":  "volatility",
    "tail risk":       "cvar_95",
    "var":             "var_95",
    "var 95":          "var_95",
}

# Every metric-name token recognised by the audit. Used by the
# tuple extractor and Check 2's superlative scan.
_ALL_METRIC_NAMES: dict[str, str] = {
    **_GAIN_METRIC_NAMES, **_LOSS_METRIC_NAMES,
}

# Reverse — canonical → "loss" | "gain". Drives Check 2.
_METRIC_KIND: dict[str, str] = {}
for _n, _c in _GAIN_METRIC_NAMES.items():
    _METRIC_KIND[_c] = "gain"
for _n, _c in _LOSS_METRIC_NAMES.items():
    _METRIC_KIND[_c] = "loss"


_SUPERLATIVES_ANY: tuple[str, ...] = (
    "best", "worst", "highest", "lowest", "most severe", "least severe",
)


# Tolerances per the user's spec.
_NUMERIC_TOLERANCE = 0.005       # Check 1 (fraction-space comparison)
_NUMERIC_TOLERANCE_PP = 0.5      # Check 1 (percentage-point comparison)
_CONSISTENCY_TOLERANCE = 0.05    # Check 3

# Percent-scaled canonical metric names. When the cache stores a value
# in fraction form (-0.3527) and the prose surfaces it in percentage
# form (35.27%, sometimes without the % sign and sometimes with the
# sign stripped), the audit must compare on a normalised scale. The
# set below names every canonical metric the codebase persists as a
# decimal fraction; non-percent metrics (sharpe_ratio, etc.) bypass
# the scale step and compare on their stored numeric value directly.
_PERCENT_METRICS: set[str] = {
    "max_drawdown", "cagr", "volatility", "cvar", "tail_risk",
    "vol", "annual_return", "drawdown",
}

# Loss metrics where the cache stores a negative fraction (-0.3527)
# but the prose typically reads a positive percentage ("max drawdown
# of 35.27%"). Sign-stripping before comparison stops a legitimate
# magnitude match from flagging.
_SIGN_INVARIANT_METRICS: set[str] = {
    "max_drawdown", "drawdown", "cvar", "tail_risk",
}


def _normalise_audit_comparison(
    generated: float, cache_value: float, metric: str,
) -> tuple[float, float, str]:
    """Bring (generated, cache_value) onto a common scale + strip the
    sign for loss metrics so the numeric tolerance compares like-for-
    like. Returns (g, c, scale_label) where scale_label is "pp" when
    the comparison is in percentage points and "raw" otherwise.

    The shape:
      * Percent metrics: if one side is fraction-scale (abs < 1) and
        the other is percentage-scale (abs >= 1), multiply the
        fraction side by 100. After this both sides are in pp.
      * Loss metrics: abs() both sides so the cache's stored negative
        does not flag against the prose's stripped positive.

    Non-percent metrics (sharpe_ratio, etc.) skip the scale step
    and return the values unchanged in "raw" mode. This isolates the
    scale-normalisation to the metric kinds where it makes sense; a
    Sharpe of 0.86 vs a cache Sharpe of 0.537 IS a real mismatch
    and must still flag at the standard tolerance.
    """
    g, c = float(generated), float(cache_value)
    is_percent = metric in _PERCENT_METRICS
    is_loss = metric in _SIGN_INVARIANT_METRICS
    if is_percent:
        # Bring whichever side is the fraction up to percentage points
        # so the comparison is in a single coordinate system. The
        # threshold check below uses _NUMERIC_TOLERANCE_PP.
        if abs(g) < 1.0 and abs(c) >= 1.0:
            g = g * 100.0
        elif abs(c) < 1.0 and abs(g) >= 1.0:
            c = c * 100.0
        if is_loss:
            g, c = abs(g), abs(c)
        return g, c, "pp"
    if is_loss:
        g, c = abs(g), abs(c)
    return g, c, "raw"


# ── Result shape ──────────────────────────────────────────────────────────


@dataclass
class AuditResult:
    """Output of audit_document(). Shape consumed by the generator
    wiring + the document_audit_metrics writer + the frontend banner."""
    flags_by_check: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {
            "numeric": [], "direction": [],
            "consistency": [], "citation": [],
            # PR #333 -- story_plan_violation flags fire when a slide
            # carries a numeric value not in its locked story plan's
            # numeric_anchors AND not in the precomputed cache. Empty
            # when no plan is supplied (skipped).
            "story_plan": [],
            # PR #336 -- brief-only checks.
            "required_citations": [],
            "section_word_count": [],
            # June 21 2026 -- numeric substitution architecture checks
            # (brief only in this layer; deck + appendix wire in
            # Layer-2 PR alongside their substitution call-sites).
            "unresolved_placeholders": [],
            "raw_numeric": [],
            # June 28 2026 (PR β) -- table structure validation.
            # Walks content_json for every TipTap table node + checks
            # against REQUIRED_TABLE_COLUMNS registry. Skipped when
            # content_json is not passed in (text-only callers).
            "table_structure": [],
        })
    skipped: dict[str, str] = field(default_factory=dict)  # check_name → reason

    @property
    def flag_counts(self) -> dict[str, int]:
        return {
            "numeric":     len(self.flags_by_check["numeric"]),
            "direction":   len(self.flags_by_check["direction"]),
            "consistency": len(self.flags_by_check["consistency"]),
            "citation":    len(self.flags_by_check["citation"]),
            "story_plan":  len(self.flags_by_check.get("story_plan", [])),
            "required_citations": len(
                self.flags_by_check.get("required_citations", [])),
            "section_word_count": len(
                self.flags_by_check.get("section_word_count", [])),
            "unresolved_placeholders": len(
                self.flags_by_check.get("unresolved_placeholders", [])),
            "raw_numeric": len(
                self.flags_by_check.get("raw_numeric", [])),
            "table_structure": len(
                self.flags_by_check.get("table_structure", [])),
            "total": sum(len(v) for v in self.flags_by_check.values()),
        }

    @property
    def has_any_flag(self) -> bool:
        return self.flag_counts["total"] > 0


# ── Helpers ───────────────────────────────────────────────────────────────


def _normalise_strategy_name(name: str | None) -> str:
    """Strategy lookup is robust to casing / hyphenation differences.
    "Regime Switching" / "regime-switching" / "REGIME_SWITCHING"
    all collapse to "regimeswitching"."""
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _parse_number(s: str) -> float | None:
    """A numeric token from the text → float, normalised to the
    cache's representation. Percentages convert to decimal so
    "7.79%" matches the cache's 0.0779; bare numbers pass through."""
    s = s.strip()
    is_pct = s.endswith("%")
    if is_pct:
        s = s[:-1].strip()
    s = s.replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return None
    if is_pct:
        v = v / 100.0
    return v


_NUMBER_RE = r"(-?\d+(?:\.\d+)?%?)"


# Tuple extractor — regex pattern catches the common attribution
# shapes the brief and the deck use:
#   "{Strategy} ... {Metric} ... {value}"   ("Regime Switching's Sharpe of 0.63")
#   "{Metric} ... {value} ... {Strategy}"   ("CAGR of 7.79% for Regime Switching")
#   "{Strategy}: {Metric} {value}"          ("Regime Switching: Sharpe 0.6291")
#
# The patterns are deliberately tight — orphan numbers and
# numbers without both a strategy AND a metric are skipped.

def _extract_attributed_numbers(
    text: str, known_strategies: Iterable[str],
) -> list[dict[str, Any]]:
    """Scan `text` for (strategy, metric, value) tuples.

    A tuple lands ONLY when both the strategy name AND a recognised
    metric name appear within 80 characters of the number. Returns
    [{strategy, metric_canonical, value, raw_match, section_hint}].
    """
    if not text:
        return []
    out: list[dict[str, Any]] = []
    strategies_norm = {s: _normalise_strategy_name(s) for s in known_strategies}
    metric_alts = sorted(_ALL_METRIC_NAMES.keys(), key=len, reverse=True)
    metric_re = "|".join(re.escape(m) for m in metric_alts)

    # Walk every number in the text.
    for m in re.finditer(_NUMBER_RE, text):
        val_raw = m.group(1)
        val = _parse_number(val_raw)
        if val is None:
            continue
        # Skip citation-year numbers in parentheses ("Sharpe (1994)",
        # "Bailey et al. (2014)"). Years are 4-digit integers >=
        # 1900; the open paren immediately before the number is the
        # giveaway. Without this filter the extractor attributes
        # "1994" to whatever strategy is in the surrounding sentence.
        prev_char = text[m.start() - 1] if m.start() > 0 else ""
        is_year_like = (
            "." not in val_raw and "%" not in val_raw
            and 1900 <= val <= 2100)
        if prev_char == "(" and is_year_like:
            continue
        # Window: 80 chars before and after the number. Wide enough
        # to catch "Regime Switching's Sharpe ratio of 0.6291" and
        # narrow enough to avoid attributing across paragraph boundaries.
        lo = max(0, m.start() - 80)
        hi = min(len(text), m.end() + 80)
        window = text[lo:hi]
        window_lc = window.lower()

        # Strategy hit — any normalised name appearing in the lower
        # window. Pick the longest match to disambiguate
        # "Max Sharpe Rolling" from "Sharpe".
        strategy_hit: str | None = None
        for display_name, norm in sorted(
                strategies_norm.items(), key=lambda kv: -len(kv[1])):
            if norm and norm in _normalise_strategy_name(window):
                strategy_hit = display_name
                break
        if not strategy_hit:
            continue

        # Metric hit — first metric name in the window. Take the
        # one closest to the number (tightest attribution).
        metric_hit: str | None = None
        best_dist = 10**9
        for mm in re.finditer(metric_re, window_lc):
            mid = (mm.start() + mm.end()) // 2
            dist = abs(mid - (m.start() - lo))
            if dist < best_dist:
                best_dist = dist
                metric_hit = mm.group(0)
        if not metric_hit:
            continue

        canonical = _ALL_METRIC_NAMES.get(metric_hit)
        if not canonical:
            continue
        out.append({
            "strategy":          strategy_hit,
            "metric":            canonical,
            "metric_token":      metric_hit,
            "value":             val,
            "raw_match":         m.group(0),
            "window":            window.strip(),
        })
    return out


# ── CHECK 1 — Numeric cross-reference ─────────────────────────────────────


def check_numeric_cross_reference(
    tuples: list[dict[str, Any]],
    strategy_cache: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Compare each extracted tuple against strategy_results_cache.

    Scale-aware: percent metrics (max_drawdown, cagr, etc.) compare in
    percentage-point space at _NUMERIC_TOLERANCE_PP, with the fraction
    side multiplied by 100 when the cache stores the value as a
    fraction (-0.3527) and the prose surfaces it as a percentage
    (35.27%). Loss metrics also strip sign so a positively-quoted
    drawdown doesn't flag against the cache's stored negative. Non-
    percent metrics (sharpe_ratio, etc.) keep the original
    fraction-space comparison at _NUMERIC_TOLERANCE -- a Sharpe of
    0.86 vs cache 0.537 IS a real mismatch and must still surface.
    """
    flags: list[dict[str, Any]] = []
    if not strategy_cache:
        return flags
    # Build a normalised lookup so the audit is robust to the cache's
    # storage convention (which is display labels).
    cache_norm: dict[str, dict[str, Any]] = {
        _normalise_strategy_name(k): v for k, v in strategy_cache.items()
    }
    for t in tuples:
        key = _normalise_strategy_name(t["strategy"])
        row = cache_norm.get(key)
        if row is None:
            # Strategy not in cache — skip, don't flag.
            continue
        cache_value = row.get(t["metric"])
        if cache_value is None:
            continue
        try:
            cache_value = float(cache_value)
        except (TypeError, ValueError):
            continue
        g_norm, c_norm, scale = _normalise_audit_comparison(
            float(t["value"]), cache_value, t["metric"])
        diff = abs(g_norm - c_norm)
        tol = (_NUMERIC_TOLERANCE_PP if scale == "pp"
               else _NUMERIC_TOLERANCE)
        if diff > tol:
            flags.append({
                "strategy":  t["strategy"],
                "metric":    t["metric"],
                "generated": t["value"],
                "cache":     cache_value,
                # Scale + tolerance the comparison ran under so the
                # frontend can render the diff in the right units.
                "scale":     scale,
                "diff":      round(diff, 6),
                "context":   t["window"][:200],
            })
    return flags


# ── CHECK 2 — Label direction ─────────────────────────────────────────────


def check_label_direction(text: str) -> list[dict[str, Any]]:
    """Scan for SUPERLATIVE + METRIC pairings. Loss-metric pairings
    are ambiguous and flagged for review per the user's spec ('flag
    any sentence where the superlative direction conflicts with the
    metric type', strict reading).

    English idiom puts the metric AFTER the superlative ('lowest
    drawdown', 'highest Sharpe'); we restrict the search window to
    the 60 chars FOLLOWING the superlative so a strategy name
    containing a metric-like substring earlier in the sentence
    ('Volatility Targeting has the lowest drawdown') doesn't false-
    positive on 'volatility'. The metric scan uses word-boundary
    regex so 'volatility' doesn't match 'Volatility' as a substring
    inside 'Volatility Targeting'.
    """
    if not text:
        return []
    flags: list[dict[str, Any]] = []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sentence in sentences:
        s_lc = sentence.lower()
        for sup in _SUPERLATIVES_ANY:
            if sup not in s_lc:
                continue
            for sup_m in re.finditer(rf"\b{re.escape(sup)}\b", s_lc):
                # Window: ONLY the 60 chars after the superlative.
                # English puts the metric after ("lowest drawdown");
                # this also avoids attributing a strategy-name
                # substring that sits earlier in the sentence.
                window_lo = sup_m.end()
                window_hi = min(len(s_lc), window_lo + 60)
                window = s_lc[window_lo:window_hi]
                # Pick the metric CLOSEST to the superlative (smallest
                # match.start()) so adjacency wins over alphabetic
                # order when multiple metric tokens appear.
                best_metric: str | None = None
                best_pos = 10**9
                for metric_name in _ALL_METRIC_NAMES.keys():
                    # Single-word metrics use word boundaries; multi-
                    # word ones are scanned as literal phrases.
                    if " " in metric_name:
                        mm = re.search(re.escape(metric_name), window)
                    else:
                        mm = re.search(
                            rf"\b{re.escape(metric_name)}\b", window)
                    if mm and mm.start() < best_pos:
                        best_pos = mm.start()
                        best_metric = metric_name
                if not best_metric:
                    continue
                canonical = _ALL_METRIC_NAMES[best_metric]
                kind = _METRIC_KIND.get(canonical)
                if kind == "loss":
                    flags.append({
                        "superlative": sup,
                        "metric":      canonical,
                        "metric_token": best_metric,
                        "sentence":    sentence.strip()[:240],
                    })
    return flags


# ── CHECK 3 — Cross-section consistency ───────────────────────────────────


def check_cross_section_consistency(
    tuples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group all extracted (strategy, metric) tuples and flag any
    group whose value range exceeds _CONSISTENCY_TOLERANCE. The
    human resolves by adding window disclosure if the divergence
    is window-driven."""
    if not tuples:
        return []
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for t in tuples:
        grouped[(t["strategy"], t["metric"])].append(float(t["value"]))
    flags: list[dict[str, Any]] = []
    for (strategy, metric), values in grouped.items():
        if len(values) < 2:
            continue
        spread = max(values) - min(values)
        if spread > _CONSISTENCY_TOLERANCE:
            flags.append({
                "strategy":   strategy,
                "metric":     metric,
                "values":     [round(v, 6) for v in values],
                "spread":     round(spread, 6),
                "note": (
                    "If these values come from different periods "
                    "(full-sample vs post-2022 vs crisis-window), "
                    "add an explicit window label before submitting."
                ),
            })
    return flags


# ── CHECK 4 — Citation completeness ───────────────────────────────────────


# Narrative-style: "Harvey (2016)", "Lopez et al. (2018)". The author
# name sits OUTSIDE the parens; the year is INSIDE. Captures the
# author run before the opening paren.
_CITATION_RE = re.compile(
    r"\b([A-Z][A-Za-z\-]+(?:\s+et\s+al\.?)?)\s*\((\d{4})\)"
)


# Parenthetical-style: "(Harvey, 2016)", "(Harvey & Liu, 2016)",
# "(Harvey, Liu, & Zhu, 2016)", "(Harvey, Liu, Zhu, & Brown, 2016)".
# The author run + year both sit INSIDE one paren pair. Captures the
# entire author run as group 1 and the year as group 2 so the caller
# can index the citation by its FIRST author surname (the APA
# convention: cite by first author, reference by first author).
#
# Previously this pattern wasn't matched at all -- only the narrative
# form was extracted -- so a brief that used parenthetical multi-
# author citations had its citations missing from the dispatcher's
# coverage check. June 21 2026 fix.
_PAREN_CITATION_RE = re.compile(
    r"\(([A-Z][A-Za-z\-]+"               # first author surname
    r"(?:,?\s+(?:&|and)\s+[A-Z][A-Za-z\-]+)?"  # & SecondAuthor (2-author)
    r"(?:,\s+[A-Z][A-Za-z\-]+)*"         # , Author3, Author4, ...
    r"(?:,?\s+(?:&|and)\s+[A-Z][A-Za-z\-]+)?"  # & LastAuthor (3+ authors)
    r"(?:\s+et\s+al\.?)?"                # or "et al."
    r"),\s+(\d{4})\)"                    # , YYYY)
)


def _first_author_surname(author_group: str) -> str:
    """Extract the first author surname from a captured citation
    group. Handles every shape the regexes capture:
      "Harvey"                          -> "Harvey"
      "Harvey et al."                   -> "Harvey"
      "Harvey & Liu"                    -> "Harvey"
      "Harvey, Liu, & Zhu"              -> "Harvey"
      "Harvey, Liu, Zhu, & Brown"       -> "Harvey"

    APA practice: multi-author citations are indexed by first author
    in the References section. This helper centralises that contract
    so both regex paths (narrative + parenthetical) use the same
    lookup rule."""
    token = author_group.strip()
    # Strip trailing "et al." first so it doesn't pollute the split.
    token = re.sub(
        r"\s+et\s+al\.?$", "", token, flags=re.IGNORECASE).strip()
    # Split on the first separator -- comma OR ampersand OR "and".
    # Whichever comes first wins.
    parts = re.split(r"\s*[,&]\s*|\s+and\s+", token, maxsplit=1)
    return parts[0].strip() if parts else token


def _extract_references_section(
    text: str, document_type: str,
) -> str | None:
    """Return the concatenated body text of EVERY References section
    in the document. Briefs sometimes carry one bibliography at the
    end (`## References`) and sometimes per-section reference blocks
    (`## References for Section 2`, `## References for Section 3`,
    etc.); the earlier implementation found only the FIRST heading
    match and missed every per-section block after it.

    For the deck the references slide carries the bibliography in
    its body / bullets; deck_slides_to_editor concatenates slides
    so the same string scan works.

    When no References heading is found at all, we fall back to the
    whole document text -- some generations inline the bibliography
    without a heading, and the author + year string-match still
    works against the full text (false positives from in-text
    citations are filtered by the cited-set lookup logic). Returns
    None only when text itself is empty.

    Concrete fix scope (June 21 2026): a brief that cited
    Hamilton (1989) / Carhart (1997) / Markowitz (1952) in per-
    section reference blocks scored 3x "missing from references"
    false positives because the single-section extractor only saw
    the first block."""
    if not text:
        return None
    # Match EVERY `#+ References...` heading and take its body up to
    # the next `#+ ` heading (any level) or end-of-text. Concatenate
    # all matches. The greedy lookahead `(?=\n#+\s|\Z)` stops the
    # body at the next heading without consuming it.
    matches = re.findall(
        r"(?:^|\n)#+\s*References\b[^\n]*\n([\s\S]*?)(?=\n#+\s|\Z)",
        text, re.IGNORECASE)
    if matches:
        return "\n\n".join(m.strip() for m in matches if m.strip())
    # Deck fallback — a slide may surface "References" without
    # markdown headings. Grab everything from "References" to end.
    m = re.search(
        r"\bReferences\b\s*\n([\s\S]+)\Z", text)
    if m:
        return m.group(1)
    # No References heading at all -- fall back to scanning the full
    # text. The author + year lookup is permissive enough that
    # inline bibliographies still match.
    return text


def check_citation_completeness(
    text: str, document_type: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """Returns (flags, skip_reason). Skip reason set when the
    References section couldn't be located (deck without a refs
    slide, etc.) — the caller records it as "skipped" rather than
    silently dropping the check.

    Citation extraction handles BOTH narrative-style and
    parenthetical-style citations (June 21 2026 fix):
      Narrative:        "Harvey (2016) shows ..."
      Parenthetical:    "(Harvey, 2016)"
      Multi-author:     "(Harvey, Liu, & Zhu, 2016)"

    Multi-author citations are indexed by FIRST author surname per
    APA convention -- the lookup against the References section uses
    only the first author. Before this fix, the narrative regex
    missed the parenthetical form entirely, so a brief that used
    parenthetical multi-author citations had its citation coverage
    silently under-counted (and Bob's section that cited
    "(Harvey, Liu, & Zhu, 2016)" produced spurious flags for
    "Liu" / "Zhu" through the secondary scan)."""
    if not text:
        return [], None
    references_body = _extract_references_section(text, document_type)
    if references_body is None:
        return [], "no References section found"
    refs_lc = references_body.lower()
    cited: set[tuple[str, str]] = set()
    # Narrative-style citations -- author run sits OUTSIDE the paren.
    for m in _CITATION_RE.finditer(text):
        author = _first_author_surname(m.group(1))
        if author:
            cited.add((author, m.group(2)))
    # Parenthetical-style citations -- entire (author run, year)
    # sits INSIDE one paren pair. Treat each match as a SINGLE
    # citation; the first-author surname is the lookup key.
    for m in _PAREN_CITATION_RE.finditer(text):
        author = _first_author_surname(m.group(1))
        if author:
            cited.add((author, m.group(2)))
    flags: list[dict[str, Any]] = []
    for author, year in sorted(cited):
        present = (author.lower() in refs_lc) and (year in refs_lc)
        if not present:
            flags.append({
                "author": author,
                "year":   year,
                "note":   "Cited in body, not found in References section.",
            })
    return flags, None


# ── CHECK 5 — Story plan violation (PR #333, deck only) ──────────────────


_STORY_PLAN_TOLERANCE = 0.01


def check_story_plan_violations(
    slides: list[dict[str, Any]],
    story_plan_slides: list[dict[str, Any]] | None,
    *,
    strategy_cache: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Flag numeric values that appear on a slide but are NOT in either
    (a) the slide's locked numeric_anchors from the story plan, within
    _STORY_PLAN_TOLERANCE, OR (b) the precomputed strategy cache. A
    flag means the per-slide LLM has substituted a number the locked
    plan did not authorise.

    Skipped silently (returns []) when story_plan_slides is falsy --
    when the deck generation ran without a plan (cold cache, fallback,
    or pre-PR-333 environment) this check has no opinion.

    slides -- the parsed AI JSON list as it arrives at the deck builder
    (one dict per slide, with bullets / table_data / speaker_notes /
    slide_number). Each value found in bullets or table cells is
    cross-checked against the matching plan entry's numeric_anchors.
    """
    if not slides or not story_plan_slides:
        return []
    by_slide_number = {
        e.get("slide_number"): e
        for e in story_plan_slides
        if isinstance(e, dict)
    }
    cache_norm: dict[str, dict[str, Any]] = {}
    if strategy_cache:
        cache_norm = {
            _normalise_strategy_name(k): v
            for k, v in strategy_cache.items()
        }
    flags: list[dict[str, Any]] = []
    for sl in slides:
        if not isinstance(sl, dict):
            continue
        slide_number = sl.get("slide_number")
        plan_entry = by_slide_number.get(slide_number)
        if not plan_entry:
            continue
        anchors_raw = plan_entry.get("numeric_anchors") or {}
        # Normalise the anchor values to floats for comparison.
        anchor_values: list[float] = []
        for v in anchors_raw.values():
            try:
                anchor_values.append(float(v))
            except (TypeError, ValueError):
                continue
        for token, val in _iter_slide_numbers(sl):
            # Anchored: matches any anchor within tolerance.
            if any(abs(val - a) <= _STORY_PLAN_TOLERANCE
                   for a in anchor_values):
                continue
            # Cache-backed: any strategy row in cache carries this
            # value within the strict numeric tolerance.
            if _value_in_cache(val, cache_norm):
                continue
            flags.append({
                "type": "story_plan_violation",
                "slide": slide_number,
                "value": val,
                "token": token,
                "message": (
                    "Numeric value not in story plan anchors or cache"),
            })
    return flags


def _iter_slide_numbers(slide: dict[str, Any]):
    """Yield (raw_token, parsed_value) for every numeric token found
    in a slide's bullets and table cells. Bullets are scanned for
    standalone numbers; the slide title and speaker_notes are NOT
    scanned (the audit narrows to the visible-on-slide surface)."""
    bullets = slide.get("bullets") or []
    for b in bullets:
        if not isinstance(b, str):
            continue
        for m in re.finditer(_NUMBER_RE, b):
            tok = m.group(1)
            v = _parse_number(tok)
            if v is not None:
                yield tok, v
    table = slide.get("table_data") or {}
    rows = table.get("rows") if isinstance(table, dict) else None
    for row in (rows or []):
        if not isinstance(row, list):
            continue
        for cell in row:
            if cell is None:
                continue
            cell_s = str(cell)
            for m in re.finditer(_NUMBER_RE, cell_s):
                tok = m.group(1)
                v = _parse_number(tok)
                if v is not None:
                    yield tok, v


def check_brief_story_plan_violations(
    content_text: str,
    brief_section_plan: dict[str, Any] | None,
    *,
    strategy_cache: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """PR #336 -- brief counterpart of check_story_plan_violations.

    Flags numeric values in the brief body that appear in NEITHER
    (a) any section's locked numeric_anchors from the story plan,
    within _STORY_PLAN_TOLERANCE, NOR (b) the precomputed strategy
    cache. A flag means the per-section LLM substituted a number the
    locked plan did not authorise.

    Skipped silently (returns []) when brief_section_plan is falsy --
    when the brief generation ran without a plan (cold cache, fallback,
    or pre-PR-333 environment) this check has no opinion.

    brief_section_plan -- the {section_key -> {key_message,
    numeric_anchors, target_length_words}} dict produced by
    generate_brief_section_plan() and persisted via story_plans
    (document_type='brief'). Unlike the deck variant, the brief plan
    is a flat dict of named sections rather than a list of slides;
    every section's anchors contribute to the UNION of allowed
    values (the body prose can quote ANY section's anchor without
    flagging since the brief is one continuous document, not slide
    silos).
    """
    if not content_text or not brief_section_plan:
        return []
    if not isinstance(brief_section_plan, dict):
        return []
    # Build the union of every section's numeric_anchors -- the brief
    # is one continuous document, so an anchor from §3 quoted in §1
    # is legitimate. Flagging only happens when the value is in NO
    # section's anchors.
    anchor_values: list[float] = []
    for section_entry in brief_section_plan.values():
        if not isinstance(section_entry, dict):
            continue
        anchors_raw = section_entry.get("numeric_anchors") or {}
        for v in anchors_raw.values():
            try:
                anchor_values.append(float(v))
            except (TypeError, ValueError):
                continue
    if not anchor_values:
        return []
    cache_norm: dict[str, dict[str, Any]] = {}
    if strategy_cache:
        cache_norm = {
            _normalise_strategy_name(k): v
            for k, v in strategy_cache.items()
        }
    flags: list[dict[str, Any]] = []
    seen: set[float] = set()
    for m in re.finditer(_NUMBER_RE, content_text):
        tok = m.group(1)
        val = _parse_number(tok)
        if val is None:
            continue
        # Skip citation-year numbers in parentheses ("(1989)" etc.) --
        # the same year-suppression heuristic the attributed-number
        # extractor uses. Without this filter every Hamilton (1989)
        # citation in the body would flag as a story-plan violation
        # for the value 1989.
        prev_char = (content_text[m.start() - 1]
                     if m.start() > 0 else "")
        is_year_like = (
            "." not in tok and "%" not in tok
            and 1900 <= val <= 2100)
        if prev_char == "(" and is_year_like:
            continue
        # De-dupe: a value cited multiple times only flags once.
        if val in seen:
            continue
        if any(abs(val - a) <= _STORY_PLAN_TOLERANCE
               for a in anchor_values):
            continue
        if _value_in_cache(val, cache_norm):
            continue
        seen.add(val)
        flags.append({
            "type": "story_plan_violation",
            "value": val,
            "token": tok,
            "message": (
                "Numeric value not in story plan anchors or cache"),
        })
    return flags


# ── CHECK 6 — Required citations (PR #336, brief only) ──────────────────


# Map each VERIFIED_CITATIONS key to the (author_tokens, year) the
# in-text check looks for. Author tokens are matched case-insensitively
# anywhere in the body; year is matched as a literal substring. Hardcoded
# so this module does NOT depend on parsing the long bibliographic
# strings out of VERIFIED_CITATIONS at runtime (a single tokenisation
# bug would silently relax every citation check). The reverse map below
# is asserted in tests so a key drift in VERIFIED_CITATIONS is caught.
_REQUIRED_CITATION_PATTERNS: dict[str, tuple[tuple[str, ...], str]] = {
    "hamilton_1989":    (("Hamilton",), "1989"),
    "ang_bekaert_2002": (("Ang", "Bekaert"), "2002"),
    "markowitz_1952":   (("Markowitz",), "1952"),
    "carhart_1997":     (("Carhart",), "1997"),
    "sharpe_1994":      (("Sharpe",), "1994"),
    "fama_french_1993": (("Fama", "French"), "1993"),
    "lo_2002":          (("Lo",), "2002"),
}


# ── CHECK 8 — Unresolved placeholders (substitution architecture) ──────
#
# The numeric-substitution architecture (June 21 2026) replaces raw
# numeric figures in the Sonnet output with {{TOKEN}} placeholders
# that the platform substitutes against the verified strategy cache
# before the evaluator sees the prose. A surviving {{...}} token in
# the final document means the writer invented a token name the
# substitution table didn't anticipate. That's a high-severity
# operator signal: either add the token to
# tools.numeric_substitution.build_substitution_table or rewrite
# the section to use an existing token.


def check_unresolved_placeholders(
    content_text: str,
    substitution_table: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Returns one flag per distinct unresolved {{TOKEN}}. Empty
    output is the green state. Fail-open: a missing import or any
    other error leaves the dispatcher with `skipped[...]` -- the
    rest of the audit still runs.

    June 28 2026 -- substitution_table kwarg added for the
    Phase-1 deferred-substitution pipeline. When supplied, the
    function applies substitution to content_text FIRST + then
    checks the substituted projection for residual {{TOKEN}}
    patterns. Under deferred substitution, content_text
    legitimately carries {{TOKEN}} placeholders that the
    substitution layer WILL resolve at export time -- those
    tokens are not 'unresolved' in the audit's sense and must
    not be flagged. Only tokens that have NO substitution-table
    entry (typo / missing / forgotten) survive the
    pre-substitution + remain flagged. When the kwarg is None
    (legacy generation), the check is unchanged: any {{TOKEN}}
    in content_text fires a flag."""
    try:
        from tools.numeric_substitution import (
            apply_substitutions, unresolved_placeholders,
        )
    except Exception:
        return []
    text_to_check = content_text
    if substitution_table:
        try:
            substituted, _replaced = apply_substitutions(
                content_text, substitution_table)
            text_to_check = substituted
        except Exception:
            # Fail-open -- check the raw text in legacy mode
            # rather than skipping the check entirely.
            text_to_check = content_text
    flags: list[dict[str, Any]] = []
    for token in unresolved_placeholders(text_to_check):
        flags.append({
            "type":     "unresolved_placeholder",
            "token":    token,
            "severity": "high",
            "message": (
                f"Unresolved placeholder {token} in document body. "
                "This token was emitted by the Sonnet writer but is "
                "not in the substitution table. Either add it to "
                "tools.numeric_substitution.build_substitution_table "
                "(if the figure exists in the cache) or rewrite the "
                "section to use an existing token. A document with "
                "any unresolved placeholder must NOT be submitted."),
        })
    return flags


def check_unverified_tags(
    content_text: str,
) -> list[dict[str, Any]]:
    """June 28 2026 -- detect <unverified>...</unverified> tags
    inserted by the hard-lock soft-fail path in
    tools/academic_export.harness_narrative.

    Each tag wraps a raw numeric that survived all 3 correction
    passes -- the Sonnet writer couldn't be driven to swap it
    for the corresponding {{TOKEN}} OR to rephrase the prose to
    remove it. Operator's June 28 directive: persist the best-
    attempt narrative with the offenders tagged inline + surface
    each tag as an audit flag so Bob + Molly review before
    submission.

    Each tag fires one flag. Empty output is the green state.

    Severity: 'high' -- raw numerics in a submission document
    require human sign-off before the brief leaves the platform.
    """
    if not content_text:
        return []
    import re as _re
    flags: list[dict[str, Any]] = []
    seen: set[str] = set()
    pattern = _re.compile(
        r"<unverified>(.*?)</unverified>", _re.DOTALL)
    for m in pattern.finditer(content_text):
        raw_value = m.group(1).strip()
        if not raw_value or raw_value in seen:
            continue
        seen.add(raw_value)
        # Snippet of surrounding context (60 chars each side).
        start = max(0, m.start() - 60)
        end = min(len(content_text), m.end() + 60)
        snippet = content_text[start:end].replace("\n", " ")
        flags.append({
            "type":      "unverified_numeric",
            "value":     raw_value,
            "severity":  "high",
            "snippet":   snippet,
            "message": (
                f"Unverified raw numeric '{raw_value}' wrapped by "
                "the hard-lock soft-fail path -- the Sonnet writer "
                "could not be driven to swap this value for a "
                "{{TOKEN}} placeholder or to rephrase the sentence "
                "to remove it after 3 correction passes. Bob / "
                "Molly: review the surrounding prose, replace the "
                "<unverified>...</unverified> wrapper with either "
                "(a) the correct {{TOKEN}} from the data reference "
                "sheet, or (b) a rephrased sentence that does not "
                "claim a specific numeric value. The brief MUST NOT "
                "be submitted while any <unverified> tag remains in "
                "the document body."),
        })
    return flags


# ── CHECK 9 — Raw numeric in body (substitution architecture) ──────────
#
# A complementary signal to check_unresolved_placeholders: the
# writer emitted a raw decimal figure (e.g. "1.24") despite the
# placeholder guide's "use {{OOS_SHARPE_BLEND}} not the raw number"
# instruction. The pattern is conservative -- only Sharpe/correlation-
# shaped decimals get flagged, and only outside currency/date
# contexts (a year "2026", a dollar amount "$1.24", a section number
# "5.1" are all exempt). Medium severity: the value MAY be correct
# but was sourced from the model's prior knowledge rather than the
# cache, which is what the substitution architecture eliminates.

# Captures "0.NN" or "1.NN" decimals that look like Sharpe ratios
# or correlation values. 2-3 trailing digits keeps the pattern
# tight (a "0.5" or "1.2345" is unlikely to be a misplaced metric).
_RAW_NUMERIC_PATTERN = re.compile(
    r"(?<![\$\d/.])\b([01]\.\d{2,3})\b(?![/.\d%])",
)


def check_numeric_consistency(
    content_text: str,
) -> list[dict[str, Any]]:
    """Returns one flag per distinct raw decimal figure in the body
    that fits the Sharpe/correlation shape. Empty output is the
    green state.

    The intent is NOT to forbid every literal number in prose --
    it's to catch the specific failure mode where the writer wrote
    a Sharpe ratio inline (bypassing the substitution architecture)
    instead of using the {{OOS_SHARPE_BLEND}} / {{BENCHMARK_SHARPE}}
    tokens. A flagged value might be correct, but its provenance
    bypassed the cache. The operator decides whether to accept
    (mark resolved) or rewrite the line.

    Currency ($1.24), section numbers (5.1, 5.2), and dates (2026)
    are exempt -- the regex uses lookarounds to skip them."""
    flags: list[dict[str, Any]] = []
    if not content_text:
        return flags
    seen: set[str] = set()
    for match in _RAW_NUMERIC_PATTERN.finditer(content_text):
        value = match.group(1)
        if value in seen:
            continue
        seen.add(value)
        # Capture ~40 chars of surrounding context for the flag
        # message so the operator can locate the line without
        # grepping the document themselves.
        start = max(0, match.start() - 20)
        end = min(len(content_text), match.end() + 20)
        snippet = content_text[start:end].replace("\n", " ").strip()
        flags.append({
            "type":     "raw_numeric_found",
            "value":    value,
            "severity": "medium",
            "context":  snippet,
            "message": (
                f"Raw decimal {value!r} in document body (context: "
                f"\"{snippet}\"). The substitution architecture "
                "expects every Sharpe / correlation value to come "
                "from a {{TOKEN}} that's resolved against the cache. "
                "Either replace with the appropriate token from "
                "tools.numeric_substitution.build_substitution_table "
                "or confirm this is a non-metric figure (date, "
                "section number, etc) and mark resolved."),
        })
    return flags


# ── CHECK 10 — Cross-deliverable consistency (Layer 2, June 21 2026) ───
#
# Brief + deck + appendix all consume the SAME substitution table
# instance (via get_substitution_table cached by data_hash). That
# means a value substituted into the brief is structurally identical
# to the same value substituted into the deck -- no drift is
# possible at substitution time.
#
# What CAN drift: a manual edit. Bob opens the brief editor, tweaks
# prose around "1.24", and accidentally types "1.23" instead.
# Substitution was correct at generation time; the edit introduced
# drift after the fact. This check fires post-edit (when the editor
# export re-runs the audit) by scanning each document for the
# verified substituted values from the shared table and flagging
# any value found in one document that disagrees with the same
# value found in another.
#
# Scope: only values that appear in at least TWO documents are
# checked. A value that lives only in the appendix (an MIN_VARIANCE
# strategy metric the brief doesn't quote) doesn't cross-deliverable-
# drift because there's no second deliverable to drift against.


def _close_numeric_variants(value: str, text: str) -> list[str]:
    """Find variants of a substituted value in text that look like
    rounding / formatting corruptions of it. Examples:
      value="1.24" -> "1.2", "1.240", "1.23", "1.25" are flagged
      value="-52.6%" -> "-52.5%", "-52.7%", "-53%" are flagged

    Conservative: only flags strings that share the integer part of
    the value (so "1.24" doesn't drag in "0.24" or "2.24" which are
    distinct figures). Skips an exact match -- we're only after
    variants, not the canonical value itself."""
    if not value or not text:
        return []
    # Strip sign + percent so the comparison runs on the magnitude.
    stripped = value.lstrip("+-").rstrip("%").rstrip("pp")
    try:
        canonical = float(stripped)
    except (TypeError, ValueError):
        return []
    integer_part = str(int(canonical)) if canonical == int(canonical) \
        else stripped.split(".")[0]
    # Find every decimal number in the text and compare magnitudes.
    variant_pattern = re.compile(
        rf"-?\b{re.escape(integer_part)}(?:\.\d{{1,3}})?%?")
    found: set[str] = set()
    for m in variant_pattern.finditer(text):
        candidate = m.group(0)
        if candidate == value:
            continue
        # Strip sign + percent on candidate side too.
        cand_stripped = candidate.lstrip("+-").rstrip("%").rstrip("pp")
        try:
            cand_val = float(cand_stripped)
        except (TypeError, ValueError):
            continue
        # Same integer part + different decimal => variant. Threshold
        # of 0.5 on the magnitude difference keeps "1.24" vs "2.24"
        # from cross-flagging (those are unambiguously different
        # figures); "1.24" vs "1.23" (0.01 difference) lands as a
        # variant.
        if abs(cand_val - canonical) > 0 and \
                abs(cand_val - canonical) < 0.5:
            found.add(candidate)
    return sorted(found)


def check_cross_deliverable_consistency(
    documents: dict[str, str],
    substitution_table: dict[str, str],
) -> list[dict[str, Any]]:
    """Confirms substituted values are consistent across deliverables.

    documents: {"executive_brief": <content>,
                "presentation_deck": <content>,
                "analytical_appendix": <content>}
        Empty / missing keys are tolerated -- the check operates on
        whichever documents the caller passes.

    substitution_table: the shared {token -> value} map produced by
        get_substitution_table for the same data_hash all three
        documents were generated against.

    Flag shape:
        type="cross_deliverable_drift"
        severity="high"
        token=<the source placeholder>
        expected=<the verified value from the table>
        found=<the variant present in the document>
        document=<which document carries the variant>

    A clean cross-deliverable check is the green state: every shared
    value matches the table verbatim in every document it appears in.

    Implementation note: the substitution layer guarantees consistency
    at GENERATION time (same table instance feeds all three). This
    check is a POST-EDIT guard -- a manual edit in the editor can
    silently introduce drift after the substitution layer has fired.
    The check belongs in the editor-export re-audit path (the same
    place Layer 1's check_unresolved_placeholders fires).
    """
    flags: list[dict[str, Any]] = []
    if not documents or not substitution_table:
        return flags

    for token, value in substitution_table.items():
        if not value or value == "—":
            # Skip em-dashes -- they're the "missing value" sentinel
            # and aren't expected to appear identically anywhere.
            continue
        # Find which documents reference this value (the substituted
        # form should appear verbatim in any deliverable that used
        # the token).
        appears_in: list[str] = []
        for doc_type, content in documents.items():
            if isinstance(content, str) and value in content:
                appears_in.append(doc_type)
        # Only check cross-consistency when the value appears in at
        # least one document -- single-document values can't drift.
        # When it appears, also scan EVERY document for variants of
        # the value (rounding-style corruptions). A document that
        # has the variant but not the canonical value is the drift
        # case the check is designed to catch.
        for doc_type, content in documents.items():
            if not isinstance(content, str):
                continue
            variants = _close_numeric_variants(value, content)
            for variant in variants:
                # Only flag when the canonical value is ALSO absent
                # in this document AND a variant is present, OR when
                # the variant differs from the canonical (true drift).
                # We surface variants regardless so a manual edit
                # that changed "1.24 vs 0.73" to "1.24 vs 0.74" gets
                # caught even though the canonical 0.73 is gone.
                flags.append({
                    "type":     "cross_deliverable_drift",
                    "severity": "high",
                    "token":    token,
                    "expected": value,
                    "found":    variant,
                    "document": doc_type,
                    "message": (
                        f"Token {token} expected value '{value}' "
                        f"but document {doc_type!r} contains the "
                        f"variant '{variant}'. A manual edit may "
                        "have changed the figure post-substitution. "
                        "Either restore the canonical value or "
                        "confirm the variant is intentional."),
                })
    return flags


def check_required_citations(
    content_text: str, document_type: str,
) -> list[dict[str, Any]]:
    """PR #336 -- positive-coverage citation check.

    Verifies that the brief body cites all seven required papers from
    VERIFIED_CITATIONS (Hamilton 1989, Ang & Bekaert 2002, Markowitz
    1952, Carhart 1997, Sharpe 1994, Fama & French 1993, Lo 2002).
    The existing check_citation_completeness catches in-text citations
    MISSING from References (orphans); this check catches the inverse:
    required citations missing entirely.

    Also verifies that a References section exists -- a brief with
    seven in-text citations but no References section to point them
    at fails the rubric just as hard as one with no citations.

    Skipped silently for non-brief document types (the deck does not
    require a formal References section, see #335)."""
    if document_type != "executive_brief":
        return []
    if not content_text:
        return []
    flags: list[dict[str, Any]] = []
    text_lower = content_text.lower()
    for key, (author_tokens, year) in _REQUIRED_CITATION_PATTERNS.items():
        authors_present = all(
            a.lower() in text_lower for a in author_tokens)
        year_present = year in content_text
        if authors_present and year_present:
            continue
        canonical = "(" + " and ".join(author_tokens) + f", {year})"
        flags.append({
            "type":             "missing_required_citation",
            "citation_key":     key,
            "expected_pattern": canonical,
            "message": (
                f"Required citation {canonical} not found in brief "
                "body. Add in-text citation in the section it grounds "
                "(Methodology for Hamilton / Markowitz / Sharpe, "
                "limitations or methodology for Lo, factor attribution "
                "for Fama-French / Carhart, dynamic blend for "
                "Ang and Bekaert)."),
        })
    # References-section check. A brief with no References heading
    # fails the rubric regardless of in-text coverage.
    refs_present = bool(
        re.search(r"(?:^|\n)#*\s*References\b", content_text,
                  re.IGNORECASE)
        or re.search(r"\bReferences\b\s*\n", content_text))
    if not refs_present:
        flags.append({
            "type":             "missing_references_section",
            "expected_pattern": "References",
            "message": (
                "No References section found at the end of the brief. "
                "Add a References heading followed by the seven "
                "verified citations in APA 7th hanging-indent format."),
        })
    return flags


# ── CHECK 7 — Per-section word counts (PR #336, brief only) ─────────────


# Target word bands per section (the brief spec from main.py's spec
# list at PR #326 + PR #335 widening of §2 for the rebalancing
# disclosure). Mins are generous to avoid false positives on short
# but rubric-complete sections; maxes pin the upper end so the brief
# does not bloat past the 5-page double-spaced ceiling.
# June 21 2026 -- upper bands tightened to fit a 5-page double-
# spaced page budget. Prior bands summed to 1730-2370 upper; user
# observed 2733 words in production, 37% over the page budget.
# Combined with the References-to-per-section-blocks change in
# agents/academic_writer.py (saves ~80-150 words off the trailing
# block), the new bands sum to 1740-2210 upper. Tightening is
# upper-only -- lower bands stay constant so a tight section
# isn't penalised; required content is preserved.
_BRIEF_SECTION_WORD_TARGETS: dict[str, tuple[int, int]] = {
    "Executive Summary":     (200, 280),  # was 200-300
    "Methodology":           (300, 380),  # was 300-400
    "Key Findings":          (480, 580),  # was 480-620
    "Limitations":           (250, 330),  # was 250-350
    "Final Recommendations": (300, 380),  # was 300-400
    "Visuals":               (210, 260),  # was 200-300
}


def check_section_word_counts(
    content_text: str, document_type: str,
) -> list[dict[str, Any]]:
    """PR #336 -- per-section word band check.

    Splits the brief body by section heading (recognising the six
    rubric section titles from PR #326), counts words in each
    section's prose, and flags any section outside its target word
    band. A section heading that does not match any recognised title
    is skipped silently -- some drafts use slight variants ("Final
    Recommendation" singular vs "Final Recommendations") and an
    over-strict heading match would false-positive on every
    presentation.

    Skipped silently for non-brief document types."""
    if document_type != "executive_brief":
        return []
    if not content_text:
        return []
    sections = _split_brief_by_section(content_text)
    flags: list[dict[str, Any]] = []
    for canonical, target in _BRIEF_SECTION_WORD_TARGETS.items():
        body = sections.get(canonical)
        if body is None:
            # Section heading not found (or doesn't match the
            # canonical variant) -- skip silently.
            continue
        word_count = len(re.findall(r"\b\w+\b", body))
        target_min, target_max = target
        if target_min <= word_count <= target_max:
            continue
        side = ("below" if word_count < target_min else "above")
        flags.append({
            "type":       "section_word_count",
            "section":    canonical,
            "word_count": word_count,
            "target_min": target_min,
            "target_max": target_max,
            "message": (
                f"{canonical} section is {word_count} words -- "
                f"{side} the {target_min}-{target_max} word target. "
                "Expand or trim to meet rubric depth requirement."),
        })
    return flags


# Sentinel chars permitted to follow a sentence-terminating
# punctuation mark at the end of a section. Closing markdown (`*`,
# `_`), closing brackets (`)`, `]`, `}`, `>`), and closing quotes
# all qualify as "trailing punctuation after a complete sentence."
_TRAILING_CLOSERS = set("*_`)]}>'\"’”'")
_SENTENCE_TERMINATORS = set(".?!")
# Tokens that signal a URL was truncated mid-prefix. Matched after
# rstrip + lowercasing so e.g. "https://doi.org/10.1" looks
# unterminated because it ends on a partial DOI fragment.
_URL_TRUNCATION_SUFFIXES = (
    "http", "https", "doi", ".org", ".com", ".io", ".gov", ".edu",
    ".1", ".2", ".3", ".4", ".5", ".6", ".7", ".8", ".9", ".0",
)


def is_content_truncated(content: str) -> bool:
    """Returns True when the content appears to have been cut off
    mid-generation. Used in two places (June 21 2026):

      1. Inside harness_narrative as a self-healing-retry trigger:
         a truncated first attempt fires a re-call with a higher
         max_tokens budget. Two retries max; fail-open thereafter.
      2. Inside the brief audit dispatcher (check_section_truncation
         below) as a safety-net flag for any section that survives
         the in-flight retry and still arrives truncated.

    Truncation signals (any one fires):
      - Open placeholder token: "{{" without a matching "}}"
      - Mid-URL prefix: ends on "http" / "doi" / ".org" / etc.
      - Mid-word: last non-whitespace char is alphanumeric (no
        terminator immediately preceding the trailing word, and
        no whitespace separator)
      - No terminator in the last 200 chars (long trailing run
        of prose with no period / ? / ! at all)

    Conservative on very short content (<50 chars): returns False
    so a deliberate one-liner doesn't false-flag."""
    if not content or len(content) < 50:
        return False
    stripped = content.rstrip()
    if not stripped:
        return False
    # Open placeholder token -- the writer started a {{TOKEN}} and
    # ran out of budget before closing it.
    last_open = stripped.rfind("{{")
    if last_open >= 0 and "}}" not in stripped[last_open:]:
        return True
    # Mid-URL prefix -- the writer started a URL and the trailing
    # characters look like a partial scheme / domain / DOI fragment.
    low = stripped.lower()
    if any(low.endswith(suf) for suf in _URL_TRUNCATION_SUFFIXES):
        # But a real sentence ending in ".1" -- e.g. a section
        # number -- could false-flag. Demand the previous char is
        # NOT whitespace AND not a sentence terminator either, so
        # only true mid-URL fragments fire.
        prev_idx = len(stripped) - 1
        # Walk back to find the start of the trailing token.
        while prev_idx > 0 and not stripped[prev_idx - 1].isspace():
            prev_idx -= 1
        trailing_token = stripped[prev_idx:].lower()
        # If the trailing token contains a "/" or "." it looks like
        # a URL fragment. Plain integers ("Section 5.1.") would have
        # a terminator already and bail via the strip-closers path
        # in _check_terminator below.
        if "/" in trailing_token or trailing_token.count(".") >= 2:
            return True
        if trailing_token in {"http", "https", "doi"}:
            return True
    # Mid-word -- the last non-whitespace char is alphanumeric
    # AND not preceded by a sentence terminator AND not just a
    # numeric trailing the way an embedded number would.
    # Strip trailing markdown closers + brackets first so e.g.
    # "(Hamilton, 1989)." reads as terminator (a closing paren
    # AFTER the period passes through this stripping path).
    s = stripped
    while s and s[-1] in _TRAILING_CLOSERS:
        s = s[:-1].rstrip()
    if not s:
        return False
    if s[-1] in _SENTENCE_TERMINATORS:
        return False
    # Last char is alphanumeric and not a terminator -- this is a
    # mid-word truncation.
    if s[-1].isalnum() or s[-1] == "'":
        # Apostrophes signal a clipped contraction like
        # "Switching's" -- the production symptom from Section 3.
        return True
    # Otherwise: ends with some non-terminator punctuation (a
    # comma, a colon, a hyphen). If the LAST 200 chars contain no
    # terminator at all, treat as truncated; otherwise let it pass
    # (a sentence ended with `--` or similar is unusual but not
    # necessarily truncated).
    last_chunk = s[-200:]
    if not any(c in _SENTENCE_TERMINATORS for c in last_chunk):
        return True
    return False


def _is_completed_sentence(body: str) -> bool:
    """Backward-compatible wrapper -- equivalent to
    `not is_content_truncated(body)` but with the original
    permissive contract: empty/short bodies are treated as
    incomplete. Used by tests that pin the older semantics; new
    callers should use is_content_truncated directly."""
    if not body:
        return False
    s = body.rstrip()
    if not s:
        return False
    while s and s[-1] in _TRAILING_CLOSERS:
        s = s[:-1].rstrip()
        if not s:
            return False
    return s[-1] in _SENTENCE_TERMINATORS


# Brief-only. The deck + appendix paths use their own per-slide /
# per-section dispatchers that don't share this split helper.
def check_section_truncation(
    content_text: str, document_type: str,
) -> list[dict[str, Any]]:
    """June 21 2026 -- a section that ends mid-sentence is a sign
    the Sonnet writer ran out of token budget. Flags any brief
    section whose body's last non-whitespace character is NOT a
    sentence terminator (`.`, `?`, `!`) after stripping trailing
    markdown / brackets / quotes.

    Medium severity: the section content is incomplete and needs
    regeneration or expansion before submission. Pair with the
    section_content_truncated structured log emitted by the
    generator wiring -- the log surfaces the last 50 chars for
    debugging, the audit flag surfaces the same to the editor UI
    so the human reviewer can see the issue without inspecting
    Render logs.

    Skipped silently for non-brief document types (the deck /
    appendix per-section writers don't share the same split-by-
    heading shape; their truncation checks live with their own
    dispatchers when added). Empty output is the green state."""
    if document_type != "executive_brief":
        return []
    if not content_text:
        return []
    sections = _split_brief_by_section(content_text)
    flags: list[dict[str, Any]] = []
    for canonical, body in sections.items():
        if not is_content_truncated(body):
            continue
        last_chars = (body or "").rstrip()[-80:]
        flags.append({
            "type":       "section_truncated",
            "section":    canonical,
            "severity":   "high",
            "last_chars": last_chars,
            "message": (
                f"{canonical} section appears truncated. The Sonnet "
                "writer's in-flight self-healing retry (up to two "
                "attempts at +1000 / +2000 max_tokens) did not "
                "produce a complete section -- regenerate manually "
                "or raise the spec's max_tokens above the second "
                f"retry ceiling. Last 80 chars: \"{last_chars}\""),
        })
    return flags


def _split_brief_by_section(text: str) -> dict[str, str]:
    """Match each canonical section heading case-insensitively and
    extract the body text up to the next recognised heading.
    Tolerates three heading shapes the brief / appendix / midpoint
    paths emit:
      - markdown:        '## Methodology'
      - numbered:        '1. Methodology' / '2. Methodology Overview'
      - plain heading:   'Methodology' on a line of its own
    """
    canonical_names = list(_BRIEF_SECTION_WORD_TARGETS.keys())
    # Heading prefix tolerated: zero-or-more markdown #, zero-or-more
    # whitespace, optional "N." numeric prefix, optional whitespace.
    # The canonical name is captured as group 1. The (?: ...|...)
    # alternation is ordered by length-descending so "Final
    # Recommendations" cannot match as just "Recommendations" if a
    # caller adds the shorter heading later.
    name_alt = "|".join(sorted(
        (re.escape(n) for n in canonical_names),
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
            (n for n in canonical_names
             if n.lower() == matched_heading.lower()),
            None)
        if canonical is None:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out[canonical] = text[start:end]
    return out


def _value_in_cache(
    val: float, cache_norm: dict[str, dict[str, Any]],
) -> bool:
    """True if any strategy row in the cache carries `val` (under
    either the strict fraction tolerance OR the percent-point
    tolerance after normalisation by metric kind). Mirrors the
    numeric check's tolerance shape without requiring tuple
    extraction -- this check is broader by design since story-plan
    violations are about "this number wasn't authorised anywhere",
    not "this number is wrong for this strategy"."""
    if not cache_norm:
        return False
    for row in cache_norm.values():
        if not isinstance(row, dict):
            continue
        for metric, cv in row.items():
            try:
                cv_f = float(cv)
            except (TypeError, ValueError):
                continue
            g_norm, c_norm, scale = _normalise_audit_comparison(
                val, cv_f, metric)
            tol = (_NUMERIC_TOLERANCE_PP if scale == "pp"
                   else _NUMERIC_TOLERANCE)
            if abs(g_norm - c_norm) <= tol:
                return True
    return False


# ── Dispatcher ────────────────────────────────────────────────────────────


def audit_document(
    text: str,
    document_type: str,
    *,
    strategy_cache: dict[str, dict[str, Any]] | None = None,
    slides: list[dict[str, Any]] | None = None,
    story_plan_slides: list[dict[str, Any]] | None = None,
    brief_section_plan: dict[str, Any] | None = None,
    content_json: dict[str, Any] | None = None,
) -> AuditResult:
    """Run the five checks and return a single result object.

    text             — the full plain-text projection of the document
                       (content_text from the editor draft adapter).
    document_type    — "executive_brief" | "presentation_deck".
    strategy_cache   — the strategy_results_cache row (latest), passed
                       in so the caller controls cache freshness and
                       the audit stays pure-Python with no DB reads.
    slides           — PR #333: the parsed AI JSON slide list. Only
                       used by the deck path's CHECK 5.
    story_plan_slides — PR #333: the locked slide plan entries from
                       story_plans. CHECK 5 skips silently when this
                       is None.

    No exception leaves the function — each check is wrapped so a
    failed check is recorded in `skipped` and the rest still run.
    """
    result = AuditResult()
    known_strategies = list((strategy_cache or {}).keys())

    # June 21 2026 -- Checks 1 (numeric cross-reference) and 3
    # (cross-section consistency) are SKIPPED for the three
    # substitution document types (brief / deck / appendix). They
    # were the pre-substitution quality gate that compared every
    # number in the prose against the strategy cache; with the
    # substitution architecture live (PR #347 for brief, #349 for
    # deck + appendix), every numeric in a successfully-substituted
    # document came FROM the cache by construction, so the cross-
    # reference is structurally redundant. Worse, it's actively
    # harmful -- the tuple extractor catches years ("2022"), month
    # counts ("287"), portfolio weights ("60", "40"), basis points
    # ("500"), and recovery months as "Sharpe ratio errors". 46 of
    # 128 flags on the most recent brief generation were false
    # positives from this check.
    #
    # The replacement checks (8 + 9, below) cover the same ground
    # without false positives: check_unresolved_placeholders flags
    # any surviving {{TOKEN}}, and check_numeric_consistency flags
    # raw decimal numbers that look like Sharpe ratios / percentages
    # the substitution should have caught. Together they bound the
    # substitution architecture's invariant: if the table built,
    # the values are correct; if a token leaked OR a raw number
    # bypassed substitution, the audit catches it.
    #
    # Midpoint paper (the only non-substitution document_type in
    # the dispatcher) keeps Checks 1 + 3 because it was authored
    # in May 2026 before the substitution architecture shipped and
    # has no token coverage.
    _SUBSTITUTION_DOC_TYPES = {
        "executive_brief", "presentation_deck", "analytical_appendix",
    }
    is_substitution_doc = document_type in _SUBSTITUTION_DOC_TYPES

    # Tuple extraction feeds Checks 1 and 3 -- skip when not needed.
    if is_substitution_doc:
        tuples: list[dict[str, Any]] = []
    else:
        try:
            tuples = _extract_attributed_numbers(
                text or "", known_strategies)
        except Exception as exc:  # noqa: BLE001
            log.warning("document_audit_extraction_failed", error=str(exc))
            tuples = []

    # Check 1 -- numeric cross-reference. Substitution documents
    # skip; the substitution-architecture checks (8 + 9) cover the
    # same invariant without false positives.
    if is_substitution_doc:
        result.skipped["numeric"] = (
            "substitution_architecture_supersedes_this_check")
    else:
        try:
            result.flags_by_check["numeric"] = (
                check_numeric_cross_reference(tuples, strategy_cache))
        except Exception as exc:  # noqa: BLE001
            log.warning("document_audit_check1_failed", error=str(exc))
            result.skipped["numeric"] = str(exc)

    # Check 2
    try:
        result.flags_by_check["direction"] = check_label_direction(text or "")
    except Exception as exc:  # noqa: BLE001
        log.warning("document_audit_check2_failed", error=str(exc))
        result.skipped["direction"] = str(exc)

    # Check 3 -- cross-section consistency. Same rationale as
    # Check 1: derives from the same tuple extractor that produces
    # the false positives, and the substitution architecture
    # guarantees same-named tokens carry the same value across
    # sections by construction (the table is built once per
    # data_hash).
    if is_substitution_doc:
        result.skipped["consistency"] = (
            "substitution_architecture_supersedes_this_check")
    else:
        try:
            result.flags_by_check["consistency"] = (
                check_cross_section_consistency(tuples))
        except Exception as exc:  # noqa: BLE001
            log.warning("document_audit_check3_failed", error=str(exc))
            result.skipped["consistency"] = str(exc)

    # Check 4
    try:
        citations, skip = check_citation_completeness(
            text or "", document_type)
        result.flags_by_check["citation"] = citations
        if skip is not None:
            result.skipped["citation"] = skip
    except Exception as exc:  # noqa: BLE001
        log.warning("document_audit_check4_failed", error=str(exc))
        result.skipped["citation"] = str(exc)

    # Check 5 -- story plan violations. Deck variant uses slides +
    # story_plan_slides; brief variant uses content_text +
    # brief_section_plan. Skips silently when no plan was supplied.
    if slides and story_plan_slides:
        try:
            result.flags_by_check["story_plan"] = (
                check_story_plan_violations(
                    slides, story_plan_slides,
                    strategy_cache=strategy_cache))
        except Exception as exc:  # noqa: BLE001
            log.warning("document_audit_check5_failed", error=str(exc))
            result.skipped["story_plan"] = str(exc)
    elif brief_section_plan and document_type == "executive_brief":
        try:
            result.flags_by_check["story_plan"] = (
                check_brief_story_plan_violations(
                    text or "", brief_section_plan,
                    strategy_cache=strategy_cache))
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "document_audit_check5_brief_failed", error=str(exc))
            result.skipped["story_plan"] = str(exc)
    else:
        # Surface the skip reason so the dispatcher's structured log
        # accurately reports why the check did not fire -- avoids the
        # frontend interpreting an empty list as "no violations" when
        # it actually means "the check did not run".
        result.skipped["story_plan"] = "no_plan_or_no_slides"

    # Check 6 -- required citations (brief only, PR #336).
    if document_type == "executive_brief":
        try:
            result.flags_by_check["required_citations"] = (
                check_required_citations(text or "", document_type))
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "document_audit_check6_failed", error=str(exc))
            result.skipped["required_citations"] = str(exc)
    else:
        result.skipped["required_citations"] = "not_a_brief"

    # Check 7 -- per-section word counts (brief only, PR #336).
    if document_type == "executive_brief":
        try:
            result.flags_by_check["section_word_count"] = (
                check_section_word_counts(text or "", document_type))
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "document_audit_check7_failed", error=str(exc))
            result.skipped["section_word_count"] = str(exc)
    else:
        result.skipped["section_word_count"] = "not_a_brief"

    # Check 8 -- unresolved {{TOKEN}} placeholders (substitution
    # architecture). Layer 2 (June 21 2026) extends the route to the
    # deck + appendix surfaces alongside the original brief surface.
    # The check itself is document-type-agnostic; the routing gate
    # below is the only difference per surface. Reuses the
    # is_substitution_doc flag set above for Checks 1 + 3.
    if is_substitution_doc:
        try:
            result.flags_by_check["unresolved_placeholders"] = (
                check_unresolved_placeholders(text or ""))
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "document_audit_check8_failed", error=str(exc))
            result.skipped["unresolved_placeholders"] = str(exc)
    else:
        result.skipped["unresolved_placeholders"] = (
            "not_a_substitution_document")

    # Check 8b -- <unverified>...</unverified> tags
    # (June 28 2026). Document-type AGNOSTIC: brief, appendix,
    # script, AND deck can all carry <unverified> tags from
    # their respective soft-fail paths (harness_narrative for
    # brief + appendix; script_generation for script;
    # _substitute_slide_content for deck). Fires for every
    # doc type so the audit banner surfaces the flags
    # regardless of which surface produced the document.
    try:
        result.flags_by_check["unverified_tags"] = (
            check_unverified_tags(text or ""))
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "document_audit_check8b_failed",
            error=str(exc))
        result.skipped["unverified_tags"] = str(exc)

    # Check 9 -- raw numeric in body (substitution bypass signal).
    # Same routing gate as Check 8.
    if is_substitution_doc:
        try:
            result.flags_by_check["raw_numeric"] = (
                check_numeric_consistency(text or ""))
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "document_audit_check9_failed", error=str(exc))
            result.skipped["raw_numeric"] = str(exc)
    else:
        result.skipped["raw_numeric"] = "not_a_substitution_document"

    # Check 10 -- section truncation (brief only, June 21 2026).
    # Detects when a section's body ends mid-sentence -- the symptom
    # of the Sonnet writer hitting max_tokens. The companion fix is
    # the per-spec max_tokens override in main.py for the two
    # longest brief sections; this audit check is the safety net
    # for future sections that hit the same ceiling.
    if document_type == "executive_brief":
        try:
            section_trunc_flags = check_section_truncation(
                text or "", document_type)
            result.flags_by_check["section_truncated"] = section_trunc_flags
            for f in section_trunc_flags:
                log.warning(
                    "section_content_truncated",
                    section=f.get("section"),
                    last_chars=f.get("last_chars"))
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "document_audit_check10_failed", error=str(exc))
            result.skipped["section_truncated"] = str(exc)
    else:
        result.skipped["section_truncated"] = "not_a_brief"

    # Check 11 (PR β, June 28 2026) -- table structure validation.
    # Walks content_json for every TipTap table node + verifies
    # every required column from REQUIRED_TABLE_COLUMNS is
    # present. Caption-prefix match locates each registered
    # table in the document (e.g. "Table B1" matches "Table B1.
    # Full-Period Performance..."). Missing columns + entirely-
    # missing required tables both fire a 'major' severity flag
    # that surfaces in the editor's audit banner before manual
    # inspection of the exported DOCX would have caught it.
    # Skipped when content_json is not passed in (text-only
    # callers); skipped for document types with no registry
    # entries (executive_brief / presentation_deck /
    # presentation_script in the initial registry).
    if content_json is None:
        result.skipped["table_structure"] = "no_content_json_passed"
    else:
        try:
            from tools.table_structure_validator import (
                REQUIRED_TABLE_COLUMNS,
                validate_table_structure,
            )
            if not REQUIRED_TABLE_COLUMNS.get(document_type):
                result.skipped["table_structure"] = (
                    "no_registered_tables_for_document_type")
            else:
                result.flags_by_check["table_structure"] = (
                    validate_table_structure(
                        content_json, document_type))
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "document_audit_check11_failed", error=str(exc))
            result.skipped["table_structure"] = str(exc)

    log.info(
        "document_audit_complete",
        document_type=document_type,
        flag_counts=result.flag_counts,
        skipped=list(result.skipped.keys()))
    return result
