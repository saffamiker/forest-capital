"""
tools/audit_engine.py — the statistical audit orchestrator.

start_audit() claims the run row and fires the three layers in the
background; _execute_audit() runs Layer 1 (raw data), Layer 2
(independent recomputation by claude-opus-4-7) and Layer 3 (consistency)
in sequence, stores every finding, and finalises the audit_runs row.

A 'running' audit_runs row is the concurrency lock — a second run while
one is in flight returns already_running. format_audit_report() renders
a run as the downloadable text report for the Analytical Appendix.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_audit_bg_tasks: set = set()

# A run still 'running' past this many minutes is treated as hung — a
# crash, an out-of-memory kill or a redeploy stopped _execute_audit
# before it could finalise the row. fail_stale_audits() marks such a row
# 'failed', which releases the concurrency lock the 'running' row
# represents. Without this a single hung run blocks every future audit
# indefinitely.
_AUDIT_TIMEOUT_MINUTES = 15
_TIMEOUT_REASON = "timeout — run exceeded 15 minutes"


def _iso(value: Any) -> str | None:
    try:
        return value.isoformat() if value is not None else None
    except Exception:  # noqa: BLE001
        return None


# ── audit_runs / audit_findings persistence ───────────────────────────────────

async def fail_stale_audits() -> int:
    """
    Marks every audit_runs row stuck in 'running' past
    _AUDIT_TIMEOUT_MINUTES as 'failed' — releasing the concurrency lock
    and recording the reason in metadata.timeout_reason. Returns the
    number of rows reaped.

    Called from is_audit_running() (so every lock check reaps first) and
    from the status-poll endpoints, so a hung run is cleared within one
    poll cycle of crossing the timeout. Fail-open.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return 0
        async with AsyncSessionLocal() as session:
            # The CASTs are required: asyncpg cannot infer the type of a
            # bind parameter inside jsonb_build_object / make_interval and
            # raises IndeterminateDatatypeError without them.
            res = await session.execute(text(
                "UPDATE audit_runs SET status = 'failed', "
                "completed_at = now(), "
                "metadata = COALESCE(metadata, '{}'::jsonb) || "
                "  jsonb_build_object('timeout_reason', CAST(:reason AS text)) "
                "WHERE status = 'running' "
                "AND triggered_at < now() "
                "  - make_interval(mins => CAST(:mins AS integer)) "
                "RETURNING id"
            ), {"reason": _TIMEOUT_REASON, "mins": _AUDIT_TIMEOUT_MINUTES})
            reaped = [int(r[0]) for r in res.fetchall()]
            await session.commit()
        for rid in reaped:
            log.warning("audit_run_timed_out", run_id=rid,
                        timeout_minutes=_AUDIT_TIMEOUT_MINUTES)
        return len(reaped)
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_fail_stale_failed", error=str(exc))
        return 0


async def is_audit_running() -> int | None:
    """The id of an audit still in the 'running' state — the statistical
    concurrency lock — or None. A run stuck past the 15-minute timeout is
    reaped (marked failed) first, so a hung run never holds the lock.
    Fail-open: a database error reports None."""
    await fail_stale_audits()
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT id FROM audit_runs WHERE status = 'running' "
                "ORDER BY id DESC LIMIT 1"))
            found = row.fetchone()
            return int(found[0]) if found else None
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_running_check_failed", error=str(exc))
        return None


async def _create_running_audit(triggered_by: str, email: str) -> int | None:
    """Inserts the 'running' audit_runs row — the report placeholder and
    the concurrency lock. Returns its id."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "INSERT INTO audit_runs (triggered_by, triggered_by_email, "
                "status) VALUES (:tb, :em, 'running') RETURNING id"
            ), {"tb": triggered_by, "em": email})
            new_id = row.scalar()
            await session.commit()
            return int(new_id) if new_id is not None else None
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_create_running_failed", error=str(exc))
        return None


# String() column limits on audit_findings (migration 017) — a finding
# whose value exceeds one of these is truncated rather than dropped.
_FINDING_COLUMN_LIMITS = {
    "check_name": 120, "metric": 80, "strategy": 80,
    "severity": 10, "status": 10, "raw_inputs_hash": 64,
}


def _trunc(value: Any, limit: int) -> Any:
    """Truncates a value to a column's character limit. None stays None."""
    if value is None:
        return None
    text_value = str(value)
    return text_value[:limit] if len(text_value) > limit else text_value


async def _store_findings(run_id: int, findings: list[dict[str, Any]]) -> None:
    """
    Inserts the audit findings for a run, COMMITTING PER ROW so one bad
    finding cannot drop the whole batch. String fields are truncated to
    their column limits before insert. Fail-open: a row that still fails
    is logged and skipped; a partial store logs a summary. The run's
    summary counts are persisted separately, so a findings-store failure
    must never go silent.
    """
    if not findings:
        return
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_store_findings_failed", run_id=run_id, error=str(exc))
        return

    stored = 0
    skipped = 0
    try:
        async with AsyncSessionLocal() as session:
            for fnd in findings:
                try:
                    await session.execute(text(
                        "INSERT INTO audit_findings (audit_run_id, layer, "
                        "check_name, metric, strategy, severity, status, "
                        "platform_value, auditor_value, discrepancy, "
                        "formula_used, raw_inputs_hash, auditor_reasoning) "
                        "VALUES (:rid, :layer, :cn, :metric, :strat, :sev, "
                        ":status, :pv, :av, :disc, :fu, :hash, :reason)"
                    ), {"rid": run_id, "layer": fnd["layer"],
                        "cn": _trunc(fnd.get("check_name"),
                                     _FINDING_COLUMN_LIMITS["check_name"]),
                        "metric": _trunc(fnd.get("metric"),
                                         _FINDING_COLUMN_LIMITS["metric"]),
                        "strat": _trunc(fnd.get("strategy"),
                                        _FINDING_COLUMN_LIMITS["strategy"]),
                        "sev": _trunc(fnd.get("severity"),
                                      _FINDING_COLUMN_LIMITS["severity"]),
                        "status": _trunc(fnd.get("status"),
                                         _FINDING_COLUMN_LIMITS["status"]),
                        "pv": fnd.get("platform_value"),
                        "av": fnd.get("auditor_value"),
                        "disc": fnd.get("discrepancy"),
                        "fu": fnd.get("formula_used"),
                        "hash": _trunc(fnd.get("raw_inputs_hash"),
                                       _FINDING_COLUMN_LIMITS["raw_inputs_hash"]),
                        "reason": fnd.get("auditor_reasoning")})
                    await session.commit()
                    stored += 1
                except Exception as exc:  # noqa: BLE001
                    await session.rollback()
                    skipped += 1
                    log.warning("audit_store_finding_skipped", run_id=run_id,
                                layer=fnd.get("layer"),
                                check_name=fnd.get("check_name"),
                                error=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_store_findings_failed", run_id=run_id, error=str(exc))
        return
    if skipped:
        log.warning("audit_store_findings_partial", run_id=run_id,
                    stored=stored, total=len(findings), skipped=skipped)


async def _finalise_audit(
    run_id: int, *, status: str, layer_statuses: dict[str, str],
    counts: dict[str, int], metadata: dict[str, Any],
    data_hash: str | None = None,
) -> None:
    """Writes the completed audit_runs row. data_hash is the lightweight
    fingerprint of the data this run verified — smart audit caching
    compares it against the live fingerprint. Fail-open."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return
        async with AsyncSessionLocal() as session:
            await session.execute(text(
                "UPDATE audit_runs SET status = :st, "
                "layer_1_status = :l1, layer_2_status = :l2, "
                "layer_3_status = :l3, total_checks = :tc, passed = :p, "
                "failed = :fl, warnings = :w, completed_at = now(), "
                "data_hash = :dh, "
                "metadata = CAST(:md AS jsonb) WHERE id = :id"
            ), {"st": status, "l1": layer_statuses.get("1"),
                "l2": layer_statuses.get("2"), "l3": layer_statuses.get("3"),
                "tc": counts["total"], "p": counts["passed"],
                "fl": counts["failed"], "w": counts["warnings"],
                "dh": data_hash,
                "md": json.dumps(metadata), "id": run_id})
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_finalise_failed", run_id=run_id, error=str(exc))


async def get_last_completed_audit_hash() -> str | None:
    """The data_hash of the most recent COMPLETED audit run — what smart
    audit caching compares the live data fingerprint against. None when
    there is no completed run yet (or on a database error — fail-open)."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT data_hash FROM audit_runs WHERE status = 'complete' "
                "ORDER BY id DESC LIMIT 1"))
            found = row.fetchone()
            return str(found[0]) if found and found[0] else None
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_last_hash_check_failed", error=str(exc))
        return None


async def get_last_substantive_audit() -> dict[str, Any] | None:
    """The most recent COMPLETED audit that actually ran the layers — at
    least one check landed AND no layer was skipped/no-data. Used by the
    /api/v1/audit/run endpoint to serve a cache hit when the current
    data hash matches a prior real run, instead of creating a new
    hollow audit_runs row with 0 checks.

    May 26 2026 — submission-night fix. Without this, the smart-audit-
    caching layer at `run_full_audit` correctly skipped re-runs on an
    unchanged data hash, BUT the manual endpoint did not — every Run
    Full Audit click on production created a new audit_runs row that
    then skipped every layer (cache cold for that specific request
    path) and stored a zero-check 'complete' row. The PDF then
    rendered "this layer was skipped" everywhere.

    Now the endpoint asks this function first: if a real complete run
    already exists for the current hash, return it instead of creating
    a new row. A new row is created only when force=true OR the hash
    has changed OR no substantive run exists yet.

    "Substantive" gate:
      status = 'complete'
      AND total_checks > 0
      AND no layer reports 'skip' / 'skipped_no_data' / null

    Fail-open: a database error returns None; the endpoint then falls
    through to start_audit (the existing path), so an outage never
    blocks a manual audit attempt.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            # SQL pre-filter — fast path, excludes obvious hollow rows
            # at the DB. The Python validator below catches anything
            # the SQL filter doesn't (alternate layer-status spellings,
            # rows with non-zero total_checks but every layer skipped,
            # etc). May 26 2026 follow-up — the user reported a hollow
            # row (audit #40) was slipping past the SQL alone.
            #
            # We pull the TOP 5 candidates ordered by id DESC and walk
            # them in Python so a single hollow row near the head
            # doesn't shadow a substantive row immediately below it.
            row = await session.execute(text(
                "SELECT id, triggered_by, triggered_at, "
                "       triggered_by_email, status, "
                "       layer_1_status, layer_2_status, layer_3_status, "
                "       total_checks, passed, failed, warnings, "
                "       completed_at, metadata, data_hash "
                "FROM audit_runs "
                "WHERE status = 'complete' "
                "  AND total_checks > 0 "
                "  AND COALESCE(layer_1_status, '') NOT IN "
                "      ('skip', 'skipped', 'skipped_no_data') "
                "  AND COALESCE(layer_2_status, '') NOT IN "
                "      ('skip', 'skipped', 'skipped_no_data') "
                "  AND COALESCE(layer_3_status, '') NOT IN "
                "      ('skip', 'skipped', 'skipped_no_data') "
                "ORDER BY id DESC LIMIT 5"))
            candidates = row.fetchall()
        if not candidates:
            return None
        for found in candidates:
            d = _run_row(found)
            if is_substantive_audit(d):
                return d
            # Diagnostic — every hollow row that slipped past the SQL
            # filter is logged so the operator can see exactly which
            # column tripped the Python re-check. Helps explain why a
            # specific audit row isn't being served as a cache hit.
            log.info(
                "audit_substantive_rejected",
                audit_id=d.get("id"),
                status=d.get("status"),
                total_checks=d.get("total_checks"),
                passed=d.get("passed"),
                failed=d.get("failed"),
                warnings=d.get("warnings"),
                layer_1_status=d.get("layer_1_status"),
                layer_2_status=d.get("layer_2_status"),
                layer_3_status=d.get("layer_3_status"),
            )
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_substantive_lookup_failed", error=str(exc))
        return None


# Layer-status values that mean "this layer did not actually run".
# Treated case-insensitively. Empty string and None are also rejected
# — a layer with no recorded status is not evidence that it ran.
_SKIPPED_LAYER_VALUES: frozenset[str] = frozenset({
    "skip", "skipped", "skipped_no_data", "no_data", "",
})


def is_substantive_audit(row: dict[str, Any] | None) -> bool:
    """True iff a candidate audit_runs row represents a real, fully-
    executed audit run — three layers ran, at least one check landed,
    no layer's status reads as 'skipped'. Used by
    get_last_substantive_audit AND by the /api/v1/audit/run endpoint's
    cache-hit guard so the validator runs on both sides of the call.

    The double-check is deliberate — the SQL filter is fast but the
    user reported audit #40 (a hollow row) slipping through. Whatever
    column shape was responsible (an unexpected layer-status spelling,
    a non-zero total_checks on an otherwise empty run, a NULL where
    the filter expected a string), this Python check catches it. May
    26 2026 follow-up.
    """
    if not row or not isinstance(row, dict):
        return False
    # Status must be 'complete'. The loud-failure branch in
    # _execute_audit writes 'failed' for hollow runs (post-PR-#173),
    # so a fresh hollow row would already be excluded here. Legacy
    # hollow rows (pre-PR-#173) carry status='complete' and need the
    # downstream checks to catch them.
    if str(row.get("status") or "").lower() != "complete":
        return False
    # Must carry at least one finding. A run with zero total_checks
    # is not substantive regardless of how every other column reads.
    tc = row.get("total_checks")
    if not isinstance(tc, int) or tc <= 0:
        return False
    # Defence in depth — passed+failed+warnings should sum to
    # total_checks on a real run; a row where the sum is zero
    # despite a non-zero total_checks is corrupt and not substantive.
    p = row.get("passed") or 0
    f = row.get("failed") or 0
    w = row.get("warnings") or 0
    try:
        if (int(p) + int(f) + int(w)) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    # Every layer must have actually run. The check is case-
    # insensitive and includes the empty-string sentinel that
    # COALESCE produces for a NULL.
    for k in ("layer_1_status", "layer_2_status", "layer_3_status"):
        v = str(row.get(k) or "").strip().lower()
        if v in _SKIPPED_LAYER_VALUES:
            return False
    return True


async def get_audit_runs() -> list[dict[str, Any]]:
    """Every audit run, newest first — summary rows only (no findings)."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text(
                "SELECT id, triggered_by, triggered_at, triggered_by_email, "
                "status, layer_1_status, layer_2_status, layer_3_status, "
                "total_checks, passed, failed, warnings, completed_at, "
                "metadata, data_hash FROM audit_runs "
                "ORDER BY triggered_at DESC"))
            return [_run_row(r) for r in rows.fetchall()]
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_runs_read_failed", error=str(exc))
        return []


def _run_row(r: Any) -> dict[str, Any]:
    return {
        "id": r[0], "triggered_by": r[1], "triggered_at": _iso(r[2]),
        "triggered_by_email": r[3], "status": r[4],
        "layer_1_status": r[5], "layer_2_status": r[6],
        "layer_3_status": r[7], "total_checks": r[8], "passed": r[9],
        "failed": r[10], "warnings": r[11], "completed_at": _iso(r[12]),
        "metadata": r[13] or {}, "data_hash": r[14],
    }


async def get_audit_run(run_id: int) -> dict[str, Any] | None:
    """One audit run with its findings grouped by layer."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            run = await session.execute(text(
                "SELECT id, triggered_by, triggered_at, triggered_by_email, "
                "status, layer_1_status, layer_2_status, layer_3_status, "
                "total_checks, passed, failed, warnings, completed_at, "
                "metadata, data_hash FROM audit_runs WHERE id = :id"),
                {"id": run_id})
            found = run.fetchone()
            if not found:
                return None
            rows = await session.execute(text(
                "SELECT id, layer, check_name, metric, strategy, severity, "
                "status, platform_value, auditor_value, discrepancy, "
                "formula_used, raw_inputs_hash, auditor_reasoning, "
                "resolved, resolution_note, resolved_by, resolved_at, "
                "auto_acknowledged, locked_disclosure_text "
                "FROM audit_findings "
                "WHERE audit_run_id = :id ORDER BY layer, id"), {"id": run_id})
            findings = [_finding_row(r) for r in rows.fetchall()]
        out = _run_row(found)
        out["findings"] = {
            "layer_1": [f for f in findings if f["layer"] == 1],
            "layer_2": [f for f in findings if f["layer"] == 2],
            "layer_3": [f for f in findings if f["layer"] == 3],
        }
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_run_read_failed", run_id=run_id, error=str(exc))
        return None


def _finding_row(r: Any) -> dict[str, Any]:
    # resolved_by, resolved_at, auto_acknowledged were added by
    # migration 044. locked_disclosure_text was added by migration 055.
    # Older rows have NULL / false for these; the PDF disclosures
    # section renders missing values as a dash and the frontend
    # omits the disclosure box for a NULL value.
    return {
        "id": r[0], "layer": r[1], "check_name": r[2], "metric": r[3],
        "strategy": r[4], "severity": r[5], "status": r[6],
        "platform_value": r[7], "auditor_value": r[8], "discrepancy": r[9],
        "formula_used": r[10], "raw_inputs_hash": r[11],
        "auditor_reasoning": r[12], "resolved": r[13], "resolution_note": r[14],
        "resolved_by": r[15] if len(r) > 15 else None,
        "resolved_at": r[16] if len(r) > 16 else None,
        "auto_acknowledged": bool(r[17]) if len(r) > 17 and r[17] is not None
                              else False,
        "locked_disclosure_text": r[18] if len(r) > 18 else None,
    }


async def resolve_finding(
    finding_id: int,
    resolved: bool,
    note: str | None,
    *,
    resolved_by: str | None = None,
    disclosure_text: str | None = None,
) -> dict[str, Any] | None:
    """
    Sets or clears the acknowledgement on an audit finding — the WARN
    acknowledge/resolve workflow. Acknowledgement is a recorded response,
    not a correction: the run's overall verdict is unchanged. Returns the
    updated finding, or None when the finding is absent or on a database
    error (fail-open).

    resolved_by — the reviewer's email. Stored on the row so the PDF
    disclosures section can render "Reviewed by [email] on [date]"
    under each disclosed warning. When clearing the acknowledgement
    (resolved=False) the column is set back to NULL alongside
    resolution_note and resolved_at — the row reverts to its
    pre-review state.

    disclosure_text — optional. The verbatim disclosure the team agreed
    to put into the report at acknowledge time. Stored in the
    locked_disclosure_text column added by migration 055 so Bob can
    copy-paste it into the brief without re-deriving. Cleared (set to
    NULL) on unresolve so a revoked acknowledgement does not leave
    a stale disclosure attached.

    Workstream A — a successful ack also writes an audit_acknowledgements
    row so the next re-run's carry pass (tools/audit_carry.apply_carry)
    can re-apply this review against an equivalent future finding. A
    successful unresolve marks the row superseded so a revoked ack is
    not silently carried forward.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        # Normalise disclosure_text -- treat empty/whitespace as None
        # so a blank textarea produces a clean NULL.
        locked_disclosure = (
            (disclosure_text or "").strip() if resolved else None)
        if locked_disclosure == "":
            locked_disclosure = None
        async with AsyncSessionLocal() as session:
            result = await session.execute(text(
                "UPDATE audit_findings SET resolved = :r, "
                "resolution_note = :n, resolved_by = :by, "
                "resolved_at = CASE WHEN :r THEN now() ELSE NULL END, "
                "auto_acknowledged = CASE WHEN :r THEN false "
                "                         ELSE auto_acknowledged END, "
                "locked_disclosure_text = :ld "
                "WHERE id = :id "
                "RETURNING id, layer, check_name, metric, strategy, "
                "severity, status, platform_value, auditor_value, "
                "discrepancy, formula_used, raw_inputs_hash, "
                "auditor_reasoning, resolved, resolution_note, "
                "resolved_by, resolved_at, auto_acknowledged, "
                "locked_disclosure_text"),
                {"r": resolved, "n": note if resolved else None,
                 "by": resolved_by if resolved else None,
                 "ld": locked_disclosure,
                 "id": finding_id})
            row = result.fetchone()
            await session.commit()
            if not row:
                return None
            finding = _finding_row(row)
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_resolve_finding_failed",
                    finding_id=finding_id, error=str(exc))
        return None

    # Persist the carry record outside the primary UPDATE's session so
    # a write failure here cannot roll back the audit_findings change.
    try:
        from tools.audit_carry import (
            record_acknowledgement, supersede_acknowledgement,
        )
        if resolved and note and resolved_by:
            await record_acknowledgement(
                finding, note, acknowledged_by=resolved_by)
        elif not resolved:
            # Revoke — supersede the unsuperseded ack for this check_id.
            await supersede_acknowledgement(finding)
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_carry_record_failed",
                    finding_id=finding_id, error=str(exc))

    return finding


async def get_latest_audit_run() -> dict[str, Any] | None:
    """The most recent audit run (with findings), or None."""
    runs = await get_audit_runs()
    if not runs:
        return None
    return await get_audit_run(int(runs[0]["id"]))


# ── IN01 submission-window attestation ────────────────────────────────────────
#
# The IN01 methodology check (Statistical audit attestation) used to be a
# redundant tautology — it mirrored all_gates_required, so PASS/FAIL flipped
# together with that check. Repurposed May 25 2026 (per user spec) to a
# submission-window attestation: IN01 passes ONLY if a project team member
# manually triggered a full QA audit on or after the submission window
# opened. This gives the executive brief and presentation a meaningful
# claim — the team ran the audit themselves, not the system on cache-warm
# fire, AND within the window leading up to the deliverable.

IN01_SUBMISSION_WINDOW_OPENS = "2026-05-25T00:00:00+00:00"
# Parsed once at module load — asyncpg's bound-parameter validator does
# NOT coerce strings to timestamptz on the client side; passing the ISO
# string as the :window parameter raises asyncpg.DataError. The string
# form stays around because two evidence-line consumers use
# IN01_SUBMISSION_WINDOW_OPENS[:10] for display (e.g. "2026-05-25"),
# so converting the constant to a datetime would break those lines —
# keep both forms, use the right one at each call site.
_IN01_SUBMISSION_WINDOW_OPENS_DT: datetime = datetime.fromisoformat(
    IN01_SUBMISSION_WINDOW_OPENS)
# Trigger sources that count as a team-driven manual run. 'manual' is the
# Run Full QA / Run Full Audit button on the QA tab; 'pre_submission' is
# the Pre-Submission Audit button. The system-driven triggers
# ('data_ingestion', 'startup', 'scheduled', 'demo') do NOT satisfy IN01
# because they fire automatically — they aren't an act of team attestation.
_IN01_MANUAL_TRIGGERS: tuple[str, ...] = ("manual", "pre_submission")


async def compute_in01_attestation() -> dict[str, Any]:
    """Returns the IN01 attestation payload the QA methodology agent
    surfaces under check_id 'IN01' (key 'audit_integration').

    Verdict logic:
      PASS — a team-member email ran a manual or pre_submission audit
             on or after IN01_SUBMISSION_WINDOW_OPENS.
      FAIL — no such run exists (no manual runs at all, last manual
             run predates the window, or last manual run was triggered
             by a non-team email — e.g. a developer testing locally).

    A DB outage returns FAIL with an explicit 'database unavailable'
    evidence string; the audit's overall verdict surfaces this rather
    than silently passing.

    The function lives here (not in agents/qa_agent.py) because the
    QA agent is synchronous and this helper is async — the caller in
    main.py runs it before invoking QAAgent.run_audit and passes the
    result in via the new audit_attestation argument.
    """
    from config import PROJECT_TEAM_EMAILS

    team_emails = list(PROJECT_TEAM_EMAILS)
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return {
                "status": "FAIL",
                "evidence": (
                    "Database unavailable — could not verify whether a "
                    "team member ran a manual full QA audit within the "
                    "submission window. Re-check after the database is "
                    "reachable."
                ),
            }
        async with AsyncSessionLocal() as session:
            # Search for a qualifying manual run by a team member,
            # newest first. A match settles the attestation as PASS.
            qualifying = await session.execute(text(
                "SELECT id, triggered_by, triggered_at, "
                "       triggered_by_email "
                "FROM audit_runs "
                "WHERE triggered_by = ANY(:trig) "
                "  AND triggered_by_email = ANY(:emails) "
                "  AND triggered_at >= :window "
                "  AND status IN ('complete', 'partial') "
                "ORDER BY triggered_at DESC LIMIT 1"
            ), {"trig": list(_IN01_MANUAL_TRIGGERS),
                "emails": team_emails,
                # asyncpg requires a datetime here — the column is
                # timestamptz and the driver does not cast strings.
                "window": _IN01_SUBMISSION_WINDOW_OPENS_DT})
            row = qualifying.fetchone()
            if row:
                return {
                    "status": "PASS",
                    "evidence": (
                        f"Manual {row[1]} audit triggered by {row[3]} "
                        f"on {_iso(row[2])} — within the submission "
                        f"window opening {IN01_SUBMISSION_WINDOW_OPENS[:10]}. "
                        f"The team's attestation that the full QA audit "
                        f"was run by a project member, not the system "
                        f"on cache-warm fire."
                    ),
                    "triggered_by": row[1],
                    "triggered_by_email": row[3],
                    "triggered_at": _iso(row[2]),
                }

            # No qualifying run — locate the most recent manual run of
            # ANY age so the FAIL evidence is specific about WHY the
            # attestation does not pass.
            fallback = await session.execute(text(
                "SELECT id, triggered_by, triggered_at, "
                "       triggered_by_email "
                "FROM audit_runs "
                "WHERE triggered_by = ANY(:trig) "
                "ORDER BY triggered_at DESC LIMIT 1"
            ), {"trig": list(_IN01_MANUAL_TRIGGERS)})
            recent = fallback.fetchone()
        if recent is None:
            return {
                "status": "FAIL",
                "evidence": (
                    "No manual QA audit has been triggered by any user "
                    "in the audit history. A project team member must "
                    "click 'Run Full QA' (or Pre-Submission Audit) "
                    "before the executive brief and presentation are "
                    "submission-ready — the audit must be the team's "
                    "act, not a system trigger."
                ),
            }
        email = recent[3] or "<unknown>"
        triggered_at = _iso(recent[2])
        # Three distinct WHY clauses so the failure mode is legible:
        # before-window, non-team-email, or both.
        before_window = recent[2] is None or triggered_at < IN01_SUBMISSION_WINDOW_OPENS
        non_team = email not in team_emails
        if before_window and non_team:
            why = (
                f"the last manual audit was by {email} on {triggered_at}, "
                f"which is both before the submission window opens "
                f"({IN01_SUBMISSION_WINDOW_OPENS[:10]}) AND was not "
                f"triggered by a project team member"
            )
        elif before_window:
            why = (
                f"the last manual audit by {email} was on {triggered_at}, "
                f"before the submission window opens "
                f"({IN01_SUBMISSION_WINDOW_OPENS[:10]})"
            )
        else:
            why = (
                f"the last manual audit by {email} was not triggered by "
                f"a project team member; system-triggered or developer "
                f"runs do not count as a team attestation"
            )
        return {
            "status": "FAIL",
            "evidence": (
                f"IN01 attestation failed — {why}. A project team "
                f"member must trigger a fresh full QA audit before the "
                f"executive brief and presentation are submission-ready."
            ),
            "last_manual_run_at": triggered_at,
            "last_manual_run_by": email,
            "last_manual_trigger": recent[1],
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("in01_attestation_query_failed", error=str(exc))
        return {
            "status": "FAIL",
            "evidence": (
                f"Could not verify a team-driven manual audit run "
                f"(query error: {exc}). Re-run the audit so the "
                f"attestation lookup can complete."
            ),
        }


# ── IN02 attestation — Academic Review complete (May 26 2026) ───────────────
#
# Previously IN02 looked for an `_academic_review` key on the
# strategy_results dict — a runtime-only hand-off that nothing was
# actually populating. Result: every audit reported "No Academic
# Review has been recorded" no matter how many times the user ran
# the Council Academic Review panel.
#
# Fix — query agent_interactions for the most recent academic_review
# row and parse the overall_rating + the review verdict's section
# count from response_summary. The Academic Review endpoint already
# writes that row via _log_interaction_bg every time it runs, so the
# IN02 attestation reads the canonical persisted record rather than
# a runtime field that no caller sets.

async def compute_in02_attestation() -> dict[str, Any]:
    """Returns the IN02 attestation payload — was an Academic Review
    run, and did it carry the five rated sections.

    Verdict logic:
      PASS — an academic_review agent_interactions row exists within
             the last 14 days AND its response_summary parses out
             five `### N. ...` section headings AND each carries a
             `**Rating:**` line (the canonical arbiter output shape).
      WARN — the row exists but the response_summary is malformed or
             carries fewer than five rated sections.
      FAIL — no academic_review row in the lookback window.

    14-day window is generous — the project's submission cycle is
    weeks-long, and re-running the review every time the audit fires
    would burn the Opus + Gemini budget for no gain. A real review
    from earlier in the week is enough evidence for IN02.

    The function lives here (not in agents/qa_agent.py) because the
    QA agent is synchronous and this helper is async — same pattern
    as compute_in01_attestation. The caller in main.py runs it before
    invoking QAAgent.run_audit and passes the result in via the
    academic_review_attestation argument.
    """
    LOOKBACK_DAYS = 14

    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return {
                "status": "FAIL",
                "evidence": (
                    "Database unavailable — could not verify whether an "
                    "Academic Review has been run. Re-check after the "
                    "database is reachable."
                ),
            }
        async with AsyncSessionLocal() as session:
            # Most recent academic_review row within the lookback
            # window. ORDER BY timestamp DESC is the canonical "what
            # was the latest review" query — same pattern the Team
            # Activity surface uses.
            row = await session.execute(text(
                "SELECT id, user_email, timestamp, response_summary, "
                "       metadata "
                "FROM agent_interactions "
                "WHERE interaction_type = 'academic_review' "
                "  AND timestamp >= now() - "
                f"      INTERVAL '{LOOKBACK_DAYS} days' "
                "ORDER BY timestamp DESC LIMIT 1"
            ))
            found = row.fetchone()
        if found is None:
            return {
                "status": "FAIL",
                "evidence": (
                    f"No Academic Review has been run in the last "
                    f"{LOOKBACK_DAYS} days. Click 'Run Academic "
                    f"Review' on the QA Audit page (Academic Review "
                    f"section) before submission — the arbiter "
                    f"verdict's five rated sections are the IN02 "
                    f"attestation."
                ),
            }

        row_id, email, ts, summary, metadata = found
        when = ts.isoformat() if ts else "?"
        meta = metadata or {}
        overall = (meta.get("overall_rating") if isinstance(meta, dict)
                   else None)

        # Source-of-truth section count (May 26 2026). Two layers:
        #   1. Trust metadata.sections_rated when the auto-review path
        #      wrote it. That value was produced by
        #      compute_review_score() — the canonical scorer — so the
        #      audit's attestation cannot disagree with the score the
        #      editor banner already displays.
        #   2. Fall back to compute_review_score(summary) for older
        #      rows OR for the manual academic-review endpoint that
        #      logs response_summary without the metadata block.
        #      Same canonical scorer, same answer.
        # The previous local regex was duplicating the parser logic
        # and missed the rating syntax when the arbiter's section
        # headings drifted (or carried alternate labels) — symptom
        # was "parsed only 1 of 5" while compute_review_score saw
        # all five.
        n_sections: int | None = None
        parse_error: bool | None = None
        if isinstance(meta, dict):
            md_count = meta.get("sections_rated")
            if isinstance(md_count, int) and md_count >= 0:
                n_sections = md_count
            pe = meta.get("parse_error")
            if isinstance(pe, bool):
                parse_error = pe
        if n_sections is None:
            try:
                from tools.academic_review_score import compute_review_score
                scored = compute_review_score(summary or "")
                n_sections = int(scored.get("sections_rated") or 0)
                if parse_error is None:
                    parse_error = bool(scored.get("parse_error"))
                if not overall:
                    overall = scored.get("rating")
            except Exception as _exc:  # noqa: BLE001
                log.warning(
                    "in02_canonical_score_failed", error=str(_exc))
                n_sections = 0
        # Re-derive parse_error from the summary when the metadata row
        # is old (pre-bridge-#82 metadata had no parse_error field) and
        # n_sections came back zero. The canonical scorer is the
        # source of truth.
        if parse_error is None and n_sections == 0:
            try:
                from tools.academic_review_score import compute_review_score
                parse_error = bool(
                    compute_review_score(summary or "").get("parse_error"))
            except Exception:  # noqa: BLE001
                parse_error = False

        if n_sections >= 5:
            return {
                "status": "PASS",
                "evidence": (
                    f"Academic Review run by {email} on {when} carries "
                    f"{n_sections} rated sections" +
                    (f"; overall rating: {overall}" if overall else "") +
                    ". The arbiter verdict is on record as the "
                    "submission-readiness attestation."
                ),
                "last_review_at": when,
                "last_review_by": email,
                "n_sections": n_sections,
                "overall_rating": overall,
            }
        # Bridge #82 — distinguish "arbiter response could not be
        # parsed" from "fewer than five sections survived parsing".
        # parse_error=True means the response carried text but the
        # parser recognised zero sections; the user needs to investigate
        # the arbiter run (refused / unparseable / heading drift) rather
        # than retry the document.
        if parse_error and n_sections == 0:
            evidence = (
                f"An Academic Review row exists ({email} on {when}), "
                f"but the arbiter response could not be parsed — zero "
                f"of five rated sections were recognised in a non-empty "
                f"response. The arbiter may have refused, drifted from "
                f"the rubric heading format, or returned an error "
                f"payload; inspect the agent_interactions row and re-run "
                f"the Academic Review."
            )
        else:
            evidence = (
                f"An Academic Review row exists ({email} on {when}), "
                f"but the verdict parsed only {n_sections} of 5 rated "
                f"sections — the arbiter response may have truncated. "
                f"Re-run the Academic Review so the full five-section "
                f"verdict lands."
            )
        return {
            "status": "WARN",
            "evidence": evidence,
            "last_review_at": when,
            "n_sections": n_sections,
            "parse_error": bool(parse_error),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("in02_attestation_query_failed", error=str(exc))
        return {
            "status": "FAIL",
            "evidence": (
                f"Could not verify whether an Academic Review has "
                f"been run (query error: {exc}). Re-run the audit so "
                f"the attestation lookup can complete."
            ),
        }


# ── Orchestration ─────────────────────────────────────────────────────────────

def _tally(findings: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(findings),
        "passed": sum(1 for f in findings if f["status"] == "pass"),
        "failed": sum(1 for f in findings if f["status"] == "fail"),
        "warnings": sum(1 for f in findings if f["status"] == "warning"),
    }


async def _execute_audit(run_id: int, *, force: bool = False) -> None:
    """The three-layer audit body — runs in the background after the
    endpoint has returned the audit_id. Fail-open: a layer failure still
    finalises the run with status 'failed' and whatever completed.

    force — when True (May 26 2026), attempts to warm strategy_results_cache
    by running the full data pipeline inline before assembling the payload.
    Without it, a cold cache made assemble_audit_payload return
    available=False, every layer skipped, and the audit completed with
    0 checks recorded — a silent failure mode the user reported as
    'all layers skip instead of actually executing'. With force=true the
    run either succeeds (warm cache → real findings) or finalises with
    status='failed' + a metadata.note that names the cold cache loudly,
    so the QA tab can surface it. The flag never affects an already-warm
    cache — the pre-warm call short-circuits when strategy_results_cache
    already carries a current row.
    """
    from tools.audit_assembler import (
        assemble_audit_payload, current_data_hash,
    )
    from tools.audit_layer1 import layer_1_raw_data_audit
    from tools.audit_layer2 import layer_2_metric_audit
    from tools.audit_layer3 import layer_3_consistency_audit

    status = "complete"
    layer_statuses = {"1": "skip", "2": "skip", "3": "skip"}
    all_findings: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}
    data_hash: str | None = None
    try:
        # The lightweight fingerprint of the data this run verifies —
        # stored on the run so smart audit caching can tell, cheaply,
        # whether a later request still reflects the same data.
        data_hash = await current_data_hash() or None

        # force=true pre-warm. Replicates the cache-population path
        # /api/backtest/compare uses on a dashboard load: if the
        # strategy cache is cold, run the pipeline inline (off the
        # event loop) so assemble_audit_payload sees real data. A
        # warming failure is logged but does not block — the audit
        # then surfaces 'available=False' via the explicit status
        # branch below, which is the loud-failure path.
        if force:
            try:
                await _prewarm_strategy_cache(run_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("audit_prewarm_failed",
                            run_id=run_id, error=str(exc))

        payload = await assemble_audit_payload()
        metadata = {
            "available": payload.get("available"),
            "raw_inputs_hash": payload.get("raw_inputs_hash"),
        }
        if payload.get("metadata"):
            metadata["study_period"] = payload["metadata"].get("study_period")
            metadata["risk_free_rate"] = payload["metadata"].get("risk_free_rate")

        # Loud-failure branch (May 26 2026). A payload that the
        # assembler marks unavailable used to fall through into the
        # layer calls, each of which returned skip+empty silently;
        # the run finalised as 'complete' with 0 checks and the user
        # saw a green-looking row carrying no information. Now we
        # detect that state explicitly and finalise as 'failed' with
        # the assembler's note, so the QA tab can surface what to
        # do (warm the dashboard, retry).
        if not payload.get("available"):
            note = payload.get(
                "note",
                "audit payload unavailable — strategy or market cache cold",
            )
            log.warning("audit_payload_unavailable",
                        run_id=run_id, note=note, forced=force)
            metadata["note"] = note
            metadata["forced"] = force
            await _finalise_audit(
                run_id, status="failed",
                layer_statuses={"1": "skipped_no_data",
                                "2": "skipped_no_data",
                                "3": "skipped_no_data"},
                counts={"total": 0, "passed": 0, "failed": 0, "warnings": 0},
                metadata=metadata, data_hash=data_hash,
            )
            log.info("audit_complete", run_id=run_id, status="failed",
                     data_hash=data_hash, total=0, passed=0, failed=0,
                     warnings=0, reason="payload_unavailable")
            return

        # Pre-flight diagnostic — log exactly what data the layers
        # are about to receive. May 26 2026 user spec: "Add logging
        # to show exactly what data the layers receive when they
        # execute." This shows up in Render once per audit run and
        # tells the operator whether the data shape is what the
        # layer code expects.
        raw = payload.get("raw_data") or {}
        assets = raw.get("asset_returns") or {}
        strategy_returns = raw.get("strategy_returns") or {}
        platform = payload.get("platform_computed") or {}
        log.info(
            "audit_layers_preflight",
            run_id=run_id,
            forced=force,
            raw_inputs_hash=payload.get("raw_inputs_hash"),
            equity_obs=len(assets.get("equity") or []),
            ig_obs=len(assets.get("ig") or []),
            hy_obs=len(assets.get("hy") or []),
            n_strategies=len(strategy_returns),
            strategy_names=sorted(list(strategy_returns.keys()))[:12],
            has_summary_statistics=bool(platform.get("summary_statistics")),
            has_regime_conditional=bool(platform.get("regime_conditional")),
            has_factor_loadings=bool(platform.get("factor_loadings")),
            has_efficient_frontier=bool(
                platform.get("efficient_frontier", {}).get("max_sharpe_point")),
            anthropic_api_key_present=bool(os.getenv("ANTHROPIC_API_KEY")),
            environment=os.getenv("ENVIRONMENT", ""),
        )

        l1 = layer_1_raw_data_audit(payload)
        log.info("audit_layer_complete",
                 run_id=run_id, layer=1,
                 status=l1.get("status"),
                 n_findings=len(l1.get("findings") or []))
        l2 = await layer_2_metric_audit(payload)
        log.info("audit_layer_complete",
                 run_id=run_id, layer=2,
                 status=l2.get("status"),
                 n_findings=len(l2.get("findings") or []))
        l3 = await layer_3_consistency_audit(payload)
        log.info("audit_layer_complete",
                 run_id=run_id, layer=3,
                 status=l3.get("status"),
                 n_findings=len(l3.get("findings") or []))
        layer_statuses = {"1": l1["status"], "2": l2["status"],
                          "3": l3["status"]}
        all_findings = l1["findings"] + l2["findings"] + l3["findings"]

        # SECOND LOUD-FAILURE BRANCH (May 26 2026 follow-up). The
        # payload-unavailable check above catches assemble_audit_payload
        # returning available=False, but a SEPARATE failure mode exists:
        # payload.available=True (strategies + market data ARE in the
        # cache) yet every layer's internal gate fires and each returns
        # {status: 'skip', findings: []}. The audit then stored as
        # 'complete' with 0 checks — exactly the hollow row the user
        # has been seeing. Promote that state to 'failed' so the
        # cache-hit guard in get_last_substantive_audit will reject it
        # AND the downloaded PDF cannot read as a real audit.
        #
        # The most common cause of this state is layer 2's internal
        # `_is_test_env() or not os.getenv('ANTHROPIC_API_KEY')` gate
        # — but L1 and L3 do NOT have that gate, so when this branch
        # fires it's because each layer returned skip without raising.
        skipped_layer_values = {"skip", "skipped", "skipped_no_data",
                                "no_data", ""}
        all_skipped = all(
            str(s or "").strip().lower() in skipped_layer_values
            for s in layer_statuses.values()
        )
        if all_skipped and len(all_findings) == 0:
            log.warning(
                "audit_all_layers_skipped",
                run_id=run_id,
                layer_statuses=layer_statuses,
                forced=force,
                note="Every layer returned skip + 0 findings despite "
                     "payload.available=True. Promoting to status=failed "
                     "so the cache-hit guard cannot serve this row.",
            )
            metadata["note"] = (
                "All three layers returned skip + 0 findings despite a "
                "fully-assembled payload. Most likely cause: layer 2's "
                "ANTHROPIC_API_KEY or ENVIRONMENT=test gate fired. Check "
                "the audit_layers_preflight log line for env state."
            )
            metadata["forced"] = force
            await _finalise_audit(
                run_id, status="failed",
                layer_statuses=layer_statuses,
                counts={"total": 0, "passed": 0,
                        "failed": 0, "warnings": 0},
                metadata=metadata, data_hash=data_hash,
            )
            log.info("audit_complete", run_id=run_id, status="failed",
                     data_hash=data_hash, total=0, passed=0, failed=0,
                     warnings=0, reason="all_layers_skipped")
            return

        await _store_findings(run_id, all_findings)
        # Workstream A — apply auto-carry of prior acks against the
        # newly-stored findings. Each WARN whose check_id has a
        # prior unsuperseded ack AND whose value has not materially
        # changed (within 0.5%) is resolved automatically; the
        # finding row's auto_acknowledged flag distinguishes a
        # carried ack from a fresh team-typed one in the UI and PDF.
        # Failures inside the carry pass log and swallow — the audit
        # itself remains valid.
        try:
            from tools.audit_carry import apply_carry
            await apply_carry(run_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("audit_carry_pass_failed",
                        run_id=run_id, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_execute_failed", run_id=run_id, error=str(exc))
        status = "failed"

    counts = _tally(all_findings)
    await _finalise_audit(run_id, status=status,
                          layer_statuses=layer_statuses, counts=counts,
                          metadata=metadata, data_hash=data_hash)
    log.info("audit_complete", run_id=run_id, status=status,
             data_hash=data_hash, **counts)


async def _prewarm_strategy_cache(run_id: int) -> None:
    """Mirror the /api/backtest/compare cache-population path so a
    force=true audit run never sees a cold strategy_results_cache. Reads
    the latest cache row first; only if it is empty does the heavy
    get_full_history + run_all_strategies pipeline execute (off-loop in
    a worker thread, never blocking the audit's event loop).

    May 26 2026 — diagnostic logging at every decision point. The user
    reported audit #43 ran force=true but still produced zero checks;
    the warm path logged nothing visible, so we couldn't tell whether
    the pipeline returned data, whether the cache was already current,
    or whether run_all_strategies actually ran. Each branch now emits
    a structured log line naming the variables that drove the decision.
    """
    import asyncio as _asyncio
    from tools.cache import (
        _compute_data_hash, get_strategy_cache, set_strategy_cache,
    )

    log.info("audit_prewarm_started", run_id=run_id)

    try:
        from tools.data_fetcher import get_full_history_async
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_prewarm_import_failed",
                    run_id=run_id, error=str(exc))
        return

    try:
        history = await get_full_history_async()
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_prewarm_history_fetch_failed",
                    run_id=run_id, error=str(exc),
                    exc_type=type(exc).__name__)
        return

    monthly = history.get("equity_monthly")
    n_rows = len(monthly) if monthly is not None else 0
    log.info(
        "audit_prewarm_history_loaded",
        run_id=run_id,
        equity_monthly_rows=n_rows,
        equity_monthly_first_date=(
            str(monthly.index[0].date()) if monthly is not None and n_rows > 0
            else None),
        equity_monthly_last_date=(
            str(monthly.index[-1].date()) if monthly is not None and n_rows > 0
            else None),
        has_ig_monthly="ig_monthly" in history,
        has_hy_monthly="hy_monthly" in history,
        history_keys=sorted(list(history.keys())),
    )

    if n_rows == 0:
        log.warning("audit_prewarm_no_history", run_id=run_id,
                    history_keys=sorted(list(history.keys())))
        return

    last_date = str(monthly.index[-1].date())
    strategy_hash = _compute_data_hash(n_rows, last_date, n_strategies=10)
    cached = await get_strategy_cache(strategy_hash)
    if cached:
        log.info("audit_prewarm_cache_hit",
                 run_id=run_id,
                 strategy_hash=strategy_hash[:8],
                 n_cached_strategies=len(cached) if cached else 0)
        return

    log.info("audit_prewarm_cache_miss_running_strategies",
             run_id=run_id, strategy_hash=strategy_hash[:8],
             n_rows=n_rows, last_date=last_date)

    from tools.backtester import run_all_strategies
    try:
        results_dict = await _asyncio.to_thread(run_all_strategies, history)
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_prewarm_strategies_failed",
                    run_id=run_id, error=str(exc),
                    exc_type=type(exc).__name__)
        return

    if not results_dict:
        log.warning("audit_prewarm_strategies_empty",
                    run_id=run_id, strategy_hash=strategy_hash[:8])
        return

    try:
        await set_strategy_cache(
            strategy_hash, results_dict, n_observations=n_rows,
            risk_free_monthly=history.get("risk_free_monthly"))
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_prewarm_cache_write_failed",
                    run_id=run_id, error=str(exc),
                    n_strategies=len(results_dict))
        return

    log.info("audit_prewarm_cache_filled",
             run_id=run_id, strategy_hash=strategy_hash[:8],
             n_strategies=len(results_dict),
             strategy_names=sorted(list(results_dict.keys())))


async def start_audit(
    triggered_by: str = "manual", email: str = "", *,
    force: bool = False,
) -> dict[str, Any]:
    """
    Claims an audit run and fires the three layers in the background.
    Returns immediately with the audit_id. A concurrent run is refused
    with already_running and the in-flight run's id.

    force — when True (May 26 2026), the background _execute_audit
    pre-warms strategy_results_cache before assembling the payload.
    Symmetric with /api/qa/audit's force flag (PR #fdd0f57): a manual
    "Run Full QA" click on a cold cache should produce real findings,
    not a silent 'all layers skip'.
    """
    existing = await is_audit_running()
    if existing is not None:
        return {"status": "already_running", "audit_id": existing}
    run_id = await _create_running_audit(triggered_by, email)
    if run_id is None:
        return {"status": "failed", "reason": "no_database"}
    try:
        import asyncio
        task = asyncio.create_task(_execute_audit(run_id, force=force))
        _audit_bg_tasks.add(task)
        task.add_done_callback(_audit_bg_tasks.discard)
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_fire_failed", run_id=run_id, error=str(exc))
        return {"status": "failed", "reason": "could_not_start",
                "audit_id": run_id}
    return {"status": "started", "audit_id": run_id, "forced": force}


# ── Smart audit caching — the auto-trigger ────────────────────────────────────


async def _run_qa_methodology() -> None:
    """
    Runs the QA methodology checklist — Tier 1 synchronously, Tier 2 in
    the background — against the current strategy results and caches the
    verdict. The methodology half of run_full_audit(). Mirrors the
    /api/v1/qa/run endpoint; the blocking pipeline work is offloaded to a
    thread so this is safe to call from an event-loop background task.
    """
    import asyncio as _asyncio
    import os

    if os.getenv("ENVIRONMENT", "").lower() == "test":
        return

    from tools.cache import (
        _compute_data_hash, get_strategy_cache, set_qa_cache,
    )
    from tools.qa_guard import begin_methodology, end_methodology
    from tools.qa_tiered import run_tier1_checks, schedule_tier2_background

    def _history_and_hash() -> tuple[Any, str]:
        from tools.data_fetcher import get_full_history
        history = get_full_history()
        monthly = history.get("equity_monthly")
        n = len(monthly) if monthly is not None else 0
        last = (str(monthly.index[-1])
                if monthly is not None and n > 0 else "unknown")
        return history, _compute_data_hash(n, last, n_strategies=10)

    # The methodology flag makes this auto-triggered run visible to the
    # global QA-run guard, so a user QA run is correctly rejected while
    # it is in flight. Cleared in finally — Tier 2 continues in the
    # background, which is not a guarded "run".
    begin_methodology()
    try:
        history, strategy_hash = await _asyncio.to_thread(_history_and_hash)
        cached = await get_strategy_cache(strategy_hash)
        if cached:
            results_dict = cached
        else:
            from tools.backtester import run_all_strategies
            results_dict = await _asyncio.to_thread(
                run_all_strategies, history)

        t1 = await _asyncio.to_thread(run_tier1_checks, results_dict)
        await set_qa_cache(strategy_hash, t1, tier=1)

        # Tier 2 — fire and forget; off_loop write engine, see qa_run.
        def _writer(h: str, v: dict, tier: int) -> None:
            _asyncio.run(set_qa_cache(h, v, tier=tier, off_loop=True))
        schedule_tier2_background(results_dict, strategy_hash, _writer)
    finally:
        end_methodology()


async def run_full_audit(reason: str = "scheduled") -> None:
    """
    The smart-audit-caching auto-trigger body: re-runs the statistical
    audit and then the QA methodology audit — in sequence, never in
    parallel, so the two never contend for the concurrency lock.

    Idempotent: a no-op when the last completed audit is still current
    for the data, so it is safe to fire after any data event. Fail-open
    throughout — a failure in either audit is logged and swallowed.

    reason — what prompted the run ("data_ingestion" | "cache_invalidation"
    | "scheduled"); stored as the audit_runs.triggered_by value and logged.
    """
    try:
        from tools.audit_assembler import is_audit_current
        currency = await is_audit_current()
        if currency.get("is_current"):
            # The data the last completed audit verified is unchanged —
            # skip, so a redeploy or a no-op data event never spends an
            # Opus audit. The skip is logged for Render-log visibility.
            log.info("audit_trigger_skipped", reason="audit_current",
                     triggered_from=reason)
            return
    except Exception as exc:  # noqa: BLE001
        log.warning("auto_audit_currency_check_failed", reason=reason,
                    error=str(exc))
        # An unclear signal — better to run the audit than to skip it.

    log.info("auto_audit_triggered", reason=reason)

    # 1. Statistical audit — claim the concurrency lock, run the layers.
    try:
        if await is_audit_running() is None:
            run_id = await _create_running_audit(reason, "auto")
            if run_id is not None:
                await _execute_audit(run_id)
        else:
            log.info("auto_audit_statistical_skipped_locked", reason=reason)
    except Exception as exc:  # noqa: BLE001
        log.warning("auto_audit_statistical_failed", reason=reason,
                    error=str(exc))

    # 2. QA methodology audit — Tier 1 + Tier 2, after the statistical run.
    try:
        await _run_qa_methodology()
    except Exception as exc:  # noqa: BLE001
        log.warning("auto_audit_qa_failed", reason=reason, error=str(exc))


def trigger_audit_async(reason: str) -> None:
    """
    Fire run_full_audit() in the background — the smart-audit-caching
    auto-trigger. Works whether or not the caller is on an event loop:
    on a loop (an async endpoint) it schedules a task; off a loop (the
    sync data pipeline) it runs in a daemon thread with its own loop.
    Fail-open — a spawn failure is logged and never raised into the
    caller's primary flow.
    """
    import asyncio
    import threading

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            task = loop.create_task(run_full_audit(reason))
            _audit_bg_tasks.add(task)
            task.add_done_callback(_audit_bg_tasks.discard)
        else:
            threading.Thread(
                target=lambda: asyncio.run(run_full_audit(reason)),
                daemon=True, name="auto-audit",
            ).start()
    except Exception as exc:  # noqa: BLE001
        log.warning("auto_audit_spawn_failed", reason=reason, error=str(exc))


# ── Export report ─────────────────────────────────────────────────────────────

_COMPUTATION_REGIMES = (
    "COMPUTATION REGIMES\n"
    "The platform computes annualised statistics in two regimes, by\n"
    "design — a value difference between the two is EXPECTED and is not\n"
    "a discrepancy:\n"
    "  - Analytics layer  — monthly return series, annualised with 12\n"
    "                       and sqrt(12). The Analytics page tables and\n"
    "                       this audit's Layer 2 recomputation use it.\n"
    "  - Dashboard layer  — daily return series, annualised with 252 and\n"
    "                       sqrt(252). The Dashboard strategy table uses\n"
    "                       it.\n"
    "CAGR is a geometric annual growth rate and is regime-independent —\n"
    "it is the same in both layers and is cross-checked directly.\n"
    "Volatility and Sharpe ARE regime-dependent: the two layers report\n"
    "different values for the same entity, which the audit documents\n"
    "rather than flags.\n"
)


def _overall(run: dict[str, Any]) -> str:
    if run.get("failed", 0) > 0:
        return "FAIL"
    if run.get("warnings", 0) > 0:
        return "WARN"
    return "PASS"


def format_audit_report(run: dict[str, Any]) -> str:
    """Renders an audit run (with grouped findings) as the downloadable
    plain-text report for the Analytical Appendix."""
    findings = run.get("findings", {})
    total = run.get("total_checks", 0) or 0
    passed = run.get("passed", 0) or 0
    pct = f"{(passed / total * 100):.0f}%" if total else "—"
    lines: list[str] = []

    def hr() -> None:
        lines.append("=" * 70)

    hr()
    lines.append("FOREST CAPITAL PORTFOLIO INTELLIGENCE SYSTEM")
    lines.append("STATISTICAL AUDIT REPORT")
    hr()
    lines.append(f"Audit run:      {run.get('id')}")
    lines.append(f"Triggered:      {run.get('triggered_at')}")
    lines.append(f"Triggered by:   {run.get('triggered_by_email') or '—'} "
                 f"({run.get('triggered_by')})")
    meta = run.get("metadata") or {}
    lines.append(f"Data hash:      {meta.get('raw_inputs_hash') or '—'}")
    lines.append("")
    lines.append("EXECUTIVE SUMMARY")
    lines.append(f"  Total checks:      {total}")
    lines.append(f"  Passed:            {passed} ({pct})")
    lines.append(f"  Warnings:          {run.get('warnings', 0)}")
    lines.append(f"  Critical failures: {run.get('failed', 0)}")
    lines.append(f"  Overall status:    {_overall(run)}")
    lines.append("")
    lines.append(_COMPUTATION_REGIMES)

    layer_titles = {
        "layer_1": "LAYER 1: RAW DATA VERIFICATION",
        "layer_2": "LAYER 2: INDEPENDENT RECOMPUTATION",
        "layer_3": "LAYER 3: CONSISTENCY CHECKS",
    }
    for key, title in layer_titles.items():
        hr()
        lines.append(title)
        hr()
        rows = findings.get(key, [])
        if not rows:
            lines.append("  (no findings — layer skipped)")
        for fnd in rows:
            mark = {"pass": "[PASS]", "fail": "[FAIL]",
                    "warning": "[WARN]"}.get(fnd["status"], "[?]")
            strat = f" · {fnd['strategy']}" if fnd.get("strategy") else ""
            lines.append(f"  {mark} {fnd['check_name']} — {fnd['metric']}"
                         f"{strat}")
            if fnd.get("platform_value"):
                lines.append(f"         platform: {fnd['platform_value']}")
            if fnd.get("auditor_value"):
                lines.append(f"         auditor:  {fnd['auditor_value']}")
            if fnd.get("discrepancy"):
                lines.append(f"         discrepancy: {fnd['discrepancy']}")
            if fnd.get("auditor_reasoning"):
                lines.append(f"         {fnd['auditor_reasoning']}")
        lines.append("")

    # Discrepancies needing attention — every FAIL / WARNING.
    hr()
    lines.append("DISCREPANCIES REQUIRING ATTENTION")
    hr()
    flagged = [fnd for rows in findings.values() for fnd in rows
               if fnd["status"] in ("fail", "warning")]
    if not flagged:
        lines.append("  None — every check passed.")
    for fnd in flagged:
        lines.append(f"  [{fnd['status'].upper()}] L{fnd['layer']} "
                     f"{fnd['check_name']} — {fnd['metric']}")
        if fnd.get("discrepancy"):
            lines.append(f"         {fnd['discrepancy']}")
    lines.append("")

    hr()
    lines.append("DATA PROVENANCE")
    hr()
    sp = meta.get("study_period") or {}
    rfr = meta.get("risk_free_rate") or {}
    lines.append(f"  Study period:  {sp.get('start')} to {sp.get('end')} "
                 f"({sp.get('months')} months)")
    lines.append(f"  Risk-free:     {rfr.get('value')} "
                 f"({rfr.get('source')}, {rfr.get('calculation')})")
    lines.append("  Factor model:  Carhart four-factor (MKT-RF, SMB, HML, MOM)")
    lines.append("  Audit model:               claude-opus-4-7")
    lines.append("  Platform computation model: claude-sonnet-4-6")
    lines.append("")
    return "\n".join(lines)
