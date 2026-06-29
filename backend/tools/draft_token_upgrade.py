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
        # June 28 2026 (Fix 8a) -- previously skipped marked
        # text nodes entirely (bold / italic / etc.) which
        # silently left any {{TOKEN}} inside emphasized prose
        # un-upgraded. The brief writer occasionally emphasises
        # headline figures (**0.86 Sharpe**) which TipTap parses
        # into a single marked text node carrying the token.
        # Now: preserve the marks list onto each split-piece
        # (text fragments stay marked; token_value nodes carry
        # the same marks via attrs.marks for the NodeView's
        # downstream styling). Token presence dominates over
        # mark-preservation -- a token inside bold renders
        # correctly resolved, then styled bold by the NodeView.
        marks = node.get("marks")
        replaced = _split_text_node(text, valid_tokens)
        if replaced is None:
            return node
        if marks:
            # Re-attach the original marks to every split piece.
            replaced = [
                {**n, "marks": marks}
                if isinstance(n, dict) else n
                for n in replaced
            ]
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
    *,
    freeze_hash_override: str | None = None,
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
      freeze_hash_override: when supplied, overrides the
                      manifest entry's data_hash on every
                      generated token_value node. Used by the
                      _auto_upgrade hook to heal legacy
                      manifests that were stamped with a live-
                      rebuild hash (post-freeze cache rebuild
                      drift); the override ensures downstream
                      export-verification looks up cache rows
                      under the freeze-bearing hash. None
                      preserves the manifest entry's stamp
                      verbatim (the legacy behaviour).

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
        # June 29 2026 (manifest-hash PR) -- freeze-aware
        # data_hash stamp. When the manifest entry's data_hash
        # is a live-rebuild hash but submission_freeze is
        # active, override with the freeze hash so any
        # downstream consumer (NodeView attrs, export
        # verification) reading manifest.data_hash sees the
        # cache-row-bearing hash. This is a defensive write --
        # the walker itself doesn't lookup cache values, but
        # downstream code that consumes the resulting
        # token_value attrs (e.g. export verification at
        # tools.export_verification.verify_export_against_
        # cache) does, and a wrong data_hash on the node
        # causes silent verification misses.
        entry_hash = entry.get("data_hash", "") or ""
        # Freeze-hash override: when supplied + entry was stamped
        # with a different hash, swap to the freeze hash so the
        # generated token_value node's data_hash points at the
        # cache row where the resolved value actually lives.
        stamped_hash = entry_hash
        if (freeze_hash_override
                and entry_hash
                and entry_hash != freeze_hash_override):
            stamped_hash = freeze_hash_override
            log.info(
                "manifest_entry_hash_overridden_to_freeze",
                token=token_literal,
                from_hash=entry_hash[:8],
                to_hash=freeze_hash_override[:8])
        valid_tokens[token_literal] = {
            "resolved":     resolved_value,
            "data_hash":    stamped_hash,
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


# ── <unverified> tag walker (June 28 2026, PR #479) ──────────────
#
# When the hard-lock soft-fail wraps a surviving raw numeric, it
# emits literal "<unverified>VALUE</unverified>" substrings in the
# narrative text (academic_export.harness_narrative for brief /
# appendix, script_generation for script, _substitute_slide_content
# for deck). At persist time content_json contains TipTap text
# nodes whose `text` field carries the literal tag substring.
#
# For the in-editor NodeView to render, those substrings must be
# parsed out of the text nodes + replaced with structured
# `<unverified>` nodes carrying the raw value as an attribute.
# Walker shape mirrors _split_text_node above: scan + splice +
# return a list. Idempotent: re-running on already-upgraded
# content skips existing nodes.

_UNVERIFIED_TAG_RE = re.compile(
    r"<unverified>(.*?)</unverified>",
    re.DOTALL)


def _split_text_node_for_unverified(
    text: str,
) -> list[dict[str, Any]] | None:
    """Walk `text` for any <unverified>VALUE</unverified> tag.
    Returns a list of TipTap node dicts (text + unverified
    alternating) when at least one tag was found, or None when
    no tags so the caller can keep the original node untouched.

    Unverified node shape:
      {"type": "unverified", "attrs": {"value": "+0.5"}}

    The NodeView (frontend) reads attrs.value and renders a
    red/amber pill displaying the raw value with a click handler
    that opens the resolution popover."""
    matches = list(_UNVERIFIED_TAG_RE.finditer(text))
    if not matches:
        return None
    out: list[dict[str, Any]] = []
    cursor = 0
    for m in matches:
        start, end = m.span()
        if start > cursor:
            out.append({
                "type": "text",
                "text": text[cursor:start],
            })
        raw_value = (m.group(1) or "").strip()
        out.append({
            "type":  "unverified",
            "attrs": {"value": raw_value},
        })
        cursor = end
    if cursor < len(text):
        out.append({
            "type": "text",
            "text": text[cursor:],
        })
    return out


def upgrade_content_json_for_unverified_tags(
    content_json: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, int]]:
    """Upgrade a draft's content_json: scan every text node
    for <unverified>...</unverified> literal substrings + split
    into structured `unverified` nodes.

    Args:
      content_json: the TipTap doc tree from
                    editor_drafts.content_json.

    Returns:
      (new_content_json, stats) where stats carries:
        nodes_upgraded:    count of unverified nodes inserted
        already_upgraded:  count of pre-existing unverified
                           nodes encountered (re-run safety)
        upgraded:          bool, True iff nodes_upgraded > 0

    Idempotent. Mirrors upgrade_content_json_to_token_values
    shape so the auto-upgrade hook can run BOTH passes in
    sequence (token upgrade first to convert {{TOKEN}}
    placeholders, then unverified upgrade to convert tag
    substrings)."""
    stats: dict[str, int] = {
        "nodes_upgraded":   0,
        "already_upgraded": 0,
        "upgraded":         False,
    }
    if not isinstance(content_json, dict):
        return content_json, stats

    def _walk(node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        ntype = node.get("type")
        if ntype == "unverified":
            stats["already_upgraded"] += 1
            return node
        if ntype == "text":
            text = node.get("text") or ""
            if not text:
                return node
            replaced = _split_text_node_for_unverified(text)
            if replaced is None:
                return node
            marks = node.get("marks")
            if marks:
                replaced = [
                    {**n, "marks": marks}
                    if isinstance(n, dict)
                    and n.get("type") != "unverified"
                    else n
                    for n in replaced
                ]
            new_token_count = sum(
                1 for n in replaced
                if isinstance(n, dict)
                and n.get("type") == "unverified")
            stats["nodes_upgraded"] += new_token_count
            return {"__splice__": replaced}
        content = node.get("content")
        if isinstance(content, list):
            new_content: list[Any] = []
            for child in content:
                walked = _walk(child)
                if (isinstance(walked, dict)
                        and "__splice__" in walked):
                    new_content.extend(walked["__splice__"])
                else:
                    new_content.append(walked)
            new_node = dict(node)
            new_node["content"] = new_content
            return new_node
        return node

    new_content_json = _walk(content_json)
    stats["upgraded"] = stats["nodes_upgraded"] > 0
    return new_content_json, stats


def upgrade_canvas_slides_for_unverified_tags(
    content_json: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, int]]:
    """Deck-specific variant. The deck's content_json is the
    canvas-element schema (NOT TipTap):
      {"slides": [{"elements": [{"type": "text",
                                 "content": "...", ...}]}]}

    Walks each text element's `content` field for
    <unverified>...</unverified> tags. Two options:
      (a) Strip the tags + leave the raw value in `content`
          (canvas can't render an inline node mid-text).
      (b) Add a parallel `unverified_values` list on the
          element so the canvas editor's renderer can paint
          red borders around the affected values.

    This implementation goes with option (b): preserve the
    literal `<unverified>VALUE</unverified>` substring in
    `content` (canvas editor renders it as-is with surrounding
    text) AND populate
    `element["unverified"] = ["+0.5", "0.005", ...]` as a
    parallel list the canvas renderer can use to apply visual
    treatment.

    Stats shape matches the TipTap variant for parallel logging.
    """
    stats: dict[str, int] = {
        "nodes_upgraded":   0,
        "already_upgraded": 0,
        "upgraded":         False,
    }
    if not isinstance(content_json, dict):
        return content_json, stats
    slides = content_json.get("slides")
    if not isinstance(slides, list):
        return content_json, stats
    out_slides: list[Any] = []
    for slide in slides:
        if not isinstance(slide, dict):
            out_slides.append(slide)
            continue
        new_slide = dict(slide)
        elements = slide.get("elements")
        if isinstance(elements, list):
            new_elements: list[Any] = []
            for el in elements:
                if not isinstance(el, dict):
                    new_elements.append(el)
                    continue
                content_str = el.get("content")
                if not isinstance(content_str, str):
                    new_elements.append(el)
                    continue
                matches = list(
                    _UNVERIFIED_TAG_RE.finditer(content_str))
                if not matches:
                    new_elements.append(el)
                    continue
                # Found unverified tags in this element's text.
                values: list[str] = []
                for m in matches:
                    v = (m.group(1) or "").strip()
                    if v and v not in values:
                        values.append(v)
                stats["nodes_upgraded"] += len(values)
                new_el = dict(el)
                # Keep the literal tag substring in content so
                # the canvas renderer can do its own
                # highlighting; also surface the value list
                # for editor inspection + popover targeting.
                new_el["unverified"] = values
                new_elements.append(new_el)
            new_slide["elements"] = new_elements
        out_slides.append(new_slide)
    new_content = dict(content_json)
    new_content["slides"] = out_slides
    stats["upgraded"] = stats["nodes_upgraded"] > 0
    return new_content, stats
