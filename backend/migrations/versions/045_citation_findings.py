"""citation_findings — Level 1 findings + citation-to-finding junction.

May 26 2026 — Citation Review redesign.

The Citation Review panel was previously a single flat list of
citations sourced for each template concept_id. The redesign per the
green-lit design doc surfaces a 3-level hierarchy:

    LEVEL 1  Finding (high-ranked, from the live QA + audit results)
    LEVEL 2    Citation Type sub-group
    LEVEL 3      Individual citation rows (checkbox + score + rationale)

This migration adds the two tables the new model needs. Existing
citations_cache rows are NOT touched — every citation keeps its
concept_id, citation_type, confidence_score and verification_status
verbatim. Findings + matches are additive layers on top.

  1. findings — per (generation_id, source, source_id). Seeded
     LIVE at the start of every Citation Review panel open from
     the existing audit_findings (statistical audit, latest
     substantive run) and qa_results_cache.checklist_json (latest
     methodology audit for the current strategy_hash). Filtered to
     high+medium rank only; IN02 (Academic Review attestation)
     excluded — mirrors the QA badge logic from PR #176. Stored
     (not materialised view) so citation_finding_matches.finding_id
     is a stable FK target — UPSERTed on every re-seed so the
     team's prior match work on an unchanged finding survives.

  2. citation_finding_matches — junction with checkbox state. A
     citation can match multiple findings (the redesign explicitly
     supports many-to-many). ON DELETE CASCADE on both FKs so a
     deleted citation or finding cleans up its matches.

KNOWN LIMITATION (acknowledged in the design doc): when a finding
is RESOLVED between Citation Review sessions and no longer surfaces
in the seed, the citation_finding_matches rows for that finding are
cascade-deleted along with the findings row. The team's match work
for that pair is silently lost. For tonight's MVP this is acceptable
— a resolved finding no longer needs citation support. Post-deadline
follow-up could either (a) keep a "resolved findings" view so the
matches persist as audit history, or (b) snapshot the matches into
a separate resolved_matches table at delete time. See the inline
comment on the citation_finding_matches ON DELETE CASCADE for the
revisit pointer.

CITATION_TYPE EXPANSION: the existing VARCHAR(40) column on
citations_cache (added in migration 043) carries no CHECK constraint
or enum binding, so the two new values — 'regulatory' and
'data_source' — require zero schema change. They're added at the
application layer (CITATION_TYPES tuple in citation_sourcing.py).
The comment update below documents the expanded six-value taxonomy
for future readers without altering storage.

Revision ID: 045
Revises: 044
Create Date: 2026-05-26
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "045"
down_revision: str | None = "044"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── 1. findings — Level 1 wrappers ───────────────────────────────────
    op.create_table(
        "findings",
        sa.Column("id", sa.BigInteger(),
                  primary_key=True, autoincrement=True),
        sa.Column("generation_id", sa.Integer(),
                  sa.ForeignKey("report_generations.id",
                                ondelete="CASCADE"),
                  nullable=False,
                  comment="The citation-review session this finding "
                          "belongs to. Per-generation scoping keeps "
                          "match state local to one analytical context."),
        sa.Column("source", sa.String(10), nullable=False,
                  comment="'audit' (audit_findings row) | 'qa' "
                          "(qa_results_cache.checklist_json item)"),
        sa.Column("source_id", sa.String(80), nullable=False,
                  comment="audit_findings.id (str-coerced) for "
                          "source='audit'; QA check_id (e.g. 'S03') "
                          "for source='qa'."),
        sa.Column("title", sa.String(200), nullable=False,
                  comment="The finding's display title. audit: "
                          "check_name + ' — ' + metric. qa: the "
                          "check's `check` field."),
        sa.Column("description", sa.Text(), nullable=True,
                  comment="The finding's detail. audit: "
                          "auditor_reasoning or discrepancy. qa: "
                          "the check's evidence string."),
        sa.Column("rank", sa.String(10), nullable=False,
                  comment="'high' | 'medium'. Low-rank findings "
                          "are not citation-worthy and are dropped "
                          "at seed time."),
        sa.Column("status", sa.String(20), nullable=True,
                  comment="Raw status from the source row: "
                          "'fail'/'warning' for audit, "
                          "'FAIL'/'WARN'/'INCOMPLETE' for qa."),
        sa.Column("severity", sa.String(20), nullable=True,
                  comment="audit_findings.severity passthrough "
                          "('critical' | 'warning' | 'info'). Null "
                          "for qa-sourced findings."),
        sa.Column("seeded_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()"),
                  comment="Most recent seed timestamp. Updated on "
                          "every UPSERT during a re-seed."),
        sa.UniqueConstraint("generation_id", "source", "source_id",
                            name="uq_findings_gen_src"),
    )

    # The Level 1 GROUP BY query reads (generation_id, rank DESC) on
    # every panel open — sort high → medium → (no low) and order by
    # title within rank. Compound index aligns the row scan exactly.
    op.create_index(
        "ix_findings_generation_rank",
        "findings", ["generation_id", "rank"])

    # ── 2. citation_finding_matches — many-to-many junction ──────────────
    op.create_table(
        "citation_finding_matches",
        sa.Column("id", sa.BigInteger(),
                  primary_key=True, autoincrement=True),
        sa.Column("citation_id", sa.Integer(),
                  sa.ForeignKey("citations_cache.id",
                                ondelete="CASCADE"),
                  nullable=False,
                  comment="The matched citation. ON DELETE CASCADE "
                          "because a deleted citation cannot still "
                          "be considered matched."),
        sa.Column("finding_id", sa.BigInteger(),
                  sa.ForeignKey("findings.id", ondelete="CASCADE"),
                  nullable=False,
                  comment="The finding the citation supports. "
                          "ON DELETE CASCADE — see KNOWN LIMITATION "
                          "in the module docstring: when a finding "
                          "resolves between sessions and is "
                          "re-seeded out of existence, its match "
                          "rows cascade-delete. Acceptable for "
                          "submission-night MVP; revisit "
                          "post-deadline."),
        sa.Column("matched_by", sa.String(120), nullable=True,
                  comment="Email of the reviewer who recorded the "
                          "match."),
        sa.Column("matched_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("match_rationale", sa.Text(), nullable=True,
                  comment="Optional user-typed note explaining why "
                          "this citation supports this finding. "
                          "Deferred — UI for entering this is "
                          "post-submission; null for now."),
        sa.UniqueConstraint("citation_id", "finding_id",
                            name="uq_cfm_citation_finding"),
    )

    op.create_index(
        "ix_cfm_citation", "citation_finding_matches", ["citation_id"])
    op.create_index(
        "ix_cfm_finding", "citation_finding_matches", ["finding_id"])

    # ── 3. citation_type comment — document the expanded taxonomy ────────
    # The column itself is VARCHAR(40) with no CHECK constraint
    # (migration 043), so the two new values 'regulatory' and
    # 'data_source' need no schema change to store. Update the
    # column comment so future readers see the full six-value
    # taxonomy without having to grep the application code.
    op.execute(sa.text(
        "COMMENT ON COLUMN citations_cache.citation_type IS "
        "'One of theoretical | empirical | methodological | "
        "regulatory | data_source | practitioner. Default "
        "''theoretical'' (set by migration 043 default + backfill). "
        "Regulatory and data_source added May 26 2026 — the LLM "
        "sourcing pipeline can tag any value; the Citation Review "
        "panel groups Level 2 by this column.'"
    ))

    # ── Changelog ────────────────────────────────────────────────────────
    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL)"
    ).bindparams(
        v=64,
        rel=datetime.now(timezone.utc),
        t="Citation Review redesign — findings + matches",
        d=(
            "Citation Review is now a 3-level hierarchy: "
            "Finding > Citation Type > Individual Citation with "
            "checkbox matching. The new `findings` table surfaces "
            "high+medium-rank rows from the live statistical-audit "
            "(audit_findings) and methodology (qa_results_cache) "
            "results, re-seeded on every panel open. The new "
            "`citation_finding_matches` junction supports "
            "many-to-many: a single citation can be matched to "
            "multiple findings. The six-value citation_type "
            "taxonomy (theoretical, empirical, methodological, "
            "regulatory, data_source, practitioner) is now "
            "documented on the column comment. Existing "
            "citations_cache rows are untouched."),
        a=(
            "The midpoint reviewer flagged that citations were "
            "weakly tied to specific findings. The new model "
            "makes the link explicit: every high-priority finding "
            "from the live analytical state can be matched to "
            "the citations that support it, grouped by citation "
            "type so a reviewer sees at a glance whether a "
            "finding is backed by theory alone (gap) or by a "
            "mix of theoretical + empirical + practitioner "
            "evidence (defensible). Adding regulatory and "
            "data_source as first-class types closes the "
            "previously missing coverage of standards / "
            "guidance / data-provider documentation."),
    ))


def downgrade() -> None:
    # Drop the matches table first — it FK-references findings.
    op.drop_index("ix_cfm_finding", table_name="citation_finding_matches")
    op.drop_index("ix_cfm_citation", table_name="citation_finding_matches")
    op.drop_table("citation_finding_matches")
    op.drop_index("ix_findings_generation_rank", table_name="findings")
    op.drop_table("findings")
    # The citation_type column comment is best-effort to restore —
    # migration 043 set the original prose; on downgrade we leave
    # the May-26 comment in place. The column itself is unchanged.
    op.execute(sa.text("DELETE FROM changelog WHERE version = 64"))
