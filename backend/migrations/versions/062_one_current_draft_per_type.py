"""editor_drafts -- enforce one is_current=true row per document_type.

June 24 2026. Production hit a state where draft 44 (ruurdsm@queens.edu)
and draft 50 (thaob@queens.edu) both carried is_current=true for
executive_brief, because two team members independently generated the
brief and the application-layer 'set is_current=false on existing rows'
UPDATE was owner-scoped (WHERE owner_email = :e AND ...). Documents are
team-shared (any team member can open any draft) so the application
intent is one current draft per document_type ACROSS THE TEAM, not per
(owner_email, document_type).

This migration:

  1. De-duplicates any existing is_current=true rows -- keeps the
     newest per document_type, flips the rest to is_current=false.
     Safe because is_current is a UI hint (which version the editor
     opens by default); the underlying drafts stay accessible.

  2. Adds a unique partial index editor_drafts_one_current_per_type
     so the duplicate state cannot recur even if a future code path
     forgets to clear is_current first. The partial WHERE clause
     scopes the constraint to active current drafts only -- it does
     not block soft-deleted rows or historical (is_current=false)
     rows from sharing the same document_type.

The application-layer UPDATE in tools/editor_drafts.create_draft was
also widened (in the same PR) to remove the owner_email filter from
the 'clear is_current first' step. The index here is defence in
depth -- the DB rejects any second is_current row before it lands.

Revision ID: 062
Revises: 061
Create Date: 2026-06-24
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "062"
down_revision: str = "061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. De-duplicate existing is_current=true rows. Keep the most
    #    recently updated row per document_type as the canonical
    #    current draft; flip the rest to is_current=false. The
    #    losers stay queryable by id -- the UI just won't open them
    #    as the default for that doc type.
    op.execute(sa.text(
        "UPDATE editor_drafts "
        "SET is_current = false "
        "WHERE id IN ("
        "  SELECT id FROM ("
        "    SELECT id, "
        "      ROW_NUMBER() OVER ("
        "        PARTITION BY document_type "
        "        ORDER BY updated_at DESC, id DESC"
        "      ) AS rn "
        "    FROM editor_drafts "
        "    WHERE is_current = true "
        "      AND is_deleted = false"
        "  ) ranked "
        "  WHERE ranked.rn > 1"
        ")"
    ))

    # 2. Unique partial index -- guards against the duplicate state
    #    recurring. IF NOT EXISTS so the migration is idempotent
    #    against partial prior application.
    op.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "editor_drafts_one_current_per_type "
        "ON editor_drafts (document_type) "
        "WHERE is_current = true AND is_deleted = false"
    ))

    # Changelog -- idempotent INSERT (matches the pattern from
    # migration 061's fix for production recoverability).
    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, NOW(), :t, :d, :a, NULL) "
        "ON CONFLICT (version) DO NOTHING"
    ).bindparams(
        v=62,
        t="One current draft per document type",
        d=(
            "Adds a unique partial index on editor_drafts "
            "(document_type) WHERE is_current = true AND "
            "is_deleted = false, and de-duplicates any existing "
            "rows that violate the new invariant. Closes the "
            "production gap where two team members generating the "
            "same document_type both ended up with is_current=true "
            "rows, leaving the editor's 'Open in Editor' button "
            "showing the wrong version for one of them."),
        a=(
            "Documents are team-shared. One current draft per "
            "document_type is the contract the application has "
            "always intended; the DB now enforces it."),
    ))


def downgrade() -> None:
    op.execute(sa.text(
        "DROP INDEX IF EXISTS "
        "editor_drafts_one_current_per_type"
    ))
    # Downgrade does NOT undo the de-duplication -- there's no
    # well-defined inverse of "kept the newest". The dropped index
    # alone restores the pre-migration state of allowing multiple
    # is_current=true rows; any consumer that relied on the
    # specific previous duplicate set was already in an
    # inconsistent state.
