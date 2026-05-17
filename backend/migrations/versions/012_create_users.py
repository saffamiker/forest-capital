"""Create the users table for per-user changelog / tour state.

The project had no users table — users are identified by email, with
sessions held in memory. The What's New modal needs a PERSISTENT
per-user record of what each user has already seen, so this migration
creates a minimal users table keyed by email:

  email                   the identity key (also the PK)
  last_changelog_seen_at  when the user last dismissed What's New
  last_tour_version_seen  the site-tour version the user last completed

The changelog and tour endpoints UPSERT into this table, so no rows
need pre-seeding — a first-time user simply has no row, which correctly
reads as "has seen nothing".

Per the changelog contract this migration also inserts one changelog
row (version 31) — the CI/CD pipeline and changelog gate that landed
in the same build.

Revision ID: 012
Revises: 011
Create Date: 2026-05-17
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision: str = "012"
down_revision: str | None = "011"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("email", sa.String(255), primary_key=True,
                  comment="Platform login email — the project-wide identity key"),
        sa.Column(
            "last_changelog_seen_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="When the user last dismissed the What's New modal; "
                    "NULL means they have seen no changelog entries",
        ),
        sa.Column(
            "last_tour_version_seen",
            sa.Integer(),
            nullable=True,
            server_default="0",
            comment="The site-tour version the user last completed",
        ),
    )

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
        "version": 31,
        "released_at": datetime(2026, 5, 17, tzinfo=timezone.utc),
        "title": "Continuous integration and the changelog gate",
        "description": (
            "A GitHub Actions pipeline runs the database migrations and "
            "the full test suite on every push, and a changelog gate "
            "fails any migration that ships without a changelog entry."
        ),
        "academic_rationale": (
            "Automated checks on every push mean a broken migration or a "
            "failing test is caught before it reaches a deadline. The "
            "changelog gate guarantees the team's record of capabilities "
            "stays complete — the evidence base for the AI-use narrative."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.drop_table("users")
    op.execute("DELETE FROM changelog WHERE version = 31")
