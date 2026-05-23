"""tools/report_writer_docx.py — docx assembly for the report-writer
midpoint paper + appendix.

May 22 2026 (item 12 commit 2). Pure document assembly: takes a
generated markdown body and a context dict, emits Word (.docx) bytes.
No LLM calls, no database reads — the upstream pipeline (template_
pipeline + report_generator) does all the gathering.

Formatting follows the FNA670 brief:
  12-point Times New Roman / equivalent serif body
  Double-spaced body, single-spaced tables / appendix data
  1-inch margins
  Page numbers in the footer
  Standard header: "Forest Capital — FNA670 Industry Practicum —
    Midpoint Check"
  Standard footer line above page number: "Confidential — Queens
    University — May 2026"

The build_paper_docx function renders a markdown body (produced by
the Academic Writer) — headings (`## Section N`), paragraphs, and
inline emphasis are all preserved. The build_appendix_docx function
assembles four sections programmatically from the context dict — it
does NOT take a markdown body, because the appendix structure is
fixed and tabular and is filled with live data, not narrative.
"""
from __future__ import annotations

import re
from io import BytesIO
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


_BODY_FONT = "Times New Roman"

_HEADER_TEXT = (
    "Forest Capital — FNA670 Industry Practicum — Midpoint Check")
_FOOTER_TEXT = "Confidential — Queens University — May 2026"


# ── Low-level helpers ────────────────────────────────────────────────────────


def _set_run_color(run, hex_color: str) -> None:
    rgb = hex_color.lstrip("#")
    run.font.color.rgb = RGBColor(
        int(rgb[0:2], 16), int(rgb[2:4], 16), int(rgb[4:6], 16))


def _add_page_number(paragraph) -> None:
    """Live PAGE field via raw OOXML."""
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)
    run._r.append(instr)
    run._r.append(end)


def _new_document(double_spaced: bool = True) -> Document:
    """Document pre-configured to the FNA670 formatting brief."""
    doc = Document()
    section = doc.sections[0]
    section.left_margin = Inches(1)
    section.right_margin = Inches(1)
    section.top_margin = Inches(1)
    section.bottom_margin = Inches(1)

    normal = doc.styles["Normal"]
    normal.font.name = _BODY_FONT
    normal.font.size = Pt(12)
    normal.paragraph_format.line_spacing_rule = (
        WD_LINE_SPACING.DOUBLE if double_spaced
        else WD_LINE_SPACING.SINGLE)
    normal.paragraph_format.space_after = Pt(0)

    # Header line — fixed across the whole document.
    header_para = section.header.paragraphs[0]
    header_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    h_run = header_para.add_run(_HEADER_TEXT)
    h_run.font.name = _BODY_FONT
    h_run.font.size = Pt(10)
    _set_run_color(h_run, "#374151")  # slate-700

    # Footer — confidential line + page number.
    footer_para = section.footer.paragraphs[0]
    footer_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    f_run = footer_para.add_run(_FOOTER_TEXT + "    Page ")
    f_run.font.name = _BODY_FONT
    f_run.font.size = Pt(9)
    _set_run_color(f_run, "#6b7280")
    _add_page_number(footer_para)

    return doc


def _add_section_heading(doc: Document, text: str, *, size: int = 13) -> None:
    """Bold heading on the body font — kept off Word Heading styles so
    the 12pt serif brief holds throughout."""
    para = doc.add_paragraph()
    para.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
    para.paragraph_format.space_before = Pt(12)
    para.paragraph_format.space_after = Pt(4)
    run = para.add_run(text)
    run.bold = True
    run.font.name = _BODY_FONT
    run.font.size = Pt(size)


def _add_body_block(doc: Document, text: str) -> None:
    """Renders a markdown-ish body block. Recognises bold (**), italic
    (*), and inline code (`) — same convention the Academic Writer
    emits. No nested lists; one paragraph per blank-line-separated
    block."""
    for block in (text or "").strip().split("\n\n"):
        block = block.strip()
        if not block:
            continue
        para = doc.add_paragraph()
        for run_text, bold, italic in _split_inline(block):
            if not run_text:
                continue
            run = para.add_run(run_text)
            run.font.name = _BODY_FONT
            run.font.size = Pt(12)
            run.bold = bold
            run.italic = italic


_INLINE_RE = re.compile(
    r"(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)")


def _split_inline(block: str) -> "list[tuple[str, bool, bool]]":
    """Splits an inline block into (text, bold, italic) tuples."""
    out: list[tuple[str, bool, bool]] = []
    for part in _INLINE_RE.split(block):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            out.append((part[2:-2], True, False))
        elif part.startswith("`") and part.endswith("`"):
            out.append((part[1:-1], False, True))  # render code as italic
        elif part.startswith("*") and part.endswith("*"):
            out.append((part[1:-1], False, True))
        else:
            out.append((part, False, False))
    return out


def _add_table(
    doc: Document,
    headers: list[str],
    rows: list[list[Any]],
    *,
    column_widths_in: list[float] | None = None,
) -> None:
    """Single-spaced data table with a header row."""
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Table Grid"
    # Header row.
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        p = cell.paragraphs[0]
        p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
        run = p.add_run(h)
        run.bold = True
        run.font.name = _BODY_FONT
        run.font.size = Pt(10)
    # Data rows.
    for r_idx, row in enumerate(rows):
        for c_idx, val in enumerate(row):
            cell = table.rows[r_idx + 1].cells[c_idx]
            p = cell.paragraphs[0]
            p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
            run = p.add_run(str(val) if val is not None else "")
            run.font.name = _BODY_FONT
            run.font.size = Pt(10)
    if column_widths_in:
        for i, w in enumerate(column_widths_in):
            if i < len(table.columns):
                for cell in table.columns[i].cells:
                    cell.width = Inches(w)


# ── Paper builder ────────────────────────────────────────────────────────────


_PAPER_SECTION_RE = re.compile(
    r"(?m)^#{1,3}\s*(?:SECTION\s+)?(\d+)\.?\s*(.*?)$",
    re.IGNORECASE)


def _split_paper_sections(
    paper_md: str,
) -> list[tuple[str, str]]:
    """Splits a paper markdown body into (heading, body) tuples.
    Falls back to a single (no-heading, body) tuple when no H2
    headings are found."""
    matches = list(_PAPER_SECTION_RE.finditer(paper_md or ""))
    if not matches:
        return [("", (paper_md or "").strip())]
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(paper_md)
        body = paper_md[start:end].strip()
        title = m.group(2).strip() or f"Section {m.group(1)}"
        heading = f"{m.group(1)}. {title}"
        out.append((heading, body))
    return out


def build_paper_docx(
    paper_md: str,
    *,
    references_md: str | None = None,
) -> bytes:
    """Renders the main paper markdown into Word bytes.

    paper_md should carry section H2 headings ('## 1. Data and
    Methodology' etc.). The renderer splits on those and lays each
    section out as a bold heading followed by the section body.

    references_md, when supplied, is appended as a final 'References'
    section. The pipeline builds it from citations_cache so it always
    matches the inline citations.
    """
    doc = _new_document(double_spaced=True)

    # Title block.
    _add_section_heading(doc, "Midpoint Check Paper", size=16)
    _add_body_block(doc, "FNA670 Industry Practicum")

    # Each section.
    sections = _split_paper_sections(paper_md)
    for heading, body in sections:
        if heading:
            _add_section_heading(doc, heading, size=13)
        _add_body_block(doc, body)

    # References.
    if references_md:
        _add_section_heading(doc, "References", size=13)
        _add_body_block(doc, references_md)

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ── Appendix builder ────────────────────────────────────────────────────────


def _fmt_value(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def _appendix_a(
    doc: Document, context: dict,
) -> None:
    """Appendix A — Platform Overview."""
    _add_section_heading(doc, "Appendix A — Platform Overview", size=14)
    _add_body_block(doc, (
        "The Forest Capital Portfolio Intelligence Platform is a "
        "purpose-built portfolio intelligence system developed for "
        "this practicum. It provides live data pipelines, a ten-"
        "strategy backtesting engine, an AI council of specialists, "
        "independent three-layer data validation, and an integrated "
        "academic review workflow."))

    vd = context.get("verified_data") or {}
    activity = context.get("team_activity") or {}

    _add_section_heading(doc, "Key platform statistics", size=11)
    _add_table(
        doc,
        headers=["Statistic", "Value"],
        rows=[
            ["Strategies analyzed",                10],
            ["Data period start",                  _fmt_value(
                vd.get("study_period_start"))],
            ["Data period end",                    _fmt_value(
                vd.get("study_period_end"))],
            ["Monthly observations",               _fmt_value(
                vd.get("n_months"))],
            ["Independent validations passed",     _fmt_value(
                activity.get("team_total_audit_validations"))],
            ["Council sessions completed",         _fmt_value(
                activity.get("team_total_council_sessions"))],
            ["UAT test steps completed",           _fmt_value(
                activity.get("team_total_uat_steps"))],
        ],
        column_widths_in=[3.0, 2.0])

    _add_body_block(doc, "")
    _add_body_block(doc, (
        "Platform URL: forest-capital.vercel.app"))
    _add_body_block(doc, (
        "Access has been granted to course faculty. Login with your "
        "Queens University email address."))

    # Methodology references — pull from citations_cache only.
    citations = context.get("citations_cache") or {}
    cited: list[str] = []
    for cid in ("cvar_coherent_risk", "four_factor_model",
                "portfolio_diversification", "regime_switching"):
        c = citations.get(cid) or {}
        if c.get("verification_status") == "verified":
            cited.append(c.get("formatted") or "")
    if cited:
        _add_section_heading(doc, "Methodological references",
                             size=11)
        _add_body_block(doc, (
            "The platform's independent validation methodology draws "
            "on established practices in quantitative finance:"))
        for entry in cited:
            _add_body_block(doc, f"• {entry}")
    else:
        _add_body_block(doc, "[CITATION REQUIRED — methodological references]")


def _appendix_b(
    doc: Document, context: dict,
) -> None:
    """Appendix B — Full Analytical Findings."""
    _add_section_heading(doc, "Appendix B — Full Analytical Findings",
                          size=14)
    _add_body_block(doc, (
        "All findings pre-computed from live platform data. Use these "
        "as the factual record alongside the main paper's evidence."))

    findings = context.get("ranked_findings") or []
    if not findings:
        _add_body_block(doc, "[DATA REQUIRED — no findings staged]")
    for i, f in enumerate(findings, start=1):
        _add_section_heading(
            doc,
            f"F{i} — {f.get('title', '')} ({f.get('nugget_strength', 'LOW')})",
            size=11)
        _add_body_block(doc, f"**FINDING:** {f.get('finding', '')}")
        ev = f.get("evidence") or []
        if ev:
            _add_body_block(doc, "**EVIDENCE:**")
            for e in ev:
                _add_body_block(doc, f"• {e}")
        _add_body_block(doc, f"**IMPLICATION:** {f.get('implication', '')}")
        if f.get("surprise"):
            _add_body_block(doc, f"**SURPRISE:** {f.get('surprise_reason') or 'yes'}")

    # Footer line.
    md = context.get("findings_metadata") or {}
    _add_body_block(doc, "")
    _add_body_block(doc, (
        f"All figures pulled live from platform analytics cache on "
        f"{md.get('computed_at', '—')}. Data hash: "
        f"{md.get('data_hash', '—')}. Independent validation: "
        f"three-layer audit {md.get('audit_status', '—')}."))


def _appendix_c(
    doc: Document, context: dict,
) -> None:
    """Appendix C — Team Activity Log."""
    _add_section_heading(doc, "Appendix C — Team Activity Log", size=14)
    _add_body_block(doc, (
        "Auditable record of team contributions throughout the "
        "project lifecycle. All counts pulled live from platform "
        "audit tables at generation time."))

    activity = context.get("team_activity") or {}
    rows: list[list[Any]] = []

    # Per-member rows.
    member_groups = [
        ("Michael Ruurds", [
            ("Commits to repository",  "michael_commits"),
            ("PRs merged",             "michael_prs_merged"),
            ("Migrations deployed",    "michael_migrations_deployed"),
            ("Failure reports resolved", "michael_failure_reports_resolved"),
        ]),
        ("Bob Thao", [
            ("UAT test steps completed", "bob_uat_steps"),
            ("Council sessions initiated", "bob_council_sessions"),
            ("Academic review runs",     "bob_academic_review_runs"),
            ("Report drafts generated",  "bob_report_drafts"),
        ]),
        ("Molly Murdock", [
            ("UAT test steps completed", "molly_uat_steps"),
            ("Failure reports filed",    "molly_failure_reports_filed"),
            ("Feedback items submitted", "molly_feedback_items"),
        ]),
    ]
    for member, fields in member_groups:
        for activity_label, field_key in fields:
            rows.append([
                member, activity_label, _fmt_value(activity.get(field_key)),
            ])

    # Platform totals row.
    rows.append([
        "Platform total", "UAT test steps",
        _fmt_value(activity.get("team_total_uat_steps")),
    ])
    rows.append([
        "Platform total", "Failure reports filed",
        _fmt_value(activity.get("team_total_failure_reports")),
    ])
    rows.append([
        "Platform total", "Failure reports resolved",
        _fmt_value(activity.get("team_total_failure_reports_resolved")),
    ])
    rows.append([
        "Platform total", "Council sessions",
        _fmt_value(activity.get("team_total_council_sessions")),
    ])
    rows.append([
        "Platform total", "Independent validations",
        _fmt_value(activity.get("team_total_audit_validations")),
    ])

    _add_table(
        doc,
        headers=["Member / Total", "Activity", "Count"],
        rows=rows,
        column_widths_in=[2.0, 3.0, 1.0])

    _add_body_block(doc, "")
    _add_body_block(doc, (
        "Activity data pulled live from platform audit log at "
        f"{context.get('generated_at', '—')}. All timestamps "
        "recorded at point of action."))


def _appendix_d(
    doc: Document, context: dict,
) -> None:
    """Appendix D — Data Validation Certificate."""
    _add_section_heading(doc, "Appendix D — Independent Data Validation Summary",
                          size=14)
    validation = context.get("validation_summary") or {}

    _add_section_heading(doc, "Three-layer audit results", size=11)
    _add_table(
        doc,
        headers=["Layer", "Status", "Checks", "Last run"],
        rows=[
            ["1 — Raw data audit",
             _fmt_value(validation.get("layer1_status")),
             _fmt_value(validation.get("layer1_count")),
             _fmt_value(validation.get("layer1_date"))],
            ["2 — Calculation audit",
             _fmt_value(validation.get("layer2_status")),
             _fmt_value(validation.get("layer2_count")),
             _fmt_value(validation.get("layer2_date"))],
            ["3 — Consistency audit",
             _fmt_value(validation.get("layer3_status")),
             _fmt_value(validation.get("layer3_count")),
             _fmt_value(validation.get("layer3_date"))],
        ],
        column_widths_in=[2.5, 1.5, 1.0, 1.5])

    _add_section_heading(doc, "Validation methodology", size=11)
    _add_body_block(doc, (
        "Layer 1 (raw data audit) verifies source data integrity "
        "against published benchmarks. US equity returns benchmarked "
        "against published S&P 500 total return index data; risk-free "
        "rate benchmarked against published DTB3 Treasury bill data "
        "(Federal Reserve H.15 release)."))
    _add_body_block(doc, (
        "Layer 2 (calculation audit) recomputes every metric from "
        "raw inputs in an independent model instance. Standard "
        "formulas: CAGR as the geometric mean of (1 + r) compounded "
        "monthly; Sharpe as (Rp − Rf) / σp annualised by √12; CVaR "
        "as the mean of returns below the VaR threshold via historical "
        "simulation; max drawdown as the maximum peak-to-trough decline "
        "in the cumulative return series."))
    _add_body_block(doc, (
        "Layer 3 (consistency audit) cross-checks every metric across "
        "every surface where it appears. Tolerances: ratios 0.001, "
        "percentages 0.01%, factor betas 0.001."))

    # Cite the GIPS reference and the methodology references from the cache.
    citations = context.get("citations_cache") or {}
    gips = citations.get("gips_verification") or {}
    if gips.get("verification_status") == "verified":
        _add_body_block(doc, (
            f"This methodology is consistent with industry standards "
            f"for independent performance verification as described "
            f"in {gips.get('formatted')}"))
    else:
        _add_body_block(doc, "[CITATION REQUIRED — GIPS]")


def _build_references_md(citations: dict) -> str:
    """Builds the consolidated References section markdown from
    verified entries only. Alphabetical by first-author surname."""
    verified = [
        c for c in (citations or {}).values()
        if c.get("verification_status") == "verified"]
    if not verified:
        return ""
    verified.sort(key=lambda c: (c.get("author") or "").lower())
    lines = [
        c.get("formatted") or "(unformatted)"
        for c in verified]
    return "\n\n".join(lines)


def build_appendix_docx(context: dict) -> bytes:
    """Assembles the four-section appendix into Word bytes.

    context schema:
      verified_data:       dict — for Appendix A statistics + footers
      ranked_findings:     list — for Appendix B
      team_activity:       dict — for Appendix C
      validation_summary:  dict — for Appendix D
      citations_cache:     dict — for inline references + References
      findings_metadata:   dict — computed_at, data_hash, audit_status
      generated_at:        str  — for Appendix C footer
    """
    doc = _new_document(double_spaced=False)

    _add_section_heading(doc, "Appendix — Midpoint Paper", size=16)
    _add_body_block(doc, (
        "This appendix accompanies the FNA670 midpoint paper. It is "
        "self-contained: every claim in the appendix is supported by "
        "live data pulled from the platform analytics cache at "
        "generation time."))

    _appendix_a(doc, context)
    _appendix_b(doc, context)
    _appendix_c(doc, context)
    _appendix_d(doc, context)

    # Consolidated References after all appendices.
    refs_md = _build_references_md(context.get("citations_cache") or {})
    if refs_md:
        _add_section_heading(doc, "References", size=14)
        _add_body_block(doc, refs_md)
    else:
        _add_section_heading(doc, "References", size=14)
        _add_body_block(doc, "[REFERENCES UNAVAILABLE — citations not yet sourced]")

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()
