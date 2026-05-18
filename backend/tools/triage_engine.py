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
from datetime import date, datetime, timezone
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Deadline the triage agent reasons against — the FNA 670 midpoint.
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
    run does not re-assess them. Failures carry no status column — the
    threshold trigger time-scopes them instead. Fail-open."""
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


# ── Step 1 — gather unaddressed items ─────────────────────────────────────────

async def _gather_unaddressed() -> tuple[list[dict], list[dict]]:
    """The current open backlog — unresolved failures and new feedback.
    Reuses the test_runner read layer (both fail-open to [])."""
    from tools.test_runner import get_all_failures, get_all_feedback

    failures = [f for f in await get_all_failures()
                if not f.get("resolved_at")]
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
    "The platform is being evaluated at the McColl School of Business on "
    "June 3rd.\n\n"
    "Produce a structured triage report with EXACTLY these five markdown "
    "sections, in this order:\n\n"
    "## IMMEDIATE ACTIONS\n"
    "Items that must be addressed before June 3rd — blocking or major "
    "severity, or anything that would embarrass the team during the "
    "presentation. For each: title, the reason it is immediate, a "
    "recommended fix approach, and an effort estimate.\n\n"
    "## QUICK WINS\n"
    "Small-effort items (Trivial/Small) that would noticeably improve the "
    "platform. For each: title, what it improves, and an effort estimate.\n\n"
    "## PATTERNS AND THEMES\n"
    "Where multiple items report the same underlying issue, group them and "
    "name the root cause. List the affected items.\n\n"
    "## POST-DEADLINE BACKLOG\n"
    "Everything else — valid requests for after June 3rd, grouped by "
    "category.\n\n"
    "## SUMMARY\n"
    "Total items reviewed; counts of immediate actions, quick wins, "
    "patterns and post-deadline items; and the recommended focus for the "
    "days before the deadline.\n\n"
    "Be direct and specific. Reference item titles and descriptions by "
    "name. Do not pad with generic advice."
)


def _triage_user_message(failures: list[dict], feedback: list[dict]) -> str:
    days = max(0, (_DEADLINE - date.today()).days)
    return (
        f"Today is {date.today().isoformat()}. You have {days} days until "
        f"the June 3rd deadline.\n\n"
        f"Here are all {len(failures) + len(feedback)} unaddressed items:\n\n"
        f"{_build_context(failures, feedback)}"
    )


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


def _generate_triage_report(failures: list[dict], feedback: list[dict]) -> str:
    """Runs the triage agent through the generator-evaluator harness.
    Falls back to the deterministic report in the test environment, with
    no API key, or on any agent error."""
    user_message = _triage_user_message(failures, feedback)
    if _is_test_env() or not os.getenv("ANTHROPIC_API_KEY"):
        return _mock_triage_report(failures, feedback)
    try:
        from agents.base import SONNET_MODEL, call_claude
        from agents.evaluator_prompts import triage_evaluator_prompt
        from agents.harness import GeneratorEvaluatorHarness

        def _generate(prompt: str) -> str:
            return call_claude(SONNET_MODEL, _TRIAGE_SYSTEM_PROMPT, prompt,
                               max_tokens=4000)

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
        # Steps 2-3 — context + agent report.
        report_text = _generate_triage_report(failures, feedback)
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
        # Step 6 — mark the assessed feedback triaged.
        await _mark_feedback_triaged([f["id"] for f in feedback])
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
