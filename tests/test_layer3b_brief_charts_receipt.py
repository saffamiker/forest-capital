"""Layer 3b -- chart embedding + verification receipt in the brief.

Pins the behaviour of build_executive_brief's Layer-3b extensions:

  * Section 6 embeds at least four inline picture shapes (Figure 1..4).
  * The APA Note. captions resolve {{TOKEN}} placeholders against the
    substitution_table passed by the caller.
  * A renderer failure inserts a placeholder paragraph and does NOT
    crash brief generation.
  * The verification receipt page is rendered at the end of the brief.

Renderers are monkeypatched to return a stub PNG so the test stays
fast and deterministic across cold-cache test environments.
"""
from __future__ import annotations

import io
import os
import sys
import zipfile

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault(
    "SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,"
    "murdockm@queens.edu,panttserk@queens.edu")


# Minimal valid 1x1 PNG (89 bytes) -- enough for python-docx to embed.
_STUB_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00"
    b"\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx"
    b"\x9cc\xfc\xcf\xc0P\x0f\x00\x05\x01\x01\x02\xcd\xe2t\x83\x00\x00"
    b"\x00\x00IEND\xaeB`\x82")


def _stub_data() -> dict:
    return {
        "study_period": {"start": "2002-07", "end": "2025-12",
                         "n_months": 282, "ff_factors_end": "2025-12"},
        "regime_conditional": [],
        "summary_statistics": [],
        "drawdown_comparison": [],
        "factor_loadings": [],
        "audit_disclosures": None,
        "rolling_correlation": {},
        "cumulative_returns": {},
        "blend_weights": {},
    }


def _narratives() -> dict[str, str]:
    return {
        "executive_summary":     "EXECUTIVE_SUMMARY_PARAGRAPH",
        "methodology":           "METHODOLOGY_PARAGRAPH",
        "key_findings":          "KEY_FINDINGS_PARAGRAPH",
        "limitations":           "LIMITATIONS_PARAGRAPH",
        "final_recommendations": "FINAL_RECOMMENDATIONS_PARAGRAPH",
        "visuals":               "VISUALS_PARAGRAPH",
    }


def _substitution_table() -> dict[str, str]:
    return {
        "{{DATA_HASH}}":              "abc12345",
        "{{PRE_2022_EQ_IG_CORR}}":    "-0.05",
        "{{POST_2022_EQ_IG_CORR}}":   "+0.57",
        "{{N_STRATEGIES}}":           "10",
        "{{OOS_SHARPE_BLEND}}":       "0.86",
        "{{OOS_SHARPE_BENCHMARK}}":   "0.43",
        "{{OOS_WINDOW}}":             "January 2022 through May 2026",
        "{{STUDY_MONTHS}}":           "282",
        "{{STUDY_START}}":            "July 2002",
        "{{STUDY_END}}":              "May 2026",
    }


def _patch_renderers_to_stub(monkeypatch):
    """Force every chart renderer used by Section 6 to return a stub
    PNG so the test does not depend on warm caches."""
    from tools import chart_render
    monkeypatch.setattr(
        chart_render, "render_cumulative_returns",
        lambda *args, **kwargs: _STUB_PNG)
    monkeypatch.setattr(
        chart_render, "render_rolling_correlation",
        lambda *args, **kwargs: _STUB_PNG)
    monkeypatch.setattr(
        chart_render, "render_efficient_frontier",
        lambda *args, **kwargs: _STUB_PNG)
    monkeypatch.setattr(
        chart_render, "render_strategy_comparison",
        lambda *args, **kwargs: _STUB_PNG)


def _doc_xml(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return zf.read("word/document.xml").decode("utf-8")


def _image_count(blob: bytes) -> int:
    """Count inline pictures by counting <w:drawing> elements in the
    document body. python-docx de-duplicates identical bytes into a
    single media/ entry but each insertion still emits its own
    <w:drawing> reference; the drawing count is the right inline-shape
    metric."""
    xml = _doc_xml(blob)
    return xml.count("<w:drawing>")


# ── Part B: chart embedding ────────────────────────────────────────────────


def test_brief_embeds_four_figures(monkeypatch):
    """Section 6 emits four inline picture shapes (Figure 1..4)."""
    _patch_renderers_to_stub(monkeypatch)
    from tools.academic_docx import build_executive_brief
    blob = build_executive_brief(
        _stub_data(), _narratives(), substitution_table=_substitution_table())
    assert _image_count(blob) >= 4, (
        "Section 6 should embed at least 4 figures")


def test_brief_renders_figure_labels(monkeypatch):
    """The four 'Figure N' bold labels appear in the body."""
    _patch_renderers_to_stub(monkeypatch)
    from tools.academic_docx import build_executive_brief
    blob = build_executive_brief(
        _stub_data(), _narratives(), substitution_table=_substitution_table())
    xml = _doc_xml(blob)
    for n in (1, 2, 3, 4):
        assert f"Figure {n}" in xml, f"Figure {n} label missing"


def test_brief_apa_notes_resolve_tokens(monkeypatch):
    """APA Note. text contains the substituted values, not the
    raw {{TOKEN}} placeholders, when a substitution_table is supplied."""
    _patch_renderers_to_stub(monkeypatch)
    from tools.academic_docx import build_executive_brief
    blob = build_executive_brief(
        _stub_data(), _narratives(), substitution_table=_substitution_table())
    xml = _doc_xml(blob)
    # The pre-2022 correlation value should land in the body.
    assert "-0.05" in xml, "pre-2022 correlation value missing from notes"
    # OOS Sharpe figures from the substitution table.
    assert "0.86" in xml, "OOS Sharpe blend value missing"
    assert "0.43" in xml, "OOS Sharpe benchmark value missing"
    # And the raw token markers must NOT appear in the body once
    # substitution has run.
    assert "{{PRE_2022_EQ_IG_CORR}}" not in xml, (
        "raw {{PRE_2022_EQ_IG_CORR}} token leaked into the body")
    assert "{{OOS_SHARPE_BLEND}}" not in xml, (
        "raw {{OOS_SHARPE_BLEND}} token leaked into the body")


def test_brief_chart_failure_renders_placeholder(monkeypatch):
    """A renderer that returns None inserts a placeholder paragraph
    and does NOT crash brief generation."""
    from tools import chart_render

    def _none(*a, **k):
        return None

    monkeypatch.setattr(chart_render, "render_cumulative_returns", _none)
    monkeypatch.setattr(chart_render, "render_rolling_correlation", _none)
    monkeypatch.setattr(chart_render, "render_efficient_frontier", _none)
    monkeypatch.setattr(chart_render, "render_strategy_comparison", _none)
    from tools.academic_docx import build_executive_brief
    blob = build_executive_brief(
        _stub_data(), _narratives(), substitution_table=_substitution_table())
    # Brief still generates (no crash) and the placeholder copy is
    # present for every figure.
    xml = _doc_xml(blob)
    for n in (1, 2, 3, 4):
        assert (f"[Figure {n}: chart unavailable -- regenerate to embed "
                "this visual]") in xml, f"Placeholder for figure {n} missing"


def test_brief_chart_renderer_raises_renders_placeholder(monkeypatch):
    """A renderer that RAISES is caught and a placeholder is inserted."""
    from tools import chart_render

    def _boom(*a, **k):
        raise RuntimeError("simulated chart failure")

    monkeypatch.setattr(chart_render, "render_cumulative_returns", _boom)
    monkeypatch.setattr(chart_render, "render_rolling_correlation", _boom)
    monkeypatch.setattr(chart_render, "render_efficient_frontier", _boom)
    monkeypatch.setattr(chart_render, "render_strategy_comparison", _boom)
    from tools.academic_docx import build_executive_brief
    blob = build_executive_brief(
        _stub_data(), _narratives(), substitution_table=_substitution_table())
    assert b"PK" == blob[:2], "Brief did not produce a valid .docx zip"
    xml = _doc_xml(blob)
    assert "[Figure 1: chart unavailable" in xml


# ── Part A4: verification receipt ──────────────────────────────────────────


def test_brief_renders_verification_receipt(monkeypatch):
    """The receipt page (DATA VERIFICATION RECEIPT title +
    Document/Generated/Exported lines) appears at the end of the brief."""
    _patch_renderers_to_stub(monkeypatch)
    from tools.academic_docx import build_executive_brief
    blob = build_executive_brief(
        _stub_data(), _narratives(),
        substitution_table=_substitution_table())
    xml = _doc_xml(blob)
    assert "DATA VERIFICATION RECEIPT" in xml
    assert "Document:" in xml and "Executive Brief" in xml
    assert "Generated:" in xml
    assert "Exported:" in xml
    assert "Data hash:" in xml
    # Receipt uses the substituted DATA_HASH value, not the raw token.
    assert "abc12345" in xml
    # Reviewer note line.
    assert (
        "Every numeric figure in this document was sourced from the "
        "platform analytics cache and confirmed at export time."
    ) in xml


def test_brief_receipt_reports_verified_state(monkeypatch):
    """A passing verification_result renders Verification: Passed."""
    _patch_renderers_to_stub(monkeypatch)
    from tools.academic_docx import build_executive_brief
    result = {
        "passed": True, "warnings": [], "errors": [],
        "data_hash_match": True, "n_values_verified": 47,
        "n_values_missing": 0, "verified_at": "2026-06-21T12:00:00Z",
        "document_type": "executive_brief",
    }
    blob = build_executive_brief(
        _stub_data(), _narratives(),
        substitution_table=_substitution_table(),
        verification_result=result)
    xml = _doc_xml(blob)
    assert "Verification: Passed" in xml
    assert "Values verified: 47 of 47" in xml


def test_brief_receipt_reports_failed_state(monkeypatch):
    """An errors-present verification_result renders Verification: Failed."""
    _patch_renderers_to_stub(monkeypatch)
    from tools.academic_docx import build_executive_brief
    result = {
        "passed": False, "warnings": [],
        "errors": [{"message": "value missing"}],
        "data_hash_match": True, "n_values_verified": 40,
        "n_values_missing": 7, "verified_at": "2026-06-21T12:00:00Z",
        "document_type": "executive_brief",
    }
    blob = build_executive_brief(
        _stub_data(), _narratives(),
        substitution_table=_substitution_table(),
        verification_result=result)
    xml = _doc_xml(blob)
    assert "Verification: Failed" in xml
    assert "Values verified: 40 of 47" in xml


def test_brief_receipt_renders_with_no_verification_result(monkeypatch):
    """At generation time verification_result is None; the receipt
    still renders with a neutral 'Not yet verified' line."""
    _patch_renderers_to_stub(monkeypatch)
    from tools.academic_docx import build_executive_brief
    blob = build_executive_brief(
        _stub_data(), _narratives(),
        substitution_table=_substitution_table(),
        verification_result=None)
    xml = _doc_xml(blob)
    assert "Verification: Not yet verified" in xml


def test_brief_backward_compatible_signature(monkeypatch):
    """The Layer 3a-and-earlier two-arg call still works -- the new
    substitution_table + verification_result kwargs default to None,
    so a caller that hasn't been migrated yet still produces a valid
    brief (with unresolved {{TOKEN}} markers in the figure notes that
    the audit will flag)."""
    _patch_renderers_to_stub(monkeypatch)
    from tools.academic_docx import build_executive_brief
    blob = build_executive_brief(_stub_data(), _narratives())
    assert b"PK" == blob[:2]
    xml = _doc_xml(blob)
    # Receipt still renders (no substitution_table -> raw tokens
    # remain in the receipt fields).
    assert "DATA VERIFICATION RECEIPT" in xml
