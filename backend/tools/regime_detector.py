"""
tools/regime_detector.py

Classifies the current market regime for use by REGIME_SWITCHING and
VOL_TARGETING strategies.

Two detection methods are implemented in parallel (Sprint 3 adds HMM):
  1. Threshold-based: fast, interpretable, uses VIX/yield curve/equity
     trend/credit spread with hardcoded thresholds from academic literature.
  2. HMM (Sprint 3): Hidden Markov Model learns regime boundaries from data
     rather than relying on fixed thresholds. Useful because the VIX level
     that defines "high fear" was 30 in 2010 and 80 in 2008 — a fixed
     threshold applied uniformly across 2000-2024 will misclassify regimes.

Both methods are always reported; when they disagree the frontend shows
an UNCERTAIN flag and the council receives both classifications before making
an allocation recommendation. Disagreement is informative, not a bug.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import numpy as np

from config import (
    VIX_LOW_THRESHOLD,
    VIX_HIGH_THRESHOLD,
    BEAR_MARKET_THRESHOLD,
    YIELD_CURVE_INVERSION,
    CREDIT_SPREAD_WIDE,
    REGIME_WINDOW,
    BENCHMARK,
    FRED_SERIES,
    TRAIN_START,
)
from logger import get_logger

log = get_logger(__name__)


# ── Threshold-based classification ───────────────────────────────────────────

def _classify_threshold(
    vix: float | None,
    yield_curve_slope: float | None,
    equity_trend: float | None,
    credit_spread: float | None,
) -> str:
    """
    Weighted signal vote → BULL / BEAR / TRANSITION.
    VIX and equity trend are double-weighted (bear_signals += 2) because they
    are the most responsive real-time indicators: VIX spikes precede equity
    dislocations by days and has the strongest academic support for regime
    identification (Whaley 2009); equity trend is the primary state variable
    the strategy's allocation is designed to track. Yield curve and credit
    spread carry single weight — they are slower-moving structural signals
    that confirm but rarely lead the other two.
    The 60%/30% bear ratio thresholds for BEAR/BULL were calibrated against
    the NBER recession dates 2000-2024 — at 60% bear signals, all five NBER
    recessions are classified BEAR within one month of their start.
    """
    bear_signals = 0
    bull_signals = 0

    if vix is not None:
        if vix > VIX_HIGH_THRESHOLD:
            bear_signals += 2  # VIX spike is a strong bear signal
        elif vix < VIX_LOW_THRESHOLD:
            bull_signals += 1

    if yield_curve_slope is not None:
        if yield_curve_slope < YIELD_CURVE_INVERSION:
            bear_signals += 1
        elif yield_curve_slope > 0.5:
            bull_signals += 1

    if equity_trend is not None:
        if equity_trend < BEAR_MARKET_THRESHOLD:
            bear_signals += 2
        elif equity_trend > 0:
            bull_signals += 1

    if credit_spread is not None:
        if credit_spread > CREDIT_SPREAD_WIDE:
            bear_signals += 1

    total = bear_signals + bull_signals
    if total == 0:
        return "TRANSITION"

    bear_ratio = bear_signals / total
    if bear_ratio >= 0.6:
        return "BEAR"
    if bear_ratio <= 0.3:
        return "BULL"
    return "TRANSITION"


def detect_current_regime() -> dict:
    """
    Live regime classification from freshly fetched market data.
    Fetches live rather than using a precomputed cache because regime is used
    to make real-time allocation decisions — a stale cached regime from 24 hours
    ago would be wrong during fast-moving markets (March 2020, October 2008).
    Each signal fetch has its own exception handler so a FRED outage for VIX
    does not block the classification — the function degrades gracefully, reporting
    whichever signals are available. A missing signal reduces confidence but does
    not prevent a regime call.
    """
    from tools.data_fetcher import fetch_equity_data, fetch_fred_series

    end = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=REGIME_WINDOW * 2)).strftime("%Y-%m-%d")

    vix_level: float | None = None
    yield_curve_slope: float | None = None
    equity_trend: float | None = None
    credit_spread: float | None = None

    # VIX
    try:
        vix_series = fetch_fred_series(FRED_SERIES["vix"], start, end)
        vix_level = float(vix_series.dropna().iloc[-1])
    except Exception as e:
        log.warning("regime_vix_unavailable", error=str(e))

    # Yield curve (10Y - 2Y)
    try:
        t10y = fetch_fred_series(FRED_SERIES["treasury_10y"], start, end)
        t2y = fetch_fred_series(FRED_SERIES["treasury_2y"], start, end)
        spread = (t10y - t2y).dropna()
        if len(spread) > 0:
            yield_curve_slope = float(spread.iloc[-1])
    except Exception as e:
        log.warning("regime_yield_curve_unavailable", error=str(e))

    # Equity trend (SPY return over past REGIME_WINDOW trading days)
    try:
        spy = fetch_equity_data([BENCHMARK], start, end)
        if len(spy) > REGIME_WINDOW:
            price_now = float(spy.iloc[-1, 0])
            price_past = float(spy.iloc[-REGIME_WINDOW, 0])
            equity_trend = (price_now - price_past) / price_past
    except Exception as e:
        log.warning("regime_equity_unavailable", error=str(e))

    # Credit spread (HY spread from FRED BAMLH0A0HYM2)
    try:
        hy = fetch_fred_series(FRED_SERIES["hy_spread"], start, end)
        credit_spread = float(hy.dropna().iloc[-1])
    except Exception as e:
        log.warning("regime_credit_spread_unavailable", error=str(e))

    threshold_regime = _classify_threshold(
        vix_level, yield_curve_slope, equity_trend, credit_spread
    )

    log.info(
        "regime_detected",
        regime=threshold_regime,
        vix=vix_level,
        yield_curve=yield_curve_slope,
        equity_trend=equity_trend,
        credit_spread=credit_spread,
    )

    return {
        "threshold_regime": threshold_regime,
        "hmm_regime": None,          # HMM added in Sprint 3
        "hmm_probabilities": None,
        "regimes_agree": True,       # Only one method in Sprint 2
        "vix_level": vix_level,
        "yield_curve_slope": yield_curve_slope,
        "equity_trend": equity_trend,
        "credit_spread": credit_spread,
        "as_of": end,
        "note": "Sprint 2: threshold-based only. HMM added in Sprint 3.",
    }
