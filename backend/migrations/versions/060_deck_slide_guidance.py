"""deck_slide_guidance -- per-user slide guidance overrides for the
presentation deck. Lets Molly upload a JSON file that overrides
per-slide title / so_what / max_bullets / bullet_guidance /
speaker_note_directive without code changes; the deck generation
pipeline merges per-slide overrides on top of the hardcoded defaults
in SLIDE_SPECIFICATIONS at generation time.

Schema:
  id            SERIAL PRIMARY KEY
  owner_email   TEXT NOT NULL   -- session.email of the uploader
  guidance_json JSONB NOT NULL  -- the full validated payload
  uploaded_at   TIMESTAMPTZ DEFAULT now()
  is_active     BOOLEAN DEFAULT true

Only one active row per (owner_email) at any time. The upload
endpoint deactivates any prior active row in the same transaction
before inserting the new one -- enforced procedurally rather than
via a partial UNIQUE INDEX to keep the migration portable.

Indexed on (owner_email, is_active) for the per-user active-row
read in the deck generation pipeline.

Revision ID: 060
Revises: 059
Create Date: 2026-06-22
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "060"
down_revision: str = "059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deck_slide_guidance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_email", sa.Text(), nullable=False),
        sa.Column(
            "guidance_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()")),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true")),
    )
    op.create_index(
        "deck_slide_guidance_owner_active",
        "deck_slide_guidance",
        ["owner_email", "is_active"],
    )


def downgrade() -> None:
    op.drop_index(
        "deck_slide_guidance_owner_active",
        table_name="deck_slide_guidance")
    op.drop_table("deck_slide_guidance")
