"""Create Sprint 5 tables: caching, auth logging, and JTI persistence.

Four new tables to support Sprint 5 features:
  strategy_results_cache  — skips run_all_strategies() on hash match; survives restarts
  regime_signals_cache    — 15-min TTL on FRED signals; avoids 30-60s FRED timeouts
  auth_attempts           — IP geolocking and rate-limit audit trail for /admin screen
  used_magic_tokens       — persists consumed JTIs so scanner pre-fetch protection
                            survives Render restarts (previously lost on each deploy)

Revision ID: 002
Revises: 001
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── 1. strategy_results_cache ─────────────────────────────────────────
    # Caches the full run_all_strategies() output keyed by a hash of the
    # underlying market data.  A cache hit returns ~200ms; a miss triggers
    # recomputation which can take 30s+.  Survives Render restarts because
    # it lives in PostgreSQL, not Python memory.
    op.create_table(
        "strategy_results_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "strategy_hash",
            sa.String(64),
            nullable=False,
            unique=True,
            comment="SHA-256 of the market_data_monthly rows used for computation",
        ),
        sa.Column(
            "results_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Full dict[str, dict] output of run_all_strategies()",
        ),
        sa.Column(
            "computed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "n_strategies",
            sa.Integer(),
            nullable=False,
            comment="Number of strategies in results_json — sanity check",
        ),
        sa.Column(
            "n_observations",
            sa.Integer(),
            nullable=True,
            comment="Monthly obs count used — cross-check against min_obs threshold",
        ),
    )
    op.create_index(
        "ix_strategy_results_cache_hash",
        "strategy_results_cache",
        ["strategy_hash"],
    )

    # ── 2. regime_signals_cache ───────────────────────────────────────────
    # Caches the current regime classification for 15 minutes.  Without this
    # cache, a FRED timeout (30-60s) blocks every /api/regime/current request.
    # The backend checks expires_at before calling FRED — on Render restarts
    # the table is populated from the last pre-restart fetch.
    op.create_table(
        "regime_signals_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("threshold_regime", sa.String(20), nullable=False),
        sa.Column("hmm_regime", sa.Integer(), nullable=True),
        sa.Column(
            "hmm_probabilities",
            postgresql.ARRAY(sa.Float()),
            nullable=True,
        ),
        sa.Column("regimes_agree", sa.Boolean(), nullable=False),
        sa.Column("vix_level", sa.Float(), nullable=True),
        sa.Column("yield_curve_slope", sa.Float(), nullable=True),
        sa.Column("credit_spread", sa.Float(), nullable=True),
        sa.Column("equity_trend", sa.Float(), nullable=True),
        sa.Column("pre_2022_avg_correlation", sa.Float(), nullable=True),
        sa.Column("post_2022_avg_correlation", sa.Float(), nullable=True),
        sa.Column(
            "fetched_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="fetched_at + 15 minutes — matches backend in-process TTL",
        ),
    )

    # ── 3. auth_attempts ──────────────────────────────────────────────────
    # Audit log for every /auth/request-link call.  Supports the /admin
    # screen's Auth Attempts section, IP geolocking enforcement, and
    # per-IP rate limiting for rejected attempts.
    op.create_table(
        "auth_attempts",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "timestamp",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "email",
            sa.String(255),
            nullable=False,
            comment="Full email for admin review; hashed in application logs",
        ),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        # Geolocation fields populated from ip-api.com (fail-open if unavailable)
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("country_code", sa.String(2), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("isp", sa.String(200), nullable=True),
        sa.Column("org", sa.String(200), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            comment="sent | rejected | geo_blocked | rate_blocked",
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default="1",
            nullable=False,
            comment="How many times this IP has attempted today",
        ),
    )
    op.create_index(
        "ix_auth_attempts_timestamp",
        "auth_attempts",
        ["timestamp"],
    )
    op.create_index(
        "ix_auth_attempts_ip_status",
        "auth_attempts",
        ["ip_address", "status"],
    )

    # ── 4. used_magic_tokens ──────────────────────────────────────────────
    # Persists consumed magic link JTIs so single-use enforcement survives
    # Render restarts.  Previously stored in a Python dict that died on
    # every deploy, allowing scanner pre-fetch to break the single-use
    # property on the first request after a redeploy.
    op.create_table(
        "used_magic_tokens",
        sa.Column("jti", sa.String(255), primary_key=True),
        sa.Column(
            "redeemed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Original token expiry — rows older than this can be pruned",
        ),
        sa.Column(
            "email_hash",
            sa.String(64),
            nullable=True,
            comment="SHA-256 of email — for audit without storing plaintext",
        ),
    )
    op.create_index(
        "ix_used_magic_tokens_expires_at",
        "used_magic_tokens",
        ["expires_at"],
        comment="Supports periodic cleanup of expired tokens",
    )


def downgrade() -> None:
    op.drop_table("used_magic_tokens")
    op.drop_table("auth_attempts")
    op.drop_table("regime_signals_cache")
    op.drop_table("strategy_results_cache")
