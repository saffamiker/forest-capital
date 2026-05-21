"""PR-driven Suggested Resolutions — pr_suggestions queue table.

The resolution gate (migration 025) lets a sysadmin attach a fix
reference to a failure report manually via the Mark Resolved modal.
PR #65 fixed the council 502 failures BEFORE that gate shipped, so
the back-fill script in backend/scripts/backfill_council_resolutions.py
had to attach the resolution by direct DB call. The Suggested
Resolutions feature closes the loop: when a PR merges, a GitHub
webhook scans its body + commits for "Resolves failure #N" references
and creates a pending_review queue entry the sysadmin can review,
approve (converting it into a real resolution on the failure report),
or dismiss.

This is a QUEUE table — pending → approved | dismissed. The terminal
states stay in the table for audit, but the UI only renders
pending_review rows (banner + review modal + row badge). After
approval, the durable evidence lives on test_results via the
fix_reference column the resolution gate writes; the pr_suggestions
row is just the workflow record.

Schema notes:

  - failure_report_id ON DELETE CASCADE — if a test_results row is
    cleaned up, its suggestions go too. The suggestion has no value
    without the failure it references.

  - matched_commit_shas / matched_on — captured at webhook time so
    the review modal can render the exact commit list and quote the
    PR-body line that triggered the match.

  - UNIQUE (failure_report_id, pr_number) — GitHub re-delivers
    webhooks on transient failures; the constraint + ON CONFLICT DO
    NOTHING in the webhook handler makes redelivery a silent no-op
    rather than a duplicate suggestion.

  - CHECK constraint on suggestion_state — three enum values.
    Mirrors the resolution_type pattern from migration 025.

  - Indexed on (suggestion_state, created_at desc) — the GET
    /api/v1/testing/suggestions query filters on state and orders
    newest first; this index covers both.

Revision ID: 026
Revises: 025
Create Date: 2026-05-22
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "026"
down_revision: str | None = "025"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "pr_suggestions",
        sa.Column("id", sa.BigInteger(), primary_key=True,
                  autoincrement=True),
        sa.Column("failure_report_id", sa.BigInteger(),
                  sa.ForeignKey("test_results.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("pr_title", sa.String(500), nullable=False),
        sa.Column("pr_url", sa.String(500), nullable=False),
        sa.Column("pr_merged_at", sa.TIMESTAMP(timezone=True),
                  nullable=False),
        sa.Column("pr_author", sa.String(100), nullable=True),
        # The list of commit SHAs the webhook saw on this PR — used by
        # the review modal to render the commit links. JSON because
        # the array length varies per PR (1 to ~50 commits in practice).
        sa.Column("matched_commit_shas", sa.JSON(), nullable=True),
        # The exact "Resolves failure #N" string that triggered the
        # match — surfaced verbatim in the review modal so the
        # reviewer can see WHY this PR was linked to this failure.
        sa.Column("matched_on", sa.Text(), nullable=True),
        sa.Column("suggestion_state", sa.String(20), nullable=False,
                  server_default="pending_review",
                  comment="pending_review | approved | dismissed"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True),
                  nullable=True),
        sa.Column("reviewed_by", sa.String(100), nullable=True),
        # Dismiss-reason free-text, captured by the dismiss endpoint
        # when the reviewer provides one. Optional; the audit trail
        # works whether or not it's filled.
        sa.Column("dismiss_reason", sa.Text(), nullable=True),
    )

    # Idempotency guard — see module docstring. A redelivered webhook
    # event hits this constraint and the handler's ON CONFLICT
    # silently swallows the duplicate INSERT.
    op.create_unique_constraint(
        "uq_pr_suggestions_failure_pr",
        "pr_suggestions",
        ["failure_report_id", "pr_number"],
    )

    # Enum-style guard. Any direct UPDATE that bypasses the API
    # validator hits this constraint instead of producing a state the
    # frontend cannot render.
    op.create_check_constraint(
        "ck_pr_suggestions_state",
        "pr_suggestions",
        "suggestion_state IN ('pending_review', 'approved', 'dismissed')",
    )

    # Covers the primary read path: pending_review newest-first for
    # the banner / review modal / GET endpoint.
    op.create_index(
        "ix_pr_suggestions_state_created",
        "pr_suggestions",
        ["suggestion_state", sa.text("created_at DESC")],
    )

    # Covers the row-badge lookup (per-failure suggestion existence
    # check). Used by GET /api/v1/testing/failures rendering when the
    # frontend joins the badge list onto the failure rows.
    op.create_index(
        "ix_pr_suggestions_failure_state",
        "pr_suggestions",
        ["failure_report_id", "suggestion_state"],
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
        "version": 45,
        "released_at": datetime(2026, 5, 22, tzinfo=timezone.utc),
        "title": "PR-driven Suggested Resolutions",
        "description": (
            "When a PR merges with a 'Resolves failure #N' reference "
            "in its body or any commit message, a GitHub webhook "
            "queues a pr_suggestions row for the named failure. The "
            "sysadmin sees a banner on Failure Reports, opens the "
            "review modal, fills in the root cause + remediation, "
            "and approves — which writes the structured resolution "
            "onto test_results and fires the tester's retest "
            "notification. Closes the loop between fix and report."
        ),
        "academic_rationale": (
            "Closing the loop on a reported failure is a quality "
            "signal the FNA 670 panel will look for. The "
            "PR-suggestion automation removes the manual step where "
            "a fix lands but the failure report stays Open because "
            "no one remembered to mark it resolved — directly "
            "supporting the Analytical Appendix's traceability "
            "narrative."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 45")
    op.drop_index("ix_pr_suggestions_failure_state",
                  table_name="pr_suggestions")
    op.drop_index("ix_pr_suggestions_state_created",
                  table_name="pr_suggestions")
    op.drop_constraint("ck_pr_suggestions_state",
                       "pr_suggestions", type_="check")
    op.drop_constraint("uq_pr_suggestions_failure_pr",
                       "pr_suggestions", type_="unique")
    op.drop_table("pr_suggestions")
