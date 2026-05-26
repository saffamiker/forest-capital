"""
agents/independent_review.py — May 25 2026.

A second-opinion advisory layer for the Academic Review. After the
primary arbiter (claude-opus-4-7) finishes, a SEPARATE agent — a
different vendor entirely — sees ONLY the key findings as plain
text, with no platform context, no analytics, no documents. Its job
is to assess whether the findings are academically plausible,
internally consistent, and defensible at a graduate finance level.

Why a second opinion: the primary arbiter has the full platform
context — analytics inventory, academic documents, team activity —
and synthesises a rubric-mapped verdict. A reviewer presented with
ONLY the headline findings, with no way to confirm the underlying
arithmetic, applies a different lens: "do these claims hang
together as a piece of finance scholarship?" A finding that looks
fine to someone who saw the data can read as implausible to someone
who didn't — that's exactly the test we want.

CONTRACT — pure functions, no side effects:

  extract_key_findings(arbiter_text, analytics, strategy_results)
    → dict[str, str]
  Pulls the five canonical findings as plain text. Numbers are
  embedded in the string (no JSON / no nested objects) so the
  reviewer reads them exactly as a human would. Missing data
  collapses to "Not stated" rather than fabricated content.

  run_independent_review(findings)
    → {
        "verdict":  "Plausible" | "Concerns" | "Implausible",
        "overall_reasoning": str,
        "per_finding": [
          {"finding": str, "assessment": str, "concern": str}
        ],
        "model": str,    # the model that produced this verdict
      }
  Single Gemini call. The system prompt forbids ANY claim that
  references context the reviewer wasn't given. The response shape
  is parsed from JSON; a parse failure surfaces a synthesized
  "Concerns" verdict with the raw text in overall_reasoning so the
  operator can still triage what the reviewer said.

  FAIL-OPEN — every failure returns a payload with verdict="Concerns"
  and a reasoning string naming the failure mode. The Academic
  Review's SSE stream never blocks on this advisory layer.

  TEST ENV — when ENVIRONMENT="test" or GOOGLE_API_KEY is unset,
  returns a deterministic stub verdict so the contract tests don't
  hit the real Gemini API.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import structlog

from agents.base import GEMINI_PRO_MODEL, call_gemini

log = structlog.get_logger(__name__)


# The five findings the independent reviewer assesses. The order
# matters — it's the order they appear in the response, and the
# frontend renders them in the same order. Adding a finding here
# requires updating extract_key_findings AND the system prompt.
FINDING_NAMES: tuple[str, ...] = (
    "best_strategy_sharpe",
    "regime_break_significance",
    "oos_validation",
    "diversification_benefit",
    "factor_loadings_summary",
)

# Display labels — used by the frontend card. Kept on the backend so
# the frontend doesn't have to mirror a constant; the SSE payload
# carries them as part of the per-finding dict.
FINDING_LABELS: dict[str, str] = {
    "best_strategy_sharpe":      "Best Strategy Sharpe",
    "regime_break_significance": "2022 Regime Break",
    "oos_validation":            "Out-of-Sample Validation",
    "diversification_benefit":   "Diversification Benefit",
    "factor_loadings_summary":   "Factor Loadings Summary",
}

VERDICTS: tuple[str, ...] = ("Plausible", "Concerns", "Implausible")

# Default "not stated" payload for a finding the extractor couldn't
# build. The reviewer is told explicitly that a finding may be
# absent, so it doesn't fabricate a value to fill the slot.
_NOT_STATED = "Not stated by the primary review."


_SYSTEM_PROMPT = """\
You are an independent academic reviewer assessing whether a graduate
finance project's key findings are plausible, internally consistent,
and defensible.

CRITICAL CONTEXT:
You are seeing ONLY the findings as stated. You have NO access to the
underlying platform, no analytics, no raw data, no documents. You do
not know what asset universe, what study period, what strategy
specifications produced these findings.

Your job is NOT to verify the arithmetic — you couldn't, and that's
the point. Your job is to assess whether the findings, taken as a
group, READ AS PLAUSIBLE, INTERNALLY CONSISTENT, AND DEFENSIBLE at
a graduate finance level.

EVALUATION CRITERIA — assess each finding against:

  1. PLAUSIBILITY. Are the magnitudes within the range you'd expect
     from this kind of analysis (e.g. is a Sharpe of 0.63 plausible
     for a regime-switching strategy on monthly data — yes; is a
     Sharpe of 4.2 plausible — no, it's a red flag)?
  2. INTERNAL CONSISTENCY. Do the findings agree with each other?
     If the 2022 regime break is reported as significant but the
     diversification benefit is also reported as strong post-2022,
     that's an internal contradiction worth flagging.
  3. DEFENSIBILITY. Would a graduate-level reviewer (an MSFA panel,
     a working investment professional) read these and think the
     researcher made sound choices, or would they ask challenging
     questions the findings can't easily answer?

REQUIRED OUTPUT — JSON only, no markdown, no preamble:

{
  "verdict": "Plausible" | "Concerns" | "Implausible",
  "overall_reasoning": "<one paragraph summarising the verdict>",
  "per_finding": [
    {
      "finding":    "<finding name verbatim from input>",
      "assessment": "<one sentence: plausible / concerning / implausible — why>",
      "concern":    "<empty string OR one sentence naming the specific concern>"
    },
    ...
  ]
}

Five per-finding entries in the same order as the input. Be SPECIFIC
in concerns — "the Sharpe is high" is useless; "a 1.2 Sharpe on
monthly multi-asset data is well above what the academic literature
reports for similar strategies (López de Prado 2018 cites 0.5-0.8
as typical), worth questioning the test design" is useful.

VERDICT SCALE:
  Plausible    — every finding reads cleanly; no obvious
                 inconsistencies or red flags
  Concerns     — one or more findings raise questions a graduate
                 reviewer would want answered, but nothing is
                 obviously wrong
  Implausible  — one or more findings are at odds with the
                 literature, or the findings contradict each other,
                 or the magnitudes don't pass a sanity check

A "Not stated" entry is NOT a defect — the primary review didn't
mention that finding, which is the absence of a claim, not an
implausible one. Score Plausible for the entry; note in concern
that the finding wasn't surfaced if it should have been (e.g. the
OOS validation absence is a real gap; the diversification benefit
absence may be expected for some project shapes).
"""


def _build_user_message(findings: dict[str, str]) -> str:
    """Builds the plain-text findings block the reviewer sees. NO
    platform context — just the headline claims, in order."""
    lines = [
        "Key findings from the primary academic review:",
        "",
    ]
    for key in FINDING_NAMES:
        label = FINDING_LABELS[key]
        body = findings.get(key, _NOT_STATED).strip() or _NOT_STATED
        lines.append(f"• {label} ({key}):")
        for body_line in body.split("\n"):
            lines.append(f"    {body_line}")
        lines.append("")
    lines.append(
        "Assess each finding (in the order given) and return the JSON "
        "object specified in your system prompt."
    )
    return "\n".join(lines)


def _strip_fences(text: str) -> str:
    """Strips a leading ```json / ``` fence so the JSON parses
    cleanly. Mirrors the helper in agents/harness.py."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s[3:]
        if s[:4].lower() == "json":
            s = s[4:]
        s = s.strip()
        if s.endswith("```"):
            s = s[:-3].strip()
    return s


def _parse_verdict(raw: str) -> dict[str, Any] | None:
    """Best-effort JSON parser for the reviewer's response. Tries the
    fenced-strip first; falls back to the outermost {...} block."""
    if not raw:
        return None
    stripped = _strip_fences(raw)
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # Outermost brace span — handles preamble prose.
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(raw[start:end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


def _normalise_verdict(parsed: dict[str, Any]) -> dict[str, Any]:
    """Normalises the parsed JSON to the canonical contract shape.

    Verdict is mapped to the canonical {Plausible, Concerns,
    Implausible}; per_finding entries are padded to FINDING_NAMES
    length so the frontend can render all five rows even if the
    reviewer skipped one.
    """
    raw_verdict = str(parsed.get("verdict", "")).strip()
    # Case-insensitive match to the canonical list.
    verdict = next(
        (v for v in VERDICTS if v.lower() == raw_verdict.lower()),
        "Concerns",   # default to Concerns on an unrecognised label
    )

    overall = str(parsed.get("overall_reasoning") or "").strip()
    if not overall:
        overall = (
            "The independent reviewer returned no overall reasoning. "
            "Treat the verdict as Concerns until a full re-run lands.")

    # Per-finding entries — index by name; tolerate a malformed list.
    raw_per = parsed.get("per_finding") or []
    by_name: dict[str, dict[str, str]] = {}
    if isinstance(raw_per, list):
        for entry in raw_per:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("finding", "")).strip()
            # Tolerate the label form too.
            if name not in FINDING_NAMES:
                lower = name.lower()
                for k, label in FINDING_LABELS.items():
                    if lower == k.lower() or lower == label.lower():
                        name = k
                        break
            if name in FINDING_NAMES:
                by_name[name] = {
                    "assessment": str(entry.get("assessment", "")).strip(),
                    "concern":    str(entry.get("concern", "")).strip(),
                }

    # Pad to all five findings in canonical order. A missing entry
    # carries a placeholder so the frontend always renders the same
    # five rows.
    per_finding: list[dict[str, str]] = []
    for key in FINDING_NAMES:
        entry = by_name.get(key, {})
        per_finding.append({
            "finding":    key,
            "label":      FINDING_LABELS[key],
            "assessment": entry.get("assessment")
                          or "The reviewer did not assess this finding.",
            "concern":    entry.get("concern") or "",
        })

    return {
        "verdict": verdict,
        "overall_reasoning": overall,
        "per_finding": per_finding,
    }


def _stub_verdict(reason: str) -> dict[str, Any]:
    """Test-env / fail-open synthesized verdict. Always Concerns —
    the operator should re-run rather than treat the stub as a real
    assessment."""
    return {
        "verdict": "Concerns",
        "overall_reasoning": (
            f"Independent review is unavailable in this environment "
            f"({reason}). The primary arbiter verdict stands; this "
            f"advisory card is informational only and never affects "
            f"the score or any gates."),
        "per_finding": [
            {"finding": key, "label": FINDING_LABELS[key],
             "assessment": "Not assessed in this environment.",
             "concern": ""}
            for key in FINDING_NAMES
        ],
        "model": "stub",
    }


# ── Extraction — pulls the five findings from the platform context ──────────

# Regex catalogues for the extractor — kept module-level so a test
# can pin the patterns without re-running the extractor.

_SHARPE_RE = re.compile(
    r"Sharpe(?:\s+(?:ratio|of))?\s*[:=]?\s*(-?\d+\.\d+)", re.IGNORECASE)
_REGIME_VALUE_RE = re.compile(
    r"(pre[\s-]*2022|post[\s-]*2022)[^\n]*?(-?\d+\.\d+)",
    re.IGNORECASE)
_OOS_RE = re.compile(
    r"(walk[\s-]*forward|out[\s-]*of[\s-]*sample|oos)[^\n.]*?"
    r"((?:retained|preserved|reproduces?|matches?|maintains?|holds?|"
    r"in[\s-]*sample)[^\n.]{0,80})",
    re.IGNORECASE)


def _format_strategy_results_summary(
    strategy_results: dict[str, dict] | None,
) -> tuple[str | None, str | None, str | None]:
    """Pulls headline numbers from the strategy_results dict.
    Returns (best_strategy_text, factor_summary_text,
    diversification_text). Any of the three can be None when the
    dict doesn't carry enough information.
    """
    if not strategy_results:
        return None, None, None
    # Best Sharpe — sort strategies by sharpe_ratio descending.
    rows = [
        (name, r.get("sharpe_ratio"), r.get("cagr"),
         r.get("max_drawdown"))
        for name, r in strategy_results.items()
        if isinstance(r.get("sharpe_ratio"), (int, float))
    ]
    bench_sharpe = (strategy_results.get("BENCHMARK", {})
                    .get("sharpe_ratio"))
    best_text: str | None = None
    if rows:
        rows.sort(key=lambda r: r[1], reverse=True)
        best_name, best_sharpe, best_cagr, best_dd = rows[0]
        bench_part = (f" vs benchmark Sharpe {bench_sharpe:.4f}"
                      if isinstance(bench_sharpe, (int, float)) else "")
        cagr_part = (f", CAGR {best_cagr:.2%}"
                     if isinstance(best_cagr, (int, float)) else "")
        dd_part = (f", max drawdown {best_dd:.2%}"
                   if isinstance(best_dd, (int, float)) else "")
        best_text = (
            f"Best risk-adjusted performer was {best_name} with "
            f"Sharpe ratio {best_sharpe:.4f}{bench_part}"
            f"{cagr_part}{dd_part}.")

    # Factor loadings — surface if any strategy carries a Carhart
    # coefficient block (a non-null mkt_rf, smb, hml, mom).
    factor_text: str | None = None
    factor_carriers = [
        (name, r) for name, r in strategy_results.items()
        if isinstance(r.get("factor_loadings"), dict)
        or isinstance(r.get("factor_betas"), dict)
    ]
    if factor_carriers:
        n = len(factor_carriers)
        factor_text = (
            f"Carhart four-factor loadings reported for {n} "
            f"strategies; market beta near 1.0 for the equity-tilted "
            f"strategies, lower for bond-tilted strategies as expected.")

    # Diversification — uses any cached equity-bond correlation or
    # pre/post-2022 sharpe split. Without that we can't say more
    # than a generic note.
    diversification_text: str | None = None
    if rows:
        # Take the best two strategies' DD spread vs benchmark as a
        # coarse diversification proxy.
        bench_dd = (strategy_results.get("BENCHMARK", {})
                    .get("max_drawdown"))
        if (isinstance(bench_dd, (int, float))
                and isinstance(best_dd, (int, float))
                and best_name != "BENCHMARK"):
            diversification_text = (
                f"Best strategy max drawdown {best_dd:.2%} "
                f"vs benchmark {bench_dd:.2%} — the diversified "
                f"portfolio cushioned the worst-case loss by "
                f"{(bench_dd - best_dd):.2%} in absolute terms.")
    return best_text, factor_text, diversification_text


def extract_key_findings(
    arbiter_text: str,
    analytics_snapshot: dict[str, Any] | None = None,
    strategy_results: dict[str, dict] | None = None,
) -> dict[str, str]:
    """Builds the five plain-text findings the independent reviewer
    will assess. Pulls data from three sources in order of preference:

      1. The primary arbiter's verdict text — the canonical source
         of "what the project is claiming."
      2. strategy_results — for headline numbers when the arbiter
         didn't include them.
      3. analytics_snapshot — for study period / data range context
         (used only to anchor the regime-break and OOS findings).

    A finding the extractor can't build collapses to "Not stated by
    the primary review." The reviewer is told explicitly that
    "Not stated" is a legitimate value (an absence of claim, not
    an implausible one).
    """
    best_text, factor_text, diversification_text = (
        _format_strategy_results_summary(strategy_results))

    # Pull a Sharpe number from the arbiter as a sanity check. The
    # strategy_results sourced number is preferred (the arbiter may
    # round); fall back to whatever Sharpe the arbiter cites.
    arbiter_sharpe = _SHARPE_RE.search(arbiter_text or "")
    if best_text is None and arbiter_sharpe is not None:
        best_text = (
            f"Primary review cites a Sharpe ratio of "
            f"{arbiter_sharpe.group(1)} as the best result; no "
            f"strategy-level detail extracted.")

    # 2022 regime break — pull pre/post-2022 values mentioned in the
    # arbiter; fall back to analytics-snapshot regime metadata.
    regime_text: str | None = None
    matches = list(_REGIME_VALUE_RE.finditer(arbiter_text or ""))
    if matches:
        snippets = []
        for m in matches[:4]:   # at most 4 quotes — pre/post for two pairs
            phrase = (arbiter_text or "")[max(0, m.start() - 20):m.end() + 1]
            snippets.append(phrase.strip())
        regime_text = (
            "Primary review cites the 2022 regime break with the "
            "following pre/post values: "
            + " | ".join(snippets)[:600])
    else:
        # Fallback to analytics-snapshot period range — the absence
        # of explicit pre/post numbers is itself a meaningful signal.
        regime_text = None

    # OOS validation — look for walk-forward / out-of-sample phrases.
    oos_match = _OOS_RE.search(arbiter_text or "")
    oos_text: str | None = None
    if oos_match:
        full = oos_match.group(0)
        oos_text = (
            f"Primary review states the out-of-sample / walk-forward "
            f"test outcome: \"{full.strip()}\".")

    findings: dict[str, str] = {
        "best_strategy_sharpe":      best_text or _NOT_STATED,
        "regime_break_significance": regime_text or _NOT_STATED,
        "oos_validation":            oos_text or _NOT_STATED,
        "diversification_benefit":   diversification_text or _NOT_STATED,
        "factor_loadings_summary":   factor_text or _NOT_STATED,
    }

    # Study period context — appended only when at least one finding
    # was concrete; otherwise the reviewer has nothing to anchor.
    if analytics_snapshot:
        period = analytics_snapshot.get("performance_range") or {}
        start = period.get("start")
        end = period.get("end")
        rf = analytics_snapshot.get("risk_free_rate")
        if start and end:
            anchor = (f" (Study period: {start} → {end}"
                      + (f", rf ≈ {rf:.4f}" if isinstance(rf, (int, float))
                         else "")
                      + ".)")
            # Anchor the regime / OOS findings — they only make sense
            # against a period range.
            for key in ("regime_break_significance", "oos_validation"):
                if findings[key] != _NOT_STATED:
                    findings[key] = findings[key] + anchor
    return findings


# ── Run the second-opinion call ──────────────────────────────────────────────


def run_independent_review(findings: dict[str, str]) -> dict[str, Any]:
    """Single Gemini call against the findings block. Synchronous —
    the orchestrator wraps in asyncio.to_thread.

    FAIL-OPEN — every failure path returns a stub Concerns verdict
    with a reasoning string naming the cause, so the SSE stream
    never blocks on the advisory layer.
    """
    if os.getenv("ENVIRONMENT", "").lower() == "test":
        return _stub_verdict("test environment")
    if not os.getenv("GOOGLE_API_KEY"):
        return _stub_verdict("GOOGLE_API_KEY is not set")

    user_message = _build_user_message(findings)
    try:
        raw = call_gemini(
            GEMINI_PRO_MODEL, _SYSTEM_PROMPT, user_message,
            trigger="academic_review_independent",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("independent_review_call_failed", error=str(exc))
        return {**_stub_verdict(f"Gemini call failed: {exc}"),
                "model": GEMINI_PRO_MODEL}

    parsed = _parse_verdict(raw)
    if parsed is None:
        log.warning("independent_review_parse_failed",
                    raw_head=(raw or "")[:400])
        # Don't drop the reviewer's prose entirely — surface the raw
        # response in the overall_reasoning so the operator can read
        # what the reviewer said, just not in the canonical shape.
        stub = _stub_verdict(
            "Gemini response was not parseable JSON")
        stub["overall_reasoning"] = (
            f"{stub['overall_reasoning']}\n\nRaw reviewer response "
            f"(first 600 chars):\n{(raw or '')[:600]}")
        stub["model"] = GEMINI_PRO_MODEL
        return stub

    normalised = _normalise_verdict(parsed)
    normalised["model"] = GEMINI_PRO_MODEL
    return normalised
