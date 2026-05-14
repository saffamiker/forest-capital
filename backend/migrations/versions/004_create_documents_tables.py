"""Create documents, document_versions, document_drafts for Sprint 6.

Storyboard editor + section editor write here. The split mirrors the
version-control model from CLAUDE.md Section 14:
  documents          — one row per logical document (storyboard, brief, etc.)
  document_versions  — immutable named snapshots; the rollback target
  document_drafts    — mutable working copy, auto-saved every 30 seconds

Sprint 6 ships only the schema this migration. The endpoints that
populate the tables (POST /api/documents/storyboard/draft, etc.) land
in a follow-up commit. Shipping the migration first lets the operator
run `alembic upgrade head` once and have the DB ready when the
endpoint code arrives — no second downtime window.

Revision ID: 004
Revises: 003
Create Date: 2026-05-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── documents ────────────────────────────────────────────────────────
    # One row per logical document. doc_type determines which generator
    # produced it and which editor renders the content. owner_email is
    # the team member responsible (Bob for written deliverables, Molly
    # for storyboard / deck / Q&A).
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "doc_type",
            sa.String(32),
            nullable=False,
            comment="storyboard | analytical_appendix | executive_brief | midpoint_paper | qa_preparation",
        ),
        sa.Column("owner_email", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "strategy_hash",
            sa.String(64),
            nullable=True,
            comment="Strategy results hash at first generation — flags drift",
        ),
        sa.Column(
            "is_finalised",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="True after the user marks the document ready for submission",
        ),
    )
    op.create_index("ix_documents_owner_email", "documents", ["owner_email"])
    op.create_index("ix_documents_doc_type", "documents", ["doc_type"])

    # ── document_versions ────────────────────────────────────────────────
    # Immutable named snapshots. The audit trail the Admin screen renders.
    # restored_from references the version the user rolled back from; null
    # when the entry is a forward edit. We APPEND on every save and never
    # UPDATE, so the table grows linearly with edit count — acceptable
    # because typical document sees <50 versions across its lifetime.
    op.create_table(
        "document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "version_name",
            sa.String(120),
            nullable=True,
            comment="User-supplied label, e.g. 'After team review'. Null for auto-saves.",
        ),
        sa.Column(
            "content",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Full immutable snapshot — slides[] for storyboard, sections[] for documents",
        ),
        sa.Column(
            "change_summary",
            sa.String(500),
            nullable=True,
            comment="Auto-generated diff summary — '3 headlines edited, slide 8 moved to position 6'",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.String(255),
            nullable=False,
            comment="Email of the team member who saved this version",
        ),
        sa.Column("strategy_hash", sa.String(64), nullable=True),
        sa.Column(
            "is_auto_save",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "restored_from",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id"),
            nullable=True,
            comment="When set, this version was created by restoring the referenced one",
        ),
    )
    op.create_index(
        "ix_document_versions_document_created",
        "document_versions",
        ["document_id", "created_at"],
    )
    # Composite unique key so the UI's "version 3 of storyboard X" lookup
    # is fast and the version_number sequence is enforced per document.
    op.create_index(
        "ix_document_versions_doc_num",
        "document_versions",
        ["document_id", "version_number"],
        unique=True,
    )

    # ── document_drafts ──────────────────────────────────────────────────
    # The mutable working copy. One row per document — auto-save UPDATEs
    # this in place rather than appending. The version history lives in
    # document_versions; a manual "Save Version" copies current draft
    # content into a new versions row and clears nothing.
    op.create_table(
        "document_drafts",
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "content",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "last_saved_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "based_on_version",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id"),
            nullable=True,
            comment="The version the draft was forked from — null for brand-new drafts",
        ),
    )


def downgrade() -> None:
    # FK order matters: drop dependents first.
    op.drop_table("document_drafts")
    op.drop_table("document_versions")
    op.drop_table("documents")
