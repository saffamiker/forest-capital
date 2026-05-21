"""
tools/macro_context.py — current-conditions context block for agent prompts.

FEATURE 2 (May 21 2026), Commit 3/5. The research agent (Commit 1)
produces a daily macro digest; the research engine (Commit 2) persists
it. This module is the read-and-inject layer the council and
academic_review prompts consume.

USAGE PATTERN — mirror of tools.academic_context:
  get_macro_context()      sync accessor returning the cached context
                           string (empty when no digest is current)
  inject_macro_context(p)  appends the context to a system prompt; a
                           no-op when the cache is empty, so every
                           agent can call it unconditionally
  refresh_macro_context()  async — re-reads the latest completed
                           digest from the engine. Called from the
                           lifespan startup hook and from research_
                           engine.run_research at the end of every
                           successful run, so the cache reflects the
                           freshest digest within one tick of land.

CACHE SEMANTICS. One dict, one key ("text"). The cache reads what
research_engine.get_latest_digest returns; freshness is the engine's
problem (a digest > 24h old still flows into agent prompts — better
to reason against last week's macro than nothing). The dashboard
widget surfaces the generated_at timestamp so the user knows how
fresh the figures are.

FAIL-OPEN END TO END. A database error during refresh leaves the
previous cache contents in place. An empty cache injects nothing.
No agent ever fails because the macro layer is unavailable.

CONTEXT BLOCK SHAPE — what an agent sees:

  === CURRENT MACRO CONDITIONS (last 7 days as of <timestamp>) ===
  Summary: <2-3 sentence overview>

  Key signals:
    • [monetary_policy] Fed minutes signalled patience on rate cuts.
        → Implication: less near-term IG duration tailwind.
        Source: https://federalreserve.gov/...
    • [inflation]       CPI print 3.1% vs 3.2% expected.
        → Implication: dovish for both equity and IG.
        Source: https://bls.gov/...
    ...

  Regime: <single paragraph regime read>

  Reason from these signals when relevant. Do NOT invent macro
  conditions absent from this block — historical reasoning is your
  default for anything not captured here.

Agents read this AFTER their system prompt and BEFORE the user
message, so the conditions frame their analysis without overriding
their role-specific instructions.
"""
from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger(__name__)


# Module-level cache, mirroring academic_context._CONTEXT_CACHE. A
# dict so a future refresh can swap the string atomically without a
# Python-level lock.
_CACHE: dict[str, str] = {"text": ""}


def _format_digest_block(digest: dict[str, Any] | None) -> str:
    """Renders a digest dict into the labelled context block agents
    read. Returns an empty string for a missing / failed digest so
    inject_macro_context becomes a no-op."""
    if not digest:
        return ""
    summary = (digest.get("summary_text") or "").strip()
    regime = (digest.get("regime_implication") or "").strip()
    signals = digest.get("key_signals") or []
    generated_at = digest.get("generated_at") or "unknown"

    # A digest with no summary AND no signals AND no regime is
    # effectively empty — render nothing rather than a hollow block
    # that takes space in every agent's input window for no value.
    if not summary and not signals and not regime:
        return ""

    lines: list[str] = [
        "",
        f"=== CURRENT MACRO CONDITIONS (last 7 days as of {generated_at}) ===",
    ]
    if summary:
        lines.append(f"Summary: {summary}")
        lines.append("")
    if signals:
        lines.append("Key signals:")
        for sig in signals:
            if not isinstance(sig, dict):
                continue
            cat = str(sig.get("category") or "other")
            stext = str(sig.get("signal") or "").strip()
            imp = str(sig.get("implication") or "").strip()
            url = str(sig.get("source_url") or "").strip()
            if not stext:
                continue
            lines.append(f"  - [{cat}] {stext}")
            if imp:
                lines.append(f"      Implication: {imp}")
            if url:
                lines.append(f"      Source: {url}")
        lines.append("")
    if regime:
        lines.append(f"Regime read: {regime}")
        lines.append("")
    lines.append(
        "Reason from these signals when relevant. Do NOT invent "
        "macro conditions absent from this block — historical "
        "reasoning is your default for anything not captured here."
    )
    return "\n".join(lines)


def get_macro_context() -> str:
    """Sync accessor — returns the cached formatted context block.
    Empty string until refresh_macro_context has populated the cache
    (cold deploy state). Every injection site calls this; an empty
    string makes inject_macro_context a no-op."""
    return _CACHE["text"]


def inject_macro_context(system_prompt: str) -> str:
    """Append the macro context to a system prompt. A no-op when the
    cache is empty, so every agent can call it unconditionally — a
    cold deploy with no digest yet runs text-only, identical to the
    pre-macro behaviour."""
    ctx = get_macro_context()
    return system_prompt + ctx if ctx else system_prompt


async def refresh_macro_context() -> None:
    """Re-reads the latest completed digest from the research engine
    and updates the in-memory cache. Called from:
      1. The lifespan startup hook (after trigger_research_async
         fires — gives the cache its first value when a previous
         deploy already produced a digest).
      2. The end of research_engine.run_research on successful runs,
         so a fresh digest flows into agent prompts within one tick.

    Fail-open: any error leaves the previous cache contents in place
    so an injection failure does not blank out every agent."""
    try:
        from tools.research_engine import get_latest_digest
        digest = await get_latest_digest()
        new_block = _format_digest_block(digest)
        previous_len = len(_CACHE["text"])
        _CACHE["text"] = new_block
        log.info("macro_context_refreshed",
                 has_digest=bool(new_block),
                 chars=len(new_block),
                 previous_chars=previous_len)
    except Exception as exc:  # noqa: BLE001
        # Fail-open — leave the previous cache contents in place. A
        # persistently broken refresh is visible in the logs.
        log.warning("macro_context_refresh_failed", error=str(exc))


def _set_cache_for_test(text: str) -> None:
    """Testing hook — lets a test write a known context block into the
    cache without monkeypatching the read accessor. NOT for production
    callers. Used by tests/test_macro_context.py (Commit 5)."""
    _CACHE["text"] = text
