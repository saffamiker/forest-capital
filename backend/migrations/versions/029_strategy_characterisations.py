"""Strategy characterisations — per-strategy pre-computed profile.

May 22 2026 — Item 9 (strategy context panels). The Portfolio Profile
panel on each strategy detail view, the behavioural_tag on the
Dashboard strategy cards, and the agent system-prompt injection
when a council / explainer / academic-review session opens in a
specific strategy's context all read from this table.

Why a new table rather than another row in analytics_metrics_cache:
each row here is PER-STRATEGY, keyed by (strategy_id, data_hash). The
existing analytics_metrics_cache holds one row per metric_kind covering
the whole portfolio. Carving the per-strategy facts out into their own
table makes the read pattern clean (one row per strategy, joined or
batch-fetched as a set) and keeps the AI-generated text fields out of
the analytics JSONB blobs where they would be awkward to query.

Schema notes:
  - (strategy_id, data_hash) UNIQUE so the refresh can upsert.
  - portfolio_characteristics + behavioural_profile are JSONB so the
    structured sub-fields (avg_holdings, avg_turnover_pct, etc., plus
    the behavioural_profile's outperforms_when / underperforms_when /
    primary_risk_factor / diversification_role) stay queryable.
  - construction_summary, regime_sensitivity and behavioural_tag are
    plain text — they're rendered verbatim in the UI and injected as
    plain text into the agent prompt.
  - All four plain-text / structured fields are AI-generated on the
    first data_hash computation, then upserted on every subsequent
    data_hash change. Generation uses the Academic Writer's Claude
    Sonnet path with the strategy's metadata + backtest characteristics
    + factor loadings + regime-conditional performance as input.
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "029"
down_revision: str | None = "028"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_characterisations",
        sa.Column("id", sa.BigInteger(), primary_key=True,
                  autoincrement=True),
        sa.Column("strategy_id", sa.String(50), nullable=False,
                  comment="The strategy's id from STRATEGY_METADATA — "
                          "e.g. 'BENCHMARK', 'VOL_TARGETING'. Not a DB "
                          "FK; STRATEGY_METADATA lives in code as the "
                          "source of truth for the ten strategies."),
        sa.Column("data_hash", sa.String(64), nullable=False,
                  comment="strategy_hash from strategy_results_cache "
                          "at the time of refresh"),
        sa.Column("construction_summary", sa.Text(), nullable=False,
                  comment="One-paragraph plain-English summary of what "
                          "the strategy does — signal, rebalance "
                          "frequency, and what makes it distinctive vs "
                          "the other nine. AI-generated."),
        sa.Column("portfolio_characteristics", sa.JSON(), nullable=False,
                  comment="Deterministic structured stats: avg_holdings, "
                          "avg_turnover_pct, avg_concentration, "
                          "rebalance_frequency."),
        sa.Column("behavioural_profile", sa.JSON(), nullable=False,
                  comment="AI-generated behavioural profile: "
                          "outperforms_when, underperforms_when, "
                          "primary_risk_factor, diversification_role."),
        sa.Column("regime_sensitivity", sa.Text(), nullable=False,
                  comment="One-sentence plain-English summary of "
                          "sensitivity to regime changes. AI-generated."),
        sa.Column("behavioural_tag", sa.String(120), nullable=False,
                  comment="Short plain-English descriptor for the "
                          "dashboard card — e.g. 'Momentum-driven, "
                          "performs in trending markets'. AI-generated."),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_unique_constraint(
        "uq_strategy_characterisations_strategy_hash",
        "strategy_characterisations",
        ["strategy_id", "data_hash"],
    )
    # Secondary read: every characterisation for the latest data_hash —
    # the Portfolio Profile fetch on the analytics page reads all ten
    # rows for the current hash in one query.
    op.create_index(
        "ix_strategy_characterisations_hash",
        "strategy_characterisations",
        ["data_hash"],
    )
    # Tertiary: the fallback read when the current data_hash has no
    # rows yet — pull the most recent characterisation per strategy
    # regardless of hash. The index orders by computed_at within each
    # strategy_id partition.
    op.create_index(
        "ix_strategy_characterisations_strategy_computed",
        "strategy_characterisations",
        ["strategy_id", sa.text("computed_at DESC")],
    )

    # ── Changelog — every migration must seed at least one entry ──────────────
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
        "version": 48,
        "released_at": datetime(2026, 5, 22, tzinfo=timezone.utc),
        "title": "Strategy context panels — per-strategy AI characterisations",
        "description": (
            "Every strategy now has a pre-computed Portfolio Profile: "
            "a plain-English construction summary, structured portfolio "
            "characteristics (avg holdings, turnover, concentration, "
            "rebalance frequency), a behavioural profile naming when "
            "the strategy outperforms / underperforms / dominates which "
            "Carhart factor / what role it plays in a multi-strategy "
            "portfolio, a one-sentence regime sensitivity statement, "
            "and a short behavioural tag. The same context flows into "
            "the council, explainer, academic-review and document-"
            "generation prompts when those sessions open against a "
            "specific strategy."
        ),
        "academic_rationale": (
            "The midpoint paper and Forest Capital presentation both "
            "need crisp answers to 'what does this strategy actually "
            "do, when does it work, and what does it add'. Pre-"
            "computing the answers — grounded in the strategy's "
            "actual backtest characteristics and factor loadings — "
            "means the team and the agents can both speak fluently "
            "about each strategy without re-deriving the picture "
            "each time."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 48")
    op.drop_index("ix_strategy_characterisations_strategy_computed",
                  table_name="strategy_characterisations")
    op.drop_index("ix_strategy_characterisations_hash",
                  table_name="strategy_characterisations")
    op.drop_constraint("uq_strategy_characterisations_strategy_hash",
                       "strategy_characterisations", type_="unique")
    op.drop_table("strategy_characterisations")
