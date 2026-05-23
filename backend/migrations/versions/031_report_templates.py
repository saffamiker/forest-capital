"""Report templates + verified-generation pipeline storage.

May 22 2026 (item 12 — verified-data midpoint paper template).

Four schema changes in one migration:

1. ALTER analytical_findings_cache — add ranked_findings JSONB,
   macro_validated bool, high_strength_count int. The
   /api/v1/reports/stage-findings endpoint now writes the strength-
   ranked finding order alongside the raw findings; macro_validated
   is TRUE only when the latest digest's summary_text parsed cleanly
   (no agent planning prose) and so can be cited.

2. CREATE citations_cache — one row per (generation_id, concept_id).
   The /api/v1/reports/source-citations endpoint runs a trusted-
   domain web search per concept and stores the verified result.
   verification_status ∈ {verified, untrusted_source, not_found}.

3. CREATE report_templates — named-template storage. The midpoint
   template is seeded at upgrade time; future templates plug in as
   additional rows. NO citation_slots column — the consolidated spec
   sources citations by concept_id at generation time, not from a
   pre-declared per-template slot list.

4. CREATE report_generations — one row per generated paper.
   Persists every input snapshot (verified_data, citations,
   activity, validation) and the two docx output paths so a
   generated paper is reproducible and downloadable for as long as
   the row exists.

Per the project convention, every migration seeds at least one
changelog entry; the entry below describes the user-visible behaviour
change so the What's New modal surfaces it.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json

from alembic import op
import sqlalchemy as sa


revision: str = "031"
down_revision: str | None = "030"
branch_labels: str | None = None
depends_on: str | None = None


# ── Midpoint system prompt (placeholders only — no hardcoded data) ──────────
#
# Every numeric value comes from {verified_data}; every citation comes
# from {citations_cache}; every activity figure from {team_activity};
# every validation status from {validation_summary}. The pipeline
# substitutes these at generation time. A regex post-scan flags any
# decimal in the generated draft that does not trace back to a
# verified field.

_MIDPOINT_SYSTEM_PROMPT = """You are the academic writer for the Forest \
Capital Industry Practicum midpoint paper.

Course: FNA670 Industry Practicum
Submission: Midpoint Check
Format: Three pages double-spaced 12-point font
Due: May 27 2026

AUDIENCE:
Primary: Dr. Panttser and the graduate cohort.
Secondary: Forest Capital.

TONE:
Graduate-level academic writing. Precise, evidence-based, confident.
Every claim supported by a specific verified number. No hedging on
findings the data supports. No inflation of findings the data does
not support. Professional capital markets vocabulary throughout.

═════════════════════════════════════════════════════════════════
CENTRAL THESIS
═════════════════════════════════════════════════════════════════

The following thesis has been VALIDATED against the live data. Do
not deviate from it:

"A diversified multi-strategy allocation cannot be evaluated on
return alone. The critical dimension is asymmetry — the ability to
limit drawdown during stress periods while participating in
recoveries. The post-2022 correlation regime shift demonstrates that
the structural assumptions underlying conventional allocation have
changed materially, making dynamic strategy selection a first-order
problem rather than a refinement."

═════════════════════════════════════════════════════════════════
VERIFIED DATA
═════════════════════════════════════════════════════════════════

{verified_data}

Use ONLY these figures. Do not invent any number. If a figure is
missing write [DATA REQUIRED] inline. The post-generation regex
scan flags any decimal not traceable to a verified field as
[UNVERIFIED NUMBER — agent may have invented this].

═════════════════════════════════════════════════════════════════
RANKED FINDINGS
═════════════════════════════════════════════════════════════════

{ranked_findings}

Structure Section 2 in this order. The highest-ranked finding opens
Section 2. Do NOT reorder — the ranking is computed from finding
strength (HIGH > MEDIUM > LOW) and magnitude.

═════════════════════════════════════════════════════════════════
CITATIONS
═════════════════════════════════════════════════════════════════

{citations_cache}

Cite inline in (Author, Year) format at the point of each relevant
finding. Cite ONLY verified entries. Write [CITATION REQUIRED]
inline for any concept where no verified source exists.

The consolidated References section (in the Appendix) is built
entirely from this cache by the pipeline. Every inline citation in
the paper MUST have a matching entry; the post-generation cross-ref
check flags violations.

═════════════════════════════════════════════════════════════════
TEAM ACTIVITY
═════════════════════════════════════════════════════════════════

{team_activity}

Use in Section 3 activity-evidence block ONLY. Every figure pulled
live; no estimates.

═════════════════════════════════════════════════════════════════
VALIDATION SUMMARY
═════════════════════════════════════════════════════════════════

{validation_summary}

Use in Appendix D ONLY. The main paper references "(See Appendix D
for full independent data validation summary.)" but does not surface
the detail inline.

═════════════════════════════════════════════════════════════════
WORD BUDGETS — HARD LIMITS
═════════════════════════════════════════════════════════════════

  Section 1 — Data and Methodology               250 words MAX
  Section 2 — Preliminary Results and Diagnostics 300 words MAX
  Section 3 — Roles and Division of Labor         150 words MAX
  Section 4 — Next Steps and Open Questions       125 words MAX
  ─────────────────────────────────────────────────────────────
  Total body                                       825 words MAX

References excluded from the count. Appendix excluded entirely.

Trim priority when over budget:
  1. Remove meaningless adjectives.
  2. Convert prose lists to compact inline format.
  3. Shorten examples.
  4. NEVER remove a number or a citation.
  5. NEVER cut the central thesis statement.
  6. NEVER cut the open question.

If the full paper exceeds 825 after trimming, cut from Section 4
first, then Section 3. NEVER cut Section 2.

═════════════════════════════════════════════════════════════════
SECTION 1 — DATA AND METHODOLOGY  (250 words max)
═════════════════════════════════════════════════════════════════

Establish: what the data is, why construction decisions are
defensible, what the analytical framework is.

Data sources: three-asset universe (US equities, IG bonds, HY
bonds), monthly return series, period as named in verified_data,
spanning multiple full market cycles including GFC, European debt
crisis, COVID shock, and the 2022 rate regime shift.

Ten strategies — name them all; group by type (static, momentum-
driven, volatility-targeting, mean-reversion, regime-switching,
factor-based); one sentence per type on the signal used. State that
strategies were selected to avoid redundancy, confirmed by
correlation analysis. Cite (See Appendix B, F4).

Constraints — long-only, fully invested, no cash. Consistent with
an institutional capital planning mandate.

Lookback windows — five strategies require initialisation periods
(36 months covariance-dependent, 12 months momentum signals);
metrics computed over feasible data periods; benchmark comparisons
use matching windows. Cite (See Appendix D for validation
methodology.).

Platform sentence: "Analysis was conducted using a purpose-built
portfolio intelligence platform with live data pipelines,
independent three-layer data validation, and AI-assisted academic
review." Add (See Appendix A.).

Quality rules: no vague statements without specifics; every
methodological decision has a one-sentence justification; the
lookback disclosure is matter-of-fact, not apologetic; active voice
throughout.

═════════════════════════════════════════════════════════════════
SECTION 2 — PRELIMINARY RESULTS AND DIAGNOSTICS  (300 words max)
═════════════════════════════════════════════════════════════════

Lead with ranked_findings[0] — the highest-strength finding from
the staged run. DO NOT assume which finding this is; the ranking is
computed.

Per paragraph: state the finding directly, give the specific
numbers from verified_data, interpret what it means for a capital
planning mandate, add the relevant appendix cross-reference.

After every finding that has a supporting citation in
citations_cache, cite inline at the point of the claim.

Closing paragraph — macro context. Use {macro_summary} from
verified_data ONLY IF macro_validated is TRUE. If macro_validated
is FALSE, OMIT the macro paragraph entirely — do not invent macro
context. Frame as: "The current macroeconomic environment provides
a live test case for the regime-conditional framework…"

Quality rules: every paragraph carries at least one specific
verified number; no paragraph longer than 6 lines double-spaced;
never use "interesting" or "notable" — say what the number means;
no hedging on supported findings.

═════════════════════════════════════════════════════════════════
SECTION 3 — ROLES AND DIVISION OF LABOR  (150 words max)
═════════════════════════════════════════════════════════════════

Roles paragraph (60 words max):
  Michael Ruurds: platform architecture, data pipeline, analytics
    infrastructure, QA oversight, deployment.
  Bob Thao: financial analysis, strategy interpretation, academic
    writing, report generation, data validation sign-off.
  Molly Murdock: user acceptance testing, presentation development,
    peer review preparation.

Activity evidence (50 words max) — pulled from {team_activity}:

  "Michael Ruurds: {michael_commits} commits,
  {michael_prs_merged} PRs merged, {michael_migrations_deployed}
  migrations deployed.
  Bob Thao: {bob_uat_steps} UAT steps, {bob_council_sessions}
  council sessions, {bob_academic_review_runs} academic review
  runs.
  Molly Murdock: {molly_uat_steps} UAT steps,
  {molly_failure_reports_filed} failure reports filed,
  {molly_feedback_items} feedback items submitted.

  Platform total: {team_total_uat_steps} test steps,
  {team_total_audit_validations} independent validations, and
  {team_total_council_sessions} council sessions completed to date."

  Add (See Appendix C for full auditable activity log.).

Coordination (40 words max) — one sentence on the platform as
coordination mechanism with auditable record of contributions.

═════════════════════════════════════════════════════════════════
SECTION 4 — NEXT STEPS AND OPEN QUESTIONS  (125 words max)
═════════════════════════════════════════════════════════════════

Three planned extensions (75 words):
  1. Dynamic regime classification — replace the 2022 calendar
     break with a Hidden Markov Model or threshold volatility
     indicator producing a continuous regime probability signal.
  2. Transaction cost sensitivity — stress at 15 bps and 20 bps
     two-sided; high-turnover strategies most sensitive.
  3. Sensitivity analysis — vary lookback windows and rebalancing
     frequencies; confirm rankings are robust.

Two limitations (30 words): the three-asset universe; the
structural correlation elevation from the same underlying assets.
Both honest, both framed as future extensions, neither apologetic.

Open question (20 words): "Given the post-2022 breakdown in
equity-bond correlation, to what extent should a capital planning
mandate treat regime-conditional portfolio construction as a
first-order problem rather than a sensitivity check — and what is
the appropriate governance framework for acting on regime signals
in a fiduciary context?"

═════════════════════════════════════════════════════════════════
SELF-CHECK BEFORE FINALISING
═════════════════════════════════════════════════════════════════

1. Every Section 2 paragraph contains at least one specific number
   from verified_data.
2. No number not in verified_data.
3. Central thesis appears in Section 1 (framing), Section 2
   (evidence), Section 4 (forward implication).
4. The word "interesting" does not appear.
5. Every Section 1 decision carries a justification.
6. Section 3 names all three team members.
7. The open question is genuinely open.
8. Total within 825 words.
9. Every inline (Author, Year) citation has a verified entry in
   citations_cache.

Generate the full draft now. Write it as a complete, submission-
ready document. Do NOT summarise or outline — write the actual
paper."""


# Section instructions — the per-section guidance the generator
# extracts when running section-by-section generation. Same content
# as embedded above but indexed so the pipeline can prompt one
# section at a time.

_MIDPOINT_SECTION_INSTRUCTIONS = [
    {"number": 1, "title": "Data and Methodology",
     "max_words": 250,
     "key_points": [
         "data sources + period", "ten strategies grouped by type",
         "constraints", "lookback windows",
         "platform sentence with cross-ref to Appendix A",
     ]},
    {"number": 2, "title": "Preliminary Results and Diagnostics",
     "max_words": 300,
     "key_points": [
         "lead with ranked_findings[0]",
         "follow ranked_findings order",
         "every paragraph carries a verified number",
         "macro paragraph only if macro_validated is True",
     ]},
    {"number": 3, "title": "Roles and Division of Labor",
     "max_words": 150,
     "key_points": [
         "roles paragraph (60w)",
         "activity evidence block from team_activity (50w)",
         "coordination sentence (40w)",
         "cross-ref to Appendix C",
     ]},
    {"number": 4, "title": "Next Steps and Open Questions",
     "max_words": 125,
     "key_points": [
         "three planned extensions",
         "two limitations",
         "one genuinely open question",
     ]},
]


# Concept IDs the citation finder will search for at generation time.
# Stored on the template so the source-citations endpoint reads the
# list and runs one web search per concept; the user's amendment
# explicitly removed the per-slot hardcoded targets so the finder
# returns the best available paper per concept.

_MIDPOINT_CONCEPTS = [
    {"concept_id": "cvar_coherent_risk",
     "search_query": "conditional value at risk coherent risk "
                      "measure seminal paper portfolio"},
    {"concept_id": "equity_bond_corr_2022",
     "search_query": "equity bond correlation regime shift inflation "
                      "2022 2023 working paper"},
    {"concept_id": "sharpe_ratio",
     "search_query": "Sharpe ratio mutual fund performance "
                      "measurement original paper"},
    {"concept_id": "portfolio_diversification",
     "search_query": "portfolio selection mean variance "
                      "diversification seminal paper"},
    {"concept_id": "momentum_strategy",
     "search_query": "momentum returns buying winners selling losers "
                      "seminal paper"},
    {"concept_id": "four_factor_model",
     "search_query": "four factor model mutual fund persistence "
                      "Carhart"},
    {"concept_id": "regime_switching",
     "search_query": "Markov regime switching nonstationary time "
                      "series seminal paper"},
    {"concept_id": "transaction_costs_factors",
     "search_query": "transaction costs momentum factor returns "
                      "decay academic paper"},
    {"concept_id": "sixty_forty_limitations",
     "search_query": "60 40 portfolio limitations inflation regime "
                      "2022 2023 academic research"},
    {"concept_id": "gips_verification",
     "search_query": "CFA Institute GIPS global investment "
                      "performance standards independent verification"},
]


def upgrade() -> None:
    # ── 1. ALTER analytical_findings_cache ────────────────────────────
    op.add_column(
        "analytical_findings_cache",
        sa.Column("ranked_findings", sa.JSON(), nullable=False,
                  server_default=sa.text("'[]'::jsonb"),
                  comment="Findings ordered HIGH > MEDIUM > LOW "
                          "by nugget strength, then by magnitude "
                          "within each tier. Used by the academic "
                          "writer to structure Section 2 (highest-"
                          "ranked finding opens the section)."))
    op.add_column(
        "analytical_findings_cache",
        sa.Column("macro_validated", sa.Boolean(), nullable=False,
                  server_default=sa.false(),
                  comment="TRUE only when the latest digest's "
                          "summary_text parsed cleanly (no agent "
                          "planning prose). Determines whether the "
                          "Section 2 macro paragraph is included."))
    op.add_column(
        "analytical_findings_cache",
        sa.Column("high_strength_count", sa.Integer(), nullable=False,
                  server_default="0",
                  comment="Denormalised count of HIGH-strength "
                          "findings — surfaced on the Report Writer "
                          "UI Stage Findings step status."))

    # ── 2. CREATE citations_cache ─────────────────────────────────────
    op.create_table(
        "citations_cache",
        sa.Column("id", sa.BigInteger(), primary_key=True,
                  autoincrement=True),
        sa.Column("generation_id", sa.BigInteger(), nullable=True,
                  comment="Optional FK to report_generations. NULL "
                          "for standalone citation searches not "
                          "tied to a specific draft."),
        sa.Column("concept_id", sa.String(80), nullable=False,
                  comment="Concept the search targeted (e.g. "
                          "'cvar_coherent_risk', 'sharpe_ratio')."),
        sa.Column("author", sa.String(500), nullable=True),
        sa.Column("year", sa.String(10), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("journal_or_institution", sa.String(500), nullable=True),
        sa.Column("volume_issue_pages", sa.String(200), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("verification_status", sa.String(40), nullable=False,
                  comment="verified | untrusted_source | not_found."),
        sa.Column("search_query_used", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_citations_cache_generation",
        "citations_cache", ["generation_id"])
    op.create_index(
        "ix_citations_cache_concept",
        "citations_cache", ["concept_id", sa.text("created_at DESC")])

    # ── 3. CREATE report_templates ────────────────────────────────────
    op.create_table(
        "report_templates",
        sa.Column("id", sa.BigInteger(), primary_key=True,
                  autoincrement=True),
        sa.Column("template_id", sa.String(80), nullable=False, unique=True,
                  comment="Slug used in URLs — never changes."),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("course", sa.String(200), nullable=True),
        sa.Column("format_spec", sa.JSON(), nullable=False,
                  server_default=sa.text("'{}'::jsonb"),
                  comment="Format brief — font, spacing, target "
                          "pages, page-limit policy."),
        sa.Column("system_prompt", sa.Text(), nullable=False,
                  comment="Agent system prompt verbatim. Carries "
                          "{verified_data}, {ranked_findings}, "
                          "{citations_cache}, {team_activity}, "
                          "{validation_summary} placeholders the "
                          "pipeline substitutes at generation time."),
        sa.Column("section_instructions", sa.JSON(), nullable=False,
                  server_default=sa.text("'[]'::jsonb"),
                  comment="Per-section guidance list — number, "
                          "title, max_words, key_points."),
        sa.Column("concepts", sa.JSON(), nullable=False,
                  server_default=sa.text("'[]'::jsonb"),
                  comment="Concept IDs + search queries the citation "
                          "finder targets at generation time. No "
                          "hardcoded citation details."),
        sa.Column("requires_staging", sa.Boolean(), nullable=False,
                  server_default=sa.true(),
                  comment="When TRUE, /generate refuses until "
                          "analytical_findings_cache is populated."),
        sa.Column("active", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_report_templates_active",
        "report_templates", ["active", "template_id"])

    # ── 4. CREATE report_generations ──────────────────────────────────
    op.create_table(
        "report_generations",
        sa.Column("id", sa.BigInteger(), primary_key=True,
                  autoincrement=True),
        sa.Column("template_id", sa.String(80), nullable=False,
                  comment="The template_id slug at generation time. "
                          "Not a DB FK so a template archive does "
                          "not orphan past generations."),
        sa.Column("findings_cache_id", sa.BigInteger(), nullable=True,
                  comment="analytical_findings_cache.id at stage time."),
        sa.Column("citations_cache_ids", sa.JSON(), nullable=False,
                  server_default=sa.text("'[]'::jsonb"),
                  comment="List of citations_cache.id rows that "
                          "fed this generation."),
        sa.Column("team_activity_snapshot", sa.JSON(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("validation_snapshot", sa.JSON(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("verified_data", sa.JSON(), nullable=False,
                  server_default=sa.text("'{}'::jsonb"),
                  comment="Every figure the agent received, after "
                          "cross-check. Sentinel-bearing fields "
                          "(DATA MISMATCH / DATA REQUIRED) appear "
                          "as strings."),
        sa.Column("thesis_validation_passed", sa.Boolean(),
                  nullable=False, server_default=sa.false()),
        sa.Column("word_counts", sa.JSON(), nullable=False,
                  server_default=sa.text("'{}'::jsonb"),
                  comment="Per-section + total word counts and "
                          "their traffic-light status."),
        sa.Column("flag_count", sa.Integer(), nullable=False,
                  server_default="0",
                  comment="Number of [DATA REQUIRED] / [DATA "
                          "MISMATCH] / [CITATION REQUIRED] / "
                          "[UNVERIFIED NUMBER] markers in the "
                          "generated paper."),
        sa.Column("paper_docx_path", sa.Text(), nullable=True),
        sa.Column("appendix_docx_path", sa.Text(), nullable=True),
        sa.Column("paper_md", sa.Text(), nullable=True,
                  comment="Generated paper markdown — kept in the "
                          "row so the download endpoint can re-emit "
                          "the docx without re-running the agent."),
        sa.Column("appendix_md", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_report_generations_template_time",
        "report_generations",
        ["template_id", sa.text("generated_at DESC")])

    # ── Seed: midpoint_check_fna670 ───────────────────────────────────
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
        "template_id":         "midpoint_check_fna670",
        "display_name":        "Midpoint Check Paper — FNA670",
        "course":              "FNA670 Industry Practicum",
        "format_spec": json.dumps({
            "font":            "12-point Times New Roman or equivalent",
            "spacing":         "double-spaced body, single-spaced tables",
            "target_pages":    3,
            "page_limit_includes_references": False,
            "page_limit_includes_appendix":   False,
        }),
        "system_prompt":       _MIDPOINT_SYSTEM_PROMPT,
        "section_instructions": json.dumps(_MIDPOINT_SECTION_INSTRUCTIONS),
        "concepts":            json.dumps(_MIDPOINT_CONCEPTS),
        "requires_staging":    True,
        "active":              True,
    }])

    # ── Changelog ─────────────────────────────────────────────────────
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
        "version": 50,
        "released_at": datetime(2026, 5, 22, tzinfo=timezone.utc),
        "title": "Report writer — verified-data midpoint paper template",
        "description": (
            "The report writer gains a Midpoint Check Paper template "
            "configured to: (1) pull every numeric figure live from "
            "the analytics endpoints, (2) cross-check against the "
            "staged findings cache, (3) source 10 academic citations "
            "via trusted-domain web search at generation time, (4) "
            "validate the central thesis against 3 data conditions, "
            "(5) rank findings by strength so Section 2 leads with "
            "the most material result, and (6) post-scan the "
            "generated draft for any unverified number or citation. "
            "The Generate Draft button is gated on Stage Findings + "
            "Source Citations + Team Activity + Validation + Cross-"
            "Reference + Thesis Validation."
        ),
        "academic_rationale": (
            "The midpoint paper is the first graded submission of "
            "the practicum. A template that mathematically prevents "
            "invented numbers, validates the thesis against the "
            "data before generation, and adapts the section "
            "structure to the strongest finding is the right shape "
            "for a defensible deliverable that satisfies both the "
            "rigour and the integrity dimensions of the rubric."
        ),
        "tour_step_id": None,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM changelog WHERE version = 50")
    op.drop_index("ix_report_generations_template_time",
                  table_name="report_generations")
    op.drop_table("report_generations")
    op.drop_index("ix_report_templates_active",
                  table_name="report_templates")
    op.drop_table("report_templates")
    op.drop_index("ix_citations_cache_concept",
                  table_name="citations_cache")
    op.drop_index("ix_citations_cache_generation",
                  table_name="citations_cache")
    op.drop_table("citations_cache")
    op.drop_column("analytical_findings_cache", "high_strength_count")
    op.drop_column("analytical_findings_cache", "macro_validated")
    op.drop_column("analytical_findings_cache", "ranked_findings")
