"""
agents/usage.py — per-request AI token-usage accumulator.

Every AI call wrapper (call_claude, call_gemini, the Grok helpers)
reports its token usage here via record_usage(). An endpoint that wants
to log the cost of an interaction brackets its work with
start_usage_capture() … collect_usage(); collect_usage() returns the
aggregated totals (and a per-agent breakdown) for the agent_interactions
row.

Mechanism: a ContextVar list, the same pattern as the harness-metrics
capture. start_usage_capture() seeds the list BEFORE any parallel agent
threads are spawned, so the contextvars.copy_context() each thread runs
under shares that one list by reference and every thread's appends land
in it. record_usage() is a silent no-op when no capture is active, so
the call wrappers can always call it unconditionally.

set_current_agent() tags subsequent records with an agent label so the
council per-agent cost breakdown can be reconstructed; it is per-context
(each copied specialist thread sets its own).
"""
from __future__ import annotations

import contextvars
from typing import Any

import structlog

from config import calculate_cost

log = structlog.get_logger(__name__)

_usage_ctx: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "ai_usage", default=None)
_agent_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "ai_usage_agent", default="unknown")


def start_usage_capture() -> None:
    """Begin capturing token usage for the current request. Call once,
    before any agent work (and before parallel agent threads spawn)."""
    _usage_ctx.set([])


def set_current_agent(label: str) -> None:
    """Tag every subsequent record_usage() in this context with `label`."""
    _agent_ctx.set(label)


def record_usage(
    model: str, input_tokens: Any, output_tokens: Any,
) -> None:
    """
    Report one AI call's token usage. A silent no-op when no capture is
    active, so call wrappers invoke it unconditionally. Fail-open — a
    malformed count is logged and dropped, never raised.
    """
    bucket = _usage_ctx.get()
    if bucket is None:
        return
    try:
        in_tok = int(input_tokens or 0)
        out_tok = int(output_tokens or 0)
    except (TypeError, ValueError):
        log.warning("usage_record_bad_counts", model=model)
        return
    bucket.append({
        "agent": _agent_ctx.get(),
        "model": model,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "estimated_cost_usd": calculate_cost(model, in_tok, out_tok),
    })


def collect_usage() -> dict[str, Any]:
    """
    Aggregate the captured usage for the agent_interactions row:
      {input_tokens, output_tokens, estimated_cost_usd, model_used,
       per_agent: {label: {input_tokens, output_tokens,
                            estimated_cost_usd, calls}}}
    Returns zeroed/None fields when nothing was captured. model_used is
    the single model when only one was used, else "multiple".
    """
    bucket = _usage_ctx.get() or []
    if not bucket:
        return {"input_tokens": None, "output_tokens": None,
                "estimated_cost_usd": None, "model_used": None,
                "per_agent": {}}

    in_tok = sum(r["input_tokens"] for r in bucket)
    out_tok = sum(r["output_tokens"] for r in bucket)
    costs = [r["estimated_cost_usd"] for r in bucket
             if r["estimated_cost_usd"] is not None]
    total_cost = round(sum(costs), 6) if costs else None
    models = {r["model"] for r in bucket}
    model_used = (next(iter(models)) if len(models) == 1 else "multiple")

    per_agent: dict[str, dict] = {}
    for r in bucket:
        a = per_agent.setdefault(r["agent"], {
            "input_tokens": 0, "output_tokens": 0,
            "estimated_cost_usd": 0.0, "calls": 0})
        a["input_tokens"] += r["input_tokens"]
        a["output_tokens"] += r["output_tokens"]
        a["estimated_cost_usd"] += r["estimated_cost_usd"] or 0.0
        a["calls"] += 1
    for a in per_agent.values():
        a["estimated_cost_usd"] = round(a["estimated_cost_usd"], 6)

    return {
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "estimated_cost_usd": total_cost,
        "model_used": model_used,
        "per_agent": per_agent,
    }
