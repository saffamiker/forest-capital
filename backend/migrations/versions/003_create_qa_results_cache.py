"""Create qa_results_cache table for Sprint 6 tiered QA.

Stores every QA verdict (Tier 1, 2, 3) keyed by strategy_hash so the Present
mode gate can check verdict ≥ WARN + run_at < 48h + strategy_hash matches
current data without ever blocking the dashboard. Multiple tiers per
strategy_hash coexist — Tier 2 results don't overwrite Tier 1, etc.

Revision ID: 003
Revises: 002
Create Date: 2026-05-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # qa_results_cache — one row per (tier, strategy_hash, run_at).
    # The Present-mode gate reads the most recent passing-or-warning row for
    # the current strategy_hash. Tier rows are immutable history: we APPEND
    # rather than UPDATE so the Admin screen can show the audit trail.
    op.create_table(
        "qa_results_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "tier",
            sa.SmallInteger(),
            nullable=False,
            comment="1 = pure-Python deterministic, 2 = Sonnet background, 3 = Opus manual",
        ),
        sa.Column(
            "strategy_hash",
            sa.String(64),
            nullable=False,
            comment="SHA-256 of (n_rows, last_date, n_strategies) — same hash as strategy_results_cache",
        ),
        sa.Column(
            "verdict",
            sa.String(8),
            nullable=False,
            comment="PASS | WARN | FAIL",
        ),
        sa.Column(
            "checklist_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Full per-check breakdown plus summary metadata",
        ),
        sa.Column(
            "run_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Tier 1: never expires (deterministic). Tier 2: run_at + 24h. Tier 3: run_at + 7d.",
        ),
    )
    # Index supports the "latest verdict for this strategy_hash" lookup
    # that the Present-mode gate runs on every status check.
    op.create_index(
        "ix_qa_results_cache_hash_run",
        "qa_results_cache",
        ["strategy_hash", "run_at"],
    )
    # Index supports the "is any tier currently fresh?" check
    op.create_index(
        "ix_qa_results_cache_expires",
        "qa_results_cache",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_table("qa_results_cache")
