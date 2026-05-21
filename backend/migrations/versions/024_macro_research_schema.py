"""Macro research schema — per-run digest store for the macro market research agent.

The macro_research_digests table stores each run of the macro market
research agent (FEATURE 2 — added May 21 2026). The agent identifies
recent (last 7 days) macroeconomic events affecting equity / IG / HY
markets — Fed announcements, CPI prints, yield curve shifts, VIX
spikes, credit-spread moves — and produces a structured digest the
council and academic_review prompts inject as a CURRENT MACRO
CONDITIONS block.

Why this matters for the project:
  The historical backtest covers 2002-2025. The council's analytical
  agents reason against that history. Without a research layer they
  cannot account for what happened *yesterday* — e.g. a recent Fed
  pivot or an unexpected CPI print. The macro digest is the bridge
  between the quantitative historical signal cache (FRED VIX/DGS2,
  regime_signals_cache) and the live news environment the council is
  asked to advise on.

Schema notes:
  - status — running | complete | failed (mirrors triage_reports and
    audit_runs conventions; the row is INSERTed in 'running' state
    before generation, so a concurrent fire can refuse via a
    is_research_running probe identical to is_triage_running).
  - triggered_by — startup | scheduled | manual; lets the activity
    log distinguish a Render cold-boot run from a user-triggered run.
  - summary_text / regime_implication — short prose; the dashboard
    surfaces them inline.
  - key_signals / citation_urls — JSONB lists; key_signals is a list
    of {category, signal, implication, source_url} dicts the
    frontend renders as bullet rows.
  - raw_response — preserved for audit; lets a future reviewer see
    exactly what the model wrote when a digest is disputed.

Revision ID: 024
Revises: 023
Create Date: 2026-05-21
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "024"
down_revision: str | None = "023"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "macro_research_digests",
        sa.Column("id", sa.BigInteger(), primary_key=True,
                  autoincrement=True),
        sa.Column("generated_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("triggered_by", sa.String(20), nullable=False,
                  comment="startup | scheduled | manual"),
        sa.Column("status", sa.String(20), nullable=False,
                  comment="running | complete | failed"),
        sa.Column("summary_text", sa.Text(), nullable=True),
        sa.Column("regime_implication", sa.Text(), nullable=True),
        # key_signals is a list of {category, signal, implication,
        # source_url}; renderer reads it as-is so the schema is
        # deliberately untyped beyond JSONB.
        sa.Column("key_signals", sa.JSON(), nullable=True),
        sa.Column("citation_urls", sa.JSON(), nullable=True),
        sa.Column("model", sa.String(80), nullable=True,
                  comment="model id that produced the digest"),
        sa.Column("raw_response", sa.Text(), nullable=True,
                  comment="full model output, for audit"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
    )
    # Single index — every read of the table sorts by generated_at
    # desc (latest first). No other query path exists.
    op.create_index(
        "ix_macro_research_digests_generated_at",
        "macro_research_digests", ["generated_at"])

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
        "version": 43,
        "released_at": datetime(2026, 5, 21, tzinfo=timezone.utc),
        "title": "Macro Market Research Agent",
        "description": (
            "A daily-scheduled macro market research agent identifies "
            "recent Fed announcements, CPI prints, yield curve shifts, "
            "VIX spikes, and credit-spread moves, and threads a "
            "CURRENT MACRO CONDITIONS digest into every council and "
            "academic_review run. The digest is also surfaced on the "
            "dashboard so the team sees what conditions the agents "
            "are reasoning against."
        ),
        "academic_rationale": (
            "The backtest covers 2002-2025 history but the panel will "
            "ask the council about today's environment. Injecting a "
            "verified-source macro digest into agent prompts lets the "
            "council answer 'how does our regime-switching strategy "
            "behave under current conditions?' rather than only "
            "'how did it behave historically?' Citation integrity is "
            "preserved through the Anthropic web_search tool — every "
            "signal is backed by a fetched URL the panel can inspect."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 43")
    op.drop_index("ix_macro_research_digests_generated_at",
                  table_name="macro_research_digests")
    op.drop_table("macro_research_digests")
