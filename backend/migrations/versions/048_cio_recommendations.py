"""cio_recommendations — the live CIO recommendation, data_hash-cached.

May 28 2026. The second of the two LLM council surfaces, and the
deliberate counterpart to play_by_play_events (migration 047):

  play_by_play_events  immutable historical record, keyed by event_id,
                       written once and never recomputed.
  cio_recommendations  a LIVE signal that ages with the data, keyed by
                       data_hash, recomputed whenever the data changes.

These two must NOT share cache logic. A play-by-play event is a settled
historical fact; the CIO recommendation reflects the CURRENT regime
state and the CURRENT live blend weights, which move every month as new
market data lands. So this table caches by data_hash: the LLM council
call fires once per data_hash, writes the full four-component
recommendation here, and every read until the next data_hash change is
served from this row. The recompute is fired by the same hash-change
pipeline that warms the analytics cache; it is never triggered manually.

The stored payload is the four-component structure from migration 045 /
PR #209: signal, confidence (regime + posterior probability + Kish ESS +
ess_warning), dissenting_view, and the four mandatory limitations.

Revision ID: 048
Revises: 047
Create Date: 2026-05-28
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "048"
down_revision: str | None = "047"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "cio_recommendations",
        sa.Column("id", sa.BigInteger(),
                  primary_key=True, autoincrement=True),
        sa.Column("data_hash", sa.String(64), nullable=False,
                  comment="The analytics data fingerprint this "
                          "recommendation was computed for "
                          "(audit_assembler.current_data_hash). The cache "
                          "key: a read whose current hash matches a stored "
                          "row is served from it; a mismatch means the data "
                          "moved and the hash pipeline recomputes."),
        sa.Column("signal", sa.Text(), nullable=True,
                  comment="Component 1: what the data says, one quantified "
                          "sentence."),
        sa.Column("confidence", postgresql.JSONB(), nullable=True,
                  comment="Component 2: {regime, probability, ess, "
                          "ess_warning}. probability is the live posterior "
                          "for `regime`; ess is the Kish effective sample "
                          "size; ess_warning is true when ess is below the "
                          "optimizer's equal-weight fallback floor."),
        sa.Column("dissenting_view", sa.Text(), nullable=True,
                  comment="Component 3: the strongest counter-argument, a "
                          "specific named limitation."),
        sa.Column("limitations", postgresql.JSONB(), nullable=True,
                  comment="Component 4: the four mandatory limitations "
                          "(three-asset universe, post-2022 sample, "
                          "transaction costs, economic significance only)."),
        sa.Column("regime", sa.String(20), nullable=True,
                  comment="The current HMM regime label at compute time."),
        sa.Column("model", sa.String(40), nullable=True,
                  comment="The model that produced the text, or "
                          "'deterministic_fallback' when the LLM was "
                          "unavailable and the structured fallback was used."),
        sa.Column("raw_json", postgresql.JSONB(), nullable=True,
                  comment="The full four-component object as produced, for "
                          "audit and for serving the landing page verbatim."),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("data_hash", name="uq_cio_data_hash"),
    )
    # The live read serves the most recent row; the cache check matches on
    # data_hash. Index both access patterns.
    op.create_index(
        "ix_cio_recommendations_computed_at",
        "cio_recommendations", ["computed_at"])

    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL)"
    ).bindparams(
        v=67,
        rel=datetime.now(timezone.utc),
        t="Live CIO recommendation (data_hash-cached)",
        d=(
            "A new cio_recommendations table caches the live CIO "
            "recommendation by data_hash. The four-component council "
            "recommendation (signal, confidence, dissenting view, "
            "limitations) is computed once per data_hash by the same "
            "hash-change pipeline that warms the analytics cache, and "
            "served from the DB until the next data change. Distinct from "
            "the play-by-play record (migration 047), which is immutable "
            "history keyed by event_id; the two surfaces deliberately do "
            "not share cache logic."),
        a=(
            "Forest Capital sees a single, current 'what would the council "
            "do today' recommendation that updates as the market data "
            "updates, with its confidence tied to the live regime posterior "
            "and effective sample size and its limitations always disclosed. "
            "Caching by data_hash keeps the expensive council call to once "
            "per data change rather than once per page view, while "
            "guaranteeing the displayed recommendation always matches the "
            "data currently on screen."),
    ))


def downgrade() -> None:
    op.drop_index("ix_cio_recommendations_computed_at",
                  table_name="cio_recommendations")
    op.drop_table("cio_recommendations")
    op.execute(sa.text("DELETE FROM changelog WHERE version = 67"))
