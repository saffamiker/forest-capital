"""Failure-resolution metadata — resolution_type + fix_reference + remediation_note.

The original failure resolution flow (migration 014) stored a single
resolution_note text field. UX experience surfaced two problems with it:

  1. No structured distinction between "no bug detected" / "code fix
     deployed" / "won't fix" outcomes — admins were writing the
     resolution type in free-form text, which the Issue Tracker view
     couldn't filter or report on reliably.
  2. No commit-reference gate on a "code fix deployed" claim — a row
     could be marked resolved with nothing more than "fixed it" in
     the note. The tester would then re-attest a step that wasn't
     actually fixed yet.

This migration adds the three columns the resolution-modal flow needs:

  resolution_type   — "no_bug_detected" | "code_fix_deployed" | "wont_fix".
                      Nullable so rows resolved before this migration
                      (legacy resolution_note-only rows) continue to
                      render with an Unknown type badge.
  fix_reference     — commit SHA / PR number / GitHub URL. Required at
                      the API layer when resolution_type =
                      'code_fix_deployed'; nullable at the DB layer so
                      the other resolution types do not carry a stray
                      empty string.
  remediation_note  — "What was changed and how does it address the
                      failure?" Required at the API layer for
                      code_fix_deployed; same nullable rationale.

The existing resolution_note column is repurposed as the "root cause"
text — the field is universally required across all three resolution
types (every resolution names what caused the failure). Existing rows
keep their resolution_note value as-is; the legacy meaning ("how was
it resolved") and the new meaning ("what caused it") are close enough
that no data migration is needed.

CHECK constraint on resolution_type ensures any future DB-level write
that bypasses the API validator can't produce an unknown enum value.

Revision ID: 025
Revises: 024
Create Date: 2026-05-21
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "025"
down_revision: str | None = "024"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("test_results",
                  sa.Column("resolution_type", sa.String(40), nullable=True))
    op.add_column("test_results",
                  sa.Column("fix_reference", sa.String(500), nullable=True))
    op.add_column("test_results",
                  sa.Column("remediation_note", sa.Text(), nullable=True))

    # Enum-style guard. Any direct UPDATE that bypasses the API
    # validator hits this constraint instead of producing an Unknown
    # value the frontend cannot render.
    op.create_check_constraint(
        "ck_test_results_resolution_type",
        "test_results",
        "resolution_type IS NULL OR resolution_type IN "
        "('no_bug_detected', 'code_fix_deployed', 'wont_fix')",
    )

    # ── Changelog — every migration must seed at least one entry ──────────────
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
        "version": 44,
        "released_at": datetime(2026, 5, 21, tzinfo=timezone.utc),
        "title": "Failure Resolution Gate",
        "description": (
            "The Mark Resolved button on Failure Reports is replaced "
            "by a resolution modal capturing the resolution type "
            "(No bug detected / Code fix deployed / Won't fix), the "
            "root cause, and — when claiming a code fix — a commit "
            "or PR reference plus a remediation note. Submit stays "
            "disabled until the required fields are populated so a "
            "code-fix claim cannot be recorded without a commit "
            "reference. Won't fix resolutions close the item without "
            "resetting the step or prompting a re-test."
        ),
        "academic_rationale": (
            "Closing the loop on a reported failure is a quality "
            "signal the FNA 670 panel will look for: the resolution "
            "metadata produces a defensible audit trail showing what "
            "was found, what caused it, what was changed, and who "
            "verified the fix. The commit-reference gate prevents "
            "a code-fix claim from being recorded without traceable "
            "evidence — directly supporting the Analytical Appendix's "
            "process and reproducibility narrative."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 44")
    op.drop_constraint("ck_test_results_resolution_type",
                       "test_results", type_="check")
    op.drop_column("test_results", "remediation_note")
    op.drop_column("test_results", "fix_reference")
    op.drop_column("test_results", "resolution_type")
