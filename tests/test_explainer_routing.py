"""
tests/test_explainer_routing.py

Verifies the Sprint 6 cost-optimisation routing in the Explainer Agent:
Grok-3-mini via xAI when XAI_API_KEY is set, Haiku as the silent
fallback when the key is unset or the xAI call fails.

The tests poke at the module's internal _call_llm wrapper rather than
running the full ExplainerAgent.explain_* methods — that lets us
assert routing decisions in isolation without paying for either LLM.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


class TestExplainerRouting:
    """_call_llm picks Grok when XAI_API_KEY is present, Haiku otherwise."""

    def test_grok_called_when_xai_key_present(self, monkeypatch) -> None:
        """When XAI_API_KEY is set, _call_llm routes to _call_grok and
        never invokes the Haiku fallback."""
        import agents.explainer_agent as ex

        monkeypatch.setenv("XAI_API_KEY", "fake-test-key")

        # _call_grok returns a known string; call_claude must NOT be invoked
        with patch.object(ex, "_call_grok", return_value="from grok") as grok_mock, \
             patch.object(ex, "call_claude") as claude_mock:
            out = ex._call_llm("system", "user", max_tokens=500)

        assert out == "from grok"
        assert grok_mock.call_count == 1
        # Haiku must not be called when Grok succeeds — that's the whole
        # point of the cost routing
        assert claude_mock.call_count == 0

    def test_haiku_fallback_when_xai_key_absent(self, monkeypatch) -> None:
        """No key → no Grok attempt → Haiku gets called directly."""
        import agents.explainer_agent as ex

        monkeypatch.delenv("XAI_API_KEY", raising=False)

        with patch.object(ex, "_call_grok") as grok_mock, \
             patch.object(ex, "call_claude", return_value="from haiku") as claude_mock:
            out = ex._call_llm("system", "user", max_tokens=500)

        assert out == "from haiku"
        assert grok_mock.call_count == 0
        assert claude_mock.call_count == 1

    def test_haiku_fallback_when_grok_raises(self, monkeypatch) -> None:
        """xAI errors (rate limit, 5xx, timeout) silently fall through
        to Haiku — callers see plain text either way."""
        import agents.explainer_agent as ex

        monkeypatch.setenv("XAI_API_KEY", "fake-test-key")

        def _grok_fail(*args, **kwargs):
            raise RuntimeError("simulated 503 from xAI")

        with patch.object(ex, "_call_grok", side_effect=_grok_fail) as grok_mock, \
             patch.object(ex, "call_claude", return_value="from haiku fallback") as claude_mock:
            out = ex._call_llm("system", "user", max_tokens=500)

        assert out == "from haiku fallback"
        assert grok_mock.call_count == 1
        assert claude_mock.call_count == 1

    def test_max_tokens_forwarded_to_both_paths(self, monkeypatch) -> None:
        """The token cap must reach whichever model actually runs.

        Asymmetric behaviour by design: the Grok branch forwards the
        caller's max_tokens verbatim; the Haiku branch enforces a floor
        of HAIKU_FALLBACK_MAX_TOKENS so the fallback path never produces
        truncated JSON. Production bug — explain_qa was truncating mid-
        string at max_tokens=800. See test_render_bugfixes.py."""
        import agents.explainer_agent as ex

        # Grok path: caller's max_tokens forwarded verbatim.
        monkeypatch.setenv("XAI_API_KEY", "fake-test-key")
        with patch.object(ex, "_call_grok", return_value="ok") as grok_mock:
            ex._call_llm("s", "u", max_tokens=1234)
        call_args = grok_mock.call_args
        assert call_args.args[-1] == 1234 or call_args.kwargs.get("max_tokens") == 1234

        # Haiku path: caller's max_tokens floored at HAIKU_FALLBACK_MAX_TOKENS.
        # 999 < 2000 → bumped up to the floor.
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        with patch.object(ex, "call_claude", return_value="ok") as claude_mock:
            ex._call_llm("s", "u", max_tokens=999)
        call_args = claude_mock.call_args
        forwarded = call_args.args[-1] if not call_args.kwargs.get("max_tokens") \
            else call_args.kwargs["max_tokens"]
        assert forwarded == ex.HAIKU_FALLBACK_MAX_TOKENS, (
            f"Haiku path should floor at HAIKU_FALLBACK_MAX_TOKENS "
            f"({ex.HAIKU_FALLBACK_MAX_TOKENS}), got {forwarded}"
        )

        # Haiku path: when caller's max_tokens already exceeds the floor,
        # pass through unchanged.
        with patch.object(ex, "call_claude", return_value="ok") as claude_mock:
            ex._call_llm("s", "u", max_tokens=3000)
        call_args = claude_mock.call_args
        forwarded = call_args.args[-1] if not call_args.kwargs.get("max_tokens") \
            else call_args.kwargs["max_tokens"]
        assert forwarded == 3000


class TestExplainerXAIConfig:
    """Module-level constants must match the xAI API contract."""

    def test_xai_url_matches_openai_compatible_path(self) -> None:
        from agents.explainer_agent import XAI_API_URL
        assert XAI_API_URL == "https://api.x.ai/v1/chat/completions"

    def test_grok_model_id_is_grok_3_mini(self) -> None:
        from agents.explainer_agent import XAI_MODEL
        assert XAI_MODEL == "grok-3-mini"

    def test_timeout_is_set_and_reasonable(self) -> None:
        from agents.explainer_agent import XAI_TIMEOUT_SECONDS
        # The Explainer fires on user interactions — too long a timeout
        # degrades UX. Too short risks dropping legitimate long-form
        # responses. 20s balances both.
        assert 10 <= XAI_TIMEOUT_SECONDS <= 60
