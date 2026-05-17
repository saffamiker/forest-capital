"""Create the changelog table and seed every feature shipped to date.

The changelog is the source of record for what the platform can do and
WHY each capability matters academically. Two consumers:
  - the What's New modal (entries newer than the user last saw),
  - the Settings → Release History section (all entries).

CHANGELOG CONTRACT: every migration from here on must insert at least
one changelog row — the CI changelog gate (scripts/changelog_gate.py)
fails a migration that does not. Each row's academic_rationale must
explain, concretely, how the feature helps the team earn higher marks.

The seed below reconstructs the feature history from the git log, in
chronological order by commit date, versions 1-30. Migration 012 adds
version 31 (the CI/CD pipeline).

Revision ID: 011
Revises: 010
Create Date: 2026-05-17
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | None = None
depends_on: str | None = None


def _d(year: int, month: int, day: int) -> datetime:
    """A timezone-aware datetime — asyncpg binds the TIMESTAMP column from
    a datetime, never a string."""
    return datetime(year, month, day, tzinfo=timezone.utc)


# version, released_at, title, description, academic_rationale
_SEED: list[tuple[int, datetime, str, str, str]] = [
    (1, _d(2026, 5, 10), "Portfolio Intelligence System launch",
     "The platform's first release — TypeScript-strict frontend, FastAPI "
     "backend, three-mode interface, and the project scaffold.",
     "Establishes a professional, reproducible analytical environment. A "
     "credible toolchain is the foundation graders expect before they "
     "weigh any finding."),
    (2, _d(2026, 5, 11), "Dashboard and strategy performance views",
     "The main dashboard with the ten portfolio strategies, their "
     "risk-adjusted metrics, and the strategy comparison table.",
     "Puts the core comparison — ten strategies against the benchmark — "
     "in one defensible view, the evidence base for the central research "
     "question."),
    (3, _d(2026, 5, 11), "Data provenance annotations",
     "Every data series carries a runtime-recorded source; the frontend "
     "displays provenance rather than hardcoding it.",
     "Transparent data provenance is a graded rigour criterion of the "
     "Analytical Appendix — a grader can trace every number to its origin."),
    (4, _d(2026, 5, 12), "AI Council of Experts",
     "Six specialist agents deliberate on portfolio questions, with an "
     "independent dissenting view.",
     "Multi-agent deliberation surfaces blind spots a single analyst "
     "would miss — and is the heart of the AI-use narrative the final "
     "presentation requires."),
    (5, _d(2026, 5, 12), "QA Audit feature",
     "A 30-point methodology audit that scores the analysis against "
     "statistical and backtesting best practice.",
     "Demonstrates the methodology has been independently checked — "
     "pre-empting the exact objections an investment-professional panel "
     "would raise."),
    (6, _d(2026, 5, 13), "Statistical Evidence page",
     "Six charts presenting significance tests, cross-validation, and the "
     "deflated Sharpe ratio.",
     "Answers 'how do you know it works?' with academic-grade evidence — "
     "the rigour the Analytical Appendix is marked on."),
    (7, _d(2026, 5, 13), "Regime Analysis page",
     "Six charts showing how each strategy performs across bull, bear, "
     "high-volatility, and rising-rate regimes.",
     "Shows findings are not an artefact of one market period — "
     "regime-conditional evidence strengthens every conclusion drawn for "
     "the deliverables."),
    (8, _d(2026, 5, 13), "Reports view",
     "A workspace generating the midpoint paper, executive brief, and "
     "presentation artefacts.",
     "Turns the analysis directly into the graded written deliverables, "
     "keeping every figure traceable to the underlying data."),
    (9, _d(2026, 5, 15), "Efficient frontier",
     "A risk-return frontier plotting the ten strategies against the set "
     "of optimal static portfolios.",
     "Frames the dynamic strategies against modern portfolio theory — the "
     "theoretical baseline a finance panel will expect to see."),
    (10, _d(2026, 5, 16), "Efficient frontier fix",
     "Corrects the optimisation to sweep the full feasible long-only "
     "space rather than a capped sliver.",
     "Corrects the portfolio optimisation to span the full feasible set. "
     "The tangency portfolio and risk-return scatter now accurately "
     "represent analytical findings — a wrong efficient frontier would "
     "undermine the credibility of the entire static allocation analysis."),
    (11, _d(2026, 5, 16), "Academic Analytics page",
     "Six analytics components — summary statistics, rolling correlation, "
     "regime-conditional performance, drawdowns, and factor loadings.",
     "Consolidates the quantitative backbone of the midpoint paper into "
     "one CSV-exportable view, so the written analysis cites verified "
     "numbers."),
    (12, _d(2026, 5, 16), "Document upload for agent context",
     "Uploaded requirements and rubric documents are injected into every "
     "AI agent's context.",
     "Every agent now reasons against the actual grading criteria — "
     "feedback is aligned to the rubric the work is marked on."),
    (13, _d(2026, 5, 16), "Markdown upload support",
     "The document upload accepts Markdown (.md) files alongside PDFs.",
     "Lets the team feed plain-text rubrics and drafts to the agents "
     "without conversion friction, keeping agent context current."),
    (14, _d(2026, 5, 16), "Settings page",
     "A single Settings page — organisation branding, data status, "
     "analytics configuration, and academic documents.",
     "Centralises the operational controls, so the team can verify data "
     "freshness and assumptions before citing results in a deliverable."),
    (15, _d(2026, 5, 16), "Data status admin view",
     "A read-only data-table health view — row counts, date ranges, and "
     "staleness per table.",
     "Lets the team confirm the dataset is current before writing the "
     "appendix — stale data behind a graded number is a credibility risk."),
    (16, _d(2026, 5, 16), "Navigation ribbon grouping",
     "The navigation ribbon was visually regrouped and the settings gear "
     "rewired to the Settings page.",
     "A coherent interface reads as a professional consultancy tool — the "
     "standard the final presentation is judged against."),
    (17, _d(2026, 5, 16), "Navigation reorder — Analytics second",
     "The Analytics page moved to the second navigation position, "
     "directly after the Dashboard.",
     "Surfaces the quantitative backbone of the paper one click from the "
     "landing view, the order a reviewer naturally follows."),
    (18, _d(2026, 5, 16), "Academic Review endpoint",
     "The council evaluates the project's academic readiness — a peer "
     "fan-out plus an arbiter verdict mapped to the rubric.",
     "Gives the team a rubric-mapped readiness check before each "
     "deadline, turning the grading criteria into an actionable checklist."),
    (19, _d(2026, 5, 16), "Cumulative total return chart",
     "Growth-of-$1 cumulative return for every strategy over the full "
     "study period.",
     "The single clearest visual of long-run outperformance — a "
     "presentation centrepiece that needs no statistical background to "
     "read."),
    (20, _d(2026, 5, 16), "Excess return and rolling excess chart",
     "Annual excess return versus the benchmark, plus a 12-month rolling "
     "excess-return chart.",
     "Directly answers the research question — did diversification beat "
     "100% equity — and shows when, not just whether."),
    (21, _d(2026, 5, 16), "Information ratio",
     "The information ratio added to the summary statistics — excess "
     "return per unit of tracking error.",
     "Measures the consistency of outperformance, the metric a "
     "professional panel uses to separate skill from luck."),
    (22, _d(2026, 5, 16), "Carhart four-factor model and true turnover",
     "Factor regressions upgraded to the Carhart four-factor model "
     "(adding momentum), and a genuine weight-change turnover figure.",
     "The four-factor model is the academic standard for attribution; "
     "true turnover makes the transaction-cost story defensible under "
     "scrutiny."),
    (23, _d(2026, 5, 16), "Parameter sensitivity analysis",
     "Shows how each dynamic strategy's Sharpe ratio responds as its key "
     "parameter is swept around its setting.",
     "Demonstrates the strategies are not overfit to one parameter "
     "choice — robustness evidence that pre-empts an overfitting "
     "objection."),
    (24, _d(2026, 5, 16), "Strategy methodology panel",
     "A source-controlled record of every strategy's construction logic, "
     "signal, and economic intuition.",
     "Gives the written report a single authoritative description of "
     "each strategy — the methodology section graders mark for clarity."),
    (25, _d(2026, 5, 16), "Team Activity logging",
     "A timestamped record of every team member's interactions with the "
     "platform, plus commit history.",
     "Creates an objective, timestamped record of every team member's "
     "engagement with the platform. Directly supports the Roles and "
     "Division of Labor section and the AI use narrative required at the "
     "final presentation."),
    (26, _d(2026, 5, 16), "Activity visualisation dashboard",
     "Charts of activity over time, the team contribution split, and "
     "agent engagement, with a presentation view.",
     "Turns the engagement record into presentation-ready evidence of "
     "shared effort for the AI-use narrative."),
    (27, _d(2026, 5, 16), "Testing Mode",
     "A per-session Testing Mode that bands activity as testing and "
     "excludes it from the analytical record.",
     "Keeps exploratory clicking out of the Team Activity evidence, so "
     "the division-of-labour record reflects genuine analytical work."),
    (28, _d(2026, 5, 16), "Contextual explainer tooltips",
     "An info icon on every chart, table column, and metric — hover for a "
     "definition, click for a live data-anchored explanation.",
     "Lets every team member understand any metric in context — raising "
     "the quality of the interpretation that reaches the written "
     "deliverables."),
    (29, _d(2026, 5, 17), "Generator-Evaluator Harness",
     "Every council response and Academic Review verdict is scored and "
     "retried before it reaches the team.",
     "Every council response and Academic Review verdict is now scored "
     "and retried before reaching the team. Raises the quality floor on "
     "all AI-generated analytical feedback — directly improving the "
     "accuracy of insights used to prepare deliverables."),
    (30, _d(2026, 5, 17), "Changelog and What's New",
     "A release history in Settings and a What's New modal that surfaces "
     "features added since each user's last visit.",
     "Keeps the whole team aware of every capability and why it matters "
     "academically — so no graded feature goes unused before a deadline."),
]


def upgrade() -> None:
    op.create_table(
        "changelog",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("version", sa.Integer(), nullable=False, unique=True,
                  comment="Monotonic release number — ordering key for display"),
        sa.Column("released_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "academic_rationale",
            sa.Text(),
            nullable=False,
            comment="Why this feature helps the team earn higher marks — "
                    "required on every changelog row by the CI changelog gate",
        ),
        sa.Column("tour_step_id", sa.String(100), nullable=True,
                  comment="Links the entry to a site-tour step, once the tour exists"),
    )

    # Seed the full feature history. op.bulk_insert into the changelog
    # table — this is the INSERT the changelog gate requires of every
    # migration.
    changelog = sa.table(
        "changelog",
        sa.column("version", sa.Integer),
        sa.column("released_at", sa.TIMESTAMP(timezone=True)),
        sa.column("title", sa.String),
        sa.column("description", sa.Text),
        sa.column("academic_rationale", sa.Text),
        sa.column("tour_step_id", sa.String),
    )
    op.bulk_insert(changelog, [
        {
            "version": v, "released_at": ts, "title": title,
            "description": desc, "academic_rationale": rationale,
            "tour_step_id": None,
        }
        for (v, ts, title, desc, rationale) in _SEED
    ])


def downgrade() -> None:
    op.drop_table("changelog")
