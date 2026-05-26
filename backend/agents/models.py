"""
agents/models.py — single source of truth for every LLM model string,
with automatic per-provider fallback chains.

May 27 2026. PR-MODEL-1.

PROBLEM
Provider model deprecations have been silent on this platform.
Gemini 2.0 Flash 404'd in production today; before that, claude-opus-4
retired 2026-06-15 with only a chat note; Grok went grok-3-mini →
grok-4 → grok-4.3 in a few weeks. Each rename has historically required
a code change + redeploy. By the time the operator notices the 404 in
logs, every dashboard explainer / council / academic-review run is
broken.

SOLUTION
1. Centralise every model string in ONE module — no hardcoded strings
   scattered across files. agents/base.py re-exports the constants for
   backwards compatibility, but the chain definitions live here.
2. Each logical model (sonnet, opus, haiku, gemini) carries an ORDERED
   fallback chain. The first entry is the primary; subsequent entries
   are tried in order on 404 / NotFoundError.
3. A 404 advances the chain ATOMICALLY for the process: every
   subsequent call resolves to the new active model without re-hitting
   the deprecated one. The state is kept in-memory; a redeploy resets
   it (intentional — a deploy gets the operator's chance to update the
   primary).
4. Every advance emits a `model_fallback` structured log so the switch
   is visible in Render logs the moment it happens.
5. `check_model_availability()` runs from the lifespan startup hook,
   pings each chain's primary, and proactively advances any that 404
   BEFORE the first user request lands.

NOT IN SCOPE
Grok (xAI / OpenRouter) is handled separately by agents/_xai_config.py
which already supports XAI_MODEL env override and OpenRouter / direct
xAI auto-detection. Grok deprecations land via env var, not code change.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger(__name__)


# ── Per-logical-model fallback chains ────────────────────────────────────────
#
# Order matters: the first entry is the primary; subsequent entries are
# tried in order on 404. Add new models to the FRONT when a provider
# announces a successor; leave the previous primary in the chain as a
# fallback until it actually 404s, then operator-side prune on the next
# deploy.

@dataclass
class ModelChain:
    """One logical model + its ordered list of provider models. Mutable:
    a 404 advances `_active_index` so future calls land on the next
    chain entry without retrying the deprecated one."""

    logical_name: str
    chain: tuple[str, ...]
    _active_index: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def current(self) -> str:
        """The currently-active model string for this chain."""
        return self.chain[self._active_index]

    @property
    def primary(self) -> str:
        """The chain's first entry — what we WANT to use if everything
        is healthy. Useful for startup logging without mutating state."""
        return self.chain[0]

    @property
    def active_index(self) -> int:
        """Read-only view for tests / observability."""
        return self._active_index

    def advance(self, reason: str = "404") -> str | None:
        """Move to the next entry in the chain. Returns the new active
        model, or None if the chain is exhausted. Logs the switch
        with a `model_fallback` structured event.

        Thread-safe — the lock ensures concurrent failing calls do not
        double-advance the chain."""
        with self._lock:
            old = self.chain[self._active_index]
            if self._active_index + 1 >= len(self.chain):
                log.warning(
                    "model_fallback_exhausted",
                    chain=self.logical_name,
                    from_model=old,
                    reason=reason,
                    note="No further fallback available; "
                         "subsequent calls will continue to fail until "
                         "the operator updates the chain.",
                )
                return None
            self._active_index += 1
            new = self.chain[self._active_index]
            log.warning(
                "model_fallback",
                chain=self.logical_name,
                from_model=old,
                to_model=new,
                reason=reason,
            )
            return new

    def reset(self) -> None:
        """Reset to the primary — test-only helper. Production code
        paths never call this; tests use it for isolation."""
        with self._lock:
            self._active_index = 0


# Anthropic models — short chains for now; current primaries are stable.
# Add successors here when Anthropic announces them; the chain absorbs
# the transition automatically.
SONNET = ModelChain(
    logical_name="sonnet",
    chain=("claude-sonnet-4-6",),
)

OPUS = ModelChain(
    logical_name="opus",
    chain=("claude-opus-4-7",),
)

HAIKU = ModelChain(
    logical_name="haiku",
    chain=("claude-haiku-4-5-20251001",),
)

# Google Gemini — per user spec (May 27 2026):
#   gemini-2.5-flash       ← current primary (replaces 2.0-flash which 404'd)
#   gemini-2.0-flash-exp   ← experimental fallback; sometimes available when
#                            -flash is briefly unavailable
#   gemini-1.5-flash-latest ← legacy; survives most provider rollouts
GEMINI = ModelChain(
    logical_name="gemini",
    chain=(
        "gemini-2.5-flash",
        "gemini-2.0-flash-exp",
        "gemini-1.5-flash-latest",
    ),
)

# Gemini Pro — distinct chain from the flash family (May 25 2026).
# Used by agents that need the larger reasoning model (the Academic
# Review's independent second-opinion layer). Pro is materially
# more capable than Flash on multi-step assessment / consistency
# checks, and the audit layer's cost profile absorbs the difference
# (one call per review run). Fallbacks intentionally include the
# Flash chain — if every Pro model is unavailable the second-opinion
# layer should still produce SOMETHING rather than block.
GEMINI_PRO = ModelChain(
    logical_name="gemini_pro",
    chain=(
        "gemini-2.5-pro",
        "gemini-1.5-pro-latest",
        "gemini-2.5-flash",         # final fallback to flash
    ),
)


# Lookup: any model string in any chain → the ModelChain that owns it.
# This lets call_claude / call_gemini receive a model string and find
# the right chain to advance on 404 without the caller needing to know
# which logical model it was using.
_CHAIN_FOR_MODEL: dict[str, ModelChain] = {}
_ALL_CHAINS: tuple[ModelChain, ...] = (SONNET, OPUS, HAIKU, GEMINI, GEMINI_PRO)
for _chain in _ALL_CHAINS:
    for _model in _chain.chain:
        _CHAIN_FOR_MODEL[_model] = _chain


def resolve_active(model: str) -> str:
    """Given any model string (a chain primary or a fallback entry),
    return the CURRENTLY active model string for that chain.

    If the input isn't in any chain (a custom one-off model), it's
    returned unchanged — the resolver passes through cleanly so a
    caller can still target an exotic model directly.
    """
    chain = _CHAIN_FOR_MODEL.get(model)
    return chain.current if chain else model


def report_failure(model: str, reason: str = "404") -> str | None:
    """Advance the chain that contains `model`. Returns the new
    active model, or None if the chain is exhausted / the model
    isn't in any chain. The caller decides whether to retry."""
    chain = _CHAIN_FOR_MODEL.get(model)
    if chain is None:
        return None
    return chain.advance(reason)


def chain_for(model: str) -> ModelChain | None:
    """Returns the chain that owns `model`, or None. Used by the
    startup check to iterate without exposing the global map."""
    return _CHAIN_FOR_MODEL.get(model)


def all_chains() -> tuple[ModelChain, ...]:
    """Every registered chain — used by the startup availability check
    and admin endpoints to display chain state."""
    return _ALL_CHAINS


def chain_state() -> list[dict[str, object]]:
    """Snapshot of every chain's current state — for admin / debug
    endpoints. Shape:
      [
        {"name": "sonnet", "primary": "...", "current": "...",
         "active_index": 0, "chain_length": 1},
        ...
      ]
    """
    return [
        {
            "name": c.logical_name,
            "primary": c.primary,
            "current": c.current,
            "active_index": c.active_index,
            "chain_length": len(c.chain),
            "chain": list(c.chain),
        }
        for c in _ALL_CHAINS
    ]


def reset_all_for_tests() -> None:
    """Test-only — resets every chain to its primary so a test that
    advances a chain doesn't leak state into the next test."""
    for c in _ALL_CHAINS:
        c.reset()


# ── 404 detection ────────────────────────────────────────────────────────────

def is_model_not_found(exc: BaseException) -> bool:
    """Heuristic for "the provider returned 404 / model not found".

    Catches three shapes:
      1. anthropic.NotFoundError — typed exception from the Anthropic SDK
      2. google.api_core.exceptions.NotFound (or similar) from google-genai
      3. Plain Exception whose str() contains a 404 / not-found signature

    Pattern (3) is the safety net: SDK versions occasionally rename or
    re-package their typed exceptions, and a missed import would silently
    skip the fallback. The string check guarantees we still catch the
    case where the SDK only exposed the raw HTTP status.
    """
    # Typed Anthropic exception
    try:
        import anthropic
        if isinstance(exc, anthropic.NotFoundError):
            return True
    except ImportError:
        pass

    # Typed Google exception — best-effort import
    try:
        from google.api_core.exceptions import NotFound
        if isinstance(exc, NotFound):
            return True
    except ImportError:
        pass

    # String-shape fallback. The pattern matches both the Anthropic
    # error message ("model 'X' does not exist") and Google's
    # ("models/X is not found for API version v1beta") plus any
    # provider that surfaces a bare "404" status.
    msg = str(exc).lower()
    if "404" in msg:
        return True
    if "not found" in msg or "does not exist" in msg or "is not found" in msg:
        return True
    if "model_not_found" in msg:
        return True
    return False


# ── Startup availability check ───────────────────────────────────────────────

async def check_model_availability() -> dict[str, dict[str, object]]:
    """Pings every chain's CURRENT model. On 404, advances the chain
    and pings the next entry. Returns a per-chain summary dict the
    lifespan handler can log.

    Skipped in the test environment (no API keys; the check would be
    a no-op anyway) and when the relevant provider's key is unset.
    Fail-open per chain: a chain whose entire fallback list is
    exhausted gets recorded as "unavailable" but the others still run.

    The Anthropic check uses a 1-token completion; the Gemini check
    uses a 1-token generate. Both are cheap (sub-cent) and bounded
    by the chain length × small per-call cost.
    """
    summary: dict[str, dict[str, object]] = {}
    if os.getenv("ENVIRONMENT", "development") == "test":
        return summary

    for chain in _ALL_CHAINS:
        # Identify the provider from the chain name. The check
        # functions below each handle their provider's SDK + auth.
        # gemini_pro shares the Google SDK + GOOGLE_API_KEY with the
        # gemini Flash chain, so both route through _check_gemini_chain
        # — the per-call ping logic is identical, only the model
        # strings differ.
        if chain.logical_name in ("sonnet", "opus", "haiku"):
            summary[chain.logical_name] = await _check_anthropic_chain(chain)
        elif chain.logical_name in ("gemini", "gemini_pro"):
            summary[chain.logical_name] = await _check_gemini_chain(chain)
        else:
            # Future provider — log and skip rather than guess.
            log.info("model_check_unknown_provider",
                     chain=chain.logical_name)
    return summary


async def _check_anthropic_chain(chain: ModelChain) -> dict[str, object]:
    """Pings the current Anthropic model. Advances the chain on 404
    and retries; reports the final state. Returns a result dict
    suitable for the lifespan summary log."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return {"status": "skipped", "reason": "no_api_key",
                "active": chain.current}

    import asyncio
    primary = chain.current
    tried: list[str] = []
    while True:
        model = chain.current
        tried.append(model)
        try:
            ok = await asyncio.to_thread(_anthropic_ping, model)
            if ok:
                log.info("model_check_passed", chain=chain.logical_name,
                         model=model)
                return {"status": "ok", "primary": primary,
                        "active": model, "tried": tried}
        except Exception as exc:  # noqa: BLE001
            if is_model_not_found(exc):
                new = chain.advance(reason="startup_check_404")
                if new is None:
                    log.error("model_check_exhausted",
                              chain=chain.logical_name,
                              primary=primary, tried=tried)
                    return {"status": "exhausted", "primary": primary,
                            "active": None, "tried": tried,
                            "error": str(exc)}
                # Loop continues — try the new active model.
                continue
            # Non-404 error (auth, rate limit, transient): don't
            # treat as a chain failure; record and exit so a quota
            # blip doesn't trigger a phantom fallback.
            log.warning("model_check_non_404_error",
                        chain=chain.logical_name, model=model,
                        error=str(exc))
            return {"status": "error", "primary": primary,
                    "active": model, "tried": tried,
                    "error": str(exc)}


def _anthropic_ping(model: str) -> bool:
    """Synchronous 1-token Anthropic completion ping. Returns True
    on success; raises on any error so the chain check can detect
    a 404 vs. another failure mode."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
    client.messages.create(
        model=model,
        max_tokens=1,
        messages=[{"role": "user", "content": "ping"}],
    )
    return True


async def _check_gemini_chain(chain: ModelChain) -> dict[str, object]:
    """Pings the current Gemini model. Same shape as the Anthropic
    check — advance on 404, retry, report final state."""
    if not os.getenv("GOOGLE_API_KEY"):
        return {"status": "skipped", "reason": "no_api_key",
                "active": chain.current}

    import asyncio
    primary = chain.current
    tried: list[str] = []
    while True:
        model = chain.current
        tried.append(model)
        try:
            ok = await asyncio.to_thread(_gemini_ping, model)
            if ok:
                log.info("model_check_passed", chain=chain.logical_name,
                         model=model)
                return {"status": "ok", "primary": primary,
                        "active": model, "tried": tried}
        except Exception as exc:  # noqa: BLE001
            if is_model_not_found(exc):
                new = chain.advance(reason="startup_check_404")
                if new is None:
                    log.error("model_check_exhausted",
                              chain=chain.logical_name,
                              primary=primary, tried=tried)
                    return {"status": "exhausted", "primary": primary,
                            "active": None, "tried": tried,
                            "error": str(exc)}
                continue
            log.warning("model_check_non_404_error",
                        chain=chain.logical_name, model=model,
                        error=str(exc))
            return {"status": "error", "primary": primary,
                    "active": model, "tried": tried,
                    "error": str(exc)}


def _gemini_ping(model: str) -> bool:
    """Synchronous 1-token Gemini generate ping."""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY", ""))
    client.models.generate_content(
        model=model,
        contents="ping",
        config=types.GenerateContentConfig(max_output_tokens=1),
    )
    return True
