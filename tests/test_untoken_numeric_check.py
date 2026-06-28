"""tests/test_untoken_numeric_check.py -- June 28 2026.

Pins the hard-lock numeric guardrail (PR follow-up to PR-DM-Lite).
"""
from __future__ import annotations

import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


class TestFindUntokenBackedNumerics:

    def test_clean_text_returns_empty(self):
        from tools.untoken_numeric_check import (
            find_untoken_backed_numerics,
        )
        text = "All values come from {{OOS_SHARPE_BLEND}}."
        viols = find_untoken_backed_numerics(text, {})
        assert viols == []

    def test_unsupported_decimal_flagged(self):
        from tools.untoken_numeric_check import (
            find_untoken_backed_numerics,
        )
        text = "The blend Sharpe is 0.86 versus benchmark."
        viols = find_untoken_backed_numerics(
            text, substitution_table={})
        assert len(viols) == 1
        assert viols[0].raw_value == "0.86"
        assert viols[0].severity == "unsupported"
        assert viols[0].suggested_token is None

    def test_value_matching_substitution_table_gets_swap_suggestion(
            self):
        from tools.untoken_numeric_check import (
            find_untoken_backed_numerics,
        )
        text = "The blend Sharpe is 0.86 versus benchmark."
        viols = find_untoken_backed_numerics(
            text,
            substitution_table={
                "{{OOS_SHARPE_BLEND}}": "0.86"})
        assert len(viols) == 1
        assert viols[0].severity == "token_available"
        assert viols[0].suggested_token == "{{OOS_SHARPE_BLEND}}"

    def test_token_protected_numeric_not_flagged(self):
        """Numeric INSIDE a {{TOKEN}} (e.g. token with digits in
        its name) is left alone."""
        from tools.untoken_numeric_check import (
            find_untoken_backed_numerics,
        )
        # {{POST_2022_CORR}} contains 2022 -- should not flag.
        text = "The {{POST_2022_CORR}} value confirms."
        viols = find_untoken_backed_numerics(text, {})
        # No flags -- 2022 is inside a token + caught by the
        # year allowlist anyway.
        assert viols == []

    def test_year_allowlisted(self):
        from tools.untoken_numeric_check import (
            find_untoken_backed_numerics,
        )
        text = "Smith (2020) found similar results."
        viols = find_untoken_backed_numerics(text, {})
        assert viols == []

    def test_anchor_value_allowed_without_token(self):
        from tools.untoken_numeric_check import (
            find_untoken_backed_numerics,
        )
        text = "The benchmark Sharpe is 0.43 over the period."
        viols = find_untoken_backed_numerics(
            text, substitution_table={},
            numeric_anchors={"benchmark_sharpe": 0.43})
        # 0.43 matches the anchor -- allowed.
        assert viols == []

    def test_single_digit_allowlisted(self):
        from tools.untoken_numeric_check import (
            find_untoken_backed_numerics,
        )
        text = "There are 3 strategies in the bundle."
        viols = find_untoken_backed_numerics(text, {})
        assert viols == []


# ── Structural-prose exemptions (June 28 2026) ────────────────


class TestStructuralProseExemptions:

    def test_sp_500_index_name_not_flagged(self):
        from tools.untoken_numeric_check import (
            find_untoken_backed_numerics,
        )
        text = ("The S&P 500 benchmark returned over the "
                "study period.")
        viols = find_untoken_backed_numerics(text, {})
        assert viols == []

    def test_definitional_100pct_equity_not_flagged(self):
        from tools.untoken_numeric_check import (
            find_untoken_backed_numerics,
        )
        text = ("The benchmark holds 100% equity throughout "
                "the period.")
        viols = find_untoken_backed_numerics(text, {})
        assert viols == []

    def test_60_40_strategy_reference_not_flagged(self):
        from tools.untoken_numeric_check import (
            find_untoken_backed_numerics,
        )
        text = "The 60/40 portfolio is the academic baseline."
        viols = find_untoken_backed_numerics(text, {})
        assert viols == []

    def test_statistical_threshold_not_flagged(self):
        from tools.untoken_numeric_check import (
            find_untoken_backed_numerics,
        )
        for line in [
            "The result is significant at p < 0.005.",
            "p = 0.001 across all strategies.",
            "alpha = 0.05 for the FDR correction.",
            "Below the p <= 0.10 threshold.",
        ]:
            viols = find_untoken_backed_numerics(line, {})
            assert viols == [], f"flagged: {line!r}"

    def test_substitution_table_value_NEVER_exempted(self):
        """Operator constraint: 'Do not exempt any value that
        appears in the substitution table.' Even if the value
        coincidentally appears inside a structural pattern, if
        a token would produce it the LLM must swap, not skip."""
        from tools.untoken_numeric_check import (
            find_untoken_backed_numerics,
        )
        # 0.005 inside a structural p-value pattern AND in the
        # substitution table -- must still flag as
        # token_available so the LLM swaps the literal.
        text = "The result is significant at p < 0.005."
        viols = find_untoken_backed_numerics(
            text,
            substitution_table={"{{P_VALUE_THRESHOLD}}": "0.005"})
        assert len(viols) == 1
        assert viols[0].severity == "token_available"
        assert viols[0].suggested_token == "{{P_VALUE_THRESHOLD}}"


class TestBuildCorrectionPrompt:

    def test_swap_lines_present_when_token_available(self):
        from tools.untoken_numeric_check import (
            NumericViolation, build_correction_prompt,
        )
        v = NumericViolation(
            raw_value="0.86",
            sentence="Sharpe is 0.86 over the period.",
            suggested_token="{{OOS_SHARPE_BLEND}}",
            severity="token_available")
        prompt = build_correction_prompt(
            "Write Section 1.", [v], iteration=1)
        assert "{{OOS_SHARPE_BLEND}}" in prompt
        assert "REPLACE these numerics" in prompt
        assert "pass 1/3" in prompt

    def test_rephrase_lines_when_no_token(self):
        from tools.untoken_numeric_check import (
            NumericViolation, build_correction_prompt,
        )
        v = NumericViolation(
            raw_value="71.7%",
            sentence="Max concentration was 71.7%.",
            suggested_token=None,
            severity="unsupported")
        prompt = build_correction_prompt(
            "Write Section B.", [v], iteration=2)
        assert "71.7%" in prompt
        assert "REPHRASE these sentences" in prompt
        assert "pass 2/3" in prompt


class TestUntokenNumericLockError:

    def test_error_payload_carries_violations(self):
        from tools.untoken_numeric_check import (
            NumericViolation, UntokenNumericLockError,
        )
        viols = [
            NumericViolation(
                raw_value="0.86",
                sentence="Sharpe is 0.86.",
                suggested_token="{{OOS_SHARPE_BLEND}}",
                severity="token_available"),
            NumericViolation(
                raw_value="71.7%",
                sentence="Max was 71.7%.",
                severity="unsupported"),
        ]
        err = UntokenNumericLockError(
            "executive_brief", "section_1", viols)
        msg = str(err)
        assert "executive_brief" in msg
        assert "section_1" in msg
        assert "2 untoken-backed numeric" in msg
        assert err.violations == viols
        assert err.document_type == "executive_brief"


class TestHarnessLoopWired:
    """Source-inspection pins -- the hard-lock loop must be in
    harness_narrative + the protected document types must be
    recognised."""

    def test_harness_imports_untoken_check(self):
        import inspect
        from tools.academic_export import harness_narrative
        src = inspect.getsource(harness_narrative)
        assert "from tools.untoken_numeric_check import" in src
        assert "find_untoken_backed_numerics" in src
        assert "UntokenNumericLockError" in src

    def test_harness_has_max_passes_constant(self):
        from tools.academic_export import _UNTOKEN_LOCK_MAX_PASSES
        assert _UNTOKEN_LOCK_MAX_PASSES == 3

    def test_harness_protects_brief_and_appendix(self):
        """Source pin: the _PROTECTED frozenset in
        harness_narrative names executive_brief +
        analytical_appendix."""
        import inspect
        from tools.academic_export import harness_narrative
        src = inspect.getsource(harness_narrative)
        # Look for the protected-set declaration body.
        assert '"executive_brief"' in src
        assert '"analytical_appendix"' in src

    def test_hard_lock_scans_raw_text_not_substituted(self):
        """REGRESSION pin -- the hard-lock MUST scan the raw
        pre-substitution text (looked up via the
        _raw_per_substituted stash) rather than the substituted
        final_text. Otherwise legitimate substituted values
        (e.g. '+0.57' from {{POST_2022_EQ_IG_CORR}}) flag as
        untoken-backed numerics and the lock recommends
        swapping for the very token that already produced them
        -- an infinite loop until the 3-pass cap raises."""
        import inspect
        from tools.academic_export import harness_narrative
        src = inspect.getsource(harness_narrative)
        # The hard-lock loop must reference the raw stash AND
        # pass raw_for_scan (not final_text) to the scanner.
        assert "_raw_per_substituted.get(" in src
        assert "raw_for_scan = _raw_per_substituted.get(" in src
        assert "raw_for_scan," in src
        # Inverse pin -- the scanner call must NOT pass
        # final_text directly (legacy buggy form).
        # The scanner call after raw_for_scan setup uses
        # raw_for_scan, not final_text.
