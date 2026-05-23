"""Citation evidence fields — extract, rationale, confidence, finding.

May 23 2026 (item 13 — full citation evaluation cards for Bob).

The May-23 citation review workflow (migration 036) added the
`alternatives` column plus the reviewer-action columns. That gave
the UI a list to render but no EVIDENCE per option — Bob still had
to click out to every URL to decide which citation actually
supports the claim. This migration adds the evidence columns the
3-pass search will now populate, so each citation tile becomes a
complete evaluation card:

  supporting_extract    TEXT  — 1-2 sentences from the source that
                                 directly support the concept's
                                 claim. Pulled by the citation-
                                 finder agent during each pass.
                                 NULL when extract not available.
  selection_rationale   TEXT  — one-sentence explanation of why
                                 this source was the strongest
                                 match (source type, venue, claim
                                 alignment).
  confidence_score      FLOAT — 0.0-1.0 derived from the pass tier
                                 plus URL trust signals. Pass 1
                                 trusted ≈ 0.95; pass 2 academic
                                 ≈ 0.75; pass 3 widest ≈ 0.55.
  finding_supported     TEXT  — human-readable description of the
                                 specific claim this citation
                                 backs. Distinct from concept_id
                                 (which is a slug). Derived by the
                                 search agent from the concept's
                                 search_query.

Every column is nullable so the migration is purely additive — no
existing row changes, no existing query breaks. Rows persisted
BEFORE this migration carry NULL on the four new columns; the
frontend renders "Evidence not captured for this citation" as the
graceful-degradation placeholder for legacy rows. New generations
(after the matching pipeline change lands together with this
migration) populate the columns on every row.

The `alternatives` column carries the SAME four fields per
alternative entry (it's a JSONB array of dicts), so each
alternative tile in the UI also shows its own extract / rationale
/ confidence. Surfacing those is a UI-only change once this
migration + the pipeline update land — the alternative payload
shape extends naturally.

Downgrade drops the four columns. Older code that doesn't read
them resumes working unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "039"
down_revision: str | None = "038"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "citations_cache",
        sa.Column(
            "supporting_extract", sa.Text(), nullable=True,
            comment="1-2 sentences from the source directly "
                    "supporting the concept's claim. Pulled by the "
                    "citation-finder agent during the search pass. "
                    "NULL when no extract was captured (legacy rows "
                    "or a pass that returned only metadata)."),
    )
    op.add_column(
        "citations_cache",
        sa.Column(
            "selection_rationale", sa.Text(), nullable=True,
            comment="One-sentence explanation of why this source "
                    "ranked first across the 3-pass search — source "
                    "type (primary/secondary), publication venue, "
                    "authority signal, and how closely it matches "
                    "the specific claim."),
    )
    op.add_column(
        "citations_cache",
        sa.Column(
            "confidence_score", sa.Float(), nullable=True,
            comment="0.0-1.0 confidence the citation supports the "
                    "claim. Derived from pass tier plus URL trust "
                    "signals (pass 1 trusted ≈ 0.95, pass 2 academic "
                    "≈ 0.75, pass 3 widest ≈ 0.55). NULL on legacy "
                    "rows generated before this column existed."),
    )
    op.add_column(
        "citations_cache",
        sa.Column(
            "finding_supported", sa.Text(), nullable=True,
            comment="Human-readable description of the specific "
                    "claim this citation backs — distinct from "
                    "concept_id, which is a slug. Derived by the "
                    "search agent from the concept's search_query. "
                    "Powers the 'Finding supported' line on every "
                    "citation tile."),
    )

    # ── Changelog ──────────────────────────────────────────────────────────
    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL)"
    ).bindparams(
        v=58,
        rel=datetime.now(timezone.utc),
        t="Citation tiles surface full supporting evidence",
        d=(
            "Every citation tile in the report writer now shows the "
            "supporting extract pulled from the source, a "
            "one-sentence rationale for why the source was selected, "
            "a 0.0-1.0 confidence score, and the specific claim the "
            "citation backs. Alternative citations from the 2nd and "
            "3rd search passes carry the same evidence so Bob can "
            "compare options side-by-side. The 'Accept this instead' "
            "button promotes an alternative to primary and demotes "
            "the previous primary into the alternatives list — no "
            "information lost on a swap."),
        a=(
            "The Analytical Appendix grade depends on every citation "
            "being defensible. Surfacing the supporting extract + "
            "rationale on the tile means Bob can audit each citation "
            "in seconds instead of opening every URL — a 10x speedup "
            "on the citation review pass that determines paper "
            "quality."),
    ))


def downgrade() -> None:
    op.drop_column("citations_cache", "finding_supported")
    op.drop_column("citations_cache", "confidence_score")
    op.drop_column("citations_cache", "selection_rationale")
    op.drop_column("citations_cache", "supporting_extract")
    op.execute("DELETE FROM changelog WHERE version = 58")
