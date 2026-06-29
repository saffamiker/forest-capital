"""council_debates -- durable audit record for the adversarial critic
+ debate round that runs as part of the academic review pipeline and
the document generation harness.

Concern 7 (revised), June 23 2026. The integrated critic + debate
round needs a persisted record so:

  1. The team can show Dr. Panttser that "we considered X" by
     pointing at a logged rebuttal (the counter_arguments column).
  2. The team can audit which findings led to story-plan patches
     and which drafts were generated from those patches
     (fix_proposals + fix_applied + new_draft_id columns).
  3. The "minor findings only -- no debate" branch is also logged
     for completeness; the table is always written, regardless of
     whether the debate round fired.

Schema (sized for the integrated flow + the soft-regen story plan
injection from Change 7k):

  id              BIGSERIAL PRIMARY KEY
  interaction_id  BIGINT REFERENCES agent_interactions(id)
                  -- ties the debate to the human-initiated request
                  -- that triggered the academic review or doc gen.
                  -- Nullable for paths that don't write to
                  -- agent_interactions (e.g. background jobs).
  context         VARCHAR(40)
                  -- 'academic_review' | 'document_generation'
  document_type   VARCHAR(40)
                  -- 'executive_brief' | 'analytical_appendix' |
                  -- 'presentation_deck' | 'presentation_script' |
                  -- 'full_package' (cross-document review only)
  critic_model    VARCHAR(40)
                  -- 'gemini' | 'grok' | 'gemini+grok' |
                  -- 'gemini-only' / 'grok-only' (partial_failure)
  critic_findings JSONB
                  -- the full merged_findings array as returned by
                  -- run_critic_review; each entry carries severity,
                  -- category, document, location, description,
                  -- evidence, recommendation, raised_by, agreed
  fatal_count     INT NOT NULL DEFAULT 0
  major_count     INT NOT NULL DEFAULT 0
  minor_count     INT NOT NULL DEFAULT 0
  peer_responses  JSONB
                  -- the council's peer responses captured at debate
                  -- time. NULL when context='document_generation'
                  -- (doc-gen path skips peer fan-out for cost).
  arbiter_resolution  TEXT
                  -- the debate-round arbiter's prose response
  was_addressed   JSONB
                  -- per-finding boolean array, same order as
                  -- critic_findings. True for ADDRESSED /
                  -- PARTIALLY ADDRESSED; False for REBUTTED /
                  -- MISSED. Encoded as JSONB instead of
                  -- BOOLEAN[] because alembic + asyncpg differ
                  -- on array DDL portability.
  counter_arguments  JSONB
                  -- list of rebuttals -- {finding, rebuttal,
                  -- model_source, agreed, logged_at}. Empty list
                  -- when no rebuttals occurred.
  fix_proposals   JSONB
                  -- list of fix-proposal objects keyed by
                  -- finding_id (index into critic_findings).
                  -- Populated by the arbiter fix-proposal step
                  -- (Change 7k). Empty array when no proposals
                  -- were generated yet.
  fix_applied     BOOLEAN NOT NULL DEFAULT FALSE
                  -- True once the team confirmed a fix via
                  -- POST /api/v1/documents/apply-fix.
  fix_applied_at  TIMESTAMPTZ
  new_draft_id    BIGINT REFERENCES editor_drafts(id)
                  -- the editor_drafts row created from the patched
                  -- story plan, when fix_applied is True.
  source_draft_id BIGINT REFERENCES editor_drafts(id)
                  -- the draft the critic actually reviewed.
                  -- Change 7l-i: each critic round is anchored to
                  -- the draft it ran against so multi-round
                  -- iteration produces a verifiable audit chain.
  parent_debate_id BIGINT REFERENCES council_debates(id)
                  -- NULL for the first critic round on a document.
                  -- Set to the prior round's debate id on each
                  -- subsequent re-run; reconstructing the chain is
                  -- a single recursive walk. Combined with
                  -- new_draft_id this is the durable audit trail:
                  --   debate(round=1, source=v1, parent=null)
                  --     → fix → editor_drafts(v2)
                  --   debate(round=2, source=v2,
                  --          parent=debate_1) ...
  data_hash       VARCHAR(64)
                  -- the data_hash the review ran against, so
                  -- stale debates can be filtered out when the
                  -- cache hash flips.
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()

Indexes:
  ix_council_debates_created_at      -- chronological audit
  ix_council_debates_document_type   -- per-doc filter
  ix_council_debates_data_hash       -- per-snapshot filter

Revision ID: 061
Revises: 060
Create Date: 2026-06-23
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "061"
down_revision: str = "060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "council_debates",
        sa.Column("id", sa.BigInteger(),
                  primary_key=True, autoincrement=True),
        sa.Column("interaction_id", sa.BigInteger(), nullable=True),
        sa.Column("context", sa.String(40), nullable=False),
        sa.Column("document_type", sa.String(40), nullable=True),
        sa.Column("critic_model", sa.String(40), nullable=True),
        sa.Column(
            "critic_findings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True),
        sa.Column("fatal_count", sa.Integer(),
                  nullable=False, server_default="0"),
        sa.Column("major_count", sa.Integer(),
                  nullable=False, server_default="0"),
        sa.Column("minor_count", sa.Integer(),
                  nullable=False, server_default="0"),
        sa.Column(
            "peer_responses",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True),
        sa.Column("arbiter_resolution", sa.Text(), nullable=True),
        sa.Column(
            "was_addressed",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True),
        sa.Column(
            "counter_arguments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True),
        sa.Column(
            "fix_proposals",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True),
        sa.Column("fix_applied", sa.Boolean(),
                  nullable=False, server_default=sa.text("FALSE")),
        sa.Column("fix_applied_at",
                  sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("new_draft_id", sa.BigInteger(), nullable=True),
        sa.Column("source_draft_id", sa.BigInteger(), nullable=True),
        sa.Column("parent_debate_id",
                  sa.BigInteger(), nullable=True),
        sa.Column("data_hash", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_council_debates_created_at",
        "council_debates", ["created_at"])
    op.create_index(
        "ix_council_debates_document_type",
        "council_debates", ["document_type"])
    op.create_index(
        "ix_council_debates_data_hash",
        "council_debates", ["data_hash"])
    op.create_index(
        "ix_council_debates_parent_debate_id",
        "council_debates", ["parent_debate_id"])
    op.create_index(
        "ix_council_debates_source_draft_id",
        "council_debates", ["source_draft_id"])

    # Changelog contract -- every migration inserts at least one row.
    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, NOW(), :t, :d, :a, NULL)"
    ).bindparams(
        v=61,
        t="Adversarial Critic + Debate Round",
        d=(
            "Records every adversarial critic pass that runs as part "
            "of an academic review or document generation: the "
            "critic findings from Gemini + Grok, the council's "
            "debate-round response with per-finding "
            "ADDRESSED/PARTIALLY/MISSED/REBUTTED verdicts, the "
            "rebuttal text for each REBUTTED finding (the durable "
            "counter-argument log), and the fix proposals plus "
            "applied-fix linkage to a new editor_drafts row."),
        a=(
            "Closes the audit-record gap identified in the Audit 7 "
            "debate-round design pass. When Dr. Panttser asks 'did "
            "you consider X?' the team can answer with a logged "
            "rebuttal."),
    ))


def downgrade() -> None:
    op.drop_index(
        "ix_council_debates_source_draft_id",
        table_name="council_debates")
    op.drop_index(
        "ix_council_debates_parent_debate_id",
        table_name="council_debates")
    op.drop_index(
        "ix_council_debates_data_hash",
        table_name="council_debates")
    op.drop_index(
        "ix_council_debates_document_type",
        table_name="council_debates")
    op.drop_index(
        "ix_council_debates_created_at",
        table_name="council_debates")
    op.drop_table("council_debates")
