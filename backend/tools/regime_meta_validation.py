"""tools/regime_meta_validation.py — Layer 3 of the Regime-Conditional
Meta-Portfolio Optimizer: OUT-OF-SAMPLE VALIDATION.

The Layer 2 blends are fit on the whole history. The faculty question
is the only one that matters for an investment recommendation: do those
regime-conditional weights GENERALISE, or are they fit to the past? This
layer answers it with a strict train/test split:

    TRAIN  the regime-conditional mean-variance blends on the PRE-split
           window only (default split 2022-01-01). These blends are
           frozen — the post-split returns never touch the optimizer.

    TEST   apply the frozen blends to the POST-split window. Each test
           month is allocated by that month's regime posterior:
               w_t = Σ_r P(r | month t) · w_r^train
           and the blend's realised return is w_t · x_t. The Sharpe of
           that out-of-sample return stream is the headline number.

    COMPARE the out-of-sample regime-conditional Sharpe against three
            baselines over the SAME test window, computed the SAME way:
              - equal-weight blend (1/N across the strategies)
              - the benchmark (100% S&P 500)
              - Regime Switching alone (the best single dynamic strategy)

WHAT IS AND IS NOT OUT OF SAMPLE

The mean-variance BLEND WEIGHTS — the thing at risk of overfitting — are
trained strictly on the pre-split window. The HMM regime posteriors are
supplied by the caller from a full-history fit (regime detection is an
unsupervised, slow-moving state estimate, and Layer 1 does not expose a
frozen model to re-score new observations). So this is an honest test of
whether the OPTIMIZER generalises, with the regime signal held fixed.
That scope is disclosed in the output ("hmm_fit": "full_history") so the
limitation is never hidden — it is exactly the kind of material caveat
the four-component recommendation structure requires.

A baseline that beats the regime-conditional blend out of sample is a
real finding, not a failure to suppress: 1/N is famously hard to beat
(DeMiguel, Garlappi & Uppal, 2009). The function reports the numbers
plainly and lets them speak.

Pure given (strategy_results, hmm_result, split_date): no DB, no HMM
fit, no cvxpy beyond what Layer 2 already needs. Fully testable with
synthetic posteriors.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import structlog

from config import RISK_AVERSION
from tools.regime_meta_optimizer import (
    _META_MAX_WEIGHT,
    REGIMES,
    align_regime_posteriors,
    blends_from_matrix,
    build_strategy_matrix,
)

log = structlog.get_logger(__name__)

# The reference baselines the OOS Sharpe is judged against. The benchmark
# and the single best dynamic strategy are pulled by id; the equal-weight
# blend is computed across the matrix.
_BENCHMARK_ID = "BENCHMARK"
_REGIME_SWITCHING_ID = "REGIME_SWITCHING"


# ── small statistics helpers ────────────────────────────────────────────────


def _annualised_sharpe(
    returns: np.ndarray,
    rf: np.ndarray | float = 0.0,
    annualization: int = 12,
) -> float | None:
    """Annualised Sharpe of a monthly return stream. Returns None when
    the series is too short or has zero variance (so the caller renders
    a dash rather than an inf)."""
    r = np.asarray(returns, dtype=float)
    if r.size < 2:
        return None
    rf_arr = (np.full(r.shape, float(rf))
              if np.isscalar(rf) else np.asarray(rf, dtype=float))
    if rf_arr.shape != r.shape:
        rf_arr = np.zeros_like(r)
    excess = r - rf_arr
    sd = excess.std(ddof=1)
    if not np.isfinite(sd) or sd <= 0:
        return None
    return float((excess.mean() / sd) * np.sqrt(annualization))


def _cagr(returns: np.ndarray, annualization: int = 12) -> float | None:
    r = np.asarray(returns, dtype=float)
    if r.size == 0:
        return None
    growth = float(np.prod(1.0 + r))
    years = r.size / annualization
    if growth <= 0 or years <= 0:
        return None
    return float(growth ** (1.0 / years) - 1.0)


def _stat_block(
    returns: np.ndarray,
    rf: np.ndarray | float,
    annualization: int = 12,
) -> dict:
    r = np.asarray(returns, dtype=float)
    sharpe = _annualised_sharpe(r, rf, annualization)
    vol = (float(r.std(ddof=1) * np.sqrt(annualization))
           if r.size >= 2 else None)
    return {
        "sharpe": None if sharpe is None else round(sharpe, 4),
        "cagr": (lambda c: None if c is None else round(c, 4))(_cagr(r, annualization)),
        "mean_ann": round(float(r.mean() * annualization), 6) if r.size else None,
        "vol_ann": None if vol is None else round(vol, 6),
        "n_months": int(r.size),
    }


def _rf_for_dates(
    risk_free: dict | float | None,
    dates: pd.DatetimeIndex,
) -> np.ndarray:
    """Build a monthly risk-free array aligned to `dates`. risk_free may
    be a {iso_date: monthly_rate} mapping, a scalar monthly rate, or
    None (→ zero). A mapping is reindexed and forward-filled; anything
    missing is zero (the conservative choice — it never inflates the
    excess return)."""
    if risk_free is None:
        return np.zeros(len(dates))
    if np.isscalar(risk_free):
        return np.full(len(dates), float(risk_free))
    try:
        idx = pd.to_datetime(list(risk_free.keys()))
        s = pd.Series(list(risk_free.values()), index=idx).sort_index()
        aligned = s.reindex(s.index.union(dates)).ffill().reindex(dates)
        return aligned.fillna(0.0).to_numpy(dtype=float)
    except (TypeError, ValueError):
        return np.zeros(len(dates))


def _reference_returns(
    name: str,
    names: list[str],
    matrix: np.ndarray,
    test_mask: np.ndarray,
    strategy_results: dict[str, dict],
    test_dates: pd.DatetimeIndex,
) -> np.ndarray | None:
    """Test-window returns for a named reference strategy. Prefer the
    matrix column (guaranteed aligned to the test dates) and fall back
    to the raw strategy_results series reindexed onto the test dates
    when the strategy was excluded from the matrix."""
    if name in names:
        return matrix[test_mask, names.index(name)]
    rows = ((strategy_results or {}).get(name) or {}).get("monthly_returns")
    if not rows:
        return None
    try:
        idx = pd.to_datetime([r[0] for r in rows])
        vals = [float(r[1]) for r in rows]
    except (TypeError, ValueError, IndexError):
        return None
    s = pd.Series(vals, index=idx).reindex(test_dates)
    if s.isna().any():
        return None
    return s.to_numpy(dtype=float)


# ── Layer 3 entry point ─────────────────────────────────────────────────────


def out_of_sample_validation(
    strategy_results: dict[str, dict],
    hmm_result: dict,
    *,
    split_date: str = "2022-01-01",
    exclude: tuple[str, ...] = (),
    risk_aversion: float = RISK_AVERSION,
    max_weight: float = _META_MAX_WEIGHT,
    min_effective_n: float | None = None,
    risk_free: dict | float | None = None,
    annualization: int = 12,
    return_series: bool = False,
) -> dict:
    """Train regime-conditional blends on the pre-split window, freeze
    them, apply to the post-split window, and compare the out-of-sample
    Sharpe against the equal-weight blend, the benchmark, and Regime
    Switching alone.

    return_series — when True, also returns the per-month blend return
    stream and the test dates (`blend_monthly`, `test_dates`) so a caller
    can build the cumulative OOS path for a chart without re-running the
    optimization.

    Returns:
      {
        "split_date": str,
        "n_train_months": int,
        "n_test_months": int,
        "names": [...],
        "hmm_fit": "full_history",
        "train_blends": {regime: {name: weight}},   # the frozen blends
        "train_effective_n": {regime: float},
        "train_fallback": [regime, ...],
        "risk_free": "zero" | "supplied",
        "oos": {
          "regime_conditional": {sharpe, cagr, mean_ann, vol_ann, n_months},
          "equal_weight":       {...},
          "benchmark":          {...},
          "regime_switching":   {...},
        },
        "verdict": {
          "beats_equal_weight": bool, "beats_benchmark": bool,
          "beats_regime_switching": bool, "summary": str,
        },
      }
    or {"error": "..."}.
    """
    names, dates, matrix = build_strategy_matrix(
        strategy_results, exclude=exclude)
    if not names:
        return {"error": "insufficient_strategy_return_data"}
    posteriors = align_regime_posteriors(dates, hmm_result)
    if not posteriors:
        return {"error": "no_regime_posteriors"}

    try:
        split = pd.Timestamp(split_date)
    except (TypeError, ValueError):
        return {"error": "bad_split_date"}

    train_mask = np.asarray(dates < split)
    test_mask = np.asarray(dates >= split)
    n_train = int(train_mask.sum())
    n_test = int(test_mask.sum())
    # A blend needs a covariance (>= 2 rows) on train, and a test window
    # to score on. Both must be non-trivial.
    if n_train < 2 or n_test < 2:
        return {"error": "insufficient_train_or_test_window",
                "n_train_months": n_train, "n_test_months": n_test}

    n = len(names)
    train_matrix = matrix[train_mask]
    train_post = {r: p[train_mask] for r, p in posteriors.items()}
    test_matrix = matrix[test_mask]
    test_post = {r: p[test_mask] for r, p in posteriors.items()}
    test_dates = dates[test_mask]

    # TRAIN: frozen blends from the pre-split window, same code as
    # production (blends_from_matrix).
    train_blends, train_eff, train_fb = blends_from_matrix(
        names, train_matrix, train_post,
        risk_aversion=risk_aversion, max_weight=max_weight,
        min_effective_n=min_effective_n)
    if not train_blends:
        return {"error": "no_train_blends_computed"}

    # Frozen blends as name-aligned vectors.
    wvecs = {
        r: np.array([blend.get(nm, 0.0) for nm in names], dtype=float)
        for r, blend in train_blends.items()
    }

    # TEST: month-by-month, allocate by that month's posterior over the
    # regimes that have a frozen blend, then realise the blend return.
    blend_ret = np.zeros(n_test)
    weight_path: list[np.ndarray] = []
    for t in range(n_test):
        num = np.zeros(n)
        denom = 0.0
        for r, wv in wvecs.items():
            p = max(float(test_post.get(r, np.zeros(n_test))[t]), 0.0)
            num += p * wv
            denom += p
        w_t = (num / denom) if denom > 0 else np.full(n, 1.0 / n)
        weight_path.append(w_t)
        blend_ret[t] = float(w_t @ test_matrix[t])

    rf_test = _rf_for_dates(risk_free, test_dates)

    # Baselines over the SAME test window, SAME Sharpe convention.
    ew_ret = test_matrix.mean(axis=1)
    bench_ret = _reference_returns(
        _BENCHMARK_ID, names, matrix, test_mask, strategy_results, test_dates)
    rs_ret = _reference_returns(
        _REGIME_SWITCHING_ID, names, matrix, test_mask, strategy_results,
        test_dates)

    oos = {
        "regime_conditional": _stat_block(blend_ret, rf_test, annualization),
        "equal_weight": _stat_block(ew_ret, rf_test, annualization),
    }
    if bench_ret is not None:
        oos["benchmark"] = _stat_block(bench_ret, rf_test, annualization)
    if rs_ret is not None:
        oos["regime_switching"] = _stat_block(rs_ret, rf_test, annualization)

    rc_sharpe = oos["regime_conditional"]["sharpe"]

    def _beats(key: str) -> bool | None:
        other = oos.get(key, {}).get("sharpe")
        if rc_sharpe is None or other is None:
            return None
        return rc_sharpe > other

    verdict = {
        "beats_equal_weight": _beats("equal_weight"),
        "beats_benchmark": _beats("benchmark"),
        "beats_regime_switching": _beats("regime_switching"),
    }
    beaten = [k for k in ("equal_weight", "benchmark", "regime_switching")
              if verdict.get(f"beats_{k}") is True]
    lost = [k for k in ("equal_weight", "benchmark", "regime_switching")
            if verdict.get(f"beats_{k}") is False]
    if rc_sharpe is None:
        verdict["summary"] = "Out-of-sample Sharpe undefined (degenerate test window)."
    else:
        verdict["summary"] = (
            f"Out-of-sample regime-conditional Sharpe {rc_sharpe:.4f} over "
            f"{n_test} test months. Beats: "
            f"{', '.join(beaten) if beaten else 'none'}. Trails: "
            f"{', '.join(lost) if lost else 'none'}.")

    result = {
        "split_date": str(split.date()),
        "n_train_months": n_train,
        "n_test_months": n_test,
        "names": names,
        "hmm_fit": "full_history",
        "train_blends": train_blends,
        "train_effective_n": train_eff,
        "train_fallback": train_fb,
        "risk_free": "supplied" if risk_free is not None else "zero",
        "oos": oos,
        "verdict": verdict,
    }
    if return_series:
        result["test_dates"] = [str(d.date()) for d in test_dates]
        result["blend_monthly"] = [round(float(x), 8) for x in blend_ret]
        # Per-month blend weight vector ({name: weight}) — lets a caller
        # count rebalancing events (a material month-over-month weight
        # shift) for the transaction-cost sensitivity analysis.
        result["blend_weights_monthly"] = [
            {names[i]: round(float(w[i]), 6) for i in range(n)}
            for w in weight_path
        ]
    return result


# ── Transaction-cost sensitivity (pure; testable without an HMM) ─────────────

def count_material_rebalances(
    blend_weights_monthly: list[dict],
    threshold: float = 0.02,
) -> int:
    """A rebalancing event is a month whose blend weights shifted by more
    than `threshold` (2% by default) in ANY single strategy versus the
    prior month. The first month seeds the position and is not counted."""
    n = 0
    prev: dict | None = None
    for w in (blend_weights_monthly or []):
        if prev is not None:
            keys = set(w) | set(prev)
            if any(abs(float(w.get(k, 0.0)) - float(prev.get(k, 0.0))) > threshold
                   for k in keys):
                n += 1
        prev = w
    return n


def compute_cost_sensitivity(
    *,
    blend_weights_monthly: list[dict],
    gross_sharpe: float | None,
    oos_vol: float | None,
    benchmark_sharpe: float | None,
    n_test_months: int,
    cost_bps: tuple[int, ...] = (10, 15, 20),
    threshold: float = 0.02,
) -> dict:
    """Transaction-cost sensitivity of the regime-conditional blend over the
    out-of-sample window. Pure given its inputs.

    For each cost assumption: total cost drag = n_rebalances * bps * 1e-4
    (a fraction over the whole window); annualised drag = total / n_years;
    net Sharpe = gross Sharpe minus the annualised drag in Sharpe units
    (annualised_drag / oos_vol) — which is exactly (gross_excess - drag) /
    oos_vol, so the net figure stays consistent with the displayed gross
    Sharpe. vs-benchmark is net_sharpe / benchmark_sharpe - 1."""
    n_rebalances = count_material_rebalances(blend_weights_monthly, threshold)
    n_years = (n_test_months / 12.0) if n_test_months else None
    scenarios: list[dict] = []
    for bps in cost_bps:
        total_drag = n_rebalances * bps * 0.0001
        ann_drag = (total_drag / n_years) if n_years else None
        net_sharpe = (
            round(gross_sharpe - ann_drag / oos_vol, 4)
            if (gross_sharpe is not None and ann_drag is not None and oos_vol)
            else None)
        vs_bench = (
            round(net_sharpe / benchmark_sharpe - 1.0, 4)
            if (net_sharpe is not None and benchmark_sharpe)
            else None)
        scenarios.append({
            "bps": bps,
            "total_cost_drag": round(total_drag, 6),
            "net_sharpe": net_sharpe,
            "vs_benchmark_pct": vs_bench,
        })
    return {
        "n_rebalances": n_rebalances,
        "material_threshold": threshold,
        "gross_sharpe": gross_sharpe,
        "oos_vol": oos_vol,
        "benchmark_sharpe": benchmark_sharpe,
        "n_test_months": n_test_months,
        "cost_bps": list(cost_bps),
        "scenarios": scenarios,
    }


# ── data_hash-cached refresh (warm pipeline) + read ─────────────────────────

_COST_METRIC_KIND = "oos_cost_sensitivity"


async def refresh_oos_cost_sensitivity(data_hash: str) -> bool:
    """Render-side: fit the HMM on the live equity series, run the OOS
    validation with the per-month weight path, count material rebalances,
    compute the 10/15/20 bps transaction-cost sensitivity, and cache it
    under metric_kind 'oos_cost_sensitivity'. Fired by the same warm
    pipeline as the forward projection; the HMM fit reuses the detector's
    in-process cache (same equity series), so it adds no extra Baum-Welch
    run. Fail-open — any failure leaves the previous cached value."""
    try:
        from tools.cache import get_latest_strategy_cache, get_monthly_returns
        from tools.precomputed_analytics import set_metric
        from tools.regime_detector import fit_hmm_historical
    except Exception as exc:  # noqa: BLE001
        log.warning("oos_cost_sensitivity_imports_unavailable", error=str(exc))
        return False
    try:
        sr = await get_latest_strategy_cache()
        monthly = await get_monthly_returns()
        if not sr or not monthly or not monthly.get("equity") \
                or not monthly.get("dates"):
            return False
        idx = pd.to_datetime(monthly["dates"])
        equity = pd.Series(monthly["equity"], index=idx).sort_index()
        hmm = fit_hmm_historical(equity)
        if not hmm or hmm.get("error"):
            log.warning("oos_cost_sensitivity_hmm_failed",
                        error=(hmm or {}).get("error"))
            return False
        # Risk-free as a {iso_date: monthly_rate} map so the OOS Sharpe
        # matches the headline (DTB3-based) gross Sharpe.
        rf_map = None
        dates = monthly.get("dates") or []
        rf = monthly.get("rf") or []
        if dates and rf and len(dates) == len(rf):
            rf_map = {str(d): float(v) for d, v in zip(dates, rf)
                      if v is not None}
        val = out_of_sample_validation(
            sr, hmm, return_series=True, risk_free=rf_map)
        if val.get("error"):
            log.warning("oos_cost_sensitivity_validation_failed",
                        error=val["error"])
            return False
        oos = val.get("oos", {})
        rc = oos.get("regime_conditional", {})
        bench = oos.get("benchmark", {})
        result = compute_cost_sensitivity(
            blend_weights_monthly=val.get("blend_weights_monthly", []),
            gross_sharpe=rc.get("sharpe"),
            oos_vol=rc.get("vol_ann"),
            benchmark_sharpe=bench.get("sharpe"),
            n_test_months=val.get("n_test_months", 0),
        )
        await set_metric(data_hash or "", _COST_METRIC_KIND, result,
                         source="oos_cost_sensitivity")
        log.info("oos_cost_sensitivity_cached",
                 n_rebalances=result["n_rebalances"])
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("oos_cost_sensitivity_refresh_failed", error=str(exc))
        return False


async def get_cached_cost_sensitivity() -> dict | None:
    """The latest cached OOS transaction-cost sensitivity for the read
    endpoint. Fail-open to None so the banner hides the section before the
    first warm computes one."""
    try:
        from tools.precomputed_analytics import get_latest_metric
        return await get_latest_metric(_COST_METRIC_KIND)
    except Exception as exc:  # noqa: BLE001
        log.warning("oos_cost_sensitivity_read_error", error=str(exc))
        return None
