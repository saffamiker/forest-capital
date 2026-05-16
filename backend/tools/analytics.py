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

import numpy as np
import pandas as pd

# The 2022 Fed hiking cycle is when the equity-bond correlation flipped
# positive — the project's central finding. Pre/post-2022 splits and the
# rolling-correlation regime marker all key off this date.
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
    rows: list[dict] = []
    for name, r in asset_series.items():
        r = r.dropna()
        rows.append({
            "asset":          name,
            "cagr":           round(_cagr(r), 4),
            "ann_volatility": round(_ann_vol(r), 4),
            "sharpe_ratio":   round(_sharpe(r, rf), 4),
            "max_drawdown":   round(_max_drawdown(r), 4),
            "skewness":       round(float(r.skew()), 4) if len(r) >= 3 else 0.0,
            "n_months":       int(len(r)),
        })
    return rows


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

def regime_conditional_performance(
    strategy_results: dict[str, dict],
    rf: pd.Series | None,
) -> list[dict]:
    """
    Splits every strategy's monthly returns at the 2022 regime break and
    reports Sharpe + CAGR for each sub-period. Sorted by post-2022 Sharpe
    descending — this is the central finding table: which strategies held
    up once equity-bond diversification stopped working.
    """
    rows: list[dict] = []
    for name, res in strategy_results.items():
        series = _pairs_to_series(res.get("monthly_returns") or [])
        if series.empty:
            continue
        pre = series[series.index < REGIME_BREAK]
        post = series[series.index >= REGIME_BREAK]
        rows.append({
            "strategy":         res.get("strategy_name") or name,
            "pre_2022_sharpe":  round(_sharpe(pre, rf), 4) if len(pre) >= 2 else None,
            "post_2022_sharpe": round(_sharpe(post, rf), 4) if len(post) >= 2 else None,
            "pre_2022_cagr":    round(_cagr(pre), 4) if len(pre) else None,
            "post_2022_cagr":   round(_cagr(post), 4) if len(post) else None,
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
    OLS regression of each strategy's monthly excess return on the Fama-French
    factors. ff_factors_monthly stores the three-factor model (MKT-RF, SMB,
    HML) — momentum is not in the dataset, so this is a three-factor (not
    Carhart four-factor) regression; the table is labelled accordingly.

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
    for col in ("mkt_rf", "smb", "hml", "rf"):
        if col in ff.columns:
            ff[col] = ff[col].astype(float) / 100.0

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
        joined = pd.concat([s_ym.rename("ret"), ff], axis=1, join="inner").dropna()
        if len(joined) < 12:
            continue

        excess = joined["ret"] - joined["rf"]
        x = sm.add_constant(joined[["mkt_rf", "smb", "hml"]])
        model = sm.OLS(excess, x).fit()

        params = model.params
        pvals = model.pvalues
        rows.append({
            "strategy":   res.get("strategy_name") or name,
            "alpha_annualized": round(float(params["const"]) * _ANN, 4),
            "alpha_significant": bool(pvals["const"] < 0.05),
            "mkt_rf":     round(float(params["mkt_rf"]), 4),
            "mkt_rf_significant": bool(pvals["mkt_rf"] < 0.05),
            "smb":        round(float(params["smb"]), 4),
            "smb_significant": bool(pvals["smb"] < 0.05),
            "hml":        round(float(params["hml"]), 4),
            "hml_significant": bool(pvals["hml"] < 0.05),
            "r_squared":  round(float(model.rsquared), 4),
            "n_months":   int(len(joined)),
        })
    rows.sort(key=lambda r: r["strategy"])
    return rows
