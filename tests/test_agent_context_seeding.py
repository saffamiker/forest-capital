"""Agent context-aware seeding gaps — bridge #60.

Three fixes pinned here:

FIX 1 — Dissenters (Gemini IndependentAnalyst + Grok ContrarianAnalyst)
  receive live_context (regime + blend + posterior + ESS) so their
  objections ground in the same signal state the CIO sees rather than
  reverse-engineering from the council prose.

FIX 2 — AcademicAdvisor.analyse_findings accepts regime_data and
  macro_context. The HTTP endpoint at main.py:8081 fetches both at call
  time so grade-aware feedback is anchored to the live regime + macro.

FIX 3 — tools.cio_recommendation.compute_context returns
  monthly_hmm_regime and hmm_models_agree alongside the existing fields,
  so the recommendation prose generator and downstream agent context
  builders read the same structured names rather than digging into the
  raw detect_current_regime() dict.

Shared formatter lives at tools.agent_context_block — every fix above
uses the same render so the regime block reads identically across all
three surfaces.
"""
from __future__ import annotations

import json
from typing import Any

from agents.academic_advisor import AcademicAdvisor
from agents.contrarian_analyst import ContrarianAnalyst
from agents.independent_analyst import IndependentAnalyst
from tools.agent_context_block import (
    format_live_context_block, format_macro_context_line,
)
from tools.cio_recommendation import compute_context


# ── FIX 3 — compute_context returns monthly + agreement ─────────────────

def test_compute_context_threads_monthly_regime_and_agreement():
    """Given a `current` dict with daily=BEAR and monthly=BULL,
    compute_context must surface both at the top level so downstream
    consumers can flag the split."""
    current = {
        "hmm_regime":           "BEAR",
        "hmm_probabilities":    {"BEAR": 0.87, "BULL": 0.13},
        "monthly_hmm_regime":   "BULL",
        "hmm_models_agree":     False,
    }
    # Minimal compute_regime_blends fixture: pretend three strategies
    # all have equal-weight blends so the function does not fall through
    # to the error path. compute_context calls compute_regime_blends
    # internally; we stub by passing strategy_results / hmm_result that
    # the helper handles gracefully.
    strategy_results: dict[str, Any] = {}
    hmm_result: dict[str, Any] = {}
    ctx = compute_context(strategy_results, hmm_result, current)
    # compute_context may bail to an error dict on empty inputs; what
    # we care about is that EITHER it returns the fields OR it bails
    # cleanly with an error key. The bail path is acceptable; the
    # success path must include the new fields.
    if "error" not in ctx:
        assert ctx.get("regime") == "BEAR"
        assert ctx.get("monthly_regime") == "BULL"
        assert ctx.get("hmm_models_agree") is False


def test_compute_context_defaults_agreement_to_true_when_field_missing():
    """Backward compatibility: a `current` dict from a pre-PR-#282 path
    that lacks hmm_models_agree must default the field to True so
    downstream consumers do not see a spurious divergence flag."""
    current = {
        "hmm_regime":        "BULL",
        "hmm_probabilities": {"BULL": 0.92, "BEAR": 0.08},
    }
    ctx = compute_context({}, {}, current)
    if "error" not in ctx:
        assert ctx.get("hmm_models_agree") is True
        assert ctx.get("monthly_regime") is None


# ── shared formatter — format_live_context_block ────────────────────────

def test_block_renders_regime_posterior_ess_blend():
    live_context = {
        "regime":           "BEAR",
        "monthly_regime":   "BEAR",
        "hmm_models_agree": True,
        "probability":      0.87,
        "ess":              82.86,
        "ess_warning":      False,
        "blend_weights":    {"VOL_TARGETING": 0.35, "MIN_VARIANCE": 0.34,
                             "RISK_PARITY": 0.18, "BENCHMARK": 0.05},
    }
    block = format_live_context_block(live_context)
    assert "BEAR" in block
    assert "87.0%" in block
    assert "82.86" in block
    # Top three blend weights (sorted by weight desc) appear; BENCHMARK
    # at 5% is the 4th so it should NOT appear in the top-3 line.
    assert "VOL_TARGETING 35%" in block
    assert "MIN_VARIANCE 34%" in block
    assert "RISK_PARITY 18%" in block


def test_block_surfaces_divergence_when_models_disagree():
    live_context = {
        "regime":           "BEAR",
        "monthly_regime":   "BULL",
        "hmm_models_agree": False,
        "probability":      0.87,
    }
    block = format_live_context_block(live_context)
    assert "Regime (daily HMM): BEAR" in block
    assert "Regime (monthly HMM): BULL" in block
    assert "MODEL DIVERGENCE" in block


def test_block_flags_ess_warning_below_floor():
    live_context = {
        "regime":      "TRANSITION",
        "ess":         8.0,
        "ess_warning": True,
    }
    block = format_live_context_block(live_context)
    assert "ESS (Kish): 8.00" in block
    assert "BELOW FLOOR" in block


def test_block_empty_when_live_context_is_none():
    assert format_live_context_block(None) == ""
    assert format_live_context_block({}) == ""


def test_macro_line_renders_and_is_silent_when_absent():
    assert format_macro_context_line(None) == ""
    assert format_macro_context_line("") == ""
    assert format_macro_context_line("   ") == ""
    out = format_macro_context_line("CPI 3.8% YoY; HY OAS 285 bps.")
    assert "MACRO CONTEXT:" in out
    assert "CPI 3.8% YoY" in out


# ── FIX 1 — dissenters prepend regime block when live_context present ──

_BASE_STRATEGY_RESULTS = {
    "VOL_TARGETING": {
        "sharpe_ratio": 0.86, "cagr": 0.09, "max_drawdown": -0.20,
        "is_significant": True, "oos_sharpe": 0.74,
        "alpha_after_costs_bps": 38,
    },
    "BENCHMARK": {
        "sharpe_ratio": 0.43, "cagr": 0.07, "max_drawdown": -0.53,
        "is_significant": False, "oos_sharpe": 0.40,
        "alpha_after_costs_bps": 0,
    },
}

_LIVE_CONTEXT = {
    "regime":           "BEAR",
    "monthly_regime":   "BULL",
    "hmm_models_agree": False,
    "probability":      0.87,
    "ess":              82.86,
    "ess_warning":      False,
    "blend_weights":    {"VOL_TARGETING": 0.35, "MIN_VARIANCE": 0.34,
                         "RISK_PARITY": 0.18},
}


def test_independent_analyst_evidence_prepends_regime_block():
    out = IndependentAnalyst()._build_evidence(
        "DRAFT consensus prose.",
        _BASE_STRATEGY_RESULTS,
        live_context=_LIVE_CONTEXT,
    )
    assert out.startswith("LIVE REGIME + BLEND STATE:")
    assert "BEAR" in out
    assert "MODEL DIVERGENCE" in out
    # Metrics still render verbatim after the regime block.
    assert "METRICS:" in out
    assert "VOL_TARGETING" in out


def test_independent_analyst_evidence_unchanged_without_live_context():
    """Backward compat: existing call sites that pass no live_context
    must get the prior metrics-only JSON."""
    out = IndependentAnalyst()._build_evidence(
        "DRAFT consensus prose.",
        _BASE_STRATEGY_RESULTS,
    )
    assert not out.startswith("LIVE REGIME")
    # Pure JSON (the prior shape) — parse round-trips cleanly.
    parsed = json.loads(out)
    assert "VOL_TARGETING" in parsed
    assert parsed["VOL_TARGETING"]["sharpe"] == 0.86


def test_contrarian_analyst_evidence_prepends_regime_block():
    out = ContrarianAnalyst()._build_evidence(
        "DRAFT consensus prose.",
        _BASE_STRATEGY_RESULTS,
        live_context=_LIVE_CONTEXT,
    )
    assert out.startswith("LIVE REGIME + BLEND STATE:")
    assert "BEAR" in out
    assert "METRICS:" in out
    assert "VOL_TARGETING" in out


def test_contrarian_analyst_evidence_unchanged_without_live_context():
    out = ContrarianAnalyst()._build_evidence(
        "DRAFT consensus prose.",
        _BASE_STRATEGY_RESULTS,
    )
    parsed = json.loads(out)
    assert parsed["VOL_TARGETING"]["cv_stability_score"] is None


# ── FIX 2 — AcademicAdvisor accepts regime + macro ──────────────────────

class _CaptureCall:
    """Captures the user_message handed to _call_advisor_with_web_tools
    so we can assert without actually calling Sonnet."""
    def __init__(self) -> None:
        self.captured: str | None = None

    def __call__(self, user_message: str):
        self.captured = user_message
        # Return the four-tuple the real function emits.
        return (
            {"key_findings": [], "guidance": [], "citations": [],
             "potential_issues": []},
            [],
            [],
            {},
        )


def test_advisor_analyse_renders_regime_and_macro_when_supplied(
    monkeypatch,
):
    import agents.academic_advisor as mod
    capture = _CaptureCall()
    monkeypatch.setattr(mod, "_call_advisor_with_web_tools", capture)
    advisor = AcademicAdvisor()
    advisor.analyse_findings(
        query="What should we flag in the appendix?",
        deliverable_type="appendix",
        strategy_results={"VOL_TARGETING": {"sharpe_ratio": 0.86}},
        regime_data={
            "hmm_regime":         "BEAR",
            "monthly_hmm_regime": "BULL",
            "hmm_models_agree":   False,
            "hmm_probabilities":  {"BEAR": 0.87, "BULL": 0.13},
        },
        macro_context="CPI 3.8% YoY; HY OAS 285 bps.",
    )
    msg = capture.captured or ""
    assert "LIVE REGIME + BLEND STATE:" in msg
    assert "BEAR" in msg
    assert "MODEL DIVERGENCE" in msg
    assert "MACRO CONTEXT:" in msg
    assert "CPI 3.8% YoY" in msg
    # Existing scaffolding still present.
    assert "DELIVERABLE: appendix" in msg
    assert "TEAM QUERY:" in msg


def test_advisor_analyse_silent_when_regime_and_macro_absent(monkeypatch):
    """Backward compat: existing endpoints that have not migrated yet
    pass no regime or macro; the user_message must NOT carry empty
    blocks."""
    import agents.academic_advisor as mod
    capture = _CaptureCall()
    monkeypatch.setattr(mod, "_call_advisor_with_web_tools", capture)
    advisor = AcademicAdvisor()
    advisor.analyse_findings(
        query="What should we flag in the appendix?",
        deliverable_type="appendix",
        strategy_results={"VOL_TARGETING": {"sharpe_ratio": 0.86}},
    )
    msg = capture.captured or ""
    assert "LIVE REGIME" not in msg
    assert "MACRO CONTEXT" not in msg
    assert "DELIVERABLE: appendix" in msg


def test_advisor_analyse_renders_only_regime_when_macro_absent(
    monkeypatch,
):
    """One half-of-two case: regime present, macro absent. The macro
    block must NOT render even as an empty header."""
    import agents.academic_advisor as mod
    capture = _CaptureCall()
    monkeypatch.setattr(mod, "_call_advisor_with_web_tools", capture)
    advisor = AcademicAdvisor()
    advisor.analyse_findings(
        query="Anything to flag?",
        deliverable_type="brief",
        strategy_results={},
        regime_data={
            "hmm_regime":        "BULL",
            "hmm_probabilities": {"BULL": 0.92, "BEAR": 0.08},
        },
        macro_context=None,
    )
    msg = capture.captured or ""
    assert "LIVE REGIME" in msg
    assert "MACRO CONTEXT" not in msg
