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


def _iso(value: Any) -> str | None:
    try:
        return value.isoformat() if value is not None else None
    except Exception:  # noqa: BLE001
        return None


# ── audit_runs / audit_findings persistence ───────────────────────────────────

async def is_audit_running() -> int | None:
    """The id of an audit still in the 'running' state — the concurrency
    lock — or None. Fail-open: a database error reports None."""
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


async def _store_findings(run_id: int, findings: list[dict[str, Any]]) -> None:
    """Bulk-inserts the audit findings for a run. Fail-open."""
    if not findings:
        return
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return
        async with AsyncSessionLocal() as session:
            for fnd in findings:
                await session.execute(text(
                    "INSERT INTO audit_findings (audit_run_id, layer, "
                    "check_name, metric, strategy, severity, status, "
                    "platform_value, auditor_value, discrepancy, "
                    "formula_used, raw_inputs_hash, auditor_reasoning) "
                    "VALUES (:rid, :layer, :cn, :metric, :strat, :sev, "
                    ":status, :pv, :av, :disc, :fu, :hash, :reason)"
                ), {"rid": run_id, "layer": fnd["layer"],
                    "cn": fnd["check_name"], "metric": fnd["metric"],
                    "strat": fnd.get("strategy"), "sev": fnd["severity"],
                    "status": fnd["status"], "pv": fnd.get("platform_value"),
                    "av": fnd.get("auditor_value"),
                    "disc": fnd.get("discrepancy"),
                    "fu": fnd.get("formula_used"),
                    "hash": fnd.get("raw_inputs_hash"),
                    "reason": fnd.get("auditor_reasoning")})
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_store_findings_failed", run_id=run_id, error=str(exc))


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
                "SELECT layer, check_name, metric, strategy, severity, "
                "status, platform_value, auditor_value, discrepancy, "
                "formula_used, raw_inputs_hash, auditor_reasoning, "
                "resolved, resolution_note FROM audit_findings "
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
    return {
        "layer": r[0], "check_name": r[1], "metric": r[2], "strategy": r[3],
        "severity": r[4], "status": r[5], "platform_value": r[6],
        "auditor_value": r[7], "discrepancy": r[8], "formula_used": r[9],
        "raw_inputs_hash": r[10], "auditor_reasoning": r[11],
        "resolved": r[12], "resolution_note": r[13],
    }


async def get_latest_audit_run() -> dict[str, Any] | None:
    """The most recent audit run (with findings), or None."""
    runs = await get_audit_runs()
    if not runs:
        return None
    return await get_audit_run(int(runs[0]["id"]))


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
