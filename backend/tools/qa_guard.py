"""
tools/qa_guard.py — global QA-run concurrency guard.

Only one QA run may be in progress platform-wide at a time. A QA run is
either a statistical audit (the three-layer recomputation) or a
methodology audit (the QA-agent checklist / tiered checks). A second run
of either kind, started while one is in flight, is REJECTED — not
queued — with a clear message.

Two in-progress signals are combined:

  - Statistical — the 'running' row in audit_runs. This is the existing
    cross-process lock: it survives a restart and is visible to every
    worker (tools.audit_engine.is_audit_running).

  - Methodology — a process-level flag. The methodology audits run
    synchronously inside their request, so a flag set for the request's
    duration is sufficient. The backend runs a single uvicorn worker, so
    the flag is platform-wide for this deployment; were it ever scaled
    to multiple workers the methodology audits would need their own
    audit_runs-style row.

qa_run_in_progress() checks both, so a guard placed on EITHER run path
sees a run started on the other — that cross-visibility is the point.

Fail-open: a check error reports "no run in progress" rather than
wedging every QA run behind a guard that cannot clear.
"""
from __future__ import annotations

import time

import structlog

log = structlog.get_logger(__name__)

# The message returned to a caller whose run is rejected.
QA_BUSY_MESSAGE = (
    "A QA run is currently in progress. Please wait for it to complete "
    "before starting a new one."
)

# Process-level methodology-run flag. Set for the duration of a
# synchronous methodology audit; cleared in a finally so an exception
# never leaves the guard stuck.
_methodology: dict[str, object] = {"active": False, "started_at": None}


def methodology_in_progress() -> bool:
    """True while a methodology audit is running in this process."""
    return bool(_methodology["active"])


def begin_methodology() -> None:
    """Mark a methodology audit as started. Pair with end_methodology()
    in a finally so the flag always clears."""
    _methodology["active"] = True
    _methodology["started_at"] = time.time()


def end_methodology() -> None:
    """Mark the methodology audit complete — the guard lifts."""
    _methodology["active"] = False
    _methodology["started_at"] = None


async def qa_run_in_progress() -> str | None:
    """
    The kind of QA run currently in progress — "methodology" or
    "statistical" — or None when the platform is free to start one.

    Fail-open: any error in the statistical (database) check is logged
    and treated as "not running", so the guard can never permanently
    block QA runs because of a transient database problem.
    """
    if methodology_in_progress():
        return "methodology"
    try:
        from tools.audit_engine import is_audit_running
        if await is_audit_running() is not None:
            return "statistical"
    except Exception as exc:  # noqa: BLE001 — fail-open
        log.warning("qa_guard_statistical_check_failed", error=str(exc))
    return None
