"""
tools/editor_drafts.py

Data-access layer for the in-platform document editor — the
editor_drafts (mutable working copy) and editor_draft_versions
(immutable named checkpoints) tables (migration 021).

Every function fails open: a database error is logged and a safe
default returned (None / [] / False), so an editor endpoint degrades
gracefully rather than 500-ing. The endpoints in main.py wrap these.
"""
from __future__ import annotations

import json
from typing import Any

import structlog

log = structlog.get_logger(__name__)

DOCUMENT_TYPES = ("midpoint_paper", "executive_brief", "presentation_deck",
                  "presentation_script", "analytical_appendix")
CREATED_FROM = ("generated", "uploaded", "manual")

# SELECT shape -- kept on the LEGACY (pre-migration-057) column
# set for backward compatibility with test environments + any
# deploy where migration 057 has not yet run. The Layer-3 columns
# (value_manifest, export_verification, data_hash) are still
# WRITTEN via update_value_manifest / update_export_verification
# below (those helpers already wrap the UPDATE in try/except), but
# READING them requires a separate selector. The export-time
# verification path in main.py reads value_manifest via
# get_draft_with_layer3 below; the default get_draft / list_drafts
# stay on the legacy SELECT so the existing test suite is unaffected.
_DRAFT_COLS = (
    "id, document_type, owner_email, title, content_json, content_text, "
    "word_count, version, is_current, is_deleted, created_from, "
    "created_at, updated_at, audit_warnings"
)
_DRAFT_COLS_LAYER3 = (
    _DRAFT_COLS
    + ", value_manifest, export_verification, data_hash"
)
_VERSION_COLS = (
    "id, draft_id, version, content_json, content_text, word_count, "
    "version_label, saved_at, saved_by"
)


def _session():
    """The async session factory, or None when no database is configured."""
    from database import AsyncSessionLocal
    return AsyncSessionLocal


def word_count(text: str | None) -> int:
    """Whitespace-delimited word count of a plain-text projection."""
    return len((text or "").split())


def _draft_row(r: Any) -> dict[str, Any]:
    base = {
        "id": r[0], "document_type": r[1], "owner_email": r[2],
        "title": r[3], "content_json": r[4], "content_text": r[5],
        "word_count": r[6], "version": r[7], "is_current": r[8],
        "is_deleted": r[9], "created_from": r[10],
        "created_at": r[11].isoformat() if r[11] else None,
        "updated_at": r[12].isoformat() if r[12] else None,
        "audit_warnings": r[13],
    }
    # Layer 3 columns (value_manifest, export_verification,
    # data_hash) -- only populated when the caller used the LAYER3
    # SELECT shape. Defaults to None for the legacy SELECT so
    # downstream consumers can `.get("value_manifest") or {}`
    # uniformly without checking for KeyError.
    base["value_manifest"] = r[14] if len(r) > 14 else None
    base["export_verification"] = r[15] if len(r) > 15 else None
    base["data_hash"] = r[16] if len(r) > 16 else None
    return base


async def get_draft_with_layer3(
    draft_id: int,
) -> dict[str, Any] | None:
    """Layer 3 variant of get_draft -- includes value_manifest,
    export_verification, and data_hash columns. Used by the
    export-time verification path in _editor_export. Returns
    None on cache miss or DB unavailable.

    Falls back to the legacy SELECT (and returns the draft
    without the Layer-3 fields) when migration 057 hasn't run --
    so the export path continues to ship the file even on a
    pre-Layer-3 environment, just without verification."""
    try:
        from sqlalchemy import text
        sf = _session()
        if sf is None:
            return None
        async with sf() as s:
            try:
                row = await s.execute(text(
                    f"SELECT {_DRAFT_COLS_LAYER3} FROM editor_drafts "
                    "WHERE id = :i AND is_deleted = false"),
                    {"i": draft_id})
                r = row.fetchone()
                if r is None:
                    return None
                return _draft_row(r)
            except Exception:  # noqa: BLE001
                # Pre-Layer-3 schema -- retry with legacy columns.
                # The export path tolerates None on Layer-3 fields
                # (verification short-circuits as 'no_value_manifest').
                # The first SELECT failed and the session is now in
                # an aborted-transaction state; PostgreSQL refuses
                # every subsequent statement on the same connection
                # with InFailedSQLTransactionError until a rollback
                # clears the state. Mirror the rollback the sibling
                # get_current_draft_with_layer3 already does so the
                # legacy fallback SELECT can actually run instead of
                # poisoning the session for the SSE flow's later
                # queries.
                try:
                    await s.rollback()
                except Exception:  # noqa: BLE001
                    pass
                row = await s.execute(text(
                    f"SELECT {_DRAFT_COLS} FROM editor_drafts "
                    "WHERE id = :i AND is_deleted = false"),
                    {"i": draft_id})
                r = row.fetchone()
                return _draft_row(r) if r is not None else None
    except Exception as exc:  # noqa: BLE001
        log.warning("editor_get_draft_layer3_failed", error=str(exc))
        return None


def _version_row(r: Any) -> dict[str, Any]:
    return {
        "id": r[0], "draft_id": r[1], "version": r[2],
        "content_json": r[3], "content_text": r[4], "word_count": r[5],
        "version_label": r[6],
        "saved_at": r[7].isoformat() if r[7] else None,
        "saved_by": r[8],
    }


async def list_drafts(owner_email: str) -> list[dict[str, Any]]:
    """Every non-deleted draft owned by this user, newest update first.
    Owner-scoped retained for tests / legacy callers; the API list
    endpoint now uses list_all_drafts (June 24 2026 -- team members
    share access to every document)."""
    try:
        from sqlalchemy import text
        sf = _session()
        if sf is None:
            return []
        async with sf() as s:
            rows = await s.execute(text(
                f"SELECT {_DRAFT_COLS} FROM editor_drafts "
                "WHERE owner_email = :e AND is_deleted = false "
                "ORDER BY updated_at DESC"), {"e": owner_email})
            return [_draft_row(r) for r in rows.fetchall()]
    except Exception as exc:  # noqa: BLE001
        log.warning("editor_list_drafts_failed", error=str(exc))
        return []


async def list_all_drafts() -> list[dict[str, Any]]:
    """Every non-deleted draft across all owners, newest update
    first. Used by the team-member-gated drafts list endpoint --
    documents are team-shared, so Mike can open a brief Bob
    generated and vice versa."""
    try:
        from sqlalchemy import text
        sf = _session()
        if sf is None:
            return []
        async with sf() as s:
            rows = await s.execute(text(
                f"SELECT {_DRAFT_COLS} FROM editor_drafts "
                "WHERE is_deleted = false "
                "ORDER BY updated_at DESC"))
            return [_draft_row(r) for r in rows.fetchall()]
    except Exception as exc:  # noqa: BLE001
        log.warning("editor_list_all_drafts_failed", error=str(exc))
        return []


async def get_draft(draft_id: int) -> dict[str, Any] | None:
    """One non-deleted draft by id, or None."""
    try:
        from sqlalchemy import text
        sf = _session()
        if sf is None:
            return None
        async with sf() as s:
            row = await s.execute(text(
                f"SELECT {_DRAFT_COLS} FROM editor_drafts "
                "WHERE id = :i AND is_deleted = false"), {"i": draft_id})
            found = row.fetchone()
            return _draft_row(found) if found else None
    except Exception as exc:  # noqa: BLE001
        log.warning("editor_get_draft_failed", error=str(exc))
        return None


async def get_current_draft(
    owner_email: str, document_type: str,
) -> dict[str, Any] | None:
    """
    The user's current draft for a document type — the one Academic
    Review reads in preference to an uploaded file. None when the user
    has no draft of that type.
    """
    try:
        from sqlalchemy import text
        sf = _session()
        if sf is None:
            return None
        async with sf() as s:
            row = await s.execute(text(
                f"SELECT {_DRAFT_COLS} FROM editor_drafts "
                "WHERE owner_email = :e AND document_type = :t "
                "AND is_current = true AND is_deleted = false "
                "ORDER BY updated_at DESC LIMIT 1"),
                {"e": owner_email, "t": document_type})
            found = row.fetchone()
            return _draft_row(found) if found else None
    except Exception as exc:  # noqa: BLE001
        log.warning("editor_get_current_draft_failed", error=str(exc))
        return None


async def get_current_draft_with_layer3(
    owner_email: str, document_type: str,
) -> dict[str, Any] | None:
    """Layer 3b (June 21 2026) -- get_current_draft variant that
    includes the Layer-3 columns (value_manifest, export_verification,
    data_hash). Used by /api/v1/report/readiness to surface per-
    document export_verification status to the Reports page badges.

    Falls back gracefully on pre-Layer-3 schemas: a SELECT against
    the LAYER3 column set on a database that hasn't run migration
    057 raises, and the helper retries with the legacy column set so
    the readiness endpoint keeps responding rather than 500ing. The
    frontend badge then shows the neutral 'Not yet exported' state
    because export_verification will be None."""
    try:
        from sqlalchemy import text
        sf = _session()
        if sf is None:
            return None
        async with sf() as s:
            try:
                row = await s.execute(text(
                    f"SELECT {_DRAFT_COLS_LAYER3} FROM editor_drafts "
                    "WHERE owner_email = :e AND document_type = :t "
                    "AND is_current = true AND is_deleted = false "
                    "ORDER BY updated_at DESC LIMIT 1"),
                    {"e": owner_email, "t": document_type})
                found = row.fetchone()
                return _draft_row(found) if found else None
            except Exception:  # noqa: BLE001
                # Pre-Layer-3 schema -- retry with the legacy column
                # set so the readiness endpoint keeps working on a
                # database that hasn't run migration 057 yet.
                # The first SELECT failed and the session is now in
                # an aborted-transaction state; PostgreSQL refuses
                # every subsequent statement on the same connection
                # with InFailedSQLTransactionError until a rollback
                # clears the state. Roll back before the legacy retry
                # so the second SELECT actually runs.
                try:
                    await s.rollback()
                except Exception:  # noqa: BLE001
                    # Rollback itself can fail if the session is too
                    # broken; the outer try/except still degrades
                    # gracefully to None below.
                    pass
                row = await s.execute(text(
                    f"SELECT {_DRAFT_COLS} FROM editor_drafts "
                    "WHERE owner_email = :e AND document_type = :t "
                    "AND is_current = true AND is_deleted = false "
                    "ORDER BY updated_at DESC LIMIT 1"),
                    {"e": owner_email, "t": document_type})
                found = row.fetchone()
                return _draft_row(found) if found else None
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "editor_get_current_draft_layer3_failed", error=str(exc))
        return None


async def create_draft(
    document_type: str, owner_email: str, title: str,
    content_json: Any, content_text: str | None,
    created_from: str = "manual",
    audit_warnings: Any | None = None,
) -> dict[str, Any] | None:
    """
    Creates a draft and makes it the current one for this owner +
    document_type — every other draft of the same type is set
    is_current = false, so there is exactly one current draft per type.

    audit_warnings — optional dict carrying the per-check flag list
    from the post-generation audit (tools.document_audit). Stored as
    JSONB. Frontend reads it on draft load and renders a banner.
    None on a clean run.
    """
    try:
        from sqlalchemy import text
        sf = _session()
        if sf is None:
            return None
        cj = json.dumps(content_json) if content_json is not None else None
        aw = (json.dumps(audit_warnings)
              if audit_warnings is not None else None)
        async with sf() as s:
            # June 24 2026 -- clear is_current on EVERY existing row
            # for this document_type, not just rows owned by the
            # generating user. Documents are team-shared (any team
            # member can open any draft), so two team members
            # generating the same doc_type used to leave both rows
            # with is_current=true. The unique partial index
            # editor_drafts_one_current_per_type (migration 062)
            # enforces this at the DB layer; this UPDATE is the
            # application-layer guard that runs first so the index
            # never has to reject.
            await s.execute(text(
                "UPDATE editor_drafts SET is_current = false "
                "WHERE document_type = :t "
                "AND is_current = true "
                "AND is_deleted = false"),
                {"t": document_type})
            row = await s.execute(text(
                "INSERT INTO editor_drafts (document_type, owner_email, "
                "title, content_json, content_text, word_count, "
                "created_from, is_current, audit_warnings) VALUES "
                "(:t, :e, :ti, CAST(:cj AS JSONB), :ct, :wc, :cf, true, "
                "CAST(:aw AS JSONB)) "
                f"RETURNING {_DRAFT_COLS}"),
                {"t": document_type, "e": owner_email, "ti": title,
                 "cj": cj, "ct": content_text,
                 "wc": word_count(content_text), "cf": created_from,
                 "aw": aw})
            found = row.fetchone()
            await s.commit()
            return _draft_row(found) if found else None
    except Exception as exc:  # noqa: BLE001
        log.warning("editor_create_draft_failed", error=str(exc))
        return None


async def update_draft(
    draft_id: int, content_json: Any, content_text: str | None,
    word_count_override: int | None = None,
) -> bool:
    """
    Auto-save — overwrites the working content and bumps updated_at.
    Silent: it does NOT create a version. Returns True on a write.
    """
    try:
        from sqlalchemy import text
        sf = _session()
        if sf is None:
            return False
        cj = json.dumps(content_json) if content_json is not None else None
        wc = (word_count_override if word_count_override is not None
              else word_count(content_text))
        async with sf() as s:
            res = await s.execute(text(
                "UPDATE editor_drafts SET content_json = CAST(:cj AS JSONB), "
                "content_text = :ct, word_count = :wc, updated_at = now() "
                "WHERE id = :i AND is_deleted = false"),
                {"cj": cj, "ct": content_text, "wc": wc, "i": draft_id})
            await s.commit()
            return res.rowcount > 0
    except Exception as exc:  # noqa: BLE001
        log.warning("editor_update_draft_failed", error=str(exc))
        return False


async def update_audit_warnings(
    draft_id: int, audit_warnings: dict[str, Any] | None,
) -> bool:
    """PR #336 -- update the audit_warnings JSONB column on a draft
    row in place. Used by the editor-export path to persist a
    post-edit audit result so the next editor render surfaces the
    flags from the actual edited text. NULL clears the column when
    the audit found nothing."""
    try:
        from sqlalchemy import text
        sf = _session()
        if sf is None:
            return False
        aw = json.dumps(audit_warnings) if audit_warnings else None
        async with sf() as s:
            res = await s.execute(text(
                "UPDATE editor_drafts "
                "SET audit_warnings = CAST(:aw AS JSONB), "
                "    updated_at = now() "
                "WHERE id = :i AND is_deleted = false"),
                {"aw": aw, "i": draft_id})
            await s.commit()
            return res.rowcount > 0
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "editor_update_audit_warnings_failed", error=str(exc))
        return False


async def update_value_manifest(
    draft_id: int, value_manifest: dict[str, Any] | None,
    data_hash: str | None = None,
) -> bool:
    """Layer 3 (June 21 2026) -- persists the substitution-table
    value manifest on the draft at generation time. Used by the
    brief / deck / appendix generators after the editor draft is
    created; the manifest is the authoritative reference the
    export-time verification reads.

    Also stores the generation data_hash on the draft (if the
    column exists -- migration 057 may have added it; the UPDATE
    silently no-ops the data_hash field on schemas without it via
    a try/except fallback).

    Fail-open: a write failure logs and returns False; the
    document still ships, the export-time verification just
    won't have a manifest to verify against."""
    try:
        from sqlalchemy import text
        sf = _session()
        if sf is None:
            return False
        vm = json.dumps(value_manifest) if value_manifest else None
        async with sf() as s:
            # Try the path that includes data_hash first. The
            # column was added by migration 057 alongside
            # value_manifest, but a pre-057 column-set still
            # works -- the inner except falls back to value-
            # manifest-only.
            #
            # June 21 2026 -- the inner retry must roll back the
            # session before re-executing. PostgreSQL puts the
            # session into an aborted-transaction state on any
            # statement failure (e.g. UndefinedColumn on the
            # data_hash field), and the retry on the SAME session
            # then fails with InFailedSQLTransactionError. Same
            # fix shape as PR #360 applied to
            # get_current_draft_with_layer3.
            try:
                res = await s.execute(text(
                    "UPDATE editor_drafts "
                    "SET value_manifest = CAST(:vm AS JSONB), "
                    "    data_hash = :dh, "
                    "    updated_at = now() "
                    "WHERE id = :i AND is_deleted = false"),
                    {"vm": vm, "dh": data_hash, "i": draft_id})
            except Exception:  # noqa: BLE001
                try:
                    await s.rollback()
                except Exception:  # noqa: BLE001
                    # Rollback can fail if the session is too
                    # broken; the outer try/except still
                    # degrades gracefully to False below.
                    pass
                res = await s.execute(text(
                    "UPDATE editor_drafts "
                    "SET value_manifest = CAST(:vm AS JSONB), "
                    "    updated_at = now() "
                    "WHERE id = :i AND is_deleted = false"),
                    {"vm": vm, "i": draft_id})
            await s.commit()
            return res.rowcount > 0
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "editor_update_value_manifest_failed", error=str(exc))
        return False


async def update_export_verification(
    draft_id: int, verification: dict[str, Any] | None,
) -> bool:
    """Layer 3 -- persist the verify_export_against_cache result
    on the draft after each export. Frontend status badges read
    this on the next draft load (no per-card round-trip needed)."""
    try:
        from sqlalchemy import text
        sf = _session()
        if sf is None:
            return False
        ev = json.dumps(verification) if verification else None
        async with sf() as s:
            res = await s.execute(text(
                "UPDATE editor_drafts "
                "SET export_verification = CAST(:ev AS JSONB), "
                "    updated_at = now() "
                "WHERE id = :i AND is_deleted = false"),
                {"ev": ev, "i": draft_id})
            await s.commit()
            return res.rowcount > 0
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "editor_update_export_verification_failed",
            error=str(exc))
        return False


async def save_version(
    draft_id: int, version_label: str | None, saved_by: str | None,
) -> dict[str, Any] | None:
    """
    Saves a named checkpoint: snapshots the draft's current content into
    editor_draft_versions at the draft's current version number, then
    increments the draft's version. Returns the new version row.
    """
    try:
        from sqlalchemy import text
        sf = _session()
        if sf is None:
            return None
        async with sf() as s:
            d = await s.execute(text(
                "SELECT version, content_json, content_text, word_count "
                "FROM editor_drafts WHERE id = :i AND is_deleted = false"),
                {"i": draft_id})
            draft = d.fetchone()
            if draft is None:
                return None
            cur_version = draft[0]
            row = await s.execute(text(
                "INSERT INTO editor_draft_versions (draft_id, version, "
                "content_json, content_text, word_count, version_label, "
                "saved_by) VALUES (:d, :v, CAST(:cj AS JSONB), :ct, :wc, "
                f":vl, :sb) RETURNING {_VERSION_COLS}"),
                {"d": draft_id, "v": cur_version,
                 "cj": json.dumps(draft[1]) if draft[1] is not None else None,
                 "ct": draft[2], "wc": draft[3],
                 "vl": version_label, "sb": saved_by})
            found = row.fetchone()
            await s.execute(text(
                "UPDATE editor_drafts SET version = version + 1, "
                "updated_at = now() WHERE id = :i"), {"i": draft_id})
            await s.commit()
            return _version_row(found) if found else None
    except Exception as exc:  # noqa: BLE001
        log.warning("editor_save_version_failed", error=str(exc))
        return None


async def list_versions(draft_id: int) -> list[dict[str, Any]]:
    """Every saved version of a draft, newest first."""
    try:
        from sqlalchemy import text
        sf = _session()
        if sf is None:
            return []
        async with sf() as s:
            rows = await s.execute(text(
                f"SELECT {_VERSION_COLS} FROM editor_draft_versions "
                "WHERE draft_id = :d ORDER BY version DESC"),
                {"d": draft_id})
            return [_version_row(r) for r in rows.fetchall()]
    except Exception as exc:  # noqa: BLE001
        log.warning("editor_list_versions_failed", error=str(exc))
        return []


async def restore_version(
    draft_id: int, version_id: int,
) -> dict[str, Any] | None:
    """
    Restores a saved version's content as the draft's current content.
    Returns the updated draft, or None if the version does not belong to
    the draft.
    """
    try:
        from sqlalchemy import text
        sf = _session()
        if sf is None:
            return None
        async with sf() as s:
            v = await s.execute(text(
                "SELECT content_json, content_text, word_count "
                "FROM editor_draft_versions "
                "WHERE id = :vi AND draft_id = :d"),
                {"vi": version_id, "d": draft_id})
            ver = v.fetchone()
            if ver is None:
                return None
            await s.execute(text(
                "UPDATE editor_drafts SET content_json = CAST(:cj AS JSONB), "
                "content_text = :ct, word_count = :wc, updated_at = now() "
                "WHERE id = :i AND is_deleted = false"),
                {"cj": json.dumps(ver[0]) if ver[0] is not None else None,
                 "ct": ver[1], "wc": ver[2], "i": draft_id})
            await s.commit()
        return await get_draft(draft_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("editor_restore_version_failed", error=str(exc))
        return None


async def soft_delete_draft(draft_id: int) -> bool:
    """Soft-deletes a draft — is_deleted = true, is_current = false."""
    try:
        from sqlalchemy import text
        sf = _session()
        if sf is None:
            return False
        async with sf() as s:
            res = await s.execute(text(
                "UPDATE editor_drafts SET is_deleted = true, "
                "is_current = false, updated_at = now() "
                "WHERE id = :i AND is_deleted = false"), {"i": draft_id})
            await s.commit()
            return res.rowcount > 0
    except Exception as exc:  # noqa: BLE001
        log.warning("editor_soft_delete_failed", error=str(exc))
        return False
