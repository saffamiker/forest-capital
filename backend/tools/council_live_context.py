"""tools/council_live_context.py: page-scoped live context for the
"Ask a question" council.

When a user asks the council a question from one of the three
council-facing landing pages, the frontend passes a `context_scope`. The
endpoint resolves that scope here into a small JSON block pulled ENTIRELY
from the existing warm caches — never a recompute, never a base64 image —
and the CIO grounds its synthesis in it. A user on the CIO Live
Recommendation tile can then ask "why is the blend defensive?" and have
the council reason from the same live numbers the tile is showing.

Each scope function is fail-open: any cache miss, DB error, or cold
deploy returns None (or a partial dict), and the council simply proceeds
without the page block — identical to the pre-scope behaviour.

Scopes (the values the request's context_scope field accepts):
  recommendation  — the CIO Live Recommendation tile
  performance     — the Council Performance Record page
  prediction      — the Forward Confidence Projection tile

An unknown scope resolves to None so a stale frontend can never break a
legitimate question.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

import structlog

log = structlog.get_logger(__name__)


async def recommendation_context() -> dict[str, Any] | None:
    """Scope "recommendation" — the live CIO recommendation tile.

    Assembled entirely from cache (no recompute):
      - the cached four-component recommendation (signal, recommendation,
        dissenting view, confidence, key risk, limitations) from
        cio_recommendations;
      - the live regime read (regime + posterior confidence) and the
        macro inputs (VIX, yield-curve slope, equity trend, credit
        spread) from detect_current_regime() — the platform's shared
        15-minute regime cache;
      - the live regime-conditional blend weights, read from the cached
        forward_projection metric (the same prob-weighted blend), rather
        than recomputing an HMM fit.
    Returns None only when nothing at all was available.
    """
    out: dict[str, Any] = {}

    try:
        from tools.cio_recommendation import get_latest_recommendation
        rec = await get_latest_recommendation()
        if rec:
            out["recommendation"] = {
                "signal": rec.get("signal"),
                "recommendation": rec.get("recommendation"),
                "dissenting_view": rec.get("dissenting_view"),
                "key_risk": rec.get("key_risk"),
                "confidence": rec.get("confidence"),
                "limitations": rec.get("limitations"),
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("scope_recommendation_read_failed", error=str(exc))

    try:
        import asyncio

        from tools.regime_detector import detect_current_regime
        live = await asyncio.to_thread(detect_current_regime)
        if live:
            regime = live.get("hmm_regime")
            probs = live.get("hmm_probabilities") or {}
            conf = probs.get(regime) if regime else None
            out["regime"] = {
                "regime": regime,
                "confidence": conf,
                "posterior": probs,
                "vix": live.get("vix_level"),
                "yield_curve_slope": live.get("yield_curve_slope"),
                "equity_trend": live.get("equity_trend"),
                "credit_spread": live.get("credit_spread"),
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("scope_regime_read_failed", error=str(exc))

    try:
        from tools.regime_meta_forward import get_cached_forward_projection
        proj = await get_cached_forward_projection()
        if proj and proj.get("blend_weights"):
            out["blend_weights"] = proj["blend_weights"]
    except Exception as exc:  # noqa: BLE001
        log.warning("scope_blend_read_failed", error=str(exc))

    return out or None


async def performance_context() -> dict[str, Any] | None:
    """Scope "performance" — the Council Performance Record page.

    From cache (no recompute): all nine frozen play-by-play event rows,
    the cached OOS Sharpe summary (blend / benchmark / equal-weight Sharpe
    + the 2-of-9 value-add count), and the scorecard's honest framing.
    Returns None when no events are stored yet.
    """
    try:
        from tools.play_by_play import (
            get_cached_oos_summary, load_stored_events, scorecard,
        )
        events = await load_stored_events()
    except Exception as exc:  # noqa: BLE001
        log.warning("scope_events_read_failed", error=str(exc))
        return None
    if not events:
        return None

    slim = [
        {
            "event_id": e.get("event_id"),
            "event_date": e.get("event_date"),
            "regime": e.get("regime"),
            "recommendation": e.get("recommendation"),
            "verdict": e.get("verdict"),
            "value_added_sharpe": e.get("value_added_sharpe"),
        }
        for e in events
    ]
    out: dict[str, Any] = {"events": slim, "scorecard": scorecard(events)}

    try:
        summary = await get_cached_oos_summary()
        if summary:
            out["oos_summary"] = {
                k: summary.get(k) for k in (
                    "blend", "benchmark", "equal_weight",
                    "value_add_events", "total_events")
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("scope_oos_summary_read_failed", error=str(exc))

    return out


async def prediction_context() -> dict[str, Any] | None:
    """Scope "prediction" — the Forward Confidence Projection tile.

    From cache (no recompute): the forward Monte Carlo bands, P(outperform
    benchmark) at 1/3/6/12 months, the live regime + probability, the
    blend weights, and the HMM transition matrix that drives the
    simulation. The limitations note is reused from the cached
    recommendation so the disclosure stays single-sourced. Returns None
    when no projection is cached yet.
    """
    try:
        from tools.regime_meta_forward import get_cached_forward_projection
        proj = await get_cached_forward_projection()
    except Exception as exc:  # noqa: BLE001
        log.warning("scope_projection_read_failed", error=str(exc))
        return None
    if not proj:
        return None

    out: dict[str, Any] = {
        "horizons_months": proj.get("horizons_months"),
        "p_outperform": proj.get("p_outperform"),
        "bands": proj.get("bands"),
        "blend_weights": proj.get("blend_weights"),
        "regime": proj.get("regime"),
        "regime_probability": proj.get("regime_probability"),
        "transition_matrix": proj.get("transition_matrix"),
    }

    try:
        from tools.cio_recommendation import get_latest_recommendation
        rec = await get_latest_recommendation()
        if rec and rec.get("limitations"):
            out["limitations"] = rec["limitations"]
    except Exception as exc:  # noqa: BLE001
        log.warning("scope_prediction_limitations_read_failed", error=str(exc))

    return out


# ── scope dispatch ──────────────────────────────────────────────────────────

_SCOPE_FUNCS: dict[str, Callable[[], Awaitable[dict[str, Any] | None]]] = {
    "recommendation": recommendation_context,
    "performance": performance_context,
    "prediction": prediction_context,
}


def known_scopes() -> tuple[str, ...]:
    """The scope identifiers the council injects context for."""
    return tuple(_SCOPE_FUNCS)


async def get_scope_context(scope: str | None) -> dict[str, Any] | None:
    """Resolve a page scope to its live cached context block. An unknown
    or None scope returns None (no injection); any resolution error is
    swallowed so a cache problem never blocks a council query."""
    fn = _SCOPE_FUNCS.get(scope or "")
    if fn is None:
        return None
    try:
        return await fn()
    except Exception as exc:  # noqa: BLE001
        log.warning("council_scope_context_failed", scope=scope, error=str(exc))
        return None
