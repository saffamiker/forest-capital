"""
tools/report_readiness.py — Workstream C (May 28 2026).

Aggregates the report-blocking conditions from both audit surfaces and
returns a single readiness verdict the frontend and the generation gate
both consume.

Blocking conditions (a report is NOT ready while any of these hold):

  Statistical audit (audit_findings):
    - Any WARN finding from the most recent completed audit run that
      has NOT been acknowledged (resolved=false).
    - Any FAIL finding from the most recent completed audit run. A FAIL
      is a material discrepancy by the audit's own definition; the
      audit panel has no acknowledge workflow for FAILs because a FAIL
      is supposed to be corrected, not disclosed.

  Methodology audit (qa_results_cache):
    - Any WARN check from the latest QA audit whose check_id does NOT
      appear in qa_intentional_overrides.
    - Any FAIL check from the latest QA audit. FAIL is similarly
      uncorrectable by disclosure — it must be fixed before reporting.

INCOMPLETE methodology checks are NOT treated as blocking — they signal
that the audit did not finish examining the check, not that a concern
was found. The reports surface them in their own way (workstream D's
appendix mentions incomplete checks) but they do not gate generation.

The readiness response is FAIL-OPEN — when either audit surface cannot
be read (database unreachable, no audit ever run, malformed cache row)
the corresponding section returns empty lists. A platform with no
audit history reports is_ready=true; the gate refuses to block on
something that does not exist.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# A blocking item from the statistical audit. Pinned to the columns the
# frontend modal needs to identify the finding — the user follows the
# link back to the QA / Statistical Audit panel to act on it.
def _stat_blocker(row: Any) -> dict[str, Any]:
    return {
        "finding_id": int(row[0]) if row[0] is not None else None,
        "layer": int(row[1]) if row[1] is not None else None,
        "check_name": row[2],
        "metric": row[3],
        "strategy": row[4],
        "status": row[5],
        "discrepancy": row[6],
    }


async def _statistical_blocking() -> dict[str, list[dict[str, Any]]]:
    """
    Reads unresolved blocking findings from the most recent completed
    audit run. Returns {unreviewed_warnings, unreviewed_failures}.

    Fail-open: a database outage or an empty audit_runs table returns
    empty lists. The gate refuses to block on something that does not
    exist.
    """
    empty = {"unreviewed_warnings": [], "unreviewed_failures": []}
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return empty
        async with AsyncSessionLocal() as session:
            # Use the most recent audit_run regardless of triggered_by
            # — a demo run still counts. We exclude runs that are still
            # in flight (status != 'complete') so a half-finished run
            # never blocks the report.
            run = await session.execute(text(
                "SELECT id FROM audit_runs "
                "WHERE status = 'complete' "
                "ORDER BY id DESC LIMIT 1"))
            run_row = run.fetchone()
            if run_row is None:
                return empty
            run_id = int(run_row[0])

            # WARN findings: blocking ONLY while unresolved. A WARN
            # with resolved=true counts as reviewed, so the team's
            # acknowledgement clears it.
            warns = await session.execute(text(
                "SELECT id, layer, check_name, metric, strategy, "
                "status, discrepancy "
                "FROM audit_findings "
                "WHERE audit_run_id = :id "
                "  AND lower(status) = 'warning' "
                "  AND COALESCE(resolved, false) = false "
                "ORDER BY layer, id"),
                {"id": run_id})
            warning_rows = [_stat_blocker(r) for r in warns.fetchall()]

            # FAIL findings: always blocking. A FAIL has no acknowledge
            # path in the panel — it is supposed to be corrected, not
            # disclosed. resolved is intentionally not filtered.
            fails = await session.execute(text(
                "SELECT id, layer, check_name, metric, strategy, "
                "status, discrepancy "
                "FROM audit_findings "
                "WHERE audit_run_id = :id "
                "  AND lower(status) IN ('fail', 'failed', 'critical') "
                "ORDER BY layer, id"),
                {"id": run_id})
            failure_rows = [_stat_blocker(r) for r in fails.fetchall()]
        return {
            "unreviewed_warnings": warning_rows,
            "unreviewed_failures": failure_rows,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("readiness_statistical_read_failed", error=str(exc))
        return empty


def _meth_blocker(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "check_id": item.get("check_id"),
        "check": item.get("check"),
        "description": item.get("description"),
        "category": item.get("category"),
        "status": item.get("status"),
    }


def _is_non_blocking_warn(item: dict[str, Any]) -> bool:
    """A WARN whose per-check classification (warn_class) is
    'non_blocking' is informational only and must NOT gate report
    generation.

    The check classifications are defined in
    agents/qa_agent.py:_SUBMISSION_CLASSIFICATIONS and attached to
    every checklist item at module import time -- so the item dict
    we receive here typically carries `warn_class` as a leaf field.
    The three values defined there:

      "disclosure_required" -- blocks until acknowledged (an
                                qa_intentional_overrides row exists)
      "non_blocking"        -- never blocks (informational)
      "blocks"              -- treated as failure (always blocks)

    Source-of-truth ordering (bridge #85):
      1. The static _SUBMISSION_CLASSIFICATIONS map keyed by check_id.
         The classification lives in code, not in the row; a cached
         qa_results_cache item written before a reclassification ships
         carries STALE warn_class data, which would otherwise keep
         the gate blocking on a finding the taxonomy now says is
         advisory. Consulting the static map first means the gate
         survives a taxonomy change without requiring cache
         invalidation. IN02 was the canonical instance: PR #300
         flipped it to non_blocking, but the cached row on Render
         still carried "disclosure_required", so generation kept
         blocking until the next audit run.
      2. The per-item warn_class field on the row (legacy / cached
         fallback). Still consulted so the gate works in test
         fixtures and for any check_id missing from the static map.
      3. Default to False (not non-blocking) so a new check added
         without a classification cannot silently sneak through.
    """
    cid = item.get("check_id")
    if cid:
        try:
            from agents.qa_agent import _SUBMISSION_CLASSIFICATIONS
            cls = _SUBMISSION_CLASSIFICATIONS.get(cid)
            if cls and cls.get("warn_class"):
                static = str(cls["warn_class"]).strip().lower()
                return static == "non_blocking"
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "readiness_static_classify_failed",
                check_id=cid, error=str(exc))
    warn_class = str(item.get("warn_class") or "").strip().lower()
    return warn_class == "non_blocking"


async def _methodology_blocking() -> dict[str, list[dict[str, Any]]]:
    """
    Reads unresolved blocking checks from the most recent QA audit.
    A WARN check is blocking unless it has a qa_intentional_overrides
    row. A FAIL check is always blocking — FAIL has no override path.

    Fail-open: when no QA audit exists yet, returns empty lists.
    """
    empty = {"unresolved_warnings": [], "unresolved_failures": []}
    try:
        from tools.cache import get_most_recent_qa_run

        recent = await get_most_recent_qa_run(min_tier=1)
        if recent is None:
            return empty
        checklist = recent.get("checklist") or {}
        items = checklist.get("items") or []
        if not items:
            return empty

        # Load the set of check_ids that have a recorded intentional
        # override. A WARN with an override is reviewed; without one
        # it blocks. Direct DB read mirrors the endpoint in main.py
        # so the readiness check uses the same source of truth.
        override_ids: set[str] = set()
        try:
            from sqlalchemy import text

            from database import AsyncSessionLocal
            if AsyncSessionLocal is not None:
                async with AsyncSessionLocal() as session:
                    rows = await session.execute(text(
                        "SELECT check_id FROM qa_intentional_overrides"))
                    override_ids = {row[0] for row in rows.fetchall()}
        except Exception as exc:  # noqa: BLE001
            log.warning("readiness_overrides_read_failed", error=str(exc))

        warnings = []
        failures = []
        for it in items:
            status = str(it.get("status") or "").upper()
            cid = it.get("check_id")
            if status == "WARN":
                if cid and cid in override_ids:
                    continue
                # Bridge #74 fix: respect the per-check warn_class
                # taxonomy from qa_agent._SUBMISSION_CLASSIFICATIONS.
                # A non_blocking WARN (AN03 sensitivity, E01 economic
                # significance) is informational only and must never
                # gate generation. Without this check the gate
                # mis-treated every WARN as blocking-pending-override,
                # which is wrong for the explicitly non_blocking
                # checks the audit panel surfaces as advisory.
                if _is_non_blocking_warn(it):
                    continue
                warnings.append(_meth_blocker(it))
            elif status == "FAIL":
                failures.append(_meth_blocker(it))
        return {
            "unresolved_warnings": warnings,
            "unresolved_failures": failures,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("readiness_methodology_read_failed", error=str(exc))
        return empty


# Bridge #91 — the caches the deck / brief / appendix generation reads
# from. cache_warm_state.WarmState.last_landed reports a per-row boolean
# from the most recent auto-warm cycle. A row landed=False means the
# downstream generator will read [DATA PENDING] for whatever section
# depends on that cache — exactly the bug we want to catch BEFORE the
# generator fires, not after.
#
# Keep the list narrow: only the analytics-layer caches that we know
# the generators consume. strategy_results_cache is upstream of every
# row in last_landed (the analytics layer reads it) but is not in
# last_landed itself — it's covered transitively because all six
# analytics rows depend on it.
_REQUIRED_CACHE_KEYS: tuple[str, ...] = (
    "academic_analytics",
    "efficient_frontier",
    "cio_recommendation",
    "performance_chart",
    "forward_projection",
    "oos_cost_sensitivity",
)


def _caches_warm() -> dict[str, Any]:
    """Reads the in-process WarmState (no DB, no recompute) and reports
    whether every analytics-layer cache the generators consume has
    landed in the most recent warm cycle.

    Returns:
        {
          "caches_warm": True / False,
          "cold_caches": ["academic_analytics", ...],
          "warm_status": "idle" | "warming" | "warm" | "failed",
        }

    Fail-open: if cache_warm_state is unavailable for any reason
    (cold import, test env stub) the helper returns caches_warm=True
    so a transient internal error doesn't block the user. The actual
    gate decision lives in compute_readiness, which respects this.

    Test env: the auto-warm hook is disabled in tests and the
    WarmState stays at status='idle' / last_landed={}, which would
    otherwise add a synthetic blocker to every existing readiness
    fixture. Treat the test env as warm so the test suite stays
    decoupled from this signal -- tests that exercise the cold-cache
    branch explicitly stub _caches_warm.
    """
    import os
    if os.environ.get("ENVIRONMENT") == "test":
        return {
            "caches_warm": True,
            "cold_caches": [],
            "warm_status": "warm",
        }
    try:
        from tools.cache_warm_state import get_warm_state

        state = get_warm_state()
        landed = state.last_landed or {}
        cold = [k for k in _REQUIRED_CACHE_KEYS
                if not bool(landed.get(k))]
        return {
            "caches_warm": state.status == "warm" and not cold,
            "cold_caches": cold,
            "warm_status": state.status,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("readiness_warm_state_read_failed", error=str(exc))
        return {
            "caches_warm": True,
            "cold_caches": [],
            "warm_status": "unknown",
        }


async def compute_readiness(
    exclude_methodology_check_ids: set[str] | None = None,
) -> dict[str, Any]:
    """
    The single readiness verdict. Combines the statistical and
    methodology blockers and counts them. Used by:
      - GET /api/v1/report/readiness — the frontend readiness indicator
      - _require_report_ready() — the generation-endpoint gate

    exclude_methodology_check_ids — the per-document advisory escape
    hatch (May 25 2026). Midpoint generation passes {"IN02"} so the
    Academic Review complete check is downgraded to advisory for that
    document type only; an IN02 WARN/FAIL still blocks the executive
    brief and the presentation deck. Filtered findings are removed
    from the methodology lists so they do not surface in the 422
    detail either — the user is told only about items that still
    block, not items that are intentionally being treated as advisory.
    """
    statistical = await _statistical_blocking()
    methodology = await _methodology_blocking()
    if exclude_methodology_check_ids:
        excl = exclude_methodology_check_ids
        methodology = {
            "unresolved_warnings": [
                it for it in methodology["unresolved_warnings"]
                if it.get("check_id") not in excl
            ],
            "unresolved_failures": [
                it for it in methodology["unresolved_failures"]
                if it.get("check_id") not in excl
            ],
        }
    blocking_count = (
        len(statistical["unreviewed_warnings"])
        + len(statistical["unreviewed_failures"])
        + len(methodology["unresolved_warnings"])
        + len(methodology["unresolved_failures"])
    )
    # Bridge #91 — pre-generation cache-warmth gate. A cold cache
    # produces a [DATA PENDING] generator output that the user
    # discovers post-generation. Including caches_warm here puts the
    # check on the same pre-flight path the statistical and
    # methodology blockers use, so the Generate endpoint refuses BEFORE
    # the LLM run rather than after. caches_warm=False contributes ONE
    # blocker (the cold caches collectively) so the count stays
    # meaningful when a normal blocker also exists.
    cache_verdict = _caches_warm()
    if not cache_verdict["caches_warm"]:
        blocking_count += 1
    return {
        "is_ready": blocking_count == 0,
        "blocking_count": blocking_count,
        "statistical": statistical,
        "methodology": methodology,
        "caches_warm": cache_verdict["caches_warm"],
        "cold_caches": cache_verdict["cold_caches"],
        "warm_status": cache_verdict["warm_status"],
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def summarise_blockers(readiness: dict[str, Any]) -> list[str]:
    """
    Renders the blocking items as a short, human-readable list of
    strings for use in the 422 error detail and the frontend modal.
    Each entry names the surface, the kind of block, and a label
    identifying the finding so the team can act on it.

    Bridge #91 — a cold cache surfaces as its own blocker entry so
    the user sees WHICH caches need warming, not just that the gate
    refused. The cache list is rendered before the audit blockers so
    the Warm Caches action is the most prominent affordance.
    """
    out: list[str] = []
    if readiness.get("caches_warm") is False:
        cold = readiness.get("cold_caches") or []
        if cold:
            out.append(
                "Caches are not warm — cold: " + ", ".join(cold))
        else:
            out.append(
                "Caches are not warm — run the analytics warm before "
                "generation.")
    stat = readiness.get("statistical") or {}
    meth = readiness.get("methodology") or {}
    for f in stat.get("unreviewed_failures") or []:
        layer = f.get("layer")
        label = f.get("check_name") or f.get("metric") or "(unnamed)"
        out.append(f"Statistical FAIL — L{layer} · {label}")
    for f in stat.get("unreviewed_warnings") or []:
        layer = f.get("layer")
        label = f.get("check_name") or f.get("metric") or "(unnamed)"
        out.append(f"Statistical WARN unreviewed — L{layer} · {label}")
    for it in meth.get("unresolved_failures") or []:
        cid = it.get("check_id") or "?"
        label = it.get("check") or it.get("description") or "(unnamed)"
        out.append(f"Methodology FAIL — {cid} · {label}")
    for it in meth.get("unresolved_warnings") or []:
        cid = it.get("check_id") or "?"
        label = it.get("check") or it.get("description") or "(unnamed)"
        out.append(f"Methodology WARN unreviewed — {cid} · {label}")
    return out
