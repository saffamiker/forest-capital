"""Create triage_reports — the automated feedback triage backlog.

The triage engine monitors the test_feedback / test_results backlog and,
on a threshold or test-pass trigger (or a manual sysadmin run), produces
a structured triage report. Each run is stored as one row here.

Triage is fail-open: a run that completes some but not all of its seven
steps still stores a row, with `status` recording how far it got
(complete / partial / failed).

Revision ID: 016
Revises: 015
Create Date: 2026-05-17
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "016"
down_revision: str | None = "015"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "triage_reports",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("triggered_by", sa.String(20), nullable=False,
                  comment="threshold | test_pass | manual"),
        sa.Column("triggered_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("items_assessed", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("report_text", sa.Text(), nullable=False,
                  server_default=""),
        sa.Column("github_issues_created", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="complete",
                  comment="complete | partial | failed"),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_triage_reports_triggered_at",
                    "triage_reports", ["triggered_at"])

    # Changelog contract — every migration inserts at least one row.
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
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
        "version": 35,
        "released_at": now,
        "title": "Automated Feedback Triage",
        "description": (
            "The platform now triages its own UAT backlog — when feedback "
            "and failure reports accumulate, or a tester completes a test "
            "pass, an AI QA lead produces a structured triage report and "
            "opens GitHub issues for the urgent items."
        ),
        "academic_rationale": (
            "Automated triage turns raw tester feedback into a prioritised "
            "action plan without manual extraction -- it keeps the team "
            "focused on what must be fixed before the June 3 midpoint and "
            "demonstrates a closed-loop quality process to faculty."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 35")
    op.drop_table("triage_reports")
