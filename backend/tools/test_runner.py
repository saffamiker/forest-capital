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
DB holds only relative paths. NOTE — on Render the filesystem is
ephemeral and these image files do NOT survive a redeploy. The
attestation row (result, description, severity, timestamps) is the
durable record; screenshots are supporting evidence only.
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
# Local disk under backend/data/uploads. Served read-only via the
# /uploads StaticFiles mount in main.py. EPHEMERAL on Render — see the
# module docstring.
_UPLOAD_ROOT = Path(__file__).parent.parent / "data" / "uploads"
_SCREENSHOT_SUBDIR = "test_screenshots"
_ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif"}
_MAX_SCREENSHOTS = 3


def save_screenshots(files: list[tuple[str, bytes]]) -> list[str]:
    """
    Writes up to three uploaded images to the local uploads directory and
    returns their relative paths (e.g. "test_screenshots/<uuid>.png").

    files: (filename, bytes) pairs. Fail-open — if the directory cannot be
    written the result is simply stored without screenshots; a failure
    here must never block an attestation.
    """
    saved: list[str] = []
    try:
        dest = _UPLOAD_ROOT / _SCREENSHOT_SUBDIR
        dest.mkdir(parents=True, exist_ok=True)
        for filename, content in files[:_MAX_SCREENSHOTS]:
            ext = Path(filename or "").suffix.lower()
            if ext not in _ALLOWED_IMAGE_EXT or not content:
                continue
            rel = f"{_SCREENSHOT_SUBDIR}/{uuid.uuid4().hex}{ext}"
            (_UPLOAD_ROOT / rel).write_bytes(content)
            saved.append(rel)
    except Exception as exc:  # noqa: BLE001
        log.warning("test_screenshot_save_failed", error=str(exc))
    return saved


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
                    resolved_at = NULL,
                    resolved_by = NULL,
                    resolution_note = NULL
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
                SELECT script_id, step_id, attested_at, resolved_at
                FROM test_results WHERE user_email = :e
            """), {"e": user_email})
            scripts: dict[str, dict[str, Any]] = {}
            for script_id, step_id, attested_at, resolved_at in rows.fetchall():
                s = scripts.setdefault(
                    script_id, {"attested_step_ids": [], "last_attested_at": None})
                # A resolved failure is pending re-test — not "attested".
                if resolved_at is None:
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
    """Every failed step across all testers — admin failure-reports view."""
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
                       resolved_at, resolved_by, resolution_note
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
            } for r in rows.fetchall()]
    except Exception as exc:  # noqa: BLE001
        log.warning("test_failures_read_failed", error=str(exc))
        return []


async def resolve_failure(
    failure_id: int, resolved_by: str, resolution_note: str,
) -> dict[str, Any] | None:
    """
    Marks a failed step resolved. The row is kept (the resolution is the
    audit trail) with resolved_at/by/note set — the frontend treats a
    resolved failure as a pending re-test, so the step re-appears for the
    tester. Returns {user_email, script_id, step_id} for the notification
    queue, or None.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text("""
                UPDATE test_results
                SET resolved_at = now(), resolved_by = :by,
                    resolution_note = :note
                WHERE id = :id AND result = 'fail'
                RETURNING user_email, script_id, step_id
            """), {"id": failure_id, "by": resolved_by, "note": resolution_note})
            found = row.fetchone()
            await session.commit()
            if found:
                return {"user_email": found[0], "script_id": found[1],
                        "step_id": found[2]}
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
    for the notification queue, or None."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text("""
                UPDATE test_feedback
                SET status = :status, resolution_note = :note,
                    resolved_by = :by,
                    resolved_at = CASE WHEN :status IN ('resolved', 'wont_do')
                                       THEN now() ELSE resolved_at END
                WHERE id = :id
                RETURNING user_email, title, status
            """), {"id": feedback_id, "status": status,
                   "note": resolution_note, "by": resolved_by})
            found = row.fetchone()
            await session.commit()
            if found:
                return {"user_email": found[0], "title": found[1],
                        "status": found[2]}
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

    The "new tests available" notification is computed on the frontend by
    diffing testScripts.ts against /api/v1/testing/unseen.
    """
    empty: dict[str, Any] = {"resolved_failures": [], "responded_feedback": []}
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return empty
        async with AsyncSessionLocal() as session:
            failures = await session.execute(text("""
                SELECT script_id, step_id, resolution_note, resolved_at
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
            return {
                "resolved_failures": [{
                    "script_id": r[0], "step_id": r[1],
                    "resolution_note": r[2], "resolved_at": _iso(r[3]),
                } for r in failures.fetchall()],
                "responded_feedback": [{
                    "id": r[0], "title": r[1], "status": r[2],
                    "resolution_note": r[3],
                } for r in feedback.fetchall()],
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
