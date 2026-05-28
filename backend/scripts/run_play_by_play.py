"""scripts/run_play_by_play.py — point-in-time play-by-play validation.

Runs the nine post-2022 event evaluations against REAL production data
and prints a per-event record: the point-in-time regime + posterior, the
blend weights active on the day, the deterministic recommendation and
dissenting view, the forward 30/60/90-day performance of the blend vs
the benchmark vs the classic 60/40, and the value-added Sharpe.

  # Real data (Render shell — DB + hmmlearn present):
  python scripts/run_play_by_play.py

The HMM is fit point-in-time per event (data up to the event month
only), so this MUST run where hmmlearn is installed. There is no
synthetic fallback: a point-in-time fit is the whole point. On a host
without hmmlearn every event reports "Point-in-time computation
unavailable" and the script exits 1, signalling "run me on Render".
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ENVIRONMENT", "production")


def _fmt_pct(x) -> str:
    return "  n/a" if x is None else f"{x * 100:+6.2f}%"


def _fmt_weights(blend, top=4) -> str:
    if not blend:
        return "(none)"
    rows = sorted(blend.items(), key=lambda kv: kv[1], reverse=True)
    return "  ".join(f"{n}={w:.2f}" for n, w in rows[:top] if w > 0.01)


async def _real_inputs():
    """(strategy_results, equity_series) from production, or raise."""
    import pandas as pd
    from tools.cache import get_latest_strategy_cache, get_monthly_returns

    strategy_results = await get_latest_strategy_cache()
    if not strategy_results:
        raise RuntimeError("strategy_results_cache is empty")
    monthly = await get_monthly_returns()
    if not monthly or not monthly.get("equity") or not monthly.get("dates"):
        raise RuntimeError("monthly equity series unavailable")
    idx = pd.to_datetime(monthly["dates"])
    equity = pd.Series(monthly["equity"], index=idx).sort_index()
    return strategy_results, equity


async def _compute_and_store():
    """The event_id cache flow: skip events already persisted, compute
    only the new ones (LLM fires once each), persist the complete rows,
    and return the durable stored set for display."""
    strategy_results, equity = await _real_inputs()
    from tools.play_by_play import (
        get_persisted_event_ids, llm_event_recommendation, load_stored_events,
        persist_events, run_play_by_play,
    )
    existing = await get_persisted_event_ids()
    new_rows = run_play_by_play(
        strategy_results, equity,
        existing_event_ids=existing,
        recommend_fn=llm_event_recommendation)
    written = await persist_events(new_rows)
    stored = await load_stored_events()
    return {
        "equity_months": equity.shape[0],
        "n_strategies": len(strategy_results),
        "n_cached": len(existing),
        "n_computed": len(new_rows),
        "n_persisted": written,
        "display": stored if stored else new_rows,
    }


def main() -> int:
    try:
        res = asyncio.run(_compute_and_store())
    except Exception as exc:  # noqa: BLE001
        print(f"[!] Real inputs unavailable ({exc}).")
        print("[!] Run on Render (DB + hmmlearn present).")
        return 1

    display = res["display"]
    print("=" * 72)
    print("PLAY-BY-PLAY VALIDATION  --  point-in-time, no look-ahead")
    print("=" * 72)
    print(f"Equity months: {res['equity_months']}   "
          f"Strategies: {res['n_strategies']}")
    print(f"Cached (skipped): {res['n_cached']}   "
          f"Computed this run: {res['n_computed']}   "
          f"Persisted: {res['n_persisted']}")
    print("30/60/90 days = 1/2/3 forward months (monthly data). "
          "Cached events are never recomputed.")
    print()

    n_evaluable = 0
    for r in display:
        print("-" * 72)
        print(f"{r['event_id']}   ({r['event_date']})")
        print(f"  {r['trigger']}")
        if r.get("error") or r.get("regime") is None:
            print(f"  [unavailable] {r.get('verdict') or r.get('error')}")
            print()
            continue
        n_evaluable += 1
        post = r["posterior"] or {}
        print(f"  Regime: {r['regime']}   "
              f"P(BULL)={post.get('bull')}  P(BEAR)={post.get('bear')}  "
              f"P(TRANSITION)={post.get('transition')}   "
              f"(train {r['n_train_months']} mo)")
        print(f"  Blend: {_fmt_weights(r['blend_weights'])}")
        print(f"  Recommendation: {r['recommendation']}")
        print(f"  Dissent: {r['dissenting_view']}")
        perf = r["performance"] or {}
        print(f"    {'series':<14} {'30d':>8} {'60d':>8} {'90d':>8}")
        for key in ("blend", "benchmark", "classic_6040"):
            blk = perf.get(key, {})
            print(f"    {key:<14} {_fmt_pct(blk.get('d30'))} "
                  f"{_fmt_pct(blk.get('d60'))} {_fmt_pct(blk.get('d90'))}")
        va = r["value_added_sharpe"]
        va_s = "n/a" if va is None else f"{va:+.2f}"
        print(f"  Value added (90d Sharpe, directional): {va_s}")
        print(f"  Verdict: {r['verdict']}")
        print()

    print("=" * 72)
    print(f"Evaluable events: {n_evaluable} / {len(display)}")
    print("=" * 72)
    return 0 if n_evaluable else 1


if __name__ == "__main__":
    sys.exit(main())
