"""document audit layer — editor_drafts.audit_warnings + document_audit_metrics.

June 3 2026. Lands the deterministic post-generation audit framework
for the executive brief and the presentation deck. The brief and deck
generators run four pure-Python checks AFTER the LLM produces content
and BEFORE the draft lands in editor_drafts:

  1. numeric cross-reference   — every cited number traces back to a
                                 cache row within 0.005 absolute
  2. label direction           — superlatives on loss metrics get
                                 flagged for human review
  3. cross-section consistency — same strategy + metric must agree
                                 within 0.05 across sections
  4. citation completeness     — every Author (Year) appears in the
                                 document's own references section

Two schema additions:

  editor_drafts.audit_warnings JSONB
    Carries the per-check flag list when any check flagged. NULL on
    a clean run. The frontend reads it on draft load and renders a
    single banner with a per-check expander.

  document_audit_metrics
    Append-only row per generation run. Mirrors the council_query_
    metrics shape so flag rates can be tracked over time. Indexed
    on (timestamp DESC) for the admin endpoint's last-N read.

Both surfaces are FAIL-OPEN inside the generator wiring: an audit
exception logs and proceeds with audit_warnings=None — the human-
facing document write is never blocked.

Revision ID: 051
Revises: 050
Create Date: 2026-06-03
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "051"
down_revision: str | None = "050"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── editor_drafts.audit_warnings ──────────────────────────────────────
    # Nullable JSONB so a clean run stores NULL (cheap) and the
    # frontend can branch on `if (draft.audit_warnings)`. No index
    # needed — the column is read on draft load only.
    op.add_column(
        "editor_drafts",
        sa.Column("audit_warnings",
                  postgresql.JSONB(astext_type=sa.Text()),
                  nullable=True,
                  comment="Per-check flag list from the post-generation "
                          "audit (tools.document_audit). NULL on a clean "
                          "run. Shape: {numeric: [...], direction: [...], "
                          "consistency: [...], citation: [...]}"),
    )

    # ── document_audit_metrics ────────────────────────────────────────────
    op.create_table(
        "document_audit_metrics",
        sa.Column("id", sa.BigInteger(), primary_key=True,
                  autoincrement=True),
        sa.Column("timestamp", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("document_type", sa.String(40), nullable=False,
                  comment="executive_brief | presentation_deck"),
        sa.Column("owner_email", sa.String(255), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=True,
                  comment="editor_drafts.id when the audit ran "
                          "alongside a successful draft create"),
        sa.Column("numeric_flag_count", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("direction_flag_count", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("consistency_flag_count", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("citation_flag_count", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("total_flag_count", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("data_hash", sa.String(64), nullable=True,
                  comment="strategy_hash live at audit time, so the "
                          "metrics row can be anchored to the exact "
                          "dataset that produced the document"),
    )
    op.create_index(
        "ix_document_audit_metrics_timestamp",
        "document_audit_metrics",
        [sa.text("timestamp DESC")],
    )
    op.create_index(
        "ix_document_audit_metrics_document_type",
        "document_audit_metrics",
        ["document_type"],
    )

    # ── Changelog (required by changelog_gate.py) ─────────────────────────
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
        "version": 70,
        "released_at": datetime(2026, 6, 3, tzinfo=timezone.utc),
        "title": "Post-generation audit layer for the brief + deck",
        "description": (
            "The executive brief and presentation deck generators now "
            "run a four-check deterministic audit after the LLM "
            "produces content and before the draft lands in "
            "editor_drafts: numeric cross-reference against the cache, "
            "label-direction on loss metrics, cross-section consistency "
            "by (strategy, metric), and citation completeness. Any flag "
            "is stored on the draft's new audit_warnings JSONB column "
            "and rendered as a banner in the document editor so Bob and "
            "Molly see the issues before finalizing. The write is "
            "never blocked — the human reviews and resolves. Per-run "
            "flag counts land in document_audit_metrics for tracking "
            "flag rates over time."
        ),
        "academic_rationale": (
            "The Forest Capital and McColl panel grading rewards "
            "evidence-grounded prose where every cited number is "
            "traceable. The four checks catch the most common ways "
            "a draft can drift from the underlying data (the LLM "
            "round-trips a Sharpe figure with the wrong post-2022 / "
            "full-sample window, says 'the highest drawdown' meaning "
            "'shallowest', cites Lopez de Prado without putting the "
            "reference in the bibliography). Catching these in the "
            "editor instead of in front of the panel improves the "
            "final-document quality."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 70")
    op.drop_index(
        "ix_document_audit_metrics_document_type",
        table_name="document_audit_metrics",
    )
    op.drop_index(
        "ix_document_audit_metrics_timestamp",
        table_name="document_audit_metrics",
    )
    op.drop_table("document_audit_metrics")
    op.drop_column("editor_drafts", "audit_warnings")
