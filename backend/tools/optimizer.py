"""
tools/optimizer.py

Six portfolio optimization methods used by Sprint 3 strategies.

Why six methods rather than one:
  Different optimization objectives are appropriate for different investor
  mandates. Mean-variance maximizes expected utility; risk parity spreads
  risk equally so no single asset dominates drawdown; min-variance targets
  the lowest-possible volatility regardless of expected return; Black-Litterman
  anchors to equilibrium before incorporating views; max-Sharpe maximizes
  risk-adjusted return; min-drawdown minimizes tail loss via CVaR proxy.
  No single method dominates across all regimes — that's one of the project's
  key empirical questions.

All methods enforce the same constraints from config:
  MIN_WEIGHT = 0.00, MAX_WEIGHT = 0.40, weights sum to 1.0 ± 1e-6.
  cvxpy is required for MEAN_VARIANCE, MIN_VARIANCE, MAX_SHARPE, MIN_DRAWDOWN.
  RISK_PARITY uses scipy (convex but non-linear; no standard LP formulation).
  BLACK_LITTERMAN is analytical (closed-form posterior, then mean-variance).
"""
from __future__ import annotations

import warnings
from typing import Optional

import numpy as np
import pandas as pd
from scipy import optimize as scipy_optimize

from config import (
    ANNUALIZATION_FACTOR,
    BL_TAU,
    MAX_WEIGHT,
    MIN_WEIGHT,
    RANDOM_SEED,
    RISK_AVERSION,
)
from logger import get_logger

log = get_logger(__name__)

try:
    import cvxpy as cp
    _CVXPY_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CVXPY_AVAILABLE = False
    warnings.warn(
        "cvxpy not installed — MEAN_VARIANCE/MIN_VARIANCE/MAX_SHARPE/MIN_DRAWDOWN "
        "will fall back to equal weight. Install cvxpy to enable these methods."
    )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _equal_weight(n: int) -> np.ndarray:
    return np.full(n, 1.0 / n)


def _clip_and_normalise(
    weights: np.ndarray,
    min_w: float = MIN_WEIGHT,
    max_w: float = MAX_WEIGHT,
) -> np.ndarray:
    """
    Clip to [min_w, max_w] and renormalise to sum to 1.
    This is applied as a post-processing step after every solver call because
    cvxpy's ECOS solver may return weights slightly outside the box constraints
    due to numerical precision (~1e-8). Renormalisation is safe here because
    all weights are non-negative after clipping (MIN_WEIGHT=0.00), so the sum
    after clipping is always positive.
    """
    weights = np.clip(weights, min_w, max_w)
    total = float(weights.sum())
    if total < 1e-10:
        return _equal_weight(len(weights))
    return weights / total


def _cov_to_corr(cov: np.ndarray) -> np.ndarray:
    """Converts covariance matrix to correlation matrix."""
    std = np.sqrt(np.diag(cov))
    return cov / np.outer(std, std)


def _returns_have_finite_moments(returns: pd.DataFrame) -> bool:
    """
    True only when the mean vector and covariance matrix derived from
    `returns` are entirely finite — the precondition every cvxpy/scipy
    solver below requires.

    A returns frame that is empty, has fewer than two rows, or carries an
    all-NaN column (a ticker the data layer failed to fetch, or one wiped
    out by an unaligned dropna) produces NaN/Inf moments. Handing those to
    CLARABEL raises "Problem data contains NaN or Inf" — and the
    efficient-frontier sweep then logs that exception 100 times, once per
    risk-aversion point. Guarding here lets each method fall back to equal
    weight with a single diagnostic line instead of a solver crash storm.
    """
    if returns.empty or returns.shape[0] < 2 or returns.shape[1] < 1:
        return False
    mu = returns.mean().to_numpy(dtype=float)
    cov = returns.cov().to_numpy(dtype=float)
    return bool(np.all(np.isfinite(mu)) and np.all(np.isfinite(cov)))


# ── Mean-variance optimisation ────────────────────────────────────────────────

def mean_variance_optimize(
    returns: pd.DataFrame,
    risk_aversion: float = RISK_AVERSION,
    min_weight: float = MIN_WEIGHT,
    max_weight: float = MAX_WEIGHT,
) -> np.ndarray:
    """
    Mean-variance utility maximisation via quadratic program.
    Maximises: μᵀw - (λ/2) wᵀΣw, subject to 1ᵀw=1, w∈[min_w, max_w].
    risk_aversion=3.0 (from config) is set at the project-typical level for
    long-only multi-asset portfolios. Lower λ (e.g. 1.0) skews heavily to
    high-return, high-volatility allocations; higher λ (>5) approaches
    min-variance. 3.0 is the Merton (1969) calibrated value for a typical
    institutional investor with moderate risk tolerance.
    cvxpy CLARABEL solver is used: ECOS is not available on all platforms
    (requires C++ build tools on Windows); CLARABEL ships as a pure Python
    wheel and handles box-constrained QPs reliably for small n (< 20 assets).
    """
    if not _CVXPY_AVAILABLE:
        log.warning("cvxpy_unavailable", fallback="equal_weight")
        return _equal_weight(returns.shape[1])

    if not _returns_have_finite_moments(returns):
        log.warning("optimizer_nonfinite_returns", method="mean_variance", fallback="equal_weight")
        return _equal_weight(returns.shape[1])

    mu = returns.mean().values
    cov = returns.cov().values
    n = len(mu)

    w = cp.Variable(n)
    utility = mu @ w - (risk_aversion / 2.0) * cp.quad_form(w, cp.psd_wrap(cov))
    constraints = [cp.sum(w) == 1.0, w >= min_weight, w <= max_weight]
    problem = cp.Problem(cp.Maximize(utility), constraints)

    try:
        problem.solve(solver=cp.CLARABEL, verbose=False)
        if problem.status in ("optimal", "optimal_inaccurate") and w.value is not None:
            return _clip_and_normalise(w.value, min_weight, max_weight)
    except Exception as exc:
        log.warning("mean_variance_solver_failed", error=str(exc), fallback="equal_weight")

    return _equal_weight(n)


# ── Risk-parity optimisation ──────────────────────────────────────────────────

def risk_parity_optimize(
    returns: pd.DataFrame,
    min_weight: float = MIN_WEIGHT,
    max_weight: float = MAX_WEIGHT,
) -> np.ndarray:
    """
    Equal risk contribution (risk parity) via scipy SLSQP.
    Each asset's risk contribution is defined as wᵢ * (Σw)ᵢ / (wᵀΣw).
    Equal risk contribution means every asset contributes 1/n of total portfolio
    variance. This cannot be expressed as a QP — it requires non-linear optimisation.
    SLSQP (sequential least squares programming) handles the nonlinear equality
    constraints and box constraints simultaneously.
    The objective (sum of squared differences from target contribution) is smooth
    and convex when the covariance matrix is PSD, so SLSQP converges reliably.
    scipy is preferred over cvxpy here because cvxpy does not support the
    non-linear w * (Σw) product directly without DCP-compliant reformulation.
    """
    if not _returns_have_finite_moments(returns):
        log.warning("optimizer_nonfinite_returns", method="risk_parity", fallback="equal_weight")
        return _equal_weight(returns.shape[1])

    cov = returns.cov().values
    n = cov.shape[0]
    target = np.full(n, 1.0 / n)

    np.random.seed(RANDOM_SEED)

    def _risk_contributions(w: np.ndarray) -> np.ndarray:
        sigma = float(np.sqrt(w @ cov @ w))
        if sigma < 1e-12:
            return np.full(n, 1.0 / n)
        return w * (cov @ w) / sigma ** 2

    def _objective(w: np.ndarray) -> float:
        rc = _risk_contributions(w)
        return float(np.sum((rc - target) ** 2))

    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    bounds = [(min_weight, max_weight)] * n
    w0 = _equal_weight(n)

    result = scipy_optimize.minimize(
        _objective,
        w0,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"ftol": 1e-12, "maxiter": 1000},
    )

    if result.success:
        return _clip_and_normalise(result.x, min_weight, max_weight)

    # Fallback: try multiple random starts and take the best
    best_val, best_w = float("inf"), w0
    for _ in range(5):
        w_init = np.random.dirichlet(np.ones(n))
        w_init = np.clip(w_init, min_weight, max_weight)
        w_init /= w_init.sum()
        r = scipy_optimize.minimize(
            _objective,
            w_init,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"ftol": 1e-12, "maxiter": 1000},
        )
        if r.fun < best_val:
            best_val, best_w = r.fun, r.x

    return _clip_and_normalise(best_w, min_weight, max_weight)


# ── Minimum variance ──────────────────────────────────────────────────────────

def min_variance_optimize(
    returns: pd.DataFrame,
    min_weight: float = MIN_WEIGHT,
    max_weight: float = MAX_WEIGHT,
) -> np.ndarray:
    """
    Minimum global variance portfolio via cvxpy quadratic program.
    Minimises wᵀΣw subject to 1ᵀw=1, w∈[min_w, max_w].
    No expected return input is needed — this is a pure variance minimisation.
    This matters when expected return estimates are unreliable: Σ can be estimated
    from 36 months of returns with much lower estimation error than μ, making
    min-variance more stable out-of-sample than mean-variance for short windows.
    The brief uses a 36-month window (OPTIMIZATION_WINDOW=36) where μ estimation
    error dominates; min-variance is therefore the more robust choice for the
    MIN_VARIANCE strategy.
    """
    if not _CVXPY_AVAILABLE:
        log.warning("cvxpy_unavailable", fallback="equal_weight")
        return _equal_weight(returns.shape[1])

    if not _returns_have_finite_moments(returns):
        log.warning("optimizer_nonfinite_returns", method="min_variance", fallback="equal_weight")
        return _equal_weight(returns.shape[1])

    cov = returns.cov().values
    n = cov.shape[0]

    w = cp.Variable(n)
    risk = cp.quad_form(w, cp.psd_wrap(cov))
    constraints = [cp.sum(w) == 1.0, w >= min_weight, w <= max_weight]
    problem = cp.Problem(cp.Minimize(risk), constraints)

    try:
        problem.solve(solver=cp.CLARABEL, verbose=False)
        if problem.status in ("optimal", "optimal_inaccurate") and w.value is not None:
            return _clip_and_normalise(w.value, min_weight, max_weight)
    except Exception as exc:
        log.warning("min_variance_solver_failed", error=str(exc), fallback="equal_weight")

    return _equal_weight(n)


# ── Black-Litterman ───────────────────────────────────────────────────────────

def black_litterman_optimize(
    returns: pd.DataFrame,
    market_weights: Optional[np.ndarray] = None,
    views_P: Optional[np.ndarray] = None,
    views_q: Optional[np.ndarray] = None,
    views_omega: Optional[np.ndarray] = None,
    tau: float = BL_TAU,
    risk_aversion: float = RISK_AVERSION,
    min_weight: float = MIN_WEIGHT,
    max_weight: float = MAX_WEIGHT,
) -> np.ndarray:
    """
    Black-Litterman posterior expected returns, then mean-variance optimisation.
    BL anchors to equilibrium (Π = λΣw_mkt) before incorporating views, preventing
    the corner solutions produced by unconstrained mean-variance on raw estimates.
    The equilibrium prior is the key contribution: without it, tiny differences in
    expected return estimates produce wildly different corner-solution portfolios.
    tau=0.05 (from config) is the standard BL calibration — it sets the weight
    given to the prior relative to the views. tau=0.05 means views receive 20x
    more weight than the prior when view confidence is equivalent to 5% of the
    return sample uncertainty.
    Sprint 3: market_weights default to equal weight (views=None → posterior=prior).
    Sprint 4 CIO agent will pass actual views from its qualitative assessment.
    """
    if not _returns_have_finite_moments(returns):
        log.warning("optimizer_nonfinite_returns", method="black_litterman", fallback="equal_weight")
        return _equal_weight(returns.shape[1])

    cov = returns.cov().values
    n = cov.shape[0]

    # Market cap prior — equal weight default for Sprint 3; Sprint 4 CIO provides real weights
    if market_weights is None:
        market_weights = _equal_weight(n)
    market_weights = np.array(market_weights)
    market_weights = market_weights / market_weights.sum()

    # Equilibrium returns (reverse-engineered from market cap weights)
    pi = risk_aversion * cov @ market_weights

    # Posterior: incorporate views if provided
    tau_sigma = tau * cov
    if views_P is not None and views_q is not None:
        P = np.array(views_P)
        q = np.array(views_q)
        omega = np.array(views_omega) if views_omega is not None else np.diag(np.diag(P @ tau_sigma @ P.T))

        # BL posterior formula (He & Litterman 1999)
        tau_sigma_inv = np.linalg.pinv(tau_sigma)
        pt_omega_inv = P.T @ np.linalg.pinv(omega) @ P
        posterior_cov = np.linalg.pinv(tau_sigma_inv + pt_omega_inv)
        mu_bl = posterior_cov @ (tau_sigma_inv @ pi + P.T @ np.linalg.pinv(omega) @ q)
    else:
        # No views → posterior collapses to equilibrium prior
        mu_bl = pi

    # Use BL posterior returns in mean-variance utility maximisation
    if not _CVXPY_AVAILABLE:
        log.warning("cvxpy_unavailable_bl", fallback="equal_weight")
        return _equal_weight(n)

    w = cp.Variable(n)
    utility = mu_bl @ w - (risk_aversion / 2.0) * cp.quad_form(w, cp.psd_wrap(cov))
    constraints = [cp.sum(w) == 1.0, w >= min_weight, w <= max_weight]
    problem = cp.Problem(cp.Maximize(utility), constraints)

    try:
        problem.solve(solver=cp.CLARABEL, verbose=False)
        if problem.status in ("optimal", "optimal_inaccurate") and w.value is not None:
            return _clip_and_normalise(w.value, min_weight, max_weight)
    except Exception as exc:
        log.warning("bl_solver_failed", error=str(exc), fallback="equal_weight")

    return _equal_weight(n)


# ── Maximum Sharpe ────────────────────────────────────────────────────────────

def max_sharpe_optimize(
    returns: pd.DataFrame,
    risk_free: float = 0.0,
    min_weight: float = MIN_WEIGHT,
    max_weight: float = MAX_WEIGHT,
    periods_per_year: int | None = None,
) -> np.ndarray:
    """
    Maximum Sharpe ratio portfolio via scipy SLSQP.
    Direct Sharpe maximisation (μᵀw / √(wᵀΣw)) is non-convex, but SLSQP
    handles it reliably for small n (< 20 assets) by directly minimising
    the negative Sharpe with box constraints. The Lasserre change-of-variables
    QP alternative does not enforce box constraints on the final weights —
    the renormalisation step after z* can violate MAX_WEIGHT. SLSQP avoids
    this problem and produces results consistent with the weight bounds that
    every other method enforces.

    risk_free is the ANNUALISED rate. It is divided by periods_per_year
    to convert to a per-period rate that matches the frequency of the
    returns DataFrame.

    periods_per_year (May 24 2026 fix): explicit annualisation factor —
    252 for daily returns, 12 for monthly. When None (default), the
    function INFERS the frequency from returns.index: a pandas DatetimeIndex
    with a monthly frequency uses 12; everything else falls back to
    ANNUALIZATION_FACTOR (252) for backward compatibility with daily callers.

    THE BUG THIS REPLACES: this function previously hardcoded `risk_free /
    ANNUALIZATION_FACTOR = risk_free / 252` regardless of the returns
    frequency. When called from run_max_sharpe_rolling (backtester.py),
    which passes MONTHLY returns, the rf was scaled to a daily rate
    (rf_annual / 252) but compared against MONTHLY mean returns. This
    overstated each asset's per-period excess return by ~21x the magnitude
    of rf. With rf ~ 0.04 annual, the bias was roughly 0.04/252 - 0.04/12
    ≈ -0.0032 per month — non-negligible against typical monthly mean
    returns of 0.003-0.008. The bias was unequal across assets (a constant
    -0.0032 boost to every asset's apparent excess) so the relative
    Sharpe ranking changed and the optimizer chose weights that did not
    actually maximise the true Sharpe.

    Fallback to min_variance when all excess returns ≤ 0 (problem infeasible).
    """
    if not _returns_have_finite_moments(returns):
        log.warning("optimizer_nonfinite_returns", method="max_sharpe", fallback="equal_weight")
        return _equal_weight(returns.shape[1])

    # Frequency inference. Monthly index → 12; otherwise default to daily.
    if periods_per_year is None:
        periods_per_year = ANNUALIZATION_FACTOR
        try:
            idx = returns.index
            if hasattr(idx, "inferred_freq"):
                freq = idx.inferred_freq or ""
                if any(t in freq.upper() for t in ("M", "Q", "Y", "A")):
                    periods_per_year = 12
            # Fallback heuristic: median spacing > 20 days = monthly.
            elif hasattr(idx, "to_series") and len(idx) > 1:
                spacing = (idx[1:] - idx[:-1]).total_seconds() / 86400
                if float(np.median(spacing)) > 20:
                    periods_per_year = 12
        except Exception:  # noqa: BLE001
            pass

    mu = returns.mean().values
    cov = returns.cov().values
    n = len(mu)
    rf_per_period = risk_free / periods_per_year
    excess = mu - rf_per_period

    if np.max(excess) <= 0:
        log.warning("max_sharpe_all_negative_excess", fallback="min_variance")
        return min_variance_optimize(returns, min_weight, max_weight)

    def _neg_sharpe(w: np.ndarray) -> float:
        ret = float(excess @ w)
        vol = float(np.sqrt(w @ cov @ w))
        return -(ret / vol) if vol > 1e-12 else 0.0

    constraints = [{"type": "eq", "fun": lambda w: float(np.sum(w)) - 1.0}]
    bounds = [(min_weight, max_weight)] * n
    np.random.seed(RANDOM_SEED)
    w0 = _equal_weight(n)

    try:
        result = scipy_optimize.minimize(
            _neg_sharpe, w0, method="SLSQP",
            bounds=bounds, constraints=constraints,
            options={"ftol": 1e-12, "maxiter": 1000},
        )
        if result.success or result.fun < 0:
            return _clip_and_normalise(result.x, min_weight, max_weight)
    except Exception as exc:
        log.warning("max_sharpe_solver_failed", error=str(exc), fallback="min_variance")

    return min_variance_optimize(returns, min_weight, max_weight)


# ── Minimum drawdown (CVaR proxy) ─────────────────────────────────────────────

def min_drawdown_optimize(
    returns: pd.DataFrame,
    cvar_alpha: float = 0.05,
    min_weight: float = MIN_WEIGHT,
    max_weight: float = MAX_WEIGHT,
) -> np.ndarray:
    """
    Drawdown-minimising portfolio via CVaR (Expected Shortfall) as a proxy.
    True max-drawdown minimisation is non-convex and requires simulation over
    full path histories — computationally expensive for a 36-month rolling window.
    CVaR at 5% (expected loss in the worst 5% of scenarios) is a convex proxy
    that correlates strongly with realised drawdown while remaining tractable.
    CVaR is a coherent risk measure (Artzner 1999); VaR is not. Using CVaR
    rather than VaR means: the objective function properly penalises tail
    scenarios that exceed the threshold, not just the threshold itself.
    The LP formulation follows Rockafellar & Uryasev (2000): introduce a
    scalar gamma (VaR level) and slack variables z_t >= max(-ret_t - gamma, 0).
    """
    if not _CVXPY_AVAILABLE:
        log.warning("cvxpy_unavailable", fallback="equal_weight")
        return _equal_weight(returns.shape[1])

    if not _returns_have_finite_moments(returns):
        log.warning("optimizer_nonfinite_returns", method="min_drawdown", fallback="equal_weight")
        return _equal_weight(returns.shape[1])

    R = returns.values  # shape: (T, n)
    T, n = R.shape

    w = cp.Variable(n)
    gamma = cp.Variable()  # VaR level
    z = cp.Variable(T)     # Excess losses above VaR

    portfolio_returns = R @ w
    constraints = [
        cp.sum(w) == 1.0,
        w >= min_weight,
        w <= max_weight,
        z >= 0,
        z >= -portfolio_returns - gamma,
    ]
    cvar = gamma + (1.0 / (cvar_alpha * T)) * cp.sum(z)
    problem = cp.Problem(cp.Minimize(cvar), constraints)

    try:
        problem.solve(solver=cp.CLARABEL, verbose=False)
        if problem.status in ("optimal", "optimal_inaccurate") and w.value is not None:
            return _clip_and_normalise(w.value, min_weight, max_weight)
    except Exception as exc:
        log.warning("min_drawdown_solver_failed", error=str(exc), fallback="min_variance")

    return min_variance_optimize(returns, min_weight, max_weight)


# ── Dispatcher ────────────────────────────────────────────────────────────────

def optimize_weights(
    method: str,
    returns: pd.DataFrame,
    risk_free: float = 0.0,
    market_weights: Optional[np.ndarray] = None,
    views_P: Optional[np.ndarray] = None,
    views_q: Optional[np.ndarray] = None,
    min_weight: float = MIN_WEIGHT,
    max_weight: float = MAX_WEIGHT,
) -> dict:
    """
    Dispatcher that routes to the appropriate optimisation method.
    Returns weights dict (ticker → weight) plus diagnostics.
    The unified interface allows the backtester to call any method without
    knowing its implementation details — swapping methods requires only
    changing the method string, not the calling code.
    """
    tickers = list(returns.columns)
    n = len(tickers)

    method_map = {
        "MEAN_VARIANCE":  lambda: mean_variance_optimize(returns, min_weight=min_weight, max_weight=max_weight),
        "RISK_PARITY":    lambda: risk_parity_optimize(returns, min_weight=min_weight, max_weight=max_weight),
        "MIN_VARIANCE":   lambda: min_variance_optimize(returns, min_weight=min_weight, max_weight=max_weight),
        "BLACK_LITTERMAN": lambda: black_litterman_optimize(
            returns, market_weights=market_weights,
            views_P=views_P, views_q=views_q,
            min_weight=min_weight, max_weight=max_weight
        ),
        "MAX_SHARPE":     lambda: max_sharpe_optimize(returns, risk_free=risk_free, min_weight=min_weight, max_weight=max_weight),
        "MIN_DRAWDOWN":   lambda: min_drawdown_optimize(returns, min_weight=min_weight, max_weight=max_weight),
    }

    if method not in method_map:
        raise ValueError(f"Unknown optimization method '{method}'. Valid: {sorted(method_map)}")

    weights_arr = method_map[method]()
    weights_dict = {t: float(w) for t, w in zip(tickers, weights_arr)}

    log.info("optimizer_run", method=method, n_assets=n, weights=weights_dict)
    return {
        "method": method,
        "weights": weights_dict,
        "sum_check": round(sum(weights_dict.values()), 8),
    }


# ── Efficient frontier ────────────────────────────────────────────────────────

def efficient_frontier(
    returns: pd.DataFrame,
    n_points: int = 100,
    min_weight: float = 0.0,
    max_weight: float = 1.0,
    periods_per_year: int = ANNUALIZATION_FACTOR,
    risk_free: float = 0.0,
) -> list[dict]:
    """
    Efficient frontier via a target-return sweep.

    For each of n_points target returns — swept linearly from the
    minimum-variance portfolio's return up to the highest single-asset
    return — minimise portfolio variance subject to:
      - fully invested:  sum(w) = 1
      - target return:   muᵀw = target
      - long-only:       min_weight <= w <= max_weight
    The locus of (volatility, return) pairs is the classic frontier
    hyperbola. Returns list of {volatility, return, sharpe, weights} dicts
    for the EfficientFrontier component.

    BOUNDS: the default is the full long-only space [0, 1] — NOT the 0.40
    MAX_WEIGHT cap the operational strategies use. With only three assets
    a 0.40 cap forces every weight into [0.20, 0.40], collapsing the
    feasible set to a sliver: the frontier then renders as a near-straight
    segment and cannot reach the max-return (single-asset) corner. The
    theoretical frontier must span the whole long-only space.

    periods_per_year sets the annualisation: 252 for daily returns, 12 for
    monthly. risk_free is the annual rate used for the Sharpe of each
    point, so the curve's tangency (max-Sharpe) point is consistent with
    the strategy scatter, which is annualised from the same series.
    """
    if not _returns_have_finite_moments(returns):
        log.warning("efficient_frontier_nonfinite_returns", fallback="empty")
        return []

    mu = returns.mean().to_numpy(dtype=float) * periods_per_year
    cov = returns.cov().to_numpy(dtype=float) * periods_per_year
    n = len(mu)

    # Covariance conditioning. A near-singular covariance matrix makes the
    # variance objective ill-posed — the optimiser can chase a flat
    # direction and return junk. Log the condition number; regularise with
    # a tiny diagonal term (a minimal shrinkage) when it exceeds 1e10.
    cond = float(np.linalg.cond(cov))
    if cond > 1e10:
        cov = cov + 1e-8 * np.eye(n)
        log.warning("efficient_frontier_cov_regularised", condition_number=cond)
    else:
        log.info("efficient_frontier_cov_condition", condition_number=round(cond, 2))

    def _portfolio_variance(w: np.ndarray) -> float:
        return float(w @ cov @ w)

    bounds = [(min_weight, max_weight)] * n
    sum_to_one = {"type": "eq", "fun": lambda w: float(np.sum(w)) - 1.0}
    w0 = np.full(n, 1.0 / n)

    # Lower sweep bound — the global minimum-variance portfolio's return.
    mv = scipy_optimize.minimize(
        _portfolio_variance, w0, method="SLSQP", bounds=bounds,
        constraints=[sum_to_one], options={"ftol": 1e-12, "maxiter": 1000},
    )
    w_mv = mv.x if mv.success else w0
    ret_min = float(mu @ w_mv)
    # Upper sweep bound — the highest achievable return is 100% in the
    # single highest-return asset (long-only, fully invested).
    ret_max = float(mu.max())

    if ret_max - ret_min < 1e-9:
        # Degenerate (all assets identical return) — one point is the frontier.
        ann_vol = float(np.sqrt(max(_portfolio_variance(w_mv), 0.0)))
        sharpe = ((ret_min - risk_free) / ann_vol) if ann_vol > 1e-12 else 0.0
        return [{
            "volatility": round(ann_vol, 4),
            "return": round(ret_min, 4),
            "sharpe": round(sharpe, 4),
            "weights": {t: round(float(wi), 4) for t, wi in zip(returns.columns, w_mv)},
        }]

    frontier: list[dict] = []
    w_prev = w_mv  # warm-start each solve from the previous target's weights
    for target in np.linspace(ret_min, ret_max, n_points):
        constraints = [
            sum_to_one,
            {"type": "eq", "fun": lambda w, t=float(target): float(mu @ w) - t},
        ]
        res = scipy_optimize.minimize(
            _portfolio_variance, w_prev, method="SLSQP", bounds=bounds,
            constraints=constraints, options={"ftol": 1e-12, "maxiter": 1000},
        )
        w = res.x if res.success else None
        if w is None:
            continue
        w_prev = w
        ann_ret = float(mu @ w)
        ann_vol = float(np.sqrt(max(w @ cov @ w, 0.0)))
        sharpe = ((ann_ret - risk_free) / ann_vol) if ann_vol > 1e-12 else 0.0
        frontier.append({
            "volatility": round(ann_vol, 4),
            "return": round(ann_ret, 4),
            "sharpe": round(sharpe, 4),
            "weights": {t: round(float(wi), 4) for t, wi in zip(returns.columns, w)},
        })

    return frontier
