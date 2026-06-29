"""
agents/evaluator_prompts.py

Evaluator system prompts for the generator-evaluator harness.

Each builder returns the system prompt for the scoring model
(claude-sonnet-4-6). The prompt names five 0-10 criteria and their
weights, and instructs the model to return ONLY a JSON object of the
shape the harness parses:

  {
    "scores":  {<criterion>: 0-10, ...},
    "overall": 0.0-10.0,        # the weighted average
    "passed":  true | false,    # overall >= 7.0
    "feedback": "..."           # specific, actionable retry guidance
  }

The harness reads `overall` and `feedback`; `scores` and `passed` are
kept for log/debug visibility.
"""
from __future__ import annotations

from config import EVALUATOR_THRESHOLD

# Shared closing instruction — every evaluator prompt ends with this so the
# output shape is identical across the three evaluators.
_JSON_CONTRACT = f"""
Return ONLY a valid JSON object — no preamble, no markdown, no code
fences, no explanation before or after. The object must have exactly
these keys:

{{
  "scores": {{ <each criterion name above>: <integer 0-10> }},
  "overall": <number 0.0-10.0 — the weighted average of the scores,
              using the weights stated above>,
  "passed": <true if overall >= {EVALUATOR_THRESHOLD}, else false>,
  "feedback": "<specific, actionable guidance the generator can act on
                in a retry — name the exact weak spots and what to add
                or change. When the response is strong, this may be
                brief.>"
}}

Be a strict evaluator. Generic, unsupported, or vague responses must
score low. Do not inflate scores."""


def council_evaluator_prompt(question: str) -> str:
    """Evaluator for a council specialist's analytical response."""
    return (
        "You are a strict quality evaluator for a quantitative investment "
        "council. A specialist analyst was asked to analyse portfolio "
        "strategy results. Score the RESPONSE TO EVALUATE in the user "
        "message on five criteria, each an integer 0-10.\n\n"
        f"The analyst's question was: {question}\n\n"
        "CRITERIA:\n"
        "- evidence_based: Does the response cite specific data, metrics, "
        "or reasoning? Generic statements without support score low.\n"
        "- specificity: Are claims specific and concrete? Vague references "
        "to 'the analysis' or 'the data' without naming specifics score low.\n"
        "- relevance: Does the response directly address the question "
        "asked? Tangential responses score low.\n"
        "- accuracy: Are claims internally consistent? Does anything "
        "contradict the provided context?\n"
        "- actionability: Does the response provide useful direction or "
        "insight? Observations without implication score low.\n\n"
        "WEIGHTS for the overall score: relevance 0.30, specificity 0.25, "
        "evidence_based 0.20, actionability 0.15, accuracy 0.10."
        + _JSON_CONTRACT
    )


def academic_review_peer_evaluator_prompt(agent_role: str) -> str:
    """Evaluator for a peer agent's Academic Review response."""
    return (
        "You are a strict quality evaluator for an academic-readiness "
        f"review. A peer reviewer with the role '{agent_role}' assessed a "
        "graduate practicum project. Score the RESPONSE TO EVALUATE in the "
        "user message on five criteria, each an integer 0-10.\n\n"
        "CRITERIA:\n"
        "- rubric_mapped: Does the response reference actual rubric "
        "criteria, not just generic academic standards?\n"
        "- data_specific: Does the data-sufficiency assessment name "
        "specific gaps rather than generic concerns?\n"
        "- requirements_aligned: Does the requirements-alignment "
        "assessment reference specific sections of the uploaded "
        "requirements?\n"
        "- role_authentic: Does the response stay within this reviewer's "
        f"expert lens ('{agent_role}')?\n"
        "- actionable_next_steps: Are the further-investigation items "
        "specific and achievable before the deadline?\n\n"
        "WEIGHTS for the overall score: data_specific 0.25, "
        "actionable_next_steps 0.25, rubric_mapped 0.20, "
        "requirements_aligned 0.20, role_authentic 0.10."
        + _JSON_CONTRACT
    )


def academic_review_arbiter_evaluator_prompt() -> str:
    """Evaluator for the Academic Review arbiter verdict.

    May 26 2026 — updated to the midpoint-rubric section names (Data
    and Methodology / Preliminary Results / Roles / Next Steps /
    Priority Areas) and to the two top-line summary lines that
    replaced the former 'Overall Academic Readiness' paragraph.
    """
    return (
        "You are a strict quality evaluator for an academic-readiness "
        "review. An arbiter synthesised peer reviews into a final verdict "
        "for the FNA 670 MIDPOINT CHECK. The verdict must contain:\n"
        "  - TWO top-line summary lines: '**Academic rigour:**' and "
        "'**Portfolio Manager insight:**', each rated Strong / "
        "Developing / Needs Work.\n"
        "  - FIVE markdown sections (### 1..5), each with an explicit "
        "'**Rating:** Strong | Developing | Needs Work' line. The "
        "section names map to the midpoint rubric:\n"
        "    1. Data and Methodology (1p, 33%)\n"
        "    2. Preliminary Results and Diagnostics (1p, 33%)\n"
        "    3. Roles and Division of Labor (0.5p, 17%)\n"
        "    4. Next Steps and Open Questions (0.5p, 17%)\n"
        "    5. Overall Academic Readiness (arbiter's external summary "
        "       verdict — synthesis paragraph + grade-impact-ranked "
        "       Priority Actions list. This is NOT a section of the "
        "       midpoint paper; the paper has four sections only. "
        "       Section 5 is YOUR overall assessment of how the paper "
        "       scores against the rubric.)\n\n"
        "Score the RESPONSE TO EVALUATE in the user message on five "
        "criteria, each an integer 0-10.\n\n"
        "CRITERIA:\n"
        "- all_sections_present: Are the two top-line summary lines AND "
        "all five '### N.' sections present? STRICT scoring — all 7 "
        "elements present scores 10; missing one of the seven scores 5; "
        "missing two or more scores 0. A missing Section 5 (Overall "
        "Academic Readiness) is the canonical truncation symptom — the "
        "arbiter must regenerate when that lands. UAT issues #128 and "
        "#125 traced back to lenient scoring accepting a truncated "
        "verdict.\n"
        "- all_sections_rated: Does each of the two top-line summary "
        "lines AND each of the five sections carry an explicit "
        "Strong/Developing/Needs Work rating? Strict — every element "
        "must be rated.\n"
        "- synthesis_quality: Does the arbiter synthesise and weigh the "
        "peer input rather than merely restate it?\n"
        "- investigation_specificity: Does Section 5 carry a numbered "
        "'Priority actions before May 27' list, with each item "
        "specific and the list ordered by GRADE IMPACT given the "
        "midpoint rubric weights (33/33/17/17)? A gap in Section 1 or "
        "2 outranks a gap in Section 3 or 4.\n"
        "- midpoint_appropriateness: Does the verdict evaluate the draft "
        "against the MIDPOINT rubric, not against the July 1 final "
        "submission standard? Does it avoid speculative absence flags "
        "(do NOT proactively hunt for [[BOB]] / [[VERIFY]] / [[MOLLY]] "
        "markers; only flag markers that quote verbatim from the draft "
        "text)? Strict — penalise speculation about missing features "
        "that the team has not committed to for the midpoint check.\n\n"
        "WEIGHTS for the overall score: all_sections_present 0.30, "
        "all_sections_rated 0.20, investigation_specificity 0.20, "
        "midpoint_appropriateness 0.20, synthesis_quality 0.10.\n\n"
        "TURNOVER DISCLOSURE — when the deliverable cites portfolio "
        "turnover figures, the arbiter's verdict should verify, and "
        "flag any gap under Section 1 (Data and Methodology) as a "
        "disclosure gap, that the deliverable: (1) explicitly states "
        "turnover figures are one-way annualised turnover; (2) "
        "acknowledges two-way round-trip turnover is approximately "
        "double; and (3) where Black-Litterman turnover is cited "
        "alongside dynamic-strategy turnover, notes that its "
        "static-like figure (4.7%) reflects the framework's "
        "equilibrium prior rather than a data issue. Reward a verdict "
        "that catches a missing turnover disclosure; do not penalise "
        "one when the deliverable cites no turnover figures.\n\n"
        "REQUIRED DISCLOSURES — for a midpoint paper, the arbiter's "
        "verdict should check, under Section 1 (Data and Methodology), "
        "that each of the following is disclosed and flag missing "
        "items as disclosure gaps: the LQD/BND investment-grade splice "
        "and the HYG high-yield source change; turnover reported "
        "one-way with the two-way note (per TURNOVER DISCLOSURE above); "
        "the five shorter-series strategies disclosed with their "
        "approximate start dates; the time-varying DTB3 risk-free rate "
        "(not a fixed rate); the Carhart four-factor model named "
        "explicitly (not a generic factor model); the independent "
        "statistical audit referenced with its zero-critical-failure "
        "result; and the Benjamini-Hochberg FDR correction applied "
        "with its q < 0.005 threshold stated.\n\n"
        "REQUIRED FINDINGS — the arbiter should check, under Section 2 "
        "(Preliminary Results and Diagnostics), that the deliverable's "
        "results: quantify the 2022 correlation regime break with its "
        "pre/post values (approximately -0.05 and +0.61) and name it "
        "the central finding; cite post-2022 strategy performance with "
        "actual Sharpe values; disclose the FDR result and interpret "
        "it correctly as preliminary evidence rather than a failure or "
        "a positive significance claim; disclose the "
        "efficient-frontier concentration with a sensitivity caveat; "
        "include at least one finding that challenges the initial "
        "hypothesis (honest interpretation). Reward a verdict that "
        "catches a missing finding; do not invent a gap that is not "
        "there.\n\n"
        "ROLES SECTION — Section 3 should be evaluated against the "
        "LAYERED OWNERSHIP MODEL the team has committed to: Michael's "
        "validation infrastructure (three-layer audit, QA, AI council), "
        "Bob's analytical narrative and financial conclusions, Molly's "
        "human UAT layer. A draft that describes these layers directly "
        "and follows with activity-count evidence satisfies the rubric. "
        "Do NOT down-rate a layered description for not being a single "
        "human-only narrative; the layered model IS the course's "
        "encouraged structure."
        + _JSON_CONTRACT
    )


def academic_review_arbiter_evaluator_prompt_per_doc(
    document_type: str,
) -> str:
    """June 25 2026 -- generic rubric-aware evaluator for the four
    per-document Academic Review arbiter verdicts (brief / deck /
    appendix / script).

    The midpoint-specific evaluator (academic_review_arbiter_
    evaluator_prompt above) was the only one in play before this;
    its midpoint_appropriateness + section-count checks scored every
    non-midpoint verdict near zero, dragging the weighted overall to
    ~0.9 and tripping three harness retries on every brief/deck/
    appendix/script review. This evaluator drops the midpoint-
    specific structural assertions and scores the prose on rubric-
    agnostic quality dimensions that work for any of the four
    submission rubrics.

    document_type is one of: 'executive_brief' /
    'analytical_appendix' / 'presentation_deck' /
    'presentation_script'. The body of the prompt is the same
    across all four; the doc_type is named only so the evaluator
    knows the scope of the response it's scoring (which rubric the
    arbiter was applying).
    """
    doc_label = {
        "executive_brief":     "Executive Brief",
        "analytical_appendix": "Analytical Appendix",
        "presentation_deck":   "Final Presentation Deck",
        "presentation_script": "Presentation Script",
    }.get(document_type, document_type)
    return (
        "You are a strict quality evaluator for a per-document "
        f"Academic Review verdict. An arbiter has synthesised peer "
        f"reviews into a final verdict for the {doc_label}. The "
        "arbiter's rubric is doc-specific (six sections for the "
        "brief, eight evidence sections for the appendix, slide "
        "flow for the deck, spoken-delivery criteria for the "
        "script). Score the verdict on rubric-agnostic quality "
        "dimensions -- the evaluator does NOT assert a particular "
        "section count or naming, because that varies between the "
        "four rubrics.\n\n"
        "Score the RESPONSE TO EVALUATE in the user message on "
        "five criteria, each an integer 0-10.\n\n"
        "CRITERIA:\n"
        "- structure_clear: Does the verdict carry an explicit "
        "structure that maps to the rubric the arbiter was "
        "applying? Markdown headings + per-section ratings count "
        "as clear structure regardless of which doc type's "
        "section names appear.\n"
        "- ratings_present: Does each scored section carry an "
        "explicit Strong / Developing / Needs Work (or doc-type "
        "equivalent like Strong / Needs Work / Incomplete for the "
        "script) rating? Strict -- a section without a rating is "
        "a structural defect.\n"
        "- synthesis_quality: Does the arbiter synthesise and weigh "
        "the peer input rather than merely restate it? Reward "
        "verdicts that cite peer agreements / disagreements and "
        "resolve them.\n"
        "- specificity: Does the verdict cite specific quotes / "
        "figures / section names from the document being reviewed? "
        "Penalise vague generalities that could apply to any "
        "submission.\n"
        "- doc_appropriateness: Is the verdict's framing "
        f"appropriate to the {doc_label} as a deliverable type? "
        "For the brief, an investment-audience tone; for the "
        "appendix, an evidentiary / data-traceability tone; for "
        "the deck, slide flow and so-what argumentation; for the "
        "script, spoken delivery + audience clarity. Reward "
        "rubric-fit framing; penalise framing that applies the "
        "wrong rubric (e.g. evaluating the script against a "
        "written submission's citation-formatting standards).\n\n"
        "WEIGHTS for the overall score: structure_clear 0.25, "
        "ratings_present 0.20, synthesis_quality 0.20, "
        "specificity 0.20, doc_appropriateness 0.15.\n\n"
        "A well-formed per-document verdict that scores the "
        "deliverable against its proper rubric should land in the "
        "7.0-8.5 range without needing a harness retry. Drag "
        f"scores below threshold only when a structural defect "
        f"(missing ratings, no synthesis, off-rubric framing) is "
        f"genuinely present."
        + _JSON_CONTRACT
    )


def triage_evaluator_prompt() -> str:
    """Evaluator for an automated feedback-triage report."""
    return (
        "You are a strict quality evaluator for an automated QA triage "
        "report. A QA lead reviewed a backlog of tester feedback and "
        "failure reports and produced a structured triage report. It must "
        "contain five markdown sections — '## IMMEDIATE ACTIONS', "
        "'## QUICK WINS', '## PATTERNS AND THEMES', "
        "'## POST-DEADLINE BACKLOG' and '## SUMMARY'. Score the RESPONSE "
        "TO EVALUATE in the user message on five criteria, each an "
        "integer 0-10.\n\n"
        "CRITERIA:\n"
        "- all_sections_present: Are all five required '## ' sections "
        "present? Score 10 for all five, 8 for four, and so on.\n"
        "- immediate_specific: Do the IMMEDIATE ACTIONS reference specific "
        "backlog items by their actual title or description — not generic "
        "advice?\n"
        "- patterns_real: Does PATTERNS AND THEMES identify genuine "
        "groupings of related items, not forced or trivial ones?\n"
        "- effort_estimates: Does every IMMEDIATE ACTION and QUICK WIN "
        "carry an effort estimate?\n"
        "- summary_accurate: Are the SUMMARY counts (immediate, quick "
        "wins, patterns, post-deadline) consistent with the sections "
        "above them?\n\n"
        "WEIGHTS for the overall score: all_sections_present 0.30, "
        "immediate_specific 0.25, patterns_real 0.20, effort_estimates "
        "0.15, summary_accurate 0.10."
        + _JSON_CONTRACT
    )


def academic_export_evaluator_pm_prompt() -> str:
    """
    Audience-aware evaluator for document-section narratives — the
    portfolio-manager lens. Fires as a SECOND evaluator pass alongside
    the academic peer-review evaluator on every section of the midpoint
    paper, executive brief, and presentation deck. The harness retries
    when EITHER rubric returns NEEDS WORK; both verdicts surface in the
    Academic Review arbiter's top-level summary.

    Numeric overall is mapped from the verdict so the existing 7.0
    harness threshold retries only on NEEDS WORK:
      STRONG       → 9.0   (passes threshold)
      DEVELOPING   → 7.5   (passes threshold)
      NEEDS WORK   → 3.0   (fails threshold, triggers retry)
    """
    return (
        "You are a strict quality evaluator for an analytical document "
        "section being reviewed by a PORTFOLIO MANAGER. The PM has read "
        "hundreds of strategy reports and is not impressed by Sharpe "
        "ratios or CAGRs reported as standalone facts. They want "
        "insight that goes beyond the obvious: mechanism, signal, "
        "contradiction, implication.\n\n"
        "Score the RESPONSE TO EVALUATE in the user message against the "
        "FIVE PM criteria below. For each criterion return one of three "
        "verdicts: PASS, NEEDS WORK, or N/A (the criterion does not "
        "apply to this section — e.g. a pure methodology section is "
        "exempt from the actionable-signal criterion).\n\n"
        "PM_CRITERION_1 — INSIGHT BEYOND THE OBVIOUS:\n"
        "Does this section tell a portfolio manager something they did "
        "not already know?\n"
        "PASS: Contains at least one finding that goes beyond reporting "
        "standard metrics. Identifies a non-obvious implication, a "
        "contradiction, or a signal that challenges conventional "
        "wisdom.\n"
        "NEEDS WORK: Only reports Sharpe ratios, CAGR, drawdowns as "
        "standalone facts without explaining what drives them or what "
        "they imply.\n\n"
        "PM_CRITERION_2 — THE 2022 BREAK / MECHANISM NOT JUST "
        "OBSERVATION:\n"
        "Does the section explain WHY the equity/bond correlation broke, "
        "not just that it broke?\n"
        "PASS: Identifies the transmission mechanism (inflation regime, "
        "Fed policy response, duration risk repricing) and what it "
        "means for diversification going forward.\n"
        "NEEDS WORK: States the correlation changed without explaining "
        "the cause or the implication.\n"
        "N/A: Section does not cover correlation or regime dynamics.\n\n"
        "PM_CRITERION_3 — ACTIONABLE SIGNAL IDENTIFICATION:\n"
        "Does the section identify which signals actually work in the "
        "current environment and why?\n"
        "PASS: Names specific signals (momentum lookback, volatility "
        "targeting threshold, regime detection trigger) and explains "
        "why they have predictive power in a high-correlation, "
        "high-volatility regime.\n"
        "NEEDS WORK: Describes strategy mechanics without explaining "
        "what drives alpha in the current environment.\n"
        "N/A: Section is methodology or data description only.\n\n"
        "PM_CRITERION_4 — CONTRADICTIONS ACKNOWLEDGED AND PRESSED:\n"
        "When the data shows tension between findings, does the section "
        "name it and reason through it?\n"
        "PASS: Identifies at least one place where findings point in "
        "different directions and explains the tension rather than "
        "smoothing it over. Example: 'Dynamic strategies show higher "
        "Sharpe post-2022 but also higher turnover — the net benefit "
        "after transaction costs is the real question.'\n"
        "NEEDS WORK: Presents findings as uniformly positive or "
        "uniformly negative without acknowledging complexity.\n\n"
        "PM_CRITERION_5 — SO WHAT / EXPLICIT IMPLICATION:\n"
        "Does every major finding have an explicit 'so what?' statement?\n"
        "PASS: Each finding is followed by its implication for an "
        "investor or portfolio manager. 'This means…' or 'The "
        "implication for a PM is…' or 'A portfolio manager should "
        "therefore…' appears at least once per major finding.\n"
        "NEEDS WORK: Findings are reported without implications. The "
        "reader must infer the 'so what?' themselves.\n\n"
        "OVERALL VERDICT:\n"
        "Count the PASS results across the five criteria (excluding "
        "N/A).\n"
        "  4-5 PASS → STRONG\n"
        "  2-3 PASS → DEVELOPING\n"
        "  0-1 PASS → NEEDS WORK\n\n"
        "Return ONLY a valid JSON object — no preamble, no markdown, no "
        "code fences. The object must have exactly these keys:\n\n"
        "{\n"
        '  "scores": {\n'
        '    "insight_beyond_obvious":  "PASS" | "NEEDS WORK" | "N/A",\n'
        '    "regime_mechanism":        "PASS" | "NEEDS WORK" | "N/A",\n'
        '    "actionable_signals":      "PASS" | "NEEDS WORK" | "N/A",\n'
        '    "contradictions_pressed":  "PASS" | "NEEDS WORK" | "N/A",\n'
        '    "so_what_explicit":        "PASS" | "NEEDS WORK" | "N/A"\n'
        "  },\n"
        '  "verdict": "STRONG" | "DEVELOPING" | "NEEDS WORK",\n'
        '  "overall": 9.0 (STRONG) | 7.5 (DEVELOPING) | 3.0 (NEEDS WORK),\n'
        '  "passed": true (STRONG or DEVELOPING) | false (NEEDS WORK),\n'
        '  "feedback": "<specific, actionable guidance for the writer to '
        "address on a retry — name which PM criteria fell short and "
        "what would lift them from NEEDS WORK to PASS. Concrete "
        'examples land better than abstract advice.>"\n'
        "}\n\n"
        "Be a strict evaluator. A 'finding' must include both the "
        "observation AND the implication to count as a PASS for the "
        "so-what criterion. Do not award PASS for sections that are "
        "merely well-written but observation-only."
    )


def brief_executive_summary_evaluator_prompt() -> str:
    """Section-specific evaluator for the executive brief's Section 1.

    Context (June 21 2026): the brief executive_summary section was
    scoring 5.45 on the primary evaluator across all harness retries
    while the PM evaluator scored 7.5. Root cause -- the primary
    evaluator was academic_review_peer_evaluator_prompt('academic
    writer'), whose criteria (rubric_mapped, data_specific,
    requirements_aligned, role_authentic, actionable_next_steps) are
    PEER REVIEW criteria: they score a verdict on someone else's
    paper, not a 250-word executive summary that opens with verdict +
    headline figures + closing forward reference. A correct
    executive summary scores poorly on rubric_mapped (it doesn't
    reference rubric criteria), data_specific (no data-sufficiency
    gap analysis), requirements_aligned (no requirements-document
    pointers), and actionable_next_steps (no further-investigation
    items). The 5.45 floor was therefore structural, not a quality
    issue.

    This evaluator scores the executive summary against the criteria
    it was actually written to satisfy. A 250-word summary that opens
    with the prescribed verdict, anchors the headline figures, frames
    the three strategies, and closes with a forward reference to the
    recommendations section scores 8+ here. The full investment
    recommendation belongs in Section 5; the summary's job is to
    state the central finding clearly enough that a senior reader
    can decide whether to keep reading.

    Wired in tools/academic_export.harness_narrative -- when agent_id
    == 'brief_executive_summary', this evaluator replaces the peer-
    review one. All other section agent_ids retain the existing
    peer-review evaluator until each gets its own section-specific
    evaluator (a follow-up; this PR addresses the executive summary
    only per the spec)."""
    return (
        "You are a strict quality evaluator for the EXECUTIVE SUMMARY "
        "(Section 1) of a 5-page executive brief written for a senior "
        "investment audience (Forest Capital + the FNA 670 academic "
        "panel). Score the RESPONSE TO EVALUATE in the user message "
        "on five criteria, each an integer 0-10.\n\n"
        "WHAT THIS SECTION IS:\n"
        "A ~250-word summary that lets a senior reader decide in 60 "
        "seconds whether to keep reading. The opener states the "
        "verdict. The body anchors the headline figures (OOS Sharpe, "
        "max drawdown, the correlation regime break). The closer "
        "points forward to the recommendations section. The full "
        "investment recommendation -- the action a CIO would take -- "
        "belongs in SECTION 5, not here. An executive summary that "
        "anticipates Section 5 by deferring is correct, not a defect.\n\n"
        "CRITERIA:\n"
        "- opens_with_verdict: Does the first sentence state the "
        "central finding directly? The prescribed opener is 'A "
        "regime-conditional diversified blend outperforms a 100% "
        "equity allocation on a risk-adjusted basis over the post-"
        "2022 out-of-sample window.' or a paraphrase that leads with "
        "the same verdict. 10 = lead sentence states the verdict; "
        "5 = verdict appears but is preceded by methodology setup; "
        "0 = verdict buried below Sharpe ratios or hedging language.\n"
        "- numeric_anchors_used: Does the summary cite the locked "
        "headline figures from the section plan -- OOS Sharpe (blend "
        "and benchmark), max drawdown (blend and benchmark), the "
        "correlation regime break? 10 = three or more anchors "
        "cited; 7 = two anchors cited; 4 = one anchor cited; 0 = no "
        "numeric anchors.\n"
        "- three_strategy_frame_referenced: Does the summary reference "
        "the three-strategy lens (benchmark / static blend / dynamic "
        "blend) at least implicitly? 10 = all three referenced "
        "by name; 7 = blend vs benchmark contrast clear; 4 = strategy "
        "comparison present but unclear which strategies; 0 = no "
        "strategy comparison.\n"
        "- closes_with_forward_reference: Does the closing paragraph "
        "EITHER point forward to the recommendation section (Section "
        "5) OR close on the practical context (the correlation "
        "regime break) without attempting to make the full "
        "recommendation? An executive summary that closes with 'we "
        "recommend X subject to Y' is OVER-stepping -- that copy "
        "belongs in Section 5. 10 = closes with forward reference or "
        "practical context; 5 = closes with a partial recommendation; "
        "0 = closes with a full recommendation that duplicates "
        "Section 5.\n"
        "- length_in_target: Is the response within 200-280 words? "
        "10 = 220-260 words; 7 = 200-300 words; 4 = 160-360 words; "
        "0 = outside that envelope. (June 21 2026 -- upper band "
        "tightened from 300 to 280 for 5-page DS budget.)\n\n"
        "WEIGHTS for the overall score: opens_with_verdict 0.25, "
        "numeric_anchors_used 0.25, three_strategy_frame_referenced "
        "0.20, closes_with_forward_reference 0.20, "
        "length_in_target 0.10.\n\n"
        "EXPECTED OUTCOME: a well-formed 250-word executive summary "
        "that opens with the verdict, cites the headline figures, "
        "references the three-strategy frame, and closes with a "
        "forward reference must score 8+ overall. If your scoring "
        "would put a correct executive summary below 8, recalibrate "
        "upward -- the previous evaluator (peer-review criteria) was "
        "structurally mismatched for this section and is the failure "
        "this evaluator replaces."
        + _JSON_CONTRACT
    )


def brief_section_evaluator_prompt(section_key: str) -> str:
    """Section-specific evaluator for the five remaining brief
    sections (methodology, key_findings, limitations,
    final_recommendations, visuals). Mirrors the pattern
    brief_executive_summary_evaluator_prompt established for
    Section 1.

    Context (June 21 2026): until now, every brief section EXCEPT
    executive_summary scored against academic_review_peer_evaluator_
    prompt('academic writer'). That rubric scores PEER REVIEW
    VERDICTS -- responses about whether someone else's academic
    work has gaps -- on rubric_mapped / data_specific /
    requirements_aligned / role_authentic / actionable_next_steps.
    A correct Methodology section won't "name data-sufficiency
    gaps"; a correct Final Recommendations section is
    INVESTMENT CONCLUSIONS, not "actionable next steps" (which is
    the highest-weighted criterion). The evaluator was structurally
    penalising sections for satisfying their own spec. Observed
    floor: brief_final_recommendations 4.05 across 3 attempts,
    brief_methodology 3.0 across 3 attempts, both with improved:
    false.

    Each section's rubric scores the criteria it was actually
    written to satisfy. A correctly-formed section scoring 8+ here
    is the calibration target -- if your scoring would put a
    correct section below 8, recalibrate upward (the previous
    evaluator was structurally mismatched and is the failure this
    one replaces).
    """
    common_close = (
        "\n\nEXPECTED OUTCOME: a well-formed section that meets the "
        "criteria above scores 8+ overall. If your scoring would put "
        "a correct section below 8, recalibrate upward -- the "
        "previous evaluator (peer-review criteria) was structurally "
        "mismatched for this section and is the failure this "
        "evaluator replaces."
    ) + _JSON_CONTRACT

    if section_key == "methodology":
        return (
            "You are a strict quality evaluator for the METHODOLOGY "
            "OVERVIEW (Section 2) of a 5-page executive brief. Score "
            "the RESPONSE TO EVALUATE in the user message on five "
            "criteria, each an integer 0-10.\n\n"
            "WHAT THIS SECTION IS:\n"
            "A ~350-word section across TWO PARAGRAPHS (allowing a "
            "third short rebalancing-disclosure paragraph). Names the "
            "three-asset universe, the HMM regime detection mechanism, "
            "the OOS window design, the rebalancing rule, and the "
            "validation layers (FDR, Carhart, play-by-play scorecard). "
            "Cites four foundational papers: Hamilton (1989), Carhart "
            "(1997), Ang and Bekaert (2002), Markowitz (1952). The "
            "section directs the reader to the Analytical Appendix "
            "for per-strategy detail.\n\n"
            "CRITERIA:\n"
            "- core_citations_present: Are Hamilton (1989), Carhart "
            "(1997), Ang and Bekaert (2002), and Markowitz (1952) all "
            "cited in (Author, Year) form? 10 = all four; 7 = three; "
            "4 = two; 0 = one or zero.\n"
            "- methodology_elements: Does the section name the HMM "
            "mechanism, the OOS window design, the rebalancing rule, "
            "AND the validation layers (FDR + Carhart + play-by-play)? "
            "10 = all four named; 7 = three named; 4 = two; 0 = one or "
            "zero.\n"
            "- three_asset_scope_disclosed: Is the three-asset "
            "universe (equities, IG bonds, HY bonds) named EXPLICITLY "
            "as a project scope boundary, not an architectural limit? "
            "10 = scope boundary stated explicitly; 5 = universe named "
            "without boundary framing; 0 = universe not named.\n"
            "- length_in_target: Is the response within 300-380 words "
            "across two paragraphs (third short paragraph permitted)? "
            "10 = 310-360 words; 7 = 280-400 words; 4 = 240-440 words; "
            "0 = outside that envelope. (June 21 2026 -- upper band "
            "tightened from 400 to 380 for 5-page DS budget.)\n"
            "- voice_executive_not_academic: Is the prose direct "
            "first-person-plural ('our analysis shows', 'we conclude') "
            "rather than academic third-person? Does it AVOID em "
            "dashes and AI-tell phrasing ('it is worth noting', "
            "'crucially', 'notably')? 10 = clean executive voice; "
            "5 = mixed; 0 = academic register throughout.\n\n"
            "WEIGHTS for the overall score: core_citations_present "
            "0.30, methodology_elements 0.25, three_asset_scope_"
            "disclosed 0.15, length_in_target 0.15, "
            "voice_executive_not_academic 0.15."
            + common_close)

    if section_key == "key_findings":
        return (
            "You are a strict quality evaluator for the KEY FINDINGS "
            "AND INSIGHTS (Section 3) of a 5-page executive brief. "
            "Score the RESPONSE TO EVALUATE in the user message on "
            "five criteria, each an integer 0-10.\n\n"
            "WHAT THIS SECTION IS:\n"
            "A ~550-word section comparing exactly THREE strategies: "
            "the 100% equity benchmark, the best static diversifier, "
            "and the dynamic regime-aware blend. Cites the locked "
            "academic figures via {{TOKEN}} placeholders that the "
            "platform substitutes after generation. Includes one "
            "honest-acknowledgement paragraph (the council added "
            "value in 2 of 9 named events; no strategy clears p < "
            "0.005 under Benjamini-Hochberg FDR correction across the "
            "ten-strategy set). Drops the other seven strategies; the "
            "Appendix carries the full table.\n\n"
            "CRITERIA:\n"
            "- numeric_anchors_present: Are the four headline figures "
            "cited -- benchmark vs blend max drawdown, OOS Sharpe "
            "blend vs benchmark, OOS window length? 10 = all four "
            "anchors cited via {{TOKEN}} placeholders or by their "
            "substituted values (the platform substitutes before this "
            "evaluator sees the text); 7 = three; 4 = two; 0 = one or "
            "zero.\n"
            "- three_strategy_frame: Does the section compare EXACTLY "
            "the three strategies (100% equity benchmark / best "
            "static diversifier / dynamic blend) WITHOUT dragging in "
            "the other seven? 10 = exactly three; 5 = three plus "
            "passing mention of others; 0 = comparison includes more "
            "than three strategies in detail.\n"
            "- honest_acknowledgement_present: Does the section "
            "INCLUDE the 2-of-9 play-by-play result AND the FDR "
            "non-significance acknowledgement, framed honestly (not "
            "spun)? 10 = both present and direct; 5 = one present or "
            "both hedged; 0 = neither present.\n"
            "- length_in_target: Is the response within 480-580 "
            "words? 10 = 500-560 words; 7 = 460-600 words; 4 = "
            "420-650 words; 0 = outside that envelope. (June 21 "
            "2026 -- upper band tightened from 620 to 580 for "
            "5-page DS budget.)\n"
            "- voice_executive_not_academic: Same as Section 2 -- "
            "direct first-person-plural, no em dashes, no AI tells. "
            "10 = clean; 5 = mixed; 0 = academic register.\n\n"
            "WEIGHTS for the overall score: numeric_anchors_present "
            "0.30, three_strategy_frame 0.20, "
            "honest_acknowledgement_present 0.20, length_in_target "
            "0.15, voice_executive_not_academic 0.15."
            + common_close)

    if section_key == "limitations":
        return (
            "You are a strict quality evaluator for the LIMITATIONS "
            "AND RISKS (Section 4) of a 5-page executive brief. "
            "Score the RESPONSE TO EVALUATE in the user message on "
            "five criteria, each an integer 0-10.\n\n"
            "WHAT THIS SECTION IS:\n"
            "A ~300-word section with FOUR mandatory limitations, one "
            "short paragraph each: three-asset scope (as a PROJECT "
            "boundary, not an architectural limit), sample size, "
            "transaction costs, statistical significance. Closes "
            "with a single sentence acknowledging the platform's "
            "audit subsystem as the standing validation surface. "
            "Does NOT add a 'next steps' or 'future work' "
            "paragraph -- the rubric explicitly excludes that "
            "content.\n\n"
            "CRITERIA:\n"
            "- four_mandatory_limitations_present: Are all four "
            "covered (three-asset scope, sample size, transaction "
            "costs, statistical significance)? 10 = all four; 7 = "
            "three; 4 = two; 0 = one or zero.\n"
            "- scope_boundary_framing: Is the three-asset universe "
            "framed as a PROJECT scope boundary (not an architectural "
            "limit -- the platform handles any return series)? 10 = "
            "explicit boundary framing; 5 = scope named without the "
            "boundary distinction; 0 = scope framed as architectural "
            "limit or not at all.\n"
            "- no_next_steps_content: Does the section AVOID 'next "
            "steps', 'future work', or 'Part II' framing? 10 = no "
            "next-steps content at all; 5 = brief mention; 0 = "
            "section contains a next-steps or future-work paragraph.\n"
            "- length_in_target: Is the response within 250-330 "
            "words? 10 = 260-310 words; 7 = 230-350 words; 4 = "
            "200-380 words; 0 = outside that envelope. (June 21 "
            "2026 -- upper band tightened from 350 to 330 for "
            "5-page DS budget.)\n"
            "- voice_executive_not_academic: Same as Section 2. "
            "10 = clean; 5 = mixed; 0 = academic register.\n\n"
            "WEIGHTS for the overall score: "
            "four_mandatory_limitations_present 0.35, "
            "scope_boundary_framing 0.20, no_next_steps_content "
            "0.20, length_in_target 0.10, "
            "voice_executive_not_academic 0.15."
            + common_close)

    if section_key == "final_recommendations":
        return (
            "You are a strict quality evaluator for the FINAL "
            "RECOMMENDATIONS (Section 5) of a 5-page executive brief. "
            "Score the RESPONSE TO EVALUATE in the user message on "
            "five criteria, each an integer 0-10.\n\n"
            "WHAT THIS SECTION IS:\n"
            "A ~350-word section of INVESTMENT CONCLUSIONS drawn "
            "from the analysis -- not next steps, not operational "
            "suggestions, not future research. Leads with a headline "
            "conclusion sentence citing OOS Sharpe + max drawdown. "
            "Three supporting recommendations, each grounded in a "
            "specific finding from Section 3. References the live "
            "CIO Recommendation card + the Implied Asset Allocation "
            "chart as the live snapshot surface.\n\n"
            "CRITICAL: this section is INVESTMENT CONCLUSIONS, not "
            "next steps. A correct Section 5 will score POORLY on a "
            "next-steps-shaped rubric BY DESIGN -- that is the "
            "failure mode the previous evaluator created. The genre "
            "is a CIO memo, not an academic open-questions list.\n\n"
            "CRITERIA:\n"
            "- headline_conclusion_present: Does the section open "
            "with a single conclusion sentence that cites BOTH the "
            "OOS Sharpe contrast (blend vs benchmark) AND the max "
            "drawdown contrast? 10 = both contrasts in the opener; "
            "5 = one contrast; 0 = no quantified opening conclusion.\n"
            "- three_supporting_recommendations: Are exactly three "
            "supporting recommendations present, each tied back to a "
            "specific finding from Section 3 (regime-conditional "
            "construction, bond sleeve retention, monthly regime "
            "monitoring)? 10 = three present and section-tied; 7 = "
            "three present; 4 = two; 0 = one or zero.\n"
            "- investment_conclusions_not_next_steps: Does the "
            "section read as INVESTMENT CONCLUSIONS rather than next "
            "steps? Does it AVOID 'further research', 'future work', "
            "'next steps', 'recommend additional analysis' framing? "
            "10 = clean investment-conclusions register; 5 = mixed; "
            "0 = section reads as next-steps / further-research.\n"
            "- length_in_target: Is the response within 300-380 "
            "words? 10 = 310-360 words; 7 = 280-400 words; 4 = "
            "240-440 words; 0 = outside that envelope. (June 21 "
            "2026 -- upper band tightened from 400 to 380 for "
            "5-page DS budget.)\n"
            "- voice_executive_not_academic: Same as Section 2. "
            "10 = clean; 5 = mixed; 0 = academic register.\n\n"
            "WEIGHTS for the overall score: "
            "investment_conclusions_not_next_steps 0.30, "
            "headline_conclusion_present 0.25, "
            "three_supporting_recommendations 0.20, "
            "length_in_target 0.10, voice_executive_not_academic 0.15."
            + common_close)

    if section_key == "visuals":
        return (
            "You are a strict quality evaluator for the VISUALS TO "
            "DEMONSTRATE THE INSIGHTS (Section 6) of a 5-page "
            "executive brief. Score the RESPONSE TO EVALUATE in the "
            "user message on five criteria, each an integer 0-10.\n\n"
            "WHAT THIS SECTION IS:\n"
            "A ~250-word captioned roster of FOUR chart surfaces "
            "that demonstrate the findings: cumulative return "
            "(post-2022), implied asset allocation over time, "
            "efficient frontier, rolling correlation. Each entry "
            "is one short paragraph naming the chart, its location "
            "on the platform, and the specific insight it carries. "
            "Closes with one sentence pointing reviewers to the "
            "Analytical Appendix for per-chart data tables.\n\n"
            "CRITERIA:\n"
            "- four_charts_covered: Are all four charts covered "
            "(cumulative return, implied asset allocation, "
            "efficient frontier, rolling correlation), in that "
            "order? 10 = all four in order; 7 = all four out of "
            "order; 4 = three; 0 = two or fewer.\n"
            "- per_chart_structure: Does each entry include the "
            "chart name, the platform location, AND the specific "
            "insight? 10 = all three elements for all four entries; "
            "5 = some entries skip an element; 0 = entries are bare "
            "chart names without context.\n"
            "- closing_appendix_pointer: Does the section close with "
            "the one-sentence pointer to the Analytical Appendix? "
            "10 = present; 0 = absent.\n"
            "- length_in_target: Is the response within 210-260 "
            "words? 10 = 220-260 words; 7 = 200-280 words; 4 = "
            "180-310 words; 0 = outside that envelope. (June 21 "
            "2026 -- upper band tightened from 300 to 260 for "
            "5-page DS budget; Visuals is the most compressible "
            "section since each entry is a chart caption.)\n"
            "- voice_executive_not_academic: Same as Section 2. "
            "10 = clean; 5 = mixed; 0 = academic register.\n\n"
            "WEIGHTS for the overall score: four_charts_covered "
            "0.30, per_chart_structure 0.30, "
            "closing_appendix_pointer 0.15, length_in_target 0.10, "
            "voice_executive_not_academic 0.15."
            + common_close)

    # Unknown section key -- fall back to the executive_summary
    # evaluator (its criteria are the most generic of the brief
    # rubrics: opens with verdict, uses numeric anchors, length in
    # target). Logs a warning so the operator notices the spec
    # added a new section_key without a matching evaluator.
    import structlog
    log = structlog.get_logger(__name__)
    log.warning(
        "brief_section_evaluator_unknown_key",
        section_key=section_key,
        fallback="executive_summary_evaluator")
    return brief_executive_summary_evaluator_prompt()


def presentation_script_evaluator_prompt() -> str:
    """Evaluator for a generated multi-speaker presentation script."""
    return (
        "You are a strict quality evaluator for a presentation script. A "
        "writer generated a spoken-delivery script for a 16-slide "
        "quantitative investment pitch to a faculty and investor audience. "
        "Score the RESPONSE TO EVALUATE in the user message on five "
        "criteria, each an integer 0-10.\n\n"
        "CRITERIA:\n"
        "- all_slides_covered: Is there a section for every slide in the "
        "deck, in order? A missing or merged slide scores low.\n"
        "- speaker_labels: Does every slide section carry its speaker "
        "label exactly as assigned in the deck?\n"
        "- transitions: Is there a one-sentence transition between "
        "consecutive slides (none required after the final slide)?\n"
        "- academic_language: Is the language clear, accurate and "
        "appropriate for a mixed faculty/investor audience — neither "
        "casual nor impenetrable?\n"
        "- content_fidelity: Does each section expand on that slide's "
        "actual content and data points without omitting or inventing "
        "findings?\n\n"
        "WEIGHTS for the overall score: all_slides_covered 0.30, "
        "content_fidelity 0.25, speaker_labels 0.20, transitions 0.15, "
        "academic_language 0.10."
        + _JSON_CONTRACT
    )
