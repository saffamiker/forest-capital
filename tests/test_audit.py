"""
tests/test_audit.py

Tests for the statistical audit system:
  - the sysadmin gate on the /api/v1/audit endpoints
  - the audit data assembler (formula specs, payload hash)
  - Layer 1 — raw-data anomaly detection
  - Layer 3 — consistency checks
  - the audit engine — concurrency lock, the export report formatter,
    the finding constructor and the discrepancy classifier

Layers 1 and 3 read every database value fail-open, so the anomaly and
consistency tests run on synthetic in-memory payloads with no database.
The endpoint tests are contract tests; a real audit run (which writes
audit_runs / audit_findings rows) is a database round-trip.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402
from auth import generate_session_token  # noqa: E402
from tools import audit_assembler, audit_engine  # noqa: E402
from tools.audit_assembler import (  # noqa: E402
    FORMULA_SPECIFICATIONS, _payload_hash, assemble_audit_payload,
)
from tools.audit_common import classify_discrepancy, make_finding  # noqa: E402
from tools.audit_layer1 import layer_1_raw_data_audit  # noqa: E402
from tools.audit_layer3 import layer_3_consistency_audit  # noqa: E402

client = TestClient(app)

SYSADMIN = {"X-API-Key": generate_session_token("ruurdsm@queens.edu")}
TEAM = {"X-API-Key": generate_session_token("thaob@queens.edu")}
VIEWER = {"X-API-Key": generate_session_token("panttserk@queens.edu")}


def _clean_payload() -> dict:
    """A synthetic, internally-consistent audit payload."""
    eq = [0.0075] * 270   # ~9.4% CAGR — inside the 8-11% band
    return {
        "available": True,
        "raw_inputs_hash": "testhash",
        "metadata": {
            "regime_break_date": "2022-01-01",
            "risk_free_rate": {"value": 0.025, "source": "FRED DTB3"},
        },
        "raw_data": {
            "asset_returns": {
                "equity": eq, "ig": [0.002] * 270, "hy": [0.004] * 270,
                "rf": [0.002] * 270,
            },
            "ff_factors": {"mkt_rf": [0.6] * 270},
            "strategy_returns": {"BENCHMARK": eq},
            "strategy_weights": {},
        },
        "platform_computed": {
            "summary_statistics": {"BENCHMARK": {"information_ratio": None}},
            "factor_loadings": {},
            "turnover": {},
            "rolling_correlation": {"pre_2022": {}, "post_2022": {}},
            "regime_conditional": {},
        },
    }


# Shared stubs for the /api/v1/audit/run contract tests. The global
# QA-run guard reads is_audit_running before start_audit; stubbing both
# keeps a contract test from 409-ing on a stale row and from inserting a
# real 'running' audit_runs row that would leak into later tests.
async def _no_run_running() -> None:
    return None


async def _fake_start_audit(triggered_by: str, email: str) -> dict:
    return {"status": "started", "audit_id": 1}


_db_ready_cache: bool | None = None


def _db_ready() -> bool:
    """True when a live PostgreSQL with the audit tables is reachable."""
    global _db_ready_cache
    if _db_ready_cache is not None:
        return _db_ready_cache
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal, engine
        if AsyncSessionLocal is None:
            _db_ready_cache = False
            return False

        async def _probe() -> bool:
            if engine is not None:
                await engine.dispose()
            async with AsyncSessionLocal() as s:
                await s.execute(text("SELECT 1 FROM audit_runs LIMIT 1"))
            return True

        _db_ready_cache = asyncio.run(_probe())
    except Exception:
        _db_ready_cache = False
    return _db_ready_cache


# ── Endpoint gating ───────────────────────────────────────────────────────────

class TestAuditEndpointGating:
    def test_run_rejects_a_viewer(self):
        assert client.post("/api/v1/audit/run", headers=VIEWER).status_code == 403

    def test_run_rejects_a_team_member(self):
        # Triggering a statistical audit is sysadmin-only — a team_member
        # (Bob / Molly) is refused. The 403 fires at the require_sysadmin
        # dependency, so start_audit is never reached and no stubbing is
        # needed.
        assert client.post("/api/v1/audit/run",
                           headers=TEAM).status_code == 403

    def test_run_admits_the_sysadmin(self, monkeypatch):
        # As above — guard and start_audit both stubbed so this stays an
        # auth/contract test that leaves no audit_runs row behind.
        monkeypatch.setattr("tools.audit_engine.is_audit_running",
                            _no_run_running)
        monkeypatch.setattr("tools.audit_engine.start_audit",
                            _fake_start_audit)
        resp = client.post("/api/v1/audit/run", headers=SYSADMIN)
        assert resp.status_code == 200
        assert "status" in resp.json()

    def test_run_unauthenticated_is_401(self):
        assert client.post("/api/v1/audit/run").status_code == 401

    def test_list_runs_rejects_a_viewer(self):
        assert client.get("/api/v1/audit/runs", headers=VIEWER).status_code == 403

    def test_list_runs_admits_the_sysadmin(self):
        # The runs list is [] with no database and may carry rows when a
        # database is present (a prior manual trigger) — assert the
        # contract (200 + a `runs` list), not an environment-dependent
        # count.
        resp = client.get("/api/v1/audit/runs", headers=SYSADMIN)
        assert resp.status_code == 200
        body = resp.json()
        assert "runs" in body and isinstance(body["runs"], list)

    def test_latest_run_open_to_all_authenticated(self):
        # /runs/latest is open to every authenticated user — viewers see
        # the read-only audit summary in the QA tab. The full findings
        # panel is team-gated in the frontend, not at this endpoint.
        for headers in (VIEWER, TEAM, SYSADMIN):
            resp = client.get("/api/v1/audit/runs/latest", headers=headers)
            assert resp.status_code == 200
            assert "run" in resp.json()
        assert client.get("/api/v1/audit/runs/latest").status_code == 401

    def test_unknown_run_is_404(self):
        assert client.get("/api/v1/audit/runs/999999",
                          headers=SYSADMIN).status_code == 404

    def test_export_of_unknown_run_is_404(self):
        assert client.get("/api/v1/audit/runs/999999/export",
                          headers=SYSADMIN).status_code == 404


# ── Assembler ─────────────────────────────────────────────────────────────────

class TestAuditAssembler:
    def test_test_env_returns_unavailable(self):
        result = asyncio.run(assemble_audit_payload())
        assert result["available"] is False

    def test_formula_specifications_cover_every_metric(self):
        for key in ("cagr", "volatility", "sharpe", "sharpe_ci_95",
                    "max_drawdown", "skewness", "excess_return",
                    "information_ratio", "factor_regression",
                    "true_turnover", "rolling_correlation", "regime_split",
                    "efficient_frontier"):
            assert key in FORMULA_SPECIFICATIONS
            assert FORMULA_SPECIFICATIONS[key]

    def test_two_computation_regimes_are_documented(self):
        # The Analytics-vs-Dashboard annualisation difference must be a
        # named spec so a cross-layer gap reads as expected, not flagged.
        assert "annualisation_regimes" in FORMULA_SPECIFICATIONS
        spec = FORMULA_SPECIFICATIONS["annualisation_regimes"].lower()
        assert "252" in spec and "12" in spec

    def test_payload_hash_is_deterministic(self):
        data = {"a": [1, 2, 3], "b": {"x": 1}}
        assert _payload_hash(data) == _payload_hash(dict(data))

    def test_payload_hash_changes_with_the_data(self):
        assert _payload_hash({"a": [1]}) != _payload_hash({"a": [2]})


# ── Layer 1 ───────────────────────────────────────────────────────────────────

class TestLayer1:
    def test_unavailable_payload_skips(self):
        assert layer_1_raw_data_audit({"available": False})["status"] == "skip"

    def test_clean_data_passes(self):
        result = layer_1_raw_data_audit(_clean_payload())
        assert result["status"] == "pass"
        assert len(result["findings"]) == 6

    def test_catches_a_monthly_return_above_50_percent(self):
        payload = _clean_payload()
        payload["raw_data"]["asset_returns"]["hy"][5] = 0.62
        result = layer_1_raw_data_audit(payload)
        assert result["status"] == "fail"
        bounds = [f for f in result["findings"]
                  if f["check_name"] == "Monthly return bounds"]
        assert bounds and bounds[0]["status"] == "fail"

    def test_catches_a_broken_weight_sum(self):
        payload = _clean_payload()
        # A persisted weight schedule (columnar) whose row does not
        # sum to 1.0 — equity 0.5 + ig 0.3 + hy 0.1 = 0.9.
        payload["raw_data"]["strategy_weights"] = {
            "S1": {"dates": ["2022-01-31"], "equity": [0.5],
                   "ig": [0.3], "hy": [0.1]},
        }
        result = layer_1_raw_data_audit(payload)
        weight = [f for f in result["findings"]
                  if f["check_name"] == "Weight constraints"]
        assert weight and weight[0]["status"] == "fail"

    def test_absent_weights_skip_the_weight_check(self):
        # No weights persisted → an honest non-failing skip, not a pass.
        result = layer_1_raw_data_audit(_clean_payload())
        weight = [f for f in result["findings"]
                  if f["check_name"] == "Weight constraints"]
        assert weight and weight[0]["status"] != "fail"

    # ── Return-series-length: expected-vs-actual disclosure (UAT L1) ──
    #
    # The series-length check WARNs when any strategy is shorter than
    # the asset history. The warning's platform_value lists actual AND
    # expected (asset_months - documented lookback) per strategy, plus
    # an UNEXPECTED GAPS section when the actual length is shorter than
    # the lookback alone predicts. These tests pin both shapes.

    def test_series_length_short_dynamic_strategies_are_named_with_expected(
        self,
    ):
        # A dynamic strategy shorter than the asset series by EXACTLY
        # its documented lookback is "as designed" — the platform_value
        # must say so explicitly.
        payload = _clean_payload()
        n_assets = len(payload["raw_data"]["asset_returns"]["equity"])
        # REGIME_SWITCHING has a 3-month lookback per _EXPECTED_LOOKBACK_MONTHS.
        payload["raw_data"]["strategy_returns"] = {
            "BENCHMARK": [0.0075] * n_assets,
            "REGIME_SWITCHING": [0.0075] * (n_assets - 3),
        }
        result = layer_1_raw_data_audit(payload)
        sl = [f for f in result["findings"]
              if f["check_name"] == "Return series length"]
        assert sl and sl[0]["status"] == "warning"
        pv = sl[0].get("platform_value", "")
        assert "REGIME_SWITCHING" in pv
        assert "actual=" in pv and "expected=" in pv
        # The "as designed" tag fires when the gap is zero.
        assert "as designed" in pv

    def test_series_length_unexpected_gap_is_flagged(self):
        # MOMENTUM_ROTATION has a 12-month lookback. If it actually
        # starts 18 months in, the gap is +6 months — investigate.
        payload = _clean_payload()
        n_assets = len(payload["raw_data"]["asset_returns"]["equity"])
        payload["raw_data"]["strategy_returns"] = {
            "BENCHMARK": [0.0075] * n_assets,
            "MOMENTUM_ROTATION": [0.0075] * (n_assets - 18),
        }
        result = layer_1_raw_data_audit(payload)
        sl = [f for f in result["findings"]
              if f["check_name"] == "Return series length"]
        assert sl and sl[0]["status"] == "warning"
        pv = sl[0].get("platform_value", "")
        # The platform value names the gap explicitly with a sign.
        assert "gap +6" in pv
        # The reasoning text includes the UNEXPECTED GAPS disclosure
        # with the specific strategy and gap.
        reasoning = sl[0].get("auditor_reasoning", "")
        assert "UNEXPECTED GAPS" in reasoning
        assert "MOMENTUM_ROTATION" in reasoning

    def test_series_length_all_match_passes(self):
        # Every strategy = asset series length → PASS, not WARN.
        result = layer_1_raw_data_audit(_clean_payload())
        sl = [f for f in result["findings"]
              if f["check_name"] == "Return series length"]
        assert sl and sl[0]["status"] == "pass"


# ── Layer 3 ───────────────────────────────────────────────────────────────────

class TestLayer3:
    def test_unavailable_payload_skips(self):
        result = asyncio.run(layer_3_consistency_audit({"available": False}))
        assert result["status"] == "skip"

    def test_benchmark_information_ratio_null_passes(self):
        result = asyncio.run(layer_3_consistency_audit(_clean_payload()))
        ir = [f for f in result["findings"]
              if f["check_name"] == "Benchmark information ratio"]
        assert ir and ir[0]["status"] == "pass"

    def test_benchmark_information_ratio_numeric_fails(self):
        payload = _clean_payload()
        payload["platform_computed"]["summary_statistics"]["BENCHMARK"][
            "information_ratio"] = 0.4
        result = asyncio.run(layer_3_consistency_audit(payload))
        ir = [f for f in result["findings"]
              if f["check_name"] == "Benchmark information ratio"]
        assert ir and ir[0]["status"] == "fail"

    def test_catches_a_sharpe_ci_inversion(self, monkeypatch):
        # The CI check reads the strategy cache — inject one whose Sharpe
        # falls outside its own confidence interval.
        async def _fake_cache():
            return {"REGIME_SWITCHING": {
                "sharpe_ratio": 0.63, "sharpe_ci_95": [0.70, 0.95],
                "cagr": None,
            }}
        import tools.cache as cache_mod
        monkeypatch.setattr(cache_mod, "get_latest_strategy_cache", _fake_cache)
        result = asyncio.run(layer_3_consistency_audit(_clean_payload()))
        ci = [f for f in result["findings"]
              if f["check_name"] == "Sharpe CI direction"]
        assert ci and ci[0]["status"] == "fail"


# ── Audit engine ──────────────────────────────────────────────────────────────

class TestAuditEngine:
    def test_concurrent_run_returns_already_running(self, monkeypatch):
        async def _running() -> int:
            return 42
        monkeypatch.setattr(audit_engine, "is_audit_running", _running)
        result = asyncio.run(audit_engine.start_audit("manual", "x@y"))
        assert result["status"] == "already_running"
        assert result["audit_id"] == 42

    def test_export_report_has_the_required_sections(self):
        run = {
            "id": 7, "triggered_by": "pre_submission",
            "triggered_at": "2026-05-17T12:00:00", "status": "complete",
            "triggered_by_email": "ruurdsm@queens.edu",
            "total_checks": 2, "passed": 1, "failed": 0, "warnings": 1,
            "metadata": {"raw_inputs_hash": "abc",
                         "study_period": {"start": "2002-07", "end": "2024-12",
                                          "months": 270},
                         "risk_free_rate": {"value": 0.025,
                                            "source": "FRED DTB3",
                                            "calculation": "mean monthly * 12"}},
            "findings": {
                "layer_1": [make_finding(1, "Benchmark CAGR sanity", "cagr",
                                         "pass", "info")],
                "layer_2": [],
                "layer_3": [make_finding(3, "Turnover direction",
                                         "true_turnover", "warning",
                                         "warning", discrepancy="x")],
            },
        }
        report = audit_engine.format_audit_report(run)
        assert "STATISTICAL AUDIT REPORT" in report
        assert "EXECUTIVE SUMMARY" in report
        assert "LAYER 1: RAW DATA VERIFICATION" in report
        assert "LAYER 2: INDEPENDENT RECOMPUTATION" in report
        assert "LAYER 3: CONSISTENCY CHECKS" in report
        assert "DATA PROVENANCE" in report
        # The two-regimes explanation must be in the report.
        assert "COMPUTATION REGIMES" in report
        assert "252" in report

    def test_make_finding_carries_every_audit_findings_field(self):
        fnd = make_finding(
            2, "Layer 2 — summary statistics", "sharpe", "fail", "critical",
            strategy="REGIME_SWITCHING", platform_value=0.63,
            auditor_value=0.61, discrepancy="3.2%",
            formula_used="mean(excess)/std*sqrt(12)",
            raw_inputs_hash="abc", auditor_reasoning="recomputed step by step")
        for field in ("layer", "check_name", "metric", "strategy", "severity",
                      "status", "platform_value", "auditor_value",
                      "discrepancy", "formula_used", "raw_inputs_hash",
                      "auditor_reasoning"):
            assert field in fnd
        # Numeric values are stringified for the text columns.
        assert fnd["platform_value"] == "0.63"

    def test_classify_discrepancy_bands(self):
        # Within 0.01% — pass.
        assert classify_discrepancy(1.0, 1.00005)[0] == "pass"
        # 0.01%-0.1% — warning.
        assert classify_discrepancy(1.0, 1.0005)[0] == "warning"
        # Beyond 0.1% — fail.
        assert classify_discrepancy(1.0, 1.05)[0] == "fail"
        # A sign flip is always a failure.
        assert classify_discrepancy(0.5, -0.5)[0] == "fail"


# ── Smart audit caching ───────────────────────────────────────────────────────


def _status(tables: list[dict]) -> dict:
    return {"available": True, "study_period": None, "tables": tables}


class TestSmartAuditCaching:
    """current_data_hash / is_audit_current / run_full_audit — the
    data-fingerprint machinery behind smart audit caching."""

    # ── current_data_hash ─────────────────────────────────────────────────────

    def test_data_hash_consistent_for_same_data(self, monkeypatch):
        async def _s() -> dict:
            return _status([{"name": "market_data_monthly", "row_count": 282,
                             "max_date": "2025-12-31", "last_updated": None}])
        monkeypatch.setattr("tools.cache.get_data_status", _s)
        h1 = asyncio.run(audit_assembler.current_data_hash())
        h2 = asyncio.run(audit_assembler.current_data_hash())
        assert h1 == h2 and h1 != ""

    def test_data_hash_changes_with_row_count(self, monkeypatch):
        def _mk(n: int) -> dict:
            return _status([{"name": "market_data_monthly", "row_count": n,
                             "max_date": "2025-12-31", "last_updated": None}])

        async def _s1() -> dict:
            return _mk(282)
        monkeypatch.setattr("tools.cache.get_data_status", _s1)
        h1 = asyncio.run(audit_assembler.current_data_hash())

        async def _s2() -> dict:
            return _mk(283)
        monkeypatch.setattr("tools.cache.get_data_status", _s2)
        h2 = asyncio.run(audit_assembler.current_data_hash())
        assert h1 != h2

    def test_data_hash_empty_when_unavailable(self, monkeypatch):
        async def _unavail() -> dict:
            return {"available": False, "tables": []}
        monkeypatch.setattr("tools.cache.get_data_status", _unavail)
        assert asyncio.run(audit_assembler.current_data_hash()) == ""

    # ── is_audit_current ──────────────────────────────────────────────────────

    @staticmethod
    def _patch_currency(monkeypatch, *, cdh, last, strat, qa):
        async def _cdh() -> str:
            return cdh

        async def _last() -> str | None:
            return last

        async def _strat() -> str | None:
            return strat

        async def _qa() -> str | None:
            return qa
        monkeypatch.setattr(audit_assembler, "current_data_hash", _cdh)
        monkeypatch.setattr(
            "tools.audit_engine.get_last_completed_audit_hash", _last)
        monkeypatch.setattr("tools.cache.get_latest_strategy_hash", _strat)
        monkeypatch.setattr("tools.cache.get_latest_qa_hash", _qa)

    def test_is_audit_current_true_when_hashes_match(self, monkeypatch):
        self._patch_currency(monkeypatch, cdh="abc", last="abc",
                             strat="s1", qa="s1")
        result = asyncio.run(audit_assembler.is_audit_current())
        assert result["is_current"] is True
        assert result["statistical_current"] and result["qa_current"]

    def test_is_audit_current_false_when_no_prior_run(self, monkeypatch):
        self._patch_currency(monkeypatch, cdh="abc", last=None,
                             strat=None, qa=None)
        result = asyncio.run(audit_assembler.is_audit_current())
        assert result["is_current"] is False

    def test_is_audit_current_false_when_hash_differs(self, monkeypatch):
        # Statistical hash drifted, QA still in parity.
        self._patch_currency(monkeypatch, cdh="abc", last="DIFFERENT",
                             strat="s1", qa="s1")
        result = asyncio.run(audit_assembler.is_audit_current())
        assert result["is_current"] is False
        assert result["statistical_current"] is False
        assert result["qa_current"] is True

    # ── run_full_audit — the idempotent auto-trigger body ─────────────────────

    def test_run_full_audit_skips_when_current(self, monkeypatch):
        async def _current() -> dict:
            return {"is_current": True}
        monkeypatch.setattr(audit_assembler, "is_audit_current", _current)
        created: list[str] = []

        async def _create(tb: str, em: str):
            created.append(tb)
            return None
        monkeypatch.setattr(audit_engine, "_create_running_audit", _create)
        asyncio.run(audit_engine.run_full_audit("data_ingestion"))
        assert created == []   # idempotent — no run created on current data

    def test_skipped_trigger_logs_audit_trigger_skipped(self, monkeypatch):
        # When the audit is already current, the bypass is logged as an
        # audit_trigger_skipped event so a redeploy that fires no Opus
        # call is visible in the Render logs.
        from structlog.testing import capture_logs

        async def _current() -> dict:
            return {"is_current": True}
        monkeypatch.setattr(audit_assembler, "is_audit_current", _current)
        with capture_logs() as logs:
            asyncio.run(audit_engine.run_full_audit("startup"))
        skipped = [e for e in logs
                   if e.get("event") == "audit_trigger_skipped"]
        assert skipped, "expected an audit_trigger_skipped log event"
        assert skipped[0]["reason"] == "audit_current"
        assert skipped[0]["triggered_from"] == "startup"

    def test_run_full_audit_proceeds_when_stale(self, monkeypatch):
        async def _stale() -> dict:
            return {"is_current": False}
        monkeypatch.setattr(audit_assembler, "is_audit_current", _stale)

        async def _no_lock():
            return None
        monkeypatch.setattr(audit_engine, "is_audit_running", _no_lock)
        created: list[str] = []

        async def _create(tb: str, em: str):
            created.append(tb)
            return 7
        monkeypatch.setattr(audit_engine, "_create_running_audit", _create)
        executed: list[int] = []

        async def _exec(rid: int):
            executed.append(rid)
        monkeypatch.setattr(audit_engine, "_execute_audit", _exec)

        async def _qa():
            return None
        monkeypatch.setattr(audit_engine, "_run_qa_methodology", _qa)
        asyncio.run(audit_engine.run_full_audit("data_ingestion"))
        assert created == ["data_ingestion"]   # triggered_by carries the reason
        assert executed == [7]

    def test_trigger_audit_async_spawns_run(self, monkeypatch):
        # The data-ingestion hook calls trigger_audit_async — verify it
        # actually fires run_full_audit in the background.
        import threading
        fired = threading.Event()
        captured: list[str] = []

        async def _fake_run(reason: str = "scheduled"):
            captured.append(reason)
            fired.set()
        monkeypatch.setattr(audit_engine, "run_full_audit", _fake_run)
        audit_engine.trigger_audit_async("data_ingestion")
        assert fired.wait(timeout=5)
        assert captured == ["data_ingestion"]

    # ── Endpoint wiring ───────────────────────────────────────────────────────

    def test_demo_reason_sets_triggered_by_demo(self, monkeypatch):
        captured: dict[str, str] = {}

        async def _fake_start(triggered_by: str, email: str) -> dict:
            captured["triggered_by"] = triggered_by
            return {"status": "started", "audit_id": 1}

        # The QA-run guard runs before start_audit and reads audit_runs
        # directly — stub it clear so a stale 'running' row never 409s
        # this contract test.
        async def _none():
            return None
        monkeypatch.setattr("tools.audit_engine.is_audit_running", _none)
        monkeypatch.setattr("tools.audit_engine.start_audit", _fake_start)
        resp = client.post("/api/v1/audit/run", headers=SYSADMIN,
                           json={"reason": "demo"})
        assert resp.status_code == 200
        assert captured["triggered_by"] == "demo"

    def test_cache_invalidate_triggers_audit(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr("tools.audit_engine.trigger_audit_async",
                            lambda reason: calls.append(reason))
        resp = client.post("/api/v1/cache/invalidate", headers=SYSADMIN)
        assert resp.status_code == 200
        assert calls == ["cache_invalidation"]

    def test_latest_run_endpoint_returns_currency(self):
        resp = client.get("/api/v1/audit/runs/latest", headers=SYSADMIN)
        assert resp.status_code == 200
        body = resp.json()
        for key in ("is_current", "statistical_current", "qa_current"):
            assert key in body

    # ── _persist_to_db auto-trigger hook (the market_data_monthly write) ──────

    def test_persist_to_db_triggers_audit_on_success(self, monkeypatch):
        import database
        import tools.data_fetcher as df

        monkeypatch.setattr(database, "DATABASE_URL",
                            "postgresql+asyncpg://x/y", raising=False)

        async def _noop_persist(*_a, **_k):
            return None
        monkeypatch.setattr(df, "_async_persist_all", _noop_persist)
        calls: list[str] = []
        monkeypatch.setattr("tools.audit_engine.trigger_audit_async",
                            lambda reason: calls.append(reason))
        df._persist_to_db({}, {}, None, None, {}, None)
        assert calls == ["data_ingestion"]

    def test_persist_to_db_skips_audit_on_failure(self, monkeypatch):
        import database
        import tools.data_fetcher as df

        monkeypatch.setattr(database, "DATABASE_URL",
                            "postgresql+asyncpg://x/y", raising=False)

        async def _boom(*_a, **_k):
            raise RuntimeError("persist failed")
        monkeypatch.setattr(df, "_async_persist_all", _boom)
        calls: list[str] = []
        monkeypatch.setattr("tools.audit_engine.trigger_audit_async",
                            lambda reason: calls.append(reason))
        df._persist_to_db({}, {}, None, None, {}, None)
        assert calls == []   # the monthly write failed — no audit fired


# ── Audit timeout — a hung 'running' row is reaped ────────────────────────────

class TestAuditTimeout:
    """fail_stale_audits — a run stuck 'running' past 15 minutes is
    marked failed so the concurrency lock releases."""

    def test_timeout_is_fifteen_minutes(self):
        assert audit_engine._AUDIT_TIMEOUT_MINUTES == 15

    def test_fail_stale_audits_no_database_returns_zero(self, monkeypatch):
        # Fail-open — no database configured reaps nothing, never raises.
        import database
        monkeypatch.setattr(database, "AsyncSessionLocal", None,
                            raising=False)
        assert asyncio.run(audit_engine.fail_stale_audits()) == 0

    def test_fail_stale_audits_reaps_a_hung_run(self):
        if not _db_ready():
            pytest.skip("no live database with the audit tables")
        from sqlalchemy import text

        from database import AsyncSessionLocal, engine

        async def scenario():
            if engine is not None:
                await engine.dispose()
            # A run 'running' for 20 minutes — past the 15-minute timeout.
            async with AsyncSessionLocal() as s:
                row = await s.execute(text(
                    "INSERT INTO audit_runs (triggered_by, status, "
                    "triggered_at) VALUES ('manual', 'running', "
                    "now() - interval '20 minutes') RETURNING id"))
                rid = int(row.scalar())
                await s.commit()
            try:
                reaped = await audit_engine.fail_stale_audits()
                assert reaped >= 1
                async with AsyncSessionLocal() as s:
                    r = await s.execute(text(
                        "SELECT status, metadata FROM audit_runs "
                        "WHERE id = :i"), {"i": rid})
                    status, metadata = r.fetchone()
                assert status == "failed"
                assert "timeout" in str(
                    (metadata or {}).get("timeout_reason", "")).lower()
            finally:
                async with AsyncSessionLocal() as s:
                    await s.execute(text(
                        "DELETE FROM audit_runs WHERE id = :i"), {"i": rid})
                    await s.commit()

        asyncio.run(scenario())

    def test_fail_stale_audits_leaves_a_fresh_run(self):
        if not _db_ready():
            pytest.skip("no live database with the audit tables")
        from sqlalchemy import text

        from database import AsyncSessionLocal, engine

        async def scenario():
            if engine is not None:
                await engine.dispose()
            # A run that started just now — well inside the timeout.
            async with AsyncSessionLocal() as s:
                row = await s.execute(text(
                    "INSERT INTO audit_runs (triggered_by, status) "
                    "VALUES ('manual', 'running') RETURNING id"))
                rid = int(row.scalar())
                await s.commit()
            try:
                await audit_engine.fail_stale_audits()
                async with AsyncSessionLocal() as s:
                    r = await s.execute(text(
                        "SELECT status FROM audit_runs WHERE id = :i"),
                        {"i": rid})
                    status = r.fetchone()[0]
                assert status == "running"   # the fresh run is untouched
            finally:
                async with AsyncSessionLocal() as s:
                    await s.execute(text(
                        "DELETE FROM audit_runs WHERE id = :i"), {"i": rid})
                    await s.commit()

        asyncio.run(scenario())

    def test_startup_reaps_a_stale_audit_run(self, monkeypatch):
        # Driving the app's lifespan startup must reap a run hung from
        # before the boot, before the server accepts its first request.
        if not _db_ready():
            pytest.skip("no live database with the audit tables")
        import main
        from structlog.testing import capture_logs
        from sqlalchemy import text

        from database import AsyncSessionLocal, engine

        # Force the lifespan's non-test startup branch to run; stub the
        # other startup tasks so only the reap is exercised.
        monkeypatch.setattr(main, "ENVIRONMENT", "production")

        async def _noop_ctx():
            return None
        monkeypatch.setattr("tools.academic_context.refresh_academic_context",
                            _noop_ctx)
        monkeypatch.setattr("tools.data_fetcher.extend_market_data",
                            lambda *a, **k: {"status": "current"})

        async def scenario():
            if engine is not None:
                await engine.dispose()
            async with AsyncSessionLocal() as s:
                row = await s.execute(text(
                    "INSERT INTO audit_runs (triggered_by, status, "
                    "triggered_at) VALUES ('manual', 'running', "
                    "now() - interval '20 minutes') RETURNING id"))
                rid = int(row.scalar())
                await s.commit()
            try:
                # Enter the lifespan — startup runs to completion (the
                # reap included) before yield, i.e. before any request.
                with capture_logs() as logs:
                    async with main.lifespan(main.app):
                        pass
                async with AsyncSessionLocal() as s:
                    r = await s.execute(text(
                        "SELECT status FROM audit_runs WHERE id = :i"),
                        {"i": rid})
                    status = r.fetchone()[0]
                assert status == "failed"
                events = [str(e.get("event", "")) for e in logs]
                assert any("Startup reap" in e for e in events)
            finally:
                async with AsyncSessionLocal() as s:
                    await s.execute(text(
                        "DELETE FROM audit_runs WHERE id = :i"), {"i": rid})
                    await s.commit()

        asyncio.run(scenario())


# ── QA / audit run endpoints are sysadmin-only ────────────────────────────────

class TestQARunEndpointGating:
    """Triggering any QA or statistical-audit run is sysadmin-only
    (require_sysadmin = the manage_users permission). A team_member
    (Bob / Molly) or a viewer is refused with 403."""

    QA_RUN_ENDPOINTS = (
        "/api/qa/audit", "/api/v1/qa/run", "/api/v1/qa/full-review",
        "/api/v1/audit/run",
    )

    def test_team_member_gets_403_on_every_qa_run_endpoint(self):
        for path in self.QA_RUN_ENDPOINTS:
            resp = client.post(path, headers=TEAM)
            assert resp.status_code == 403, f"{path} should 403 a team_member"

    def test_viewer_gets_403_on_every_qa_run_endpoint(self):
        for path in self.QA_RUN_ENDPOINTS:
            resp = client.post(path, headers=VIEWER)
            assert resp.status_code == 403, f"{path} should 403 a viewer"

    def test_sysadmin_can_trigger_the_methodology_audit(self):
        # The sysadmin clears the gate; the methodology endpoints
        # short-circuit to 200 in the test environment.
        assert client.post("/api/qa/audit",
                           headers=SYSADMIN).status_code == 200
        assert client.post("/api/v1/qa/run",
                           headers=SYSADMIN).status_code == 200

    def test_sysadmin_can_trigger_the_statistical_audit(self, monkeypatch):
        # start_audit is stubbed so this contract test inserts no real
        # audit_runs row — the point is the sysadmin clears the gate.
        monkeypatch.setattr("tools.audit_engine.is_audit_running",
                            _no_run_running)
        monkeypatch.setattr("tools.audit_engine.start_audit",
                            _fake_start_audit)
        resp = client.post("/api/v1/audit/run", headers=SYSADMIN)
        assert resp.status_code == 200


# ── Fixes: findings persistence, PDF layer status, WARN acknowledge ───────────


def _audit_run_fixture(**over) -> dict:
    """A complete audit_runs dict for the PDF builder."""
    run = {
        "id": 99, "triggered_by": "manual", "triggered_at": "2026-05-19T10:00:00",
        "triggered_by_email": "ruurdsm@queens.edu", "status": "complete",
        "layer_1_status": "pass", "layer_2_status": "skip",
        "layer_3_status": "pass", "total_checks": 56, "passed": 50,
        "failed": 0, "warnings": 6, "completed_at": "2026-05-19T10:05:00",
        "metadata": {"raw_inputs_hash": "deadbeef",
                     "study_period": {"start": "2002-07", "end": "2024-12",
                                      "months": 270},
                     "risk_free_rate": {"value": 0.025, "source": "FRED DTB3"}},
        "data_hash": "deadbeef",
        "findings": {"layer_1": [], "layer_2": [], "layer_3": []},
    }
    run.update(over)
    return run


def _pdf_text(pdf: bytes) -> str:
    import io

    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join(p.extract_text() or "" for p in reader.pages)


class TestStoreFindingsRobustness:
    def test_trunc_truncates_to_the_column_limit(self):
        assert audit_engine._trunc("x" * 200, 120) == "x" * 120
        assert audit_engine._trunc("short", 120) == "short"
        assert audit_engine._trunc(None, 80) is None
        # A non-string value is stringified before truncation.
        assert audit_engine._trunc(123456, 3) == "123"

    def test_column_limits_match_migration_017(self):
        limits = audit_engine._FINDING_COLUMN_LIMITS
        assert limits["check_name"] == 120
        assert limits["metric"] == 80
        assert limits["strategy"] == 80
        assert limits["raw_inputs_hash"] == 64

    def test_store_findings_commits_per_row_truncating_and_skipping(self):
        # Per-row commit: an over-long field is truncated and stored; a
        # malformed finding is skipped; neither drops the whole batch.
        if not _db_ready():
            pytest.skip("no live database")
        from sqlalchemy import text

        from database import AsyncSessionLocal

        async def _run() -> tuple[int, list[str]]:
            async with AsyncSessionLocal() as s:
                r = await s.execute(text(
                    "INSERT INTO audit_runs (triggered_by, status) "
                    "VALUES ('manual', 'running') RETURNING id"))
                run_id = r.fetchone()[0]
                await s.commit()
            findings = [
                make_finding(1, "Good check", "cagr", "pass", "info"),
                make_finding(1, "L" * 250, "metric", "warning", "warning"),
                {"check_name": "no layer key"},   # malformed — skipped
            ]
            await audit_engine._store_findings(run_id, findings)
            async with AsyncSessionLocal() as s:
                rows = await s.execute(text(
                    "SELECT check_name FROM audit_findings "
                    "WHERE audit_run_id = :id ORDER BY id"), {"id": run_id})
                names = [row[0] for row in rows.fetchall()]
                await s.execute(text("DELETE FROM audit_runs WHERE id = :id"),
                                {"id": run_id})
                await s.commit()
            return run_id, names

        _run_id, names = asyncio.run(_run())
        # The good row and the truncated row stored; the malformed skipped.
        assert len(names) == 2
        assert any(len(name) == 120 for name in names)   # truncated to 120


class TestAuditPdfLayerStatus:
    def test_layer_empty_message_distinguishes_ran_from_skipped(self):
        from tools.audit_pdf import _layer_empty_message
        assert "no individual findings" in _layer_empty_message("pass")
        assert "no individual findings" in _layer_empty_message("warn")
        assert "no individual findings" in _layer_empty_message("warning")
        assert _layer_empty_message("skip") == "This layer was skipped."
        assert _layer_empty_message(None) == "This layer was skipped."

    def test_pdf_reports_a_ran_layer_with_no_findings(self):
        from tools.audit_pdf import build_statistical_audit_pdf
        # Layer 1 ran (status pass) but recorded no findings — the PDF
        # must not call it "skipped".
        pdf = build_statistical_audit_pdf(_audit_run_fixture())
        assert pdf.startswith(b"%PDF")
        assert "no individual findings" in _pdf_text(pdf)

    def test_pdf_includes_an_acknowledged_resolution_note(self):
        from tools.audit_pdf import build_statistical_audit_pdf
        finding = make_finding(3, "Turnover direction", "true_turnover",
                               "warning", "warning", discrepancy="minor")
        finding["resolved"] = True
        finding["resolution_note"] = "ACKNOTETOKEN reviewed and accepted"
        pdf = build_statistical_audit_pdf(
            _audit_run_fixture(findings={"layer_1": [], "layer_2": [],
                                         "layer_3": [finding]}))
        text = _pdf_text(pdf)
        assert "Acknowledged" in text
        assert "ACKNOTETOKEN" in text


class TestWarnAcknowledgeEndpoints:
    RESOLVE = "/api/v1/audit/findings/{}/resolve"
    UNRESOLVE = "/api/v1/audit/findings/{}/unresolve"

    def test_resolve_unauthenticated_is_401(self):
        assert client.post(self.RESOLVE.format(1),
                           json={"resolution_note": "x"}).status_code == 401

    def test_resolve_rejects_a_viewer(self):
        # The acknowledge endpoints are team_member-gated.
        assert client.post(self.RESOLVE.format(1), headers=VIEWER,
                           json={"resolution_note": "x"}).status_code == 403

    def test_resolve_requires_a_note(self):
        # A team member clears the gate; a blank note is a 422.
        resp = client.post(self.RESOLVE.format(1), headers=TEAM,
                           json={"resolution_note": "   "})
        assert resp.status_code == 422

    def test_resolve_unknown_finding_is_404(self):
        resp = client.post(self.RESOLVE.format(999999999), headers=TEAM,
                           json={"resolution_note": "accepted"})
        assert resp.status_code == 404

    def test_unresolve_rejects_a_viewer(self):
        assert client.post(self.UNRESOLVE.format(1),
                           headers=VIEWER).status_code == 403

    def test_unresolve_unknown_finding_is_404(self):
        assert client.post(self.UNRESOLVE.format(999999999),
                           headers=TEAM).status_code == 404

    def test_resolve_then_unresolve_round_trip(self):
        # Acknowledging sets resolved + the note; unresolving clears both.
        if not _db_ready():
            pytest.skip("no live database")
        from sqlalchemy import text

        from database import AsyncSessionLocal

        async def _seed() -> tuple[int, int]:
            async with AsyncSessionLocal() as s:
                r = await s.execute(text(
                    "INSERT INTO audit_runs (triggered_by, status) "
                    "VALUES ('manual', 'complete') RETURNING id"))
                run_id = r.fetchone()[0]
                fr = await s.execute(text(
                    "INSERT INTO audit_findings (audit_run_id, layer, "
                    "check_name, metric, severity, status) VALUES "
                    "(:rid, 3, 'Turnover', 'true_turnover', 'warning', "
                    "'warning') RETURNING id"), {"rid": run_id})
                finding_id = fr.fetchone()[0]
                await s.commit()
            return run_id, finding_id

        run_id, finding_id = asyncio.run(_seed())
        try:
            resolved = client.post(self.RESOLVE.format(finding_id), headers=TEAM,
                                   json={"resolution_note": "accepted as a "
                                         "documented limitation"})
            assert resolved.status_code == 200
            assert resolved.json()["resolved"] is True
            assert "documented limitation" in resolved.json()["resolution_note"]

            cleared = client.post(self.UNRESOLVE.format(finding_id),
                                  headers=TEAM)
            assert cleared.status_code == 200
            assert cleared.json()["resolved"] is False
            assert cleared.json()["resolution_note"] is None
        finally:
            async def _clean() -> None:
                async with AsyncSessionLocal() as s:
                    await s.execute(text(
                        "DELETE FROM audit_runs WHERE id = :id"),
                        {"id": run_id})
                    await s.commit()
            asyncio.run(_clean())
