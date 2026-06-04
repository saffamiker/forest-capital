"""Tour v3 + UAT v9 — surface PR #257 (/admin/health) and PR #264
(AuditWarningsBanner) in the onboarding tour and the validation
test suite.

June 3 2026.

WHY:
The previous /admin/health and AuditWarningsBanner PRs shipped real
user-facing surfaces with no path through the guided tour and no
coverage in the UAT scripts. A site-tour-and-UAT audit (run after
PRs #265-#269 landed) flagged both as zero-coverage gaps.

WHAT THIS MIGRATION DOES:
Two changelog entries — one per concern, per the convention that
every migration ships at least one changelog row:

  - version 72: TOUR_VERSION 2 → 3
      Tour gains a step targeting the Runtime health panel link in
      Settings (target="[data-tour=admin-health-runtime-link]",
      route="/settings"). The document-editor step body extends to
      mention the AuditWarningsBanner — numeric mismatches, label
      direction errors, cross-section inconsistencies, and missing
      citations — so users know it's coming when they open a
      generated draft.

      Bumping TOUR_VERSION makes /api/v1/changelog/unseen report
      has_tour_update=true for every user whose
      last_tour_version_seen < 3 — the tour re-surfaces with the
      two new pieces.

  - version 73: TEST_SCRIPT_VERSION 8 → 9
      Twelve new test steps:

      michael_ruurds_v1 — six /admin/health checks (Settings quick
      link → page load → invariant verdict → Layer 4 → warm history
      → any-user access) plus a curl smoke check confirming
      /api/v1/admin/council-metrics carries
      cio_token_reduction_vs_baseline per question_type.

      molly_murdock_v1 — five AuditWarningsBanner checks wedged
      between molly_deck and molly_export_zip (open the just-
      generated draft, banner renders, flag rows show finding +
      suggested fix, expand/collapse, persists on re-open per
      migration 051).

      Bumping TEST_SCRIPT_VERSION makes /api/v1/testing/unseen
      report a "test cases available" login notification for every
      tester whose most-recent attestation < 9.

CODE CHANGES (in the same commit, NOT in this migration):
  - backend/config.py: TOUR_VERSION 2→3, TEST_SCRIPT_VERSION 8→9
  - frontend/src/constants/tourSteps.ts: +runtime-health step,
    document-editor body updated
  - frontend/src/constants/testScripts.ts: TEST_SCRIPT_VERSION
    constant 8→9, new steps added to michael_ruurds_v1 + molly_
    murdock_v1
  - frontend/src/pages/Settings.tsx: data-tour="admin-health-
    runtime-link" on the RuntimeHealthLink button so the new tour
    step has an anchor to target

DOWNGRADE deletes both changelog rows. The TOUR_VERSION and
TEST_SCRIPT_VERSION constants in config.py would also need to
revert to 2 / 8 to fully undo — a separate code change, not a
DB migration concern.
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "053"
down_revision: str | None = "052"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    changelog = sa.table(
        "changelog",
        sa.column("version", sa.Integer),
        sa.column("released_at", sa.TIMESTAMP(timezone=True)),
        sa.column("title", sa.String),
        sa.column("description", sa.Text),
        sa.column("academic_rationale", sa.Text),
        sa.column("tour_step_id", sa.String),
    )
    op.bulk_insert(changelog, [
        {
            "version": 72,
            "released_at": datetime(2026, 6, 3, tzinfo=timezone.utc),
            "title": "Site tour v3 — runtime health + audit warnings",
            "description": (
                "TOUR_VERSION bumps 2 → 3 so the guided tour re-"
                "surfaces with two pieces. A new step targets the "
                "Runtime health panel quick-link in Settings — the "
                "/admin/health page is the platform's runtime-"
                "validation surface (top-line invariant verdict + "
                "Layer 4 data-quality fixtures + warm history of the "
                "last seven runs from PR #257). The document-editor "
                "step's body extends to mention the AuditWarnings"
                "Banner — the four deterministic checks (numeric "
                "mismatches, label direction errors, cross-section "
                "inconsistencies, missing citations) from PR #264 — "
                "so users know the banner is coming when they open a "
                "generated draft."
            ),
            "academic_rationale": (
                "The /admin/health verdict and the document audit "
                "warnings are the two most direct surfaces a grader "
                "would use to verify that the analytical surface is "
                "live and the deliverable is internally consistent. "
                "Pointing every user at them on the guided tour "
                "shifts the team's catch-rate on issues from "
                "reactive (after submission) to proactive (before "
                "submission), which directly improves the rigor "
                "score on the rubric."
            ),
            "tour_step_id": "runtime-health",
        },
        {
            "version": 73,
            "released_at": datetime(2026, 6, 3, tzinfo=timezone.utc),
            "title": "UAT test script v9 — PR #257 + #264 + #265 coverage",
            "description": (
                "TEST_SCRIPT_VERSION bumps 8 → 9. Twelve new test "
                "steps cover features that shipped without UAT "
                "coverage: six /admin/health checks in "
                "michael_ruurds_v1 (Settings quick-link → page load "
                "→ invariant verdict → Layer 4 → warm history → "
                "any-user access), one curl smoke check against "
                "/api/v1/admin/council-metrics confirming the "
                "cio_token_reduction_vs_baseline aggregate from PR "
                "#265 is in the response, and five AuditWarnings"
                "Banner checks in molly_murdock_v1 wedged between "
                "molly_deck and molly_export_zip (open the just-"
                "generated draft, banner renders, flag rows show "
                "finding + suggested fix, expand/collapse, persists "
                "on re-open per migration 051)."
            ),
            "academic_rationale": (
                "The site-tour-and-UAT audit run after PRs #265–#269 "
                "landed showed zero coverage for /admin/health, the "
                "AuditWarningsBanner, and the council-metrics CIO-"
                "input aggregate across all four test scripts. UAT "
                "is the team's last verification gate before "
                "submission; surfaces without UAT coverage have no "
                "before-grader check that they work as documented. "
                "Bumping the script version forces re-attestation so "
                "the gap closes on the next UAT pass."
            ),
            "tour_step_id": None,
        },
    ])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version IN (72, 73)")
