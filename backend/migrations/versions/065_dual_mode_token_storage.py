"""editor_drafts.migration_run + pre_migration_content_json -- dual-mode token storage.

June 28 2026. PR-DM-Lite. Enables dual-mode token storage in
editor_drafts.content_json: every numeric token resolved at
generation time can be upgraded from a plain text string ("0.63")
to a structured token_value node carrying both the resolved value
AND the source token reference + cache hash + timestamp.

Two new columns:

  migration_run  BOOLEAN DEFAULT FALSE NOT NULL
    Idempotency guard for the upgrade pass. Once a draft has been
    walked by upgrade_content_json_to_token_values + its plain-text
    numeric values have been converted to token_value nodes, this
    flag flips to TRUE so subsequent triggers no-op.

  pre_migration_content_json  JSONB NULL
    Snapshot of content_json BEFORE the upgrade pass mutates it.
    Reversion path: POST /api/v1/admin/revert-draft-migration/{id}
    restores content_json from this column. Nullable -- only
    populated when the upgrade pass actually fires.

Both columns are nullable / defaulted so legacy rows continue to
function. The upgrade pass is gated by migration_run = FALSE AND
value_manifest IS NOT NULL (drafts pre-Layer-3 with no manifest
cannot be upgraded; they remain plain text + flagged in the
review UI for the operator).

Revision ID: 065
Revises: 064
Create Date: 2026-06-28
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "065"
down_revision: str | None = "064"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # migration_run -- idempotency guard. DEFAULT FALSE so every
    # existing row is treated as not-yet-migrated until the
    # upgrade pass + admin endpoint flip it.
    op.execute(sa.text(
        "ALTER TABLE editor_drafts "
        "ADD COLUMN IF NOT EXISTS migration_run "
        "BOOLEAN NOT NULL DEFAULT FALSE"))

    # pre_migration_content_json -- snapshot for reversion. NULL
    # until the upgrade pass fires; populated as a one-shot
    # before content_json is mutated.
    op.execute(sa.text(
        "ALTER TABLE editor_drafts "
        "ADD COLUMN IF NOT EXISTS pre_migration_content_json "
        "JSONB"))

    # Changelog -- idempotent (matches migrations 061/062/063
    # pattern). Records the schema change + the architectural
    # intent so a post-deploy reader sees the freeze-integrity
    # connection.
    op.execute(sa.text(
        "INSERT INTO changelog (commit_sha, summary, applied_at) "
        "SELECT 'migration_065', "
        "  'editor_drafts.migration_run + pre_migration_content_json "
        "(dual-mode token storage -- PR-DM-Lite, June 28 2026)', "
        "  :ts "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM changelog WHERE commit_sha = 'migration_065')"
        ).bindparams(ts=datetime.now(timezone.utc)))


def downgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE editor_drafts "
        "DROP COLUMN IF EXISTS pre_migration_content_json"))
    op.execute(sa.text(
        "ALTER TABLE editor_drafts "
        "DROP COLUMN IF EXISTS migration_run"))
