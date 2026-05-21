"""Triage resolution schema — per-item tracking, GitHub linkage, retest workflow.

The triage_reports table (migration 016) stored each run's prose verdict
in one `report_text` blob. Item-level resolution was impossible: the
findings the agent wrote under IMMEDIATE ACTIONS / QUICK WINS / PATTERNS
/ POST-DEADLINE BACKLOG lived only as markdown bullets inside the blob
and had no row identity. GitHub issues opened against blocking items
were tracked in `metadata.github_issues` JSONB, but the source
`test_results` / `test_feedback` rows carried no back-pointer to the
issue, so a second triage couldn't ask "is this item already issued?"

This migration adds the missing layer:

  1. Run-level resolution columns on triage_reports — when Michael
     marks a whole run handled.
  2. New triage_report_items child table — one row per finding,
     addressable by id. Carries the GitHub issue link, the source-row
     back-pointer, full resolution + retest workflow fields.
  3. Back-pointer columns on test_results + test_feedback so a second
     triage run can detect already-issued items without scanning every
     triage_reports.metadata array.

The retest workflow lets Claude Code call resolve_triage_items() after
applying a fix: requires_retest=True triggers a notification to the
reporter, retest_completed_at lands when the reporter re-attests via
the test runner.

Revision ID: 023
Revises: 022
Create Date: 2026-05-21
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision: str = "023"
down_revision: str | None = "022"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── 1. triage_reports resolution columns ───────────────────────────────────
    # Run-level workflow — when Michael (or the team) marks the whole
    # triage report handled. The per-item resolution lives on the new
    # triage_report_items table.
    op.add_column("triage_reports",
                  sa.Column("resolved_at", sa.TIMESTAMP(timezone=True),
                            nullable=True))
    op.add_column("triage_reports",
                  sa.Column("resolved_by", sa.String(100), nullable=True))
    op.add_column("triage_reports",
                  sa.Column("resolution_note", sa.Text(), nullable=True))
    op.add_column("triage_reports",
                  sa.Column("requires_retest", sa.Boolean(),
                            nullable=False, server_default=sa.false()))
    op.add_column("triage_reports",
                  sa.Column("retest_requested_at",
                            sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("triage_reports",
                  sa.Column("retest_completed_at",
                            sa.TIMESTAMP(timezone=True), nullable=True))

    # ── 2. triage_report_items — one row per finding ───────────────────────────
    # Parses out of report_text into addressable rows. CASCADE delete
    # so removing a triage_reports row drops its items with it; the
    # report is the authoritative parent.
    op.create_table(
        "triage_report_items",
        sa.Column("id", sa.BigInteger(), primary_key=True,
                  autoincrement=True),
        sa.Column("report_id", sa.BigInteger(),
                  sa.ForeignKey("triage_reports.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("item_type", sa.String(20), nullable=False,
                  comment="immediate | quick_win | pattern | backlog"),
        sa.Column("item_title", sa.String(500), nullable=False),
        sa.Column("item_body", sa.Text(), nullable=True),
        # GitHub issue linkage — populated when triage_engine opened an
        # issue for this item (only blocking/major-severity immediates
        # get issues today). null on items that did not warrant one.
        sa.Column("github_issue_number", sa.Integer(), nullable=True),
        sa.Column("github_issue_url", sa.String(500), nullable=True),
        # Back-pointer to the originating UAT row. source_item_type is
        # "failure" (test_results.id) or "feedback" (test_feedback.id);
        # nullable because pattern / backlog items synthesise across
        # multiple sources and don't map to a single row.
        sa.Column("source_item_type", sa.String(20), nullable=True,
                  comment="failure | feedback"),
        sa.Column("source_item_id", sa.BigInteger(), nullable=True),
        # Resolution workflow.
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True),
                  nullable=True),
        sa.Column("resolved_by", sa.String(100), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("fix_commit", sa.String(100), nullable=True,
                  comment="The git commit SHA that addressed this item"),
        # Retest workflow. When a fix is functional (not cosmetic) the
        # reporter should re-run the test. requires_retest flips the
        # frontend Re-test pill on; retest_requested_at logs when the
        # reporter was notified; retest_completed_at lands when they
        # re-attest via the test runner.
        sa.Column("requires_retest", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("retest_requested_at", sa.TIMESTAMP(timezone=True),
                  nullable=True),
        sa.Column("retest_completed_at", sa.TIMESTAMP(timezone=True),
                  nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_triage_report_items_report_id",
                    "triage_report_items", ["report_id"])
    op.create_index("ix_triage_report_items_resolved_at",
                    "triage_report_items", ["resolved_at"])
    op.create_index("ix_triage_report_items_source",
                    "triage_report_items",
                    ["source_item_type", "source_item_id"])

    # ── 3. Back-pointer columns on UAT source tables ───────────────────────────
    # triage_engine populates github_issue_number + github_issue_url
    # whenever it opens an issue for an item — so a second triage can
    # ask "has this failure / feedback already been issued?" without
    # scanning every triage_reports.metadata array. triaged_at on
    # test_results lets _gather_unaddressed skip already-triaged
    # failures (previously they re-flowed into every run).
    op.add_column("test_results",
                  sa.Column("github_issue_number", sa.Integer(),
                            nullable=True))
    op.add_column("test_results",
                  sa.Column("github_issue_url", sa.String(500),
                            nullable=True))
    op.add_column("test_results",
                  sa.Column("triaged_at", sa.TIMESTAMP(timezone=True),
                            nullable=True))
    op.add_column("test_feedback",
                  sa.Column("github_issue_number", sa.Integer(),
                            nullable=True))
    op.add_column("test_feedback",
                  sa.Column("github_issue_url", sa.String(500),
                            nullable=True))

    # ── Changelog entry — every migration must seed one ────────────────────────
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
        "version": 42,
        "released_at": datetime(2026, 5, 21, tzinfo=timezone.utc),
        "title": "Triage Resolution Workflow",
        "description": (
            "Triage reports gain per-item resolution tracking. Each "
            "finding the QA-lead agent identifies now has its own row "
            "(triage_report_items), carries the GitHub issue link and "
            "a back-pointer to the originating UAT failure or "
            "feedback, and tracks a full resolve + retest workflow. "
            "When a fix lands, Claude Code calls resolve_triage_items() "
            "which marks the item resolved, records the fix commit, "
            "and — when the change is functional — notifies the "
            "original reporter to re-test."
        ),
        "academic_rationale": (
            "A closed-loop quality process is one of the rubric "
            "signals the FNA 670 panel will look for. Per-item triage "
            "resolution + automatic re-test notifications turn the "
            "raw UAT backlog into an evidence trail of what was "
            "found, what was fixed, and who confirmed the fix — "
            "directly supporting the Analytical Appendix's process "
            "section."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 42")
    op.drop_column("test_feedback", "github_issue_url")
    op.drop_column("test_feedback", "github_issue_number")
    op.drop_column("test_results", "triaged_at")
    op.drop_column("test_results", "github_issue_url")
    op.drop_column("test_results", "github_issue_number")
    op.drop_index("ix_triage_report_items_source",
                  table_name="triage_report_items")
    op.drop_index("ix_triage_report_items_resolved_at",
                  table_name="triage_report_items")
    op.drop_index("ix_triage_report_items_report_id",
                  table_name="triage_report_items")
    op.drop_table("triage_report_items")
    op.drop_column("triage_reports", "retest_completed_at")
    op.drop_column("triage_reports", "retest_requested_at")
    op.drop_column("triage_reports", "requires_retest")
    op.drop_column("triage_reports", "resolution_note")
    op.drop_column("triage_reports", "resolved_by")
    op.drop_column("triage_reports", "resolved_at")
