"""platform_config -- single-row config table for the submission freeze.

June 21 2026. Layer 4 of the deterministic substitution architecture
adds a *submission freeze* -- a sysadmin-flipped flag that locks
document generators to a frozen `data_hash` for the FNA 670 final
submission deadline (June 30 2026). On submission day the team
generates the brief / deck / appendix, verifies each against the
strategy_results_cache, then activates the freeze. Documents
generated thereafter read from the cache via the frozen hash so the
exported PDFs / DOCX / PPTX never drift from what was submitted --
even though the live platform continues to update normally (regime
detector, CIO card, daily digest, Investment Outlook all keep
reading the live hash).

A single key/value table is the lightest possible surface for one
boolean-flag-with-metadata. Future config knobs land here too --
each gets its own key, JSONB payload, idempotent UPSERT.

Schema:
  key         VARCHAR PRIMARY KEY -- e.g. "submission_freeze"
  value       JSONB NOT NULL      -- arbitrary payload per key
  updated_at  TIMESTAMPTZ         -- defaults to NOW() on write

Seeded with the "submission_freeze" row in the OFF state. Activation
flips active=true and records freeze_hash / freeze_date /
activated_by / activated_at; deactivation clears the hash and date.
The fail-open contract: a missing row OR an unreadable DB reports
{active: False, freeze_hash: None} so a cold deploy / database
outage never accidentally locks document generation.

Revision ID: 058
Revises: 056
Create Date: 2026-06-21
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "058"
# Revises 056 (story_plans). Layer 3a (#350) adds migration 057 on a
# parallel branch -- when that PR merges, alembic's merge mechanic
# (or a manual rebase) reconciles the chain. The freeze module is
# independent of the value_manifest / export_verification columns
# 057 adds; it reads editor_drafts opportunistically and falls back
# when the Layer 3a columns are absent.
down_revision: str | None = "056"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "platform_config",
        sa.Column("key", sa.String(), primary_key=True,
                  comment="Config key -- e.g. submission_freeze. "
                          "One row per knob."),
        sa.Column("value",
                  postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False,
                  comment="JSON payload for this config key. Shape "
                          "is per-key; submission_freeze carries "
                          "{active, freeze_hash, freeze_date, "
                          "activated_by, activated_at}."),
        sa.Column("updated_at",
                  sa.DateTime(timezone=True),
                  server_default=sa.text("now()"),
                  nullable=False,
                  comment="Last UPSERT timestamp. Driven by the "
                          "DB clock so the application-side write "
                          "never races with the audit log."),
    )

    # Seed the submission_freeze row in the OFF state. ON CONFLICT
    # DO NOTHING so re-running the migration (or running it after a
    # manual seed) never overwrites a live freeze.
    op.execute(sa.text(
        "INSERT INTO platform_config (key, value) "
        "VALUES (:k, CAST(:v AS JSONB)) "
        "ON CONFLICT (key) DO NOTHING"
    ).bindparams(
        k="submission_freeze",
        v='{"active": false, "freeze_hash": null, '
          '"freeze_date": null, "activated_by": null, '
          '"activated_at": null}',
    ))

    # Changelog row -- consistent with the pattern every other
    # migration in this series uses (PR #331 + #341 fix). Use a
    # native datetime for released_at (NEVER sa.text("now()") -- the
    # changelog row's released_at is an int-coerced ms field in some
    # downstream consumers, and the migration must own that value).
    # v=78 deliberately skips v=77, which is reserved for Layer 3a
    # (PR #350)'s 057_export_verification migration.
    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL) "
        "ON CONFLICT (version) DO NOTHING"
    ).bindparams(
        v=78,
        rel=datetime.now(timezone.utc),
        t="Submission freeze for the FNA 670 final deadline",
        d=(
            "A platform_config row gates the document generators on "
            "a frozen data_hash for the June 30 2026 submission. "
            "When the freeze is active the brief, deck, and appendix "
            "read the strategy_results_cache at the frozen hash so "
            "the exported deliverables never drift from what was "
            "submitted. The live Investment Outlook, CIO "
            "recommendation, regime detector, and daily digest all "
            "continue reading the live hash -- the freeze isolates "
            "document generation only."),
        a=(
            "Layer 4 of the deterministic substitution architecture. "
            "Layers 1-3 eliminated drift at generation time, "
            "extension time, and export time. Layer 4 closes the "
            "submission-day loop: a one-flag toggle prevents a "
            "between-generate-and-submit data ingest from silently "
            "changing the figures in the exported documents."),
    ))


def downgrade() -> None:
    op.drop_table("platform_config")
