"""agents/peer_review.py — Peer Review Assistant + Thesis Defense Prep.

Item 7 (May 23 2026). Two related features built on the
academic_review infrastructure:

  FEATURE A — Peer Review Assistant
    Bob, Michael, and Molly each must review another team's
    midpoint submission for the June 3 cohort meetup (3-4 minute
    critical review per student + 2-minute Q&A). Upload the other
    team's PDF / DOCX, the agent evaluates it against the four
    FNA 670 midpoint rubric dimensions, and produces a structured
    review script the reviewer can read aloud — observations per
    dimension, course-concept anchors, and 2-3 suggested Q&A
    questions. Tone: critical but professional.

  FEATURE B — Thesis Defense Prep
    Generates a mock panel Q&A sheet against the team's OWN
    most-recently-submitted midpoint draft. Anticipated questions
    across three categories — technical/methodological,
    academic/theoretical, governance/practical — each with a
    suggested response the team can rehearse against before the
    cohort meetup OR the July 1 panel.

Both flows reuse the existing peer fan-out + arbiter pattern from
academic_review — same SSE wire format, same generator-evaluator
harness, same Sonnet / Opus model assignments. The differences are
PURELY in the prompts:
  - Different rubric framing (reviewing another team vs anticipating
    questions about your own submission).
  - Different output structure (review script vs Q&A prep sheet).
  - Different context block (uploaded peer paper vs current draft).

The endpoint surface is in main.py — these helpers just produce
the prompt + context shape the streaming layer expects.
"""
from __future__ import annotations

import io
from typing import Any

from agents.academic_review import (
    chunk_arbiter_text,
    peer_agent_ids,
)


# ── Multi-format text extraction (peer papers) ──────────────────────────────


# Maximum bytes accepted for an uploaded peer paper. Tighter than
# the academic_documents 10MB ceiling because peer papers are
# 3-page midpoint submissions — anything beyond a few hundred KB
# is almost certainly an image-heavy PDF or a malformed upload.
MAX_PEER_PAPER_BYTES: int = 2 * 1024 * 1024  # 2 MB


def extract_peer_paper_text(filename: str, raw: bytes) -> str:
    """Extracts plain text from an uploaded peer-team midpoint
    submission. Routes by extension — PDF / .md / .docx are
    supported; everything else raises ValueError.

    Unlike tools.academic_context.extract_document_text (which is
    PDF-only by design), this extractor handles all three formats
    inline so the Peer Review endpoint can accept whatever Bob's
    cohort produces. The peer paper is NOT persisted to
    academic_documents — it lives in-memory for the duration of
    one review session and is discarded.

    Raises ValueError when the format is unsupported or no text
    can be extracted (e.g. a scanned-image PDF). The endpoint
    catches and returns 422.
    """
    if not raw:
        raise ValueError("Uploaded file is empty.")
    if len(raw) > MAX_PEER_PAPER_BYTES:
        raise ValueError(
            f"Uploaded file exceeds {MAX_PEER_PAPER_BYTES} bytes.")
    name = (filename or "").strip().lower()
    if name.endswith(".pdf"):
        return _extract_pdf_text(raw)
    if name.endswith(".md") or name.endswith(".markdown"):
        return _extract_md_text(raw)
    if name.endswith(".docx"):
        return _extract_docx_text(raw)
    raise ValueError(
        f"Unsupported file type for '{filename}'. Accepted formats: "
        f".pdf, .docx, .md.")


def _extract_pdf_text(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise ValueError(
            "PDF support unavailable — pypdf not installed.") from exc
    reader = PdfReader(io.BytesIO(raw))
    text = "\n".join(
        (page.extract_text() or "") for page in reader.pages).strip()
    if not text:
        raise ValueError(
            "No text could be extracted from the PDF — it may be a "
            "scanned image. Upload a text-based PDF.")
    return text


def _extract_md_text(raw: bytes) -> str:
    # Markdown is plain UTF-8; render as-is. Strip leading/trailing
    # whitespace so the peer-agent prompts don't blow up on empty
    # newlines.
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ValueError(
            "Markdown file is not valid UTF-8.") from exc


def _extract_docx_text(raw: bytes) -> str:
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover
        raise ValueError(
            "DOCX support unavailable — python-docx not installed."
        ) from exc
    doc = Document(io.BytesIO(raw))
    parts: list[str] = []
    # Walk paragraphs IN ORDER. Tables are walked separately —
    # python-docx's iteration is by body element kind, not source
    # order, so a paper with interleaved tables and prose can
    # render slightly out of order. Acceptable for a peer-review
    # input where the content matters more than the layout.
    for para in doc.paragraphs:
        t = (para.text or "").strip()
        if t:
            parts.append(t)
    for table in doc.tables:
        for row in table.rows:
            cells = [(c.text or "").strip() for c in row.cells]
            row_text = " | ".join(c for c in cells if c)
            if row_text:
                parts.append(row_text)
    text = "\n".join(parts).strip()
    if not text:
        raise ValueError(
            "No text could be extracted from the .docx — the "
            "document may be empty or image-only.")
    return text


# ── FEATURE A — Peer Review Assistant ───────────────────────────────────────


# The four FNA 670 midpoint rubric dimensions. The peer agents
# evaluate the uploaded paper against EACH dimension and emit
# observations, course-concept anchors, and suggested Q&A.
PEER_RUBRIC_DIMENSIONS: list[str] = [
    "Clarity and rigor of written submission",
    "Evidence of meaningful analytical progress",
    "Quality of preliminary results and interpretation",
    "Clear division of labor",
]


_PEER_REVIEW_PEER_QUESTION = """=== YOUR TASK — PEER REVIEW NOTES ===
You are reviewing ANOTHER team's midpoint submission (3 pages,
double-spaced) for the FNA 670 cohort meetup peer review. Each
student in our team will deliver a 3-4 minute critical review
followed by 2-minute Q&A. These peer notes will feed into the
arbiter's review script generator below.

The submitted document is included in the context block above
(under "Peer team submission"). Evaluate it against the FOUR
midpoint rubric dimensions verbatim:

  1. Clarity and rigor of written submission
  2. Evidence of meaningful analytical progress
  3. Quality of preliminary results and interpretation
  4. Clear division of labor

For each dimension, produce two short paragraphs from your
expert lens:

  OBSERVATION — what is strong, what is missing, what is unclear.
  Cite specific passages from the submission when possible (one
  short quote, not a paragraph). Reference course concepts from
  Markowitz, Carhart, Fama-French, regime-switching, factor
  investing, or the Forest Capital practicum brief where the
  submission's claims connect to (or contradict) them.

  Q&A SUGGESTIONS — 1-2 questions the reviewer could ask the
  presenters to probe their analytical reasoning. Specific to the
  submission, not generic. Format each as a single sentence ending
  in '?'.

TONE — critical but professional. The cohort is graded on the
quality of peer feedback so reviews are expected to find genuine
weaknesses. Avoid: hedging, padding, generic encouragement, or
restating what the paper said without commentary.

OUT OF SCOPE — do NOT compare the submission to Forest Capital's
own work. The reviewer evaluates the submission on its own merits
against the rubric, not against an alternative project. Avoid
references to "our project" or "Forest Capital found that …"."""


_PEER_REVIEW_ARBITER_INSTRUCTIONS = """=== YOUR TASK — REVIEW SCRIPT ===
You are the arbiter. The peer notes above evaluated the uploaded
peer submission against the four FNA 670 rubric dimensions from
multiple expert lenses. Synthesise them into a STRUCTURED REVIEW
SCRIPT a reviewer can deliver aloud in 3-4 minutes.

Output the script in this EXACT markdown format so the UI can
parse it:

**Overall verdict:** <Strong | Developing | Needs Work>
**Estimated delivery time:** <X-Y minutes at 130 wpm>

The verdict line is a single judgement aggregated across the four
dimensions. Use Needs Work if any dimension is materially missing;
Developing if every dimension is at least covered but some are
shallow; Strong if every dimension is well executed.

### 1. Clarity and rigor of written submission
**Dimension rating:** <Strong | Developing | Needs Work>
<2-3 paragraphs of OBSERVATIONS the reviewer reads aloud. Cite
specific passages from the submission when relevant. Reference
course concepts where applicable.>

**Suggested questions for Q&A:**
- <question 1>
- <question 2>

### 2. Evidence of meaningful analytical progress
**Dimension rating:** <Strong | Developing | Needs Work>
<2-3 paragraphs of observations.>

**Suggested questions for Q&A:**
- <question 1>
- <question 2>

### 3. Quality of preliminary results and interpretation
**Dimension rating:** <Strong | Developing | Needs Work>
<2-3 paragraphs of observations.>

**Suggested questions for Q&A:**
- <question 1>
- <question 2>

### 4. Clear division of labor
**Dimension rating:** <Strong | Developing | Needs Work>
<2-3 paragraphs of observations.>

**Suggested questions for Q&A:**
- <question 1>
- <question 2>

### Closing summary
<One paragraph wrap-up the reviewer reads at the end — surfaces
the single most important strength + the single most important
weakness so the presenters walk away with a clear takeaway.>

DELIVERY TIME — target 3-4 minutes at 130 words per minute.
Total prose across the four sections + closing should land around
400-520 words. Note the estimated delivery time at the top so the
reviewer can adjust pacing.

TONE GUARD — critical but professional throughout. The peer
review is part of the graded deliverable; generic encouragement
("nice work!") reads as low-effort feedback. Specific
observations grounded in the submission's text are what graders
look for."""


def build_peer_review_peer_prompt(context_block: str) -> str:
    """The user-message body sent to every peer agent for the
    Peer Review Assistant. The context_block is prepended by the
    caller (includes the peer team's submitted text)."""
    return context_block + "\n\n" + _PEER_REVIEW_PEER_QUESTION


def build_peer_review_arbiter_prompt(
    context_block: str, peer_responses: dict[str, str],
) -> str:
    """The arbiter prompt. Stitches the context, the peer notes,
    and the review-script generation instructions."""
    notes = []
    for agent_id, text in peer_responses.items():
        notes.append(f"## Peer agent — {agent_id}\n{text.strip()}")
    peer_block = "\n\n".join(notes) if notes else "(no peer notes)"
    return (
        context_block
        + "\n\n=== PEER REVIEW NOTES ===\n\n"
        + peer_block
        + "\n\n"
        + _PEER_REVIEW_ARBITER_INSTRUCTIONS
    )


# ── FEATURE B — Thesis Defense Prep ─────────────────────────────────────────


# The three Q&A categories the panel-prep sheet is organised
# around. Named verbatim per the May 23 2026 spec.
DEFENSE_CATEGORIES: list[str] = [
    "Technical / methodological",
    "Academic / theoretical",
    "Governance / practical",
]


_DEFENSE_PREP_PEER_QUESTION = """=== YOUR TASK — DEFENSE PREP NOTES ===
The submitted midpoint draft is in the context block above (under
"Team submission"). The team is preparing to present it at the
cohort meetup AND defend it at the July 1 panel. Your job is to
anticipate the questions an MSFA panel — Dr. Panttser, the Forest
Capital partners, and the broader graduate cohort — would ask the
team.

From your expert lens, propose 3-5 anticipated questions across
THE THREE CATEGORIES BELOW. For each question, also propose a
2-3 sentence response the team could rehearse against.

  TECHNICAL / METHODOLOGICAL
    Questions about the data, the backtest, the statistical
    framework, the cross-validation, the regime detection, the
    factor model. Examples of the SHAPE: "Why p < 0.005 and not
    p < 0.05?", "How did you handle the 2007 BND inception gap?",
    "What is the OOS window?", "How does CPCV differ from k-fold
    and why does it matter here?".

  ACADEMIC / THEORETICAL
    Questions linking the work to the published literature —
    Markowitz mean-variance, the Fama-French / Carhart factor
    model, regime-switching literature, the diversification
    decay hypothesis, the post-2022 correlation regime, the
    Deflated Sharpe Ratio framework. Examples of the SHAPE:
    "How does this extend Markowitz?", "Where does the 2022
    break sit in the regime-switching literature?", "Why use
    the Carhart 4-factor over Fama-French 3?".

  GOVERNANCE / PRACTICAL
    Questions about fiduciary responsibility, capital planning,
    implementation, transaction costs, real-world constraints,
    and what a Forest Capital partner would actually do with
    the recommendation. Examples of the SHAPE: "What happens
    when this strategy is deployed at $X AUM?", "How does this
    survive a flight-to-quality event?", "What does the
    capital plan look like under the recommended allocation?",
    "Have you stress-tested the recommendation against the
    Forest Capital mandate?".

For each anticipated question:
  QUESTION — the panel's question, phrased as the panel would
  ask it. Specific to THIS submission's findings, not generic.
  SUGGESTED RESPONSE — 2-3 sentences the team can use as the
  spine of their answer. Ground the response in the
  submission's actual numbers and findings (use the context
  block above). Flag honestly when the response should
  acknowledge a limitation rather than defend.

These peer notes will feed into the arbiter's Q&A prep sheet
generator below. Be SPECIFIC — generic questions ("how confident
are you?") are not useful. The shape "Given that you found X, why
not Y?" is more useful than "Why X?"."""


_DEFENSE_PREP_ARBITER_INSTRUCTIONS = """=== YOUR TASK — Q&A PREP SHEET ===
You are the arbiter. The peer notes above proposed anticipated
panel questions across three categories from multiple expert
lenses. Synthesise into a STRUCTURED Q&A PREP SHEET the team can
rehearse against.

Output in this EXACT markdown format so the UI can parse it:

**Mock panel — overall readiness:** <Strong | Developing | Needs Work>

The readiness line aggregates how well-prepared the submission
is to defend itself across the three categories. Strong when
every category is well covered; Developing when one category has
weak spots; Needs Work when the submission has material gaps a
panel would expose.

### 1. Technical / methodological
**Category readiness:** <Strong | Developing | Needs Work>

For each anticipated question in this category, render:

**Q:** <the panel's question, specific to this submission>
**Suggested response:** <2-3 sentences grounded in the
submission's findings. Flag limitations honestly where
warranted; do not invent strengths.>

Include 4-6 questions in this section.

### 2. Academic / theoretical
**Category readiness:** <Strong | Developing | Needs Work>

[Same Q/A format. Include 3-5 questions.]

### 3. Governance / practical
**Category readiness:** <Strong | Developing | Needs Work>

[Same Q/A format. Include 3-5 questions.]

### Rehearsal recommendations
<One paragraph naming the THREE highest-risk questions across
the three categories and which team member should rehearse each
one based on the submission's division of labour.>

TONE — mock-panel adversarial. The panel's questions are not
softballs; the prep sheet should sound like a sceptical reviewer
probing the submission's weakest points. Suggested responses
should be honest about limitations rather than defensive — the
graders reward honest acknowledgment of constraints over
performative confidence.

GROUND EVERY QUESTION IN THE SUBMISSION — the team's submission
is in the context block above. Specific questions about the
submission's actual numbers (the 2022 correlation shift, the
FDR result, the regime-switching strategy's Sharpe, the BND
splice) are more useful than generic methodology questions."""


def build_defense_prep_peer_prompt(context_block: str) -> str:
    """User-message body for every peer agent in the Defense
    Prep flow. context_block prepended by the caller (includes
    the team's own submitted draft text)."""
    return context_block + "\n\n" + _DEFENSE_PREP_PEER_QUESTION


def build_defense_prep_arbiter_prompt(
    context_block: str, peer_responses: dict[str, str],
) -> str:
    notes = []
    for agent_id, text in peer_responses.items():
        notes.append(f"## Peer agent — {agent_id}\n{text.strip()}")
    peer_block = "\n\n".join(notes) if notes else "(no peer notes)"
    return (
        context_block
        + "\n\n=== ANTICIPATED QUESTION NOTES ===\n\n"
        + peer_block
        + "\n\n"
        + _DEFENSE_PREP_ARBITER_INSTRUCTIONS
    )


# ── Orchestration — single-arbiter generator-evaluator runs ────────────────
#
# Both features are focused-output flows (a review script for one,
# a Q&A prep sheet for the other). A single thoughtful Opus arbiter
# call wrapped in the GeneratorEvaluatorHarness gives the quality
# the user asked for without paying for the 7-agent fan-out
# academic_review uses for its multi-rubric breadth. If a later
# iteration wants the orthogonal-perspective fan-out, the peer
# prompts above are ready — academic_review.run_peer_fan_out can
# be wired in by passing each peer a context_block already loaded
# with the task framing.


# Model the arbiter uses. Imported lazily so test environments
# without an Anthropic key never hit the model constant.
def _opus_model() -> str:
    from agents.base import OPUS_MODEL
    return OPUS_MODEL


def _sonnet_model() -> str:
    from agents.base import SONNET_MODEL
    return SONNET_MODEL


def _is_test_env() -> bool:
    import os
    return os.getenv("ENVIRONMENT", "").lower() == "test"


def _evaluator_prompt() -> str:
    """Evaluator system prompt for the harness's quality gate.
    Scores the generated output against five focused criteria so
    a draft that misses a rubric section or trails off mid-answer
    gets regenerated rather than streamed."""
    return (
        "You score peer-review and defense-prep outputs on five "
        "dimensions, each 1-10. Return ONLY a JSON object with "
        "this exact shape:\n"
        '{"scores":{"rubric_coverage":N,"specificity":N,'
        '"actionability":N,"tone":N,"completeness":N},'
        '"overall":N.NN,"passed":bool,"feedback":"..."}\n\n'
        "Criteria:\n"
        "  rubric_coverage — every required section / dimension "
        "is present and rated.\n"
        "  specificity — observations and questions are grounded "
        "in the submission's actual text, not generic.\n"
        "  actionability — suggestions / questions are concrete "
        "enough to act on.\n"
        "  tone — critical-but-professional; flag hedging, "
        "padding, or performative confidence.\n"
        "  completeness — output is complete; no mid-section "
        "truncation.\n\n"
        "passed = overall >= 7.0. Return ONLY the JSON."
    )


def _peer_review_system_prompt() -> str:
    return (
        "You are a senior FNA 670 reviewer evaluating ANOTHER "
        "team's midpoint submission for the cohort meetup peer "
        "review. Produce a critical-but-professional review "
        "script the reviewer can deliver in 3-4 minutes. Ground "
        "every observation in the submitted text — generic "
        "encouragement is not useful."
    )


def _defense_prep_system_prompt() -> str:
    return (
        "You are a mock panel of senior FNA 670 reviewers + "
        "Forest Capital partners stress-testing the team's "
        "midpoint draft before the cohort meetup and the July 1 "
        "panel. Generate the sharpest realistic anticipated "
        "questions across three categories — technical, "
        "academic, governance — with rehearsable responses. "
        "Honest about limitations beats performative defence."
    )


def run_peer_review_with_harness(context_block: str) -> str:
    """Generates the Peer Review Assistant verdict, harness-gated.
    Returns the accepted markdown. Test env returns a deterministic
    mock so SSE wire-format tests pass without an API key.
    """
    if _is_test_env():
        return mock_peer_review_verdict("(test environment)")
    from agents.base import call_claude
    from agents.harness import GeneratorEvaluatorHarness

    sys_prompt = _peer_review_system_prompt()
    generator_prompt = (
        _PEER_REVIEW_ARBITER_INSTRUCTIONS + "\n\n" + context_block)

    def _generate(prompt: str) -> str:
        # The harness threads feedback into `prompt` directly on
        # retries — we just forward it to the model.
        return call_claude(
            model=_opus_model(),
            system_prompt=sys_prompt,
            user_message=prompt,
            max_tokens=2200,
        )

    harness = GeneratorEvaluatorHarness(
        evaluator_model=_sonnet_model(),
    )
    result = harness.run(
        generator_fn=_generate,
        evaluator_prompt=_evaluator_prompt(),
        generator_prompt=generator_prompt,
        context=context_block,
        agent_id="peer_review_assistant",
    )
    return result.response


def run_defense_prep_with_harness(context_block: str) -> str:
    """Generates the Thesis Defense Prep Q&A sheet, harness-gated.
    Test env returns a deterministic mock."""
    if _is_test_env():
        return mock_defense_prep_verdict("(test environment)")
    from agents.base import call_claude
    from agents.harness import GeneratorEvaluatorHarness

    sys_prompt = _defense_prep_system_prompt()
    generator_prompt = (
        _DEFENSE_PREP_ARBITER_INSTRUCTIONS + "\n\n" + context_block)

    def _generate(prompt: str) -> str:
        return call_claude(
            model=_opus_model(),
            system_prompt=sys_prompt,
            user_message=prompt,
            max_tokens=2400,
        )

    harness = GeneratorEvaluatorHarness(
        evaluator_model=_sonnet_model(),
    )
    result = harness.run(
        generator_fn=_generate,
        evaluator_prompt=_evaluator_prompt(),
        generator_prompt=generator_prompt,
        context=context_block,
        agent_id="thesis_defense_prep",
    )
    return result.response


# ── Shared utilities ───────────────────────────────────────────────────────


# Re-export so callers don't have to reach into academic_review
# for the chunker. Keeps the peer-review module the single import
# the endpoint needs.
__all__ = [
    "PEER_RUBRIC_DIMENSIONS",
    "DEFENSE_CATEGORIES",
    "MAX_PEER_PAPER_BYTES",
    "extract_peer_paper_text",
    "build_peer_review_peer_prompt",
    "build_peer_review_arbiter_prompt",
    "build_peer_review_context_block",
    "render_peer_review_context_block",
    "build_defense_prep_peer_prompt",
    "build_defense_prep_arbiter_prompt",
    "build_defense_prep_context_block",
    "render_defense_prep_context_block",
    "run_peer_review_with_harness",
    "run_defense_prep_with_harness",
    "mock_peer_review_verdict",
    "mock_defense_prep_verdict",
    "chunk_arbiter_text",
    "peer_agent_ids",
]


def mock_peer_review_verdict(submission_name: str) -> str:
    """Test-environment / cold-deploy stand-in for the Peer Review
    Assistant arbiter output. Returns a well-formed (parseable)
    markdown verdict with placeholder content so endpoint tests
    can assert wire-format conformance without an Anthropic key."""
    name = submission_name or "the submission"
    return (
        f"**Overall verdict:** Developing\n"
        f"**Estimated delivery time:** 3-4 minutes at 130 wpm\n\n"
        f"### 1. Clarity and rigor of written submission\n"
        f"**Dimension rating:** Developing\n"
        f"This is a deterministic mock review of {name} for the "
        f"test environment.\n\n"
        f"**Suggested questions for Q&A:**\n"
        f"- Mock question about clarity?\n"
        f"- Mock question about rigour?\n\n"
        f"### 2. Evidence of meaningful analytical progress\n"
        f"**Dimension rating:** Developing\nMock observations.\n\n"
        f"**Suggested questions for Q&A:**\n"
        f"- Mock progress question?\n\n"
        f"### 3. Quality of preliminary results and interpretation\n"
        f"**Dimension rating:** Developing\nMock observations.\n\n"
        f"**Suggested questions for Q&A:**\n"
        f"- Mock results question?\n\n"
        f"### 4. Clear division of labor\n"
        f"**Dimension rating:** Developing\nMock observations.\n\n"
        f"**Suggested questions for Q&A:**\n"
        f"- Mock division-of-labor question?\n\n"
        f"### Closing summary\n"
        f"This is a deterministic test-environment mock; no real "
        f"agent ran.\n"
    )


def mock_defense_prep_verdict(team_name: str) -> str:
    """Test-environment stand-in for the Defense Prep arbiter
    output. Same purpose as mock_peer_review_verdict — a parseable
    markdown sheet so the wire-format tests run without an API
    key."""
    return (
        f"**Mock panel — overall readiness:** Developing\n\n"
        f"### 1. Technical / methodological\n"
        f"**Category readiness:** Developing\n\n"
        f"**Q:** Mock technical question for {team_name}?\n"
        f"**Suggested response:** Mock response anchored to the "
        f"submission's findings.\n\n"
        f"### 2. Academic / theoretical\n"
        f"**Category readiness:** Developing\n\n"
        f"**Q:** Mock academic question?\n"
        f"**Suggested response:** Mock academic response.\n\n"
        f"### 3. Governance / practical\n"
        f"**Category readiness:** Developing\n\n"
        f"**Q:** Mock governance question?\n"
        f"**Suggested response:** Mock governance response.\n\n"
        f"### Rehearsal recommendations\n"
        f"Deterministic test-environment mock — no real agent ran.\n"
    )


# ── Context-block helpers (built up by main.py from the inputs) ────────────


def build_peer_review_context_block(
    submission_name: str,
    submission_text: str,
    rubric_dimensions: list[str] | None = None,
) -> dict[str, Any]:
    """Assembles the context block injected into every peer agent
    prompt for the Peer Review Assistant. Returns a dict the
    endpoint serialises into the prompt; tests assert on the
    presence of each field."""
    return {
        "submission_name":   submission_name,
        "submission_text":   submission_text,
        "rubric_dimensions": rubric_dimensions or PEER_RUBRIC_DIMENSIONS,
    }


def render_peer_review_context_block(
    ctx: dict[str, Any],
) -> str:
    """Renders the peer-review context dict into the leading
    string the peer / arbiter prompts prepend. Distinct from the
    builder so tests can exercise the rendering independently
    of the assembly."""
    lines: list[str] = [
        "=== PEER TEAM SUBMISSION ===",
        f"Name: {ctx.get('submission_name', '(unnamed)')}",
        "",
        "FNA 670 midpoint rubric dimensions evaluated:",
    ]
    for i, dim in enumerate(ctx.get("rubric_dimensions",
                                     PEER_RUBRIC_DIMENSIONS), 1):
        lines.append(f"  {i}. {dim}")
    lines.append("")
    lines.append("--- BEGIN SUBMITTED TEXT ---")
    lines.append(ctx.get("submission_text", "(no text extracted)"))
    lines.append("--- END SUBMITTED TEXT ---")
    return "\n".join(lines)


def build_defense_prep_context_block(
    team_name: str,
    draft_text: str,
    categories: list[str] | None = None,
) -> dict[str, Any]:
    """Context for the Thesis Defense Prep flow. The team's OWN
    submitted draft text is the primary input; the categories
    drive the Q&A structure."""
    return {
        "team_name":  team_name,
        "draft_text": draft_text,
        "categories": categories or DEFENSE_CATEGORIES,
    }


def render_defense_prep_context_block(ctx: dict[str, Any]) -> str:
    lines: list[str] = [
        "=== TEAM SUBMISSION ===",
        f"Team: {ctx.get('team_name', '(unnamed)')}",
        "",
        "Q&A categories anticipated for the cohort meetup + July 1 panel:",
    ]
    for i, cat in enumerate(ctx.get("categories", DEFENSE_CATEGORIES), 1):
        lines.append(f"  {i}. {cat}")
    lines.append("")
    lines.append("--- BEGIN TEAM DRAFT TEXT ---")
    lines.append(ctx.get("draft_text", "(no draft text available)"))
    lines.append("--- END TEAM DRAFT TEXT ---")
    return "\n".join(lines)
