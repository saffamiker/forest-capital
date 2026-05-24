"""Final Submission marker — mark a saved version as the FINAL one
that Defense Prep and Citation Adjudication should reference.

May 24 2026 (P5 — Final Submission marker + Defense Prep version pinning).

WHY:
Bob iterates through many draft versions in the Report Writer.
When he settles on the version he'll submit, he wants to mark it
explicitly so the downstream consumers (Defense Prep, Citation
Adjudication) reference the SAME version every time — not "the
most recent draft" which shifts every time he edits.

WHAT THIS MIGRATION ADDS:
- `report_paper_versions.is_final_submission` (BOOLEAN, default
   false). The frontend's "Mark as Final Submission" button sets
   this to true on one version; doing so first clears the flag
   on any other version of the same generation (only one version
   per generation can be the Final at any time).
- `report_paper_versions.final_submission_at` (TIMESTAMPTZ,
   nullable). Records when the version was marked Final, so the
   audit log can replay the sequence of marks if needed.

The endpoint logic (POST .../versions/{n}/mark-final, DELETE
.../versions/final-marker) and the downstream wiring live in the
backend route handlers; this migration only adds the columns.

A generation with no Final-marked version falls back to the most
recent saved version — the prior behavior — so existing flows
keep working unchanged.

Downgrade drops both columns; the older "most recent" behavior
resumes.
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "040"
down_revision: str | None = "039"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "report_paper_versions",
        sa.Column(
            "is_final_submission", sa.Boolean(),
            nullable=False, server_default=sa.text("false"),
            comment="True when this version is the Final Submission "
                    "marker. Only one version per generation may be "
                    "Final at a time. Defense Prep and Citation "
                    "Adjudication reference the Final-marked version "
                    "when one exists, falling back to the most recent "
                    "saved version when none is marked."),
    )
    op.add_column(
        "report_paper_versions",
        sa.Column(
            "final_submission_at", sa.DateTime(timezone=True),
            nullable=True,
            comment="When the version was marked as Final Submission. "
                    "Null when not marked. Updated to now() on mark "
                    "and cleared on unmark."),
    )

    # Partial index — only one row per generation can be the Final
    # marker at a time. Postgres-only (the project's only DB is
    # Postgres) so the partial index is the cleanest enforcement.
    op.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_report_paper_versions_final_per_generation "
        "ON report_paper_versions (generation_id) "
        "WHERE is_final_submission = true"
    ))

    # ── Changelog ──────────────────────────────────────────────────────────
    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL)"
    ).bindparams(
        v=59,
        rel=datetime.now(timezone.utc),
        t="Final Submission marker — pin one version as the submission",
        d=(
            "Every saved version in the Report Writer Version "
            "History panel now has a Mark as Final Submission "
            "button. Marking a version pins it as the canonical "
            "submission — Defense Prep and Citation Adjudication "
            "both reference the Final-marked version when one "
            "exists. The current draft is no longer the same as "
            "the submitted draft, so editing after the mark does "
            "not silently change what reviewers see. Unmarking is "
            "a one-click revert; the version itself stays in "
            "history."),
        a=(
            "Bob's workflow has him iterating on the paper while "
            "the cohort presentation walks reviewers through "
            "specific citations and findings. Without a Final "
            "marker, the version reviewers see can drift between "
            "Defense Prep generation and the cohort meeting. "
            "Pinning the Final version means Defense Prep, "
            "Citation Adjudication, and the downloaded artefact "
            "all reference the same paper — eliminating a class "
            "of 'wait, that's not what I saw before' surprises."),
    ))


def downgrade() -> None:
    op.execute(sa.text(
        "DROP INDEX IF EXISTS "
        "uq_report_paper_versions_final_per_generation"
    ))
    op.drop_column("report_paper_versions", "final_submission_at")
    op.drop_column("report_paper_versions", "is_final_submission")
    op.execute("DELETE FROM changelog WHERE version = 59")
