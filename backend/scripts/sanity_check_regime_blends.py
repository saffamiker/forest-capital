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


def _print_sensitivity(strategy_results, hmm_result, caps=(0.30, 0.40, 0.50)):
    """Re-run the blends at several per-strategy caps so we can see how
    binding the 0.40 diversification constraint is. If the same
    strategy tops every regime at every cap level, the tilt is driven
    by the moments (a genuine finding), not by the constraint."""
    from tools.regime_meta_optimizer import compute_regime_blends

    print("-" * 68)
    print("BOX-CONSTRAINT SENSITIVITY  (top 3 by weight per regime)")
    print("-" * 68)
    for cap in caps:
        built = compute_regime_blends(
            strategy_results, hmm_result, max_weight=cap)
        if built.get("error"):
            print(f"  cap {cap:.2f}: error {built['error']}")
            continue
        print(f"  cap = {cap:.2f}")
        for regime in ("BULL", "BEAR", "TRANSITION"):
            blend = built["blends"].get(regime)
            if not blend:
                continue
            rows = sorted(blend.items(), key=lambda kv: kv[1],
                          reverse=True)[:3]
            # Count how many strategies sit AT the cap (binding).
            at_cap = sum(1 for _, w in blend.items()
                         if abs(w - cap) < 1e-3)
            top3 = "  ".join(f"{n}={w:.3f}" for n, w in rows)
            ew = "  [EW]" if regime in built["fallback"] else ""
            print(f"    {regime:<11} {top3}   ({at_cap} at cap){ew}")
        print()


def _print_dominance(strategy_results, hmm_result, built):
    """Per-regime Sharpe ranking next to the blend weight. Answers the
    faculty question: is the top-weighted strategy genuinely the
    highest-Sharpe one in that regime (a finding), or is it loaded to
    the cap despite a mid-pack Sharpe (a covariance / constraint
    effect worth understanding)?"""
    from tools.regime_meta_optimizer import regime_strategy_diagnostics

    diag = regime_strategy_diagnostics(strategy_results, hmm_result)
    if diag.get("error"):
        print(f"regime_strategy_diagnostics error: {diag['error']}")
        return
    cap = built.get("max_weight", 0.40)
    print("-" * 68)
    print("MOMENTUM-DOMINANCE CHECK  (Sharpe rank vs blend weight)")
    print(f"  Sharpe is raw return/vol within each regime, annualised. "
          f"Cap = {cap:.2f}.")
    print("-" * 68)
    for regime in ("BULL", "BEAR", "TRANSITION"):
        rinfo = diag["regimes"].get(regime)
        if not rinfo:
            continue
        blend = (built.get("blends") or {}).get(regime, {})
        per = rinfo["per_strategy"]
        # Sort by Sharpe rank ascending (rank 1 = best).
        ordered = sorted(per.items(), key=lambda kv: kv[1]["rank"])
        top = rinfo.get("top_sharpe")
        print(f"  {regime}  (top Sharpe: {top})")
        print(f"    {'strategy':<22} {'rank':>4} {'sharpe':>8} "
              f"{'meanA':>8} {'volA':>7} {'weight':>8}")
        for name, m in ordered[:6]:
            w = blend.get(name, 0.0)
            flag = " <-CAP" if abs(w - cap) < 1e-3 else ""
            print(f"    {name:<22} {m['rank']:>4} "
                  f"{m['sharpe_ann']:>8.3f} {m['mean_ann']:>8.3f} "
                  f"{m['vol_ann']:>7.3f} {w:>8.3f}{flag}")
        # The one-line verdict the reviewer wants.
        top_weighted = max(blend.items(), key=lambda kv: kv[1],
                           default=(None, 0.0))[0] if blend else None
        if top_weighted and top_weighted == top:
            print(f"    -> top weight IS top Sharpe: genuine tilt (a)")
        elif top_weighted:
            tw_rank = per.get(top_weighted, {}).get("rank", "?")
            print(f"    -> top weight is {top_weighted} (Sharpe rank "
                  f"{tw_rank}): covariance/constraint-driven, review (b)")
            # WHY is the top-Sharpe strategy displaced? Show its
            # regime-conditional correlation with the strategies that
            # took the weight. High correlation == no diversification
            # to add == the optimizer correctly deprioritises it.
            corr = rinfo.get("corr", {})
            displacers = [
                n for n, _ in sorted(
                    blend.items(), key=lambda kv: kv[1], reverse=True)
                if blend.get(n, 0.0) > 1e-6 and n != top
            ][:3]
            row = corr.get(top, {})
            if top and displacers and row:
                pairs = "; ".join(
                    f"{d}={row.get(d, float('nan')):.2f}"
                    for d in displacers)
                print(f"       corr({top} vs displacers): {pairs}")
                vals = [row.get(d) for d in displacers
                        if row.get(d) is not None]
                if vals and (sum(vals) / len(vals)) > 0.70:
                    print(f"       -> high correlation ("
                          f"{sum(vals) / len(vals):.2f} avg): {top} adds "
                          f"return but no diversification; the optimizer "
                          f"correctly deprioritises it. Not a bug.")
                elif vals:
                    print(f"       -> low/moderate correlation ("
                          f"{sum(vals) / len(vals):.2f} avg): displacement "
                          f"is mean/constraint-driven, worth a closer look.")
        print()


def _print_oos(strategy_results, hmm_result, rf_map, split_date="2022-01-01"):
    """Layer 3: train the regime-conditional blends on the pre-2022
    window, freeze them, apply to post-2022, and compare the
    out-of-sample Sharpe against equal weight, the benchmark, and
    Regime Switching alone. The reference Sharpes are recomputed here
    over the same window so they can be checked against the known
    targets (EW 0.7136 / benchmark 0.5255 / Regime Switching 0.6211);
    if they match, the regime-conditional number is trustworthy."""
    from tools.regime_meta_validation import out_of_sample_validation

    res = out_of_sample_validation(
        strategy_results, hmm_result, split_date=split_date,
        risk_free=rf_map)
    print("-" * 68)
    print(f"LAYER 3 -- OUT-OF-SAMPLE VALIDATION (split {split_date})")
    print("-" * 68)
    if res.get("error"):
        print(f"  error: {res['error']}")
        print()
        return
    print(f"  Train months: {res['n_train_months']}   "
          f"Test months: {res['n_test_months']}   "
          f"HMM fit: {res['hmm_fit']}   rf: {res['risk_free']}")
    if res.get("train_fallback"):
        print(f"  Train EW fallback regimes: {res['train_fallback']}")
    print()
    oos = res["oos"]
    targets = {"equal_weight": 0.7136, "benchmark": 0.5255,
               "regime_switching": 0.6211}
    print(f"    {'series':<22} {'sharpe':>8} {'CAGR':>8} {'volA':>7} "
          f"{'target':>8}")
    order = ["regime_conditional", "equal_weight", "benchmark",
             "regime_switching"]
    for key in order:
        b = oos.get(key)
        if not b:
            continue
        sh = b["sharpe"]
        cg = b["cagr"]
        vol = b["vol_ann"]
        tgt = targets.get(key)
        sh_s = "   --" if sh is None else f"{sh:8.4f}"
        cg_s = "   --" if cg is None else f"{cg:8.4f}"
        vol_s = "  --" if vol is None else f"{vol:7.4f}"
        tgt_s = "" if tgt is None else f"{tgt:8.4f}"
        print(f"    {key:<22} {sh_s} {cg_s} {vol_s} {tgt_s}")
    print()
    print(f"  {res['verdict']['summary']}")
    print()


async def _real_inputs():
    """Pull the real production inputs. Returns (strategy_results,
    hmm_result, current_regime, rf_map) or raises. rf_map is the monthly
    DTB3 risk-free rate keyed by iso date, threaded into the Layer 3 OOS
    Sharpe so the baselines use the same time-varying rate as the rest
    of the platform."""
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

    rf_map = None
    if monthly.get("rf"):
        rf_map = {d: r for d, r in zip(monthly["dates"], monthly["rf"])}

    current = detect_current_regime()
    return strategy_results, hmm_result, current, rf_map


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
    return strategy_results, hmm_result, current, None  # rf_map None


def main() -> int:
    synthetic = "--synthetic" in sys.argv
    banner = "REAL PRODUCTION DATA"
    if synthetic:
        strategy_results, hmm_result, current, rf_map = _synthetic_inputs()
        banner = "SYNTHETIC FIXTURE (illustrative only — run on Render for real)"
    else:
        try:
            strategy_results, hmm_result, current, rf_map = asyncio.run(
                _real_inputs())
        except Exception as exc:  # noqa: BLE001
            print(f"[!] Real inputs unavailable ({exc}).")
            print("[!] Falling back to the SYNTHETIC fixture so the "
                  "output shape is reviewable.")
            print("[!] Run on Render (DB + hmmlearn present) for the "
                  "real weights.\n")
            strategy_results, hmm_result, current, rf_map = _synthetic_inputs()
            banner = ("SYNTHETIC FIXTURE (illustrative only — real "
                      "inputs were unavailable)")

    from tools.regime_meta_optimizer import (
        compute_regime_blends, probability_weighted_blend,
        regime_strategy_diagnostics,
    )

    built = compute_regime_blends(strategy_results, hmm_result)
    if built.get("error"):
        print(f"compute_regime_blends error: {built['error']}")
        return 1

    print("=" * 68)
    print(f"REGIME-CONDITIONAL META-PORTFOLIO BLENDS -- {banner}")
    print("=" * 68)
    print(f"Strategies in matrix : {len(built['names'])}")
    print(f"Common months        : {built['n_months']}")
    print(f"Effective N / regime : {built['effective_n']}")
    print(f"Box constraint       : {built.get('box_constraint_note')}")
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

    # Faculty pre-emption: is the cap binding, and is the dominant
    # strategy genuinely top-Sharpe or a covariance/constraint effect?
    _print_sensitivity(strategy_results, hmm_result)
    _print_dominance(strategy_results, hmm_result, built)

    # Layer 3: out-of-sample validation (train pre-2022, test post-2022).
    _print_oos(strategy_results, hmm_result, rf_map)

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
