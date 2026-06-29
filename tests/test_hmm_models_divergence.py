"""Surface the daily-vs-monthly HMM divergence at the three sites that
the recon for bridge-prompt #33 identified:

  1. tools.regime_detector.detect_current_regime() — must return the
     monthly HMM regime alongside the daily one and a hmm_models_agree
     boolean so downstream consumers can flag the split without
     re-fitting either model.

  2. tools.cio_recommendation._attach_divergence() — must overlay a
     divergence_disclosure field on the recommendation dict ONLY when
     the two HMMs disagree. Live-state overlay; never baked into the
     cached prose.

  3. tools.email_digest._read_latest_regime_signals_for_digest path —
     when detect_current_regime reports hmm_models_agree=False the
     digest's What-would-trigger-a-rebalance section must include a
     "Model divergence" line. Silent when the two HMMs agree.

The tests stub out the heavy regime detection so they run in the same
~3-minute budget as the rest of backend pytest. The point is the WIRING
between the three surfaces, not re-validating the HMM fit itself.
"""
import pytest

from tools.cio_recommendation import _attach_divergence


# ── _attach_divergence ───────────────────────────────────────────────────

def test_attach_divergence_silent_when_models_agree():
    rec = {"signal": "x", "recommendation": "y"}
    out = _attach_divergence(
        rec, daily_regime="BEAR", confidence=0.87,
        monthly_regime="BEAR", hmm_models_agree=True)
    assert "divergence_disclosure" not in out


def test_attach_divergence_emits_disclosure_when_models_disagree():
    rec = {"signal": "x", "recommendation": "y"}
    out = _attach_divergence(
        rec, daily_regime="BEAR", confidence=0.87,
        monthly_regime="BULL", hmm_models_agree=False)
    note = out["divergence_disclosure"]
    assert "BEAR at 87.0%" in note
    assert "BULL" in note
    assert "monthly model" in note and "daily model" in note


def test_attach_divergence_silent_when_monthly_regime_missing():
    """Fail-open: a missing monthly_regime means we cannot compose the
    disclosure cleanly. The flag is a single line; without both labels
    it would read as a half-line typo. Drop it rather than render
    something misleading."""
    rec = {"signal": "x"}
    out = _attach_divergence(
        rec, daily_regime="BEAR", confidence=0.87,
        monthly_regime=None, hmm_models_agree=False)
    assert "divergence_disclosure" not in out


def test_attach_divergence_handles_none_rec():
    """get_endpoint_recommendation can return None on a cold cache; the
    overlay must not raise in that case."""
    assert _attach_divergence(
        None, daily_regime="BEAR", confidence=0.5,
        monthly_regime="BULL", hmm_models_agree=False) is None


def test_attach_divergence_renders_em_dash_when_confidence_missing():
    rec = {}
    out = _attach_divergence(
        rec, daily_regime="BEAR", confidence=None,
        monthly_regime="BULL", hmm_models_agree=False)
    assert "BEAR at —" in out["divergence_disclosure"]


# ── detect_current_regime result shape ────────────────────────────────────

def test_detect_current_regime_includes_monthly_and_agreement(monkeypatch):
    """Stub the heavy data fetch + HMM and assert the returned dict
    carries both monthly_hmm_regime and hmm_models_agree. Without these
    fields the recommendation overlay and digest disclosure can never
    fire."""
    from tools import regime_detector, data_fetcher

    # Force cache miss so the stub runs.
    regime_detector._regime_cache.clear()

    # Stub the FRED / equity fetch path AT SOURCE (regime_detector imports
    # these lazily inside the function). We do not care about the
    # threshold values for this test; only that the HMM section runs.
    import pandas as pd

    monkeypatch.setattr(
        data_fetcher, "fetch_fred_series",
        lambda *a, **kw: pd.Series([20.0]))

    def _fake_equity(_syms, _start, _end):
        idx = pd.date_range("2010-01-01", periods=400, freq="D")
        return pd.DataFrame({"SPY": range(len(idx))}, index=idx)
    monkeypatch.setattr(data_fetcher, "fetch_equity_data", _fake_equity)

    # Stub classify_hmm_regime — daily call returns BEAR, monthly returns
    # BULL. This is the case that must produce hmm_models_agree=False.
    calls = []
    def _fake_classify(rets, *args, **kwargs):
        calls.append(len(rets))
        # Daily series will be ~399 obs; monthly will be ~287.
        if len(rets) > 300:
            return {
                "current_regime_label": "BEAR",
                "current_probabilities": {"BEAR": 0.87, "BULL": 0.13},
            }
        return {
            "current_regime_label": "BULL",
            "current_probabilities": {"BULL": 0.80, "BEAR": 0.20},
        }
    monkeypatch.setattr(
        regime_detector, "classify_hmm_regime", _fake_classify)
    monkeypatch.setattr(regime_detector, "_HMM_AVAILABLE", True)

    # Stub monthly returns for the second HMM fit.
    def _fake_monthly():
        idx = pd.date_range("2002-01-31", periods=287, freq="ME")
        return pd.DataFrame({
            "equity_return": [0.01] * len(idx),
            "ig_return":     [0.005] * len(idx),
        }, index=idx)
    monkeypatch.setattr(data_fetcher, "build_monthly_returns", _fake_monthly)

    result = regime_detector.detect_current_regime()

    assert result["hmm_regime"] == "BEAR"
    assert result["monthly_hmm_regime"] == "BULL"
    assert result["hmm_models_agree"] is False
    # Both HMMs were actually called.
    assert len(calls) == 2


def test_detect_current_regime_agrees_when_both_models_say_same(monkeypatch):
    from tools import regime_detector, data_fetcher
    regime_detector._regime_cache.clear()

    import pandas as pd
    monkeypatch.setattr(
        data_fetcher, "fetch_fred_series",
        lambda *a, **kw: pd.Series([20.0]))
    monkeypatch.setattr(
        data_fetcher, "fetch_equity_data",
        lambda *a, **kw: pd.DataFrame(
            {"SPY": range(400)},
            index=pd.date_range("2010-01-01", periods=400, freq="D")))
    monkeypatch.setattr(
        regime_detector, "classify_hmm_regime",
        lambda rets, *a, **kw: {
            "current_regime_label": "BULL",
            "current_probabilities": {"BULL": 0.9, "BEAR": 0.1}})
    monkeypatch.setattr(regime_detector, "_HMM_AVAILABLE", True)
    monkeypatch.setattr(
        data_fetcher, "build_monthly_returns",
        lambda: pd.DataFrame(
            {"equity_return": [0.01] * 287, "ig_return": [0.005] * 287},
            index=pd.date_range("2002-01-31", periods=287, freq="ME")))

    result = regime_detector.detect_current_regime()
    assert result["hmm_regime"] == "BULL"
    assert result["monthly_hmm_regime"] == "BULL"
    assert result["hmm_models_agree"] is True


# ── digest divergence rendering ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_digest_renders_divergence_line_on_disagreement(monkeypatch):
    from tools import email_digest

    async def _signals():
        return {
            "vix_level": 15.4, "credit_spread": 2.74,
            "yield_curve_slope": 0.42, "equity_trend": 0.09,
            "hmm_regime": "BEAR", "fetched_at": "2026-06-06"}
    monkeypatch.setattr(
        email_digest, "_read_latest_regime_signals_for_digest", _signals)

    async def _metric(_name):
        return {"blends": {"BEAR": {"VOL_TARGETING": 0.35}}}
    monkeypatch.setattr(
        "tools.precomputed_analytics.get_latest_metric", _metric)

    monkeypatch.setattr(
        "tools.regime_detector.detect_current_regime",
        lambda: {
            "monthly_hmm_regime": "BULL",
            "hmm_models_agree": False})

    section = await email_digest._section_rebalance_triggers()
    assert "Model divergence" in section.html
    assert "BEAR" in section.html and "BULL" in section.html
    assert "Model divergence" in section.text


@pytest.mark.asyncio
async def test_digest_silent_when_models_agree(monkeypatch):
    from tools import email_digest

    async def _signals():
        return {
            "vix_level": 15.4, "credit_spread": 2.74,
            "yield_curve_slope": 0.42, "equity_trend": 0.09,
            "hmm_regime": "BULL", "fetched_at": "2026-06-06"}
    monkeypatch.setattr(
        email_digest, "_read_latest_regime_signals_for_digest", _signals)

    async def _metric(_name):
        return {"blends": {"BULL": {"BLACK_LITTERMAN": 0.40}}}
    monkeypatch.setattr(
        "tools.precomputed_analytics.get_latest_metric", _metric)

    monkeypatch.setattr(
        "tools.regime_detector.detect_current_regime",
        lambda: {
            "monthly_hmm_regime": "BULL",
            "hmm_models_agree": True})

    section = await email_digest._section_rebalance_triggers()
    assert "Model divergence" not in section.html
    assert "Model divergence" not in section.text
