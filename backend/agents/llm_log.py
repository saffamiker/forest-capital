"""
agents/llm_log.py — single emit point for the `llm_call` structured log.

May 25 2026. PR 1 of the LLM token-audit workstream.

PURPOSE
Every LLM call across the codebase (Anthropic, Gemini, Grok via OpenAI-
compatible SDK, direct SDK calls inside research_agent / audit_layer2 /
academic_advisor) emits the same structured log event so token leaks
become visible in Render logs without any further code change:

    {
      "event": "llm_call",
      "function": "<name>",
      "model": "<model string>",
      "trigger": "<caller-provided>",
      "input_tokens": N,
      "output_tokens": N,
      "hash_gate": true | false
    }

WHY ONE HELPER
The codebase has 38+ LLM call sites across three providers and four
direct-SDK paths. A single helper keeps the emit shape identical
everywhere — a downstream log query like
`event:llm_call AND hash_gate:false AND trigger:harness_evaluator`
catches every variant of the leak in one filter, regardless of which
provider or which wrapper the call went through.

THE TRIGGER FIELD
Caller-supplied free-form string. Convention: lower_snake_case naming
the caller's analytical context — for example "council_specialist:
equity_analyst", "harness_evaluator", "statistical_audit_layer2",
"research_digest", "strategy_characterisation". When omitted the
default "unspecified" makes un-labeled call sites grep-able so we can
walk through and label them over time.

THE HASH_GATE FIELD
Boolean saying "was there a data_hash or TTL comparison BEFORE this
call fired". Default False — most call sites today have no gate. The
field exists so future gates (PRs 3-5 of this workstream) become
visible without further log-line changes: a call site that flips from
hash_gate=false to hash_gate=true is the gate landing in production.

FAIL-OPEN
A log emit failure must never break an LLM call. Every catch swallows
silently — telemetry is best-effort, the response to the user matters
more. Same discipline as agents/usage.py's record_usage.
"""
from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

# Default trigger label for call sites that haven't been threaded yet.
# Grep-able sentinel: a log query for `trigger:unspecified` surfaces the
# un-labeled remainder as the codebase migrates.
TRIGGER_UNSPECIFIED = "unspecified"


def log_llm_call(
    *,
    function: str,
    model: str,
    trigger: str = TRIGGER_UNSPECIFIED,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    hash_gate: bool = False,
    **extra: object,
) -> None:
    """
    Emit one `llm_call` event for one LLM SDK call.

    Call AFTER the SDK returns so token counts are real. None tokens
    (a failed/aborted call before usage is reported) are emitted as
    0 so the log shape stays consistent.

    `extra` lets callers attach context-specific fields (n_searches,
    n_tool_uses, etc.) without changing the helper signature.
    """
    try:
        log.info(
            "llm_call",
            function=function,
            model=model,
            trigger=trigger,
            input_tokens=int(input_tokens or 0),
            output_tokens=int(output_tokens or 0),
            hash_gate=bool(hash_gate),
            **extra,
        )
    except Exception:  # noqa: BLE001 — telemetry must never break a call
        pass
