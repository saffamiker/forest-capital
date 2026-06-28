"""tests/test_substitution_deferral_phase_2.py -- June 28 2026.

Phase 2 substitution-deferral end-to-end pins.

Per spec:
  * Brief / appendix / script content_json carries {{TOKEN}}
    placeholders intact when DEFER_SUBSTITUTION_TO_EXPORT is on
    AND a substitution_table is supplied to the editor-content
    builders.
  * Deck content_json carries RESOLVED VALUES only (the deck
    audit established that canvas-element JSON is structurally
    incompatible with the dual-mode token_value architecture,
    so the deck substitutes at the deck_slides_to_editor
    boundary REGARDLESS of flag state).
  * content_text is derived from the SUBSTITUTED projection in
    all four document types so full-text search + word counts
    see resolved values.
  * The {{TOKEN}}-only upgrade pass (PR #466) converts the
    surviving placeholders in brief/appendix/script
    content_json into token_value nodes correctly.
"""
from __future__ import annotations

import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── Helpers ────────────────────────────────────────────────────


def _flatten_text_from_tiptap(node) -> str:
    """Concat every text/token_value-resolved leaf in a TipTap
    tree -- used to confirm content_json carries either tokens
    or resolved values as expected."""
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "token_value":
        attrs = node.get("attrs") or {}
        return str(attrs.get("override")
                   or attrs.get("resolved") or "")
    if node.get("text"):
        return str(node["text"])
    return "".join(
        _flatten_text_from_tiptap(c)
        for c in (node.get("content") or []))


_SUB_TABLE = {
    "{{OOS_SHARPE_BLEND}}":     "0.86",
    "{{OOS_SHARPE_BENCHMARK}}": "0.43",
    "{{DATA_HASH}}":            "c421fb89",
    # June 28 2026 -- the deck SLIDE_TITLES fallback for slot
    # 7 references these. _normalize_slides pads up to
    # DECK_SLIDE_COUNT and inserts the canonical titles
    # verbatim, so the substitution_table must cover them too.
    "{{CURRENT_REGIME}}":       "BULL",
    "{{REGIME_CONFIDENCE}}":    "82%",
}


# ── Brief ────────────────────────────────────────────────────


class TestBriefDeferral:

    def test_content_json_preserves_tokens(self):
        """Under flag ON (caller supplies substitution_table),
        executive_brief_to_editor builds content_json with
        {{TOKEN}} placeholders surviving in text leaves."""
        from tools.editor_content import executive_brief_to_editor
        narratives = {
            "executive_summary": (
                "The blend Sharpe is {{OOS_SHARPE_BLEND}} versus "
                "benchmark {{OOS_SHARPE_BENCHMARK}}."),
        }
        cj, ct = executive_brief_to_editor(
            narratives, substitution_table=_SUB_TABLE)
        flat = _flatten_text_from_tiptap(cj)
        # content_json keeps the tokens literally
        assert "{{OOS_SHARPE_BLEND}}" in flat
        assert "{{OOS_SHARPE_BENCHMARK}}" in flat
        # content_text carries the substituted projection
        assert "0.86" in ct
        assert "0.43" in ct
        assert "{{OOS_SHARPE_BLEND}}" not in ct
        assert "{{OOS_SHARPE_BENCHMARK}}" not in ct

    def test_no_substitution_table_legacy_behaviour(self):
        """When substitution_table is None, both columns share
        the raw narratives (legacy behaviour preserved)."""
        from tools.editor_content import executive_brief_to_editor
        narratives = {
            "executive_summary":
                "Pre-resolved Sharpe 0.86 in the brief.",
        }
        cj, ct = executive_brief_to_editor(narratives)
        flat = _flatten_text_from_tiptap(cj)
        assert "0.86" in flat
        assert "0.86" in ct


class TestAppendixDeferral:

    def test_content_json_preserves_tokens(self):
        from tools.editor_content import (
            analytical_appendix_to_editor,
        )
        narratives = {
            "appendix_a": (
                "Section A uses {{OOS_SHARPE_BLEND}} as the "
                "headline figure."),
        }
        cj, ct = analytical_appendix_to_editor(
            narratives, substitution_table=_SUB_TABLE)
        flat = _flatten_text_from_tiptap(cj)
        assert "{{OOS_SHARPE_BLEND}}" in flat
        assert "0.86" in ct
        assert "{{OOS_SHARPE_BLEND}}" not in ct


# ── Deck (substitutes always) ─────────────────────────────────


class TestDeckAlwaysSubstitutes:

    def test_content_json_has_zero_tokens_when_table_supplied(
            self):
        """The deck audit established that canvas content_json
        is structurally incompatible with dual-mode token_value
        nodes. So the deck ALWAYS substitutes at the
        deck_slides_to_editor boundary regardless of flag.
        After substitution, content_json must contain ZERO
        {{TOKEN}} strings."""
        from tools.editor_content import deck_slides_to_editor
        # Use slide 2 + 4 -- slide 1 is title-only by canonical
        # spec (body discarded). Slide 2 + 4 keep body bullets +
        # tables so the substitution can be verified across
        # title, bullets, table cells, and speaker_notes.
        slides = [
            {"slide_number": 1, "title": "Intro", "bullets": [],
             "speaker_notes": ""},
            {"slide_number": 2,
             "title": "Sharpe headline: {{OOS_SHARPE_BLEND}}",
             "bullets": ["Blend is {{OOS_SHARPE_BLEND}}",
                         "Bench is {{OOS_SHARPE_BENCHMARK}}"],
             "speaker_notes":
                "Discuss {{OOS_SHARPE_BLEND}} vs benchmark.",
             "table_data": {
                "headers": ["Metric", "{{DATA_HASH}}"],
                "rows":    [["Sharpe", "{{OOS_SHARPE_BLEND}}"]]}},
        ]
        cj, ct = deck_slides_to_editor(
            slides, substitution_table=_SUB_TABLE)
        # Walk the canvas content_json + assert no raw tokens.
        import json
        flat_json = json.dumps(cj)
        assert "{{OOS_SHARPE_BLEND}}" not in flat_json
        assert "{{OOS_SHARPE_BENCHMARK}}" not in flat_json
        assert "{{DATA_HASH}}" not in flat_json
        # Verify resolved values are present.
        assert "0.86" in flat_json
        assert "0.43" in flat_json
        assert "c421fb89" in flat_json
        # content_text mirrors content_json (both substituted).
        assert "{{" not in ct
        assert "0.86" in ct

    def test_no_substitution_table_preserves_tokens(self):
        """When substitution_table is not supplied (e.g. flag
        OFF + caller didn't build one), deck content_json keeps
        tokens AS-IS. This is the legacy behaviour."""
        from tools.editor_content import deck_slides_to_editor
        slides = [
            {"slide_number": 1,
             "title": "Sharpe headline: {{OOS_SHARPE_BLEND}}",
             "bullets": [],
             "speaker_notes": ""},
        ]
        cj, ct = deck_slides_to_editor(slides)
        import json
        flat_json = json.dumps(cj)
        assert "{{OOS_SHARPE_BLEND}}" in flat_json


# ── Build_executive_brief substitution at render time ──────────


class TestBriefBuilderResolvesAtExport:

    def test_substitution_table_applied_to_narratives_at_top(
            self):
        """Source-inspection pin: build_executive_brief applies
        substitution to the narratives dict at the top of the
        function (before 19 _add_brief_body call sites). This
        ensures DOCX body prose renders resolved values even
        when content_json carries tokens (Phase 2 deferred
        substitution path)."""
        import inspect
        from tools.academic_docx import build_executive_brief
        src = inspect.getsource(build_executive_brief)
        # The substitution dict-comprehension over narratives.
        assert "_apply_substitutions(v, substitution_table)" in (
            src)
        # Must happen BEFORE any _add_brief_body call.
        sub_idx = src.find(
            "_apply_substitutions(v, substitution_table)")
        body_idx = src.find("_add_brief_body(")
        assert sub_idx > -1
        assert body_idx > -1
        assert sub_idx < body_idx


# ── Upgrade-pass compatibility ────────────────────────────────


class TestUpgradePassCompatWithDeferredContent:

    def test_upgrade_pass_finds_tokens_in_deferred_content_json(
            self):
        """End-to-end: Phase 2 produces content_json with
        {{TOKEN}} placeholders. The {{TOKEN}}-only upgrade pass
        from PR #466 must walk that content + emit token_value
        nodes for each placeholder."""
        from tools.editor_content import executive_brief_to_editor
        from tools.draft_token_upgrade import (
            upgrade_content_json_to_token_values,
        )
        narratives = {
            "executive_summary":
                "Sharpe is {{OOS_SHARPE_BLEND}} for the blend.",
        }
        cj, _ct = executive_brief_to_editor(
            narratives, substitution_table=_SUB_TABLE)
        # The upgrade pass takes content_json + value_manifest
        # (keyed by resolved value -> {token, ...}).
        value_manifest = {
            "0.86": {"token": "{{OOS_SHARPE_BLEND}}",
                     "data_hash": "c421fb89",
                     "generated_at": "2026-06-28T00:00:00Z"},
        }
        upgraded, stats = upgrade_content_json_to_token_values(
            cj, value_manifest)
        assert stats["nodes_upgraded"] >= 1
        # The upgraded tree contains a token_value node with
        # the correct attrs.
        import json
        flat = json.dumps(upgraded)
        assert '"type": "token_value"' in flat
        assert '"{{OOS_SHARPE_BLEND}}"' in flat
        assert '"0.86"' in flat
