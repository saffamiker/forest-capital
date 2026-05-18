"""
tools/audit_common.py — shared helpers for the three audit layers.

A "finding" is one row of the audit_findings table. make_finding()
builds the dict every layer emits; layer_status() rolls a layer's
findings up into its pass / fail status.
"""
from __future__ import annotations

from typing import Any

# Tolerance bands for a numeric recomputation (Layer 2). A discrepancy
# is the absolute fractional difference |auditor - platform| / |platform|.
TOLERANCE_PASS = 0.0001     # <= 0.01% — pass
TOLERANCE_WARN = 0.001      # 0.01%–0.1% — warning; above 0.1% — fail


def make_finding(
    layer: int,
    check_name: str,
    metric: str,
    status: str,
    severity: str,
    *,
    strategy: str | None = None,
    platform_value: Any = None,
    auditor_value: Any = None,
    discrepancy: str | None = None,
    formula_used: str | None = None,
    raw_inputs_hash: str | None = None,
    auditor_reasoning: str | None = None,
) -> dict[str, Any]:
    """One audit finding. status is pass | fail | warning; severity is
    critical | warning | info. Values are stringified for the text
    columns so any numeric type stores cleanly."""
    return {
        "layer": layer,
        "check_name": check_name,
        "metric": metric,
        "strategy": strategy,
        "severity": severity,
        "status": status,
        "platform_value": None if platform_value is None else str(platform_value),
        "auditor_value": None if auditor_value is None else str(auditor_value),
        "discrepancy": discrepancy,
        "formula_used": formula_used,
        "raw_inputs_hash": raw_inputs_hash,
        "auditor_reasoning": auditor_reasoning,
    }


def layer_status(findings: list[dict[str, Any]]) -> str:
    """A layer passes unless one of its findings failed. Warnings and
    skips do not fail a layer."""
    return "fail" if any(f.get("status") == "fail" for f in findings) else "pass"


def classify_discrepancy(platform: float, auditor: float) -> tuple[str, float]:
    """Compares a platform value with the auditor's recomputed value and
    returns (status, discrepancy_fraction). PASS within 0.01%, WARNING up
    to 0.1%, FAIL beyond — or on a sign flip (directionally wrong)."""
    if platform == 0.0:
        diff = abs(auditor)
        frac = diff
    else:
        frac = abs(auditor - platform) / abs(platform)
    # A sign flip on a non-trivial value is always a failure.
    if platform * auditor < 0 and max(abs(platform), abs(auditor)) > 1e-6:
        return "fail", frac
    if frac <= TOLERANCE_PASS:
        return "pass", frac
    if frac <= TOLERANCE_WARN:
        return "warning", frac
    return "fail", frac
