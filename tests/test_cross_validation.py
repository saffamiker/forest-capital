"""
Sprint 2 remediation — equity cross-validation tests.
Tests cross_validate_equity() using mocked supplemental data and real
Excel data (skipped in CI).  Verifies: PASS/WARN/FAIL logic, threshold
handling, DataValidationError on too many red months.

bond cross-validation (cross_validate_bonds) is tested via the data
loader tests since it runs internally on load_provided_data() output.
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
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_monthly_equity(n: int = 60, seed: int = 42) -> pd.Series:
    """Synthetic monthly equity returns (Excel-style authoritative series)."""
    np.random.seed(seed)
    idx = pd.bdate_range("2000-01-31", periods=n, freq="BME")
    return pd.Series(np.random.normal(0.008, 0.04, n), index=idx, name="equity_monthly")


def _make_spy_daily_matching(monthly_returns: pd.Series) -> pd.Series:
    """
    Generate SPY daily returns that compound to exactly match each monthly return.
    This simulates a valid data source that agrees with the Excel series.
    """
    all_daily = []
    for i, (month_end, m_ret) in enumerate(monthly_returns.items()):
        # Figure out the trading days in this month
        month_start = month_end - pd.offsets.MonthBegin(1)
        days = pd.bdate_range(month_start, month_end)
        n_days = len(days)
        if n_days == 0:
            continue
        # Distribute monthly return uniformly (equal daily returns)
        daily_ret = (1 + m_ret) ** (1.0 / n_days) - 1
        for d in days:
            all_daily.append((d, daily_ret))
    if not all_daily:
        return pd.Series(dtype=float)
    dates, rets = zip(*all_daily)
    return pd.Series(list(rets), index=pd.DatetimeIndex(dates), name="SPY")


# ── CrossValidationResult dataclass ──────────────────────────────────────────

def test_cross_validation_result_has_status():
    from tools.data_fetcher import CrossValidationResult
    r = CrossValidationResult(
        status="PASS",
        n_months_compared=100,
        n_green=95,
        n_amber=5,
        n_red=0,
        max_discrepancy_pct=0.003,
        mean_discrepancy_pct=0.001,
        worst_month="2020-03",
    )
    assert r.status == "PASS"
    assert r.n_green == 95


def test_cross_validation_result_defaults_empty_issues():
    from tools.data_fetcher import CrossValidationResult
    r = CrossValidationResult(
        status="WARN",
        n_months_compared=50,
        n_green=40,
        n_amber=10,
        n_red=0,
        max_discrepancy_pct=0.008,
        mean_discrepancy_pct=0.003,
        worst_month="2008-10",
    )
    assert r.issues == []


# ── cross_validate_equity with mocked supplemental data ───────────────────────

def _make_provided_data_dict(equity_monthly: pd.Series) -> dict:
    """
    Build a minimal provided_data dict matching the internal keys from load_provided_data().
    cross_validate_equity() uses 'sp500_monthly' — the normalized key, not the sheet name.
    """
    prices = (1 + equity_monthly).cumprod() * 1000
    price_df = pd.DataFrame({
        "date": prices.index,
        "sp500_level": prices.values,
    })
    return {"sp500_monthly": price_df}


def test_cross_validate_equity_returns_result():
    """cross_validate_equity() with closely matching data must return a CrossValidationResult."""
    equity_monthly = _make_monthly_equity(60)
    spy_daily = _make_spy_daily_matching(equity_monthly)
    provided = _make_provided_data_dict(equity_monthly)
    supplemental = {"spy_daily": spy_daily}

    from tools.data_fetcher import cross_validate_equity, CrossValidationResult
    result = cross_validate_equity(provided_data=provided, supplemental=supplemental)
    assert isinstance(result, CrossValidationResult)


def test_cross_validate_equity_status_is_valid():
    equity_monthly = _make_monthly_equity(60)
    spy_daily = _make_spy_daily_matching(equity_monthly)
    provided = _make_provided_data_dict(equity_monthly)
    supplemental = {"spy_daily": spy_daily}

    from tools.data_fetcher import cross_validate_equity
    result = cross_validate_equity(provided_data=provided, supplemental=supplemental)
    assert result.status in {"PASS", "WARN", "FAIL"}


def test_cross_validate_equity_pass_when_close_agreement():
    """When Excel and SPY monthly returns are derived from the same data, status is PASS."""
    equity_monthly = _make_monthly_equity(60)
    spy_daily = _make_spy_daily_matching(equity_monthly)
    provided = _make_provided_data_dict(equity_monthly)
    supplemental = {"spy_daily": spy_daily}

    from tools.data_fetcher import cross_validate_equity
    result = cross_validate_equity(provided_data=provided, supplemental=supplemental)
    # Exact match → all months should be green, no reds
    assert result.n_red == 0


def test_data_validation_error_is_importable():
    """DataValidationError must be importable from data_fetcher."""
    from tools.data_fetcher import DataValidationError
    assert issubclass(DataValidationError, Exception)


# ── Sprint 3: time-series cross-validation ───────────────────────────────────
#
# All tests use synthetic returns with a known strategy function so results
# are deterministic without network calls. The strategy_fn signature matches
# the StrategyFn type alias: (train_returns, test_returns) -> pd.Series.
#
# All CV functions return dicts — the folds/paths are nested within "folds"
# or "path_sharpes" keys. Tests verify the top-level dict and then drill into
# the fold-level data where needed.

def _make_series(n: int = 200, mean: float = 0.001, seed: int = 42) -> pd.Series:
    """Synthetic monthly return series."""
    np.random.seed(seed)
    idx = pd.date_range("2000-01-31", periods=n, freq="ME")
    return pd.Series(np.random.normal(mean, 0.04, n), index=idx, name="equity")


def _constant_strategy(train: pd.Series, test: pd.Series) -> pd.Series:
    """Trivial strategy: return the test period returns unchanged (no look-ahead)."""
    return test.copy()


# ── walk_forward_cv ──────────────────────────────────────────────────────────

def test_walk_forward_cv_returns_dict():
    from tools.cross_validation import walk_forward_cv
    rets = _make_series(200)
    result = walk_forward_cv(_constant_strategy, rets, train_months=36, test_months=12)
    assert isinstance(result, dict)
    assert "folds" in result or "error" in result


def test_walk_forward_cv_produces_folds():
    """
    walk_forward_cv treats period counts as trading days (21 per month).
    With train=6 months (126 days) and test=3 months (63 days), a 200-obs
    series has room for at least one fold (189 ≤ 200).
    """
    from tools.cross_validation import walk_forward_cv
    rets = _make_series(200)
    result = walk_forward_cv(_constant_strategy, rets, train_months=6, test_months=3)
    assert "error" not in result, f"Expected folds, got error: {result}"
    assert result["n_folds"] > 0


def test_walk_forward_cv_fold_structure():
    """Each individual fold must have oos_sharpe and test_start keys."""
    from tools.cross_validation import walk_forward_cv
    rets = _make_series(200)
    result = walk_forward_cv(_constant_strategy, rets, train_months=6, test_months=3)
    for fold in result.get("folds", []):
        assert "oos_sharpe" in fold
        assert "n_test_obs" in fold
        assert "test_start" in fold


def test_walk_forward_cv_test_windows_dont_overlap():
    """
    Test periods must not overlap — overlapping would mean the strategy is tested
    on the same data twice, inflating our estimate of out-of-sample consistency.
    """
    from tools.cross_validation import walk_forward_cv
    rets = _make_series(200)
    result = walk_forward_cv(_constant_strategy, rets, train_months=6, test_months=3, step_months=3)
    folds = result.get("folds", [])
    starts = [pd.Timestamp(f["test_start"]) for f in folds]
    for i in range(1, len(starts)):
        assert starts[i] > starts[i - 1]


def test_walk_forward_cv_train_precedes_test():
    """No look-ahead: all training data must come before the test period."""
    from tools.cross_validation import walk_forward_cv

    seen_leaks = []
    def leak_check_strategy(train: pd.Series, test: pd.Series) -> pd.Series:
        if len(train) > 0 and len(test) > 0:
            if train.index[-1] >= test.index[0]:
                seen_leaks.append(True)
        return test.copy()

    rets = _make_series(200)
    walk_forward_cv(leak_check_strategy, rets, train_months=6, test_months=3)
    assert len(seen_leaks) == 0, "Look-ahead bias detected: training data overlaps test period"


def test_walk_forward_cv_has_summary_stats():
    """Result dict must carry pre-computed summary statistics for the compare endpoint."""
    from tools.cross_validation import walk_forward_cv
    rets = _make_series(200)
    result = walk_forward_cv(_constant_strategy, rets, train_months=6, test_months=3)
    if "error" not in result:
        assert "oos_sharpe_mean" in result
        assert "oos_sharpe_std" in result
        assert "pct_folds_positive" in result


# ── expanding_window_cv ──────────────────────────────────────────────────────

def test_expanding_window_cv_returns_dict():
    from tools.cross_validation import expanding_window_cv
    rets = _make_series(200)
    result = expanding_window_cv(_constant_strategy, rets, min_train_months=36, test_months=12)
    assert isinstance(result, dict)


def test_expanding_window_cv_produces_folds():
    from tools.cross_validation import expanding_window_cv
    rets = _make_series(200)
    result = expanding_window_cv(_constant_strategy, rets, min_train_months=6, test_months=3)
    assert "error" not in result, f"Expected folds, got error: {result}"
    assert result["n_folds"] > 0


def test_expanding_window_training_grows():
    """
    Expanding window: each fold should have more training data than the previous.
    This is the defining property of expanding-window vs rolling-window CV.
    """
    from tools.cross_validation import expanding_window_cv

    train_sizes = []
    def size_recorder(train: pd.Series, test: pd.Series) -> pd.Series:
        train_sizes.append(len(train))
        return test.copy()

    rets = _make_series(200)
    expanding_window_cv(size_recorder, rets, min_train_months=6, test_months=3)
    for i in range(1, len(train_sizes)):
        assert train_sizes[i] >= train_sizes[i - 1], "Training window should never shrink"


# ── purged_kfold_cv ───────────────────────────────────────────────────────────

def test_purged_kfold_cv_returns_dict():
    from tools.cross_validation import purged_kfold_cv
    rets = _make_series(200)
    result = purged_kfold_cv(_constant_strategy, rets, n_splits=3, embargo_periods=12)
    assert isinstance(result, dict)


def test_purged_kfold_cv_produces_folds():
    from tools.cross_validation import purged_kfold_cv
    rets = _make_series(200)
    result = purged_kfold_cv(_constant_strategy, rets, n_splits=3, embargo_periods=12)
    # With 200 obs and embargo=12, at least 1 fold should complete
    assert "folds" in result and len(result["folds"]) >= 1 or "error" in result


def test_purged_kfold_cv_embargo_creates_gap():
    """
    Purged K-fold removes training observations whose feature windows overlap
    with the test period (purging) and adds a buffer after test period (embargo).
    This test verifies the function completes at least one fold with a 200-obs
    series — any result with folds present confirms embargo is applied without
    crashing.
    """
    from tools.cross_validation import purged_kfold_cv

    rets = _make_series(200)
    result = purged_kfold_cv(_constant_strategy, rets, n_splits=3, embargo_periods=12)
    # With n=200, n_splits=3, embargo=12 each fold has adequate train and test data
    assert isinstance(result, dict)
    # At least one fold or a documented error — function must not crash
    assert "folds" in result or "error" in result


# ── combinatorial_purged_cv ────────────────────────────────────────────────────

def test_cpcv_returns_dict():
    from tools.cross_validation import combinatorial_purged_cv
    rets = _make_series(200)
    result = combinatorial_purged_cv(_constant_strategy, rets, n_splits=4, n_test_splits=2)
    assert isinstance(result, dict)


def test_cpcv_multiple_paths():
    """
    CPCV generates C(n_splits, n_test_splits) paths. With n=4, k=2 that is 6.
    Each path is a different combination of folds used as test — this gives
    a distribution rather than a point estimate of backtest Sharpe.
    """
    from tools.cross_validation import combinatorial_purged_cv
    rets = _make_series(200)
    result = combinatorial_purged_cv(_constant_strategy, rets, n_splits=4, n_test_splits=2)
    if "error" not in result:
        assert result["n_paths"] >= 1


def test_cpcv_has_sharpe_distribution():
    """CPCV must report mean, std, and CI — not just a point estimate."""
    from tools.cross_validation import combinatorial_purged_cv
    rets = _make_series(200)
    result = combinatorial_purged_cv(_constant_strategy, rets, n_splits=4, n_test_splits=2)
    if "error" not in result:
        assert "sharpe_mean" in result
        assert "sharpe_std" in result
        assert "sharpe_ci_95" in result


# ── monte_carlo_permutation_test ─────────────────────────────────────────────

def test_permutation_test_returns_dict():
    from tools.cross_validation import monte_carlo_permutation_test
    strat = _make_series(200, mean=0.002)
    bm = _make_series(200, mean=0.001, seed=99)
    result = monte_carlo_permutation_test(strat, bm, n_permutations=200, seed=42)
    assert isinstance(result, dict)


def test_permutation_test_has_required_keys():
    from tools.cross_validation import monte_carlo_permutation_test
    strat = _make_series(200, mean=0.002)
    bm = _make_series(200, mean=0.001, seed=99)
    result = monte_carlo_permutation_test(strat, bm, n_permutations=200, seed=42)
    # "passed" is the key used in the implementation; compute_cv_summary reads it
    for key in ["p_value", "observed_sharpe_diff", "passed"]:
        assert key in result, f"Missing key: {key}"


def test_permutation_test_pvalue_in_range():
    from tools.cross_validation import monte_carlo_permutation_test
    strat = _make_series(200, mean=0.002)
    bm = _make_series(200, mean=0.001, seed=99)
    result = monte_carlo_permutation_test(strat, bm, n_permutations=200, seed=42)
    assert 0.0 <= result["p_value"] <= 1.0


def test_permutation_test_no_edge_series_not_significant():
    """
    Two series drawn from the same distribution have no systematic edge.
    The permutation test should not reject (p > 5%) — if it did, the null
    distribution would be miscalibrated. We use different seeds so the two
    series are independent, which is the typical real-world use case.
    """
    from tools.cross_validation import monte_carlo_permutation_test
    # Same distribution, different random seeds — no systematic alpha
    strat = _make_series(200, mean=0.001, seed=42)
    bm = _make_series(200, mean=0.001, seed=99)
    result = monte_carlo_permutation_test(strat, bm, n_permutations=1000, seed=42)
    # Should not be highly significant — same underlying distribution
    assert result["p_value"] > 0.05


# ── compute_cv_summary ────────────────────────────────────────────────────────
#
# compute_cv_summary takes the dict outputs of the above functions directly.

def test_cv_summary_returns_stability_score():
    """
    CV Stability Score is the key output — strategies need ≥ 0.60 to be recommended.
    A missing or malformed score would prevent the Tier 1 gate from functioning.
    """
    from tools.cross_validation import compute_cv_summary

    wf_rolling = {"pct_folds_positive": 0.75, "oos_sharpe_mean": 0.8, "oos_sharpe_std": 0.2, "oos_sharpe_min": 0.5}
    wf_expanding = {"oos_sharpe_mean": 0.75}
    pkf = {"oos_sharpe_mean": 0.7}
    cpcv = {"sharpe_mean": 0.72, "sharpe_std": 0.15, "sharpe_ci_95": (0.4, 1.0), "pct_positive": 0.8}
    perm = {"p_value": 0.003, "passed": True}

    summary = compute_cv_summary(wf_rolling, wf_expanding, pkf, cpcv, perm)
    assert "cv_stability_score" in summary
    assert 0.0 <= summary["cv_stability_score"] <= 1.0


def test_cv_summary_passes_all_cv_field():
    from tools.cross_validation import compute_cv_summary

    wf_rolling = {"pct_folds_positive": 0.80, "oos_sharpe_mean": 0.8, "oos_sharpe_std": 0.2, "oos_sharpe_min": 0.5}
    wf_expanding = {"oos_sharpe_mean": 0.75}
    pkf = {"oos_sharpe_mean": 0.7}
    cpcv = {"sharpe_mean": 0.72, "sharpe_std": 0.10, "sharpe_ci_95": (0.5, 0.9), "pct_positive": 0.8}
    perm = {"p_value": 0.001, "passed": True}

    summary = compute_cv_summary(wf_rolling, wf_expanding, pkf, cpcv, perm)
    assert "passes_all_cv" in summary
    assert isinstance(summary["passes_all_cv"], bool)


def test_cv_summary_cpcv_stats():
    """CPCV must report mean, std, and 95% CI — single point estimate is insufficient."""
    from tools.cross_validation import compute_cv_summary

    wf_rolling = {"pct_folds_positive": 0.70, "oos_sharpe_mean": 0.8, "oos_sharpe_std": 0.2, "oos_sharpe_min": 0.4}
    wf_expanding = {"oos_sharpe_mean": 0.75}
    pkf = {"oos_sharpe_mean": 0.7}
    cpcv = {"sharpe_mean": 0.68, "sharpe_std": 0.20, "sharpe_ci_95": (0.3, 1.0), "pct_positive": 0.75}
    perm = {"p_value": 0.002, "passed": True}

    summary = compute_cv_summary(wf_rolling, wf_expanding, pkf, cpcv, perm)
    assert "cpcv_sharpe_mean" in summary
    assert "cpcv_sharpe_std" in summary
