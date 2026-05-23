"""Citation review workflow — 7-state machine + reviewer actions.

May 23 2026 (item 1 — full citation review workflow).

Extends citations_cache with the four columns the reviewer workflow
needs:

  alternatives       JSONB    — alternative citations from search
                                 passes 2 and 3 (less-trusted +
                                 widest), so a reviewer can select
                                 one rather than enter a citation
                                 manually.
  reviewer_email     VARCHAR  — who reviewed (NULL until reviewed).
  reviewed_at        TIMESTAMPTZ — when the review action was taken.
  review_action      VARCHAR  — accept_untrusted | select_alternative
                                 | reject | manual_add.

The verification_status field already exists as a 40-char string;
no schema change needed for the 7 states themselves. The states are
defined as a module-level constant in tools/template_pipeline.py:

  not_found          — search returned nothing usable
  pending_review     — needs human decision (was: untrusted_source)
  verified           — auto-verified, trusted domain
  human_verified     — reviewer accepted a pending_review citation
  search_selected    — reviewer picked an alternative from pass 2/3
  manually_added     — reviewer entered the citation manually
  rejected           — reviewer rejected — no citation for this concept

The old "untrusted_source" state continues to be written by the
existing source_citations() function and read by citation_quality()
as a non-verified state; the new pipeline writes "pending_review"
instead, but both map to the same "needs review" bucket so a paper
generated under the old code path still renders correctly.

This migration is purely additive — no existing rows changed, no
existing columns dropped, no existing endpoints broken. Downgrade
removes the four columns; the older 3-state behaviour resumes.
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "036"
down_revision: str | None = "035"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # The four new columns. All nullable — a row may pre-date the
    # reviewer workflow OR may simply not yet have been reviewed.
    op.add_column(
        "citations_cache",
        sa.Column("alternatives", postgresql.JSONB(), nullable=True,
                  comment="Alternative citations from search pass 2 "
                          "(less-trusted academic) and pass 3 "
                          "(widest). One JSONB array of objects, "
                          "each carrying the same shape as the "
                          "primary citation. The reviewer picks one "
                          "via the select_alternative action."),
    )
    op.add_column(
        "citations_cache",
        sa.Column("reviewer_email", sa.String(255), nullable=True,
                  comment="Email of the reviewer who took an action "
                          "on this citation. NULL until reviewed."),
    )
    op.add_column(
        "citations_cache",
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True),
                  nullable=True,
                  comment="When the reviewer action landed. NULL "
                          "until reviewed."),
    )
    op.add_column(
        "citations_cache",
        sa.Column("review_action", sa.String(40), nullable=True,
                  comment="accept_untrusted | select_alternative | "
                          "reject | manual_add. NULL until reviewed."),
    )

    # ── Changelog ──────────────────────────────────────────────────────────
    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL)"
    ).bindparams(
        v=55,
        rel=datetime.now(timezone.utc),
        t="Full citation review workflow",
        d=(
            "The midpoint paper's citation pipeline now runs three "
            "search passes when the first trusted-domain pass returns "
            "nothing — a wider academic pass, then a widest pass. "
            "Bob reviews each citation that needs a decision and "
            "either accepts the search result, selects an alternative, "
            "enters a citation manually, or rejects the concept "
            "entirely. Every reviewed citation is logged with the "
            "reviewer's email and timestamp."),
        a=(
            "The Analytical Appendix grade depends on every citation "
            "being verified and accurate. The 3-pass search dramatically "
            "reduces 'not found' results, and the 7-state machine makes "
            "the reviewer's decision explicit and auditable."),
    ))


def downgrade() -> None:
    op.drop_column("citations_cache", "review_action")
    op.drop_column("citations_cache", "reviewed_at")
    op.drop_column("citations_cache", "reviewer_email")
    op.drop_column("citations_cache", "alternatives")
    op.execute("DELETE FROM changelog WHERE version = 55")
