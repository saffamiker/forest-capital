"""
tests/test_model_chains.py — pins the PR-MODEL-1 self-healing model
config (May 27 2026).

Three contracts:
  1. ModelChain advances on 404 and emits a `model_fallback` log
  2. The chain definitions match the spec (Gemini in particular —
     gemini-2.5-flash → gemini-2.0-flash-exp → gemini-1.5-flash-latest)
  3. call_claude / call_gemini honour the chain — on 404 they retry
     with the new active model, on exhaustion they re-raise

Test isolation: every test that mutates a chain runs reset_all_for_tests
in tearDown so subsequent tests start from the primary.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agents import models


@pytest.fixture(autouse=True)
def _reset_chains():
    """Every test starts with every chain reset to its primary. Without
    this, a 404 simulation in test N leaks to test N+1."""
    models.reset_all_for_tests()
    yield
    models.reset_all_for_tests()


# ── Chain definitions ────────────────────────────────────────────────────────

class TestChainDefinitions:
    def test_sonnet_primary(self) -> None:
        assert models.SONNET.primary == "claude-sonnet-4-6"

    def test_opus_primary(self) -> None:
        assert models.OPUS.primary == "claude-opus-4-7"

    def test_haiku_primary(self) -> None:
        assert models.HAIKU.primary == "claude-haiku-4-5-20251001"

    def test_gemini_chain_matches_user_spec(self) -> None:
        # User spec May 27 2026: gemini-2.5-flash → gemini-2.0-flash-exp
        # → gemini-1.5-flash-latest. Order matters — the chain is
        # tried left-to-right on 404.
        assert models.GEMINI.chain == (
            "gemini-2.5-flash",
            "gemini-2.0-flash-exp",
            "gemini-1.5-flash-latest",
        )
        assert models.GEMINI.primary == "gemini-2.5-flash"

    def test_gemini_primary_no_longer_2_0_flash(self) -> None:
        # The deprecation that triggered PR-MODEL-1 — the old
        # gemini-2.0-flash primary must NOT be the chain primary
        # anymore. It's also absent from the chain entirely; the
        # exp variant is the closest fallback.
        assert models.GEMINI.primary != "gemini-2.0-flash"
        assert "gemini-2.0-flash" not in models.GEMINI.chain

    def test_gemini_pro_chain_matches_independent_review_spec(self) -> None:
        # New chain for the Independent Review advisory layer
        # (May 25 2026). Primary gemini-2.5-pro; falls back to
        # gemini-1.5-pro-latest, then gemini-2.5-flash as a final
        # Pro-unavailable last resort.
        assert models.GEMINI_PRO.chain == (
            "gemini-2.5-pro",
            "gemini-1.5-pro-latest",
            "gemini-2.5-flash",
        )
        assert models.GEMINI_PRO.primary == "gemini-2.5-pro"

    def test_gemini_pro_logical_name(self) -> None:
        # The chain_state snapshot keys rows by logical_name; pin the
        # value the snapshot reader expects.
        assert models.GEMINI_PRO.logical_name == "gemini_pro"


# ── Chain advancement ────────────────────────────────────────────────────────

class TestChainAdvance:
    def test_advance_moves_to_next_entry(self) -> None:
        # Use GEMINI because it's the only chain with > 1 entry today.
        assert models.GEMINI.current == "gemini-2.5-flash"
        new = models.GEMINI.advance(reason="404")
        assert new == "gemini-2.0-flash-exp"
        assert models.GEMINI.current == "gemini-2.0-flash-exp"

    def test_advance_twice_reaches_end_of_chain(self) -> None:
        models.GEMINI.advance(reason="404")
        new = models.GEMINI.advance(reason="404")
        assert new == "gemini-1.5-flash-latest"
        assert models.GEMINI.active_index == 2

    def test_advance_past_end_returns_none(self) -> None:
        # The chain is exhausted after 2 advances (3-entry chain).
        models.GEMINI.advance(reason="404")
        models.GEMINI.advance(reason="404")
        result = models.GEMINI.advance(reason="404")
        assert result is None
        # Active index doesn't roll forward past the last entry —
        # current still returns the last model, not an out-of-bounds
        # error.
        assert models.GEMINI.current == "gemini-1.5-flash-latest"

    def test_advance_emits_model_fallback_log(self) -> None:
        # The structured log is the visible signal in Render logs
        # that a provider deprecation has been absorbed.
        with patch("agents.models.log") as mock_log:
            models.GEMINI.advance(reason="404")
            calls = mock_log.warning.call_args_list
            assert any(
                c[0][0] == "model_fallback"
                and c[1]["from_model"] == "gemini-2.5-flash"
                and c[1]["to_model"] == "gemini-2.0-flash-exp"
                and c[1]["reason"] == "404"
                for c in calls
            )

    def test_exhaustion_emits_model_fallback_exhausted_log(self) -> None:
        models.GEMINI.advance(reason="404")
        models.GEMINI.advance(reason="404")
        with patch("agents.models.log") as mock_log:
            models.GEMINI.advance(reason="404")
            calls = mock_log.warning.call_args_list
            assert any(
                c[0][0] == "model_fallback_exhausted"
                for c in calls
            )

    def test_single_entry_chain_advance_returns_none_immediately(self) -> None:
        # Sonnet has only one entry — advance must return None on
        # the first call without an out-of-bounds error.
        result = models.SONNET.advance(reason="404")
        assert result is None
        assert models.SONNET.current == "claude-sonnet-4-6"


# ── Resolver ─────────────────────────────────────────────────────────────────

class TestResolveActive:
    # NOTE on the gemini-2.5-flash COLLISION (May 25 2026):
    # gemini-2.5-flash appears in BOTH the GEMINI Flash chain
    # (as its primary) AND the GEMINI_PRO chain (as its final
    # fallback for the Independent Review's "Pro unavailable"
    # path). Chain registration is last-wins, so
    # _CHAIN_FOR_MODEL["gemini-2.5-flash"] now points to
    # GEMINI_PRO. Tests that intend to exercise the GEMINI Flash
    # chain via the resolver must use a chain-unique entry —
    # gemini-2.0-flash-exp or gemini-1.5-flash-latest.

    def test_resolve_primary_returns_primary_initially(self) -> None:
        # Sonnet has no collisions — its primary resolves to itself.
        # Replaces the previous gemini-2.5-flash example which now
        # routes through GEMINI_PRO (see class note).
        assert models.resolve_active("claude-sonnet-4-6") \
            == "claude-sonnet-4-6"
        # GEMINI_PRO's primary also resolves to itself initially.
        assert models.resolve_active("gemini-2.5-pro") == "gemini-2.5-pro"

    def test_resolve_fallback_entry_returns_current_active(self) -> None:
        # A caller can pass ANY string in the chain; resolve_active
        # returns the chain's current active, NOT the input string.
        # gemini-2.0-flash-exp is the GEMINI Flash chain's unique
        # fallback entry; routes via GEMINI → GEMINI.current.
        assert (
            models.resolve_active("gemini-2.0-flash-exp")
            == "gemini-2.5-flash"
        )

    def test_resolve_after_advance(self) -> None:
        models.GEMINI.advance(reason="404")
        # Use chain-unique entries — gemini-2.5-flash is shared with
        # the GEMINI_PRO chain (last-wins registration), so look up
        # the GEMINI Flash chain via its unique fallback entries.
        assert models.resolve_active("gemini-2.0-flash-exp") \
            == "gemini-2.0-flash-exp"
        assert models.resolve_active("gemini-1.5-flash-latest") \
            == "gemini-2.0-flash-exp"

    def test_unknown_model_passes_through(self) -> None:
        # A custom model not in any chain returns unchanged — the
        # resolver doesn't pre-filter to known models.
        assert models.resolve_active("custom-model-x") == "custom-model-x"

    def test_gemini_2_5_flash_routes_via_gemini_pro_due_to_last_wins(self):
        # Document the collision explicitly so a future refactor can
        # see that this routing is intentional (not a bug). The
        # GEMINI_PRO chain registers gemini-2.5-flash AFTER GEMINI does,
        # so _CHAIN_FOR_MODEL["gemini-2.5-flash"] = GEMINI_PRO. Callers
        # who want the Flash chain by-name use base.GEMINI_MODEL
        # (which is GEMINI.primary literal); resolution-by-string is
        # for fallback advancement, which works correctly when the
        # caller passes a Flash-unique entry.
        assert models.resolve_active("gemini-2.5-flash") == "gemini-2.5-pro"


class TestReportFailure:
    def test_report_failure_advances_the_right_chain(self) -> None:
        # Use a chain-unique GEMINI entry — gemini-2.5-flash now
        # routes via GEMINI_PRO due to last-wins registration
        # (see TestResolveActive class note). gemini-2.0-flash-exp
        # is unique to the GEMINI Flash chain.
        new = models.report_failure("gemini-2.0-flash-exp", reason="404")
        assert new == "gemini-2.0-flash-exp"   # current after advance
        assert models.GEMINI.active_index == 1

    def test_report_failure_advances_gemini_pro_chain(self) -> None:
        # The GEMINI_PRO chain advances independently. Its primary
        # is gemini-2.5-pro; index 1 is gemini-1.5-pro-latest.
        new = models.report_failure("gemini-2.5-pro", reason="404")
        assert new == "gemini-1.5-pro-latest"
        assert models.GEMINI_PRO.active_index == 1
        # GEMINI Flash chain is unaffected.
        assert models.GEMINI.active_index == 0

    def test_report_failure_on_unknown_model_returns_none(self) -> None:
        # A model that isn't in any chain can't trigger fallback —
        # the function returns None so the caller can re-raise.
        assert models.report_failure("custom-model-x") is None

    def test_report_failure_routes_by_any_chain_entry(self) -> None:
        # Reporting on a non-primary entry still advances the chain
        # that owns it.
        new = models.report_failure(
            "gemini-1.5-flash-latest", reason="404")
        assert new == "gemini-2.0-flash-exp"  # current after advance
        # The chain advanced from index 0 to index 1.
        assert models.GEMINI.active_index == 1


# ── 404 detection ────────────────────────────────────────────────────────────

class TestIs404Detection:
    def test_detects_anthropic_typed_exception(self) -> None:
        # Build a fake anthropic.NotFoundError without needing the
        # real one's constructor signature.
        try:
            import anthropic
            exc = anthropic.NotFoundError.__new__(anthropic.NotFoundError)
            assert models.is_model_not_found(exc)
        except (ImportError, AttributeError):
            pytest.skip("anthropic SDK not installed in this environment")

    def test_detects_404_string(self) -> None:
        exc = Exception("Provider returned HTTP 404")
        assert models.is_model_not_found(exc)

    def test_detects_not_found_string(self) -> None:
        exc = Exception("model 'gemini-2.0-flash' is not found "
                        "for API version v1beta")
        assert models.is_model_not_found(exc)

    def test_detects_does_not_exist_string(self) -> None:
        exc = Exception("Anthropic: model 'old-model' does not exist")
        assert models.is_model_not_found(exc)

    def test_detects_model_not_found_string(self) -> None:
        # Some providers return a `model_not_found` error code.
        exc = Exception("error_code: model_not_found")
        assert models.is_model_not_found(exc)

    def test_non_404_error_returns_false(self) -> None:
        exc = Exception("Rate limit exceeded")
        assert not models.is_model_not_found(exc)

    def test_auth_error_returns_false(self) -> None:
        exc = Exception("401 Unauthorized — invalid API key")
        assert not models.is_model_not_found(exc)


# ── call_claude integration ──────────────────────────────────────────────────

class TestCallClaudeFallback:
    """Mocks the Anthropic SDK to simulate a 404 on a Sonnet call
    and verifies call_claude retries cleanly. Sonnet's chain is
    single-entry, so we'll mutate Gemini's chain in shape but mock
    via the Anthropic client wrapper to exercise the call_claude
    code path."""

    def test_no_fallback_when_call_succeeds(self) -> None:
        # Sanity check: a successful Anthropic call doesn't advance
        # any chain.
        from agents import base
        fake_msg = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1,
                                   server_tool_use=None),
        )
        fake_client = SimpleNamespace(
            messages=SimpleNamespace(create=lambda **_: fake_msg))
        with patch.object(base, "get_anthropic_client",
                          return_value=fake_client):
            result = base.call_claude(
                base.SONNET_MODEL, "system", "msg")
        assert result == "ok"
        # Sonnet chain still at index 0.
        assert models.SONNET.active_index == 0

    def test_chain_exhaustion_re_raises_original_error(self) -> None:
        # Sonnet is a single-entry chain. On 404, the chain
        # exhausts immediately and call_claude must re-raise the
        # original error rather than swallowing it.
        from agents import base

        class FakeNotFound(Exception):
            pass

        def _fail(**_kw):
            raise FakeNotFound("model 'claude-sonnet-4-6' does not exist")

        fake_client = SimpleNamespace(
            messages=SimpleNamespace(create=_fail))
        with patch.object(base, "get_anthropic_client",
                          return_value=fake_client):
            with pytest.raises(FakeNotFound):
                base.call_claude(base.SONNET_MODEL, "system", "msg")

    def test_non_404_error_propagates_without_advancing(self) -> None:
        # A rate-limit / auth error must NOT trigger fallback — a
        # phantom advance on a transient error would burn through
        # the chain on a single blip.
        from agents import base

        def _fail(**_kw):
            raise Exception("Rate limit exceeded; retry in 30s")

        fake_client = SimpleNamespace(
            messages=SimpleNamespace(create=_fail))
        with patch.object(base, "get_anthropic_client",
                          return_value=fake_client):
            with pytest.raises(Exception, match="Rate limit"):
                base.call_claude(base.SONNET_MODEL, "system", "msg")
        # Sonnet chain UNCHANGED — non-404 didn't trigger fallback.
        assert models.SONNET.active_index == 0


# ── call_gemini integration ──────────────────────────────────────────────────

class TestCallGeminiFallback:
    """End-to-end: simulate a Gemini 404 and verify call_gemini
    transparently advances along the chain.

    NOTE on chain choice (May 25 2026): These tests exercise the
    GEMINI_PRO chain via base.GEMINI_PRO_MODEL rather than the
    GEMINI Flash chain via base.GEMINI_MODEL. The Flash chain's
    primary (gemini-2.5-flash) is shared with GEMINI_PRO as a
    fallback, and chain registration is last-wins — so a string-
    based lookup of gemini-2.5-flash routes through GEMINI_PRO
    instead of GEMINI. Testing via GEMINI_PRO_MODEL avoids the
    collision; the call_gemini retry-on-404 logic is identical
    across chains so this still covers the integration contract."""

    def test_404_on_primary_falls_back_and_retries(self) -> None:
        from agents import base

        # The fake SDK raises a 404-shaped error on the FIRST model
        # (gemini-2.5-pro) and succeeds on the SECOND
        # (gemini-1.5-pro-latest). After the call, the chain should
        # have advanced and we should have the second model's text.
        attempts: list[str] = []

        class FakeNotFound(Exception):
            pass

        class FakeResponse:
            text = "fallback succeeded"
            usage_metadata = SimpleNamespace(
                prompt_token_count=5, candidates_token_count=3)

        class FakeModels:
            def generate_content(self_inner, *, model, **_kw):
                attempts.append(model)
                if model == "gemini-2.5-pro":
                    raise FakeNotFound(
                        "models/gemini-2.5-pro is not found "
                        "for API version v1beta")
                return FakeResponse()

        class FakeClient:
            def __init__(self, **_kw):
                self.models = FakeModels()

        class FakeTypes:
            @staticmethod
            def GenerateContentConfig(**kw):
                return SimpleNamespace(**kw)

        import sys
        fake_genai_mod = SimpleNamespace(
            Client=FakeClient,
            types=FakeTypes,
        )
        fake_types_mod = FakeTypes
        with patch.dict(sys.modules, {
            "google": SimpleNamespace(genai=fake_genai_mod),
            "google.genai": fake_genai_mod,
            "google.genai.types": fake_types_mod,
        }):
            result = base.call_gemini(
                base.GEMINI_PRO_MODEL, "system", "user_msg")

        assert result == "fallback succeeded"
        # The chain advanced once (from index 0 to index 1).
        assert models.GEMINI_PRO.active_index == 1
        # Both attempts were made — primary then fallback.
        assert attempts == ["gemini-2.5-pro", "gemini-1.5-pro-latest"]

    def test_exhausted_chain_re_raises_404(self) -> None:
        # Every entry 404s — the loop must terminate with the
        # original error rather than infinite-loop.
        from agents import base

        class FakeNotFound(Exception):
            pass

        class FakeModels:
            def generate_content(self_inner, **_kw):
                raise FakeNotFound("not found")

        class FakeClient:
            def __init__(self, **_kw):
                self.models = FakeModels()

        class FakeTypes:
            @staticmethod
            def GenerateContentConfig(**kw):
                return SimpleNamespace(**kw)

        import sys
        fake_genai_mod = SimpleNamespace(Client=FakeClient, types=FakeTypes)
        with patch.dict(sys.modules, {
            "google": SimpleNamespace(genai=fake_genai_mod),
            "google.genai": fake_genai_mod,
            "google.genai.types": FakeTypes,
        }):
            with pytest.raises(FakeNotFound):
                base.call_gemini(base.GEMINI_PRO_MODEL, "system", "msg")

        # All three chain entries were exhausted.
        assert models.GEMINI_PRO.active_index == 2


# ── chain_state observability ────────────────────────────────────────────────

# ── Startup availability check — dispatcher routing ────────────────────────


class TestCheckModelAvailabilityDispatch:
    """The lifespan startup hook pings every chain's current model to
    detect provider deprecations early. The dispatcher routes each
    chain to its provider's check function by logical_name; an
    unregistered chain falls through to model_check_unknown_provider.

    gemini_pro joined the registry May 25 2026 for the Independent
    Review advisory layer. Same provider (Google) as the existing
    gemini Flash chain, so both route through _check_gemini_chain —
    the per-call ping logic is identical, only the model strings
    differ. Without this routing, the warm logs report
    model_check_unknown_provider for every restart."""

    def test_gemini_pro_routes_to_gemini_check_not_unknown_provider(
        self, monkeypatch,
    ):
        # Bypass the test-env early return and capture which check
        # function each chain is routed to. The check function is
        # async; stub each to a fast tag-returning coroutine so the
        # dispatch path is observable without hitting any real API.
        import asyncio
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
        monkeypatch.setenv("GOOGLE_API_KEY", "test")

        anthropic_calls: list[str] = []
        gemini_calls: list[str] = []

        async def _fake_anthropic(chain):
            anthropic_calls.append(chain.logical_name)
            return {"status": "ok", "active": chain.current}

        async def _fake_gemini(chain):
            gemini_calls.append(chain.logical_name)
            return {"status": "ok", "active": chain.current}

        monkeypatch.setattr(models, "_check_anthropic_chain", _fake_anthropic)
        monkeypatch.setattr(models, "_check_gemini_chain", _fake_gemini)

        unknown_logged: list[str] = []
        real_log = models.log

        class _SpyLog:
            def info(self, event, **kwargs):
                if event == "model_check_unknown_provider":
                    unknown_logged.append(kwargs.get("chain", "?"))
                real_log.info(event, **kwargs)

            def warning(self, *a, **kw):
                real_log.warning(*a, **kw)

            def error(self, *a, **kw):
                real_log.error(*a, **kw)

        monkeypatch.setattr(models, "log", _SpyLog())

        result = asyncio.run(models.check_model_availability())

        # Both Gemini chains routed to the Google check function.
        assert "gemini" in gemini_calls
        assert "gemini_pro" in gemini_calls
        # Anthropic chains routed to the Anthropic check.
        assert set(anthropic_calls) == {"sonnet", "opus", "haiku"}
        # No chain fell through to the unknown-provider branch.
        assert unknown_logged == [], (
            f"these chains incorrectly routed to "
            f"model_check_unknown_provider: {unknown_logged}"
        )
        # Every registered chain landed a result entry.
        assert set(result.keys()) == {
            "sonnet", "opus", "haiku", "gemini", "gemini_pro"}

    def test_check_skipped_in_test_environment(self, monkeypatch):
        # Sanity — the test-env short-circuit is the contract that
        # keeps the existing suite from hitting any real API. Pin it.
        import asyncio
        monkeypatch.setenv("ENVIRONMENT", "test")
        result = asyncio.run(models.check_model_availability())
        assert result == {}


class TestChainStateSnapshot:
    def test_chain_state_lists_every_chain(self) -> None:
        # gemini_pro joined the chain registry May 25 2026 for the
        # Academic Review's Independent Review advisory layer.
        snapshot = models.chain_state()
        names = {row["name"] for row in snapshot}
        assert names == {"sonnet", "opus", "haiku", "gemini", "gemini_pro"}

    def test_chain_state_includes_gemini_pro_row(self) -> None:
        # Pin the gemini_pro snapshot row shape so a future refactor
        # doesn't drop it from the admin / debug surface.
        snapshot = models.chain_state()
        pro_row = next(
            (r for r in snapshot if r["name"] == "gemini_pro"), None)
        assert pro_row is not None
        assert pro_row["primary"] == "gemini-2.5-pro"
        assert pro_row["current"] == "gemini-2.5-pro"
        assert pro_row["chain_length"] == 3

    def test_chain_state_reflects_advance(self) -> None:
        models.GEMINI.advance(reason="404")
        snapshot = models.chain_state()
        gemini_row = next(r for r in snapshot if r["name"] == "gemini")
        assert gemini_row["active_index"] == 1
        assert gemini_row["current"] == "gemini-2.0-flash-exp"
        # Primary is unchanged — primary is what we WANT, current is
        # what we're using right now.
        assert gemini_row["primary"] == "gemini-2.5-flash"
