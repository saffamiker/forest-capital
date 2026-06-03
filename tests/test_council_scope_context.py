"""Page-scoped council context — Part 4 backend tests.

Covers the three context scopes (recommendation / performance /
prediction), the null/unknown fail-open path, and the CIO injection
contract (page_context appears in the synthesis prompt only when a scope
was passed). Every underlying cache read is monkeypatched so the tests
are deterministic and never touch the DB or the network.
"""
import pytest


# ── scope dispatch + per-scope field assembly ──────────────────────────────

@pytest.mark.asyncio
async def test_recommendation_scope_injects_fields(monkeypatch):
    async def fake_rec():
        return {
            "signal": "S", "recommendation": "R", "dissenting_view": "D",
            "key_risk": "K",
            "confidence": {"regime": "BEAR", "probability": 0.9},
            "limitations": ["L1"],
        }
    monkeypatch.setattr(
        "tools.cio_recommendation.get_latest_recommendation", fake_rec)
    monkeypatch.setattr(
        "tools.regime_detector.detect_current_regime",
        lambda: {
            "hmm_regime": "BEAR", "hmm_probabilities": {"BEAR": 0.9},
            "vix_level": 22.0, "yield_curve_slope": -0.1,
            "equity_trend": -0.05, "credit_spread": 4.5,
        })

    async def fake_proj():
        return {"blend_weights": {"MIN_VARIANCE": 0.4, "RISK_PARITY": 0.4}}
    monkeypatch.setattr(
        "tools.regime_meta_forward.get_cached_forward_projection", fake_proj)

    from tools.council_live_context import get_scope_context
    out = await get_scope_context("recommendation")
    assert out is not None
    assert out["recommendation"]["signal"] == "S"
    assert out["recommendation"]["dissenting_view"] == "D"
    assert out["regime"]["regime"] == "BEAR"
    assert out["regime"]["confidence"] == 0.9
    assert out["regime"]["vix"] == 22.0
    assert out["regime"]["credit_spread"] == 4.5
    assert out["blend_weights"]["MIN_VARIANCE"] == 0.4


@pytest.mark.asyncio
async def test_performance_scope_injects_fields(monkeypatch):
    async def fake_events():
        return [
            {
                "event_id": "SVB", "event_date": "2023-03-31", "regime": "BEAR",
                "recommendation": "defensive", "verdict": "added value",
                "value_added_sharpe": 0.30,
                # fields that must be dropped from the slim view:
                "blend_weights": {"MIN_VARIANCE": 0.4}, "posterior": {"BEAR": 0.8},
            },
        ]
    monkeypatch.setattr("tools.play_by_play.load_stored_events", fake_events)

    async def fake_oos():
        return {"blend": 0.8576, "benchmark": 0.4341, "equal_weight": 0.1264,
                "value_add_events": 2, "total_events": 9}
    monkeypatch.setattr("tools.play_by_play.get_cached_oos_summary", fake_oos)

    from tools.council_live_context import get_scope_context
    out = await get_scope_context("performance")
    assert out is not None
    assert out["events"][0]["event_id"] == "SVB"
    assert out["events"][0]["value_added_sharpe"] == 0.30
    # slim projection — heavy fields are not carried into the council prompt
    assert "blend_weights" not in out["events"][0]
    assert "posterior" not in out["events"][0]
    assert out["oos_summary"]["blend"] == 0.8576
    assert out["oos_summary"]["value_add_events"] == 2
    assert out["scorecard"]["n_total"] == 1


@pytest.mark.asyncio
async def test_prediction_scope_injects_fields(monkeypatch):
    async def fake_proj():
        return {
            "horizons_months": [1, 3, 6, 12],
            "p_outperform": {"benchmark": {"12": 0.62}},
            "bands": {"blend": {"12": {"median": 0.1}}},
            "blend_weights": {"MIN_VARIANCE": 0.4},
            "regime": "TRANSITION", "regime_probability": 0.7,
            "transition_matrix": {"BULL": {"BULL": 0.8, "BEAR": 0.2}},
        }
    monkeypatch.setattr(
        "tools.regime_meta_forward.get_cached_forward_projection", fake_proj)

    async def fake_rec():
        return {"limitations": ["not a forecast", "HMM convergence note"]}
    monkeypatch.setattr(
        "tools.cio_recommendation.get_latest_recommendation", fake_rec)

    from tools.council_live_context import get_scope_context
    out = await get_scope_context("prediction")
    assert out is not None
    assert out["p_outperform"]["benchmark"]["12"] == 0.62
    assert out["transition_matrix"]["BULL"]["BULL"] == 0.8
    assert out["regime"] == "TRANSITION"
    assert out["limitations"] == ["not a forecast", "HMM convergence note"]


@pytest.mark.asyncio
async def test_null_and_unknown_scope_inject_nothing():
    """A null or unknown scope resolves to None — no injection."""
    from tools.council_live_context import get_scope_context
    assert await get_scope_context(None) is None
    assert await get_scope_context("") is None
    assert await get_scope_context("bogus") is None


# ── CIO injection contract — page_context appears only when scoped ──────────

def _capture_synthesis(monkeypatch):
    """Build a CIO whose call_claude + visual context are stubbed; returns
    a dict the test reads the captured synthesis user_message from."""
    import agents.cio as cio_mod
    captured: dict = {}

    def fake_call_claude(model, system, user, **kw):
        captured["user"] = user
        return "FINAL RECOMMENDATION: ok"

    monkeypatch.setattr(cio_mod, "call_claude", fake_call_claude)
    # No chart snapshots in the test path — keep the synthesis text-only.
    monkeypatch.setattr(cio_mod, "snapshots_dir_exists", lambda: False)
    return cio_mod, captured


def test_synthesis_injects_page_context_when_scoped(monkeypatch):
    cio_mod, captured = _capture_synthesis(monkeypatch)
    cio = cio_mod.CIO()
    cio._synthesise(
        "why is the blend defensive?", "draft", {}, {}, {}, {}, {}, {}, {},
        live_context={"recommendation": {"signal": "defensive tilt"}},
    )
    assert "page_context" in captured["user"]
    assert "council-facing page" in captured["user"]


def test_synthesis_omits_page_context_when_unscoped(monkeypatch):
    cio_mod, captured = _capture_synthesis(monkeypatch)
    cio = cio_mod.CIO()
    cio._synthesise(
        "which strategies do you recommend?", "draft", {}, {}, {}, {}, {}, {}, {},
        live_context=None,
    )
    assert "page_context" not in captured["user"]
    assert "council-facing page" not in captured["user"]


# ── deliberate() accepts and threads live_context ────────────────────────────
#
# June 3 2026 — the streaming endpoint variant has accepted
# live_context since PR #229 but the synchronous deliberate() was
# silently dropping it. The baseline-capture script (PR #262) hit
# `TypeError: unexpected keyword argument 'live_context'` on first
# run against the freshly migrated DB. This contract pins both the
# kwarg AND that the value reaches the synthesis prompt — so the
# sync and streaming paths can't drift again.


def _capture_full_deliberate(monkeypatch):
    """Stub every specialist + every LLM call so deliberate() runs
    deterministically without an API key. Returns a dict that
    accumulates the user-prompts every call_claude saw, plus the
    final result the public method returned."""
    import agents.cio as cio_mod
    captured: dict = {"user_prompts": []}

    def fake_call_claude(model, system, user, **kw):
        captured["user_prompts"].append(user)
        return "FINAL RECOMMENDATION: stub"

    monkeypatch.setattr(cio_mod, "call_claude", fake_call_claude)
    monkeypatch.setattr(cio_mod, "snapshots_dir_exists", lambda: False)

    # Stub every specialist so deliberate() never tries a real LLM
    # fan-out. Each returns the minimal shape the CIO consumes.
    def _stub_analyse(*a, **kw):
        return {"summary": "stub", "technical_findings": {}}

    cio = cio_mod.CIO()
    monkeypatch.setattr(cio._equity, "analyse", _stub_analyse)
    monkeypatch.setattr(cio._fi, "analyse", _stub_analyse)
    monkeypatch.setattr(cio._risk, "analyse", _stub_analyse)
    monkeypatch.setattr(cio._quant, "analyse", _stub_analyse)
    monkeypatch.setattr(
        cio._gemini, "challenge",
        lambda *a, **kw: {"summary": "g", "technical_findings": {}})
    monkeypatch.setattr(
        cio._grok, "challenge",
        lambda *a, **kw: {"summary": "k", "technical_findings": {}})
    return cio, captured


def test_deliberate_accepts_live_context_kwarg(monkeypatch):
    """Sync deliberate() must accept live_context. Regression guard
    against the June 3 2026 baseline-capture script crash:
    TypeError: unexpected keyword argument 'live_context'."""
    cio, _captured = _capture_full_deliberate(monkeypatch)
    result = cio.deliberate(
        "what is the regime?", strategy_results={},
        live_context={"regime": {"hmm_state": "BULL"}})
    # Doesn't raise; returns the standard council shape.
    assert "final_recommendation" in result


def test_deliberate_threads_live_context_into_synthesis_prompt(monkeypatch):
    """The kwarg must actually thread to _compile_draft_consensus
    AND _synthesise — not just be accepted and dropped. We check by
    looking at the user-prompt text the synthesis call_claude saw."""
    cio, captured = _capture_full_deliberate(monkeypatch)
    cio.deliberate(
        "what is the regime?", strategy_results={},
        live_context={"regime": {"hmm_state": "BULL",
                                 "hmm_confidence": 0.87}})
    # At least one of the captured prompts (draft consensus or
    # synthesis) must reference page_context, which is the marker
    # _compile_draft_consensus / _synthesise inject when
    # live_context is provided.
    assert any("page_context" in p for p in captured["user_prompts"]), (
        "Expected at least one CIO prompt to mention page_context, "
        f"got: {[p[:80] for p in captured['user_prompts']]}")


def test_deliberate_without_live_context_omits_page_context(monkeypatch):
    """The pre-PR-229 behaviour: deliberate() without live_context
    must NOT inject page_context — keeps the test environment's
    existing snapshot-tests stable."""
    cio, captured = _capture_full_deliberate(monkeypatch)
    cio.deliberate("which strategy?", strategy_results={})
    for p in captured["user_prompts"]:
        assert "page_context" not in p, (
            "page_context leaked into a prompt despite live_context=None")
