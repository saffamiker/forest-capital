"""audit_findings.locked_disclosure_text — capture the team's
acknowledgement disclosure verbatim at acknowledge time.

June 6 2026. Bridge #75. WARN findings often have a disclosure_text
suggested by the audit engine ("this 0.4% Sharpe discrepancy is
within bootstrap-CI noise; report explicitly"). The acknowledge
workflow accepted a `resolution_note` (the team's internal review
comment) but did NOT preserve the disclosure_text that the team
agreed to put into the report. As a result, Bob had to re-derive
or re-find the disclosure for every acknowledged WARN when writing
the executive brief.

This column stores the disclosure verbatim at acknowledge time so
the next read of /audit/findings can surface it to Bob in a
copy-paste-ready form, and the analytical appendix generation (a
follow-up PR) can include the locked text under a "Disclosed WARNs"
section without re-querying the audit engine.

Nullable on purpose: a finding acknowledged WITHOUT a disclosure
(internal-review-only) leaves the column NULL. The frontend treats
NULL as "no disclosure was locked" and does not surface a
copy-paste box for that row.

Revision ID: 055
Revises: 054
Create Date: 2026-06-06
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "055"
down_revision: str | None = "054"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "audit_findings",
        sa.Column(
            "locked_disclosure_text", sa.Text(), nullable=True,
            comment=(
                "The disclosure text the team agreed to include in "
                "the report when acknowledging this WARN. Locked at "
                "acknowledge time and surfaced verbatim on subsequent "
                "audit panel reads + appendix generation. NULL when "
                "the finding was acknowledged without a disclosure "
                "(internal review only)."
            ),
        ),
    )
    # ON CONFLICT (version) DO NOTHING (bridge #79 idempotency
    # safeguard) -- same lesson as migration 054. Versions 72-73
    # are taken by migration 053; v=74 is now taken by migration
    # 054 (post-fix). This migration uses v=75. The DO NOTHING
    # clause makes any future collision a safe re-run rather than
    # a crashed migration.
    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL) "
        "ON CONFLICT (version) DO NOTHING"
    ).bindparams(
        v=75,
        rel=datetime.now(timezone.utc),
        t="Audit findings: capture locked disclosure text",
        d=(
            "WARN findings have a disclosure_text the audit engine "
            "suggests for the report. The acknowledge workflow now "
            "captures that text verbatim into a new "
            "locked_disclosure_text column at acknowledge time so "
            "Bob can copy-paste the agreed disclosure straight into "
            "the executive brief without re-querying the audit "
            "engine. A follow-up PR will include the locked text "
            "under a 'Disclosed WARNs' section of the analytical "
            "appendix automatically."
        ),
        a=(
            "Acknowledging a WARN is a recorded disclosure, not a "
            "correction. The disclosure agreed to at acknowledge "
            "time must be the disclosure that reaches the report -- "
            "otherwise the team's review loses fidelity over the "
            "weeks between acknowledge and submission. Locking the "
            "text at acknowledge time eliminates that drift."
        ),
    ))


def downgrade() -> None:
    op.drop_column("audit_findings", "locked_disclosure_text")
    op.execute(sa.text(
        "DELETE FROM changelog WHERE version = :v"
    ).bindparams(v=75))
