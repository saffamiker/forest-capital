"""tools/pipeline_audit.py — report-writer pipeline audit log.

Item 12 commit 5 (May 22 2026). Backs migration 033's
report_pipeline_audit table. The report writer UI records per-step
timing + status client-side, then POSTs one audit row per pipeline
run (success or failure) via /api/v1/reports/pipeline-audit. The
sysadmin Settings panel reads back through list_audit_runs() and
get_audit_run() for the per-run breakdown.

Fail-open per the project convention. Every write returns False /
None / [] on a DB error; the audit is informational and must never
block the user's primary workflow.
"""
from __future__ import annotations

import json
from typing import Any

import structlog

log = structlog.get_logger(__name__)


_INSERT_COLS = (
    "generation_id, template_id, triggered_by, "
    "step_1_status, step_1_ms, "
    "step_2_status, step_2_ms, "
    "step_3_status, step_3_ms, "
    "step_4_status, step_4_ms, "
    "step_5_status, step_5_ms, step_5_mismatch_count, "
    "step_6_status, step_6_ms, step_6_conditions, "
    "step_7_status, step_7_ms, "
    "total_pipeline_ms, failure_step, failure_reason"
)


def _safe_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


async def record_audit_run(
    *,
    generation_id: int | None,
    template_id: str,
    triggered_by: str | None,
    steps: dict[str, Any],
    total_pipeline_ms: int | None,
    failure_step: int | None,
    failure_reason: str | None,
) -> int | None:
    """Inserts one audit row. `steps` is the flat dict the frontend
    posts — keys like 'step_1_status', 'step_1_ms', 'step_5_mismatch_
    count', 'step_6_conditions'. Returns the new id or None on error."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                f"INSERT INTO report_pipeline_audit ({_INSERT_COLS}) "
                "VALUES ("
                ":g, :t, :u, "
                ":s1, :ms1, :s2, :ms2, :s3, :ms3, :s4, :ms4, "
                ":s5, :ms5, :mc5, "
                ":s6, :ms6, :c6, "
                ":s7, :ms7, "
                ":total, :fs, :fr) RETURNING id"
            ), {
                "g":  generation_id,
                "t":  template_id,
                "u":  triggered_by,
                "s1": steps.get("step_1_status"),
                "ms1": _safe_int(steps.get("step_1_ms")),
                "s2": steps.get("step_2_status"),
                "ms2": _safe_int(steps.get("step_2_ms")),
                "s3": steps.get("step_3_status"),
                "ms3": _safe_int(steps.get("step_3_ms")),
                "s4": steps.get("step_4_status"),
                "ms4": _safe_int(steps.get("step_4_ms")),
                "s5": steps.get("step_5_status"),
                "ms5": _safe_int(steps.get("step_5_ms")),
                "mc5": _safe_int(steps.get("step_5_mismatch_count")),
                "s6": steps.get("step_6_status"),
                "ms6": _safe_int(steps.get("step_6_ms")),
                "c6": json.dumps(steps.get("step_6_conditions") or [],
                                 default=str),
                "s7": steps.get("step_7_status"),
                "ms7": _safe_int(steps.get("step_7_ms")),
                "total": _safe_int(total_pipeline_ms),
                "fs": _safe_int(failure_step),
                "fr": (failure_reason or None),
            })
            new_id = r.scalar()
            await s.commit()
            return int(new_id) if new_id is not None else None
    except Exception as exc:  # noqa: BLE001
        log.warning("pipeline_audit_write_failed", error=str(exc))
        return None


_SELECT_COLS = (
    "id, generation_id, template_id, triggered_by, run_at, "
    "step_1_status, step_1_ms, step_2_status, step_2_ms, "
    "step_3_status, step_3_ms, step_4_status, step_4_ms, "
    "step_5_status, step_5_ms, step_5_mismatch_count, "
    "step_6_status, step_6_ms, step_6_conditions, "
    "step_7_status, step_7_ms, "
    "total_pipeline_ms, failure_step, failure_reason"
)


def _row_to_dict(row: "tuple[Any, ...]") -> dict[str, Any]:
    conditions = row[18]
    if isinstance(conditions, str):
        try:
            conditions = json.loads(conditions)
        except json.JSONDecodeError:
            conditions = []
    return {
        "id":               int(row[0]),
        "generation_id":    row[1],
        "template_id":      row[2],
        "triggered_by":     row[3],
        "run_at":           row[4].isoformat() if row[4] else None,
        "step_1_status":    row[5],
        "step_1_ms":        row[6],
        "step_2_status":    row[7],
        "step_2_ms":        row[8],
        "step_3_status":    row[9],
        "step_3_ms":        row[10],
        "step_4_status":    row[11],
        "step_4_ms":        row[12],
        "step_5_status":    row[13],
        "step_5_ms":        row[14],
        "step_5_mismatch_count": row[15],
        "step_6_status":    row[16],
        "step_6_ms":        row[17],
        "step_6_conditions": conditions or [],
        "step_7_status":    row[19],
        "step_7_ms":        row[20],
        "total_pipeline_ms": row[21],
        "failure_step":     row[22],
        "failure_reason":   row[23],
    }


async def list_audit_runs(
    *, limit: int = 100,
) -> list[dict[str, Any]]:
    """Newest-first audit runs for the sysadmin admin view."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                f"SELECT {_SELECT_COLS} FROM report_pipeline_audit "
                "ORDER BY run_at DESC LIMIT :n"
            ), {"n": int(limit)})
            return [_row_to_dict(row) for row in r.fetchall()]
    except Exception as exc:  # noqa: BLE001
        log.warning("pipeline_audit_list_failed", error=str(exc))
        return []


async def get_audit_run(audit_id: int) -> dict[str, Any] | None:
    """One audit run by id — surfaces the full step breakdown."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                f"SELECT {_SELECT_COLS} FROM report_pipeline_audit "
                "WHERE id = :i"
            ), {"i": int(audit_id)})
            row = r.fetchone()
            if not row:
                return None
            return _row_to_dict(row)
    except Exception as exc:  # noqa: BLE001
        log.warning("pipeline_audit_read_failed", error=str(exc))
        return None


async def update_generation_timings(
    generation_id: int, timings: dict[str, Any],
) -> bool:
    """Persists the per-step elapsed milliseconds dict on the
    report_generations.pipeline_timings column. Called after the
    pipeline's step 7 completes so the row carries the timing
    history for the summary card."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return False
        async with AsyncSessionLocal() as s:
            await s.execute(text(
                "UPDATE report_generations "
                "SET pipeline_timings = :t WHERE id = :i"
            ), {"t": json.dumps(timings or {}, default=str),
                "i": int(generation_id)})
            await s.commit()
            return True
    except Exception as exc:  # noqa: BLE001
        log.warning("update_pipeline_timings_failed", error=str(exc))
        return False
