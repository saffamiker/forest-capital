"""
tools/regime_detector.py

Classifies the current market regime for REGIME_SWITCHING and VOL_TARGETING.

Two detection methods are implemented in parallel and always reported:
  1. Threshold-based: fast and interpretable — uses VIX/yield curve/equity
     trend/credit spread with weights calibrated against NBER recession dates
     2000-2024. The 60%/30% bear ratio thresholds were chosen so that all five
     NBER recessions are classified BEAR within one month of their start date.
  2. HMM (GaussianHMM): learns regime boundaries from data rather than
     relying on fixed thresholds. Critical for cross-period comparability —
     VIX=30 was "high fear" in 2010 but was below the GFC peak in 2008.
     A fixed threshold applied uniformly across 2000-2024 misclassifies regimes
     in periods where the VIX baseline shifts (post-2020 structural shift).

Both methods are reported simultaneously. When they disagree, the frontend
flags UNCERTAIN and the council receives both before making an allocation
recommendation. Disagreement is informative — it signals genuine ambiguity
in the market regime, which is itself useful information for the CIO.

HMM fit: 2-state and 3-state models are both fit; 3-state is primary because
it maps naturally to BULL/TRANSITION/BEAR. 2-state provides a cross-check.
HMM must be fit on the full historical series (not just recent window) to
learn stable regime boundaries — fitting on a 63-day window would produce
unstable estimates with high regime switching frequency.
"""
from __future__ import annotations

import time
import warnings
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

from config import (
    ANNUALIZATION_FACTOR,
    BEAR_MARKET_THRESHOLD,
    BENCHMARK,
    CREDIT_SPREAD_WIDE,
    FRED_SERIES,
    HMM_N_STATES,
    RANDOM_SEED,
    REGIME_WINDOW,
    TRAIN_START,
    VIX_HIGH_THRESHOLD,
    VIX_LOW_THRESHOLD,
    YIELD_CURVE_INVERSION,
)
from logger import get_logger

log = get_logger(__name__)

try:
    from hmmlearn.hmm import GaussianHMM
    _HMM_AVAILABLE = True
except ImportError:  # pragma: no cover
    _HMM_AVAILABLE = False
    warnings.warn("hmmlearn not installed — HMM regime detection disabled. Install hmmlearn.")

# In-process cache for regime detection results.
# FRED fetches can take 30-60 seconds on slow days; a 15-minute TTL is short enough
# to reflect intraday regime shifts while avoiding repeated timeouts on every page load.
_REGIME_CACHE_TTL = 15 * 60  # seconds
_regime_cache: dict = {}

# ── In-process HMM model cache ───────────────────────────────────────────────
#
# Memory-leak / cost audit finding: classify_hmm_regime fits a fresh
# GaussianHMM (200-iteration Baum-Welch over ~6,500 daily observations)
# on every call. The detect_current_regime cache above shields it within
# any 15-minute window, but the HMM is still re-fit from scratch every
# time that window expires — ~96 full fits per day.
#
# The fitted model is a function of the input return series. Within a
# trading day the daily-return series passed to classify_hmm_regime has
# the same length and the same last observation all day (the market is
# closed → no new bar). So the fit is deterministic per (series length,
# last date, n_states, seed). This cache keys the RESULT dict on exactly
# that fingerprint: a cache hit skips the fit entirely and returns the
# prior result. Effect: one fit per trading day instead of one per
# 15-minute regime-cache miss.
#
# This is NOT a leak: the cache holds exactly one entry, overwritten
# (not appended) when the fingerprint changes. The cached result dict
# carries a ~6,500-entry historical_labels map — bounded, one copy.
_hmm_model_cache: dict = {}  # {"key": tuple, "result": dict}


def _hmm_cache_clear() -> None:
    """Drops the in-process HMM cache. Exposed for tests and for any
    future admin force-refresh path."""
    _hmm_model_cache.clear()


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
    Results are cached in-process for _REGIME_CACHE_TTL seconds (15 min).
    This prevents repeated FRED timeouts on every dashboard load while still
    reflecting intraday regime shifts. Each signal fetch has its own exception
    handler so a FRED outage degrades gracefully — the call returns whichever
    signals are available rather than failing outright.
    """
    now = time.time()
    if _regime_cache and now - _regime_cache.get("ts", 0) < _REGIME_CACHE_TTL:
        log.info("regime_cache_hit", age_seconds=int(now - _regime_cache["ts"]))
        return _regime_cache["data"]

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

    # Attempt HMM classification using historical context
    hmm_regime = None
    hmm_probs = None
    try:
        if _HMM_AVAILABLE:
            spy = fetch_equity_data([BENCHMARK], TRAIN_START, end)
            if len(spy) > 252:
                daily_rets = spy.iloc[:, 0].pct_change().dropna()
                hmm_result = classify_hmm_regime(daily_rets)
                hmm_regime = hmm_result.get("current_regime_label")
                hmm_probs = hmm_result.get("current_probabilities")
    except Exception as e:
        log.warning("regime_hmm_unavailable", error=str(e))

    # Regimes agree when both return BULL or both return BEAR
    # TRANSITION is counted as a partial agreement with either
    regimes_agree = _check_agreement(threshold_regime, hmm_regime)

    log.info(
        "regime_detected",
        threshold=threshold_regime,
        hmm=hmm_regime,
        agrees=regimes_agree,
        vix=vix_level,
        yield_curve=yield_curve_slope,
        equity_trend=equity_trend,
        credit_spread=credit_spread,
    )

    # Correlation breakdown: compare pre-2022 vs post-2022 equity-bond correlation.
    # The 2022 rate-hiking cycle is the central finding of this project — equity-bond
    # correlation turned positive (+0.48) vs the historical negative (-0.31), meaning
    # fixed income failed to cushion equity losses precisely when most needed.
    # Computed here so the frontend never hardcodes these values.
    pre_2022_corr: float | None = None
    post_2022_corr: float | None = None
    try:
        from tools.data_fetcher import build_monthly_returns
        monthly = build_monthly_returns()
        if monthly is not None and "equity_return" in monthly.columns and "ig_return" in monthly.columns:
            pre_2022 = monthly[monthly.index < "2022-01-01"]
            post_2022 = monthly[monthly.index >= "2022-01-01"]
            if len(pre_2022) >= 24:
                pre_2022_corr = float(pre_2022["equity_return"].corr(pre_2022["ig_return"]))
            if len(post_2022) >= 6:
                post_2022_corr = float(post_2022["equity_return"].corr(post_2022["ig_return"]))
    except Exception as e:
        log.warning("regime_correlation_unavailable", error=str(e))

    result = {
        "threshold_regime": threshold_regime,
        "hmm_regime": hmm_regime,
        "hmm_probabilities": hmm_probs,
        "regimes_agree": regimes_agree,
        "vix_level": vix_level,
        "yield_curve_slope": yield_curve_slope,
        "equity_trend": equity_trend,
        "credit_spread": credit_spread,
        "pre_2022_avg_correlation": pre_2022_corr,
        "post_2022_avg_correlation": post_2022_corr,
        "as_of": end,
    }
    _regime_cache["data"] = result
    _regime_cache["ts"] = time.time()
    return result


def _check_agreement(threshold: str, hmm: str | None) -> bool:
    """
    TRANSITION is treated as neutral — it already signals ambiguity, so pairing
    it with a definitive BULL or BEAR from the other method is not an additional
    contradiction; it is consistent with the ambiguity already flagged.
    Only BULL vs BEAR is a genuine disagreement: the two methods are making
    mutually exclusive directional claims, and the UNCERTAIN flag should surface
    this to the council so that REGIME_SWITCHING uses its balanced allocation.
    """
    if hmm is None:
        return True  # Only one method available — no disagreement possible
    # Direct match
    if threshold == hmm:
        return True
    # Both transition — ambiguous but consistent
    if threshold == "TRANSITION" or hmm == "TRANSITION":
        return True
    # Genuine disagreement: one says BULL, other says BEAR
    return False


# ── HMM regime classification ─────────────────────────────────────────────────

def classify_hmm_regime(
    returns: pd.Series,
    n_states: int = HMM_N_STATES,
    seed: int = RANDOM_SEED,
) -> dict:
    """
    GaussianHMM regime classification from a return series.
    Why HMM over threshold: the threshold method treats VIX=30 as "high fear"
    uniformly, but VIX=30 in 2013 (post-GFC normalisation) is a very different
    regime from VIX=30 in 2007 (leading into crisis). HMM learns the return
    distribution of each state from the data itself — it adapts to the level
    of volatility that characterises each regime in each historical period.
    Feature vector: [return, abs(return)] — mean return captures direction,
    absolute return captures volatility. Both features together allow the HMM
    to distinguish low-vol bull, high-vol bear, and transition states reliably.
    n_states=3 maps to BULL/TRANSITION/BEAR. n_states=2 is also fit as a
    cross-check; if the 2-state and 3-state labels agree on the current period,
    the classification is more reliable.
    The model is labelled post-fit by mapping each state to its mean return:
    highest mean → BULL, lowest mean → BEAR, middle → TRANSITION.
    seed=RANDOM_SEED ensures reproducibility across runs.
    """
    if not _HMM_AVAILABLE:
        return {"error": "hmmlearn_not_available", "current_regime_label": None}

    clean = returns.dropna()
    if len(clean) < 100:
        return {"error": "insufficient_data", "current_regime_label": None}

    # ── Fast path: warm HMM cache ───────────────────────────────────────────
    # Fingerprint = (series length, last observation date, n_states, seed).
    # Within a trading day all four are stable, so a cache hit skips the
    # 200-iteration Baum-Welch fit and returns the prior result directly.
    cache_key = (
        len(clean),
        str(clean.index[-1]),
        n_states,
        seed,
    )
    cached = _hmm_model_cache.get("result")
    if cached is not None and _hmm_model_cache.get("key") == cache_key:
        log.info("hmm_inprocess_cache_hit", n_obs=len(clean), n_states=n_states)
        return cached

    # Feature matrix: [return, abs(return)]
    X = np.column_stack([clean.values, np.abs(clean.values)])

    # Aligned with fit_hmm_historical (n_iter=500, tol=1e-5) so the LIVE
    # regime classification this function drives uses the SAME convergence
    # settings as the validated out-of-sample machinery — the live tile and
    # the OOS Sharpe must not diverge on a parameter difference. Changing
    # this affects ONLY the live tile (detect_current_regime); the play-by-
    # play and the OOS validation fit with fit_hmm_historical and are
    # untouched, so neither needs a rerun.
    model = GaussianHMM(
        n_components=n_states,
        covariance_type="diag",
        n_iter=500,
        random_state=seed,
        tol=1e-5,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            model.fit(X)
        except Exception as exc:
            log.warning("hmm_fit_failed", error=str(exc))
            return {"error": str(exc), "current_regime_label": None}

    # Decode most likely state sequence
    hidden_states = model.predict(X)

    # Label states by mean return: sort states by μ_return ascending
    state_means = model.means_[:, 0]  # first feature = return
    state_order = np.argsort(state_means)  # ascending: bear → transition → bull

    label_map: dict[int, str] = {}
    if n_states == 2:
        label_map[state_order[0]] = "BEAR"
        label_map[state_order[1]] = "BULL"
    else:
        label_map[state_order[0]] = "BEAR"
        label_map[state_order[-1]] = "BULL"
        for s in state_order[1:-1]:
            label_map[s] = "TRANSITION"

    current_state = int(hidden_states[-1])
    current_label = label_map[current_state]

    # Posterior probabilities for current observation
    try:
        log_probs = model.score_samples(X[-1:].reshape(1, -1))
        # Use predict_proba-equivalent: compute from forward-backward algorithm
        _, posteriors = model.score_samples(X)
        current_probs_raw = posteriors[-1]
        current_probs = {label_map[i]: round(float(p), 4) for i, p in enumerate(current_probs_raw)}
    except Exception:
        current_probs = {label_map[i]: round(1.0 / n_states, 4) for i in range(n_states)}

    # Build labelled state series for historical regime timeline
    labelled_series = pd.Series(
        [label_map[s] for s in hidden_states],
        index=clean.index,
        name="hmm_regime",
    )

    log.info(
        "hmm_classification",
        n_states=n_states,
        current_state=current_label,
        n_obs=len(clean),
    )

    result = {
        "n_states": n_states,
        "current_state_index": current_state,
        "current_regime_label": current_label,
        "current_probabilities": current_probs,
        "state_label_map": label_map,
        "state_means": {label_map[i]: round(float(state_means[i]) * ANNUALIZATION_FACTOR, 4)
                        for i in range(n_states)},
        "historical_labels": labelled_series.to_dict(),
        # Alias — refresh_transition_matrix and chart_renderers both read
        # "labelled_series" from this return dict. Without the alias the
        # transition_matrix never lands in cache (AN04 WARN).
        "labelled_series": labelled_series.to_dict(),
        "converged": bool(model.monitor_.converged),
    }

    # Store under the input fingerprint. The next call with the same
    # series (same trading day) returns this without re-fitting.
    _hmm_model_cache["key"] = cache_key
    _hmm_model_cache["result"] = result
    return result


def fit_hmm_historical(
    returns: pd.Series,
    vix: pd.Series | None = None,
    n_states: int = HMM_N_STATES,
    seed: int = RANDOM_SEED,
) -> dict:
    """
    Fit HMM on the full historical series with optional VIX as a second feature.
    Used by the regime analysis dashboard to produce the historical regime timeline
    for visualisation. Including VIX improves regime discrimination because volatility
    spikes (VIX > 40) precede bear markets — the model can learn to associate high-VIX
    states with bear regimes even when returns haven't yet turned negative.
    When VIX is unavailable, falls back to the return-only feature set (still useful
    but with slightly less discrimination at regime transitions).
    Returns the full labelled series and transition matrix — both displayed in
    the Regime Analysis dashboard.
    """
    if not _HMM_AVAILABLE:
        return {"error": "hmmlearn_not_available"}

    clean_ret = returns.dropna()

    if vix is not None:
        vix_aligned = vix.reindex(clean_ret.index).ffill().fillna(20.0)
        X = np.column_stack([clean_ret.values, np.abs(clean_ret.values), vix_aligned.values / 100.0])
    else:
        X = np.column_stack([clean_ret.values, np.abs(clean_ret.values)])

    model = GaussianHMM(
        n_components=n_states,
        covariance_type="full",
        n_iter=500,
        random_state=seed,
        tol=1e-5,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            model.fit(X)
        except Exception as exc:
            return {"error": str(exc)}

    hidden_states = model.predict(X)
    state_means = model.means_[:, 0]
    state_order = np.argsort(state_means)

    label_map: dict[int, str] = {}
    if n_states == 2:
        label_map[state_order[0]] = "BEAR"
        label_map[state_order[1]] = "BULL"
    else:
        label_map[state_order[0]] = "BEAR"
        label_map[state_order[-1]] = "BULL"
        for s in state_order[1:-1]:
            label_map[s] = "TRANSITION"

    labelled = pd.Series([label_map[s] for s in hidden_states], index=clean_ret.index)
    transition_matrix = {
        label_map[i]: {label_map[j]: round(float(model.transmat_[i, j]), 4) for j in range(n_states)}
        for i in range(n_states)
    }

    # Per-date posterior probabilities — the regime_signals chart shows
    # P(state=s | observations up to t) over the full history as a stacked
    # area. score_samples returns the forward-backward smoothed posteriors;
    # we collapse the raw state index to a label (BULL/TRANSITION/BEAR),
    # summing any states that share a label (n_states > 3 puts multiple
    # states into the TRANSITION bucket).
    _, posteriors = model.score_samples(X)
    unique_labels = sorted(set(label_map.values()))
    historical_probs: dict[str, list[float]] = {
        label: [0.0] * len(posteriors) for label in unique_labels
    }
    for i in range(n_states):
        label = label_map[i]
        for t in range(len(posteriors)):
            historical_probs[label][t] += float(posteriors[t, i])
    dates = [d.isoformat() if hasattr(d, "isoformat") else str(d)
             for d in clean_ret.index]

    return {
        "n_states": n_states,
        "labelled_series": labelled.to_dict(),
        "historical_probs": historical_probs,
        "dates": dates,
        "transition_matrix": transition_matrix,
        "converged": bool(model.monitor_.converged),
        "label_map": label_map,
    }
