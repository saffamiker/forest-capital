"""tests/test_table_structure_validator.py -- June 28 2026.

Pins the table-structure validator + REQUIRED_TABLE_COLUMNS
registry (PR β).
"""
from __future__ import annotations

import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── Registry shape ─────────────────────────────────────────────


class TestRequiredTableColumnsRegistry:

    def test_registry_carries_appendix_required_tables(self):
        from tools.table_structure_validator import (
            REQUIRED_TABLE_COLUMNS,
        )
        appx = REQUIRED_TABLE_COLUMNS["analytical_appendix"]
        for key in ("Table B1", "Table C1", "Table D1",
                    "Table E1", "Table G1"):
            assert key in appx, (
                f"{key} missing from analytical_appendix registry")

    def test_table_b1_includes_excess_return(self):
        """The motivating bug -- Table B.1 must include the
        'Excess Return vs Benchmark' column. PR α restored the
        column in the generator; this registry pin ensures any
        future regression in the column list fires an audit
        flag."""
        from tools.table_structure_validator import (
            REQUIRED_TABLE_COLUMNS,
        )
        cols = REQUIRED_TABLE_COLUMNS[
            "analytical_appendix"]["Table B1"]
        assert "Excess Return vs Benchmark" in cols
        # Position after CAGR -- per operator spec.
        assert cols.index("Excess Return vs Benchmark") == (
            cols.index("CAGR") + 1)

    def test_brief_deck_script_have_empty_registries(self):
        """Per operator spec: brief, deck, script carry no
        data tables today. Empty dicts so the validator runs
        uniformly; future tables = one-line registry add."""
        from tools.table_structure_validator import (
            REQUIRED_TABLE_COLUMNS,
        )
        for dt in ("executive_brief", "presentation_deck",
                   "presentation_script"):
            assert dt in REQUIRED_TABLE_COLUMNS
            assert REQUIRED_TABLE_COLUMNS[dt] == {}


# ── Caption matching ─────────────────────────────────────────


class TestCaptionMatches:

    def test_exact_prefix_matches(self):
        from tools.table_structure_validator import (
            _caption_matches_key,
        )
        assert _caption_matches_key(
            "Table B1. Full-Period Performance", "Table B1")
        assert _caption_matches_key("Table B1: header", "Table B1")
        assert _caption_matches_key(
            "TABLE B1 -- foo", "Table B1")

    def test_word_boundary_rejects_substring(self):
        """'Table B11' must NOT match the 'Table B1' key."""
        from tools.table_structure_validator import (
            _caption_matches_key,
        )
        assert not _caption_matches_key("Table B11.", "Table B1")
        assert not _caption_matches_key("Table B1A.", "Table B1")

    def test_whitespace_tolerant(self):
        from tools.table_structure_validator import (
            _caption_matches_key,
        )
        assert _caption_matches_key("Table  B1.\nFoo", "Table B1")


# ── Validator end-to-end ─────────────────────────────────────


def _make_table_node(headers: list[str], rows: int = 1) -> dict:
    """Build a TipTap table node with the given header row +
    `rows` empty data rows."""
    header_row = {
        "type": "tableRow",
        "content": [
            {"type": "tableHeader", "content": [{
                "type": "paragraph", "content": [{
                    "type": "text", "text": h}]}]}
            for h in headers
        ],
    }
    data_rows = [
        {"type": "tableRow", "content": [
            {"type": "tableCell", "content": [{
                "type": "paragraph", "content": [{
                    "type": "text", "text": "—"}]}]}
            for _ in headers
        ]}
        for _ in range(rows)
    ]
    return {"type": "table", "content": [header_row] + data_rows}


def _make_doc(
    table_specs: list[tuple[str, list[str]]],
) -> dict:
    """Build a TipTap doc with one (caption_paragraph, table)
    pair per spec entry."""
    content = []
    for caption, headers in table_specs:
        content.append({
            "type": "paragraph",
            "content": [{"type": "text", "text": caption}],
        })
        content.append(_make_table_node(headers))
    return {"type": "doc", "content": content}


class TestValidateTableStructure:

    def test_all_columns_present_no_flags(self):
        from tools.table_structure_validator import (
            validate_table_structure,
        )
        doc = _make_doc([
            ("Table B1. Full-Period Performance",
             ["Strategy", "Sharpe", "CAGR",
              "Excess Return vs Benchmark",
              "Volatility", "Sortino", "Calmar", "Max DD"]),
            ("Table C1. Statistical Tests",
             ["Strategy", "p (paired t)", "p (FDR-adj)",
              "DSR p", "PSR", "SPA pass"]),
            ("Table D1. Bootstrap CI",
             ["Strategy", "Sharpe", "95% CI low",
              "95% CI high", "Overlaps benchmark"]),
            ("Table E1. Factor Loadings",
             ["Strategy", "Alpha", "MKT-RF", "SMB", "HML",
              "MOM", "R-squared"]),
            ("Table G1. Cost Sensitivity",
             ["Bps per rebalance", "Net Sharpe",
              "vs Benchmark", "Material rebalances"]),
        ])
        flags = validate_table_structure(
            doc, "analytical_appendix")
        assert flags == []

    def test_missing_column_flags(self):
        """The motivating bug: Table B1 ships without
        'Excess Return vs Benchmark'."""
        from tools.table_structure_validator import (
            validate_table_structure,
        )
        doc = _make_doc([
            ("Table B1. Full-Period Performance",
             ["Strategy", "Sharpe", "CAGR",
              # No Excess Return!
              "Volatility", "Sortino", "Calmar", "Max DD"]),
        ])
        flags = validate_table_structure(
            doc, "analytical_appendix")
        # Five required tables -> 5 flags expected (4 missing
        # entirely + 1 with missing column).
        b1_flags = [f for f in flags
                    if f["table_name"] == "Table B1"]
        assert len(b1_flags) == 1
        assert b1_flags[0]["severity"] == "major"
        assert "Excess Return vs Benchmark" in (
            b1_flags[0]["missing_columns"])
        assert b1_flags[0]["check_type"] == "table_structure"

    def test_missing_table_entirely_flagged(self):
        from tools.table_structure_validator import (
            validate_table_structure,
        )
        # Empty document -- no tables at all.
        doc = {"type": "doc", "content": []}
        flags = validate_table_structure(
            doc, "analytical_appendix")
        # 5 required tables -> 5 missing flags
        assert len(flags) == 5
        for f in flags:
            assert f["caption"] == ""
            assert f["present_columns"] == []
            assert "no matching caption" in f["message"]

    def test_skips_unregistered_document_types(self):
        from tools.table_structure_validator import (
            validate_table_structure,
        )
        # executive_brief has empty registry -> no validation
        # work + no flags.
        doc = _make_doc([
            ("Some random table", ["A", "B"]),
        ])
        flags = validate_table_structure(doc, "executive_brief")
        assert flags == []

    def test_extra_tables_ignored(self):
        """Tables in the document that aren't in the registry
        are ignored -- registry is positive allow-list, not
        closed enumeration."""
        from tools.table_structure_validator import (
            validate_table_structure,
        )
        doc = _make_doc([
            ("Table B1. Full-Period Performance",
             ["Strategy", "Sharpe", "CAGR",
              "Excess Return vs Benchmark",
              "Volatility", "Sortino", "Calmar", "Max DD"]),
            ("Table C1. Statistical Tests",
             ["Strategy", "p (paired t)", "p (FDR-adj)",
              "DSR p", "PSR", "SPA pass"]),
            ("Table D1. Bootstrap CI",
             ["Strategy", "Sharpe", "95% CI low",
              "95% CI high", "Overlaps benchmark"]),
            ("Table E1. Factor Loadings",
             ["Strategy", "Alpha", "MKT-RF", "SMB", "HML",
              "MOM", "R-squared"]),
            ("Table G1. Cost Sensitivity",
             ["Bps per rebalance", "Net Sharpe",
              "vs Benchmark", "Material rebalances"]),
            ("Table Z9. Operator scratch -- not registered",
             ["Foo", "Bar"]),
        ])
        flags = validate_table_structure(
            doc, "analytical_appendix")
        assert flags == []  # Table Z9 ignored; B/C/D/E/G complete

    def test_whitespace_insensitive_header_match(self):
        from tools.table_structure_validator import (
            validate_table_structure,
        )
        # Header rendered with trailing whitespace + different
        # case still matches.
        doc = _make_doc([
            ("Table B1. Foo",
             [" strategy ", "sharpe", "CAGR",
              "EXCESS RETURN VS BENCHMARK",
              "volatility", "Sortino", "Calmar", "max dd"]),
        ])
        flags = validate_table_structure(
            doc, "analytical_appendix")
        b1_flags = [f for f in flags
                    if f["table_name"] == "Table B1"]
        # Whitespace + case-insensitive match -> no missing.
        assert b1_flags == [] or (
            "missing_columns" in b1_flags[0]
            and not b1_flags[0]["missing_columns"])

    def test_token_value_node_in_header_renders_resolved(self):
        """A dual-mode upgraded table header containing a
        token_value node (rare but possible) renders as the
        resolved value for matching purposes."""
        from tools.table_structure_validator import (
            _extract_text,
        )
        cell = {"type": "tableCell", "content": [{
            "type": "paragraph", "content": [
                {"type": "token_value", "attrs": {
                    "token":    "{{X}}",
                    "resolved": "Strategy",
                }},
            ]}]}
        assert _extract_text(cell).strip() == "Strategy"


# ── Audit pipeline integration ───────────────────────────────


class TestAuditPipelineWiring:

    def test_audit_document_accepts_content_json_kwarg(self):
        import inspect
        from tools.document_audit import audit_document
        sig = inspect.signature(audit_document)
        assert "content_json" in sig.parameters

    def test_flag_counts_includes_table_structure(self):
        from tools.document_audit import AuditResult
        r = AuditResult()
        assert "table_structure" in r.flag_counts
        assert r.flag_counts["table_structure"] == 0

    def test_audit_runs_table_structure_check(self):
        from tools.document_audit import audit_document
        # Doc missing the Table B1 'Excess Return' column.
        doc = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{
                    "type": "text",
                    "text": "Table B1. Full-Period Performance"}]},
                {"type": "table", "content": [
                    {"type": "tableRow", "content": [
                        {"type": "tableHeader", "content": [{
                            "type": "paragraph", "content": [{
                                "type": "text",
                                "text": h}]}]}
                        for h in ["Strategy", "Sharpe", "CAGR",
                                  # Excess Return missing
                                  "Volatility", "Sortino",
                                  "Calmar", "Max DD"]
                    ]},
                ]},
            ],
        }
        result = audit_document(
            text="", document_type="analytical_appendix",
            content_json=doc)
        ts_flags = result.flags_by_check["table_structure"]
        b1_missing = [f for f in ts_flags
                      if f["table_name"] == "Table B1"
                      and "Excess Return vs Benchmark"
                      in f["missing_columns"]]
        assert len(b1_missing) == 1

    def test_audit_skips_table_structure_when_no_content_json(
            self):
        from tools.document_audit import audit_document
        result = audit_document(
            text="", document_type="analytical_appendix")
        assert "table_structure" in result.skipped
