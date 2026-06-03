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
_NUMERIC_TOLERANCE = 0.005       # Check 1
_CONSISTENCY_TOLERANCE = 0.05    # Check 3


# ── Result shape ──────────────────────────────────────────────────────────


@dataclass
class AuditResult:
    """Output of audit_document(). Shape consumed by the generator
    wiring + the document_audit_metrics writer + the frontend banner."""
    flags_by_check: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {
            "numeric": [], "direction": [],
            "consistency": [], "citation": [],
        })
    skipped: dict[str, str] = field(default_factory=dict)  # check_name → reason

    @property
    def flag_counts(self) -> dict[str, int]:
        return {
            "numeric":     len(self.flags_by_check["numeric"]),
            "direction":   len(self.flags_by_check["direction"]),
            "consistency": len(self.flags_by_check["consistency"]),
            "citation":    len(self.flags_by_check["citation"]),
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
    Flag deviations > _NUMERIC_TOLERANCE absolute."""
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
        diff = abs(float(t["value"]) - cache_value)
        if diff > _NUMERIC_TOLERANCE:
            flags.append({
                "strategy":  t["strategy"],
                "metric":    t["metric"],
                "generated": t["value"],
                "cache":     cache_value,
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


_CITATION_RE = re.compile(
    r"\b([A-Z][A-Za-z\-]+(?:\s+et\s+al\.?)?)\s*\((\d{4})\)"
)


def _extract_references_section(
    text: str, document_type: str,
) -> str | None:
    """Return the body text of the References section. For the brief
    that's "## References" through next "## " heading or end-of-text.
    For the deck the references slide carries the bibliography in
    its body / bullets; deck_slides_to_editor concatenates them so
    the same string scan works."""
    if not text:
        return None
    # Find the first occurrence of "References" as a section heading.
    m = re.search(
        r"(?:^|\n)#+\s*References\b[^\n]*\n([\s\S]*?)"
        r"(?:\n#+\s|\Z)",
        text, re.IGNORECASE)
    if m:
        return m.group(1)
    # Deck fallback — a slide may surface "References" without
    # markdown headings. Grab everything from "References" to end.
    m = re.search(
        r"\bReferences\b\s*\n([\s\S]+)\Z", text)
    if m:
        return m.group(1)
    return None


def check_citation_completeness(
    text: str, document_type: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """Returns (flags, skip_reason). Skip reason set when the
    References section couldn't be located (deck without a refs
    slide, etc.) — the caller records it as "skipped" rather than
    silently dropping the check."""
    if not text:
        return [], None
    references_body = _extract_references_section(text, document_type)
    if references_body is None:
        return [], "no References section found"
    refs_lc = references_body.lower()
    cited: set[tuple[str, str]] = set()
    for m in _CITATION_RE.finditer(text):
        author = m.group(1).strip()
        # Stripped of "et al." for the lookup so "Lopez et al. (2018)"
        # in-text matches "Lopez, M. ... (2018)" in refs.
        author_root = re.sub(
            r"\s+et\s+al\.?$", "", author, flags=re.IGNORECASE).strip()
        if author_root:
            cited.add((author_root, m.group(2)))
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


# ── Dispatcher ────────────────────────────────────────────────────────────


def audit_document(
    text: str,
    document_type: str,
    *,
    strategy_cache: dict[str, dict[str, Any]] | None = None,
) -> AuditResult:
    """Run the four checks and return a single result object.

    text             — the full plain-text projection of the document
                       (content_text from the editor draft adapter).
    document_type    — "executive_brief" | "presentation_deck".
    strategy_cache   — the strategy_results_cache row (latest), passed
                       in so the caller controls cache freshness and
                       the audit stays pure-Python with no DB reads.

    No exception leaves the function — each check is wrapped so a
    failed check is recorded in `skipped` and the rest still run.
    """
    result = AuditResult()
    known_strategies = list((strategy_cache or {}).keys())

    # Tuple extraction feeds Checks 1 and 3 — do it once.
    try:
        tuples = _extract_attributed_numbers(text or "", known_strategies)
    except Exception as exc:  # noqa: BLE001
        log.warning("document_audit_extraction_failed", error=str(exc))
        tuples = []

    # Check 1
    try:
        result.flags_by_check["numeric"] = check_numeric_cross_reference(
            tuples, strategy_cache)
    except Exception as exc:  # noqa: BLE001
        log.warning("document_audit_check1_failed", error=str(exc))
        result.skipped["numeric"] = str(exc)

    # Check 2
    try:
        result.flags_by_check["direction"] = check_label_direction(text or "")
    except Exception as exc:  # noqa: BLE001
        log.warning("document_audit_check2_failed", error=str(exc))
        result.skipped["direction"] = str(exc)

    # Check 3
    try:
        result.flags_by_check["consistency"] = check_cross_section_consistency(
            tuples)
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

    log.info(
        "document_audit_complete",
        document_type=document_type,
        flag_counts=result.flag_counts,
        skipped=list(result.skipped.keys()))
    return result
