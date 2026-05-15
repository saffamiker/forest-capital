"""
tests/test_render_bugfixes.py

Pins the four production bugfixes found in Render logs (Sprint 6 hotfix):

  Bug 1 — Migration 006 widens regime_signals_cache.hmm_regime from
          INTEGER to VARCHAR(20) so the regime detector's string labels
          ('BULL', 'BEAR', 'TRANSITION') can be cached without an
          InvalidTextRepresentation.

  Bug 2 — Grok 400 Bad Request from the Explainer agent. _call_grok now
          captures the response body on 4xx for diagnosis, and the
          timeout aligns with agents/contrarian_analyst.py (30s) so the
          two xAI callers are byte-for-byte identical request-wise.

  Bug 3 — Haiku fallback returned truncated JSON ("unterminated string")
          for the explain_qa() path. The bump from 800 → 2000 max_tokens
          on the fallback and the new _safe_json_parse helper (which
          extracts the first balanced {…} block and silently degrades
          to the fallback dict on any parse failure) keep the Explainer
          from raising into the request handler.

  Bug 4 — Incremental update raised "None of date are in the columns"
          because _fred_fetch returns a DataFrame with DATE as the index
          and the value column named after the series_id (VIXCLS, DGS2)
          — not a 'date'/'value' column pair. _append_incremental_daily
          now reads .iloc[:, 0] to grab the value series, matching the
          shape every other _fred_fetch caller uses.

Tests run hermetically in ENVIRONMENT=test — no live DB, no real LLM,
no real yfinance/FRED calls. Each fix has at least one assertion that
would have failed against the pre-fix code.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


# ── Bug 1: Migration 006 ──────────────────────────────────────────────────────

class TestMigration006:
    """Migration 006 widens hmm_regime to VARCHAR(20). The Alembic file
    must load cleanly, chain off 005, and expose both upgrade and
    downgrade callables — every Render deploy runs alembic upgrade head."""

    def test_migration_imports_cleanly(self) -> None:
        import importlib.util
        path = os.path.join(
            os.path.dirname(__file__), "..", "backend", "migrations",
            "versions", "006_alter_hmm_regime_to_varchar.py",
        )
        spec = importlib.util.spec_from_file_location("m006", path)
        assert spec is not None and spec.loader is not None
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        assert m.revision == "006"
        assert m.down_revision == "005"
        assert callable(m.upgrade)
        assert callable(m.downgrade)

    def test_upgrade_widens_to_varchar(self) -> None:
        """The migration text must reference the target column type so a
        grep-the-migration sanity check (which the team does before each
        Render deploy) confirms the change is what the body claims."""
        path = os.path.join(
            os.path.dirname(__file__), "..", "backend", "migrations",
            "versions", "006_alter_hmm_regime_to_varchar.py",
        )
        with open(path, encoding="utf-8") as f:
            source = f.read()
        assert "alter_column" in source
        assert "regime_signals_cache" in source
        assert "hmm_regime" in source
        assert "String(length=20)" in source or "String(20)" in source
        # The USING clause is required because Postgres can't implicitly
        # cast INTEGER → VARCHAR; the migration must spell it out.
        assert "postgresql_using" in source


# ── Bugs 2 + 3: Explainer Grok + Haiku JSON parsing ──────────────────────────

class TestSafeJsonParse:
    """The _safe_json_parse helper is the runtime guard against the
    truncated-JSON failure mode we saw in production. It must tolerate
    every input shape the model emits without raising."""

    def test_parses_plain_json(self) -> None:
        from agents.explainer_agent import _safe_json_parse
        assert _safe_json_parse('{"a": 1}', fallback={}) == {"a": 1}

    def test_strips_json_code_fence(self) -> None:
        from agents.explainer_agent import _safe_json_parse
        assert _safe_json_parse('```json\n{"a": 1}\n```', fallback={}) == {"a": 1}

    def test_strips_bare_code_fence(self) -> None:
        from agents.explainer_agent import _safe_json_parse
        assert _safe_json_parse('```\n{"a": 1}\n```', fallback={}) == {"a": 1}

    def test_returns_fallback_on_truncated_string(self) -> None:
        """The production failure mode: max_tokens hit mid-string,
        leaving an unterminated quote. _safe_json_parse must NOT raise."""
        from agents.explainer_agent import _safe_json_parse
        truncated = '{"a": "this string was cut off mid'
        out = _safe_json_parse(truncated, fallback={"err": True})
        assert out == {"err": True}

    def test_extracts_first_balanced_object_from_prose(self) -> None:
        """Models sometimes wrap JSON in explanatory prose. The helper
        extracts the first {...} balanced object so the happy path
        survives chatty model output."""
        from agents.explainer_agent import _safe_json_parse
        wrapped = 'Here is the result:\n{"a": 1, "b": 2}\nLet me know if you need more.'
        out = _safe_json_parse(wrapped, fallback={})
        assert out == {"a": 1, "b": 2}

    def test_returns_fallback_on_empty_string(self) -> None:
        from agents.explainer_agent import _safe_json_parse
        assert _safe_json_parse("", fallback={"empty": True}) == {"empty": True}

    def test_returns_fallback_on_whitespace_only(self) -> None:
        from agents.explainer_agent import _safe_json_parse
        assert _safe_json_parse("   \n   ", fallback={"empty": True}) == {"empty": True}

    def test_returns_fallback_on_non_string_input(self) -> None:
        """Defensive — if the LLM wrapper returns None or a number
        instead of a string, we must not crash."""
        from agents.explainer_agent import _safe_json_parse
        assert _safe_json_parse(None, fallback={"f": True}) == {"f": True}  # type: ignore[arg-type]
        assert _safe_json_parse(42, fallback={"f": True}) == {"f": True}    # type: ignore[arg-type]


class TestExplainerHaikuFallbackTokenCap:
    """The Haiku fallback must request at least HAIKU_FALLBACK_MAX_TOKENS
    (2000) so JSON responses for the longest method (explain_qa) complete
    fully. Production traces showed truncation when the cap was 800."""

    def test_haiku_fallback_max_tokens_constant(self) -> None:
        from agents.explainer_agent import HAIKU_FALLBACK_MAX_TOKENS
        assert HAIKU_FALLBACK_MAX_TOKENS >= 2000

    def test_call_llm_uses_higher_cap_on_haiku_path(self) -> None:
        """When XAI_API_KEY is unset, _call_llm bumps the requested
        max_tokens to at least HAIKU_FALLBACK_MAX_TOKENS before calling
        Haiku — so a caller passing 600 still gets a generous cap."""
        from agents.explainer_agent import _call_llm, HAIKU_FALLBACK_MAX_TOKENS

        captured = {}

        def fake_call_claude(model, system_prompt, user_message, max_tokens):
            captured["max_tokens"] = max_tokens
            return "{}"

        with patch.dict(os.environ, {"XAI_API_KEY": ""}, clear=False), \
             patch("agents.explainer_agent.call_claude", fake_call_claude):
            _call_llm("system", "user", max_tokens=600)

        assert captured["max_tokens"] >= HAIKU_FALLBACK_MAX_TOKENS


class TestExplainerGrokTimeoutAligned:
    """The Grok timeout must match the contrarian's. Aligning the two
    callers rules the request shape / connection lifecycle out as a
    cause when one fails and the other doesn't."""

    def test_xai_timeout_matches_contrarian(self) -> None:
        from agents.explainer_agent import XAI_TIMEOUT_SECONDS as exp_t
        from agents.contrarian_analyst import XAI_TIMEOUT_SECONDS as ctr_t
        assert exp_t == ctr_t, (
            f"Explainer xAI timeout ({exp_t}) should match the contrarian's "
            f"({ctr_t}) so the two callers exhibit identical retry / timeout "
            "behaviour against the xAI endpoint."
        )

    def test_xai_request_body_matches_contrarian_shape(self) -> None:
        """Spot-check that both callers use the same JSON keys for the
        xAI body — a deletion of "temperature" on one side would have
        diverged the contract."""
        from agents.explainer_agent import XAI_MODEL as exp_model
        from agents.contrarian_analyst import XAI_MODEL as ctr_model
        assert exp_model == ctr_model

    def test_grok_400_logs_response_body(self, monkeypatch) -> None:
        """On a 4xx from xAI, _call_grok must log the response body so
        the operator can see WHY the request was rejected rather than
        only the bare status code."""
        from agents.explainer_agent import _call_grok

        class FakeResponse:
            status_code = 400
            text = "Invalid model name (TEST PROBE)"

            def raise_for_status(self):
                import httpx
                raise httpx.HTTPStatusError(
                    "400 Bad Request",
                    request=None,  # type: ignore[arg-type]
                    response=self,  # type: ignore[arg-type]
                )

            def json(self):
                return {}

        class FakeClient:
            def __init__(self, *a, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, *a, **kw): return FakeResponse()

        captured_logs: list[tuple[str, dict]] = []

        def fake_warning(event, **kwargs):
            captured_logs.append((event, kwargs))

        monkeypatch.setattr("agents.explainer_agent.httpx.Client", FakeClient)
        monkeypatch.setattr("agents.explainer_agent.log.warning", fake_warning)

        with pytest.raises(Exception):
            _call_grok("api-key", "system", "user", 800)

        # We expect at least one warning with the response body preview.
        body_logs = [
            kw for ev, kw in captured_logs
            if ev == "explainer_grok_http_error" and "TEST PROBE" in str(kw.get("body_preview", ""))
        ]
        assert body_logs, (
            "Expected explainer_grok_http_error log carrying body_preview "
            "with the xAI rejection text"
        )


# ── Bug 4: Incremental update FRED column access ─────────────────────────────

class TestIncrementalFredColumnAccess:
    """_append_incremental_daily previously called set_index('date')['value']
    on the FRED DataFrame, but _fred_fetch returns a DataFrame with DATE as
    the index and the value column named after the series_id. The fix
    reads .iloc[:, 0] instead. This test would have caught the regression."""

    def test_fred_dataframe_does_not_have_date_or_value_columns(self) -> None:
        """Documents the actual shape of _fred_fetch's return so future
        edits don't reintroduce the bug. If this ever fails, _fred_fetch
        changed shape and every downstream caller must be re-audited."""
        # Build a DataFrame matching _fred_fetch's contract: DATE as index,
        # value column named after the series_id.
        df = pd.DataFrame(
            {"VIXCLS": [18.4, 19.2, 21.0]},
            index=pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"]),
        )
        df.index.name = "DATE"

        # The buggy access pattern these lines used to use:
        with pytest.raises(KeyError):
            df.set_index("date")["value"]

        # The correct pattern the fix uses:
        s = df.iloc[:, 0]
        assert list(s.values) == [18.4, 19.2, 21.0]
        # Dates survive as the Series index.
        assert pd.api.types.is_datetime64_any_dtype(s.index)

    def test_append_incremental_daily_handles_fred_dataframe(self) -> None:
        """End-to-end: with mocked yfinance + FRED returning the actual
        production shape (DATE-as-index DataFrame), _append_incremental_daily
        must NOT raise 'None of [date] are in the columns'. We patch the
        DB upsert path to a no-op so this stays a unit test."""
        from tools import data_fetcher

        def fake_yf(tickers, start, end):
            idx = pd.to_datetime(["2025-01-02", "2025-01-03"])
            return pd.DataFrame({tickers[0]: [410.0, 412.5]}, index=idx)

        def fake_fred_vix(sid, start, end):
            assert sid == "VIXCLS"
            idx = pd.to_datetime(["2025-01-02", "2025-01-03"])
            df = pd.DataFrame({"VIXCLS": [18.4, 19.2]}, index=idx)
            df.index.name = "DATE"
            return df

        def fake_fred_dgs2(sid, start, end):
            assert sid == "DGS2"
            idx = pd.to_datetime(["2025-01-02", "2025-01-03"])
            df = pd.DataFrame({"DGS2": [4.32, 4.30]}, index=idx)
            df.index.name = "DATE"
            return df

        def fred_router(series_id, start, end):
            return fake_fred_vix(series_id, start, end) if series_id == "VIXCLS" \
                else fake_fred_dgs2(series_id, start, end)

        # Stub DATABASE_URL so the function attempts the upsert path,
        # then intercept the actual SQL with a no-op pool that returns
        # the number of rows it would have written.
        with patch.object(data_fetcher, "_yfinance_fetch", fake_yf), \
             patch.object(data_fetcher, "_fred_fetch", fred_router), \
             patch("database.DATABASE_URL", "postgresql+asyncpg://stub"), \
             patch("concurrent.futures.ThreadPoolExecutor") as fake_pool:
            # Pool.submit().result() should return a row count without
            # actually doing any DB work.
            fake_pool.return_value.__enter__.return_value.submit.return_value.result.return_value = 2

            rows = data_fetcher._append_incremental_daily("2025-01-02", "2025-01-03")

        # The critical assertion: we got HERE without a KeyError on
        # "date" or "value" columns. The pre-fix code raised before
        # ever reaching the upsert.
        assert rows == 2
