"""Executive brief — six-section rubric-trap-aware structure.

Rebuilt May 30 2026. Pins the six-section contract so a future
narrative-prompt edit cannot silently drop or reorder the rubric-trap
answer:

  1. The Static Recommendation     — Part I answer, plainly stated
  2. The Central Finding           — 2022 break as our INTERPRETATION
  3. Analytical Judgment           — the five human decisions
  4. Platform as Evidence Base     — platform AFTER human judgment
  5. Evidence Summary              — findings in interpretive terms
  6. Part II Preview               — regime-conditional as logical consequence

The brief addresses the rubric's "where is the human judgment" question
DIRECTLY. The platform is the evidence layer, not the conclusion layer.
Tone rules forbid "the platform found"; mandate "our analysis shows".
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


# ── Editor-content section order ───────────────────────────────────────────


def test_brief_sections_in_required_order():
    from tools.editor_content import _EXEC_BRIEF_SECTIONS
    headings = [h for h, _key, _callout in _EXEC_BRIEF_SECTIONS]
    assert headings == [
        "1. The Static Recommendation",
        "2. The Central Finding",
        "3. Analytical Judgment and Methodology Decisions",
        "4. Platform as Evidence Base",
        "5. Evidence Summary",
        "6. Part II Preview",
    ]


def test_brief_section_keys_match_narrative_contract():
    """The editor adapter and the docx builder must agree on the keys.
    A drift here would silently leave a section showing [DATA PENDING]."""
    from tools.editor_content import _EXEC_BRIEF_SECTIONS
    keys = [key for _h, key, _c in _EXEC_BRIEF_SECTIONS]
    assert keys == [
        "static_rec",
        "central_finding",
        "human_judgment",
        "platform_role",
        "evidence",
        "part_ii_preview",
    ]


# ── docx renderer reads the new keys ──────────────────────────────────────


def test_build_executive_brief_renders_all_six_section_headings():
    from tools.academic_docx import build_executive_brief
    data = {
        "study_period": {"start": "2002-07", "end": "2025-12",
                         "n_months": 282, "ff_factors_end": "2025-12"},
        "regime_conditional": [],
        "summary_statistics": [],
        "drawdown_comparison": [],
        "factor_loadings": [],
        "audit_disclosures": None,
    }
    narratives = {
        "static_rec":      "STATIC_REC_PARAGRAPH",
        "central_finding": "CENTRAL_FINDING_PARAGRAPH",
        "human_judgment":  "HUMAN_JUDGMENT_PARAGRAPH",
        "platform_role":   "PLATFORM_ROLE_PARAGRAPH",
        "evidence":        "EVIDENCE_PARAGRAPH",
        "part_ii_preview": "PART_II_PARAGRAPH",
    }
    blob = build_executive_brief(data, narratives)
    # The bytes carry the docx zip; decode the document.xml to read the
    # rendered headings + body text.
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        doc_xml = zf.read("word/document.xml").decode("utf-8")
    # Every section heading present.
    for heading in (
        "1. The Static Recommendation",
        "2. The Central Finding",
        "3. Analytical Judgment and Methodology Decisions",
        "4. Platform as Evidence Base",
        "5. Evidence Summary",
        "6. Part II Preview",
    ):
        assert heading in doc_xml, f"heading missing: {heading}"
    # Every narrative paragraph wired into its section.
    for token in (
        "STATIC_REC_PARAGRAPH",
        "CENTRAL_FINDING_PARAGRAPH",
        "HUMAN_JUDGMENT_PARAGRAPH",
        "PLATFORM_ROLE_PARAGRAPH",
        "EVIDENCE_PARAGRAPH",
        "PART_II_PARAGRAPH",
    ):
        assert token in doc_xml, f"narrative token missing: {token}"
    # Sections must appear in order.
    idx = [doc_xml.index(h) for h in (
        "1. The Static Recommendation",
        "2. The Central Finding",
        "3. Analytical Judgment and Methodology Decisions",
        "4. Platform as Evidence Base",
        "5. Evidence Summary",
        "6. Part II Preview",
    )]
    assert idx == sorted(idx), "Brief sections rendered out of order."


# ── Brief tone rules are embedded in every section prompt ─────────────────


def test_brief_tone_rules_constant_present():
    """The tone rules constant must name the platform-vs-judgment
    contract — never 'the platform found', always 'our analysis
    shows', platform as DATA source not conclusion source."""
    from main import _BRIEF_TONE_RULES
    assert "Never write 'the platform found'" in _BRIEF_TONE_RULES
    assert "our analysis shows" in _BRIEF_TONE_RULES
    assert "source of DATA, never as the source of conclusions" \
        in _BRIEF_TONE_RULES
    # No em-dashes is a project-wide prose rule.
    assert "No em dashes" in _BRIEF_TONE_RULES


# ── Static Recommendation lead carries the required framing ───────────────


def _runtime_main_source() -> str:
    """Returns the runtime content of backend/main.py — i.e. with
    Python string-literal continuations collapsed. The Edit-formatted
    source breaks a long prompt across many lines like
        "first half " \\
        "second half"
    A raw file read sees `"first half " "second half"` (with the
    intervening close-space-open). At parse time Python concatenates
    them into `first half second half`. Tests that pin AGENT-FACING
    text must look for the concatenated runtime string. This helper
    collapses the `"\\n               "` pattern to recover it."""
    import re
    from pathlib import Path
    raw = (Path(__file__).resolve().parents[1]
           / "backend" / "main.py").read_text(encoding="utf-8")
    # Match a closing quote, optional whitespace, newline, indent
    # whitespace, opening quote — and drop the whole sequence.
    return re.sub(r'"\s*\n\s+"', '', raw)


def test_static_recommendation_spec_carries_required_quote():
    """Section 1 must instruct the agent to use the verbatim
    'For a Forest Capital capital planning mandate' opening — this
    is the Part I answer plainly stated, which the rubric requires
    be surfaced first rather than buried under dynamic framing."""
    source = _runtime_main_source()
    # The Static Recommendation spec quotes the required opening
    # verbatim so the agent leads the brief with the Part I answer.
    assert ("For a Forest Capital capital planning mandate operating "
            "under a long-only, fully-invested constraint") in source
    # And the explicit framing label.
    assert "It is the Part I answer." in source


def test_central_finding_spec_frames_2022_as_interpretation():
    """Section 2 must instruct the agent that the 2022 break is OUR
    interpretation, not a data output."""
    source = _runtime_main_source()
    assert ("Our interpretation of the post-2022 data is that the "
            "equity-bond correlation shift represents a structural "
            "change") in source


def test_human_judgment_spec_names_five_decisions_in_order():
    """Section 3 must list the five human decisions verbatim and in
    delivery order. The rubric trap answer."""
    source = _runtime_main_source()
    # Each of the five decisions is numbered explicitly inside the spec.
    for marker in (
        "1. The regime-inversion interpretation",
        "2. Strategy selection across distinct signal mechanisms",
        "3. Proactive statistical disclosure",
        "4. Structured dissent in the AI council",
        "5. Constraint framework as fiduciary design",
    ):
        assert marker in source, f"Decision missing from spec: {marker}"
    # The closing framing line.
    assert ("The platform gives us the evidence. The interpretation, "
            "the design, and the governance framework are ours.") in source


def test_platform_role_spec_introduces_platform_after_judgment():
    """Section 4 must frame the platform as evidence layer, NOT
    conclusion layer."""
    source = _runtime_main_source()
    assert ("The platform was built to generate auditable, "
            "reproducible evidence for conclusions reached through "
            "analysis.") in source
    assert "The interpretations and design decisions are ours." \
        in source


def test_part_ii_preview_spec_frames_as_logical_consequence():
    """Section 6 must frame the regime-conditional extension as the
    LOGICAL CONSEQUENCE of Part I, not a separate exercise."""
    source = _runtime_main_source()
    assert "logical consequence" in source.lower()
    assert "bootstrap confidence intervals" in source.lower()


def test_brief_specs_apply_tone_rules_to_every_section():
    """Every section spec must thread _BRIEF_TONE_RULES so the agent
    knows the platform-vs-judgment contract throughout."""
    source = _runtime_main_source()
    # The constant is concatenated at the end of every section's
    # `task` string — count must match the number of sections (6).
    occurrences = source.count("+ _BRIEF_TONE_RULES")
    assert occurrences == 6, (
        f"_BRIEF_TONE_RULES is concatenated in {occurrences} section(s); "
        "every section (6) must apply the tone rules.")
