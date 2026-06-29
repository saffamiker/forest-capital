"""Executive brief -- six-section structure (June 18 2026 rubric rewrite).

The previous structure (June 6 2026) led with "The Answer", embedded
a "Five Human Decisions" section + a "Part II preview", and ordered
the sections to address the midpoint feedback. Rubric review against
the FNA 670 spec found:

  * "Five Human Decisions" is a project-process section (division-of-
    labor disclosure) -- non-rubric content.
  * "Part II preview" is "next steps / future work" content the rubric
    explicitly excludes from the brief.
  * Section ordering had Methodology at §3 instead of the rubric's §2.
  * §5 framed the recommendation as a point-in-time portfolio position
    rather than investment conclusions drawn from the analysis.

This file pins the rubric-aligned structure:

  1. Executive Summary       -- verdict + headline figures
  2. Methodology Overview    -- HMM + OOS window + validation layers
  3. Key Findings            -- three-strategy comparison + 2-of-9
  4. Limitations and Risks   -- four mandatory limitations, no Part II
  5. Final Recommendations   -- investment conclusions from the OOS
     evidence; cached-regime fallback so the section never renders
     [DATA PENDING] under a degraded live build
  6. Visuals to Demonstrate the Insights -- captioned roster of the
     platform's chart surfaces

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
        "1. Executive Summary",
        "2. Methodology Overview",
        "3. Key Findings and Insights",
        "4. Limitations and Risks",
        "5. Final Recommendations",
        "6. Visuals to Demonstrate the Insights",
    ]


def test_brief_section_keys_match_narrative_contract():
    """The editor adapter and the docx builder must agree on the keys.
    A drift here would silently leave a section showing [DATA PENDING]."""
    from tools.editor_content import _EXEC_BRIEF_SECTIONS
    keys = [key for _h, key, _c in _EXEC_BRIEF_SECTIONS]
    assert keys == [
        "executive_summary",
        "methodology",
        "key_findings",
        "limitations",
        "final_recommendations",
        "visuals",
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
        "executive_summary":     "EXECUTIVE_SUMMARY_PARAGRAPH",
        "methodology":           "METHODOLOGY_PARAGRAPH",
        "key_findings":          "KEY_FINDINGS_PARAGRAPH",
        "limitations":           "LIMITATIONS_PARAGRAPH",
        "final_recommendations": "FINAL_RECOMMENDATIONS_PARAGRAPH",
        "visuals":               "VISUALS_PARAGRAPH",
    }
    blob = build_executive_brief(data, narratives)
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        doc_xml = zf.read("word/document.xml").decode("utf-8")
    # Every section heading present.
    for heading in (
        "1. Executive Summary",
        "2. Methodology Overview",
        "3. Key Findings and Insights",
        "4. Limitations and Risks",
        "5. Final Recommendations",
        "6. Visuals to Demonstrate the Insights",
    ):
        assert heading in doc_xml, f"heading missing: {heading}"
    # Every narrative paragraph wired into its section.
    for token in (
        "EXECUTIVE_SUMMARY_PARAGRAPH",
        "METHODOLOGY_PARAGRAPH",
        "KEY_FINDINGS_PARAGRAPH",
        "LIMITATIONS_PARAGRAPH",
        "FINAL_RECOMMENDATIONS_PARAGRAPH",
        "VISUALS_PARAGRAPH",
    ):
        assert token in doc_xml, f"narrative token missing: {token}"
    # Sections must appear in rubric order.
    idx = [doc_xml.index(h) for h in (
        "1. Executive Summary",
        "2. Methodology Overview",
        "3. Key Findings and Insights",
        "4. Limitations and Risks",
        "5. Final Recommendations",
        "6. Visuals to Demonstrate the Insights",
    )]
    assert idx == sorted(idx), "Brief sections rendered out of order."


# ── Brief tone rules are embedded in every section prompt ─────────────────


def test_brief_tone_rules_constant_present():
    """The tone rules constant must name the platform-vs-judgment
    contract -- never 'the platform found', always 'our analysis
    shows', platform as DATA source not conclusion source."""
    from main import _BRIEF_TONE_RULES
    assert "Never write 'the platform found'" in _BRIEF_TONE_RULES
    assert "our analysis shows" in _BRIEF_TONE_RULES
    assert "source of DATA, never as the source of conclusions" \
        in _BRIEF_TONE_RULES


# ── Section content pins (the rubric-aligned prompts) ────────────────────


def _runtime_main_source() -> str:
    """Returns the runtime content of backend/main.py -- i.e. with
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
    return re.sub(r'"\s*\n\s+"', '', raw)


def test_executive_summary_leads_with_verdict():
    """§1 must open with the verdict sentence + headline figures.

    June 22 2026 (PR #370) -- the literal numbers in the prompt
    text were tokenized to {{OOS_SHARPE_BLEND}} /
    {{OOS_SHARPE_BENCHMARK}} / {{REGIME_SWITCHING_MAX_DD}} /
    {{BENCHMARK_MAX_DD}}. The substitution table resolves them
    against the locked Path A values at generation time. The
    test contract here is the SHAPE of the section's headline,
    not the literal numbers -- pin the tokens instead so a
    future drift back to hardcoded numbers gets flagged."""
    source = _runtime_main_source()
    assert ("A regime-conditional diversified blend outperforms a 100% "
            "equity allocation on a risk-adjusted basis") in source
    # The OOS Sharpe headline tokens belong at the top.
    assert ("{{OOS_SHARPE_BLEND}}" in source
            and "{{OOS_SHARPE_BENCHMARK}}" in source)
    # The drawdown headline tokens belong at the top.
    assert ("{{REGIME_SWITCHING_MAX_DD}}" in source
            and "{{BENCHMARK_MAX_DD}}" in source)


def test_methodology_overview_is_brief():
    """§2 must keep methodology to TWO PARAGRAPHS MAXIMUM (background,
    not the centrepiece)."""
    source = _runtime_main_source()
    assert "TWO PARAGRAPHS MAXIMUM" in source
    assert "Brevity is the contract" in source


def test_key_findings_section_drops_other_strategies():
    """§3 (Key Findings) must instruct the agent to compare exactly
    THREE strategies (benchmark, best static, dynamic blend) and the
    other strategies must NOT be named. Locked figures + 2-of-9 +
    FDR cited verbatim.

    June 29 2026 -- "DROP the other seven strategies" replaced by
    the three-strategy-submission-scope framing (the brief +
    appendix + deck + script all operate on the same restricted set
    now; "other seven" is no longer the framing because the platform
    no longer exposes them to document generation at all). The brief
    Section 3 prompt now references "three submission strategies"
    instead."""
    source = _runtime_main_source()
    assert "exactly THREE strategies" in source
    assert "2 of 9 named market events" in source
    assert "FDR" in source or "p < 0.005" in source
    # Post-three-strategy-scope framing: the prompt names the
    # three submission strategies + omits the "drop the other
    # seven" instruction (the central filter removes them
    # upstream so the prompt doesn't need to defend against
    # them appearing).
    assert "three submission strategies" in source


def test_key_findings_references_platform_visuals_by_name():
    """§3 must instruct the agent to reference the platform's visuals
    by name (the cumulative-return chart, the implied-allocation chart)
    when stating the headline findings -- rubric §6 wants visuals
    integrated with insights."""
    source = _runtime_main_source()
    assert "cumulative return chart" in source.lower()
    assert "implied asset allocation over time chart" in source.lower()


def test_limitations_drops_part_ii_preview():
    """§4 must NOT contain Part II / future work / next steps content.
    The rubric grades the brief on investment conclusions; future work
    is explicitly outside scope."""
    source = _runtime_main_source()
    # The new §4 spec must explicitly forbid the Part II content.
    assert "Do NOT add a 'next steps', 'future work', or 'Part II'" in source


def test_limitations_carries_four_mandatory_limitations():
    """§4 must name the four mandatory limitations (three-asset scope,
    sample size, transaction costs, statistical significance)."""
    source = _runtime_main_source()
    assert "THREE-ASSET SCOPE" in source
    assert "SAMPLE SIZE" in source
    assert "TRANSACTION COSTS" in source
    assert "STATISTICAL SIGNIFICANCE" in source


def test_final_recommendations_framed_as_investment_conclusions():
    """§5 must frame the recommendation as INVESTMENT CONCLUSIONS
    drawn from the analysis -- NOT a point-in-time portfolio
    position. The rubric distinguishes the two and grades the brief
    on the former."""
    source = _runtime_main_source()
    assert "INVESTMENT CONCLUSIONS" in source
    # The headline conclusion sentence shape is pinned -- the verbal
    # frame "we recommend that ... be considered as a core approach"
    # is the rubric-correct framing.
    assert ("we recommend that a regime-conditional "
            "allocation framework be considered as a core") in source
    # The explicit "NOT next steps / NOT future research" guard.
    assert "NOT next steps" in source
    assert "NOT future research" in source


def test_final_recommendations_uses_cached_regime_fallback_disclosure():
    """When the live regime is stale, §5 must disclose this explicitly
    via a fixed disclosure sentence -- so the audience knows whether
    the recommendation references the live read or the most recent
    cached read."""
    source = _runtime_main_source()
    assert "live_recommendation.is_stale" in source
    assert "The live regime read at generation time was unavailable" \
        in source
    assert "most recent cached regime read" in source


def test_final_recommendations_requires_locked_vs_live_framing():
    """June 22 2026 -- §5 must instruct the writer to include a
    framing sentence BEFORE the regime reading paragraph that
    distinguishes the locked academic record (Sharpe / drawdown /
    OOS figures cited above) from the live regime read + implied
    weights that follow. Without this sentence the panel reads
    the recommendation as a single state when it's actually two
    layered states. The framing is verbatim-or-close-equivalent,
    not strict literal."""
    source = _runtime_main_source()
    # The directive is present.
    assert "BEFORE the regime reading" in source
    # Key phrases from the required framing sentence.
    assert ("analytical performance figures throughout this "
            "brief reflect the December 2025 academic submission "
            "record") in source
    assert ("live platform readings at the time of generation"
            in source)
    # The rationale -- two layered states -- is in the spec so
    # the writer understands WHY the sentence matters and won't
    # paraphrase it away in a rewrite.
    assert "two different states" in source


def test_key_findings_warns_against_doubling_percent_on_improvement_pct():
    """June 22 2026 -- §3 caught a production bug rendering
    '+98%%' (doubled percent sign) when the writer cited
    {{OOS_SHARPE_IMPROVEMENT_PCT}} + a literal '%'. The token
    already emits the full '+98%' string (see
    numeric_substitution.py:310: f'{sign}{abs(improvement_pct):.0f}%').
    The §3 prompt must explicitly warn the writer not to add
    any suffix or prefix to this token. The user-provided
    verbatim instruction is pinned here in full."""
    source = _runtime_main_source()
    # Verbatim instruction text the operator specified.
    assert ("IMPORTANT: The token {{OOS_SHARPE_IMPROVEMENT_PCT}} "
            "already resolves to a complete formatted string "
            "including the + prefix and % suffix") in source
    assert ("Do NOT append any additional % character or suffix "
            "after this token") in source
    assert "Write it exactly as the token resolves." in source


def test_section_g_appendix_prohibits_placeholder_and_open_items():
    """June 22 2026 -- Section G appendix prompt was producing
    a 'must be inserted before final submission' placeholder
    note + a 'five open items' list alongside the actual
    transaction cost sensitivity table. The table is generated
    programmatically and always present; no placeholder is
    appropriate.

    No explicit instruction in the prompt asks for the
    placeholder -- the Sonnet writer hallucinates it. The fix
    is a NEGATIVE instruction (explicit prohibition) added to
    the prompt."""
    source = _runtime_main_source()
    # The PROHIBITED CONTENT block is present in the Section G
    # prompt.
    assert "PROHIBITED CONTENT" in source
    # The specific phrases the writer was hallucinating must
    # be named explicitly so the LLM doesn't paraphrase them
    # back in.
    assert "must be inserted here" in source.lower() or (
        "'must be inserted here'" in source)
    assert "open-items list" in source
    assert "five open items" in source.lower()
    assert "PROGRAMMATICALLY" in source
    assert "ALWAYS present" in source


def test_visuals_section_is_not_emitted():
    """June 26 2026 -- Section 6 ('Visuals to Demonstrate the
    Insights') was removed as a generation artifact with no
    submission value. The brief now ends after Section 5: Final
    Recommendations. This test pins the removal so a future
    revert doesn't reintroduce the captioned-chart prose.

    The four visual artifact names previously rostered in §6
    are still referenced inline from Sections 3 and 4 (the
    cumulative-return chart, the implied-asset-allocation chart,
    the efficient frontier, the rolling correlation chart); the
    Analytical Appendix carries the per-chart data tables. The
    redundant captioned roster does not need to be reproduced
    in the brief itself."""
    source = _runtime_main_source()
    # The spec-list entry that drove Section 6 must be gone.
    # 'key": "visuals"' is the canonical fingerprint of the spec.
    assert '"key": "visuals"' not in source
    # The Section 6 task verbatim header must not appear.
    assert "Write Section 6: VISUALS TO DEMONSTRATE THE INSIGHTS" \
        not in source


def test_brief_specs_apply_tone_rules_to_every_section():
    """Every section spec must thread _BRIEF_TONE_RULES so the agent
    knows the platform-vs-judgment contract throughout.

    June 26 2026 -- expected count dropped from 6 to 5 after the
    removal of Section 6 (Visuals to Demonstrate the Insights);
    the brief now carries five rubric-aligned sections."""
    source = _runtime_main_source()
    occurrences = source.count("+ _BRIEF_TONE_RULES")
    assert occurrences == 5, (
        f"_BRIEF_TONE_RULES is concatenated in {occurrences} section(s); "
        "every section (5) must apply the tone rules.")


def test_brief_does_not_carry_five_human_decisions_section():
    """The Five Human Decisions section was a non-rubric project
    artifact (division-of-labor disclosure). The rubric-aligned brief
    drops it entirely; this test pins the removal so a future revert
    doesn't reintroduce non-rubric content."""
    source = _runtime_main_source()
    # The section header / agent_id / spec key must all be absent.
    assert "five_human_decisions" not in source
    assert "FIVE HUMAN DECISIONS" not in source


def test_brief_does_not_carry_part_ii_preview():
    """The Part II preview content was "next steps / future work" --
    explicitly excluded by the rubric. This test pins the removal."""
    source = _runtime_main_source()
    # The previous spec's distinctive Part II markers must be gone.
    assert "limitations_and_part_ii" not in source
    assert "LIMITATIONS AND PART II" not in source
    assert "PART II preview" not in source


# ── Asset-class aggregator (shared with digest section 1) ────────────────


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
        assert eq == pytest.approx(0.5 * 0.5 + 0.5 * 0.3, abs=1e-9)
        assert bd == pytest.approx(0.5 * 0.5 + 0.5 * 0.7, abs=1e-9)

    def test_strategy_missing_from_strategies_returns_none_when_no_other(self):
        from tools.academic_export import aggregate_blend_to_asset_classes
        assert aggregate_blend_to_asset_classes(
            {"GHOST": 0.5}, {"X": {"avg_equity_weight": 0.5,
                                    "avg_bond_weight": 0.5}}
        ) == (None, None)

    def test_malformed_values_skipped_gracefully(self):
        from tools.academic_export import aggregate_blend_to_asset_classes
        blend = {"A": "0.5", "B": None, "C": 0.5}
        strategies = {
            "A": {"avg_equity_weight": "0.5", "avg_bond_weight": 0.5},
            "B": {"avg_equity_weight": 0.5, "avg_bond_weight": 0.5},
            "C": {"avg_equity_weight": 0.5, "avg_bond_weight": 0.5},
        }
        eq, bd = aggregate_blend_to_asset_classes(blend, strategies)
        assert eq is not None
        assert bd is not None
