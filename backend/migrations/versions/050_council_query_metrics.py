"""council_query_metrics — per-query token + HMM alignment log.

June 3 2026. Lands alongside the question-type context-injection
classifier (tools/council_question_bundles.py). The classifier picks
a small context bundle per question type; this table is the
measurement framework — one row per /api/council/query call so the
team can quantify (a) token-cost reduction vs the pre-classifier
baseline and (b) HMM alignment between the council's recommendation
and the live regime read.

The table is APPEND-ONLY — every query writes exactly one row, no
upserts. A BIGINT PK keeps it cheap; one index on (timestamp DESC)
serves the admin endpoint's "last 30 rows" read.

Columns track:
  question_type             classifier output or "full" / "baseline_full"
  input_tokens, output_tokens     totals from agents.usage.collect_usage()
  context_bundle_size       char count of the injected JSON block
  hmm_state, hmm_confidence detect_current_regime() at query time
  recommendation_direction  risk_on | defensive | balanced — extracted
                            from the synthesis text via the keyword
                            extractor in tools/council_direction_extractor
  hmm_alignment_score       continuous score per the June 3 2026
                            amendment:  base * confidence
                            base = 1.0 on a clean match, 0.0 on a
                            clean mismatch, 0.5 on TRANSITION /
                            balanced
                            (the original 0/0.5/1.0 ladder is retired
                            here — see column comment for the formula)
  data_hash                 the strategy_hash live when the query ran,
                            so a metric row can be anchored to the
                            exact dataset that produced it

Revision ID: 050
Revises: 049
Create Date: 2026-06-03
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "050"
down_revision: str | None = "049"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "council_query_metrics",
        sa.Column("id", sa.BigInteger(), primary_key=True,
                  autoincrement=True),
        sa.Column(
            "timestamp",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "question_type",
            sa.String(40),
            nullable=False,
            comment="Classifier output: regime | recommendation | risk | "
                    "statistical | forward | full (fallback) | "
                    "baseline_full (baseline-capture rows from the "
                    "pre-classifier run, used as the comparison baseline)",
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=True,
                  comment="Sum of input tokens across every agent call "
                          "in this query — from agents.usage.collect_usage"),
        sa.Column("output_tokens", sa.Integer(), nullable=True,
                  comment="Sum of output tokens across every agent call"),
        sa.Column("context_bundle_size", sa.Integer(), nullable=True,
                  comment="Character count of the JSON-serialised "
                          "context block the CIO received — the headline "
                          "token-reduction metric"),
        sa.Column("hmm_state", sa.String(20), nullable=True,
                  comment="BULL | BEAR | TRANSITION at query time"),
        sa.Column("hmm_confidence", sa.Float(), nullable=True,
                  comment="HMM posterior confidence (0.0 - 1.0)"),
        sa.Column(
            "recommendation_direction",
            sa.String(20),
            nullable=True,
            comment="risk_on | defensive | balanced — keyword-extracted "
                    "from the synthesis prose by "
                    "tools.council_direction_extractor.extract_direction",
        ),
        sa.Column(
            "hmm_alignment_score",
            sa.Float(),
            nullable=True,
            comment="Continuous score: base_score (0|1 on direction-vs-"
                    "regime match) * hmm_confidence. Replaces the older "
                    "0/0.5/1.0 ladder. A correct call in a 95%-confident "
                    "BULL regime scores 0.95; a balanced call in a 51% "
                    "TRANSITION scores 0.255. The full formula lives in "
                    "tools.council_direction_extractor.alignment_score.",
        ),
        sa.Column("data_hash", sa.String(64), nullable=True,
                  comment="strategy_hash live at query time, so a row "
                          "can be anchored to the exact dataset that "
                          "produced it"),
    )
    op.create_index(
        "ix_council_query_metrics_timestamp",
        "council_query_metrics",
        [sa.text("timestamp DESC")],
    )
    op.create_index(
        "ix_council_query_metrics_question_type",
        "council_query_metrics",
        ["question_type"],
    )

    # ── Changelog — every migration must seed one entry per the
    #    backend/scripts/changelog_gate.py contract.
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
        "version": 69,
        "released_at": datetime(2026, 6, 3, tzinfo=timezone.utc),
        "title": "Council question-type context bundles + measurement framework",
        "description": (
            "Council queries now classify the question (regime, "
            "recommendation, risk, statistical, forward) and inject a "
            "narrower context bundle tailored to that question type. "
            "Each query writes one row to council_query_metrics with "
            "input/output tokens, the injected bundle size, the live "
            "HMM state, the council's directional recommendation, and "
            "a continuous HMM-alignment score so the cost reduction "
            "and the directional-accuracy improvement are both "
            "measurable from the same row. /api/v1/admin/council-"
            "metrics surfaces the last 30 rows and the per-type "
            "aggregates."
        ),
        "academic_rationale": (
            "The Sept-onward Forest Capital demo flow asks the "
            "council pointed questions ('what is the current regime?', "
            "'what allocation do you recommend?'). Question-type "
            "context narrowing keeps the agent grounded on the data "
            "the question actually needs, which improves the quality "
            "of the answer the panel sees — a vague answer ('the data "
            "shows…') becomes a specific answer ('the post-2022 "
            "regime-conditional Sharpe shows 0.86 for the blend vs "
            "0.43 for the benchmark'). The measurement framework "
            "lets the team show the panel quantitative before/after "
            "evidence rather than asserting it."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 69")
    op.drop_index(
        "ix_council_query_metrics_question_type",
        table_name="council_query_metrics",
    )
    op.drop_index(
        "ix_council_query_metrics_timestamp",
        table_name="council_query_metrics",
    )
    op.drop_table("council_query_metrics")
