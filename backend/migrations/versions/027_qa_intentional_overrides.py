"""QA intentional-override audit trail — qa_intentional_overrides.

May 22 2026 — companion table for the QA Action Required UI commit
(f96d897). The Mark as Intentional button on a methodology_decision
WARN finding records a permanent override: the team has reviewed the
finding and confirmed the current behaviour is intentional, not a
defect. The override SHOULD outlive the audit run that surfaced the
WARN — a check marked intentional in May should still read as
intentional when a fresh audit runs in June.

Joining onto the per-audit-run findings would couple the override to
that one audit row. Splitting it into its own table keyed by
check_id makes the override a property of the CHECK, not of any
single audit run — the QA panel can surface "previously marked
intentional" on a fresh audit immediately.

Schema notes:
  - check_id is the human key (e.g. 'P03', 'D02'). Indexed UNIQUE
    so a second Mark-Intentional click on the same check
    UPDATES the existing row rather than producing a duplicate.
  - marked_by is the sysadmin's email at the time the override was
    recorded. Kept as a string (no FK into platform_users) so a
    deactivated user's overrides remain attributable.
  - note is the optional free-text justification — what the team
    decided and why.
  - audit_run_hash captures the strategy_hash the audit was running
    against when the override was first recorded, so the audit trail
    shows which version of the data the team examined when they
    decided the WARN was intentional. NOT a foreign key — the
    strategy_results_cache row may rotate out before this override
    is reviewed.

The Flag for Fix path does NOT write to this table — it routes
straight into the existing triage_report_items system. Only Mark as
Intentional surfaces here.
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "027"
down_revision: str | None = "026"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "qa_intentional_overrides",
        sa.Column("id", sa.BigInteger(), primary_key=True,
                  autoincrement=True),
        sa.Column("check_id", sa.String(20), nullable=False,
                  comment="QA checklist id (e.g. 'P03', 'D02')"),
        sa.Column("marked_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("marked_by", sa.String(255), nullable=False,
                  comment="Sysadmin email at time of override"),
        sa.Column("note", sa.Text(), nullable=True,
                  comment="Optional free-text justification"),
        sa.Column("audit_run_hash", sa.String(64), nullable=True,
                  comment="strategy_hash on the audit run when "
                          "the override was recorded; for audit "
                          "trail traceability"),
    )

    # Uniqueness — a single override per check_id. A second Mark
    # Intentional UPDATEs the existing row rather than creating
    # a duplicate. Mirrors the migration 026 pattern of using
    # uniqueness as the idempotency guard.
    op.create_unique_constraint(
        "uq_qa_intentional_overrides_check",
        "qa_intentional_overrides",
        ["check_id"],
    )

    # Primary read pattern: enumerate all overrides for the latest
    # audit so the QA panel can render the "Confirmed intentional"
    # badge on each marked check. Ordered most-recent first.
    op.create_index(
        "ix_qa_intentional_overrides_marked_at",
        "qa_intentional_overrides",
        [sa.text("marked_at DESC")],
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
        "version": 46,
        "released_at": datetime(2026, 5, 22, tzinfo=timezone.utc),
        "title": "QA intentional-override audit trail",
        "description": (
            "Companion to the QA Action Required UI: when a "
            "methodology_decision WARN finding is reviewed and "
            "confirmed as intentional design, the override is "
            "recorded in qa_intentional_overrides and surfaces on "
            "every subsequent audit. The override outlives the "
            "audit run that surfaced the WARN — a check marked "
            "intentional in May still reads as intentional when a "
            "fresh audit runs in June."
        ),
        "academic_rationale": (
            "Several QA findings are ambiguous between 'intentional "
            "methodology' and 'accidental bug' (P03 transaction "
            "costs is the canonical example). Recording the team's "
            "judgement against each ambiguous finding produces an "
            "auditable trail of methodology decisions — directly "
            "supporting the Analytical Appendix's traceability "
            "narrative and pre-empting graders' questions about why "
            "a given WARN remains in the audit report."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 46")
    op.drop_index("ix_qa_intentional_overrides_marked_at",
                  table_name="qa_intentional_overrides")
    op.drop_constraint("uq_qa_intentional_overrides_check",
                       "qa_intentional_overrides", type_="unique")
    op.drop_table("qa_intentional_overrides")
