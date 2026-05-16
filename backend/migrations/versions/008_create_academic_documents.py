"""Create academic_documents for the agent-context upload feature.

Uploaded PDFs and plain-text files (the midpoint rubric, the final
presentation requirements, and any other reference material) have their
text extracted server-side and stored here. Every AI agent injects the
full text of every row as system context on each invocation, so the
council, advisors, writers and QA agents are always aware of the
academic evaluation criteria when they produce analysis.

Only the extracted text is persisted — never the raw binary. The table
therefore stays small and is cheap to load in full on every agent call.

Revision ID: 008
Revises: 007
Create Date: 2026-05-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "academic_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "name",
            sa.String(255),
            nullable=False,
            comment="Original uploaded filename — shown in the document list",
        ),
        sa.Column(
            "document_type",
            sa.String(40),
            nullable=False,
            comment="midpoint_requirements | final_presentation_requirements | other",
        ),
        sa.Column(
            "content_text",
            sa.Text(),
            nullable=False,
            comment="Server-side-extracted plain text — the only persisted form; "
                    "raw PDF/binary is never stored",
        ),
        sa.Column(
            "uploaded_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_academic_documents_uploaded_at",
        "academic_documents",
        ["uploaded_at"],
    )


def downgrade() -> None:
    op.drop_table("academic_documents")
