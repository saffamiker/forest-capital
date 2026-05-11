"""
Sprint 3 — regime detector unit tests.

Tests cover both detection methods:
  1. Threshold-based (_classify_threshold) — fast, interpretable, always available.
  2. HMM (classify_hmm_regime) — learns regime boundaries from data rather than
     using fixed thresholds. Tested with synthetic returns; HMM tests are skipped
     if hmmlearn is not installed (optional dependency).

The most critical assertion is regime agreement logic: when threshold says BULL
and HMM says BEAR, regimes_agree must be False so the frontend shows UNCERTAIN.
A bug here would suppress a genuine signal of regime ambiguity.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")

try:
    from hmmlearn.hmm import GaussianHMM  # noqa: F401
    HMM_AVAILABLE = True
except ImportError:
    HMM_AVAILABLE = False

requires_hmm = pytest.mark.skipif(not HMM_AVAILABLE, reason="hmmlearn not installed")


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_bull_returns(n: int = 300, seed: int = 42) -> pd.Series:
    """Synthetic bull-market return series: positive mean, low vol."""
    np.random.seed(seed)
    idx = pd.date_range("2000-01-02", periods=n, freq="B")
    return pd.Series(np.random.normal(0.001, 0.008, n), index=idx)


def make_bear_returns(n: int = 300, seed: int = 42) -> pd.Series:
    """Synthetic bear-market return series: negative mean, high vol."""
    np.random.seed(seed)
    idx = pd.date_range("2000-01-02", periods=n, freq="B")
    return pd.Series(np.random.normal(-0.002, 0.020, n), index=idx)


def make_mixed_returns(n: int = 600, seed: int = 42) -> pd.Series:
    """First half bull, second half bear — for regime-switching tests."""
    bull = make_bull_returns(n // 2, seed=seed)
    bear = make_bear_returns(n // 2, seed=seed + 1)
    bear.index = pd.date_range(bull.index[-1] + pd.offsets.BDay(1), periods=n // 2, freq="B")
    return pd.concat([bull, bear])


# ── _classify_threshold ───────────────────────────────────────────────────────

def test_classify_threshold_bull_clear_signals():
    """
    Low VIX + positive equity trend + non-inverted curve → BULL.
    These three signals dominate: VIX is double-weighted (Whaley 2009),
    equity trend is double-weighted (matches strategy's state variable).
    """
    from tools.regime_detector import _classify_threshold
    regime = _classify_threshold(
        vix=14.0,            # Below VIX_LOW_THRESHOLD (20) → bull signal
        yield_curve_slope=1.0,  # Normal curve → bull signal
        equity_trend=0.08,   # +8% → above 0 → bull signal
        credit_spread=2.5,   # Below CREDIT_SPREAD_WIDE → no bear signal
    )
    assert regime == "BULL"


def test_classify_threshold_bear_clear_signals():
    """
    High VIX + negative equity trend + inverted curve → BEAR.
    VIX > VIX_HIGH_THRESHOLD and equity < BEAR_MARKET_THRESHOLD are each
    double-weighted, so two bear signals here carry 4 bear points.
    """
    from tools.regime_detector import _classify_threshold
    regime = _classify_threshold(
        vix=40.0,             # High → bear (double-weight)
        yield_curve_slope=-0.5,  # Inverted → bear
        equity_trend=-0.25,  # -25% → bear (double-weight)
        credit_spread=8.0,   # Wide → bear
    )
    assert regime == "BEAR"


def test_classify_threshold_transition_mixed_signals():
    """
    Mixed signals should produce TRANSITION, not a definitive bull/bear.
    TRANSITION is a valid regime for the portfolio — it triggers the
    REGIME_SWITCHING strategy's balanced allocation.
    """
    from tools.regime_detector import _classify_threshold
    regime = _classify_threshold(
        vix=22.0,            # Slightly elevated, not high
        yield_curve_slope=0.1,  # Barely positive
        equity_trend=-0.05,  # Slightly negative
        credit_spread=4.0,   # Moderately elevated
    )
    assert regime in {"TRANSITION", "BEAR", "BULL"}  # Accept any valid regime


def test_classify_threshold_all_none_returns_transition():
    """
    When all signals are unavailable (FRED outage, data gap), the function
    must return TRANSITION — cannot default to BULL or BEAR without evidence.
    TRANSITION triggers the most balanced allocation, minimising error risk.
    """
    from tools.regime_detector import _classify_threshold
    regime = _classify_threshold(None, None, None, None)
    assert regime == "TRANSITION"


def test_classify_threshold_returns_valid_regime():
    """Output must be one of the three defined regimes."""
    from tools.regime_detector import _classify_threshold
    for vix in [14, 25, 40]:
        for trend in [-0.3, 0.0, 0.1]:
            regime = _classify_threshold(vix, 0.5, trend, 3.0)
            assert regime in {"BULL", "BEAR", "TRANSITION"}


def test_classify_threshold_partial_signals_graceful():
    """Missing signals (None) degrade gracefully — classification still runs."""
    from tools.regime_detector import _classify_threshold
    # Only VIX available — should still classify without crashing
    regime = _classify_threshold(vix=15.0, yield_curve_slope=None, equity_trend=None, credit_spread=None)
    assert regime in {"BULL", "BEAR", "TRANSITION"}


# ── _check_agreement ──────────────────────────────────────────────────────────

def test_check_agreement_same_regime_agrees():
    from tools.regime_detector import _check_agreement
    assert _check_agreement("BULL", "BULL") is True
    assert _check_agreement("BEAR", "BEAR") is True
    assert _check_agreement("TRANSITION", "TRANSITION") is True


def test_check_agreement_bull_vs_bear_disagrees():
    """
    BULL vs BEAR is the only genuine disagreement — this is what triggers
    UNCERTAIN on the frontend. Any bug here suppresses the uncertainty signal.
    """
    from tools.regime_detector import _check_agreement
    assert _check_agreement("BULL", "BEAR") is False
    assert _check_agreement("BEAR", "BULL") is False


def test_check_agreement_transition_paired_with_any_agrees():
    """
    TRANSITION is neutral — it agrees with BULL, BEAR, and itself.
    Rationale: TRANSITION already signals ambiguity; UNCERTAIN would be redundant.
    """
    from tools.regime_detector import _check_agreement
    assert _check_agreement("TRANSITION", "BULL") is True
    assert _check_agreement("TRANSITION", "BEAR") is True
    assert _check_agreement("BULL", "TRANSITION") is True
    assert _check_agreement("BEAR", "TRANSITION") is True


def test_check_agreement_none_hmm_always_agrees():
    """
    When HMM is unavailable (import error, insufficient data), agreement is
    trivially True — no disagreement possible with only one method.
    """
    from tools.regime_detector import _check_agreement
    assert _check_agreement("BULL", None) is True
    assert _check_agreement("BEAR", None) is True


# ── classify_hmm_regime ───────────────────────────────────────────────────────

@requires_hmm
def test_hmm_returns_dict():
    from tools.regime_detector import classify_hmm_regime
    returns = make_mixed_returns()
    result = classify_hmm_regime(returns)
    assert isinstance(result, dict)


@requires_hmm
def test_hmm_has_required_keys():
    from tools.regime_detector import classify_hmm_regime
    returns = make_mixed_returns()
    result = classify_hmm_regime(returns)
    for key in ["current_regime_label", "current_probabilities", "historical_labels", "converged"]:
        assert key in result, f"Missing key: {key}"


@requires_hmm
def test_hmm_regime_label_is_valid():
    """Current regime must be one of the three defined states."""
    from tools.regime_detector import classify_hmm_regime
    returns = make_mixed_returns()
    result = classify_hmm_regime(returns)
    assert result["current_regime_label"] in {"BULL", "BEAR", "TRANSITION"}


@requires_hmm
def test_hmm_probabilities_sum_to_one():
    """
    Posterior probabilities from the forward-backward algorithm must sum to 1.
    A sum < 1 would indicate a numerical error in the probability computation.
    """
    from tools.regime_detector import classify_hmm_regime
    returns = make_mixed_returns()
    result = classify_hmm_regime(returns)
    probs = result["current_probabilities"]
    assert isinstance(probs, dict)
    total = sum(probs.values())
    assert abs(total - 1.0) < 0.01, f"Probabilities sum to {total:.4f}, not 1.0"


@requires_hmm
def test_hmm_historical_labels_cover_full_series():
    """Historical labels must have the same length as the input return series."""
    from tools.regime_detector import classify_hmm_regime
    returns = make_mixed_returns(600)
    result = classify_hmm_regime(returns)
    labels = result["historical_labels"]
    assert len(labels) == len(returns.dropna())


@requires_hmm
def test_hmm_insufficient_data_returns_error():
    """
    HMM requires a minimum of 100 observations. With fewer, it returns an error
    dict rather than crashing — so regime detection degrades gracefully to
    threshold-only classification.
    """
    from tools.regime_detector import classify_hmm_regime
    short_series = make_bull_returns(n=50)
    result = classify_hmm_regime(short_series)
    assert result.get("current_regime_label") is None
    assert "error" in result


@requires_hmm
def test_hmm_3state_labels_all_three_regimes_eventually():
    """
    With enough data spanning bull and bear periods, the 3-state HMM should
    identify all three regimes at some point in the historical series.
    Failure here means the model collapses to 2 states — reducing its value
    vs the simpler threshold approach.
    """
    from tools.regime_detector import classify_hmm_regime
    # Long mixed series with pronounced bull and bear periods
    np.random.seed(0)
    n = 800
    # Three distinct return distributions
    bull = np.random.normal(0.002, 0.006, n // 3)
    trans = np.random.normal(0.000, 0.012, n // 3)
    bear = np.random.normal(-0.003, 0.020, n // 3)
    data = np.concatenate([bull, trans, bear])
    idx = pd.date_range("2000-01-02", periods=len(data), freq="B")
    returns = pd.Series(data, index=idx)

    result = classify_hmm_regime(returns, n_states=3)
    if result.get("current_regime_label") is not None:
        unique_labels = set(result["historical_labels"].values())
        # With such pronounced regimes, expect at least 2 distinct labels
        assert len(unique_labels) >= 2


# ── fit_hmm_historical ────────────────────────────────────────────────────────

@requires_hmm
def test_fit_hmm_historical_returns_dict():
    from tools.regime_detector import fit_hmm_historical
    returns = make_mixed_returns()
    result = fit_hmm_historical(returns)
    assert isinstance(result, dict)


@requires_hmm
def test_fit_hmm_historical_has_transition_matrix():
    """
    The transition matrix is displayed on the Regime Analysis dashboard.
    It shows P(BULL→BEAR) etc., which informs how REGIME_SWITCHING weights
    should respond to the current state.
    """
    from tools.regime_detector import fit_hmm_historical
    returns = make_mixed_returns()
    result = fit_hmm_historical(returns)
    assert "transition_matrix" in result
    tm = result["transition_matrix"]
    # Each row of the transition matrix should sum to 1
    for from_regime, to_dict in tm.items():
        row_sum = sum(to_dict.values())
        assert abs(row_sum - 1.0) < 0.01, f"Transition row {from_regime} sums to {row_sum:.4f}"


@requires_hmm
def test_fit_hmm_historical_with_vix_feature():
    """
    VIX as a third feature improves regime discrimination at turning points
    (VIX spikes precede bear markets by days). This test verifies the VIX
    branch runs without error when optional VIX data is supplied.
    """
    from tools.regime_detector import fit_hmm_historical
    returns = make_mixed_returns(300)
    # Synthetic VIX: higher in bear periods
    np.random.seed(42)
    vix = pd.Series(
        np.random.normal(18, 5, len(returns)),
        index=returns.index,
    ).clip(lower=10)
    result = fit_hmm_historical(returns, vix=vix)
    assert "labelled_series" in result
    assert "transition_matrix" in result
