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
                "auto_acknowledged FROM audit_findings "
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
    # migration 044. Older rows have NULL / false for these; the
    # PDF disclosures section renders missing values as a dash.
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
    }


async def resolve_finding(
    finding_id: int,
    resolved: bool,
    note: str | None,
    *,
    resolved_by: str | None = None,
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
        async with AsyncSessionLocal() as session:
            result = await session.execute(text(
                "UPDATE audit_findings SET resolved = :r, "
                "resolution_note = :n, resolved_by = :by, "
                "resolved_at = CASE WHEN :r THEN now() ELSE NULL END, "
                "auto_acknowledged = CASE WHEN :r THEN false "
                "                         ELSE auto_acknowledged END "
                "WHERE id = :id "
                "RETURNING id, layer, check_name, metric, strategy, "
                "severity, status, platform_value, auditor_value, "
                "discrepancy, formula_used, raw_inputs_hash, "
                "auditor_reasoning, resolved, resolution_note, "
                "resolved_by, resolved_at, auto_acknowledged"),
                {"r": resolved, "n": note if resolved else None,
                 "by": resolved_by if resolved else None,
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
                "window": IN01_SUBMISSION_WINDOW_OPENS})
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


# ── Orchestration ─────────────────────────────────────────────────────────────

def _tally(findings: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(findings),
        "passed": sum(1 for f in findings if f["status"] == "pass"),
        "failed": sum(1 for f in findings if f["status"] == "fail"),
        "warnings": sum(1 for f in findings if f["status"] == "warning"),
    }


async def _execute_audit(run_id: int) -> None:
    """The three-layer audit body — runs in the background after the
    endpoint has returned the audit_id. Fail-open: a layer failure still
    finalises the run with status 'failed' and whatever completed."""
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
        payload = await assemble_audit_payload()
        metadata = {
            "available": payload.get("available"),
            "raw_inputs_hash": payload.get("raw_inputs_hash"),
        }
        if payload.get("metadata"):
            metadata["study_period"] = payload["metadata"].get("study_period")
            metadata["risk_free_rate"] = payload["metadata"].get("risk_free_rate")

        l1 = layer_1_raw_data_audit(payload)
        l2 = await layer_2_metric_audit(payload)
        l3 = await layer_3_consistency_audit(payload)
        layer_statuses = {"1": l1["status"], "2": l2["status"],
                          "3": l3["status"]}
        all_findings = l1["findings"] + l2["findings"] + l3["findings"]
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


async def start_audit(
    triggered_by: str = "manual", email: str = "",
) -> dict[str, Any]:
    """
    Claims an audit run and fires the three layers in the background.
    Returns immediately with the audit_id. A concurrent run is refused
    with already_running and the in-flight run's id.
    """
    existing = await is_audit_running()
    if existing is not None:
        return {"status": "already_running", "audit_id": existing}
    run_id = await _create_running_audit(triggered_by, email)
    if run_id is None:
        return {"status": "failed", "reason": "no_database"}
    try:
        import asyncio
        task = asyncio.create_task(_execute_audit(run_id))
        _audit_bg_tasks.add(task)
        task.add_done_callback(_audit_bg_tasks.discard)
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_fire_failed", run_id=run_id, error=str(exc))
        return {"status": "failed", "reason": "could_not_start",
                "audit_id": run_id}
    return {"status": "started", "audit_id": run_id}


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
