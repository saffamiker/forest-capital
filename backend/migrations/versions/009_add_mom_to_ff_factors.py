"""Add the momentum (MOM) factor column to ff_factors_monthly.

The factor table shipped as the Fama-French three-factor model
(MKT-RF, SMB, HML). This migration adds the fourth Carhart factor —
momentum — so factor-loading regressions can run the four-factor model.

The column is nullable on creation: migration 009 only adds the schema.
The MOM data is fetched from Ken French's F-F_Momentum_Factor file and
backfilled in a follow-up step; once every row is populated the column
can be tightened to NOT NULL.

Per the approved plan, migration 009 adds ONLY the mom column. True
turnover is per-strategy and lives inside strategy_results_cache's
results_json blob, not as a table column — so no turnover column here.

Revision ID: 009
Revises: 008
Create Date: 2026-05-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "ff_factors_monthly",
        sa.Column(
            "mom",
            sa.Float(),
            nullable=True,
            comment="Momentum factor (Mom / WML) — Carhart fourth factor, "
                    "as percent. Nullable until backfilled from Ken French.",
        ),
    )


def downgrade() -> None:
    op.drop_column("ff_factors_monthly", "mom")
