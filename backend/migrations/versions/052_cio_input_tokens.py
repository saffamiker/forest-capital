"""council_query_metrics.cio_input_tokens — like-for-like bundle measurement.

June 3 2026. The existing input_tokens column captures the SUM across
every agent call in a council deliberation (4 specialists + draft +
2 dissenters + synthesis), which dominates the bundle-driven CIO
prompt cost and obscures the classifier's effect. The new column
captures the per-agent "cio" total from agents.usage.collect_usage()
so the cost-comparison aggregates in /admin/council-metrics surface
a clean bundle-impact signal.

The "cio" label covers BOTH _compile_draft_consensus and _synthesise
(both _tag_agent("cio") before their call_claude). Both receive the
live_context dict and are the only two calls in the deliberation
whose prompt content the bundle directly changes. So per_agent.cio.
input_tokens IS the right "bundle effect" measurement.

Nullable so rows written before this migration carry NULL and don't
break the AVG() aggregate query.

Revision ID: 052
Revises: 051
Create Date: 2026-06-03
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "052"
down_revision: str | None = "051"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "council_query_metrics",
        sa.Column("cio_input_tokens", sa.Integer(), nullable=True,
                  comment="Input tokens attributed to the 'cio' agent "
                          "label specifically (per_agent.cio from "
                          "agents.usage.collect_usage). Spans both the "
                          "draft-consensus and synthesis call_claude "
                          "calls. NULL on rows written before this "
                          "column existed."),
    )

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
        "version": 71,
        "released_at": datetime(2026, 6, 3, tzinfo=timezone.utc),
        "title": "Council metrics: per-CIO input tokens",
        "description": (
            "council_query_metrics gains a cio_input_tokens column "
            "capturing the agents.usage.per_agent['cio'] total from "
            "each council query. This is the LIKE-FOR-LIKE bundle "
            "comparison metric — the wider input_tokens column "
            "captures every agent call's input including 4 "
            "specialists + 2 dissenters whose prompts the bundle "
            "does NOT change. /admin/council-metrics surfaces both "
            "totals so a reader can see the bundle-effect signal "
            "without the specialist-call noise."
        ),
        "academic_rationale": (
            "The question-type context-bundle work (PR #262) is "
            "instrumented to demonstrate cost reduction. The wider "
            "total-tokens figure includes chart-vision content "
            "blocks on every specialist call — a fixed cost per "
            "deliberation independent of bundle choice. Isolating "
            "the CIO call tokens makes the bundle's effect "
            "measurable for the methodology section of the brief."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 71")
    op.drop_column("council_query_metrics", "cio_input_tokens")
