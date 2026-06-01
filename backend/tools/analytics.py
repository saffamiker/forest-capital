"""
tools/analytics.py

Academic analytics layer for the midpoint paper and the analytics view.

Every figure is derived from data already in PostgreSQL — market_data_monthly
(equity/IG/HY monthly returns), strategy_results_cache (the ten strategy
results), and ff_factors_monthly (Fama-French factors). This module adds no
new data source.

The compute functions are pure: they take plain dict / list inputs and return
plain dicts, so they unit-test without a database or an event loop. The single
DB-touching entry point (assemble_academic_analytics) lives in main.py.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

# The 2022 Fed hiking cycle is when the equity-bond correlation flipped
# positive — the project's central finding. Pre/post-2022 splits and the
# rolling-correlation regime marker all key off this date.
#
# CONVENTION (UAT L2 boundary-date audit, May 24 2026):
# 2022-01-01 is the boundary date applied UNIFORMLY across every
# component that splits at the regime break. The convention is:
#
#   pre_2022  = every observation whose timestamp is STRICTLY LESS THAN
#               2022-01-01 (i.e., everything dated 2021-12-31 or earlier).
#   post_2022 = every observation whose timestamp is GREATER THAN OR
#               EQUAL TO 2022-01-01 (i.e., everything dated 2022-01-01
#               or later — January 2022's month-end of 2022-01-31 is
#               firmly in POST).
#
# This rule is applied at the OBSERVATION-TIMESTAMP level — every
# component that consumes the regime break uses `index < REGIME_BREAK`
# vs `index >= REGIME_BREAK` against the relevant series index. For
# rolling-window metrics (12-month rolling correlation) the rule is
# applied to the ROLLING-VALUE timestamp, not to each contributing
# observation: the rolling correlation value dated 2022-01-31 reflects
# the 12-month window ending on that date (Feb 2021 → Jan 2022 inclusive)
# and is classified as POST because 2022-01-31 >= 2022-01-01. The first
# 11 post-2022 rolling values therefore carry pre-2022 history in their
# lookback windows by construction; this is intentional and documented
# in the auditor formula spec so independent recomputation produces the
# same boundary classification.
REGIME_BREAK = pd.Timestamp("2022-01-01")

# Monthly → annual. All return series in this project are monthly.
_ANN = 12


# ── Series helpers ────────────────────────────────────────────────────────────

def _pairs_to_series(pairs: list) -> pd.Series:
    """A monthly_returns list of [iso_date, value] pairs → a date-indexed
    float Series. strategy_results_cache stores returns as ordered pairs
    (see backtester.py) so chronological order survives the JSON round-trip."""
    if not pairs:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([p[0] for p in pairs])
    vals = [float(p[1]) for p in pairs]
    return pd.Series(vals, index=idx).sort_index()


def _cagr(r: pd.Series) -> float:
    """Geometric (compound) annual growth rate from a monthly return series."""
    if len(r) == 0:
        return 0.0
    growth = float((1.0 + r).prod())
    if growth <= 0.0:
        return -1.0
    return growth ** (_ANN / len(r)) - 1.0


def _ann_vol(r: pd.Series) -> float:
    """Annualised volatility — monthly std scaled by sqrt(12)."""
    return float(r.std(ddof=1) * np.sqrt(_ANN)) if len(r) > 1 else 0.0


def _sharpe(r: pd.Series, rf: pd.Series | None = None) -> float:
    """Annualised Sharpe ratio. rf, when given, is the aligned monthly
    risk-free series — the project never uses a fixed-constant rate."""
    if len(r) < 2:
        return 0.0
    excess = r - rf.reindex(r.index).fillna(0.0) if rf is not None else r
    sd = float(excess.std(ddof=1))
    if sd < 1e-12:
        return 0.0
    return float(excess.mean() / sd * np.sqrt(_ANN))


def _max_drawdown(r: pd.Series) -> float:
    """Largest peak-to-trough loss of the cumulative return curve."""
    if len(r) == 0:
        return 0.0
    curve = (1.0 + r).cumprod()
    dd = curve / curve.cummax() - 1.0
    return float(dd.min())


# ── Bootstrap CI on Sharpe ────────────────────────────────────────────────────
#
# Block bootstrap on monthly returns (Künsch 1989, Politis-Romano 1994).
# Resampling INDIVIDUAL months destroys the within-block autocorrelation
# of financial returns; resampling contiguous BLOCKS of 12 months
# preserves the within-year serial structure (and the volatility
# clustering that 23 years of monthly data still exhibits) while letting
# the block boundaries reshape the resample.
#
# Block length 12 was chosen against block length 6 and 24 for two
# project-specific reasons: a 12-month block preserves the calendar-year
# return clustering equity markets exhibit, and it matches the
# annualisation horizon used everywhere else in the platform. Shorter
# blocks under-state the CI (autocorrelation leaks through block joins);
# longer blocks over-state it (fewer effective independent draws). 12 is
# the standard length in the regime-aware portfolio-construction
# literature for monthly data of this horizon.
#
# 10,000 resamples is the standard CI-stability count — at this size the
# 2.5%/97.5% empirical percentiles of the resampled Sharpe are stable to
# ~0.005, well below the table's 2-decimal-place display precision.
#
# seed=42 is the project-wide deterministic seed (config.RANDOM_SEED).
# Same input → same CI bounds → reproducible across CI runs.

_BOOTSTRAP_BLOCK_SIZE = 12
_BOOTSTRAP_N_RESAMPLES = 10_000
_BOOTSTRAP_SEED = 42
_BOOTSTRAP_MIN_OBS = 24       # CI is meaningless on fewer than 2 years
_BOOTSTRAP_CONFIDENCE = 0.95


def bootstrap_sharpe_ci(
    returns: pd.Series,
    rf: "pd.Series | float | None" = None,
    *,
    block_size: int = _BOOTSTRAP_BLOCK_SIZE,
    n_resamples: int = _BOOTSTRAP_N_RESAMPLES,
    seed: int = _BOOTSTRAP_SEED,
    confidence: float = _BOOTSTRAP_CONFIDENCE,
) -> dict | None:
    """Stationary-circular block bootstrap CI on the annualised monthly
    Sharpe ratio. Returns {point, ci_low, ci_high, n_resamples,
    block_size, n_observations, samples} or None when the series is too
    short to resample meaningfully.

    The Sharpe formula matches tools/backtester._m_sharpe exactly:
      excess = returns - rf_aligned (per-month rf when rf is a Series,
              broadcast constant when rf is scalar, zero when rf is None)
      sharpe = mean(excess) / std(excess, ddof=1) * sqrt(12)

    The block-resample uses np.random.Generator(PCG64, seed) so two
    parallel calls don't interfere with the global numpy RNG state, and
    the seed produces the SAME bounds across runs. `samples` is included
    on the returned dict for the density-overlap visualisation; callers
    that only need the CI strip it off before serialising.
    """
    clean = returns.dropna()
    n = len(clean)
    if n < _BOOTSTRAP_MIN_OBS:
        return None

    # Align the rf series the same way the backtester does. A None rf
    # means rf=0 throughout; we still need to subtract a zero vector
    # so the std/sample paths are identical to the rf-present branch.
    if rf is None:
        rf_aligned = pd.Series(0.0, index=clean.index)
    elif isinstance(rf, (int, float)):
        rf_aligned = pd.Series(float(rf), index=clean.index)
    else:
        rf_aligned = (rf.reindex(clean.index).ffill().bfill())
        if rf_aligned.isna().any():
            rf_aligned = rf_aligned.fillna(float(rf.mean()))
    excess = (clean.values - rf_aligned.values).astype(float)

    def _sharpe(arr: np.ndarray) -> float:
        sd = float(np.std(arr, ddof=1))
        if sd <= 0:
            return 0.0
        return float(np.mean(arr) / sd * math.sqrt(12))

    point = _sharpe(excess)

    # Stationary block bootstrap with circular wraparound. n_blocks is
    # ceil(n/block_size); we trim the concatenated resample to exactly
    # n so the Sharpe is computed over the same sample size every time.
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block_size))
    boots = np.empty(n_resamples, dtype=float)
    # Circular index: wraps past the end for blocks that start within
    # block_size-1 of the boundary. Removes the right-edge bias a
    # straight non-overlapping block bootstrap exhibits.
    for i in range(n_resamples):
        starts = rng.integers(0, n, size=n_blocks)
        idx = (starts[:, None]
               + np.arange(block_size)[None, :]) % n
        resample = excess[idx.ravel()][:n]
        boots[i] = _sharpe(resample)

    alpha = (1.0 - confidence) / 2.0
    lo = float(np.quantile(boots, alpha))
    hi = float(np.quantile(boots, 1.0 - alpha))

    return {
        "point":           point,
        "ci_low":          lo,
        "ci_high":         hi,
        "n_resamples":     n_resamples,
        "block_size":      block_size,
        "n_observations":  n,
        "confidence":      confidence,
        # The resampled Sharpe distribution — used by the density-overlap
        # visualisation. Sorted so the frontend can compute kernel
        # densities or percentile bands without an extra pass.
        "samples":         np.sort(boots).tolist(),
    }


def bootstrap_ci_table(
    strategy_results: dict[str, dict],
    rf: "pd.Series | None" = None,
    *,
    include_samples: bool = False,
) -> list[dict]:
    """One row per strategy (plus BENCHMARK if present in
    strategy_results) carrying the bootstrap Sharpe CI. The table is
    sorted by point Sharpe descending so the strongest strategies head
    the list — same convention as the strategy-comparison table.

    The bootstrap Sharpe distribution (`samples`) is stripped by default
    because it's 10,000 floats per strategy — too large for a chart-data
    payload. The density-overlap endpoint asks for include_samples=True
    explicitly.
    """
    rows: list[dict] = []
    for name, res in (strategy_results or {}).items():
        s = _pairs_to_series(res.get("monthly_returns") or [])
        ci = bootstrap_sharpe_ci(s, rf=rf)
        if ci is None:
            continue
        row = {
            "strategy":        res.get("strategy_name") or name,
            "sharpe":          round(ci["point"], 4),
            "ci_low":          round(ci["ci_low"], 4),
            "ci_high":         round(ci["ci_high"], 4),
            "n_resamples":     ci["n_resamples"],
            "block_size":      ci["block_size"],
            "n_observations":  ci["n_observations"],
        }
        if include_samples:
            # Down-sample to 1000 evenly-spaced quantiles for the chart
            # — preserves the distribution shape while keeping the
            # payload bounded. The full 10k samples stay in the cache
            # only when a caller explicitly opts in.
            samples = ci["samples"]
            if len(samples) > 1000:
                step = len(samples) // 1000
                samples = samples[::step][:1000]
            row["samples"] = [round(float(x), 4) for x in samples]
        rows.append(row)
    rows.sort(key=lambda r: r["sharpe"], reverse=True)
    return rows


def _recovery_months(r: pd.Series) -> int | None:
    """Months from the deepest drawdown trough back to a new equity high.
    None when the series never recovers inside the sample — an honest
    'still underwater' rather than a misleading zero."""
    if len(r) == 0:
        return None
    curve = (1.0 + r).cumprod()
    peak = curve.cummax()
    dd = (curve / peak - 1.0).to_numpy()
    trough = int(dd.argmin())
    peak_before = float(peak.iloc[trough])
    for i in range(trough + 1, len(curve)):
        if float(curve.iloc[i]) >= peak_before:
            return i - trough
    return None


# ── 1. Summary statistics ─────────────────────────────────────────────────────

def summary_statistics(
    asset_series: dict[str, pd.Series],
    rf: pd.Series | None,
) -> list[dict]:
    """
    CAGR, annualised volatility, Sharpe, max drawdown and skewness for each
    named return series. Used for the equity / IG / HY / BENCHMARK summary
    table — the headline figures the midpoint paper opens with.
    """
    # Benchmark series + CAGR — the reference for the excess-return and
    # information-ratio columns.
    bench_series: pd.Series | None = None
    bench_cagr: float | None = None
    for name, r in asset_series.items():
        if "BENCHMARK" in name.upper():
            bench_series = r.dropna()
            bench_cagr = _cagr(bench_series)
            break

    rows: list[dict] = []
    for name, r in asset_series.items():
        r = r.dropna()
        cagr = _cagr(r)

        # Information ratio = annualised mean monthly excess return over the
        # benchmark / annualised tracking error. Undefined (None) for the
        # benchmark itself AND for the EQUITY asset — the benchmark is 100%
        # equity, so EQUITY *is* the benchmark: its excess return is a zero
        # vector and the ratio is 0/0. The EQUITY asset series and the
        # BENCHMARK strategy series are economically identical but not
        # bit-identical (different source tables, rounding), so a naive
        # computation divides tiny noise into a spurious IR — guard against
        # it. Also None when no benchmark series is supplied.
        info_ratio: float | None = None
        is_benchmark_equiv = name.strip().upper() in ("EQUITY", "BENCHMARK")
        if bench_series is not None and not is_benchmark_equiv:
            excess_m = (r - bench_series.reindex(r.index)).dropna()
            te = float(excess_m.std(ddof=1)) if len(excess_m) > 1 else 0.0
            if te > 1e-12:
                info_ratio = round(float(excess_m.mean() / te * np.sqrt(_ANN)), 4)

        rows.append({
            "asset":          name,
            "cagr":           round(cagr, 4),
            # Excess return vs the 100% equity benchmark — the benchmark's
            # own row is 0.0; None when no benchmark series is supplied.
            "excess_return":  (round(cagr - bench_cagr, 4)
                               if bench_cagr is not None else None),
            "ann_volatility": round(_ann_vol(r), 4),
            "sharpe_ratio":   round(_sharpe(r, rf), 4),
            "information_ratio": info_ratio,
            "max_drawdown":   round(_max_drawdown(r), 4),
            "skewness":       round(float(r.skew()), 4) if len(r) >= 3 else 0.0,
            "n_months":       int(len(r)),
            # Actual data period of this series — disclosure for the
            # Period column. The four asset series all span the full
            # study period; the dynamic strategies (shown on the
            # cumulative-return chart) start later, see cumulative_returns.
            "period_start":   str(r.index[0].date()) if len(r) else None,
            "period_end":     str(r.index[-1].date()) if len(r) else None,
        })
    return rows


# ── Rolling excess return ─────────────────────────────────────────────────────

def rolling_excess_return(
    strategy_results: dict[str, dict], window: int = 12,
) -> dict:
    """
    12-month rolling excess total return of each strategy vs the 100%
    equity benchmark — the strategy's trailing-`window`-month compound
    return minus the benchmark's, at each month. Surfaces the periods of
    relative out/under-performance the Part I secondary objective asks for.
    """
    empty = {"strategies": [], "points": [], "window_months": window}
    bench = strategy_results.get("BENCHMARK", {})
    bench_s = _pairs_to_series(bench.get("monthly_returns") or [])
    if bench_s.empty:
        return empty

    def _trailing(s: pd.Series) -> pd.Series:
        return (1.0 + s).rolling(window).apply(lambda x: x.prod(), raw=True) - 1.0

    bench_roll = _trailing(bench_s)
    series: dict[str, pd.Series] = {}
    for name, res in strategy_results.items():
        if name == "BENCHMARK":
            continue
        s = _pairs_to_series(res.get("monthly_returns") or [])
        if s.empty:
            continue
        excess = (_trailing(s) - bench_roll.reindex(s.index)).dropna()
        if not excess.empty:
            series[res.get("strategy_name") or name] = excess

    if not series:
        return empty
    all_dates = sorted(set().union(*[set(v.index) for v in series.values()]))
    strategies = sorted(series.keys())
    points: list[dict] = []
    for d in all_dates:
        row: dict = {"date": str(d.date())}
        for label in strategies:
            v = series[label].get(d)
            row[label] = None if v is None or pd.isna(v) else round(float(v), 4)
        points.append(row)
    return {"strategies": strategies, "points": points, "window_months": window}


# ── Cumulative total return ───────────────────────────────────────────────────

def cumulative_returns(strategy_results: dict[str, dict]) -> dict:
    """
    Growth-of-$1 cumulative total return for every strategy.

    Each series starts at exactly 1.0 on a baseline month one period before
    its first return, then compounds (1 + r) month by month. The dynamic
    strategies consume an initialisation lookback window, so their baseline
    is later than the full study period — `start_dates` carries each
    strategy's first actual return month so the chart can disclose the
    shorter histories rather than imply a flat or zero pre-history.
    """
    curves: dict[str, pd.Series] = {}
    start_dates: dict[str, str] = {}
    for name, res in strategy_results.items():
        s = _pairs_to_series(res.get("monthly_returns") or [])
        if s.empty:
            continue
        label = res.get("strategy_name") or name
        base_date = s.index[0] - pd.offsets.MonthEnd(1)
        curve = (1.0 + s).cumprod()
        curves[label] = pd.concat([pd.Series([1.0], index=[base_date]), curve])
        start_dates[label] = str(s.index[0].date())

    if not curves:
        return {"strategies": [], "points": [], "start_dates": {}}

    all_dates = sorted(set().union(*[set(c.index) for c in curves.values()]))
    strategies = sorted(curves.keys())
    points: list[dict] = []
    for d in all_dates:
        row: dict = {"date": str(d.date())}
        for label in strategies:
            v = curves[label].get(d)
            row[label] = None if v is None or pd.isna(v) else round(float(v), 4)
        points.append(row)
    return {"strategies": strategies, "points": points,
            "start_dates": start_dates}


# ── 2. Rolling correlation ────────────────────────────────────────────────────

def rolling_correlation(
    equity: pd.Series,
    ig: pd.Series,
    hy: pd.Series,
    window: int = 12,
) -> dict:
    """
    12-month rolling correlation of equity vs IG and equity vs HY, plus the
    pre- and post-2022 averages of each pair. The 2022 break is where the
    equity-bond diversification benefit broke down.
    """
    df = pd.DataFrame({"equity": equity, "ig": ig, "hy": hy}).dropna()
    roll_ig = df["equity"].rolling(window).corr(df["ig"])
    roll_hy = df["equity"].rolling(window).corr(df["hy"])

    points: list[dict] = []
    for date in df.index:
        ci = roll_ig.get(date)
        ch = roll_hy.get(date)
        points.append({
            "date":      str(date.date()),
            "equity_ig": None if ci is None or pd.isna(ci) else round(float(ci), 4),
            "equity_hy": None if ch is None or pd.isna(ch) else round(float(ch), 4),
        })

    pre = df.index < REGIME_BREAK
    post = df.index >= REGIME_BREAK

    def _avg(roll: pd.Series, mask) -> float | None:
        sel = roll[mask].dropna()
        return round(float(sel.mean()), 4) if len(sel) else None

    return {
        "window_months": window,
        "regime_break":  str(REGIME_BREAK.date()),
        "points":        points,
        "pre_2022":  {"equity_ig": _avg(roll_ig, pre),  "equity_hy": _avg(roll_hy, pre)},
        "post_2022": {"equity_ig": _avg(roll_ig, post), "equity_hy": _avg(roll_hy, post)},
    }


# ── 3. Regime-conditional performance ─────────────────────────────────────────

def _safe_sharpe(r: pd.Series, rf: pd.Series | None) -> float | None:
    """_sharpe wrapper that converts NaN to None instead of letting it
    leak into the cached row.

    _sharpe's mean/std arithmetic can produce NaN when the sub-period
    series is all-NaN — pandas' mean()/std() default skipna=True, but
    a fully-NaN series still returns NaN. round(nan, 4) is nan; nan
    serialised to JSONB causes the validator to mark the row invalid
    with "pre_2022_sharpe_unexpectedly_null" because months stays
    >= 2 while sharpe lands as null. Returning None explicitly when
    the result is non-finite preserves the diagnostic — the validator
    still flags the row as incomplete, but the failure mode is
    legible (the upstream couldn't compute a Sharpe) rather than
    cryptic (NaN drift through JSON).
    """
    if len(r) < 2:
        return None
    value = _sharpe(r, rf)
    if not math.isfinite(value):
        return None
    return round(value, 4)


def _safe_cagr(r: pd.Series) -> float | None:
    """_cagr companion to _safe_sharpe — guards against NaN drift the
    same way (a NaN cagr would corrupt the cached row too)."""
    if len(r) == 0:
        return None
    value = _cagr(r)
    if not math.isfinite(value):
        return None
    return round(value, 4)


def regime_conditional_performance(
    strategy_results: dict[str, dict],
    rf: pd.Series | None,
) -> list[dict]:
    """
    Splits every strategy's monthly returns at the 2022 regime break and
    reports Sharpe + CAGR for each sub-period. Sorted by post-2022 Sharpe
    descending — this is the central finding table: which strategies held
    up once equity-bond diversification stopped working.

    Sharpe / CAGR go through _safe_sharpe / _safe_cagr so a NaN-producing
    edge case (a sub-period that is entirely NaN after rf-alignment, or
    a degenerate cumulative product) falls back to None — the validator
    can then flag the row cleanly rather than swallowing NaN into JSONB.
    """
    rows: list[dict] = []
    for name, res in strategy_results.items():
        series = _pairs_to_series(res.get("monthly_returns") or [])
        if series.empty:
            continue
        # Drop NaN BEFORE the regime split so a strategy whose backtester
        # emitted NaN early-month markers (lookback-window stubs) doesn't
        # poison the Sharpe / CAGR arithmetic. _pairs_to_series already
        # casts every value via float(), so a NaN here was emitted as
        # an explicit float('nan') by the producer.
        series = series.dropna()
        if series.empty:
            continue
        pre = series[series.index < REGIME_BREAK]
        post = series[series.index >= REGIME_BREAK]
        rows.append({
            "strategy":         res.get("strategy_name") or name,
            "pre_2022_sharpe":  _safe_sharpe(pre, rf),
            "post_2022_sharpe": _safe_sharpe(post, rf),
            "pre_2022_cagr":    _safe_cagr(pre),
            "post_2022_cagr":   _safe_cagr(post),
            "pre_2022_months":  int(len(pre)),
            "post_2022_months": int(len(post)),
        })
    rows.sort(key=lambda r: (r["post_2022_sharpe"] is not None, r["post_2022_sharpe"] or 0.0),
              reverse=True)
    return rows


# ── 4. Drawdown comparison ────────────────────────────────────────────────────

def drawdown_comparison(strategy_results: dict[str, dict]) -> list[dict]:
    """
    Max drawdown and recovery period (months to a new equity high) for every
    strategy. Sorted by max drawdown ascending — the deepest loss first, so
    the worst-case ranking is the first thing the reader sees.
    """
    rows: list[dict] = []
    for name, res in strategy_results.items():
        series = _pairs_to_series(res.get("monthly_returns") or [])
        if series.empty:
            continue
        rows.append({
            "strategy":         res.get("strategy_name") or name,
            "max_drawdown":     round(_max_drawdown(series), 4),
            "recovery_months":  _recovery_months(series),
        })
    rows.sort(key=lambda r: r["max_drawdown"])
    return rows


# ── 6. Fama-French factor loadings ────────────────────────────────────────────

def factor_loadings(
    strategy_results: dict[str, dict],
    ff_factors: list[dict],
) -> list[dict]:
    """
    OLS regression of each strategy's monthly excess return on the
    Carhart (1997) four factors: MKT-RF, SMB, HML and MOM (momentum).

    MOM is nullable in ff_factors_monthly — the earliest months predate
    the momentum-factor backfill. Rows with no MOM are dropped per
    strategy, so a strategy whose history lies entirely before the
    backfill falls back to a three-factor regression; `model` records
    which form was used.

    Returns per strategy: factor betas, annualised alpha, R², and a
    significant flag per coefficient (p < 0.05).
    """
    if not ff_factors:
        return []

    # ff values are published as percent — convert to decimal once.
    ff = pd.DataFrame(ff_factors)
    if "yyyymm" not in ff.columns or ff.empty:
        return []
    ff = ff.set_index("yyyymm")
    for col in ("mkt_rf", "smb", "hml", "mom", "rf"):
        if col in ff.columns:
            ff[col] = pd.to_numeric(ff[col], errors="coerce") / 100.0

    try:
        import statsmodels.api as sm
    except ImportError:  # pragma: no cover
        return []

    rows: list[dict] = []
    for name, res in strategy_results.items():
        series = _pairs_to_series(res.get("monthly_returns") or [])
        if len(series) < 12:
            continue
        # Key the strategy series by yyyymm so it aligns with ff's integer key.
        s_ym = pd.Series(
            series.to_numpy(),
            index=[d.year * 100 + d.month for d in series.index],
        )
        joined = pd.concat([s_ym.rename("ret"), ff], axis=1, join="inner")
        # MOM present and non-null on enough rows → four-factor; else fall back.
        with_mom = joined.dropna(subset=["ret", "mkt_rf", "smb", "hml",
                                         "mom", "rf"]) \
            if "mom" in joined.columns else joined.iloc[0:0]
        if len(with_mom) >= 12:
            fit_df, factors, model_label = with_mom, \
                ["mkt_rf", "smb", "hml", "mom"], "carhart_4factor"
        else:
            fit_df = joined.dropna(subset=["ret", "mkt_rf", "smb", "hml", "rf"])
            factors, model_label = ["mkt_rf", "smb", "hml"], "ff_3factor"
        if len(fit_df) < 12:
            continue

        excess = fit_df["ret"] - fit_df["rf"]
        x = sm.add_constant(fit_df[factors])
        model = sm.OLS(excess, x).fit()

        params = model.params
        pvals = model.pvalues
        # 95% confidence intervals for every coefficient. The
        # factor_loadings chart in the canvas editor renders each beta
        # as a horizontal bar with error bars drawn from these CIs —
        # statsmodels exposes them on the fitted OLS directly so the
        # analytics layer remains the single source of truth.
        ci = model.conf_int(alpha=0.05)
        row: dict = {
            "strategy":   res.get("strategy_name") or name,
            "model":      model_label,
            "alpha_annualized": round(float(params["const"]) * _ANN, 4),
            "alpha_significant": bool(pvals["const"] < 0.05),
            "alpha_lo": round(float(ci.loc["const", 0]) * _ANN, 4),
            "alpha_hi": round(float(ci.loc["const", 1]) * _ANN, 4),
            "mkt_rf":     round(float(params["mkt_rf"]), 4),
            "mkt_rf_significant": bool(pvals["mkt_rf"] < 0.05),
            "mkt_rf_lo": round(float(ci.loc["mkt_rf", 0]), 4),
            "mkt_rf_hi": round(float(ci.loc["mkt_rf", 1]), 4),
            "smb":        round(float(params["smb"]), 4),
            "smb_significant": bool(pvals["smb"] < 0.05),
            "smb_lo": round(float(ci.loc["smb", 0]), 4),
            "smb_hi": round(float(ci.loc["smb", 1]), 4),
            "hml":        round(float(params["hml"]), 4),
            "hml_significant": bool(pvals["hml"] < 0.05),
            "hml_lo": round(float(ci.loc["hml", 0]), 4),
            "hml_hi": round(float(ci.loc["hml", 1]), 4),
            "r_squared":  round(float(model.rsquared), 4),
            "n_months":   int(len(fit_df)),
        }
        if "mom" in factors:
            row["mom"] = round(float(params["mom"]), 4)
            row["mom_significant"] = bool(pvals["mom"] < 0.05)
            row["mom_lo"] = round(float(ci.loc["mom", 0]), 4)
            row["mom_hi"] = round(float(ci.loc["mom", 1]), 4)
        else:
            row["mom"] = None
            row["mom_significant"] = False
            row["mom_lo"] = None
            row["mom_hi"] = None
        rows.append(row)
    rows.sort(key=lambda r: r["strategy"])
    return rows
