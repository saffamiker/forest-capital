"""tests/test_draft_token_upgrade.py -- June 28 2026.

Tests the dual-mode token storage upgrade pass + the post-
upgrade rewriter + the review summary builder.

June 28 2026 (post-hotfix) -- matching is now exclusively on
{{TOKEN_NAME}} placeholders surviving in text nodes. Reverse-
lookup on resolved values was removed (it produced systemic
false positives on short integer manifest values like 2 / 10
/ 15 / 20 matching unrelated prose numbers).
"""
from __future__ import annotations

import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── Token-placeholder splitting (replaces value-pattern tests) ──


class TestSplitTextNodeOnTokenPlaceholders:

    def test_single_token_splits_into_three(self):
        """A text node with one {{TOKEN}} placeholder splits
        into [text-before, token_value, text-after]."""
        from tools.draft_token_upgrade import _split_text_node
        valid_tokens = {
            "{{OOS_SHARPE_BLEND}}": {
                "resolved": "0.86",
                "data_hash": "c421fb89",
                "generated_at": "2026-06-21T12:00:00Z",
            },
        }
        result = _split_text_node(
            "Sharpe {{OOS_SHARPE_BLEND}} vs benchmark",
            valid_tokens)
        assert result is not None
        assert len(result) == 3
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "Sharpe "
        assert result[1]["type"] == "token_value"
        assert result[1]["attrs"]["token"] == (
            "{{OOS_SHARPE_BLEND}}")
        assert result[1]["attrs"]["resolved"] == "0.86"
        assert result[1]["attrs"]["data_hash"] == "c421fb89"
        assert result[2]["type"] == "text"
        assert result[2]["text"] == " vs benchmark"

    def test_no_match_returns_none(self):
        """A text node with no {{TOKEN}} placeholders returns
        None so the caller keeps the original node untouched."""
        from tools.draft_token_upgrade import _split_text_node
        result = _split_text_node(
            "Sharpe 0.86 vs benchmark",
            {"{{OOS_SHARPE_BLEND}}": {
                "resolved": "0.86",
                "data_hash": "h", "generated_at": "t"}})
        assert result is None

    def test_unknown_token_left_intact(self):
        """Token literal NOT in valid_tokens is preserved as
        plain text -- never wrapped, never given a fabricated
        cache hash. Safety contract."""
        from tools.draft_token_upgrade import _split_text_node
        result = _split_text_node(
            "Has {{UNKNOWN_TOKEN}} and that's it",
            {"{{OTHER_TOKEN}}": {
                "resolved": "x", "data_hash": "h",
                "generated_at": "t"}})
        assert result is None

    def test_short_resolved_value_in_prose_never_matched(self):
        """REGRESSION pin -- the buggy version matched any
        prose '2' against PLAY_BY_PLAY_VALUE_ADD's resolved
        value '2'. The fixed matcher matches only on
        {{TOKEN}} placeholders, so prose digits are never
        touched. This pin defends against any future revert
        to reverse-lookup matching."""
        from tools.draft_token_upgrade import _split_text_node
        valid_tokens = {
            "{{PLAY_BY_PLAY_VALUE_ADD}}": {
                "resolved": "2",
                "data_hash": "h", "generated_at": "t"},
            "{{N_STRATEGIES}}": {
                "resolved": "10",
                "data_hash": "h", "generated_at": "t"},
        }
        # Prose with naked digits that previously over-matched:
        # citation years, sensitivity bps, sentence-leading
        # numbers, decimal-leading digits.
        prose_lines = [
            "Smith (2020) found 10% returns",
            "At 10bp the Sharpe was 2.5",
            "Section 2 covers 15 strategies",
            "Volatility was 2.86 across 20 windows",
        ]
        for line in prose_lines:
            result = _split_text_node(line, valid_tokens)
            assert result is None, (
                f"Plain prose line was incorrectly upgraded: "
                f"{line!r}")

    def test_two_tokens_in_same_text(self):
        from tools.draft_token_upgrade import _split_text_node
        valid_tokens = {
            "{{A}}": {
                "resolved": "1.0", "data_hash": "h",
                "generated_at": "t"},
            "{{B}}": {
                "resolved": "2.0", "data_hash": "h",
                "generated_at": "t"},
        }
        result = _split_text_node(
            "Values {{A}} and {{B}} here.", valid_tokens)
        assert result is not None
        tv_count = sum(
            1 for n in result if n["type"] == "token_value")
        assert tv_count == 2


# ── Full upgrade pass on a representative doc ────────────────────


class TestUpgradeContentJson:

    def test_minimal_brief_upgrade(self):
        """When content_json carries an unsubstituted {{TOKEN}}
        placeholder + value_manifest has an entry whose token
        matches, the placeholder gets wrapped in a token_value
        node."""
        from tools.draft_token_upgrade import (
            upgrade_content_json_to_token_values,
        )
        content_json = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "text",
                     "text": "Sharpe {{OOS_SHARPE_BLEND}}."},
                ]},
            ],
        }
        # value_manifest is keyed by VALUE (preserves the
        # production schema). The upgrade pass inverts it.
        manifest = {
            "0.86": {"token": "{{OOS_SHARPE_BLEND}}",
                     "data_hash": "c421fb89",
                     "generated_at": "2026-06-21T12:00:00Z"},
        }
        new_json, stats = (
            upgrade_content_json_to_token_values(
                content_json, manifest))
        assert stats["nodes_upgraded"] == 1
        assert stats["upgraded"] is True
        para = new_json["content"][0]
        assert len(para["content"]) == 3
        assert para["content"][1]["type"] == "token_value"
        assert para["content"][1]["attrs"]["token"] == (
            "{{OOS_SHARPE_BLEND}}")
        assert para["content"][1]["attrs"]["resolved"] == "0.86"

    def test_pre_substituted_prose_produces_zero_upgrades(self):
        """When generation-time substitution baked values into
        content_json (no {{TOKEN}} placeholders survive), the
        upgrade pass produces 0 nodes_upgraded -- correct
        behaviour after the June 28 fix. The buggy version
        would have over-matched on the resolved value strings."""
        from tools.draft_token_upgrade import (
            upgrade_content_json_to_token_values,
        )
        content_json = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "text",
                     "text": "Blend Sharpe is 0.86. "
                             "Benchmark is 0.43. "
                             "Across 2 of 9 events."},
                ]},
            ],
        }
        manifest = {
            "0.86": {"token": "{{OOS_SHARPE_BLEND}}",
                     "data_hash": "h",
                     "generated_at": "t"},
            "0.43": {"token": "{{OOS_SHARPE_BENCHMARK}}",
                     "data_hash": "h",
                     "generated_at": "t"},
            "2":    {"token": "{{PLAY_BY_PLAY_VALUE_ADD}}",
                     "data_hash": "h",
                     "generated_at": "t"},
            "9":    {"token": "{{PLAY_BY_PLAY_EVENTS}}",
                     "data_hash": "h",
                     "generated_at": "t"},
        }
        new_json, stats = (
            upgrade_content_json_to_token_values(
                content_json, manifest))
        # No {{TOKEN}} placeholders in the text -- zero upgrades.
        assert stats["nodes_upgraded"] == 0
        assert stats["upgraded"] is False
        # Content is unchanged.
        assert new_json == content_json

    def test_null_manifest_no_op(self):
        from tools.draft_token_upgrade import (
            upgrade_content_json_to_token_values,
        )
        content_json = {"type": "doc", "content": []}
        result, stats = (
            upgrade_content_json_to_token_values(
                content_json, None))
        assert result == content_json
        assert stats["upgraded"] is False

    def test_idempotent_re_run(self):
        """Re-running on an already-upgraded document
        produces 0 new nodes_upgraded; already_upgraded is
        populated for the existing token_value nodes."""
        from tools.draft_token_upgrade import (
            upgrade_content_json_to_token_values,
        )
        content_json = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "Sharpe {{X}} here."},
                ]},
            ],
        }
        manifest = {
            "0.86": {"token": "{{X}}",
                     "data_hash": "h", "generated_at": "t"},
        }
        once, _ = upgrade_content_json_to_token_values(
            content_json, manifest)
        twice, stats_twice = (
            upgrade_content_json_to_token_values(once, manifest))
        assert stats_twice["nodes_upgraded"] == 0
        assert stats_twice["already_upgraded"] >= 1

    def test_unknown_token_in_text_left_intact(self):
        """An {{UNKNOWN}} token literal in text whose manifest
        entry doesn't exist stays as plain text."""
        from tools.draft_token_upgrade import (
            upgrade_content_json_to_token_values,
        )
        content_json = {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [
                {"type": "text",
                 "text": "{{KNOWN}} and {{UNKNOWN}}"},
            ]}],
        }
        manifest = {
            "0.86": {"token": "{{KNOWN}}",
                     "data_hash": "h", "generated_at": "t"},
        }
        new_json, stats = upgrade_content_json_to_token_values(
            content_json, manifest)
        assert stats["nodes_upgraded"] == 1
        # The {{UNKNOWN}} text is preserved verbatim.
        para = new_json["content"][0]
        all_text = "".join(
            c.get("text") or ""
            for c in para["content"]
            if c.get("type") == "text")
        assert "{{UNKNOWN}}" in all_text


# ── apply_token_updates (unchanged contract) ─────────────────────


class TestApplyTokenUpdates:

    def test_updates_resolved_to_new_value(self):
        from tools.draft_token_upgrade import apply_token_updates
        content_json = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "token_value", "attrs": {
                        "token":      "{{X}}",
                        "resolved":   "0.86",
                        "data_hash":  "old",
                        "resolved_at": "old_ts",
                    }},
                ]},
            ],
        }
        new_json, updates = apply_token_updates(
            content_json, {"{{X}}": "0.91"}, "new_hash")
        assert len(updates) == 1
        assert updates[0]["old_value"] == "0.86"
        assert updates[0]["new_value"] == "0.91"
        node = new_json["content"][0]["content"][0]
        assert node["attrs"]["resolved"] == "0.91"
        assert node["attrs"]["data_hash"] == "new_hash"

    def test_skips_when_value_unchanged(self):
        from tools.draft_token_upgrade import apply_token_updates
        content_json = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "token_value", "attrs": {
                        "token":    "{{X}}",
                        "resolved": "0.86",
                    }},
                ]},
            ],
        }
        _, updates = apply_token_updates(
            content_json, {"{{X}}": "0.86"}, "h")
        assert len(updates) == 0

    def test_skips_overridden_nodes(self):
        from tools.draft_token_upgrade import apply_token_updates
        content_json = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "token_value", "attrs": {
                        "token":    "{{X}}",
                        "resolved": "0.86",
                        "override": "0.8591",
                    }},
                ]},
            ],
        }
        _, updates = apply_token_updates(
            content_json, {"{{X}}": "0.91"}, "h")
        assert len(updates) == 0

    def test_selected_tokens_gate(self):
        from tools.draft_token_upgrade import apply_token_updates
        content_json = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "token_value", "attrs": {
                        "token": "{{A}}", "resolved": "1.0"}},
                    {"type": "token_value", "attrs": {
                        "token": "{{B}}", "resolved": "2.0"}},
                ]},
            ],
        }
        _, updates = apply_token_updates(
            content_json,
            {"{{A}}": "9.0", "{{B}}": "8.0"}, "h",
            selected_tokens={"{{A}}"})
        assert len(updates) == 1
        assert updates[0]["token"] == "{{A}}"


# ── Review summary (unchanged contract) ──────────────────────────


class TestBuildReviewSummary:

    def test_match_vs_mismatch_vs_overridden(self):
        from tools.draft_token_upgrade import build_review_summary
        content_json = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "token_value", "attrs": {
                        "token": "{{MATCH}}",
                        "resolved": "0.86"}},
                    {"type": "token_value", "attrs": {
                        "token": "{{MISMATCH}}",
                        "resolved": "0.43"}},
                    {"type": "token_value", "attrs": {
                        "token": "{{OVERRIDE}}",
                        "resolved": "0.63",
                        "override": "0.6291"}},
                ]},
            ],
        }
        table = {
            "{{MATCH}}":    "0.86",
            "{{MISMATCH}}": "0.50",
            "{{OVERRIDE}}": "0.65",
        }
        out = build_review_summary(content_json, table)
        assert len(out) == 3
        m = {e["token"]: e for e in out}
        assert m["{{MATCH}}"]["match"] is True
        assert m["{{MISMATCH}}"]["match"] is False
        assert m["{{OVERRIDE}}"]["current_value"] == "0.6291"
        assert m["{{OVERRIDE}}"]["overridden"] is True
