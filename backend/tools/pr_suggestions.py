"""
tools/pr_suggestions.py — read/write helpers for pr_suggestions.

The webhook (Commit 2) populates the table; this module (Commit 3) is
the consumer side. Three operations the API endpoints in main.py
call into:

  list_pending_suggestions()  → the GET /suggestions response data.
                                Joins to test_results so each row
                                carries the failure context the
                                review modal needs.

  approve_suggestion(id, ...)  → flips a pending suggestion to
                                approved, writes the structured
                                resolution onto its failure via
                                resolve_failure, and auto-dismisses
                                any sibling pending suggestions for
                                the SAME failure_report_id (decision
                                point 4 — "cleaner queue, the audit
                                trail is preserved in the approved
                                suggestion record itself").

  dismiss_suggestion(id, ...)  → flips a pending suggestion to
                                dismissed; touches no test_results
                                row. The failure stays Open.

Every helper is fail-open at the table level — a database error
logs and returns the equivalent of "didn't happen" so the endpoint
can return a clean 4xx/5xx without leaking DB internals.

pending_count_by_failure() supports the row-badge query (Commit 5);
it returns {failure_id: count} so the frontend can render the
"[Fix available — review]" pill on matched failure rows in one
shot rather than per-row.
"""
from __future__ import annotations

import json
from typing import Any

import structlog

log = structlog.get_logger(__name__)


async def list_pending_suggestions() -> list[dict[str, Any]]:
    """
    Returns every `pending_review` suggestion joined to its failure
    row. The join is the whole point of the GET endpoint — without
    the failure metadata the review modal cannot render the "Failure
    half" of the card.

    The response intentionally returns raw script_id + step_id; the
    frontend resolves step_title and feature via the same helpers
    Failure Reports and the Issue Tracker use (stepTitle, the
    ROUTE_TO_FEATURE map). The mapping data lives in testScripts.ts
    and is awkward to maintain on the backend; this keeps it in one
    place.

    Ordering: newest pr_merged_at first — the modal queue surfaces
    the freshest PRs at the top so a reviewer sees today's work
    before historical entries.

    Fail-open → [].
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text("""
                SELECT s.id, s.failure_report_id,
                       s.pr_number, s.pr_title, s.pr_url,
                       s.pr_merged_at, s.pr_author,
                       s.matched_commit_shas, s.matched_on,
                       s.created_at,
                       t.script_id, t.step_id, t.user_email,
                       t.failure_description, t.actual_result,
                       t.severity, t.attested_at
                FROM pr_suggestions s
                JOIN test_results t ON s.failure_report_id = t.id
                WHERE s.suggestion_state = 'pending_review'
                ORDER BY s.pr_merged_at DESC, s.id DESC
            """))
            out: list[dict[str, Any]] = []
            for r in rows.fetchall():
                # matched_commit_shas is JSON in the column; asyncpg
                # may return it as a list already (driver default) OR
                # as a string (older drivers). Handle both so a
                # future driver swap doesn't silently break the
                # response shape.
                shas = r[7]
                if isinstance(shas, str):
                    try:
                        shas = json.loads(shas)
                    except json.JSONDecodeError:
                        shas = []
                out.append({
                    "suggestion_id":       r[0],
                    "failure_report_id":   r[1],
                    "pr_number":           r[2],
                    "pr_title":            r[3],
                    "pr_url":              r[4],
                    "pr_merged_at":        _iso(r[5]),
                    "pr_author":           r[6],
                    "matched_commit_shas": shas or [],
                    "matched_on":          r[8],
                    "created_at":          _iso(r[9]),
                    "failure": {
                        "id":                  r[1],
                        "script_id":           r[10],
                        "step_id":             r[11],
                        "user_email":          r[12],
                        "failure_description": r[13],
                        "actual_result":       r[14],
                        "severity":            r[15],
                        "attested_at":         _iso(r[16]),
                    },
                })
            return out
    except Exception as exc:  # noqa: BLE001
        log.warning("pr_suggestions_list_failed", error=str(exc))
        return []


async def get_suggestion(suggestion_id: int) -> dict[str, Any] | None:
    """Returns one suggestion (any state) by id, joined to its
    failure row. Used by approve/dismiss for the pre-flight check.
    Fail-open → None."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text("""
                SELECT s.id, s.failure_report_id, s.pr_number, s.pr_title,
                       s.pr_url, s.suggestion_state,
                       t.user_email, t.script_id, t.step_id
                FROM pr_suggestions s
                JOIN test_results t ON s.failure_report_id = t.id
                WHERE s.id = :id
            """), {"id": suggestion_id})
            found = row.fetchone()
            if not found:
                return None
            return {
                "id":                found[0],
                "failure_report_id": found[1],
                "pr_number":         found[2],
                "pr_title":          found[3],
                "pr_url":            found[4],
                "state":             found[5],
                "user_email":        found[6],
                "script_id":         found[7],
                "step_id":           found[8],
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("pr_suggestions_get_failed", error=str(exc))
        return None


async def approve_suggestion(
    suggestion_id: int, *, reviewed_by: str,
    root_cause: str, remediation_note: str,
    resolution_type: str = "code_fix_deployed",
) -> dict[str, Any] | None:
    """
    The end-to-end approve flow:

      1. Read the suggestion — must exist, must be in pending_review.
      2. Call resolve_failure with the structured resolution metadata.
         resolution_type defaults to 'code_fix_deployed' (the modal
         pre-selects it). fix_reference is set to "#{pr_number}" so
         the linkified ResolutionCard renders the PR link.
      3. Update the suggestion: state='approved', reviewed_at=now(),
         reviewed_by=session email.
      4. Auto-dismiss any OTHER pending suggestions for the same
         failure_report_id (decision point 4) — once a failure is
         resolved, sibling suggestions for it are stale by
         definition. The dismiss_reason explains the cascade.

    Returns a summary dict {failure_id, user_email, siblings_dismissed:
    list[int]} or None on a not-found / wrong-state suggestion. The
    handler maps None → 404 or 409.

    Notification firing: get_notifications derives the resolved_failures
    notice from test_results state, so the resolve_failure call in
    step 2 is what surfaces the "🔁 Fix ready" pill to the reporter on
    next login. No explicit notification code needed.
    """
    suggestion = await get_suggestion(suggestion_id)
    if not suggestion:
        log.warning("pr_suggestions_approve_not_found", id=suggestion_id)
        return None
    if suggestion["state"] != "pending_review":
        log.warning("pr_suggestions_approve_wrong_state",
                    id=suggestion_id, state=suggestion["state"])
        return None

    from tools.test_runner import resolve_failure
    resolved = await resolve_failure(
        suggestion["failure_report_id"],
        reviewed_by,
        root_cause,
        resolution_type=resolution_type,
        fix_reference=f"#{suggestion['pr_number']}",
        remediation_note=remediation_note,
    )
    if resolved is None:
        # The failure row vanished between the suggestion's creation
        # and this approve call (cascade-delete is the most likely
        # culprit). The suggestion's FK would have cascaded too, so
        # this is rare — but we surface it cleanly.
        log.warning("pr_suggestions_approve_resolve_failed",
                    id=suggestion_id,
                    failure_id=suggestion["failure_report_id"])
        return None

    siblings_dismissed: list[int] = []
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            # Mark THIS suggestion approved.
            await session.execute(text("""
                UPDATE pr_suggestions SET
                    suggestion_state = 'approved',
                    reviewed_at = now(),
                    reviewed_by = :by
                WHERE id = :id
            """), {"id": suggestion_id, "by": reviewed_by})

            # Auto-dismiss siblings (decision point 4). Returning the
            # sibling ids lets the frontend optimistically remove them
            # from the queue display without a full reload.
            sib = await session.execute(text("""
                UPDATE pr_suggestions SET
                    suggestion_state = 'dismissed',
                    reviewed_at = now(),
                    reviewed_by = :by,
                    dismiss_reason = :reason
                WHERE failure_report_id = :fid
                  AND suggestion_state = 'pending_review'
                  AND id <> :id
                RETURNING id
            """), {
                "id": suggestion_id,
                "fid": suggestion["failure_report_id"],
                "by": reviewed_by,
                "reason":
                    f"Auto-dismissed when suggestion {suggestion_id} "
                    f"(PR #{suggestion['pr_number']}) was approved.",
            })
            siblings_dismissed = [int(row[0]) for row in sib.fetchall()]
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("pr_suggestions_approve_finalise_failed",
                    id=suggestion_id, error=str(exc))
        # The resolve_failure call above DID land — we don't want to
        # roll that back. Return what we know.
        return {
            "failure_id":          suggestion["failure_report_id"],
            "user_email":          resolved.get("user_email"),
            "siblings_dismissed":  [],
        }

    log.info("pr_suggestions_approved",
             id=suggestion_id,
             failure_id=suggestion["failure_report_id"],
             pr_number=suggestion["pr_number"],
             siblings_dismissed=siblings_dismissed)
    return {
        "failure_id":          suggestion["failure_report_id"],
        "user_email":          resolved.get("user_email"),
        "siblings_dismissed":  siblings_dismissed,
    }


async def dismiss_suggestion(
    suggestion_id: int, *, reviewed_by: str,
    dismiss_reason: str | None = None,
) -> bool:
    """
    Marks a pending suggestion dismissed. Returns True when the
    UPDATE affected a row, False otherwise (suggestion not found OR
    not in pending_review). The failure report is NOT touched —
    dismissal is purely a queue action.

    Fail-open → False.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return False
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("""
                UPDATE pr_suggestions SET
                    suggestion_state = 'dismissed',
                    reviewed_at = now(),
                    reviewed_by = :by,
                    dismiss_reason = :reason
                WHERE id = :id AND suggestion_state = 'pending_review'
            """), {
                "id": suggestion_id,
                "by": reviewed_by,
                "reason": dismiss_reason,
            })
            await session.commit()
            ok = (result.rowcount or 0) > 0
            if ok:
                log.info("pr_suggestions_dismissed",
                         id=suggestion_id, reason=dismiss_reason)
            return ok
    except Exception as exc:  # noqa: BLE001
        log.warning("pr_suggestions_dismiss_failed",
                    id=suggestion_id, error=str(exc))
        return False


async def pending_count_by_failure() -> dict[int, int]:
    """
    Returns {failure_id: pending_count} for every failure that has at
    least one pending suggestion. Backs the Failure Reports row-badge
    rendering in Commit 5 — the frontend fetches this once on load
    and joins it onto the failure list to decide which rows show the
    "[Fix available — review]" pill.

    Fail-open → {}.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return {}
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text("""
                SELECT failure_report_id, COUNT(*) AS n
                FROM pr_suggestions
                WHERE suggestion_state = 'pending_review'
                GROUP BY failure_report_id
            """))
            return {int(r[0]): int(r[1]) for r in rows.fetchall()}
    except Exception as exc:  # noqa: BLE001
        log.warning("pr_suggestions_count_failed", error=str(exc))
        return {}


def _iso(value: Any) -> str | None:
    """ISO format for any value with isoformat(); None passes through.
    Pure helper kept local to avoid importing test_runner just for it."""
    if value is None:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)
