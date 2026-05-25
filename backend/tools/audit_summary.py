"""
tools/audit_summary.py — Workstream D (May 28 2026).

Deterministic audit disclosures for the generated reports. The midpoint
paper and executive brief both carry an Audit Disclosure Appendix at
the end of the document; the executive brief also carries a one-line
audit summary in its Executive Summary section and a body paragraph
that frames the audit's role in the methodology.

Every figure rendered here is computed from the platform's own audit
records — there is no AI generation. The text is identical across
runs against the same audit state so a grader comparing two exports
sees the same disclosure language. The body paragraph and summary
sentence omit no acknowledged disclosure: the audit ran, the warnings
that surfaced were reviewed, and the rationale travels with the
document.

The Workstream C report-readiness gate refuses to generate a document
while any WARN is unreviewed, so by the time the builder calls into
this module every WARN finding either has resolved_by + resolution_note
(statistical) or a qa_intentional_overrides row (methodology). Failure
findings would also be blocked by the gate, so the appendix records
acknowledged warnings only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def _isofmt(value: Any) -> str:
    """Renders a datetime / ISO string / None as YYYY-MM-DD. Used by the
    body paragraph + the appendix table. None becomes '—' so the row
    stays well-formed regardless of legacy data."""
    if value is None:
        return "—"
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text:
        return "—"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
    except ValueError:
        return text


async def gather_audit_disclosures() -> dict[str, Any]:
    """
    Reads the latest statistical + methodology audits and returns the
    structured disclosures bundle the report builders consume.

    Shape:
      {
        available: bool,
        statistical: {
          present: bool,
          run_id: int | None,
          completed_at: str | None,
          total: int, passed: int, warnings: int, failures: int,
          acknowledged: [
            {
              layer: int, check_name: str, metric: str, strategy: str?,
              status: str, discrepancy: str?,
              resolved_by: str?, resolved_at: str (YYYY-MM-DD),
              resolution_note: str,
              auto_acknowledged: bool,
            },
            ...
          ],
        },
        methodology: {
          present: bool,
          verdict: str | None,
          run_at: str | None,
          total: int, passed: int, warnings: int, failures: int,
          intentional: [
            {
              check_id: str, check: str, description: str, category: str,
              marked_by: str, marked_at: str (YYYY-MM-DD),
              note: str,
            },
            ...
          ],
        },
        generated_at: ISO timestamp,
      }

    Fail-open: any read error returns the empty bundle shape with
    `available` false. The document builders fall back to a "no audit
    on record" disclosure line in that case.
    """
    bundle: dict[str, Any] = {
        "available": False,
        "statistical": {
            "present": False, "run_id": None, "completed_at": None,
            "total": 0, "passed": 0, "warnings": 0, "failures": 0,
            "acknowledged": [],
        },
        "methodology": {
            "present": False, "verdict": None, "run_at": None,
            "total": 0, "passed": 0, "warnings": 0, "failures": 0,
            "intentional": [],
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # ── Statistical: latest completed audit_runs row + its findings ───
    try:
        from tools.audit_engine import get_latest_audit_run

        run = await get_latest_audit_run()
        if run is not None:
            findings = run.get("findings") or {}
            all_findings = (
                (findings.get("layer_1") or [])
                + (findings.get("layer_2") or [])
                + (findings.get("layer_3") or [])
            )
            acks = []
            for f in all_findings:
                if not f.get("resolved"):
                    continue
                if str(f.get("status") or "").lower() != "warning":
                    continue
                acks.append({
                    "layer": f.get("layer"),
                    "check_name": f.get("check_name"),
                    "metric": f.get("metric"),
                    "strategy": f.get("strategy"),
                    "status": f.get("status"),
                    "discrepancy": f.get("discrepancy"),
                    "resolved_by": f.get("resolved_by"),
                    "resolved_at": _isofmt(f.get("resolved_at")),
                    "resolution_note": f.get("resolution_note") or "",
                    "auto_acknowledged": bool(f.get("auto_acknowledged")),
                })
            bundle["statistical"] = {
                "present": True,
                "run_id": run.get("id"),
                "completed_at": _isofmt(run.get("completed_at")),
                "total": int(run.get("total_checks") or 0),
                "passed": int(run.get("passed") or 0),
                "warnings": int(run.get("warnings") or 0),
                "failures": int(run.get("failed") or 0),
                "acknowledged": acks,
            }
            bundle["available"] = True
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_summary_statistical_read_failed", error=str(exc))

    # ── Methodology: latest QA audit checklist + intentional overrides ─
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        from tools.cache import get_most_recent_qa_run

        recent = await get_most_recent_qa_run(min_tier=1)
        if recent is not None:
            checklist = recent.get("checklist") or {}
            items = checklist.get("items") or []
            total = len(items) or int(checklist.get("checks_total") or 0)
            passed = sum(
                1 for it in items
                if str(it.get("status") or "").upper() == "PASS"
            )
            warnings = sum(
                1 for it in items
                if str(it.get("status") or "").upper() == "WARN"
            )
            failures = sum(
                1 for it in items
                if str(it.get("status") or "").upper() == "FAIL"
            )

            # Pull every intentional override and surface those whose
            # check_id corresponds to a WARN item in the checklist.
            overrides_by_id: dict[str, dict[str, Any]] = {}
            try:
                if AsyncSessionLocal is not None:
                    async with AsyncSessionLocal() as session:
                        rows = await session.execute(text(
                            "SELECT check_id, marked_at, marked_by, note "
                            "FROM qa_intentional_overrides "
                            "ORDER BY marked_at DESC"))
                        for row in rows.fetchall():
                            overrides_by_id[row[0]] = {
                                "marked_at": _isofmt(row[1]),
                                "marked_by": row[2] or "—",
                                "note": row[3] or "",
                            }
            except Exception as exc:  # noqa: BLE001
                log.warning("audit_summary_overrides_read_failed",
                            error=str(exc))

            intentional = []
            for it in items:
                cid = it.get("check_id")
                if not cid or cid not in overrides_by_id:
                    continue
                ov = overrides_by_id[cid]
                intentional.append({
                    "check_id": cid,
                    "check": it.get("check"),
                    "description": it.get("description"),
                    "category": it.get("category"),
                    "marked_by": ov["marked_by"],
                    "marked_at": ov["marked_at"],
                    "note": ov["note"],
                })

            bundle["methodology"] = {
                "present": True,
                "verdict": checklist.get("verdict") or recent.get("verdict"),
                "run_at": _isofmt(recent.get("run_at")),
                "total": total,
                "passed": passed,
                "warnings": warnings,
                "failures": failures,
                "intentional": intentional,
            }
            bundle["available"] = True
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_summary_methodology_read_failed", error=str(exc))

    return bundle


def audit_summary_sentence(disclosures: dict[str, Any]) -> str:
    """
    Single sentence for the Executive Summary section of the brief.

    Reports the audit's headline numbers and the count of disclosed
    warnings the reader can find in the appendix. Deliberately one
    sentence — the brief targets investment-audience readers who do
    not need a full audit summary in the body.
    """
    stat = disclosures.get("statistical") or {}
    meth = disclosures.get("methodology") or {}
    if not (stat.get("present") or meth.get("present")):
        return (
            "No platform audit was on record at generation time; the "
            "Audit Disclosure Appendix is empty for this draft."
        )
    stat_warns = len(stat.get("acknowledged") or [])
    meth_warns = len(meth.get("intentional") or [])
    total_disclosed = stat_warns + meth_warns
    stat_total = int(stat.get("total") or 0)
    meth_total = int(meth.get("total") or 0)

    if total_disclosed == 0:
        return (
            f"An independent statistical audit ({stat_total} checks) and a "
            f"methodology review ({meth_total} checks) ran prior to this "
            f"draft and surfaced no disclosures requiring acknowledgement."
        )
    return (
        f"An independent statistical audit ({stat_total} checks) and a "
        f"methodology review ({meth_total} checks) ran prior to this "
        f"draft; {total_disclosed} warning"
        f"{'s' if total_disclosed != 1 else ''} ha"
        f"{'ve' if total_disclosed != 1 else 's'} been reviewed and "
        f"are disclosed in the appendix."
    )


def audit_body_paragraph(disclosures: dict[str, Any]) -> str:
    """
    Mid-document paragraph for the methodology section. Frames how the
    audit subsystem fits into the analytical pipeline and points the
    reader at the appendix for the per-warning detail.

    Deterministic — never AI-generated. The Workstream C readiness
    gate guarantees every WARN has been reviewed, so the language can
    state that flatly without qualification.
    """
    stat = disclosures.get("statistical") or {}
    meth = disclosures.get("methodology") or {}
    if not (stat.get("present") or meth.get("present")):
        return (
            "No platform audit was on record at the time of generation. "
            "The team should run the platform's statistical audit and "
            "methodology review before submission so the Audit Disclosure "
            "Appendix carries the audit verdict and any reviewed warnings."
        )
    parts = [
        "Every analytical figure in this document was produced by the "
        "Forest Capital Portfolio Intelligence System and independently "
        "verified by two platform audits before the draft was generated. "
    ]
    if stat.get("present"):
        parts.append(
            f"The statistical audit recomputed every metric from raw "
            f"data with an independent model (claude-opus-4-7) and ran "
            f"{int(stat.get('total') or 0)} checks across three layers "
            f"(raw data verification, independent recomputation, and "
            f"cross-platform consistency); "
            f"{int(stat.get('passed') or 0)} passed and "
            f"{int(stat.get('warnings') or 0)} surfaced warnings that "
            f"the team has reviewed and disclosed in the appendix below. "
        )
    if meth.get("present"):
        parts.append(
            f"The methodology review evaluated the analytical approach "
            f"against a {int(meth.get('total') or 0)}-point checklist "
            f"covering data integrity, portfolio mechanics, statistical "
            f"rigour, cross-validation, overfitting controls, economic "
            f"significance, and presentation quality; "
            f"{int(meth.get('passed') or 0)} passed and "
            f"{int(meth.get('warnings') or 0)} surfaced warnings, each "
            f"either acknowledged or marked as intentional methodology. "
        )
    parts.append(
        "The platform refuses to generate a report while any audit "
        "warning is unreviewed, so the disclosures recorded here represent "
        "the team's complete response to the audit findings on file at "
        "the time of generation."
    )
    return "".join(parts)


def acknowledged_statistical_rows(
    disclosures: dict[str, Any],
) -> list[list[str]]:
    """Rows for the statistical disclosures appendix table. Columns:
    Layer · Check · Reviewed by · Reviewed at · Note. Empty list when
    no warnings were acknowledged or the audit is absent."""
    stat = disclosures.get("statistical") or {}
    rows: list[list[str]] = []
    for a in stat.get("acknowledged") or []:
        check_label = a.get("check_name") or a.get("metric") or "(unnamed)"
        if a.get("strategy"):
            check_label = f"{check_label} · {a['strategy']}"
        rows.append([
            f"L{a.get('layer', '?')}",
            check_label,
            a.get("resolved_by") or "—",
            a.get("resolved_at") or "—",
            a.get("resolution_note") or "(no note)",
        ])
    return rows


def intentional_methodology_rows(
    disclosures: dict[str, Any],
) -> list[list[str]]:
    """Rows for the methodology disclosures appendix table. Columns:
    Check · Category · Marked by · Marked at · Note."""
    meth = disclosures.get("methodology") or {}
    rows: list[list[str]] = []
    for it in meth.get("intentional") or []:
        check_label = it.get("check") or it.get("description") or "(unnamed)"
        if it.get("check_id"):
            check_label = f"{it['check_id']} · {check_label}"
        rows.append([
            check_label,
            it.get("category") or "—",
            it.get("marked_by") or "—",
            it.get("marked_at") or "—",
            it.get("note") or "(no note)",
        ])
    return rows
