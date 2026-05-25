"""audit_acknowledgements — run-independent ack storage + finding metadata.

Schema-only migration for the audit-acknowledgement workstream
(May 28 2026). Adds:

  1. audit_acknowledgements — a new table that stores ONE canonical
     acknowledgement per stable check identity, independent of
     audit_run lifetime. A re-run produces fresh audit_findings rows
     with resolved=false by default; the next-PR auto-carry pass
     consults this table to re-apply the team's prior judgment when
     the underlying numeric value has not materially changed
     (within 0.5% relative tolerance). The previous design — the
     ack lived on the audit_findings row itself via resolved +
     resolution_note — caused every re-run to silently drop every
     prior acknowledgement.

  2. audit_findings.auto_acknowledged — boolean flag set true by
     the next-PR carry pass when a new finding is auto-resolved
     from a prior ack. Distinguishes a fresh team-typed ack from
     a machine-applied carry so the UI and PDF can label them
     differently (the team wants to see what they reviewed THIS
     audit vs. what was carried).

  3. audit_findings.resolved_by — captures the reviewer's email at
     ack time. The current resolve_finding endpoint (audit_engine.py)
     has the session email but doesn't write it. The statistical
     audit PDF disclosures section (next PR) needs this to render
     the "Reviewed by:" line.

  4. audit_findings.resolved_at — captures the ack timestamp.
     Mirrors resolved_by — the resolved boolean exists today but
     the WHEN does not. PDF + auto-carry both need it.

NO CODE-PATH CHANGES IN THIS MIGRATION. The endpoints that read /
write audit_findings continue to work unchanged — the three new
columns default to NULL / false, so reads see the same shape they
saw before. The audit-ack workstream's later PRs populate the new
fields once the schema is in place.

Revision ID: 044
Revises: 043
Create Date: 2026-05-28
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "044"
down_revision: str | None = "043"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── 1. audit_acknowledgements ───────────────────────────────────────
    #
    # check_id is the stable identifier across runs. The next-PR
    # code path composes it from the audit_finding row at insert
    # time (e.g. "L2.cagr.EQUITY" from layer/metric/strategy) and
    # re-composes it on each new finding to look up the row. The
    # composition lives in Python — this migration is type-only.
    #
    # platform_value_at_ack stores the numeric value snapped at
    # ack time, used by the 0.5%-tolerance auto-carry check.
    # platform_value_raw is the string repr for non-numeric values
    # (Layer 1/3 audit findings carry text like "5 strategies × 282
    # months"); the carry pass matches those by exact string equality.
    #
    # No FK to audit_findings. The whole point of this table is to
    # outlive the run that produced the original finding.
    op.create_table(
        "audit_acknowledgements",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("check_id", sa.String(120), nullable=False,
                  comment="Stable cross-run identifier, "
                          "composed from layer.metric.strategy."),
        sa.Column("verdict_at_ack", sa.String(10), nullable=False,
                  comment="The finding's status at ack time — "
                          "typically 'warning' since only WARNs "
                          "are acknowledged. Stored so the carry "
                          "pass can refuse to carry an ack across "
                          "a verdict change."),
        sa.Column("platform_value_at_ack", sa.Float(), nullable=True,
                  comment="Numeric snapshot of the platform value "
                          "at ack time. NULL when the value is "
                          "non-numeric — platform_value_raw carries "
                          "the string representation instead."),
        sa.Column("platform_value_raw", sa.Text(), nullable=True,
                  comment="String representation of the platform "
                          "value at ack time, populated when the "
                          "value is non-numeric so the carry pass "
                          "can match by exact string equality."),
        sa.Column("resolution_note", sa.Text(), nullable=False),
        sa.Column("acknowledged_by", sa.String(255), nullable=False),
        sa.Column("acknowledged_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        # superseded flips true when a re-run detects material change
        # (numeric delta > 0.5% or non-numeric string mismatch). The
        # row is preserved for history rather than deleted; the carry
        # pass filters WHERE superseded = false.
        sa.Column("superseded", sa.Boolean(), nullable=False,
                  server_default=sa.false(),
                  comment="True once a re-run detected material "
                          "change in the underlying finding. The "
                          "row stays as an audit-trail record; the "
                          "carry pass ignores superseded rows."),
        sa.Column("superseded_at", sa.TIMESTAMP(timezone=True),
                  nullable=True),
    )

    # Lookup index — the carry pass queries WHERE check_id = :c AND
    # superseded = false; an index on (check_id, superseded) makes
    # that O(1) instead of a sequential scan on every re-run.
    op.create_index(
        "ix_audit_acknowledgements_check_active",
        "audit_acknowledgements",
        ["check_id", "superseded"],
    )

    # ── 2. audit_findings additions ─────────────────────────────────────
    #
    # All three columns default-safe so existing rows + every code
    # path that reads audit_findings continues to work unchanged.
    # The next-PR auto-carry pass writes auto_acknowledged + resolved
    # together; the statistical-audit PDF disclosures section
    # surfaces resolved_by + resolved_at under each WARN.
    op.add_column(
        "audit_findings",
        sa.Column("auto_acknowledged", sa.Boolean(),
                  nullable=False,
                  server_default=sa.false(),
                  comment="True when the resolved flag was applied "
                          "by the carry pass rather than typed by "
                          "a reviewer. Used by the UI + PDF to "
                          "label carried acknowledgements "
                          "distinctly from fresh ones."),
    )
    op.add_column(
        "audit_findings",
        sa.Column("resolved_by", sa.String(255), nullable=True,
                  comment="Email of the reviewer who acknowledged "
                          "the finding (or 'auto_carry' when the "
                          "carry pass set resolved=true). The "
                          "statistical-audit PDF disclosures "
                          "section reads this for the 'Reviewed "
                          "by:' line."),
    )
    op.add_column(
        "audit_findings",
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True),
                  nullable=True,
                  comment="Timestamp of the acknowledgement. "
                          "Mirrors the resolved boolean — only "
                          "populated when resolved=true."),
    )

    # ── Changelog ───────────────────────────────────────────────────────
    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL)"
    ).bindparams(
        v=63,
        rel=datetime.now(timezone.utc),
        t="Audit acknowledgements — schema foundation",
        d=(
            "Adds the audit_acknowledgements table plus three new "
            "audit_findings columns (auto_acknowledged, resolved_by, "
            "resolved_at). The new table stores ONE canonical "
            "acknowledgement per stable check identity, independent "
            "of audit_run lifetime, so a re-run that surfaces the "
            "same finding can carry forward the team's prior "
            "review automatically when the underlying value has "
            "not materially changed. The three audit_findings "
            "columns capture the reviewer email + timestamp + a "
            "flag distinguishing carried acks from fresh ones. "
            "This migration is schema-only — the carry pass, the "
            "endpoint changes, the frontend UI and the PDF "
            "disclosures section all ship in follow-up PRs once "
            "the schema is in place."),
        a=(
            "The audit ack/resolve workflow as originally shipped "
            "lost the team's review every re-run — the resolution "
            "lived on the audit_findings row, and a new run "
            "produced fresh rows with resolved=false. The team was "
            "forced to re-type the same disclosure for every check "
            "that resurfaced unchanged. For the Analytical "
            "Appendix this is more than a UX irritation: it "
            "obscures the team's documented judgment across the "
            "project's full audit history. The new table makes "
            "the acknowledgement durable and machine-checkable, "
            "and the new audit_findings columns let the PDF "
            "render an honest 'Reviewed by Bob on 2026-05-22' "
            "line under every disclosed warning."),
    ))


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM changelog WHERE version = 63"))
    op.drop_column("audit_findings", "resolved_at")
    op.drop_column("audit_findings", "resolved_by")
    op.drop_column("audit_findings", "auto_acknowledged")
    op.drop_index(
        "ix_audit_acknowledgements_check_active",
        table_name="audit_acknowledgements",
    )
    op.drop_table("audit_acknowledgements")
