"""Create data tables for Sprint 2 provenance and market data storage.

Tables are created in dependency order — data_series_registry has no foreign
keys so it must exist before the three tables that reference it.  This order
matches the CLAUDE.md Section 4b Sprint 2 migration requirement verbatim.

Revision ID: 001
Revises: (none — first migration)
Create Date: 2026-05-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# Alembic revision identifiers
revision: str = "001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── 1. data_series_registry ───────────────────────────────────────────
    # Every data series loaded by the pipeline registers here with its
    # source type and metadata.  All other tables reference this via FK.
    # Created first because it has no dependencies.
    op.create_table(
        "data_series_registry",
        sa.Column("series_id", sa.String, primary_key=True),
        sa.Column("display_name", sa.String, nullable=False),
        sa.Column("source_type", sa.String, nullable=False),   # excel_provided | yfinance | fred_api | ken_french | constant
        sa.Column("source_detail", postgresql.JSONB, nullable=False),
        sa.Column("frequency", sa.String, nullable=False),     # daily | monthly | quarterly
        sa.Column("date_range_start", sa.Date, nullable=True),
        sa.Column("date_range_end", sa.Date, nullable=True),
        sa.Column("row_count", sa.Integer, nullable=True),
        sa.Column("loaded_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_validated", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("validation_status", sa.String, nullable=True),  # pass | warn | fail
    )

    # ── 2. market_data_monthly ────────────────────────────────────────────
    # One row per calendar month.  Every value column paired with a source
    # column so provenance is unambiguous at the row level — the frontend
    # cannot display a number without knowing where it came from.
    op.create_table(
        "market_data_monthly",
        sa.Column("date", sa.Date, primary_key=True),
        # Return series
        sa.Column("equity_return", sa.Float, nullable=False),
        sa.Column("equity_source", sa.String, sa.ForeignKey("data_series_registry.series_id"), nullable=False),
        sa.Column("ig_return", sa.Float, nullable=False),
        sa.Column("ig_source", sa.String, sa.ForeignKey("data_series_registry.series_id"), nullable=False),
        sa.Column("hy_return", sa.Float, nullable=False),
        sa.Column("hy_source", sa.String, sa.ForeignKey("data_series_registry.series_id"), nullable=False),
        sa.Column("risk_free_rate", sa.Float, nullable=False),
        sa.Column("risk_free_source", sa.String, sa.ForeignKey("data_series_registry.series_id"), nullable=False),
        # Signal series (nullable — not always available for every month)
        sa.Column("vix_month_avg", sa.Float, nullable=True),
        sa.Column("vix_source", sa.String, sa.ForeignKey("data_series_registry.series_id"), nullable=True),
        sa.Column("yield_curve", sa.Float, nullable=True),
        sa.Column("yield_curve_source", sa.String, sa.ForeignKey("data_series_registry.series_id"), nullable=True),
        sa.Column("hy_spread", sa.Float, nullable=True),
        sa.Column("hy_spread_source", sa.String, sa.ForeignKey("data_series_registry.series_id"), nullable=True),
        sa.Column("ig_spread", sa.Float, nullable=True),
        sa.Column("ig_spread_source", sa.String, sa.ForeignKey("data_series_registry.series_id"), nullable=True),
        sa.Column("gdp_growth", sa.Float, nullable=True),
        sa.Column("gdp_source", sa.String, sa.ForeignKey("data_series_registry.series_id"), nullable=True),
        sa.Column("pe_ratio", sa.Float, nullable=True),
        sa.Column("pe_source", sa.String, sa.ForeignKey("data_series_registry.series_id"), nullable=True),
    )

    # ── 3. market_data_daily ──────────────────────────────────────────────
    # Daily returns for momentum and volatility models.  SPY daily comes
    # from yfinance (the only yfinance series); bonds and signals from Excel
    # or FRED.  Separate from monthly because we never mix frequencies in
    # portfolio construction — daily is for signal models only.
    op.create_table(
        "market_data_daily",
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("equity_return", sa.Float, nullable=True),
        sa.Column("equity_source", sa.String, sa.ForeignKey("data_series_registry.series_id"), nullable=True),
        sa.Column("ig_return", sa.Float, nullable=True),
        sa.Column("ig_source", sa.String, sa.ForeignKey("data_series_registry.series_id"), nullable=True),
        sa.Column("hy_return", sa.Float, nullable=True),
        sa.Column("hy_source", sa.String, sa.ForeignKey("data_series_registry.series_id"), nullable=True),
        sa.Column("vix", sa.Float, nullable=True),
        sa.Column("vix_source", sa.String, sa.ForeignKey("data_series_registry.series_id"), nullable=True),
        sa.Column("dgs2", sa.Float, nullable=True),
        sa.Column("dgs2_source", sa.String, sa.ForeignKey("data_series_registry.series_id"), nullable=True),
    )

    # ── 4. data_validation_log ────────────────────────────────────────────
    # Every validation check run by the pipeline is recorded here with its
    # outcome.  This is the audit trail for the Analytical Appendix —
    # graders can see that every sanity check ran and what it found.
    op.create_table(
        "data_validation_log",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("timestamp", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("check_name", sa.String, nullable=False),
        sa.Column("series_id", sa.String, sa.ForeignKey("data_series_registry.series_id"), nullable=True),
        sa.Column("status", sa.String, nullable=False),   # pass | warn | fail
        sa.Column("detail", postgresql.JSONB, nullable=False),
    )


def downgrade() -> None:
    # Drop in reverse dependency order — children before parent.
    op.drop_table("data_validation_log")
    op.drop_table("market_data_daily")
    op.drop_table("market_data_monthly")
    op.drop_table("data_series_registry")
