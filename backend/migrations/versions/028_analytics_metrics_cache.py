"""Analytics metrics cache — pre-computed analytics keyed by data hash.

May 22 2026 — item 7 in the sprint queue (performance audit). The
existing /api/v1/analytics/academic endpoint runs 7 NumPy reductions
on every request — summary statistics, cumulative returns, rolling
correlation, rolling excess return, regime-conditional performance,
drawdown comparison, Carhart factor loadings. Fast on hot path
(maybe 200-500ms) but ALL of this work duplicates on every page load
even though the underlying data (market_data_monthly +
strategy_results_cache) only changes on a fresh ingestion.

This table stores the COMPUTED analytics output keyed by
(data_hash, metric_kind). The refresh fires once when the strategy
cache is written; the endpoint reads a single JSONB row and returns
it. Cold-cache fallback: the endpoint computes inline if the row
hasn't been written yet, so a fresh deploy gracefully serves the
first request that arrives before the refresh hook lands.

This is also the foundation for item 8 — the diversification suite
metrics (correlation matrix, VaR/CVaR, capture ratios, drawdown
duration, crisis performance, MCTR, distribution) all land as
additional rows in this same table with their own metric_kind
strings. The architectural decision the user confirmed earlier:
"new analytics_metrics_cache table, one row per metric".

Schema notes:
  - data_hash + metric_kind UNIQUE — one row per (data, metric)
    pair. An upsert on refresh replaces the old payload.
  - payload JSONB — the full pre-computed structure. The endpoint
    reads it verbatim and returns it through the response.
  - computed_at — timestamp the row was written; surfaced on the
    response so the user knows how fresh the analytics are.
  - source TEXT — informational label naming what wrote the row
    (e.g. 'academic_analytics_refresh', 'frontier_refresh') for
    debugging when a row's payload looks off.
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "028"
down_revision: str | None = "027"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "analytics_metrics_cache",
        sa.Column("id", sa.BigInteger(), primary_key=True,
                  autoincrement=True),
        sa.Column("data_hash", sa.String(64), nullable=False,
                  comment="strategy_hash from strategy_results_cache "
                          "at the time of refresh"),
        sa.Column("metric_kind", sa.String(50), nullable=False,
                  comment="e.g. 'academic_analytics', "
                          "'efficient_frontier', 'correlation_matrix'"),
        sa.Column("payload", sa.JSON(), nullable=False,
                  comment="Pre-computed metric payload — served "
                          "verbatim through the GET endpoint"),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("source", sa.String(100), nullable=True,
                  comment="Informational — which writer fired the "
                          "row (for debugging)"),
    )
    op.create_unique_constraint(
        "uq_analytics_metrics_cache_hash_kind",
        "analytics_metrics_cache",
        ["data_hash", "metric_kind"],
    )
    # Primary read path: by (data_hash, metric_kind). The unique
    # constraint above doubles as the index covering this.
    # Secondary read path: latest by metric_kind (for the cold-deploy
    # case where the latest data_hash may not match the row's hash
    # yet because the refresh hook is still in flight).
    op.create_index(
        "ix_analytics_metrics_cache_kind_computed",
        "analytics_metrics_cache",
        ["metric_kind", sa.text("computed_at DESC")],
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
        "version": 47,
        "released_at": datetime(2026, 5, 22, tzinfo=timezone.utc),
        "title": "Analytics metrics cache — pre-computed on data-hash change",
        "description": (
            "Analytics endpoints (academic analytics, efficient "
            "frontier, sensitivity, plus the upcoming "
            "diversification suite) now read pre-computed payloads "
            "from analytics_metrics_cache keyed by data_hash. The "
            "compute fires once when the strategy cache is written; "
            "page loads serve a single JSONB row. The Analytics "
            "page latency drops from 200-500ms NumPy compute to a "
            "single indexed lookup."
        ),
        "academic_rationale": (
            "The Forest Capital and FNA 670 panel demos depend on "
            "snappy navigation between Analytics / Dashboard / "
            "Statistical Evidence. A page that takes 500ms to load "
            "looks unfinished even when the data and analysis are "
            "right. Pre-computing the analytics removes the on-load "
            "compute and presents the system as production-grade."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 47")
    op.drop_index("ix_analytics_metrics_cache_kind_computed",
                  table_name="analytics_metrics_cache")
    op.drop_constraint("uq_analytics_metrics_cache_hash_kind",
                       "analytics_metrics_cache", type_="unique")
    op.drop_table("analytics_metrics_cache")
