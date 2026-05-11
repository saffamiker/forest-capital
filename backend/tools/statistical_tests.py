"""
Statistical significance testing suite.
Tiered thresholds enforced by config:
  Tier 1 (full period, n >= 220): p < 0.005
  Tier 2 (sub-period, n >= 60):   p < 0.05
  Directional only (stress tests): no p-values
Every function returns threshold_tier so callers can disclose it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.stattools import jarque_bera
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.stattools import adfuller
from statsmodels.stats.multitest import multipletests

from config import (
    ANNUALIZATION_FACTOR,
    P_THRESHOLD_PRIMARY,
    P_THRESHOLD_SUBPERIOD,
    FDR_Q_VALUE,
    BOOTSTRAP_SAMPLES,
    BLOCK_SIZE,
    RANDOM_SEED,
    MIN_OBSERVATIONS_FOR_POWER,
    MIN_OBSERVATIONS_SUBPERIOD,
)
from logger import get_logger

log = get_logger(__name__)


def _threshold_tier(n: int) -> tuple[float, str]:
    """Return (threshold, tier_label) based on number of observations."""
    if n >= MIN_OBSERVATIONS_FOR_POWER:
        return P_THRESHOLD_PRIMARY, "tier1"
    if n >= MIN_OBSERVATIONS_SUBPERIOD:
        return P_THRESHOLD_SUBPERIOD, "tier2"
    return float("nan"), "directional"


# ── Tier 1 primary tests ──────────────────────────────────────────────────────

def paired_ttest(
    strategy_returns: pd.Series,
    benchmark_returns: pd.Series,
    threshold: float = P_THRESHOLD_PRIMARY,
) -> dict:
    """
    Paired t-test: does the strategy's daily excess return over benchmark
    differ significantly from zero?
    """
    s, b = strategy_returns.align(benchmark_returns, join="inner")
    active = s - b
    n = len(active)
    t_stat, p_value = stats.ttest_1samp(active.dropna(), 0.0)
    effective_threshold, tier = _threshold_tier(n)
    if threshold != P_THRESHOLD_PRIMARY:
        effective_threshold = threshold

    log.info("stat_test", test="paired_ttest", n=n, p_value=round(p_value, 6))
    return {
        "test": "paired_ttest",
        "t_stat": float(t_stat),
        "p_value": float(p_value),
        "n_observations": n,
        "threshold": effective_threshold,
        "threshold_tier": tier,
        "passed": bool(p_value < effective_threshold),
    }


def jobson_korkie_test(
    sharpe_a: float,
    sharpe_b: float,
    returns_a: pd.Series,
    returns_b: pd.Series,
    n: int | None = None,
    threshold: float = P_THRESHOLD_PRIMARY,
) -> dict:
    """
    Jobson-Korkie test for equality of two Sharpe ratios.
    H0: Sharpe(A) == Sharpe(B).
    """
    a, b = returns_a.align(returns_b, join="inner")
    n_obs = n or len(a)
    if n_obs < 10:
        return {"test": "jobson_korkie", "error": "insufficient observations", "passed": False}

    mu_a, mu_b = a.mean(), b.mean()
    sigma_a, sigma_b = a.std(), b.std()
    sigma_ab = float(np.cov(a, b)[0, 1])

    # Asymptotic variance of Sharpe difference (Jobson-Korkie 1981)
    theta = (
        (1 / n_obs)
        * (
            2 * sigma_a ** 2 * sigma_b ** 2
            - 2 * sigma_a * sigma_b * sigma_ab
            + 0.5 * mu_a ** 2 * sigma_b ** 2
            + 0.5 * mu_b ** 2 * sigma_a ** 2
            - (mu_a * mu_b * sigma_ab ** 2) / (sigma_a * sigma_b)
        )
    )
    if theta <= 0:
        return {"test": "jobson_korkie", "error": "degenerate variance", "passed": False}

    z_stat = (mu_a / sigma_a - mu_b / sigma_b) / float(np.sqrt(theta))
    p_value = float(2 * (1 - stats.norm.cdf(abs(z_stat))))
    effective_threshold, tier = _threshold_tier(n_obs)

    log.info("stat_test", test="jobson_korkie", n=n_obs, p_value=round(p_value, 6))
    return {
        "test": "jobson_korkie",
        "z_stat": float(z_stat),
        "p_value": p_value,
        "n_observations": n_obs,
        "threshold": effective_threshold,
        "threshold_tier": tier,
        "passed": bool(p_value < effective_threshold),
    }


def alpha_significance_test(
    strategy_returns: pd.Series,
    benchmark_returns: pd.Series,
) -> dict:
    """
    OLS regression alpha test: strategy_return = alpha + beta * benchmark_return + e.
    Uses Newey-West SE if autocorrelation is detected.
    """
    s, b = strategy_returns.align(benchmark_returns, join="inner")
    n = len(s)
    effective_threshold, tier = _threshold_tier(n)

    X = pd.DataFrame({"const": 1.0, "bm": b})
    # OLS
    from numpy.linalg import lstsq
    XA = X.values
    y = s.values
    coefs, _, _, _ = lstsq(XA, y, rcond=None)
    alpha_daily = float(coefs[0])
    residuals = y - XA @ coefs

    # Check autocorrelation in residuals
    lb_result = acorr_ljungbox(residuals, lags=[10], return_df=True)
    has_autocorr = bool(lb_result["lb_pvalue"].values[0] < 0.05)

    if has_autocorr:
        # Newey-West standard errors
        from statsmodels.regression.linear_model import OLS
        from statsmodels.tools.tools import add_constant
        import statsmodels.api as sm
        model = OLS(y, XA).fit(cov_type="HAC", cov_kwds={"maxlags": 5})
        alpha_se = float(model.bse[0])
        t_stat = alpha_daily / alpha_se if alpha_se > 0 else 0.0
        p_value = float(2 * (1 - stats.t.cdf(abs(t_stat), df=n - 2)))
    else:
        # Standard OLS SE
        resid_var = float(np.var(residuals, ddof=XA.shape[1]))
        XtX_inv = np.linalg.pinv(XA.T @ XA)
        se_alpha = float(np.sqrt(resid_var * XtX_inv[0, 0]))
        t_stat = alpha_daily / se_alpha if se_alpha > 0 else 0.0
        p_value = float(2 * (1 - stats.t.cdf(abs(t_stat), df=n - 2)))

    log.info(
        "stat_test",
        test="alpha_significance",
        n=n,
        p_value=round(p_value, 6),
        has_autocorr=has_autocorr,
    )
    return {
        "test": "alpha_significance",
        "alpha_daily": alpha_daily,
        "alpha_annualised": alpha_daily * ANNUALIZATION_FACTOR,
        "alpha_bps": alpha_daily * ANNUALIZATION_FACTOR * 10_000,
        "t_stat": float(t_stat),
        "p_value": p_value,
        "n_observations": n,
        "threshold": effective_threshold,
        "threshold_tier": tier,
        "has_autocorrelation": has_autocorr,
        "newey_west_used": has_autocorr,
        "passed": bool(p_value < effective_threshold),
    }


# ── Diagnostic tests (not Tier 1 gates — inform method selection) ─────────────

def normality_test(returns: pd.Series) -> dict:
    """Jarque-Bera normality test. H0: returns are normally distributed."""
    clean = returns.dropna()
    jb_stat, p_value, skewness, kurtosis = jarque_bera(clean)
    return {
        "test": "jarque_bera",
        "jb_stat": float(jb_stat),
        "p_value": float(p_value),
        "skewness": float(skewness),
        "excess_kurtosis": float(kurtosis),
        "normality_rejected": bool(p_value < 0.05),
        "n_observations": len(clean),
    }


def autocorrelation_test(returns: pd.Series, lags: int = 10) -> dict:
    """Ljung-Box test for serial autocorrelation. H0: no autocorrelation."""
    clean = returns.dropna()
    lb_result = acorr_ljungbox(clean, lags=[lags], return_df=True)
    lb_stat = float(lb_result["lb_stat"].values[0])
    p_value = float(lb_result["lb_pvalue"].values[0])
    return {
        "test": "ljung_box",
        "lags": lags,
        "lb_stat": lb_stat,
        "p_value": p_value,
        "has_autocorrelation": bool(p_value < 0.05),
        "n_observations": len(clean),
    }


def stationarity_test(returns: pd.Series) -> dict:
    """Augmented Dickey-Fuller test. H0: unit root (non-stationary)."""
    clean = returns.dropna()
    adf_stat, p_value, used_lag, n_obs, critical_values, _ = adfuller(clean, autolag="AIC")
    return {
        "test": "adf",
        "adf_stat": float(adf_stat),
        "p_value": float(p_value),
        "used_lag": int(used_lag),
        "n_observations": int(n_obs),
        "critical_values": {k: float(v) for k, v in critical_values.items()},
        "is_stationary": bool(p_value < 0.05),
    }


# ── Block bootstrap Sharpe ────────────────────────────────────────────────────

def block_bootstrap_sharpe(
    strategy_returns: pd.Series,
    risk_free_rate: pd.Series | float,
    benchmark_sharpe: float = 0.0,
    n_samples: int = BOOTSTRAP_SAMPLES,
    block_size: int = BLOCK_SIZE,
    seed: int = RANDOM_SEED,
) -> dict:
    """
    Block bootstrap for Sharpe ratio inference.
    Preserves autocorrelation structure by resampling blocks.
    Returns p_value = P(bootstrap Sharpe <= observed Sharpe) assuming H0: SR=0.
    """
    np.random.seed(seed)

    clean = strategy_returns.dropna()
    n = len(clean)
    values = clean.values

    if isinstance(risk_free_rate, (int, float)):
        rf_values = np.full(n, risk_free_rate / ANNUALIZATION_FACTOR)
    else:
        rf_aligned = risk_free_rate.reindex(clean.index, method="ffill").fillna(
            float(risk_free_rate.mean())
        )
        rf_values = rf_aligned.values

    excess = values - rf_values

    # Observed Sharpe
    obs_sharpe = float(np.mean(excess) / np.std(excess) * np.sqrt(ANNUALIZATION_FACTOR)) if np.std(excess) > 0 else 0.0

    # Bootstrap distribution under H0 (demean excess returns)
    demean_excess = excess - excess.mean()
    n_blocks = int(np.ceil(n / block_size))

    bootstrap_sharpes = []
    for _ in range(n_samples):
        starts = np.random.randint(0, max(n - block_size + 1, 1), size=n_blocks)
        boot = np.concatenate([demean_excess[s: s + block_size] for s in starts])[:n]
        s = float(np.mean(boot) / np.std(boot) * np.sqrt(ANNUALIZATION_FACTOR)) if np.std(boot) > 0 else 0.0
        bootstrap_sharpes.append(s)

    bootstrap_array = np.array(bootstrap_sharpes)
    # Two-sided p-value: proportion of bootstrap samples as extreme as observed
    p_value = float(np.mean(np.abs(bootstrap_array) >= abs(obs_sharpe)))

    effective_threshold, tier = _threshold_tier(n)

    log.info("stat_test", test="block_bootstrap_sharpe", n=n, p_value=round(p_value, 6))
    return {
        "test": "block_bootstrap_sharpe",
        "observed_sharpe": obs_sharpe,
        "bootstrap_mean": float(bootstrap_array.mean()),
        "bootstrap_std": float(bootstrap_array.std()),
        "p_value": p_value,
        "n_observations": n,
        "n_samples": n_samples,
        "block_size": block_size,
        "threshold": effective_threshold,
        "threshold_tier": tier,
        "passed": bool(p_value < effective_threshold),
    }


# ── Multiple comparison correction (FDR) ────────────────────────────────────

def multiple_comparison_correction(
    p_values_dict: dict[str, float],
    method: str = "fdr_bh",
    alpha: float = FDR_Q_VALUE,
) -> dict:
    """
    Benjamini-Hochberg FDR correction across multiple strategy p-values.
    Returns original and corrected p-values with pass/fail flags.
    """
    if not p_values_dict:
        return {"error": "empty p-values dict"}

    names = list(p_values_dict.keys())
    raw = np.array([p_values_dict[n] for n in names])

    reject, p_corrected, _, _ = multipletests(raw, alpha=alpha, method=method)

    result = {}
    for i, name in enumerate(names):
        result[name] = {
            "p_raw": float(raw[i]),
            "p_corrected": float(p_corrected[i]),
            "passed": bool(reject[i]),
        }

    log.info(
        "stat_test",
        test="fdr_correction",
        n_strategies=len(names),
        n_passed=int(np.sum(reject)),
    )
    return {
        "method": method,
        "alpha": alpha,
        "strategies": result,
        "n_passed": int(np.sum(reject)),
        "n_tested": len(names),
    }


# ── Power analysis ────────────────────────────────────────────────────────────

def power_check(
    n_obs: int,
    effect_size: float = 0.3,
    alpha: float = P_THRESHOLD_PRIMARY,
    power: float = 0.80,
) -> dict:
    """
    Check whether n_obs provides sufficient power to detect effect_size at alpha.
    Returns is_adequately_powered, n_required, recommended_threshold.
    """
    from scipy.stats import norm

    # Two-sided t-test required n: n = (z_alpha/2 + z_beta)^2 / effect_size^2
    z_alpha = norm.ppf(1 - alpha / 2)
    z_beta = norm.ppf(power)
    n_required = int(np.ceil(((z_alpha + z_beta) ** 2) / (effect_size ** 2)))

    if n_obs >= MIN_OBSERVATIONS_FOR_POWER:
        recommended_threshold = P_THRESHOLD_PRIMARY
        tier = "tier1"
    elif n_obs >= MIN_OBSERVATIONS_SUBPERIOD:
        recommended_threshold = P_THRESHOLD_SUBPERIOD
        tier = "tier2"
    else:
        recommended_threshold = float("nan")
        tier = "directional"

    return {
        "n_observations": n_obs,
        "n_required_for_80pct_power": n_required,
        "effect_size": effect_size,
        "alpha": alpha,
        "is_adequately_powered": n_obs >= n_required,
        "recommended_threshold": recommended_threshold,
        "threshold_tier": tier,
    }
