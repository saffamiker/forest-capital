"""editor_drafts.data_hash -- backfill to Hash A so Draft + Live Hash agree.

June 25 2026. Migration 063 backfilled editor_drafts.data_hash with the
canonical production string f2e87dec7dcabe71. That value was Hash B --
tools.cache._compute_data_hash(n_rows, last_date, n_strategies=10) at the
time of rollout. The Light Refresh status table reads the Live Hash
column from tools.audit_assembler.current_data_hash (Hash A: SHA256-16
of market_data_monthly + ff_factors_monthly metadata) and the Draft
Hash column from editor_drafts.data_hash. Two different formulas; same
underlying data; mathematically guaranteed mismatch on every row. Every
current draft permanently rendered 'Stale' in the panel regardless of
how many refreshes the team ran.

This migration:

  1. Computes the live Hash A by querying market_data_monthly +
     ff_factors_monthly the same way tools.cache.get_data_status does
     (COUNT, MAX(date)) and assembling the canonical string the same
     way tools.audit_assembler.current_data_hash does
     (name:row_count:max_date:None per table, sorted, joined by '|',
     SHA256-16). Inlined here rather than imported from the runtime
     module so the migration can run in alembic's sync connection
     without the asyncio runtime.

  2. UPDATEs every editor_drafts row whose is_current=true and
     is_deleted=false, setting data_hash to the computed Hash A.
     Historical rows stay on whatever they had (NULL, Hash B, etc.) --
     the chip's 'No hash' state is the correct signal there.

  3. Records the rollout in changelog with an ON CONFLICT DO NOTHING
     guard (the standard pattern from migrations 061-063).

Application-side change riding this migration: main.py's Light Refresh
endpoint (POST /api/v1/data/light-refresh) now calls
current_data_hash() (Hash A) instead of _compute_data_hash (Hash B)
so every subsequent refresh continues to stamp Hash A. _compute_data_hash
itself is preserved -- it is still called by other paths (admin
warm-caches, etc.) and may continue to live alongside Hash A; this PR
only unbreaks the Light Refresh + tile path.

The migration is FAIL-OPEN. If the source tables can't be read or are
empty, the backfill is skipped (the current data_hash values stay as
they were); the application-side change in main.py is the load-bearing
fix and the migration is the accelerator that makes the chips flip
green on first deploy without waiting for a Light Refresh click.

Revision ID: 064
Revises: 063
Create Date: 2026-06-25
"""
from __future__ import annotations

import hashlib

from alembic import op
import sqlalchemy as sa


revision: str = "064"
down_revision: str = "063"
branch_labels = None
depends_on = None


def _compute_hash_a_sync(conn) -> str | None:
    """Inline sync re-implementation of audit_assembler.current_data_hash
    for use inside an alembic migration. Returns the SHA256-16 of the
    canonical 'name:row_count:max_date:None'-per-table string, or None
    when the source tables can't be read."""
    try:
        parts: list[str] = []
        # market_data_monthly: keyed by `date`. get_data_status
        # carries last_updated=None for this table, so the
        # canonical string literally contains the string 'None'.
        row = conn.execute(sa.text(
            "SELECT COUNT(*), MAX(date) FROM market_data_monthly"
        )).fetchone()
        if row and row[0]:
            cnt, mx = row[0], row[1]
            # str(date) matches get_data_status's str(mx) shape.
            parts.append(
                f"market_data_monthly:{int(cnt)}:{str(mx)}:None")

        # ff_factors_monthly: keyed by `yyyymm` integer; the
        # canonical max_date is the last day of that month.
        row = conn.execute(sa.text(
            "SELECT COUNT(*), MAX(yyyymm) FROM ff_factors_monthly"
        )).fetchone()
        if row and row[0]:
            cnt, ym = row[0], row[1]
            if ym is not None:
                y, m = int(ym) // 100, int(ym) % 100
                # First of next month minus one day = last day of
                # the captured month. Matches _ym_to_date in
                # tools/cache.get_data_status.
                from datetime import datetime as _dt
                from datetime import timedelta as _td
                from datetime import timezone as _tz
                first_next = _dt(
                    y + (m // 12), (m % 12) + 1, 1,
                    tzinfo=_tz.utc)
                last_day = (first_next - _td(days=1)).date()
                parts.append(
                    f"ff_factors_monthly:{int(cnt)}:{last_day}:None")

        if not parts:
            return None
        parts.sort()
        canonical = "|".join(parts)
        return hashlib.sha256(
            canonical.encode("utf-8")).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        # Migration runs in environments where the source tables
        # may not be present (a brand-new database spun up by CI
        # before market_data is loaded). Fail open -- the
        # application's Light Refresh change still does the right
        # thing on the first user click post-deploy.
        return None


def upgrade() -> None:
    conn = op.get_bind()

    hash_a = _compute_hash_a_sync(conn)
    if hash_a:
        # Backfill ONLY the current drafts. Historical rows keep
        # their existing data_hash (NULL / Hash B / whatever);
        # the chip's third state ('No hash / Stale') is the
        # correct signal against those.
        conn.execute(sa.text(
            "UPDATE editor_drafts "
            "SET data_hash = :h "
            "WHERE is_current = true "
            "AND is_deleted = false "
            "AND data_hash IS DISTINCT FROM :h"
        ), {"h": hash_a})

    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, NOW(), :t, :d, :a, NULL) "
        "ON CONFLICT (version) DO NOTHING"
    ).bindparams(
        v=64,
        t="Unify draft data_hash on Hash A",
        d=(
            "Backfills editor_drafts.data_hash on current rows to "
            "the canonical Hash A value (audit_assembler."
            "current_data_hash). Pairs with the Light Refresh "
            "endpoint change so the Draft Hash + Live Hash columns "
            "read the same formula and the panel reports Current "
            "rather than Stale on a fresh refresh."),
        a=(
            "Hash A is the canonical platform-state fingerprint by "
            "design intent -- derived-cache churn does NOT "
            "invalidate it (audit_assembler comment block, June 22 "
            "2026). The previous Hash B was hardcoded to "
            "n_strategies=10, making it brittle to any future "
            "change in the strategy count."),
    ))


def downgrade() -> None:
    # No-op downgrade -- the backfill cannot be reversed without
    # knowing what each row's previous data_hash was, and the
    # migration's purpose is forward-only (chip-state correctness).
    # Dropping the column itself is migration 063's downgrade
    # concern, not ours.
    pass
