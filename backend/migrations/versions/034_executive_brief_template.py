"""Executive Brief template — second report template seeded.

May 22 2026 (item 12 commit D).

Seeds a new report_templates row (executive_brief_fna670) and a
matching report_rubrics row. The executive brief uses the same
eleven-step pipeline, the same /reports/writer UI, and the same
/generate / /final-check / /academic-review endpoints — only the
template_id, system prompt, rubric, and docx formatter differ.

Key distinctions from the midpoint paper:
  Audience       Forest Capital leadership (sophisticated but
                 non-quantitative; capital allocation decisions)
  Format         2-page memo, Calibri 11pt single-spaced
  Section count  5 sections + a TO/FROM/DATE/RE header
  Word budget    490 total (60/180/80/80/90)
  Tone           Direct, declarative, no hedging, no academic
                 framing, no methodology unless essential
  Rubric         Four criteria graded against professional advisory
                 standards: executive_clarity, actionability,
                 evidence_quality, brevity

The frontend Template dropdown auto-renders this row because it
queries GET /api/v1/reports/templates — no code change required
to make it selectable.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json

from alembic import op
import sqlalchemy as sa


revision: str = "034"
down_revision: str | None = "033"
branch_labels: str | None = None
depends_on: str | None = None


# ── Executive Brief system prompt ───────────────────────────────────────────


_EXEC_BRIEF_SYSTEM_PROMPT = """You are writing an executive brief for \
Forest Capital leadership summarising the key findings from the FNA670 \
Industry Practicum portfolio analysis.

AUDIENCE:
Forest Capital senior leadership. Financially sophisticated but not
quantitative researchers. They make capital allocation decisions. They
have 5 minutes to read this document.

TONE:
Professional advisory. Direct. Confident. Every sentence earns its
place. No academic hedging. No methodology explanation unless essential
to the finding. No passive voice. Active, declarative sentences
throughout.

CENTRAL MESSAGE:
"A diversified multi-strategy allocation materially reduces tail risk
without sacrificing long-run return. The post-2022 correlation regime
shift makes dynamic strategy selection a current operational priority,
not a future refinement."

VERIFIED DATA:
{verified_data}

Use ONLY these figures. Every bullet in Section 2 must contain exactly
one number from this block.

RANKED FINDINGS:
{ranked_findings}

The HIGH-strength findings (F1, F2, F3, F4, F6, F8 in typical runs)
populate Section 2. Frame each as an IMPLICATION for capital planning,
not as a statistical observation. NOT: "The benchmark ranks 6th on
Sharpe ratio." YES: "The all-equity benchmark underperforms 5 of 10
tested strategies on risk-adjusted return — a 0.10 Sharpe unit gap
that exceeds the typical active management fee threshold."

CITATIONS:
{citations_cache}

The executive brief does NOT carry inline academic citations — findings
speak for themselves to a leadership audience. The References cache
above is referenced only for the optional methodology note in the
footer.

TEAM ACTIVITY:
{team_activity}

Not used in the brief body. The "Full methodology and data validation
documentation available on request" footer line is sufficient.

VALIDATION SUMMARY:
{validation_summary}

Not used in the brief body — leadership audiences trust validation
without needing to see the audit detail.

WORD BUDGETS — HARD LIMITS:
  Section 1 — The Situation              60 words MAX
  Section 2 — Key Findings              180 words MAX (6 bullets ×30)
  Section 3 — Risk Implications          80 words MAX
  Section 4 — Current Environment        80 words MAX
  Section 5 — Recommended Next Steps     90 words MAX
  ───────────────────────────────────────────────
  Total body                            490 words MAX

When over budget: cut filler, not numbers. Never cut Section 5 below
three bullets.

═════════════════════════════════════════════════════════════════
HEADER (not counted in page limit)
═════════════════════════════════════════════════════════════════

TO: Forest Capital Leadership
FROM: FNA670 Industry Practicum Team
DATE: May 27, 2026
RE: Multi-Strategy Portfolio Diversification — Preliminary Findings

═════════════════════════════════════════════════════════════════
SECTION 1 — THE SITUATION  (60 words max)
═════════════════════════════════════════════════════════════════

2-3 sentences. What changed in 2022 and why it matters for capital
planning. Lead with F2 (correlation shift). No methodology. Just the
fact and its implication.

═════════════════════════════════════════════════════════════════
SECTION 2 — KEY FINDINGS  (180 words max — 5-6 bullets)
═════════════════════════════════════════════════════════════════

Each bullet: one finding, one number, one implication. Maximum 2
lines per bullet. No academic citations inline. Use the HIGH-strength
findings — frame each as an implication for capital planning.

═════════════════════════════════════════════════════════════════
SECTION 3 — RISK IMPLICATIONS  (80 words max — one paragraph)
═════════════════════════════════════════════════════════════════

F3 tail risk finding in plain English. The CVaR ratio framed as a
capital preservation argument, not a statistical observation.

═════════════════════════════════════════════════════════════════
SECTION 4 — CURRENT ENVIRONMENT  (80 words max — one paragraph)
═════════════════════════════════════════════════════════════════

Macro context from the digest. Which strategies are favored and why
in one sentence each. Include this section ONLY IF macro_validated
is TRUE — otherwise omit entirely.

═════════════════════════════════════════════════════════════════
SECTION 5 — RECOMMENDED NEXT STEPS  (90 words max — 3 bullets)
═════════════════════════════════════════════════════════════════

Specific, actionable, time-bound recommendations to Forest Capital
leadership. Examples:

"Commission sensitivity analysis at 15bps and 20bps transaction costs
to confirm strategy rankings before final allocation."

"Evaluate dynamic regime detection as a live signal for quarterly
rebalancing decisions."

"Consider expanding the asset universe to include real assets or
international exposure to reduce structural correlation constraints."

═════════════════════════════════════════════════════════════════
FOOTER (one line)
═════════════════════════════════════════════════════════════════

"Full methodology and data validation documentation available on
request. Platform: forest-capital.vercel.app"

═════════════════════════════════════════════════════════════════
QUALITY REQUIREMENTS
═════════════════════════════════════════════════════════════════

  No sentence longer than 25 words.
  No paragraph longer than 4 lines.
  Every bullet contains exactly one number.
  No academic jargon without a plain-English equivalent.
  No hedging language — may, might, could, possibly, appears to,
    seems to — replaced with: does, shows, produces, reduces,
    increases.
  Total length: 2 pages maximum, single-spaced.

═════════════════════════════════════════════════════════════════
SELF-CHECK BEFORE FINALISING
═════════════════════════════════════════════════════════════════

  1. Can a non-quant reader understand every sentence?
  2. Every bullet has exactly one number?
  3. No sentence over 25 words?
  4. Total under 490 words?
  5. No hedging language present?
  6. Recommended next steps are specific and actionable?

═════════════════════════════════════════════════════════════════
[BOB] CALLOUT POINTS
═════════════════════════════════════════════════════════════════

End the brief with these two BOB callouts so the editor surfaces
them as resolvable blocks (Bob's input is required before
submission):

[BOB — Your recommended framing for how Forest Capital should act
on these findings — one sentence that captures the investment
thesis in your own words]

[BOB — Any specific context about the Forest Capital mandate that
should shape the recommended next steps]

Generate the full brief now. Write it as a complete, submission-
ready memo. Do NOT summarise or outline — write the actual brief."""


_EXEC_BRIEF_SECTION_INSTRUCTIONS = [
    {"number": 1, "title": "The Situation",
     "max_words": 60,
     "key_points": [
         "lead with F2 correlation shift",
         "2-3 sentences only",
         "no methodology, no academic framing",
     ]},
    {"number": 2, "title": "Key Findings",
     "max_words": 180,
     "key_points": [
         "5-6 bullets each with one number and one implication",
         "use HIGH-strength findings only",
         "frame as capital planning implication",
         "maximum 2 lines per bullet",
     ]},
    {"number": 3, "title": "Risk Implications",
     "max_words": 80,
     "key_points": [
         "F3 tail risk in plain English",
         "capital preservation framing",
     ]},
    {"number": 4, "title": "Current Environment",
     "max_words": 80,
     "key_points": [
         "macro digest paragraph",
         "skip entirely if macro_validated is false",
     ]},
    {"number": 5, "title": "Recommended Next Steps",
     "max_words": 90,
     "key_points": [
         "3 bullets — specific, actionable, time-bound",
         "framed as recommendations to leadership",
     ]},
]


# Same concept list as the midpoint paper — the citations cache is
# templated to support both surfaces. Future templates can declare
# their own concept set if their references diverge materially.
_EXEC_BRIEF_CONCEPTS: list[dict] = []


# ── Executive Brief rubric ──────────────────────────────────────────────────


_EXEC_BRIEF_CRITERIA = [
    {
        "criterion_id": "executive_clarity",
        "section": "all",
        "description": (
            "Can a non-quantitative C-suite reader understand the "
            "finding and its implication in 30 seconds?"),
        "weight": None,
        "indicators_of_success": [
            "Every sentence reads cleanly without quantitative "
            "training.",
            "No academic jargon without a plain-English equivalent.",
            "Section 1 establishes the situation in 2-3 sentences.",
            "Section 2 bullets each land in 2 lines or fewer.",
        ],
    },
    {
        "criterion_id": "actionability",
        "section": "section_5",
        "description": (
            "Does every finding lead to a specific implication for "
            "capital allocation decisions?"),
        "weight": None,
        "indicators_of_success": [
            "Section 5 lists 3 specific, time-bound next steps.",
            "Section 2 bullets state implications, not statistical "
            "observations.",
            "Recommendations are addressed to Forest Capital "
            "leadership, not the analyst.",
        ],
    },
    {
        "criterion_id": "evidence_quality",
        "section": "all",
        "description": (
            "Are all claims supported by specific verified numbers?"),
        "weight": None,
        "indicators_of_success": [
            "Every bullet in Section 2 contains exactly one verified "
            "number.",
            "Section 3 names the CVaR ratio or comparable tail-risk "
            "figure.",
            "No invented or hedged numbers.",
        ],
    },
    {
        "criterion_id": "brevity",
        "section": "all",
        "description": (
            "Is the language tight? No academic hedging, no "
            "unnecessary methodology explanation, no passive voice."),
        "weight": None,
        "indicators_of_success": [
            "Total body under 490 words.",
            "No sentence longer than 25 words.",
            "No use of 'may', 'might', 'could', 'possibly', "
            "'appears to', 'seems to'.",
            "Active voice throughout — passive only where the agent "
            "is genuinely unknown.",
        ],
    },
]


_EXEC_BRIEF_RUBRIC_TEXT = """Executive Brief Evaluation Criteria

The executive brief is evaluated against professional advisory
standards, not academic grading criteria. Four criteria:

CRITERION 1 — EXECUTIVE CLARITY
Can a non-quantitative C-suite reader understand the finding and its
implication in 30 seconds? Plain English throughout, no academic
jargon without a plain-English equivalent.

CRITERION 2 — ACTIONABILITY
Does every finding lead to a specific implication for capital
allocation decisions? Section 5 lists 3 specific, time-bound next
steps addressed to leadership.

CRITERION 3 — EVIDENCE QUALITY
Are all claims supported by specific verified numbers? Every Section
2 bullet contains exactly one verified number, no invented figures,
no hedged statistics.

CRITERION 4 — BREVITY
Is the language tight? No academic hedging, no unnecessary methodology
explanation, no passive voice. Total body under 490 words, no
sentence over 25 words.

FORMAT REQUIREMENTS
  Two pages maximum, single-spaced.
  Calibri 11pt, 1-inch margins.
  Professional memo header (TO/FROM/DATE/RE).
  Section headings: bold, left-aligned, all caps.
  No page numbers needed for a 2-page document.
"""


_EXEC_BRIEF_FORMAT_SPEC = {
    "font":            "Calibri 11pt",
    "spacing":         "single-spaced body",
    "target_pages":    2,
    "page_limit_includes_references": False,
    "page_limit_includes_appendix":   False,
    "memo_style":      True,
}


def upgrade() -> None:
    # ── Seed report_templates row ────────────────────────────────────
    templates = sa.table(
        "report_templates",
        sa.column("template_id", sa.String),
        sa.column("display_name", sa.String),
        sa.column("course", sa.String),
        sa.column("format_spec", sa.JSON),
        sa.column("system_prompt", sa.Text),
        sa.column("section_instructions", sa.JSON),
        sa.column("concepts", sa.JSON),
        sa.column("requires_staging", sa.Boolean),
        sa.column("active", sa.Boolean),
    )
    op.bulk_insert(templates, [{
        "template_id":         "executive_brief_fna670",
        "display_name":        "Executive Brief — FNA670",
        "course":              "FNA670 Industry Practicum",
        "format_spec":         json.dumps(_EXEC_BRIEF_FORMAT_SPEC),
        "system_prompt":       _EXEC_BRIEF_SYSTEM_PROMPT,
        "section_instructions": json.dumps(
            _EXEC_BRIEF_SECTION_INSTRUCTIONS),
        "concepts":            json.dumps(_EXEC_BRIEF_CONCEPTS),
        "requires_staging":    True,
        "active":              True,
    }])

    # ── Seed report_rubrics row ──────────────────────────────────────
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
        "template_id":     "executive_brief_fna670",
        "version":         1,
        "rubric_text":     _EXEC_BRIEF_RUBRIC_TEXT,
        "criteria":        json.dumps(_EXEC_BRIEF_CRITERIA),
        "uploaded_by":     "system",
        "source_filename": "executive_brief_rubric_seeded.txt",
        "active":          True,
    }])

    # ── Changelog ────────────────────────────────────────────────────
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
        "version": 53,
        "released_at": datetime(2026, 5, 22, tzinfo=timezone.utc),
        "title": "Executive Brief — second report writer template",
        "description": (
            "The report writer's template dropdown gains an Executive "
            "Brief option targeted at Forest Capital leadership. The "
            "brief shares the same eleven-step pipeline, the same "
            "editor + iteration + final-check + academic-review "
            "endpoints, and the same /reports/writer UI — only the "
            "system prompt, rubric, word budgets, and docx formatter "
            "differ. 490-word memo in Calibri 11pt single-spaced, "
            "scored against professional advisory criteria rather "
            "than the academic rubric."
        ),
        "academic_rationale": (
            "The midpoint paper is graded; the executive brief is "
            "delivered to Forest Capital. A leadership audience "
            "needs different framing, different word density, and "
            "different evaluation criteria — sharing the underlying "
            "pipeline keeps verification rigorous while shifting "
            "the surface to the actual reader."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 53")
    op.execute(
        "DELETE FROM report_rubrics "
        "WHERE template_id = 'executive_brief_fna670'")
    op.execute(
        "DELETE FROM report_templates "
        "WHERE template_id = 'executive_brief_fna670'")
