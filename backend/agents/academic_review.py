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


# May 28 2026 — team role division context. Prepended to the team-
# engagement block in the arbiter (and peer) context so the verdict
# evaluates each member's contributions against role-appropriate
# expectations rather than raw commit / interaction counts. Michael's
# front-loaded platform engineering should NOT be read as analytical
# disengagement; Bob's analytical writing and Molly's presentation
# work are the genuine analytical surfaces to assess.
_TEAM_ROLE_CONTEXT_LINES: list[str] = [
    "",
    "TEAM ROLE DIVISION CONTEXT",
    "Michael Ruurds is the platform engineer — commit count and QA "
    "activity reflect front-loaded engineering work, not analytical "
    "disengagement. Bob Thao owns analytical interpretation, written "
    "deliverables, and Council engagement. Molly Murdock owns "
    "presentation, visualisation, and rehearsal. Engagement metrics "
    "should be evaluated against role-appropriate contributions, not "
    "raw commit counts.",
    "",
    "When assessing team engagement, flag genuine analytical gaps "
    "(e.g. unresolved [[BOB]] markers, missing interpretation "
    "sections) rather than engineering commit disparity.",
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
) -> str:
    """
    Renders the analytics inventory, the grouped documents and the
    team-activity summary into one structured text block injected into
    every agent prompt. Missing document types render as "(not yet
    uploaded)" — never an error.
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

    # — Documents —
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
                text = text[:_DOC_CHAR_CAP] + "\n…[document truncated for review]"
            lines.append(f"\n[{label}: {d.get('name', 'document')}]\n{text}")

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
    "midpoint_paper": "midpoint_draft",
    "presentation_deck": "presentation_slides",
    "executive_brief": "other",
}


async def gather_review_context(
    reviewer_email: str | None = None,
) -> dict[str, Any]:
    """
    Assembles the full review context: the analytics snapshot, the
    documents grouped by type, and the formatted context block that gets
    injected into every agent prompt.

    reviewer_email — when given, the reviewer's current editor drafts
    (tools/editor_drafts) take precedence over an uploaded academic
    document of the same kind, so Academic Review evaluates the live
    working draft rather than a stale uploaded file.
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
    block = build_review_context_block(
        analytics, docs_by_type, team_activity, team_members)
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
    "Review this project's academic readiness. Address all areas "
    "concisely, from your expert perspective:\n\n"
    "1. DATA SUFFICIENCY — breadth, depth, time range, factor-model "
    "coverage; name specific gaps.\n"
    "2. REQUIREMENTS & RUBRIC ALIGNMENT — does the current work satisfy the "
    "stated criteria; what is unmet.\n"
    "3. DELIVERABLE QUALITY — assess any draft materials present; what would "
    "strengthen them.\n"
    "4. AREAS FOR FURTHER INVESTIGATION — the highest-leverage actions before "
    "the deadline; specific, not generic.\n"
)

# Fifth dimension — appended only when more than one team member has
# recorded activity. With a single active user the platform may simply
# not be in use by the whole team yet; assessing task-sharing then would
# penalise an adoption gap, not a division-of-labour problem.
_PEER_DIMENSION_5 = (
    "5. TEAM ENGAGEMENT AND TASK SHARING — Based on the team activity "
    "summary provided, assess whether the team's engagement with the "
    "platform reflects genuine shared effort. Is analytical work "
    "distributed across team members or concentrated? Does the pattern of "
    "interactions suggest coordinated division of labour?\n"
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

_ARBITER_INSTRUCTIONS = """=== YOUR TASK — ARBITER VERDICT ===
You are the arbiter. Integrate and WEIGH the peer review notes above — do
not restate them. Produce a structured, rubric-mapped verdict that opens
with a TWO-LINE TOP-LEVEL SUMMARY and is followed by five rubric sections,
in this exact markdown format so the UI can parse it:

**Academic rigour:** <Strong | Developing | Needs Work>
**Portfolio Manager insight:** <Strong | Developing | Needs Work>

The two top-level lines summarise the deliverable through two distinct
lenses:

  ACADEMIC RIGOUR — methodology, citations, data provenance, structural
  completeness against the FNA 670 rubric. Aggregate this from the five
  rubric sections below; a deliverable with mostly Strong section
  ratings should read Strong here.

  PORTFOLIO MANAGER INSIGHT — does the document tell a PM something
  they did not already know? Score against these five PM criteria
  (PASS / NEEDS WORK / N/A per criterion) and aggregate:
    1. Insight beyond the obvious — non-obvious finding, contradiction,
       or signal that challenges conventional wisdom.
    2. The 2022 break — mechanism (inflation, Fed policy, duration
       repricing), not just observation. N/A if not covered.
    3. Actionable signal identification — names specific signals and
       why they have predictive power in the current regime. N/A if
       methodology-only.
    4. Contradictions acknowledged and pressed — tensions between
       findings explained, not smoothed over.
    5. So what / explicit implication — every major finding followed
       by what a PM should do, conclude, or watch for.
  4-5 PASS → Strong; 2-3 PASS → Developing; 0-1 PASS → Needs Work.

After the two top-level lines, produce the five rubric sections:

### 1. Data Sufficiency and Methodology
**Rating:** <Strong | Developing | Needs Work>
<your assessment>

### 2. Requirements and Rubric Alignment
**Rating:** <Strong | Developing | Needs Work>
<your assessment>

### 3. Deliverable Quality
**Rating:** <Strong | Developing | Needs Work>
<your assessment>

### 4. Priority Areas for Further Investigation
**Rating:** <Strong | Developing | Needs Work>
<a numbered list, ordered by impact — specific actions, not generic advice>

### 5. Overall Academic Readiness
**Rating:** <Strong | Developing | Needs Work>
<one paragraph>

VISUAL EVIDENCE — chart snapshots may be attached to your prompt:
rolling_correlation, cumulative_returns, regime_signals, factor_loadings,
drawdown_periods, significance_journey, oos_performance. The peer notes
above may reference what they saw on these charts. When you assess the
document under Data Sufficiency and Methodology, cross-check the
document's quantitative claims against the visual evidence — a claim
that disagrees with what is plainly visible on the chart is a serious
methodological concern. When no charts are attached (cold deploy), do
not refer to chart features; reason from the peer notes alone.

THE CENTRAL FINDING — the most important analytical finding in this
project is the 2022 equity-bond correlation regime break. A submission
that does not quantify it with actual pre/post values (approximately
-0.05 and +0.61) and connect it to strategy performance differences is
materially incomplete regardless of other qualities. The FDR correction
result (zero strategies significant at q < 0.005) must be present and
correctly interpreted; a submission that omits it or misrepresents it as
a positive finding is a methodological disclosure failure. The
independent three-layer statistical audit (zero critical failures
across 59 checks) is a distinguishing feature of the project and should
be cited as evidence of analytical rigour in the methodology section.
When evaluating citations, check they are real and support the specific
claim made — fabricated or misattributed citations are a serious concern
to flag under Requirements and Rubric Alignment.

UNRESOLVED MARKERS CHECK — if any document in the context still
contains [[VERIFY]] or [[VERIFY CITATION]] markers, or unresolved
[[BOB]] / [[MOLLY]] callouts, flag it prominently under section 2,
Requirements and Rubric Alignment: state that the draft contains
unresolved verification markers, that every marker must be resolved
before submission, and that a document carrying unresolved markers is
not ready to submit.

EXTERNAL CITATIONS CHECK — within sections 1 and 2:
Check whether the work contextualises its key findings with external
academic citations. A submission with no citations is weaker than one
that situates its findings within the existing literature. Specifically
look for: a citation supporting the 2022 correlation regime break, a
citation supporting the FDR methodology choice, a citation for the
Carhart four-factor model, and a References section in APA format.
- If citations are present and real (authors and titles that match
  known literature), note this positively under section 1, Data
  Sufficiency and Methodology.
- If citations appear fabricated (authors or titles that do not match
  any known work), flag it as a serious concern under section 2,
  Requirements and Rubric Alignment.

Every rating is exactly one of: Strong, Developing, Needs Work. Be direct
and actionable — the team is preparing a graded submission, so generic
encouragement is not useful."""

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

    multi_user — only consulted in the default (written) review mode.
    """
    parts = [context_block, "", "=== PEER REVIEW NOTES ==="]
    for agent_id, text in peer_responses.items():
        name = _PEER_AGENTS.get(agent_id, {}).get("name", agent_id)
        parts.append(f"\n--- {name} ---\n{text}")
    parts.append("")
    if script_review:
        # Script rubric is self-contained — no section-6 append.
        instructions = _ARBITER_INSTRUCTIONS_SCRIPT
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
    True when the verdict carries the Section 5 heading the active rubric
    requires. Both rubrics end with a Section 5 — the written rubric
    calls it "Overall Academic Readiness" and the script rubric "Overall
    Delivery Readiness".

    UAT 2026-05-24 (#53/#59) — the prior check was
    `expected_title.lower() in text.lower()` which searched the WHOLE
    verdict text for the title. The arbiter sometimes wrote section 5
    with an off-rubric heading (e.g. "Overall Readiness Assessment")
    AND mentioned "Overall Academic Readiness" elsewhere in the body
    of a different section — the loose check passed, the fallback
    didn't fire, and the user saw a section 5 with the wrong title or
    no rating badge. The tightened check requires the rubric-correct
    title to appear ON the `### 5.` heading line itself.
    """
    import re
    expected_title = ("Overall Delivery Readiness" if script_review
                      else "Overall Academic Readiness")
    # `### 5.` heading line whose text matches the rubric-correct
    # title. Case-insensitive, multiline. The pattern is anchored to
    # line start so a `### 5. ` literal embedded mid-sentence in
    # someone's prose body never matches.
    pattern = re.compile(
        r"^\s*###\s*5\.\s*" + re.escape(expected_title),
        re.IGNORECASE | re.MULTILINE,
    )
    return bool(pattern.search(text))


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

    The verdict is generated in full (non-streaming) before the endpoint
    streams it, so a failed attempt is never shown to the client — only
    the accepted verdict is streamed. Synchronous (the harness is sync);
    the endpoint runs this in asyncio.to_thread so the event loop stays
    free. Fail-open: an arbiter generation failure returns the
    deterministic mock verdict rather than raising.
    """
    user_message = build_arbiter_user_message(
        context_block, peer_responses, multi_user, script_review)
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
        if not _verdict_has_section_5(result.response, script_review):
            log.warning(
                "academic_review_section_5_fallback_applied",
                arbiter_chars=len(result.response),
                attempts=result.attempts,
            )
            return _assemble_section_5_fallback(
                result.response, peer_responses, script_review)
        return result.response
    except Exception as exc:  # noqa: BLE001
        log.error("academic_review_arbiter_failed", error=str(exc))
        return _mock_arbiter_text()
