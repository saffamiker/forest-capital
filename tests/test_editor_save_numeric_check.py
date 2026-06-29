"""tests/test_editor_save_numeric_check.py -- June 28 2026.

Pins the touchpoint 5 hard-lock warning (editor save -- WARN,
not BLOCK).
"""
from __future__ import annotations

import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


class TestExtractPlainTextSkipsTokenValue:

    def test_token_value_nodes_excluded_from_scan(self):
        """The key difference vs the harness-time scanner: a
        content_json may already carry token_value nodes (from
        the dual-mode upgrade). The editor-save scanner must NOT
        flag the resolved values inside token_value attrs --
        those are by construction token-backed."""
        from tools.editor_save_numeric_check import (
            _extract_plain_text,
        )
        doc = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Sharpe of "},
                    {"type": "token_value", "attrs": {
                        "token": "{{OOS_SHARPE_BLEND}}",
                        "resolved": "0.86"}},
                    {"type": "text", "text": " is strong."},
                ]}]}
        text = _extract_plain_text(doc)
        # Token_value's "0.86" must NOT appear -- only the
        # plain-text nodes the operator could have typed.
        assert text == "Sharpe of  is strong."
        assert "0.86" not in text


class TestScannerEntryPoint:

    def test_empty_content_returns_empty(self):
        from tools.editor_save_numeric_check import (
            scan_editor_save_for_untoken_numerics,
        )
        assert scan_editor_save_for_untoken_numerics(
            None, {"{{X}}": "0.86"}) == []
        assert scan_editor_save_for_untoken_numerics(
            {"type": "doc", "content": []},
            {"{{X}}": "0.86"}) == []

    def test_no_substitution_table_returns_empty(self):
        """Without a substitution table the scanner has no way
        to know what's token-backed -- fail-open with no
        warnings."""
        from tools.editor_save_numeric_check import (
            scan_editor_save_for_untoken_numerics,
        )
        doc = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [
                    {"type": "text",
                     "text": "Sharpe is 0.86 over period."}]}]}
        assert scan_editor_save_for_untoken_numerics(
            doc, None) == []
        assert scan_editor_save_for_untoken_numerics(
            doc, {}) == []

    def test_untoken_value_in_plain_text_flagged(self):
        from tools.editor_save_numeric_check import (
            scan_editor_save_for_untoken_numerics,
        )
        doc = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [
                    {"type": "text",
                     "text": "The operator typed 0.86 here."}]}]}
        warns = scan_editor_save_for_untoken_numerics(
            doc,
            substitution_table={
                "{{OOS_SHARPE_BLEND}}": "0.86"})
        assert len(warns) == 1
        assert warns[0]["offending_value"] == "0.86"
        assert warns[0]["severity"] == "token_available"
        assert warns[0]["suggested_token"] == (
            "{{OOS_SHARPE_BLEND}}")

    def test_token_value_resolved_NOT_flagged(self):
        """Resolved values INSIDE token_value nodes are skipped
        by _extract_plain_text -- never reach the scanner."""
        from tools.editor_save_numeric_check import (
            scan_editor_save_for_untoken_numerics,
        )
        doc = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Sharpe of "},
                    {"type": "token_value", "attrs": {
                        "token": "{{OOS_SHARPE_BLEND}}",
                        "resolved": "0.86"}},
                    {"type": "text", "text": " is strong."},
                ]}]}
        warns = scan_editor_save_for_untoken_numerics(
            doc,
            substitution_table={
                "{{OOS_SHARPE_BLEND}}": "0.86"})
        assert warns == []

    def test_structural_prose_not_flagged(self):
        """The structural exemptions from PR #468 hold for the
        editor-save scanner too (same find_untoken_backed_numerics
        underneath)."""
        from tools.editor_save_numeric_check import (
            scan_editor_save_for_untoken_numerics,
        )
        doc = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [
                    {"type": "text",
                     "text": (
                         "The S&P 500 returned positively in the "
                         "100% equity strategy at p < 0.005.")}]}]}
        warns = scan_editor_save_for_untoken_numerics(
            doc, substitution_table={"{{X}}": "0.86"})
        assert warns == []


class TestEndpointWired:

    def test_patch_endpoint_imports_scanner(self):
        import inspect
        from main import editor_update_draft
        src = inspect.getsource(editor_update_draft)
        assert (
            "from tools.editor_save_numeric_check import"
            in src)
        assert "scan_editor_save_for_untoken_numerics" in src
        assert "log_editor_overrides" in src

    def test_response_carries_numeric_warnings_field(self):
        import inspect
        from main import editor_update_draft
        src = inspect.getsource(editor_update_draft)
        assert '"numeric_warnings"' in src

    def test_save_persists_regardless_of_warnings(self):
        """Source pin: the warning scan happens BEFORE persist
        but the warnings never block update_draft. The save is
        always attempted, warnings or no warnings."""
        import inspect
        from main import editor_update_draft
        src = inspect.getsource(editor_update_draft)
        scan_idx = src.find(
            "scan_editor_save_for_untoken_numerics")
        update_idx = src.find("await update_draft(")
        assert scan_idx > -1
        assert update_idx > -1
        # The update_draft call must come AFTER the scan
        # (warnings collected first, persist always fires).
        assert scan_idx < update_idx
        # The update_draft call is NOT gated by warning count
        # -- search for 'if not warnings' before the persist;
        # such a guard would mean blocking on warnings.
        between = src[scan_idx:update_idx]
        assert "if warnings" not in between
        assert "raise" not in between
