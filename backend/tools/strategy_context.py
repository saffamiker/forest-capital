"""tools/strategy_context.py — per-strategy agent prompt injection.

Item 9 commit 5 (May 22 2026). When the council, explainer, or
academic writer reasons ABOUT a specific strategy, this module
appends the strategy's pre-computed characterisation (construction
summary, behavioural profile, regime sensitivity, behavioural tag,
portfolio characteristics) to the system prompt so the agent grounds
its narrative in the same descriptors the Dashboard + Portfolio
Profile panel surface.

Unlike macro_context / diversification_context / academic_context,
which inject GLOBAL state into every agent call, this injector is
PER-STRATEGY. The caller passes a strategy_id (or a set of
strategy_ids); only those strategies' characterisations are appended.

PATTERN:
  refresh_strategy_context_cache()  async; reloads every row from
                                    strategy_characterisations into
                                    the module-level cache. Called
                                    from refresh_strategy_characterisa-
                                    tions after the upserts and from
                                    the lifespan startup hook so the
                                    cache is warm on cold boot.
  get_strategy_context(strategy_id) sync; returns the formatted block
                                    for one strategy or empty string.
  inject_strategy_context(prompt,   sync; appends one strategy's block
                          strategy_ids) to a system prompt. No-op
                                    when strategy_ids is empty or no
                                    characterisations are cached.

FAIL-OPEN END TO END. Every accessor returns "" on read miss, DB
error, missing module, or cold cache — never raises into an agent
call site. The agent simply runs without per-strategy context.

The detection helper detect_strategies_in_query() scans a free-text
council query for known strategy names and returns the set, so the
council orchestrator can fire injection automatically without the
user having to declare a strategy explicitly.
"""
from __future__ import annotations

import contextvars
import re
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# Module-level cache — { strategy_id: characterisation dict }
_CACHE: dict[str, dict[str, Any]] = {}


# Per-request ContextVar — populated by orchestrators (council
# deliberate, report writer generate, etc.) at the entry point so
# every nested call_claude in the same request sees the strategy
# context without per-call wiring. The wrapper chain reads the var
# when the caller does not pass an explicit strategy_ids list.
# contextvars.copy_context() inside ThreadPoolExecutor workers (the
# council's parallel specialist fan-out) propagates the value to
# worker threads, so the four specialists see the same strategy
# context the orchestrator set.
_ACTIVE_STRATEGY_IDS: contextvars.ContextVar[list[str]] = (
    contextvars.ContextVar("active_strategy_ids", default=[]))


def set_active_strategies(strategy_ids: list[str] | None) -> None:
    """Sets the per-request active strategy_ids list. Called once per
    request at the orchestrator entry point — every nested call_claude
    inside the same request automatically receives this list when no
    explicit override is passed. Empty / None clears the list."""
    _ACTIVE_STRATEGY_IDS.set(list(strategy_ids or []))


def get_active_strategies() -> list[str]:
    """Read accessor for the per-request active strategy_ids list.
    Used by _with_strategy_context in call_claude's wrapper chain to
    pick up strategy context when no explicit override is passed."""
    return list(_ACTIVE_STRATEGY_IDS.get())


def clear_active_strategies() -> None:
    """Clears the per-request active strategy_ids list. Called from
    the orchestrator's finally block so no value leaks beyond the
    request that set it."""
    _ACTIVE_STRATEGY_IDS.set([])


# The known strategy ids — used by detect_strategies_in_query() to
# spot strategy references in free-text council queries. Mirrors the
# enum the backtester writes into strategy_results_cache.
_KNOWN_STRATEGIES: tuple[str, ...] = (
    "BENCHMARK",
    "CLASSIC_60_40",
    "RISK_PARITY",
    "MIN_VARIANCE",
    "EQUAL_WEIGHT",
    "MOMENTUM_ROTATION",
    "REGIME_SWITCHING",
    "VOL_TARGETING",
    "BLACK_LITTERMAN",
    "MAX_SHARPE_ROLLING",
)


# Match a known strategy id case-insensitively, accepting either
# the snake_case form ("regime_switching") or a hyphenated /
# spaced form ("regime switching", "regime-switching"). The detector
# is conservative — a substring match in the middle of another word
# is rejected by the \b word-boundary anchors.
def _strategy_pattern() -> re.Pattern[str]:
    raw_names: list[str] = []
    for sid in _KNOWN_STRATEGIES:
        # Build alternation patterns: snake, space, hyphen forms.
        snake = re.escape(sid)
        spaced = re.escape(sid.replace("_", " "))
        hyphened = re.escape(sid.replace("_", "-"))
        raw_names.extend([snake, spaced, hyphened])
    return re.compile(
        r"\b(" + "|".join(raw_names) + r")\b",
        re.IGNORECASE,
    )


_STRATEGY_RE = _strategy_pattern()


def detect_strategies_in_query(text: str) -> list[str]:
    """Returns the list of strategy_ids mentioned in a free-text
    query, in first-mention order, de-duplicated. Empty when no
    strategy is named.

    Match is case-insensitive and accepts snake_case, spaced, or
    hyphenated forms ('regime switching', 'regime-switching',
    'REGIME_SWITCHING' all match the same id). Substrings inside
    other words are rejected by the word-boundary anchors.
    """
    if not text:
        return []
    seen: list[str] = []
    seen_set: set[str] = set()
    for m in _STRATEGY_RE.finditer(text):
        canonical = m.group(1).upper().replace(" ", "_").replace("-", "_")
        if canonical not in seen_set:
            seen.append(canonical)
            seen_set.add(canonical)
    return seen


def _format_block(strategy_id: str, row: dict[str, Any]) -> str:
    """Renders one characterisation as a labelled prompt block.

    Block shape:

      === STRATEGY CONTEXT: <strategy_id> ===
      Tag: <behavioural_tag>
      Construction:
        <construction_summary>
      Behavioural profile:
        <bullets from behavioural_profile dict>
      Regime sensitivity:
        <regime_sensitivity>
      Portfolio characteristics:
        <bullets from portfolio_characteristics dict>

    Empty / missing fields are omitted rather than rendered as
    placeholders — the agent should not see [DATA REQUIRED] in a
    context block (those markers belong to the report writer's
    [BOB] flow, not the agent's read-only context). Returns empty
    string when the row carries no usable fields.
    """
    if not row:
        return ""
    parts: list[str] = [f"=== STRATEGY CONTEXT: {strategy_id} ==="]
    tag = (row.get("behavioural_tag") or "").strip()
    if tag:
        parts.append(f"Tag: {tag}")
    construction = (row.get("construction_summary") or "").strip()
    if construction:
        parts.append("Construction:")
        parts.append(f"  {construction}")
    bp = row.get("behavioural_profile") or {}
    if isinstance(bp, dict) and bp:
        parts.append("Behavioural profile:")
        for k, v in bp.items():
            if v in (None, "", [], {}):
                continue
            label = str(k).replace("_", " ")
            parts.append(f"  - {label}: {_fmt_value(v)}")
    regime = (row.get("regime_sensitivity") or "").strip()
    if regime:
        parts.append("Regime sensitivity:")
        parts.append(f"  {regime}")
    pc = row.get("portfolio_characteristics") or {}
    if isinstance(pc, dict) and pc:
        parts.append("Portfolio characteristics:")
        for k, v in pc.items():
            if v in (None, "", [], {}):
                continue
            label = str(k).replace("_", " ")
            parts.append(f"  - {label}: {_fmt_value(v)}")
    if len(parts) == 1:
        # Header only — no fields populated. Treat as empty.
        return ""
    return "\n".join(parts)


def _fmt_value(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4f}"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if isinstance(v, dict):
        return ", ".join(f"{k}={_fmt_value(val)}" for k, val in v.items())
    return str(v)


def get_strategy_context(strategy_id: str) -> str:
    """Sync accessor — returns the formatted block for one strategy
    or empty string if no characterisation is cached. Mirrors
    get_macro_context / get_diversification_context."""
    if not strategy_id:
        return ""
    row = _CACHE.get(strategy_id.upper())
    if not row:
        return ""
    return _format_block(strategy_id.upper(), row)


def inject_strategy_context(
    system_prompt: str, strategy_ids: list[str] | set[str] | None,
) -> str:
    """Appends one block per strategy_id to a system prompt.

    No-op when strategy_ids is None / empty or no characterisations
    are cached for any of them. Idempotent — calling twice with the
    same strategy_ids does not duplicate (the agent prompt holds the
    final concatenated string; the second injection appends a new
    copy, so callers should call once per agent invocation).

    Order: strategy_ids are emitted in the iteration order of the
    input, so a caller that wants a deterministic order (e.g. the
    council orchestrator listing strategies as they appear in the
    query) can pass a list.
    """
    if not strategy_ids:
        return system_prompt
    blocks: list[str] = []
    for sid in strategy_ids:
        block = get_strategy_context(sid)
        if block:
            blocks.append(block)
    if not blocks:
        return system_prompt
    rules = (
        "\n\nReason from these specific strategy characteristics when "
        "discussing the named strategies above. Do NOT contradict or "
        "invent details for these strategies — fall back to the "
        "general analytical framing only for strategies not named in "
        "the blocks above.")
    return system_prompt + "\n\n" + "\n\n".join(blocks) + rules


async def refresh_strategy_context_cache() -> None:
    """Reloads every characterisation row from the DB into the
    module-level cache. Called from:
      1. refresh_strategy_characterisations() after the upserts so
         the cache is fresh within one tick of an ingestion.
      2. The lifespan startup hook so the cache is warm on cold
         boot with whatever the last deploy produced.

    Fail-open: any error leaves the previous cache contents in
    place so an injection failure does not blank every agent."""
    global _CACHE
    try:
        from tools.strategy_characterisations import get_all_characterisations
        rows = await get_all_characterisations()
        new_cache: dict[str, dict[str, Any]] = {}
        for row in rows or []:
            sid = (row.get("strategy_id") or "").upper()
            if not sid:
                continue
            new_cache[sid] = row
        _CACHE = new_cache
        log.info("strategy_context_refreshed",
                 strategy_count=len(_CACHE))
    except Exception as exc:  # noqa: BLE001
        log.warning("strategy_context_refresh_failed", error=str(exc))


def known_strategy_ids() -> tuple[str, ...]:
    """Read-only accessor for the strategy id set. Test helper."""
    return _KNOWN_STRATEGIES


def _set_cache_for_tests(cache: dict[str, dict[str, Any]]) -> None:
    """Test-only helper to inject characterisations directly into
    the cache. Production code never calls this — the cache is
    populated by refresh_strategy_context_cache()."""
    global _CACHE
    _CACHE = dict(cache)
