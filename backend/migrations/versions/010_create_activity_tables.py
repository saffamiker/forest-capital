"""Create the Team Activity logging tables.

Three append-only tables back the Team Activity feature — the objective
evidence of how the practicum team engaged with the platform, used as
input to the Roles & Division of Labor deliverable and the AI-use
narrative for the July 1 presentation.

  session_events     — UI telemetry: logins, logouts, page views,
                       feature clicks, exports. Batched from the
                       frontend, inserted by POST /api/v1/activity/events.
  agent_interactions — substantive AI work: council runs, academic
                       reviews, document uploads, QA audits. Logged
                       server-side, non-blocking, from the agent
                       endpoints themselves.
  commit_activity    — git history, populated by the GitHub push
                       webhook and the manual sync endpoint.

A user is identified by EMAIL, not a foreign key: the project has no
users table — every existing per-user table (auth_attempts,
documents.owner_email) keys on the email string, so these follow suit.

session_id is the frontend-generated UUID that groups one login
session — distinct from the JWT's internal session_id. It is stored as
a 36-char string so a raw text() insert binds it without a uuid cast.

session_type ("analytical" | "testing") travels per request from the
frontend; it is never persisted as a user preference — Testing Mode
resets to analytical on every login.

Revision ID: 010
Revises: 009
Create Date: 2026-05-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── session_events — UI telemetry ─────────────────────────────────────
    op.create_table(
        "session_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_email",
            sa.String(255),
            nullable=False,
            comment="Authenticated user's email — the identity key "
                    "(no users table exists; email is the project-wide identity)",
        ),
        sa.Column(
            "session_id",
            sa.String(36),
            nullable=False,
            comment="Frontend-generated UUID grouping one login session",
        ),
        sa.Column(
            "session_type",
            sa.String(20),
            nullable=False,
            server_default="analytical",
            comment="analytical | testing — sent per request, never a stored preference",
        ),
        sa.Column(
            "event_type",
            sa.String(40),
            nullable=False,
            comment="login | logout | page_view | feature_click | export | login_failed",
        ),
        sa.Column(
            "timestamp",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("page", sa.String(255), nullable=True,
                  comment="Current route at the time of the event"),
        sa.Column("feature", sa.String(120), nullable=True,
                  comment="Feature identifier for feature_click / export events"),
        sa.Column("duration_seconds", sa.Integer(), nullable=True,
                  comment="Time spent on the previous route (page_view events)"),
        sa.Column("ip_address", sa.String(64), nullable=True,
                  comment="login / logout events only"),
        sa.Column("user_agent", sa.Text(), nullable=True,
                  comment="login events only"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_session_events_timestamp", "session_events", ["timestamp"])
    op.create_index("ix_session_events_user_email", "session_events", ["user_email"])
    op.create_index("ix_session_events_session_type", "session_events",
                    ["session_type"])

    # ── agent_interactions — substantive AI work ──────────────────────────
    op.create_table(
        "agent_interactions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_email", sa.String(255), nullable=False,
                  comment="Authenticated user's email — the identity key"),
        sa.Column("session_id", sa.String(36), nullable=False,
                  comment="Frontend-generated UUID grouping one login session"),
        sa.Column(
            "session_type",
            sa.String(20),
            nullable=False,
            server_default="analytical",
            comment="analytical | testing",
        ),
        sa.Column(
            "interaction_type",
            sa.String(40),
            nullable=False,
            comment="council | academic_review | qa | document_upload",
        ),
        sa.Column(
            "timestamp",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("question_text", sa.Text(), nullable=True),
        sa.Column("agents_involved", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=True, comment="List of agent identifiers consulted"),
        sa.Column("response_summary", sa.Text(), nullable=True,
                  comment="First 500 chars of the response"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_agent_interactions_timestamp", "agent_interactions",
                    ["timestamp"])
    op.create_index("ix_agent_interactions_user_email", "agent_interactions",
                    ["user_email"])
    op.create_index("ix_agent_interactions_session_type", "agent_interactions",
                    ["session_type"])

    # ── commit_activity — git history ─────────────────────────────────────
    # Populated by the GitHub push webhook and the manual sync endpoint.
    # No team-email filter applies here: commits are attributed by git
    # author name, and every commit on the branch is logged regardless.
    op.create_table(
        "commit_activity",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("sha", sa.String(40), nullable=False, unique=True,
                  comment="Full 40-char commit SHA — upsert key"),
        sa.Column("author", sa.String(255), nullable=False,
                  comment="git author email — resolved through "
                          "GIT_AUTHOR_EMAIL_MAP to a platform identity for "
                          "display; shown as-is when unmapped"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.TIMESTAMP(timezone=True), nullable=False,
                  comment="git author date"),
        sa.Column("files_changed", sa.Integer(), nullable=True),
        sa.Column("insertions", sa.Integer(), nullable=True),
        sa.Column("deletions", sa.Integer(), nullable=True),
        sa.Column("github_url", sa.Text(), nullable=True),
        sa.Column("branch", sa.String(120), nullable=False, server_default="main"),
        sa.Column(
            "synced_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_commit_activity_timestamp", "commit_activity", ["timestamp"])


def downgrade() -> None:
    op.drop_table("commit_activity")
    op.drop_table("agent_interactions")
    op.drop_table("session_events")
