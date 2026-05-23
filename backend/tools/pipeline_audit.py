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


async def upsert_active_run(
    *,
    template_id: str,
    triggered_by: str | None,
    steps: dict[str, Any],
    total_pipeline_ms: int | None = None,
    failure_step: int | None = None,
    failure_reason: str | None = None,
    generation_id: int | None = None,
    audit_id: int | None = None,
) -> int | None:
    """Idempotent UPSERT for incremental persistence.

    On first call (audit_id None) inserts a fresh row and returns the
    new id. On subsequent calls with the same audit_id updates the
    existing row's step columns, total ms, failure fields, and
    generation_id. The frontend round-trips the audit_id so every
    step completion writes to the same row.

    This is the second key persistence pattern alongside
    record_audit_run() (the terminal one-shot write). The terminal
    write is retained for compatibility with the original commit-5
    contract; new callers from commit-B use the incremental flow.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as s:
            if audit_id is None:
                r = await s.execute(text(
                    f"INSERT INTO report_pipeline_audit ({_INSERT_COLS}) "
                    "VALUES ("
                    ":g, :t, :u, "
                    ":s1, :ms1, :s2, :ms2, :s3, :ms3, :s4, :ms4, "
                    ":s5, :ms5, :mc5, "
                    ":s6, :ms6, :c6, "
                    ":s7, :ms7, "
                    ":total, :fs, :fr) RETURNING id"
                ), _build_audit_params(
                    generation_id, template_id, triggered_by, steps,
                    total_pipeline_ms, failure_step, failure_reason))
                new_id = r.scalar()
                await s.commit()
                return int(new_id) if new_id is not None else None
            # Update existing row — only fields that are present in
            # the steps dict are overwritten so a later step does not
            # blank an earlier step's result.
            updates: list[str] = []
            params: dict[str, Any] = {"i": int(audit_id)}
            for n in (1, 2, 3, 4, 5, 6, 7):
                key_s = f"step_{n}_status"
                key_ms = f"step_{n}_ms"
                if key_s in steps:
                    updates.append(f"{key_s} = :{key_s}")
                    params[key_s] = steps[key_s]
                if key_ms in steps:
                    updates.append(f"{key_ms} = :{key_ms}")
                    params[key_ms] = _safe_int(steps[key_ms])
            if "step_5_mismatch_count" in steps:
                updates.append(
                    "step_5_mismatch_count = :step_5_mismatch_count")
                params["step_5_mismatch_count"] = _safe_int(
                    steps["step_5_mismatch_count"])
            if "step_6_conditions" in steps:
                updates.append(
                    "step_6_conditions = :step_6_conditions")
                params["step_6_conditions"] = json.dumps(
                    steps["step_6_conditions"], default=str)
            if total_pipeline_ms is not None:
                updates.append("total_pipeline_ms = :total")
                params["total"] = _safe_int(total_pipeline_ms)
            if failure_step is not None:
                updates.append("failure_step = :fs")
                params["fs"] = _safe_int(failure_step)
            if failure_reason is not None:
                updates.append("failure_reason = :fr")
                params["fr"] = failure_reason
            if generation_id is not None:
                updates.append("generation_id = :g")
                params["g"] = int(generation_id)
            if not updates:
                return int(audit_id)
            await s.execute(text(
                "UPDATE report_pipeline_audit "
                f"SET {', '.join(updates)} WHERE id = :i"
            ), params)
            await s.commit()
            return int(audit_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("pipeline_audit_upsert_failed", error=str(exc))
        return None


def _build_audit_params(
    generation_id: int | None,
    template_id: str,
    triggered_by: str | None,
    steps: dict[str, Any],
    total_pipeline_ms: int | None,
    failure_step: int | None,
    failure_reason: str | None,
) -> dict[str, Any]:
    return {
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
        "fr": failure_reason,
    }


async def get_active_run_for_user(
    user_email: str,
    *,
    window_hours: int = 2,
) -> dict[str, Any] | None:
    """Returns the most recent audit row started by `user_email`
    within the past `window_hours` hours. Used by the report writer's
    restore-on-mount path so Bob can navigate away and come back.

    The row may be in any state — fresh, in-progress, complete, or
    failed. The frontend decides whether to restore based on the
    per-step statuses and the run_at timestamp."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                f"SELECT {_SELECT_COLS} FROM report_pipeline_audit "
                "WHERE triggered_by = :u "
                "AND run_at > now() - (:h * interval '1 hour') "
                "ORDER BY run_at DESC LIMIT 1"
            ), {"u": user_email, "h": int(window_hours)})
            row = r.fetchone()
            if not row:
                return None
            return _row_to_dict(row)
    except Exception as exc:  # noqa: BLE001
        log.warning("pipeline_audit_active_read_failed", error=str(exc))
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
