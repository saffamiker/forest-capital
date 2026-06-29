"""editor_drafts.value_manifest + export_verification (Layer 3).

June 21 2026. The numeric substitution architecture (PR #347 +
#349) replaces raw figures in generated documents with placeholder
tokens that the platform substitutes against the verified strategy
cache. Layer 3 adds export-time verification: after the user
clicks Download, the exported text is scanned for every value the
substitution table produced and any drift (manual edit, stale
cache, formatting corruption) is flagged before the file leaves
the platform.

Two new nullable JSONB columns on editor_drafts:

  value_manifest
    Snapshot of every numeric value the substitution table
    produced at generation time, with provenance:
      {
        "1.24": {"token": "{{OOS_SHARPE_BLEND}}",
                  "data_hash": "c421fb89...",
                  "generated_at": "2026-06-21T..."},
        "0.73": {"token": "{{OOS_SHARPE_BENCHMARK}}", ...},
        ...
      }
    Built once per generation by tools.numeric_substitution
    .build_value_manifest. The export-time check uses this as
    the authoritative reference for what every number in the
    document should be.

  export_verification
    Result of the most recent verify_export_against_cache run.
    Shape:
      {
        "passed": true | false,
        "warnings": [...],
        "errors": [...],
        "data_hash_match": true | false,
        "verified_at": "2026-06-21T...",
        "document_type": "executive_brief"
      }
    Stored after each download so a Reports-page badge can show
    the current verification state without re-running the check.

Nullable on both -- historical drafts (pre-Layer-3) carry NULL,
and the verification path short-circuits when value_manifest is
absent (a draft from before this column existed shouldn't fail
verification just because no manifest was recorded). No indexes
needed -- both columns are read on draft load only, never used
in a WHERE filter.

Revision ID: 057
Revises: 056
Create Date: 2026-06-21
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "057"
down_revision: str | None = "056"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "editor_drafts",
        sa.Column("value_manifest",
                  postgresql.JSONB(astext_type=sa.Text()),
                  nullable=True,
                  comment="Snapshot of every numeric value the "
                          "substitution table produced at "
                          "generation time, with provenance "
                          "(token, data_hash, generated_at). "
                          "NULL for drafts created before "
                          "Layer-3 shipped."),
    )
    op.add_column(
        "editor_drafts",
        sa.Column("export_verification",
                  postgresql.JSONB(astext_type=sa.Text()),
                  nullable=True,
                  comment="Result of the most recent "
                          "verify_export_against_cache run on "
                          "this draft. Shape: "
                          "{passed, warnings, errors, "
                          "data_hash_match, verified_at, "
                          "document_type}. NULL when the draft "
                          "has not yet been exported."),
    )

    # Changelog row -- consistent with the pattern every other
    # migration in this series uses (PR #331 + #341 fix).
    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL) "
        "ON CONFLICT (version) DO NOTHING"
    ).bindparams(
        v=77,
        rel=datetime.now(timezone.utc),
        t="Export-time verification on editor drafts",
        d=(
            "Two JSONB columns on editor_drafts (value_manifest, "
            "export_verification) capture the substitution-table "
            "snapshot at generation time and the verification "
            "result at export time. The pre-submission flow uses "
            "these to confirm the exported document matches the "
            "cache before submission."),
        a=(
            "Layer-3 of the deterministic substitution "
            "architecture. Layer 1 (#347) eliminated drift at "
            "generation time. Layer 2 (#349) extended substitution "
            "to deck + appendix and added the cross-deliverable "
            "consistency check. Layer 3 closes the loop at export "
            "time -- a manual edit in the editor that changes "
            "'1.24' to '1.23' is caught before the file leaves "
            "the platform."),
    ))


def downgrade() -> None:
    op.drop_column("editor_drafts", "export_verification")
    op.drop_column("editor_drafts", "value_manifest")
