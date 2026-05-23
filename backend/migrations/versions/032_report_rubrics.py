"""Report rubrics — rubric storage for the report writer's academic review.

May 22 2026 (item 12 commit 2 — academic review integration).

The report writer's Step 10 (Run Academic Review) scores the draft
against a rubric. The rubric for the midpoint paper template is the
FNA670 Midpoint Check rubric — four criteria: clarity and rigor,
analytical progress, results quality, division of labor. Peer feedback
quality is graded at the meetup, not in the written submission, so it
is excluded.

This migration is the rubric's storage layer:

  report_rubrics — (template_id, version) UNIQUE. Stores both the
    parsed criteria JSON and the original uploaded rubric text. The
    review endpoint queries by template_id and uses the latest active
    version; older versions stay in the table as the rubric audit trail.

Seeds the FNA670 midpoint rubric so the academic review feature works
out of the box. Future templates upload their own rubrics via the
report writer's Upload Rubric button.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json

from alembic import op
import sqlalchemy as sa


revision: str = "032"
down_revision: str | None = "031"
branch_labels: str | None = None
depends_on: str | None = None


# The FNA670 midpoint rubric — four criteria, structured.

_FNA670_MIDPOINT_CRITERIA = [
    {
        "criterion_id": "clarity_and_rigor",
        "section": "all",
        "description": (
            "Clarity and rigor of the written submission. Graduate-level "
            "academic writing, precise vocabulary, active voice, no "
            "hedging on supported findings, every claim backed by a "
            "specific verified number."),
        "weight": None,
        "indicators_of_success": [
            "Every paragraph carries at least one specific number or "
            "citation.",
            "Active voice throughout — passive constructions only where "
            "the agent is genuinely unknown.",
            "No use of 'interesting', 'notable', or other non-specific "
            "filler language.",
            "Word budgets respected for each section.",
        ],
    },
    {
        "criterion_id": "analytical_progress",
        "section": "section_2",
        "description": (
            "Evidence of meaningful analytical progress. The paper "
            "demonstrates that the team has built infrastructure, "
            "produced findings, and interpreted them — not just framed "
            "a research question."),
        "weight": None,
        "indicators_of_success": [
            "Section 2 contains specific verified figures from the "
            "platform analytics.",
            "Findings are interpreted in terms of their implication "
            "for the capital planning mandate.",
            "The central thesis is supported by the analytical work, "
            "not asserted independently.",
            "Appendix B (Full Analytical Findings) demonstrates depth "
            "beyond what fits in the three-page main paper.",
        ],
    },
    {
        "criterion_id": "results_quality",
        "section": "section_2",
        "description": (
            "Quality of preliminary results and interpretation. Results "
            "are presented in a way that supports the thesis without "
            "overstating what the data shows."),
        "weight": None,
        "indicators_of_success": [
            "Section 2 leads with the highest-strength finding from "
            "the ranked findings list.",
            "Every quantitative claim cross-references the source "
            "(Appendix B, F1; verified_data.field).",
            "Interpretation distinguishes between what the data shows "
            "and what conventional theory would predict.",
            "Surprises (findings that contradict conventional theory) "
            "are flagged as such, not normalised.",
        ],
    },
    {
        "criterion_id": "division_of_labor",
        "section": "section_3",
        "description": (
            "Clear division of labor within the group. The reader can "
            "tell who did what, with concrete evidence drawn from the "
            "platform activity log."),
        "weight": None,
        "indicators_of_success": [
            "All three team members named with specific roles.",
            "Activity evidence block uses verified counts from the "
            "platform team_activity (commits, UAT steps, council "
            "sessions, failure reports, etc.).",
            "Coordination mechanism described in one sentence.",
            "Cross-reference to Appendix C for the full auditable "
            "activity log.",
        ],
    },
]


_FNA670_RUBRIC_TEXT = """FNA670 Industry Practicum — Midpoint Check Rubric

The midpoint check is graded against four written criteria. Peer
feedback quality is assessed at the meetup, not in the written
submission.

CRITERION 1 — CLARITY AND RIGOR
Clarity and rigor of the written submission. Graduate-level academic
writing, precise vocabulary, active voice, no hedging on supported
findings, every claim backed by a specific verified number. Word
budgets respected for each section.

CRITERION 2 — ANALYTICAL PROGRESS
Evidence of meaningful analytical progress. The paper demonstrates
that the team has built infrastructure, produced findings, and
interpreted them — not just framed a research question.

CRITERION 3 — RESULTS QUALITY
Quality of preliminary results and interpretation. Results are
presented in a way that supports the thesis without overstating what
the data shows. Section 2 leads with the highest-strength finding;
surprises are flagged as such.

CRITERION 4 — DIVISION OF LABOR
Clear division of labor within the group. The reader can tell who did
what, with concrete evidence drawn from the platform activity log.
All three team members named with specific roles, activity evidence
block uses verified counts, coordination mechanism described.

FORMAT REQUIREMENTS
  Three pages, double-spaced, 12-point font.
  Section 1 (Data and Methodology): one page (≈ 250 words).
  Section 2 (Preliminary Results and Diagnostics): one page (≈ 300 words).
  Section 3 (Roles and Division of Labor): half page (≈ 150 words).
  Section 4 (Next Steps and Open Questions): half page (≈ 125 words).
  References and appendix are excluded from the page count.
"""


def upgrade() -> None:
    op.create_table(
        "report_rubrics",
        sa.Column("id", sa.BigInteger(), primary_key=True,
                  autoincrement=True),
        sa.Column("template_id", sa.String(80), nullable=False,
                  comment="report_templates.template_id this rubric "
                          "scores against. Not a FK so a template "
                          "archive does not orphan rubrics."),
        sa.Column("version", sa.Integer(), nullable=False,
                  server_default="1",
                  comment="Monotonic per-template version. Each upload "
                          "increments; the latest version is queried "
                          "by the academic review endpoint."),
        sa.Column("rubric_text", sa.Text(), nullable=False,
                  comment="Raw rubric as text (PDF/docx extracted on "
                          "upload). Carried so a reviewer can read "
                          "the original alongside the parsed criteria."),
        sa.Column("criteria", sa.JSON(), nullable=False,
                  server_default=sa.text("'[]'::jsonb"),
                  comment="Parsed structured criteria list — each entry "
                          "{criterion_id, section, description, weight, "
                          "indicators_of_success}. The review agent "
                          "scores each criterion separately."),
        sa.Column("uploaded_by", sa.String(255), nullable=True,
                  comment="Email of the user who uploaded the rubric."),
        sa.Column("source_filename", sa.String(500), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False,
                  server_default=sa.true(),
                  comment="Allows soft-deactivation of obsolete "
                          "rubric versions without losing history."),
        sa.Column("uploaded_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("template_id", "version",
                            name="uq_report_rubrics_template_version"),
    )
    op.create_index(
        "ix_report_rubrics_template_active",
        "report_rubrics",
        ["template_id", "active", sa.text("version DESC")])

    # Also extend report_generations with academic-review fields so the
    # review result + readiness flag persist alongside the draft.
    op.add_column(
        "report_generations",
        sa.Column("academic_review", sa.JSON(), nullable=True,
                  comment="Full review payload from the latest "
                          "/academic-review run — per-criterion scores, "
                          "gaps, suggestions, flag lists. NULL until "
                          "Step 10 has been run."))
    op.add_column(
        "report_generations",
        sa.Column("academic_readiness", sa.String(80), nullable=True,
                  comment="Overall readiness from the latest review — "
                          "ready_to_submit | needs_minor_revision | "
                          "needs_significant_revision. Drives the "
                          "download gate alongside flag_count."))
    op.add_column(
        "report_generations",
        sa.Column("academic_review_at", sa.TIMESTAMP(timezone=True),
                  nullable=True))

    # Seed the FNA670 midpoint rubric so the review endpoint works on
    # day one — no upload required before the first review.
    rubrics = sa.table(
        "report_rubrics",
        sa.column("template_id",     sa.String),
        sa.column("version",         sa.Integer),
        sa.column("rubric_text",     sa.Text),
        sa.column("criteria",        sa.JSON),
        sa.column("uploaded_by",     sa.String),
        sa.column("source_filename", sa.String),
        sa.column("active",          sa.Boolean),
    )
    op.bulk_insert(rubrics, [{
        "template_id":     "midpoint_check_fna670",
        "version":         1,
        "rubric_text":     _FNA670_RUBRIC_TEXT,
        "criteria":        json.dumps(_FNA670_MIDPOINT_CRITERIA),
        "uploaded_by":     "system",
        "source_filename": "FNA670_midpoint_rubric_seeded.txt",
        "active":          True,
    }])

    # Changelog.
    changelog = sa.table(
        "changelog",
        sa.column("version",            sa.Integer),
        sa.column("released_at",        sa.TIMESTAMP(timezone=True)),
        sa.column("title",              sa.String),
        sa.column("description",        sa.Text),
        sa.column("academic_rationale", sa.Text),
        sa.column("tour_step_id",       sa.String),
    )
    op.bulk_insert(changelog, [{
        "version": 51,
        "released_at": datetime(2026, 5, 22, tzinfo=timezone.utc),
        "title": "Report writer — academic review integration",
        "description": (
            "The report writer gains an academic review step that "
            "scores the draft against the FNA670 midpoint rubric "
            "before submission. Four criteria graded Strong / "
            "Developing / Needs Work with specific gap analysis, "
            "actionable suggestions, and a soft download gate on "
            "Needs Significant Revision. Bob can apply suggestions "
            "inline and re-run the review until readiness is "
            "Ready to Submit."
        ),
        "academic_rationale": (
            "A graded paper that passes the regex post-check on "
            "numbers and citations is not necessarily a strong paper. "
            "Surfacing the rubric criteria as first-class scores in "
            "the editor means Bob iterates against the same standard "
            "the grader will apply, with traceable evidence and "
            "concrete suggestions for each gap. The soft gate on "
            "Needs Significant Revision is recorded if Bob overrides "
            "it, preserving the audit trail without blocking a "
            "deliberate submission."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 51")
    op.drop_column("report_generations", "academic_review_at")
    op.drop_column("report_generations", "academic_readiness")
    op.drop_column("report_generations", "academic_review")
    op.drop_index("ix_report_rubrics_template_active",
                  table_name="report_rubrics")
    op.drop_table("report_rubrics")
