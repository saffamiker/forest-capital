"""tools/table_structure_validator.py -- June 28 2026.

Table structure validation (PR β).

PROBLEM
  Table B.1 in the analytical appendix shipped missing the
  "Excess Return vs Benchmark" column that the surrounding text
  claimed was present (caught manually by operator audit on
  June 28). The document audit pipeline checks numeric values
  + citations but does NOT verify that generated tables contain
  all required columns. A missing column survives every
  existing check.

FIX
  After content_json is assembled, walk every TipTap table node
  in the document, look up its caption against
  REQUIRED_TABLE_COLUMNS, and emit a structured audit flag for
  any missing required column.

REGISTRY SHAPE

  REQUIRED_TABLE_COLUMNS: dict[document_type, dict[caption_prefix,
                                                   list[required_cols]]]

  Caption prefix matching: TipTap renders each table with a
  preceding caption paragraph (e.g. "Table B1. Full-Period
  Performance by Strategy..."). The registry keys on a
  caption-prefix string ("Table B1") so a minor caption text
  edit (operator adds "(2026 update)" suffix) doesn't break
  validation. Match is case-insensitive + whitespace-tolerant.

  Adding a new required table = one entry in the registry.
  No code change.

CONTENT_JSON SHAPE (TipTap)
  Tables sit as inline blocks within the document tree:
    {"type": "table", "content": [
        {"type": "tableRow", "content": [
            {"type": "tableCell", "content": [
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "Strategy"}
                ]}
            ]},
            ...
        ]},
        ...
    ]}
  The first tableRow carries the headers.
"""
from __future__ import annotations

import logging
import re
from typing import Any

try:
    import structlog
    log = structlog.get_logger(__name__)
except ImportError:
    log = logging.getLogger(__name__)  # type: ignore[assignment]


# ── Registry: required columns per table per document type ──────


REQUIRED_TABLE_COLUMNS: dict[str, dict[str, list[str]]] = {
    "analytical_appendix": {
        "Table B1": [
            "Strategy", "Sharpe", "CAGR",
            "Excess Return vs Benchmark",
            "Volatility", "Sortino", "Calmar", "Max DD",
        ],
        "Table C1": [
            "Strategy", "p (paired t)", "p (FDR-adj)",
            "DSR p", "PSR", "SPA pass",
        ],
        "Table D1": [
            "Strategy", "Sharpe",
            "95% CI low", "95% CI high",
            "Overlaps benchmark",
        ],
        "Table E1": [
            "Strategy", "Alpha", "MKT-RF",
            "SMB", "HML", "MOM", "R-squared",
        ],
        "Table G1": [
            "Bps per rebalance", "Net Sharpe",
            "vs Benchmark", "Material rebalances",
        ],
    },
    # June 28 2026 -- executive_brief + presentation_deck +
    # presentation_script carry no data tables today. Empty dicts
    # so the validator can be invoked uniformly; adding a future
    # table for any of these is one registry entry.
    "executive_brief":     {},
    "presentation_deck":   {},
    "presentation_script": {},
}


# ── Caption matching helpers ─────────────────────────────────────


def _norm_caption(text: str) -> str:
    """Case-insensitive, whitespace-collapsed comparable form.
    'Table B1. Full-Period Performance' and 'table  B1.\\nFull-
    period performance' compare equal. Preserves the meaningful
    word boundaries so 'Table B11' doesn't false-match a 'Table
    B1' registry key."""
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text).strip().lower()


def _caption_matches_key(caption: str, key: str) -> bool:
    """Returns True when the caption text starts with the
    registry key (after normalisation). Word-boundary check
    prevents 'Table B11' matching the 'Table B1' key.

    Examples:
      caption='Table B1. Full-Period Performance'  key='Table B1'  -> True
      caption='Table B11. Other'                   key='Table B1'  -> False
      caption='Table B1: '                         key='Table B1'  -> True
    """
    nc = _norm_caption(caption)
    nk = _norm_caption(key)
    if not nc.startswith(nk):
        return False
    # The character immediately following the matched prefix
    # must be a word boundary (., ', ', :, etc) -- not another
    # digit / letter that would extend the table identifier.
    nxt = nc[len(nk):len(nk) + 1]
    return nxt == "" or not nxt.isalnum()


# ── TipTap tree walker ─────────────────────────────────────────


def _extract_text(node: Any) -> str:
    """Flatten a node's text content. Handles plain text +
    token_value (June 28 dual-mode) nodes uniformly so a
    header cell containing a substituted value still produces
    the right header string."""
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "token_value":
        attrs = node.get("attrs") or {}
        return str(attrs.get("override") or attrs.get("resolved") or "")
    if node.get("text"):
        return str(node["text"])
    return "".join(_extract_text(c) for c in (node.get("content") or []))


def _table_headers(table_node: dict[str, Any]) -> list[str]:
    """The header cells of a TipTap table node. Returns the
    first tableRow's cell text contents in order. Empty list if
    the table has no rows or no cells."""
    for row in (table_node.get("content") or []):
        if not isinstance(row, dict):
            continue
        if row.get("type") != "tableRow":
            continue
        cells = row.get("content") or []
        headers: list[str] = []
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            ct = cell.get("type")
            if ct not in {"tableCell", "tableHeader"}:
                continue
            headers.append(_extract_text(cell).strip())
        return headers
    return []


def _walk_tables_with_captions(
    content_json: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Returns [(caption_text, table_node), ...] in document
    order. Caption text = the text of the paragraph IMMEDIATELY
    preceding each table node in the document's top-level
    content array. Tables with no preceding paragraph get an
    empty caption (excluded from validation)."""
    out: list[tuple[str, dict[str, Any]]] = []
    if not isinstance(content_json, dict):
        return out
    top = content_json.get("content")
    if not isinstance(top, list):
        return out
    prev_caption = ""
    for node in top:
        if not isinstance(node, dict):
            continue
        ntype = node.get("type")
        if ntype == "paragraph":
            prev_caption = _extract_text(node).strip()
            continue
        if ntype == "table":
            out.append((prev_caption, node))
            prev_caption = ""
            continue
        # Reset caption when an intervening block appears
        # (heading / list / etc). A heading immediately before a
        # table is a valid caption replacement; in practice the
        # _add_table helper at academic_docx.py:423 writes a
        # paragraph caption, so the paragraph path dominates.
        if ntype == "heading":
            prev_caption = _extract_text(node).strip()
            continue
        prev_caption = ""
    return out


# ── Public entry point ─────────────────────────────────────────


def validate_table_structure(
    content_json: dict[str, Any],
    document_type: str,
) -> list[dict[str, Any]]:
    """Validate that every required table in the document
    carries every required column.

    Returns a list of structured audit flags:
      {
        check_type:      "table_structure",
        severity:        "major",
        table_name:      str   -- registry key matched
        caption:         str   -- caption text in the document
        missing_columns: list[str]
        present_columns: list[str]
        message:         str
      }

    Empty list when document_type has no entries in the
    registry OR every required column is present in every
    required table.

    Tables present in the document but NOT in the registry are
    ignored -- the registry is a positive allow-list of required
    tables, not a closed enumeration of every table.

    Required tables missing entirely from the document (no
    caption match) are flagged with present_columns=[].
    """
    flags: list[dict[str, Any]] = []
    registry = REQUIRED_TABLE_COLUMNS.get(document_type, {})
    if not registry:
        # No registered tables for this document type -- nothing
        # to validate.
        return flags
    if not isinstance(content_json, dict):
        log.warning(
            "table_structure_validator_skipped_non_dict_content",
            document_type=document_type)
        return flags

    walked = _walk_tables_with_captions(content_json)

    # For each registry entry, find the matching table in the
    # document.
    for table_key, required_cols in registry.items():
        match: dict[str, Any] | None = None
        caption_text: str = ""
        for cap, table_node in walked:
            if _caption_matches_key(cap, table_key):
                match = table_node
                caption_text = cap
                break

        if match is None:
            flags.append({
                "check_type":      "table_structure",
                "severity":        "major",
                "table_name":      table_key,
                "caption":         "",
                "missing_columns": list(required_cols),
                "present_columns": [],
                "message":         (
                    f"{table_key} is REQUIRED for "
                    f"{document_type} but no matching caption "
                    f"was found in the document. Required "
                    f"columns: {', '.join(required_cols)}."),
            })
            continue

        headers = _table_headers(match)
        # Header comparison is case-insensitive +
        # whitespace-normalised so a stylistic edit
        # (cell ends with newline / extra space) doesn't trip
        # the check. Required-column strings ARE compared
        # verbatim against the normalised header set.
        header_norm = {
            re.sub(r"\s+", " ", h).strip().lower()
            for h in headers}
        missing: list[str] = []
        for col in required_cols:
            col_norm = re.sub(r"\s+", " ", col).strip().lower()
            if col_norm not in header_norm:
                missing.append(col)

        if missing:
            flags.append({
                "check_type":      "table_structure",
                "severity":        "major",
                "table_name":      table_key,
                "caption":         caption_text,
                "missing_columns": missing,
                "present_columns": headers,
                "message":         (
                    f"{table_key} is missing required "
                    f"column(s): {', '.join(missing)}. The "
                    f"surrounding text claims these columns are "
                    f"present."),
            })

    if flags:
        log.warning(
            "table_structure_validator_flags",
            document_type=document_type,
            flag_count=len(flags),
            missing_tables=sum(
                1 for f in flags if not f["caption"]),
            missing_columns=sum(
                len(f["missing_columns"]) for f in flags),
        )
    return flags
