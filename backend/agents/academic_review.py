"""
agents/academic_review.py

Orchestration for POST /api/council/academic-review.

The council evaluates the project's academic readiness:
  1. A server-assembled context block (analytics inventory + uploaded
     academic documents) is injected into every agent prompt.
  2. Every peer agent (all council agents except the academic advisor)
     answers a stock four-part review question through its own expert
     lens, in parallel.
  3. The academic advisor acts as the ARBITER — it receives all peer
     responses plus the context block and synthesises a five-section,
     rubric-mapped verdict.

MODEL CHOICE: peers run on the project's current Sonnet (SONNET_MODEL =
claude-sonnet-4-6); the arbiter runs on the project's current Opus
(OPUS_MODEL = claude-opus-4-7) — an upgrade over the advisor's usual
Sonnet, applied only within this flow. The original spec named the dated
strings claude-sonnet-4-20250514 / claude-opus-4-20250514, but the
project standardised on -4-6 / -4-7 and deliberately moved off
claude-opus-4 because it retires 2026-06-15 — using it here would break
this feature before the July 1 final.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

try:
    import structlog
    log = structlog.get_logger(__name__)
except ImportError:  # pragma: no cover
    import logging
    log = logging.getLogger(__name__)  # type: ignore[assignment]

from agents.base import SONNET_MODEL, OPUS_MODEL, GEMINI_MODEL, call_claude
from tools.chart_vision import (
    ACADEMIC_REVIEW_CHARTS, get_charts_for_context, snapshots_dir_exists,
)

PEER_MODEL = SONNET_MODEL      # claude-sonnet-4-6
ARBITER_MODEL = OPUS_MODEL     # claude-opus-4-7  (Opus for the arbiter step only)
PEER_MAX_TOKENS = 800          # ~400-word cap with headroom
# 4000 token cap so the arbiter has room for ALL five rubric sections
# plus the two top-level summary lines plus the prefatory framing.
# The previous 2000-token cap was tight: the verbose Academic Rigour /
# PM Insight framing burned ~600 tokens before Section 1, leaving
# ~1400 for the five sections + 4 callouts (visual evidence, central
# finding, unresolved markers, external citations). Truncation
# routinely lopped off Section 5 — UAT #128 / #125 reported "Overall
# Readiness Assessment absent" and "only 4 sections returned"; both
# were the same truncation symptom. The bigger budget plus the
# tightened evaluator scoring (all_sections_present rubric in
# evaluator_prompts.py — missing sections now score below threshold
# so the harness retries) and the post-generation Section 5 fallback
# (assemble_section_5_fallback below) together guarantee the verdict
# always carries a Section 5 by the time it streams.
ARBITER_MAX_TOKENS = 4000


def _academic_review_visual_context(
    n_strategies: int | None = None,
) -> list[dict] | None:
    """ACADEMIC_REVIEW_CHARTS snapshots as content blocks, or None when
    no snapshots are on disk (cold deploy, first run). Used by every
    Claude-based peer and the arbiter. The Gemini and Grok peers route
    through their own SDKs and do not consume Anthropic image blocks —
    they fall back to the text-only path naturally.

    n_strategies — threaded through to the chart-vision scope sentences
    so the all-strategy captions render the exact count. The endpoint
    reads it from gather_review_context()["analytics"]["strategy_count"]
    and passes it through the fan-out and arbiter call chain."""
    if not snapshots_dir_exists():
        log.info("academic_review_no_snapshots_dir",
                 note="proceeding without visual context")
        return None
    blocks = get_charts_for_context(
        ACADEMIC_REVIEW_CHARTS, n_strategies=n_strategies)
    if not blocks:
        log.info("academic_review_no_snapshots_available",
                 note="proceeding without visual context")
        return None
    return blocks

# ── Peer agent registry ───────────────────────────────────────────────────────
# Every council agent EXCEPT the academic advisor (the arbiter). Mirrors
# main.py _AGENT_META; kept here as config so the peer list is derived, not
# hardcoded at the call site, and so this module never imports main.py.
_PEER_AGENTS: dict[str, dict[str, str]] = {
    "equity_analyst": {
        "name": "Equity Analyst", "kind": "claude",
        "lens": "equity market structure, factor exposure, and momentum signals",
    },
    "fixed_income_analyst": {
        "name": "Fixed Income Analyst", "kind": "claude",
        "lens": "fixed income, duration, credit, and the equity-bond "
                "diversification question",
    },
    "risk_manager": {
        "name": "Risk Manager", "kind": "claude",
        "lens": "tail risk, drawdown, stress testing, and statistical rigour",
    },
    "quant_backtester": {
        "name": "Quant Backtester", "kind": "claude",
        "lens": "backtest methodology, cross-validation, and overfitting control",
    },
    "cio": {
        "name": "Chief Investment Officer", "kind": "claude",
        "lens": "the overall investment thesis and how the pieces cohere",
    },
    "independent_analyst": {
        "name": "Independent Analyst (Gemini)", "kind": "gemini",
        "lens": "independent challenge, blind spots, and alternative readings",
    },
    "contrarian_analyst": {
        "name": "Contrarian Analyst (Grok)", "kind": "grok",
        "lens": "contrarian stress-testing and the strongest case against the work",
    },
}

DOC_TYPE_LABELS: dict[str, str] = {
    "midpoint_requirements": "MIDPOINT CHECK-IN REQUIREMENTS",
    "final_presentation_requirements": "FINAL PRESENTATION REQUIREMENTS",
    "midpoint_draft": "MIDPOINT DRAFT",
    "presentation_slides": "PRESENTATION SLIDES",
    "presentation_script": "PRESENTATION SCRIPT",
    # PR — academic review brief-specific rubric. The executive brief
    # used to land under "other" (the catch-all) which rendered fine
    # but routed the rubric through the midpoint instructions and
    # produced a 5.5/10 scoring floor. Promoting brief_review to a
    # first-class document type lets the context block surface the
    # brief content with a clear label AND lets the rubric switch
    # in build_arbiter_user_message detect the brief case directly.
    "brief_review": "EXECUTIVE BRIEF",
    # PR — deck-specific + appendix-specific rubrics (extends #351).
    # Both surfaced because their previous routing through the
    # midpoint rubric produced the same structural 5.5/10 floor as
    # the brief used to.
    "deck_review": "PRESENTATION DECK",
    "appendix_review": "ANALYTICAL APPENDIX",
    "other": "OTHER REFERENCE DOCUMENT",
}

_DOC_CHAR_CAP = 8000   # per-document, keeps prompts bounded


def peer_agent_ids() -> list[str]:
    """The peer agents — every council agent except the academic advisor
    (which is the arbiter). Derived from the registry, never hardcoded."""
    return list(_PEER_AGENTS.keys())


def _is_test_env() -> bool:
    return os.getenv("ENVIRONMENT", "development") == "test"


# ── Context assembly ──────────────────────────────────────────────────────────

def group_documents_by_type(docs: list[dict]) -> dict[str, list[dict]]:
    """
    Groups uploaded academic documents by document_type. Every type in
    DOC_TYPE_LABELS appears as a key — types with no uploads map to an
    empty list, so callers can render "(not yet uploaded)" without a
    missing-key check.
    """
    grouped: dict[str, list[dict]] = {t: [] for t in DOC_TYPE_LABELS}
    for d in docs or []:
        dt = d.get("document_type", "other")
        grouped.setdefault(dt, []).append(d)
    return grouped


# May 26 2026 — team role division context, revised to reflect the
# explicit ownership framing the course encourages: human judgment
# layered on automated validation. The prior phrasing positioned
# Michael as "front-loaded engineering, not analytical disengagement"
# which framed his work as a defensive disclaimer. The course
# rewards the structure directly: Michael builds the validation
# infrastructure that makes the team's analytical judgments
# auditable; Bob owns the analytical interpretation that sits on
# top; Molly owns the human UAT layer that catches what automated
# checks miss.
_TEAM_ROLE_CONTEXT_LINES: list[str] = [
    "",
    "TEAM ROLE DIVISION CONTEXT — analytical ownership",
    "The team operates a layered division of labor that the FNA 670 "
    "course encourages: human judgment on top of automated "
    "validation. Each member owns a distinct analytical layer.",
    "",
    "Michael Ruurds builds and operates the validation infrastructure "
    "that underpins the team's analytical integrity. This includes "
    "the three-layer independent audit, the automated QA checks, and "
    "the AI council. His commit and QA activity reflect this "
    "engineering layer.",
    "",
    "Bob Thao interprets the platform's findings, makes analytical "
    "judgments on items the platform flags for human review, and "
    "owns the academic narrative and financial conclusions.",
    "",
    "Molly Murdock conducts human UAT. She verifies that platform "
    "outputs match real-world expectations, files failure reports "
    "when automated checks miss edge cases, and provides the human "
    "sign-off layer that automated testing cannot replace.",
    "",
    "Evaluate Section 3 against this layered ownership model, not "
    "against a missing human-only division of labor. A draft that "
    "describes the layers clearly and follows with activity evidence "
    "satisfies the rubric. Do NOT flag Michael's engineering activity "
    "as analytical disengagement; do NOT flag Molly's lower commit "
    "count as a contribution gap. Each layer is its own analytical "
    "ownership.",
]


def format_team_activity_block(
    team_activity: dict[str, Any] | None,
    team_members: list[tuple[str, str]] | None = None,
) -> list[str]:
    """
    Renders the team-activity summary into context lines for the agent
    prompt. Analytical sessions ONLY — testing-session activity is never
    shown to agents. Every project-team member is listed, including any
    with no recorded activity, so the division-of-labour assessment is
    fair. Returns [] when no activity data is available.

    team_members is the (email, display_name) list resolved from
    platform_users by _resolve_team_members(); when None (e.g. a direct
    call), it falls back to the config allowlist.
    """
    if not team_activity:
        return []
    from config import PROJECT_TEAM_EMAILS, TEAM_MEMBER_NAMES

    if team_members is None:
        team_members = [(e, TEAM_MEMBER_NAMES.get(e, e))
                        for e in sorted(PROJECT_TEAM_EMAILS)]

    per_member = {m["user"]: m for m in team_activity.get("per_member", [])}
    lines: list[str] = ["", "TEAM ENGAGEMENT (analytical sessions only)"]
    for email, name in team_members:
        m = per_member.get(email)
        if not m:
            lines.append(f"- {name}: no recorded platform activity")
            continue
        lines.append(
            f"- {name}: {m.get('council_interactions', 0)} council, "
            f"{m.get('academic_review_sessions', 0)} academic review, "
            f"{m.get('document_uploads', 0)} uploads, "
            f"{m.get('qa_audits', 0)} QA, {m.get('page_views', 0)} page views"
            + (f" — last active {str(m['last_active'])[:10]}"
               if m.get("last_active") else "")
        )
    commits = team_activity.get("commits", {})
    if commits.get("total"):
        by_author = ", ".join(
            f"{TEAM_MEMBER_NAMES.get(a, a)} {n}"
            for a, n in (commits.get("by_author") or {}).items()
        )
        lines.append(
            f"Commits: {commits.get('total', 0)} total, "
            f"{commits.get('this_week', 0)} in the last 7 days — {by_author}"
        )
    lines.append(
        f"Total substantive interactions: {team_activity.get('total_interactions', 0)}"
    )
    return lines


def _team_activity_multi_user(team_activity: dict[str, Any] | None) -> bool:
    """
    True when more than one team member has recorded activity — gates
    the division-of-labour review dimension. With a single active user
    the dimension is omitted (a not-yet-adopted platform must not be
    penalised), so this check decides whether it appears at all.
    """
    if not team_activity:
        return False
    active: set[str] = set()
    for m in team_activity.get("per_member", []):
        substantive = (m.get("council_interactions", 0)
                       + m.get("academic_review_sessions", 0)
                       + m.get("document_uploads", 0) + m.get("qa_audits", 0))
        if substantive > 0:
            active.add(m["user"])
    for author in (team_activity.get("commits", {}).get("by_author") or {}):
        active.add(author)
    return len(active) > 1


async def _resolve_team_members() -> list[tuple[str, str]]:
    """
    The project team — active sysadmin and team_member users from
    platform_users — as (email, display_name) pairs, sorted by email.

    Fail-open: if the platform_users table is unreachable or holds no
    matching rows, fall back to the config allowlist so the Academic
    Review still assembles its team-engagement block.
    """
    from config import PROJECT_TEAM_EMAILS, TEAM_MEMBER_NAMES
    fallback = [(e, TEAM_MEMBER_NAMES.get(e, e))
                for e in sorted(PROJECT_TEAM_EMAILS)]
    try:
        from tools.platform_users import list_all_users
        users = await list_all_users()
        members = sorted(
            (u["email"], u.get("display_name") or u["email"])
            for u in users
            if u.get("is_active")
            and u.get("role") in ("sysadmin", "team_member")
        )
        return members or fallback
    except Exception as exc:  # noqa: BLE001
        log.warning("academic_review_team_resolve_failed", error=str(exc))
        return fallback


def build_review_context_block(
    analytics: dict[str, Any], docs_by_type: dict[str, list[dict]],
    team_activity: dict[str, Any] | None = None,
    team_members: list[tuple[str, str]] | None = None,
    target_review_type: str | None = None,
    value_manifest: dict[str, Any] | None = None,
) -> str:
    """
    Renders the analytics inventory, the grouped documents and the
    team-activity summary into one structured text block injected into
    every agent prompt. Missing document types render as "(not yet
    uploaded)" — never an error.

    target_review_type — June 23 2026, per-doc scoping. When supplied
    (a value from DOC_TYPE_LABELS like "brief_review", "deck_review",
    "appendix_review", or "presentation_script"), the documents
    block is split into a PRIMARY DOCUMENT FOR REVIEW section (full
    content of the target type) and a SUPPORTING CONTEXT section
    (500-char-truncated summary of each non-target type) so the
    arbiter has cross-reference awareness without flooding the
    prompt. None (the cross-document default) renders every type at
    full content under PROJECT DOCUMENTS unchanged.

    value_manifest — June 23 2026, Concern 6b. When supplied (the
    target document's editor_drafts.value_manifest snapshot, ie
    {value: {token, data_hash, generated_at}}), a NUMERIC REFERENCE
    block is appended after ANALYTICS INVENTORY giving the arbiter
    the authoritative cache values that were substituted into the
    document at generation time. Only the per-doc reviews carry a
    target document and therefore a target manifest -- cross-doc
    reviews intentionally skip this section (the cross-deliverable
    consistency check covers that surface).
    """
    lines: list[str] = ["=== PROJECT CONTEXT FOR ACADEMIC REVIEW ===", ""]

    # — Analytics inventory —
    lines.append("ANALYTICS INVENTORY")
    lines.append(f"- Strategies analysed: {analytics.get('strategy_count', 0)}")
    pr = analytics.get("performance_range")
    if pr:
        lines.append(
            f"- Performance record: {pr['start']} to {pr['end']} "
            f"({pr['n_months']} months)"
        )
    else:
        lines.append("- Performance record: (no monthly data loaded)")
    rf = analytics.get("risk_free_rate")
    if rf is not None:
        lines.append(
            f"- Risk-free rate: {rf * 100:.2f}% "
            "(FRED DTB3, 3-month T-bill, mean monthly rate annualised)"
        )
    comps = analytics.get("analytics_components") or []
    lines.append(
        "- Analytics components available: "
        + (", ".join(comps) if comps else "(none — analytics data not yet loaded)")
    )

    # — Numeric reference (Concern 6b, June 23 2026) —
    # Authoritative cache values for the target document. The
    # arbiter validates per-claim figures against this list; the
    # per-document arbiter prompt (Concern 6c) instructs it to flag
    # mismatches as HIGH severity numeric inconsistency and unknown
    # figures as freehand-figure / manual-verification cases.
    # Cross-document reviews intentionally skip this section -- the
    # cross-deliverable consistency check covers that surface and a
    # full union of every doc's manifest would flood the prompt.
    if target_review_type and value_manifest:
        lines.append("")
        lines.append(
            "NUMERIC REFERENCE "
            "(authoritative cache values for this review)")
        lines.append(
            "These values were substituted into the document at "
            "generation time. Treat any figure in the document that "
            "contradicts these as a potential error.")
        # value_manifest is {value -> {token, data_hash,
        # generated_at}}. Emit one line per token in
        # TOKEN: value form. Skip entries with missing tokens or
        # missing values defensively -- a corrupt manifest entry
        # should not abort the render.
        token_to_value: dict[str, str] = {}
        for v, meta in value_manifest.items():
            if not isinstance(meta, dict):
                continue
            token = meta.get("token")
            if not token or not v:
                continue
            # If two values share a token (shouldn't happen but
            # could under a manifest-corruption edge), keep the
            # first encountered -- the per-token list is meant to
            # be unique.
            if token not in token_to_value:
                token_to_value[token] = str(v)
        for tok in sorted(token_to_value):
            lines.append(f"  {tok}: {token_to_value[tok]}")
        if not token_to_value:
            lines.append("  (manifest is empty)")

    # — Documents —
    if target_review_type and target_review_type in DOC_TYPE_LABELS:
        # Per-doc scoping: split into PRIMARY + SUPPORTING. Only the
        # FOUR review-target types render with content -- the others
        # (requirements docs, midpoint_draft, other) stay in the
        # SUPPORTING block when present, since they can still inform
        # consistency checks.
        primary_label = DOC_TYPE_LABELS[target_review_type]
        lines.append("")
        lines.append(
            f"PRIMARY DOCUMENT FOR REVIEW: {primary_label}")
        primary_docs = docs_by_type.get(target_review_type) or []
        if not primary_docs:
            lines.append("(not yet uploaded)")
        else:
            for d in primary_docs:
                text = (d.get("content_text") or "").strip()
                if len(text) > _DOC_CHAR_CAP:
                    text = (text[:_DOC_CHAR_CAP]
                            + "\n…[document truncated for review]")
                lines.append(
                    f"\n[{primary_label}: "
                    f"{d.get('name', 'document')}]\n{text}")
        # Supporting context -- abbreviated summaries of the other
        # types, 500 chars each so the arbiter can spot cross-doc
        # consistency issues without paying the full content cost.
        lines.append("")
        lines.append("SUPPORTING CONTEXT (abbreviated)")
        any_supporting = False
        for dtype, label in DOC_TYPE_LABELS.items():
            if dtype == target_review_type:
                continue
            docs = docs_by_type.get(dtype) or []
            if not docs:
                continue
            for d in docs:
                any_supporting = True
                text = (d.get("content_text") or "").strip()
                snippet = text[:500]
                if len(text) > 500:
                    snippet += "…"
                lines.append(f"\n[{label}]\n{snippet}")
        if not any_supporting:
            lines.append("(no supporting documents present)")
    else:
        lines.append("")
        lines.append("PROJECT DOCUMENTS")
        for dtype, label in DOC_TYPE_LABELS.items():
            docs = docs_by_type.get(dtype) or []
            if not docs:
                lines.append(f"\n[{label}]\n(not yet uploaded)")
                continue
            for d in docs:
                text = (d.get("content_text") or "").strip()
                if len(text) > _DOC_CHAR_CAP:
                    text = (text[:_DOC_CHAR_CAP]
                            + "\n…[document truncated for review]")
                lines.append(
                    f"\n[{label}: {d.get('name', 'document')}]\n{text}")

    # — Team role division context (May 28 2026) —
    # Prepended to the engagement block so the verdict reads the role
    # framing BEFORE the raw activity counts. Always present, even
    # when team_activity is empty — the role context still informs
    # how the verdict frames Bob's analytical surfaces and Michael's
    # engineering work for the BOB-callout review in section 4.
    lines.extend(_TEAM_ROLE_CONTEXT_LINES)

    # — Team engagement —
    lines.extend(format_team_activity_block(team_activity, team_members))

    return "\n".join(lines)


async def _gather_analytics_snapshot() -> dict[str, Any]:
    """
    Lightweight descriptive snapshot of the analytics layer, read straight
    from the cache tables — NOT the full /api/v1/analytics/academic compute.
    """
    snapshot: dict[str, Any] = {
        "strategy_count": 0,
        "performance_range": None,
        "risk_free_rate": None,
        "analytics_components": [],
    }
    try:
        from tools.cache import (
            get_data_status, get_monthly_returns, get_latest_strategy_cache,
        )

        strategies = await get_latest_strategy_cache()
        snapshot["strategy_count"] = len(strategies) if strategies else 0

        monthly = await get_monthly_returns()
        if monthly and monthly.get("dates"):
            dates = monthly["dates"]
            snapshot["performance_range"] = {
                "start": dates[0], "end": dates[-1], "n_months": len(dates),
            }
            rf = monthly.get("rf") or []
            if rf:
                snapshot["risk_free_rate"] = round(sum(rf) / len(rf) * 12, 4)

        ds = await get_data_status()
        ff_rows = 0
        for t in (ds or {}).get("tables", []):
            if t.get("name") == "ff_factors_monthly":
                ff_rows = t.get("row_count", 0)

        if snapshot["strategy_count"] and snapshot["performance_range"]:
            comps = ["summary statistics", "rolling correlation",
                     "regime-conditional performance", "drawdown comparison",
                     "turnover"]
            if ff_rows:
                comps.append("Fama-French factor loadings")
            snapshot["analytics_components"] = comps
    except Exception as exc:  # noqa: BLE001
        log.warning("academic_review_analytics_snapshot_failed", error=str(exc))
    return snapshot


# An editor document_type → the review document_type it stands in for.
# When the reviewer has a current editor draft of one of these, its live
# content is reviewed in preference to any uploaded file of that kind.
_EDITOR_TO_REVIEW_TYPE = {
    # June 21 2026 -- midpoint_paper removed. The May 27 midpoint
    # paper deadline has passed; the editor draft remains for audit
    # history but no longer needs to overlay into the council review
    # context. Leaving the mapping in place caused the brief / deck /
    # appendix review contexts to bleed midpoint_draft content into
    # the docs_by_type slot (the academic_documents-table filter at
    # tools/academic_context.py::_INJECTION_EXCLUDED_TYPES blocks the
    # row-read path but the editor-draft overlay bypassed it).
    # PR — academic review brief-specific rubric. Previously "other"
    # (the catch-all) which routed the executive brief's verdict
    # through the midpoint rubric and produced a structural 5.5/10
    # floor — the brief's Section 5 (Final Recommendations) scored
    # Needs Work mechanically because the midpoint rubric expects
    # "Next Steps and Open Questions" framing. The new "brief_review"
    # value is consumed by the rubric switch in
    # build_arbiter_user_message / run_arbiter_with_harness and by
    # compute_review_score's weighted-aggregate mode.
    "executive_brief": "brief_review",
    # PR — deck-specific + appendix-specific rubrics. Previously the
    # deck routed to "presentation_slides" (a document-type label,
    # not a rubric) and the appendix had no routing at all, so both
    # fell through to the midpoint rubric with the same structural
    # 5.5/10 floor. The new values flow through the same rubric
    # switch + weighted-aggregate mode the brief uses.
    "presentation_deck": "deck_review",
    "analytical_appendix": "appendix_review",
}


async def gather_review_context(
    reviewer_email: str | None = None,
    document_type: str | None = None,
) -> dict[str, Any]:
    """
    Assembles the full review context: the analytics snapshot, the
    documents grouped by type, and the formatted context block that gets
    injected into every agent prompt.

    reviewer_email — when given, the reviewer's current editor drafts
    (tools/editor_drafts) take precedence over an uploaded academic
    document of the same kind, so Academic Review evaluates the live
    working draft rather than a stale uploaded file.

    document_type — June 23 2026, per-doc scoping. When supplied
    (a query-param value like "executive_brief" /
    "presentation_deck" / "analytical_appendix" /
    "presentation_script"), the assembled context block splits into
    a PRIMARY DOCUMENT FOR REVIEW section (full content of the
    target) plus a SUPPORTING CONTEXT section (abbreviated summaries
    of the other deliverables). None (the cross-document default)
    keeps the legacy behaviour: every document type at full content
    under PROJECT DOCUMENTS.
    """
    analytics = await _gather_analytics_snapshot()
    docs: list[dict] = []
    try:
        from tools.academic_context import _read_all_with_content
        docs = await _read_all_with_content()
    except Exception as exc:  # noqa: BLE001
        log.warning("academic_review_documents_read_failed", error=str(exc))
    docs_by_type = group_documents_by_type(docs)

    # Overlay the reviewer's editor drafts — a current draft replaces the
    # uploaded file of the corresponding type.
    if reviewer_email:
        try:
            from tools.editor_drafts import get_current_draft
            for ed_type, rv_type in _EDITOR_TO_REVIEW_TYPE.items():
                draft = await get_current_draft(reviewer_email, ed_type)
                if draft and (draft.get("content_text") or "").strip():
                    docs_by_type[rv_type] = [{
                        "document_type": rv_type,
                        "name": (f"{draft['title']} "
                                 f"(editor draft, v{draft['version']})"),
                        "content_text": draft["content_text"],
                    }]
        except Exception as exc:  # noqa: BLE001
            log.warning("academic_review_editor_overlay_failed",
                        error=str(exc))

    # Team-activity summary — analytical sessions only; testing-session
    # activity is never injected into agent context.
    team_activity: dict[str, Any] | None = None
    try:
        from tools.activity_log import get_activity_summary
        team_activity = await get_activity_summary(analytical_only=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("academic_review_team_activity_failed", error=str(exc))
    multi_user = _team_activity_multi_user(team_activity)

    # Project team — resolved from platform_users (fail-open to config).
    team_members = await _resolve_team_members()
    # Map the editor's document_type (the public query-param value)
    # to the review-internal review_type key the documents dict is
    # keyed under. Script's editor type IS its review key, so the
    # lookup falls through to the identity mapping there.
    target_review_type: str | None = None
    target_value_manifest: dict[str, Any] | None = None
    if document_type:
        target_review_type = _EDITOR_TO_REVIEW_TYPE.get(
            document_type, document_type)
        # Concern 6b -- read the target draft's value_manifest so
        # the context block can surface the authoritative cache
        # values to the arbiter. The Layer-3 helper returns the
        # manifest alongside content_text; we already overlaid the
        # draft above so this read is for the manifest only.
        if reviewer_email:
            try:
                from tools.editor_drafts import (
                    get_current_draft_with_layer3,
                )
                draft_l3 = await get_current_draft_with_layer3(
                    reviewer_email, document_type)
                if draft_l3:
                    target_value_manifest = (
                        draft_l3.get("value_manifest") or None)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "academic_review_value_manifest_read_failed",
                    document_type=document_type, error=str(exc))
    block = build_review_context_block(
        analytics, docs_by_type, team_activity, team_members,
        target_review_type=target_review_type,
        value_manifest=target_value_manifest)
    present = [t for t, v in docs_by_type.items() if v]
    missing = [t for t, v in docs_by_type.items() if not v]
    log.info(
        "academic_review_context_assembled",
        strategy_count=analytics["strategy_count"],
        document_types_present=present,
        document_types_missing=missing,
        team_activity_multi_user=multi_user,
    )
    return {
        "analytics": analytics,
        "documents_by_type": docs_by_type,
        "document_types_present": present,
        "document_types_missing": missing,
        "team_activity": team_activity,
        "multi_user_activity": multi_user,
        "context_block": block,
    }


# ── Peer fan-out ──────────────────────────────────────────────────────────────

_PEER_QUESTION_BASE = (
    "Review this project's MIDPOINT CHECK readiness against the FNA 670 "
    "rubric. The submission is a 3-page midpoint paper, due May 27 2026. "
    "Page budget per section: Data and Methodology (1p), Preliminary "
    "Results and Diagnostics (1p), Roles and Division of Labor (0.5p), "
    "Next Steps and Open Questions (0.5p). Total body 825 words.\n\n"
    "Address every area below concisely, from your expert lens. Evaluate "
    "what IS in the current draft, not against a target the team has not "
    "yet committed to. The midpoint check is a progress report, not the "
    "final submission.\n\n"
    "1. DATA AND METHODOLOGY (1p) — three-asset universe (US equities, IG "
    "bonds, HY bonds), 286+ monthly observations, ten strategies, "
    "statistical methodology (FDR correction, deflated Sharpe, CPCV, "
    "block bootstrap), data provenance. Name specific gaps.\n"
    "2. PRELIMINARY RESULTS AND DIAGNOSTICS (1p) — the 2022 correlation "
    "regime break (the central finding), the FDR result, top-line strategy "
    "comparisons, audit verification. Does the section quantify the 2022 "
    "break with actual pre/post values, and present the FDR result "
    "honestly?\n"
    "3. ROLES AND DIVISION OF LABOR (0.5p) — does Section 3 state each "
    "member's analytical ownership directly, framed as human judgment "
    "on top of automated validation? See the TEAM ROLE DIVISION CONTEXT "
    "above for the ownership model the section should evaluate against.\n"
    "4. NEXT STEPS AND OPEN QUESTIONS (0.5p) — does the section name "
    "concrete next steps before the July 1 final submission, and at "
    "least one substantive open question for the midpoint meetup peer "
    "review?\n"
    "5. AREAS FOR FURTHER INVESTIGATION — the highest-leverage actions "
    "before the May 27 midpoint deadline. Specific, not generic.\n"
)

# Sixth dimension (was 5th before the May 26 2026 midpoint-rubric
# revision) — appended only when more than one team member has
# recorded activity. With a single active user the platform may simply
# not be in use by the whole team yet; assessing task-sharing then
# would penalise an adoption gap, not a division-of-labour problem.
# Frame against the layered ownership model (see TEAM ROLE DIVISION
# CONTEXT above): genuine shared effort across the validation layer
# (Michael), the analytical-narrative layer (Bob), and the UAT layer
# (Molly) is the desired pattern — not equal raw counts.
_PEER_DIMENSION_5 = (
    "6. TEAM ENGAGEMENT AND TASK SHARING — Based on the team activity "
    "summary provided, assess whether the team's engagement reflects "
    "the layered ownership model (validation infrastructure, "
    "analytical narrative, human UAT). Each member should be active "
    "in their layer; equal raw counts across layers are NOT the "
    "target. Flag genuine analytical gaps, not engineering vs "
    "analytical activity disparity.\n"
)

_PEER_QUESTION_CLOSE = "\nKeep your whole response under 400 words."


def _peer_question(multi_user: bool) -> str:
    """The peer review question — gains the team-engagement dimension
    only when more than one member has platform activity."""
    parts = [_PEER_QUESTION_BASE]
    if multi_user:
        parts.append(_PEER_DIMENSION_5)
    parts.append(_PEER_QUESTION_CLOSE)
    return "".join(parts)


def _peer_system_prompt(meta: dict[str, str]) -> str:
    # The Claude-based peers (everyone except the Gemini and Grok dissenters)
    # receive ACADEMIC_REVIEW_CHARTS snapshots alongside the prompt:
    # rolling_correlation, cumulative_returns, regime_signals, factor_loadings,
    # drawdown_periods, significance_journey, oos_performance.
    # Verify the document's claims against the visual evidence — a chart
    # contradicting a written claim is a flagworthy methodological concern
    # under Requirements and Rubric Alignment. The Gemini and Grok peers
    # do not consume Anthropic content blocks; they fall back to the
    # text-only path naturally.
    from agents.base import VISUAL_REASONING_RULES
    return (
        f"You are the {meta['name']} on a quantitative investment council "
        f"advising a graduate practicum team (course FNA 670, McColl School "
        f"of Business). The team is preparing a "
        f"GRADED academic submission for the Forest Capital portfolio-analysis "
        f"project. Review the project through your expert lens — {meta['lens']}. "
        f"Be direct, specific and actionable: the team needs to know what to "
        f"fix before a graded deadline, not generic encouragement.\n\n"
        f"VISUAL CONTEXT — chart snapshots may be attached: "
        f"rolling_correlation, cumulative_returns, regime_signals, "
        f"factor_loadings, drawdown_periods, significance_journey, "
        f"oos_performance. Verify the document's quantitative claims against "
        f"the visual evidence. drawdown_periods and significance_journey "
        f"directly support claims about strategy robustness; oos_performance "
        f"directly supports claims about out-of-sample validity. A chart "
        f"that contradicts the document's claim is a flagworthy issue.\n\n"
        f"{VISUAL_REASONING_RULES}"
    )


def _peer_user_message(context_block: str, multi_user: bool = False) -> str:
    return f"{_peer_question(multi_user)}\n\n{context_block}"


def _mock_peer_review(meta: dict[str, str]) -> str:
    return (
        f"[{meta['name']} — mock review: live model unavailable in this "
        f"environment] 1. Data sufficiency: the 282-month record is adequate; "
        f"flag factor-model coverage. 2. Rubric alignment: cannot verify "
        f"without the uploaded requirements. 3. Deliverable quality: upload "
        f"drafts to enable assessment. 4. Further investigation: prioritise "
        f"the {meta['lens']} angle before the deadline."
    )


def _call_gemini_peer(system_prompt: str, user_message: str) -> str:
    """Replicates agents/independent_analyst.py's Gemini call pattern —
    the shared call_gemini wrapper over the google-genai SDK."""
    api_key = os.getenv("GOOGLE_API_KEY", "")
    if _is_test_env() or not api_key:
        return _mock_peer_review(_PEER_AGENTS["independent_analyst"])
    from agents.base import call_gemini
    return call_gemini(GEMINI_MODEL, system_prompt, user_message,
                       trigger="academic_review_peer:gemini")


def _call_grok_peer(system_prompt: str, user_message: str) -> str:
    """Replicates agents/contrarian_analyst.py's Grok (xAI) call pattern."""
    from agents._xai_config import resolve_xai_config, build_headers
    xai = resolve_xai_config()
    if _is_test_env() or xai is None:
        return _mock_peer_review(_PEER_AGENTS["contrarian_analyst"])
    import httpx
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            xai.chat_url,
            headers=build_headers(xai.api_key, xai.provider),
            json={
                "model": xai.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "max_tokens": PEER_MAX_TOKENS,
                "temperature": 0.7,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def run_peer_agent(
    agent_id: str, context_block: str, multi_user: bool = False,
    n_strategies: int | None = None,
) -> tuple[str, str]:
    """
    Runs one peer agent's review. Synchronous — designed to be wrapped in
    asyncio.to_thread() for the parallel fan-out. Returns (agent_id, text);
    never raises — a failed agent degrades to a mock review so the council
    always returns a full set of peer responses.

    n_strategies — threaded through to the chart-vision scope sentences.
    """
    meta = _PEER_AGENTS[agent_id]
    # Test environment has no API keys — short-circuit to a deterministic
    # mock (consistent with the Gemini/Grok helpers), so the fan-out is
    # fast and deterministic under pytest.
    if _is_test_env():
        return agent_id, _mock_peer_review(meta)
    system_prompt = _peer_system_prompt(meta)
    user_message = _peer_user_message(context_block, multi_user)

    # ACADEMIC_REVIEW_CHARTS snapshots — built once per peer call and
    # threaded through the Claude generator. Gemini and Grok do not use
    # Anthropic content blocks, so the visual_context kwarg is reserved
    # for the call_claude path only. Evaluators MUST NOT see this — the
    # harness's _evaluate omits the kwarg.
    visual_context = _academic_review_visual_context(n_strategies)

    # The agent call is routed through the generator-evaluator harness.
    # _generate dispatches on the agent kind; the harness retries it with
    # evaluator feedback when the response scores below threshold. This
    # runs inside the peer's own asyncio.to_thread task, so a retry never
    # blocks the other peers in the fan-out.
    def _generate(prompt: str) -> str:
        if meta["kind"] == "claude":
            return call_claude(PEER_MODEL, system_prompt, prompt,
                               max_tokens=PEER_MAX_TOKENS,
                               visual_context=visual_context,
                               trigger="academic_review_peer:claude")
        if meta["kind"] == "gemini":
            return _call_gemini_peer(system_prompt, prompt)
        if meta["kind"] == "grok":
            return _call_grok_peer(system_prompt, prompt)
        raise ValueError(f"unknown peer kind: {meta['kind']}")

    try:
        from agents.harness import GeneratorEvaluatorHarness
        from agents.evaluator_prompts import academic_review_peer_evaluator_prompt
        harness = GeneratorEvaluatorHarness()
        result = harness.run(
            generator_fn=_generate,
            evaluator_prompt=academic_review_peer_evaluator_prompt(meta["name"]),
            generator_prompt=user_message,
            context=context_block[:6000],
            agent_id=agent_id,
        )
        return agent_id, result.response
    except Exception as exc:  # noqa: BLE001
        log.warning("academic_review_peer_failed", agent=agent_id, error=str(exc))
    return agent_id, _mock_peer_review(meta)


async def run_peer_fan_out(
    context_block: str, multi_user: bool = False,
    n_strategies: int | None = None,
) -> dict[str, str]:
    """Fans the review question out to every peer agent in parallel.

    n_strategies — threaded through to every peer's chart-vision scope
    sentences. Read by the endpoint from
    gather_review_context()["analytics"]["strategy_count"]."""
    ids = peer_agent_ids()
    results = await asyncio.gather(
        *[asyncio.to_thread(
            run_peer_agent, aid, context_block, multi_user, n_strategies)
          for aid in ids]
    )
    return {aid: text for aid, text in results}


# ── Arbiter (academic advisor, Opus) ──────────────────────────────────────────

_ARBITER_INSTRUCTIONS = """=== YOUR TASK — ARBITER VERDICT (MIDPOINT CHECK) ===
You are the arbiter for the FNA 670 MIDPOINT CHECK submission (3-page
paper, due May 27 2026). This is a PROGRESS REPORT, not the July 1
final submission. Evaluate against the midpoint rubric, not the final
submission standard.

CRITICAL EVALUATION POSTURE — read what IS in the current draft.
Do not speculate, do not list things you would expect to see, do not
flag absence of features that the team has not committed to for the
midpoint check. The midpoint check rewards demonstrated analytical
progress; it does not require the full final-submission polish.

DO NOT proactively hunt for [[BOB]] / [[VERIFY]] / [[MOLLY]] markers
in the draft. Earlier versions of the report template embedded these
markers as section callouts; PR #178 removed them. The current
template does not produce them as boilerplate. If you observe one in
the draft text you have been given, flag it under the relevant
section. If you do not observe one, do not mention them. Do not write
"the draft contains unresolved markers" unless you can quote the
marker text verbatim from the draft you received.

The midpoint rubric has FOUR weighted sections (page budget noted):

  1. Data and Methodology              1 page    (33%)
  2. Preliminary Results and Diagnostics   1 page    (33%)
  3. Roles and Division of Labor       0.5 page  (17%)
  4. Next Steps and Open Questions     0.5 page  (17%)

The verdict opens with a TWO-LINE TOP-LEVEL SUMMARY and is followed by
five rubric sections, in this exact markdown format so the UI can
parse it:

**Academic rigour:** <Strong | Developing | Needs Work>
**Portfolio Manager insight:** <Strong | Developing | Needs Work>

The two top-level lines summarise the deliverable through two lenses:

  ACADEMIC RIGOUR — methodology, citations, data provenance,
  structural completeness against the midpoint rubric. Aggregate from
  the five sections below, weighted by the midpoint percentages
  (33/33/17/17 for sections 1-4; section 5 is synthesis). A
  deliverable with mostly Strong section ratings should read Strong
  here.

  PORTFOLIO MANAGER INSIGHT — does the document tell a PM something
  they did not already know? At the midpoint stage, the question is
  whether the analytical PROGRESS so far reads as substantive, not
  whether the paper is final-submission-ready. Score against these
  five PM criteria (PASS / NEEDS WORK / N/A per criterion) and
  aggregate:
    1. Insight beyond the obvious — a non-obvious finding,
       contradiction, or signal that challenges conventional wisdom.
    2. The 2022 break — mechanism (inflation, Fed policy, duration
       repricing), not just observation. N/A if not covered.
    3. Actionable signal identification — names specific signals and
       why they have predictive power. N/A if methodology-only.
    4. Contradictions acknowledged and pressed — tensions between
       findings explained, not smoothed over.
    5. So what / explicit implication — every major finding followed
       by what a PM should do, conclude, or watch for.
  4-5 PASS → Strong; 2-3 PASS → Developing; 0-1 PASS → Needs Work.

After the two top-level lines, produce these five rubric sections.
The first four map DIRECTLY to the midpoint rubric (so the headings
read as the rubric, not as a generic review). Each section assesses
ONLY what the corresponding rubric section in the draft contains:

### 1. Data and Methodology (1p, 33%)
**Rating:** <Strong | Developing | Needs Work>
<assessment of Section 1 of the midpoint draft: three-asset universe,
286+ monthly observations, ten strategies named, statistical
methodology (FDR correction, deflated Sharpe, CPCV, block bootstrap),
data provenance>

### 2. Preliminary Results and Diagnostics (1p, 33%)
**Rating:** <Strong | Developing | Needs Work>
<assessment of Section 2: the 2022 regime break with actual pre/post
values, FDR result presented honestly, top-line strategy comparisons,
audit verification. This section carries the central thesis>

### 3. Roles and Division of Labor (0.5p, 17%)
**Rating:** <Strong | Developing | Needs Work>
<assessment of Section 3 against the LAYERED OWNERSHIP MODEL
(see TEAM ROLE DIVISION CONTEXT above): does the section state each
member's analytical ownership directly — Michael's validation
infrastructure, Bob's analytical narrative, Molly's human UAT — and
follow with activity-count evidence? Do NOT mark down a layered
description for not being a single human-only narrative; the layered
model IS the course's encouraged structure>

### 4. Next Steps and Open Questions (0.5p, 17%)
**Rating:** <Strong | Developing | Needs Work>
<assessment of Section 4: concrete steps before the July 1 final
submission, and at least one substantive open question for the May 27
midpoint peer-review meetup>

### 5. Overall Academic Readiness
**Rating:** <Strong | Developing | Needs Work>
<This section is YOUR external summary verdict, NOT a section the
team needs to add to the midpoint paper. The midpoint paper has the
four rubric sections above (Data and Methodology, Preliminary Results
and Diagnostics, Roles and Division of Labor, Next Steps and Open
Questions) — nothing else is required. Section 5 here is YOUR overall
assessment of how the draft scores against the rubric AS A WHOLE.

Write one short synthesis paragraph that integrates sections 1-4 and
names whether this draft is on track for the May 27 midpoint deadline,
FOLLOWED by a numbered list titled "Priority actions before May 27 —
ordered by grade impact" of the highest-leverage fixes, ordered by
GRADE IMPACT given the midpoint rubric weights. A gap in section 1 or
2 (33% weight each) is higher impact than a gap in section 3 or 4
(17% weight each), all else equal. Be specific.

Do NOT word this section as if the team needs to write an "Overall
Academic Readiness" section in the paper. They do not. The midpoint
paper has four sections, full stop.>
The Section 5 title MUST be literally "Overall Academic Readiness" —
the UI's truncation detector and fallback both pin on this exact
title (UAT #53/#59 history). Do not rename it.

VISUAL EVIDENCE — chart snapshots may be attached to your prompt:
rolling_correlation, cumulative_returns, regime_signals, factor_loadings,
drawdown_periods, significance_journey, oos_performance. The peer
notes above may reference what they saw on these charts. When you
assess Section 1 (Data and Methodology), cross-check the document's
quantitative claims against the visual evidence. A claim that
disagrees with what is plainly visible on the chart is a serious
methodological concern. When no charts are attached (cold deploy), do
not refer to chart features; reason from the peer notes alone.

THE CENTRAL FINDING — the most important analytical finding in this
project is the 2022 equity-bond correlation regime break. A midpoint
submission that does not quantify it with actual pre/post values
(approximately -0.05 and +0.61) and connect it to strategy performance
differences is materially incomplete. The FDR correction result (zero
strategies significant at q < 0.005) must be present and correctly
interpreted; a submission that omits it or misrepresents it as a
positive finding is a methodological disclosure failure. The
independent three-layer statistical audit (zero critical failures
across 59 checks) is a distinguishing feature of the project and
should be cited as evidence of analytical rigour in Section 1. When
evaluating citations that ARE in the draft, check they are real and
support the specific claim made; fabricated or misattributed citations
are a serious concern. The midpoint check does NOT require a full
references list — only that the citations PRESENT are correct.

Every rating is exactly one of: Strong, Developing, Needs Work. Be
direct and actionable. The team is preparing a graded submission on a
short clock — generic encouragement is not useful. But the converse
also holds: do not invent concerns to look thorough. Read what's in
the draft, evaluate that, stop.

NON-NUMERIC CONSISTENCY CHECK

In addition to the rubric above, flag any of the following that you
detect across the four documents:

- Regime label inconsistency: if the brief calls the current regime
  BULL but the deck or script says TRANSITION or BEAR, flag it as
  HIGH severity.

- Date and period inconsistency: if one document says Q1 2022 and
  another says Q2 2022 for the same event, flag it.

- Citation year inconsistency: if Hamilton (1989) appears as
  Hamilton (1990) in any document, flag it.

- Narrative coherence: if the brief's central argument and the
  deck's SO WHAT titles tell materially different stories, flag it.

- Freehand figures: if you observe a numeric claim in any document
  that contradicts the Key Findings figures provided in the context
  block, flag it as a potential hallucination.

Label all findings from this section as NON_NUMERIC_CONSISTENCY
with severity HIGH or MEDIUM. These flags exist BECAUSE the data
cross-reference pass that runs alongside this review catches
substituted numeric tokens only; the gaps named above are exactly
what the cross-reference cannot see, so this review is the
primary safety net for them."""

# Script-specific rubric — used when Academic Review runs against a
# presentation_script editor draft. The default rubric evaluates a
# written academic submission (citation formatting, paragraph
# structure, footnotes). Applying that against a SPOKEN delivery script
# scores formatting low even when the script itself is coherent — the
# verdict misleads the presenter. The script rubric evaluates the
# things that DO matter for a spoken delivery (argument coherence,
# audience clarity, slide coverage, transitions) and explicitly skips
# the written-submission criteria that don't apply.
#
# Verdict categories are also adjusted:
#   Strong       — ready to deliver
#   Needs Work   — sections unclear or missing key findings
#   Incomplete   — slides missing script content
# (replaces the default Strong / Developing / Needs Work scale; the
# downgrade from Developing → Incomplete signals the specific failure
# mode of a script that has gaps rather than just being weak prose.)
_ARBITER_INSTRUCTIONS_SCRIPT = """=== YOUR TASK — ARBITER VERDICT (PRESENTATION SCRIPT) ===

SCOPE
You are reviewing the Presentation Script only. The context block
contains the full text of this document under PRIMARY DOCUMENT FOR
REVIEW, and abbreviated summaries of the other deliverables under
SUPPORTING CONTEXT. Evaluate ONLY the primary document against the
rubric below. You may reference supporting context to check
consistency but do not grade it.

NUMERIC VALIDATION
The NUMERIC REFERENCE section in the context block contains the
authoritative cache values that were substituted into this document
at generation time. For every numeric claim you encounter in the
primary document:
- If the figure appears in NUMERIC REFERENCE and matches: note it
  as cache-verified.
- If the figure appears in NUMERIC REFERENCE but does not match:
  flag it as HIGH severity numeric inconsistency.
- If the figure does NOT appear in NUMERIC REFERENCE: flag it as a
  freehand figure requiring manual verification.

You are the arbiter for a presentation script — a spoken delivery
document, NOT a written academic submission. Integrate and WEIGH the
peer review notes above. Produce a structured, rubric-mapped verdict
with EXACTLY five sections in this exact markdown format so the UI can
parse it:

### 1. Argument Coherence Across Slides
**Rating:** <Strong | Needs Work | Incomplete>
<Does the argument build cleanly from slide to slide? Are transitions
logical? Does the overall arc lead the audience from the research
question to the conclusion?>

### 2. Clarity for a Mixed Faculty / Investor Audience
**Rating:** <Strong | Needs Work | Incomplete>
<Is technical depth appropriate — neither dumbed down nor opaque?
Are statistical concepts (FDR, Sharpe, regime correlation) introduced
with enough context for a non-specialist? Is jargon defined when first
used?>

### 3. Coverage of Key Findings
**Rating:** <Strong | Needs Work | Incomplete>
<Does the script cover the project's central findings — the 2022
equity-bond correlation regime break (pre/post values), the FDR
result (zero strategies significant at q < 0.005), the
regime-conditional performance pattern, the independent statistical
audit? Each must be present and explained, not just named.>

### 4. Speaker Differentiation and Voice
**Rating:** <Strong | Needs Work | Incomplete>
<Do different speakers carry distinct material? Is the voice
consistent across a single speaker's sections? Are speaker labels
present on every slide? A script with every slide assigned to one
speaker, or speaker boundaries that interrupt a finding mid-flow,
scores lower.>

### 5. Overall Delivery Readiness
**Rating:** <Strong | Needs Work | Incomplete>
<One paragraph. Strong = ready to deliver; Needs Work = sections
unclear or missing key findings; Incomplete = slides missing script
content.>

EVALUATION SCOPE — what this rubric DOES evaluate:
  - Argument coherence and flow across slides
  - Clarity for a mixed faculty / investor audience
  - Coverage of all key findings (the 2022 break, the FDR result,
    the audit, regime-conditional performance)
  - Appropriate technical depth (statistical concepts introduced
    with enough context for a non-specialist; jargon defined)
  - Logical transitions between sections
  - Speaker differentiation (different speakers + consistent voice
    per speaker)

EVALUATION SCOPE — what this rubric DOES NOT evaluate (and you must
NOT comment on these, because they don't apply to a spoken delivery):
  - Citation formatting
  - Academic writing style or paragraph structure
  - Footnotes or APA reference lists
  - Page count or word count formatting

THE CENTRAL FINDING — the 2022 equity-bond correlation regime break
remains the single most important analytical point. A script that
does not name the pre/post correlation values and connect them to
strategy performance differences is Incomplete on Section 3
regardless of how polished the other slides are. The FDR result must
be present and framed correctly — as methodological honesty under a
strict threshold, NOT as a positive significance finding.

UNRESOLVED MARKERS CHECK — if the script still contains [[VERIFY]]
or [[BOB]] / [[MOLLY]] callouts, flag prominently under section 5,
Overall Delivery Readiness: the presenter has unresolved working aids
that must be addressed before delivery.

Every rating is exactly one of: Strong, Needs Work, Incomplete. Be
direct and actionable — the team is preparing a graded delivery, so
generic encouragement is not useful."""

# Brief-specific rubric — used when Academic Review runs against an
# executive_brief editor draft. The default rubric evaluates the
# MIDPOINT paper (4 sections: Data & Methodology / Preliminary Results
# / Roles / Next Steps). Applying that against the executive brief
# scored Section 5 (Final Recommendations) Needs Work mechanically
# because it is deliberately framed as investment conclusions, not
# "Next Steps and Open Questions" (PR #344's INVESTABLE_CONCLUSION_GUARD).
# That mismatch produced a structural 5.5/10 scoring floor on every
# brief review. This rubric replaces the four midpoint sections with
# the SIX brief sections, weighted by the brief's section budget so
# Key Findings (25%) and Methodology (20%) carry the verdict and a
# weak Visuals (5%) does not tank the score.
_ARBITER_INSTRUCTIONS_BRIEF = """=== YOUR TASK — ARBITER VERDICT (EXECUTIVE BRIEF) ===

SCOPE
You are reviewing the Executive Brief only. The context block
contains the full text of this document under PRIMARY DOCUMENT FOR
REVIEW, and abbreviated summaries of the other deliverables under
SUPPORTING CONTEXT. Evaluate ONLY the primary document against the
rubric below. You may reference supporting context to check
consistency but do not grade it.

NUMERIC VALIDATION
The NUMERIC REFERENCE section in the context block contains the
authoritative cache values that were substituted into this document
at generation time. For every numeric claim you encounter in the
primary document:
- If the figure appears in NUMERIC REFERENCE and matches: note it
  as cache-verified.
- If the figure appears in NUMERIC REFERENCE but does not match:
  flag it as HIGH severity numeric inconsistency.
- If the figure does NOT appear in NUMERIC REFERENCE: flag it as a
  freehand figure requiring manual verification.

You are the arbiter for the FNA 670 EXECUTIVE BRIEF submission — a
short investment brief written for a senior investment audience AND
graded by the FNA 670 academic panel. Evaluate against the BRIEF
rubric (six weighted sections), not the midpoint paper rubric.

CRITICAL EVALUATION POSTURE — read what IS in the current draft.
Do not speculate, do not list things you would expect to see, do not
flag absence of features that the team has not committed to for the
brief. The brief is a senior-audience deliverable; it does NOT contain
roles / division of labor (that lived in the midpoint paper) and does
NOT contain "Next Steps and Open Questions" framing (the brief
deliberately closes with investable Final Recommendations).

AUDIENCE — the brief is read by a senior investment audience first
(portfolio managers, allocators) and graded by the FNA 670 academic
panel second. Both lenses matter: investment substance for the PM
read, methodological rigour for the academic panel.

The verdict opens with a TWO-LINE TOP-LEVEL SUMMARY and is followed by
six rubric sections, in this exact markdown format so the UI can
parse it:

**Academic rigour:** <Strong | Developing | Needs Work>
**Portfolio Manager insight:** <Strong | Developing | Needs Work>

The two top-level lines summarise the brief through two lenses:

  ACADEMIC RIGOUR — methodology disclosed correctly, statistical
  results presented honestly, data provenance clear, citations
  accurate. Aggregate from the six sections below, weighted by the
  brief percentages (15/20/25/15/20/5).

  PORTFOLIO MANAGER INSIGHT — does the brief tell a PM something they
  did not already know? Score against these five PM criteria
  (PASS / NEEDS WORK / N/A per criterion) and aggregate:
    1. Insight beyond the obvious — a non-obvious finding,
       contradiction, or signal that challenges conventional wisdom.
    2. The 2022 break — mechanism (inflation, Fed policy, duration
       repricing), not just observation.
    3. Actionable signal identification — names specific signals or
       allocations and why they have predictive power.
    4. Contradictions acknowledged and pressed — tensions between
       findings explained, not smoothed over.
    5. So what / explicit implication — every major finding followed
       by what a PM should do, conclude, or watch for.
  4-5 PASS → Strong; 2-3 PASS → Developing; 0-1 PASS → Needs Work.

After the two top-level lines, produce these six rubric sections.
Each heading maps DIRECTLY to a section in the brief; assess ONLY
what the corresponding section in the draft contains:

### 1. Executive Summary (15%)
**Rating:** <Strong | Developing | Needs Work>
Strong: opens with a clear thesis the PM can act on, names the central
finding (the 2022 regime break) in plain language, and previews the
recommendation. One tight paragraph or short bullet set.
Developing: thesis is present but soft; key finding mentioned without
its mechanism; recommendation hinted at but not stated.
Needs Work: no thesis, generic framing, or recommendation absent.

### 2. Methodology Overview (20%)
**Rating:** <Strong | Developing | Needs Work>
Strong: names the three-asset universe, the monthly observation window,
the statistical methods actually used (FDR correction, deflated Sharpe,
CPCV, block bootstrap) in plain language a PM can follow, and
acknowledges the three-layer independent statistical audit.
Developing: methods named but one or more skipped, or jargon used
without unpacking, or audit not cited.
Needs Work: methodology block missing, methods misnamed, or audit
omitted from a brief that materially depends on it.

### 3. Key Findings and Insights (25%)
**Rating:** <Strong | Developing | Needs Work>
Strong: the 2022 equity-bond correlation regime break is quantified
with pre/post values (approximately -0.05 and +0.61) AND connected to
strategy performance differences; the FDR result (zero strategies
significant at q < 0.005) is presented honestly as methodological
discipline; at least one non-obvious insight surfaces with a "so what"
for the PM.
Developing: 2022 break mentioned without pre/post values, or FDR
result soft-pedalled, or insights stated without PM-actionable
framing.
Needs Work: central finding absent, FDR result misrepresented as a
positive significance finding, or section reads as a numeric dump
without interpretation.

### 4. Limitations and Risks (15%)
**Rating:** <Strong | Developing | Needs Work>
Strong: data window limitations, the FDR-stringency tradeoff,
regime-dependence risk, and the single-asset-class scope are all
named with specific PM-relevant implications (what could break the
conclusion).
Developing: limitations named generically or only one or two of the
above covered.
Needs Work: section absent, or limitations framed as boilerplate
disclaimers without PM-relevant implications.

### 5. Final Recommendations (20%)
**Rating:** <Strong | Developing | Needs Work>
Strong: concrete, investable conclusions framed as allocation
guidance, risk-budget changes, or regime-conditional triggers a PM
can implement. Each recommendation tied back to a finding in
Section 3. NOT framed as "further research would benefit from..." or
"next steps include..." — those phrasings belong in an academic paper,
not an executive brief. This section deliberately closes the brief on
investable conclusions; PR #344's INVESTABLE_CONCLUSION_GUARD enforces
the framing in generation.
Developing: recommendations are present but soft or generic, or one
recommendation reads as a research roadmap rather than an investment
conclusion.
Needs Work: recommendations absent, OR framed entirely as "next
steps" / "further research" language (which is the wrong genre).

### 6. Visuals (5%)
**Rating:** <Strong | Developing | Needs Work>
Strong: the brief embeds or references the supporting charts that
substantiate its claims (rolling correlation, cumulative returns,
significance journey, drawdown periods) and each chart caption ties
the visual to the finding it supports.
Developing: charts present but undercaptioned, or visuals referenced
in text without being embedded.
Needs Work: no visuals at all in a deliverable that depends on
quantitative claims.

PROHIBITED PATTERNS — flag any section that contains these:
  - "Further research would benefit from..."
  - "Next steps include..."
  - Roles and division of labor content (the brief is not the
    midpoint paper — roles content is out of scope here)
  - Evaluator feedback tables or harness artifacts visible in section
    content (the brief is the deliverable, not the build log)
  - OOS Sharpe figures cited without window definition (a PM cannot
    interpret a Sharpe number without the window it was computed over)
  - Two different values for the same metric in different sections
    without explicit window labeling (e.g. one Sharpe in Section 3 and
    a different Sharpe in Section 5 with no note that the windows
    differ — this reads as an internal inconsistency to the PM)
  - Specifically: if any section contains a markdown table with
    headers like "Prior Issue" and "Resolution Applied", or rows
    referencing "PM_CRITERION" labels, score that section Needs Work
    regardless of other content quality. These are internal harness
    artifacts that must not appear in a submitted document.

VISUAL EVIDENCE — chart snapshots may be attached to your prompt:
rolling_correlation, cumulative_returns, regime_signals, factor_loadings,
drawdown_periods, significance_journey, oos_performance. When you
assess Section 3 (Key Findings) and Section 6 (Visuals), cross-check
the brief's quantitative claims against the visual evidence. A claim
that disagrees with what is plainly visible on the chart is a serious
methodological concern. When no charts are attached (cold deploy), do
not refer to chart features; reason from the peer notes alone.

THE CENTRAL FINDING — the most important analytical finding in this
project is the 2022 equity-bond correlation regime break. A brief
that does not quantify it with actual pre/post values (approximately
-0.05 and +0.61) and connect it to a PM-actionable allocation
implication is materially incomplete. The FDR correction result (zero
strategies significant at q < 0.005) must be present and correctly
interpreted as methodological honesty under a strict threshold — a
brief that frames it as a positive significance finding is a
methodological disclosure failure.

Every rating is exactly one of: Strong, Developing, Needs Work. Be
direct and actionable. The team is preparing a graded submission on a
short clock — generic encouragement is not useful. But the converse
also holds: do not invent concerns to look thorough. Read what's in
the draft, evaluate that, stop."""


# PR — academic review deck-specific + appendix-specific rubrics.
# Extends PR #351 (brief rubric) to the deck and appendix. Both
# document types previously routed through the midpoint rubric and
# produced the same structural 5.5/10 floor — the midpoint rubric's
# four sections (Data + Methodology / Preliminary Results / Roles /
# Next Steps) have zero direct mapping to a finished presentation
# deck or an analytical appendix.
#
# Deck rubric — six weighted sections matching the deck structure:
#   Opening + Central Argument 15%, Analytical Evidence 25%,
#   Economic Storytelling 20%, Live Demo + AI Methodology 20%,
#   Investment Recommendation 15%, Presentation Quality 5%.
#
# Appendix rubric — five weighted sections matching the appendix
# structure (workbook-style; no audience-facing "presentation
# quality" surface):
#   Data Sources + Methodology 20%, Portfolio Construction 20%,
#   Calculations + Models 25%, Performance Metrics + Visualizations
#   20%, Sensitivity + Robustness 15%.
_ARBITER_INSTRUCTIONS_DECK = """=== YOUR TASK — ARBITER VERDICT (PRESENTATION DECK) ===

SCOPE
You are reviewing the Final Presentation Deck only. The context
block contains the full text of this document under PRIMARY
DOCUMENT FOR REVIEW, and abbreviated summaries of the other
deliverables under SUPPORTING CONTEXT. Evaluate ONLY the primary
document against the rubric below. You may reference supporting
context to check consistency but do not grade it.

NUMERIC VALIDATION
The NUMERIC REFERENCE section in the context block contains the
authoritative cache values that were substituted into this document
at generation time. For every numeric claim you encounter in the
primary document:
- If the figure appears in NUMERIC REFERENCE and matches: note it
  as cache-verified.
- If the figure appears in NUMERIC REFERENCE but does not match:
  flag it as HIGH severity numeric inconsistency.
- If the figure does NOT appear in NUMERIC REFERENCE: flag it as a
  freehand figure requiring manual verification.

You are the arbiter for the FNA 670 FINAL PRESENTATION DECK — an
18-20 minute, 11-slide deck delivered to a mixed audience of senior
investment professionals (Forest Capital partners) AND the FNA 670
academic panel (Dr. Katerina Panttser). Evaluate against the DECK
rubric (six weighted sections), not the midpoint paper rubric.

CRITICAL EVALUATION POSTURE — read what IS in the current draft.
Do not speculate, do not list things you would expect to see, do not
flag absence of features that the team has not committed to for the
deck. The deck does NOT contain roles / division of labor and does
NOT close with "Next Steps and Open Questions" framing — the deck
closes with an investment recommendation.

AUDIENCE — Forest Capital partners read the deck for an investable
conclusion; the academic panel reads it for methodological rigour.
Both lenses matter.

The verdict opens with a TWO-LINE TOP-LEVEL SUMMARY and is followed
by six rubric sections, in this exact markdown format so the UI can
parse it:

**Academic rigour:** <Strong | Developing | Needs Work>
**Portfolio Manager insight:** <Strong | Developing | Needs Work>

  ACADEMIC RIGOUR — quantitative analysis is honest, statistical
  caveats are disclosed, OOS results carry window definitions,
  academic grounding is verbalised on the methodology slide.
  Aggregate from the six sections below weighted by the deck
  percentages (15/25/20/20/15/5).

  PORTFOLIO MANAGER INSIGHT — would a Forest Capital executive
  leave the room knowing what to do with the information? Score
  against four PM criteria (PASS / NEEDS WORK / N/A per criterion)
  and aggregate:
    1. Central argument front-loaded — the PM understands the
       investment conclusion before slide 5, not after slide 9.
    2. Mechanism explained — the 2022 correlation break is named
       as the cause of static-blend underperformance, not just
       observed.
    3. Honest about the misses — play-by-play 2-of-9 result,
       Liberation Day underperformance, post-2022 static-blend
       weakness all surfaced honestly.
    4. Closing recommendation is investable — names allocation
       guidance, conditions to revisit, regime triggers to watch.
  3-4 PASS → Strong; 1-2 PASS → Developing; 0 PASS → Needs Work.

After the two top-level lines, produce these six rubric sections.
Each heading maps to a phase of the deck:

### 1. Opening and Central Argument (15%)
**Rating:** <Strong | Developing | Needs Work>
Strong: slide 1 states the central investment question and answers
it immediately with the headline quantitative result (OOS Sharpe
1.24 vs 0.73 benchmark). The audience understands within 60 seconds
why diversification beats 100% equity and what the evidence shows.
The three-strategy frame (Benchmark vs Static vs Dynamic) is
established in the opening slides.
Developing: central argument present but not front-loaded — the
audience must wait until slide 3+ to understand the core finding.
Needs Work: no clear central argument in opening slides; the deck
opens with methodology before stating what it found.

### 2. Analytical Evidence (25%)
**Rating:** <Strong | Developing | Needs Work>
Strong: performance metrics (OOS Sharpe, max drawdown, recovery
months) cited with explicit window definitions. The 2022
correlation break is identified as the mechanism. Pre/post-2022
sub-period results presented honestly INCLUDING post-2022
underperformance. Play-by-play scorecard presented with the honest
2-of-9 result. All figures internally consistent across slides.
Developing: key metrics present but missing window definitions or
sub-period breakdown; post-2022 underperformance not addressed;
minor figure inconsistencies across slides.
Needs Work: raw performance numbers without context or window
definitions; no sub-period analysis; figures inconsistent across
slides.

### 3. Economic Storytelling (20%)
**Rating:** <Strong | Developing | Needs Work>
Strong: explains WHY regime detection improves outcomes (HMM
identifies persistent structural states, not reactive momentum
signals). Names Hamilton (1989) and Ang and Bekaert (2002)
verbally on the methodology slide. The 2022 correlation inversion
is explained mechanically (Fed tightening drove simultaneous
equity and bond losses). The current macro environment (CPI level,
dot plot) is contextualised against historical regimes.
Developing: economic intuition present but superficial; regime
switching described without explaining the mechanism; academic
grounding absent from verbal delivery.
Needs Work: results presented without economic explanation; no
mechanism for why the strategy works; no academic grounding.

### 4. Live Demo and AI Methodology (20%)
**Rating:** <Strong | Developing | Needs Work>
Strong: slide 9 demo follows a structured sequence (Investment
Outlook live signal, Council deliberation with dissenting view,
Reports page document generation, URL hand-off). Slide 10 honestly
addresses what worked AND what did not (multi-model validation
worked; LLM arithmetic limitation acknowledged; council as
analytical engine, not product pitch). Demo and AI methodology
together establish platform credibility without promotional
language.
Developing: demo present but unstructured or promotional; AI
methodology slide present but lacks honest reflection on
limitations.
Needs Work: no structured demo sequence; AI methodology slide
reads as a product pitch rather than honest academic reflection.

### 5. Investment Recommendation (15%)
**Rating:** <Strong | Developing | Needs Work>
Strong: concluding slides state an unambiguous investment
recommendation. Three supporting conclusions grounded in
quantitative evidence. The condition under which the
recommendation would be revisited is stated. Reads as a CIO
conclusion, not an academic summary.
Developing: recommendation present but hedged; supporting
conclusions lack specific figures; closing slides read as
academic summary rather than investment conclusion.
Needs Work: no clear recommendation; deck ends with "further
research" framing or lists next steps rather than investment
conclusions.

### 6. Presentation Quality (5%)
**Rating:** <Strong | Developing | Needs Work>
Strong: 18-20 minute timing discipline evident in speaker notes
depth. Slides are visual with minimal text. Charts reference figure
numbers. Transitions connect slides logically.
Developing: timing likely off (speaker notes too thin or too
dense); some slides text-heavy; charts present but not referenced.
Needs Work: slides appear to be read verbatim; no visual
discipline; charts absent or unreferenced.

PROHIBITED PATTERNS — flag any section that contains these:
  - "Further research would benefit from..."
  - "Next steps include..."
  - Roles and division of labor content (out of scope for the deck)
  - Evaluator feedback tables or harness artifacts visible in
    slide content
  - OOS Sharpe figures cited without window definition
  - Two different values for the same metric across slides
    without window labels
  - Promotional AI language ("cutting-edge", "revolutionary",
    "game-changing") — academic deck, not vendor pitch
  - Specifically: if any slide contains a markdown table with
    headers like "Prior Issue" and "Resolution Applied", or rows
    referencing "PM_CRITERION" labels, score that section Needs
    Work regardless of other content quality. These are internal
    harness artifacts that must not appear in a submitted document.

VISUAL EVIDENCE — chart snapshots may be attached to your prompt.
When you assess Section 2 (Analytical Evidence) and Section 6
(Presentation Quality), cross-check the deck's quantitative claims
against the visual evidence. A claim that disagrees with what is
plainly visible on the chart is a serious methodological concern.
When no charts are attached (cold deploy), do not refer to chart
features; reason from the peer notes alone.

Every rating is exactly one of: Strong, Developing, Needs Work. Be
direct and actionable. Read what's in the draft, evaluate that,
stop."""


_ARBITER_INSTRUCTIONS_APPENDIX = """=== YOUR TASK — ARBITER VERDICT (ANALYTICAL APPENDIX) ===

SCOPE
You are reviewing the Analytical Appendix only. The context block
contains the full text of this document under PRIMARY DOCUMENT FOR
REVIEW, and abbreviated summaries of the other deliverables under
SUPPORTING CONTEXT. Evaluate ONLY the primary document against the
rubric below. You may reference supporting context to check
consistency but do not grade it.

NUMERIC VALIDATION
The NUMERIC REFERENCE section in the context block contains the
authoritative cache values that were substituted into this document
at generation time. For every numeric claim you encounter in the
primary document:
- If the figure appears in NUMERIC REFERENCE and matches: note it
  as cache-verified.
- If the figure appears in NUMERIC REFERENCE but does not match:
  flag it as HIGH severity numeric inconsistency.
- If the figure does NOT appear in NUMERIC REFERENCE: flag it as a
  freehand figure requiring manual verification.


You are the arbiter for the FNA 670 ANALYTICAL APPENDIX — a
workbook-style deliverable (35% of project grade) that documents
every assumption, calculation, and visualisation behind the
executive brief and presentation deck. The audience is the FNA 670
academic panel (primary) and any portfolio manager who needs to
independently verify the brief's claims (secondary). Evaluate
against the APPENDIX rubric (five weighted sections), not the
midpoint paper rubric.

CRITICAL EVALUATION POSTURE — read what IS in the current draft.
Do not speculate, do not list things you would expect to see, do
not flag absence of features that the team has not committed to
for the appendix. The appendix does NOT contain roles / division
of labor and does NOT close with "Next Steps and Open Questions"
framing. The appendix is a reference document, not a narrative.

AUDIENCE — the academic panel reads the appendix to verify
methodology; a portfolio manager reads it to check the executive
brief's claims independently. Both lenses matter.

The verdict opens with a TWO-LINE TOP-LEVEL SUMMARY and is followed
by five rubric sections, in this exact markdown format so the UI
can parse it:

**Academic rigour:** <Strong | Developing | Needs Work>
**Portfolio Manager insight:** <Strong | Developing | Needs Work>

  ACADEMIC RIGOUR — are all calculations transparent, reproducible,
  and grounded in documented methodology? Aggregate from the five
  sections below weighted by the appendix percentages (20/20/25/20/15).

  PORTFOLIO MANAGER INSIGHT — could a portfolio manager use this
  appendix to independently verify every claim in the executive
  brief? PASS / NEEDS WORK across these criteria:
    1. Data sources and proxies fully named.
    2. Strategy construction rules complete enough to replicate.
    3. Calculations traceable to the underlying return series.
    4. Sensitivity analysis disclosed (transaction costs, sample
       windows, bootstrap intervals).
  3-4 PASS → Strong; 1-2 PASS → Developing; 0 PASS → Needs Work.

After the two top-level lines, produce these five rubric sections.
Each heading maps to a section of the appendix:

### 1. Data Sources and Methodology (20%)
**Rating:** <Strong | Developing | Needs Work>
Strong: data sources explicitly named (S&P 500, IG bonds via
AGG/BND proxy, HY bonds via HYG/JNK proxy). Study period stated
with justification (287 months, July 2002 through May 2026). All
assumptions documented (long-only, fully invested, no cash, no
short positions). Initialization periods for each strategy class
stated with precise start dates.
Developing: data sources named but proxies not specified; study
period stated without justification; some assumptions missing.
Needs Work: data sources vague or missing; assumptions
undocumented; no initialization period disclosure.

### 2. Portfolio Construction Methodology (20%)
**Rating:** <Strong | Developing | Needs Work>
Strong: all 10 strategy construction rules documented
transparently. Static blend weights justified by Markowitz (1952)
mean-variance theory. Dynamic blend allocation grids (BULL/BEAR/
TRANSITION equity/IG/HY weights) stated explicitly. HMM three-
state structure and transition matrix documented. Rebalancing
rule (monthly evaluation, 2pp gate) disclosed with deviation from
quarterly cadence justified.
Developing: strategy rules present but incomplete; missing
theoretical justification for static blend or HMM parameter
choices.
Needs Work: strategy rules vague; no theoretical justification;
rebalancing rule absent.

### 3. All Calculations and Models (25%)
**Rating:** <Strong | Developing | Needs Work>
Strong: full 10-strategy performance table with CAGR, volatility,
Sharpe, max drawdown, recovery months, pre/post-2022 sub-period
Sharpe. Factor loading table (Fama-French 3-factor + Carhart
momentum). Benjamini-Hochberg FDR correction results across all
10 strategies. OOS cost sensitivity surface (10/15/20bp).
Bootstrap confidence intervals on post-2022 sub-period Sharpe.
Data hash cited for reproducibility.
Developing: most calculations present but missing one or two
required tables; FDR correction present but not explained; factor
attribution incomplete.
Needs Work: calculations incomplete; key metrics missing; no FDR
correction; no factor attribution.

### 4. Performance Metrics and Visualizations (20%)
**Rating:** <Strong | Developing | Needs Work>
Strong: four or more charts present with APA figure numbers and
Note captions. Each chart tied to a specific finding. Cumulative
return, rolling correlation, efficient frontier, and OOS Sharpe
comparison all present. Chart data traceable to the 287-month
return series.
Developing: charts present but missing APA formatting or figure
numbers; fewer than four charts; charts not tied to specific
findings.
Needs Work: charts absent or unformatted; no figure numbers or
captions.

### 5. Sensitivity and Robustness Analysis (15%)
**Rating:** <Strong | Developing | Needs Work>
Strong: walk-forward sensitivity analysis showing Sharpe
stability across sample window sizes. Transaction cost sensitivity
(net Sharpe at 10/15/20bp). Bootstrap confidence intervals
confirming directional results hold despite wide bands. Crisis
period performance (2008, 2020, 2022) documented separately.
Developing: some sensitivity analysis present but incomplete;
missing crisis period breakdown or bootstrap intervals.
Needs Work: no sensitivity analysis; results presented as point
estimates only.

PROHIBITED PATTERNS — flag any section that contains these:
  - Figures not traceable to the data hash
  - Calculations without documented assumptions
  - Performance claims without the corresponding methodology
    disclosure
  - Charts without figure numbers or APA notes
  - Sharpe ratios cited without study period definition
  - Specifically: if any section contains a markdown table with
    headers like "Prior Issue" and "Resolution Applied", or rows
    referencing "PM_CRITERION" labels, score that section Needs
    Work regardless of other content quality. These are internal
    harness artifacts that must not appear in a submitted document.

Every rating is exactly one of: Strong, Developing, Needs Work. Be
direct and actionable. Read what's in the draft, evaluate that,
stop."""


# Section 6 — appended only when more than one team member has recorded
# activity. With a single active user, assessing division of labour would
# penalise an adoption gap rather than a real task-sharing problem.
_ARBITER_SECTION_6 = """

### 6. Team Engagement and Division of Labour
**Rating:** <Strong | Developing | Needs Work>
<Assess task sharing from the team activity summary in the context block.
Reference specific engagement patterns — who is active, who is not,
whether the distribution of interactions supports the division-of-labour
claims the midpoint paper makes.>"""


def build_arbiter_user_message(
    context_block: str, peer_responses: dict[str, str],
    multi_user: bool = False, script_review: bool = False,
    brief_review: bool = False, deck_review: bool = False,
    appendix_review: bool = False,
) -> str:
    """Builds the arbiter's user message: the context block, every peer's
    review notes, and the verdict instructions.

    script_review — when True (a presentation_script editor draft is the
    target), the script-specific rubric is used instead of the default
    written-submission rubric. The script rubric evaluates spoken-
    delivery criteria (coherence, audience clarity, slide coverage,
    speaker differentiation) and explicitly skips citation formatting /
    paragraph structure / footnotes. Section 6 (division of labour) is
    NOT applied in script mode — a script's verdict stays focused on
    delivery readiness, not team engagement.

    brief_review — when True (an executive_brief editor draft is the
    target), the brief-specific rubric is used. The brief rubric
    evaluates the SIX brief sections (Executive Summary / Methodology /
    Key Findings / Limitations / Final Recommendations / Visuals) with
    section weights (15/20/25/15/20/5), and explicitly skips the
    "Next Steps" framing that scored Final Recommendations Needs Work
    mechanically under the midpoint rubric. Section 6 (division of
    labour) is NOT applied in brief mode — the brief is a senior-
    audience deliverable, not a team-effort progress report.

    script_review takes precedence over brief_review when both flag
    True (a script of a brief is unusual; routing the script rubric
    is the more defensible default).

    multi_user — only consulted in the default (midpoint) review mode.
    """
    parts = [context_block, "", "=== PEER REVIEW NOTES ==="]
    for agent_id, text in peer_responses.items():
        name = _PEER_AGENTS.get(agent_id, {}).get("name", agent_id)
        parts.append(f"\n--- {name} ---\n{text}")
    parts.append("")
    if script_review:
        # Script rubric is self-contained — no section-6 append.
        instructions = _ARBITER_INSTRUCTIONS_SCRIPT
    elif brief_review:
        # Brief rubric is self-contained — no section-6 append.
        instructions = _ARBITER_INSTRUCTIONS_BRIEF
    elif deck_review:
        # Deck rubric is self-contained — no section-6 append.
        instructions = _ARBITER_INSTRUCTIONS_DECK
    elif appendix_review:
        # Appendix rubric is self-contained — no section-6 append.
        instructions = _ARBITER_INSTRUCTIONS_APPENDIX
    else:
        instructions = _ARBITER_INSTRUCTIONS
        if multi_user:
            instructions += _ARBITER_SECTION_6
    parts.append(instructions)
    return "\n".join(parts)


def _mock_arbiter_text() -> str:
    """Deterministic five-section verdict for the test environment."""
    return (
        "### 1. Data Sufficiency and Methodology\n"
        "**Rating:** Developing\n"
        "Mock verdict — the live arbiter model is unavailable in this "
        "environment.\n\n"
        "### 2. Requirements and Rubric Alignment\n**Rating:** Needs Work\n"
        "Upload the requirements documents to enable a real assessment.\n\n"
        "### 3. Deliverable Quality\n**Rating:** Developing\n"
        "Upload draft materials to enable assessment.\n\n"
        "### 4. Priority Areas for Further Investigation\n**Rating:** Developing\n"
        "1. Upload the midpoint rubric. 2. Upload draft deliverables.\n\n"
        "### 5. Overall Academic Readiness\n**Rating:** Developing\n"
        "This is a deterministic mock verdict for the test environment."
    )


def chunk_arbiter_text(text: str) -> list[str]:
    """Splits the completed verdict into word-group chunks so the SSE
    consumer still sees the verdict arrive progressively."""
    words = text.split(" ")
    return [" ".join(words[i:i + 12]) + " " for i in range(0, len(words), 12)]


def _verdict_has_section_5(text: str, script_review: bool) -> bool:
    """
    True when the verdict carries the Section 5 heading the active
    rubric requires. Both rubrics end with a Section 5 — the written
    rubric calls it "Overall Academic Readiness" and the script
    rubric "Overall Delivery Readiness".

    UAT 2026-05-24 (#53/#59) — the prior check was
    `expected_title.lower() in text.lower()` which searched the WHOLE
    verdict text for the title. The arbiter sometimes wrote section 5
    with an off-rubric heading AND mentioned the rubric title in the
    body of another section — the loose check passed, the fallback
    didn't fire, and the user saw a section 5 with the wrong title or
    no rating badge.

    PR-LLM-2 (May 28 2026) — the strict-title-only detector was
    firing the fallback on EVERY review run because the arbiter
    consistently writes a close-but-not-exact title (e.g. "Overall
    Readiness", "Overall Project Readiness"). Loosened to accept
    EITHER the exact rubric title OR any `### 5. Overall <words>`
    pattern — the latter covers every observed variant so the
    fallback only fires when section 5 is genuinely absent.
    """
    import re
    expected_title = ("Overall Delivery Readiness" if script_review
                      else "Overall Academic Readiness")
    # Pass 1: rubric-exact title on the heading line.
    exact = re.compile(
        r"^\s*###\s*5\.\s*" + re.escape(expected_title),
        re.IGNORECASE | re.MULTILINE,
    )
    if exact.search(text):
        return True
    # Pass 2 (PR-LLM-2): `### 5. Overall <anything>` — covers every
    # observed model variant ("Overall Readiness", "Overall Project
    # Readiness", "Overall Verdict", "Overall Readiness Assessment"
    # etc.). The body content is usable as-is — only the off-rubric
    # title fired the prior fallback every run. The wrong-title
    # rename branch in _assemble_section_5_fallback still REPLACES
    # the title in place if a caller hits that path, so the
    # rendered output remains rubric-correct downstream.
    overall_variant = re.compile(
        r"^\s*###\s*5\.\s*Overall\b",
        re.IGNORECASE | re.MULTILINE,
    )
    return bool(overall_variant.search(text))


def _assemble_section_5_fallback(
    text: str, peer_responses: dict[str, str], script_review: bool,
) -> str:
    """
    Defence-in-depth: when the harness returned a verdict without
    Section 5, append (or repair) a substantive fallback so the
    rendered output always carries the correctly-titled section.
    UAT #128/#125 root cause was Section 5 truncation; UAT #53/#59
    root cause was Section 5 with a wrong title slipping past the
    detector.

    The fallback rating is derived from the present sections:
      - Any "Needs Work" → "Needs Work"
      - Otherwise any "Developing" → "Developing"  (or "Needs Work"
        for the script-review rating scale which uses Incomplete)
      - Otherwise "Strong"

    UAT 2026-05-24 (#53/#59) — when the model wrote a Section 5
    with an off-rubric title (e.g. `### 5. Overall Readiness
    Assessment` instead of `### 5. Overall Academic Readiness`),
    the prior fallback APPENDED a fresh section 5 alongside the
    model's wrong-titled one — the user saw two Section 5s. Now we
    REPLACE the wrong-titled heading in place when one exists,
    preserving the model's body content underneath it; only when
    there's no `### 5.` heading at all do we append a freshly
    assembled section.
    """
    import re

    title = ("Overall Delivery Readiness" if script_review
             else "Overall Academic Readiness")

    # ── Case 1: a `### 5.` heading already exists with the WRONG
    # title. Rewrite the heading line to the rubric-correct title
    # and preserve the body the model already wrote. The detector
    # (`_verdict_has_section_5`) requires the title to match
    # exactly on the heading line, so this branch only fires when
    # the heading is present but mistitled.
    wrong_heading = re.compile(
        r"^(\s*###\s*5\.\s*)(.+)$",
        re.MULTILINE,
    )
    if wrong_heading.search(text):
        return wrong_heading.sub(
            lambda m: f"{m.group(1)}{title}",
            text,
            count=1,
        )

    # ── Case 2: no `### 5.` heading at all — assemble + append a
    # full fallback so the rendered output always carries five
    # sections. Rating derived from the four present sections.
    ratings = re.findall(
        r"\*\*Rating:\*\*\s*(Strong|Developing|Needs Work|Incomplete)",
        text, re.IGNORECASE)
    norm = [r.strip().title() for r in ratings]
    if script_review:
        # Script rubric uses Strong / Needs Work / Incomplete.
        if any(r == "Incomplete" for r in norm):
            rating = "Incomplete"
        elif any(r == "Needs Work" for r in norm):
            rating = "Needs Work"
        else:
            rating = "Strong"
    else:
        if any(r == "Needs Work" for r in norm):
            rating = "Needs Work"
        elif any(r == "Developing" for r in norm):
            rating = "Developing"
        else:
            rating = "Strong"

    n_peers = len(peer_responses)
    return (
        f"{text.rstrip()}\n\n"
        f"### 5. {title}\n"
        f"**Rating:** {rating}\n"
        f"This overall rating is aggregated from the four section "
        f"verdicts above (a generated paragraph was not returned and "
        f"this fallback was assembled). The submission was reviewed "
        f"by {n_peers} peer agents whose detailed notes are available "
        f"under the Peer Responses accordion below; the section "
        f"ratings reflect the consensus of those reviews. Address the "
        f"Priority Areas in section 4 in order of impact, and revisit "
        f"every section marked below Strong before the next "
        f"submission.\n"
    )


def run_arbiter_with_harness(
    context_block: str,
    peer_responses: dict[str, str],
    multi_user: bool = False,
    script_review: bool = False,
    n_strategies: int | None = None,
    brief_review: bool = False,
    deck_review: bool = False,
    appendix_review: bool = False,
) -> str:
    """
    Generates the arbiter verdict IN FULL and runs it through the
    generator-evaluator harness — a verdict scoring below threshold is
    regenerated with the evaluator's feedback. Returns the best-scoring
    verdict text.

    script_review — when True, the script-specific rubric is used (see
    build_arbiter_user_message). The verdict's rating scale switches
    from Strong/Developing/Needs Work to Strong/Needs Work/Incomplete
    and the evaluation categories move from written-submission criteria
    to spoken-delivery criteria.

    brief_review — when True, the brief-specific rubric is used. The
    verdict produces SIX rubric sections (Executive Summary /
    Methodology / Key Findings / Limitations / Final Recommendations /
    Visuals) weighted 15/20/25/15/20/5. The Section-5 detector +
    fallback (built for the midpoint rubric's "Overall Academic
    Readiness" section) is skipped in brief mode — Section 5 of the
    brief rubric is "Final Recommendations" and the brief carries six
    sections, so the midpoint-shaped detector would fire on every run.

    The verdict is generated in full (non-streaming) before the endpoint
    streams it, so a failed attempt is never shown to the client — only
    the accepted verdict is streamed. Synchronous (the harness is sync);
    the endpoint runs this in asyncio.to_thread so the event loop stays
    free. Fail-open: an arbiter generation failure returns the
    deterministic mock verdict rather than raising.
    """
    user_message = build_arbiter_user_message(
        context_block, peer_responses, multi_user, script_review,
        brief_review=brief_review,
        deck_review=deck_review,
        appendix_review=appendix_review)
    if _is_test_env() or not os.getenv("ANTHROPIC_API_KEY"):
        return _mock_arbiter_text()

    from agents.academic_advisor import _SYSTEM_PROMPT as advisor_prompt
    from agents.harness import GeneratorEvaluatorHarness
    from agents.evaluator_prompts import academic_review_arbiter_evaluator_prompt

    # ACADEMIC_REVIEW_CHARTS snapshots for the arbiter's synthesis.
    # Captured in the generator closure so a harness retry reuses the
    # same visual context. Evaluators MUST NOT see this — the harness's
    # _evaluate omits the kwarg.
    visual_context = _academic_review_visual_context(n_strategies)

    def _generate(prompt: str) -> str:
        return call_claude(ARBITER_MODEL, advisor_prompt, prompt,
                           max_tokens=ARBITER_MAX_TOKENS,
                           visual_context=visual_context,
                           trigger="academic_review_arbiter")

    try:
        harness = GeneratorEvaluatorHarness()
        result = harness.run(
            generator_fn=_generate,
            evaluator_prompt=academic_review_arbiter_evaluator_prompt(),
            generator_prompt=user_message,
            context=context_block[:6000],
            agent_id="academic_advisor",
        )
        # Defence-in-depth: even after the tightened evaluator + the
        # 4000-token budget, if Section 5 is somehow still missing,
        # append a fallback assembled from the four present section
        # ratings. The user sees five sections every time; UAT
        # #128/#125 cannot reappear.
        #
        # Skipped in brief_review mode — the brief rubric has SIX
        # sections (Section 5 is "Final Recommendations", not
        # "Overall Academic Readiness") and the midpoint-shaped
        # detector + fallback would fire on every brief run.
        # Same logic applies to deck_review (six sections, Section
        # 5 is "Investment Recommendation") and appendix_review
        # (FIVE sections, Section 5 is "Sensitivity and Robustness
        # Analysis"). The midpoint-shaped detector would mis-fire
        # in every one of those modes.
        if brief_review or deck_review or appendix_review:
            return result.response
        if not _verdict_has_section_5(result.response, script_review):
            # PR-LLM-2 (May 28 2026) — diagnostic logging. The previous
            # log line carried only the length and attempts; future
            # parser fixes need to see what the model actually wrote.
            # Capture the trailing 1500 chars (where Section 5 should
            # appear) plus every `### N.` heading the model emitted
            # so we can compare expected vs actual structure at a
            # glance in the Render logs.
            import re

            headings_found = re.findall(
                r"^\s*###\s*\d+\.\s*[^\n]+$",
                result.response, re.MULTILINE)
            log.warning(
                "academic_review_section_5_fallback_applied",
                arbiter_chars=len(result.response),
                attempts=result.attempts,
                trailing_1500_chars=result.response[-1500:],
                headings_found=headings_found,
                script_review=script_review,
            )
            return _assemble_section_5_fallback(
                result.response, peer_responses, script_review)
        return result.response
    except Exception as exc:  # noqa: BLE001
        log.error("academic_review_arbiter_failed", error=str(exc))
        return _mock_arbiter_text()
