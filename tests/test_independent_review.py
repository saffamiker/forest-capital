"""
tests/test_independent_review.py — May 25 2026.

Coverage for the Academic Review's independent second-opinion layer.
Three surfaces tested:

  1. extract_key_findings — pure function. Builds the five plain-text
     findings from the arbiter text + optional analytics snapshot +
     optional strategy_results. Missing data collapses to
     "Not stated by the primary review." — no fabrication.

  2. run_independent_review — the test-env stub path. ENVIRONMENT=test
     short-circuits to a deterministic Concerns stub so the contract
     tests never hit the real Gemini API.

  3. Parser helpers — _strip_fences, _parse_verdict, _normalise_verdict.
     Lock the JSON-tolerance contract so the live reviewer's output
     parses cleanly even when wrapped in fences or preceded by prose.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")


# ── extract_key_findings — the five canonical findings ───────────────────────


class TestExtractKeyFindings:
    """Pulls the five findings from the platform context. Each one
    is a plain-text string the independent reviewer reads as a human
    would; a missing source falls through to 'Not stated' explicitly."""

    def test_returns_all_five_canonical_finding_keys(self):
        from agents.independent_review import (
            FINDING_NAMES, extract_key_findings,
        )

        out = extract_key_findings("")
        # Every canonical key is present, even on empty input.
        assert set(out.keys()) == set(FINDING_NAMES)

    def test_empty_input_collapses_to_not_stated(self):
        from agents.independent_review import extract_key_findings

        out = extract_key_findings("")
        for value in out.values():
            assert "Not stated" in value

    def test_strategy_results_drive_the_best_strategy_finding(self):
        from agents.independent_review import extract_key_findings

        strategy_results = {
            "BENCHMARK": {"sharpe_ratio": 0.52, "cagr": 0.08,
                          "max_drawdown": -0.50},
            "REGIME_SWITCHING": {"sharpe_ratio": 0.63, "cagr": 0.10,
                                 "max_drawdown": -0.18},
            "MOMENTUM_ROTATION": {"sharpe_ratio": 0.58, "cagr": 0.09,
                                  "max_drawdown": -0.22},
        }
        out = extract_key_findings(
            "Arbiter text mentions regime break.",
            strategy_results=strategy_results,
        )
        # Best Sharpe is REGIME_SWITCHING.
        assert "REGIME_SWITCHING" in out["best_strategy_sharpe"]
        assert "0.63" in out["best_strategy_sharpe"]
        # Benchmark Sharpe surfaces as comparison.
        assert "0.52" in out["best_strategy_sharpe"]

    def test_arbiter_text_drives_regime_break_finding(self):
        from agents.independent_review import extract_key_findings

        arbiter = (
            "The 2022 regime break shifted equity-bond correlation from "
            "pre-2022 -0.05 to post-2022 +0.61.")
        out = extract_key_findings(arbiter)
        # The regime finding picks up pre/post-2022 markers.
        assert "Not stated" not in out["regime_break_significance"]
        assert "pre-2022" in out["regime_break_significance"].lower() \
            or "pre 2022" in out["regime_break_significance"].lower()

    def test_arbiter_text_drives_oos_finding(self):
        from agents.independent_review import extract_key_findings

        arbiter = (
            "Walk-forward out-of-sample testing retained 80% of "
            "in-sample Sharpe for the dynamic strategies.")
        out = extract_key_findings(arbiter)
        assert "Not stated" not in out["oos_validation"]
        assert "walk-forward" in out["oos_validation"].lower() \
            or "out-of-sample" in out["oos_validation"].lower()

    def test_analytics_snapshot_anchors_period_to_concrete_findings(self):
        from agents.independent_review import extract_key_findings

        snap = {
            "performance_range": {"start": "2002-07-31",
                                  "end": "2024-12-31",
                                  "n_months": 270},
            "risk_free_rate": 0.025,
            "strategy_count": 10,
        }
        arbiter = (
            "Walk-forward out-of-sample testing was validated against "
            "the holdout window with the dynamic strategies.")
        out = extract_key_findings(arbiter, analytics_snapshot=snap)
        # The concrete OOS finding got the study-period anchor appended.
        assert "2002-07-31" in out["oos_validation"]
        assert "2024-12-31" in out["oos_validation"]

    def test_not_stated_findings_do_not_get_period_anchor(self):
        """A "Not stated" finding should NOT have a study-period anchor
        appended — the anchor only makes sense against a concrete
        claim, not a placeholder."""
        from agents.independent_review import extract_key_findings

        snap = {
            "performance_range": {"start": "2002-07-31",
                                  "end": "2024-12-31"},
            "risk_free_rate": 0.025,
        }
        out = extract_key_findings("", analytics_snapshot=snap)
        # No concrete arbiter text → regime finding stays "Not stated"
        # and doesn't get the period suffix.
        assert "2002-07-31" not in out["regime_break_significance"]
        assert "Not stated" in out["regime_break_significance"]


# ── run_independent_review — test-env stub path ──────────────────────────────


class TestRunIndependentReviewTestEnv:
    """ENVIRONMENT=test short-circuits to the stub verdict before
    any Gemini call fires. Pins the test-env contract so the
    integration tests in main don't accidentally hit the real API."""

    def test_test_env_returns_stub_verdict(self, monkeypatch):
        # Test environment is set at module import; explicit assert.
        monkeypatch.setenv("ENVIRONMENT", "test")
        from agents.independent_review import (
            FINDING_NAMES, run_independent_review,
        )
        result = run_independent_review({k: "anything" for k in FINDING_NAMES})
        assert result["verdict"] == "Concerns"
        assert "test environment" in result["overall_reasoning"]
        # All five canonical findings are represented in per_finding.
        per = {p["finding"] for p in result["per_finding"]}
        assert per == set(FINDING_NAMES)
        # model="stub" so a frontend can tell this was a fallback.
        assert result["model"] == "stub"

    def test_missing_google_api_key_returns_stub_verdict(self, monkeypatch):
        # Force out of test env to exercise the API-key check; then
        # remove the key. The stub still wins because the env override
        # path falls through.
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        from agents.independent_review import run_independent_review
        result = run_independent_review({"best_strategy_sharpe": "x"})
        assert result["verdict"] == "Concerns"
        assert "GOOGLE_API_KEY" in result["overall_reasoning"]
        assert result["model"] == "stub"


# ── Parser helpers — JSON tolerance contract ─────────────────────────────────


class TestParserHelpers:

    def test_strip_fences_handles_json_fence(self):
        from agents.independent_review import _strip_fences
        wrapped = '```json\n{"verdict":"Plausible"}\n```'
        out = _strip_fences(wrapped)
        assert out == '{"verdict":"Plausible"}'

    def test_strip_fences_handles_bare_fence(self):
        from agents.independent_review import _strip_fences
        wrapped = '```\n{"verdict":"Plausible"}\n```'
        out = _strip_fences(wrapped)
        assert out == '{"verdict":"Plausible"}'

    def test_parse_verdict_handles_prose_preamble(self):
        from agents.independent_review import _parse_verdict
        raw = ('Here is my assessment:\n\n'
               '{"verdict":"Plausible","overall_reasoning":"ok",'
               '"per_finding":[]}')
        parsed = _parse_verdict(raw)
        assert parsed is not None
        assert parsed["verdict"] == "Plausible"

    def test_parse_verdict_returns_none_on_unparseable(self):
        from agents.independent_review import _parse_verdict
        assert _parse_verdict("no JSON here at all") is None
        assert _parse_verdict("") is None

    def test_normalise_verdict_pads_missing_findings(self):
        """Reviewer that only scored 3 of 5 findings — _normalise pads
        the other 2 with placeholder entries so the frontend renders
        the full five-row card."""
        from agents.independent_review import (
            FINDING_NAMES, _normalise_verdict,
        )
        parsed = {
            "verdict": "Concerns",
            "overall_reasoning": "ok",
            "per_finding": [
                {"finding": "best_strategy_sharpe",
                 "assessment": "fine", "concern": ""},
                {"finding": "regime_break_significance",
                 "assessment": "fine", "concern": ""},
                {"finding": "oos_validation",
                 "assessment": "fine", "concern": ""},
            ],
        }
        out = _normalise_verdict(parsed)
        # All five findings present.
        assert len(out["per_finding"]) == 5
        per_names = [p["finding"] for p in out["per_finding"]]
        assert per_names == list(FINDING_NAMES)
        # The two missing ones carry the "did not assess" placeholder.
        by_name = {p["finding"]: p for p in out["per_finding"]}
        assert "did not assess" in by_name["diversification_benefit"][
            "assessment"].lower()
        assert "did not assess" in by_name["factor_loadings_summary"][
            "assessment"].lower()

    def test_normalise_verdict_unknown_label_defaults_to_concerns(self):
        from agents.independent_review import _normalise_verdict
        parsed = {
            "verdict": "Fantastic",   # not a canonical verdict
            "overall_reasoning": "x",
            "per_finding": [],
        }
        out = _normalise_verdict(parsed)
        assert out["verdict"] == "Concerns"

    def test_normalise_verdict_resolves_label_form_too(self):
        """The reviewer might write `finding: "Best Strategy Sharpe"`
        (the display label) instead of the canonical key. _normalise
        accepts both."""
        from agents.independent_review import _normalise_verdict
        parsed = {
            "verdict": "Plausible",
            "overall_reasoning": "x",
            "per_finding": [{
                "finding":   "Best Strategy Sharpe",   # label form
                "assessment": "looks ok",
                "concern":    "",
            }],
        }
        out = _normalise_verdict(parsed)
        # The label form mapped back to the canonical key.
        best = next(p for p in out["per_finding"]
                    if p["finding"] == "best_strategy_sharpe")
        assert best["assessment"] == "looks ok"
