"""[BOB] block pre-population — system prompts updated to draft content.

May 23 2026 (item 1 — [BOB] pre-population, single focused commit).

Updates the system prompts for both seeded templates (midpoint_check_
fna670 and executive_brief_fna670) so the academic writer
pre-populates each [BOB] block with a DRAFT paragraph drawn from
verified_data + ranked_findings + macro_summary +
strategy_characterisations + the central thesis — instead of
leaving the block as a terse "Your input needed" placeholder.

The reviewer's job becomes Review and Personalise rather than
write-from-scratch — the same content authority moves to the
human, but the agent does the data-grounded first pass.

Why a migration: the template's system_prompt is stored in the
report_templates row (seeded by migration 031 / 034). Updating the
prompt is a data change, not a schema change. The migration uses
an UPDATE with op.execute so existing report_generations rows are
unaffected — only future generations carry the new prompt.

Frontend pieces (bob_blocks_reviewed counter, [Mark as reviewed]
button, [Rephrase in my voice] / [Expand] / [Accept draft as-is]
toolbar, badge state visual) ship in the same commit but are
frontend-only — no further schema needed. The [Mark as reviewed]
button just calls the existing /resolve-bob endpoint with the
current (edited or unedited) draft content, which replaces the
[BOB] marker in paper_md and decrements the unresolved count.

Behavioural invariants preserved:
  • Existing [BOB] blocks generated under the OLD prompt still
    render correctly — they just show their original placeholder
    text in the editable textarea. The reviewer can replace it.
  • The /resolve-bob endpoint contract is unchanged.
  • The 5 marker kinds (DATA REQUIRED, CITATION REQUIRED, etc.)
    other than BOB keep their old behaviour — they're missing-data
    flags, not author-input prompts.
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
from sqlalchemy.sql import text


revision: str = "035"
down_revision: str | None = "034"
branch_labels: str | None = None
depends_on: str | None = None


# Appended to the midpoint paper system prompt. The agent reads the
# instructions and produces 4 pre-populated [BOB: <draft>] blocks at
# the end of the paper (or interspersed at the sections the prompt
# names). The frontend's pre-populated badge renders the draft as
# editable text the reviewer can keep, rephrase, expand, or replace.

_MIDPOINT_BOB_APPENDIX = """

═════════════════════════════════════════════════════════════════
[BOB] PRE-POPULATED DRAFT BLOCKS — REQUIRED
═════════════════════════════════════════════════════════════════

In addition to the four sections above, end the paper with FOUR
[BOB] blocks containing PRE-POPULATED draft content. Each block is
your best attempt at what Bob (the lead analyst) would write if he
had the verified data and ranked findings in front of him.

FORMAT (literal):
  [BOB: <60-120 word draft paragraph>]

Each draft must:
  - Cite ONLY numbers that appear in verified_data.
  - Cite ONLY entries from citations_cache whose
    verification_status is 'verified' (or any verified-bucket
    state).
  - Use first-person plural — we / our analysis / our findings.
  - Connect to the central thesis (the 2022 correlation regime
    shift, dynamic strategy selection as a first-order problem).
  - Read as a coherent paragraph, NOT a bullet list.

REQUIRED FOUR BLOCKS, in order, at the end of the paper:

1. 2022 correlation shift implication:
   Use equity_ig_corr_pre_2022 and equity_ig_corr_post_2022 from
   verified_data. Frame as what the shift means for a capital
   planning mandate like Forest Capital's. One concrete
   implication.

2. Strategy selection in the current environment:
   Use macro_summary (ONLY if macro_validated is True — else omit
   the macro reference) and the top-3 strategies from
   ranked_findings. One sentence per favoured strategy. Anchor in
   what the data actually shows, not what is typically said.

3. Academic connections:
   Cite the regime-shift and CVaR entries from citations_cache
   when verified — use APA inline form (Author, Year). Connect
   findings to course concepts the readings imply.

4. Open question framing:
   Build on the governance question already in Section 4. Refine
   to a single sharp question a peer reviewer or panel member
   would meaningfully engage with. No hedging.

The reviewer treats every [BOB] block as a DRAFT — they can
[Mark as reviewed] to accept verbatim, [Rephrase in my voice] for
a different tone, [Expand] for more detail, or edit directly.

Do NOT skip these [BOB] blocks. Do NOT replace them with
[CITATION REQUIRED] or [DATA REQUIRED] markers — those are for
genuinely missing inputs, NOT for content you can draft from the
data you have.
"""


# Appended to the executive brief system prompt. The brief already
# has two end-of-document [BOB] callouts; this rewrites them to be
# pre-populated drafts instead of placeholder prompts.

_BRIEF_BOB_APPENDIX = """

═════════════════════════════════════════════════════════════════
[BOB] PRE-POPULATED DRAFT BLOCKS — REQUIRED
═════════════════════════════════════════════════════════════════

In addition to the five sections above, end the brief with TWO
[BOB] blocks containing PRE-POPULATED draft content. Each is
your best attempt at what Bob would write if he had the verified
data and ranked findings in front of him.

FORMAT (literal): [BOB: <30-60 word draft paragraph>]

Drafts must use ONLY verified_data figures, cite ONLY verified
citations_cache entries, and read in the professional advisory
tone the brief requires (declarative, no hedging, no academic
jargon, every sentence earns its place).

REQUIRED TWO BLOCKS, in order:

1. Recommended framing for Forest Capital:
   One sentence that captures the investment thesis as Bob would
   put it to leadership. Use verified_data figures only.

2. Forest Capital mandate context:
   One sentence on context (mandate type, capital scale, time
   horizon) that should shape the next-steps recommendations.

The reviewer treats these as DRAFTS — review, personalise, and
mark as reviewed before generating the final docx.

Do NOT skip these. Do NOT replace with placeholder markers.
"""


def upgrade() -> None:
    # Append the [BOB] instruction to both seeded templates. UPDATE
    # rather than overwrite — keeps the rest of each prompt intact
    # and is a no-op for templates added in future migrations that
    # don't have these template_id slugs.
    op.execute(
        text("UPDATE report_templates "
             "SET system_prompt = system_prompt || :a "
             "WHERE template_id = 'midpoint_check_fna670'")
        .bindparams(a=_MIDPOINT_BOB_APPENDIX))
    op.execute(
        text("UPDATE report_templates "
             "SET system_prompt = system_prompt || :a "
             "WHERE template_id = 'executive_brief_fna670'")
        .bindparams(a=_BRIEF_BOB_APPENDIX))

    # Changelog entry.
    changelog = op.get_bind()
    changelog.execute(
        text(
            "INSERT INTO changelog "
            "(version, released_at, title, description, "
            " academic_rationale, tour_step_id) "
            "VALUES (:v, :rel, :t, :d, :r, NULL)")
        .bindparams(
            v=54,
            rel=datetime(2026, 5, 23, tzinfo=timezone.utc),
            t="Report writer — [BOB] blocks pre-populated by the agent",
            d=(
                "The academic writer now drafts every [BOB] block "
                "with verified-data-grounded paragraph content. Bob's "
                "job in the editor becomes Review and Personalise "
                "rather than write-from-scratch — the agent provides "
                "the first pass, Bob refines the framing and voice. "
                "Four pre-populated blocks on the midpoint paper "
                "(2022 correlation implication, strategy selection, "
                "academic connections, open question) and two on the "
                "executive brief (recommended framing, mandate "
                "context). [Mark as reviewed], [Rephrase in my voice], "
                "[Expand], and [Accept draft as-is] toolbar actions "
                "in the editor."),
            r=(
                "A graduate-paper deadline at midnight is the wrong "
                "moment for an empty cursor. Pre-population doesn't "
                "remove Bob's authorial control — it gives him a "
                "data-grounded starting point grounded in the same "
                "verified data the rest of the paper uses. The "
                "[Mark as reviewed] state distinguishes accepted "
                "agent prose from prose Bob has personalised; the "
                "audit trail shows which is which.")))


def downgrade() -> None:
    # Strip the appended instructions on both templates. Uses
    # REPLACE on a fixed marker — safe because the appendices start
    # with their distinctive headings and contain no caller-
    # controlled data.
    op.execute(
        text("UPDATE report_templates "
             "SET system_prompt = REPLACE(system_prompt, :a, '') "
             "WHERE template_id = 'midpoint_check_fna670'")
        .bindparams(a=_MIDPOINT_BOB_APPENDIX))
    op.execute(
        text("UPDATE report_templates "
             "SET system_prompt = REPLACE(system_prompt, :a, '') "
             "WHERE template_id = 'executive_brief_fna670'")
        .bindparams(a=_BRIEF_BOB_APPENDIX))
    op.execute(text("DELETE FROM changelog WHERE version = 54"))
