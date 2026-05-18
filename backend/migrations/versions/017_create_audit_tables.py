"""Create audit_runs and audit_findings — the statistical audit system.

The audit independently recomputes every analytics metric with a
separate model (claude-opus-4-7) and records each check as a finding.
audit_runs is one row per audit; audit_findings is one row per check
across the three audit layers.

Revision ID: 017
Revises: 016
Create Date: 2026-05-17
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "017"
down_revision: str | None = "016"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "audit_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("triggered_by", sa.String(20), nullable=False,
                  comment="manual | scheduled | pre_submission"),
        sa.Column("triggered_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("triggered_by_email", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="running",
                  comment="running | complete | failed"),
        sa.Column("layer_1_status", sa.String(10), nullable=True,
                  comment="pass | fail | skip"),
        sa.Column("layer_2_status", sa.String(10), nullable=True),
        sa.Column("layer_3_status", sa.String(10), nullable=True),
        sa.Column("total_checks", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warnings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_audit_runs_triggered_at",
                    "audit_runs", ["triggered_at"])

    op.create_table(
        "audit_findings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("audit_run_id", sa.BigInteger(),
                  sa.ForeignKey("audit_runs.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("layer", sa.Integer(), nullable=False,
                  comment="1 | 2 | 3"),
        sa.Column("check_name", sa.String(120), nullable=False),
        sa.Column("metric", sa.String(80), nullable=False),
        sa.Column("strategy", sa.String(80), nullable=True),
        sa.Column("severity", sa.String(10), nullable=False,
                  server_default="info",
                  comment="critical | warning | info"),
        sa.Column("status", sa.String(10), nullable=False,
                  comment="pass | fail | warning"),
        sa.Column("platform_value", sa.Text(), nullable=True),
        sa.Column("auditor_value", sa.Text(), nullable=True),
        sa.Column("discrepancy", sa.Text(), nullable=True),
        sa.Column("formula_used", sa.Text(), nullable=True),
        sa.Column("raw_inputs_hash", sa.String(64), nullable=True,
                  comment="SHA256 of the input data — reproducibility"),
        sa.Column("auditor_reasoning", sa.Text(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("resolution_note", sa.Text(), nullable=True),
    )
    op.create_index("ix_audit_findings_run",
                    "audit_findings", ["audit_run_id"])

    # Changelog contract — every migration inserts at least one row.
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
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
        "version": 36,
        "released_at": now,
        "title": "Statistical Audit System",
        "description": (
            "Every analytical figure on the platform can now be "
            "independently re-verified — a separate AI model recomputes "
            "every metric from the raw data and flags any discrepancy."
        ),
        "academic_rationale": (
            "An independent statistical audit is the platform's strongest "
            "accuracy guarantee -- every number shown to Forest Capital "
            "and faculty has been recomputed from scratch by a separate "
            "model, with full working shown, and the audit report can be "
            "attached to the Analytical Appendix as evidence."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 36")
    op.drop_table("audit_findings")
    op.drop_table("audit_runs")
