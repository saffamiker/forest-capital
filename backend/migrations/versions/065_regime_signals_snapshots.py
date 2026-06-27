"""regime_signals_snapshots: hash-keyed snapshots of regime_signals_cache

The regime_signals_cache table is a single-row 15-min TTL cache by design
(LIVE platform construct, no data_hash column). Document generators
under submission freeze need a HASH-AWARE read so the freeze-locked
deliverable doesn't leak post-freeze regime signals into watchpoint
tokens ({{VIX_CURRENT}}, {{YIELD_CURVE_CURRENT}}, {{CREDIT_SPREAD_CURRENT}},
{{EQUITY_TREND_CURRENT}}, {{ESS_CURRENT}}).

This migration adds a hash-keyed snapshot table with EXPLICIT COLUMNS
(same column set as regime_signals_cache minus TTL fields, plus
data_hash + snapshotted_at). Explicit columns -- not opaque JSON --
so operator INSERTs are simple SQL and direct column queries work
(e.g. 'select vix_level from regime_signals_snapshots where
data_hash = ...').

On freeze activation (submission_freeze.set_freeze_config(
active=True, freeze_hash=...)), the caller passes the current
regime_signals_cache row into snapshot_regime_signals_for_hash(
freeze_hash, signals_row). Document generators under freeze read
the snapshot for the active freeze hash via
get_regime_snapshot_for_hash; on miss they set live_signals=None
(watchpoint tokens render em-dash, NOT the deprecated
[DATA PENDING] placeholder).

Revision ID: 065_regime_signals_snapshots
Revises: 064_unify_draft_hash_to_hash_a
Create Date: 2026-06-27
"""
from alembic import op
import sqlalchemy as sa


revision = "065_regime_signals_snapshots"
down_revision = "064_unify_draft_hash_to_hash_a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "regime_signals_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True,
                  autoincrement=True),
        sa.Column("data_hash", sa.String(length=64),
                  nullable=False, unique=True),
        # Regime classification (same columns as regime_signals_cache,
        # minus TTL fields).
        sa.Column("threshold_regime", sa.String(length=32),
                  nullable=True),
        sa.Column("hmm_regime", sa.String(length=32), nullable=True),
        sa.Column("hmm_probabilities", sa.JSON(), nullable=True),
        sa.Column("regimes_agree", sa.Boolean(), nullable=True),
        # Macro factor levels.
        sa.Column("vix_level", sa.Float(), nullable=True),
        sa.Column("yield_curve_slope", sa.Float(), nullable=True),
        sa.Column("credit_spread", sa.Float(), nullable=True),
        sa.Column("equity_trend", sa.Float(), nullable=True),
        # Correlation regime markers (used by section narrative).
        sa.Column("pre_2022_avg_correlation", sa.Float(),
                  nullable=True),
        sa.Column("post_2022_avg_correlation", sa.Float(),
                  nullable=True),
        # When the snapshot was captured -- distinct from the
        # fetched_at on the live regime_signals_cache row.
        sa.Column(
            "snapshotted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()")),
    )
    # Lookup index on data_hash is implicit via the UNIQUE constraint;
    # add a snapshotted_at index for housekeeping queries (e.g.
    # 'list snapshots older than 90 days for cleanup').
    op.create_index(
        "ix_regime_signals_snapshots_snapshotted_at",
        "regime_signals_snapshots",
        ["snapshotted_at"])


def downgrade() -> None:
    op.drop_index(
        "ix_regime_signals_snapshots_snapshotted_at",
        table_name="regime_signals_snapshots")
    op.drop_table("regime_signals_snapshots")
