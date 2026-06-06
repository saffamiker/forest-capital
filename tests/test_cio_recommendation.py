"""Tests for tools/cio_recommendation.py. The pure context assembly and
the four-component recommendation (with its deterministic fail-open) are
covered here; the data_hash cache persistence and the live-context build
are Render-side (DB + HMM fit) and exercised there."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")

import pandas as pd  # noqa: E402

from agents.recommendation_prompts import MANDATORY_LIMITATIONS  # noqa: E402
from tools import cio_recommendation as cio  # noqa: E402


def _month_dates(n: int, start: str = "2012-01-31") -> list[str]:
    return [d.date().isoformat()
            for d in pd.date_range(start, periods=n, freq="ME")]


def _strategy_results(n_months: int = 120, n_strats: int = 6,
                      seed: int = 11) -> dict:
    rng = np.random.default_rng(seed)
    dates = _month_dates(n_months)
    out: dict = {}
    for k in range(n_strats):
        rets = rng.normal(0.004 + 0.001 * k, 0.02 + 0.004 * k, n_months)
        out[f"STRAT_{k}"] = {"monthly_returns": [
            [dates[t], round(float(rets[t]), 6)] for t in range(n_months)]}
    return out


def _hmm_result(n_months: int = 120) -> dict:
    dates = _month_dates(n_months)
    half = n_months // 2
    bull = [0.8] * half + [0.2] * (n_months - half)
    bear = [0.1] * half + [0.7] * (n_months - half)
    trans = [round(1.0 - b - e, 6) for b, e in zip(bull, bear)]
    return {"dates": dates,
            "historical_probs": {"BULL": bull, "BEAR": bear,
                                 "TRANSITION": trans}}


_CURRENT = {"hmm_regime": "BEAR",
            "hmm_probabilities": {"BULL": 0.15, "BEAR": 0.65,
                                  "TRANSITION": 0.20}}


class TestComputeContext:

    def test_assembles_live_context(self):
        ctx = cio.compute_context(
            _strategy_results(120, 6), _hmm_result(120), _CURRENT)
        assert "error" not in ctx
        assert ctx["regime"] == "BEAR"
        assert ctx["probability"] == pytest.approx(0.65, abs=1e-6)
        assert set(ctx["posterior"].keys()) == {"BULL", "BEAR", "TRANSITION"}
        assert sum(ctx["blend_weights"].values()) == pytest.approx(
            1.0, abs=1e-5)
        assert ctx["ess"] is not None
        assert isinstance(ctx["ess_warning"], bool)

    def test_ess_warning_when_below_floor(self):
        # A high min_effective_n is not directly settable here, but a
        # small synthetic posterior mass for the current regime yields a
        # low ESS. We assert the flag is a bool consistent with the floor.
        ctx = cio.compute_context(
            _strategy_results(120, 6), _hmm_result(120), _CURRENT)
        floor = 2.0 * len(ctx["names"])
        assert ctx["ess_warning"] == (ctx["ess"] < floor)

    def test_error_propagates(self):
        ctx = cio.compute_context({}, _hmm_result(120), _CURRENT)
        assert ctx["error"] == "insufficient_strategy_return_data"


class TestGenerateRecommendation:

    def test_fails_open_to_deterministic_four_component(self):
        # No API key in the test env -> call_claude raises -> deterministic.
        ctx = cio.compute_context(
            _strategy_results(120, 6), _hmm_result(120), _CURRENT)
        rec = cio.generate_recommendation(ctx, macro_context="")
        assert rec["_model"] == cio._DETERMINISTIC
        assert rec["signal"] and rec["dissenting_view"]
        # the card's four one-sentence fields are all present
        assert rec["recommendation"] and rec["key_risk"]
        assert "—" not in rec["recommendation"]
        assert "—" not in rec["key_risk"]
        # confidence carries the four sub-fields
        assert set(rec["confidence"].keys()) == {
            "regime", "probability", "ess", "ess_warning"}
        # the four mandatory limitations, verbatim
        assert rec["limitations"] == list(MANDATORY_LIMITATIONS)
        assert len(rec["limitations"]) == 4
        # project prose rule
        assert "—" not in rec["signal"]
        assert "—" not in rec["dissenting_view"]


# ── get_prior_recommendation (June 5 2026) ────────────────────────────────


class TestGetPriorRecommendation:
    """The helper that backs Section C of the transparency structure.
    Returns the most recent recommendation written under a data_hash
    that differs from the current one — None when no such row exists.
    Fail-open contract: any DB error returns None and the caller
    omits Section C cleanly."""

    @pytest.mark.asyncio
    async def test_returns_none_when_db_unavailable(self, monkeypatch):
        # Test env has no DB, so _DB_AVAILABLE is False and the helper
        # short-circuits without ever opening a session. Pins the
        # fail-open contract.
        from tools import cio_recommendation as cio_mod

        monkeypatch.setattr(cio_mod, "_DB_AVAILABLE", False)
        result = await cio_mod.get_prior_recommendation("any-hash-here")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_current_hash_empty(self):
        # Empty hash means we can't tell what's "different from current"
        # so the helper refuses to query (returning the latest row would
        # mis-attribute it as a prior). Pins the safety check.
        from tools import cio_recommendation as cio_mod

        assert await cio_mod.get_prior_recommendation("") is None
        # The async signature is callable — keyword-mistyping check.
        assert await cio_mod.get_prior_recommendation(None) is None  # type: ignore[arg-type]

    def test_signature_matches_other_readers(self):
        # The three DB readers (get_cached_for_hash, get_latest_
        # recommendation, get_prior_recommendation) should follow the
        # same shape — all async, all returning dict | None, all fail-
        # open. This pin catches a future signature drift.
        import inspect

        from tools import cio_recommendation as cio_mod

        sig = inspect.signature(cio_mod.get_prior_recommendation)
        assert list(sig.parameters.keys()) == ["current_data_hash"]
        assert inspect.iscoroutinefunction(cio_mod.get_prior_recommendation)


# ── CIO system prompt: Section A/B/C mandate (June 5 2026) ────────────────


class TestSystemPromptStructureMandate:
    """The CIO system prompt is the contract for every council output.
    Section A / B / C are mandatory; this test pins each section's
    name + the CONDITIONAL nature of Section C so a later prompt
    refactor doesn't quietly drop the structure."""

    def test_three_sections_named_in_system_prompt(self):
        from agents.cio import _SYSTEM_PROMPT

        # The three section headers, exactly as the model is told to
        # write them. A drift on any of these breaks parsing of the
        # response in the frontend (where the panel reads the section
        # boundaries).
        assert "### A. Signal snapshot" in _SYSTEM_PROMPT
        assert "### B. Weight justification" in _SYSTEM_PROMPT
        assert "### C. Shift narrative" in _SYSTEM_PROMPT

    def test_section_c_is_conditional(self):
        from agents.cio import _SYSTEM_PROMPT

        # Section C only fires when a prior recommendation is in the
        # data block. The CONDITIONAL keyword + the "OMIT Section C
        # entirely" instruction are the two halves of that contract.
        assert "CONDITIONAL" in _SYSTEM_PROMPT
        assert "prior_recommendation" in _SYSTEM_PROMPT
        assert "OMIT Section C" in _SYSTEM_PROMPT

    def test_meta_questions_skip_the_structure(self):
        # Meta questions (peer-reviewer anticipation, framing) don't
        # get A/B/C because they aren't strategy recommendations.
        # Pinned so a later refactor doesn't accidentally apply the
        # mandate universally.
        from agents.cio import _SYSTEM_PROMPT

        assert "META questions" in _SYSTEM_PROMPT
        assert "skip the A/B/C structure" in _SYSTEM_PROMPT

    def test_confidence_mirrors_context(self):
        ctx = cio.compute_context(
            _strategy_results(120, 6), _hmm_result(120), _CURRENT)
        rec = cio.generate_recommendation(ctx)
        assert rec["confidence"]["regime"] == "BEAR"
        assert rec["confidence"]["ess_warning"] == ctx["ess_warning"]

    def test_context_error_short_circuits(self):
        rec = cio.generate_recommendation({"error": "boom"})
        assert rec["error"] == "boom"

    def test_deterministic_always_has_mandatory_limitations(self):
        rec = cio._deterministic_recommendation(
            {"regime": "BULL", "probability": 0.7, "ess": 300.0,
             "ess_warning": False, "blend_weights": {"A": 0.6, "B": 0.4}})
        assert rec["limitations"] == list(MANDATORY_LIMITATIONS)
        assert rec["confidence"]["regime"] == "BULL"
        assert rec["confidence"]["ess_warning"] is False
        assert rec["recommendation"] and rec["key_risk"]
