"""
critic_review -- adversarial critic review using Gemini + Grok.

June 23 2026. The existing academic-review council (peers + arbiter) is
too collegial -- each agent reviews through its lens but is not
explicitly adversarial. For the final June 30 submission, the team
needs an actively-hostile critic that hunts for methodological,
factual, logical, citation, and presentational errors. Gemini already
serves as the independent dissenter in academic_review; Grok is the
contrarian peer. Both are natural fits for an explicitly adversarial
role.

Two models run IN PARALLEL via asyncio (one model's latency, not two).
Each is told to return a JSON array of findings + a PROSE_SUMMARY
line. The merge layer dedupes overlapping findings (same category +
location + similar description), marks them `agreed: True`, and sorts
the merged list by severity then agreement.

Fatal findings are flagged prominently but are NEVER blocking -- the
spec is "team makes the final call." The endpoint surfaces severity
counts so the UI can render a non-blocking advisory banner.

Output shape (see /api/council/critic-review):

    {
      "document_scope": "executive_brief" | ... | "full_package",
      "gemini_findings":   [<finding>, ...],
      "grok_findings":     [<finding>, ...],
      "merged_findings":   [<finding>, ...],
      "prose_summary":     "<string>",
      "fatal_count":       <int>,
      "major_count":       <int>,
      "minor_count":       <int>,
      "model_agreement":   "<string>",
      "partial_failure":   <bool>,
    }

Finding shape:

    {
      "severity":       "Fatal" | "Major" | "Minor",
      "category":       "methodological" | "factual" | "logical" |
                        "presentational" | "citation" | "consistency",
      "document":       "<document_type or 'cross-document'>",
      "location":       "<section / slide / paragraph>",
      "description":    "<what the error is>",
      "evidence":       "<quote or paraphrase from the document>",
      "recommendation": "<what should be changed>",
      "agreed":         <bool>            (only on merged_findings)
      "raised_by":      "gemini" | "grok" (only on merged_findings)
    }
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

import structlog

from agents.base import GEMINI_MODEL, call_gemini


log = structlog.get_logger()


# ── Critic prompts ─────────────────────────────────────────────────────

_CRITIC_SEVERITY_DEFINITIONS = """SEVERITY DEFINITIONS:

Fatal -- an error that would cause an academic panel to reject the
submission or an investment committee to dismiss the analysis.
Examples: look-ahead bias in backtest, Sharpe ratio contradicting the
NUMERIC REFERENCE, missing core methodology disclosure.

Major -- a significant weakness that would lower the grade or raise
serious questions. Examples: unsupported claim, missing limitation,
internal contradiction.

Minor -- a presentational or minor factual issue that should be
corrected but would not sink the submission.

OUTPUT FORMAT:

Return a JSON array of findings only. No preamble, no conclusion prose.
Each finding must have: severity, category, document, location,
description, evidence, recommendation.

Then on a new line after the JSON array, write a prose summary
starting with PROSE_SUMMARY: that gives a 3-5 sentence overall
assessment of the package's readiness."""


_GEMINI_CRITIC_SYSTEM = (
    "You are a harsh but fair academic critic reviewing a graduate "
    "finance practicum submission for FNA 670 at the McColl School of "
    "Business. Your job is to find every significant error, weakness, "
    "or unsupported claim in the document(s) provided. You are NOT here "
    "to encourage -- you are here to find what would cause an "
    "experienced finance academic or investment professional to reject "
    "or downgrade this work.\n\n"
    "WHAT TO LOOK FOR:\n"
    "Methodological errors: flawed backtesting logic, look-ahead bias, "
    "survivorship bias, inappropriate benchmarks, regime detection "
    "errors, factor model misapplication, invalid statistical "
    "inference.\n"
    "Factual errors: any numeric claim that contradicts the NUMERIC "
    "REFERENCE values provided. Any date, period, or citation year that "
    "appears inconsistent across documents.\n"
    "Logical errors: conclusions not supported by evidence, internal "
    "contradictions, circular reasoning, overstated certainty.\n"
    "Presentational errors: claims made without evidence, undefined "
    "jargon, missing limitations disclosures, audience mismatch.\n"
    "Citation errors: missing required citations, wrong years, claims "
    "attributed to wrong authors.\n"
    "Consistency errors (full-package only): figures, regime labels, or "
    "conclusions that differ across documents.\n\n"
    + _CRITIC_SEVERITY_DEFINITIONS
)


_GROK_CRITIC_SYSTEM = (
    "You are a contrarian finance professional and academic critic. You "
    "have seen many graduate practicum submissions that oversell their "
    "results, hide their assumptions, and confuse backtested "
    "performance with predictive validity. Your job is to be the "
    "skeptic in the room -- find the claims that won't survive scrutiny "
    "from an experienced allocator or a rigorous academic reviewer.\n\n"
    "WHAT TO LOOK FOR:\n"
    "Methodological errors: flawed backtesting logic, look-ahead bias, "
    "survivorship bias, inappropriate benchmarks, regime detection "
    "errors, factor model misapplication, invalid statistical "
    "inference.\n"
    "Factual errors: any numeric claim that contradicts the NUMERIC "
    "REFERENCE values provided. Any date, period, or citation year that "
    "appears inconsistent across documents.\n"
    "Logical errors: conclusions not supported by evidence, internal "
    "contradictions, circular reasoning, overstated certainty.\n"
    "Presentational errors: claims made without evidence, undefined "
    "jargon, missing limitations disclosures, audience mismatch.\n"
    "Citation errors: missing required citations, wrong years, claims "
    "attributed to wrong authors.\n"
    "Consistency errors (full-package only): figures, regime labels, or "
    "conclusions that differ across documents.\n\n"
    "Pay particular attention to:\n"
    "- Regime detection claims that may be post-hoc rationalized\n"
    "- Sharpe ratios and drawdown figures that seem too clean\n"
    "- Any OOS framing that may actually contain in-sample data\n"
    "- Conclusions that outrun the evidence in the methodology\n\n"
    + _CRITIC_SEVERITY_DEFINITIONS
)


# Conservative caps -- the critic returns a JSON array; allowing ~3000
# tokens is enough for ~15-20 findings + the prose summary.
_CRITIC_MAX_TOKENS = 3000


# ── Context builder ───────────────────────────────────────────────────

def _is_test_env() -> bool:
    return os.getenv("ENVIRONMENT", "development") == "test"


_DOC_TYPE_TO_LABEL: dict[str, str] = {
    "executive_brief":     "Executive Brief",
    "presentation_deck":   "Final Presentation Deck",
    "analytical_appendix": "Analytical Appendix",
    "presentation_script": "Presentation Script",
}


_ACADEMIC_CONTEXT_FOOTER = (
    "\nACADEMIC CONTEXT:\n"
    "Course: FNA 670, MSFA practicum, Queens University of Charlotte "
    "/ McColl School of Business\n"
    "Scope: Three-asset portfolio (equities, bonds, alternatives) with "
    "regime-conditional dynamic allocation\n"
    "Submission: June 30 panel defense\n"
)


async def _read_draft(
    owner_email: str, doc_type: str,
) -> dict[str, Any] | None:
    """Read the current draft of the given doc_type for the owner.
    Returns the Layer-3 draft dict (carries value_manifest) or None."""
    try:
        from tools.editor_drafts import get_current_draft_with_layer3
        return await get_current_draft_with_layer3(
            owner_email, doc_type)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "critic_review_draft_read_failed",
            document_type=doc_type, error=str(exc))
        return None


def _format_manifest_lines(
    manifest: dict[str, Any] | None,
) -> list[str]:
    if not manifest:
        return []
    token_to_value: dict[str, str] = {}
    for v, meta in manifest.items():
        if not isinstance(meta, dict):
            continue
        token = meta.get("token")
        if not token or not v:
            continue
        if token not in token_to_value:
            token_to_value[token] = str(v)
    return [f"  {tok}: {token_to_value[tok]}"
            for tok in sorted(token_to_value)]


async def build_critic_context(
    reviewer_email: str | None = None,
    document_type: str | None = None,
) -> str:
    """Assemble the context block injected into both critic prompts.

    document_type=None  -> full-package mode (all four documents,
                           merged manifest)
    document_type=<key> -> per-document mode (single document, its
                           manifest only)
    """
    is_per_doc = document_type is not None
    primary_label = (
        _DOC_TYPE_TO_LABEL.get(document_type or "", "Unknown")
        if is_per_doc else None)

    lines: list[str] = ["=== ADVERSARIAL CRITIC REVIEW CONTEXT ==="]
    lines.append("")
    if is_per_doc:
        lines.append(f"REVIEW SCOPE: Per-Document: {primary_label}")
    else:
        lines.append(
            "REVIEW SCOPE: Full Package: all four deliverables")

    # ── Numeric reference ──
    lines.append("")
    lines.append("NUMERIC REFERENCE (authoritative cache values)")
    lines.append(
        "These are the ground-truth figures. Any document claim that "
        "contradicts these is a factual error.")
    manifest_lines: list[str] = []
    if reviewer_email:
        if is_per_doc:
            draft = await _read_draft(reviewer_email, document_type)
            if draft:
                manifest_lines = _format_manifest_lines(
                    draft.get("value_manifest"))
        else:
            # Merge manifests across the four deliverables. Tokens
            # are stable across docs (same substitution table) so a
            # union by token keeps the list short and unique.
            merged: dict[str, str] = {}
            for dt in _DOC_TYPE_TO_LABEL:
                d = await _read_draft(reviewer_email, dt)
                if not d:
                    continue
                for v, meta in (d.get("value_manifest") or {}).items():
                    if not isinstance(meta, dict):
                        continue
                    token = meta.get("token")
                    if not token or not v:
                        continue
                    if token not in merged:
                        merged[token] = str(v)
            manifest_lines = [
                f"  {tok}: {merged[tok]}" for tok in sorted(merged)]
    if manifest_lines:
        lines.extend(manifest_lines)
    else:
        lines.append("  (no value manifest available)")

    # ── Primary document(s) ──
    lines.append("")
    if is_per_doc:
        lines.append(f"PRIMARY DOCUMENT FOR REVIEW: {primary_label}")
        if reviewer_email:
            draft = await _read_draft(reviewer_email, document_type)
            text = (draft or {}).get("content_text") or ""
            if text.strip():
                lines.append(text.strip())
            else:
                lines.append("(no draft content available)")
    else:
        lines.append("PRIMARY DOCUMENTS FOR REVIEW (all four):")
        if reviewer_email:
            for dt, label in _DOC_TYPE_TO_LABEL.items():
                draft = await _read_draft(reviewer_email, dt)
                text = (draft or {}).get("content_text") or ""
                lines.append("")
                lines.append(f"[{label}]")
                lines.append(text.strip() if text.strip()
                             else "(no draft content available)")

    lines.append(_ACADEMIC_CONTEXT_FOOTER)
    return "\n".join(lines)


# ── Model output parser ───────────────────────────────────────────────

_PROSE_MARKER = "PROSE_SUMMARY:"


def _parse_critic_output(
    raw: str,
) -> tuple[list[dict[str, Any]], str, bool]:
    """Parse a critic model's JSON-array-plus-prose-summary output.

    Returns (findings, prose_summary, parse_failed). Fault-tolerant:
    a malformed response returns ([], '', True) so the merge layer
    can degrade gracefully when one model fails.
    """
    if not raw:
        return [], "", True

    # Split on PROSE_SUMMARY: marker (case-insensitive search to be
    # forgiving on capitalization).
    marker_pos = raw.lower().find(_PROSE_MARKER.lower())
    if marker_pos != -1:
        json_part = raw[:marker_pos].strip()
        prose_part = raw[marker_pos + len(_PROSE_MARKER):].strip()
    else:
        json_part = raw.strip()
        prose_part = ""

    # The model may wrap the JSON in markdown fences (```json ... ```).
    # Strip them defensively.
    if json_part.startswith("```"):
        json_part = re.sub(
            r"^```[a-zA-Z]*\n", "", json_part)
        json_part = re.sub(r"\n```\s*$", "", json_part)

    # If the JSON has trailing prose without the marker, try to
    # locate the array bounds and parse.
    findings: list[dict[str, Any]] = []
    try:
        parsed = json.loads(json_part)
        if isinstance(parsed, list):
            findings = [f for f in parsed if isinstance(f, dict)]
        else:
            return [], prose_part, True
    except Exception:  # noqa: BLE001
        # Try a bracket extraction fallback: find the first [ and
        # the last matching ].
        try:
            start = json_part.index("[")
            end = json_part.rindex("]") + 1
            parsed = json.loads(json_part[start:end])
            if isinstance(parsed, list):
                findings = [
                    f for f in parsed if isinstance(f, dict)]
        except Exception:  # noqa: BLE001
            return [], prose_part, True

    return findings, prose_part, False


# ── Model calls ───────────────────────────────────────────────────────


def _call_gemini_critic(context_block: str) -> str:
    if _is_test_env():
        return ('[]\nPROSE_SUMMARY: '
                'Test environment -- no critic findings generated.')
    try:
        return call_gemini(
            GEMINI_MODEL,
            _GEMINI_CRITIC_SYSTEM,
            context_block,
            trigger="critic_review:gemini",
            max_output_tokens=_CRITIC_MAX_TOKENS)
    except Exception as exc:  # noqa: BLE001
        log.warning("critic_review_gemini_call_failed",
                    error=str(exc))
        return ""


def _call_grok_critic(context_block: str) -> str:
    if _is_test_env():
        return ('[]\nPROSE_SUMMARY: '
                'Test environment -- no critic findings generated.')
    try:
        from agents._xai_config import (
            resolve_xai_config, build_headers,
        )
        xai = resolve_xai_config()
        if xai is None:
            log.warning(
                "critic_review_grok_call_failed",
                error="no xai config (no API key configured)")
            return ""
        import httpx
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                xai.chat_url,
                headers=build_headers(xai.api_key, xai.provider),
                json={
                    "model": xai.model,
                    "messages": [
                        {"role": "system",
                         "content": _GROK_CRITIC_SYSTEM},
                        {"role": "user", "content": context_block},
                    ],
                    "max_tokens": _CRITIC_MAX_TOKENS,
                    "temperature": 0.5,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        log.warning("critic_review_grok_call_failed", error=str(exc))
        return ""


# ── Merge logic ───────────────────────────────────────────────────────

_SEVERITY_ORDER = {"Fatal": 0, "Major": 1, "Minor": 2}


def _normalise_severity(s: Any) -> str:
    if not isinstance(s, str):
        return "Minor"
    cap = s.strip().capitalize()
    return cap if cap in _SEVERITY_ORDER else "Minor"


def _normalise_text(s: Any) -> str:
    if not isinstance(s, str):
        return ""
    return re.sub(r"\s+", " ", s).strip().lower()


def _signature(f: dict[str, Any]) -> tuple[str, str, str]:
    """Cheap-but-effective dedup key: category + location + the first
    four normalised words of the description. Four words is the
    sweet spot for catching paraphrased findings ("Look-ahead bias in
    backtest implementation" vs "Look-ahead bias in backtest
    discovered" both share the first four words) without collapsing
    distinct findings that happen to start with the same generic
    framing words."""
    desc_words = _normalise_text(
        f.get("description")).split()[:4]
    return (
        _normalise_text(f.get("category")),
        _normalise_text(f.get("location")),
        " ".join(desc_words),
    )


def _merge_findings(
    gemini: list[dict[str, Any]], grok: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate by category + location + similar description.
    A pair is "agreed" when both models surface a finding with the
    same signature. Sort by severity then agreement."""
    by_sig: dict[
        tuple[str, str, str],
        dict[str, Any]] = {}
    for f in gemini:
        sig = _signature(f)
        by_sig[sig] = {**f, "raised_by": "gemini", "agreed": False}
    for f in grok:
        sig = _signature(f)
        if sig in by_sig:
            existing = by_sig[sig]
            # Same finding from both -- promote to agreed and keep
            # the harsher severity.
            sev_existing = _SEVERITY_ORDER.get(
                _normalise_severity(existing.get("severity")), 2)
            sev_new = _SEVERITY_ORDER.get(
                _normalise_severity(f.get("severity")), 2)
            if sev_new < sev_existing:
                existing["severity"] = _normalise_severity(
                    f.get("severity"))
            existing["agreed"] = True
            existing["raised_by"] = "both"
        else:
            by_sig[sig] = {**f, "raised_by": "grok", "agreed": False}
    merged = list(by_sig.values())
    merged.sort(key=lambda f: (
        _SEVERITY_ORDER.get(
            _normalise_severity(f.get("severity")), 2),
        0 if f.get("agreed") else 1,
    ))
    return merged


def _count_severities(
    findings: list[dict[str, Any]],
) -> tuple[int, int, int]:
    fatal = sum(1 for f in findings
                if _normalise_severity(f.get("severity")) == "Fatal")
    major = sum(1 for f in findings
                if _normalise_severity(f.get("severity")) == "Major")
    minor = sum(1 for f in findings
                if _normalise_severity(f.get("severity")) == "Minor")
    return fatal, major, minor


def _model_agreement_note(
    gemini: list[dict[str, Any]], grok: list[dict[str, Any]],
    merged: list[dict[str, Any]],
) -> str:
    if not gemini and not grok:
        return "Both models returned no findings."
    agreed = sum(1 for f in merged if f.get("agreed"))
    only_gemini = sum(
        1 for f in merged if f.get("raised_by") == "gemini")
    only_grok = sum(
        1 for f in merged if f.get("raised_by") == "grok")
    return (
        f"Gemini surfaced {len(gemini)} finding(s); "
        f"Grok surfaced {len(grok)}. "
        f"{agreed} agreed across both; "
        f"{only_gemini} raised only by Gemini, "
        f"{only_grok} raised only by Grok.")


# ── Public entry ──────────────────────────────────────────────────────


async def run_critic_review(
    reviewer_email: str | None,
    document_type: str | None = None,
) -> dict[str, Any]:
    """Run Gemini and Grok critic prompts in parallel, parse and merge
    their findings, and return the structured response."""
    context_block = await build_critic_context(
        reviewer_email=reviewer_email,
        document_type=document_type)

    # Parallel fan-out -- one model's latency, not two. Each helper
    # already returns "" on failure (the partial_failure flag picks
    # that case up below).
    gemini_raw, grok_raw = await asyncio.gather(
        asyncio.to_thread(_call_gemini_critic, context_block),
        asyncio.to_thread(_call_grok_critic,   context_block),
    )

    gemini_findings, gemini_prose, gemini_parse_failed = (
        _parse_critic_output(gemini_raw))
    grok_findings, grok_prose, grok_parse_failed = (
        _parse_critic_output(grok_raw))

    partial_failure = (
        (gemini_parse_failed or not gemini_raw)
        or (grok_parse_failed or not grok_raw))

    merged = _merge_findings(gemini_findings, grok_findings)
    fatal, major, minor = _count_severities(merged)

    # Pick the longer of the two prose summaries; both models are
    # asked to provide one. If both are empty, build a fallback.
    if len(gemini_prose) >= len(grok_prose):
        prose = gemini_prose or grok_prose
    else:
        prose = grok_prose or gemini_prose
    if not prose:
        prose = (
            f"Critic review identified {fatal} Fatal, {major} Major, "
            f"and {minor} Minor finding(s) across both models.")

    return {
        "document_scope":   document_type or "full_package",
        "gemini_findings":  gemini_findings,
        "grok_findings":    grok_findings,
        "merged_findings":  merged,
        "prose_summary":    prose,
        "fatal_count":      fatal,
        "major_count":      major,
        "minor_count":      minor,
        "model_agreement":  _model_agreement_note(
            gemini_findings, grok_findings, merged),
        "partial_failure":  partial_failure,
    }
