"""
tools/triage_engine.py — automated feedback triage.

run_triage() monitors the UAT backlog (test_feedback + test_results),
sends every unaddressed item to a QA-lead agent, stores the structured
triage report in triage_reports, and opens GitHub issues for the urgent
items.

The seven steps (see CLAUDE.md → Automated Feedback Triage):
  1. Gather unaddressed items — return early if the backlog is empty.
  2. Build the triage context for the agent.
  3. Agent triage call — claude-sonnet-4-6 via the harness.
  4. Parse the report sections.
  5. Create GitHub issues for the blocking / major items.
  6. Store the report; mark the assessed feedback 'triaged'.
  7. The stored report is itself the login-notification source.

FAIL-OPEN BY DESIGN: a failure at any step never loses the run — the
report row is still stored with whatever completed, and `status`
records how far it got (complete / partial / failed). A concurrent run
is skipped via the running-row lock.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timezone
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Triage gate the agent reasons against — the June 3 cohort
# peer-review presentation at McColl, when the platform itself is
# demonstrated live. Bob's May 27 midpoint paper is the first hard
# submission deadline but it is a written-only deliverable that does
# not depend on platform uptime, so the triage cutoff stays at the
# cohort presentation when the platform must be working in front of
# the panel.
_DEADLINE = date(2026, 6, 3)

# A backlog item warrants a GitHub issue when its severity is this high.
# Failures carry a tester-assigned severity; feedback carries the
# AI-assigned ai_severity from submit-time categorisation.
_ISSUE_FAILURE_SEVERITIES = {"blocking", "major"}
_ISSUE_FEEDBACK_SEVERITIES = {"blocking", "major", "critical"}

# The five sections every triage report must contain.
_REQUIRED_SECTIONS = (
    "## IMMEDIATE ACTIONS", "## QUICK WINS", "## PATTERNS AND THEMES",
    "## POST-DEADLINE BACKLOG", "## SUMMARY",
)


def _is_test_env() -> bool:
    return os.getenv("ENVIRONMENT", "").lower() == "test"


def _iso(value: Any) -> str | None:
    try:
        return value.isoformat() if value is not None else None
    except Exception:  # noqa: BLE001
        return None


# ── triage_reports persistence ────────────────────────────────────────────────

async def is_triage_running() -> bool:
    """True when a triage_reports row is still in the 'running' state —
    the concurrency lock. Fail-open: a database error reports False so a
    triage is never permanently blocked by a stale read."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return False
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT 1 FROM triage_reports WHERE status = 'running' LIMIT 1"))
            return row.fetchone() is not None
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_running_check_failed", error=str(exc))
        return False


async def count_unaddressed_items(since: datetime | None = None) -> int:
    """
    The number of unaddressed backlog items — new feedback plus unresolved
    failures. When `since` is given, only items created after that instant
    are counted (the threshold-trigger window). Fail-open → 0.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return 0
        async with AsyncSessionLocal() as session:
            fb_sql = "SELECT COUNT(*) FROM test_feedback WHERE status = 'new'"
            fr_sql = ("SELECT COUNT(*) FROM test_results "
                      "WHERE result = 'fail' AND resolved_at IS NULL")
            params: dict[str, Any] = {}
            if since is not None:
                fb_sql += " AND submitted_at > :since"
                fr_sql += " AND attested_at > :since"
                params["since"] = since
            fb = await session.execute(text(fb_sql), params)
            fr = await session.execute(text(fr_sql), params)
            return int((fb.scalar() or 0) + (fr.scalar() or 0))
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_count_failed", error=str(exc))
        return 0


async def last_triage_at() -> datetime | None:
    """The triggered_at of the most recent triage run, or None."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT triggered_at FROM triage_reports "
                "ORDER BY triggered_at DESC LIMIT 1"))
            found = row.fetchone()
            return found[0] if found else None
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_last_at_failed", error=str(exc))
        return None


async def _create_running_report(triggered_by: str) -> int | None:
    """Inserts the 'running' row — both the report placeholder and the
    concurrency lock. Returns its id, or None on a database error."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "INSERT INTO triage_reports (triggered_by, status, report_text) "
                "VALUES (:tb, 'running', '') RETURNING id"
            ), {"tb": triggered_by})
            new_id = row.scalar()
            await session.commit()
            return int(new_id) if new_id is not None else None
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_create_running_failed", error=str(exc))
        return None


async def _finalise_report(
    report_id: int, *, items_assessed: int, report_text: str,
    github_issues_created: int, status: str, metadata: dict[str, Any],
) -> None:
    """Updates the running row with the completed report. Fail-open."""
    try:
        import json

        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return
        async with AsyncSessionLocal() as session:
            await session.execute(text(
                "UPDATE triage_reports SET items_assessed = :n, "
                "report_text = :rt, github_issues_created = :gh, "
                "status = :st, metadata = CAST(:md AS jsonb) WHERE id = :id"
            ), {"n": items_assessed, "rt": report_text,
                "gh": github_issues_created, "st": status,
                "md": json.dumps(metadata), "id": report_id})
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_finalise_failed", report_id=report_id, error=str(exc))


async def get_all_triage_reports() -> list[dict[str, Any]]:
    """Every triage report, newest first — the Settings → Triage tab."""
    return await _read_reports(latest_only=False)


async def get_latest_triage_report() -> dict[str, Any] | None:
    """The most recent triage report, or None."""
    reports = await _read_reports(latest_only=True)
    return reports[0] if reports else None


async def _read_reports(*, latest_only: bool) -> list[dict[str, Any]]:
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        limit = "LIMIT 1" if latest_only else ""
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text(
                "SELECT id, triggered_by, triggered_at, items_assessed, "
                "report_text, github_issues_created, status, metadata "
                f"FROM triage_reports ORDER BY triggered_at DESC {limit}"))
            return [{
                "id": r[0], "triggered_by": r[1], "triggered_at": _iso(r[2]),
                "items_assessed": r[3], "report_text": r[4],
                "github_issues_created": r[5], "status": r[6],
                "metadata": r[7] or {},
            } for r in rows.fetchall()]
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_read_reports_failed", error=str(exc))
        return []


async def _mark_feedback_triaged(feedback_ids: list[int]) -> None:
    """Moves the assessed feedback rows from 'new' to 'triaged' so a later
    run does not re-assess them. Fail-open."""
    if not feedback_ids:
        return
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return
        async with AsyncSessionLocal() as session:
            await session.execute(text(
                "UPDATE test_feedback SET status = 'triaged' "
                "WHERE id = ANY(:ids) AND status = 'new'"
            ), {"ids": feedback_ids})
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_mark_triaged_failed", error=str(exc))


async def _mark_failures_triaged(failure_ids: list[int]) -> None:
    """Stamps test_results.triaged_at on every assessed failure so the
    next triage's _gather_unaddressed skips them. Migration 023 added
    the column. Fail-open."""
    if not failure_ids:
        return
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return
        async with AsyncSessionLocal() as session:
            await session.execute(text(
                "UPDATE test_results SET triaged_at = now() "
                "WHERE id = ANY(:ids) AND triaged_at IS NULL"
            ), {"ids": failure_ids})
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_mark_failures_triaged_failed", error=str(exc))


# ── Step 1 — gather unaddressed items ─────────────────────────────────────────

async def _gather_unaddressed() -> tuple[list[dict], list[dict]]:
    """The current open backlog — unresolved failures and new feedback.
    Reuses the test_runner read layer (both fail-open to []).

    Migration 023 added test_results.triaged_at — a failure that a
    previous triage run already assessed (and which is still unresolved
    waiting on a fix) is no longer re-flagged in every subsequent run.
    The agent context stays focused on the genuinely new backlog plus
    items that have been resolved-and-await-retest (Commit 4 of this
    workflow injects the resolved-item context separately so the agent
    does not re-raise fixes already in flight)."""
    from tools.test_runner import get_all_failures, get_all_feedback

    failures = [f for f in await get_all_failures()
                if not f.get("resolved_at")
                and not f.get("triaged_at")]
    feedback = [f for f in await get_all_feedback({})
                if f.get("status") == "new"]
    return failures, feedback


# ── Step 2 — build the triage context ─────────────────────────────────────────

def _build_context(failures: list[dict], feedback: list[dict]) -> str:
    """Structures every item into the text block the agent reasons over."""
    lines: list[str] = ["FAILURE REPORTS:"]
    if not failures:
        lines.append("  (none)")
    for f in failures:
        lines.append(
            f"  - [failure #{f['id']}] step {f.get('step_id')} "
            f"({f.get('script_id')}) · severity {f.get('severity') or 'unset'}\n"
            f"    description: {f.get('failure_description') or '—'}\n"
            f"    actual result: {f.get('actual_result') or '—'}\n"
            f"    browser: {f.get('browser_info') or '—'} · "
            f"reported by {f.get('user_email')} at {f.get('attested_at')}")

    lines.append("")
    lines.append("FEEDBACK AND ENHANCEMENT REQUESTS:")
    if not feedback:
        lines.append("  (none)")
    for f in feedback:
        tags = ", ".join(f.get("ai_tags") or [])
        lines.append(
            f"  - [feedback #{f['id']}] {f.get('feedback_type')}: "
            f"{f.get('title')}\n"
            f"    description: {f.get('description') or '—'}\n"
            f"    AI category: {f.get('ai_category') or '—'} · "
            f"AI severity: {f.get('ai_severity') or '—'} · "
            f"AI effort: {f.get('ai_effort_estimate') or '—'}\n"
            f"    AI summary: {f.get('ai_summary') or '—'} · tags: {tags}\n"
            f"    submitted by {f.get('user_email')} at {f.get('submitted_at')}")
    return "\n".join(lines)


# ── Step 3 — the triage agent ─────────────────────────────────────────────────

_TRIAGE_SYSTEM_PROMPT = (
    "You are the QA lead reviewing a backlog of feedback and failure "
    "reports from a UAT test pass of an investment analytics platform. "
    "The platform is demonstrated live at the June 3rd cohort "
    "peer-review presentation at McColl School of Business; that is "
    "the triage gate this report reasons against. (Bob's May 27th "
    "midpoint paper is a written submission that does not depend on "
    "platform uptime, so it is not the triage cutoff here.)\n\n"
    "Previously resolved items are provided for context. Do not re-raise "
    "these unless you have evidence the fix did not work. Items marked "
    "requires_retest=True are awaiting reporter verification — they are "
    "expected to clear when the reporter re-attests the test step. Do "
    "not list awaiting-retest items as IMMEDIATE ACTIONS; they belong "
    "to the verification flow, not the triage flow.\n\n"
    "Produce a structured triage report with EXACTLY these five markdown "
    "sections, in this order:\n\n"
    "## IMMEDIATE ACTIONS\n"
    "Items that must be addressed before the June 3rd cohort "
    "presentation — blocking or major severity, or anything that would "
    "embarrass the team during the live demo. For each: title, the "
    "reason it is immediate, a recommended fix approach, and an effort "
    "estimate.\n\n"
    "## QUICK WINS\n"
    "Small-effort items (Trivial/Small) that would noticeably improve the "
    "platform. For each: title, what it improves, and an effort estimate.\n\n"
    "## PATTERNS AND THEMES\n"
    "Where multiple items report the same underlying issue, group them and "
    "name the root cause. List the affected items.\n\n"
    "## POST-DEADLINE BACKLOG\n"
    "Everything else — valid requests for after the June 3rd cohort "
    "presentation, grouped by category.\n\n"
    "## SUMMARY\n"
    "Total items reviewed; counts of immediate actions, quick wins, "
    "patterns and post-deadline items; and the recommended focus for the "
    "days before the cohort presentation.\n\n"
    "Be direct and specific. Reference item titles and descriptions by "
    "name. Do not pad with generic advice."
)


async def _recent_resolved_items(window_days: int = 14) -> list[dict[str, Any]]:
    """
    Triage items resolved in the last `window_days` — injected into
    the agent prompt so the QA-lead does not re-raise fixed issues.
    Includes items still awaiting reporter retest so the agent knows
    those are in flight, not pending fresh action.

    Fail-open: a missing migration-023 table returns []. The agent
    then runs without resolved-item context (the same behaviour as
    before this commit) — never blocks the triage run.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        async with AsyncSessionLocal() as session:
            # SQL injection-safe: window_days is cast to int so the
            # interval literal never accepts caller-supplied SQL.
            window = int(window_days)
            rows = await session.execute(text(
                "SELECT id, item_type, item_title, resolution_note, "
                " fix_commit, requires_retest, retest_completed_at "
                "FROM triage_report_items "
                "WHERE resolved_at IS NOT NULL "
                f" AND resolved_at > now() - interval '{window} days' "
                "ORDER BY resolved_at DESC"
            ))
            return [{
                "id": int(r[0]), "item_type": r[1], "item_title": r[2],
                "resolution_note": r[3], "fix_commit": r[4],
                "requires_retest": bool(r[5]),
                "retest_completed_at": _iso(r[6]),
            } for r in rows.fetchall()]
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_recent_resolved_failed", error=str(exc))
        return []


def _format_resolved_items_block(items: list[dict[str, Any]]) -> str:
    """
    Renders the resolved-item context the agent reasons against.
    Empty list → empty block; the user message simply omits the
    section so a brand-new deployment (no resolved items yet) reads
    cleanly. Each line names the resolved item, its fix commit
    (short SHA), and the retest state — "Retest: pending" or "Retest:
    complete" or "Retest: not required".
    """
    if not items:
        return ""
    lines = ["RECENTLY RESOLVED ITEMS (last 14 days):"]
    for it in items:
        commit = (it.get("fix_commit") or "")[:8] or "—"
        if it.get("retest_completed_at"):
            retest = "complete"
        elif it.get("requires_retest"):
            retest = "pending"
        else:
            retest = "not_required"
        title = (it.get("item_title") or "(untitled)")[:120]
        note = (it.get("resolution_note") or "—")[:200]
        lines.append(
            f"  - [{it.get('item_type')}] {title}\n"
            f"      Resolved: {note}\n"
            f"      Commit: {commit}\n"
            f"      Retest: {retest}"
        )
    lines.append("")
    return "\n".join(lines)


def _triage_user_message(
    failures: list[dict], feedback: list[dict],
    resolved_block: str = "",
) -> str:
    days = max(0, (_DEADLINE - date.today()).days)
    parts = [
        f"Today is {date.today().isoformat()}. You have {days} days "
        f"until the June 3rd cohort peer-review presentation.",
    ]
    if resolved_block:
        parts.append("")
        parts.append(resolved_block.rstrip())
    parts.append("")
    parts.append(
        f"Here are all {len(failures) + len(feedback)} unaddressed items:")
    parts.append("")
    parts.append(_build_context(failures, feedback))
    return "\n".join(parts)


def _mock_triage_report(failures: list[dict], feedback: list[dict]) -> str:
    """A deterministic five-section report for the test environment and
    the no-API-key path — exercises the same parse / store path as a real
    agent response."""
    n = len(failures) + len(feedback)
    imm = [f for f in failures
           if (f.get("severity") or "") in _ISSUE_FAILURE_SEVERITIES]
    imm += [f for f in feedback
            if (f.get("ai_severity") or "") in _ISSUE_FEEDBACK_SEVERITIES]
    imm_lines = "\n".join(
        f"- {i.get('title') or i.get('failure_description') or 'item'} "
        f"— flagged immediate; effort: Small" for i in imm) or "- None."
    return (
        "## IMMEDIATE ACTIONS\n"
        f"{imm_lines}\n\n"
        "## QUICK WINS\n"
        "- None identified in this deterministic summary; effort: Trivial.\n\n"
        "## PATTERNS AND THEMES\n"
        "- No cross-item pattern detected in this deterministic summary.\n\n"
        "## POST-DEADLINE BACKLOG\n"
        f"- {len(feedback)} feedback item(s) recorded for review after the "
        "deadline.\n\n"
        "## SUMMARY\n"
        f"Total items reviewed: {n}. Immediate actions: {len(imm)}. "
        "Quick wins: 0. Patterns identified: 0. "
        f"Post-deadline: {len(feedback)}.\n"
        "(Deterministic triage summary — generated without the triage agent.)"
    )


def _generate_triage_report(
    failures: list[dict], feedback: list[dict],
    resolved_block: str = "",
) -> str:
    """Runs the triage agent through the generator-evaluator harness.
    Falls back to the deterministic report in the test environment, with
    no API key, or on any agent error.

    resolved_block — formatted output of _format_resolved_items_block,
    injected ABOVE the unaddressed-items context so the agent knows
    which items are already in flight and does not re-raise them."""
    user_message = _triage_user_message(failures, feedback, resolved_block)
    if _is_test_env() or not os.getenv("ANTHROPIC_API_KEY"):
        return _mock_triage_report(failures, feedback)
    try:
        from agents.base import SONNET_MODEL, call_claude
        from agents.evaluator_prompts import triage_evaluator_prompt
        from agents.harness import GeneratorEvaluatorHarness

        def _generate(prompt: str) -> str:
            return call_claude(SONNET_MODEL, _TRIAGE_SYSTEM_PROMPT, prompt,
                               max_tokens=4000,
                               trigger="triage_engine")

        harness = GeneratorEvaluatorHarness()
        result = harness.run(
            generator_fn=_generate,
            evaluator_prompt=triage_evaluator_prompt(),
            generator_prompt=user_message,
            context=_build_context(failures, feedback)[:6000],
            agent_id="triage",
        )
        return result.response
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_agent_failed", error=str(exc))
        return _mock_triage_report(failures, feedback)


# ── Step 4 — parse the report sections ────────────────────────────────────────

def _parse_sections(report_text: str) -> dict[str, bool]:
    """Records which of the five required sections the report contains —
    stored in metadata for display and the evaluator's benefit."""
    return {s.lstrip("# ").strip(): (s in report_text)
            for s in _REQUIRED_SECTIONS}


# Maps the agent's section heading to the item_type column value the
# triage_report_items.item_type CHECK accepts.
_SECTION_TO_ITEM_TYPE = {
    "IMMEDIATE ACTIONS": "immediate",
    "QUICK WINS": "quick_win",
    "PATTERNS AND THEMES": "pattern",
    "POST-DEADLINE BACKLOG": "backlog",
}

# Matches an item-reference like "[failure #62]" or "[feedback #4]" so
# the parser can back-link parsed items to their UAT source rows.
_SOURCE_REF_RE = re.compile(
    r"\[(failure|feedback)\s*#(\d+)\]", re.IGNORECASE)

# Matches a bullet line — agent output uses "- ", "* ", or "1. " /
# "1) " for numbered items.
_BULLET_RE = re.compile(r"^(?:[-*]|\d+[.\)])\s+(.+)$")


def _split_report_into_section_blocks(
    report_text: str,
) -> dict[str, list[str]]:
    """
    Walks the agent's verdict and groups raw bullet lines by section.
    Returns {item_type: [bullet_lines]} keyed by the canonical
    item_type strings (immediate / quick_win / pattern / backlog). The
    SUMMARY section is skipped — it carries aggregate counts, not
    individual items.

    Permissive parser: tolerates the agent omitting a section, using
    different bullet markers, or interleaving prose between bullets.
    The parser only collects lines that look like list items, leaving
    framing prose out of the parsed item list.
    """
    blocks: dict[str, list[str]] = {v: [] for v in _SECTION_TO_ITEM_TYPE.values()}
    current: str | None = None
    multiline_buf: list[str] = []

    def _flush() -> None:
        if current and multiline_buf:
            blocks[current].append("\n".join(multiline_buf).strip())
        multiline_buf.clear()

    for raw_line in (report_text or "").splitlines():
        # Section header (## SECTION_NAME) — switch the current bucket.
        if raw_line.startswith("## "):
            _flush()
            header = raw_line[3:].strip().upper()
            current = _SECTION_TO_ITEM_TYPE.get(header)
            continue

        if current is None:
            continue

        bullet_match = _BULLET_RE.match(raw_line.rstrip())
        if bullet_match:
            # Start of a new item — flush whatever was in the buffer.
            _flush()
            multiline_buf.append(bullet_match.group(1).strip())
        elif multiline_buf and raw_line.startswith(("  ", "\t")):
            # Continuation line for the in-progress bullet (the agent
            # often indents follow-up detail). Inspect the raw line —
            # `.strip()` removes leading whitespace, so the check has
            # to run against raw_line before any normalisation, or
            # this branch is dead code and continuation lines are
            # silently dropped.
            multiline_buf.append(raw_line.strip())
        elif multiline_buf and raw_line.strip() == "":
            # Blank line ends the current bullet.
            _flush()
        # Otherwise: prose line in the section — ignored.

    _flush()
    return blocks


def _parse_item_block(
    block: str, issues_by_source: dict[tuple[str, int], dict[str, Any]],
) -> dict[str, Any]:
    """
    Turns one raw bullet block into the triage_report_items row payload.

    Title heuristic: the first line of the block, capped at 500 chars
    (the column limit). Body: the remainder.

    Source linkage: if the block carries a "[failure #N]" or
    "[feedback #N]" reference, source_item_type + source_item_id pin
    the back-pointer. The issues_by_source map (built from
    issues_created) attaches github_issue_number + github_issue_url
    when an issue was opened for the same source row.
    """
    lines = block.splitlines()
    title = (lines[0].strip() if lines else "(untitled item)")[:500]
    body = "\n".join(lines[1:]).strip() or None

    source_type: str | None = None
    source_id: int | None = None
    gh_number: int | None = None
    gh_url: str | None = None
    match = _SOURCE_REF_RE.search(block)
    if match:
        source_type = match.group(1).lower()
        try:
            source_id = int(match.group(2))
        except (TypeError, ValueError):
            source_id = None
        if source_id is not None:
            issue = issues_by_source.get((source_type, source_id))
            if issue:
                gh_number = issue.get("number")
                gh_url = issue.get("url")

    return {
        "item_title": title, "item_body": body,
        "github_issue_number": gh_number, "github_issue_url": gh_url,
        "source_item_type": source_type, "source_item_id": source_id,
    }


async def _store_triage_items(
    report_id: int, report_text: str,
    issues_created: list[dict[str, Any]],
) -> list[int]:
    """
    Parses report_text into normalised triage_report_items rows and
    INSERTs them. Returns the list of new row ids in insertion order.

    Fail-open: any database error logs and returns []. The triage run
    still completes — the items are an additive layer over the
    existing report_text blob, not a substitute.
    """
    if not report_text:
        return []
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []

        # Index the GitHub issues by source so the parser can attach
        # the issue number/url whenever a parsed item references the
        # same source row the engine already issued.
        issues_by_source: dict[tuple[str, int], dict[str, Any]] = {}
        for issue in issues_created:
            src_type = issue.get("item_type")
            src_id = issue.get("item_id")
            if isinstance(src_id, int) and src_type:
                issues_by_source[(str(src_type), src_id)] = issue

        # Parse → list of (item_type, payload).
        blocks = _split_report_into_section_blocks(report_text)
        rows_to_insert: list[dict[str, Any]] = []
        for item_type, item_blocks in blocks.items():
            for block in item_blocks:
                payload = _parse_item_block(block, issues_by_source)
                rows_to_insert.append({
                    "report_id": report_id,
                    "item_type": item_type,
                    **payload,
                })

        if not rows_to_insert:
            return []

        new_ids: list[int] = []
        async with AsyncSessionLocal() as session:
            for row in rows_to_insert:
                result = await session.execute(text(
                    "INSERT INTO triage_report_items "
                    "(report_id, item_type, item_title, item_body, "
                    " github_issue_number, github_issue_url, "
                    " source_item_type, source_item_id) "
                    "VALUES (:report_id, :item_type, :item_title, "
                    " :item_body, :github_issue_number, "
                    " :github_issue_url, :source_item_type, "
                    " :source_item_id) RETURNING id"
                ), row)
                new_id = result.scalar()
                if new_id is not None:
                    new_ids.append(int(new_id))
            await session.commit()
        log.info("triage_items_stored", report_id=report_id,
                 n_items=len(new_ids))
        return new_ids
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_items_store_failed",
                    report_id=report_id, error=str(exc))
        return []


async def _back_populate_source_rows(
    issues_created: list[dict[str, Any]],
    failure_ids: list[int],
) -> None:
    """
    Updates test_results and test_feedback with github_issue_number /
    github_issue_url for every source row the triage engine just
    opened a GitHub issue against. Also stamps triaged_at on every
    assessed failure (the feedback equivalent is the status='triaged'
    transition handled elsewhere). Fail-open per UPDATE so one bad
    row never blocks the rest."""
    if not issues_created and not failure_ids:
        return
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return
        async with AsyncSessionLocal() as session:
            for issue in issues_created:
                src_type = issue.get("item_type")
                src_id = issue.get("item_id")
                num = issue.get("number")
                url = issue.get("url")
                if not isinstance(src_id, int) or num is None:
                    continue
                table = ("test_results" if src_type == "failure"
                          else "test_feedback" if src_type == "feedback"
                          else None)
                if table is None:
                    continue
                await session.execute(text(
                    f"UPDATE {table} SET github_issue_number = :n, "
                    f"github_issue_url = :u WHERE id = :id"
                ), {"n": int(num), "u": str(url or ""), "id": src_id})
            # Stamp triaged_at on every assessed failure so the next
            # run skips them in _gather_unaddressed.
            if failure_ids:
                await session.execute(text(
                    "UPDATE test_results SET triaged_at = now() "
                    "WHERE id = ANY(:ids) AND triaged_at IS NULL"
                ), {"ids": failure_ids})
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_back_populate_failed", error=str(exc))


# ── Step 5 — GitHub issues ────────────────────────────────────────────────────

def _immediate_items(
    failures: list[dict], feedback: list[dict],
) -> list[dict[str, Any]]:
    """The blocking / major backlog items — the set that gets GitHub
    issues. Severity is deterministic (tester-assigned for failures,
    AI-assigned for feedback) so the issue set never depends on parsing
    the agent's prose."""
    items: list[dict[str, Any]] = []
    for f in failures:
        if (f.get("severity") or "") in _ISSUE_FAILURE_SEVERITIES:
            items.append({
                "item_type": "failure", "item_id": f["id"],
                "severity": f.get("severity"), "category": "bug",
                "title": (f.get("failure_description") or "Test failure")[:80],
                "body": (
                    f"**Failure** in step `{f.get('step_id')}` "
                    f"({f.get('script_id')})\n\n"
                    f"- Severity: {f.get('severity')}\n"
                    f"- Description: {f.get('failure_description') or '—'}\n"
                    f"- Actual result: {f.get('actual_result') or '—'}\n"
                    f"- Browser: {f.get('browser_info') or '—'}\n"
                    f"- Reported by: {f.get('user_email')}\n\n"
                    "_Opened automatically by the triage engine._"),
            })
    for f in feedback:
        if (f.get("ai_severity") or "") in _ISSUE_FEEDBACK_SEVERITIES:
            items.append({
                "item_type": "feedback", "item_id": f["id"],
                "severity": f.get("ai_severity"),
                "category": f.get("ai_category") or "enhancement",
                "title": (f.get("title") or "Feedback item")[:80],
                "body": (
                    f"**Feedback** — {f.get('feedback_type')}\n\n"
                    f"- AI severity: {f.get('ai_severity')}\n"
                    f"- AI category: {f.get('ai_category') or '—'}\n"
                    f"- AI summary: {f.get('ai_summary') or '—'}\n"
                    f"- Description: {f.get('description') or '—'}\n"
                    f"- Submitted by: {f.get('user_email')}\n\n"
                    "_Opened automatically by the triage engine._"),
            })
    return items


async def _create_github_issue(
    title: str, body: str, labels: list[str],
) -> dict[str, Any] | None:
    """Opens one GitHub issue. Returns {number, url} or None. Fail-open —
    every failure mode (no token, API error, network) returns None and is
    logged; the caller never lets it abort the triage run."""
    from config import GITHUB_REPO, GITHUB_TOKEN
    if not GITHUB_TOKEN:
        log.info("triage_github_skipped", reason="GITHUB_TOKEN not set")
        return None
    try:
        import httpx

        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            resp = await client.post(
                f"https://api.github.com/repos/{GITHUB_REPO}/issues",
                json={"title": title, "body": body, "labels": labels})
            if resp.status_code in (200, 201):
                d = resp.json()
                return {"number": d.get("number"), "url": d.get("html_url")}
            log.warning("triage_github_issue_rejected",
                        status=resp.status_code)
            return None
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_github_issue_failed", error=str(exc))
        return None


async def _ensure_labels() -> None:
    """Ensures the triage label set exists on the repository before issues
    are opened. Fail-open — GitHub auto-creates an unknown label on issue
    creation anyway, so a failure here only means default label colours.
    The implementation lives in tools/github_labels (added in Commit 5);
    until then this is a no-op."""
    try:
        from tools.github_labels import ensure_triage_labels
        await ensure_triage_labels()
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_label_setup_failed", error=str(exc))


async def _open_issues_for(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Opens a GitHub issue per immediate item. Every step is fail-open."""
    if not items:
        return []
    await _ensure_labels()
    created: list[dict[str, Any]] = []
    for it in items:
        labels = [str(it["severity"] or "").strip(), str(it["category"])]
        labels = [lbl for lbl in labels if lbl]
        issue = await _create_github_issue(it["title"], it["body"], labels)
        if issue:
            created.append({
                "item_type": it["item_type"], "item_id": it["item_id"],
                "number": issue["number"], "url": issue["url"],
            })
    return created


# ── run_triage — the orchestrator ─────────────────────────────────────────────

async def run_triage(triggered_by: str = "manual") -> dict[str, Any]:
    """
    The full seven-step triage run. `triggered_by` is
    threshold | test_pass | manual.

    Returns a small status dict. Always safe to call fire-and-forget — a
    concurrent run is skipped, an empty backlog returns early, and any
    step failure still stores the report with status partial / failed.
    """
    # Concurrency lock — one triage at a time.
    if await is_triage_running():
        log.info("triage_skipped_already_running")
        return {"status": "skipped", "reason": "already_running"}

    # Step 1 — gather. An empty backlog needs no report.
    failures, feedback = await _gather_unaddressed()
    total = len(failures) + len(feedback)
    if total == 0:
        log.info("triage_skipped_empty_backlog")
        return {"status": "skipped", "reason": "empty_backlog"}

    report_id = await _create_running_report(triggered_by)
    if report_id is None:
        log.warning("triage_no_report_row")
        return {"status": "failed", "reason": "no_database"}

    status = "complete"
    report_text = ""
    issues_created: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {"triggered_by": triggered_by}
    try:
        # Steps 2-3 — context + agent report. Resolved-item context
        # (Commit 4 of the triage-resolution build) is fetched and
        # rendered into a block prepended to the unaddressed items.
        # The agent prompt instructs it not to re-raise items in this
        # block unless it has evidence the fix didn't work.
        resolved_recent = await _recent_resolved_items()
        resolved_block = _format_resolved_items_block(resolved_recent)
        metadata["resolved_in_context"] = [r["id"] for r in resolved_recent]
        report_text = _generate_triage_report(
            failures, feedback, resolved_block=resolved_block)
        # Step 4 — record which sections are present.
        metadata["sections"] = _parse_sections(report_text)
        # Step 5 — GitHub issues for the blocking / major items.
        immediate = _immediate_items(failures, feedback)
        metadata["immediate_count"] = len(immediate)
        try:
            issues_created = await _open_issues_for(immediate)
        except Exception as exc:  # noqa: BLE001
            # GitHub issue creation is fail-open — the report still stores.
            log.warning("triage_github_step_failed", error=str(exc))
            status = "partial"
        metadata["github_issues"] = issues_created
        # Step 6 — mark the assessed feedback triaged + (new) the
        # failures triaged. test_feedback uses the status column;
        # test_results uses migration-023's triaged_at column.
        await _mark_feedback_triaged([f["id"] for f in feedback])
        # Step 6b (new) — parse the verdict into triage_report_items
        # rows and back-populate the github_issue columns on the
        # source UAT rows. Per-item resolution is the workflow these
        # rows unlock — Commit 3 of the triage-resolution build adds
        # resolve_triage_items() that updates them by id.
        new_item_ids = await _store_triage_items(
            report_id, report_text, issues_created)
        metadata["item_ids"] = new_item_ids
        await _back_populate_source_rows(
            issues_created, [f["id"] for f in failures])
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_run_failed", report_id=report_id, error=str(exc))
        status = "failed"

    await _finalise_report(
        report_id, items_assessed=total, report_text=report_text,
        github_issues_created=len(issues_created), status=status,
        metadata=metadata)

    # Step 7 — the stored report is the login-notification source; the
    # frontend surfaces it from GET /api/v1/testing/triage/latest.
    log.info("triage_complete", report_id=report_id, items=total,
             issues=len(issues_created), status=status,
             triggered_by=triggered_by)
    return {
        "status": status, "report_id": report_id, "items_assessed": total,
        "github_issues_created": len(issues_created),
    }


# ── triage_report_items — read + resolve workflow (Commit 2 of 6) ─────────────

async def get_all_triage_items(
    report_id: int | None = None,
) -> list[dict[str, Any]]:
    """
    Every triage_report_items row, newest report first, then by
    item_type (immediate → quick_win → pattern → backlog), then by id.
    When report_id is given, restricts to that one report.

    Fail-open: a missing migration-023 table returns [] so the
    sysadmin Settings panel renders an empty list rather than 500ing.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        params: dict[str, Any] = {}
        where = ""
        if report_id is not None:
            where = "WHERE report_id = :rid"
            params["rid"] = report_id
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text(
                "SELECT id, report_id, item_type, item_title, item_body, "
                " github_issue_number, github_issue_url, "
                " source_item_type, source_item_id, "
                " resolved_at, resolved_by, resolution_note, fix_commit, "
                " requires_retest, retest_requested_at, "
                " retest_completed_at, created_at "
                f"FROM triage_report_items {where} "
                "ORDER BY report_id DESC, "
                " CASE item_type "
                "  WHEN 'immediate' THEN 0 WHEN 'quick_win' THEN 1 "
                "  WHEN 'pattern' THEN 2 ELSE 3 END, id"
            ), params)
            return [{
                "id": r[0], "report_id": r[1], "item_type": r[2],
                "item_title": r[3], "item_body": r[4],
                "github_issue_number": r[5], "github_issue_url": r[6],
                "source_item_type": r[7], "source_item_id": r[8],
                "resolved_at": _iso(r[9]), "resolved_by": r[10],
                "resolution_note": r[11], "fix_commit": r[12],
                "requires_retest": bool(r[13]),
                "retest_requested_at": _iso(r[14]),
                "retest_completed_at": _iso(r[15]),
                "created_at": _iso(r[16]),
            } for r in rows.fetchall()]
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_items_read_failed", error=str(exc))
        return []


async def resolve_triage_item(
    item_id: int, *, resolved_by: str, resolution_note: str,
    fix_commit: str | None = None, requires_retest: bool = False,
) -> dict[str, Any] | None:
    """
    Marks a triage_report_items row resolved. Used by the
    /api/v1/testing/triage/items/{id}/resolve endpoint AND the
    tools.triage_resolver helper Claude Code calls after applying a
    fix. Returns the updated row's source_item_type / source_item_id /
    reporter so the caller can dispatch the retest notification
    (Commit 3 wires that path).

    requires_retest=True stamps retest_requested_at to now() — the
    frontend's TestNotifications surfaces a "Fix ready for retest"
    pill to the reporter on next login. Fail-open: a missing row or
    a database error returns None and the caller skips notification.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "UPDATE triage_report_items SET "
                " resolved_at = now(), resolved_by = :by, "
                " resolution_note = :note, fix_commit = :commit, "
                " requires_retest = :retest, "
                " retest_requested_at = CASE WHEN :retest "
                "   THEN now() ELSE retest_requested_at END "
                "WHERE id = :id "
                "RETURNING id, source_item_type, source_item_id, "
                " item_title, requires_retest"
            ), {"id": item_id, "by": resolved_by,
                "note": resolution_note, "commit": fix_commit,
                "retest": bool(requires_retest)})
            found = row.fetchone()
            await session.commit()
            if found is None:
                return None
            return {
                "id": int(found[0]),
                "source_item_type": found[1],
                "source_item_id": found[2],
                "item_title": found[3],
                "requires_retest": bool(found[4]),
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_resolve_item_failed",
                    item_id=item_id, error=str(exc))
        return None


async def unresolve_triage_item(item_id: int) -> bool:
    """
    Clears every resolution field on a triage_report_items row.
    Backs the PATCH /unresolve endpoint — sysadmin recovery when an
    item was marked resolved in error. Returns True on success.
    Fail-open returns False.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return False
        async with AsyncSessionLocal() as session:
            result = await session.execute(text(
                "UPDATE triage_report_items SET "
                " resolved_at = NULL, resolved_by = NULL, "
                " resolution_note = NULL, fix_commit = NULL, "
                " requires_retest = false, retest_requested_at = NULL, "
                " retest_completed_at = NULL "
                "WHERE id = :id"
            ), {"id": item_id})
            await session.commit()
            return (result.rowcount or 0) > 0
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_unresolve_item_failed",
                    item_id=item_id, error=str(exc))
        return False


async def mark_retest_complete(item_id: int) -> bool:
    """
    Stamps retest_completed_at to now() — called when the reporter
    re-attests the test step the item resolved. Closes the loop on
    the resolve → notify → reporter retests workflow. Fail-open.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return False
        async with AsyncSessionLocal() as session:
            result = await session.execute(text(
                "UPDATE triage_report_items SET retest_completed_at = now() "
                "WHERE id = :id AND retest_requested_at IS NOT NULL "
                " AND retest_completed_at IS NULL"
            ), {"id": item_id})
            await session.commit()
            return (result.rowcount or 0) > 0
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_retest_complete_failed",
                    item_id=item_id, error=str(exc))
        return False
