"""
tools/citation_findings.py — Citation Review Level-1 findings.

The Citation Review panel reads its top-level wrappers (Level 1) from
THIS module. Each wrapper is a high+medium-priority finding sourced
LIVE from the existing analytical state at panel-open time:

  * audit_findings — the latest substantive statistical audit run
    (status='complete', total_checks>0, no skipped layers).
    Per-finding rank derived from {status, severity}.

  * qa_results_cache.checklist_json — the most recent methodology
    audit's verdict for the current strategy_hash. Per-check rank
    derived from {status, action_type}. IN02 excluded — attestation
    state, not a citation-worthy finding (mirrors the QA badge
    exclusion from PR #176).

`seed_findings_for_generation(generation_id)` is the entry point. It
runs on every GET to /api/v1/citations/findings/{generation_id} (per
the design doc), UPSERTing findings rows so the team's prior
citation_finding_matches survive an unchanged finding across
sessions. Findings that are no longer high+medium priority are
deleted; their matches CASCADE.

Returns the structured findings list the endpoint surfaces to the
frontend.
"""
from __future__ import annotations

import json
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# Check IDs that NEVER surface as Level-1 findings. IN02 is the
# Academic Review attestation — its WARN state is by design, not a
# citation-worthy finding. Same exclusion the QA badge applies
# (frontend/src/stores/qaStore.ts _BADGE_EXCLUDED_CHECK_IDS).
_QA_EXCLUDED_CHECK_IDS: frozenset[str] = frozenset({"IN02"})

# Rank vocabulary. 'low' is dropped at seed time — not citation-worthy.
_RANK_HIGH = "high"
_RANK_MEDIUM = "medium"


def _rank_audit_finding(status: str, severity: str) -> str | None:
    """Maps an audit_findings row to {high, medium, None}.

    high   — status='fail' OR severity='critical'
    medium — status='warning' AND severity='warning'
    None   — everything else (info, pass, etc.) — DROPPED.
    """
    s = (status or "").strip().lower()
    sev = (severity or "").strip().lower()
    if s == "fail" or sev == "critical":
        return _RANK_HIGH
    if s == "warning" and sev == "warning":
        return _RANK_MEDIUM
    return None


def _rank_qa_check(status: str, action_type: str) -> str | None:
    """Maps a qa_results_cache checklist item to {high, medium, None}.

    high   — status='FAIL' OR action_type='code_fix'
    medium — status='WARN' AND action_type='methodology_decision'
    None   — everything else (PASS, INCOMPLETE/planned_extension,
             WARN/disclosure_required without methodology context).

    INCOMPLETE is intentionally NOT a finding source for Citation
    Review: an incomplete check is "we couldn't evaluate this", not
    "we found something that needs citation support". The QA panel's
    re-run flow handles INCOMPLETE; Citation Review doesn't.
    """
    s = (status or "").strip().upper()
    a = (action_type or "").strip().lower()
    if s == "FAIL" or a == "code_fix":
        return _RANK_HIGH
    if s == "WARN" and a == "methodology_decision":
        return _RANK_MEDIUM
    return None


async def _gather_audit_findings() -> list[dict[str, Any]]:
    """Reads the latest SUBSTANTIVE statistical audit's findings.

    'Substantive' uses the same predicate as
    tools.audit_engine.get_last_substantive_audit — status='complete',
    total_checks>0, no skipped layers. A hollow audit (the bug the
    cache-hit guard catches) has no findings to surface here.
    Resolved findings (audit_findings.resolved=true) are also dropped
    — the team has already addressed them.

    Returns [] on any DB error or when no substantive audit exists.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        # The latest substantive run + its findings, in ONE query.
        # The substantive filter is identical to is_substantive_audit
        # (audit_engine.py) — kept here as inline SQL rather than a
        # function call so this module has no audit_engine import
        # dependency (the gather happens at panel-open time and we
        # don't want to bring the audit-engine startup-side imports
        # into the citation-review code path).
        sql = (
            "SELECT f.id, f.check_name, f.metric, f.severity, "
            "       f.status, f.auditor_reasoning, f.discrepancy "
            "FROM audit_findings f "
            "JOIN audit_runs r ON r.id = f.audit_run_id "
            "WHERE r.id = ( "
            "    SELECT id FROM audit_runs "
            "    WHERE status = 'complete' "
            "      AND total_checks > 0 "
            "      AND COALESCE(layer_1_status, '') NOT IN "
            "          ('skip', 'skipped', 'skipped_no_data') "
            "      AND COALESCE(layer_2_status, '') NOT IN "
            "          ('skip', 'skipped', 'skipped_no_data') "
            "      AND COALESCE(layer_3_status, '') NOT IN "
            "          ('skip', 'skipped', 'skipped_no_data') "
            "    ORDER BY id DESC LIMIT 1 "
            ") "
            "  AND f.status IN ('fail', 'warning') "
            "  AND COALESCE(f.resolved, false) = false"
        )
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text(sql))
            return [
                {
                    "source": "audit",
                    "source_id": str(r[0]),
                    "title": (
                        f"{r[1]} — {r[2]}" if r[2] else str(r[1] or "")
                    ),
                    "description": (
                        str(r[5] or r[6] or "") or None
                    ),
                    "status": str(r[4] or "").lower() or None,
                    "severity": str(r[3] or "").lower() or None,
                    "rank": _rank_audit_finding(r[4], r[3]),
                }
                for r in rows.fetchall()
            ]
    except Exception as exc:  # noqa: BLE001
        log.warning("citation_findings_audit_read_failed",
                    error=str(exc), exc_type=type(exc).__name__)
        return []


async def _gather_qa_findings() -> list[dict[str, Any]]:
    """Reads the latest QA methodology verdict's high+medium-priority
    items from qa_results_cache.checklist_json.

    Pulls the most-recent verdict row (any tier — Tier 1 deterministic
    counts, Tier 2 Sonnet counts, Tier 3 Opus counts) and walks its
    checklist items. Excludes IN02 (attestation, not actionable for
    citation review). Filters to rank='high' or 'medium'.

    Returns [] on any DB error or when no QA cache row exists.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT checklist_json FROM qa_results_cache "
                "ORDER BY run_at DESC LIMIT 1"))
            found = row.fetchone()
        if not found or not found[0]:
            return []
        checklist = found[0]
        if isinstance(checklist, str):
            try:
                checklist = json.loads(checklist)
            except json.JSONDecodeError:
                return []
        items = (
            checklist.get("items") if isinstance(checklist, dict)
            else None
        ) or []
        out: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("check_id") or "").strip()
            if not cid or cid in _QA_EXCLUDED_CHECK_IDS:
                continue
            rank = _rank_qa_check(
                str(item.get("status") or ""),
                str(item.get("action_type") or ""))
            if rank is None:
                continue
            out.append({
                "source":      "qa",
                "source_id":   cid,
                "title":       str(item.get("check") or cid),
                "description": (str(item.get("evidence") or "")
                                or None),
                "status":      str(item.get("status") or "") or None,
                "severity":    None,
                "rank":        rank,
            })
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("citation_findings_qa_read_failed",
                    error=str(exc), exc_type=type(exc).__name__)
        return []


async def seed_findings_for_generation(
    generation_id: int,
) -> list[dict[str, Any]]:
    """Re-seeds the `findings` table for one generation_id and returns
    the fresh list. Called at the start of every Citation Review
    panel open via GET /api/v1/citations/findings/{generation_id}.

    UPSERT semantics: rows that already exist for the same
    (generation_id, source, source_id) keep their `id` so any
    citation_finding_matches rows referencing them survive. Rows that
    no longer appear in the seed are DELETED — their matches
    CASCADE.

    Returns: list of dicts with id / source / source_id / title /
    description / rank / status / severity, sorted by rank
    (high → medium) then by title. Empty list on any failure;
    fail-open contract — the panel still renders.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        audit_rows = await _gather_audit_findings()
        qa_rows = await _gather_qa_findings()
        all_rows = [r for r in (audit_rows + qa_rows)
                    if r.get("rank") in (_RANK_HIGH, _RANK_MEDIUM)]

        async with AsyncSessionLocal() as session:
            # 1. UPSERT each fresh finding. Unique constraint on
            # (generation_id, source, source_id) makes this idempotent.
            for r in all_rows:
                await session.execute(text(
                    "INSERT INTO findings "
                    "(generation_id, source, source_id, title, "
                    " description, rank, status, severity, "
                    " seeded_at) "
                    "VALUES (:g, :src, :sid, :t, :d, :r, :st, "
                    "        :sev, now()) "
                    "ON CONFLICT (generation_id, source, source_id) "
                    "DO UPDATE SET "
                    "  title       = EXCLUDED.title, "
                    "  description = EXCLUDED.description, "
                    "  rank        = EXCLUDED.rank, "
                    "  status      = EXCLUDED.status, "
                    "  severity    = EXCLUDED.severity, "
                    "  seeded_at   = EXCLUDED.seeded_at"
                ), {
                    "g":   int(generation_id),
                    "src": r["source"],
                    "sid": r["source_id"],
                    "t":   r["title"][:200],
                    "d":   r.get("description"),
                    "r":   r["rank"],
                    "st":  r.get("status"),
                    "sev": r.get("severity"),
                })

            # 2. DELETE findings rows that didn't make it into the
            # current seed. Their (source, source_id) tuples are no
            # longer present, so the matches cascade-delete with them.
            # See KNOWN LIMITATION in migration 045's docstring.
            if all_rows:
                # Build the IN (...) clause via tuples.
                pairs_sql = ", ".join(
                    f"(:src_{i}, :sid_{i})" for i in range(len(all_rows)))
                params: dict[str, Any] = {"g": int(generation_id)}
                for i, r in enumerate(all_rows):
                    params[f"src_{i}"] = r["source"]
                    params[f"sid_{i}"] = r["source_id"]
                await session.execute(text(
                    f"DELETE FROM findings "
                    f"WHERE generation_id = :g "
                    f"  AND (source, source_id) NOT IN ({pairs_sql})"
                ), params)
            else:
                # No fresh findings — clear the table for this gen.
                await session.execute(text(
                    "DELETE FROM findings WHERE generation_id = :g"
                ), {"g": int(generation_id)})

            await session.commit()

            # 3. Read back the canonical list (after UPSERT/DELETE)
            # with each finding's matched_count joined in.
            result = await session.execute(text(
                "SELECT f.id, f.source, f.source_id, f.title, "
                "       f.description, f.rank, f.status, f.severity, "
                "       COALESCE(m.matched_count, 0) AS matched_count "
                "FROM findings f "
                "LEFT JOIN ( "
                "  SELECT finding_id, COUNT(*) AS matched_count "
                "  FROM citation_finding_matches "
                "  GROUP BY finding_id "
                ") m ON m.finding_id = f.id "
                "WHERE f.generation_id = :g "
                "ORDER BY "
                "  CASE f.rank WHEN 'high' THEN 0 "
                "             WHEN 'medium' THEN 1 "
                "             ELSE 2 END, "
                "  f.title"
            ), {"g": int(generation_id)})
            return [
                {
                    "id":             int(row[0]),
                    "source":         str(row[1]),
                    "source_id":      str(row[2]),
                    "title":          str(row[3]),
                    "description":    (str(row[4]) if row[4] else None),
                    "rank":           str(row[5]),
                    "status":         (str(row[6]) if row[6] else None),
                    "severity":       (str(row[7]) if row[7] else None),
                    "matched_count":  int(row[8] or 0),
                }
                for row in result.fetchall()
            ]
    except Exception as exc:  # noqa: BLE001
        log.warning("citation_findings_seed_failed",
                    error=str(exc), exc_type=type(exc).__name__,
                    generation_id=generation_id)
        return []


async def get_matched_finding_ids_by_citation(
    generation_id: int,
) -> dict[int, list[int]]:
    """Returns a {citation_id → [finding_id, ...]} map for every
    citation that has at least one match against any of the findings
    in this generation's set. The frontend joins this against the
    citation list so each row knows which findings it's checked into.

    Returns {} on any failure. The panel still renders — citations
    simply read as unmatched until the next refresh.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return {}
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text(
                "SELECT m.citation_id, m.finding_id "
                "FROM citation_finding_matches m "
                "JOIN findings f ON f.id = m.finding_id "
                "WHERE f.generation_id = :g"
            ), {"g": int(generation_id)})
            out: dict[int, list[int]] = {}
            for row in rows.fetchall():
                cid, fid = int(row[0]), int(row[1])
                out.setdefault(cid, []).append(fid)
            return out
    except Exception as exc:  # noqa: BLE001
        log.warning("citation_findings_matches_read_failed",
                    error=str(exc), exc_type=type(exc).__name__)
        return {}


async def record_match(
    citation_id: int, finding_id: int, matched_by: str,
) -> dict[str, Any]:
    """Idempotent upsert of a citation_finding_matches row.

    A second call on the same (citation_id, finding_id) refreshes
    matched_at and matched_by — the team can re-attest without
    raising. Returns {ok, citation_id, finding_id, matched_by,
    matched_at} on success; {ok: false, error: …} on a DB error or
    on an FK violation (either id doesn't exist).
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return {"ok": False, "error": "database_unavailable"}
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "INSERT INTO citation_finding_matches "
                "(citation_id, finding_id, matched_by, matched_at) "
                "VALUES (:cid, :fid, :by, now()) "
                "ON CONFLICT (citation_id, finding_id) "
                "DO UPDATE SET "
                "  matched_at = now(), "
                "  matched_by = EXCLUDED.matched_by "
                "RETURNING matched_at"
            ), {"cid": int(citation_id), "fid": int(finding_id),
                "by": matched_by[:120]})
            result = row.fetchone()
            await session.commit()
        return {
            "ok":         True,
            "citation_id": int(citation_id),
            "finding_id":  int(finding_id),
            "matched_by":  matched_by,
            "matched_at":  result[0].isoformat() if result else None,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("citation_findings_match_failed",
                    error=str(exc), exc_type=type(exc).__name__,
                    citation_id=citation_id, finding_id=finding_id)
        return {"ok": False, "error": str(exc)}


async def remove_match(
    citation_id: int, finding_id: int,
) -> dict[str, Any]:
    """Idempotent delete of a citation_finding_matches row. Removing
    a non-existent match returns {ok: true, deleted: false} — same
    contract as the QA endpoint's mark-intentional DELETE.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return {"ok": False, "error": "database_unavailable"}
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "DELETE FROM citation_finding_matches "
                "WHERE citation_id = :cid AND finding_id = :fid "
                "RETURNING id"
            ), {"cid": int(citation_id), "fid": int(finding_id)})
            deleted = row.fetchone() is not None
            await session.commit()
        return {
            "ok":          True,
            "citation_id": int(citation_id),
            "finding_id":  int(finding_id),
            "deleted":     deleted,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("citation_findings_unmatch_failed",
                    error=str(exc), exc_type=type(exc).__name__,
                    citation_id=citation_id, finding_id=finding_id)
        return {"ok": False, "error": str(exc)}
