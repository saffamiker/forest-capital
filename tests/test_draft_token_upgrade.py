"""tests/test_draft_token_upgrade.py -- June 28 2026.

Tests the dual-mode token storage upgrade pass + the post-
upgrade rewriter + the review summary builder.
"""
from __future__ import annotations

import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── Word-boundary pattern ────────────────────────────────────────


class TestValuePatternBoundaries:

    def test_does_not_match_substring_inside_longer_number(self):
        """The classic case: '0.86' must NOT match inside
        '10.86' or '0.860'. Word-boundary regex uses a negative
        lookbehind + lookahead for digit-or-dot characters."""
        from tools.draft_token_upgrade import _build_value_pattern
        pattern = _build_value_pattern(["0.86"])
        assert pattern.search("0.86") is not None
        assert pattern.search("Sharpe 0.86 vs") is not None
        assert pattern.search("10.86") is None
        assert pattern.search("0.860") is None
        assert pattern.search("100.86%") is None

    def test_matches_signed_values(self):
        from tools.draft_token_upgrade import _build_value_pattern
        pattern = _build_value_pattern(["-29.7%"])
        assert pattern.search("drawdown -29.7%") is not None

    def test_matches_integer_values(self):
        from tools.draft_token_upgrade import _build_value_pattern
        pattern = _build_value_pattern(["287"])
        assert pattern.search("287 months") is not None
        assert pattern.search("2870") is None
        assert pattern.search("1287") is None


# ── Text node splitting ──────────────────────────────────────────


class TestSplitTextNode:

    def test_single_match_splits_into_three(self):
        from tools.draft_token_upgrade import (
            _build_value_pattern, _split_text_node,
        )
        manifest = {
            "0.86": {"token": "{{X}}", "data_hash": "h",
                     "generated_at": "t"},
        }
        pattern = _build_value_pattern(["0.86"])
        result = _split_text_node(
            "Sharpe 0.86 vs benchmark", pattern, manifest)
        assert result is not None
        assert len(result) == 3
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "Sharpe "
        assert result[1]["type"] == "token_value"
        assert result[1]["attrs"]["token"] == "{{X}}"
        assert result[1]["attrs"]["resolved"] == "0.86"
        assert result[2]["type"] == "text"
        assert result[2]["text"] == " vs benchmark"

    def test_no_match_returns_none(self):
        from tools.draft_token_upgrade import (
            _build_value_pattern, _split_text_node,
        )
        pattern = _build_value_pattern(["0.86"])
        result = _split_text_node(
            "Sharpe 0.43 vs benchmark", pattern,
            {"0.86": {"token": "{{X}}", "data_hash": "h",
                      "generated_at": "t"}})
        assert result is None


# ── Full upgrade pass on a representative doc ────────────────────


class TestUpgradeContentJson:

    def test_minimal_brief_upgrade(self):
        from tools.draft_token_upgrade import (
            upgrade_content_json_to_token_values,
        )
        content_json = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "text",
                     "text": "The blend Sharpe is 0.86."},
                ]},
            ],
        }
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
        # Paragraph now has 3 children: text -> token_value -> text
        para = new_json["content"][0]
        assert len(para["content"]) == 3
        assert para["content"][1]["type"] == "token_value"
        assert para["content"][1]["attrs"]["token"] == (
            "{{OOS_SHARPE_BLEND}}")

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
        from tools.draft_token_upgrade import (
            upgrade_content_json_to_token_values,
        )
        content_json = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "Value 0.86 here."},
                ]},
            ],
        }
        manifest = {
            "0.86": {"token": "{{X}}", "data_hash": "h",
                     "generated_at": "t"},
        }
        once, _ = upgrade_content_json_to_token_values(
            content_json, manifest)
        twice, stats_twice = (
            upgrade_content_json_to_token_values(once, manifest))
        # Second pass finds the token_value node + does not
        # re-split it. nodes_upgraded == 0 + already_upgraded > 0.
        assert stats_twice["nodes_upgraded"] == 0
        assert stats_twice["already_upgraded"] >= 1


# ── apply_token_updates ──────────────────────────────────────────


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
        # Overridden node never auto-updates.
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


# ── Review summary ───────────────────────────────────────────────


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
        # Overridden node compares the override value to cache.
        assert m["{{OVERRIDE}}"]["current_value"] == "0.6291"
        assert m["{{OVERRIDE}}"]["overridden"] is True
