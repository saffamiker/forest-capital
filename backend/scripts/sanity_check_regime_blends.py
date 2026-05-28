"""scripts/sanity_check_regime_blends.py — Layer 2 sanity check.

Prints the regime-conditional meta-portfolio blends so they can be
eyeballed against intuition before Layer 3 is built:

  w_BULL       expected: tilts to momentum / max-Sharpe strategies
  w_BEAR       expected: tilts to min-variance / vol-targeting
  w_TRANSITION expected: more diversified, closer to equal weight

Also prints the current regime + P(BULL/BEAR/TRANSITION), the live
probability-weighted blend, and which regime has the lowest Kish
effective sample size (the one most likely to fall back to equal
weight).

USAGE

  # Real data (run on Render or any host with the DB + hmmlearn):
  python scripts/sanity_check_regime_blends.py

  # Forced synthetic demo (runs anywhere, NO DB / NO hmmlearn):
  python scripts/sanity_check_regime_blends.py --synthetic

The real path mirrors production exactly:
  strategy returns  ← cache.get_latest_strategy_cache()  (run_all_strategies output)
  HMM posteriors    ← fit_hmm_historical(equity monthly series)
  current regime    ← detect_current_regime()
  blends            ← regime_meta_optimizer.compute_regime_blends()
  live blend        ← regime_meta_optimizer.probability_weighted_blend()

If the DB or hmmlearn is unavailable the script prints a clear
"SYNTHETIC" banner and uses a deterministic fixture so the OUTPUT
SHAPE can still be reviewed — but those numbers are illustrative,
not the production weights. Run on Render for real numbers.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Make the backend package importable when run as `python scripts/...`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ENVIRONMENT", "production")


def _fmt_weights(blend: dict[str, float], top: int = 10) -> str:
    """Render a {strategy: weight} blend sorted by weight descending,
    so the heaviest tilts are obvious at a glance."""
    if not blend:
        return "    (empty)"
    rows = sorted(blend.items(), key=lambda kv: kv[1], reverse=True)
    lines = []
    for name, w in rows[:top]:
        bar = "#" * int(round(w * 40))
        lines.append(f"    {name:<22} {w:6.3f}  {bar}")
    return "\n".join(lines)


async def _real_inputs():
    """Pull the real production inputs. Returns
    (strategy_results, hmm_result, current_regime) or raises."""
    from tools.cache import get_latest_strategy_cache, get_monthly_returns

    strategy_results = await get_latest_strategy_cache()
    if not strategy_results:
        raise RuntimeError("strategy_results_cache is empty")

    monthly = await get_monthly_returns()
    if not monthly or not monthly.get("equity") or not monthly.get("dates"):
        raise RuntimeError("monthly equity series unavailable")

    import pandas as pd
    from tools.regime_detector import (
        detect_current_regime, fit_hmm_historical,
    )
    idx = pd.to_datetime(monthly["dates"])
    equity = pd.Series(monthly["equity"], index=idx)
    hmm_result = fit_hmm_historical(equity)
    if hmm_result.get("error"):
        raise RuntimeError(f"HMM fit failed: {hmm_result['error']}")

    current = detect_current_regime()
    return strategy_results, hmm_result, current


def _synthetic_inputs():
    """Deterministic fixture so the script runs with NO DB / NO
    hmmlearn. Numbers are illustrative ONLY."""
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(42)
    n = 180
    dates = [d.date().isoformat()
             for d in pd.date_range("2010-01-31", periods=n, freq="ME")]
    strat_profiles = {
        "BENCHMARK":          (0.006, 0.045),
        "CLASSIC_60_40":      (0.004, 0.028),
        "RISK_PARITY":        (0.004, 0.022),
        "MIN_VARIANCE":       (0.003, 0.016),
        "EQUAL_WEIGHT":       (0.004, 0.026),
        "MOMENTUM_ROTATION":  (0.007, 0.050),
        "REGIME_SWITCHING":   (0.006, 0.030),
        "VOL_TARGETING":      (0.004, 0.018),
        "BLACK_LITTERMAN":    (0.005, 0.027),
        "MAX_SHARPE_ROLLING": (0.007, 0.040),
    }
    strategy_results = {}
    for name, (mean, vol) in strat_profiles.items():
        rets = rng.normal(mean, vol, n)
        strategy_results[name] = {
            "monthly_returns": [
                [dates[t], round(float(rets[t]), 6)] for t in range(n)
            ]
        }
    half = n // 2
    bull = [0.75] * half + [0.25] * (n - half)
    bear = [0.10] * half + [0.65] * (n - half)
    trans = [round(1.0 - b - e, 6) for b, e in zip(bull, bear)]
    hmm_result = {
        "dates": dates,
        "historical_probs": {"BULL": bull, "BEAR": bear,
                             "TRANSITION": trans},
        "transition_matrix": {
            "BULL": {"BULL": 0.88, "TRANSITION": 0.09, "BEAR": 0.03},
            "TRANSITION": {"BULL": 0.20, "TRANSITION": 0.60, "BEAR": 0.20},
            "BEAR": {"BULL": 0.04, "TRANSITION": 0.16, "BEAR": 0.80},
        },
    }
    current = {
        "hmm_regime": "BEAR",
        "hmm_probabilities": {"BULL": 0.18, "TRANSITION": 0.22,
                              "BEAR": 0.60},
    }
    return strategy_results, hmm_result, current


def main() -> int:
    synthetic = "--synthetic" in sys.argv
    banner = "REAL PRODUCTION DATA"
    if synthetic:
        strategy_results, hmm_result, current = _synthetic_inputs()
        banner = "SYNTHETIC FIXTURE (illustrative only — run on Render for real)"
    else:
        try:
            strategy_results, hmm_result, current = asyncio.run(
                _real_inputs())
        except Exception as exc:  # noqa: BLE001
            print(f"[!] Real inputs unavailable ({exc}).")
            print("[!] Falling back to the SYNTHETIC fixture so the "
                  "output shape is reviewable.")
            print("[!] Run on Render (DB + hmmlearn present) for the "
                  "real weights.\n")
            strategy_results, hmm_result, current = _synthetic_inputs()
            banner = ("SYNTHETIC FIXTURE (illustrative only — real "
                      "inputs were unavailable)")

    from tools.regime_meta_optimizer import (
        compute_regime_blends, probability_weighted_blend,
    )

    built = compute_regime_blends(strategy_results, hmm_result)
    if built.get("error"):
        print(f"compute_regime_blends error: {built['error']}")
        return 1

    print("=" * 68)
    print(f"REGIME-CONDITIONAL META-PORTFOLIO BLENDS — {banner}")
    print("=" * 68)
    print(f"Strategies in matrix : {len(built['names'])}")
    print(f"Common months        : {built['n_months']}")
    print(f"Effective N / regime : {built['effective_n']}")
    if built["fallback"]:
        print(f"Equal-weight fallback: {built['fallback']}")
    print()

    for regime in ("BULL", "BEAR", "TRANSITION"):
        blend = built["blends"].get(regime)
        if blend is None:
            continue
        ess = built["effective_n"].get(regime)
        flag = "  [EW FALLBACK]" if regime in built["fallback"] else ""
        print(f"w_{regime}  (effective N = {ess}){flag}")
        print(_fmt_weights(blend))
        print()

    # Lowest-ESS regime — the one most likely to be under-determined.
    ess_map = {r: built["effective_n"].get(r) for r in built["blends"]
               if built["effective_n"].get(r) is not None}
    if ess_map:
        lowest = min(ess_map, key=ess_map.get)
        print(f"Lowest effective sample size: {lowest} "
              f"(ESS = {ess_map[lowest]}) -- most likely to fall back "
              f"to equal weight.")
        print()

    # Current regime + posterior + live blend.
    posterior = current.get("hmm_probabilities") or {}
    print("-" * 68)
    print(f"Current regime (HMM) : {current.get('hmm_regime')}")
    print("Current posterior    :")
    for r in ("BULL", "TRANSITION", "BEAR"):
        if r in posterior:
            print(f"    P({r:<10}) = {posterior[r]:.4f}")
    print()

    live = probability_weighted_blend(built["blends"], posterior)
    print("Live blend  w = sum P(r)*w_r :")
    print(_fmt_weights(live))
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
