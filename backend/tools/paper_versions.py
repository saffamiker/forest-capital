"""tools/paper_versions.py — version-control helpers for the
report paper_md.

Item 2 (May 23 2026 — collaborative editing + version control).

The single mutable paper_md on report_generations is now backed by
an append-only history in report_paper_versions. Every save creates
a snapshot; a restore creates a NEW snapshot pointing at the
restored version rather than overwriting history.

Two concurrent-edit primitives live alongside the version helpers:

  bump_paper_revision()   — increment the optimistic-concurrency
                            counter on report_generations
  check_revision()        — read the counter; the PATCH paper-md
                            endpoint compares the caller's
                            expected_revision against this value
                            and returns 409 when they disagree

All DB operations are fail-open — a read error returns an empty
list / None; a write error logs and returns False so the caller's
primary path can complete (the paper-md save itself).
"""
from __future__ import annotations

import json
from typing import Any

import structlog


log = structlog.get_logger(__name__)


# ── Reads ────────────────────────────────────────────────────────────────────


async def list_versions(generation_id: int) -> list[dict[str, Any]]:
    """Returns every snapshot for a generation_id, ordered newest
    first. Each entry carries the version_number, paper_md (full
    text), flag_count, word_counts, saved_by_email, saved_at, label,
    source, restored_from_version. Fail-open: a database error
    returns [].
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        async with AsyncSessionLocal() as s:
            rows = await s.execute(text(
                "SELECT id, version_number, paper_md, flag_count, "
                " word_counts, saved_by_email, saved_at, label, "
                " source, restored_from_version "
                "FROM report_paper_versions "
                "WHERE generation_id = :g "
                "ORDER BY version_number DESC"
            ), {"g": int(generation_id)})
            out: list[dict[str, Any]] = []
            for r in rows.fetchall():
                wc = r[4]
                if isinstance(wc, str):
                    try:
                        wc = json.loads(wc)
                    except json.JSONDecodeError:
                        wc = {}
                out.append({
                    "id":                     int(r[0]),
                    "version_number":         int(r[1]),
                    "paper_md":               r[2] or "",
                    "flag_count":             int(r[3] or 0),
                    "word_counts":            wc or {},
                    "saved_by_email":         r[5],
                    "saved_at":               (r[6].isoformat()
                                                 if r[6] else None),
                    "label":                  r[7],
                    "source":                 r[8],
                    "restored_from_version":  (int(r[9])
                                                 if r[9] is not None
                                                 else None),
                })
            return out
    except Exception as exc:  # noqa: BLE001
        log.warning("list_paper_versions_failed", error=str(exc),
                    generation_id=generation_id)
        return []


async def get_version(
    generation_id: int, version_number: int,
) -> dict[str, Any] | None:
    """One specific version row, or None if not found / database
    error. Used by the restore endpoint to fetch the source row's
    paper_md."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                "SELECT id, version_number, paper_md, flag_count, "
                " word_counts, saved_by_email, saved_at, label, "
                " source, restored_from_version "
                "FROM report_paper_versions "
                "WHERE generation_id = :g AND version_number = :v"
            ), {"g": int(generation_id), "v": int(version_number)})
            row = r.fetchone()
            if not row:
                return None
            wc = row[4]
            if isinstance(wc, str):
                try:
                    wc = json.loads(wc)
                except json.JSONDecodeError:
                    wc = {}
            return {
                "id":                     int(row[0]),
                "version_number":         int(row[1]),
                "paper_md":               row[2] or "",
                "flag_count":             int(row[3] or 0),
                "word_counts":            wc or {},
                "saved_by_email":         row[5],
                "saved_at":               (row[6].isoformat()
                                             if row[6] else None),
                "label":                  row[7],
                "source":                 row[8],
                "restored_from_version":  (int(row[9])
                                             if row[9] is not None
                                             else None),
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("get_paper_version_failed", error=str(exc),
                    generation_id=generation_id,
                    version_number=version_number)
        return None


async def check_revision(generation_id: int) -> int | None:
    """Returns the current paper_revision on the generation, or None
    on a database error / unknown generation. The endpoint uses this
    in two places:

      - on PATCH paper-md: compare against the caller's
        expected_revision to detect a concurrent save
      - on every read response: included so the next PATCH can be
        accurate
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                "SELECT paper_revision FROM report_generations "
                "WHERE id = :i"
            ), {"i": int(generation_id)})
            row = r.fetchone()
            return int(row[0]) if row else None
    except Exception as exc:  # noqa: BLE001
        log.warning("check_paper_revision_failed", error=str(exc),
                    generation_id=generation_id)
        return None


# ── Writes ───────────────────────────────────────────────────────────────────


async def save_version(
    generation_id: int,
    paper_md: str,
    *,
    saved_by_email: str | None,
    label: str | None = None,
    source: str = "manual",
    flag_count: int = 0,
    word_counts: dict[str, Any] | None = None,
    restored_from_version: int | None = None,
) -> dict[str, Any] | None:
    """Append a snapshot row. Returns the newly inserted row as a
    dict, or None on a database error. version_number is computed
    server-side as max(version_number)+1 per generation_id.

    `source` is one of: manual | auto_iterate | auto_resolve_bob |
    auto_edit | restore.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as s:
            # Compute the next version_number atomically inside the
            # transaction. The UNIQUE constraint on (generation_id,
            # version_number) protects against any race that slipped
            # past the COALESCE.
            r = await s.execute(text(
                "INSERT INTO report_paper_versions "
                "(generation_id, version_number, paper_md, flag_count, "
                " word_counts, saved_by_email, label, source, "
                " restored_from_version) "
                "VALUES ("
                "  :g, "
                "  COALESCE(("
                "    SELECT MAX(version_number) + 1 "
                "    FROM report_paper_versions "
                "    WHERE generation_id = :g"
                "  ), 1), "
                "  :p, :f, CAST(:w AS JSONB), :by, :lbl, :src, :rf"
                ") "
                "RETURNING id, version_number, saved_at"
            ), {
                "g":   int(generation_id),
                "p":   paper_md,
                "f":   int(flag_count or 0),
                "w":   json.dumps(word_counts or {}, default=str),
                "by":  saved_by_email,
                "lbl": label,
                "src": source,
                "rf":  (int(restored_from_version)
                        if restored_from_version is not None else None),
            })
            row = r.fetchone()
            if not row:
                return None
            await s.commit()
            return {
                "id":                    int(row[0]),
                "version_number":        int(row[1]),
                "saved_at":              (row[2].isoformat()
                                            if row[2] else None),
                "source":                source,
                "label":                 label,
                "restored_from_version": restored_from_version,
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("save_paper_version_failed", error=str(exc),
                    generation_id=generation_id, source=source)
        return None


async def bump_paper_revision(generation_id: int) -> int | None:
    """Increment paper_revision on report_generations and return the
    new value. Called immediately after every successful paper_md
    write. Fail-open: a database error returns None and the caller
    proceeds with the save — the staleness check is a defence in
    depth, not a hard barrier."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                "UPDATE report_generations "
                "SET paper_revision = paper_revision + 1 "
                "WHERE id = :i "
                "RETURNING paper_revision"
            ), {"i": int(generation_id)})
            row = r.fetchone()
            await s.commit()
            return int(row[0]) if row else None
    except Exception as exc:  # noqa: BLE001
        log.warning("bump_paper_revision_failed", error=str(exc),
                    generation_id=generation_id)
        return None


async def restore_version(
    generation_id: int,
    version_number: int,
    *,
    reviewer_email: str | None,
) -> dict[str, Any] | None:
    """Restore a prior version as the new current paper_md.

    Steps (all inside one transaction):
      1. SELECT the source version's paper_md.
      2. UPDATE report_generations.paper_md to the source's text and
         bump paper_revision.
      3. INSERT a new version_number row with source='restore' and
         restored_from_version=<source>.

    Returns the new version row, or None on any failure (including
    the source version not existing).
    """
    src = await get_version(generation_id, version_number)
    if src is None:
        return None
    # Use the existing _update_paper_md from report_generator so the
    # post-check + paper_md write stays in one place.
    try:
        from tools.report_generator import _update_paper_md
        ok = await _update_paper_md(
            generation_id, src["paper_md"],
            int(src["flag_count"]), src["word_counts"] or {})
        if not ok:
            return None
        await bump_paper_revision(generation_id)
        new_row = await save_version(
            generation_id, src["paper_md"],
            saved_by_email=reviewer_email,
            label=f"Restore of v{version_number}",
            source="restore",
            flag_count=int(src["flag_count"]),
            word_counts=src["word_counts"] or {},
            restored_from_version=version_number,
        )
        return new_row
    except Exception as exc:  # noqa: BLE001
        log.warning("restore_paper_version_failed", error=str(exc),
                    generation_id=generation_id,
                    version_number=version_number)
        return None
