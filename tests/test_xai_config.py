"""
tests/test_xai_config.py

Tests for agents/_xai_config.py — the shared provider resolver that
lets the Explainer and Contrarian agents transparently target either
direct xAI (`xai-...` keys, api.x.ai) or OpenRouter (`sk-or-...` keys,
openrouter.ai).

The resolver is small and pure; these tests pin the prefix-detection
contract plus the env-var override paths so a future deploy that
switches keys never silently routes through the wrong provider.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def _set_xai_env(monkeypatch, key: str = "", base_url: str = "", model: str = ""):
    """Sets the three env vars the resolver inspects, with empty-string
    meaning 'cleared' (resolve_xai_config strips and treats as unset)."""
    monkeypatch.setenv("XAI_API_KEY", key)
    monkeypatch.setenv("XAI_BASE_URL", base_url)
    monkeypatch.setenv("XAI_MODEL", model)


class TestProviderDetection:
    """Auto-detection by API-key prefix is the headline contract."""

    def test_sk_or_prefix_routes_to_openrouter(self, monkeypatch):
        _set_xai_env(monkeypatch, key="sk-or-v1-abc123")
        from agents._xai_config import resolve_xai_config

        cfg = resolve_xai_config()
        assert cfg is not None
        assert cfg.provider == "openrouter"
        assert cfg.base_url == "https://openrouter.ai/api/v1"
        assert cfg.chat_url == "https://openrouter.ai/api/v1/chat/completions"
        assert cfg.model == "x-ai/grok-4"

    def test_xai_prefix_routes_to_direct_xai(self, monkeypatch):
        _set_xai_env(monkeypatch, key="xai-abc123")
        from agents._xai_config import resolve_xai_config

        cfg = resolve_xai_config()
        assert cfg is not None
        assert cfg.provider == "direct_xai"
        assert cfg.base_url == "https://api.x.ai/v1"
        assert cfg.chat_url == "https://api.x.ai/v1/chat/completions"
        assert cfg.model == "grok-4"

    def test_unknown_prefix_falls_back_to_direct_xai(self, monkeypatch):
        """Unknown prefixes (the historical default) route to direct xAI
        rather than silently going to OpenRouter, so a wrong key
        produces a 401 we can diagnose rather than a billing surprise."""
        _set_xai_env(monkeypatch, key="random-key-without-prefix")
        from agents._xai_config import resolve_xai_config

        cfg = resolve_xai_config()
        assert cfg is not None
        assert cfg.provider == "unknown"
        assert cfg.base_url == "https://api.x.ai/v1"
        assert cfg.model == "grok-4"


class TestNoKey:
    """An unset / blank XAI_API_KEY resolves to None — callers fall back
    to Haiku (Explainer) or the deterministic mock (Contrarian)."""

    def test_unset_key_returns_none(self, monkeypatch):
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        from agents._xai_config import resolve_xai_config
        assert resolve_xai_config() is None

    def test_empty_key_returns_none(self, monkeypatch):
        _set_xai_env(monkeypatch, key="")
        from agents._xai_config import resolve_xai_config
        assert resolve_xai_config() is None

    def test_whitespace_only_key_returns_none(self, monkeypatch):
        _set_xai_env(monkeypatch, key="   ")
        from agents._xai_config import resolve_xai_config
        assert resolve_xai_config() is None


class TestEnvOverrides:
    """XAI_BASE_URL and XAI_MODEL override auto-detection independently.
    Used for operator emergencies where one provider degrades and the
    team wants to pin the other without a redeploy."""

    def test_xai_base_url_overrides_auto_detection(self, monkeypatch):
        # sk-or-... would normally route to OpenRouter, but the override
        # pins direct xAI for this deploy.
        _set_xai_env(
            monkeypatch,
            key="sk-or-v1-abc123",
            base_url="https://api.x.ai/v1",
        )
        from agents._xai_config import resolve_xai_config

        cfg = resolve_xai_config()
        assert cfg is not None
        assert cfg.base_url == "https://api.x.ai/v1"
        assert cfg.chat_url == "https://api.x.ai/v1/chat/completions"
        # provider stays "openrouter" because it reflects the key's
        # natural billing relationship, not the URL override.
        assert cfg.provider == "openrouter"

    def test_xai_model_override(self, monkeypatch):
        _set_xai_env(
            monkeypatch,
            key="xai-abc",
            model="grok-3",  # pretend we want the full grok-3 not grok-4
        )
        from agents._xai_config import resolve_xai_config

        cfg = resolve_xai_config()
        assert cfg is not None
        assert cfg.model == "grok-3"

    def test_base_url_strips_trailing_slash(self, monkeypatch):
        """The override should never produce //chat/completions URLs
        regardless of how the operator types the value."""
        _set_xai_env(
            monkeypatch,
            key="xai-abc",
            base_url="https://api.x.ai/v1/",  # trailing slash
        )
        from agents._xai_config import resolve_xai_config

        cfg = resolve_xai_config()
        assert cfg is not None
        assert cfg.chat_url == "https://api.x.ai/v1/chat/completions"

    def test_blank_override_is_ignored(self, monkeypatch):
        """An empty XAI_BASE_URL must not produce a malformed URL —
        the auto-detected base must kick in."""
        _set_xai_env(monkeypatch, key="xai-abc", base_url="", model="")
        from agents._xai_config import resolve_xai_config

        cfg = resolve_xai_config()
        assert cfg is not None
        assert cfg.base_url == "https://api.x.ai/v1"
        assert cfg.model == "grok-4"


class TestHeaders:
    """OpenRouter gets attribution headers (HTTP-Referer, X-Title);
    direct xAI does not. Both share the bearer token."""

    def test_direct_xai_has_only_auth_and_content_type(self):
        from agents._xai_config import build_headers
        h = build_headers("xai-abc", "direct_xai")
        assert h["Authorization"] == "Bearer xai-abc"
        assert h["Content-Type"] == "application/json"
        assert "HTTP-Referer" not in h
        assert "X-Title" not in h

    def test_openrouter_adds_attribution_headers(self):
        from agents._xai_config import build_headers
        h = build_headers("sk-or-abc", "openrouter")
        assert h["Authorization"] == "Bearer sk-or-abc"
        assert h["Content-Type"] == "application/json"
        # OpenRouter analytics rely on these for traffic attribution.
        assert "HTTP-Referer" in h
        assert "X-Title" in h
        # Title should mention Forest Capital so it's recognisable on the
        # OpenRouter dashboard.
        assert "Forest Capital" in h["X-Title"]


class TestIntegrationWithExplainer:
    """The Explainer's _call_grok must use the resolver's chat_url +
    model rather than the legacy hardcoded constants."""

    def test_explainer_call_grok_uses_resolved_endpoint(self, monkeypatch):
        """When XAI_API_KEY starts with sk-or-, the request goes to
        openrouter.ai with x-ai/grok-4 — even though the legacy
        XAI_API_URL / XAI_MODEL module constants still point at direct xAI."""
        import agents.explainer_agent as ex

        _set_xai_env(monkeypatch, key="sk-or-v1-test")

        captured: dict = {}

        class FakeResponse:
            status_code = 200
            text = ""
            def raise_for_status(self): pass
            def json(self):
                return {"choices": [{"message": {"content": "ok"}}]}

        class FakeClient:
            def __init__(self, *a, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, url, headers=None, json=None):  # noqa: A002
                captured["url"] = url
                captured["headers"] = headers
                captured["json"] = json
                return FakeResponse()

        monkeypatch.setattr(ex, "httpx", type("_H", (), {"Client": FakeClient}))

        ex._call_grok("sk-or-v1-test", "system", "user", 500)

        assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
        assert captured["json"]["model"] == "x-ai/grok-4"
        # Authorization header carries the actual env-resolved key.
        assert captured["headers"]["Authorization"].startswith("Bearer sk-or-v1-test")


class TestBackwardsCompatibleExports:
    """The legacy module constants stay readable so older tests that
    import them for assertions don't break. They describe the DIRECT
    xAI path — runtime code uses the resolver instead."""

    def test_contrarian_legacy_constants_preserved(self):
        from agents.contrarian_analyst import XAI_API_URL, XAI_MODEL, XAI_TIMEOUT_SECONDS
        assert XAI_API_URL == "https://api.x.ai/v1/chat/completions"
        assert XAI_MODEL == "grok-4"
        assert XAI_TIMEOUT_SECONDS == 30.0

    def test_explainer_legacy_constants_preserved(self):
        from agents.explainer_agent import XAI_API_URL, XAI_MODEL, XAI_TIMEOUT_SECONDS
        assert XAI_API_URL == "https://api.x.ai/v1/chat/completions"
        assert XAI_MODEL == "grok-4"
        assert XAI_TIMEOUT_SECONDS == 30.0
