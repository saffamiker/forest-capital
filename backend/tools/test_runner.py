"""
tools/test_runner.py

Data and AI layer behind the guided UAT test runner — everything the
/api/v1/testing/* endpoints in main.py need that is not routing.

Two halves:

  Persistence — async CRUD against test_results and test_feedback
  (migration 014). Every function is fail-open: a DB error is logged and
  swallowed, never raised into an endpoint response, so test logging can
  never block the UI.

  AI helpers — quality_check() scores a failure report or feedback
  submission before it is stored (the quality gate); categorize_feedback()
  classifies a feedback submission. Both call claude-sonnet-4-6 and are
  fail-open: on any error — including the test environment, where no API
  key is configured — quality_check passes the submission and
  categorize_feedback returns empty categorisation.

Screenshots: stored to the local uploads directory, never as BLOBs; the
DB holds only relative paths. config.SCREENSHOT_DIR is /data/test_screenshots
on Render (a persistent disk — survives redeployments) and
backend/data/test_screenshots in local development. The attestation
row remains the durable record (result, description, severity,
timestamps); screenshots are supporting evidence.

Cleanup — cleanup_old_screenshots() runs from the application lifespan
on every cold start and drops files older than 30 days, so the disk
does not grow forever. delete_screenshots(paths) is a per-row helper
for an explicit delete (no production code path deletes test_results
or test_feedback rows today; the helper is provided so a future delete
endpoint can keep the disk in sync with the database).
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

ADMIN_EMAIL = "ruurdsm@queens.edu"

# ── Screenshot storage ────────────────────────────────────────────────────────
# config.SCREENSHOT_DIR is /data/test_screenshots on Render (a persistent
# disk — survives redeployments) and backend/data/test_screenshots in
# local development. Served read-only via the /uploads StaticFiles mount
# in main.py. The DB stores the relative path "test_screenshots/<uuid>"
# so the /uploads mount (rooted one level above SCREENSHOT_DIR) resolves it.
from config import SCREENSHOT_DIR

_SCREENSHOT_SUBDIR = "test_screenshots"
_ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif"}
_MAX_SCREENSHOTS = 3


def save_screenshots(files: list[tuple[str, bytes]]) -> list[str]:
    """
    Writes up to three uploaded images to the persistent screenshot
    directory and returns their relative paths (e.g.
    "test_screenshots/<uuid>.png").

    files: (filename, bytes) pairs. Fail-open — if the directory cannot be
    written the result is simply stored without screenshots; a failure
    here must never block an attestation.
    """
    saved: list[str] = []
    try:
        dest = Path(SCREENSHOT_DIR)
        dest.mkdir(parents=True, exist_ok=True)
        for filename, content in files[:_MAX_SCREENSHOTS]:
            ext = Path(filename or "").suffix.lower()
            if ext not in _ALLOWED_IMAGE_EXT or not content:
                continue
            fname = f"{uuid.uuid4().hex}{ext}"
            (dest / fname).write_bytes(content)
            saved.append(f"{_SCREENSHOT_SUBDIR}/{fname}")
    except Exception as exc:  # noqa: BLE001
        log.warning("test_screenshot_save_failed", error=str(exc))
    return saved


# Screenshots older than this on startup are unlinked. 30 days matches
# the typical UAT cycle plus a one-cycle buffer — old enough that
# anything still attached to an open failure report would already have
# been resolved.
_SCREENSHOT_RETENTION_DAYS = 30


def cleanup_old_screenshots() -> tuple[int, int]:
    """
    Drops every file under SCREENSHOT_DIR whose mtime is older than
    _SCREENSHOT_RETENTION_DAYS. Returns (deleted, remaining) — counts of
    files removed and files still present after the sweep.

    Fail-open — a missing or unreadable directory returns (0, 0) and
    logs a warning; never raises. The caller (the lifespan startup
    hook) treats the result as informational, not a gate.
    """
    import time
    deleted = 0
    remaining = 0
    try:
        dest = Path(SCREENSHOT_DIR)
        if not dest.exists():
            return 0, 0
        cutoff = time.time() - _SCREENSHOT_RETENTION_DAYS * 86400
        for entry in dest.iterdir():
            if not entry.is_file():
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink()
                    deleted += 1
                else:
                    remaining += 1
            except OSError as exc:
                # A file we can't stat or unlink — log and skip, do not
                # abort the sweep. Treat as remaining (it survived).
                log.warning("test_screenshot_skip", file=entry.name,
                            error=str(exc))
                remaining += 1
    except Exception as exc:  # noqa: BLE001
        log.warning("test_screenshot_cleanup_failed", error=str(exc))
    return deleted, remaining


def delete_screenshots(paths: list[str] | None) -> int:
    """
    Removes the on-disk files for the given DB-stored relative paths
    (e.g. ["test_screenshots/<uuid>.png", ...]). Returns the number of
    files actually deleted.

    A row's screenshot paths sit on disk under SCREENSHOT_DIR's parent
    (see the /uploads StaticFiles mount in main.py — rooted one level
    above SCREENSHOT_DIR so the "test_screenshots/<uuid>" prefix
    resolves). The per-row delete helper for any future endpoint that
    removes test_results / test_feedback rows; fail-open per file so
    a missing or already-deleted file is not an error.
    """
    if not paths:
        return 0
    parent = Path(SCREENSHOT_DIR).parent
    removed = 0
    for relpath in paths:
        try:
            target = parent / str(relpath)
            # Refuse to follow a path that escapes SCREENSHOT_DIR — the
            # stored prefix is "test_screenshots/<uuid>", any ".." or
            # absolute-path argument is a misuse.
            if not str(target.resolve()).startswith(
                    str(Path(SCREENSHOT_DIR).resolve())):
                continue
            if target.exists() and target.is_file():
                target.unlink()
                removed += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("test_screenshot_delete_failed",
                        path=str(relpath), error=str(exc))
    return removed


# ── test_results persistence ──────────────────────────────────────────────────

async def record_result(
    *,
    user_email: str,
    session_type: str,
    script_id: str,
    step_id: str,
    result: str,
    notes: str | None = None,
    failure_description: str | None = None,
    expected_result: str | None = None,
    actual_result: str | None = None,
    severity: str | None = None,
    browser_info: str | None = None,
    screenshot_paths: list[str] | None = None,
    low_quality: bool = False,
    override_reason: str | None = None,
) -> dict[str, Any] | None:
    """
    Upserts one attested test-step result on (user_email, script_id,
    step_id). A re-attestation overwrites the row and flips `overridden`
    true so the audit trail records that the result was revised. Returns
    the stored row, or None when the database is unavailable.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text("""
                INSERT INTO test_results (
                    user_email, script_id, step_id, result, notes,
                    failure_description, expected_result, actual_result,
                    severity, browser_info, screenshot_paths, low_quality,
                    override_reason, session_type, attested_at, overridden)
                VALUES (
                    :user_email, :script_id, :step_id, :result, :notes,
                    :failure_description, :expected_result, :actual_result,
                    :severity, :browser_info, :screenshot_paths, :low_quality,
                    :override_reason, :session_type, now(), false)
                ON CONFLICT (user_email, script_id, step_id) DO UPDATE SET
                    result = EXCLUDED.result,
                    notes = EXCLUDED.notes,
                    failure_description = EXCLUDED.failure_description,
                    expected_result = EXCLUDED.expected_result,
                    actual_result = EXCLUDED.actual_result,
                    severity = EXCLUDED.severity,
                    browser_info = EXCLUDED.browser_info,
                    screenshot_paths = EXCLUDED.screenshot_paths,
                    low_quality = EXCLUDED.low_quality,
                    override_reason = EXCLUDED.override_reason,
                    session_type = EXCLUDED.session_type,
                    attested_at = now(),
                    overridden = true,
                    -- Resolution-field carry-over for the Issue Tracker's
                    -- "Passed" terminal state. Re-attesting as PASS after
                    -- a resolution preserves the resolution evidence so the
                    -- tracker can render the row as Passed (with the
                    -- resolution metadata still on it). Re-attesting as
                    -- FAIL — a regression — clears the prior resolution
                    -- so the row appears as a fresh Open failure.
                    -- See compute_issue_status() for the consumer logic.
                    resolved_at = CASE WHEN EXCLUDED.result = 'fail'
                        THEN NULL ELSE test_results.resolved_at END,
                    resolved_by = CASE WHEN EXCLUDED.result = 'fail'
                        THEN NULL ELSE test_results.resolved_by END,
                    resolution_note = CASE WHEN EXCLUDED.result = 'fail'
                        THEN NULL ELSE test_results.resolution_note END,
                    resolution_type = CASE WHEN EXCLUDED.result = 'fail'
                        THEN NULL ELSE test_results.resolution_type END,
                    fix_reference = CASE WHEN EXCLUDED.result = 'fail'
                        THEN NULL ELSE test_results.fix_reference END,
                    remediation_note = CASE WHEN EXCLUDED.result = 'fail'
                        THEN NULL ELSE test_results.remediation_note END
                RETURNING id, result, severity, attested_at, overridden
            """), {
                "user_email": user_email, "script_id": script_id,
                "step_id": step_id, "result": result, "notes": notes,
                "failure_description": failure_description,
                "expected_result": expected_result,
                "actual_result": actual_result, "severity": severity,
                "browser_info": browser_info,
                "screenshot_paths": screenshot_paths,
                "low_quality": low_quality, "override_reason": override_reason,
                "session_type": session_type,
            })
            stored = row.fetchone()
            await session.commit()
            if stored:
                return {
                    "id": stored[0], "result": stored[1],
                    "severity": stored[2], "attested_at": _iso(stored[3]),
                    "overridden": stored[4],
                }
    except Exception as exc:  # noqa: BLE001
        log.warning("test_result_record_failed", error=str(exc))
    return None


async def get_results(user_email: str) -> list[dict[str, Any]]:
    """Every test_results row for one user, newest attestation first."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text("""
                SELECT script_id, step_id, result, severity,
                       failure_description, screenshot_paths, attested_at,
                       overridden, resolved_at, resolution_note, low_quality
                FROM test_results WHERE user_email = :e
                ORDER BY attested_at DESC
            """), {"e": user_email})
            return [{
                "script_id": r[0], "step_id": r[1], "result": r[2],
                "severity": r[3], "failure_description": r[4],
                "screenshot_paths": list(r[5]) if r[5] else [],
                "attested_at": _iso(r[6]), "overridden": r[7],
                "resolved_at": _iso(r[8]), "resolution_note": r[9],
                "low_quality": r[10],
            } for r in rows.fetchall()]
    except Exception as exc:  # noqa: BLE001
        log.warning("test_results_read_failed", error=str(exc))
        return []


async def get_summary(user_email: str) -> dict[str, dict[str, int]]:
    """
    Per-script attested-result counts for one user — {script_id:
    {pass, fail, skip}}. The frontend, which owns testScripts.ts, derives
    the total and pending counts from its own step inventory.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return {}
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text("""
                SELECT script_id, result, COUNT(*)
                FROM test_results WHERE user_email = :e
                GROUP BY script_id, result
            """), {"e": user_email})
            out: dict[str, dict[str, int]] = {}
            for script_id, result, count in rows.fetchall():
                bucket = out.setdefault(
                    script_id, {"pass": 0, "fail": 0, "skip": 0})
                if result in bucket:
                    bucket[result] = int(count)
            return out
    except Exception as exc:  # noqa: BLE001
        log.warning("test_summary_read_failed", error=str(exc))
        return {}


async def get_unseen(user_email: str) -> dict[str, Any]:
    """
    Per-script attested-step inventory for one user — the data the
    frontend's login-notification check diffs against testScripts.ts to
    surface scripts with new or changed (un-attested) steps.
    """
    try:
        from sqlalchemy import text

        from config import TEST_SCRIPT_VERSION
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return {"test_script_version": TEST_SCRIPT_VERSION, "scripts": {}}
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text("""
                SELECT script_id, step_id, attested_at, resolved_at,
                       resolution_type
                FROM test_results WHERE user_email = :e
            """), {"e": user_email})
            scripts: dict[str, dict[str, Any]] = {}
            for (script_id, step_id, attested_at, resolved_at,
                 resolution_type) in rows.fetchall():
                s = scripts.setdefault(
                    script_id, {"attested_step_ids": [], "last_attested_at": None})
                # A resolved failure is pending re-test — not "attested" —
                # EXCEPT for 'wont_fix' resolutions (Migration 025) which
                # close the item without prompting a re-test. The step
                # stays attested at whatever the tester last submitted.
                if resolved_at is None or resolution_type == "wont_fix":
                    s["attested_step_ids"].append(step_id)
                s["last_attested_at"] = _max_iso(
                    s["last_attested_at"], _iso(attested_at))
            return {"test_script_version": TEST_SCRIPT_VERSION,
                    "scripts": scripts}
    except Exception as exc:  # noqa: BLE001
        log.warning("test_unseen_read_failed", error=str(exc))
        from config import TEST_SCRIPT_VERSION
        return {"test_script_version": TEST_SCRIPT_VERSION, "scripts": {}}


async def get_all_failures() -> list[dict[str, Any]]:
    """Every failed step across all testers — admin failure-reports view.

    Returns the three new migration-023 columns (github_issue_number,
    github_issue_url, triaged_at) alongside the existing ones so the
    triage engine's _gather_unaddressed can filter by triaged_at and
    the frontend can display the GitHub linkage."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text("""
                SELECT id, user_email, script_id, step_id, failure_description,
                       expected_result, actual_result, severity, browser_info,
                       screenshot_paths, low_quality, attested_at,
                       resolved_at, resolved_by, resolution_note,
                       github_issue_number, github_issue_url, triaged_at,
                       resolution_type, fix_reference, remediation_note
                FROM test_results WHERE result = 'fail'
                ORDER BY
                    CASE severity WHEN 'blocking' THEN 0 WHEN 'major' THEN 1
                                  WHEN 'minor' THEN 2 ELSE 3 END,
                    attested_at DESC
            """))
            return [{
                "id": r[0], "user_email": r[1], "script_id": r[2],
                "step_id": r[3], "failure_description": r[4],
                "expected_result": r[5], "actual_result": r[6],
                "severity": r[7], "browser_info": r[8],
                "screenshot_paths": list(r[9]) if r[9] else [],
                "low_quality": r[10], "attested_at": _iso(r[11]),
                "resolved_at": _iso(r[12]), "resolved_by": r[13],
                "resolution_note": r[14],
                "github_issue_number": r[15],
                "github_issue_url": r[16],
                "triaged_at": _iso(r[17]),
                # Migration 025 — resolution-gate metadata.
                "resolution_type": r[18],
                "fix_reference": r[19],
                "remediation_note": r[20],
            } for r in rows.fetchall()]
    except Exception as exc:  # noqa: BLE001
        log.warning("test_failures_read_failed", error=str(exc))
        return []


# Resolution-type vocabulary (migration 025). The CHECK constraint on
# test_results.resolution_type names the same three values — keep these
# tuples in sync if the vocabulary ever changes.
RESOLUTION_TYPES: tuple[str, ...] = (
    "no_bug_detected", "code_fix_deployed", "wont_fix",
)


# Issue Tracker status vocabulary (Prompt B Part 6). Computed from the
# row's current state; no separate column — the four states are a
# function of (result, resolved_at, resolution_type). See
# compute_issue_status() below.
ISSUE_STATUS = ("open", "pending_retest", "passed", "closed")


def compute_issue_status(row: dict[str, Any]) -> str:
    """
    Maps a test_results row to one of the four Issue Tracker statuses.

      open           — failure reported, no resolution recorded yet
      pending_retest — resolved with type ∈ (no_bug_detected,
                       code_fix_deployed) and the tester has not yet
                       re-attested. Step is in the tester's re-test queue.
      passed         — tester re-attested as PASS after a resolution.
                       record_result preserves the resolution fields on
                       a fail → pass transition (see the UPSERT comment),
                       which is what makes this state observable.
      closed         — resolution_type = 'wont_fix'. The step stays at
                       its current attested state; no re-test prompt.

    The mapping is order-sensitive: the `open` guard runs first because
    a row with resolved_at IS NULL can never have a resolution_type;
    the `closed` check beats the `passed` check because a wont_fix
    that was somehow re-attested as pass should still read as closed.
    """
    if row.get("resolved_at") is None:
        return "open"
    if row.get("resolution_type") == "wont_fix":
        return "closed"
    if row.get("result") == "pass":
        return "passed"
    return "pending_retest"


async def get_issue_tracker_rows() -> list[dict[str, Any]]:
    """
    Every row that has ever failed — the Issue Tracker scope.
    Includes the currently-failing rows (Open / Pending re-test /
    Closed) AND the rows that previously failed but have been
    re-attested as Pass (the Passed terminal state). A row that has
    only ever passed is NOT included; the tracker is about issues,
    not the full attestation history.

    Each row carries every column the tracker UI renders plus the
    computed `status` from compute_issue_status(). Fail-open → [].
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text("""
                SELECT id, user_email, script_id, step_id, result,
                       failure_description, severity, attested_at,
                       resolved_at, resolved_by, resolution_note,
                       resolution_type, fix_reference, remediation_note,
                       github_issue_number, github_issue_url
                FROM test_results
                WHERE result = 'fail' OR resolved_at IS NOT NULL
                ORDER BY attested_at DESC
            """))
            out: list[dict[str, Any]] = []
            for r in rows.fetchall():
                row = {
                    "id": r[0], "user_email": r[1], "script_id": r[2],
                    "step_id": r[3], "result": r[4],
                    "failure_description": r[5], "severity": r[6],
                    "attested_at": _iso(r[7]),
                    "resolved_at": _iso(r[8]), "resolved_by": r[9],
                    "resolution_note": r[10],
                    "resolution_type": r[11],
                    "fix_reference": r[12],
                    "remediation_note": r[13],
                    "github_issue_number": r[14],
                    "github_issue_url": r[15],
                }
                row["status"] = compute_issue_status(row)
                out.append(row)
            return out
    except Exception as exc:  # noqa: BLE001
        log.warning("issue_tracker_read_failed", error=str(exc))
        return []


async def resolve_failure(
    failure_id: int, resolved_by: str, resolution_note: str,
    *,
    resolution_type: str,
    fix_reference: str | None = None,
    remediation_note: str | None = None,
) -> dict[str, Any] | None:
    """
    Marks a failed step resolved. The row is kept (the resolution is the
    audit trail) with resolved_at/by/note set plus the migration-025
    metadata (resolution_type, fix_reference, remediation_note).

    For resolution_type in ('no_bug_detected', 'code_fix_deployed') the
    frontend treats the row as a pending re-test — the step re-appears
    for the tester via get_notifications.resolved_failures. For
    'wont_fix' the step is NOT reset: the tester sees an informational
    "closed" card (no CTA) and the step's attestation is left as-is.

    Returns {user_email, script_id, step_id, resolution_type} for the
    notification queue, or None when no row matched (404 path).
    """
    if resolution_type not in RESOLUTION_TYPES:
        # Defence in depth — the endpoint already validates this, but
        # a direct caller (a future test or admin script) must hit the
        # same gate or the CHECK constraint would raise instead.
        log.warning("resolve_failure_invalid_type", type=resolution_type)
        return None
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text("""
                UPDATE test_results
                SET resolved_at = now(), resolved_by = :by,
                    resolution_note = :note,
                    resolution_type = :rtype,
                    fix_reference = :fix_ref,
                    remediation_note = :remed
                WHERE id = :id AND result = 'fail'
                RETURNING user_email, script_id, step_id
            """), {
                "id": failure_id, "by": resolved_by, "note": resolution_note,
                "rtype": resolution_type, "fix_ref": fix_reference,
                "remed": remediation_note,
            })
            found = row.fetchone()
            await session.commit()
            if found:
                return {
                    "user_email": found[0],
                    "script_id": found[1],
                    "step_id": found[2],
                    "resolution_type": resolution_type,
                }
    except Exception as exc:  # noqa: BLE001
        log.warning("test_failure_resolve_failed", error=str(exc))
    return None


# ── test_feedback persistence ─────────────────────────────────────────────────

async def submit_feedback(
    *,
    user_email: str,
    script_id: str | None,
    step_id: str | None,
    source_route: str | None,
    feedback_type: str,
    title: str,
    description: str,
    priority: str | None,
    screenshot_paths: list[str] | None,
    browser_info: str | None,
    low_quality: bool,
    ai: dict[str, Any],
) -> dict[str, Any] | None:
    """Inserts one feedback row with its AI categorisation. Returns the
    stored row (including the categorisation) or None."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text("""
                INSERT INTO test_feedback (
                    user_email, script_id, step_id, source_route,
                    feedback_type, title, description, priority,
                    screenshot_paths, browser_info, low_quality,
                    ai_category, ai_severity, ai_effort_estimate, ai_tags,
                    ai_summary, ai_confidence)
                VALUES (
                    :user_email, :script_id, :step_id, :source_route,
                    :feedback_type, :title, :description, :priority,
                    :screenshot_paths, :browser_info, :low_quality,
                    :ai_category, :ai_severity, :ai_effort_estimate, :ai_tags,
                    :ai_summary, :ai_confidence)
                RETURNING id, submitted_at, status
            """), {
                "user_email": user_email, "script_id": script_id,
                "step_id": step_id, "source_route": source_route,
                "feedback_type": feedback_type, "title": title,
                "description": description, "priority": priority,
                "screenshot_paths": screenshot_paths,
                "browser_info": browser_info, "low_quality": low_quality,
                "ai_category": ai.get("category"),
                "ai_severity": ai.get("severity"),
                "ai_effort_estimate": ai.get("effort_estimate"),
                "ai_tags": ai.get("tags"),
                "ai_summary": ai.get("summary"),
                "ai_confidence": ai.get("ai_confidence"),
            })
            stored = row.fetchone()
            await session.commit()
            if stored:
                return {
                    "id": stored[0], "submitted_at": _iso(stored[1]),
                    "status": stored[2],
                    "ai_category": ai.get("category"),
                    "ai_severity": ai.get("severity"),
                    "ai_effort_estimate": ai.get("effort_estimate"),
                    "ai_tags": ai.get("tags") or [],
                    "ai_summary": ai.get("summary"),
                    "ai_confidence": ai.get("ai_confidence"),
                    "low_quality": low_quality,
                }
    except Exception as exc:  # noqa: BLE001
        log.warning("test_feedback_submit_failed", error=str(exc))
    return None


async def get_all_feedback(filters: dict[str, Any]) -> list[dict[str, Any]]:
    """Every feedback row, newest first, with optional column filters —
    the admin feedback-backlog view."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        clauses, params = [], {}
        for col, key in [("ai_category", "category"), ("ai_severity", "severity"),
                         ("ai_effort_estimate", "effort"), ("status", "status"),
                         ("user_email", "user_email")]:
            if filters.get(key):
                clauses.append(f"{col} = :{key}")
                params[key] = filters[key]
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text(f"""
                SELECT id, user_email, script_id, step_id, source_route,
                       feedback_type, title, description, priority,
                       screenshot_paths, low_quality, ai_category, ai_severity,
                       ai_effort_estimate, ai_tags, ai_summary, ai_confidence,
                       status, resolution_note, resolved_by, resolved_at,
                       submitted_at
                FROM test_feedback{where}
                ORDER BY submitted_at DESC
            """), params)
            return [{
                "id": r[0], "user_email": r[1], "script_id": r[2],
                "step_id": r[3], "source_route": r[4], "feedback_type": r[5],
                "title": r[6], "description": r[7], "priority": r[8],
                "screenshot_paths": list(r[9]) if r[9] else [],
                "low_quality": r[10], "ai_category": r[11], "ai_severity": r[12],
                "ai_effort_estimate": r[13], "ai_tags": list(r[14]) if r[14] else [],
                "ai_summary": r[15], "ai_confidence": r[16], "status": r[17],
                "resolution_note": r[18], "resolved_by": r[19],
                "resolved_at": _iso(r[20]), "submitted_at": _iso(r[21]),
            } for r in rows.fetchall()]
    except Exception as exc:  # noqa: BLE001
        log.warning("test_feedback_read_failed", error=str(exc))
        return []


async def resolve_feedback(
    feedback_id: int, status: str, resolution_note: str | None, resolved_by: str,
) -> dict[str, Any] | None:
    """Updates a feedback row's status. Returns {user_email, title, status}
    for the notification queue, or None when the row does not exist.

    Split into a SELECT-then-UPDATE pair on purpose. The earlier shape used
    UPDATE ... RETURNING + Result.fetchone(), which returns None silently in
    production (sqlalchemy 2.0 + asyncpg surfaces RETURNING rows via
    `.returned_defaults` / iteration in some configurations, not fetchone) —
    leaving every resolve attempt 404-ing despite the row existing. The
    SELECT pre-flight is bulletproof: a missing row returns None up-front
    without an UPDATE, a present row drives an UPDATE-without-RETURNING.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            existing = await session.execute(
                text("SELECT user_email, title FROM test_feedback WHERE id = :id"),
                {"id": feedback_id},
            )
            preflight = existing.fetchone()
            if preflight is None:
                return None
            # `status` is bound twice on different sides: as the value
            # for a VARCHAR(20) column AND inside a `IN ('text','text')`
            # text comparison. asyncpg's prepared-statement type
            # inference deduces ONE type per $N placeholder across the
            # full statement, so a single shared :status raises
            # AmbiguousParameterError (text vs character varying). Pass
            # the value as two separately-named parameters so each $N
            # lives in a single-type context — solved.
            sets_resolved_at = status in ("resolved", "wont_do")
            await session.execute(text("""
                UPDATE test_feedback
                SET status = :status, resolution_note = :note,
                    resolved_by = :by,
                    resolved_at = CASE WHEN :sets_resolved_at
                                       THEN now() ELSE resolved_at END
                WHERE id = :id
            """), {"id": feedback_id, "status": status,
                   "note": resolution_note, "by": resolved_by,
                   "sets_resolved_at": sets_resolved_at})
            await session.commit()
            return {"user_email": preflight[0], "title": preflight[1],
                    "status": status}
    except Exception as exc:  # noqa: BLE001
        log.warning("test_feedback_resolve_failed", error=str(exc))
    return None


async def get_notifications(user_email: str) -> dict[str, Any]:
    """
    The operational login notifications for one tester, derived (no
    notifications table):

      resolved_failures  — the tester's failed steps an admin has marked
        resolved and that have not yet been re-attested (a re-attestation
        upsert clears resolved_at, so these self-clear).
      responded_feedback — the tester's feedback an admin has moved off
        'new'. Bounded to the last 21 days so it does not nag forever.
      retest_requested — triage_report_items resolved with
        requires_retest=true whose source UAT row (test_results or
        test_feedback) is owned by this user. Surfaces as "Fix ready
        for retest" in TestNotifications (Item 3 Commit 5). Joins on
        the source_item_type/source_item_id back-pointer migration 023
        added to triage_report_items; the JOIN clears entries once
        retest_completed_at lands. Bounded to the last 21 days like
        responded_feedback so a stale fix stops nagging.

    The "new tests available" notification is computed on the frontend by
    diffing testScripts.ts against /api/v1/testing/unseen.
    """
    empty: dict[str, Any] = {
        "resolved_failures": [], "responded_feedback": [],
        "retest_requested": [],
    }
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return empty
        async with AsyncSessionLocal() as session:
            failures = await session.execute(text("""
                SELECT script_id, step_id, resolution_note, resolved_at,
                       resolution_type, fix_reference, remediation_note
                FROM test_results
                WHERE user_email = :e AND result = 'fail'
                  AND resolved_at IS NOT NULL
                ORDER BY resolved_at DESC
            """), {"e": user_email})
            feedback = await session.execute(text("""
                SELECT id, title, status, resolution_note
                FROM test_feedback
                WHERE user_email = :e AND status <> 'new'
                  AND resolved_at IS NOT NULL
                  AND resolved_at > now() - interval '21 days'
                ORDER BY resolved_at DESC
            """), {"e": user_email})
            # Triage items awaiting retest, joined to whichever UAT
            # source row owns user_email. Returns the script_id/step_id
            # for failure-sourced items so the frontend can deep-link
            # the tester back into the test runner.
            retest = await session.execute(text("""
                SELECT i.id, i.item_title, i.resolution_note,
                       i.fix_commit, i.retest_requested_at,
                       i.source_item_type, i.source_item_id,
                       tr.script_id, tr.step_id
                FROM triage_report_items i
                LEFT JOIN test_results tr ON i.source_item_type = 'failure'
                  AND i.source_item_id = tr.id
                LEFT JOIN test_feedback tf ON i.source_item_type = 'feedback'
                  AND i.source_item_id = tf.id
                WHERE i.requires_retest = true
                  AND i.retest_completed_at IS NULL
                  AND i.retest_requested_at IS NOT NULL
                  AND i.retest_requested_at > now() - interval '21 days'
                  AND (tr.user_email = :e OR tf.user_email = :e)
                ORDER BY i.retest_requested_at DESC
            """), {"e": user_email})
            return {
                "resolved_failures": [{
                    "script_id": r[0], "step_id": r[1],
                    "resolution_note": r[2], "resolved_at": _iso(r[3]),
                    # Migration 025 — drives the three-variant card in
                    # TestNotifications (no_bug_detected /
                    # code_fix_deployed / wont_fix). None on legacy
                    # rows resolved before this migration; the frontend
                    # falls back to the unspecified-type variant.
                    "resolution_type": r[4],
                    "fix_reference": r[5],
                    "remediation_note": r[6],
                } for r in failures.fetchall()],
                "responded_feedback": [{
                    "id": r[0], "title": r[1], "status": r[2],
                    "resolution_note": r[3],
                } for r in feedback.fetchall()],
                "retest_requested": [{
                    "item_id": r[0], "item_title": r[1],
                    "resolution_note": r[2], "fix_commit": r[3],
                    "retest_requested_at": _iso(r[4]),
                    "source_item_type": r[5], "source_item_id": r[6],
                    "script_id": r[7], "step_id": r[8],
                } for r in retest.fetchall()],
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("test_notifications_read_failed", error=str(exc))
        return empty


# ── AI helpers — quality gate and categorisation ──────────────────────────────

def _parse_json(raw: str) -> dict[str, Any]:
    """Tolerant JSON parse — strips a ```json fence if the model added one."""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s[3:]
        if s[:4].lower() == "json":
            s = s[4:]
        end = s.rfind("```")
        if end != -1:
            s = s[:end]
    return json.loads(s.strip())


_QUALITY_SYSTEM = (
    "You are a strict but fair evaluator of UAT test submissions. "
    "Return ONLY valid JSON — no prose, no code fence."
)


def quality_check(
    submission_type: str, step_context: str, description: str,
    actual_result: str | None = None,
) -> dict[str, Any]:
    """
    Scores a failure report or feedback submission before storage — the
    quality gate. Returns {passed, overall, clarification_request}.

    Fail-open: on any error, or in the test environment (no API key),
    returns passed=True so a flawed evaluator never blocks a submission.
    """
    if os.getenv("ENVIRONMENT", "development") == "test" \
            or not os.getenv("ANTHROPIC_API_KEY"):
        return {"passed": True, "overall": 10.0, "clarification_request": ""}

    label = "failure report" if submission_type == "failure" else "feedback"
    user_message = (
        f"You are evaluating the quality of a UAT test submission for an "
        f"investment analytics platform.\n\n"
        f"Type: {label}\n"
        f"Step context: {step_context}\n"
        f"Description: {description}\n"
        f"Actual result (failures only): {actual_result or 'n/a'}\n\n"
        f"Score on three criteria, each 0-10:\n"
        f"1. CLARITY: Is it clear what happened or is being requested? "
        f"Vague statements like 'it broke' or 'make it better' score low.\n"
        f"2. SPECIFICITY: Does it reference specific elements, actions, or "
        f"values? Score high for specifics, low for generics.\n"
        f"3. ACTIONABILITY: Could a developer act on this without "
        f"follow-up? Complete descriptions score high.\n\n"
        f"Return ONLY valid JSON:\n"
        f'{{"scores": {{"clarity": 0-10, "specificity": 0-10, '
        f'"actionability": 0-10}}, "overall": 0.0-10.0, '
        f'"passed": true/false, "clarification_request": "One specific '
        f"question to ask the tester if overall < 7.0. Direct, not "
        f'condescending. Empty string if passed."}}'
    )
    try:
        from agents.base import SONNET_MODEL, call_claude
        parsed = _parse_json(call_claude(
            SONNET_MODEL, _QUALITY_SYSTEM, user_message, max_tokens=400))
        overall = float(parsed.get("overall", 10.0))
        passed = bool(parsed.get("passed", overall >= 7.0))
        return {
            "passed": passed,
            "overall": max(0.0, min(10.0, overall)),
            "clarification_request": (
                "" if passed else str(parsed.get("clarification_request", ""))),
        }
    except Exception as exc:  # noqa: BLE001
        # Fail-open — never block a submission because the evaluator failed.
        log.warning("test_quality_check_failed", error=str(exc))
        return {"passed": True, "overall": 10.0, "clarification_request": ""}


_CATEGORIZE_SYSTEM = (
    "You categorise UAT feedback for an engineering backlog. "
    "Return ONLY valid JSON — no prose, no code fence."
)

_CATEGORIZE_EMPTY: dict[str, Any] = {
    "category": None, "severity": None, "effort_estimate": None,
    "tags": None, "summary": None, "ai_confidence": None,
}


def categorize_feedback(
    feedback_type: str, title: str, description: str, step_context: str,
) -> dict[str, Any]:
    """
    Classifies a feedback submission for the engineering backlog. Returns
    {category, severity, effort_estimate, tags, summary, ai_confidence}.

    Fail-open: on any error, or in the test environment, returns empty
    categorisation (all None) — the feedback is still stored.
    """
    if os.getenv("ENVIRONMENT", "development") == "test" \
            or not os.getenv("ANTHROPIC_API_KEY"):
        return dict(_CATEGORIZE_EMPTY)

    user_message = (
        f"You are categorizing user feedback from a guided UAT test "
        f"session for an investment analytics platform.\n\n"
        f"Tester-selected type: {feedback_type}\n"
        f"Title: {title}\n"
        f"Description: {description}\n"
        f"Step context: {step_context}\n\n"
        f"Return ONLY valid JSON:\n"
        f'{{"category": "Bug Report | New Feature | Enhancement | UX Issue '
        f'| Question | Out of Scope", "severity": "Blocking | Major | Minor '
        f'| Cosmetic | N/A", "effort_estimate": "Trivial | Small | Medium '
        f'| Large | Unknown", "tags": ["max 3 from: council, '
        f"academic_review, analytics, dashboard, settings, reports, "
        f"document_generation, team_activity, navigation, performance, "
        f'data_quality, accessibility, mobile, export"], "summary": "One '
        f'sentence restatement in clear engineering terms.", '
        f'"ai_confidence": 0.0-1.0}}'
    )
    try:
        from agents.base import SONNET_MODEL, call_claude
        parsed = _parse_json(call_claude(
            SONNET_MODEL, _CATEGORIZE_SYSTEM, user_message, max_tokens=400))
        tags = parsed.get("tags")
        conf = parsed.get("ai_confidence")
        return {
            "category": parsed.get("category"),
            "severity": parsed.get("severity"),
            "effort_estimate": parsed.get("effort_estimate"),
            "tags": [str(t) for t in tags][:3] if isinstance(tags, list) else None,
            "summary": parsed.get("summary"),
            "ai_confidence": float(conf) if isinstance(conf, (int, float)) else None,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("test_categorize_failed", error=str(exc))
        return dict(_CATEGORIZE_EMPTY)


# ── helpers ───────────────────────────────────────────────────────────────────

def _iso(value: Any) -> str | None:
    """A datetime → ISO string, or None."""
    try:
        return value.isoformat() if value is not None else None
    except Exception:  # noqa: BLE001
        return None


def _max_iso(a: str | None, b: str | None) -> str | None:
    """The later of two ISO strings."""
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)
