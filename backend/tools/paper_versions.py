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
            # May 24 2026 — defensive read of is_final_submission /
            # final_submission_at: migration 040 adds these columns,
            # but the test environment runs against a schema-free
            # SQLite shim that hasn't been migrated yet. The COALESCE
            # below is harmless on Postgres; on the test shim the
            # missing columns surface as a column-not-found error,
            # which is caught by the outer try/except so the panel
            # still renders with these fields absent.
            try:
                rows = await s.execute(text(
                    "SELECT id, version_number, paper_md, flag_count, "
                    " word_counts, saved_by_email, saved_at, label, "
                    " source, restored_from_version, "
                    " COALESCE(is_final_submission, false), "
                    " final_submission_at "
                    "FROM report_paper_versions "
                    "WHERE generation_id = :g "
                    "ORDER BY version_number DESC"
                ), {"g": int(generation_id)})
                has_final = True
            except Exception:
                rows = await s.execute(text(
                    "SELECT id, version_number, paper_md, flag_count, "
                    " word_counts, saved_by_email, saved_at, label, "
                    " source, restored_from_version "
                    "FROM report_paper_versions "
                    "WHERE generation_id = :g "
                    "ORDER BY version_number DESC"
                ), {"g": int(generation_id)})
                has_final = False
            out: list[dict[str, Any]] = []
            for r in rows.fetchall():
                wc = r[4]
                if isinstance(wc, str):
                    try:
                        wc = json.loads(wc)
                    except json.JSONDecodeError:
                        wc = {}
                entry = {
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
                }
                if has_final:
                    entry["is_final_submission"] = bool(r[10])
                    entry["final_submission_at"] = (
                        r[11].isoformat() if r[11] else None)
                else:
                    entry["is_final_submission"] = False
                    entry["final_submission_at"] = None
                out.append(entry)
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


# ── Deletes ─────────────────────────────────────────────────────────────────
#
# May 24 2026 — per-version + bulk delete helpers (Report Writer
# Version History panel Delete UX). Both fail-open: a database error
# returns False so the caller (the endpoint) can surface a 500 with a
# clear message rather than crashing the route.


async def delete_version(
    generation_id: int, version_number: int,
) -> bool:
    """Hard-deletes one report_paper_versions row.

    Returns True when exactly one row was removed; False on any DB
    error or when the row did not exist. Counterpart of save_version.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return False
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                "DELETE FROM report_paper_versions "
                "WHERE generation_id = :g AND version_number = :v"
            ), {"g": int(generation_id), "v": int(version_number)})
            await s.commit()
            ok = (r.rowcount or 0) > 0
            if ok:
                log.info("paper_version_deleted",
                         generation_id=generation_id,
                         version_number=version_number)
            return ok
    except Exception as exc:  # noqa: BLE001
        log.warning("delete_paper_version_failed", error=str(exc),
                    generation_id=generation_id,
                    version_number=version_number)
        return False


# ── Final Submission marker (May 24 2026, P5) ────────────────────────────────
#
# Bob marks one version as the "Final Submission" so Defense Prep
# and Citation Adjudication reference the SAME version every time —
# not "the most recent draft". The marker lives on report_paper_
# versions.is_final_submission (migration 040). At most ONE row per
# generation_id may be the Final marker (enforced by a partial
# unique index).
#
# When the marked version is later DELETED via delete_version,
# the marker is naturally dropped with the row. The endpoint logic
# surfaces a warning in that case.


async def mark_version_final(
    generation_id: int, version_number: int,
) -> dict[str, Any] | None:
    """Marks one version of a generation as the Final Submission.
    Clears any prior Final marker on the same generation (only one
    Final per generation, enforced by partial unique index too).

    Returns the marked version row, or None on database error.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as s:
            # Clear any existing Final marker on this generation.
            await s.execute(text(
                "UPDATE report_paper_versions "
                "SET is_final_submission = false, "
                "    final_submission_at = NULL "
                "WHERE generation_id = :g "
                "  AND is_final_submission = true"
            ), {"g": int(generation_id)})
            # Mark the named version as Final.
            r = await s.execute(text(
                "UPDATE report_paper_versions "
                "SET is_final_submission = true, "
                "    final_submission_at = now() "
                "WHERE generation_id = :g AND version_number = :v "
                "RETURNING version_number"
            ), {"g": int(generation_id), "v": int(version_number)})
            await s.commit()
            row = r.fetchone()
            if not row:
                log.warning("mark_version_final_not_found",
                            generation_id=generation_id,
                            version_number=version_number)
                return None
            log.info("paper_version_marked_final",
                     generation_id=generation_id,
                     version_number=version_number)
            return await get_version(generation_id, version_number)
    except Exception as exc:  # noqa: BLE001
        log.warning("mark_version_final_failed", error=str(exc),
                    generation_id=generation_id,
                    version_number=version_number)
        return None


async def unmark_final_version(generation_id: int) -> bool:
    """Clears the Final Submission marker on a generation. Returns
    True if any row was unmarked, False otherwise."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return False
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                "UPDATE report_paper_versions "
                "SET is_final_submission = false, "
                "    final_submission_at = NULL "
                "WHERE generation_id = :g "
                "  AND is_final_submission = true"
            ), {"g": int(generation_id)})
            await s.commit()
            ok = (r.rowcount or 0) > 0
            if ok:
                log.info("paper_version_final_unmarked",
                         generation_id=generation_id)
            return ok
    except Exception as exc:  # noqa: BLE001
        log.warning("unmark_final_version_failed", error=str(exc),
                    generation_id=generation_id)
        return False


async def get_final_version(
    generation_id: int,
) -> dict[str, Any] | None:
    """Returns the Final-marked version row for a generation, or
    None if no version is marked Final. Used by Defense Prep and
    Citation Adjudication to resolve "the canonical submission"
    for this generation — they fall back to the most recent
    version when no Final marker exists.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                "SELECT version_number "
                "FROM report_paper_versions "
                "WHERE generation_id = :g "
                "  AND is_final_submission = true "
                "LIMIT 1"
            ), {"g": int(generation_id)})
            row = r.fetchone()
            if not row:
                return None
            return await get_version(generation_id, int(row[0]))
    except Exception as exc:  # noqa: BLE001
        log.warning("get_final_version_failed", error=str(exc),
                    generation_id=generation_id)
        return None


async def get_canonical_version(
    generation_id: int,
) -> dict[str, Any] | None:
    """Returns the canonical submission version for a generation:
    the Final-marked version when one exists, the most recent
    saved version otherwise. This is the function Defense Prep
    and Citation Adjudication call so the same row backs both
    consumers.
    """
    final = await get_final_version(generation_id)
    if final is not None:
        return final
    # Fallback — most recent saved version.
    versions = await list_versions(generation_id)
    return versions[0] if versions else None


async def delete_all_versions(generation_id: int) -> int:
    """Hard-deletes EVERY report_paper_versions row for the generation.
    Useful for the Delete All Drafts flow when the user wants to start
    fresh.

    Returns the number of rows deleted; 0 on any DB error.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return 0
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                "DELETE FROM report_paper_versions "
                "WHERE generation_id = :g"
            ), {"g": int(generation_id)})
            await s.commit()
            n = int(r.rowcount or 0)
            log.info("paper_versions_bulk_deleted",
                     generation_id=generation_id, rows=n)
            return n
    except Exception as exc:  # noqa: BLE001
        log.warning("delete_all_paper_versions_failed", error=str(exc),
                    generation_id=generation_id)
        return 0
        return None
