"""tools/document_audit_metrics.py — write + read for document_audit_metrics.

Per-generation-run audit-flag log. One row per executive brief or
presentation deck generation, written alongside the draft create.
Mirrors the council_query_metrics pattern.

The admin endpoint /api/v1/admin/document-audit-metrics serves the
last-N rows + per-document-type aggregates so the team can track
flag rates over time.
"""
from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger(__name__)


async def write_metric(
    *,
    document_type: str,
    owner_email: str,
    draft_id: int | None,
    flag_counts: dict[str, int],
    data_hash: str | None,
) -> None:
    """Insert one row. Fail-open — a write failure logs and returns
    rather than blocking the generator's main path. Skipped in the
    test environment (no DB)."""
    try:
        import os
        if (os.environ.get("ENVIRONMENT") or "").lower() == "test":
            return
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return
        async with AsyncSessionLocal() as session:
            await session.execute(text(
                "INSERT INTO document_audit_metrics "
                "(document_type, owner_email, draft_id, "
                " numeric_flag_count, direction_flag_count, "
                " consistency_flag_count, citation_flag_count, "
                " total_flag_count, data_hash) VALUES "
                "(:dt, :oe, :did, :nf, :df, :cf, :tf, :tot, :dh)"
            ), {
                "dt":  document_type,
                "oe":  owner_email,
                "did": draft_id,
                "nf":  int(flag_counts.get("numeric", 0)),
                "df":  int(flag_counts.get("direction", 0)),
                "cf":  int(flag_counts.get("consistency", 0)),
                "tf":  int(flag_counts.get("citation", 0)),
                "tot": int(flag_counts.get("total", 0)),
                "dh":  data_hash,
            })
            await session.commit()
        log.info("document_audit_metric_written",
                 document_type=document_type, flag_counts=flag_counts)
    except Exception as exc:  # noqa: BLE001
        log.warning("document_audit_metric_write_failed",
                    error=str(exc))


async def read_recent(limit: int = 30) -> dict[str, Any]:
    """Returns {available, rows, aggregates} — the shape the
    /api/v1/admin/document-audit-metrics endpoint surfaces.

    Aggregates: per document_type, the AVG of each check's flag
    count and the running total flag count. Useful for spotting
    a regression in generation quality at a glance.
    """
    import os
    limit = max(1, min(int(limit or 30), 200))
    if (os.environ.get("ENVIRONMENT") or "").lower() == "test":
        return {"available": False, "rows": [], "aggregates": {}}
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return {"available": False, "rows": [], "aggregates": {}}
        rows: list[dict[str, Any]] = []
        per_type: dict[str, dict[str, Any]] = {}
        async with AsyncSessionLocal() as db:
            r = await db.execute(text(
                "SELECT id, timestamp, document_type, owner_email, "
                " draft_id, numeric_flag_count, direction_flag_count, "
                " consistency_flag_count, citation_flag_count, "
                " total_flag_count, data_hash "
                "FROM document_audit_metrics "
                "ORDER BY timestamp DESC LIMIT :n"), {"n": limit})
            for row in r.fetchall():
                rows.append({
                    "id":                     row[0],
                    "timestamp":              (row[1].isoformat()
                                               if hasattr(row[1], "isoformat")
                                               else str(row[1])),
                    "document_type":          row[2],
                    "owner_email":            row[3],
                    "draft_id":               row[4],
                    "numeric_flag_count":     row[5],
                    "direction_flag_count":   row[6],
                    "consistency_flag_count": row[7],
                    "citation_flag_count":    row[8],
                    "total_flag_count":       row[9],
                    "data_hash": (row[10] or "")[:8] if row[10] else None,
                })
            agg_r = await db.execute(text(
                "SELECT document_type, "
                " AVG(numeric_flag_count)::float, "
                " AVG(direction_flag_count)::float, "
                " AVG(consistency_flag_count)::float, "
                " AVG(citation_flag_count)::float, "
                " AVG(total_flag_count)::float, "
                " COUNT(*)::int "
                "FROM document_audit_metrics "
                "GROUP BY document_type"))
            for dt, an, ad, ac, ai, at, n in agg_r.fetchall():
                per_type[dt] = {
                    "avg_numeric_flags":     an,
                    "avg_direction_flags":   ad,
                    "avg_consistency_flags": ac,
                    "avg_citation_flags":    ai,
                    "avg_total_flags":       at,
                    "n_runs":                n,
                }
        return {
            "available":  True,
            "rows":       rows,
            "aggregates": {"per_document_type": per_type},
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("document_audit_metrics_read_failed",
                    error=str(exc))
        return {"available": False, "rows": [], "aggregates": {}}
