"""Create the guided UAT test-runner tables: test_results and test_feedback.

The in-platform guided test runner replaces the static markdown test
guide with an interactive, logged, attested system. Test SCRIPTS are
versioned with the code (constants/testScripts.ts) — only RESULTS and
FEEDBACK are persisted here, per user and per step, with timestamps,
override capability, structured failure reports, and AI categorisation.

test_results  — one attested row per (user, script, step). The unique
constraint makes a re-attestation an UPSERT; a re-attestation flips
`overridden` true so the audit trail records that the row was revised.

test_feedback — free-form tester feedback. A submission is either
step-linked (script_id + step_id set) or free-form (both null,
source_route set — the "Suggest an enhancement" button). Every entry
carries AI categorisation fields filled at submit time.

Revision ID: 014
Revises: 013
Create Date: 2026-05-17
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "014"
down_revision: str | None = "013"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── test_results — one attested row per (user, script, step) ──────────
    op.create_table(
        "test_results",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_email", sa.String(255), nullable=False),
        sa.Column("script_id", sa.String(80), nullable=False),
        sa.Column("step_id", sa.String(120), nullable=False),
        sa.Column("result", sa.String(20), nullable=False,
                  comment="pass | fail | skip"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("failure_description", sa.Text(), nullable=True),
        sa.Column("expected_result", sa.Text(), nullable=True),
        sa.Column("actual_result", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(20), nullable=True,
                  comment="blocking | major | minor | cosmetic"),
        sa.Column("browser_info", sa.Text(), nullable=True),
        sa.Column("screenshot_paths", postgresql.ARRAY(sa.Text()), nullable=True,
                  comment="Relative paths under the uploads mount — never BLOBs"),
        sa.Column("low_quality", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(255), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("attested_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("overridden", sa.Boolean(), nullable=False,
                  server_default=sa.false(),
                  comment="True once the row has been re-attested"),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("session_type", sa.String(20), nullable=False,
                  server_default="testing", comment="analytical | testing"),
        sa.UniqueConstraint("user_email", "script_id", "step_id",
                            name="uq_test_results_user_script_step"),
    )
    op.create_index("ix_test_results_user_email", "test_results", ["user_email"])
    op.create_index("ix_test_results_result", "test_results", ["result"])

    # ── test_feedback — step-linked or free-form tester feedback ──────────
    op.create_table(
        "test_feedback",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_email", sa.String(255), nullable=False),
        # Nullable: a free-form "Suggest an enhancement" submission has no
        # script/step — it carries source_route instead.
        sa.Column("script_id", sa.String(80), nullable=True),
        sa.Column("step_id", sa.String(120), nullable=True),
        sa.Column("source_route", sa.String(255), nullable=True,
                  comment="Route captured for a free-form (non-step) suggestion"),
        sa.Column("feedback_type", sa.String(40), nullable=False,
                  comment="feature_request | question | observation | confusion"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(20), nullable=True,
                  comment="must_have | should_have | nice_to_have"),
        sa.Column("screenshot_paths", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("browser_info", sa.Text(), nullable=True),
        sa.Column("low_quality", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        # AI categorisation — filled by the categoriser at submit time.
        sa.Column("ai_category", sa.String(40), nullable=True),
        sa.Column("ai_severity", sa.String(20), nullable=True),
        sa.Column("ai_effort_estimate", sa.String(20), nullable=True),
        sa.Column("ai_tags", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        # Resolution.
        sa.Column("status", sa.String(20), nullable=False, server_default="new",
                  comment="new | noted | planned | wont_do | resolved"),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.String(255), nullable=True),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_test_feedback_user_email", "test_feedback", ["user_email"])
    op.create_index("ix_test_feedback_status", "test_feedback", ["status"])

    # ── Changelog contract — every migration inserts at least one row ─────
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
        "version": 33,
        "released_at": datetime(2026, 5, 17, tzinfo=timezone.utc),
        "title": "Guided UAT Test Runner",
        "description": (
            "An interactive, logged test runner — walk through each test "
            "case in-platform, record an attested pass/fail/skip, file "
            "structured failure reports, and submit AI-categorised feedback."
        ),
        "academic_rationale": (
            "Guided UAT test runner with attested results, structured "
            "failure reports, and AI-categorized feedback provides "
            "objective, timestamped evidence of systematic quality "
            "assurance -- directly supports Analytical Appendix "
            "transparency criterion."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 33")
    op.drop_table("test_feedback")
    op.drop_table("test_results")
