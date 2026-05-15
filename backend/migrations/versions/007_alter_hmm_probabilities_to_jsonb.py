"""007 — ALTER regime_signals_cache.hmm_probabilities ARRAY(Float) -> JSONB.

Migration 002 created hmm_probabilities as ARRAY(Float), assuming the HMM
detector would emit a positional list [p_bull, p_bear, p_transition].
The actual detector (tools/regime_detector.py:352) emits a DICT keyed by
state label — {"BULL": 0.82, "BEAR": 0.12, "TRANSITION": 0.06} — which
is more useful for downstream display ("BULL: 82%" reads better than
indexing by position).

Production traces from set_regime_cache showed asyncpg raising
"sized iterable expected got dict" when binding the dict to the
ARRAY-typed parameter. JSONB stores the dict natively, comes back as
a Python dict on read, and supports first-class PostgreSQL queries
(e.g. `WHERE hmm_probabilities->>'BULL' > '0.5'`) for any future
analytics.

The cache has a 15-minute TTL and rebuilds continuously, so the
migration's data-loss window is at most one regime fetch.
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "007"
down_revision: str | None = "006"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # ARRAY(Float) → JSONB. There's no safe in-place cast since the
    # detector now writes dicts not arrays; existing rows (if any) would
    # convert to JSON arrays, which is the wrong shape for the new code.
    # We null + alter — the 15-minute TTL means the next regime fetch
    # repopulates the row.
    op.execute("UPDATE regime_signals_cache SET hmm_probabilities = NULL")
    op.alter_column(
        "regime_signals_cache",
        "hmm_probabilities",
        existing_type=postgresql.ARRAY(sa.Float()),
        type_=postgresql.JSONB(),
        existing_nullable=True,
        postgresql_using="NULL::jsonb",
    )


def downgrade() -> None:
    # JSONB → ARRAY(Float). Existing JSONB rows are dicts (not lists)
    # so the cast can't succeed — null them first. Same TTL argument
    # applies: the cache rebuilds within 15 minutes.
    op.execute("UPDATE regime_signals_cache SET hmm_probabilities = NULL")
    op.alter_column(
        "regime_signals_cache",
        "hmm_probabilities",
        existing_type=postgresql.JSONB(),
        type_=postgresql.ARRAY(sa.Float()),
        existing_nullable=True,
        postgresql_using="NULL::float8[]",
    )
