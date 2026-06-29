"""backend/scripts/audit_token_value_nodes.py -- one-shot June 28 2026.

Exports every token_value node from a list of editor drafts as a
CSV review file. Operator uses the output to manually mark
correct vs false-positive nodes before any surgical cleanup runs.

USAGE (on Render shell):

    cd backend
    python -m scripts.audit_token_value_nodes
    # writes /tmp/token_value_audit_<timestamp>.csv
    # prints the path on stdout

Default drafts: 60 (analytical_appendix, thaob) + 64
(executive_brief, thaob). Override via CLI args:

    python -m scripts.audit_token_value_nodes 60 64 71

CSV columns:
  draft_id, document_type, owner_email, token, resolved,
  before_60chars, after_60chars

before_60chars / after_60chars carry the last/first 60 chars of
prose from the immediately-preceding / immediately-following
sibling text node, so the operator can spot whether the node is
embedded in a sensible numeric context vs an unrelated piece of
prose.

CONTRACT
  - Read-only. Connects to DATABASE_URL via asyncpg.
  - Never writes back to editor_drafts.
  - Walks content_json depth-first; for each token_value node
    found, captures (token, resolved, before, after).
  - 'before' = preceding sibling's text content (last 60 chars).
  - 'after' = following sibling's text content (first 60 chars).
  - Token_value nodes nested inside marks / lists / blockquotes
    still report the surrounding text correctly because the walk
    captures siblings at every level.
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Iterator


sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))


# ── Tree walker ────────────────────────────────────────────────


def _text_of(node: Any) -> str:
    """Flattened plain text of a node + its descendants. Used to
    build the 'before' / 'after' context strings."""
    if not isinstance(node, dict):
        return ""
    ntype = node.get("type")
    if ntype == "token_value":
        # Treat token_value text content as the resolved value.
        # In context strings, an adjacent token_value just renders
        # as its resolved string -- so the operator sees the prose
        # as it'd appear in the editor.
        attrs = node.get("attrs") or {}
        return str(attrs.get("override") or attrs.get("resolved") or "")
    if node.get("text"):
        return str(node["text"])
    parts = [_text_of(c) for c in (node.get("content") or [])]
    return "".join(parts)


def _walk_token_value_nodes(
    node: Any,
) -> Iterator[tuple[dict[str, Any], str, str]]:
    """Yield (node, before_context, after_context) for every
    token_value node in the tree.

    before_context = last 60 chars of the concatenated text from
    all left-sibling subtrees at the parent level.
    after_context = first 60 chars of the concatenated text from
    all right-sibling subtrees at the parent level.

    Walks containers (paragraph / heading / list / blockquote /
    table / etc) at every depth and reports siblings within each
    container."""
    if not isinstance(node, dict):
        return
    content = node.get("content")
    if isinstance(content, list):
        # Sibling-level scan: for each child that is a
        # token_value, capture its surrounding context from the
        # adjacent children in this same content array.
        for i, child in enumerate(content):
            if isinstance(child, dict) and (
                    child.get("type") == "token_value"):
                # Concatenate every text-bearing sibling BEFORE
                # this position + take the last 60 chars.
                before = "".join(
                    _text_of(content[j]) for j in range(i)
                )
                after = "".join(
                    _text_of(content[j])
                    for j in range(i + 1, len(content))
                )
                yield (
                    child,
                    before[-60:] if before else "",
                    after[:60] if after else "",
                )
        # Recurse into containers (paragraph carries text leaves,
        # but a nested table / list contains paragraphs that hold
        # the token_value nodes). The sibling-level scan above
        # handles in-paragraph context; recursion handles deeper
        # container nesting.
        for child in content:
            yield from _walk_token_value_nodes(child)


# ── DB fetch ──────────────────────────────────────────────────


async def _fetch_drafts(
    draft_ids: list[int],
) -> list[dict[str, Any]]:
    """Read draft rows from the DB. Returns list of
    {id, document_type, owner_email, content_json}."""
    from database import AsyncSessionLocal
    from sqlalchemy import text

    if AsyncSessionLocal is None:
        raise RuntimeError(
            "AsyncSessionLocal unavailable -- DATABASE_URL "
            "must be set when running this script.")

    out: list[dict[str, Any]] = []
    async with AsyncSessionLocal() as s:
        rows = await s.execute(
            text(
                "SELECT id, document_type, owner_email, "
                "content_json "
                "FROM editor_drafts "
                "WHERE id = ANY(:ids) "
                "  AND is_current = true "
                "  AND is_deleted = false "
                "ORDER BY id"),
            {"ids": draft_ids})
        for row in rows.fetchall():
            draft_id, doc_type, owner, content_json = row
            # Parse JSON if returned as a string (driver-dependent).
            if isinstance(content_json, str):
                try:
                    content_json = json.loads(content_json)
                except json.JSONDecodeError:
                    content_json = {}
            out.append({
                "id":            draft_id,
                "document_type": doc_type,
                "owner_email":   owner,
                "content_json":  content_json or {},
            })
    return out


# ── Main ──────────────────────────────────────────────────────


def _scrub(s: str) -> str:
    """Collapse whitespace + strip newlines so the CSV row stays
    on a single line. Preserves character order so the operator
    can still match prose to the original document."""
    return " ".join((s or "").split())


async def _run(draft_ids: list[int]) -> str:
    drafts = await _fetch_drafts(draft_ids)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = f"/tmp/token_value_audit_{ts}.csv"

    total_rows = 0
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "draft_id", "document_type", "owner_email",
            "token", "resolved",
            "before_60chars", "after_60chars",
        ])
        for d in drafts:
            for node, before, after in (
                    _walk_token_value_nodes(d["content_json"])):
                attrs = node.get("attrs") or {}
                w.writerow([
                    d["id"],
                    d["document_type"],
                    d["owner_email"],
                    str(attrs.get("token", "")),
                    str(attrs.get("override")
                        or attrs.get("resolved") or ""),
                    _scrub(before),
                    _scrub(after),
                ])
                total_rows += 1

    print(f"Wrote {total_rows} token_value rows to {out_path}")
    return out_path


def _parse_ids(argv: list[str]) -> list[int]:
    """CLI args after the script name. Defaults to 60 + 64 when
    no args supplied."""
    if len(argv) <= 1:
        return [60, 64]
    out: list[int] = []
    for a in argv[1:]:
        try:
            out.append(int(a))
        except ValueError:
            print(f"Skipping non-integer arg: {a!r}",
                  file=sys.stderr)
    return out or [60, 64]


if __name__ == "__main__":
    ids = _parse_ids(sys.argv)
    print(f"Auditing token_value nodes for drafts: {ids}")
    asyncio.run(_run(ids))
