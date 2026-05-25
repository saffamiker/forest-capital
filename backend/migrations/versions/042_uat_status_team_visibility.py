"""view_uat_status — make the Test Administration panel visible to
every team member.

May 24 2026 (UAT issue #119).

WHY:
Bob and Molly want to see real-time UAT progress (failures, issue
tracker, feedback backlog) from Settings → Test Administration without
needing sysadmin access. Previously the three read endpoints
(failures / issue-tracker / feedback) were gated on view_admin and
only ruurdsm@ could see the data. The action endpoints (resolve
failure, resolve feedback, trigger triage, approve suggestion) remain
manage_users / view_admin gated — team_member READS but does NOT MUTATE.

WHAT THIS MIGRATION ADDS:
- The new `view_uat_status` permission appended to every active
  team_member and sysadmin platform_users row. Migration 015 seeded
  those rows with the permissions known at the time; this migration
  back-fills the new permission so no admin has to manually re-grant
  it after the application code change ships.

- A "Custom" permission set (a sysadmin manually edited the array
  away from a preset) is left alone — the migration only adds to
  rows that don't already have it.

The PERMISSIONS / ROLE_PRESETS constants in config.py are updated
in the same commit; rows minted AFTER this migration runs (new users
added by a sysadmin) will already carry view_uat_status because the
preset they're seeded from carries it.

VIEWER rows are NOT updated — the viewer preset is intentionally
narrow (view_analytics + ask_council). A guest (Dr. Panttser at
review time) should not see the UAT backlog.

Downgrade removes view_uat_status from every row that has it, so a
rollback uniformly reverses the back-fill.
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "042"
down_revision: str | None = "041"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # PostgreSQL array_append (idempotent — running the migration
    # twice does NOT duplicate the entry because the WHERE clause
    # excludes rows that already carry it). The role gate is on
    # role IN ('team_member', 'sysadmin') so viewer rows stay narrow.
    op.execute(sa.text("""
        UPDATE platform_users
        SET permissions = array_append(permissions, 'view_uat_status')
        WHERE is_active = true
          AND role IN ('team_member', 'sysadmin')
          AND NOT ('view_uat_status' = ANY(permissions))
    """))

    # ── Changelog ──────────────────────────────────────────────────────────
    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL)"
    ).bindparams(
        v=61,
        rel=datetime.now(timezone.utc),
        t="UAT status panel — visible to every team member, read-only",
        d=(
            "Bob and Molly now see real-time UAT progress in "
            "Settings → Test Administration: every failure report, "
            "the issue tracker, and the feedback backlog. The data "
            "is READ-ONLY for them — Mark Resolved, the Triage "
            "Reports block, the Suggested Resolutions banner, and "
            "the editable feedback status select stay sysadmin-only. "
            "A new view_uat_status permission opens the data; the "
            "existing manage_users / view_admin gates on the action "
            "endpoints stay unchanged so a team_member READS but "
            "does NOT MUTATE. This migration back-fills the new "
            "permission onto every active team_member + sysadmin "
            "row so no manual re-grant is needed."),
        a=(
            "Bob writes the midpoint paper and the executive brief "
            "against the UAT outcomes; Molly builds the presentation "
            "deck and rehearsal flow knowing which test paths still "
            "fail. Hiding the UAT status behind a sysadmin gate "
            "forced them to ask Michael for screenshots every time "
            "they wanted to check progress — a coordination tax that "
            "was slowing the team down with eight days left before "
            "the midpoint submission. Surfacing the read view across "
            "the team removes that tax without giving anyone the "
            "ability to change a row by accident."),
    ))


def downgrade() -> None:
    # Reverse the back-fill — strip view_uat_status from every row.
    # array_remove is idempotent (no-op if the value is absent).
    op.execute(sa.text("""
        UPDATE platform_users
        SET permissions = array_remove(permissions, 'view_uat_status')
        WHERE 'view_uat_status' = ANY(permissions)
    """))
    # Drop the changelog row so a re-upgrade does not collide on (version).
    op.execute(sa.text("DELETE FROM changelog WHERE version = 61"))
