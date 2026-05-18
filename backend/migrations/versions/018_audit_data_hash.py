"""Add data_hash to audit_runs — smart audit caching.

The statistical audit now stores a lightweight fingerprint of the data
it verified (market_data_monthly / ff_factors_monthly /
strategy_results_cache row counts and newest dates). is_audit_current()
compares the live fingerprint to the last completed run's data_hash, so
the audit re-runs only when the underlying data has actually changed.

Revision ID: 018
Revises: 017
Create Date: 2026-05-18
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision: str = "018"
down_revision: str | None = "017"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "audit_runs",
        sa.Column("data_hash", sa.String(64), nullable=True,
                  comment="lightweight data fingerprint — smart audit caching"),
    )

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
        "version": 37,
        "released_at": now,
        "title": "Smart Audit Caching",
        "description": (
            "The statistical and methodology audits now re-run only when "
            "the underlying market data actually changes. A cached, "
            "verified result is served instantly while the data is "
            "unchanged, and audits fire automatically on a data update."
        ),
        "academic_rationale": (
            "Re-running the independent audit on unchanged data spends "
            "the Opus model budget and tells the team nothing new. "
            "Caching the verified result keeps the audit evidence current "
            "for the Analytical Appendix while reserving a fresh run for "
            "the moments it matters — a data update, or a live "
            "presentation of the audit to Forest Capital."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 37")
    op.drop_column("audit_runs", "data_hash")
