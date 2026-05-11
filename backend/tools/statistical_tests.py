"""
tools/statistical_tests.py

Full statistical significance suite for all 10 portfolio strategies.

Tiered thresholds (Benjamin et al. 2018, p < 0.005 for Tier 1) are enforced
by CLAUDE.md config, not hardcoded in tests. The tier depends on observation
count, which varies by sub-period and regime window. Every function returns
threshold_tier so the caller can disclose which tier applies — the QA checklist
explicitly requires this disclosure alongside every reported p-value.

Why p < 0.005 rather than the conventional 0.05:
  Testing 10 strategies simultaneously, each with multiple metrics, would
  produce several false positives at p < 0.05 by random chance alone. The
  stricter threshold, combined with BH FDR correction and CPCV, makes it very
  difficult for a lucky or overfitted strategy to pass all five Tier 1 gates.

Sub-period tests use p < 0.05 (Tier 2) because n is insufficient for 80%
power at p < 0.005 — applying Tier 1 there would create systematic false
negatives. Stress test windows are directional only (no p-values) because
n < 60 makes any formal test meaningless. Threshold tier must always be cited.
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
    Paired (one-sample) t-test on daily active returns (strategy minus benchmark).
    Paired rather than independent two-sample: the same market conditions drive
    both series on every day, so the natural test is whether the daily difference
    deviates from zero. An independent t-test would ignore this pairing and inflate
    variance, making it harder to detect real outperformance — biasing against us
    rather than for us, but still wrong. H0: mean active return = 0.
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
    Jobson-Korkie (1981) test for equality of two Sharpe ratios.
    A naive z-test on the Sharpe difference (SR_A - SR_B) / sqrt(2/n) ignores
    the covariance between the two strategies' returns, which inflates the
    test statistic when they are positively correlated (as ours tend to be).
    JK derives the correct asymptotic variance of the Sharpe difference under
    joint normality — the test statistic accounts for the cross-moment between
    the two return series. H0: Sharpe(A) == Sharpe(B).
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
    OLS alpha test with conditional Newey-West HAC correction.
    Newey-West SE is applied only when Ljung-Box detects autocorrelation in
    the residuals (p < 0.05), not unconditionally. Applying HAC to non-autocorrelated
    residuals inflates standard errors unnecessarily and reduces power — we would
    miss genuine alphas by over-correcting. The conditional path is the right choice:
    use the simplest correct estimator. OLS alpha = intercept from the CAPM regression,
    testing whether outperformance is explained by beta exposure alone.
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
    """
    Jarque-Bera normality test — appropriate for large samples.
    JB rather than Shapiro-Wilk because Shapiro-Wilk is designed for n < 2000;
    our full-period series has ~6,500 daily observations where SW becomes over-
    powered and rejects normality for economically trivial deviations. JB tests
    skewness and excess kurtosis jointly, which is exactly what matters here:
    financial returns are typically left-skewed and fat-tailed, and JB quantifies
    the severity. The result gates whether we use the block bootstrap (normality
    rejected → bootstrap) or standard OLS inference.
    """
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
    """
    Ljung-Box test for serial autocorrelation up to 10 lags.
    Ljung-Box rather than Durbin-Watson because DW tests lag-1 autocorrelation
    only — it would miss weekly seasonality or momentum autocorrelation in our
    return series, both of which appear in daily equity data. Testing 10 lags
    captures the two-week window where short-term momentum is most common.
    The result gates whether alpha_significance_test uses standard or Newey-West SE,
    and informs the block bootstrap block size selection in Sprint 3.
    """
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
    """
    Augmented Dickey-Fuller test for unit root. H0: series is non-stationary.
    AIC lag selection (autolag="AIC") is used rather than a fixed lag count
    because the optimal lag length varies across asset classes and regimes.
    AIC trades off model fit against complexity to find the lag that best
    captures the autocorrelation structure without overfitting. A fixed lag
    of, say, 5 would underfit momentum-heavy equity series and overfit the
    mean-reverting bond series, producing inconsistent results across strategies.
    """
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
    Block bootstrap Sharpe ratio test — used when normality_test rejects H0.
    Standard iid bootstrap resamples individual days, which destroys the
    volatility clustering and autocorrelation structure in financial returns.
    Block bootstrap resamples contiguous blocks of BLOCK_SIZE=21 trading days
    (~1 month), preserving within-block serial correlation. BLOCK_SIZE=21 is
    set in config to match approximately one month — the natural autocorrelation
    horizon for momentum signals. Changing it to 5 or 63 would produce different
    p-values; 21 is the compromise between capturing momentum (~21 days) and
    preserving enough block independence for the distribution to converge.
    seed=RANDOM_SEED=42 is fixed for reproducibility — required by QA checklist.
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
    Benjamini-Hochberg FDR correction across all 10 strategy p-values.
    BH rather than Bonferroni because our strategies are positively correlated
    (they all hold SPY; many share bond allocations). Bonferroni assumes
    independence among tests — when tests are correlated, Bonferroni over-
    corrects and generates systematic false negatives. BH controls the false
    discovery rate under positive dependence (PRDS condition), which our
    strategy universe satisfies. FDR_Q_VALUE = 0.005 matches the Tier 1
    threshold — the correction does not relax the primary standard.
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
    Statistical power analysis before applying any significance threshold.
    effect_size=0.3 (Cohen's d "small-medium") is calibrated to 0.3 Sharpe units —
    the minimum improvement that would be economically meaningful for a Forest Capital
    mandate (0.3 Sharpe above the 0.61 benchmark = 0.91 minimum threshold for
    recommendation). An effect size of 0.1 would be statistically detectable but
    economically irrelevant; 0.5 would require unrealistically large outperformance.
    The function returns recommended_threshold rather than just is_adequately_powered
    so callers automatically use the correct tier — preventing the use of p < 0.005
    on sub-periods where it would produce systematic false negatives.
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


# ── Deflated Sharpe Ratio (Lopez de Prado & Bailey 2014) ──────────────────────

def deflated_sharpe_ratio(
    sharpe: float,
    n_obs: int,
    n_trials: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> dict:
    """
    Deflated Sharpe Ratio corrects for the multiple-testing bias in Sharpe ratios.
    When 10 strategies are tested, the best Sharpe is selected — its observed value
    is inflated by the selection process (same mechanism as p-hacking). DSR asks:
    what is the minimum Sharpe ratio that should be deemed significant given that
    n_trials strategies were evaluated?
    The formula (Bailey & López de Prado 2014, Equation 4):
      SR* = sqrt(V[SR]) * [(1 - γ) * Φ^{-1}(1 - 1/n_trials) + γ * Φ^{-1}(1 - 1/(n_trials * e))]
    where V[SR] = (1 + (1 - skew*SR + (kurt-1)/4 * SR²)) / (n-1)
    is the variance of the Sharpe ratio estimator under non-normality.
    n_trials=10 throughout because we always test all 10 strategies.
    Changing n_trials changes the minimum required Sharpe — more trials → higher bar.
    """
    from scipy.stats import norm

    # Variance of the Sharpe ratio estimator under non-normality
    # kurtosis here is excess kurtosis; the formula uses excess kurtosis (kurt - 3)
    excess_kurt = kurtosis - 3.0
    var_sr = (
        1.0
        + 0.5 * sharpe ** 2 * (excess_kurt + 1.0)
        - sharpe * skewness
        + sharpe ** 2 * skewness ** 2 / 4.0
    ) / max(n_obs - 1, 1)
    std_sr = float(np.sqrt(max(var_sr, 0.0)))

    # DSR threshold: minimum SR to be significant given n_trials strategies tested
    euler_gamma = 0.5772156649
    if n_trials > 1:
        z1 = norm.ppf(1.0 - 1.0 / n_trials)
        z2 = norm.ppf(1.0 - 1.0 / (n_trials * np.e))
        sr_star = std_sr * ((1.0 - euler_gamma) * z1 + euler_gamma * z2)
    else:
        sr_star = std_sr * norm.ppf(1.0 - 0.5)  # n_trials=1 → standard z-test

    # DSR p-value: P(observed SR > SR* under H0)
    if std_sr > 0:
        z = (sharpe - sr_star) / std_sr
        p_value = float(1.0 - norm.cdf(z))
    else:
        z, p_value = 0.0, 1.0

    from config import P_THRESHOLD_DSR
    log.info("stat_test", test="deflated_sharpe_ratio", sharpe=round(sharpe, 4), p_value=round(p_value, 6))

    return {
        "test": "deflated_sharpe_ratio",
        "observed_sharpe": sharpe,
        "sr_star": round(float(sr_star), 6),
        "std_sr": round(std_sr, 6),
        "z_stat": round(float(z), 4),
        "p_value": round(p_value, 6),
        "n_obs": n_obs,
        "n_trials": n_trials,
        "skewness": skewness,
        "excess_kurtosis": excess_kurt,
        "threshold": P_THRESHOLD_DSR,
        "threshold_tier": "tier1",
        "passed": bool(p_value < P_THRESHOLD_DSR),
    }


# ── Probabilistic Sharpe Ratio ────────────────────────────────────────────────

def probabilistic_sharpe_ratio(
    sharpe: float,
    benchmark_sharpe: float,
    n_obs: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> dict:
    """
    Probabilistic Sharpe Ratio: P(true SR > benchmark SR) (Bailey & López de Prado 2012).
    The observed Sharpe ratio is an estimate of the true Sharpe — it has sampling
    uncertainty that standard reporting ignores. PSR reports the probability that
    the true Sharpe ratio (the population parameter) exceeds a benchmark Sharpe
    ratio, accounting for non-normality via the Cornish-Fisher correction.
    PSR is strictly more informative than the point estimate because it reveals
    whether the observed outperformance is precise (high PSR even at small
    advantage) or uncertain (low PSR even at large apparent advantage).
    benchmark_sharpe=0.0 by default (test against zero); in practice, pass the
    benchmark strategy's Sharpe for head-to-head comparison.
    """
    from scipy.stats import norm

    excess_kurt = kurtosis - 3.0
    # Variance of the SR estimator (same as DSR)
    var_sr = (
        1.0
        + 0.5 * sharpe ** 2 * (excess_kurt + 1.0)
        - sharpe * skewness
        + sharpe ** 2 * skewness ** 2 / 4.0
    ) / max(n_obs - 1, 1)
    std_sr = float(np.sqrt(max(var_sr, 0.0)))

    if std_sr > 0:
        z = (sharpe - benchmark_sharpe) / std_sr
        psr = float(norm.cdf(z))
        p_value = float(1.0 - psr)
    else:
        psr = 1.0 if sharpe > benchmark_sharpe else 0.0
        p_value = 0.0 if sharpe > benchmark_sharpe else 1.0

    # 95% confidence interval on the Sharpe estimate
    ci_low = sharpe - 1.96 * std_sr
    ci_high = sharpe + 1.96 * std_sr

    return {
        "test": "probabilistic_sharpe_ratio",
        "observed_sharpe": sharpe,
        "benchmark_sharpe": benchmark_sharpe,
        "psr": round(psr, 6),           # P(true SR > benchmark SR)
        "p_value": round(p_value, 6),   # 1 - PSR (for significance testing)
        "sharpe_ci_95": (round(ci_low, 4), round(ci_high, 4)),
        "std_sr": round(std_sr, 6),
        "n_obs": n_obs,
    }


# ── SPA test (Hansen 2005) ────────────────────────────────────────────────────

def spa_test(
    all_strategy_returns: dict[str, pd.Series],
    benchmark_returns: pd.Series,
    n_boot: int = BOOTSTRAP_SAMPLES,
    seed: int = RANDOM_SEED,
) -> dict:
    """
    Hansen's (2005) Superior Predictive Ability test — guards against data snooping.
    When testing k strategies and reporting the best, the winner's Sharpe is
    inflated by the selection process (data snooping bias). The SPA test asks:
    given that we evaluated k strategies and selected the best, is the best
    strategy's outperformance genuinely superior to the benchmark, or could it
    have arisen from chance selection among strategies with no skill?
    Method: bootstrap the active return series, resample, compute the maximum
    Sharpe difference across all strategies in each bootstrap sample (building the
    null distribution of the maximum), then compare the observed maximum against it.
    p_spa < 0.005 (Tier 1) means the best strategy survives data-snooping correction.
    block bootstrap preserves autocorrelation structure in active returns.
    """
    np.random.seed(seed)

    # Align all strategies to benchmark
    aligned = {}
    b = benchmark_returns.dropna()
    for name, rets in all_strategy_returns.items():
        s, bm = rets.align(b, join="inner")
        aligned[name] = (s - bm).dropna()  # active returns

    if not aligned:
        return {"error": "no_aligned_strategies", "passed": False}

    # Observed Sharpe differences (strategy Sharpe - benchmark Sharpe)
    b_sharpe = float(b.mean() / b.std() * np.sqrt(ANNUALIZATION_FACTOR)) if b.std() > 0 else 0.0
    obs_diffs = {}
    for name, active in aligned.items():
        if len(active) > 5:
            sr = float(active.mean() / active.std() * np.sqrt(ANNUALIZATION_FACTOR)) if active.std() > 0 else 0.0
            obs_diffs[name] = sr

    if not obs_diffs:
        return {"error": "no_valid_strategies", "passed": False}

    best_strategy = max(obs_diffs, key=obs_diffs.__getitem__)
    obs_max = obs_diffs[best_strategy]

    # Bootstrap null distribution using block bootstrap on active returns
    from config import BLOCK_SIZE
    block_size = BLOCK_SIZE

    # Use all strategies' active returns concatenated for bootstrap consistency
    active_arrays = {n: v.values for n, v in aligned.items() if len(v) > block_size}
    if not active_arrays:
        return {"error": "insufficient_data", "passed": False}

    # Reference active return series (use best strategy for bootstrapping)
    ref = active_arrays.get(best_strategy, list(active_arrays.values())[0])
    n = len(ref)

    null_maxima = []
    for _ in range(n_boot):
        n_blocks = int(np.ceil(n / block_size))
        starts = np.random.randint(0, max(n - block_size + 1, 1), size=n_blocks)
        boot_idx = np.concatenate([np.arange(s, min(s + block_size, n)) for s in starts])[:n]

        # Compute Sharpe for each strategy on this bootstrap sample (demeaned — H0)
        boot_max = -np.inf
        for name, arr in active_arrays.items():
            boot_arr = arr[boot_idx] - arr.mean()  # demean to enforce H0
            if len(boot_arr) > 2 and boot_arr.std() > 0:
                boot_sr = float(np.mean(boot_arr) / np.std(boot_arr) * np.sqrt(ANNUALIZATION_FACTOR))
                if boot_sr > boot_max:
                    boot_max = boot_sr
        if boot_max > -np.inf:
            null_maxima.append(boot_max)

    if not null_maxima:
        return {"error": "bootstrap_failed", "passed": False}

    null_array = np.array(null_maxima)
    p_spa = float(np.mean(null_array >= obs_max))

    from config import P_THRESHOLD_PRIMARY
    log.info(
        "stat_test",
        test="spa",
        best_strategy=best_strategy,
        obs_max_sharpe=round(obs_max, 4),
        p_value=round(p_spa, 6),
    )

    return {
        "test": "spa",
        "best_strategy": best_strategy,
        "best_strategy_sharpe_diff": round(obs_max, 4),
        "all_sharpe_diffs": {k: round(v, 4) for k, v in obs_diffs.items()},
        "n_strategies": len(obs_diffs),
        "n_bootstrap": n_boot,
        "p_spa": round(p_spa, 6),
        "null_mean": round(float(null_array.mean()), 4),
        "null_std": round(float(null_array.std()), 4),
        "threshold": P_THRESHOLD_PRIMARY,
        "threshold_tier": "tier1",
        "passes_spa": bool(p_spa < P_THRESHOLD_PRIMARY),
    }
