"""Silent-failure and UI disclosure gaps — bridge #61.

Four fixes pinned here:

FIX 1 -- The CIO card now renders an inline notice when the
  recommendation dict carries `_model: "deterministic_fallback"` so the
  user is never shown a fail-open response as if it were a live LLM
  run. Pin in the frontend test file
  frontend/src/__tests__/cio-deterministic-fallback.test.tsx.

FIX 2 -- macro staleness gate
  inject_macro_context now appends a staleness disclosure when the
  cached digest is more than 24 hours old. Existing behaviour for a
  fresh digest is preserved.

FIX 3 -- empty-cache disclosure
  inject_macro_context now ALWAYS injects something: either the
  cached block (fresh or stale), or a "MACRO CONTEXT UNAVAILABLE"
  disclosure when the cache is empty. Agents are never silently
  reasoning without macro context anymore.

FIX 4 -- regime_detector signals_partial flag
  detect_current_regime now returns signals_partial=True when any of
  the four FRED signals (VIX / yield curve / equity trend / credit
  spread) failed to fetch. Additive only -- existing fallback
  behaviour is unchanged.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from tools import macro_context, regime_detector


# ── FIX 2 -- staleness gate ─────────────────────────────────────────────

def test_inject_fresh_digest_no_staleness_notice():
    """A digest generated 5 minutes ago does not trigger the staleness
    disclosure -- the cached block is appended verbatim."""
    five_min_ago = (datetime.now(timezone.utc)
                    - timedelta(minutes=5)).isoformat()
    macro_context._set_cache_for_test(
        "\n=== CURRENT MACRO CONDITIONS (last 7 days as of fresh) ===\n"
        "Summary: CPI 3.8% YoY.", generated_at=five_min_ago)
    out = macro_context.inject_macro_context("ROLE PROMPT.")
    assert "ROLE PROMPT." in out
    assert "CURRENT MACRO CONDITIONS" in out
    assert "hours old" not in out


def test_inject_stale_digest_appends_age_disclosure():
    """A digest generated 36 hours ago triggers the staleness
    disclosure. The cached block is still appended -- the user gets
    background AND a freshness flag."""
    stale_ts = (datetime.now(timezone.utc)
                - timedelta(hours=36)).isoformat()
    macro_context._set_cache_for_test(
        "\n=== CURRENT MACRO CONDITIONS (last 7 days as of stale) ===\n"
        "Summary: stale view.", generated_at=stale_ts)
    out = macro_context.inject_macro_context("ROLE PROMPT.")
    assert "stale view." in out
    assert "hours old" in out
    assert "live refresh unavailable at query time" in out


def test_inject_no_timestamp_does_not_trigger_disclosure():
    """A digest in the cache without a generated_at field falls
    through to the prior behaviour (no staleness disclosure). Older
    digest rows that lacked the field must not start emitting a fake
    staleness warning."""
    macro_context._set_cache_for_test(
        "\n=== CURRENT MACRO CONDITIONS (last 7 days as of unknown) ===\n"
        "Summary: timestampless.", generated_at=None)
    out = macro_context.inject_macro_context("ROLE PROMPT.")
    assert "timestampless." in out
    assert "hours old" not in out


# ── FIX 3 -- empty-cache disclosure ─────────────────────────────────────

def test_inject_empty_cache_injects_unavailable_notice():
    """The cold-deploy / persistently-failed-refresh path. The agent
    must see a "macro context unavailable" disclosure rather than a
    bare system prompt that the agent might silently complete with
    invented macro reasoning."""
    macro_context._set_cache_for_test("", generated_at=None)
    out = macro_context.inject_macro_context("ROLE PROMPT.")
    assert "ROLE PROMPT." in out
    assert "MACRO CONTEXT UNAVAILABLE" in out
    assert "proceed without macro grounding" in out


def test_inject_preserves_role_prompt_at_head():
    """The role prompt always comes first. All branches append the
    macro layer (or the disclosure) AFTER the role prompt."""
    macro_context._set_cache_for_test("", generated_at=None)
    out = macro_context.inject_macro_context("ROLE PROMPT FIRST.")
    assert out.startswith("ROLE PROMPT FIRST.")


# ── FIX 4 -- regime_detector signals_partial flag ───────────────────────

def test_detect_current_regime_flags_partial_signals(monkeypatch):
    """When some FRED fetches fail, signals_partial must be True. The
    other fields still populate where the fetches succeeded; the
    existing fallback behaviour is unchanged. We exercise the
    additive flag path."""
    from tools import data_fetcher

    regime_detector._regime_cache.clear()

    # VIX present, yield curve missing (raises), equity trend missing,
    # credit spread present. signals_partial should fire.
    call_count = {"n": 0}

    def _fake_fred(series_id, _start, _end):
        # FRED_SERIES["vix"]="VIXCLS", ["treasury_10y"]="DGS10",
        # ["treasury_2y"]="DGS2", ["hy_spread"]="BAMLH0A0HYM2".
        if series_id == "VIXCLS":
            return pd.Series([16.0])
        if series_id == "BAMLH0A0HYM2":
            return pd.Series([2.5])
        # All other (treasury yields) raise.
        call_count["n"] += 1
        raise RuntimeError("FRED outage")
    monkeypatch.setattr(data_fetcher, "fetch_fred_series", _fake_fred)
    # Equity fetch raises -> equity_trend stays None.
    monkeypatch.setattr(
        data_fetcher, "fetch_equity_data",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("equity outage")))
    monkeypatch.setattr(regime_detector, "_HMM_AVAILABLE", False)

    result = regime_detector.detect_current_regime()

    assert result["signals_partial"] is True
    # VIX + credit spread came through; yield curve + equity did not.
    assert result["vix_level"] == 16.0
    assert result["credit_spread"] == 2.5
    assert result["yield_curve_slope"] is None
    assert result["equity_trend"] is None


def test_detect_current_regime_flags_complete_signals(monkeypatch):
    """When all four signals succeed, signals_partial must be False."""
    from tools import data_fetcher

    regime_detector._regime_cache.clear()

    def _fake_fred(series_id, _start, _end):
        return pd.Series(
            [16.0] if series_id == "VIXCLS"
            else [2.5] if series_id == "BAMLH0A0HYM2"
            else [4.5] if series_id == "DGS10"
            else [4.0])  # DGS2

    monkeypatch.setattr(data_fetcher, "fetch_fred_series", _fake_fred)
    monkeypatch.setattr(
        data_fetcher, "fetch_equity_data",
        lambda *a, **kw: pd.DataFrame(
            {"SPY": range(400)},
            index=pd.date_range("2010-01-01", periods=400, freq="D")))
    monkeypatch.setattr(regime_detector, "_HMM_AVAILABLE", False)

    result = regime_detector.detect_current_regime()

    assert result["signals_partial"] is False
    assert result["vix_level"] == 16.0
    assert result["credit_spread"] == 2.5
    assert result["yield_curve_slope"] is not None
    assert result["equity_trend"] is not None


def test_detect_current_regime_signals_partial_is_additive(monkeypatch):
    """The new flag must be additive only -- all existing fields are
    still present and the existing fallback behaviour is unchanged."""
    from tools import data_fetcher

    regime_detector._regime_cache.clear()

    def _all_raise(*_a, **_kw):
        raise RuntimeError("outage")
    monkeypatch.setattr(data_fetcher, "fetch_fred_series", _all_raise)
    monkeypatch.setattr(data_fetcher, "fetch_equity_data", _all_raise)
    monkeypatch.setattr(regime_detector, "_HMM_AVAILABLE", False)

    result = regime_detector.detect_current_regime()

    # Existing fields still present.
    for key in ("threshold_regime", "hmm_regime", "hmm_probabilities",
                "regimes_agree", "monthly_hmm_regime",
                "hmm_models_agree", "vix_level", "yield_curve_slope",
                "equity_trend", "credit_spread",
                "pre_2022_avg_correlation",
                "post_2022_avg_correlation", "as_of"):
        assert key in result, f"missing existing field: {key}"
    # New additive field.
    assert "signals_partial" in result
    assert result["signals_partial"] is True
    # Existing fallback behaviour: with all four signals None, the
    # threshold classifier returns TRANSITION per the existing path
    # (classify_threshold(None, None, None, None) -> TRANSITION).
    assert result["threshold_regime"] == "TRANSITION"
