"""tests/test_deck_slides_to_editor.py -- June 8 2026.

BUG 2 -- the deck generator wrote text-only slides; users had to add
charts manually via the ChartPicker sidebar. editor_content.
deck_slides_to_editor now consults DECK_SLIDE_CHART_KEYS and embeds a
chart element on every slide that has a contextually-appropriate
platform chart configured.

BUG 1 -- the same helper used to collapse table_data into raw
" | " separator strings. It now emits a proper markdown table (with
the `|---|` separator row) so PresentationPreview's parser can render
it as a styled HTML table.

These tests pin both contracts.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")


# ── Bug 2: per-slide chart auto-embed ────────────────────────────────

class TestDeckSlideChartAutoEmbed:
    """Slides with an entry in DECK_SLIDE_CHART_KEYS open in the editor
    with a chart element already in place. Slides without an entry
    carry no chart element -- title + body text only."""

    def test_chart_keys_map_uses_available_chart_keys_only(self):
        """The chart_key strings must be keys the frontend's
        /api/v1/charts/available endpoint recognises. Hardcode the
        accepted keys here so a future rename of an underlying
        renderer breaks this test instead of silently producing
        unrecognised chartKey elements in the editor."""
        from tools.editor_content import DECK_SLIDE_CHART_KEYS
        VALID = {
            "regime_signals", "regime_conditional_returns",
            "factor_loadings", "factor_returns_attribution",
            "rolling_correlation", "cumulative_returns",
            "rolling_sharpe", "rolling_excess_return",
            "return_distribution", "monthly_returns_heatmap",
            "drawdown_periods", "risk_return", "sensitivity",
            "significance_journey", "oos_performance",
            "p_value_distribution", "team_activity",
        }
        for slide_num, key in DECK_SLIDE_CHART_KEYS.items():
            assert key in VALID, (
                f"DECK_SLIDE_CHART_KEYS[{slide_num}]={key!r} "
                f"is not in the /api/v1/charts/available set")

    def test_slide_with_chart_emits_chart_element(self):
        """A slide whose number is in DECK_SLIDE_CHART_KEYS must carry
        a chart element with the configured chartKey. Slide 4 (rolling
        correlation -- the 2022 break) is the canonical example."""
        from tools.editor_content import (
            DECK_SLIDE_CHART_KEYS, deck_slides_to_editor,
        )
        ai_slides = [
            {"slide_number": 4, "title": "The 2022 Break",
             "bullets": ["A", "B"], "table_data": None,
             "speaker_notes": ""},
        ]
        content_json, _text = deck_slides_to_editor(ai_slides)
        slide_4 = content_json["slides"][3]   # 0-indexed
        chart_elements = [
            e for e in slide_4["elements"] if e["type"] == "chart"]
        assert len(chart_elements) == 1
        assert chart_elements[0]["chartKey"] == DECK_SLIDE_CHART_KEYS[4]
        # Chart sits on the right half of the canvas; bullets narrow
        # to the left half.
        assert chart_elements[0]["x"] >= 400

    def test_slide_without_chart_emits_no_chart_element(self):
        """Slide 1 (stat card) and slide 8 (event scorecard table)
        do not map to a chart. The editor opens those slides with
        title + body only -- no chart placeholder."""
        from tools.editor_content import (
            DECK_SLIDE_CHART_KEYS, deck_slides_to_editor,
        )
        # Confirm via the map that slide 1 has no chart configured.
        assert 1 not in DECK_SLIDE_CHART_KEYS

        ai_slides = [
            {"slide_number": 1, "title": "Question",
             "bullets": ["Answer"], "table_data": None,
             "speaker_notes": ""}
        ]
        content_json, _ = deck_slides_to_editor(ai_slides)
        slide_1 = content_json["slides"][0]
        chart_elements = [
            e for e in slide_1["elements"] if e["type"] == "chart"]
        assert chart_elements == []
        # The body text element spans the full canvas width (840)
        # since no chart needs the right half.
        body = [e for e in slide_1["elements"] if e["type"] == "text"][1]
        assert body["width"] == 840

    def test_every_chart_slide_has_a_chart_after_full_run(self):
        """End-to-end across the full deck: every slide listed in
        DECK_SLIDE_CHART_KEYS lands a chart element in the editor
        canvas. June 27 2026 -- 11-slide deck (Investment Case
        merge collapsed old slides 3 + 4 into one)."""
        from tools.academic_deck import DECK_SLIDE_COUNT
        from tools.editor_content import (
            DECK_SLIDE_CHART_KEYS, deck_slides_to_editor,
        )
        # Empty AI slides -> _normalize_slides fills canonical titles
        # for all slides; the chart-auto-embed must still fire.
        content_json, _ = deck_slides_to_editor([])
        slides = content_json["slides"]
        assert len(slides) == DECK_SLIDE_COUNT == 11
        for slide_num, expected_key in DECK_SLIDE_CHART_KEYS.items():
            slide = slides[slide_num - 1]
            chart_elements = [
                e for e in slide["elements"] if e["type"] == "chart"]
            assert len(chart_elements) == 1, (
                f"slide {slide_num} missing chart element")
            assert chart_elements[0]["chartKey"] == expected_key


# ── Bug 1: table_data renders as proper markdown ─────────────────────

class TestDeckSlideMarkdownTable:
    """table_data lands in the slide body as a proper markdown table
    with the `|---|` separator row so PresentationPreview detects it
    and renders a styled HTML <table>. The pre-bug emit used raw
    ` | ` separators with no `|---|` header row -- the preview showed
    raw pipes."""

    # June 26 2026 -- table_data now flows into a first-class
    # type='table' canvas element with structured rows/columns in
    # its table_config (no markdown-pipe text in the body). The
    # tests below pin the new contract; the legacy markdown-in-body
    # contract was a transitional shape so the editor's table
    # preview could render before the Configure panel existed.

    def test_table_data_emits_type_table_element(self):
        from tools.editor_content import deck_slides_to_editor
        ai_slides = [
            {"slide_number": 3, "title": "Numbers",
             "bullets": ["Three strategies"],
             "table_data": {
                 "headers": ["Strategy", "OOS Sharpe", "Max DD"],
                 "rows": [
                     ["Dynamic Blend", "0.81", "-15%"],
                     ["Classic 60/40", "0.62", "-22%"],
                 ],
             },
             "speaker_notes": ""}
        ]
        content_json, _ = deck_slides_to_editor(ai_slides)
        slide_3 = content_json["slides"][2]
        tables = [e for e in slide_3["elements"]
                  if e["type"] == "table"]
        assert len(tables) == 1, (
            "table_data should emit exactly one type='table' element")
        tc = tables[0]["table_config"]
        assert tc["columns"] == ["Strategy", "OOS Sharpe", "Max DD"]
        assert tc["rows"] == [
            ["Dynamic Blend", "0.81", "-15%"],
            ["Classic 60/40", "0.62", "-22%"],
        ]
        # Table type defaults to 'performance' when the slide spec
        # doesn't override.
        assert tc["table_type"] == "performance"

    def test_short_row_padded_to_header_count(self):
        """Rows shorter than the header column count are right-padded
        with empty cells so the table stays rectangular even when an
        LLM emits an off-by-one row.

        June 26 2026 -- assertion updated to inspect the table
        element's table_config.rows shape (was the body text's
        markdown string)."""
        from tools.editor_content import deck_slides_to_editor
        ai_slides = [
            {"slide_number": 3, "title": "Numbers",
             "bullets": [],
             "table_data": {
                 "headers": ["A", "B", "C"],
                 "rows": [["1"]],
             },
             "speaker_notes": ""}
        ]
        content_json, _ = deck_slides_to_editor(ai_slides)
        tables = [e for e in content_json["slides"][2]["elements"]
                  if e["type"] == "table"]
        assert tables, "table element should be emitted"
        assert tables[0]["table_config"]["rows"] == [["1", "", ""]]

    def test_long_row_truncated_to_header_count(self):
        from tools.editor_content import deck_slides_to_editor
        ai_slides = [
            {"slide_number": 3, "title": "Numbers",
             "bullets": [],
             "table_data": {
                 "headers": ["A", "B"],
                 "rows": [["1", "2", "3", "4"]],
             },
             "speaker_notes": ""}
        ]
        content_json, _ = deck_slides_to_editor(ai_slides)
        tables = [e for e in content_json["slides"][2]["elements"]
                  if e["type"] == "table"]
        assert tables, "table element should be emitted"
        # Only the first two cells survive; the row stays
        # rectangular against the two-column header.
        assert tables[0]["table_config"]["rows"] == [["1", "2"]]

    def test_no_table_data_emits_no_separator_row(self):
        from tools.editor_content import deck_slides_to_editor
        ai_slides = [
            {"slide_number": 2, "title": "Comparison",
             "bullets": ["No table on this slide"],
             "table_data": None,
             "speaker_notes": ""}
        ]
        content_json, _ = deck_slides_to_editor(ai_slides)
        body = [e for e in content_json["slides"][1]["elements"]
                if e["type"] == "text"][1]
        # The markdown signature MUST NOT appear -- PresentationPreview
        # would otherwise try to render a non-existent table.
        assert "|---" not in body["content"]
        # June 26 2026 -- no type='table' element either when
        # table_data is absent.
        tables = [e for e in content_json["slides"][1]["elements"]
                  if e["type"] == "table"]
        assert tables == []
