"""commit_summaries — plain-English summaries of commits for Team Activity.

May 29 2026. The Team Activity report showed raw commit/PR messages in
developer jargon ("fix: asyncpg bind type for event_date"). This table
caches a plain-English, non-technical one-sentence summary per commit
SHA so a faculty reviewer reads "Fixed a data-storage error that was
preventing historical event analysis from saving" as the primary label,
with the muted technical message beneath. Each SHA is summarised once
by the Anthropic Haiku model and served from this cache thereafter.

Revision ID: 049
Revises: 048
Create Date: 2026-05-29
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "049"
down_revision: str | None = "048"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "commit_summaries",
        sa.Column("sha", sa.String(64), primary_key=True,
                  comment="The commit SHA the summary describes."),
        sa.Column("plain_summary", sa.Text(), nullable=False,
                  comment="One-sentence plain-English description for a "
                          "non-technical reviewer."),
        sa.Column("model", sa.String(64), nullable=True,
                  comment="The model that produced the summary."),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )

    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL)"
    ).bindparams(
        v=68,
        rel=datetime.now(timezone.utc),
        t="Plain-English Team Activity summaries + live release count",
        d=(
            "A new commit_summaries table caches a plain-English, "
            "non-technical one-sentence description per commit SHA, "
            "generated once by the Anthropic Haiku model. The Team "
            "Activity report now shows this readable summary as the "
            "primary label with the technical commit message muted "
            "beneath it, and the merged-release count is read live from "
            "the GitHub API rather than from the partially-synced local "
            "commit table."),
        a=(
            "The Roles and Division of Labor narrative and the AI-use "
            "discussion are read by non-technical faculty. Translating "
            "developer commit messages into plain English, and showing "
            "the true merged-release count, lets a reviewer see the scope "
            "and substance of the team's platform work without having to "
            "read git jargon."),
    ))


def downgrade() -> None:
    op.drop_table("commit_summaries")
    op.execute(sa.text("DELETE FROM changelog WHERE version = 68"))
