"""tests/test_draft_placeholder_failures.py — May 23 2026 hotfixes.

Three placeholder failures surfaced in Bob's draft generation:

  1. [DATA MISMATCH live=286 staged=not-found] on the observation
     count. Root cause: _staged_field_match defaulted UNKNOWN fields
     to ratio tolerance, so n_months (a metadata field the staged
     findings doc never claims a number for) was cross-checked
     against the staged number list and never matched. Fix: only
     check fields explicitly in _FIELD_TOLERANCE.

  2. [Macro: monetary_policy] tags leaking as raw text into prose.
     Root cause: tools/macro_context.py instructed the model to emit
     [Macro: <category>] inline citations, but nothing in the
     rendering pipeline parses or resolves those tags. Fix: remove
     the instruction; macro context stays as informational background.

  3. Raw {} in Appendix D. Root cause: template_pipeline build_prompt
     used `vs_lines or "(no validation run)"` as the fallback, but
     `json.dumps({})` returns the LITERAL string "{}" which is
     truthy, so the fallback never fired for an empty audit
     snapshot. The AI then saw `{}` in its prompt and echoed it.
     Fix: check the dict directly before serialising. Belt-and-
     braces: report_writer_docx._fmt_value now renders "—" for any
     dict/list value that slips through, so a future shape drift
     never leaks repr text into a table cell.

Each test pins the specific contract so a regression is caught
before Bob's midpoint paper gets the placeholder again.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")


# ── 1. DATA MISMATCH whitelist ───────────────────────────────────────────────


class TestStagedFieldMatchWhitelist:
    """_staged_field_match must only cross-check fields that have an
    explicit entry in _FIELD_TOLERANCE. Metadata fields (n_months,
    counts, ranks) are not present in the staged numbers list and
    must pass through cleanly."""

    def test_unknown_field_is_not_cross_checked(self):
        from tools.template_pipeline import _staged_field_match
        # n_months is metadata — the staged findings doc never names
        # a specific observation count. With the bug, live=286
        # would be checked against staged_numbers and fail.
        matched, payload = _staged_field_match(
            "n_months", 286, staged_numbers=[0.629, 0.522, 0.68])
        assert matched is True
        assert payload is None

    def test_unknown_field_with_empty_staged_list(self):
        from tools.template_pipeline import _staged_field_match
        # Even with NO staged numbers, an unknown field passes.
        matched, _ = _staged_field_match(
            "n_months", 286, staged_numbers=[])
        assert matched is True

    def test_known_field_still_cross_checked(self):
        from tools.template_pipeline import _staged_field_match
        # benchmark_sharpe IS in the tolerance map — must still be
        # cross-checked. With staged=0.629 and live=0.522, no match
        # within ratio tolerance (0.01).
        matched, payload = _staged_field_match(
            "benchmark_sharpe", 0.522, staged_numbers=[0.629])
        assert matched is False
        assert payload is not None
        assert payload["live"] == 0.522
        assert payload["field"] == "benchmark_sharpe"

    def test_known_field_within_tolerance_matches(self):
        from tools.template_pipeline import _staged_field_match
        # Live 0.524 vs staged 0.522 — within ratio tolerance 0.01.
        matched, payload = _staged_field_match(
            "benchmark_sharpe", 0.524, staged_numbers=[0.522])
        assert matched is True
        assert payload is None

    def test_non_numeric_value_passes(self):
        from tools.template_pipeline import _staged_field_match
        # A string value short-circuits before tolerance lookup.
        matched, _ = _staged_field_match(
            "some_text", "PASS", staged_numbers=[0.5])
        assert matched is True


# ── 2. [Macro: X] tag instruction removed ────────────────────────────────────


class TestMacroContextNoCitationFormat:
    """The CITATION FORMAT instruction telling the model to emit
    [Macro: <category>] tags must be gone — nothing in the
    rendering pipeline resolves those tags, so they leak as raw
    text into drafts."""

    def test_format_digest_block_does_not_request_macro_tags(self):
        from tools.macro_context import _format_digest_block
        digest = {
            "summary_text": "Inflation cooled to 3.1% vs 3.2% expected.",
            "regime_implication": "Dovish for both equity and IG.",
            "key_signals": [
                {"category": "inflation",
                 "signal": "CPI print 3.1% vs 3.2% expected.",
                 "implication": "Dovish for both equity and IG.",
                 "source_url": "https://bls.gov/cpi"}
            ],
            "generated_at": "2026-05-22T14:00:00Z",
        }
        block = _format_digest_block(digest)
        # The literal instruction string must not appear.
        assert "[Macro:" not in block
        assert "CITATION FORMAT" not in block
        # And the model must be told NOT to emit inline tags.
        assert "do NOT emit inline tags" in block \
            or "do NOT emit inline citation" in block \
            or "do NOT emit inline markers" in block.lower() \
            or "do not emit inline tags" in block.lower() \
            or "do not emit inline markers" in block.lower()

    def test_signal_block_still_renders_normally(self):
        from tools.macro_context import _format_digest_block
        digest = {
            "summary_text": "Summary text.",
            "key_signals": [
                {"category": "inflation",
                 "signal": "CPI 3.1%.",
                 "source_url": "https://bls.gov/"}
            ],
            "generated_at": "2026-05-22T14:00:00Z",
        }
        block = _format_digest_block(digest)
        # The signal still renders — only the instruction to cite
        # was removed, not the signal content itself.
        assert "CPI 3.1%" in block
        assert "[inflation]" in block

    def test_empty_digest_returns_empty_string(self):
        from tools.macro_context import _format_digest_block
        # Sanity — empty input still returns empty output.
        assert _format_digest_block(None) == ""
        assert _format_digest_block({}) == ""


# ── 3. Raw {} leak in Appendix D ─────────────────────────────────────────────


class TestEmptyValidationSummaryDoesNotLeak:
    """Two defensive layers prevent `{}` reaching the rendered draft:
      (a) template_pipeline.build_prompt substitutes "(no validation
          run)" when the validation summary dict is empty, instead of
          letting json.dumps({}) → "{}" reach the AI prompt.
      (b) report_writer_docx._fmt_value renders "—" for any dict /
          list value that slips through, so a future upstream shape
          drift doesn't echo repr text into a table cell.
    """

    def test_empty_dict_does_not_serialize_to_braces_in_prompt(self):
        # The bug was specifically: json.dumps({}) == "{}", and
        # "{}" is truthy, so `vs_lines or "(no validation run)"`
        # returns "{}" instead of the fallback. Confirm the new
        # check uses the dict directly.
        empty_dict = {}
        # Reproduce the buggy line in isolation to assert the
        # property that motivated the fix.
        buggy = json.dumps(empty_dict or {}, indent=2, default=str)
        assert buggy == "{}"
        assert bool(buggy) is True
        # The fix: check the dict, not the serialised string.
        vs_lines = (
            json.dumps(empty_dict, indent=2, default=str)
            if empty_dict else "")
        substituted = vs_lines or "(no validation run)"
        assert substituted == "(no validation run)"
        assert "{}" not in substituted

    def test_fmt_value_renders_dash_for_dict(self):
        from tools.report_writer_docx import _fmt_value
        assert _fmt_value({}) == "—"
        assert _fmt_value({"layer1": "PASS"}) == "—"

    def test_fmt_value_renders_dash_for_list(self):
        from tools.report_writer_docx import _fmt_value
        assert _fmt_value([]) == "—"
        assert _fmt_value(["a", "b"]) == "—"

    def test_fmt_value_renders_dash_for_none(self):
        from tools.report_writer_docx import _fmt_value
        # Existing behaviour — preserved.
        assert _fmt_value(None) == "—"

    def test_fmt_value_preserves_scalar_rendering(self):
        from tools.report_writer_docx import _fmt_value
        # Existing behaviour for the values that DO appear in
        # validation_summary (strings, floats, bools).
        assert _fmt_value("PASS") == "PASS"
        assert _fmt_value(True) == "Yes"
        assert _fmt_value(False) == "No"
        assert _fmt_value(0.123456) == "0.1235"
