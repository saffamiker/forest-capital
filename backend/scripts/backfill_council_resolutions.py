"""
backend/scripts/backfill_council_resolutions.py

One-off back-fill for the four council failures PR #65 fixed.

PR #65 ("May 21 batch — UAT/Bob fixes + Triage resolution (6) + Chart
vision (5) + Macro research (5)") shipped the council SSE streaming
fix that resolved the 502 timeouts under sequential agent calls. The
PR-driven Suggested Resolutions automation didn't exist at merge time,
so no resolution rows were created on test_results. This script
back-fills them.

For each unresolved failure whose step_id matches the four council
test steps PR #65 addressed, it calls tools.test_runner.resolve_failure
with the structured resolution metadata (resolution_type =
code_fix_deployed, fix_reference = "#65", plus the root-cause and
remediation notes). The function is the same one POST
/api/v1/testing/failures/{id}/resolve calls under the hood — direct
invocation produces the bitwise-identical row state and the same
retest notification fires (notifications are derived from
test_results state by get_notifications, not pushed).

Run from the Render shell:

    python backend/scripts/backfill_council_resolutions.py

IDEMPOTENT — the query filters on `resolved_at IS NULL` so a second
run skips rows the first run already resolved.

The user who appears in resolved_by is hardcoded to ruurdsm@queens.edu
— the project sysadmin who would be running this back-fill. Mirrors
what `session.get("email")` would set on a real HTTP POST against the
endpoint.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

# Ensure the backend modules are importable when run from /backend.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Step IDs PR #65's council SSE fix addressed. The four titles in the
# back-fill request map onto these step_ids in testScripts.ts:
#   council_submit      — "Council returns multi-agent responses"
#   council_markdown    — "Responses render as markdown"
#   bob_council_ask     — "Ask the 2022 diversification question"
#   bob_review_readiness — "Overall Readiness assessment"
TARGET_STEP_IDS = (
    "council_submit",
    "council_markdown",
    "bob_council_ask",
    "bob_review_readiness",
)


# Resolution payload — exactly as specified in the back-fill request.
RESOLUTION = {
    "resolved_by":      "ruurdsm@queens.edu",
    "resolution_note":  "Council 502 timeout under sequential agent calls.",
    "resolution_type":  "code_fix_deployed",
    "fix_reference":    "#65",
    "remediation_note": (
        "Stream council via SSE; specialists run in parallel via "
        "ThreadPoolExecutor."
    ),
}


async def _find_unresolved_council_failures() -> list[dict[str, Any]]:
    """Returns every unresolved test_results row whose step_id is in
    TARGET_STEP_IDS. The query mirrors the get_all_failures filter
    (result = 'fail') and adds the resolved_at IS NULL clause so the
    back-fill is idempotent — re-running the script after the first
    resolution lands is a no-op."""
    from sqlalchemy import text

    from database import AsyncSessionLocal
    if AsyncSessionLocal is None:
        print("[!] DATABASE_URL not configured — cannot run the back-fill.")
        return []
    async with AsyncSessionLocal() as session:
        rows = await session.execute(text("""
            SELECT id, user_email, script_id, step_id, failure_description,
                   attested_at
            FROM test_results
            WHERE result = 'fail'
              AND resolved_at IS NULL
              AND step_id = ANY(:step_ids)
            ORDER BY user_email, step_id
        """), {"step_ids": list(TARGET_STEP_IDS)})
        return [{
            "id":                  r[0],
            "user_email":          r[1],
            "script_id":           r[2],
            "step_id":             r[3],
            "failure_description": r[4],
            "attested_at":         r[5],
        } for r in rows.fetchall()]


async def _main() -> None:
    from tools.test_runner import resolve_failure

    print("=" * 70)
    print("Back-fill: council failures fixed by PR #65")
    print("=" * 70)

    failures = await _find_unresolved_council_failures()
    if not failures:
        print("\nNo unresolved failures matching the four council step IDs.")
        print("Nothing to do — the back-fill is idempotent and may already")
        print("have been applied. Exiting cleanly.")
        return

    print(f"\nFound {len(failures)} unresolved council failure(s):\n")
    for f in failures:
        attested = f["attested_at"].isoformat() if f["attested_at"] else "—"
        print(f"  id={f['id']:>5}  step={f['step_id']:<22}  "
              f"tester={f['user_email']:<22}  attested={attested}")

    print()
    print("=" * 70)
    print("Resolving each via tools.test_runner.resolve_failure")
    print(f"  resolved_by:      {RESOLUTION['resolved_by']}")
    print(f"  resolution_type:  {RESOLUTION['resolution_type']}")
    print(f"  fix_reference:    {RESOLUTION['fix_reference']}")
    print(f"  resolution_note:  {RESOLUTION['resolution_note']}")
    print(f"  remediation_note: {RESOLUTION['remediation_note']}")
    print("=" * 70)
    print()

    resolved_ids: list[int] = []
    failed_ids: list[int] = []
    notified_testers: set[str] = set()

    for f in failures:
        result = await resolve_failure(
            f["id"],
            RESOLUTION["resolved_by"],
            RESOLUTION["resolution_note"],
            resolution_type=RESOLUTION["resolution_type"],
            fix_reference=RESOLUTION["fix_reference"],
            remediation_note=RESOLUTION["remediation_note"],
        )
        if result is None:
            print(f"  [FAIL] id={f['id']} — resolve_failure returned None")
            failed_ids.append(f["id"])
            continue
        resolved_ids.append(f["id"])
        notified_testers.add(result["user_email"])
        print(f"  [OK]   id={f['id']:>5}  step={f['step_id']:<22}  "
              f"→ notify {result['user_email']}")

    print()
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"  Resolved:  {len(resolved_ids)} — ids {resolved_ids}")
    if failed_ids:
        print(f"  Failed:    {len(failed_ids)} — ids {failed_ids}")
    print(f"  Testers to be notified on next login: "
          f"{sorted(notified_testers)}")
    print()
    print("Notifications fire automatically — get_notifications() derives")
    print("the resolved_failures kind from the test_results state, so each")
    print("listed tester sees the three-variant 'Code fix deployed' card")
    print("on next login.")


if __name__ == "__main__":
    asyncio.run(_main())
