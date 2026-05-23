"""Analytical findings cache — pre-computed report findings.

May 22 2026 — analytical staging report (the layer between the raw
analytics_metrics_cache rows and the Academic Writer prompts).

Each row holds one full staging-report run: a structured findings list
in JSONB plus the rendered markdown report. The Academic Writer reads
the most recent row and injects its markdown into the system prompt
before generating any document section, so reports are grounded in
verified pre-computed findings rather than model assumptions.

Triggered ON DEMAND (POST /api/v1/reports/stage-findings) -- not on a
data-hash change. Findings require interpretation (each finding carries
a NUGGET STRENGTH rating and a SURPRISE flag); pre-computing them
silently on every data write would mean every fresh ingestion produces
a new "implication" paragraph that nobody asked for. Explicit trigger
is the right contract.

Schema notes:
  - One row per run. computed_at is the natural ordering key; the
    read accessor pulls ORDER BY computed_at DESC LIMIT 1.
  - findings JSONB carries the structured per-finding output (FINDING /
    EVIDENCE / IMPLICATION / NUGGET STRENGTH / SURPRISE for all 11).
  - findings_md is the rendered markdown surfaced in the report writer
    UI and injected into the Academic Writer prompts verbatim.
  - macro_digest_id FK -- when not null, identifies the macro_research_
    digests row whose summary was bundled into the run. A run with a
    null FK happened when no completed macro digest was available.
  - strategy_count and surprise_count are denormalised summary counts
    for the UI's "Findings staged" success banner.
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "030"
down_revision: str | None = "029"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "analytical_findings_cache",
        sa.Column("id", sa.BigInteger(), primary_key=True,
                  autoincrement=True),
        sa.Column("data_hash", sa.String(64), nullable=False,
                  comment="strategy_hash at the time of staging"),
        sa.Column("findings", sa.JSON(), nullable=False,
                  comment="Structured per-finding output -- the 11 "
                          "findings each with FINDING / EVIDENCE / "
                          "IMPLICATION / NUGGET STRENGTH / SURPRISE."),
        sa.Column("findings_md", sa.Text(), nullable=False,
                  comment="Rendered markdown report. Surfaced in the "
                          "report writer UI; injected verbatim into "
                          "the Academic Writer prompts."),
        sa.Column("macro_digest_id", sa.BigInteger(), nullable=True,
                  comment="FK to macro_research_digests.id when a "
                          "completed digest was bundled with this "
                          "staging run; null otherwise. No DB-level "
                          "FK constraint so a digest row can be "
                          "pruned without orphaning findings."),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("strategy_count", sa.Integer(), nullable=False,
                  server_default="0",
                  comment="Count of strategies covered by this run "
                          "(usually 10)."),
        sa.Column("surprise_count", sa.Integer(), nullable=False,
                  server_default="0",
                  comment="Count of findings flagged SURPRISE=yes."),
    )
    op.create_index(
        "ix_analytical_findings_cache_computed",
        "analytical_findings_cache",
        [sa.text("computed_at DESC")],
    )

    # ── Changelog ────────────────────────────────────────────────────
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
        "version": 49,
        "released_at": datetime(2026, 5, 22, tzinfo=timezone.utc),
        "title": "Analytical staging report — pre-computed findings cache",
        "description": (
            "Eleven structured findings (benchmark competitiveness, "
            "regime shift evidence, tail-risk divergence, natural "
            "complements, efficient frontier shift, diversification "
            "benefit, momentum-vs-mean-reversion, crisis performance, "
            "factor exposure, macro context alignment, surprises) are "
            "now staged on demand via POST /api/v1/reports/"
            "stage-findings. Every Academic Writer prompt injects the "
            "latest staged findings_md as a context block so generated "
            "reports cite only pre-computed numbers, not model "
            "assumptions."
        ),
        "academic_rationale": (
            "The midpoint paper and the Forest Capital executive brief "
            "both need a defensible factual backbone. Staging "
            "findings explicitly before writing means every numeric "
            "claim in a generated document has a verifiable "
            "provenance trail back to the live data, with the "
            "interpretation step (FINDING / IMPLICATION / NUGGET "
            "STRENGTH) standing alongside the raw numbers rather than "
            "inferred at draft time by the model."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 49")
    op.drop_index("ix_analytical_findings_cache_computed",
                  table_name="analytical_findings_cache")
    op.drop_table("analytical_findings_cache")
