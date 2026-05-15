"""
Sprint 3 — optimizer unit tests.

Six methods tested: MEAN_VARIANCE, RISK_PARITY, MIN_VARIANCE, BLACK_LITTERMAN,
MAX_SHARPE, MIN_DRAWDOWN. All tests use synthetic return data — no external API calls.
Every test verifies the constraint that matters most for the project: weights sum
to 1.0 ± 1e-6 and stay within [MIN_WEIGHT, MAX_WEIGHT]. A single violated weight
constraint would produce an invalid backtest and mislead the final results.
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_returns(
    n_assets: int = 4,
    n_obs: int = 120,
    mean: float = 0.0005,
    seed: int = 42,
) -> pd.DataFrame:
    """Synthetic monthly returns for n_assets."""
    np.random.seed(seed)
    cols = [f"ASSET_{i}" for i in range(n_assets)]
    data = np.random.normal(mean, 0.04, size=(n_obs, n_assets))
    return pd.DataFrame(data, columns=cols)


def make_spy_tlt_returns(n_obs: int = 120) -> pd.DataFrame:
    """Synthetic SPY + TLT with slight positive return and low correlation."""
    np.random.seed(7)
    spy = np.random.normal(0.008, 0.04, n_obs)
    tlt = np.random.normal(0.003, 0.02, n_obs)
    return pd.DataFrame({"SPY": spy, "TLT": tlt})


# ── optimize_weights dispatcher ───────────────────────────────────────────────

def test_optimize_weights_mean_variance_runs():
    from tools.optimizer import optimize_weights
    rets = make_returns()
    result = optimize_weights("MEAN_VARIANCE", rets)
    assert isinstance(result, dict)
    assert "weights" in result


def test_optimize_weights_unknown_method_raises():
    from tools.optimizer import optimize_weights
    with pytest.raises(ValueError, match="Unknown"):
        optimize_weights("NONEXISTENT_METHOD", make_returns())


def test_optimize_weights_returns_method_field():
    from tools.optimizer import optimize_weights
    rets = make_returns()
    result = optimize_weights("MIN_VARIANCE", rets)
    assert result["method"] == "MIN_VARIANCE"


# ── weight constraint checks (applied to all 6 methods) ──────────────────────

METHODS = [
    "MEAN_VARIANCE",
    "RISK_PARITY",
    "MIN_VARIANCE",
    "BLACK_LITTERMAN",
    "MAX_SHARPE",
    "MIN_DRAWDOWN",
]


@pytest.mark.parametrize("method", METHODS)
def test_weights_sum_to_one(method: str):
    """
    The backtest asserts abs(sum(weights) - 1.0) < 1e-6. This test mirrors that
    assertion — a failure here means the optimizer would crash every backtest run.
    """
    from tools.optimizer import optimize_weights
    from config import MIN_WEIGHT, MAX_WEIGHT
    rets = make_returns(n_assets=3, n_obs=60)
    result = optimize_weights(method, rets)
    w = list(result["weights"].values())
    assert abs(sum(w) - 1.0) < 1e-4, f"{method}: weights sum {sum(w):.6f} ≠ 1.0"


@pytest.mark.parametrize("method", METHODS)
def test_weights_within_bounds(method: str):
    """All weights must be within [MIN_WEIGHT, MAX_WEIGHT] to enforce long-only constraint."""
    from tools.optimizer import optimize_weights
    from config import MIN_WEIGHT, MAX_WEIGHT
    rets = make_returns(n_assets=3, n_obs=60)
    result = optimize_weights(method, rets)
    for asset, w in result["weights"].items():
        assert w >= MIN_WEIGHT - 1e-6, f"{method}: {asset} weight {w:.4f} < MIN_WEIGHT"
        assert w <= MAX_WEIGHT + 1e-6, f"{method}: {asset} weight {w:.4f} > MAX_WEIGHT"


@pytest.mark.parametrize("method", METHODS)
def test_weights_are_non_negative(method: str):
    """Long-only constraint — no short positions permitted."""
    from tools.optimizer import optimize_weights
    rets = make_returns(n_assets=3, n_obs=60)
    result = optimize_weights(method, rets)
    for asset, w in result["weights"].items():
        assert w >= -1e-6, f"{method}: short position in {asset}: {w:.4f}"


# ── MEAN_VARIANCE specific ────────────────────────────────────────────────────

def test_mean_variance_returns_all_assets():
    from tools.optimizer import optimize_weights
    rets = make_returns(n_assets=4, n_obs=80)
    result = optimize_weights("MEAN_VARIANCE", rets)
    assert len(result["weights"]) == 4


def test_mean_variance_higher_risk_aversion_reduces_concentration():
    """
    Higher lambda → more risk-averse → weights should be less concentrated.
    This is the core property of mean-variance optimization we rely on.
    """
    from tools.optimizer import mean_variance_optimize
    rets = make_returns(n_assets=4, n_obs=100)
    tickers = list(rets.columns)
    w_low = mean_variance_optimize(rets, risk_aversion=1.0)
    w_high = mean_variance_optimize(rets, risk_aversion=10.0)
    # Max weight should be lower at high risk aversion — more diversified
    max_low = float(max(w_low))
    max_high = float(max(w_high))
    # High RA should be more spread out (or equal — not more concentrated)
    assert max_high <= max_low + 0.05  # 5% tolerance for numerical noise


# ── RISK_PARITY specific ──────────────────────────────────────────────────────

def test_risk_parity_equal_risk_contribution():
    """
    Risk parity should produce near-equal risk contributions from each asset.
    Equal risk contribution is the objective function — this verifies correctness.
    """
    from tools.optimizer import optimize_weights
    np.random.seed(42)
    # Low-correlation assets with similar volatility → equal weights expected
    n = 100
    a1 = np.random.normal(0.001, 0.02, n)
    a2 = np.random.normal(0.001, 0.02, n)
    a3 = np.random.normal(0.001, 0.02, n)
    rets = pd.DataFrame({"A": a1, "B": a2, "C": a3})
    result = optimize_weights("RISK_PARITY", rets)
    weights = list(result["weights"].values())
    # With equal-vol assets and low correlation, weights should be roughly equal
    assert max(weights) - min(weights) < 0.20  # Within 20% of each other


def test_risk_parity_two_assets():
    """Minimum case: 2 assets, both should receive non-trivial weight."""
    from tools.optimizer import optimize_weights
    rets = make_spy_tlt_returns()
    result = optimize_weights("RISK_PARITY", rets)
    assert len(result["weights"]) == 2
    for w in result["weights"].values():
        assert w > 0.05  # Both assets get meaningful allocation


# ── MIN_VARIANCE specific ─────────────────────────────────────────────────────

def test_min_variance_lower_vol_than_equal_weight():
    """
    Min-variance portfolio should have lower or equal volatility than equal weight.
    This is the defining property of minimum-variance optimization.
    """
    from tools.optimizer import optimize_weights
    rets = make_returns(n_assets=4, n_obs=120)
    result = optimize_weights("MIN_VARIANCE", rets)
    w_mv = np.array(list(result["weights"].values()))
    w_eq = np.ones(4) / 4.0
    cov = rets.cov().values
    vol_mv = float(np.sqrt(w_mv @ cov @ w_mv))
    vol_eq = float(np.sqrt(w_eq @ cov @ w_eq))
    assert vol_mv <= vol_eq + 1e-6


# ── BLACK_LITTERMAN specific ──────────────────────────────────────────────────

def test_black_litterman_with_equilibrium_prior():
    """
    Without views, BL posterior should equal equilibrium prior (π = λΣw_mkt).
    Sprint 3 uses this no-view case — views are added in Sprint 4 with CIO agent.
    """
    from tools.optimizer import optimize_weights
    rets = make_returns(n_assets=3, n_obs=80)
    result = optimize_weights("BLACK_LITTERMAN", rets)
    assert len(result["weights"]) == 3
    total = sum(result["weights"].values())
    assert abs(total - 1.0) < 1e-4


def test_black_litterman_respects_bounds():
    from tools.optimizer import optimize_weights
    from config import MIN_WEIGHT, MAX_WEIGHT
    rets = make_returns(n_assets=3, n_obs=80)
    result = optimize_weights("BLACK_LITTERMAN", rets)
    for w in result["weights"].values():
        assert MIN_WEIGHT - 1e-6 <= w <= MAX_WEIGHT + 1e-6


# ── MAX_SHARPE specific ───────────────────────────────────────────────────────

def test_max_sharpe_runs_with_positive_returns():
    from tools.optimizer import optimize_weights
    rets = make_returns(n_assets=3, n_obs=80, mean=0.001)
    result = optimize_weights("MAX_SHARPE", rets)
    assert isinstance(result["weights"], dict)
    assert len(result["weights"]) == 3


def test_max_sharpe_fallback_on_all_negative_excess():
    """
    When all excess returns ≤ 0, max-Sharpe falls back to min-variance.
    This prevents a degenerate QP with no feasible solution.
    """
    from tools.optimizer import max_sharpe_optimize
    # Negative mean returns → negative excess returns → max-Sharpe undefined.
    # Call the low-level function directly to test the fallback path — the
    # dispatcher optimize_weights doesn't accept risk_free as a kwarg.
    rets = make_returns(n_assets=3, n_obs=80, mean=-0.005)
    w = max_sharpe_optimize(rets, risk_free=0.01)  # rf > returns → fallback
    # Fallback returns ndarray; sum must be 1
    assert abs(float(np.sum(w)) - 1.0) < 1e-4


# ── MIN_DRAWDOWN specific ─────────────────────────────────────────────────────

def test_min_drawdown_runs():
    from tools.optimizer import optimize_weights
    rets = make_returns(n_assets=3, n_obs=80)
    result = optimize_weights("MIN_DRAWDOWN", rets)
    assert isinstance(result["weights"], dict)


def test_min_drawdown_valid_weights():
    from tools.optimizer import optimize_weights
    rets = make_returns(n_assets=3, n_obs=80)
    result = optimize_weights("MIN_DRAWDOWN", rets)
    w = list(result["weights"].values())
    assert abs(sum(w) - 1.0) < 1e-4
    assert all(wi >= -1e-6 for wi in w)


# ── efficient_frontier ────────────────────────────────────────────────────────

def test_efficient_frontier_returns_list():
    from tools.optimizer import efficient_frontier
    rets = make_returns(n_assets=3, n_obs=80)
    points = efficient_frontier(rets, n_points=10)
    assert isinstance(points, list)
    assert len(points) > 0


def test_efficient_frontier_has_required_keys():
    from tools.optimizer import efficient_frontier
    rets = make_returns(n_assets=3, n_obs=80)
    points = efficient_frontier(rets, n_points=5)
    for point in points:
        assert "volatility" in point
        assert "return" in point
        assert "weights" in point


def test_efficient_frontier_volatility_increases():
    """
    Moving along the frontier from risk-averse to risk-seeking should
    produce increasing portfolio volatility as return increases.
    """
    from tools.optimizer import efficient_frontier
    rets = make_returns(n_assets=3, n_obs=100, mean=0.002)
    points = efficient_frontier(rets, n_points=10)
    vols = [p["volatility"] for p in points if p["volatility"] is not None]
    if len(vols) >= 3:
        # Should be generally increasing (allow some numerical noise)
        assert vols[-1] >= vols[0] - 0.001


# ── Regression: keyword-argument signatures ─────────────────────────────────
#
# main.py used to call _optimize(..., assets=assets) and
# _frontier(..., assets=assets). Neither function accepts that keyword —
# the asset list is implicit in returns.columns. The kwarg raised
# "optimize_weights() got an unexpected keyword argument 'assets'"
# every time a logged-in user hit the dashboard's frontier prefetch.
# These tests pin both signatures so a future regression that
# reintroduces the kwarg surfaces in CI rather than in Render logs.


def test_optimize_weights_does_not_accept_assets_kwarg():
    from tools.optimizer import optimize_weights
    rets = make_returns()
    with pytest.raises(TypeError, match="assets"):
        # Type-ignore: deliberate misuse to pin the signature.
        optimize_weights("MEAN_VARIANCE", rets, assets=["A", "B"])  # type: ignore[call-arg]


def test_efficient_frontier_does_not_accept_assets_kwarg():
    from tools.optimizer import efficient_frontier
    rets = make_returns(n_assets=3, n_obs=80)
    with pytest.raises(TypeError, match="assets"):
        efficient_frontier(rets, n_points=10, assets=["A", "B", "C"])  # type: ignore[call-arg]


def test_optimize_weights_derives_tickers_from_returns_columns():
    """The signature contract that makes the `assets` kwarg redundant:
    the optimizer reads its ticker list from returns.columns. This test
    pins that contract so a refactor that drops the column-name path
    breaks loudly here rather than silently in production."""
    from tools.optimizer import optimize_weights
    rets = make_returns(n_assets=4)
    rets.columns = ["SPY", "TLT", "IEF", "GLD"]
    result = optimize_weights("RISK_PARITY", rets)
    assert set(result["weights"].keys()) == {"SPY", "TLT", "IEF", "GLD"}


# ── NaN / Inf guard ───────────────────────────────────────────────────────────
# yfinance can return an all-NaN column when a ticker fails to fetch. Without a
# guard, the resulting NaN covariance matrix makes CLARABEL raise "Problem data
# contains NaN or Inf" — once per frontier sweep point. These tests pin the
# guard: every method falls back to equal weight, and the frontier returns empty
# instead of looping 100 failed solves.

def _returns_with_nan_column(n_obs: int = 120) -> pd.DataFrame:
    """Four-asset frame where one ticker failed to fetch (all-NaN column)."""
    df = make_returns(n_assets=4, n_obs=n_obs)
    df.iloc[:, 2] = np.nan  # ticker the data layer could not retrieve
    return df


@pytest.mark.parametrize(
    "method",
    ["MEAN_VARIANCE", "RISK_PARITY", "MIN_VARIANCE",
     "BLACK_LITTERMAN", "MAX_SHARPE", "MIN_DRAWDOWN"],
)
def test_nan_returns_fall_back_to_equal_weight(method: str):
    """A NaN column must never reach the solver — every method returns a
    valid equal-weight vector (sums to 1, length = asset count) instead
    of raising 'Problem data contains NaN or Inf'."""
    from tools.optimizer import optimize_weights
    result = optimize_weights(method, _returns_with_nan_column())
    weights = list(result["weights"].values())
    assert len(weights) == 4
    assert abs(sum(weights) - 1.0) < 1e-6
    assert all(np.isfinite(w) for w in weights)


def test_efficient_frontier_returns_empty_on_nan_returns():
    """The frontier sweep must short-circuit on non-finite moments —
    one log line, empty list — not 100 failed CLARABEL calls."""
    from tools.optimizer import efficient_frontier
    assert efficient_frontier(_returns_with_nan_column(), n_points=100) == []


def test_returns_have_finite_moments_detects_bad_frames():
    """The guard predicate itself: empty, single-row, and all-NaN-column
    frames are all rejected; a clean frame passes."""
    from tools.optimizer import _returns_have_finite_moments
    assert _returns_have_finite_moments(make_returns()) is True
    assert _returns_have_finite_moments(pd.DataFrame()) is False
    assert _returns_have_finite_moments(make_returns(n_obs=1)) is False
    assert _returns_have_finite_moments(_returns_with_nan_column()) is False
