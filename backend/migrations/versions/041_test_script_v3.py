"""Test script version bump v2 → v3 — Analytics study-period MM-DD-YYYY alignment.

May 24 2026 (UAT bug #117).

WHY:
The `an_study_period` test step in `michael_ruurds_v1` listed an
expected result in natural-language form ("Jul 2002 to the most
recent ingested month — 286 months on Render now"). Two things
had drifted:

  1. The Analytics Study Period line was rendering raw-ISO dates
     ("2002-07-31 → 2026-04-30") — the last surface on the
     Analytics page that hadn't picked up the platform-wide
     MM-DD-YYYY convention introduced in PR #120 (commit
     bdbd702 / ID 278 / `dateFormat.ts`).
  2. The pipeline auto-extends past the Excel anchor each month
     (MONTHLY DATA AUTO-EXTENSION, May 18 2026) so the hardcoded
     "286 months" claim only stays accurate until the next
     month closes.

This migration ships nothing schema-side — it's a data-only
changelog entry. The actual fix lives in two places:

  - `frontend/src/pages/AcademicAnalytics.tsx` — `study_period.start`
    and `study_period.end` (plus `ffTable.min_date` / `max_date`)
    now flow through `formatDate()` so the rendered Study Period
    line reads as "07-31-2002 → 04-30-2026".
  - `frontend/src/constants/testScripts.ts` — the
    `an_study_period` step's `expectedResult` re-written to
    name the MM-DD-YYYY format and the "286 or higher" fact so
    the test stays correct as months continue to land.

WHAT THIS MIGRATION DOES:
  - Inserts changelog version 60 (UAT 117) per the changelog
    contract for every migration from 011 onward.
  - The TEST_SCRIPT_VERSION bump 2 → 3 happens in `config.py`
    and `frontend/src/constants/testScripts.ts` (in the same
    commit). On the next user login, `/api/v1/testing/unseen`
    sees their last-attested version < 3 and surfaces a "test
    cases available" login notification so the affected step
    is re-attested under the new wording.

A bump is the right tool here: the step's *meaning* didn't
change, but its *expected result text* did, and stale
attestations would otherwise pass a test the tester never read
the new wording for.

Downgrade only deletes the changelog row — there's no schema
state to reverse. A real downgrade would also need to roll
TEST_SCRIPT_VERSION back to 2 in code, which is a separate code
change, not a DB migration concern.
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "041"
down_revision: str | None = "040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL)"
    ).bindparams(
        v=60,
        rel=datetime.now(timezone.utc),
        t="Study period date format + test script v3",
        d=(
            "The Analytics page's Study Period line now renders "
            "dates as MM-DD-YYYY (e.g. \"07-31-2002 → 04-30-2026\") "
            "matching the platform-wide date format introduced in "
            "PR #120. The Factor Model line — the only other "
            "raw-ISO date pair on the Analytics page — was "
            "wrapped through the same helper. The UAT test step "
            "`an_study_period` was re-written so its expected "
            "result names the new format and accommodates the "
            "month count growing past 286 as the pipeline auto-"
            "extends past each closed calendar month."),
        a=(
            "Date format inconsistencies are the kind of detail "
            "graders catch first. The Analytics page is the "
            "primary surface the midpoint paper cites; pinning "
            "every visible date on it through the same helper "
            "removes the chance that a reader sees one date in "
            "MM-DD-YYYY and another in YYYY-MM-DD on the same "
            "screen. The test script bump forces a fresh "
            "attestation so the next UAT cycle picks up the "
            "exact wording the team will defend in the panel."),
    ))


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 60")
