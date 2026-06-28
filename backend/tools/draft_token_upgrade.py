"""tools/draft_token_upgrade.py -- June 28 2026.

Dual-mode token storage upgrade pass (PR-DM-Lite).

CONTEXT
  At generation time, the numeric substitution layer replaces
  {{TOKEN}} placeholders with resolved values (e.g. "0.86"). The
  resolved value is baked into editor_drafts.content_json as a
  plain TipTap text node. The token reference is permanently
  lost.

  This makes light-refresh-driven re-substitution heuristic:
  the rewriter has to scan the document for the resolved value
  string + look it up in editor_drafts.value_manifest to find
  out which token produced it. Same-value collisions (two tokens
  producing the same string, e.g. "0.43" for both BENCHMARK
  Sharpe + something else) collapse in the manifest to one
  entry, breaking the reverse lookup.

DUAL-MODE FIX
  Upgrade plain-text numeric nodes to structured token_value
  nodes that carry both the resolved value AND the source token
  reference:

    PLAIN NODE (legacy):
      {"type": "text", "text": "0.86"}

    TOKEN_VALUE NODE (upgraded):
      {"type": "token_value", "attrs": {
        "token":       "{{OOS_SHARPE_BLEND}}",
        "resolved":    "0.86",
        "resolved_at": "2026-06-21T...",
        "data_hash":   "c421fb89..."
      }}

  Subsequent rewrites become exact (node.attrs.token lookup
  against the current substitution table) instead of heuristic.
  The council numeric review becomes exact equality check
  instead of plausibility reasoning.

UPGRADE PASS (this module)
  upgrade_content_json_to_token_values(content_json,
                                       value_manifest)
    Walks every text node in content_json. For each text node:
      1. For every (value, {token, data_hash, generated_at}) in
         value_manifest:
      2. Find every word-boundary occurrence of the value string
         inside the text node's content.
      3. Split the text node at each occurrence into
         [before_text, token_value_node, after_text].
      4. Return the rebuilt subtree.
    Skips token_value nodes that already exist (idempotent).
    Word-boundary regex prevents "0.86" from matching inside
    "10.86" or "0.860".

  Same-value collapse caveat: value_manifest is keyed by value
  string, so when two tokens produced the same value at
  generation time only ONE manifest entry was kept ("last write
  wins" per build_value_manifest). The upgrade pass assigns
  the surviving entry's token to the FIRST occurrence in the
  document; later occurrences also get the same token tag,
  which may be incorrect for one of them. Flagged in the review
  panel as value_ambiguous; operator manually verifies.

SAFETY
  Pre-mutation snapshot: callers must save content_json into
  editor_drafts.pre_migration_content_json BEFORE invoking this
  function so the admin revert endpoint can restore the
  legacy state.

  migration_run flag: callers must set it to TRUE after a
  successful upgrade so re-triggers no-op.
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


# ── Token-placeholder matcher (June 28 2026 fix) ────────────────
#
# CRITICAL FIX: the original PR-DM-Lite matcher walked text nodes
# looking for occurrences of the resolved-value strings from
# value_manifest (reverse-lookup). That produced systemic false
# positives -- any prose number "2" / "10" / "15" / "20" that
# happened to match a manifest value (PLAY_BY_PLAY_VALUE_ADD,
# N_STRATEGIES, etc) got wrongly tagged. Reference citation years,
# ticker symbols, section numbers, sensitivity-bp figures all
# got over-matched.
#
# Per operator directive (June 28): matching is now EXCLUSIVELY on
# {{TOKEN_NAME}} placeholders that survive in the text node. Only
# unsubstituted token strings can produce a token_value node. The
# reverse lookup is never used.
#
# ARCHITECTURAL CONSEQUENCE: if generation-time substitution baked
# every {{TOKEN}} into a resolved value before writing content_json,
# the upgrade pass will produce 0 token_value nodes (there are no
# token placeholders left to find). The dual-mode upgrade only
# fires where {{TOKEN}} placeholders survive to content_json --
# e.g. figure captions or tables where the substitution layer
# defers to export-time _apply_substitutions. Operator path to
# verify after deploy: re-run upgrade-all-drafts; nodes_upgraded
# in the response shows how many places had unsubstituted tokens
# in content_json.

# Recognised token pattern: {{UPPERCASE_TOKEN_WITH_UNDERSCORES_AND_DIGITS}}
_TOKEN_PATTERN = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")


def _split_text_node(
    text: str,
    valid_tokens: dict[str, dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Walks `text` for any {{TOKEN_NAME}} placeholder whose
    name matches an entry in the supplied token-to-manifest
    map. Returns a list of TipTap node dicts (text + token_value
    alternating) when at least one match was found, or None when
    no matches (so the caller can keep the original node
    untouched).

    valid_tokens is a {token_literal: manifest_entry} dict where
    token_literal is the full '{{TOKEN_NAME}}' string and
    manifest_entry carries token + value + data_hash +
    generated_at (the same shape returned by build_value_manifest,
    but rebuilt by the caller so each token is the lookup key
    rather than the value).

    Tokens found in text but NOT in valid_tokens are LEFT INTACT
    as plain text. This is the safety contract: a stray
    {{UNKNOWN_TOKEN}} in prose never gets wrapped, never gets
    a fabricated cache hash."""
    matches = list(_TOKEN_PATTERN.finditer(text))
    if not matches:
        return None
    # Filter to matches whose token literal is in valid_tokens.
    valid_matches = [
        m for m in matches if m.group(0) in valid_tokens
    ]
    if not valid_matches:
        return None
    out: list[dict[str, Any]] = []
    cursor = 0
    for m in valid_matches:
        start, end = m.span()
        if start > cursor:
            out.append({
                "type": "text",
                "text": text[cursor:start],
            })
        token_literal = m.group(0)
        entry = valid_tokens[token_literal]
        out.append({
            "type": "token_value",
            "attrs": {
                "token":       token_literal,
                # The resolved value comes from the manifest
                # entry (the key in value_manifest was the
                # resolved string at generation time). Falls
                # back to empty if the manifest schema is
                # malformed.
                "resolved":    str(entry.get("resolved", "")),
                "data_hash":   entry.get("data_hash", ""),
                "resolved_at": entry.get("generated_at", ""),
            },
        })
        cursor = end
    if cursor < len(text):
        out.append({
            "type": "text",
            "text": text[cursor:],
        })
    return out


# ── Tree walker ──────────────────────────────────────────────────


def _walk_and_upgrade(
    node: Any,
    valid_tokens: dict[str, dict[str, Any]],
    stats: dict[str, int],
) -> Any:
    """Recursive walk. Returns the (possibly-modified) node.
    Mutations only happen for type='text' leaves whose text
    contains one or more {{TOKEN_NAME}} placeholders whose
    token literal is in valid_tokens -- everything else is
    preserved verbatim including marks, attrs, and unknown node
    types (e.g. token_value nodes from a re-run).

    valid_tokens shape: {token_literal: manifest_entry} where
    token_literal is '{{NAME}}' and entry carries resolved /
    data_hash / generated_at."""
    if not isinstance(node, dict):
        return node
    ntype = node.get("type")
    # Skip nodes that have already been upgraded.
    if ntype == "token_value":
        stats["already_upgraded"] += 1
        return node
    # For text leaves, attempt to split.
    if ntype == "text":
        text = node.get("text") or ""
        if not text:
            return node
        # Marks (bold/italic/etc) wrap whole text spans -- a
        # mid-mark token_value would awkwardly split the mark.
        # In practice token placeholders live in unmarked spans;
        # preserve marked text untouched as a safety net.
        if node.get("marks"):
            return node
        replaced = _split_text_node(text, valid_tokens)
        if replaced is None:
            return node
        # Split produced 1+ token_value nodes; count them.
        new_token_count = sum(
            1 for n in replaced
            if isinstance(n, dict)
            and n.get("type") == "token_value")
        stats["nodes_upgraded"] += new_token_count
        # Return a wrapper list so the caller can splice into
        # the parent's content array (handled below).
        return {"__splice__": replaced}
    # For container nodes, recurse into content + splice in
    # any text-node-splits.
    content = node.get("content")
    if isinstance(content, list):
        new_content: list[Any] = []
        for child in content:
            walked = _walk_and_upgrade(
                child, valid_tokens, stats)
            if (isinstance(walked, dict)
                    and "__splice__" in walked):
                new_content.extend(walked["__splice__"])
            else:
                new_content.append(walked)
        # Construct a new dict so we don't mutate the input
        # (callers may want to keep the original for the
        # pre-migration snapshot column).
        new_node = dict(node)
        new_node["content"] = new_content
        return new_node
    return node


# ── Public entry point ──────────────────────────────────────────


def upgrade_content_json_to_token_values(
    content_json: dict[str, Any],
    value_manifest: dict[str, dict[str, Any]] | None,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Upgrade a draft's content_json from plain-text numeric
    values to token_value nodes.

    Args:
      content_json:   the TipTap doc tree from
                      editor_drafts.content_json.
      value_manifest: the per-draft snapshot from
                      editor_drafts.value_manifest, shape
                      {value: {token, data_hash, generated_at}}.
                      Pass None for legacy drafts that pre-date
                      migration 057 -- the function then no-ops
                      and returns (content_json, {} stats).

    Returns:
      (new_content_json, stats) where stats carries:
        - manifest_entries:   total entries in the input manifest
        - nodes_upgraded:     count of token_value nodes inserted
        - already_upgraded:   count of pre-existing token_value
                              nodes encountered (re-run safety)
        - upgraded:           bool, True iff nodes_upgraded > 0

    The caller is responsible for:
      1. Snapshotting content_json into
         editor_drafts.pre_migration_content_json BEFORE
         invoking this function.
      2. Setting editor_drafts.migration_run = TRUE after
         persisting the returned new_content_json.

    Idempotent: re-invoking on an already-upgraded document
    walks the existing token_value nodes (counted in stats but
    not re-processed) and finds no plain-text numerics left to
    upgrade. nodes_upgraded == 0 on a re-run.
    """
    stats: dict[str, int] = {
        "manifest_entries": 0,
        "nodes_upgraded":   0,
        "already_upgraded": 0,
        "upgraded":         False,
    }
    if not isinstance(content_json, dict):
        log.warning("draft_token_upgrade_skipped_non_dict")
        return content_json, stats
    if not isinstance(value_manifest, dict) or not value_manifest:
        log.info("draft_token_upgrade_skipped_no_manifest")
        return content_json, stats
    stats["manifest_entries"] = len(value_manifest)
    # June 28 2026 -- token-placeholder matcher. Build a
    # {token_literal: entry} index where token_literal is the
    # full '{{NAME}}' string. The manifest is keyed by RESOLVED
    # VALUE; we invert + carry the value as entry["resolved"]
    # so the token_value node attrs get populated correctly
    # (token, resolved, data_hash, resolved_at).
    valid_tokens: dict[str, dict[str, Any]] = {}
    for resolved_value, entry in value_manifest.items():
        if not (isinstance(entry, dict)
                and isinstance(resolved_value, str)):
            continue
        token_literal = entry.get("token")
        if not isinstance(token_literal, str):
            continue
        if not (token_literal.startswith("{{")
                and token_literal.endswith("}}")):
            continue
        # Last-write-wins on collision (rare -- two manifest
        # entries claiming the same token name shouldn't happen
        # because build_value_manifest writes one entry per
        # value+token pair). The resolved value the upgrade
        # writes is the one from the chosen entry.
        valid_tokens[token_literal] = {
            "resolved":     resolved_value,
            "data_hash":    entry.get("data_hash", ""),
            "generated_at": entry.get("generated_at", ""),
        }
    if not valid_tokens:
        log.warning(
            "draft_token_upgrade_skipped_empty_token_index")
        return content_json, stats
    new_content_json = _walk_and_upgrade(
        content_json, valid_tokens, stats)
    stats["upgraded"] = stats["nodes_upgraded"] > 0
    log.info(
        "draft_token_upgrade_complete",
        manifest_entries=stats["manifest_entries"],
        nodes_upgraded=stats["nodes_upgraded"],
        already_upgraded=stats["already_upgraded"])
    return new_content_json, stats


# ── Reverse direction -- exact rewriter (post-upgrade) ───────────


def apply_token_updates(
    content_json: dict[str, Any],
    substitution_table: dict[str, str],
    data_hash: str,
    selected_tokens: set[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """For an already-upgraded content_json (token_value nodes
    in place), update each node's attrs.resolved to the value
    in the supplied substitution_table.

    Args:
      content_json:        upgraded TipTap doc
      substitution_table:  {token: current_value} -- the canonical
                           freeze-aware substitution table
      data_hash:           the freeze-effective hash; written to
                           each updated node's attrs.data_hash
      selected_tokens:     when supplied, only update nodes whose
                           attrs.token is in this set. Used by the
                           review panel's "apply selected updates"
                           flow. None = apply to every changed
                           token.

    Returns:
      (new_content_json, updates_applied) where updates_applied
      is a list of {token, old_value, new_value, data_hash,
      applied_at} entries for the audit log.

    Read-only when attrs.override is set -- an explicitly-
    overridden node is never auto-updated. Operator must clear
    the override first.
    """
    from datetime import datetime, timezone

    updates_applied: list[dict[str, Any]] = []
    applied_at = datetime.now(timezone.utc).isoformat()

    def _walk(node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        ntype = node.get("type")
        if ntype == "token_value":
            attrs = dict(node.get("attrs") or {})
            token = attrs.get("token")
            if not token:
                return node
            # Overridden nodes never auto-update.
            if attrs.get("override") is not None:
                return node
            # Token selection gate.
            if (selected_tokens is not None
                    and token not in selected_tokens):
                return node
            new_value = substitution_table.get(token)
            if new_value is None:
                return node
            old_value = attrs.get("resolved")
            if old_value == new_value:
                return node
            # Mutate.
            attrs["resolved"] = new_value
            attrs["resolved_at"] = applied_at
            attrs["data_hash"] = data_hash
            new_node = dict(node)
            new_node["attrs"] = attrs
            updates_applied.append({
                "token":      token,
                "old_value":  old_value,
                "new_value":  new_value,
                "data_hash":  data_hash,
                "applied_at": applied_at,
            })
            return new_node
        content = node.get("content")
        if isinstance(content, list):
            new_content = [_walk(c) for c in content]
            new_node = dict(node)
            new_node["content"] = new_content
            return new_node
        return node

    new_content_json = _walk(content_json)
    log.info(
        "apply_token_updates_complete",
        updates_count=len(updates_applied),
        data_hash=data_hash[:8] if data_hash else "")
    return new_content_json, updates_applied


# ── Review summary -- shape the frontend renders ─────────────────


def build_review_summary(
    content_json: dict[str, Any],
    substitution_table: dict[str, str],
) -> list[dict[str, Any]]:
    """Walk content_json + return one summary entry per
    token_value node found. Each entry carries the data the
    review panel needs to render the row.

    Returns list of:
      {
        token:           "{{...}}",
        current_value:   str -- attrs.resolved (or override),
        cache_value:     str -- substitution_table[token],
        match:           bool -- current_value == cache_value,
        overridden:      bool -- attrs.override is not None,
        last_updated:    str -- attrs.resolved_at,
        data_hash:       str -- attrs.data_hash,
      }
    """
    out: list[dict[str, Any]] = []

    def _walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "token_value":
            attrs = node.get("attrs") or {}
            token = attrs.get("token") or ""
            current = attrs.get("override") or attrs.get("resolved")
            cache = substitution_table.get(token)
            out.append({
                "token":         token,
                "current_value": current,
                "cache_value":   cache,
                "match":         current == cache,
                "overridden":    attrs.get("override") is not None,
                "last_updated":  attrs.get("resolved_at"),
                "data_hash":     attrs.get("data_hash"),
            })
        content = node.get("content")
        if isinstance(content, list):
            for c in content:
                _walk(c)

    _walk(content_json)
    return out
