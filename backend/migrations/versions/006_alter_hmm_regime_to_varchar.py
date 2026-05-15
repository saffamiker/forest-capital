"""006 — ALTER regime_signals_cache.hmm_regime INTEGER -> VARCHAR(20).

Migration 002 created regime_signals_cache.hmm_regime as INTEGER, but
the regime detector emits string labels like 'BULL', 'BEAR', 'TRANSITION'.
Writes failed in production with InvalidTextRepresentation. This migration
widens the column so the regime cache can store the actual label strings
the detector produces.

Down-revision: we explicitly do NOT cast existing INTEGER values back to
strings on downgrade — instead, downgrade nulls the column for any row
that holds a non-numeric value. The cache is rebuilt every 15 minutes so
nulling here is safe; the next regime fetch will repopulate.
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "006"
down_revision: str | None = "005"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Widen hmm_regime to VARCHAR(20). Existing INTEGER values (0/1/2) are
    # cast to their string representation via USING — preserves any rows
    # that landed in the cache before this fix. The cache is short-lived
    # (15-min TTL) so even without USING the downside would be transient,
    # but explicit casts keep prod stable through the migration window.
    op.alter_column(
        "regime_signals_cache",
        "hmm_regime",
        existing_type=sa.Integer(),
        type_=sa.String(length=20),
        existing_nullable=True,
        postgresql_using="hmm_regime::text",
    )


def downgrade() -> None:
    # Reverse: VARCHAR(20) -> INTEGER. String labels like 'BULL' cannot
    # cast to int — we null those rows first so the column type change
    # succeeds without IntegrityError. The cache rebuilds on the next
    # /api/regime/current request.
    op.execute(
        "UPDATE regime_signals_cache "
        "SET hmm_regime = NULL "
        "WHERE hmm_regime !~ '^-?[0-9]+$'"
    )
    op.alter_column(
        "regime_signals_cache",
        "hmm_regime",
        existing_type=sa.String(length=20),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="NULLIF(hmm_regime, '')::integer",
    )
