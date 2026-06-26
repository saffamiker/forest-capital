"""tests/test_appendix_preflight_hash_match.py -- PR for hash-
matched appendix pre-flight gate (June 22 2026).

PR #365 introduced a pre-flight gate that 409s when ANY source
row is empty. The gate accepted stale-hash rows as "warm cache"
which let the appendix render against data the brief didn't see
(the c421fb89 vs f2e87dec hash-confusion incident).

This PR tightens the gate: compute the canonical current
strategy hash via _compute_data_hash(n_rows, last_date,
n_strategies=10), then verify each source carries a row AT
THAT HASH. A stale-hash row counts as missing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


# ── get_metric_by_hash helper ────────────────────────────────────────────


class TestGetMetricByHash:
    """The new hash-matched read replaces the previous gate's
    use of get_latest_metric (which returned the most recent
    row regardless of hash and would accept stale-hash
    payloads as 'warm cache')."""

    def test_signature_exists_and_is_async(self):
        from tools.precomputed_analytics import get_metric_by_hash
        import inspect
        assert inspect.iscoroutinefunction(get_metric_by_hash)

    def test_empty_data_hash_returns_none(self):
        # An empty data_hash is invalid input -- short-circuit
        # rather than running a SELECT with empty bind.
        import asyncio
        from tools.precomputed_analytics import get_metric_by_hash
        out = asyncio.run(get_metric_by_hash("academic_analytics", ""))
        assert out is None


# ── Pre-flight gate -- hash match scenarios ─────────────────────────────


def _make_history(n_rows: int, last_date: str):
    """Build a minimal history bundle the gate's _compute_data_hash
    call needs. Only equity_monthly is read; other fields default
    to empty so unrelated code paths don't trip."""
    import pandas as pd
    dates = pd.date_range(end=last_date, periods=n_rows, freq="ME")
    equity = pd.Series([1.0] * n_rows, index=dates)
    return {"equity_monthly": equity}


class TestAppendixPreflightHashMatch:
    """The four scenarios from the spec:
      1. hash match pass (all sources at canonical hash -> gate
         allows generation)
      2. hash mismatch 409 (sources at a DIFFERENT hash -> 409)
      3. empty cache 409 (no rows at any hash -> 409)
      4. partial mismatch (strategy_results warm but metrics
         stale -> 409 listing the stale metrics)"""

    def _stub_brief_and_appendix_grounding(self, monkeypatch):
        async def _fake_brief():
            return {"content_text": "brief body",
                    "content_hash": "h", "draft_id": 1}

        async def _fake_appendix():
            return {"content_text": "appendix body",
                    "content_hash": "h", "draft_id": 2}

        monkeypatch.setattr(
            "tools.brief_grounding.get_brief_for_grounding",
            _fake_brief)
        monkeypatch.setattr(
            "tools.brief_grounding.get_appendix_for_grounding",
            _fake_appendix)

    def _stub_history_and_hash(self, monkeypatch, canonical_hash):
        # Returns a history that hashes to canonical_hash. We
        # short-circuit the actual hash computation by also
        # stubbing _compute_data_hash so we don't need the real
        # input shape.
        async def _fake_history_async():
            return _make_history(287, "2026-05-31")

        def _fake_hash(_n_rows, _last_date, n_strategies):
            return canonical_hash

        monkeypatch.setattr(
            "tools.data_fetcher.get_full_history_async",
            _fake_history_async)
        monkeypatch.setattr(
            "tools.cache._compute_data_hash", _fake_hash)

    def _stub_gather(self, monkeypatch):
        # Stub gather_analytical_appendix_data to return a
        # populated bundle so the post-gate path doesn't crash.
        async def _fake_data():
            return {
                "available": True,
                "study_period": {"n_months": 287},
                "summary_statistics": [], "regime_conditional": [],
                "drawdown_comparison": [],
                "factor_loadings": [{"strategy": "A"}],
                "cumulative_returns": {}, "rolling_correlation": {},
                "strategy_results": {"A": {"sharpe_ratio": 0.5}},
                "strategy_metadata": {}, "risk_free_rate": None,
                "team_summary": {}, "last_review_text": None,
                "academic_docs": [], "audit_disclosures": None,
                "bootstrap_ci_sharpe": [{"strategy": "A"}],
                "crisis_performance": None,
                "cost_sensitivity": {"net_sharpe_15bp": 0.5},
                "invariant_summary": None,
                "data_hash": "stub",
            }
        monkeypatch.setattr(
            "tools.academic_export.gather_analytical_appendix_data",
            _fake_data)

    def test_hash_match_pass(self, monkeypatch):
        """All three sources carry rows at the canonical hash ->
        gate passes; appendix generation proceeds past the
        pre-flight check."""
        import asyncio
        import main as main_module
        self._stub_brief_and_appendix_grounding(monkeypatch)
        self._stub_history_and_hash(monkeypatch, "canonical_xyz")
        self._stub_gather(monkeypatch)

        # All three sources hit at the canonical hash.
        async def _fake_strategy_cache(h):
            assert h == "canonical_xyz"
            return {"A": {"sharpe_ratio": 0.5}}

        async def _fake_metric(kind, h):
            assert h == "canonical_xyz"
            if kind == "academic_analytics":
                return {"bootstrap_ci_sharpe": [{"strategy": "A"}],
                        "factor_loadings": [{"strategy": "A"}]}
            if kind == "oos_cost_sensitivity":
                return {"net_sharpe_15bp": 0.5}
            return None

        monkeypatch.setattr(
            "tools.cache.get_strategy_cache", _fake_strategy_cache)
        monkeypatch.setattr(
            "tools.precomputed_analytics.get_metric_by_hash",
            _fake_metric)

        # The gate passes; the downstream code continues. The
        # downstream eventually hits the test-env Anthropic
        # short-circuit and the function returns a tuple. The
        # important thing is that it does NOT raise
        # HTTPException(409) here.
        try:
            result = asyncio.run(main_module._generate_appendix_document(
                "ruurdsm@queens.edu"))
            assert result is not None  # returned the tuple
        except Exception as exc:
            # Anything other than HTTPException is fine for this
            # gate-focused test -- the downstream may fail on
            # DB writes in test env, but the gate must NOT 409.
            from fastapi import HTTPException
            assert not isinstance(exc, HTTPException), (
                f"gate should pass for hash match; got 409: "
                f"{exc.detail if hasattr(exc, 'detail') else exc}")

    def test_strategy_cache_at_wrong_hash_409(self, monkeypatch):
        """Strategy cache has a row but at the WRONG hash ->
        409. This is the c421fb89 vs f2e87dec scenario the gate
        was tightened to catch."""
        import asyncio
        import main as main_module
        from fastapi import HTTPException
        self._stub_brief_and_appendix_grounding(monkeypatch)
        self._stub_history_and_hash(monkeypatch, "canonical_xyz")
        self._stub_gather(monkeypatch)

        # Strategy cache returns None at canonical hash (no row
        # matches even though the latest-row reader would find
        # something at a stale hash).
        async def _fake_strategy_cache(_h):
            return None

        async def _fake_metric(_kind, _h):
            return {"bootstrap_ci_sharpe": [],
                    "factor_loadings": []}

        monkeypatch.setattr(
            "tools.cache.get_strategy_cache", _fake_strategy_cache)
        monkeypatch.setattr(
            "tools.precomputed_analytics.get_metric_by_hash",
            _fake_metric)

        try:
            asyncio.run(main_module._generate_appendix_document(
                "ruurdsm@queens.edu"))
        except HTTPException as exc:
            assert exc.status_code == 409
            detail = exc.detail or ""
            # The detail surfaces the canonical hash + the
            # specific missing source.
            assert "canonical_xyz" in detail
            assert "strategy_results_cache" in detail
            # And directs to the admin endpoint.
            assert "/api/v1/admin/refresh-appendix-caches" in detail
            return
        assert False, "expected HTTPException(409)"

    def test_empty_cache_409(self, monkeypatch):
        """Nothing in any cache at any hash -> 409 listing all
        four sources as missing."""
        import asyncio
        import main as main_module
        from fastapi import HTTPException
        self._stub_brief_and_appendix_grounding(monkeypatch)
        self._stub_history_and_hash(monkeypatch, "canonical_xyz")
        self._stub_gather(monkeypatch)

        async def _none(*_a, **_k):
            return None

        monkeypatch.setattr("tools.cache.get_strategy_cache", _none)
        monkeypatch.setattr(
            "tools.precomputed_analytics.get_metric_by_hash", _none)

        try:
            asyncio.run(main_module._generate_appendix_document(
                "ruurdsm@queens.edu"))
        except HTTPException as exc:
            assert exc.status_code == 409
            detail = exc.detail or ""
            # All four sources surfaced as missing.
            assert "strategy_results_cache" in detail
            assert "bootstrap_ci_sharpe" in detail
            assert "factor_loadings" in detail
            assert "cost_sensitivity" in detail
            return
        assert False, "expected HTTPException(409)"

    def test_partial_mismatch_strategy_warm_metrics_stale_409(
        self, monkeypatch,
    ):
        """The exact production scenario: strategy_results_cache
        warm at canonical hash, but bootstrap/factor_loadings/
        cost_sensitivity metrics are at a stale hash from a
        prior data state. Gate must 409 with the metric-level
        mismatch surfaced -- not silently allow the appendix
        to render mixed-hash data."""
        import asyncio
        import main as main_module
        from fastapi import HTTPException
        self._stub_brief_and_appendix_grounding(monkeypatch)
        self._stub_history_and_hash(monkeypatch, "canonical_xyz")
        self._stub_gather(monkeypatch)

        # Strategy warm at canonical hash.
        async def _fake_strategy_cache(_h):
            return {"A": {"sharpe_ratio": 0.5}}

        # Metrics return None at canonical hash (stale at some
        # other hash, but the hash-matched read sees nothing).
        async def _fake_metric(_kind, _h):
            return None

        monkeypatch.setattr(
            "tools.cache.get_strategy_cache", _fake_strategy_cache)
        monkeypatch.setattr(
            "tools.precomputed_analytics.get_metric_by_hash",
            _fake_metric)

        try:
            asyncio.run(main_module._generate_appendix_document(
                "ruurdsm@queens.edu"))
        except HTTPException as exc:
            assert exc.status_code == 409
            detail = exc.detail or ""
            # Strategy cache passed -- not in the missing list.
            assert "strategy_results_cache (no row at hash" not in detail
            # But the three metrics are listed as missing.
            assert "bootstrap_ci_sharpe" in detail
            assert "factor_loadings" in detail
            assert "cost_sensitivity" in detail
            assert "canonical_xyz" in detail
            return
        assert False, "expected HTTPException(409)"

    def test_canonical_hash_unavailable_409(self, monkeypatch):
        """If _compute_data_hash can't be computed (full data
        history unreadable), the gate cannot proceed -- 409
        rather than letting the appendix render against an
        unknown-hash cache."""
        import asyncio
        import main as main_module
        from fastapi import HTTPException
        self._stub_brief_and_appendix_grounding(monkeypatch)
        self._stub_gather(monkeypatch)

        async def _broken_history():
            raise RuntimeError("history unreadable")

        monkeypatch.setattr(
            "tools.data_fetcher.get_full_history_async",
            _broken_history)

        try:
            asyncio.run(main_module._generate_appendix_document(
                "ruurdsm@queens.edu"))
        except HTTPException as exc:
            assert exc.status_code == 409
            assert "canonical hash unavailable" in (exc.detail or "")
            return
        assert False, "expected HTTPException(409)"
