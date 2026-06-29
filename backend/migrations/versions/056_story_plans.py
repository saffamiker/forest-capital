"""story_plans -- shared cache for deck story arcs and brief section plans.

June 19 2026. The deck and the executive brief both depend on a stable
"story plan" -- a structured outline with locked numeric anchors per
slide / section -- that the per-slide / per-section LLM passes consume
to produce prose. Previously each slide was an independent Sonnet call
with no shared state, leading to (a) cross-slide numeric inconsistency
(the same metric appearing as different values across slides) and
(b) drift from the locked academic figures.

This table caches the story plan keyed by data_hash + document_type
so the (expensive, multi-pass Opus) plan generation runs ONCE per
data_hash and the per-slide / per-section rendering passes always see
the same locked anchors.

Schema mirrors cio_recommendations: a flat row per (data_hash,
document_type) carrying the JSON plan plus structured prose surfaces
(full_script for the deck speaker script, anticipated_questions from
Grok's contrarian pass, dissenting_view + limitations from Gemini's
blind-spot pass).

Conflict policy mirrors cio_recommendations (PR #324): a guarded
DO UPDATE that overwrites only when the existing row is a
deterministic_fallback (transient LLM failure) and the incoming row
is a real LLM plan. Real plans are never overwritten on a re-warm of
the same data_hash; the data hash drives all invalidation.

Revision ID: 056
Revises: 055
Create Date: 2026-06-19
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "056"
down_revision: str | None = "055"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "story_plans",
        sa.Column("id", sa.BigInteger(),
                  primary_key=True, autoincrement=True),
        sa.Column("data_hash", sa.String(64), nullable=False,
                  comment="The analytics data fingerprint this plan was "
                          "computed for. Plans are recomputed only when "
                          "the data_hash changes."),
        sa.Column("document_type", sa.String(40), nullable=False,
                  comment="'deck' | 'brief' -- the document the plan "
                          "structures. Both share the table so the same "
                          "data_hash can carry both plans for cross-"
                          "consistency joins."),
        sa.Column("central_argument", sa.Text(), nullable=True,
                  comment="The one-sentence thesis the plan defends."),
        sa.Column("plan_json", postgresql.JSONB(), nullable=False,
                  comment="The full structured plan: slide_plan (deck) "
                          "or section_plan (brief), with per-unit "
                          "headlines, numeric_anchors, speaker_notes."),
        sa.Column("full_script", sa.Text(), nullable=True,
                  comment="Deck only -- the word-for-word presenter "
                          "script generated in Pass 2 from the slide plan."),
        sa.Column("anticipated_questions", postgresql.JSONB(),
                  nullable=True,
                  comment="Grok contrarian pass output -- the hardest "
                          "committee questions + suggested answers."),
        sa.Column("dissenting_view", sa.Text(), nullable=True,
                  comment="Gemini independent pass output -- the strongest "
                          "honest counter-argument to the plan."),
        sa.Column("limitations_surfaced", postgresql.JSONB(),
                  nullable=True,
                  comment="Gemini independent pass output -- gaps and "
                          "blind spots the plan should disclose."),
        sa.Column("model", sa.String(40), nullable=True,
                  comment="The model that produced the plan, or "
                          "'deterministic_fallback' when the LLM was "
                          "unavailable and the structured fallback was used."),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("data_hash", "document_type",
                            name="uq_story_plans_hash_type"),
    )
    op.create_index(
        "ix_story_plans_computed_at",
        "story_plans", ["computed_at"])

    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL)"
    ).bindparams(
        # The changelog.version column is INTEGER + UNIQUE (migration
        # 011). The original "056" string bind caused the INSERT to
        # fail on production with a type mismatch -- and because the
        # whole upgrade() runs in a transaction, the failing INSERT
        # rolled back the preceding CREATE TABLE, leaving the
        # story_plans table absent. The integer fix below also has
        # to PICK A FREE VERSION: v=56 is already taken by an earlier
        # migration; the next free slot after migration 055 (v=75)
        # is v=76.
        #
        # June 21 2026 -- the second half of #331. released_at was
        # being bound as sa.text("now()") which produces a TextClause
        # object, not a datetime. asyncpg expects datetime instances
        # for TIMESTAMPTZ columns and would re-raise the same
        # transaction-killing type error on production despite the
        # integer-version fix. datetime.now(timezone.utc) matches what
        # every other migration that INSERTs a changelog row uses
        # (see 055_audit_findings_locked_disclosure for the canonical
        # pattern).
        v=76,
        rel=datetime.now(timezone.utc),
        t="Story-plan cache for deck and brief",
        d="A shared structured outline keyed by data_hash + document_type. "
          "Locks numeric anchors and the central argument so the per-slide "
          "and per-section LLM passes always cite consistent figures.",
        a="Cross-slide numeric inconsistency in the deck audit panel "
          "(48 numeric flags, 7 cross-section consistency flags as of "
          "June 18). The fix is structural: generate the plan once per "
          "data_hash, render prose around the locked plan."))


def downgrade() -> None:
    op.drop_index("ix_story_plans_computed_at",
                  table_name="story_plans")
    op.drop_table("story_plans")
