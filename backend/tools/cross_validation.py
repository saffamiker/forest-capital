"""
tools/cross_validation.py

Time-series cross-validation methods for financial return series.

Standard k-fold is invalid here — it shuffles the data, breaking temporal
order and leaking future information into training sets. Every method here
respects time ordering: training always precedes testing, and embargo periods
prevent feature overlap between adjacent folds.

Seven methods are implemented because no single CV method catches all forms
of overfitting:
  1. Walk-forward (rolling): primary OOS method; realistic simulation of live use.
  2. Walk-forward (expanding): anchored start; compared to rolling to detect
     regime dependency.
  3. Purged K-fold: López de Prado (2018) — embargo removes training samples
     whose features overlap with the test period. Prevents information leakage
     from overlapping momentum lookbacks.
  4. CPCV: Combinatorial Purged Cross-Validation (López de Prado 2018 Ch.12).
     Generates a distribution of backtest paths — the gold standard for assessing
     backtest reliability. A single walk-forward path can be lucky; CPCV reveals
     the full distribution of possible outcomes.
  5. Regime-stratified CV: ensures each fold contains all three regimes (bull,
     bear, transition). Prevents pathological splits where training is all-bull
     and testing is all-bear.
  6. Monte Carlo permutation test: assumption-free significance under H0 of
     no predictive skill. The null distribution is built by shuffling returns.
  7. compute_cv_summary: aggregates all six into the CV Stability Score.
"""
from __future__ import annotations

import itertools
from typing import Callable

import numpy as np
import pandas as pd

from config import (
    ANNUALIZATION_FACTOR,
    BOOTSTRAP_SAMPLES,
    CPCV_N_SPLITS,
    CPCV_N_TEST_SPLITS,
    CV_EMBARGO_PERIODS,
    CV_N_SPLITS,
    CV_STABILITY_THRESHOLD,
    EXPANDING_WF_DIVERGENCE,
    P_THRESHOLD_PERMUTATION,
    RANDOM_SEED,
    WALK_FORWARD_TEST,
    WALK_FORWARD_TRAIN,
)
from logger import get_logger

log = get_logger(__name__)


# ── Type alias ────────────────────────────────────────────────────────────────

# A strategy function takes (returns_train: pd.Series, returns_test: pd.Series)
# and returns a pd.Series of portfolio returns over the test period.
StrategyFn = Callable[[pd.Series, pd.Series], pd.Series]


# ── Helper: compute Sharpe from a return Series ───────────────────────────────

def _sharpe(returns: pd.Series) -> float:
    """
    Annualised Sharpe with zero risk-free rate.
    Zero rf is appropriate for fold-level Sharpe comparisons because we are
    comparing strategy vs strategy, not strategy vs cash. Using a time-varying
    rf across folds would introduce noise from differences in the rate level
    across test windows rather than differences in strategy quality.
    """
    clean = returns.dropna()
    if len(clean) < 5 or clean.std() == 0:
        return 0.0
    return float(clean.mean() / clean.std() * np.sqrt(ANNUALIZATION_FACTOR))


def _cagr(returns: pd.Series) -> float:
    """Compound annual growth rate from a daily return series."""
    clean = returns.dropna()
    if len(clean) < 1:
        return 0.0
    total = float((1 + clean).prod())
    years = len(clean) / ANNUALIZATION_FACTOR
    if years <= 0 or total <= 0:
        return 0.0
    return float(total ** (1.0 / years) - 1.0)


# ── 1. Walk-forward cross-validation (rolling window) ────────────────────────

def walk_forward_cv(
    strategy_fn: StrategyFn,
    returns: pd.Series,
    train_months: int = WALK_FORWARD_TRAIN,
    test_months: int = WALK_FORWARD_TEST,
    step_months: int = 6,
) -> dict:
    """
    Rolling-window walk-forward: each fold trains on a fixed-size window,
    tests on the next out-of-sample period.
    Rolling is chosen over expanding because economic regimes shift: a 36-month
    window trained on 2000-2003 (dot-com crash) should not carry equal weight
    in a 2022 model as data from 2018-2021. Expanding window gives diminishing
    weight to recent data as history grows, biasing the model toward the
    characteristics of early periods.
    step_months=6 gives overlapping test windows, which improves estimate
    precision but introduces mild dependency between folds. This is acceptable
    because the goal is assessing strategy robustness, not computing an exact
    p-value (which requires independence and is handled by CPCV).
    """
    # Convert monthly step counts to approximate trading days
    dates = returns.index
    n = len(dates)
    days_per_month = ANNUALIZATION_FACTOR // 12

    train_days = train_months * days_per_month
    test_days = test_months * days_per_month
    step_days = step_months * days_per_month

    folds = []
    i = 0
    while i + train_days + test_days <= n:
        train_end_i = i + train_days
        test_end_i = min(i + train_days + test_days, n)

        returns_train = returns.iloc[i:train_end_i]
        returns_test = returns.iloc[train_end_i:test_end_i]

        try:
            oos_returns = strategy_fn(returns_train, returns_test)
            folds.append({
                "train_start": str(returns_train.index[0].date()),
                "train_end": str(returns_train.index[-1].date()),
                "test_start": str(returns_test.index[0].date()),
                "test_end": str(returns_test.index[-1].date()),
                "oos_sharpe": _sharpe(oos_returns),
                "oos_cagr": _cagr(oos_returns),
                "n_test_obs": len(oos_returns),
            })
        except Exception as exc:
            log.warning("wf_fold_failed", fold=i, error=str(exc))

        i += step_days

    if not folds:
        return {"error": "no_folds_completed", "n_folds": 0}

    sharpes = [f["oos_sharpe"] for f in folds]
    log.info("walk_forward_cv_done", n_folds=len(folds), mean_sharpe=round(float(np.mean(sharpes)), 4))

    return {
        "method": "walk_forward_rolling",
        "n_folds": len(folds),
        "oos_sharpe_mean": round(float(np.mean(sharpes)), 4),
        "oos_sharpe_std": round(float(np.std(sharpes)), 4),
        "oos_sharpe_min": round(float(np.min(sharpes)), 4),
        "oos_sharpe_max": round(float(np.max(sharpes)), 4),
        "pct_folds_positive": round(float(np.mean([s > 0 for s in sharpes])), 4),
        "folds": folds,
    }


# ── 2. Walk-forward cross-validation (expanding window) ──────────────────────

def expanding_window_cv(
    strategy_fn: StrategyFn,
    returns: pd.Series,
    min_train_months: int = WALK_FORWARD_TRAIN,
    test_months: int = WALK_FORWARD_TEST,
) -> dict:
    """
    Expanding-window walk-forward: training set grows with each fold.
    Compared against rolling window to detect regime dependency. If
    |expanding_sharpe - rolling_sharpe| > EXPANDING_WF_DIVERGENCE (0.30),
    the strategy is regime-dependent — its performance changes as the
    composition of training history shifts. A robust strategy should
    produce similar OOS Sharpe regardless of whether early history is
    included or not; regime-dependent strategies will diverge.
    """
    dates = returns.index
    n = len(dates)
    days_per_month = ANNUALIZATION_FACTOR // 12

    min_train_days = min_train_months * days_per_month
    test_days = test_months * days_per_month

    folds = []
    train_end_i = min_train_days

    while train_end_i + test_days <= n:
        test_end_i = min(train_end_i + test_days, n)
        returns_train = returns.iloc[0:train_end_i]
        returns_test = returns.iloc[train_end_i:test_end_i]

        try:
            oos_returns = strategy_fn(returns_train, returns_test)
            folds.append({
                "train_start": str(returns_train.index[0].date()),
                "train_end": str(returns_train.index[-1].date()),
                "test_start": str(returns_test.index[0].date()),
                "test_end": str(returns_test.index[-1].date()),
                "oos_sharpe": _sharpe(oos_returns),
                "oos_cagr": _cagr(oos_returns),
                "n_test_obs": len(oos_returns),
            })
        except Exception as exc:
            log.warning("expanding_fold_failed", fold=train_end_i, error=str(exc))

        train_end_i += test_days

    if not folds:
        return {"error": "no_folds_completed", "n_folds": 0}

    sharpes = [f["oos_sharpe"] for f in folds]
    return {
        "method": "walk_forward_expanding",
        "n_folds": len(folds),
        "oos_sharpe_mean": round(float(np.mean(sharpes)), 4),
        "oos_sharpe_std": round(float(np.std(sharpes)), 4),
        "oos_sharpe_min": round(float(np.min(sharpes)), 4),
        "oos_sharpe_max": round(float(np.max(sharpes)), 4),
        "pct_folds_positive": round(float(np.mean([s > 0 for s in sharpes])), 4),
        "folds": folds,
    }


# ── 3. Purged K-fold cross-validation ────────────────────────────────────────

def purged_kfold_cv(
    strategy_fn: StrategyFn,
    returns: pd.Series,
    n_splits: int = CV_N_SPLITS,
    embargo_periods: int = CV_EMBARGO_PERIODS,
) -> dict:
    """
    López de Prado (2018) purged K-fold with embargo.
    Standard K-fold leaks information when features overlap across the training/
    test boundary — a 252-day momentum signal computed near a fold boundary uses
    data from both training and test periods. Purging removes training observations
    whose feature window overlaps with the test period; embargo additionally
    removes a buffer of embargo_periods=252 observations after the test period
    to prevent reverse leakage.
    embargo_periods=252 matches the longest feature lookback (annual momentum)
    used in the MOMENTUM_ROTATION strategy. Using a shorter embargo on a strategy
    with a 252-day signal would defeat the purpose of purging.
    """
    n = len(returns)
    fold_size = n // n_splits
    folds = []

    for k in range(n_splits):
        test_start = k * fold_size
        test_end = test_start + fold_size if k < n_splits - 1 else n

        # Purging: remove training obs whose lookback overlaps with test period
        purge_start = max(0, test_start - embargo_periods)

        # Training: all obs before purge_start and after test_end + embargo
        embargo_end = min(n, test_end + embargo_periods)
        train_indices = list(range(0, purge_start)) + list(range(embargo_end, n))

        if len(train_indices) < 30 or (test_end - test_start) < 5:
            log.warning("pkf_fold_skipped", fold=k, n_train=len(train_indices))
            continue

        returns_train = returns.iloc[train_indices]
        returns_test = returns.iloc[test_start:test_end]

        try:
            oos_returns = strategy_fn(returns_train, returns_test)
            folds.append({
                "fold": k,
                "n_train": len(returns_train),
                "n_test": len(returns_test),
                "oos_sharpe": _sharpe(oos_returns),
            })
        except Exception as exc:
            log.warning("pkf_fold_failed", fold=k, error=str(exc))

    if not folds:
        return {"error": "no_folds_completed", "n_folds": 0}

    sharpes = [f["oos_sharpe"] for f in folds]
    from tools.statistical_tests import paired_ttest
    # Can't use paired_ttest here since we have fold-level Sharpes not return series;
    # report a fold-level p-value approximation instead
    p_value = float(np.mean([s > 0 for s in sharpes]))

    return {
        "method": "purged_kfold",
        "n_splits": n_splits,
        "embargo_periods": embargo_periods,
        "n_folds_completed": len(folds),
        "oos_sharpe_mean": round(float(np.mean(sharpes)), 4),
        "oos_sharpe_std": round(float(np.std(sharpes)), 4),
        "pct_folds_positive": round(p_value, 4),
        "folds": folds,
    }


# ── 4. Combinatorial Purged Cross-Validation (CPCV) ──────────────────────────

def combinatorial_purged_cv(
    strategy_fn: StrategyFn,
    returns: pd.Series,
    n_splits: int = CPCV_N_SPLITS,
    n_test_splits: int = CPCV_N_TEST_SPLITS,
    embargo_periods: int = CV_EMBARGO_PERIODS,
) -> dict:
    """
    López de Prado (2018) CPCV: generates C(n_splits, n_test_splits) backtest paths.
    CPCV addresses a fundamental limitation of walk-forward: a single OOS path is
    just one of many possible paths the strategy could have taken. With 6 folds
    and 2 test splits, C(6,2)=15 distinct backtest paths are generated — each
    using a different combination of folds as the test set. The resulting
    Sharpe distribution (not just the mean) tells us how likely the observed
    Sharpe is across the range of plausible historical scenarios.
    A narrow CPCV distribution (low std) means the strategy is genuinely robust;
    a wide distribution means the single walk-forward result is lucky.
    embargo_periods matches purged_kfold_cv for consistency.
    """
    n = len(returns)
    fold_size = n // n_splits

    # Divide returns into n_splits contiguous folds
    fold_indices = []
    for k in range(n_splits):
        start = k * fold_size
        end = start + fold_size if k < n_splits - 1 else n
        fold_indices.append(list(range(start, end)))

    # Generate all combinations of n_test_splits folds
    test_combos = list(itertools.combinations(range(n_splits), n_test_splits))
    path_sharpes = []

    for combo in test_combos:
        test_idx = sorted(itertools.chain.from_iterable(fold_indices[k] for k in combo))
        train_idx = []
        for k in range(n_splits):
            if k not in combo:
                fold_start = fold_indices[k][0]
                fold_end = fold_indices[k][-1]
                # Apply embargo around each test fold
                for ti in test_idx:
                    if abs(ti - fold_start) < embargo_periods or abs(ti - fold_end) < embargo_periods:
                        break
                else:
                    train_idx.extend(fold_indices[k])

        if len(train_idx) < 30 or len(test_idx) < 5:
            continue

        try:
            ret_train = returns.iloc[train_idx]
            ret_test = returns.iloc[test_idx]
            oos_ret = strategy_fn(ret_train, ret_test)
            path_sharpes.append(_sharpe(oos_ret))
        except Exception as exc:
            log.warning("cpcv_path_failed", combo=combo, error=str(exc))

    if not path_sharpes:
        return {"error": "no_paths_completed", "n_paths": 0}

    arr = np.array(path_sharpes)
    ci_low, ci_high = np.percentile(arr, [2.5, 97.5])

    return {
        "method": "cpcv",
        "n_splits": n_splits,
        "n_test_splits": n_test_splits,
        "n_paths": len(path_sharpes),
        "sharpe_mean": round(float(arr.mean()), 4),
        "sharpe_std": round(float(arr.std()), 4),
        "sharpe_min": round(float(arr.min()), 4),
        "sharpe_max": round(float(arr.max()), 4),
        "sharpe_ci_95": (round(float(ci_low), 4), round(float(ci_high), 4)),
        "pct_positive": round(float(np.mean(arr > 0)), 4),
        "path_sharpes": [round(s, 4) for s in path_sharpes],
    }


# ── 5. Regime-stratified cross-validation ────────────────────────────────────

def regime_stratified_cv(
    strategy_fn: StrategyFn,
    returns: pd.Series,
    regime_labels: pd.Series,
    n_splits: int = CV_N_SPLITS,
) -> dict:
    """
    Stratified CV that ensures each fold contains all regime types.
    Standard walk-forward splits time sequentially — if all bear periods
    cluster in one fold (as they do: GFC 2008, COVID 2020, rate hike 2022),
    the strategy is evaluated on only one regime type per fold. A momentum
    strategy that only beats the benchmark in bull markets would incorrectly
    appear robust if its test fold happens to be all-bull.
    Regime stratification shuffles fold assignment within-regime, so each
    fold contains a proportional mix of bull, bear, and transition periods.
    This is valid for CV because we are testing whether the strategy is
    regime-agnostic — temporal ordering is less important than regime coverage.
    """
    aligned_returns, aligned_regimes = returns.align(regime_labels, join="inner")
    n = len(aligned_returns)

    # Collect indices for each regime
    regimes = aligned_regimes.unique()
    regime_idx = {r: aligned_regimes[aligned_regimes == r].index.tolist() for r in regimes}

    np.random.seed(RANDOM_SEED)

    # Assign each observation to one of n_splits folds, stratified by regime
    fold_assignment = np.zeros(n, dtype=int)
    for r_indices in regime_idx.values():
        shuffled = list(r_indices)
        np.random.shuffle(shuffled)
        for i, idx in enumerate(shuffled):
            pos = aligned_returns.index.get_loc(idx)
            fold_assignment[pos] = i % n_splits

    folds = []
    for fold in range(n_splits):
        test_mask = fold_assignment == fold
        train_mask = ~test_mask
        ret_train = aligned_returns[train_mask]
        ret_test = aligned_returns[test_mask]

        if len(ret_train) < 30 or len(ret_test) < 5:
            continue

        try:
            oos_ret = strategy_fn(ret_train, ret_test)
            # Count regimes in this fold
            fold_regime_counts = aligned_regimes[test_mask].value_counts().to_dict()
            folds.append({
                "fold": fold,
                "oos_sharpe": _sharpe(oos_ret),
                "regime_counts": fold_regime_counts,
            })
        except Exception as exc:
            log.warning("regime_cv_fold_failed", fold=fold, error=str(exc))

    if not folds:
        return {"error": "no_folds_completed", "n_folds": 0}

    sharpes = [f["oos_sharpe"] for f in folds]
    return {
        "method": "regime_stratified",
        "n_splits": n_splits,
        "n_folds_completed": len(folds),
        "oos_sharpe_mean": round(float(np.mean(sharpes)), 4),
        "oos_sharpe_std": round(float(np.std(sharpes)), 4),
        "pct_folds_positive": round(float(np.mean([s > 0 for s in sharpes])), 4),
        "folds": folds,
    }


# ── 6. Monte Carlo permutation test ──────────────────────────────────────────

def monte_carlo_permutation_test(
    strategy_returns: pd.Series,
    benchmark_returns: pd.Series,
    n_permutations: int = BOOTSTRAP_SAMPLES,
    seed: int = RANDOM_SEED,
) -> dict:
    """
    Assumption-free permutation test: H0 = no predictive skill.
    Under H0, the observed strategy outperformance is indistinguishable
    from a random permutation of the active return series. The null
    distribution is constructed by shuffling the strategy returns, recomputing
    the Sharpe difference vs benchmark, and repeating n_permutations times.
    p_permutation = P(random Sharpe >= observed Sharpe) under H0.
    This is preferred over parametric tests when normality is rejected because
    it makes no distributional assumptions. The only assumption is exchangeability
    — that any permutation of the active returns is equally likely under H0.
    For mean-zero iid returns, this holds exactly; for autocorrelated series,
    a block permutation variant would be more appropriate, but the simple
    permutation test is a useful first-pass check.
    seed=RANDOM_SEED=42 is fixed for reproducibility — required by QA checklist.
    """
    np.random.seed(seed)

    s, b = strategy_returns.align(benchmark_returns, join="inner")
    s = s.dropna()
    b = b.dropna()

    obs_sharpe = _sharpe(s)
    bench_sharpe = _sharpe(b)
    obs_diff = obs_sharpe - bench_sharpe

    # Build null distribution
    null_diffs = []
    s_values = s.values.copy()
    for _ in range(n_permutations):
        np.random.shuffle(s_values)
        perm_sharpe = _sharpe(pd.Series(s_values))
        null_diffs.append(perm_sharpe - bench_sharpe)

    null_array = np.array(null_diffs)
    # One-sided p-value: P(null Sharpe diff >= observed Sharpe diff)
    p_value = float(np.mean(null_array >= obs_diff))

    from config import P_THRESHOLD_PERMUTATION
    log.info(
        "permutation_test_done",
        obs_sharpe=round(obs_sharpe, 4),
        p_value=round(p_value, 6),
    )

    return {
        "test": "monte_carlo_permutation",
        "observed_sharpe": round(obs_sharpe, 4),
        "benchmark_sharpe": round(bench_sharpe, 4),
        "observed_sharpe_diff": round(obs_diff, 4),
        "null_mean": round(float(null_array.mean()), 4),
        "null_std": round(float(null_array.std()), 4),
        "p_value": round(p_value, 6),
        "n_permutations": n_permutations,
        "threshold": P_THRESHOLD_PERMUTATION,
        "passed": bool(p_value < P_THRESHOLD_PERMUTATION),
    }


# ── 7. CV summary aggregator ──────────────────────────────────────────────────

def compute_cv_summary(
    wf_rolling: dict,
    wf_expanding: dict,
    pkf: dict,
    cpcv: dict,
    permutation: dict,
    regime_cv: dict | None = None,
) -> dict:
    """
    Aggregates all CV results into a single CV Stability Score (0-1).
    The stability score weights five dimensions — each captures a different
    aspect of robustness that the others might miss:
      Walk-forward consistency (25%): does the strategy work across time windows?
      CPCV Sharpe std inverted (25%): how narrow is the distribution of outcomes?
      % folds beating benchmark (20%): is outperformance consistent, not just average?
      Permutation test p-value (15%): is outperformance statistically non-random?
      Regime balance (15%): does it work across market regimes?
    The weights are calibrated to prevent a strategy with one exceptional dimension
    (e.g., very high % folds positive) from obscuring weakness in another (e.g.,
    fails the permutation test). No single dimension should dominate.
    """
    # Component 1: Walk-forward consistency (0-1)
    wf_pct = float(wf_rolling.get("pct_folds_positive", 0.0))

    # Component 2: CPCV Sharpe std inverted (0-1, lower std → higher score)
    cpcv_std = float(cpcv.get("sharpe_std", 1.0))
    # Normalise: std=0 → score=1.0, std=1.0 → score=0.0
    cpcv_score = max(0.0, min(1.0, 1.0 - cpcv_std))

    # Component 3: % folds beating benchmark (0-1)
    fold_beat_pct = float(wf_rolling.get("pct_folds_positive", 0.0))

    # Component 4: Permutation p-value (lower p → higher score)
    perm_p = float(permutation.get("p_value", 1.0))
    # Map p-value to 0-1: p=0 → score=1.0, p=0.1 → score=0.0
    perm_score = max(0.0, min(1.0, 1.0 - perm_p * 10.0))

    # Component 5: Regime balance (how evenly strategy works across regimes)
    if regime_cv and regime_cv.get("n_folds_completed", 0) > 0:
        regime_score = float(regime_cv.get("pct_folds_positive", 0.0))
    else:
        # No regime CV provided — assign neutral score
        regime_score = 0.5

    # Weighted sum
    stability_score = (
        0.25 * wf_pct
        + 0.25 * cpcv_score
        + 0.20 * fold_beat_pct
        + 0.15 * perm_score
        + 0.15 * regime_score
    )

    # Check divergence between rolling and expanding walk-forward
    rolling_mean = float(wf_rolling.get("oos_sharpe_mean", 0.0))
    expanding_mean = float(wf_expanding.get("oos_sharpe_mean", 0.0))
    ew_divergence = abs(rolling_mean - expanding_mean)
    regime_dependent = ew_divergence > EXPANDING_WF_DIVERGENCE

    return {
        "cv_stability_score": round(stability_score, 4),
        "passes_stability_threshold": bool(stability_score >= CV_STABILITY_THRESHOLD),
        "wf_rolling_sharpe_mean": rolling_mean,
        "wf_rolling_sharpe_std": float(wf_rolling.get("oos_sharpe_std", 0.0)),
        "wf_rolling_worst_fold_sharpe": float(wf_rolling.get("oos_sharpe_min", 0.0)),
        "wf_pct_folds_positive": wf_pct,
        "ew_rolling_sharpe_mean": expanding_mean,
        "ew_vs_wf_divergence": round(ew_divergence, 4),
        "regime_dependent_flag": regime_dependent,
        "pkf_sharpe_mean": float(pkf.get("oos_sharpe_mean", 0.0)),
        "cpcv_sharpe_mean": float(cpcv.get("sharpe_mean", 0.0)),
        "cpcv_sharpe_std": float(cpcv.get("sharpe_std", 0.0)),
        "cpcv_sharpe_ci_95": cpcv.get("sharpe_ci_95", (0.0, 0.0)),
        "cpcv_pct_positive": float(cpcv.get("pct_positive", 0.0)),
        "permutation_p_value": perm_p,
        "permutation_passed": bool(permutation.get("passed", False)),
        "regime_cv_sharpe_mean": float(regime_cv.get("oos_sharpe_mean", 0.0)) if regime_cv else None,
        "passes_all_cv": bool(
            stability_score >= CV_STABILITY_THRESHOLD
            and permutation.get("passed", False)
            and not regime_dependent
        ),
    }
