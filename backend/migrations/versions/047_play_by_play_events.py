"""play_by_play_events — point-in-time event validation records.

May 28 2026 — Layer 3+ of the Regime-Conditional Meta-Portfolio
Optimizer. The play-by-play feature evaluates a fixed set of named
post-2022 market events (SVB, the debt-ceiling standoff, Higher for
Longer, the October 2023 selloff, the Q4 2023 rally, the yen carry
unwind, the election/tariff repricing, the 2025 trade war, Liberation
Day) STRICTLY point-in-time: at each event the HMM posterior and the
regime-conditional blend weights are computed from data available up to
that month only, then the recommendation is scored against the actual
forward 30/60/90-day returns of the blend, the benchmark, and the
classic 60/40.

This table is the durable record behind the Council Performance Record
page and the final-presentation play-by-play slide. One row per
event_id, UPSERTed on recompute. The quantitative fields (posterior,
blend_weights, performance, value_added_sharpe) are deterministic given
the data; the recommendation / dissenting_view are generated text and
may be regenerated. data_hash lets a reader see which data snapshot a
row reflects so a stale row is obvious.

Revision ID: 047
Revises: 046
Create Date: 2026-05-28
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "047"
down_revision: str | None = "046"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "play_by_play_events",
        sa.Column("id", sa.BigInteger(),
                  primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(40), nullable=False,
                  comment="Stable slug, e.g. 'svb_2023_03'. One row per "
                          "event; recompute UPSERTs on this key."),
        sa.Column("event_date", sa.Date(), nullable=False,
                  comment="Month-end the event is anchored to. The HMM "
                          "posterior and blend are point-in-time as of "
                          "this date; performance is the forward window "
                          "after it."),
        sa.Column("trigger", sa.Text(), nullable=False,
                  comment="One factual sentence describing the trigger. "
                          "No editorial bias; the HMM sees only return / "
                          "volatility patterns, never the news."),
        sa.Column("regime", sa.String(20), nullable=True,
                  comment="Point-in-time HMM regime label at event_date "
                          "(BULL | BEAR | TRANSITION)."),
        sa.Column("posterior", postgresql.JSONB(), nullable=True,
                  comment="{bull, bear, transition} posterior at "
                          "event_date, from a fit on data up to that "
                          "month only."),
        sa.Column("blend_weights", postgresql.JSONB(), nullable=True,
                  comment="{strategy: weight} live regime-conditional "
                          "blend active at the event, from blends trained "
                          "on the pre-event window only."),
        sa.Column("recommendation", sa.Text(), nullable=True,
                  comment="One-sentence council recommendation in the "
                          "four-component framing. Generated text; "
                          "fail-open to a factual template off the "
                          "weights when the LLM is unavailable."),
        sa.Column("dissenting_view", sa.Text(), nullable=True,
                  comment="One-sentence strongest counter-argument, a "
                          "specific named limitation."),
        sa.Column("performance", postgresql.JSONB(), nullable=True,
                  comment="{blend|benchmark|classic_6040: {d30, d60, "
                          "d90}} forward cumulative returns. 30/60/90 "
                          "days map to 1/2/3 forward months (the data is "
                          "monthly); granularity disclosed."),
        sa.Column("verdict", sa.Text(), nullable=True,
                  comment="One-sentence summary of whether the blend "
                          "added value over the forward window."),
        sa.Column("value_added_sharpe", sa.Float(), nullable=True,
                  comment="Annualised Sharpe of the blend minus the "
                          "benchmark over the forward window. A "
                          "3-observation directional figure (90-day "
                          "window), never significance-tested; null when "
                          "the window is too short or has no variance."),
        sa.Column("hmm_fit", sa.String(20), nullable=True,
                  comment="'point_in_time' — the play-by-play HMM is fit "
                          "on data up to event_date, unlike the Layer 3 "
                          "full-history fit."),
        sa.Column("n_train_months", sa.Integer(), nullable=True,
                  comment="Months in the pre-event training window the "
                          "blend was fit on."),
        sa.Column("data_hash", sa.String(64), nullable=True,
                  comment="Fingerprint of the data snapshot this row "
                          "reflects, so a stale row is detectable."),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("event_id", name="uq_pbp_event_id"),
    )
    op.create_index(
        "ix_pbp_event_date", "play_by_play_events", ["event_date"])

    # ── Changelog (required from migration 011 onward) ───────────────────
    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL)"
    ).bindparams(
        v=66,
        rel=datetime.now(timezone.utc),
        t="Play-by-play event validation",
        d=(
            "A new play_by_play_events table stores point-in-time "
            "evaluations of nine named post-2022 market events. At each "
            "event the HMM regime posterior and the regime-conditional "
            "blend weights are computed from data available up to that "
            "month only (no look-ahead), then the recommendation is "
            "scored against the actual forward 30/60/90-day returns of "
            "the blend, the benchmark, and the classic 60/40. Feeds the "
            "Council Performance Record page and the presentation "
            "play-by-play slide."),
        a=(
            "The strongest defence of a regime-aware strategy is showing "
            "it would have made the right call in real time, event by "
            "event, using only the data a manager had on the day. The "
            "play-by-play turns the aggregate out-of-sample Sharpe into a "
            "narrative a faculty panel can interrogate one event at a "
            "time, with the no-look-ahead constraint enforced in code and "
            "the monthly-granularity and short-window caveats disclosed "
            "on every figure."),
    ))


def downgrade() -> None:
    op.drop_index("ix_pbp_event_date", table_name="play_by_play_events")
    op.drop_table("play_by_play_events")
    op.execute(sa.text("DELETE FROM changelog WHERE version = 66"))
