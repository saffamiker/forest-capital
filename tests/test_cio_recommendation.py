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


# ── Persistence recovery (June 18 2026) ──────────────────────────────────
#
# When the LLM call fails on a warm and the deterministic fallback is
# persisted under a data_hash, the previous ON CONFLICT DO NOTHING
# pinned that fallback row -- a later successful warm at the same hash
# was silently dropped, leaving the digest stuck on "Live regime
# unavailable" until the next monthly data tick changed the hash.
#
# The fix flips the conflict resolution to a guarded UPSERT: a real
# LLM row (any model that is NOT 'deterministic_fallback') overwrites
# a previously stored fallback; the reverse case (real then fallback)
# never overwrites. These tests pin the SQL string shape -- the live
# behaviour is verified on Render where the actual Postgres engine
# evaluates the WHERE clause; the CI test DB is fail-open SQLite so
# this catches regressions at the statement-shape layer.


# ── LLM JSON parse hardening (June 19 2026) ──────────────────────────────
#
# The CIO recommendation LLM occasionally emits preamble text before the
# JSON body or trailing prose after the closing brace. The previous
# json.loads on a best-effort slice surfaced cryptic "Expecting ',' "
# delimiter errors that gave no clue what the model had emitted.
# _parse_recommendation_json hardens the path:
#   * markdown fences (with or without `json` tag) are stripped,
#   * the body is bracketed by find('{') / rfind('}') so any preamble
#     or trailing prose is discarded,
#   * a raw-preview WARNING fires on parse failure so Render logs carry
#     the truncated response that broke the parse.


class TestParseRecommendationJson:

    def test_parses_clean_object(self):
        out = cio._parse_recommendation_json(
            '{"signal": "s", "dissenting_view": "d", '
            '"confidence": {"regime": "BULL"}}')
        assert out["signal"] == "s"
        assert out["confidence"]["regime"] == "BULL"

    def test_strips_markdown_fence_with_json_tag(self):
        out = cio._parse_recommendation_json(
            "```json\n"
            '{"signal": "s", "dissenting_view": "d", '
            '"confidence": {"regime": "BEAR"}}\n'
            "```")
        assert out["confidence"]["regime"] == "BEAR"

    def test_strips_preamble_before_first_brace(self):
        out = cio._parse_recommendation_json(
            "Here is the JSON object you requested:\n\n"
            '{"signal": "s", "dissenting_view": "d", '
            '"confidence": {"regime": "TRANSITION"}}')
        assert out["confidence"]["regime"] == "TRANSITION"

    def test_strips_trailing_prose_after_last_brace(self):
        out = cio._parse_recommendation_json(
            '{"signal": "s", "dissenting_view": "d", '
            '"confidence": {"regime": "BULL"}}\n\n'
            "Note: this concludes the recommendation.")
        assert out["confidence"]["regime"] == "BULL"

    def test_raises_value_error_when_no_object_braces(self, monkeypatch):
        captured: list[tuple[str, dict]] = []

        def _capture(event, **kwargs):
            captured.append((event, kwargs))

        monkeypatch.setattr(cio.log, "warning", _capture)
        with pytest.raises(ValueError):
            cio._parse_recommendation_json(
                "Sorry, I cannot produce this output.")
        events = [e for e, _ in captured]
        assert "cio_recommendation_no_json_object" in events
        # The raw preview survives so the operator can see what the
        # model actually emitted.
        kwargs = next(kw for e, kw in captured
                      if e == "cio_recommendation_no_json_object")
        assert "Sorry, I cannot produce this output." \
            in kwargs.get("raw_preview", "")

    def test_logs_raw_preview_on_parse_failure(self, monkeypatch):
        captured: list[tuple[str, dict]] = []

        def _capture(event, **kwargs):
            captured.append((event, kwargs))

        monkeypatch.setattr(cio.log, "warning", _capture)
        # The brace-pair lookup succeeds but the body is malformed
        # JSON. The original delimiter error must propagate, AND the
        # raw preview must land in the structured log so a regression
        # hunter can see what the model wrote.
        bad = ('{"signal": "s", '
               '"dissenting_view": "d", '
               '"confidence": {regime: "BULL"}}')   # unquoted key
        import json as _json
        with pytest.raises(_json.JSONDecodeError):
            cio._parse_recommendation_json(bad)
        events = [e for e, _ in captured]
        assert "cio_recommendation_json_parse_failed" in events

    def test_raises_when_root_is_not_a_dict(self, monkeypatch):
        captured: list[tuple[str, dict]] = []

        def _capture(event, **kwargs):
            captured.append((event, kwargs))

        monkeypatch.setattr(cio.log, "warning", _capture)
        # A JSON array at the root level should not be accepted as a
        # recommendation -- the caller treats only dicts as valid.
        # The brace-pair scan would pull the empty {} from a body
        # like "[{...}]" -- guard by isinstance check at the end.
        # Construct a response where the brace-pair extraction yields
        # a non-object: a bare string in braces.
        with pytest.raises(ValueError):
            cio._parse_recommendation_json('{"just a value"}')
        # Either parse_failed (more likely -- malformed) or
        # not_object (if the slice happened to parse). Both are
        # acceptable signals -- the contract is just "no dict, no
        # acceptance".
        events = {e for e, _ in captured}
        assert events.intersection({
            "cio_recommendation_json_parse_failed",
            "cio_recommendation_json_not_object",
        })


class TestPersistRecoveryUpsert:

    @pytest.mark.asyncio
    async def test_persist_uses_guarded_do_update(self, monkeypatch):
        """The ON CONFLICT clause is DO UPDATE with the
        deterministic_fallback guard, not DO NOTHING. Captures the
        SQL text passed to session.execute and pins both halves of
        the WHERE clause (existing row must be a fallback, new row
        must NOT be a fallback)."""
        from tools import cio_recommendation as cio_mod

        captured: dict = {"sql": None, "params": None, "committed": False}

        class _FakeSession:
            async def execute(self, sql, params):
                captured["sql"] = str(sql)
                captured["params"] = params

            async def commit(self):
                captured["committed"] = True

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

        # Force the DB-available branch + redirect AsyncSessionLocal to
        # the fake. The fake captures the SQL string for inspection.
        monkeypatch.setattr(cio_mod, "_DB_AVAILABLE", True)
        monkeypatch.setattr(
            cio_mod, "AsyncSessionLocal", lambda: _FakeSession())

        await cio_mod._persist(
            "abc123",
            {"signal": "s", "confidence": {"regime": "BULL"},
             "dissenting_view": "d", "limitations": ["a"],
             "_model": "claude-sonnet-4-6"},
            "BULL")

        sql = captured["sql"] or ""
        assert "ON CONFLICT (data_hash) DO UPDATE" in sql
        # The recovery guard: only overwrite if the existing row is a
        # fallback. This is the half that lets a successful warm clear
        # a previously poisoned cache row.
        assert (
            "cio_recommendations.model "
            "      = 'deterministic_fallback'" in sql)
        # The reverse guard: never overwrite a real row with a fallback.
        assert (
            "EXCLUDED.model "
            "      IS DISTINCT FROM 'deterministic_fallback'" in sql)
        # computed_at bumps so reads ordered by recency see the
        # corrected row immediately.
        assert "computed_at = now()" in sql
        # The DO NOTHING shape MUST be gone -- a regression that
        # accidentally restores it would re-introduce the digest
        # poisoning.
        assert "DO NOTHING" not in sql
        assert captured["committed"] is True

    @pytest.mark.asyncio
    async def test_persist_short_circuits_when_db_unavailable(
            self, monkeypatch):
        """The Render-side path runs on Postgres; CI runs without a DB.
        With _DB_AVAILABLE False the helper must return immediately
        without raising, so the test matrix mirrors the production
        fail-open contract."""
        from tools import cio_recommendation as cio_mod

        monkeypatch.setattr(cio_mod, "_DB_AVAILABLE", False)
        # No fake session needed -- the short-circuit fires first.
        await cio_mod._persist(
            "abc123",
            {"signal": "s", "_model": "deterministic_fallback"},
            "BULL")
        # If we reach here without raising, the contract held.

    @pytest.mark.asyncio
    async def test_persist_swallows_db_errors(self, monkeypatch):
        """A DB failure during persistence must log + return, never
        raise -- otherwise a warm failure would poison the entire
        warm pipeline rather than just the CIO recommendation surface."""
        from tools import cio_recommendation as cio_mod

        class _ExplodingSession:
            async def execute(self, *_args, **_kw):
                raise RuntimeError("boom")

            async def commit(self):  # pragma: no cover -- never reached
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

        monkeypatch.setattr(cio_mod, "_DB_AVAILABLE", True)
        monkeypatch.setattr(
            cio_mod, "AsyncSessionLocal", lambda: _ExplodingSession())

        # Must not raise.
        await cio_mod._persist(
            "abc123",
            {"signal": "s", "_model": "claude-sonnet-4-6"},
            "BULL")
