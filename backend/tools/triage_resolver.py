"""
tools/triage_resolver.py — programmatic resolution of triage items.

Claude Code calls this helper from the end of every fix prompt that
addresses triage items. It marks each item resolved (resolved_at,
resolved_by="claude_code", resolution_note, fix_commit), and — when
requires_retest is True — stamps retest_requested_at so the original
reporter sees a "Fix ready for retest" notification on next login.

The notification surface is NOT a separate table. test_runner's
get_notifications already derives "resolved_failures" and
"responded_feedback" from existing state; this commit extends it to
also derive "retest_requested" from triage_report_items where
requires_retest=true AND retest_completed_at IS NULL AND the source
UAT row's user_email matches the requesting user. The frontend
TestNotifications component picks up the new entry kind in Commit 5.

The function is async and reuses tools.triage_engine.resolve_triage_item
for the DB update, so the same workflow path runs whether the
resolution comes from a sysadmin clicking the UI button (which hits
the /resolve endpoint that also calls resolve_triage_item) or from
Claude Code calling resolve_triage_items() directly. Fail-open per
item: if one update fails the rest still attempt.

Usage from a fix prompt's end:

  from tools.triage_resolver import resolve_triage_items
  await resolve_triage_items(
      item_ids=[42, 43, 44],
      resolution_note="Fixed the regime-switching panel — DataExplain "
                      "now carries Ask the Council. CSS overflow on "
                      "tooltips moved to a portal.",
      fix_commit="21619d3",
      requires_retest=True,
  )
"""
from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger(__name__)


# The resolved_by value stamped on every row this helper touches. Tells
# the audit trail "Claude Code applied this fix" vs. a sysadmin clicking
# the UI button (which stamps the sysadmin's email). The team activity
# breakdown can filter by this string when reporting AI vs. human
# resolution rates for the final-presentation narrative.
CLAUDE_CODE_AUTHOR = "claude_code"


async def resolve_triage_items(
    item_ids: list[int], *,
    resolution_note: str,
    fix_commit: str,
    requires_retest: bool = True,
) -> dict[str, Any]:
    """
    Marks every triage item in `item_ids` resolved. Returns a small
    summary: {resolved, failed, notified_reporters, item_titles}.

    resolution_note — one paragraph naming what was fixed. Surfaces in
      the frontend resolution UI (Settings → Triage Reports) and, when
      requires_retest=True, in the reporter's "Fix ready for retest"
      notification body.
    fix_commit — the git commit SHA the fix landed in. Renders as a
      clickable GitHub commit link in the resolution UI.
    requires_retest — True when the fix is functional and the reporter
      should re-run the UAT step that surfaced the bug. False for
      cosmetic / documentation fixes that don't change behaviour.

    Fail-open per item — a missing item or DB error on one row logs
    the failure and continues. The summary's `failed` list carries
    every item id that did not resolve cleanly.
    """
    from tools.triage_engine import resolve_triage_item

    resolved: list[int] = []
    failed: list[int] = []
    notified: list[str] = []
    item_titles: list[str] = []

    for item_id in item_ids:
        result = await resolve_triage_item(
            item_id,
            resolved_by=CLAUDE_CODE_AUTHOR,
            resolution_note=resolution_note,
            fix_commit=fix_commit,
            requires_retest=requires_retest,
        )
        if result is None:
            failed.append(item_id)
            log.warning("triage_resolver_item_missing", item_id=item_id)
            continue
        resolved.append(item_id)
        item_titles.append(str(result.get("item_title") or f"item #{item_id}"))
        # Per-item resolution log — formatted as the spec asked.
        log.info(
            "triage_resolved",
            item_id=item_id,
            note=resolution_note,
            commit=(fix_commit[:8] if fix_commit else None),
            requires_retest=bool(result.get("requires_retest")),
        )
        # Find the reporter and stage the notification path for the
        # frontend. The notification itself is derived (no inserts) —
        # test_runner.get_notifications surfaces it from the
        # triage_report_items row plus the source UAT row's user_email.
        if result.get("requires_retest"):
            reporter = await _reporter_for_source(
                result.get("source_item_type"),
                result.get("source_item_id"),
            )
            if reporter:
                notified.append(reporter)

    log.info("triage_resolver_run_complete",
             resolved=len(resolved), failed=len(failed),
             notified=sorted(set(notified)),
             fix_commit=(fix_commit[:8] if fix_commit else None))
    return {
        "resolved": resolved,
        "failed": failed,
        "notified_reporters": sorted(set(notified)),
        "item_titles": item_titles,
    }


async def _reporter_for_source(
    source_type: str | None, source_id: int | None,
) -> str | None:
    """
    Looks up the user_email on the originating test_results /
    test_feedback row. Used by resolve_triage_items to know which
    tester should see the "Fix ready for retest" notification.

    Patterns aren't backed by a single source row, so source_type is
    None for those — function returns None and no notification fires.
    Fail-open: a missing row or DB error returns None.
    """
    if not source_type or not isinstance(source_id, int):
        return None
    table = ("test_results" if source_type == "failure"
              else "test_feedback" if source_type == "feedback"
              else None)
    if table is None:
        return None
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                f"SELECT user_email FROM {table} WHERE id = :id"
            ), {"id": source_id})
            found = row.fetchone()
            return found[0] if found and found[0] else None
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_reporter_lookup_failed",
                    source_type=source_type, source_id=source_id,
                    error=str(exc))
        return None
