"""Report writer pipeline audit log + per-generation timings.

May 22 2026 (item 12 commit 5 — pipeline UX fix).

Three schema changes:

1. ALTER report_generations — pipeline_timings JSONB. Holds the
   per-step elapsed milliseconds for the generation that produced
   the row (when populated). The summary card in the report writer
   reads this back for the post-generation pipeline benchmark.

2. CREATE report_pipeline_audit — one row per pipeline RUN
   (including failed runs that never reached step 7). Captures
   per-step status, per-step elapsed ms, the mismatch count for
   step 5, the condition results for step 6, and the failure step
   + reason when a run did not complete.

3. Changelog entry 52 — the user-visible behaviour change is the
   per-step pipeline UI with timing display and the sysadmin
   audit view.

Per the project convention the migration is fail-open at apply
time: a JSONB column default and the standard text columns are
non-breaking for existing report_generations rows.
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "033"
down_revision: str | None = "032"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── 1. ALTER report_generations ─────────────────────────────────────
    op.add_column(
        "report_generations",
        sa.Column("pipeline_timings", sa.JSON(), nullable=True,
                  comment="Per-step elapsed ms recorded by the report-"
                          "writer UI. NULL for rows generated before "
                          "the timing instrumentation shipped."))

    # ── 2. CREATE report_pipeline_audit ─────────────────────────────────
    op.create_table(
        "report_pipeline_audit",
        sa.Column("id", sa.BigInteger(), primary_key=True,
                  autoincrement=True),
        sa.Column("generation_id", sa.BigInteger(), nullable=True,
                  comment="report_generations.id when the run reached "
                          "step 7 and produced a generation row. NULL "
                          "for runs that failed before step 7."),
        sa.Column("template_id", sa.String(80), nullable=False),
        sa.Column("triggered_by", sa.String(255), nullable=True,
                  comment="Email of the user who initiated the run. "
                          "NULL when the run originated from a system "
                          "trigger (e.g. an automated nightly cycle, "
                          "though none of those exist yet)."),
        sa.Column("run_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        # Per-step status + ms (compact columns for fast filtering).
        sa.Column("step_1_status", sa.String(20), nullable=True),
        sa.Column("step_1_ms",     sa.Integer(),  nullable=True),
        sa.Column("step_2_status", sa.String(20), nullable=True),
        sa.Column("step_2_ms",     sa.Integer(),  nullable=True),
        sa.Column("step_3_status", sa.String(20), nullable=True),
        sa.Column("step_3_ms",     sa.Integer(),  nullable=True),
        sa.Column("step_4_status", sa.String(20), nullable=True),
        sa.Column("step_4_ms",     sa.Integer(),  nullable=True),
        sa.Column("step_5_status", sa.String(20), nullable=True),
        sa.Column("step_5_ms",     sa.Integer(),  nullable=True),
        sa.Column("step_5_mismatch_count", sa.Integer(), nullable=True),
        sa.Column("step_6_status", sa.String(20), nullable=True),
        sa.Column("step_6_ms",     sa.Integer(),  nullable=True),
        sa.Column("step_6_conditions", sa.JSON(), nullable=True,
                  comment="Per-condition payload from validate_thesis: "
                          "id, threshold, value, passed."),
        sa.Column("step_7_status", sa.String(20), nullable=True),
        sa.Column("step_7_ms",     sa.Integer(),  nullable=True),
        sa.Column("total_pipeline_ms", sa.Integer(), nullable=True),
        sa.Column("failure_step", sa.Integer(), nullable=True,
                  comment="Step number where the pipeline failed; "
                          "NULL for successful runs."),
        sa.Column("failure_reason", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_report_pipeline_audit_run_at",
        "report_pipeline_audit",
        [sa.text("run_at DESC")])
    op.create_index(
        "ix_report_pipeline_audit_template",
        "report_pipeline_audit",
        ["template_id", sa.text("run_at DESC")])

    # ── Changelog ───────────────────────────────────────────────────────
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
        "version": 52,
        "released_at": datetime(2026, 5, 22, tzinfo=timezone.utc),
        "title": "Report writer — per-step pipeline + audit log",
        "description": (
            "The report writer's eleven-step pipeline now shows each "
            "step with its own Run button, auto-cascades through the "
            "preparation steps (1 → 2/3/4 in parallel → 5 → 6 → 7), "
            "and records elapsed time per step. Sysadmins gain a "
            "Pipeline Audit view in Settings that lists every run "
            "with its step-by-step timings and failure reasons for "
            "performance regression tracking."
        ),
        "academic_rationale": (
            "A multi-step generation pipeline that fails opaquely is "
            "an obstacle to the submission deadline. Per-step "
            "visibility lets Bob (and the supporting engineer) see "
            "exactly which gate blocked a run and how long the "
            "successful runs take — both essential when iterating "
            "against a graded deliverable on a fixed deadline."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 52")
    op.drop_index("ix_report_pipeline_audit_template",
                  table_name="report_pipeline_audit")
    op.drop_index("ix_report_pipeline_audit_run_at",
                  table_name="report_pipeline_audit")
    op.drop_table("report_pipeline_audit")
    op.drop_column("report_generations", "pipeline_timings")
