"""tools/report_rubrics.py — storage + read access for report_rubrics.

May 22 2026 (item 12 commit 2). Backs migration 032. The report
writer's academic review reads the latest active rubric for a
template via get_latest_rubric(template_id); the Upload Rubric UI
calls upload_rubric(...) to add a new version.

Fail-open per the project convention.
"""
from __future__ import annotations

import json
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def _coerce_json(v: Any, fallback: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return fallback
    return v if v is not None else fallback


async def get_latest_rubric(
    template_id: str,
) -> dict[str, Any] | None:
    """Returns the highest-version active rubric for a template, or
    None when nothing is on file."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                "SELECT id, template_id, version, rubric_text, "
                " criteria, uploaded_by, source_filename, active, "
                " uploaded_at "
                "FROM report_rubrics "
                "WHERE template_id = :t AND active = TRUE "
                "ORDER BY version DESC LIMIT 1"
            ), {"t": template_id})
            row = r.fetchone()
            if not row:
                return None
            return {
                "id":              int(row[0]),
                "template_id":     row[1],
                "version":         int(row[2]),
                "rubric_text":     row[3],
                "criteria":        _coerce_json(row[4], []),
                "uploaded_by":     row[5],
                "source_filename": row[6],
                "active":          bool(row[7]),
                "uploaded_at":     (
                    row[8].isoformat() if row[8] is not None else None),
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("rubric_read_failed", error=str(exc))
        return None


async def list_rubrics(
    template_id: str,
) -> list[dict[str, Any]]:
    """Every rubric version for a template, newest first. Drives the
    Rubric Management UI section in the report writer."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                "SELECT id, template_id, version, rubric_text, "
                " criteria, uploaded_by, source_filename, active, "
                " uploaded_at "
                "FROM report_rubrics "
                "WHERE template_id = :t "
                "ORDER BY version DESC"
            ), {"t": template_id})
            return [{
                "id":              int(row[0]),
                "template_id":     row[1],
                "version":         int(row[2]),
                "rubric_text":     row[3],
                "criteria":        _coerce_json(row[4], []),
                "uploaded_by":     row[5],
                "source_filename": row[6],
                "active":          bool(row[7]),
                "uploaded_at":     (
                    row[8].isoformat() if row[8] is not None else None),
            } for row in r.fetchall()]
    except Exception as exc:  # noqa: BLE001
        log.warning("rubric_list_failed", error=str(exc))
        return []


async def upload_rubric(
    template_id: str,
    rubric_text: str,
    criteria: list[dict[str, Any]],
    *,
    uploaded_by: str | None,
    source_filename: str | None = None,
) -> int | None:
    """Inserts a new rubric version. The version increments off the
    highest existing version for the template; concurrent uploads
    are rare so a non-locking SELECT MAX is sufficient.

    Returns the new id, or None on a DB error / cold environment."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                "SELECT COALESCE(MAX(version), 0) FROM report_rubrics "
                "WHERE template_id = :t"
            ), {"t": template_id})
            next_version = int((r.scalar() or 0)) + 1
            r = await s.execute(text(
                "INSERT INTO report_rubrics "
                "(template_id, version, rubric_text, criteria, "
                " uploaded_by, source_filename, active) "
                "VALUES (:t, :v, :rt, :c, :ub, :sf, TRUE) "
                "RETURNING id"
            ), {
                "t":  template_id,
                "v":  next_version,
                "rt": rubric_text,
                "c":  json.dumps(criteria or []),
                "ub": uploaded_by,
                "sf": source_filename,
            })
            new_id = r.scalar()
            await s.commit()
            return int(new_id) if new_id is not None else None
    except Exception as exc:  # noqa: BLE001
        log.warning("rubric_upload_failed", error=str(exc))
        return None
