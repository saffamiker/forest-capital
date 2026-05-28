"""tools/regime_meta_forward.py: Layer 4 of the Regime-Conditional
Meta-Portfolio Optimizer: FORWARD MONTE CARLO CONFIDENCE BANDS.

Layers 2 and 3 answer "what is the live blend" and "does it generalise".
This layer answers the question a presentation audience asks next: given
the regime we are in today, what does the LIVE blend's forward path look
like, and how wide is the uncertainty around it. The output is an
expected forward path (the median cumulative return) plus a 90% band
(5th to 95th percentile) at a set of horizons, derived by simulation
rather than a closed-form approximation.

WHY SIMULATE THE REGIME PATH RATHER THAN A SINGLE-REGIME DRAW

The blend is regime-conditional: in BULL it holds w_BULL, in BEAR it
holds w_BEAR, and so on. A forward projection that froze today's regime
would understate the uncertainty, because the regime itself is a random
walk over the simulation horizon. So each Monte Carlo path is a REGIME
PATH: the initial regime is sampled from the current posterior, and each
subsequent month is stepped through the HMM transition matrix. On a path
sitting in regime r at month t, the blend's monthly return is drawn from
the regime-conditional return distribution of the frozen blend w_r:

    portfolio mean_t = w_r . mu_r
    portfolio var_t  = w_r . cov_r . w_r^T

a Normal draw whose mean and variance are exactly the first two moments
of the blend's return under the Layer 2 regime-conditional moments. The
benchmark is drawn from the SAME regime path each month (its own mu_r /
cov_r diagonal entry), so the outperformance probability is computed on
matched regime realisations, not against an independent benchmark draw.

The transition matrix is the regime persistence model: a row-stochastic
matrix P where P[r][s] = P(regime s next month | regime r this month).
When the supplied HMM result carries no usable transition matrix we fall
back to a persistence model (0.8 on the diagonal, the remainder split
evenly), so the projection always has a regime dynamic, just a generic
one, with the source flagged in the output.

Fail-open throughout, mirroring Layers 2 and 3: a missing matrix, absent
posteriors, an upstream blend error, or an unusable transition matrix
each either return {"error": ...} or fall back to a documented default
with a diagnostic log line. The simulation is seeded (default 42) so the
bands are exactly reproducible for the same inputs and seed.
"""
from __future__ import annotations

import numpy as np
import structlog

from config import RISK_AVERSION
from tools.regime_meta_optimizer import (
    _META_MAX_WEIGHT,
    align_regime_posteriors,
    build_strategy_matrix,
    compute_regime_blends,
    probability_weighted_blend,
    regime_conditional_moments,
)

log = structlog.get_logger(__name__)

# The benchmark column id. When it is not in the matrix we cannot draw a
# matched benchmark path, so the outperformance probability is reported
# as None rather than fabricated.
_BENCHMARK_ID = "BENCHMARK"

# Persistence fallback: when no transition matrix is supplied, a regime
# is assumed to persist with probability 0.8 and otherwise move to one of
# the other present regimes with equal probability. 0.8 is a deliberately
# generic monthly persistence (a regime is far more likely to continue
# than to flip), and the fallback is flagged in the output so a reviewer
# knows the dynamic was not data-derived.
_PERSISTENCE_DIAG = 0.8


def _build_transition_matrix(
    hmm_result: dict,
    present: list[str],
) -> tuple[np.ndarray, str]:
    """Row-stochastic transition matrix over the regimes in `present`.

    hmm_result.get("transition_matrix") is expected to be a nested dict
    {from_regime: {to_regime: prob}}. We restrict it to the regimes that
    are present in BOTH the blends and the moments (the only regimes a
    path can actually occupy), then renormalise each row over that subset
    so it sums to 1. A row that is missing, empty, or sums to zero after
    restriction falls back to the persistence row for that regime, so a
    partially-specified matrix degrades gracefully per row.

    Returns (matrix, source) where source is "hmm" when at least one row
    came from the supplied matrix and "persistence_fallback" when the
    matrix was missing or wholly unusable.
    """
    k = len(present)
    persistence = _persistence_matrix(k)
    raw = (hmm_result or {}).get("transition_matrix")
    if not isinstance(raw, dict) or k == 0:
        log.warning("forward_mc_transition_missing",
                    fallback="persistence_fallback")
        return persistence, "persistence_fallback"

    matrix = np.zeros((k, k), dtype=float)
    any_row_from_hmm = False
    for i, frm in enumerate(present):
        row = raw.get(frm)
        if not isinstance(row, dict):
            matrix[i] = persistence[i]
            continue
        # Restrict to present regimes only; a probability to a regime we
        # are not simulating is dropped and the row renormalised.
        vals = np.array(
            [max(float(row.get(to, 0.0) or 0.0), 0.0) for to in present],
            dtype=float)
        total = vals.sum()
        if np.isfinite(total) and total > 0:
            matrix[i] = vals / total
            any_row_from_hmm = True
        else:
            matrix[i] = persistence[i]

    if not any_row_from_hmm:
        log.warning("forward_mc_transition_unusable",
                    fallback="persistence_fallback")
        return persistence, "persistence_fallback"
    return matrix, "hmm"


def _persistence_matrix(k: int) -> np.ndarray:
    """Generic persistence matrix: 0.8 on the diagonal, the remaining
    0.2 split equally among the other regimes. With a single regime the
    matrix is simply [[1.0]] (a state that can only stay)."""
    if k <= 0:
        return np.empty((0, 0))
    if k == 1:
        return np.ones((1, 1))
    off = (1.0 - _PERSISTENCE_DIAG) / (k - 1)
    matrix = np.full((k, k), off, dtype=float)
    np.fill_diagonal(matrix, _PERSISTENCE_DIAG)
    return matrix


def _initial_distribution(
    current_posterior: dict | None,
    present: list[str],
) -> np.ndarray:
    """Initial-regime sampling distribution over `present`, read from the
    current posterior and renormalised over the present regimes. An
    unusable posterior (missing, all-zero, non-finite) falls back to a
    uniform start so the simulation always has a valid initial mix."""
    k = len(present)
    if k == 0:
        return np.empty(0)
    uniform = np.full(k, 1.0 / k)
    if not isinstance(current_posterior, dict):
        return uniform
    vals = np.array(
        [max(float(current_posterior.get(r, 0.0) or 0.0), 0.0)
         for r in present],
        dtype=float)
    total = vals.sum()
    if not np.isfinite(total) or total <= 0:
        log.warning("forward_mc_posterior_unusable", fallback="uniform")
        return uniform
    return vals / total


def forward_monte_carlo(
    strategy_results: dict[str, dict],
    hmm_result: dict,
    current_posterior: dict | None,
    *,
    n_paths: int = 10000,
    horizons: tuple[int, ...] = (1, 3, 6, 12),
    seed: int = 42,
    exclude: tuple[str, ...] = (),
    risk_aversion: float = RISK_AVERSION,
    max_weight: float = _META_MAX_WEIGHT,
    min_effective_n: float | None = None,
    annualization: int = 12,
) -> dict:
    """Forward Monte Carlo confidence bands for the live regime-conditional
    blend.

    The frozen per-regime blends (Layer 2) and the regime-conditional
    moments are combined with a regime transition model into a forward
    simulation: each path samples an initial regime from
    current_posterior, walks the regime forward through the transition
    matrix, and on each month draws the blend's return (and a matched
    benchmark return) from the regime-conditional Normal. Cumulative
    returns are summarised at each horizon into a median and a 90% band.

    Returns:
      {
        "names": [...],
        "n_paths": int, "seed": int, "horizons_months": [...],
        "blend_weights": {strategy: weight},   # live prob-weighted blend
        "bands": {
          "<h>": {"median": float, "p05": float, "p95": float,
                  "p_outperform_benchmark": float | None},
          ...
        },
        "transition_source": "hmm" | "persistence_fallback",
      }
    or {"error": "..."} on the documented failure paths.
    """
    # 1. Strategy return matrix. No usable matrix means no moments to
    #    simulate from.
    names, dates, matrix = build_strategy_matrix(
        strategy_results, exclude=exclude)
    if not names:
        return {"error": "insufficient_strategy_return_data"}

    # 2. Aligned regime posteriors. Without them the moments cannot be
    #    weighted by regime.
    posteriors = align_regime_posteriors(dates, hmm_result)
    if not posteriors:
        return {"error": "no_regime_posteriors"}

    # 3. Frozen per-regime blends from Layer 2. Propagate any upstream
    #    error verbatim so the caller sees the real cause.
    blends_result = compute_regime_blends(
        strategy_results, hmm_result, exclude=exclude,
        risk_aversion=risk_aversion, max_weight=max_weight,
        min_effective_n=min_effective_n)
    if "error" in blends_result:
        return blends_result
    blends = blends_result["blends"]

    # 4. Regime-conditional moments for every regime that has BOTH a
    #    blend and a posterior. A regime missing from either cannot be
    #    simulated (no weights or no moments), so it is dropped here and
    #    the transition / initial distributions renormalise over what
    #    remains.
    n = len(names)
    moments: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for regime, blend in blends.items():
        post = posteriors.get(regime)
        if post is None:
            continue
        mu_r, cov_r, _ = regime_conditional_moments(matrix, post)
        moments[regime] = (mu_r, cov_r)
    present = [r for r in blends if r in moments]
    if not present:
        return {"error": "no_regime_moments_for_simulation"}

    # Frozen blend weight vectors, name-aligned, one per present regime.
    wvecs = {
        r: np.array([blends[r].get(nm, 0.0) for nm in names], dtype=float)
        for r in present
    }

    # 5. Transition matrix over the present regimes (row-stochastic),
    #    with a persistence fallback when the HMM does not supply one.
    transition, transition_source = _build_transition_matrix(
        hmm_result, present)

    # 6. Initial-regime distribution from the current posterior.
    init_dist = _initial_distribution(current_posterior, present)

    # Precompute per-regime portfolio + benchmark return parameters so the
    # inner simulation loop only draws, never recomputes moments. The
    # variance is clipped at zero: a degenerate regime covariance can
    # produce a tiny negative quadratic form through floating point, which
    # would make the Normal scale invalid.
    bench_idx = names.index(_BENCHMARK_ID) if _BENCHMARK_ID in names else None
    port_mean = np.empty(len(present))
    port_std = np.empty(len(present))
    bench_mean = np.empty(len(present))
    bench_std = np.empty(len(present))
    for i, r in enumerate(present):
        mu_r, cov_r = moments[r]
        wv = wvecs[r]
        port_mean[i] = float(wv @ mu_r)
        port_var = float(wv @ cov_r @ wv)
        port_std[i] = np.sqrt(max(port_var, 0.0))
        if bench_idx is not None:
            bench_mean[i] = float(mu_r[bench_idx])
            bench_std[i] = np.sqrt(max(float(cov_r[bench_idx, bench_idx]), 0.0))

    horizon_list = [int(h) for h in horizons if int(h) >= 1]
    if not horizon_list:
        return {"error": "no_valid_horizons"}
    max_h = max(horizon_list)

    # 6/7. Seeded forward simulation. Vectorised across paths, looped over
    #    months: at each step every path's current regime indexes its
    #    return parameters, a Normal draw produces that month's blend (and
    #    matched benchmark) return, and returns are compounded into the
    #    cumulative growth factor along each path.
    rng = np.random.default_rng(seed)
    k = len(present)
    regime_state = rng.choice(k, size=n_paths, p=init_dist)

    # Cumulative GROWTH factors (product of 1+r); converted to cumulative
    # return (factor - 1) at the recorded horizons.
    blend_growth = np.ones(n_paths)
    bench_growth = np.ones(n_paths) if bench_idx is not None else None
    horizon_set = set(horizon_list)
    blend_cum: dict[int, np.ndarray] = {}
    bench_cum: dict[int, np.ndarray] = {}

    for month in range(1, max_h + 1):
        means = port_mean[regime_state]
        stds = port_std[regime_state]
        blend_growth = blend_growth * (1.0 + rng.normal(means, stds))
        if bench_idx is not None:
            b_means = bench_mean[regime_state]
            b_stds = bench_std[regime_state]
            bench_growth = bench_growth * (1.0 + rng.normal(b_means, b_stds))
        if month in horizon_set:
            blend_cum[month] = blend_growth - 1.0
            if bench_idx is not None:
                bench_cum[month] = bench_growth - 1.0
        # Step the regime forward for next month. Each path transitions
        # from its current state by that state's row of the transition
        # matrix. Done after recording so month h reflects h regime draws.
        if month < max_h:
            regime_state = _step_regimes(rng, regime_state, transition)

    # 8. Summarise each horizon: median and the 90% band, plus the
    #    matched-path outperformance probability when the benchmark is in
    #    the universe.
    bands: dict[str, dict] = {}
    for h in horizon_list:
        cum = blend_cum[h]
        band = {
            "median": round(float(np.median(cum)), 6),
            "p05": round(float(np.percentile(cum, 5)), 6),
            "p95": round(float(np.percentile(cum, 95)), 6),
        }
        if bench_idx is not None:
            band["p_outperform_benchmark"] = round(
                float(np.mean(cum > bench_cum[h])), 6)
        else:
            band["p_outperform_benchmark"] = None
        bands[str(h)] = band

    # The live allocation the bands describe: the probability-weighted mix
    # of the frozen blends under the current posterior. Reported so the
    # presentation can show the weights alongside their projected path.
    blend_weights = probability_weighted_blend(
        blends, current_posterior if isinstance(current_posterior, dict)
        else {})

    return {
        "names": names,
        "n_paths": int(n_paths),
        "seed": int(seed),
        "horizons_months": horizon_list,
        "blend_weights": blend_weights,
        "bands": bands,
        "transition_source": transition_source,
    }


def _step_regimes(
    rng: np.random.Generator,
    regime_state: np.ndarray,
    transition: np.ndarray,
) -> np.ndarray:
    """Advance every path's regime one month via the transition matrix.

    Uses the inverse-CDF trick vectorised across paths: a single uniform
    per path is compared against the cumulative transition row of its
    current state. This is far cheaper than an rng.choice per path and,
    crucially for the reproducibility contract, consumes the random
    stream deterministically (one uniform vector per step)."""
    k = transition.shape[0]
    if k == 1:
        # Only one regime can exist; every path stays put. Still draw to
        # keep the stream advancing predictably is unnecessary here, so
        # we simply return the state unchanged.
        return regime_state
    cumulative = np.cumsum(transition, axis=1)        # (k, k)
    draws = rng.random(regime_state.shape[0])         # one uniform per path
    rows = cumulative[regime_state]                   # (n_paths, k)
    # searchsorted per row: the first column whose cumulative prob exceeds
    # the draw is the next regime.
    next_state = (draws[:, None] < rows).argmax(axis=1)
    return next_state.astype(regime_state.dtype)
