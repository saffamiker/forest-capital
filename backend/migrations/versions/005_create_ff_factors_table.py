"""Create ff_factors_monthly for Sprint 6 cost optimisation.

Replaces the broken pandas-datareader fetch with a direct HTTP download
from Ken French's Dartmouth page. Same DB-cache + incremental-update
pattern as market_data_monthly: full history fetched once at pipeline
init, append-only updates on subsequent runs.

The table is intentionally minimal — only the 3 factors used by the
OLS regression in tools/chart_data.py (Mkt-RF, SMB, HML) plus the
risk-free rate. Mom (momentum) and other supplementary factors are
not used by any consumer yet; add columns later if needed without a
schema break.

Revision ID: 005
Revises: 004
Create Date: 2026-05-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # yyyymm is the natural key (Ken French publishes month-level data
    # tagged YYYYMM). Storing it as an integer keeps the table compact
    # and lets the incremental-update logic use simple `> max(yyyymm)`
    # filters rather than parsing date strings on every read.
    op.create_table(
        "ff_factors_monthly",
        sa.Column(
            "yyyymm",
            sa.Integer(),
            primary_key=True,
            comment="Year-month identifier — e.g. 202412 for December 2024",
        ),
        sa.Column(
            "mkt_rf",
            sa.Float(),
            nullable=False,
            comment="Market excess return over the risk-free rate (Rm-Rf), as percent",
        ),
        sa.Column(
            "smb",
            sa.Float(),
            nullable=False,
            comment="Small Minus Big — size factor, percent",
        ),
        sa.Column(
            "hml",
            sa.Float(),
            nullable=False,
            comment="High Minus Low — value factor, percent",
        ),
        sa.Column(
            "rf",
            sa.Float(),
            nullable=False,
            comment="One-month T-bill rate, percent",
        ),
        sa.Column(
            "source",
            sa.String(50),
            server_default="ken_french_direct",
            nullable=False,
            comment="Provenance tag — matches data_series_registry.source_type",
        ),
        sa.Column(
            "fetched_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("ff_factors_monthly")
