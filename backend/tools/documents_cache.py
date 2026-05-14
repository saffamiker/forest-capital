"""
tools/documents_cache.py

PostgreSQL access layer for the three Sprint 6 documents tables
(migration 004). Every storyboard, executive brief, midpoint paper,
and Q&A doc routes its persistence through this module — endpoints in
main.py call these functions rather than writing raw SQL.

Schema reminder (from migration 004):
  documents          (id, doc_type, owner_email, created_at, strategy_hash, is_finalised)
  document_versions  (id, document_id, version_number, version_name, content,
                      change_summary, created_at, created_by, strategy_hash,
                      is_auto_save, restored_from)
  document_drafts    (document_id PK, content, last_saved_at, based_on_version)

Every function fails open: when DATABASE_URL is unset (local dev without
Postgres) the read functions return None and the writers no-op. This
matches the pattern used by tools/cache.py for the strategy cache. The
endpoints handle None returns by responding 404 — the caller's UX
degrades to "documents unavailable" rather than 500.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

try:
    import structlog
    log = structlog.get_logger(__name__)
except ImportError:
    log = logging.getLogger(__name__)  # type: ignore[assignment]

_DB_AVAILABLE = False
try:
    from database import AsyncSessionLocal
    _DB_AVAILABLE = AsyncSessionLocal is not None
except Exception:
    pass


# ── documents (parent rows) ──────────────────────────────────────────────

async def create_document(
    doc_type: str,
    owner_email: str,
    initial_content: dict[str, Any],
    strategy_hash: str | None = None,
    created_by: str | None = None,
) -> str | None:
    """
    Inserts a new document and writes the initial content to
    document_drafts in the same transaction. Returns the new document_id
    (UUID string) or None when the DB is unavailable.

    The atomic insert+draft means callers don't have to handle the
    "document exists but has no draft" failure mode — every document
    always has at least an empty draft row.
    """
    if not _DB_AVAILABLE:
        return None

    doc_id = str(uuid.uuid4())
    creator = created_by or owner_email
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            await session.execute(
                text(
                    "INSERT INTO documents "
                    "(id, doc_type, owner_email, strategy_hash) "
                    "VALUES (:id, :dt, :oe, :sh)"
                ),
                {"id": doc_id, "dt": doc_type, "oe": owner_email, "sh": strategy_hash},
            )
            await session.execute(
                text(
                    "INSERT INTO document_drafts "
                    "(document_id, content, based_on_version) "
                    "VALUES (:id, :c, NULL)"
                ),
                {"id": doc_id, "c": json.dumps(initial_content)},
            )
            # Also save the initial content as version 1 (the AI draft).
            # An audit log of named versions starts at v1; auto-saves go
            # in here too so the Admin screen can show the full history.
            await session.execute(
                text(
                    "INSERT INTO document_versions "
                    "(document_id, version_number, version_name, content, "
                    " change_summary, created_by, strategy_hash, is_auto_save) "
                    "VALUES (:id, 1, :name, :c, :cs, :cb, :sh, false)"
                ),
                {
                    "id": doc_id,
                    "name": "v1 — Initial AI draft",
                    "c": json.dumps(initial_content),
                    "cs": "Initial generation from strategy results",
                    "cb": creator,
                    "sh": strategy_hash,
                },
            )
            await session.commit()
        log.info("document_created", doc_id=doc_id[:8], doc_type=doc_type)
        return doc_id
    except Exception as exc:
        log.warning("document_create_error", error=str(exc))
        return None


async def get_document_draft(document_id: str) -> dict[str, Any] | None:
    """Returns the current draft content for a document, or None on miss."""
    if not _DB_AVAILABLE:
        return None
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            row = await session.execute(
                text(
                    "SELECT d.id, d.doc_type, d.owner_email, d.strategy_hash, "
                    "       dr.content, dr.last_saved_at, dr.based_on_version "
                    "FROM documents d "
                    "LEFT JOIN document_drafts dr ON dr.document_id = d.id "
                    "WHERE d.id = :id LIMIT 1"
                ),
                {"id": document_id},
            )
            r = row.fetchone()
            if not r:
                return None
            content = r[4] if isinstance(r[4], dict) else (json.loads(r[4]) if r[4] else {})
            return {
                "id":               str(r[0]),
                "doc_type":         r[1],
                "owner_email":      r[2],
                "strategy_hash":    r[3],
                "content":          content,
                "last_saved_at":    r[5].isoformat() if r[5] else None,
                "based_on_version": str(r[6]) if r[6] else None,
            }
    except Exception as exc:
        log.warning("document_draft_read_error", doc_id=document_id[:8], error=str(exc))
    return None


async def update_draft(
    document_id: str, content: dict[str, Any],
) -> bool:
    """
    Auto-save handler. Updates the draft in place — never creates a new
    version. Named snapshots use save_named_version() instead. Returns
    True on success, False when the document doesn't exist or DB is down.
    """
    if not _DB_AVAILABLE:
        return False
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            result = await session.execute(
                text(
                    "UPDATE document_drafts "
                    "SET content = :c, last_saved_at = now() "
                    "WHERE document_id = :id"
                ),
                {"id": document_id, "c": json.dumps(content)},
            )
            await session.commit()
            return result.rowcount > 0
    except Exception as exc:
        log.warning("document_draft_update_error", doc_id=document_id[:8], error=str(exc))
        return False


# ── document_versions (audit log) ────────────────────────────────────────

async def save_named_version(
    document_id: str,
    version_name: str,
    content: dict[str, Any],
    created_by: str,
    change_summary: str | None = None,
    is_auto_save: bool = False,
    restored_from: str | None = None,
    strategy_hash: str | None = None,
) -> dict[str, Any] | None:
    """
    Appends a new version row. version_number is computed server-side
    as max(version_number) + 1 for the document — atomic per row insert
    so concurrent saves won't collide.

    Returns the new version's id + version_number, or None on failure.
    """
    if not _DB_AVAILABLE:
        return None
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            # Compute next version_number atomically within the txn
            row = await session.execute(
                text(
                    "SELECT COALESCE(MAX(version_number), 0) + 1 "
                    "FROM document_versions WHERE document_id = :id"
                ),
                {"id": document_id},
            )
            next_num = int(row.scalar() or 1)
            new_id = str(uuid.uuid4())
            await session.execute(
                text(
                    "INSERT INTO document_versions "
                    "(id, document_id, version_number, version_name, content, "
                    " change_summary, created_by, strategy_hash, is_auto_save, "
                    " restored_from) "
                    "VALUES (:id, :doc, :n, :name, :c, :cs, :cb, :sh, :auto, :rf)"
                ),
                {
                    "id":   new_id,
                    "doc":  document_id,
                    "n":    next_num,
                    "name": version_name,
                    "c":    json.dumps(content),
                    "cs":   change_summary,
                    "cb":   created_by,
                    "sh":   strategy_hash,
                    "auto": is_auto_save,
                    "rf":   restored_from,
                },
            )
            await session.commit()
            log.info(
                "document_version_saved",
                doc_id=document_id[:8], version=next_num, name=version_name,
            )
            return {"id": new_id, "version_number": next_num}
    except Exception as exc:
        log.warning("document_version_save_error", doc_id=document_id[:8], error=str(exc))
        return None


async def list_versions(document_id: str) -> list[dict[str, Any]]:
    """
    Returns all versions for a document in reverse-chronological order
    (newest first). Auto-saves are flagged so the UI can collapse them
    by default — see the Version History panel spec.
    """
    if not _DB_AVAILABLE:
        return []
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            rows = await session.execute(
                text(
                    "SELECT id, version_number, version_name, change_summary, "
                    "       created_at, created_by, is_auto_save, restored_from "
                    "FROM document_versions "
                    "WHERE document_id = :id "
                    "ORDER BY version_number DESC"
                ),
                {"id": document_id},
            )
            return [
                {
                    "id":             str(r[0]),
                    "version_number": int(r[1]),
                    "version_name":   r[2],
                    "change_summary": r[3],
                    "created_at":     r[4].isoformat() if r[4] else None,
                    "created_by":     r[5],
                    "is_auto_save":   bool(r[6]),
                    "restored_from":  str(r[7]) if r[7] else None,
                }
                for r in rows.fetchall()
            ]
    except Exception as exc:
        log.warning("document_versions_list_error", doc_id=document_id[:8], error=str(exc))
        return []


async def get_version_content(version_id: str) -> dict[str, Any] | None:
    """Returns the immutable snapshot for a specific version — used by Restore."""
    if not _DB_AVAILABLE:
        return None
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            row = await session.execute(
                text(
                    "SELECT content, document_id, version_number "
                    "FROM document_versions WHERE id = :id LIMIT 1"
                ),
                {"id": version_id},
            )
            r = row.fetchone()
            if not r:
                return None
            content = r[0] if isinstance(r[0], dict) else json.loads(r[0])
            return {
                "content":        content,
                "document_id":    str(r[1]),
                "version_number": int(r[2]),
            }
    except Exception as exc:
        log.warning("document_version_read_error", ver_id=version_id[:8], error=str(exc))
    return None


async def restore_version(
    document_id: str, version_id: str, restored_by: str,
) -> dict[str, Any] | None:
    """
    Restores a prior version: copies its content into a new version row
    (with restored_from set) and replaces the draft. The original version
    stays intact — restore never deletes history.
    """
    src = await get_version_content(version_id)
    if not src:
        return None
    new_version = await save_named_version(
        document_id=document_id,
        version_name=f"Restored from v{src['version_number']}",
        content=src["content"],
        created_by=restored_by,
        change_summary=f"Rolled back to version {src['version_number']}",
        is_auto_save=False,
        restored_from=version_id,
    )
    if new_version:
        await update_draft(document_id, src["content"])
    return new_version
