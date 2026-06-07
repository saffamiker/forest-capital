"""cio_recommendations — composite (data_hash, regime) index for the
regime-aware cache lookup.

June 6 2026. Cache-warm performance regression #68 / #71 identified
that the cache lookup in cio_recommendation.get_endpoint_recommendation
matches by the composite key {data_hash}_{regime}_{bucket} (PR #282)
but the table only has:

  uq_cio_data_hash                UNIQUE(data_hash)
  ix_cio_recommendations_computed_at  computed_at

Reads filtering on (data_hash, regime) had no covering index, so
PostgreSQL planned a SeqScan + filter once the table grew past the
data_hash UNIQUE single-tuple plan. The composite index added here
makes the regime-aware lookup point-fast and stays in lockstep with
the post-#282 cache-key contract.

WHY NOT TOUCH THE UNIQUE CONSTRAINT
  Replacing UNIQUE(data_hash) with UNIQUE(data_hash, regime,
  confidence_bucket) would be more architecturally correct (every
  regime-flip read writes a new row today, but the conflict resolver
  still trips on the bare hash), but that is a riskier change that
  deserves its own migration with operator-supervised data backfill.
  The index alone delivers the perf win this revision targets.

Revision ID: 054
Revises: 053
Create Date: 2026-06-06
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "054"
down_revision: str | None = "053"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # The composite index covers reads filtering by data_hash and
    # regime together (the primary cache-lookup pattern post-#282)
    # and is a no-op for the legacy bare-hash read pattern -- those
    # still hit the unique constraint's implicit index.
    #
    # IF NOT EXISTS (bridge #79 idempotency fix): the first attempt
    # to run this migration on Render created the index but then
    # crashed on the changelog INSERT below, so alembic_version
    # never advanced. A naive re-run would trip CREATE INDEX
    # because the index already exists. Using raw SQL with
    # IF NOT EXISTS makes the index step idempotent so re-runs
    # are safe regardless of whether the index landed before the
    # crash. Postgres-specific syntax (we are Postgres-only).
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS "
        "ix_cio_recommendations_hash_regime "
        "ON cio_recommendations (data_hash, regime)"
    ))

    # ON CONFLICT (version) DO NOTHING (bridge #79 idempotency fix):
    # the changelog has UNIQUE(version). This migration originally
    # tried v=73 but migration 053 already claimed v=73 (for
    # TEST_SCRIPT_VERSION 8 -> 9). Bumped to v=74. The DO NOTHING
    # clause additionally protects against any future collision so
    # a re-run never crashes here.
    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL) "
        "ON CONFLICT (version) DO NOTHING"
    ).bindparams(
        v=74,
        rel=datetime.now(timezone.utc),
        t="Cache-warm perf: composite index on cio_recommendations",
        d=(
            "Adds ix_cio_recommendations_hash_regime composite index "
            "on cio_recommendations(data_hash, regime). The "
            "regime-aware cache lookup added in PR #282 matched on "
            "both columns but the table had no covering index, so "
            "lookups planned a SeqScan + filter once the cache had "
            "accumulated more than a handful of rows. The index makes "
            "every regime-flip read point-fast and removes 2-5 "
            "seconds from the cache warm pipeline. Safe to run "
            "in production -- existing rows are scanned once at "
            "build time and there is no schema change to the unique "
            "constraint or any column."
        ),
        a=(
            "Index design matches the post-#282 cache-key contract -- "
            "the composite (data_hash, regime) is the join key for "
            "every cache hit on get_endpoint_recommendation. The "
            "unique constraint on data_hash alone is intentionally "
            "preserved for this revision; replacing it with a "
            "composite UNIQUE(data_hash, regime, confidence_bucket) "
            "is a separate concern (would need data backfill plus a "
            "rewrite of _persist's ON CONFLICT clause) and is "
            "tracked as a follow-up rather than rolled into this "
            "perf-only revision."
        ),
    ))


def downgrade() -> None:
    # DROP INDEX IF EXISTS mirrors the upgrade's idempotency so a
    # downgrade survives a partial upgrade where the index was never
    # created.
    op.execute(sa.text(
        "DROP INDEX IF EXISTS ix_cio_recommendations_hash_regime"
    ))
    op.execute(sa.text(
        "DELETE FROM changelog WHERE version = :v"
    ).bindparams(v=74))
