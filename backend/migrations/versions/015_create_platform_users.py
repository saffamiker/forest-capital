"""Create platform_users — database-managed access control.

Access control moves off the hardcoded config allowlists onto a table
the sysadmin (Michael) manages from inside the platform. Each row has a
`role` preset (sysadmin / team_member / viewer) for display and a
`permissions` text array that is the authoritative capability set —
roles are presets over the permissions, not constraints.

This table is separate from the migration-012 `users` table, which
holds per-user changelog / tour state — a different concern.

ALLOWED_EMAILS and PROJECT_TEAM_EMAILS remain in config.py as the
emergency fallback (the auth layer fails open against them when this
table is unreachable) — they are intentionally NOT removed.

Seeded from the current config allowlists: Michael as sysadmin, Molly
and Bob as team_member, and every other ALLOWED_EMAILS address (e.g.
Dr. Panttser) as viewer.

Revision ID: 015
Revises: 014
Create Date: 2026-05-17
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "015"
down_revision: str | None = "014"
branch_labels: str | None = None
depends_on: str | None = None

# Permission presets — kept in lockstep with config.ROLE_PRESETS. Inlined
# here so the migration does not depend on the app config at upgrade time.
_VIEWER = ["view_analytics", "ask_council"]
_TEAM = _VIEWER + ["team_member", "generate_documents", "export_package"]
_SYSADMIN = _TEAM + ["manage_users", "view_admin"]


def upgrade() -> None:
    op.create_table(
        "platform_users",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("role", sa.String(20), nullable=False, server_default="viewer",
                  comment="sysadmin | team_member | viewer — the preset label"),
        sa.Column("permissions", postgresql.ARRAY(sa.Text()), nullable=False,
                  server_default=sa.text("'{}'"),
                  comment="Authoritative capability set; roles are presets over it"),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=True,
                  comment="Email of the sysadmin who added this user"),
        sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index("ix_platform_users_email", "platform_users", ["email"])

    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    users = sa.table(
        "platform_users",
        sa.column("email", sa.String),
        sa.column("display_name", sa.String),
        sa.column("role", sa.String),
        sa.column("permissions", postgresql.ARRAY(sa.Text())),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.TIMESTAMP(timezone=True)),
        sa.column("created_by", sa.String),
    )
    op.bulk_insert(users, [
        {"email": "ruurdsm@queens.edu", "display_name": "Michael Ruurds",
         "role": "sysadmin", "permissions": _SYSADMIN, "is_active": True,
         "created_at": now, "created_by": "system"},
        {"email": "murdockm@queens.edu", "display_name": "Molly Murdock",
         "role": "team_member", "permissions": _TEAM, "is_active": True,
         "created_at": now, "created_by": "system"},
        {"email": "thaob@queens.edu", "display_name": "Bob Thao",
         "role": "team_member", "permissions": _TEAM, "is_active": True,
         "created_at": now, "created_by": "system"},
        # Every other ALLOWED_EMAILS address seeds as a viewer.
        {"email": "panttserk@queens.edu", "display_name": "Dr. Panttser",
         "role": "viewer", "permissions": _VIEWER, "is_active": True,
         "created_at": now, "created_by": "system"},
    ])

    # Changelog contract — every migration inserts at least one row.
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
        "version": 34,
        "released_at": now,
        "title": "Platform User Management",
        "description": (
            "Access control is now database-managed — the sysadmin adds, "
            "edits and deactivates users, with per-user permissions, from "
            "Settings → Users."
        ),
        "academic_rationale": (
            "Sysadmin-managed access control removes hardcoded config "
            "dependencies and gives Michael full control over who can "
            "access the platform -- appropriate for a production-ready "
            "system being shown to Forest Capital and faculty."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 34")
    op.drop_table("platform_users")
