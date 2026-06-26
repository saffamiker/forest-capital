"""
scripts/patch_document_content.py

One-off operator script: patch the LIVE Executive Brief and
Analytical Appendix drafts in editor_drafts with the
pre-submission content corrections paired with the
fix(generation-prompts) PR. The prompts PR prevents FUTURE
regenerations from reintroducing these defects; this script
patches the EXISTING drafts so the team can submit the current
versions without a costly regenerate-and-edit loop.

The script uses DATABASE_URL from the environment. The Render
shell already exports this (the running web service reads the
same variable); locally you would need to export it to point at
the staging or production DB before running -- the script bails
with a clear error if DATABASE_URL is unset.

USAGE (Render shell, post-merge):

    python scripts/patch_document_content.py

Optional --dry-run flag: log every transform's effect but ROLL
BACK at the end rather than committing. Useful for inspecting
output before a real run.

    python scripts/patch_document_content.py --dry-run

IDEMPOTENT -- each transform checks whether the patch has
already been applied before mutating. A second run is a no-op.
Safe to re-run after a partial failure.

TARGET ROW SELECTION

  is_current = true AND is_deleted = false

  No draft IDs are hardcoded; the script queries by document_type
  + is_current + is_deleted so a future re-run picks up the
  then-current draft.

TRANSFORMS

  Brief:
    B1 -- remove numbered H1 '# N. Title' that precedes
          '## Section N: Title' H2 (all six sections).
    B2 -- Benjamin vs Benjamini citation audit: split the
          conflated 'Benjamin et al. (2018) FDR correction'
          attribution into 'Benjamin et al. (2018)' for the
          p < 0.005 threshold + 'Benjamini & Hochberg (1995)'
          for the FDR correction methodology. Fixes reference
          list entries.
    B3 -- remove Section 6 'Visuals to Demonstrate the Insights'
          entirely (everything from that H2 to the end of the
          doc).

  Appendix:
    A1 -- same Benjamin vs Benjamini audit (Sections C and E).
    A2 -- crisis-window dagger: strip '†' from every cell in
          Table F1 + drop the footnote definition paragraph.
          (Justification: for the canonical strategy set every
          strategy fully covers every named crisis window, so
          the daggers are non-information. A future
          regeneration with a partial-overlap strategy would
          want the daggers reintroduced -- inspect before
          re-running in that case.)
    A3 -- ensure Table D1 lives inside the Section D block.
          Moves the caption + table if it's been emitted in a
          separate evidence-tables block at the end.
    A4 -- add 'Excess Return vs Benchmark' column to Table B1
          computed as strategy CAGR - 8.88%.

SUMMARY -- printed on completion, listing per-transform edit
counts and the new version of each draft.

EXIT CODES
  0  every transform applied (or already-applied)
  1  unexpected error -- nothing committed
  2  one or both target drafts were not found
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from copy import deepcopy
from typing import Any

import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("patch_document_content")


# Benchmark CAGR for Table B1's Excess Return column (per user
# spec). Surface as a constant so a future re-run with a
# different benchmark is one-line change.
BENCHMARK_CAGR_PCT = 8.88


# ─── TipTap node helpers ───────────────────────────────────────────────────

def _node_text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    if node.get("text") is not None:
        return str(node["text"])
    return "".join(
        _node_text(c) for c in (node.get("content") or []))


def _is_heading(node: Any, level: int | None = None) -> bool:
    if not isinstance(node, dict) or node.get("type") != "heading":
        return False
    if level is None:
        return True
    return (node.get("attrs") or {}).get("level") == level


def _content_to_text(doc: dict) -> str:
    """Flatten content_json into the content_text shape the
    backend's save flow already uses."""
    nodes = (doc.get("content") or []) if isinstance(doc, dict) else []
    out = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if n.get("type") == "table":
            rows = []
            for row in (n.get("content") or []):
                cells = [_node_text(c)
                         for c in (row.get("content") or [])]
                rows.append(" | ".join(c.strip() for c in cells))
            out.append("\n".join(rows))
            continue
        t = _node_text(n).strip()
        if t:
            out.append(t)
    return "\n\n".join(out)


# ─── Transforms ────────────────────────────────────────────────────────────

def transform_b1_remove_numbered_h1(doc: dict) -> int:
    """Drop H1 'N. Title' nodes that immediately precede an H2
    'Section N: ...' for the same N. Mutates doc in place; returns
    the count of removed H1s."""
    nodes = list(doc.get("content") or [])
    h1_re = re.compile(r"^\s*(\d+)\.\s+")
    h2_re = re.compile(r"^\s*Section\s+(\d+)[:\s]")
    out: list = []
    removed = 0
    i = 0
    while i < len(nodes):
        n = nodes[i]
        if _is_heading(n, 1):
            m1 = h1_re.match(_node_text(n))
            if m1:
                j = i + 1
                # skip blank paragraphs to the next real node
                while j < len(nodes):
                    nxt = nodes[j]
                    if (isinstance(nxt, dict)
                            and nxt.get("type") == "paragraph"
                            and not _node_text(nxt).strip()):
                        j += 1
                        continue
                    break
                if j < len(nodes) and _is_heading(nodes[j], 2):
                    m2 = h2_re.match(_node_text(nodes[j]))
                    if m2 and m1.group(1) == m2.group(1):
                        removed += 1
                        i += 1
                        continue
        out.append(n)
        i += 1
    doc["content"] = out
    return removed


_BENJAMIN_FIXES: list[tuple[str, str, str]] = [
    # Wrong: 'Benjamin et al. (2018) FDR correction' -- FDR is
    # from Benjamini & Hochberg 1995, not the 2018 threshold
    # paper.
    (r"Benjamin et al\.?,?\s*\(?2018\)?\s+(?=FDR)",
     "Benjamini & Hochberg (1995) ",
     "swap Benjamin->Benjamini before 'FDR'"),
    (r"Benjamin et al\.?,?\s*\(?2018\)?\s+(?=false discovery)",
     "Benjamini & Hochberg (1995) ",
     "swap Benjamin->Benjamini before 'false discovery'"),
    # Wrong: 'Benjamin-Hochberg' (hyphenated, missing -i suffix).
    (r"\bBenjamin[-‐-―]Hochberg\b",
     "Benjamini-Hochberg",
     "fix 'Benjamin-Hochberg' -> 'Benjamini-Hochberg'"),
    # Wrong: 'Benjamini et al., 2018' (the 2018 threshold paper
    # was authored by Daniel Benjamin et al., not Yoav Benjamini).
    (r"\bBenjamini et al\.?,?\s*\(?2018\)?",
     "Benjamin et al. (2018)",
     "swap Benjamini->Benjamin for the 2018 threshold paper"),
    # Reference list: the FDR paper's first author is Yoav
    # Benjamini, not 'Benjamin, Y.'.
    (r"\bBenjamin,\s*Y\.?\s*,?\s*&\s*Hochberg",
     "Benjamini, Y., & Hochberg",
     "fix reference list 'Benjamin, Y. & Hochberg'"),
]


def transform_benjamin_citations(doc: dict) -> list[str]:
    """Walk every text run and apply the Benjamin vs Benjamini
    fixes. Returns a list of edit descriptions (one per pattern
    that matched, with count)."""
    edits: list[str] = []

    def _patch(s: str) -> str:
        new = s
        for pat, repl, label in _BENJAMIN_FIXES:
            replaced, n = re.subn(pat, repl, new,
                                  flags=re.IGNORECASE)
            if n:
                edits.append(f"{label} (x{n})")
                new = replaced
        return new

    def _walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("text") is not None:
            node["text"] = _patch(node["text"])
        for c in (node.get("content") or []):
            _walk(c)

    for n in (doc.get("content") or []):
        _walk(n)
    return edits


def transform_b3_remove_section_6(doc: dict) -> int:
    """Drop every node from '## Section 6: ...' (or '# 6. Visuals
    ...' if B1 hasn't run yet) onward. Returns the count of
    removed nodes."""
    nodes = list(doc.get("content") or [])
    sec6_re = re.compile(
        r"^\s*(?:Section\s+6\b|6\.\s+Visuals\s+to\s+Demonstrate)",
        re.IGNORECASE)
    cut_at = None
    for i, n in enumerate(nodes):
        if (_is_heading(n, 1) or _is_heading(n, 2)) \
                and sec6_re.match(_node_text(n)):
            cut_at = i
            break
    if cut_at is None:
        return 0
    removed = len(nodes) - cut_at
    doc["content"] = nodes[:cut_at]
    return removed


def transform_a2_dagger_footnote(doc: dict) -> int:
    """Strip '†' from every cell + drop the footnote definition
    paragraph. Returns total daggers + footnote nodes removed."""
    removed = 0

    def _walk(node: Any) -> None:
        nonlocal removed
        if not isinstance(node, dict):
            return
        if node.get("text") is not None:
            new, n = re.subn(r"†", "", node["text"])
            if n:
                removed += n
                node["text"] = new
        for c in (node.get("content") or []):
            _walk(c)

    nodes = list(doc.get("content") or [])
    out: list = []
    footnote_re = re.compile(
        r"†\s+(indicates|flags|denotes|symbol)\s+",
        re.IGNORECASE)
    for n in nodes:
        if isinstance(n, dict) and n.get("type") == "paragraph":
            if footnote_re.match(_node_text(n).strip()):
                removed += 1
                continue
        _walk(n)
        out.append(n)
    doc["content"] = out
    return removed


def transform_a3_move_table_d1(doc: dict) -> bool:
    """If Table D1 is outside the Section D block, splice it in.
    Returns True if a move occurred."""
    nodes = list(doc.get("content") or [])

    def _section_heading_re(letter: str) -> re.Pattern:
        return re.compile(
            rf"^\s*(?:Section\s+{letter}\b|{letter}\.\s+)",
            re.IGNORECASE)

    d_heading = _section_heading_re("D")
    other_section = re.compile(
        r"^\s*(?:Section\s+[A-Z]\b|[A-Z]\.\s+)",
        re.IGNORECASE)

    d_start = None
    d_end = len(nodes)
    for i, n in enumerate(nodes):
        if (_is_heading(n, 1) or _is_heading(n, 2)) \
                and d_heading.match(_node_text(n)):
            d_start = i
        elif (d_start is not None
                and (_is_heading(n, 1) or _is_heading(n, 2))
                and other_section.match(_node_text(n))
                and not d_heading.match(_node_text(n))):
            d_end = i
            break
    if d_start is None:
        return False

    caption_re = re.compile(r"\bTable\s+D1\b", re.IGNORECASE)
    caption_idx = None
    table_idx = None
    for i, n in enumerate(nodes):
        if (isinstance(n, dict)
                and n.get("type") in ("paragraph", "heading")
                and caption_re.search(_node_text(n))):
            caption_idx = i
            j = i + 1
            while j < len(nodes):
                if (isinstance(nodes[j], dict)
                        and nodes[j].get("type") == "table"):
                    table_idx = j
                    break
                if (isinstance(nodes[j], dict)
                        and nodes[j].get("type") == "paragraph"
                        and not _node_text(nodes[j]).strip()):
                    j += 1
                    continue
                break
            break

    if caption_idx is None or table_idx is None:
        return False
    # Already inside Section D block -- nothing to do.
    if d_start < caption_idx < d_end:
        return False

    block = nodes[caption_idx:table_idx + 1]
    without = nodes[:caption_idx] + nodes[table_idx + 1:]
    # Re-locate the section boundary on the mutated list.
    new_d_end = len(without)
    new_d_start = None
    for i, n in enumerate(without):
        if (_is_heading(n, 1) or _is_heading(n, 2)) \
                and d_heading.match(_node_text(n)):
            new_d_start = i
        elif (new_d_start is not None
                and (_is_heading(n, 1) or _is_heading(n, 2))
                and other_section.match(_node_text(n))
                and not d_heading.match(_node_text(n))):
            new_d_end = i
            break
    doc["content"] = (
        without[:new_d_end] + block + without[new_d_end:])
    return True


def transform_a4_excess_return_column(doc: dict) -> int:
    """Add 'Excess Return vs Benchmark' column to Table B1.
    Returns count of data rows updated, or -1 when the column or
    table heuristics did not match (transform skipped).
    Idempotent: returns 0 when the column already exists."""
    nodes = doc.get("content") or []
    caption_re = re.compile(r"\bTable\s+B1\b", re.IGNORECASE)

    table_idx = None
    for i, n in enumerate(nodes):
        if isinstance(n, dict) and n.get("type") == "table":
            for k in range(max(0, i - 5), i):
                if (isinstance(nodes[k], dict)
                        and caption_re.search(_node_text(nodes[k]))):
                    table_idx = i
                    break
        if table_idx is not None:
            break
    if table_idx is None:
        log.warning(
            "A4 -- Table B1 not located; leaving content "
            "unchanged.")
        return -1

    table = nodes[table_idx]
    rows = list(table.get("content") or [])
    if not rows:
        return 0
    header_row = rows[0]
    header_cells = list(header_row.get("content") or [])
    header_texts = [_node_text(c).strip() for c in header_cells]

    if any("Excess Return" in h for h in header_texts):
        log.info(
            "A4 -- Excess Return column already present (no-op).")
        return 0

    cagr_idx = None
    for ci, h in enumerate(header_texts):
        if re.fullmatch(
                r"\s*CAGR\s*(?:\(\s*%\s*\))?\s*", h,
                flags=re.IGNORECASE):
            cagr_idx = ci
            break
    if cagr_idx is None:
        log.warning(
            "A4 -- CAGR column not located in Table B1 header "
            "(headers seen: %s); transform skipped.",
            header_texts)
        return -1

    def _clone(src: dict, new_text: str) -> dict:
        cell = deepcopy(src)
        cell["content"] = [{
            "type": "paragraph",
            "content": [{"type": "text", "text": new_text}],
        }]
        return cell

    header_cells.append(
        _clone(header_cells[-1], "Excess Return vs Benchmark"))
    header_row["content"] = header_cells

    updated = 0
    for row in rows[1:]:
        cells = list(row.get("content") or [])
        if len(cells) <= cagr_idx:
            continue
        cagr_text = _node_text(cells[cagr_idx]).strip()
        m = re.search(r"(-?\d+(?:\.\d+)?)", cagr_text)
        if not m:
            cells.append(_clone(cells[-1], "[DATA PENDING]"))
        else:
            cagr = float(m.group(1))
            excess = cagr - BENCHMARK_CAGR_PCT
            sign = "+" if excess > 0 else ""
            cells.append(
                _clone(cells[-1], f"{sign}{excess:.2f}%"))
        row["content"] = cells
        updated += 1
    return updated


# ─── DB + driver ───────────────────────────────────────────────────────────

async def _fetch_current(
        conn: asyncpg.Connection, document_type: str) -> dict | None:
    row = await conn.fetchrow(
        "SELECT id, content_json, version "
        "FROM editor_drafts "
        "WHERE document_type = $1 "
        "AND is_current = TRUE AND is_deleted = FALSE "
        "LIMIT 1",
        document_type)
    if row is None:
        return None
    cj = row["content_json"]
    if isinstance(cj, str):
        cj = json.loads(cj)
    return {"id": row["id"], "content_json": cj,
            "version": row["version"]}


async def _commit_draft(
        conn: asyncpg.Connection, draft_id: int,
        content_json: dict, content_text: str) -> int:
    """Update the row + return the new version."""
    new_version = await conn.fetchval(
        "UPDATE editor_drafts SET "
        "content_json = $2::jsonb, content_text = $3, "
        "version = version + 1, updated_at = NOW() "
        "WHERE id = $1 "
        "RETURNING version",
        draft_id, json.dumps(content_json), content_text)
    return new_version


def _normalise_dsn(raw: str) -> str:
    """asyncpg uses 'postgresql://' or 'postgres://' (no
    '+asyncpg' driver suffix). Strip the SQLAlchemy-style driver
    qualifier if present so the same DATABASE_URL the FastAPI
    backend uses works here."""
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://",
                  raw)


async def main(dry_run: bool) -> int:
    dsn_raw = os.environ.get("DATABASE_URL")
    if not dsn_raw:
        log.error(
            "DATABASE_URL is not set. Run on the Render shell "
            "(which exports it) or set it locally before running.")
        return 1
    dsn = _normalise_dsn(dsn_raw)

    summary: dict[str, dict[str, Any]] = {
        "brief": {}, "appendix": {},
    }
    rc = 0

    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            # ── Brief ────────────────────────────────────────────
            brief = await _fetch_current(conn, "executive_brief")
            if brief is None:
                log.error(
                    "Brief -- no current executive_brief draft.")
                rc = 2
            else:
                log.info(
                    "Brief -- patching draft id=%s version=%s",
                    brief["id"], brief["version"])
                doc = deepcopy(brief["content_json"]) or {}
                if not isinstance(doc, dict):
                    log.error(
                        "Brief content_json is not a dict; "
                        "skipping.")
                    rc = 1
                else:
                    b1 = transform_b1_remove_numbered_h1(doc)
                    log.info(
                        "  B1: removed %d duplicate H1 "
                        "heading(s).", b1)
                    b2 = transform_benjamin_citations(doc)
                    for e in b2:
                        log.info("  B2: %s", e)
                    if not b2:
                        log.info(
                            "  B2: no Benjamin/Benjamini swaps "
                            "needed.")
                    b3 = transform_b3_remove_section_6(doc)
                    log.info(
                        "  B3: removed %d node(s) for Section "
                        "6.", b3)
                    new_text = _content_to_text(doc)
                    new_version = brief["version"] + 1
                    if not dry_run:
                        new_version = await _commit_draft(
                            conn, brief["id"], doc, new_text)
                    summary["brief"] = {
                        "id": brief["id"],
                        "version_before": brief["version"],
                        "version_after": new_version,
                        "h1_removed": b1,
                        "citation_edits": b2,
                        "s6_nodes_removed": b3,
                    }

            # ── Appendix ─────────────────────────────────────────
            appx = await _fetch_current(
                conn, "analytical_appendix")
            if appx is None:
                log.error(
                    "Appendix -- no current analytical_appendix "
                    "draft.")
                rc = max(rc, 2)
            else:
                log.info(
                    "Appendix -- patching draft id=%s "
                    "version=%s",
                    appx["id"], appx["version"])
                doc = deepcopy(appx["content_json"]) or {}
                if not isinstance(doc, dict):
                    log.error(
                        "Appendix content_json is not a dict; "
                        "skipping.")
                    rc = 1
                else:
                    a1 = transform_benjamin_citations(doc)
                    for e in a1:
                        log.info("  A1: %s", e)
                    if not a1:
                        log.info(
                            "  A1: no Benjamin/Benjamini swaps "
                            "needed.")
                    a2 = transform_a2_dagger_footnote(doc)
                    log.info(
                        "  A2: stripped %d dagger / footnote "
                        "node(s).", a2)
                    a3 = transform_a3_move_table_d1(doc)
                    log.info(
                        "  A3: Table D1 %s.",
                        "moved into Section D block" if a3
                        else "already in place / not found")
                    a4 = transform_a4_excess_return_column(doc)
                    if a4 == -1:
                        log.warning(
                            "  A4: skipped (heuristic miss; "
                            "inspect Table B1 manually).")
                    elif a4 == 0:
                        log.info(
                            "  A4: already patched (no-op).")
                    else:
                        log.info(
                            "  A4: added Excess Return column "
                            "to %d data row(s).", a4)
                    new_text = _content_to_text(doc)
                    new_version = appx["version"] + 1
                    if not dry_run:
                        new_version = await _commit_draft(
                            conn, appx["id"], doc, new_text)
                    summary["appendix"] = {
                        "id": appx["id"],
                        "version_before": appx["version"],
                        "version_after": new_version,
                        "citation_edits": a1,
                        "daggers_removed": a2,
                        "d1_moved": a3,
                        "b1_rows_updated": a4,
                    }

            if dry_run:
                raise _DryRunAbort
    except _DryRunAbort:
        log.info("Dry-run -- transaction rolled back.")
    finally:
        await conn.close()

    # ── Summary ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("PATCH SUMMARY")
    print("=" * 60)
    for key, info in summary.items():
        print(f"\n{key.upper()}")
        if not info:
            print("  (no draft found)")
            continue
        for k, v in info.items():
            print(f"  {k}: {v}")
    if dry_run:
        print("\n(dry-run -- no changes were committed)")
    print("=" * 60)
    return rc


class _DryRunAbort(Exception):
    """Sentinel to bail out of the transaction cleanly."""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Patch the current executive_brief and "
            "analytical_appendix drafts in editor_drafts."))
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Log every transform but roll back at the end. "
             "Useful for inspecting output before a real run.")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.dry_run)))
