"""Collaborative editing + version control for report paper_md.

May 23 2026 (item 2 — collaborative editing + version control).

Two additions:

1. New table `report_paper_versions` — one row per saved snapshot of
   a generation's paper_md. Snapshots are created either manually
   (the reviewer hits Save Version) or implicitly when an iterate /
   resolve-bob / inline-edit changes the paper, so the history is
   always restorable. Restoring a prior version creates a NEW
   version entry pointing at it — the audit trail is never lost.

2. New column `report_generations.paper_revision` — integer
   counter, incremented on every paper_md change. The PATCH endpoint
   accepts an `expected_revision` parameter and returns 409 if the
   caller's revision is stale (someone else saved while they were
   editing). The frontend uses this for concurrent-edit detection
   and offers a "refresh and re-apply" dialog.

The version table is fully additive — existing endpoints continue
to work unchanged, and a generation with zero version rows just
shows an empty history list.

Downgrade drops the table + column; the older single-version
behaviour resumes.
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "037"
down_revision: str | None = "036"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── 1. New table — report_paper_versions ──────────────────────────────────
    op.create_table(
        "report_paper_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True,
                  autoincrement=True),
        sa.Column("generation_id", sa.BigInteger(), nullable=False,
                  comment="FK to report_generations. CASCADE on "
                          "delete so a removed generation's history "
                          "vanishes too."),
        sa.Column("version_number", sa.Integer(), nullable=False,
                  comment="Monotonic 1-based counter per "
                          "generation_id. The first save is v1, the "
                          "next v2, and so on. Restoring a prior "
                          "version creates a new entry at the next "
                          "version number — the older row is never "
                          "overwritten."),
        sa.Column("paper_md", sa.Text(), nullable=False,
                  comment="The complete paper_md at this point in "
                          "time. Stored as a full snapshot rather "
                          "than a diff so a restore is a single "
                          "read + write rather than a replay."),
        sa.Column("flag_count", sa.Integer(), nullable=False,
                  server_default="0",
                  comment="The flag_count at this snapshot — "
                          "useful for the version history UI to "
                          "show 'this version had 5 unresolved "
                          "markers' without re-running the post-"
                          "check."),
        sa.Column("word_counts", sa.JSON(), nullable=False,
                  server_default=sa.text("'{}'::jsonb"),
                  comment="Per-section word counts at this "
                          "snapshot."),
        sa.Column("saved_by_email", sa.String(255), nullable=True,
                  comment="The email of the reviewer who triggered "
                          "the save. NULL on a system-triggered "
                          "snapshot (e.g. the auto-snapshot on "
                          "iterate-text accept)."),
        sa.Column("saved_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("label", sa.String(120), nullable=True,
                  comment="Optional human-readable label "
                          "('pre-Molly-edit', 'after Section 2 "
                          "rewrite'). NULL on system snapshots."),
        sa.Column("source", sa.String(40), nullable=False,
                  server_default="manual",
                  comment="Why the snapshot was created. One of: "
                          "manual (reviewer hit Save), "
                          "auto_iterate (iterate-text accept), "
                          "auto_resolve_bob (BOB block resolution), "
                          "auto_edit (inline paper_md PATCH), "
                          "restore (created by a restore action — "
                          "label carries the source version)."),
        sa.Column("restored_from_version", sa.Integer(), nullable=True,
                  comment="When source = restore, the version_number "
                          "that was restored to produce this row."),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["report_generations.id"],
            ondelete="CASCADE"),
        sa.UniqueConstraint(
            "generation_id", "version_number",
            name="uq_paper_versions_gen_ver"),
    )
    op.create_index(
        "ix_paper_versions_gen_saved",
        "report_paper_versions",
        ["generation_id", sa.text("saved_at DESC")])

    # ── 2. New column — report_generations.paper_revision ────────────────────
    op.add_column(
        "report_generations",
        sa.Column("paper_revision", sa.Integer(), nullable=False,
                  server_default="0",
                  comment="Optimistic-concurrency counter. The PATCH "
                          "paper-md endpoint accepts an "
                          "expected_revision body field and returns "
                          "409 when it does not match — that means "
                          "another reviewer's save landed between "
                          "the GET and the PATCH."),
    )
    # Backfill existing rows: every generation that already exists
    # gets paper_revision=0 from the server_default. No further
    # work needed since the migration is additive only.

    # ── Changelog ─────────────────────────────────────────────────────────────
    op.execute(sa.text(
        "INSERT INTO changelog "
        "(version, released_at, title, description, "
        " academic_rationale, tour_step_id) "
        "VALUES (:v, :rel, :t, :d, :a, NULL)"
    ).bindparams(
        v=56,
        rel=datetime.now(timezone.utc),
        t="Collaborative editing + version control",
        d=(
            "Every paper save now creates a versioned snapshot in "
            "the new report_paper_versions table. The version "
            "history panel lets a reviewer browse, preview, and "
            "restore any prior snapshot — a restore creates a new "
            "version entry rather than overwriting history. The "
            "paper-md PATCH endpoint now carries a revision counter "
            "so two reviewers editing simultaneously get a 409 "
            "instead of silently overwriting each other."),
        a=(
            "Bob's midpoint paper deserves an audit trail. The "
            "version history surfaces every significant edit and "
            "the optimistic-concurrency check prevents Molly's "
            "in-progress copy edits from being clobbered by Bob's "
            "AI-iterated accept."),
    ))


def downgrade() -> None:
    op.drop_column("report_generations", "paper_revision")
    op.drop_index("ix_paper_versions_gen_saved",
                  table_name="report_paper_versions")
    op.drop_table("report_paper_versions")
    op.execute("DELETE FROM changelog WHERE version = 56")
