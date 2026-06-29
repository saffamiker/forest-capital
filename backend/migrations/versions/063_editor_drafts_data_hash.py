"""editor_drafts.data_hash -- per-draft analytics data fingerprint.

June 25 2026. Multiple frontend + backend code paths (the tile
data-hash chip in DocumentGenerationPanel, the Light Refresh status
table, the editor's DataHashChip, the draft-list response) read
editor_drafts.data_hash but no migration ever added the column.
Result: every chip rendered 'No hash', the Light Refresh UPDATE
silently no-op'd (SQLAlchemy rejected the column on PostgreSQL),
and the data_hash stamp at generation time never persisted.

This migration:

  1. ALTERs editor_drafts to add data_hash VARCHAR(64) NULL. The
     IF NOT EXISTS guard makes the migration idempotent against
     deployments where someone may have applied the column by
     hand to unblock the chip rendering.

  2. CREATEs ix_editor_drafts_data_hash so the data-hash-based
     stale-detection queries (Light Refresh + tile rendering) do
     not scan the entire table.

  3. Backfills the existing current drafts with the canonical
     production hash 'f2e87dec7dcabe71' so the tile chips flip
     from gray 'No hash' to green 'Data current' as soon as the
     deploy lands. Backfill scoped to is_current=true AND
     is_deleted=false; historical drafts stay NULL so the chip
     correctly reports 'No hash' against drafts that pre-date
     this column.

  4. Changelog row -- idempotent INSERT (matches migrations 061
     + 062's pattern).

Application changes that ride this migration:

  tools/editor_drafts.create_draft now accepts a data_hash kwarg
  and includes it in the INSERT, so every freshly-generated draft
  is stamped with the strategy_hash at generation time. The
  generation endpoints + create_draft callers thread the live
  hash through. _DRAFT_COLS gained data_hash so the API response
  surfaces the value.

Revision ID: 063
Revises: 062
Create Date: 2026-06-25
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "063"
down_revision: str = "062"
branch_labels = None
depends_on = None


# Canonical production hash used by the backfill so freshly-deployed
# drafts surface 'Data current' instead of 'No hash' on first render.
# Sourced from the operator's confirmation that f2e87dec7dcabe71 is
# the analytics cache fingerprint at the time of this migration.
_PROD_HASH = "f2e87dec7dcabe71"


def upgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE editor_drafts "
        "ADD COLUMN IF NOT EXISTS data_hash VARCHAR(64)"
    ))

    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_editor_drafts_data_hash "
        "ON editor_drafts (data_hash)"
    ))

    # Backfill existing current drafts. Historical (is_current=false)
    # drafts stay NULL -- the chip's third state ('No hash --
    # regenerate recommended') is the correct signal for those.
    op.execute(sa.text(
        "UPDATE editor_drafts "
        "SET data_hash = :h "
        "WHERE is_current = true "
        "AND is_deleted = false "
        "AND data_hash IS NULL"
    ).bindparams(h=_PROD_HASH))

    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, NOW(), :t, :d, :a, NULL) "
        "ON CONFLICT (version) DO NOTHING"
    ).bindparams(
        v=63,
        t="Per-draft data hash on editor_drafts",
        d=(
            "Adds editor_drafts.data_hash + index. Closes the gap "
            "where the tile data-hash chip and Light Refresh "
            "status table referenced the column but no migration "
            "had added it -- chips rendered 'No hash' and the "
            "refresh UPDATE silently failed. Backfills existing "
            "current drafts with f2e87dec7dcabe71."),
        a=(
            "The data hash is the lightweight fingerprint of the "
            "analytics cache the draft was generated against. "
            "Per-draft hash stamping is what makes the Light "
            "Refresh stale-detection table work without re-running "
            "verify_export_against_cache on every page load."),
    ))


def downgrade() -> None:
    op.execute(sa.text(
        "DROP INDEX IF EXISTS ix_editor_drafts_data_hash"
    ))
    op.execute(sa.text(
        "ALTER TABLE editor_drafts DROP COLUMN IF EXISTS data_hash"
    ))
