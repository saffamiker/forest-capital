"""Add council query allocation to platform_users — viewer interaction limit.

Viewers receive a lifetime allocation of council queries: council_queries_limit
(default 5) is the cap, council_queries_used tracks total queries since the
account was created. There is no daily reset — once a viewer reaches the cap
they are blocked until a sysadmin adjusts the limit or resets the count from
Settings → Users. Team members and sysadmins are unlimited
(council_queries_limit NULL).

Revision ID: 019
Revises: 018
Create Date: 2026-05-18
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision: str = "019"
down_revision: str | None = "018"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("platform_users", sa.Column(
        "council_queries_used", sa.Integer(), nullable=False,
        server_default="0",
        comment="lifetime count of council queries used"))
    op.add_column("platform_users", sa.Column(
        "council_queries_limit", sa.Integer(), nullable=True,
        server_default="5",
        comment="lifetime council query allowance; NULL = unlimited"))
    # Team members and sysadmins are unlimited — clear the seeded default.
    op.execute(
        "UPDATE platform_users SET council_queries_limit = NULL "
        "WHERE role IN ('team_member', 'sysadmin')")

    # Changelog contract — every migration inserts at least one row.
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    changelog = sa.table(
        "changelog",
        sa.column("version", sa.Integer),
        sa.column("released_at", sa.TIMESTAMP(timezone=True)),
        sa.column("title", sa.String),
        sa.column("description", sa.Text),
        sa.column("academic_rationale", sa.Text),
        sa.column("tour_step_id", sa.String),
    )
    op.bulk_insert(changelog, [{
        "version": 38,
        "released_at": now,
        "title": "Viewer Council Query Allocation",
        "description": (
            "Viewer accounts now have a lifetime allocation of AI Council "
            "queries (5 by default). Team members and sysadmins remain "
            "unlimited. A sysadmin can adjust the limit, reset the count, "
            "or grant unlimited access per user from Settings → Users."
        ),
        "academic_rationale": (
            "The platform can now be shared with guest reviewers and "
            "faculty without exposing an unbounded, billable AI surface — "
            "each guest gets a fixed, auditable council allowance, while "
            "the project team keeps unrestricted access for the analysis "
            "and the deliverables."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 38")
    op.drop_column("platform_users", "council_queries_limit")
    op.drop_column("platform_users", "council_queries_used")
