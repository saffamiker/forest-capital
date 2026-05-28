"""tools/play_by_play.py — point-in-time event validation.

The aggregate out-of-sample Sharpe (Layer 3) answers "does the
regime-conditional blend generalise?". The play-by-play answers the
question a faculty panel actually asks: "would it have made the right
call on the day, event by event, with only the data a manager had at
the time?".

For each of nine named post-2022 events this module, STRICTLY
point-in-time:

  1. fits the HMM on the equity series up to the event month only and
     reads the regime posterior at that month (no look-ahead),
  2. trains the regime-conditional blends on the pre-event window only
     (the same blends_from_matrix code Layer 2/3 use), and mixes them
     by the point-in-time posterior into the live blend weights,
  3. scores that blend against the benchmark and the classic 60/40 over
     the actual forward 30/60/90-day returns (1/2/3 forward months; the
     data is monthly, and that granularity is disclosed on every
     figure), and
  4. reports a one-sentence recommendation, a one-sentence dissenting
     view (a specific named limitation), and a value-added figure in
     Sharpe terms over the forward window.

WHAT IS POINT-IN-TIME. Both the HMM posterior AND the blend weights use
data only up to the event month. This is stricter than Layer 3 (which
freezes the blends on pre-2022 but reads posteriors off a full-history
HMM fit). The cost is one expanding-window Baum-Welch fit per event,
which is why the HMM step lives behind regime_detector and only runs
where hmmlearn is installed (Render). The forward-performance and
value-added maths are pure and fully unit-tested with synthetic data.

The value-added Sharpe is a THREE-OBSERVATION figure over the 90-day
window. It is directional only, never significance-tested, exactly as
the project treats every stress window (STRESS_TEST_USE_PVALUES=False).
Disclosed as such on the figure.

Fail-open throughout: a missing forward month, a failed HMM fit, or an
unavailable strategy degrades the affected field to None with a
diagnostic log line. The event row always assembles.
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
    probability_weighted_blend,
)

log = structlog.get_logger(__name__)

_BENCHMARK_ID = "BENCHMARK"
_CLASSIC_6040_ID = "CLASSIC_60_40"

# 30 / 60 / 90 days map to 1 / 2 / 3 forward months on monthly data.
_HORIZON_MONTHS = {"d30": 1, "d60": 2, "d90": 3}

# The nine events. Dates are anchored to the event MONTH-END: the
# point-in-time posterior incorporates that month's return (the event is
# already realised by month-end, no look-ahead) and the forward window
# is the months AFTER it. Triggers are one factual sentence each, no
# editorial bias; the HMM never sees the news, only return/volatility.
EVENTS: tuple[dict[str, str], ...] = (
    {"event_id": "svb_2023_03", "event_date": "2023-03-31",
     "label": "SVB Collapse",
     "trigger": "Silicon Valley Bank failed on March 10 2023, the "
                "second-largest US bank failure at the time, triggering "
                "acute regional-bank funding stress."},
    {"event_id": "debt_ceiling_2023_05", "event_date": "2023-05-31",
     "label": "Debt Ceiling Crisis",
     "trigger": "The US federal debt-ceiling standoff approached the "
                "projected default date through late May 2023 before a "
                "resolution in early June."},
    {"event_id": "higher_for_longer_2023_09", "event_date": "2023-09-30",
     "label": "Higher for Longer",
     "trigger": "The Federal Reserve signalled at its September 2023 "
                "meeting that policy rates would stay elevated for "
                "longer than markets had priced."},
    {"event_id": "everything_selloff_2023_10", "event_date": "2023-10-31",
     "label": "Everything Selloff",
     "trigger": "A broad cross-asset selloff in October 2023 saw "
                "equities and long-duration bonds fall together as the "
                "10-year Treasury yield neared 5%, alongside Middle East "
                "escalation."},
    {"event_id": "everything_rally_2023_12", "event_date": "2023-12-31",
     "label": "Everything Rally",
     "trigger": "A sharp cross-asset rally through November and December "
                "2023 followed a dovish Federal Reserve pivot, lifting "
                "equities and bonds together."},
    {"event_id": "yen_carry_2024_08", "event_date": "2024-08-31",
     "label": "Yen Carry Unwind",
     "trigger": "An unwind of the yen carry trade in early August 2024 "
                "drove a brief but severe global volatility spike."},
    {"event_id": "election_tariff_2024_11", "event_date": "2024-11-30",
     "label": "Election / Tariff Repricing",
     "trigger": "The November 2024 US election outcome prompted a "
                "repricing of growth, tariff, and interest-rate "
                "expectations."},
    {"event_id": "trade_war_2025_02", "event_date": "2025-02-28",
     "label": "Trade War Escalation",
     "trigger": "Escalating trade-policy tension in early 2025 raised "
                "tariff and supply-chain risk across markets."},
    {"event_id": "liberation_day_2025_04", "event_date": "2025-04-30",
     "label": "Liberation Day Tariff Shock",
     "trigger": "The April 2025 reciprocal-tariff announcement drove a "
                "sharp risk-off repricing across equities and credit."},
)


# ── pure forward-performance maths (fully unit-tested) ───────────────────────


def _series_for(strategy_results: dict, name: str) -> pd.Series | None:
    rows = ((strategy_results or {}).get(name) or {}).get("monthly_returns")
    if not rows:
        return None
    try:
        idx = pd.to_datetime([r[0] for r in rows])
        vals = [float(r[1]) for r in rows]
    except (TypeError, ValueError, IndexError):
        return None
    return pd.Series(vals, index=idx).sort_index()


def _forward_months(
    series: pd.Series, event_date: pd.Timestamp, n: int,
) -> np.ndarray:
    """The first n monthly returns STRICTLY after event_date. Fewer than
    n when the data runs out (the caller treats a short window as a
    missing horizon)."""
    fwd = series[series.index > event_date]
    return fwd.to_numpy(dtype=float)[:n]


def _blend_forward_months(
    strategy_results: dict,
    blend_weights: dict[str, float],
    event_date: pd.Timestamp,
    n: int,
) -> np.ndarray:
    """Forward monthly returns of the fixed-weight blend: each month the
    return is the weighted sum of the constituent strategies' returns
    (monthly rebalance to the recommended target). Only months every
    weighted strategy covers are used."""
    cols = {}
    for name, w in (blend_weights or {}).items():
        if w <= 0:
            continue
        s = _series_for(strategy_results, name)
        if s is None:
            continue
        cols[name] = s[s.index > event_date]
    if not cols:
        return np.empty(0)
    frame = pd.concat(cols, axis=1).dropna(how="any").sort_index().iloc[:n]
    if frame.empty:
        return np.empty(0)
    w = np.array([blend_weights[c] for c in frame.columns], dtype=float)
    total = w.sum()
    if total <= 0:
        return np.empty(0)
    w = w / total
    return (frame.to_numpy(dtype=float) @ w)


def _compound(returns: np.ndarray) -> float | None:
    r = np.asarray(returns, dtype=float)
    if r.size == 0:
        return None
    return float(np.prod(1.0 + r) - 1.0)


def _annualised_sharpe(returns: np.ndarray, annualization: int = 12) -> float | None:
    r = np.asarray(returns, dtype=float)
    if r.size < 2:
        return None
    sd = r.std(ddof=1)
    if not np.isfinite(sd) or sd <= 0:
        return None
    return float((r.mean() / sd) * np.sqrt(annualization))


def _horizon_block(monthly: np.ndarray) -> dict[str, float | None]:
    """Cumulative return at each of the 30/60/90-day (1/2/3-month)
    horizons, or None where the forward window is too short."""
    out: dict[str, float | None] = {}
    for key, m in _HORIZON_MONTHS.items():
        out[key] = (round(_compound(monthly[:m]), 6)
                    if monthly.size >= m else None)
    return out


def compute_event_performance(
    strategy_results: dict,
    blend_weights: dict[str, float],
    event_date: str | pd.Timestamp,
    *,
    annualization: int = 12,
) -> dict:
    """PURE: forward 30/60/90-day performance of the blend, the
    benchmark, and the classic 60/40, plus the value-added Sharpe and a
    one-sentence verdict. Given the blend weights and the strategy
    return series, this needs no HMM and no DB, so it is the unit-tested
    core. Returns {performance, value_added_sharpe, verdict}."""
    try:
        ed = pd.Timestamp(event_date)
    except (TypeError, ValueError):
        return {"performance": {}, "value_added_sharpe": None,
                "verdict": "Invalid event date."}

    max_m = max(_HORIZON_MONTHS.values())
    blend_fwd = _blend_forward_months(
        strategy_results, blend_weights, ed, max_m)
    bench_s = _series_for(strategy_results, _BENCHMARK_ID)
    classic_s = _series_for(strategy_results, _CLASSIC_6040_ID)
    bench_fwd = (_forward_months(bench_s, ed, max_m)
                 if bench_s is not None else np.empty(0))
    classic_fwd = (_forward_months(classic_s, ed, max_m)
                   if classic_s is not None else np.empty(0))

    performance = {
        "blend": _horizon_block(blend_fwd),
        "benchmark": _horizon_block(bench_fwd),
        "classic_6040": _horizon_block(classic_fwd),
    }

    # Value added in Sharpe terms over the 90-day (3-month) window. A
    # three-observation, directional figure only.
    blend_sh = _annualised_sharpe(blend_fwd[:max_m], annualization)
    bench_sh = _annualised_sharpe(bench_fwd[:max_m], annualization)
    value_added = (round(blend_sh - bench_sh, 4)
                   if (blend_sh is not None and bench_sh is not None)
                   else None)

    verdict = _verdict(performance, value_added)
    return {"performance": performance,
            "value_added_sharpe": value_added,
            "verdict": verdict}


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def _verdict(performance: dict, value_added: float | None) -> str:
    blend90 = performance.get("blend", {}).get("d90")
    bench90 = performance.get("benchmark", {}).get("d90")
    if blend90 is None or bench90 is None:
        return ("Forward window incomplete; performance not yet "
                "evaluable for this event.")
    direction = ("added value" if blend90 > bench90
                 else "did not add value")
    va = ("" if value_added is None
          else f" Value added {value_added:+.2f} Sharpe over 90 days "
               f"(3-month, directional).")
    return (f"Over 90 days the blend returned {_pct(blend90)} vs the "
            f"benchmark {_pct(bench90)}: the council {direction}.{va}")


# ── recommendation / dissent (deterministic, fail-open) ──────────────────────


def _top_strategies(blend_weights: dict[str, float], k: int = 3) -> list[str]:
    return [n for n, _ in sorted(
        (blend_weights or {}).items(), key=lambda kv: kv[1], reverse=True)
        if blend_weights.get(n, 0.0) > 0.05][:k]


def event_recommendation(
    regime: str | None,
    posterior: dict[str, float] | None,
    blend_weights: dict[str, float],
) -> dict[str, str]:
    """A factual, deterministic one-sentence recommendation and
    dissenting view derived from the point-in-time regime and weights.
    Deterministic by design: an event record must not depend on an LLM
    being reachable (the same call the academic-export pipeline makes).
    The dissenting view always names a specific limitation, never a
    generic hedge, in the spirit of the four-component structure."""
    tops = _top_strategies(blend_weights)
    tops_str = ", ".join(tops) if tops else "an equal-weight blend"
    p = 0.0
    if posterior and regime:
        try:
            p = float(posterior.get(regime.lower(), posterior.get(regime, 0.0)) or 0.0)
        except (TypeError, ValueError):
            p = 0.0
    reg = regime or "uncertain"
    rec = (f"With the regime read as {reg}"
           f"{f' (P={p:.0%})' if p else ''}, the point-in-time blend "
           f"tilted toward {tops_str}, the regime-conditional allocation "
           f"for this state.")
    dissent = (
        "This call rests on a point-in-time HMM with no news input and a "
        "training window that ends at the event month; a regime misread "
        "at a turning point like this would not be corrected until the "
        "next month's data arrived.")
    return {"recommendation": rec, "dissenting_view": dissent}


# ── point-in-time HMM + blend (Render-side; hmmlearn) ────────────────────────


def point_in_time_blend(
    strategy_results: dict,
    equity_series: pd.Series,
    event_date: str | pd.Timestamp,
    *,
    exclude: tuple[str, ...] = (),
    risk_aversion: float = RISK_AVERSION,
    max_weight: float = _META_MAX_WEIGHT,
    min_effective_n: float | None = None,
) -> dict:
    """Fit the HMM on equity up to event_date, read the posterior at the
    event month, train the regime blends on the pre-event window, and
    mix them by the posterior. Render-side (needs hmmlearn). Fail-open:
    returns {"error": ...} which the caller records as a null blend.

    The HMM fit is imported lazily so this module imports cleanly in
    environments without hmmlearn (the pure performance maths above do
    not need it)."""
    try:
        ed = pd.Timestamp(event_date)
    except (TypeError, ValueError):
        return {"error": "bad_event_date"}

    eq = equity_series[equity_series.index <= ed]
    if eq.shape[0] < 24:
        return {"error": "insufficient_equity_history"}

    try:
        from tools.regime_detector import fit_hmm_historical
    except Exception as exc:  # noqa: BLE001
        return {"error": f"regime_detector_unavailable: {exc}"}

    hmm = fit_hmm_historical(eq)
    if not hmm or hmm.get("error"):
        return {"error": f"hmm_fit_failed: {(hmm or {}).get('error')}"}

    # Strategy matrix restricted to the pre-event-inclusive window.
    names, dates, matrix = build_strategy_matrix(
        strategy_results, exclude=exclude)
    if not names:
        return {"error": "insufficient_strategy_return_data"}
    train_mask = np.asarray(dates <= ed)
    if train_mask.sum() < 2:
        return {"error": "insufficient_train_window"}
    train_dates = dates[train_mask]
    train_matrix = matrix[train_mask]

    posteriors_full = align_regime_posteriors(train_dates, hmm)
    if not posteriors_full:
        return {"error": "no_regime_posteriors"}

    blends, _eff, _fb = blends_from_matrix(
        names, train_matrix, posteriors_full,
        risk_aversion=risk_aversion, max_weight=max_weight,
        min_effective_n=min_effective_n)
    if not blends:
        return {"error": "no_blends_computed"}

    # Posterior AT the event month: the last row of each aligned series.
    post_at_event = {
        r: float(posteriors_full[r][-1])
        for r in posteriors_full if len(posteriors_full[r])
    }
    total = sum(max(v, 0.0) for v in post_at_event.values())
    if total > 0:
        post_at_event = {r: max(v, 0.0) / total
                         for r, v in post_at_event.items()}
    regime = (max(post_at_event, key=post_at_event.get)
              if post_at_event else None)
    blend_weights = probability_weighted_blend(blends, post_at_event)

    return {
        "regime": regime,
        "posterior": {
            "bull": round(post_at_event.get("BULL", 0.0), 4),
            "bear": round(post_at_event.get("BEAR", 0.0), 4),
            "transition": round(post_at_event.get("TRANSITION", 0.0), 4),
        },
        "blend_weights": blend_weights,
        "n_train_months": int(train_mask.sum()),
    }


def evaluate_event(
    event: dict[str, str],
    strategy_results: dict,
    equity_series: pd.Series,
    *,
    exclude: tuple[str, ...] = (),
    **blend_kwargs,
) -> dict:
    """Full point-in-time record for one event: the regime/posterior/
    blend (Render-side HMM), the forward performance (pure), and a
    deterministic recommendation/dissent. Fail-open: the HMM step
    failing yields null regime/blend but still a complete row with the
    trigger and any performance computable from a null blend (none)."""
    pit = point_in_time_blend(
        strategy_results, equity_series, event["event_date"],
        exclude=exclude, **blend_kwargs)
    row: dict = {
        "event_id": event["event_id"],
        "event_date": event["event_date"],
        "trigger": event["trigger"],
        "hmm_fit": "point_in_time",
    }
    if pit.get("error"):
        log.warning("play_by_play_pit_failed",
                    event_id=event["event_id"], error=pit["error"])
        row.update({"regime": None, "posterior": None,
                    "blend_weights": None, "n_train_months": None,
                    "recommendation": None, "dissenting_view": None,
                    "performance": None, "value_added_sharpe": None,
                    "verdict": f"Point-in-time computation unavailable: "
                               f"{pit['error']}."})
        return row

    regime = pit["regime"]
    posterior = pit["posterior"]
    blend_weights = pit["blend_weights"]
    perf = compute_event_performance(
        strategy_results, blend_weights, event["event_date"])
    rec = event_recommendation(regime, posterior, blend_weights)

    row.update({
        "regime": regime,
        "posterior": posterior,
        "blend_weights": blend_weights,
        "n_train_months": pit["n_train_months"],
        "recommendation": rec["recommendation"],
        "dissenting_view": rec["dissenting_view"],
        "performance": perf["performance"],
        "value_added_sharpe": perf["value_added_sharpe"],
        "verdict": perf["verdict"],
    })
    return row


def run_play_by_play(
    strategy_results: dict,
    equity_series: pd.Series,
    *,
    exclude: tuple[str, ...] = (),
    **blend_kwargs,
) -> list[dict]:
    """Evaluate every event in EVENTS. Each is independent and fail-open,
    so one event's HMM failure never blocks the rest."""
    out: list[dict] = []
    for event in EVENTS:
        try:
            out.append(evaluate_event(
                event, strategy_results, equity_series,
                exclude=exclude, **blend_kwargs))
        except Exception as exc:  # noqa: BLE001
            log.warning("play_by_play_event_error",
                        event_id=event["event_id"], error=str(exc))
            out.append({"event_id": event["event_id"],
                        "event_date": event["event_date"],
                        "trigger": event["trigger"],
                        "error": str(exc)})
    return out
