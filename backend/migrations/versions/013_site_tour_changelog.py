"""Ship the site tour: a changelog entry and a TOUR_VERSION bump.

The guided site tour has no schema of its own — per-user tour state
already lives in the users table (012, last_tour_version_seen). This
migration only satisfies the changelog contract: it inserts the
changelog row announcing the tour (version 32) and is the release
that pairs with TOUR_VERSION = 2 in backend/config.py.

The row's tour_step_id is "welcome" — the id of the tour's first
step (frontend/src/constants/tourSteps.ts). That column is how a
changelog entry points at a specific step in the walkthrough.

Bumping TOUR_VERSION to 2 makes /api/v1/changelog/unseen report
has_tour_update = true for every user whose last_tour_version_seen is
below 2, which re-surfaces the What's New modal's "View updated site
tour" button and lets SiteTour auto-start once per login session.

Revision ID: 013
Revises: 012
Create Date: 2026-05-17
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision: str = "013"
down_revision: str | None = "012"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
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
        "version": 32,
        "released_at": datetime(2026, 5, 17, tzinfo=timezone.utc),
        "title": "Site Tour",
        "description": (
            "Guided walkthrough of the full platform, step by step."
        ),
        "academic_rationale": (
            "The tour connects every platform feature to a specific "
            "grading criterion and positions the analysis for Forest "
            "Capital. New team members can onboard in minutes and "
            "understand how each tool contributes to the project "
            "deliverables."
        ),
        "tour_step_id": "welcome",
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 32")
