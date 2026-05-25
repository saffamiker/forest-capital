"""citation_type — multi-layered citation sourcing (foundational PR).

May 24 2026 (empirical-citation R&D foundation — closes the gap the
reviewer flagged where every project citation was 50+ year old
academic theory).

WHY:
The submission needs FOUR distinct layers of supporting literature
per finding, not one. The reviewer rightly observed that a graduate
research report citing only Markowitz 1952 / Sharpe 1966 / Fama-French
1993 reads as theoretical wallpaper. Modern empirical confirmation +
methodological validation + practitioner perspective land the argument
the foundational reference alone cannot.

THIS MIGRATION:
Adds two columns to citations_cache so the new pipeline (LLM-driven
query generator + 4-pass search + scoring) has somewhere to store
the layer tag and the auditor's confidence assessment. The pipeline
wiring itself ships in a follow-up PR; this migration is the
data-model foundation.

  citation_type VARCHAR(40), NOT NULL, default 'theoretical' —
    one of: 'theoretical' | 'empirical' | 'methodological' |
    'practitioner'. The default backfills every existing row with
    'theoretical' (Markowitz / Sharpe / etc. are theoretical
    foundations and that's exactly what the existing pipeline
    produced before today).

  trust_flag VARCHAR(20), NULLABLE — one of: 'verified' |
    'unverified' | 'paywalled' | 'stale' | 'mismatch'. Null on
    legacy rows (the new pipeline populates it; legacy rows
    render as 'unverified' in the UI via the graceful-degradation
    placeholder).

  scoring_rationale TEXT, NULLABLE — one-sentence explanation of
    why the citation scored as it did, surfaced as the Citation
    Review panel's hover-detail. Null on legacy rows.

CITATION TYPE TAXONOMY:
  theoretical    — foundational models / frameworks (Markowitz,
                   Sharpe, Fama-French, etc.)
  empirical      — peer-reviewed studies with original data, 2015+
                   (Journal of Finance, Journal of Portfolio
                   Management, etc.)
  methodological — technique / validation papers (CPCV, HMM, CVaR
                   methodology references)
  practitioner   — industry research (AQR, CFA Institute, MSCI,
                   JPMorgan AM, Vanguard, Fed working papers)

TRUST FLAG TAXONOMY:
  verified    — DOI confirmed, content matches claim
  unverified  — URL found but content not checked
  paywalled   — abstract only, full text not confirmed
  stale       — pre-2015 and not foundational type (auto-flagged)
  mismatch    — source found but claim not directly supported
                (do not surface to reviewer)

INDEXES:
  Compound (generation_id, citation_type) supports the Citation
  Review panel's GROUP BY query: "give me every citation for
  generation N, grouped by type, top 2 per type by confidence".
  The frontend does the top-N selection in JS once the rows arrive,
  but the index gives the DB the cheap path for the row scan.

The scoring side of the new pipeline writes to the existing
confidence_score column (migration 039) — no schema change needed
there. This migration is additive only; every existing query
continues to work because (a) the new columns are nullable or
default, and (b) no existing read code path mentions either field.

Downgrade drops both new columns; the older single-type behaviour
resumes because every reader of citation_type / trust_flag is a
NEW code path landing together with this migration.
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "043"
down_revision: str | None = "042"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── 1. citation_type ─────────────────────────────────────────────
    op.add_column(
        "citations_cache",
        sa.Column(
            "citation_type", sa.String(40),
            nullable=False, server_default=sa.text("'theoretical'"),
            comment="One of 'theoretical' | 'empirical' | "
                    "'methodological' | 'practitioner'. Default "
                    "'theoretical' backfills every existing row "
                    "because that's what the pre-multi-layer "
                    "pipeline produced."),
    )

    # Explicit back-fill — defence in depth alongside the server
    # default. Idempotent — a re-upgrade does not duplicate work.
    op.execute(sa.text("""
        UPDATE citations_cache
        SET citation_type = 'theoretical'
        WHERE citation_type IS NULL OR citation_type = ''
    """))

    # ── 2. trust_flag ────────────────────────────────────────────────
    op.add_column(
        "citations_cache",
        sa.Column(
            "trust_flag", sa.String(20),
            nullable=True,
            comment="Reviewer-facing assessment: 'verified' | "
                    "'unverified' | 'paywalled' | 'stale' | "
                    "'mismatch'. Null on legacy rows (pre-multi-"
                    "layer pipeline); the frontend renders the "
                    "graceful-degradation 'unverified' placeholder."),
    )

    # ── 3. scoring_rationale ─────────────────────────────────────────
    op.add_column(
        "citations_cache",
        sa.Column(
            "scoring_rationale", sa.Text(),
            nullable=True,
            comment="One-sentence explanation of the confidence "
                    "score: source quality, relevance to the "
                    "claim, recency, verifiability. Surfaced as "
                    "the Citation Review panel hover-detail."),
    )

    # ── 4. Compound index for the Citation Review group-by query ────
    op.create_index(
        "ix_citations_cache_type",
        "citations_cache", ["generation_id", "citation_type"])

    # ── Changelog ────────────────────────────────────────────────────
    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL)"
    ).bindparams(
        v=62,
        rel=datetime.now(timezone.utc),
        t="Multi-layered citation sourcing — schema foundation",
        d=(
            "Citations now carry a citation_type tag (theoretical / "
            "empirical / methodological / practitioner) plus a "
            "trust_flag and scoring_rationale. The new pipeline (LLM-"
            "driven query generation + 4-pass search + confidence "
            "scoring) ships in a follow-up PR; this migration adds "
            "the columns so the new pipeline has somewhere to store "
            "its output. Existing citations backfill as "
            "'theoretical' so the report writer continues to render "
            "them correctly during the transition."),
        a=(
            "The midpoint reviewer flagged that the submission's "
            "citations were all 50+ year old academic theory. A "
            "graduate research report needs four layers of support: "
            "the foundational frameworks (Markowitz, Sharpe, Fama-"
            "French), recent empirical confirmation, methodological "
            "validation for the techniques used (CPCV, HMM, CVaR), "
            "and practitioner perspective (AQR, CFA Institute, Fed "
            "working papers). Tagging every citation by type lets "
            "the Citation Review panel surface gaps explicitly — "
            "the reviewer sees at a glance when a finding has only "
            "theoretical backing and needs empirical support."),
    ))


def downgrade() -> None:
    op.drop_index("ix_citations_cache_type", table_name="citations_cache")
    op.drop_column("citations_cache", "scoring_rationale")
    op.drop_column("citations_cache", "trust_flag")
    op.drop_column("citations_cache", "citation_type")
    op.execute(sa.text("DELETE FROM changelog WHERE version = 62"))
