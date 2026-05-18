"""Create editor_drafts and editor_draft_versions — the document editor.

The in-platform document editor lets Bob refine the midpoint paper and
Molly the presentation deck without leaving the platform. editor_drafts
is the mutable working copy (auto-saved every 30 seconds); a row in
editor_draft_versions is an immutable named checkpoint — the restore
target.

Naming: migration 004 already created tables literally named
document_drafts and document_versions for the Sprint 6 storyboard
editor — a different domain. To avoid the collision, the document
editor's tables are namespaced editor_drafts / editor_draft_versions
and coexist with the 004 tables.

Revision ID: 021
Revises: 020
Create Date: 2026-05-18
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "021"
down_revision: str | None = "020"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "editor_drafts",
        sa.Column("id", sa.BigInteger(), primary_key=True,
                  autoincrement=True),
        sa.Column("document_type", sa.String(30), nullable=False,
                  comment="midpoint_paper | executive_brief | "
                          "presentation_deck"),
        sa.Column("owner_email", sa.String(255), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        # content_json — TipTap document JSON for paper/brief drafts,
        # {slides:[...]} for a presentation_deck draft.
        sa.Column("content_json", postgresql.JSONB(), nullable=True),
        # content_text — plain text, what the AI / Academic Review reads.
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False,
                  server_default="1"),
        sa.Column("is_current", sa.Boolean(), nullable=False,
                  server_default=sa.true(),
                  comment="the active draft for this owner+document_type"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False,
                  server_default=sa.false(),
                  comment="soft delete — DELETE clears is_current too"),
        sa.Column("created_from", sa.String(20), nullable=False,
                  server_default="manual",
                  comment="generated | uploaded | manual"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_editor_drafts_owner_email",
                    "editor_drafts", ["owner_email"])
    op.create_index("ix_editor_drafts_document_type",
                    "editor_drafts", ["document_type"])

    op.create_table(
        "editor_draft_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True,
                  autoincrement=True),
        sa.Column("draft_id", sa.BigInteger(),
                  sa.ForeignKey("editor_drafts.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_json", postgresql.JSONB(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("version_label", sa.String(200), nullable=True,
                  comment="user label, e.g. 'After first AI review'"),
        sa.Column("saved_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("saved_by", sa.String(255), nullable=True),
    )
    op.create_index("ix_editor_draft_versions_draft_id",
                    "editor_draft_versions", ["draft_id"])

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
        "version": 40,
        "released_at": now,
        "title": "In-Platform Document Editor",
        "description": (
            "A generated midpoint paper or presentation deck now opens "
            "in an in-platform editor — a rich-text or slide editor with "
            "auto-save, named version history, inline verification "
            "markers, and a writing assistant. Academic Review runs "
            "against the live editor content."
        ),
        "academic_rationale": (
            "Drafting the graded deliverables inside the platform keeps "
            "every edit, resolved marker and version save in the "
            "documented contribution record, and lets the team run "
            "Academic Review against the working draft instead of a "
            "stale uploaded file."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 40")
    op.drop_index("ix_editor_draft_versions_draft_id",
                  table_name="editor_draft_versions")
    op.drop_table("editor_draft_versions")
    op.drop_index("ix_editor_drafts_document_type",
                  table_name="editor_drafts")
    op.drop_index("ix_editor_drafts_owner_email",
                  table_name="editor_drafts")
    op.drop_table("editor_drafts")
