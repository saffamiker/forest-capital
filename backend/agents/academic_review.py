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

from agents.base import SONNET_MODEL, OPUS_MODEL, call_claude

PEER_MODEL = SONNET_MODEL      # claude-sonnet-4-6
ARBITER_MODEL = OPUS_MODEL     # claude-opus-4-7  (Opus for the arbiter step only)
PEER_MAX_TOKENS = 800          # ~400-word cap with headroom
ARBITER_MAX_TOKENS = 2000

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


def format_team_activity_block(team_activity: dict[str, Any] | None) -> list[str]:
    """
    Renders the team-activity summary into context lines for the agent
    prompt. Analytical sessions ONLY — testing-session activity is never
    shown to agents. Every project-team member is listed, including any
    with no recorded activity, so the division-of-labour assessment is
    fair. Returns [] when no activity data is available.
    """
    if not team_activity:
        return []
    from config import PROJECT_TEAM_EMAILS, TEAM_MEMBER_NAMES

    per_member = {m["user"]: m for m in team_activity.get("per_member", [])}
    lines: list[str] = ["", "TEAM ENGAGEMENT (analytical sessions only)"]
    for email in sorted(PROJECT_TEAM_EMAILS):
        name = TEAM_MEMBER_NAMES.get(email, email)
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


def build_review_context_block(
    analytics: dict[str, Any], docs_by_type: dict[str, list[dict]],
    team_activity: dict[str, Any] | None = None,
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

    # — Team engagement —
    lines.extend(format_team_activity_block(team_activity))

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


async def gather_review_context() -> dict[str, Any]:
    """
    Assembles the full review context: the analytics snapshot, the
    documents grouped by type, and the formatted context block that gets
    injected into every agent prompt.
    """
    analytics = await _gather_analytics_snapshot()
    docs: list[dict] = []
    try:
        from tools.academic_context import _read_all_with_content
        docs = await _read_all_with_content()
    except Exception as exc:  # noqa: BLE001
        log.warning("academic_review_documents_read_failed", error=str(exc))
    docs_by_type = group_documents_by_type(docs)

    # Team-activity summary — analytical sessions only; testing-session
    # activity is never injected into agent context.
    team_activity: dict[str, Any] | None = None
    try:
        from tools.activity_log import get_activity_summary
        team_activity = await get_activity_summary(analytical_only=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("academic_review_team_activity_failed", error=str(exc))
    multi_user = _team_activity_multi_user(team_activity)

    block = build_review_context_block(analytics, docs_by_type, team_activity)
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
    return (
        f"You are the {meta['name']} on a quantitative investment council "
        f"advising an MSFA graduate practicum team. The team is preparing a "
        f"GRADED academic submission for the Forest Capital portfolio-analysis "
        f"project. Review the project through your expert lens — {meta['lens']}. "
        f"Be direct, specific and actionable: the team needs to know what to "
        f"fix before a graded deadline, not generic encouragement."
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
    """Replicates agents/independent_analyst.py's Gemini call pattern."""
    api_key = os.getenv("GOOGLE_API_KEY", "")
    if _is_test_env() or not api_key:
        return _mock_peer_review(_PEER_AGENTS["independent_analyst"])
    import google.generativeai as genai  # type: ignore[import-untyped]
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-pro", system_instruction=system_prompt)
    return model.generate_content(user_message).text


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
) -> tuple[str, str]:
    """
    Runs one peer agent's review. Synchronous — designed to be wrapped in
    asyncio.to_thread() for the parallel fan-out. Returns (agent_id, text);
    never raises — a failed agent degrades to a mock review so the council
    always returns a full set of peer responses.
    """
    meta = _PEER_AGENTS[agent_id]
    # Test environment has no API keys — short-circuit to a deterministic
    # mock (consistent with the Gemini/Grok helpers), so the fan-out is
    # fast and deterministic under pytest.
    if _is_test_env():
        return agent_id, _mock_peer_review(meta)
    system_prompt = _peer_system_prompt(meta)
    user_message = _peer_user_message(context_block, multi_user)

    # The agent call is routed through the generator-evaluator harness.
    # _generate dispatches on the agent kind; the harness retries it with
    # evaluator feedback when the response scores below threshold. This
    # runs inside the peer's own asyncio.to_thread task, so a retry never
    # blocks the other peers in the fan-out.
    def _generate(prompt: str) -> str:
        if meta["kind"] == "claude":
            return call_claude(PEER_MODEL, system_prompt, prompt,
                               max_tokens=PEER_MAX_TOKENS)
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
) -> dict[str, str]:
    """Fans the review question out to every peer agent in parallel."""
    ids = peer_agent_ids()
    results = await asyncio.gather(
        *[asyncio.to_thread(run_peer_agent, aid, context_block, multi_user)
          for aid in ids]
    )
    return {aid: text for aid, text in results}


# ── Arbiter (academic advisor, Opus) ──────────────────────────────────────────

_ARBITER_INSTRUCTIONS = """=== YOUR TASK — ARBITER VERDICT ===
You are the arbiter. Integrate and WEIGH the peer review notes above — do
not restate them. Produce a structured, rubric-mapped verdict with EXACTLY
five sections, in this exact markdown format so the UI can parse it:

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

Every rating is exactly one of: Strong, Developing, Needs Work. Be direct
and actionable — the team is preparing a graded submission, so generic
encouragement is not useful."""

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
    multi_user: bool = False,
) -> str:
    """Builds the arbiter's user message: the context block, every peer's
    review notes, and the verdict instructions. The sixth section —
    division of labour — is included only when more than one team member
    has platform activity."""
    parts = [context_block, "", "=== PEER REVIEW NOTES ==="]
    for agent_id, text in peer_responses.items():
        name = _PEER_AGENTS.get(agent_id, {}).get("name", agent_id)
        parts.append(f"\n--- {name} ---\n{text}")
    parts.append("")
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


def run_arbiter_with_harness(
    context_block: str,
    peer_responses: dict[str, str],
    multi_user: bool = False,
) -> str:
    """
    Generates the arbiter verdict IN FULL and runs it through the
    generator-evaluator harness — a verdict scoring below threshold is
    regenerated with the evaluator's feedback. Returns the best-scoring
    verdict text.

    The verdict is generated in full (non-streaming) before the endpoint
    streams it, so a failed attempt is never shown to the client — only
    the accepted verdict is streamed. Synchronous (the harness is sync);
    the endpoint runs this in asyncio.to_thread so the event loop stays
    free. Fail-open: an arbiter generation failure returns the
    deterministic mock verdict rather than raising.
    """
    user_message = build_arbiter_user_message(
        context_block, peer_responses, multi_user)
    if _is_test_env() or not os.getenv("ANTHROPIC_API_KEY"):
        return _mock_arbiter_text()

    from agents.academic_advisor import _SYSTEM_PROMPT as advisor_prompt
    from agents.harness import GeneratorEvaluatorHarness
    from agents.evaluator_prompts import academic_review_arbiter_evaluator_prompt

    def _generate(prompt: str) -> str:
        return call_claude(ARBITER_MODEL, advisor_prompt, prompt,
                           max_tokens=ARBITER_MAX_TOKENS)

    try:
        harness = GeneratorEvaluatorHarness()
        result = harness.run(
            generator_fn=_generate,
            evaluator_prompt=academic_review_arbiter_evaluator_prompt(),
            generator_prompt=user_message,
            context=context_block[:6000],
            agent_id="academic_advisor",
        )
        return result.response
    except Exception as exc:  # noqa: BLE001
        log.error("academic_review_arbiter_failed", error=str(exc))
        return _mock_arbiter_text()
