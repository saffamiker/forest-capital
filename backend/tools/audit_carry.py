"""
tools/audit_carry.py — Workstream A (May 28 2026).

Audit-acknowledgement auto-carry.

Background. Migration 044 introduced the audit_acknowledgements table —
ONE canonical acknowledgement per stable check identity, independent of
the audit_run lifetime. The previous design put the ack on the
audit_findings row itself (via resolved + resolution_note), so every
re-run produced fresh rows with resolved=false and the team's prior
review was silently dropped. They had to retype the same disclosure
against the same finding on every audit cycle.

The carry pass closes that loop. After _store_findings persists the new
audit_findings for a re-run:

  1. For each newly-stored WARN finding, compose its stable check_id
     ("L{layer}.{metric}.{strategy_or_underscore}") and look up the
     audit_acknowledgements row WHERE check_id = :c AND superseded =
     false.

  2. If a row exists AND the platform value has not materially changed
     (numeric within 0.5% relative tolerance, or exact string match
     for non-numeric values), the ack is carried forward — the
     audit_findings row is UPDATEd with resolved=true,
     resolution_note=ack.resolution_note, resolved_by='auto_carry',
     resolved_at=now(), auto_acknowledged=true.

  3. If a row exists but the value HAS materially changed, the ack is
     stale — the original review may no longer apply. The row is
     marked superseded=true with superseded_at=now() so the carry pass
     never reapplies it; the finding stays unreviewed for the team to
     re-evaluate.

The companion direction — recording an acknowledgement when the team
manually acks a finding — lives next to resolve_finding in
audit_engine.py (record_acknowledgement + supersede_acknowledgement).

Failure modes are silent: any database error in a per-row update logs
and skips that row; the rest of the carry pass continues. A bad ack
never corrupts the audit run's findings.
"""
from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger(__name__)


# Relative tolerance for numeric value matching. 0.5% is the
# user-specified threshold — a finding whose recomputed value moves by
# less than this is considered "the same finding" and the prior ack
# still applies.
DEFAULT_TOLERANCE = 0.005

# Absolute tolerance used when the previous value is exactly 0, since
# a relative tolerance is undefined there.
ABS_TOLERANCE_NEAR_ZERO = 1e-4


def compose_check_id(finding: dict[str, Any]) -> str:
    """
    The stable cross-run identifier for a finding. Migration 044's
    schema comment names this composition:
      "L{layer}.{metric}.{strategy}" — e.g. "L2.cagr.EQUITY".

    A finding with no strategy (Layer 1 / Layer 3 cross-platform
    checks) uses an underscore in the strategy slot to keep the
    identifier well-formed and unambiguous. The result is bounded by
    the schema's 120-character column limit and truncated if
    necessary so a long check name does not break the join key.
    """
    layer = finding.get("layer")
    metric = (finding.get("metric") or "").strip() or "_"
    strategy = (finding.get("strategy") or "").strip() or "_"
    raw = f"L{layer}.{metric}.{strategy}"
    return raw[:120]


def _parse_numeric(text: Any) -> float | None:
    """Best-effort numeric parse for an audit_findings.platform_value
    string. Strips a trailing '%' so '4.5%' is treated as 4.5. None /
    empty / unparseable returns None — the caller falls back to a
    string equality check."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    # Strip a single trailing percent sign — the audit layers store
    # percentages as either "4.5" or "4.5%" depending on the metric.
    if s.endswith("%"):
        s = s[:-1].strip()
    try:
        return float(s)
    except ValueError:
        return None


def value_matches_within_tolerance(
    prev_numeric: float | None,
    prev_raw: str | None,
    current_value: Any,
    tolerance: float = DEFAULT_TOLERANCE,
) -> bool:
    """
    True when the current finding's platform_value matches the value
    stored on the audit_acknowledgements row closely enough to carry
    the ack forward.

    Numeric path: both sides parse as float; difference within
    `tolerance` of |prev|, or within ABS_TOLERANCE_NEAR_ZERO when the
    previous value is exactly zero (relative tolerance is undefined
    there).

    String path: when either side fails to parse as a number, fall
    back to exact string equality on the raw values. This is the
    path Layer 1 / Layer 3 findings use — many of their values are
    descriptive strings rather than numerics.
    """
    current_numeric = _parse_numeric(current_value)
    if prev_numeric is not None and current_numeric is not None:
        if prev_numeric == 0.0:
            return abs(current_numeric) <= ABS_TOLERANCE_NEAR_ZERO
        return abs(current_numeric - prev_numeric) / abs(prev_numeric) <= tolerance
    # Non-numeric — exact equality on the raw string.
    if prev_raw is not None:
        return str(prev_raw).strip() == str(current_value or "").strip()
    return False


async def record_acknowledgement(
    finding: dict[str, Any],
    note: str,
    acknowledged_by: str,
) -> None:
    """
    Upserts an audit_acknowledgements row when a reviewer manually
    acknowledges a finding. Called from resolve_finding's success
    path alongside the existing audit_findings UPDATE so the next
    re-run's carry pass can find this ack and apply it.

    Fail-open: a database error here logs and swallows. The
    audit_findings UPDATE that preceded it remains valid; the team
    will only see the next re-run drop the ack rather than the
    current run breaking.
    """
    check_id = compose_check_id(finding)
    pv = finding.get("platform_value")
    numeric = _parse_numeric(pv)
    raw = None if numeric is not None else (str(pv) if pv is not None else None)
    verdict = str(finding.get("status") or "warning").lower()
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return
        async with AsyncSessionLocal() as session:
            # No unique constraint on check_id in migration 044 (the
            # row outlives the audit_run that produced it, so multiple
            # ack rows over time are expected — but only one should
            # be unsuperseded at a time). On ack, mark every prior
            # unsuperseded row for this check_id superseded, then
            # INSERT the new row.
            await session.execute(text(
                "UPDATE audit_acknowledgements "
                "SET superseded = true, superseded_at = now() "
                "WHERE check_id = :c AND superseded = false"),
                {"c": check_id})
            await session.execute(text(
                "INSERT INTO audit_acknowledgements "
                "(check_id, verdict_at_ack, platform_value_at_ack, "
                " platform_value_raw, resolution_note, "
                " acknowledged_by) "
                "VALUES (:c, :v, :n, :r, :note, :by)"),
                {"c": check_id, "v": verdict,
                 "n": numeric, "r": raw,
                 "note": note, "by": acknowledged_by})
            await session.commit()
        log.info("audit_ack_recorded", check_id=check_id, by=acknowledged_by)
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_ack_record_failed",
                    check_id=check_id, error=str(exc))


async def supersede_acknowledgement(finding: dict[str, Any]) -> None:
    """
    Marks the unsuperseded audit_acknowledgements row for this
    finding's check_id as superseded. Called from resolve_finding's
    unresolve path (the manual Revoke flow) so a revoked ack does
    not silently get carried forward on the next re-run.

    Fail-open: a database error here does not undo the audit_findings
    UPDATE that already cleared resolved.
    """
    check_id = compose_check_id(finding)
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return
        async with AsyncSessionLocal() as session:
            await session.execute(text(
                "UPDATE audit_acknowledgements "
                "SET superseded = true, superseded_at = now() "
                "WHERE check_id = :c AND superseded = false"),
                {"c": check_id})
            await session.commit()
        log.info("audit_ack_superseded", check_id=check_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_ack_supersede_failed",
                    check_id=check_id, error=str(exc))


async def apply_carry(run_id: int) -> dict[str, int]:
    """
    The carry pass itself. Called after _store_findings persists the
    new audit_findings for a fresh run. Returns a counts dict for
    logging:
      {carried: N, value_changed: M, no_prior_ack: K, errors: E}

    Only WARN findings are eligible — FAIL has no acknowledge workflow
    so a FAIL cannot have an ack to carry.
    """
    counts = {"carried": 0, "value_changed": 0,
              "no_prior_ack": 0, "errors": 0}
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return counts
        async with AsyncSessionLocal() as session:
            # 1. Load every WARN finding from this run that is NOT
            #    already resolved (a fresh row starts unresolved by
            #    default; defensive in case anything pre-set it).
            rows = await session.execute(text(
                "SELECT id, layer, metric, strategy, platform_value "
                "FROM audit_findings "
                "WHERE audit_run_id = :rid "
                "  AND lower(status) = 'warning' "
                "  AND COALESCE(resolved, false) = false"),
                {"rid": run_id})
            findings = rows.fetchall()
            if not findings:
                return counts

            # 2. Pull every unsuperseded ack in one query so the loop
            #    below is in-memory rather than per-finding round trip.
            ack_rows = await session.execute(text(
                "SELECT check_id, platform_value_at_ack, "
                "       platform_value_raw, resolution_note, "
                "       acknowledged_by, id "
                "FROM audit_acknowledgements "
                "WHERE superseded = false"))
            acks_by_check: dict[str, dict[str, Any]] = {}
            for r in ack_rows.fetchall():
                acks_by_check[r[0]] = {
                    "platform_value_at_ack": r[1],
                    "platform_value_raw": r[2],
                    "resolution_note": r[3],
                    "acknowledged_by": r[4],
                    "id": r[5],
                }

            # 3. Decide per finding.
            for f in findings:
                fid, layer, metric, strategy, pv = f
                cid = compose_check_id({
                    "layer": layer, "metric": metric, "strategy": strategy,
                })
                ack = acks_by_check.get(cid)
                if ack is None:
                    counts["no_prior_ack"] += 1
                    continue
                if not value_matches_within_tolerance(
                    ack["platform_value_at_ack"],
                    ack["platform_value_raw"],
                    pv,
                ):
                    # The original review may no longer apply — mark
                    # the ack superseded so a future re-run with the
                    # same value drift does not carry it.
                    try:
                        await session.execute(text(
                            "UPDATE audit_acknowledgements "
                            "SET superseded = true, superseded_at = now() "
                            "WHERE id = :id"),
                            {"id": ack["id"]})
                        counts["value_changed"] += 1
                    except Exception as exc:  # noqa: BLE001
                        log.warning("audit_carry_supersede_failed",
                                    check_id=cid, error=str(exc))
                        counts["errors"] += 1
                    continue
                # Match — carry the ack forward.
                try:
                    await session.execute(text(
                        "UPDATE audit_findings "
                        "SET resolved = true, "
                        "    resolution_note = :note, "
                        "    resolved_by = 'auto_carry', "
                        "    resolved_at = now(), "
                        "    auto_acknowledged = true "
                        "WHERE id = :id"),
                        {"note": ack["resolution_note"], "id": fid})
                    counts["carried"] += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("audit_carry_apply_failed",
                                check_id=cid, error=str(exc))
                    counts["errors"] += 1
            await session.commit()
        log.info("audit_carry_complete", run_id=run_id, **counts)
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_carry_failed", run_id=run_id, error=str(exc))
        counts["errors"] += 1
    return counts
