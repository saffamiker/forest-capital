"""
backend/scripts/resolve_completed_triage_items.py

Calls tools.triage_resolver.resolve_triage_items for the triage items
the UAT-fix and Bob-fix commits addressed earlier in the May 21 queue:

  Bob fixes (commit 3b64389 — "fix -- Bob academic review Overall
  Readiness Assessment and section count (#128, #125)"):
    item_ids: [128, 125]

  UAT fixes (commit 21619d3 — "fix -- council button on explainer
  panel, settings gear, text overflow, spelling, cost empty state,
  agent engagement source"):
    item_ids: [59, 49, 3, 6, 4]

Standalone script — run with `python scripts/resolve_completed_triage_items.py`
from the backend directory. resolve_triage_items is fail-open per item:
an id missing from the database lands in `failed`, the rest still
resolve. Both batches are reported separately so failures are easy to
re-dispatch.
"""
from __future__ import annotations

import asyncio
import os
import sys

# Ensure the backend modules are importable when run from /backend.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _main() -> None:
    from tools.triage_resolver import resolve_triage_items

    print("=" * 70)
    print("Resolving Bob academic-review triage items (#128, #125)")
    print("=" * 70)
    bob = await resolve_triage_items(
        item_ids=[128, 125],
        resolution_note=(
            "Academic review section assembly fixed. Overall Readiness "
            "Assessment now renders. All expected sections present."
        ),
        fix_commit="3b64389",
        requires_retest=True,
    )
    print(f"  resolved:           {bob['resolved']}")
    print(f"  failed:             {bob['failed']}")
    print(f"  notified_reporters: {bob['notified_reporters']}")
    print(f"  item_titles:        {bob['item_titles']}")

    print()
    print("=" * 70)
    print("Resolving UAT triage items (#59, #49, #3, #6, #4)")
    print("=" * 70)
    uat = await resolve_triage_items(
        item_ids=[59, 49, 3, 6, 4],
        resolution_note=(
            "Council button on Data Explain panel restored; settings "
            "gear now navigates to /settings; text overflow on explainer "
            "panels fixed with break-words/overflow-wrap; 'Organisation' "
            "spelling corrected to 'Organization'; cost-tracking empty "
            "state distinguishes \"no spend yet\" from \"no interactions \""
            "; agent engagement tile now reads summary.most_active_agents "
            "instead of the paginated events feed."
        ),
        fix_commit="21619d3",
        requires_retest=True,
    )
    print(f"  resolved:           {uat['resolved']}")
    print(f"  failed:             {uat['failed']}")
    print(f"  notified_reporters: {uat['notified_reporters']}")
    print(f"  item_titles:        {uat['item_titles']}")


if __name__ == "__main__":
    asyncio.run(_main())
