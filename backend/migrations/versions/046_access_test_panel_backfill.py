"""access_test_panel — back-fill the missing permission onto every
active team_member and sysadmin platform_users row.

May 26 2026 — urgent fix.

WHY:
The `access_test_panel` permission was added to config.ROLE_PRESETS
on May 24 2026 (ID 275 follow-up). It's the gate that opens the
Settings → Test Administration section (Failure Reports / Feedback
Backlog / Issue Tracker tabs + the sysadmin-only Triage Reports
block). That same May-24 commit added `view_uat_status` and shipped
migration 042 to back-fill it; `access_test_panel` was REGISTERED in
config.PERMISSIONS and added to the team_member + sysadmin presets,
but a corresponding back-fill migration was never written.

The result: every platform_users row seeded by migration 015
(May 17 2026) — including Michael (sysadmin), Bob (team_member) and
Molly (team_member) — has a frozen `permissions` array that does NOT
include `access_test_panel`. resolve_user reads the array verbatim
(no derivation from role preset), so even a sign-out / sign-back-in
cycle delivers a JWT that still lacks the permission. The frontend's
`useCanAccessTestPanel()` returns false, and Settings.tsx hides the
Test Administration section.

USER IMPACT (May 26 2026):
Bob and Molly are running UAT but can't see the live Failure Reports
and Feedback Backlog they need to hand off to engineering. Michael
cannot see them either. This is blocking the UAT workflow.

WHAT THIS MIGRATION ADDS:
For every active team_member and sysadmin row, append
'access_test_panel' if not already present. The WHERE clause makes
the back-fill idempotent — re-running the migration is a no-op on a
row that already carries the permission (e.g. a newer row minted
after May 24 from the updated preset).

A "Custom" permission set (a sysadmin manually edited the array away
from a preset) is left alone — the migration only ADDS the missing
permission to rows that don't carry it, never strips or replaces.

VIEWER rows are NOT updated — viewer is intentionally narrow
(view_analytics + ask_council). A guest never needs Test
Administration.

Mirrors migration 042's pattern exactly so a future reader sees the
two back-fills as a pair of sibling fixes for the same May-24 omission.

Downgrade removes 'access_test_panel' from every row that has it, so
a rollback uniformly reverses the back-fill. Note that a newer row
minted after this migration runs will already carry the permission
from its preset — the downgrade strips it from EVERY row regardless
of insert date, which is the conservative choice for a rollback.

Revision ID: 046
Revises: 045
Create Date: 2026-05-26
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "046"
down_revision: str | None = "045"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # PostgreSQL array_append (idempotent — the WHERE clause excludes
    # rows that already carry the entry, so the migration is a no-op
    # on re-run). Role gate restricts the back-fill to team_member and
    # sysadmin; viewer rows stay narrow.
    op.execute(sa.text("""
        UPDATE platform_users
        SET permissions = array_append(permissions, 'access_test_panel')
        WHERE is_active = true
          AND role IN ('team_member', 'sysadmin')
          AND NOT ('access_test_panel' = ANY(permissions))
    """))

    # ── Changelog ──────────────────────────────────────────────────────────
    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL)"
    ).bindparams(
        v=65,
        rel=datetime.now(timezone.utc),
        t="Test Administration panel — back-fill access_test_panel permission",
        d=(
            "Every existing team_member and sysadmin row now carries "
            "the access_test_panel permission. Previously, rows seeded "
            "by migration 015 (May 17 2026) had a frozen permissions "
            "array missing 'access_test_panel' — added to the role "
            "presets on May 24 2026 but never back-filled. The result "
            "was that Michael, Bob and Molly all saw the Settings "
            "page WITHOUT the Test Administration section (Failure "
            "Reports, Feedback Backlog, Issue Tracker), even though "
            "their role assignment said they should. This migration "
            "is the missing sibling to migration 042 — same pattern, "
            "different permission. Idempotent: re-running is a no-op "
            "on a row that already carries the permission, so newer "
            "rows minted after the May-24 preset update are unaffected. "
            "Viewer rows are deliberately untouched — viewers do not "
            "see the Test Administration panel."),
        a=(
            "Bob and Molly use the live Failure Reports and Feedback "
            "Backlog to hand off testing-surfaced issues to Michael "
            "for engineering work. Hiding the panel from them blocked "
            "the UAT loop in the final week before the midpoint paper "
            "submission, forcing back-channel coordination and lost "
            "time. Restoring the panel for every team member closes "
            "that gap — testing-surfaced issues now have a single "
            "shared queue all three can see and prioritise."),
    ))


def downgrade() -> None:
    # Reverse the back-fill — strip 'access_test_panel' from every row
    # that has it. array_remove is idempotent (no-op if the value is
    # absent). Note: this is broader than just the rows the upgrade
    # touched — a newer row minted from an updated preset will also
    # have the permission stripped on rollback. Acceptable for a
    # downgrade: the post-rollback state is "no row carries the
    # permission", matching the pre-this-migration state of the
    # config code.
    op.execute(sa.text("""
        UPDATE platform_users
        SET permissions = array_remove(permissions, 'access_test_panel')
        WHERE 'access_test_panel' = ANY(permissions)
    """))
    # Drop the changelog row so a re-upgrade does not collide on (version).
    op.execute(sa.text("DELETE FROM changelog WHERE version = 65"))
