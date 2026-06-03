"""tests/test_compare_council_metrics.py

Mock-only tests for scripts/compare_council_metrics.py. The script is
operator infrastructure — its --confirm-gated path makes 10 real
council deliberations and is not a unit-test target. The mocks here
cover:

  - The --confirm safety gate (exit 1 without --confirm or --dry-run).
  - --dry-run prints a zero table and never imports the heavy modules.
  - The classifier dispatch — the 5 questions classify cleanly so
    the typed path goes to the right resolver.
  - The CIO-input extraction mirrors main.py's live-endpoint path.
  - The HMM-state extraction handles both context shapes.
  - The reduction-percentage formatter renders the right strings.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)

import pytest

from scripts.compare_council_metrics import (  # noqa: E402
    _QUESTIONS, _fmt_int, _fmt_float, _fmt_pct_reduction,
    _hmm_state_from_context, _per_agent_cio_input, main,
)


# ── Safety gate ───────────────────────────────────────────────────────


class TestSafetyGate:
    """Without --confirm or --dry-run the script must refuse to run.
    A real run is 10 council deliberations and 10-30 dollars of
    LLM spend; the gate is the only thing standing between a typo
    in the operator's command line and an unexpected charge."""

    def test_without_flags_exits_1(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["compare_council_metrics"])
        rc = main()
        assert rc == 1
        err = capsys.readouterr().err
        assert "Refusing to run" in err
        assert "--confirm" in err
        assert "--dry-run" in err

    def test_dry_run_exits_0_without_confirm(self, monkeypatch, capsys):
        """--dry-run alone is the safe path — no LLM, no DB write."""
        monkeypatch.setattr(
            sys, "argv",
            ["compare_council_metrics", "--dry-run"])
        rc = main()
        assert rc == 0
        out = capsys.readouterr().out
        # Five question labels rendered in the table.
        for label, _ in _QUESTIONS:
            assert label in out
        assert "dry-run" in out


# ── Question set matches the spec ─────────────────────────────────────


class TestQuestionSet:
    """The user's spec for this script names exactly these five
    questions. Pinned verbatim so an accidental edit gets caught."""

    EXPECTED = {
        "REGIME":
            "What is the current market regime and how confident are you?",
        "RECOMMENDATION":
            "What allocation does the council recommend given current "
            "conditions?",
        "RISK":
            "What is the downside risk profile of the current portfolio?",
        "STATISTICAL":
            "Is the portfolio's outperformance statistically significant?",
        "FORWARD":
            "What is the 6-month forward outlook for the blend?",
    }

    def test_questions_match_spec_verbatim(self):
        actual = dict(_QUESTIONS)
        assert actual == self.EXPECTED


# ── Classifier dispatch ──────────────────────────────────────────────


class TestClassifierDispatch:
    """Each baseline question is worded to trigger one and only one
    bundle. If the classifier ever fails to clean-classify these
    five, the table's typed-path column would collapse to 'full' for
    every row and the comparison would lose its signal."""

    def test_every_question_classifies_to_its_label(self):
        from tools.council_question_bundles import classify_question
        expected = {
            "REGIME":         "regime",
            "RECOMMENDATION": "recommendation",
            "RISK":           "risk",
            "STATISTICAL":    "statistical",
            "FORWARD":        "forward",
        }
        for label, question in _QUESTIONS:
            got = classify_question(question)
            assert got == expected[label], (
                f"Question for {label} expected to classify as "
                f"{expected[label]!r} but got {got!r}: {question}")


# ── CIO input extraction ─────────────────────────────────────────────


class TestCIOInputExtraction:
    """Mirrors main.py:5387-5391's extraction. The metric the script
    prints in the comparison table MUST match what the row writer
    stamps in council_query_metrics.cio_input_tokens — otherwise
    one number on screen and another in the dashboard, which is the
    failure mode PR #266 just fixed."""

    def test_extracts_cio_input_when_present(self):
        usage = {"per_agent": {"cio": {"input_tokens": 12345}}}
        assert _per_agent_cio_input(usage) == 12345

    def test_returns_none_when_cio_label_missing(self):
        usage = {"per_agent": {"equity_analyst": {"input_tokens": 1}}}
        assert _per_agent_cio_input(usage) is None

    def test_returns_none_when_per_agent_empty(self):
        assert _per_agent_cio_input({"per_agent": {}}) is None

    def test_returns_none_when_per_agent_missing(self):
        assert _per_agent_cio_input({}) is None

    def test_returns_none_when_cio_is_not_a_dict(self):
        # Shouldn't happen in practice, but the extractor must
        # tolerate it — collect_usage() can hypothetically return
        # an unexpected shape on a partial failure.
        usage = {"per_agent": {"cio": "not-a-dict"}}
        assert _per_agent_cio_input(usage) is None


# ── HMM state extraction ─────────────────────────────────────────────


class TestHMMStateExtraction:
    """Two context shapes carry HMM state:
      - regime bundle: ctx['regime']['hmm_state'] + ['hmm_confidence']
      - recommendation_context: ctx['hmm']['state'] + ['confidence']
    The extractor must handle both so the alignment score lands
    regardless of which bundle ran on the typed path."""

    def test_regime_bundle_shape(self):
        ctx = {"regime": {"hmm_state": "BULL", "hmm_confidence": 0.85}}
        state, conf = _hmm_state_from_context(ctx)
        assert state == "BULL"
        assert conf == 0.85

    def test_recommendation_context_shape(self):
        ctx = {"hmm": {"state": "BEAR", "confidence": 0.62}}
        state, conf = _hmm_state_from_context(ctx)
        assert state == "BEAR"
        assert conf == 0.62

    def test_neither_shape_returns_none_pair(self):
        ctx = {"unrelated": "shape"}
        assert _hmm_state_from_context(ctx) == (None, None)

    def test_none_context_returns_none_pair(self):
        assert _hmm_state_from_context(None) == (None, None)

    def test_non_dict_context_returns_none_pair(self):
        # Defensive — recommendation_context() can return None on a
        # cold cache, and asyncio.gather can hand back unexpected
        # shapes when wrapped wrong.
        assert _hmm_state_from_context("string") == (None, None)


# ── Formatters ───────────────────────────────────────────────────────


class TestFormatters:
    """The table is the script's user surface. Off-by-one or wrong
    sign on the reduction column would mislead the user reading it,
    so the rendering is pinned."""

    def test_fmt_int_renders_thousands_separators(self):
        # int formatter must right-align in a 9-char field for the
        # table to stay tidy.
        s = _fmt_int(155396)
        assert "155,396" in s

    def test_fmt_int_handles_none(self):
        assert "--" in _fmt_int(None)

    def test_fmt_int_handles_non_int(self):
        assert "--" in _fmt_int("oops")

    def test_fmt_float_renders_two_decimals(self):
        s = _fmt_float(0.4946).strip()
        assert s == "0.49"

    def test_fmt_float_handles_none(self):
        assert "--" in _fmt_float(None)

    def test_pct_reduction_negative_when_typed_smaller(self):
        # Baseline 100K, typed 60K → 40K saved → -40.0% (negative
        # because the typed column is LOWER; the sign carries the
        # "reduction" meaning).
        s = _fmt_pct_reduction(100_000, 60_000).strip()
        assert s.startswith("-40.0")

    def test_pct_reduction_positive_when_typed_larger(self):
        # Surfaced for debug — typed should not exceed baseline in
        # normal operation. A '+' result is the operator's signal.
        s = _fmt_pct_reduction(100, 120).strip()
        assert s.startswith("+20.0")

    def test_pct_reduction_handles_missing_data(self):
        # Either side missing → '--', never a divide-by-zero crash.
        assert "--" in _fmt_pct_reduction(None, 50)
        assert "--" in _fmt_pct_reduction(50, None)
        assert "--" in _fmt_pct_reduction(0, 50)

    def test_pct_reduction_handles_zero_baseline(self):
        # No divide-by-zero — graceful '--'.
        assert "--" in _fmt_pct_reduction(0, 100)
