"""tools/report_templates.py — storage layer for the report_templates table.

May 22 2026 (item 12 — verified-data midpoint paper template). Backs
migration 031. The endpoint and the generation pipeline read templates
through this module; the migration seeds the midpoint_check_fna670
row at upgrade time, and future templates (executive brief, final
presentation appendix) plug in as additional rows.

Fail-open per the project convention — every read returns None / []
on a missing DB or a SQL error so the API surfaces a graceful empty
state rather than a 500.
"""
from __future__ import annotations

import json
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def _coerce_json(v: Any, fallback: Any) -> Any:
    """asyncpg may return JSONB as parsed Python objects OR as
    serialised strings depending on driver configuration. Accept both
    so the read accessors don't have to branch at every call site."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return fallback
    return v if v is not None else fallback


def _row_to_dict(r: "tuple[Any, ...]") -> dict[str, Any]:
    """SELECT projection mapper. Column order:
      template_id, display_name, course, format, target_pages,
      sections, system_prompt, data_pull_fields, citation_slots,
      requires_staging, active, created_at."""
    return {
        "template_id":      r[0],
        "display_name":     r[1],
        "course":           r[2],
        "format":           r[3],
        "target_pages":     r[4],
        "sections":         _coerce_json(r[5], []),
        "system_prompt":    r[6],
        "data_pull_fields": _coerce_json(r[7], []),
        "citation_slots":   _coerce_json(r[8], []),
        "requires_staging": bool(r[9]),
        "active":           bool(r[10]),
        "created_at":       (
            r[11].isoformat() if r[11] is not None else None),
    }


_PROJECTION = (
    "template_id, display_name, course, format, target_pages, "
    "sections, system_prompt, data_pull_fields, citation_slots, "
    "requires_staging, active, created_at")


async def list_active_templates() -> list[dict[str, Any]]:
    """Every active template, ordered by display_name. Drives the
    report-writer template dropdown."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text(
                f"SELECT {_PROJECTION} FROM report_templates "
                f"WHERE active = TRUE ORDER BY display_name"))
            return [_row_to_dict(r) for r in rows.fetchall()]
    except Exception as exc:  # noqa: BLE001
        log.warning("report_templates_list_failed", error=str(exc))
        return []


async def get_template(template_id: str) -> dict[str, Any] | None:
    """One row by template_id. Returns None on miss (404 path)."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                f"SELECT {_PROJECTION} FROM report_templates "
                f"WHERE template_id = :t AND active = TRUE LIMIT 1"
            ), {"t": template_id})
            found = row.fetchone()
            if not found:
                return None
            return _row_to_dict(found)
    except Exception as exc:  # noqa: BLE001
        log.warning("report_template_read_failed",
                    template_id=template_id, error=str(exc))
        return None
