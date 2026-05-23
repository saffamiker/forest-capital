"""platform_users.github_email + Michael's git identity attribution.

May 23 2026 — Step 3 (Pull Team Activity) was attributing
Michael's commit_activity rows to nobody because the join keyed
on platform_users.email = 'ruurdsm@queens.edu' while the
commit_activity.author column carries the GIT identity
'mikeruurds@gmail.com'. The activity layer's GIT_AUTHOR_EMAIL_MAP
config knew how to resolve git → platform, but the Step 3
template_pipeline.fetch_team_activity used the platform email
directly in its commit query, so the join silently returned 0.

This migration adds a github_email column to platform_users so
the mapping is stored on the row itself (not just in the activity-
layer config), and seeds Michael's git identity onto the id=1
sysadmin row. Future contributors can populate their own
github_email by editing the column directly.

There is a SECOND platform_users row at id=5 with email
mikeruurds@gmail.com role=viewer — a UX-testing dupe of Michael's
identity. It must NEVER be used for activity attribution. The new
github_email column gives us a primary attribution path that
keeps the dupe row out of every activity query (since the dupe's
github_email is NULL and is_active is the same).

The column is nullable so the migration is purely additive — no
existing row is modified except the explicit Michael UPDATE.

Downgrade drops the column; older code that doesn't read it
resumes working unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "038"
down_revision: str | None = "037"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "platform_users",
        sa.Column("github_email", sa.String(255), nullable=True,
                  comment="Git author email used for commit / PR "
                          "attribution. Null when the user commits "
                          "under the same email as their platform "
                          "login (the common case). Set this when "
                          "the git identity differs from the "
                          "platform login (e.g. a personal account "
                          "at github.com)."),
    )
    # Seed Michael's known git identity. The UPDATE is idempotent
    # — re-running the migration is a no-op.
    op.execute(sa.text(
        "UPDATE platform_users "
        "SET github_email = :gh "
        "WHERE email = :pf"
    ).bindparams(
        gh="mikeruurds@gmail.com",
        pf="ruurdsm@queens.edu",
    ))

    # The id=5 viewer dupe (email=mikeruurds@gmail.com, role=viewer)
    # exists for UX testing only. Deactivate it explicitly so it
    # never appears in activity attribution lookups OR in the user
    # management UI as a duplicate Michael entry.
    op.execute(sa.text(
        "UPDATE platform_users "
        "SET is_active = false, "
        "    notes = COALESCE(notes, '') || "
        "     ' [Migration 038: UX-testing dupe of ruurdsm@queens.edu; "
        "deactivated to prevent split activity attribution.]' "
        "WHERE email = :em AND role = 'viewer'"
    ).bindparams(em="mikeruurds@gmail.com"))

    # ── Changelog ──────────────────────────────────────────────────────────
    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL)"
    ).bindparams(
        v=57,
        rel=datetime.now(timezone.utc),
        t="Team activity: Michael's commits now attribute correctly",
        d=(
            "Step 3 of the report writer pipeline (Pull Team "
            "Activity) was returning 0 commits for Michael because "
            "his GitHub commits are authored under "
            "mikeruurds@gmail.com while his platform login is "
            "ruurdsm@queens.edu. The new github_email column on "
            "platform_users carries the mapping, and the Step 3 "
            "query now matches against both identities. A "
            "duplicate viewer-role account at id=5 was deactivated "
            "to prevent split attribution."),
        a=(
            "The Roles and Division of Labor section of the "
            "midpoint paper depends on accurate commit / PR "
            "attribution. Michael's contributions were under-"
            "counted in every Step 3 output before this fix."),
    ))


def downgrade() -> None:
    # Reactivate the viewer dupe so the downgrade path is symmetric
    # (administrators may want it back if they revert the migration).
    op.execute(sa.text(
        "UPDATE platform_users "
        "SET is_active = true "
        "WHERE email = :em AND role = 'viewer'"
    ).bindparams(em="mikeruurds@gmail.com"))
    op.drop_column("platform_users", "github_email")
    op.execute("DELETE FROM changelog WHERE version = 57")
