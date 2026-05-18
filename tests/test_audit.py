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
from tools import audit_engine  # noqa: E402
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


# ── Endpoint gating ───────────────────────────────────────────────────────────

class TestAuditEndpointGating:
    def test_run_rejects_a_viewer(self):
        assert client.post("/api/v1/audit/run", headers=VIEWER).status_code == 403

    def test_run_admits_a_team_member(self):
        # The Statistical Audit moved to the QA tab — running it requires
        # team_member, not sysadmin. No database in the test env, so
        # start_audit returns a status field with a 200.
        resp = client.post("/api/v1/audit/run", headers=TEAM)
        assert resp.status_code == 200
        assert "status" in resp.json()

    def test_run_admits_the_sysadmin(self):
        # No database in the test env → start_audit returns failed/
        # no_database; the contract is a 200 with a status field.
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
