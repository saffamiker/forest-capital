"""editor_numeric_overrides -- audit trail for direct-editor untoken numerics.

June 28 2026. Touchpoint 5 of the hard-lock numeric guardrail.

CONTEXT
  The hard-lock at generation time (PR #466 + PR #468) catches
  untoken-backed numerics in LLM-generated prose by looping the
  Sonnet writer with correction feedback. But after a draft
  lands in the editor, a team member can type a raw numeric
  directly into the content (replace "{{OOS_SHARPE_BLEND}}"
  with "0.86" by hand, add a "71.7%" prose claim, etc). These
  manual edits bypass the harness loop.

  The submission-integrity invariant says: no numeric reaches
  persisted content_json without a corresponding verified token,
  EXCEPT via explicit operator acknowledgment in the editor.

  This table is the audit trail for those operator
  acknowledgments -- every untoken-backed numeric the editor
  save endpoint detects gets one row here so the operator can
  later answer "where did this number come from?"

SCOPE
  Naming follows the qa_intentional_overrides pattern (PR #117
  migration 027). That table is QA-checklist-scoped + uniqueness-
  constrained on check_id, so it's the wrong fit for per-edit
  per-numeric logging. This table is the editor-save analog.

  One row per (draft_id, offending_value, save timestamp).
  Repeated saves of the same draft with the same offending value
  produce repeated rows -- the warning banner stays dismissible
  but the audit trail is permanent.

Revision ID: 066
Revises: 065
Create Date: 2026-06-28
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "066"
down_revision: str | None = "065"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "editor_numeric_overrides",
        sa.Column("id", sa.BigInteger(), primary_key=True,
                  autoincrement=True),
        sa.Column("draft_id", sa.BigInteger(), nullable=False,
                  comment="editor_drafts.id of the draft the "
                          "operator was editing when the "
                          "untoken-backed numeric was saved"),
        sa.Column("document_type", sa.String(40), nullable=True,
                  comment="executive_brief / analytical_appendix "
                          "/ etc -- redundant with editor_drafts "
                          "but persisted here so a draft delete "
                          "doesn't orphan the audit trail"),
        sa.Column("user_email", sa.String(255), nullable=False,
                  comment="session email of the team member who "
                          "saved the offending content"),
        sa.Column("offending_value", sa.String(64), nullable=False,
                  comment="the raw numeric string flagged by "
                          "find_untoken_backed_numerics (e.g. "
                          "'0.86', '+98%', '-29.7%')"),
        sa.Column("sentence_context",
                  postgresql.TEXT(),
                  nullable=True,
                  comment="200-char window surrounding the "
                          "offending numeric in the saved "
                          "content_text -- helps the operator "
                          "locate the edit during later review"),
        sa.Column("suggested_token", sa.String(120),
                  nullable=True,
                  comment="closest matching {{TOKEN}} from the "
                          "substitution table when the value "
                          "matches a known substitution output; "
                          "NULL when no matching token exists"),
        sa.Column("saved_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.func.now(),
                  comment="timestamp of the save call"),
    )

    op.create_index(
        "ix_editor_numeric_overrides_draft",
        "editor_numeric_overrides",
        ["draft_id", sa.text("saved_at DESC")])

    op.create_index(
        "ix_editor_numeric_overrides_saved_at",
        "editor_numeric_overrides",
        [sa.text("saved_at DESC")])

    # Changelog -- mirrors migration 065's pattern.
    op.execute(sa.text(
        "INSERT INTO changelog (commit_sha, summary, applied_at) "
        "SELECT 'migration_066', "
        "  'editor_numeric_overrides -- audit trail for "
        "untoken-backed numerics introduced via direct editor "
        "save (touchpoint 5 of hard-lock guardrail, June 28 2026)', "
        "  :ts "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM changelog WHERE commit_sha = 'migration_066')"
        ).bindparams(ts=datetime.now(timezone.utc)))


def downgrade() -> None:
    op.execute(sa.text(
        "DROP INDEX IF EXISTS ix_editor_numeric_overrides_saved_at"))
    op.execute(sa.text(
        "DROP INDEX IF EXISTS ix_editor_numeric_overrides_draft"))
    op.execute(sa.text(
        "DROP TABLE IF EXISTS editor_numeric_overrides"))
