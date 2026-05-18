"""Add token-usage and cost columns to agent_interactions.

Every AI agent call now records its input/output token counts, the
model used and an estimated USD cost, so the Team Activity page can
surface an AI spend summary. Costs are ESTIMATES from published API
rates (config.TOKEN_COSTS_USD) — actual provider billing may differ.

Revision ID: 020
Revises: 019
Create Date: 2026-05-18
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision: str = "020"
down_revision: str | None = "019"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("agent_interactions", sa.Column(
        "input_tokens", sa.Integer(), nullable=True,
        comment="total input tokens across the interaction's AI calls"))
    op.add_column("agent_interactions", sa.Column(
        "output_tokens", sa.Integer(), nullable=True,
        comment="total output tokens across the interaction's AI calls"))
    op.add_column("agent_interactions", sa.Column(
        "model_used", sa.String(80), nullable=True,
        comment="model string; 'multiple' for a multi-model interaction"))
    op.add_column("agent_interactions", sa.Column(
        "estimated_cost_usd", sa.Numeric(10, 6), nullable=True,
        comment="estimated USD cost — see config.TOKEN_COSTS_USD"))

    # Changelog contract — every migration inserts at least one row.
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
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
        "version": 39,
        "released_at": now,
        "title": "AI Token Usage & Cost Tracking",
        "description": (
            "Every AI agent call now records its token usage and an "
            "estimated cost. Team Activity gains an AI Spend Summary — "
            "totals by period, by model, and by feature — and council "
            "sessions show a per-query and per-agent cost breakdown."
        ),
        "academic_rationale": (
            "Transparent cost accounting lets the team show, in the "
            "AI-use narrative for the July 1 presentation, exactly what "
            "the platform's AI council and audits cost to run — and lets "
            "the sysadmin see what each guest reviewer is spending before "
            "extending their council allowance."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 39")
    op.drop_column("agent_interactions", "estimated_cost_usd")
    op.drop_column("agent_interactions", "model_used")
    op.drop_column("agent_interactions", "output_tokens")
    op.drop_column("agent_interactions", "input_tokens")
