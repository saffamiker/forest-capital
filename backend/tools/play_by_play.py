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

_DB_AVAILABLE = False
try:  # pragma: no cover - environment dependent
    from database import AsyncSessionLocal
    _DB_AVAILABLE = AsyncSessionLocal is not None
except Exception:  # noqa: BLE001
    AsyncSessionLocal = None  # type: ignore[assignment]

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

# Curated key-limitation annotations surfaced by the read endpoint and
# flagged prominently in the UI. Liberation Day is the model's clearest
# limitation: the regime filter read the April-2025 tariff shock as a
# risk-off regime and positioned defensively, then missed the sharp
# relief rally that followed, for a negative value-added. The dissenting
# view recorded at the event predicted exactly this overfitting to the
# shock. Stated plainly rather than buried: the council's edge is
# capital preservation in genuine bear regimes, not calling sharp
# V-shaped reversals.
KEY_LIMITATION_NOTES: dict[str, str] = {
    "liberation_day_2025_04": (
        "Key limitation. The regime filter classified the April 2025 "
        "tariff shock as a risk-off regime and positioned defensively, "
        "then missed the subsequent relief rally, for a negative "
        "value-added over the forward window. The dissenting view "
        "recorded at the event predicted this overfitting to the shock. "
        "The council's edge is capital preservation in sustained bear "
        "regimes, not calling sharp V-shaped reversals."),
}


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
    recommend_fn=None,
    **blend_kwargs,
) -> dict:
    """Full point-in-time record for one event: the regime/posterior/
    blend (Render-side HMM), the forward performance (pure), and the
    recommendation/dissent. Fail-open: the HMM step failing yields null
    regime/blend but still a complete row with the trigger.

    recommend_fn(event, regime, posterior, blend_weights) -> {
    recommendation, dissenting_view}. Defaults to the deterministic
    event_recommendation. The LLM-backed generator (llm_event_
    recommendation) is injected here on first compute and is the ONLY
    place the LLM fires; once the row is persisted the event is skipped
    forever, so the LLM never runs again for that event_id."""
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
    rec_fn = recommend_fn or (
        lambda ev, rg, po, bw: event_recommendation(rg, po, bw))
    rec = rec_fn(event, regime, posterior, blend_weights)

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


def is_persistable(row: dict) -> bool:
    """A row is a COMPLETE historical fact, safe to freeze forever, only
    when the point-in-time regime computed AND the forward window is
    fully realised (the 90-day / 3-month horizon is present). An event
    whose forward window has not yet elapsed is not persisted, so it is
    recomputed on a later run once the data exists, then frozen. This is
    what makes 'never recompute' correct: we only ever cache settled
    history."""
    if row.get("error") or row.get("regime") is None:
        return False
    perf = row.get("performance") or {}
    d90 = (perf.get("blend") or {}).get("d90")
    # d90 must be a FINITE number. None means the forward window has not
    # elapsed; NaN means a degenerate computation (it would be sanitised
    # to null on write and freeze an incomplete fact). Either way the
    # event is not yet a settled historical fact and is recomputed next
    # run rather than frozen.
    try:
        return bool(d90 is not None and np.isfinite(float(d90)))
    except (TypeError, ValueError):
        return False


def run_play_by_play(
    strategy_results: dict,
    equity_series: pd.Series,
    *,
    existing_event_ids: frozenset[str] | set[str] = frozenset(),
    exclude: tuple[str, ...] = (),
    recommend_fn=None,
    **blend_kwargs,
) -> list[dict]:
    """Evaluate every event in EVENTS that is NOT already cached. An
    event_id present in existing_event_ids is skipped entirely and never
    recomputed (these are immutable historical facts). Each computed
    event is independent and fail-open, so one event's HMM failure never
    blocks the rest. Returns only the freshly-computed rows."""
    out: list[dict] = []
    for event in EVENTS:
        if event["event_id"] in existing_event_ids:
            continue  # cached historical fact; never recompute
        try:
            out.append(evaluate_event(
                event, strategy_results, equity_series,
                exclude=exclude, recommend_fn=recommend_fn, **blend_kwargs))
        except Exception as exc:  # noqa: BLE001
            log.warning("play_by_play_event_error",
                        event_id=event["event_id"], error=str(exc))
            out.append({"event_id": event["event_id"],
                        "event_date": event["event_date"],
                        "trigger": event["trigger"],
                        "error": str(exc)})
    return out


# ── LLM recommendation (fires ONCE per event_id, then frozen) ────────────────


def llm_event_recommendation(
    event: dict[str, str],
    regime: str | None,
    posterior: dict[str, float] | None,
    blend_weights: dict[str, float],
) -> dict[str, str]:
    """A council-style point-in-time recommendation + dissent for one
    event, in the four-component framing (PR #209), generated by a single
    LLM call. POINT-IN-TIME INPUT ONLY: the event trigger, the regime
    read, the posterior, and the blend weights as of the event month. It
    is never given the forward performance (that would be look-ahead);
    forward returns are for scoring, not for the recommendation.

    Fail-open: any LLM error (no API key in the test environment, a
    transient outage) falls back to the deterministic event_recommendation
    so a row always assembles. Because the caller persists the row and
    then skips this event forever, this LLM call fires at most once per
    event_id, ever."""
    try:
        from agents.base import SONNET_MODEL, call_claude
    except Exception:  # noqa: BLE001
        return event_recommendation(regime, posterior, blend_weights)

    tops = _top_strategies(blend_weights, k=5)
    system = (
        "You are the CIO of a quantitative investment council giving a "
        "point-in-time recommendation. You are told only what was known "
        "at the event month: the trigger, the regime read, the regime "
        "posterior, and the regime-conditional blend weights. You do NOT "
        "know what happened next. Reply with STRICT JSON only, no prose, "
        "no code fence: {\"recommendation\": \"<one sentence>\", "
        "\"dissenting_view\": \"<one sentence naming a specific "
        "limitation, not a generic hedge>\"}. Never use em dashes.")
    user = (
        f"Event ({event['event_date']}): {event['trigger']}\n"
        f"Regime read: {regime}; posterior {posterior}.\n"
        f"Regime-conditional blend (top weights): {tops}.\n"
        "Give the council's one-sentence recommendation for how to be "
        "positioned from here, and the strongest one-sentence dissenting "
        "view. Ground both in the regime read and the weights; cite the "
        "regime posterior as the confidence signal.")
    try:
        import json
        raw = call_claude(SONNET_MODEL, system, user, max_tokens=400,
                          trigger="play_by_play_recommendation")
        text = (raw or "").strip()
        # Tolerate a stray code fence.
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{"):]
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        rec = str(data.get("recommendation") or "").strip()
        dis = str(data.get("dissenting_view") or "").strip()
        if rec and dis:
            return {"recommendation": rec, "dissenting_view": dis}
    except Exception as exc:  # noqa: BLE001
        log.warning("play_by_play_llm_failed",
                    event_id=event.get("event_id"), error=str(exc))
    return event_recommendation(regime, posterior, blend_weights)


# ── persistence: event_id cache, write-once, never recompute ─────────────────


def _finite_or_none(x):
    """A float that is finite, else None. NaN / Inf reaching a numeric
    column or a JSONB cast is what crashed svb_2023_03 (its short early
    training window produced a degenerate weight); coercing to None keeps
    the row persistable as settled history with a null where the value
    was undefined."""
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    return xf if np.isfinite(xf) else None


def _json_safe(obj) -> str:
    """json.dumps with NaN / Inf replaced by null. Postgres rejects the
    literal NaN that Python's json emits by default ('{"w": NaN}' is not
    valid JSON), failing the CAST AS JSONB. Recursively sanitising to
    null makes any degenerate value storable rather than fatal."""
    import json

    def _clean(v):
        if isinstance(v, float):
            return v if np.isfinite(v) else None
        if isinstance(v, dict):
            return {k: _clean(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [_clean(x) for x in v]
        return v

    return json.dumps(_clean(obj))


async def get_persisted_event_ids() -> set[str]:
    """event_ids of COMPLETE persisted rows — the run skips only these.

    A row counts as a settled, never-recompute fact only when its regime
    resolved, its performance block exists, AND the forward 90-day figure
    is present — the same completeness is_persistable enforces at write
    time. An INCOMPLETE row (regime null, no performance, or a null
    blend.d90 — left by a degraded or partial earlier run) is deliberately
    NOT treated as cached, so the event is recomputed on the next run and
    its row upgraded in place (persist_events does ON CONFLICT DO UPDATE).
    This is what makes a failed event retryable while a settled fact stays
    frozen. Fail-open to an empty set (a DB problem means 'nothing
    cached', so the run recomputes rather than silently doing nothing)."""
    if not _DB_AVAILABLE:
        return set()
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            rows = await session.execute(text(
                "SELECT event_id FROM play_by_play_events "
                "WHERE regime IS NOT NULL "
                "AND performance IS NOT NULL "
                "AND (performance->'blend'->>'d90') IS NOT NULL"))
            return {r[0] for r in rows.fetchall()}
    except Exception as exc:  # noqa: BLE001
        log.warning("play_by_play_existing_read_error", error=str(exc))
        return set()


def _skip_reason(row: dict) -> str:
    """Why a row is not persistable — logged per skip so a '0 rows landed'
    production run is diagnosable from the logs rather than silent."""
    if row.get("error"):
        return f"error:{row['error']}"
    if row.get("regime") is None:
        return "regime_none"
    d90 = ((row.get("performance") or {}).get("blend") or {}).get("d90")
    if d90 is None:
        return "blend_d90_none (forward window incomplete)"
    try:
        if not np.isfinite(float(d90)):
            return "blend_d90_nan"
    except (TypeError, ValueError):
        return "blend_d90_non_numeric"
    return "unknown"


async def persist_events(rows: list[dict], *, data_hash: str | None = None) -> int:
    """INSERT each COMPLETE row (is_persistable) into play_by_play_events.

    ON CONFLICT (event_id) DO UPDATE: a COMPLETE row is never recomputed
    (get_persisted_event_ids skips it), so the only conflict that ever
    fires is an INCOMPLETE row from a degraded earlier run being upgraded
    to a settled fact — failed events are retryable, settled facts stay
    frozen. Each row commits in its OWN transaction (a single shared commit
    let one bad row abort the whole batch), and every commit is explicit.

    Instrumented: each non-persistable row logs its skip reason, and a
    persist summary (rows_in / persistable / written) is logged at the end,
    so a run that computes events but writes zero rows is diagnosable from
    the logs. Returns the number written. Fail-open."""
    if not _DB_AVAILABLE:
        log.warning("play_by_play_persist_db_unavailable", rows_in=len(rows))
        return 0
    import json  # noqa: F401 — kept for parity with the JSONB cast helpers

    from sqlalchemy import text
    stmt = text(
        "INSERT INTO play_by_play_events "
        "(event_id, event_date, trigger, regime, posterior, "
        " blend_weights, recommendation, dissenting_view, performance, "
        " verdict, value_added_sharpe, hmm_fit, n_train_months, "
        " data_hash) "
        "VALUES (:event_id, :event_date, :trigger, :regime, "
        " CAST(:posterior AS JSONB), CAST(:blend_weights AS JSONB), "
        " :recommendation, :dissenting_view, CAST(:performance AS JSONB), "
        " :verdict, :value_added_sharpe, :hmm_fit, :n_train_months, "
        " :data_hash) "
        "ON CONFLICT (event_id) DO UPDATE SET "
        " event_date = EXCLUDED.event_date, "
        " trigger = EXCLUDED.trigger, "
        " regime = EXCLUDED.regime, "
        " posterior = EXCLUDED.posterior, "
        " blend_weights = EXCLUDED.blend_weights, "
        " recommendation = EXCLUDED.recommendation, "
        " dissenting_view = EXCLUDED.dissenting_view, "
        " performance = EXCLUDED.performance, "
        " verdict = EXCLUDED.verdict, "
        " value_added_sharpe = EXCLUDED.value_added_sharpe, "
        " hmm_fit = EXCLUDED.hmm_fit, "
        " n_train_months = EXCLUDED.n_train_months, "
        " data_hash = EXCLUDED.data_hash, "
        " computed_at = now()")
    written = 0
    n_persistable = 0
    try:
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            for r in rows:
                if not is_persistable(r):
                    log.warning("play_by_play_skip_not_persistable",
                                event_id=r.get("event_id"),
                                reason=_skip_reason(r))
                    continue
                n_persistable += 1
                # Commit each row in its OWN transaction so one bad row
                # (e.g. a NaN that fails the JSONB cast) cannot abort the
                # batch and block the rest.
                try:
                    await session.execute(stmt, {
                        "event_id": r["event_id"],
                        "event_date": r["event_date"],
                        "trigger": r["trigger"],
                        "regime": r.get("regime"),
                        "posterior": _json_safe(r.get("posterior")),
                        "blend_weights": _json_safe(r.get("blend_weights")),
                        "recommendation": r.get("recommendation"),
                        "dissenting_view": r.get("dissenting_view"),
                        "performance": _json_safe(r.get("performance")),
                        "verdict": r.get("verdict"),
                        "value_added_sharpe": _finite_or_none(
                            r.get("value_added_sharpe")),
                        "hmm_fit": r.get("hmm_fit", "point_in_time"),
                        "n_train_months": r.get("n_train_months"),
                        "data_hash": data_hash,
                    })
                    await session.commit()
                    written += 1
                except Exception as row_exc:  # noqa: BLE001
                    await session.rollback()
                    log.warning("play_by_play_persist_row_error",
                                event_id=r.get("event_id"),
                                error=str(row_exc))
    except Exception as exc:  # noqa: BLE001
        log.warning("play_by_play_persist_error", error=str(exc))
    log.info("play_by_play_persist_summary",
             rows_in=len(rows), persistable=n_persistable, written=written)
    return written


_STORED_COLS = (
    "event_id", "event_date", "trigger", "regime", "posterior",
    "blend_weights", "recommendation", "dissenting_view", "performance",
    "verdict", "value_added_sharpe", "hmm_fit", "n_train_months",
    "data_hash",
)


async def load_stored_events() -> list[dict]:
    """Every persisted event row, ordered by event_date. The read path
    behind the Council Performance Record page / slide.

    There is NO is_frozen filter: a play_by_play_events row only exists
    once it was a complete, settled fact (is_persistable gate at write
    time), so row existence IS the frozen flag. The WHERE computed_at IS
    NOT NULL guard is belt-and-braces (every persisted row has the
    server-default timestamp). Columns are mapped by explicit index
    rather than result.keys() to match the proven cache.py read pattern.
    Logs the row count so a "page shows empty but table has rows" report
    is diagnosable from the Render logs. Fail-open to []."""
    if not _DB_AVAILABLE:
        log.warning("play_by_play_load_db_unavailable")
        return []
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            result = await session.execute(text(
                "SELECT " + ", ".join(_STORED_COLS) + " "
                "FROM play_by_play_events "
                "WHERE computed_at IS NOT NULL ORDER BY event_date"))
            fetched = result.fetchall()
            out: list[dict] = []
            for r in fetched:
                row = {_STORED_COLS[i]: r[i] for i in range(len(_STORED_COLS))}
                if row.get("event_date") is not None:
                    row["event_date"] = str(row["event_date"])
                out.append(row)
            log.info("play_by_play_load_ok", n_events=len(out))
            return out
    except Exception as exc:  # noqa: BLE001
        log.warning("play_by_play_load_error", error=str(exc))
        return []


def scorecard(rows: list[dict]) -> dict:
    """Aggregate verdict across the evaluated events. Honest framing,
    not a win-rate boast: the count of events where the blend added
    value, out of those evaluable, plus the standing interpretation that
    the council's value is capital preservation in bear regimes rather
    than bull-market outperformance. Pure; computed from the stored
    rows so the UI and the agents read the same numbers."""
    evaluable = [r for r in rows
                 if r.get("value_added_sharpe") is not None]
    value_added = [r for r in evaluable
                   if (r.get("value_added_sharpe") or 0.0) > 0]
    return {
        "n_total": len(rows),
        "n_evaluable": len(evaluable),
        "n_value_added": len(value_added),
        "value_added_event_ids": [r["event_id"] for r in value_added],
        "framing": (
            "The regime-conditional council added value in "
            f"{len(value_added)} of {len(evaluable)} evaluated events. "
            "Its edge is capital preservation in genuine bear regimes, "
            "not bull-market outperformance: it positions defensively "
            "when the regime turns and gives back relative ground in "
            "sharp risk-on reversals (see the Liberation Day "
            "limitation)."),
    }


# ── cumulative chart series (precomputed, data_hash-cached) ──────────────────

_CHART_METRIC_KIND = "performance_chart"


def _cumulative(returns) -> list[float | None]:
    """Cumulative return (product of 1+r, minus 1) along a monthly
    series. A non-finite month does not advance the curve and records a
    null at that point so the chart line breaks cleanly rather than
    jumping."""
    out: list[float | None] = []
    growth = 1.0
    for r in returns:
        try:
            rf = float(r)
        except (TypeError, ValueError):
            out.append(None)
            continue
        if not np.isfinite(rf):
            out.append(None)
            continue
        growth *= (1.0 + rf)
        out.append(round(growth - 1.0, 6))
    return out


def compute_performance_chart(
    strategy_results: dict,
    hmm_result: dict,
    *,
    split_date: str = "2022-01-01",
) -> dict:
    """PURE: the post-split cumulative return series for the Council
    Performance Record chart. The regime-conditional blend is the Layer 3
    out-of-sample path (train pre-split, apply post-split); the benchmark
    and the classic 60/40 are their raw returns over the same months.
    Returns {series: [{date, regime_conditional, benchmark,
    classic_6040}], event_markers: [iso, ...]} or {} when the OOS path is
    unavailable. Pure given strategy_results + hmm_result, so it is unit-
    tested without a live HMM fit."""
    from tools.regime_meta_validation import out_of_sample_validation

    oos = out_of_sample_validation(
        strategy_results, hmm_result, split_date=split_date,
        return_series=True)
    dates = oos.get("test_dates") or []
    blend_monthly = oos.get("blend_monthly") or []
    if oos.get("error") or not dates or len(blend_monthly) != len(dates):
        return {}

    idx = pd.to_datetime(dates)
    bench = _series_for(strategy_results, _BENCHMARK_ID)
    classic = _series_for(strategy_results, _CLASSIC_6040_ID)
    bench_m = (bench.reindex(idx).to_numpy()
               if bench is not None else [None] * len(dates))
    classic_m = (classic.reindex(idx).to_numpy()
                 if classic is not None else [None] * len(dates))

    blend_cum = _cumulative(blend_monthly)
    bench_cum = _cumulative(bench_m)
    classic_cum = _cumulative(classic_m)
    series = [
        {"date": dates[t],
         "regime_conditional": blend_cum[t],
         "benchmark": bench_cum[t],
         "classic_6040": classic_cum[t]}
        for t in range(len(dates))
    ]
    lo, hi = dates[0], dates[-1]
    markers = [e["event_date"] for e in EVENTS if lo <= e["event_date"] <= hi]
    return {"series": series, "event_markers": markers}


async def refresh_performance_chart(data_hash: str) -> bool:
    """Render-side: fit the HMM on the live equity series, compute the
    post-2022 chart, and cache it under metric_kind 'performance_chart'
    keyed by data_hash. Fired by the same warm pipeline that refreshes
    the analytics and CIO recommendation; the read endpoint serves the
    cached row so no OOS recompute ever runs on a page load. Fail-open."""
    try:
        from tools.cache import get_latest_strategy_cache, get_monthly_returns
        from tools.precomputed_analytics import set_metric
        from tools.regime_detector import fit_hmm_historical
    except Exception as exc:  # noqa: BLE001
        log.warning("performance_chart_imports_unavailable", error=str(exc))
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
            log.warning("performance_chart_hmm_failed",
                        error=(hmm or {}).get("error"))
            return False
        chart = compute_performance_chart(sr, hmm)
        if not chart.get("series"):
            return False
        await set_metric(data_hash or "", _CHART_METRIC_KIND, chart,
                         source="play_by_play")
        log.info("performance_chart_cached",
                 points=len(chart["series"]),
                 markers=len(chart.get("event_markers") or []))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("performance_chart_refresh_failed", error=str(exc))
        return False


async def get_cached_performance_chart() -> dict | None:
    """The latest cached chart series for the read endpoint. Fail-open to
    None so the page renders its empty state cleanly before the first
    warm computes one."""
    try:
        from tools.precomputed_analytics import get_latest_metric
        return await get_latest_metric(_CHART_METRIC_KIND)
    except Exception as exc:  # noqa: BLE001
        log.warning("performance_chart_read_error", error=str(exc))
        return None
