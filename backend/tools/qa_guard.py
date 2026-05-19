"""
tools/qa_guard.py — per-type QA-run concurrency guards.

There are two INDEPENDENT kinds of QA run, each with its own lock so one
never blocks the other:

  - Statistical — the three-layer recomputation. Its lock is the
    'running' row in audit_runs (tools.audit_engine.is_audit_running) —
    a cross-process lock that survives a restart and is visible to every
    worker. is_audit_running() reaps any run stuck 'running' past the
    15-minute timeout, so a crashed run can never hold the lock forever.

  - Methodology — the QA-agent checklist / tiered checks. Its lock is a
    process-level flag, set for the synchronous run's duration. The
    backend runs a single uvicorn worker, so the flag is platform-wide
    for this deployment. A flag still set past the 15-minute timeout
    (a crashed run that never reached end_methodology) is treated as
    stale and auto-cleared — the in-memory counterpart of the
    statistical audit's audit_runs timeout.

A statistical audit and a methodology audit MAY run concurrently — they
verify different things and contend for nothing. Each run-triggering
endpoint guards ONLY on its own type: statistical_audit_in_progress()
gates /api/v1/audit/run; methodology_in_progress() gates the QA-agent
endpoints.

Fail-open: a check error reports "not running" rather than wedging every
QA run behind a guard that cannot clear.
"""
from __future__ import annotations

import time

import structlog

log = structlog.get_logger(__name__)

# The 409 messages returned to a caller whose run is rejected.
QA_BUSY_MESSAGE_STATISTICAL = (
    "A statistical audit is already in progress. Please wait for it to "
    "complete before starting another."
)
QA_BUSY_MESSAGE_METHODOLOGY = (
    "A methodology audit is already in progress. Please wait for it to "
    "complete before starting another."
)

# A methodology flag still set past this is treated as a crashed run that
# never reached end_methodology, and auto-cleared. Mirrors the statistical
# audit's 15-minute audit_runs timeout (audit_engine._AUDIT_TIMEOUT_MINUTES).
_METHODOLOGY_TIMEOUT_SECONDS = 15 * 60

# Process-level methodology-run flag. Set for the duration of a
# synchronous methodology audit; cleared in a finally so an exception
# never leaves the guard stuck.
_methodology: dict[str, object] = {"active": False, "started_at": None}


def methodology_in_progress() -> bool:
    """True while a methodology audit is running in this process. A flag
    still set past the timeout is a crashed run that never reached
    end_methodology — treated as stale and cleared, so a hung run can
    never block methodology audits indefinitely."""
    if not _methodology["active"]:
        return False
    started = _methodology["started_at"]
    if isinstance(started, (int, float)) and (
            time.time() - started > _METHODOLOGY_TIMEOUT_SECONDS):
        log.warning("methodology_run_timed_out",
                    timeout_minutes=_METHODOLOGY_TIMEOUT_SECONDS // 60)
        _methodology["active"] = False
        _methodology["started_at"] = None
        return False
    return True


def begin_methodology() -> None:
    """Mark a methodology audit as started. Pair with end_methodology()
    in a finally so the flag always clears."""
    _methodology["active"] = True
    _methodology["started_at"] = time.time()


def end_methodology() -> None:
    """Mark the methodology audit complete — the guard lifts."""
    _methodology["active"] = False
    _methodology["started_at"] = None


async def statistical_audit_in_progress() -> bool:
    """
    True while a statistical (three-layer) audit is in flight.

    Reads the audit_runs 'running' row via is_audit_running(), which
    first reaps any run stuck past the 15-minute timeout — so a crashed
    run never reports as in-progress here.

    Fail-open: any error in the database check is logged and treated as
    "not running", so the guard can never permanently block statistical
    audits because of a transient database problem.
    """
    try:
        from tools.audit_engine import is_audit_running
        return await is_audit_running() is not None
    except Exception as exc:  # noqa: BLE001 — fail-open
        log.warning("qa_guard_statistical_check_failed", error=str(exc))
        return False
