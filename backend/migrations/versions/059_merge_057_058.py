"""Merge migration -- collapse 057 + 058 back into one head.

Migrations 057 (editor_drafts.value_manifest + export_verification
columns, Layer 3a / PR #350) and 058 (platform_config table,
Layer 4 / PR #352) were authored in parallel and both branched
from 056. After both shipped, `alembic heads` returned two heads
which blocks every subsequent migration -- alembic refuses to
upgrade past a multi-head state without an explicit merge.

This migration is the merge: empty upgrade / downgrade body, both
057 and 058 listed as parents, restoring a single linear head
(059) for future migrations to build on.

No schema changes here. The changelog row documents the merge for
the operator audit trail so the version sequence in the Admin ->
Changelog UI stays contiguous.

Revision ID: 059
Revises: 057, 058
Create Date: 2026-06-21
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "059"
down_revision: tuple[str, str] = ("057", "058")
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL) "
        "ON CONFLICT (version) DO NOTHING"
    ).bindparams(
        v=79,
        rel=datetime.now(timezone.utc),
        t="Merge migration heads (057 + 058)",
        d=(
            "Layer 3a (export-verification columns, PR #350) and "
            "Layer 4 (platform_config table, PR #352) both "
            "branched from migration 056 and shipped in parallel, "
            "leaving alembic in a multi-head state. This merge "
            "migration restores a single linear head so future "
            "migrations can build on the combined schema."),
        a=(
            "Operationally invisible -- no schema changes. Resolves "
            "an alembic state divergence introduced by parallel "
            "Layer 3a / Layer 4 development. Future migrations now "
            "have a single parent (059) instead of competing "
            "candidates."),
    ))


def downgrade() -> None:
    pass
