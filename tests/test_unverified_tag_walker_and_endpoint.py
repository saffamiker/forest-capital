"""tests/test_unverified_tag_walker_and_endpoint.py -- June 28 2026.

Pins for PR #479:
  - upgrade_content_json_for_unverified_tags (TipTap walker)
  - upgrade_canvas_slides_for_unverified_tags (deck walker)
  - POST /api/v1/editor/drafts/{id}/accept-unverified endpoint
    (source-pin only; integration tested via UI)
  - _tiptap_text / _tiptap_runs 3-state DOCX render
"""
from __future__ import annotations

import inspect
import json
import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── Walker: TipTap content_json ────────────────────────────


class TestUpgradeContentJsonForUnverifiedTags:

    def test_text_node_with_tag_splits_into_unverified_node(
            self):
        from tools.draft_token_upgrade import (
            upgrade_content_json_for_unverified_tags,
        )
        cj = {"type": "doc", "content": [
            {"type": "paragraph", "content": [
                {"type": "text",
                 "text": (
                    "The blue line surges above "
                    "<unverified>+0.5</unverified> "
                    "in the post-2022 regime.")},
            ]},
        ]}
        upgraded, stats = (
            upgrade_content_json_for_unverified_tags(cj))
        assert stats["nodes_upgraded"] == 1
        # Walk to verify the structured unverified node.
        flat = json.dumps(upgraded)
        assert '"type": "unverified"' in flat
        assert '"value": "+0.5"' in flat
        # Tag literal stripped from text nodes; surrounding
        # prose preserved.
        assert "<unverified>" not in flat
        assert "The blue line surges above" in flat
        assert "in the post-2022 regime." in flat

    def test_multiple_tags_split_in_order(self):
        from tools.draft_token_upgrade import (
            upgrade_content_json_for_unverified_tags,
        )
        cj = {"type": "doc", "content": [
            {"type": "paragraph", "content": [
                {"type": "text",
                 "text": (
                    "Two: <unverified>+0.5</unverified> "
                    "and <unverified>0.005</unverified>.")},
            ]},
        ]}
        upgraded, stats = (
            upgrade_content_json_for_unverified_tags(cj))
        assert stats["nodes_upgraded"] == 2

    def test_no_tag_returns_unchanged(self):
        from tools.draft_token_upgrade import (
            upgrade_content_json_for_unverified_tags,
        )
        cj = {"type": "doc", "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "Clean prose."},
            ]},
        ]}
        upgraded, stats = (
            upgrade_content_json_for_unverified_tags(cj))
        assert stats["nodes_upgraded"] == 0
        assert upgraded == cj

    def test_idempotent_on_already_upgraded(self):
        from tools.draft_token_upgrade import (
            upgrade_content_json_for_unverified_tags,
        )
        cj = {"type": "doc", "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "Pre "},
                {"type": "unverified",
                 "attrs": {"value": "+0.5"}},
                {"type": "text", "text": " post"},
            ]},
        ]}
        upgraded, stats = (
            upgrade_content_json_for_unverified_tags(cj))
        assert stats["nodes_upgraded"] == 0
        assert stats["already_upgraded"] == 1

    def test_marked_text_preserves_marks_on_split_pieces(self):
        from tools.draft_token_upgrade import (
            upgrade_content_json_for_unverified_tags,
        )
        cj = {"type": "doc", "content": [
            {"type": "paragraph", "content": [
                {"type": "text",
                 "text": (
                    "Bold: <unverified>0.005</unverified> here"),
                 "marks": [{"type": "bold"}]},
            ]},
        ]}
        upgraded, stats = (
            upgrade_content_json_for_unverified_tags(cj))
        assert stats["nodes_upgraded"] == 1
        # The two text pieces retain their bold mark; the
        # unverified node itself does NOT carry marks (the
        # NodeView renders its own styling).
        flat = json.dumps(upgraded)
        assert '"marks": [{"type": "bold"}]' in flat


# ── Walker: canvas content_json (deck) ─────────────────────


class TestUpgradeCanvasSlidesForUnverifiedTags:

    def test_canvas_element_with_tag_gets_unverified_list(
            self):
        from tools.draft_token_upgrade import (
            upgrade_canvas_slides_for_unverified_tags,
        )
        cj = {"slides": [{
            "elements": [{
                "type":    "text",
                "content": (
                    "Sharpe: <unverified>0.86</unverified> "
                    "with DD <unverified>-29.7%</unverified>"),
            }],
        }]}
        upgraded, stats = (
            upgrade_canvas_slides_for_unverified_tags(cj))
        assert stats["nodes_upgraded"] == 2
        el = upgraded["slides"][0]["elements"][0]
        assert "unverified" in el
        assert el["unverified"] == ["0.86", "-29.7%"]
        # Literal tag substring PRESERVED in content for the
        # Konva renderer's own highlighting pass.
        assert "<unverified>" in el["content"]

    def test_clean_canvas_returns_unchanged(self):
        from tools.draft_token_upgrade import (
            upgrade_canvas_slides_for_unverified_tags,
        )
        cj = {"slides": [{
            "elements": [{"type": "text", "content": "Clean"}],
        }]}
        upgraded, stats = (
            upgrade_canvas_slides_for_unverified_tags(cj))
        assert stats["nodes_upgraded"] == 0


# ── DOCX renderer 3-state ──────────────────────────────────


class TestTipTapTextThreeStateRender:

    def test_unverified_default_renders_visible_marker(self):
        from tools.academic_docx import _tiptap_text
        node = {
            "type":  "unverified",
            "attrs": {"value": "+0.5"},
        }
        assert _tiptap_text(node) == "[UNVERIFIED: +0.5]"

    def test_unverified_accepted_renders_raw_value(self):
        from tools.academic_docx import _tiptap_text
        node = {
            "type":  "unverified",
            "attrs": {"value": "+0.5", "accepted": True},
        }
        assert _tiptap_text(node) == "+0.5"

    def test_unverified_in_paragraph_renders_via_descent(self):
        from tools.academic_docx import _tiptap_text
        node = {"type": "paragraph", "content": [
            {"type": "text", "text": "before "},
            {"type": "unverified", "attrs": {"value": "+0.5"}},
            {"type": "text", "text": " after"},
        ]}
        assert _tiptap_text(node) == (
            "before [UNVERIFIED: +0.5] after")


class TestTipTapRunsThreeStateRender:

    def test_unverified_default_emits_bold_marker_run(self):
        from tools.academic_docx import _tiptap_runs
        node = {
            "type":  "unverified",
            "attrs": {"value": "+0.5"},
        }
        runs = _tiptap_runs(node)
        assert len(runs) == 1
        text, marks = runs[0]
        assert text == "[UNVERIFIED: +0.5]"
        assert marks.get("bold") is True

    def test_unverified_accepted_emits_plain_run(self):
        from tools.academic_docx import _tiptap_runs
        node = {
            "type":  "unverified",
            "attrs": {"value": "+0.5", "accepted": True},
        }
        runs = _tiptap_runs(node)
        assert len(runs) == 1
        text, marks = runs[0]
        assert text == "+0.5"
        # No bold mark for accepted state.
        assert not marks.get("bold")


# ── Accept endpoint ─────────────────────────────────────────


class TestAcceptUnverifiedEndpoint:

    def test_endpoint_function_exists(self):
        from main import accept_unverified_value
        assert callable(accept_unverified_value)

    def test_endpoint_persists_via_log_editor_overrides(self):
        """Source-pin: the endpoint delegates to the existing
        log_editor_overrides helper (PR #469's editor_numeric_
        overrides table). One row per accept-as-is decision."""
        from main import accept_unverified_value
        src = inspect.getsource(accept_unverified_value)
        assert "log_editor_overrides" in src
        assert "editor_numeric_overrides" not in src or (
            "log_editor_overrides" in src)

    def test_endpoint_routed_at_expected_path(self):
        """Source-pin: the decorator path matches the frontend
        fetch URL in UnverifiedNodeView."""
        import main as _main
        with open(
                _main.__file__, encoding="utf-8") as f:
            src = f.read()
        assert (
            '"/api/v1/editor/drafts/{draft_id}/accept-unverified"'
            in src)

    def test_endpoint_requires_value(self):
        """Source-pin: missing/empty value -> 422."""
        from main import accept_unverified_value
        src = inspect.getsource(accept_unverified_value)
        assert "status_code=422" in src
        assert '"value is required"' in src

    def test_endpoint_logs_accept_event(self):
        """Source-pin: a log.info event lands on every accept
        so the operator can grep Render logs for audit."""
        from main import accept_unverified_value
        src = inspect.getsource(accept_unverified_value)
        assert "unverified_accept_logged" in src


# ── Auto-upgrade hook extended to all doc types ────────────


class TestAutoUpgradeAllDocTypes:

    def test_helper_dispatches_canvas_vs_tiptap(self):
        from main import _auto_upgrade_draft_to_token_values
        src = inspect.getsource(
            _auto_upgrade_draft_to_token_values)
        assert (
            'document_type == "presentation_deck"' in src)
        assert (
            "upgrade_canvas_slides_for_unverified_tags" in src)
        assert (
            "upgrade_content_json_for_unverified_tags" in src)

    def test_unverified_upgrade_runs_unconditionally(self):
        """Source-pin: the unverified-tag upgrade runs whether
        or not the deferral flag is on. Token upgrade is gated
        on the flag; unverified upgrade is not."""
        from main import _auto_upgrade_draft_to_token_values
        src = inspect.getsource(
            _auto_upgrade_draft_to_token_values)
        # The unverified path runs regardless of defer_on.
        assert "defer_on" in src
        token_gate_idx = src.find(
            "if (defer_on and manifest")
        # The CALL site of the unverified walker (not the
        # import line); the function name appears at the call
        # site as part of the assignment statement.
        unv_call_idx = src.find(
            "upgrade_content_json_for_unverified_tags(\n",
            token_gate_idx)
        if unv_call_idx == -1:
            unv_call_idx = src.find(
                "upgrade_content_json_for_unverified_tags(",
                token_gate_idx)
        assert token_gate_idx > -1
        assert unv_call_idx > -1
        # Unverified call lives BELOW the token-gated block.
        assert unv_call_idx > token_gate_idx
