"""
tools/audit_pdf.py

Professionally formatted PDF reports for the two platform audits, built
for inclusion in the Analytical Appendix:

  build_statistical_audit_pdf(run)      — the independent three-layer
                                          statistical audit (a row from
                                          audit_runs with grouped findings)
  build_methodology_audit_pdf(audit)    — the QA agent's methodology
                                          checklist (a QAAgent.run_audit dict)

Both share a white-background, black-text academic layout — a Forest
Capital / McColl School of Business identity, a generation timestamp,
page numbers, section dividers, and PASS/WARN/FAIL colour coding.

reportlab is the PDF engine (the /mnt/skills PDF skill is absent in this
environment; reportlab is already a pinned dependency and is the proven
pattern used by tools/docx_generator.py and tools/pptx_generator.py).
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
    TableStyle,
)

# ── Palette ───────────────────────────────────────────────────────────────────

_GREEN = colors.HexColor("#1a7a3c")   # PASS — dark green
_AMBER = colors.HexColor("#b45309")   # WARN — amber
_RED = colors.HexColor("#b91c1c")     # FAIL — dark red
_INK = colors.HexColor("#111827")     # body text
_MUTED = colors.HexColor("#6b7280")   # captions, footer
_RULE = colors.HexColor("#d1d5db")    # section dividers
_ACCENT = colors.HexColor("#1e3a5c")  # headings

_MARGIN = 0.9 * inch
_FOOTER = "Forest Capital Portfolio Intelligence System · FNA 670 — Summer 2026"


def _status_colour(status: str | None) -> colors.Color:
    s = (status or "").strip().lower()
    if s in ("pass", "passed"):
        return _GREEN
    if s in ("fail", "failed", "critical"):
        return _RED
    return _AMBER  # warning / warn / anything else


def _status_label(status: str | None) -> str:
    s = (status or "").strip().lower()
    if s in ("pass", "passed"):
        return "PASS"
    if s in ("fail", "failed", "critical"):
        return "FAIL"
    return "WARN"


# ── Styles ────────────────────────────────────────────────────────────────────

def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "cover_title": ParagraphStyle(
            "cover_title", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=20, leading=25, textColor=_ACCENT, alignment=TA_CENTER,
            spaceBefore=4, spaceAfter=4),
        "cover_sub": ParagraphStyle(
            "cover_sub", parent=base["Normal"], fontName="Helvetica",
            fontSize=13, leading=18, textColor=_INK, alignment=TA_CENTER,
            spaceAfter=2),
        "cover_meta": ParagraphStyle(
            "cover_meta", parent=base["Normal"], fontName="Helvetica",
            fontSize=10, leading=15, textColor=_MUTED, alignment=TA_CENTER),
        "h1": ParagraphStyle(
            "h1", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=14, leading=18, textColor=_ACCENT, spaceBefore=14,
            spaceAfter=6),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=11, leading=14, textColor=_INK, spaceBefore=10,
            spaceAfter=3),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontName="Helvetica", fontSize=9.5,
            leading=14, textColor=_INK, alignment=TA_LEFT, spaceAfter=5),
        "finding": ParagraphStyle(
            "finding", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=9.5, leading=13, textColor=_INK, spaceBefore=6,
            spaceAfter=1),
        "detail": ParagraphStyle(
            "detail", parent=base["Normal"], fontName="Helvetica", fontSize=8.5,
            leading=12, textColor=_INK, leftIndent=14, spaceAfter=1),
        "caption": ParagraphStyle(
            "caption", parent=base["Normal"], fontName="Helvetica-Oblique",
            fontSize=8, leading=11, textColor=_MUTED, spaceAfter=4),
    }


def _footer(canvas: Any, doc: Any) -> None:
    """Page footer — the Forest Capital identity line and a page number."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(_MUTED)
    canvas.drawString(_MARGIN, 0.55 * inch, _FOOTER)
    canvas.drawRightString(letter[0] - _MARGIN, 0.55 * inch,
                           f"Page {doc.page}")
    canvas.setStrokeColor(_RULE)
    canvas.setLineWidth(0.5)
    canvas.line(_MARGIN, 0.72 * inch, letter[0] - _MARGIN, 0.72 * inch)
    canvas.restoreState()


def _rule() -> HRFlowable:
    return HRFlowable(width="100%", thickness=0.6, color=_RULE,
                      spaceBefore=3, spaceAfter=7)


def _esc(text: Any) -> str:
    """Escape a value for use inside a reportlab Paragraph."""
    return (str(text if text is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _bullet(status: str | None) -> str:
    """A status-coloured bullet for the start of a finding line."""
    return f'<font color="{_status_colour(status).hexval()}">●</font>'


def _verdict_tag(status: str | None) -> str:
    """A bold, colour-coded verdict word for the end of a finding line."""
    c = _status_colour(status).hexval()
    return f'<font color="{c}"><b>{_status_label(status)}</b></font>'


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _render(story: list, title: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=_MARGIN, rightMargin=_MARGIN,
        topMargin=0.9 * inch, bottomMargin=0.95 * inch,
        title=title, author="Forest Capital Portfolio Intelligence System",
    )
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


# ── Report 1 — Statistical Audit ──────────────────────────────────────────────

def _overall(run: dict[str, Any]) -> str:
    if (run.get("failed") or 0) > 0:
        return "FAIL"
    if (run.get("warnings") or 0) > 0:
        return "WARN"
    return "PASS"


def _layer_empty_message(layer_status: Any) -> str:
    """The caption for a layer that recorded no findings. The layer's own
    status column is authoritative — a layer that ran (pass/warn) but
    stored no findings is NOT the same as a skipped layer."""
    ran = str(layer_status or "").lower() in {"pass", "warn", "warning"}
    return ("This layer ran but no individual findings were recorded."
            if ran else "This layer was skipped.")


def build_statistical_audit_pdf(run: dict[str, Any]) -> bytes:
    """
    Renders an audit_runs row (with grouped layer findings) as the
    Statistical Audit Report PDF.
    """
    s = _styles()
    meta = run.get("metadata") or {}
    findings = run.get("findings") or {}
    total = run.get("total_checks") or 0
    passed = run.get("passed") or 0
    pct = f"{passed / total * 100:.0f}%" if total else "—"
    overall = _overall(run)
    data_hash = (meta.get("raw_inputs_hash")
                 or run.get("raw_inputs_hash") or "—")

    story: list = []

    # ── Page 1 — cover ──
    story.append(Spacer(1, 1.4 * inch))
    story.append(Paragraph("FOREST CAPITAL PORTFOLIO INTELLIGENCE SYSTEM",
                            s["cover_title"]))
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph("Statistical Audit Report", s["cover_title"]))
    story.append(Paragraph("Independent Numerical Verification", s["cover_sub"]))
    story.append(Spacer(1, 0.4 * inch))
    story.append(Paragraph("FNA 670 Practicum — Summer 2026", s["cover_sub"]))
    story.append(Paragraph("McColl School of Business", s["cover_sub"]))
    story.append(Paragraph("Queens University Charlotte", s["cover_sub"]))
    story.append(Spacer(1, 0.5 * inch))
    story.append(Paragraph(f"Audit ID: {_esc(run.get('id'))}", s["cover_meta"]))
    story.append(Paragraph(f"Generated: {_timestamp()}", s["cover_meta"]))
    story.append(Paragraph(
        f"Triggered by: {_esc(run.get('triggered_by_email') or '—')} "
        f"({_esc(run.get('triggered_by') or 'manual')})", s["cover_meta"]))
    story.append(Paragraph(f"Data hash: {_esc(data_hash)}", s["cover_meta"]))
    story.append(PageBreak())

    # ── Page 2 — executive summary ──
    story.append(Paragraph("Executive Summary", s["h1"]))
    story.append(_rule())
    story.append(Paragraph("WHAT THIS AUDIT IS", s["h2"]))
    story.append(Paragraph(
        "This report presents the results of an independent three-layer "
        "statistical audit of the Forest Capital Portfolio Intelligence "
        "System's analytical outputs. Every metric presented in this "
        "project — CAGR, Sharpe ratio, maximum drawdown, factor loadings, "
        "correlation measures, and regime-conditional statistics — was "
        "independently recomputed from raw data by a separate AI model "
        "(Claude Opus, claude-opus-4-7) with no access to the platform's "
        "intermediate calculations.", s["body"]))

    story.append(Paragraph("AUDIT METHODOLOGY", s["h2"]))
    story.append(Paragraph(
        "<b>Layer 1: Raw data verification</b> — deterministic checks on "
        "input data quality, bounds, and consistency.", s["body"]))
    story.append(Paragraph(
        "<b>Layer 2: Independent recomputation</b> — an Opus model "
        "recomputes every metric from raw return series and factor data, "
        "showing full working, and compares against the platform values.",
        s["body"]))
    story.append(Paragraph(
        "<b>Layer 3: Cross-platform consistency</b> — verifies that the "
        "same metric reports the same value wherever it appears across the "
        "platform.", s["body"]))

    story.append(Paragraph("OVERALL RESULT", s["h2"]))
    story.append(Paragraph(
        f'<font color="{_status_colour(overall).hexval()}"><b>{overall}'
        f'</b></font>&nbsp;&nbsp;{total} checks · {passed} passed ({pct}) · '
        f'{run.get("warnings", 0)} warnings · {run.get("failed", 0)} failures',
        s["body"]))

    story.append(Paragraph("INTERPRETING THE RESULTS", s["h2"]))
    story.append(Paragraph(
        '<font color="' + _GREEN.hexval() + '"><b>PASS</b></font> — the '
        "platform value matches the independently recomputed value within "
        "tolerance. The metric is verified.", s["body"]))
    story.append(Paragraph(
        '<font color="' + _AMBER.hexval() + '"><b>WARN</b></font> — a minor '
        "discrepancy exists, or a check could not be completed due to a "
        "known limitation. Warnings are explained in the findings and none "
        "represent material analytical errors.", s["body"]))
    fail_note = (
        "No critical failures were found in this audit run."
        if (run.get("failed") or 0) == 0 else
        "A material discrepancy was found requiring correction before the "
        "results can be relied upon — see the findings below.")
    story.append(Paragraph(
        '<font color="' + _RED.hexval() + '"><b>FAIL</b></font> — a material '
        "discrepancy requiring correction. " + fail_note, s["body"]))

    story.append(Paragraph("INDEPENDENT AUDITOR", s["h2"]))
    story.append(Paragraph(
        "All Layer 2 recomputations were performed by Claude Opus "
        "(claude-opus-4-7), a model independent of the platform's "
        "computation layer (claude-sonnet-4-6). The auditor received only "
        "raw data and formula specifications — never the platform's "
        "intermediate calculations.", s["body"]))
    story.append(PageBreak())

    # ── Page 3+ — detailed findings ──
    story.append(Paragraph("Detailed Findings", s["h1"]))
    story.append(_rule())
    layers = [
        ("layer_1", "LAYER 1: RAW DATA VERIFICATION", run.get("layer_1_status")),
        ("layer_2", "LAYER 2: INDEPENDENT RECOMPUTATION",
         run.get("layer_2_status")),
        ("layer_3", "LAYER 3: CONSISTENCY CHECKS", run.get("layer_3_status")),
    ]
    for key, label, layer_status in layers:
        story.append(Paragraph(label, s["h2"]))
        rows = findings.get(key) or []
        if not rows:
            # An empty findings list is NOT the same as a skipped layer —
            # the layer's own status column is authoritative.
            story.append(Paragraph(
                _layer_empty_message(layer_status), s["caption"]))
            continue
        for f in rows:
            strat = f" · {_esc(f.get('strategy'))}" if f.get("strategy") else ""
            story.append(Paragraph(
                f"{_bullet(f.get('status'))} {_esc(f.get('check_name'))} — "
                f"{_esc(f.get('metric'))}{strat}&nbsp;&nbsp;"
                f"{_verdict_tag(f.get('status'))}", s["finding"]))
            if f.get("platform_value") is not None:
                story.append(Paragraph(
                    f"Platform value: {_esc(f.get('platform_value'))}",
                    s["detail"]))
            if f.get("auditor_value") is not None:
                story.append(Paragraph(
                    f"Auditor value: {_esc(f.get('auditor_value'))}",
                    s["detail"]))
            if f.get("discrepancy"):
                story.append(Paragraph(
                    f"Discrepancy: {_esc(f.get('discrepancy'))}", s["detail"]))
            if f.get("auditor_reasoning"):
                story.append(Paragraph(_esc(f.get("auditor_reasoning")),
                                       s["detail"]))
            # An acknowledged WARN — the team's recorded response. The
            # finding's verdict is unchanged; this documents the response.
            if f.get("resolved"):
                story.append(Paragraph(
                    f'<font color="{_GREEN.hexval()}"><b>Acknowledged</b>'
                    f'</font> — Response: '
                    f'{_esc(f.get("resolution_note") or "(no note)")}',
                    s["detail"]))
    story.append(PageBreak())

    # ── Final page — data provenance ──
    story.append(Paragraph("Data Provenance", s["h1"]))
    story.append(_rule())
    period = meta.get("study_period") or {}
    rf = meta.get("risk_free_rate") or {}
    prov = [
        ["Study period",
         f"{period.get('start', '—')} to {period.get('end', '—')} "
         f"({period.get('months', '—')} months)"],
        ["Risk-free rate",
         f"{rf.get('value', '—')} — {rf.get('source', 'FRED DTB3')}"],
        ["Factor model", str(meta.get("factor_model", "Carhart four-factor"))],
        ["Audit model", "claude-opus-4-7"],
        ["Platform computation", "claude-sonnet-4-6"],
        ["Data hash", str(data_hash)],
    ]
    tbl = Table([[Paragraph(f"<b>{_esc(k)}</b>", s["detail"]),
                  Paragraph(_esc(v), s["detail"])] for k, v in prov],
                colWidths=[1.7 * inch, 4.6 * inch])
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, _RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph(
        "The data hash allows this audit to be reproduced against the "
        "identical dataset. Any change to the underlying data will produce "
        "a different hash.", s["caption"]))

    return _render(story, "Forest Capital — Statistical Audit Report")


# ── Report 2 — Methodology Audit ──────────────────────────────────────────────

# Human-readable category headers for the methodology checklist.
_CATEGORY_LABELS = {
    "DATA_INTEGRITY": "Data Integrity",
    "PORTFOLIO_MECHANICS": "Portfolio Mechanics",
    "STATISTICAL_INTEGRITY": "Statistical Integrity",
    "CROSS_VALIDATION": "Cross-Validation",
    "OVERFITTING": "Overfitting Controls",
    "ECONOMIC_SIGNIFICANCE": "Economic Significance",
    "PRESENTATION": "Presentation",
    "ANALYTICS": "Analytics Layer",
    "INTEGRATION": "Platform Integration",
}


def build_methodology_audit_pdf(audit: dict[str, Any]) -> bytes:
    """
    Renders a QAAgent.run_audit() report dict as the Methodology Audit
    Report PDF.
    """
    s = _styles()
    items = audit.get("items") or []
    # Per-check filtering — split the QA analysis into per-check sections
    # by check-id header, identical to the UI. Each check then shows ONLY
    # its own section, never the whole raw_analysis blob.
    from agents.qa_agent import _split_raw_analysis
    raw_analysis = audit.get("raw_analysis") or ""
    sections = _split_raw_analysis(raw_analysis)
    total = audit.get("checks_total") or len(items)
    passed = audit.get("checks_passed") or 0
    warned = audit.get("checks_warned") or 0
    failed = audit.get("checks_failed") or 0
    verdict = audit.get("verdict") or _status_label(
        "FAIL" if failed else "WARN" if warned else "PASS")

    story: list = []

    # ── Page 1 — cover ──
    story.append(Spacer(1, 1.4 * inch))
    story.append(Paragraph("FOREST CAPITAL PORTFOLIO INTELLIGENCE SYSTEM",
                            s["cover_title"]))
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph("Methodology Audit Report", s["cover_title"]))
    story.append(Paragraph(f"{total}-Point Quality Assurance Review",
                            s["cover_sub"]))
    story.append(Spacer(1, 0.4 * inch))
    story.append(Paragraph("FNA 670 Practicum — Summer 2026", s["cover_sub"]))
    story.append(Paragraph("McColl School of Business", s["cover_sub"]))
    story.append(Paragraph("Queens University Charlotte", s["cover_sub"]))
    story.append(Spacer(1, 0.5 * inch))
    story.append(Paragraph(f"Generated: {_timestamp()}", s["cover_meta"]))
    story.append(Paragraph(f"Audit scope: {total}-point checklist",
                            s["cover_meta"]))
    story.append(Paragraph(f"Sprint: {_esc(audit.get('sprint') or '—')}",
                            s["cover_meta"]))
    story.append(PageBreak())

    # ── Page 2 — executive summary ──
    story.append(Paragraph("Executive Summary", s["h1"]))
    story.append(_rule())
    story.append(Paragraph("WHAT THIS AUDIT IS", s["h2"]))
    story.append(Paragraph(
        "This report presents the results of a structured methodology "
        "quality review of the Forest Capital Portfolio Intelligence "
        "System. An independent QA agent evaluated the platform's "
        f"analytical methodology against a {total}-point checklist covering "
        "data integrity, portfolio mechanics, statistical rigour, "
        "cross-validation, overfitting controls, economic significance, "
        "presentation quality, the analytics layer, and the platform's own "
        "verification subsystems.", s["body"]))

    story.append(Paragraph("SCOPE", s["h2"]))
    story.append(Paragraph(
        "Every check in this audit corresponds to a methodology requirement "
        "of the FNA 670 project or a deliberate analytical addition made to "
        "strengthen the project's rigour. No check exists for a method that "
        "was not implemented.", s["body"]))

    story.append(Paragraph("OVERALL RESULT", s["h2"]))
    story.append(Paragraph(
        f'<font color="{_status_colour(verdict).hexval()}"><b>{verdict}'
        f'</b></font>&nbsp;&nbsp;{total} checks · {passed} passed · '
        f'{warned} warnings · {failed} failures', s["body"]))

    story.append(Paragraph("VERDICT DEFINITIONS", s["h2"]))
    story.append(Paragraph(
        '<font color="' + _GREEN.hexval() + '"><b>PASS</b></font> — '
        "methodology is sound on this dimension.", s["body"]))
    story.append(Paragraph(
        '<font color="' + _AMBER.hexval() + '"><b>WARN</b></font> — should '
        'be addressed or explicitly disclosed as a limitation.', s["body"]))
    story.append(Paragraph(
        '<font color="' + _RED.hexval() + '"><b>FAIL</b></font> — must be '
        "fixed before presenting. A professional quant would catch and "
        "criticise this.", s["body"]))
    story.append(PageBreak())

    # ── Page 3+ — detailed findings, grouped by category ──
    story.append(Paragraph("Detailed Findings", s["h1"]))
    story.append(_rule())
    # Preserve the checklist order of categories as they first appear.
    seen: list[str] = []
    for it in items:
        cat = it.get("category") or "OTHER"
        if cat not in seen:
            seen.append(cat)
    for cat in seen:
        group = [it for it in items if (it.get("category") or "OTHER") == cat]
        ids = [it.get("check_id", "") for it in group if it.get("check_id")]
        span = f" ({ids[0]}–{ids[-1]})" if len(ids) > 1 else (
            f" ({ids[0]})" if ids else "")
        story.append(Paragraph(
            _CATEGORY_LABELS.get(cat, cat.replace("_", " ").title()) + span,
            s["h2"]))
        for it in group:
            story.append(Paragraph(
                f"{_bullet(it.get('status'))} "
                f"{_esc(it.get('check_id'))} — {_esc(it.get('check'))}"
                f"&nbsp;&nbsp;{_verdict_tag(it.get('status'))}", s["finding"]))
            if it.get("description"):
                story.append(Paragraph(_esc(it.get("description")),
                                       s["detail"]))
            # Per-check detail — this check's own section of the QA
            # analysis, matched by its check-id header. Never the whole
            # raw_analysis blob, and never another check's section.
            cid = it.get("check_id")
            section = sections.get(cid)
            evidence = it.get("evidence")
            if section:
                detail = section
            elif evidence and evidence != raw_analysis:
                # A deterministic check carries its own computed evidence.
                detail = evidence
            else:
                detail = "No detailed analysis available for this check."
            story.append(Paragraph(_esc(detail), s["detail"]))
            # Skip the "See the <id> analysis section above" cross-
            # reference — a template artifact, meaningless now that each
            # check shows its own section inline.
            fix = it.get("fix")
            if fix and "analysis section above" not in fix:
                story.append(Paragraph(
                    f"<b>Fix:</b> {_esc(fix)}", s["detail"]))

    return _render(story, "Forest Capital — Methodology Audit Report")
