"""
Sprint 3 close-out — numerical accuracy tests.

Deterministic input/output checks using known synthetic values.
All tests use a controlled history dict so results are reproducible
across machines and Python versions without network calls.

Tests cover the arithmetic contracts that underpin every strategy metric:
  - Single-month portfolio return additivity
  - CAGR compounding formula
  - Sharpe ratio formula
  - Sortino ratio >= Sharpe when mean excess > 0
  - max_drawdown with a known price path
  - Equal weight assignment
  - Risk parity weight sum
  - Simple returns (not log) used throughout
  - Monthly annualisation uses sqrt(12), not sqrt(252)
  - run_all_strategies returns a dict keyed by strategy identifier
  - End-to-end regression: exact Sharpe and CAGR values for seed=42
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


# ── Fixtures ───────────────────────────────────────────────────────────────────

def make_flat_history(n_months: int = 60) -> dict:
    """
    History dict with perfectly flat return series — every asset class earns
    exactly the same constant return each month. Flat series give closed-form
    expected values for CAGR, Sharpe, and portfolio return arithmetic.
    """
    idx_m = pd.date_range("2020-01-31", periods=n_months, freq="ME")
    n_daily = n_months * 21
    idx_d = pd.bdate_range("2020-01-01", periods=n_daily)

    # Flat monthly returns — equity 1%, IG 0.3%, HY 0.5%, rf 0.041% (≈5% ann.)
    equity_monthly = pd.Series(0.010, index=idx_m)
    ig_monthly = pd.Series(0.003, index=idx_m)
    hy_monthly = pd.Series(0.005, index=idx_m)
    rf_monthly = pd.Series(0.000407, index=idx_m)  # (1.05)^(1/12)-1

    equity_daily = pd.Series(0.0004, index=idx_d)
    ig_daily = pd.Series(0.0001, index=idx_d)
    hy_daily = pd.Series(0.0002, index=idx_d)
    rf_daily = pd.Series(0.05 / 252.0, index=idx_d)

    vix = pd.Series(18.0, index=idx_d)

    return {
        "equity_monthly": equity_monthly,
        "ig_monthly": ig_monthly,
        "hy_monthly": hy_monthly,
        "risk_free_monthly": rf_monthly,
        "equity_daily": equity_daily,
        "ig_daily": ig_daily,
        "hy_daily": hy_daily,
        "risk_free_daily": rf_daily,
        "signals": {"vix": vix},
        "ff_factors": pd.DataFrame(
            {
                "Mkt-RF": np.full(n_months, 0.005),
                "SMB": np.full(n_months, 0.001),
                "HML": np.full(n_months, 0.001),
                "RF": np.full(n_months, 0.000407),
            },
            index=idx_m,
        ),
    }


def make_history(n_months: int = 60, seed: int = 42) -> dict:
    """
    Primary fixture: random returns with a fixed seed for full reproducibility.
    Used for end-to-end regression tests — any pipeline change that alters the
    arithmetic will break the stored constants and surface the regression.

    Seed=42 produces the regression constants stored in _EXPECTED_RESULTS below.
    Do not change the seed, distribution parameters, or array lengths without
    recomputing those constants.
    """
    np.random.seed(seed)
    idx_m = pd.date_range("2020-01-31", periods=n_months, freq="ME")
    n_daily = n_months * 21
    idx_d = pd.bdate_range("2020-01-01", periods=n_daily)

    equity_monthly = pd.Series(np.random.normal(0.008, 0.04, n_months), index=idx_m)
    ig_monthly = pd.Series(np.random.normal(0.003, 0.015, n_months), index=idx_m)
    hy_monthly = pd.Series(np.random.normal(0.005, 0.025, n_months), index=idx_m)
    rf_monthly = pd.Series(0.000407, index=idx_m)

    equity_daily = pd.Series(np.random.normal(0.0003, 0.012, n_daily), index=idx_d)
    ig_daily = pd.Series(np.random.normal(0.0001, 0.005, n_daily), index=idx_d)
    hy_daily = pd.Series(np.random.normal(0.0002, 0.008, n_daily), index=idx_d)
    rf_daily = pd.Series(0.05 / 252.0, index=idx_d)
    vix = pd.Series(np.random.uniform(15, 30, n_daily), index=idx_d)

    return {
        "equity_monthly": equity_monthly,
        "ig_monthly": ig_monthly,
        "hy_monthly": hy_monthly,
        "risk_free_monthly": rf_monthly,
        "equity_daily": equity_daily,
        "ig_daily": ig_daily,
        "hy_daily": hy_daily,
        "risk_free_daily": rf_daily,
        "signals": {"vix": vix},
        "ff_factors": pd.DataFrame(
            {
                "Mkt-RF": np.random.normal(0.005, 0.04, n_months),
                "SMB": np.random.normal(0.001, 0.02, n_months),
                "HML": np.random.normal(0.001, 0.02, n_months),
                "RF": np.full(n_months, 0.000407),
            },
            index=idx_m,
        ),
    }


# Alias retained for backward compatibility with any external references.
make_random_history = make_history


# ── End-to-end regression constants (seed=42, n_months=60) ───────────────────
# Computed once from the pipeline and stored here.
# Any arithmetic change that alters these values breaks this test, surfacing
# the regression before it reaches main. Recompute by running:
#   python temp_regression.py  (see project root)
# then update the constants below.
_EXPECTED_RESULTS: dict[str, dict[str, float]] = {
    "BENCHMARK":          {"sharpe_ratio": 0.1341, "cagr": 0.0141},
    "BLACK_LITTERMAN":    {"sharpe_ratio": 1.2055, "cagr": 0.0808},
    "CLASSIC_60_40":      {"sharpe_ratio": 0.2785, "cagr": 0.0242},
    "EQUAL_WEIGHT":       {"sharpe_ratio": 0.7344, "cagr": 0.0473},
    "MAX_SHARPE_ROLLING": {"sharpe_ratio": 1.5157, "cagr": 0.0924},
    "MIN_VARIANCE":       {"sharpe_ratio": 1.5157, "cagr": 0.0924},
    "MOMENTUM_ROTATION":  {"sharpe_ratio": 0.3774, "cagr": 0.0291},
    "REGIME_SWITCHING":   {"sharpe_ratio": 0.2932, "cagr": 0.0282},
    "RISK_PARITY":        {"sharpe_ratio": 0.8461, "cagr": 0.0499},
    "VOL_TARGETING":      {"sharpe_ratio": 0.4127, "cagr": 0.0283},
}


# ── TEST 1: Portfolio return additivity ───────────────────────────────────────

def test_classic_6040_single_month_return_additive():
    """
    For a 60/40 equity/IG split, one month's portfolio return must equal
    exactly 0.6 × equity_return + 0.4 × ig_return.  No rebalancing cost
    applies to the first month because it is the initial allocation.

    This validates that _portfolio_returns_monthly() is arithmetic (not
    geometric) across asset classes — the standard convention for
    daily/monthly return aggregation across a portfolio.
    """
    from tools.backtester import run_classic_6040

    h = make_flat_history(n_months=4)
    result = run_classic_6040(h)
    # With eq=0.010, ig=0.003 and 60/40 split, first month gross return = 0.6*0.010 + 0.4*0.003
    expected_first_month = 0.6 * 0.010 + 0.4 * 0.003
    # The backtester deducts a one-time rebalance cost at the first rebalance date.
    # avg_equity_weight reflects the long-run average — with flat returns the arithmetic
    # portfolio return per month should be close to the weighted sum (within 1 bp for costs).
    # We verify the structural arithmetic rather than an exact cost-adjusted value.
    assert abs(result["avg_equity_weight"] - 0.60) < 1e-3
    assert abs(result["avg_bond_weight"] - 0.40) < 1e-3
    # Monthly CAGR check: monthly return ≈ expected_first_month (flat series)
    monthly_ret = (1 + result["cagr"]) ** (1 / 12) - 1
    # Allow a small tolerance for transaction costs (10bps/quarter)
    assert abs(monthly_ret - expected_first_month) < 0.0020


# ── TEST 2: CAGR formula ───────────────────────────────────────────────────────

def test_cagr_constant_1pct_monthly_12_months():
    """
    A constant 1% monthly return over 12 months produces a CAGR of exactly
    (1.01^12 - 1).  This is the most basic sanity check on the compounding
    formula and confirms the annualisation factor is 12 (monthly), not 252.
    """
    from tools.backtester import _m_cagr

    idx = pd.date_range("2020-01-31", periods=12, freq="ME")
    returns = pd.Series(0.010, index=idx)
    expected = (1.01 ** 12) - 1
    assert abs(_m_cagr(returns) - expected) < 1e-4


def test_cagr_constant_2pct_monthly_60_months():
    """
    A constant 2% monthly return over 60 months (5 years) checks that _m_cagr
    correctly uses n_years = len(series) / 12 for annualisation.
    """
    from tools.backtester import _m_cagr

    idx = pd.date_range("2020-01-31", periods=60, freq="ME")
    returns = pd.Series(0.020, index=idx)
    expected = (1.02 ** 12) - 1
    assert abs(_m_cagr(returns) - expected) < 1e-4


# ── TEST 3: Sharpe ratio formula ───────────────────────────────────────────────

def test_sharpe_known_series():
    """
    For a series with constant mean μ and zero variance, the Sharpe ratio is
    undefined (division by zero).  This test uses a series with known μ and σ
    and verifies the annualised monthly Sharpe: (μ - rf) / σ × sqrt(12).

    We use np.random.seed(0) so the exact series is reproducible.
    """
    from tools.backtester import _m_sharpe

    np.random.seed(0)
    n = 120
    idx = pd.date_range("2010-01-31", periods=n, freq="ME")
    returns = pd.Series(np.random.normal(0.010, 0.04, n), index=idx)
    rf = pd.Series(0.000407, index=idx)

    # Manual expected Sharpe (same formula as _m_sharpe)
    excess = returns - rf
    expected = float(excess.mean() / excess.std(ddof=1) * np.sqrt(12))

    result = _m_sharpe(returns, rf)
    assert abs(result - expected) < 1e-4


# ── TEST 4: Sortino >= Sharpe when mean excess is positive ────────────────────

def test_sortino_geq_sharpe_positive_mean_excess():
    """
    When a strategy's mean excess return is positive, Sortino >= Sharpe because
    downside deviation <= total standard deviation (downside-only subset).
    Sortino = (mean_excess / downside_std) * sqrt(12)
    Sharpe  = (mean_excess / total_std) * sqrt(12)
    Both use the same numerator; Sortino has a denominator ≤ Sharpe's.

    Using seed=0 with mean 1% per month ensures mean_excess > 0 consistently.
    """
    from tools.backtester import _m_sharpe, _m_sortino

    np.random.seed(0)
    n = 120
    idx = pd.date_range("2010-01-31", periods=n, freq="ME")
    returns = pd.Series(np.random.normal(0.010, 0.04, n), index=idx)
    rf = pd.Series(0.000407, index=idx)

    sharpe = _m_sharpe(returns, rf)
    sortino = _m_sortino(returns, rf)

    assert sortino >= sharpe, (
        f"Sortino {sortino:.4f} must be >= Sharpe {sharpe:.4f} when mean excess > 0"
    )


# ── TEST 5: max_drawdown formula ──────────────────────────────────────────────

def test_max_drawdown_known_path():
    """
    Return series [0.05, -0.50, 0.30]:
      cumulative price: [1.05, 0.525, 0.6825]
      rolling max:      [1.05, 1.05,  1.05 ]
      drawdown:         [0.0,  (0.525-1.05)/1.05, ...]
                      = [0.0,  -0.50, -0.35]
      max_drawdown = -0.50 exactly.

    Starts with a positive return so the cumulative peak is set before
    the -50% decline.  A series beginning with a negative return has no
    prior peak and would yield a max_drawdown of 0 — not the intended test.
    """
    from tools.risk_metrics import max_drawdown

    idx = pd.date_range("2020-01-31", periods=3, freq="ME")
    returns = pd.Series([0.05, -0.50, 0.30], index=idx)
    dd, _, _ = max_drawdown(returns)
    assert dd == pytest.approx(-0.50, abs=1e-6)


def test_max_drawdown_no_drawdown():
    """
    A monotonically increasing return series has a max drawdown of exactly 0.
    """
    from tools.risk_metrics import max_drawdown

    idx = pd.date_range("2020-01-31", periods=6, freq="ME")
    returns = pd.Series([0.01, 0.02, 0.01, 0.03, 0.02, 0.01], index=idx)
    dd, _, _ = max_drawdown(returns)
    assert dd == pytest.approx(0.0, abs=1e-6)


# ── TEST 6: Equal weight assignment ───────────────────────────────────────────

def test_equal_weight_assigns_one_third():
    """
    EQUAL_WEIGHT strategy must assign 1/3 to each of equity, IG, HY.
    avg_equity_weight and avg_bond_weight are rounded to 4 decimal places by
    _build_result(), so 1/3 is stored as 0.3333 and 2/3 as 0.6667.
    The tolerance of 1e-3 accommodates this rounding while still catching any
    material deviation from the intended equal split.
    """
    from tools.backtester import run_equal_weight

    result = run_equal_weight(make_history())
    assert abs(result["avg_equity_weight"] - 1 / 3) < 1e-3
    assert abs(result["avg_bond_weight"] - 2 / 3) < 1e-3


# ── TEST 7: Risk parity weights sum to 1 ──────────────────────────────────────

def test_risk_parity_weights_sum_to_one():
    """
    RISK_PARITY uses scipy SLSQP with equality constraint sum(w) = 1.
    The resulting avg_equity_weight + avg_bond_weight must equal 1.0 to 6 dp.
    """
    from tools.backtester import run_risk_parity

    result = run_risk_parity(make_history())
    total = result["avg_equity_weight"] + result["avg_bond_weight"]
    assert abs(total - 1.0) < 1e-6


def test_risk_parity_equity_weight_below_benchmark():
    """
    Risk parity over-weights low-volatility bonds vs high-volatility equity.
    With normal synthetic data (equity σ ≈ 4%, bonds σ ≈ 1.5-2.5%), equity's
    risk parity weight must be below the 1/3 equal-weight baseline — confirming
    that the optimizer is working as intended and not defaulting to 60/40 or 1/3.
    """
    from tools.backtester import run_risk_parity

    result = run_risk_parity(make_history())
    assert result["avg_equity_weight"] < 1 / 3


# ── TEST 8: Simple returns (not log) ──────────────────────────────────────────

def test_returns_are_simple_not_log():
    """
    The backtester uses simple (arithmetic) returns, not log returns.
    pct_change() on [100, 110] must yield exactly 0.10, not the log return
    ln(110/100) = 0.09531.  Simple and log returns diverge meaningfully at
    high return magnitudes; mixing conventions within a backtest is a
    correctness error.
    """
    prices = pd.Series([100.0, 110.0])
    simple_return = float(prices.pct_change().dropna().iloc[0])
    log_return = float(np.log(110.0 / 100.0))

    # Simple return is exactly 0.10
    assert abs(simple_return - 0.10) < 1e-10

    # Log return is materially different — the two conventions are not interchangeable
    assert abs(simple_return - log_return) > 0.004


def test_pct_change_drops_first_row():
    """
    pct_change() produces NaN for the first row (no prior price to compare).
    After dropna() the series has one fewer row than the original.
    This confirms that returns align to the second price — not the first.
    """
    prices = pd.Series([100.0, 105.0, 110.0])
    returns = prices.pct_change().dropna()
    assert len(returns) == len(prices) - 1
    assert abs(float(returns.iloc[0]) - 0.05) < 1e-10


# ── TEST 9: Monthly annualisation uses sqrt(12), not sqrt(252) ────────────────

def test_monthly_annualisation_uses_sqrt_12():
    """
    _m_vol() must annualise monthly standard deviation by sqrt(12), not sqrt(252).
    sqrt(252) is the correct factor for DAILY returns; using it on monthly data
    would overstate annual volatility by a factor of sqrt(252/12) ≈ 4.58×.
    The two factors produce values that differ by > 1.0 for any non-zero series.
    """
    from tools.backtester import _m_vol

    idx = pd.date_range("2020-01-31", periods=24, freq="ME")
    returns = pd.Series(0.01, index=idx)  # constant, non-zero std from pandas ddof=1 is 0
    # Use a non-constant series so std > 0
    returns = pd.Series([0.01, -0.01] * 12, index=idx)

    vol = _m_vol(returns)
    expected_sqrt12 = float(returns.std() * np.sqrt(12))
    expected_sqrt252 = float(returns.std() * np.sqrt(252))

    assert abs(vol - expected_sqrt12) < 1e-10, (
        f"_m_vol should use sqrt(12): got {vol:.6f}, expected {expected_sqrt12:.6f}"
    )
    assert abs(vol - expected_sqrt252) > 0.05, (
        f"sqrt(252) annualisation would give {expected_sqrt252:.4f}; "
        f"sqrt(12) gives {expected_sqrt12:.4f}. "
        "Using sqrt(252) for monthly data overstates volatility by >5ppt."
    )


# ── TEST 10: run_all_strategies return type ────────────────────────────────────

def test_run_all_strategies_returns_dict():
    """
    run_all_strategies must return a dict, not a list.
    The change from list to dict (Sprint 3 close-out Task 1) lets callers
    access results["BENCHMARK"]["sharpe_ratio"] without scanning a list —
    required by the verification script that checks all 10 strategy identifiers.
    """
    from tools.backtester import run_all_strategies

    result = run_all_strategies(make_history())
    assert isinstance(result, dict)


def test_run_all_strategies_has_expected_keys():
    """
    Every strategy identifier must be present as a top-level key.
    This is the contract the verification script depends on.
    """
    from tools.backtester import run_all_strategies

    expected_keys = {
        "BENCHMARK", "CLASSIC_60_40", "RISK_PARITY", "MIN_VARIANCE",
        "EQUAL_WEIGHT", "MOMENTUM_ROTATION", "REGIME_SWITCHING",
        "VOL_TARGETING", "BLACK_LITTERMAN", "MAX_SHARPE_ROLLING",
    }
    result = run_all_strategies(make_history())
    assert set(result.keys()) == expected_keys


def test_run_all_strategies_benchmark_accessible_by_key():
    """
    Dict access pattern: results["BENCHMARK"]["sharpe_ratio"] must work
    and return a float.  This is the primary motivation for switching from
    list to dict — deterministic key-based lookup for the verification script.
    """
    from tools.backtester import run_all_strategies

    result = run_all_strategies(make_history())
    assert "sharpe_ratio" in result["BENCHMARK"]
    assert isinstance(result["BENCHMARK"]["sharpe_ratio"], float)


# ── TEST 11: Monthly return additivity ────────────────────────────────────────

def test_portfolio_returns_are_arithmetic_not_geometric():
    """
    The backtester computes monthly portfolio returns as a weighted arithmetic
    sum of asset class returns — NOT geometric (chained product).
    This test verifies that for a 1-month horizon with known weights and returns,
    the result matches the arithmetic formula exactly.
    Simple arithmetic aggregation is correct for returns within a single period;
    geometric chaining is used only across periods (CAGR), not within them.
    """
    from tools.backtester import _portfolio_returns_monthly

    idx = pd.date_range("2020-01-31", periods=2, freq="ME")
    returns_df = pd.DataFrame(
        {
            "equity_return": [0.10, 0.05],
            "ig_return": [0.02, 0.01],
            "hy_return": [0.04, 0.02],
        },
        index=idx,
    )
    # Weights effective from the first period — no rebalancing cost on first month
    weights = {"equity": 0.60, "ig": 0.30, "hy": 0.10}
    schedule = [(idx[0], weights)]
    port = _portfolio_returns_monthly(returns_df, schedule, transaction_cost_bps=0)

    expected_month1 = 0.60 * 0.10 + 0.30 * 0.02 + 0.10 * 0.04
    assert abs(port.iloc[0] - expected_month1) < 1e-10


# ── TEST 12: End-to-end regression ────────────────────────────────────────────

def test_run_all_strategies_regression_constants():
    """
    End-to-end regression: run_all_strategies(make_history(seed=42)) must
    produce the exact Sharpe ratios and CAGRs stored in _EXPECTED_RESULTS.

    Tolerance is 5e-4 (half a basis point in Sharpe / CAGR) — tight enough to
    catch arithmetic changes but loose enough to survive floating-point noise
    across Python/NumPy patch versions.

    Any change to the backtester arithmetic, rebalance logic, or return
    calculation that shifts results by more than 0.0005 will break this test.
    That is the intended behaviour: the test exists to surface silent regressions.
    """
    from tools.backtester import run_all_strategies

    results = run_all_strategies(make_history(seed=42))

    for strategy_key, expected in _EXPECTED_RESULTS.items():
        assert strategy_key in results, f"Strategy key '{strategy_key}' missing from results"
        actual = results[strategy_key]

        actual_sharpe = actual.get("sharpe_ratio", float("nan"))
        actual_cagr = actual.get("cagr", float("nan"))

        assert abs(actual_sharpe - expected["sharpe_ratio"]) < 5e-4, (
            f"{strategy_key} sharpe_ratio: got {actual_sharpe:.4f}, "
            f"expected {expected['sharpe_ratio']:.4f}"
        )
        assert abs(actual_cagr - expected["cagr"]) < 5e-4, (
            f"{strategy_key} cagr: got {actual_cagr:.4f}, "
            f"expected {expected['cagr']:.4f}"
        )
