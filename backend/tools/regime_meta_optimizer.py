"""tools/regime_meta_optimizer.py — Layer 2 of the Regime-Conditional
Meta-Portfolio Optimizer.

The platform's ten strategies are themselves treated as "assets" and a
mean-variance optimization is run at the META-PORTFOLIO level, once per
regime state. The result is three regime-specific blends of the ten
strategies:

    w_BULL, w_BEAR, w_TRANSITION

and a live allocation that mixes them by the current HMM posterior:

    w = P(BULL)·w_BULL + P(BEAR)·w_BEAR + P(TRANSITION)·w_TRANSITION

WHY PROBABILITY-WEIGHTED MOMENTS (not hard regime subsets)

Layer 1 (regime_detector.fit_hmm_historical) exposes the HMM posterior
probability vector P(regime | month) for every month as a CONTINUOUS
signal, not just a hard BULL/BEAR/TRANSITION label. Layer 2 uses that
signal directly: the regime-conditional mean and covariance for regime
r are computed as responsibility-weighted moments, every month
contributing in proportion to its posterior membership in r:

    p_t      = P(regime = r | month t)                (the posterior)
    mu_r     = Σ_t p_t · x_t            / Σ_t p_t
    cov_r    = Σ_t p_t · (x_t-mu_r)(x_t-mu_r)ᵀ / Σ_t p_t

x_t is the vector of the ten strategies' returns in month t. This is
exactly the M-step moment estimate of a Gaussian mixture / HMM: it is
the statistically natural way to read "the covariance that prevails in
the bear regime" off the posteriors, and it avoids the small-sample
fragility of hard-subsetting (a regime with 18 hard-labelled months
would give a singular 10x10 covariance; the soft estimate pools the
full history, weighted).

The optimizer is the same box-constrained, long-only, fully-invested
mean-variance QP the 3-asset optimizer uses (cvxpy CLARABEL), only the
asset set is the ten strategies and the moments are regime-conditional.

Fail-open throughout: a missing HMM result, cvxpy absence, a non-finite
moment, or a degenerate regime falls back to equal weight with a
diagnostic log line. The caller always receives a complete blend.

Layers 3 (out-of-sample validation) and 4 (forward Monte Carlo
confidence bands) build on the functions here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import structlog

from config import MAX_WEIGHT, MIN_WEIGHT, RISK_AVERSION

log = structlog.get_logger(__name__)

# The three regime labels Layer 1 emits. TRANSITION is present only when
# the HMM was fit with n_states >= 3 (the project default).
REGIMES = ("BULL", "BEAR", "TRANSITION")

# Per-strategy cap at the meta level. 0.40 (the project's MAX_WEIGHT)
# is a DIVERSIFICATION CONSTRAINT consistent with an institutional
# mandate: no single strategy may exceed 40% of the meta-portfolio,
# regardless of how strongly a regime favours it. The cap forces a
# minimum of three strategies into any fully-invested blend and stops
# the optimizer from collapsing a ten-strategy meta-portfolio onto a
# single highest-Sharpe strategy. The cap is echoed in the
# compute_regime_blends output (max_weight + box_constraint_note) so
# the constraint is auditable, and it is a parameter so a sensitivity
# sweep (e.g. 0.30 / 0.40 / 0.50) can probe how binding it is.
_META_MAX_WEIGHT = MAX_WEIGHT
_META_MIN_WEIGHT = MIN_WEIGHT


def _box_constraint_note(max_weight: float) -> str:
    """Human-readable justification for the per-strategy cap, echoed in
    the blend output so a reviewer sees the mandate behind the number."""
    return (
        f"Maximum {max_weight:.0%} in any single strategy: a "
        f"diversification constraint consistent with an institutional "
        f"mandate.")

try:
    import cvxpy as cp
    _CVXPY_AVAILABLE = True
except ImportError:  # pragma: no cover - environment dependent
    _CVXPY_AVAILABLE = False


# ── Strategy return matrix assembly ─────────────────────────────────────────


def build_strategy_matrix(
    strategy_results: dict[str, dict],
    *,
    exclude: tuple[str, ...] = (),
) -> tuple[list[str], pd.DatetimeIndex, np.ndarray]:
    """Assemble a (T x N) matrix of monthly returns with the N
    strategies as columns, aligned on their COMMON month-end dates.

    strategy_results is the run_all_strategies() output: a dict keyed
    by strategy id, each value carrying a `monthly_returns` list of
    [iso_date, return_float] pairs.

    Strategies start on different dates (five dynamic strategies begin
    after their lookback window). The matrix uses the INTERSECTION of
    every included strategy's dates so the covariance is computed on a
    common, fully-populated window. Strategies in `exclude` are
    dropped (e.g. pass exclude=("BENCHMARK",) to optimize across the
    nine active strategies only).

    Returns (names, dates, matrix):
      names   — column order, the strategy ids
      dates   — the common DatetimeIndex (rows of the matrix)
      matrix  — float ndarray, shape (len(dates), len(names))
    Returns ([], empty index, empty array) when fewer than two
    strategies have usable return series (a covariance needs >= 2
    assets).
    """
    series_by_name: dict[str, pd.Series] = {}
    for name, res in (strategy_results or {}).items():
        if name in exclude:
            continue
        rows = (res or {}).get("monthly_returns") or []
        if not rows:
            continue
        try:
            idx = pd.to_datetime([r[0] for r in rows])
            vals = [float(r[1]) for r in rows]
        except (TypeError, ValueError, IndexError):
            continue
        s = pd.Series(vals, index=idx).dropna()
        if not s.empty:
            series_by_name[name] = s

    if len(series_by_name) < 2:
        return [], pd.DatetimeIndex([]), np.empty((0, 0))

    # Align on the common date intersection. concat(axis=1) unions the
    # index; dropna() then restricts to months every strategy covers.
    names = sorted(series_by_name.keys())
    frame = pd.concat([series_by_name[n] for n in names], axis=1)
    frame.columns = names
    frame = frame.dropna(how="any").sort_index()
    if frame.shape[0] < 2 or frame.shape[1] < 2:
        return [], pd.DatetimeIndex([]), np.empty((0, 0))
    return names, frame.index, frame.to_numpy(dtype=float)


# ── Regime posterior alignment ──────────────────────────────────────────────


def align_regime_posteriors(
    dates: pd.DatetimeIndex,
    hmm_result: dict,
) -> dict[str, np.ndarray]:
    """Align Layer 1's per-date posteriors onto the matrix `dates`.

    hmm_result is the fit_hmm_historical() return dict carrying
    `dates` (iso strings) and `historical_probs` (label -> list of
    per-date P(regime)). The HMM is fit on the equity return series,
    which may not share every date with the strategy matrix, so we
    reindex each regime's posterior onto `dates`, forward-filling the
    nearest prior posterior (a regime read is a slow-moving state;
    carrying the last known posterior to a missing month is the
    correct nearest-available behaviour).

    Returns {regime_label: ndarray aligned to dates}. A regime with no
    posterior data (e.g. TRANSITION absent in a 2-state fit) is
    omitted from the result. Returns {} when the HMM result carries no
    usable posteriors.
    """
    probs = (hmm_result or {}).get("historical_probs") or {}
    hmm_dates = (hmm_result or {}).get("dates") or []
    if not probs or not hmm_dates or len(dates) == 0:
        return {}
    try:
        hmm_index = pd.to_datetime(hmm_dates)
    except (TypeError, ValueError):
        return {}

    out: dict[str, np.ndarray] = {}
    for label, series_vals in probs.items():
        if not isinstance(series_vals, list) or len(series_vals) != len(
                hmm_index):
            continue
        s = pd.Series(series_vals, index=hmm_index).sort_index()
        # ffill across the union, then restrict to the matrix dates.
        aligned = s.reindex(
            s.index.union(dates)).ffill().reindex(dates)
        # Any leading gap (matrix starts before the HMM) backfills so
        # the optimizer never sees a NaN posterior; 0.0 is the safe
        # fallback (that month simply does not contribute to the
        # regime's moments).
        out[label] = aligned.fillna(0.0).to_numpy(dtype=float)
    return out


# ── Probability-weighted regime-conditional moments ─────────────────────────


def regime_conditional_moments(
    matrix: np.ndarray,
    posterior: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Responsibility-weighted mean + covariance for one regime.

    matrix    — (T x N) strategy returns
    posterior — (T,) per-month P(regime | month), the weights

    Returns (mu, cov, effective_n):
      mu          — (N,) probability-weighted mean per strategy
      cov         — (N x N) probability-weighted covariance
      effective_n — Kish effective sample size of the weighting,
                    (Σp)² / Σp². A low effective_n (< ~ N) flags a
                    regime whose covariance is under-determined; the
                    caller can fall back to equal weight.

    The covariance uses the reliability (frequency-weight) unbiased
    correction factor V1²/(V1²-V2) where V1=Σp, V2=Σp², so it reduces
    to the ordinary 1/(T-1) sample covariance when every weight is 1.
    """
    p = np.asarray(posterior, dtype=float)
    p = np.clip(p, 0.0, None)
    v1 = p.sum()
    if v1 <= 0 or matrix.shape[0] == 0:
        n = matrix.shape[1] if matrix.ndim == 2 else 0
        return np.zeros(n), np.zeros((n, n)), 0.0
    v2 = float((p ** 2).sum())
    w = p / v1                                   # normalised weights
    mu = (w[:, None] * matrix).sum(axis=0)       # (N,)
    centered = matrix - mu                       # (T x N)
    # Weighted covariance: Σ w_t (x_t-mu)(x_t-mu)ᵀ, then unbiased
    # reliability correction. cov = (Wc).T @ c where Wc = w_t * c.
    weighted = centered * w[:, None]
    cov = weighted.T @ centered                  # (N x N), biased
    denom = 1.0 - (v2 / (v1 ** 2))
    if denom > 1e-12:
        cov = cov / denom                        # unbiased correction
    effective_n = (v1 ** 2) / v2 if v2 > 0 else 0.0
    return mu, cov, float(effective_n)


# ── Meta-level mean-variance optimizer ──────────────────────────────────────


def meta_mean_variance(
    mu: np.ndarray,
    cov: np.ndarray,
    *,
    risk_aversion: float = RISK_AVERSION,
    min_weight: float = _META_MIN_WEIGHT,
    max_weight: float = _META_MAX_WEIGHT,
) -> np.ndarray:
    """Long-only, fully-invested mean-variance QP over PRECOMPUTED
    moments (the regime-conditional mu / cov). Maximises
    μᵀw - (λ/2)·wᵀΣw subject to 1ᵀw = 1, w ∈ [min_w, max_w].

    Distinct from optimizer.mean_variance_optimize, which derives
    mu/cov from a returns DataFrame — here the moments are
    regime-conditional and supplied directly, so the optimizer cannot
    recompute them from raw returns.

    Fail-open: cvxpy missing, non-finite moments, an infeasible box
    (max_weight·N < 1), or a solver failure each fall back to equal
    weight with a diagnostic log line.
    """
    n = len(mu)
    if n == 0:
        return np.empty(0)
    if max_weight * n < 1.0 - 1e-9:
        # The box cannot sum to 1 — relax to equal weight rather than
        # hand back an infeasible problem.
        log.warning("meta_mv_infeasible_box", n=n, max_weight=max_weight)
        return _equal_weight(n)
    if not _CVXPY_AVAILABLE:
        log.warning("meta_mv_cvxpy_unavailable", fallback="equal_weight")
        return _equal_weight(n)
    if not (np.all(np.isfinite(mu)) and np.all(np.isfinite(cov))):
        log.warning("meta_mv_nonfinite_moments", fallback="equal_weight")
        return _equal_weight(n)

    w = cp.Variable(n)
    utility = mu @ w - (risk_aversion / 2.0) * cp.quad_form(
        w, cp.psd_wrap(cov))
    constraints = [cp.sum(w) == 1.0, w >= min_weight, w <= max_weight]
    problem = cp.Problem(cp.Maximize(utility), constraints)
    try:
        problem.solve(solver=cp.CLARABEL, verbose=False)
        if problem.status in ("optimal", "optimal_inaccurate") \
                and w.value is not None:
            return _clip_and_normalise(w.value, min_weight, max_weight)
    except Exception as exc:  # noqa: BLE001
        log.warning("meta_mv_solver_failed", error=str(exc),
                    fallback="equal_weight")
    return _equal_weight(n)


def _equal_weight(n: int) -> np.ndarray:
    return np.full(n, 1.0 / n) if n > 0 else np.empty(0)


def _clip_and_normalise(
    w: np.ndarray, min_w: float, max_w: float,
) -> np.ndarray:
    """Clip the solver's weights to the box and renormalise to sum 1.
    CLARABEL can return values a hair outside [min_w, max_w]; clipping
    then renormalising keeps the contract (sum 1, within box) exact to
    floating point."""
    clipped = np.clip(w, min_w, max_w)
    total = clipped.sum()
    if total <= 0:
        return _equal_weight(len(w))
    return clipped / total


# ── Regime blends + live probability-weighted allocation ────────────────────


def compute_regime_blends(
    strategy_results: dict[str, dict],
    hmm_result: dict,
    *,
    exclude: tuple[str, ...] = (),
    risk_aversion: float = RISK_AVERSION,
    max_weight: float = _META_MAX_WEIGHT,
    min_effective_n: float | None = None,
) -> dict:
    """Top-level Layer 2 entry point. Produces one mean-variance blend
    of the strategies per regime.

    Returns:
      {
        "names":   [strategy ids in column order],
        "n_months": int,            # rows in the common matrix
        "blends":  {
            "BULL": {name: weight, ...},
            "BEAR": {name: weight, ...},
            "TRANSITION": {name: weight, ...},   # when present
        },
        "effective_n": {regime: float},  # Kish ESS per regime
        "fallback":   [regime, ...],     # regimes that fell back to EW
        "max_weight": float,             # the per-strategy cap applied
        "box_constraint_note": str,      # plain-English justification
      }
    or {"error": "..."} when the inputs cannot produce a matrix.

    max_weight — the per-strategy diversification cap (institutional
    mandate; see _META_MAX_WEIGHT). Exposed so a sensitivity sweep can
    re-run the blends at 0.30 / 0.40 / 0.50 to probe how binding it is.

    min_effective_n — when a regime's Kish effective sample size is
    below this, its covariance is treated as under-determined and the
    regime falls back to equal weight. Defaults to 2·N (twice the
    number of strategies), a conservative floor for a stable NxN
    covariance.
    """
    names, dates, matrix = build_strategy_matrix(
        strategy_results, exclude=exclude)
    if not names:
        return {"error": "insufficient_strategy_return_data"}

    posteriors = align_regime_posteriors(dates, hmm_result)
    if not posteriors:
        return {"error": "no_regime_posteriors"}

    n = len(names)
    floor = (2.0 * n) if min_effective_n is None else min_effective_n

    blends: dict[str, dict[str, float]] = {}
    effective: dict[str, float] = {}
    fallback: list[str] = []

    for regime in REGIMES:
        post = posteriors.get(regime)
        if post is None:
            continue  # regime absent (e.g. 2-state fit has no TRANSITION)
        mu, cov, ess = regime_conditional_moments(matrix, post)
        effective[regime] = round(ess, 2)
        if ess < floor:
            w = _equal_weight(n)
            fallback.append(regime)
        else:
            w = meta_mean_variance(
                mu, cov, risk_aversion=risk_aversion,
                max_weight=max_weight)
            # meta_mean_variance falls back internally to EW on solver
            # trouble; detect that so the caller knows.
            if np.allclose(w, _equal_weight(n)):
                fallback.append(regime)
        blends[regime] = {names[i]: round(float(w[i]), 6)
                          for i in range(n)}

    if not blends:
        return {"error": "no_regime_blends_computed"}

    return {
        "names": names,
        "n_months": int(len(dates)),
        "blends": blends,
        "effective_n": effective,
        "fallback": fallback,
        "max_weight": max_weight,
        "box_constraint_note": _box_constraint_note(max_weight),
    }


def regime_strategy_diagnostics(
    strategy_results: dict[str, dict],
    hmm_result: dict,
    *,
    exclude: tuple[str, ...] = (),
    annualization: int = 12,
) -> dict:
    """Per-regime, per-strategy moment diagnostics — the evidence that
    separates a GENUINE highest-Sharpe finding from a constraint or
    covariance artifact when one strategy keeps hitting the cap.

    For each regime it reports every strategy's regime-conditional
    annualised mean, volatility and Sharpe (mean·A / vol·√A), the
    Sharpe rank, and the single highest-Sharpe strategy. The caller
    pairs this with the blend weights: if the top-WEIGHTED strategy is
    also the top-SHARPE strategy, the load is a genuine finding (a). If
    a strategy is loaded to the cap while ranking mid-pack on Sharpe,
    mean-variance is loading it for its low covariance with the rest
    (a diversification effect, not necessarily an artifact) — the
    covariance row makes that visible rather than leaving it to
    guesswork (b).

    Sharpe here is a RAW return / vol ratio (no risk-free subtraction)
    — it matches the optimizer's mu, which is raw return, and serves
    only as a within-regime ranking, not a reported performance figure.

    Returns:
      {
        "names": [...],
        "regimes": {
          "BULL": {
            "effective_n": float,
            "top_sharpe": name,
            "per_strategy": {
              name: {"mean_ann", "vol_ann", "sharpe_ann", "rank"}
            },
          }, ...
        },
      }
    or {"error": "..."} mirroring compute_regime_blends.
    """
    names, dates, matrix = build_strategy_matrix(
        strategy_results, exclude=exclude)
    if not names:
        return {"error": "insufficient_strategy_return_data"}
    posteriors = align_regime_posteriors(dates, hmm_result)
    if not posteriors:
        return {"error": "no_regime_posteriors"}

    a = float(annualization)
    sqrt_a = np.sqrt(a)
    regimes: dict[str, dict] = {}
    for regime in REGIMES:
        post = posteriors.get(regime)
        if post is None:
            continue
        mu, cov, ess = regime_conditional_moments(matrix, post)
        vol = np.sqrt(np.clip(np.diag(cov), 0.0, None))
        mean_ann = mu * a
        vol_ann = vol * sqrt_a
        with np.errstate(divide="ignore", invalid="ignore"):
            sharpe_ann = np.where(vol > 0, (mu * a) / (vol * sqrt_a), 0.0)
        # Regime-conditional correlation matrix. This is the evidence
        # that explains a high-Sharpe strategy receiving zero weight:
        # mean-variance correctly deprioritises a strategy that is
        # highly correlated with the ones already in the blend (it adds
        # return but no diversification). corr_ij = cov_ij/(σ_i·σ_j).
        with np.errstate(divide="ignore", invalid="ignore"):
            outer = np.outer(vol, vol)
            corr_mat = np.where(outer > 0, cov / outer, 0.0)
        # Rank 1 = highest Sharpe. argsort descending, then invert.
        order = np.argsort(-sharpe_ann)
        rank = np.empty(len(names), dtype=int)
        for r, idx in enumerate(order):
            rank[idx] = r + 1
        per_strategy = {
            names[i]: {
                "mean_ann": round(float(mean_ann[i]), 6),
                "vol_ann": round(float(vol_ann[i]), 6),
                "sharpe_ann": round(float(sharpe_ann[i]), 4),
                "rank": int(rank[i]),
            }
            for i in range(len(names))
        }
        corr = {
            names[i]: {
                names[j]: round(float(corr_mat[i, j]), 4)
                for j in range(len(names))
            }
            for i in range(len(names))
        }
        regimes[regime] = {
            "effective_n": round(float(ess), 2),
            "top_sharpe": names[int(order[0])] if len(order) else None,
            "per_strategy": per_strategy,
            "corr": corr,
        }

    if not regimes:
        return {"error": "no_regime_diagnostics_computed"}
    return {"names": names, "regimes": regimes}


def probability_weighted_blend(
    blends: dict[str, dict[str, float]],
    posterior: dict[str, float],
) -> dict[str, float]:
    """Live allocation: mix the regime blends by the CURRENT posterior.

        w = Σ_r P(r) · w_r

    blends    — {regime: {strategy: weight}} from compute_regime_blends
    posterior — {regime: P(regime)} the current HMM read (Layer 1's
                detect_current_regime hmm_probabilities). Need not sum
                to 1; it is renormalised over the regimes that ALSO
                have a blend, so a posterior carrying a TRANSITION
                probability when only BULL/BEAR blends exist degrades
                gracefully.

    Returns {strategy: weight} summing to 1 (within floating point).
    Returns {} when no regime is common to both inputs.
    """
    common = [r for r in posterior
              if r in blends and posterior.get(r) is not None]
    total_p = sum(max(float(posterior[r]), 0.0) for r in common)
    if not common or total_p <= 0:
        return {}

    # Union of every strategy named in any contributing blend.
    strategies: set[str] = set()
    for r in common:
        strategies.update(blends[r].keys())

    out: dict[str, float] = {name: 0.0 for name in strategies}
    for r in common:
        weight_r = max(float(posterior[r]), 0.0) / total_p
        for name, w in blends[r].items():
            out[name] += weight_r * float(w)

    # Renormalise defensively — the regime blends each sum to 1, and
    # the regime weights sum to 1, so the result already sums to 1;
    # the explicit normalise absorbs any per-blend rounding drift.
    s = sum(out.values())
    if s > 0:
        out = {k: round(v / s, 6) for k, v in out.items()}
    return out
