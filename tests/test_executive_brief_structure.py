"""Executive brief — six-section structure (June 6 2026 rewrite).

The midpoint panel feedback was: too academic / no investable
conclusion / 3-of-5 on the division-of-labor rubric / simplify the
strategy set. This rewrite replaces the May 30 structure with the
spec the user pushed through the bridge (#21):

  1. The Answer                — verdict first, no methodology
  2. The Evidence              — 3 strategies, honest FDR + 2-of-9
  3. The Methodology           — background, two paragraphs max
  4. Five Human Decisions      — Bob / Michael / Molly NAMED
  5. The Recommendation        — LIVE regime + asset-class weights
  6. Limitations and Part II   — boundaries + extension preview

The brief addresses the rubric's "where is the human judgment" question
DIRECTLY in Section 4 by naming each team member with their specific
decision. The platform is the evidence layer, not the conclusion layer.
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
        "1. The Answer",
        "2. The Evidence",
        "3. The Methodology",
        "4. Five Human Decisions",
        "5. The Recommendation",
        "6. Limitations and Part II",
    ]


def test_brief_section_keys_match_narrative_contract():
    """The editor adapter and the docx builder must agree on the keys.
    A drift here would silently leave a section showing [DATA PENDING]."""
    from tools.editor_content import _EXEC_BRIEF_SECTIONS
    keys = [key for _h, key, _c in _EXEC_BRIEF_SECTIONS]
    assert keys == [
        "the_answer",
        "the_evidence",
        "methodology",
        "five_human_decisions",
        "the_recommendation",
        "limitations_and_part_ii",
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
        "the_answer":              "THE_ANSWER_PARAGRAPH",
        "the_evidence":            "THE_EVIDENCE_PARAGRAPH",
        "methodology":             "METHODOLOGY_PARAGRAPH",
        "five_human_decisions":    "FIVE_DECISIONS_PARAGRAPH",
        "the_recommendation":      "THE_RECOMMENDATION_PARAGRAPH",
        "limitations_and_part_ii": "LIMITATIONS_PARAGRAPH",
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
        "1. The Answer",
        "2. The Evidence",
        "3. The Methodology",
        "4. Five Human Decisions",
        "5. The Recommendation",
        "6. Limitations and Part II",
    ):
        assert heading in doc_xml, f"heading missing: {heading}"
    # Every narrative paragraph wired into its section.
    for token in (
        "THE_ANSWER_PARAGRAPH",
        "THE_EVIDENCE_PARAGRAPH",
        "METHODOLOGY_PARAGRAPH",
        "FIVE_DECISIONS_PARAGRAPH",
        "THE_RECOMMENDATION_PARAGRAPH",
        "LIMITATIONS_PARAGRAPH",
    ):
        assert token in doc_xml, f"narrative token missing: {token}"
    # Sections must appear in order.
    idx = [doc_xml.index(h) for h in (
        "1. The Answer",
        "2. The Evidence",
        "3. The Methodology",
        "4. Five Human Decisions",
        "5. The Recommendation",
        "6. Limitations and Part II",
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


# ── Section 1: the verdict-first opening ──────────────────────────────────


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


def test_the_answer_spec_leads_with_verdict():
    """Section 1 must instruct the agent to use the verbatim 'Yes. A
    regime-aware diversified blend outperforms...' opening — the
    verdict leads, methodology comes later."""
    source = _runtime_main_source()
    assert ("Yes. A regime-aware diversified blend outperforms a 100% "
            "equity allocation by 27 percentage points of maximum "
            "drawdown") in source
    # And the explicit "no methodology yet" instruction.
    assert "Do NOT discuss the methodology yet" in source


def test_the_evidence_spec_drops_other_strategies():
    """Section 2 must instruct the agent to compare exactly THREE
    strategies (benchmark, best static, dynamic blend) and drop the
    other seven. The locked figures + 2-of-9 + FDR result must be
    cited verbatim."""
    source = _runtime_main_source()
    assert "exactly THREE strategies" in source
    # Locked figures.
    assert "-52.6%" in source
    assert "-25.3%" in source
    assert "0.86" in source
    assert "0.43" in source
    # Honest validation result.
    assert "2 of 9 named market events" in source
    # FDR acknowledgement.
    assert "FDR" in source or "p < 0.005" in source
    # The "drop the other 7" rule.
    assert "DROP the other seven strategies" in source


def test_the_methodology_spec_is_brief():
    """Section 3 must instruct the agent to keep methodology to TWO
    PARAGRAPHS MAXIMUM (background, not the centrepiece)."""
    source = _runtime_main_source()
    assert "TWO PARAGRAPHS MAXIMUM" in source
    assert "background, not the centrepiece" in source


def test_five_human_decisions_spec_names_team_members_explicitly():
    """Section 4 is the critical section addressing the 3/5 division-
    of-labor score. Each of the five decisions must name Bob Thao,
    Michael Ruurds, or Molly Murdock explicitly with the format
    'Decision / Who made it / Why the platform could not make it
    alone.'"""
    source = _runtime_main_source()
    # The five specific decision titles with their attributed person.
    assert "1. Regime hypothesis — Bob Thao" in source
    assert "2. Economic significance threshold — Bob Thao" in source
    assert "3. Out-of-sample window design — Michael Ruurds" in source
    assert "4. Asset scope — Michael Ruurds" in source
    assert "5. Validation framework — Molly Murdock" in source
    # The structural instruction.
    assert ("Decision / Who made it / Why the platform could not make "
            "it alone") in source
    # The "do not anonymise" close.
    assert "do not anonymise" in source.lower() or \
        "name them every time" in source


def test_the_recommendation_spec_uses_live_data():
    """Section 5 must instruct the agent to pull regime + confidence
    + asset-class weights from live_recommendation in context, and
    express the recommendation as ASSET CLASS allocations not
    strategy names."""
    source = _runtime_main_source()
    assert "live_recommendation" in source
    assert "ASSET CLASS ALLOCATIONS" in source
    # The "Forest Capital fills each envelope" framing.
    assert ("Forest Capital fills each envelope with its own security "
            "selection") in source
    # The graceful DATA PENDING fallback when live data is cold.
    assert "[DATA PENDING]" in source or "is not currently available" \
        in source


def test_limitations_and_part_ii_spec_frames_as_logical_consequence():
    """Section 6 must frame the Part II extension as the LOGICAL
    CONSEQUENCE of Part I + cite the bootstrap CI overlap that
    motivates the regime-conditional construction."""
    source = _runtime_main_source()
    assert "LOGICAL CONSEQUENCE" in source
    assert "bootstrap confidence intervals" in source.lower() or \
        "Bootstrap confidence intervals" in source
    # Three-asset scope as PROJECT boundary, not architectural.
    assert "PROJECT boundary" in source or "project boundary" in source.lower()


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


# ── Asset-class aggregator (June 6 2026 — shared with digest section 1) ──


class TestAggregateBlendToAssetClasses:
    """The aggregator that takes per-strategy blend weights and produces
    portfolio-level (equity_pct, bond_pct) shares. Shared between the
    brief's Section 5 and the daily digest's implied-asset-allocation
    section."""

    def test_simple_blend_aggregates_correctly(self):
        from tools.academic_export import aggregate_blend_to_asset_classes
        blend = {"MIN_VARIANCE": 0.4, "RISK_PARITY": 0.6}
        strategies = {
            "MIN_VARIANCE":  {"avg_equity_weight": 0.30,
                              "avg_bond_weight":   0.70},
            "RISK_PARITY":   {"avg_equity_weight": 0.50,
                              "avg_bond_weight":   0.50},
        }
        eq, bd = aggregate_blend_to_asset_classes(blend, strategies)
        assert eq == pytest.approx(0.4 * 0.30 + 0.6 * 0.50, abs=1e-9)
        assert bd == pytest.approx(0.4 * 0.70 + 0.6 * 0.50, abs=1e-9)

    def test_empty_blend_returns_none(self):
        from tools.academic_export import aggregate_blend_to_asset_classes
        assert aggregate_blend_to_asset_classes({}, {"X": {}}) == (None, None)

    def test_empty_strategies_returns_none(self):
        from tools.academic_export import aggregate_blend_to_asset_classes
        assert aggregate_blend_to_asset_classes(
            {"X": 0.5}, {}) == (None, None)

    def test_zero_weights_are_skipped(self):
        from tools.academic_export import aggregate_blend_to_asset_classes
        blend = {"A": 0.5, "B": 0.0, "C": 0.5}
        strategies = {
            "A": {"avg_equity_weight": 0.5, "avg_bond_weight": 0.5},
            "B": {"avg_equity_weight": 0.9, "avg_bond_weight": 0.1},
            "C": {"avg_equity_weight": 0.3, "avg_bond_weight": 0.7},
        }
        eq, bd = aggregate_blend_to_asset_classes(blend, strategies)
        # B is zero-weighted so it contributes nothing.
        assert eq == pytest.approx(0.5 * 0.5 + 0.5 * 0.3, abs=1e-9)
        assert bd == pytest.approx(0.5 * 0.5 + 0.5 * 0.7, abs=1e-9)

    def test_strategy_missing_from_strategies_returns_none_when_no_other(self):
        # When the only blend strategy isn't in strategies, no
        # contribution lands.
        from tools.academic_export import aggregate_blend_to_asset_classes
        assert aggregate_blend_to_asset_classes(
            {"GHOST": 0.5}, {"X": {"avg_equity_weight": 0.5,
                                    "avg_bond_weight": 0.5}}
        ) == (None, None)

    def test_malformed_values_skipped_gracefully(self):
        # NaN / strings / None values in weights don't crash.
        from tools.academic_export import aggregate_blend_to_asset_classes
        blend = {"A": "0.5", "B": None, "C": 0.5}
        strategies = {
            "A": {"avg_equity_weight": "0.5", "avg_bond_weight": 0.5},
            "B": {"avg_equity_weight": 0.5, "avg_bond_weight": 0.5},
            "C": {"avg_equity_weight": 0.5, "avg_bond_weight": 0.5},
        }
        eq, bd = aggregate_blend_to_asset_classes(blend, strategies)
        # "0.5" coerces to 0.5; None weights skip B.
        assert eq is not None
        assert bd is not None
