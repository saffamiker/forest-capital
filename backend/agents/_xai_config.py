"""
agents/_xai_config.py

Shared xAI / OpenRouter provider resolver for the two Grok-consuming
agents (contrarian_analyst, explainer_agent).

Why this module exists:
  Forest Capital's Render deployment originally used a direct xAI key
  (`xai-...`) against `https://api.x.ai/v1`. The team later switched to
  an OpenRouter key (`sk-or-...`) for cost routing — OpenRouter brokers
  a single billing relationship across Grok + several Claude models.
  The two providers share the OpenAI-compatible request shape but
  differ in three places:

    Direct xAI:   base_url = https://api.x.ai/v1        model = grok-4
    OpenRouter:   base_url = https://openrouter.ai/api/v1  model = x-ai/grok-4

  Auto-detection by API key prefix keeps both agents free of provider-
  branching code: they call `resolve_xai_config()` once at request
  time, get back the `(api_key, base_url, model, provider)` tuple,
  and POST `{base_url}/chat/completions` with the resolved model.

Override behaviour:
  XAI_BASE_URL — if set, takes precedence over the prefix-based
                 auto-detection. Used in tests + the rare deploy where
                 the team wants to force one provider regardless of
                 which key happens to be on Render.
  XAI_MODEL    — same idea for the model name. When unset, the
                 provider's canonical model is used.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import structlog

log = structlog.get_logger(__name__)


# Canonical endpoints + models per provider. Kept in this module so the
# two agents stay in lockstep — when xAI/OpenRouter retire a model alias
# we update one constant here. grok-3-mini was retired on OpenRouter
# (404 Not Found) and replaced with grok-4 — May 2026.
_DIRECT_XAI_BASE_URL = "https://api.x.ai/v1"
_DIRECT_XAI_MODEL = "grok-4"
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_OPENROUTER_MODEL = "x-ai/grok-4"


@dataclass(frozen=True)
class XAIConfig:
    """Resolved provider config for one Grok request."""
    api_key: str
    base_url: str         # e.g. "https://api.x.ai/v1" — NO trailing slash, NO /chat/completions
    chat_url: str         # full URL including /chat/completions
    model: str
    provider: str         # "direct_xai" | "openrouter" | "unknown"


def _detect_provider(api_key: str) -> tuple[str, str, str]:
    """
    Returns (base_url, model, provider) based on the key prefix.

    Prefix conventions documented by each provider's API docs:
      `sk-or-...`  → OpenRouter (routes through their broker)
      `xai-...`    → direct xAI (Anthropic-style prefix scheme)

    An unknown prefix falls back to direct xAI on the principle that
    the historical default of this codebase is direct — and a wrong
    base_url surfaces immediately as a 401/404 rather than silently
    routing through someone else's broker.
    """
    if api_key.startswith("sk-or-"):
        return _OPENROUTER_BASE_URL, _OPENROUTER_MODEL, "openrouter"
    if api_key.startswith("xai-"):
        return _DIRECT_XAI_BASE_URL, _DIRECT_XAI_MODEL, "direct_xai"
    return _DIRECT_XAI_BASE_URL, _DIRECT_XAI_MODEL, "unknown"


def resolve_xai_config() -> XAIConfig | None:
    """
    Returns the resolved Grok config, or None if no XAI_API_KEY is set.

    Auto-detection by API key prefix can be overridden by setting
    XAI_BASE_URL (and optionally XAI_MODEL) explicitly. The override
    path exists for operator emergencies — if OpenRouter starts
    returning 5xx on grok-4, the team can pin XAI_BASE_URL +
    XAI_MODEL on Render and route through direct xAI without a redeploy.
    """
    api_key = os.getenv("XAI_API_KEY", "").strip()
    if not api_key:
        return None

    auto_base_url, auto_model, provider = _detect_provider(api_key)

    # Env-var overrides — XAI_BASE_URL wins, XAI_MODEL wins independently.
    # When the operator pins a base_url that doesn't match the key's
    # natural provider we log a warning so the choice is visible in
    # Render logs (catches the case where the override was meant to be
    # temporary but got committed and forgotten).
    override_base = os.getenv("XAI_BASE_URL", "").strip().rstrip("/")
    override_model = os.getenv("XAI_MODEL", "").strip()

    base_url = override_base or auto_base_url
    model = override_model or auto_model

    if override_base and override_base != auto_base_url:
        log.info(
            "xai_base_url_overridden",
            auto=auto_base_url,
            override=override_base,
            key_provider=provider,
        )

    return XAIConfig(
        api_key=api_key,
        base_url=base_url,
        chat_url=f"{base_url}/chat/completions",
        model=model,
        # Override-driven configs still report the key-derived provider
        # so log analytics can group requests by the underlying billing
        # relationship rather than the URL.
        provider=provider,
    )


# Default headers for the OpenAI-compatible chat-completions call.
# OpenRouter recommends an HTTP-Referer + X-Title pair so request
# analytics can attribute traffic to this project — these are optional
# but help when reviewing OpenRouter's dashboard.
def build_headers(api_key: str, provider: str) -> dict[str, str]:
    """Headers tuned per provider; both wrap a bearer token."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if provider == "openrouter":
        # OpenRouter attribution headers — purely cosmetic for the
        # dashboard, but cheap to include and improves observability.
        headers["HTTP-Referer"] = "https://forest-capital.vercel.app"
        headers["X-Title"] = "Forest Capital Portfolio Intelligence System"
    return headers
