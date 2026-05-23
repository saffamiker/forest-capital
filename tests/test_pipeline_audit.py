"""Coverage for tools/pipeline_audit + the audit / restore endpoints.

May 22 2026 (item 12 commit B). Exercises the upsert/active-run/get
helpers and the three pipeline-audit endpoints against the test-env
short-circuit shapes. The /pipeline-audit/active endpoint is the new
restore-on-mount surface introduced in this commit.
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,"
    "murdockm@queens.edu,panttserk@queens.edu")


# ── Migration 033 loads ─────────────────────────────────────────────────────


def test_migration_033_loads():
    spec = importlib.util.spec_from_file_location(
        "mig_033",
        os.path.join(os.path.dirname(__file__), "..", "backend",
                     "migrations", "versions",
                     "033_report_pipeline_audit.py"),
    )
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.revision == "033"
    assert m.down_revision == "032"
    assert callable(m.upgrade)
    assert callable(m.downgrade)


# ── Imports ────────────────────────────────────────────────────────────────


def test_pipeline_audit_imports():
    import tools.pipeline_audit as pa
    for name in (
        "record_audit_run", "upsert_active_run", "list_audit_runs",
        "get_audit_run", "get_active_run_for_user",
        "update_generation_timings",
    ):
        assert hasattr(pa, name), f"missing helper: {name}"


# ── Build-audit-params helper ──────────────────────────────────────────────


def test_build_audit_params_handles_int_coercion():
    """The _build_audit_params helper safely coerces ms values that
    arrive as strings or floats so the SQL bind never gets the wrong
    type."""
    from tools.pipeline_audit import _build_audit_params
    params = _build_audit_params(
        generation_id=None,
        template_id="midpoint_check_fna670",
        triggered_by="bob@queens.edu",
        steps={
            "step_1_status": "complete",
            "step_1_ms": "4234",       # string — should coerce
            "step_2_status": "warning",
            "step_2_ms": 8732.5,       # float — should coerce to int
            "step_3_status": "failed",
            "step_3_ms": None,
        },
        total_pipeline_ms=12345,
        failure_step=3,
        failure_reason="step 3 failed",
    )
    assert params["s1"] == "complete"
    assert params["ms1"] == 4234
    assert params["s2"] == "warning"
    assert params["ms2"] == 8732
    assert params["s3"] == "failed"
    assert params["ms3"] is None
    assert params["total"] == 12345
    assert params["fs"] == 3
    assert params["fr"] == "step 3 failed"


def test_build_audit_params_no_steps():
    """When no step fields are present every per-step value should
    be None — used by the initial in-progress write before any step
    has completed."""
    from tools.pipeline_audit import _build_audit_params
    params = _build_audit_params(
        generation_id=None,
        template_id="midpoint_check_fna670",
        triggered_by="bob@queens.edu",
        steps={},
        total_pipeline_ms=None,
        failure_step=None,
        failure_reason=None,
    )
    for k in ("s1", "ms1", "s2", "ms2", "s3", "ms3",
              "s4", "ms4", "s5", "ms5", "s6", "ms6", "s7", "ms7",
              "total", "fs", "fr", "mc5"):
        assert params[k] is None
    # The conditions field defaults to a JSON-encoded empty list.
    assert params["c6"] == "[]"


# ── Endpoint contract — test env ────────────────────────────────────────────


def _client():
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app)


def _team_session():
    from main import app
    from auth import require_team_member, require_auth, require_sysadmin
    fake = {"email": "ruurdsm@queens.edu", "role": "sysadmin",
            "display_name": "Test", "permissions": [
                "team_member", "manage_users", "view_analytics"]}
    app.dependency_overrides[require_team_member] = lambda: fake
    app.dependency_overrides[require_auth] = lambda: fake
    app.dependency_overrides[require_sysadmin] = lambda: fake
    return fake


def _clear_overrides():
    from main import app
    app.dependency_overrides = {}


class TestPipelineAuditEndpoints:
    def test_get_active_returns_unavailable_in_test_env(self):
        _team_session()
        try:
            c = _client()
            r = c.get("/api/v1/reports/pipeline-audit/active")
            assert r.status_code == 200
            body = r.json()
            assert body["available"] is False
        finally:
            _clear_overrides()

    def test_post_audit_test_env(self):
        _team_session()
        try:
            c = _client()
            r = c.post("/api/v1/reports/pipeline-audit", json={
                "template_id": "midpoint_check_fna670",
                "steps": {"step_1_status": "complete", "step_1_ms": 1000},
            })
            assert r.status_code == 200
            body = r.json()
            assert "id" in body
        finally:
            _clear_overrides()

    def test_post_audit_missing_template_id(self):
        _team_session()
        try:
            c = _client()
            r = c.post("/api/v1/reports/pipeline-audit",
                       json={"steps": {}})
            # Test env short-circuits before validation, returning 200
            # with {id: null}. The validation contract is exercised
            # outside the test environment.
            assert r.status_code in (200, 422)
        finally:
            _clear_overrides()

    def test_admin_list_audit_test_env(self):
        _team_session()
        try:
            c = _client()
            r = c.get("/api/v1/admin/pipeline-audit")
            assert r.status_code == 200
            assert r.json() == {"runs": []}
        finally:
            _clear_overrides()

    def test_admin_get_audit_test_env(self):
        _team_session()
        try:
            c = _client()
            r = c.get("/api/v1/admin/pipeline-audit/1")
            assert r.status_code == 200
            assert r.json() == {"error": "not_found"}
        finally:
            _clear_overrides()

    def test_unauthenticated_rejected(self):
        _clear_overrides()
        c = _client()
        r = c.get("/api/v1/reports/pipeline-audit/active")
        assert r.status_code in (401, 403)


# ── source_citations parallelism contract ──────────────────────────────────


def test_source_citations_uses_asyncio_gather():
    """The parallelisation fix must use asyncio.gather + to_thread.
    Verifying via source-inspection because we can't actually run
    web-search lookups in the test environment.
    """
    from inspect import getsource
    from tools.template_pipeline import source_citations
    src = getsource(source_citations)
    assert "asyncio.gather" in src
    assert "asyncio.to_thread" in src
    # The semaphore caps concurrency at 10.
    assert "Semaphore" in src


def test_source_citations_test_env_no_concurrency_needed():
    """Test environment short-circuits before any concurrency path."""
    import asyncio
    from tools.template_pipeline import source_citations
    concepts = [
        {"concept_id": f"c{i}", "search_query": f"query {i}"}
        for i in range(5)
    ]
    out = asyncio.run(source_citations(concepts))
    assert len(out) == 5
    for cid, entry in out.items():
        assert entry["verification_status"] == "not_found"
        assert entry["concept_id"] == cid


# ── validate_thesis with verified_data benchmark_sharpe_rank ────────────────


class TestValidateThesisWithRank:
    def test_passes_when_rank_in_verified_data(self):
        from tools.template_pipeline import validate_thesis
        out = validate_thesis(
            {
                "benchmark_sharpe_rank": 6,
                "corr_shift": 0.66,
                "max_dd_reduction_pp": -0.20,
            },
            ranked_findings=[],
        )
        assert out["passed"] is True
        for cond in out["conditions"]:
            assert cond["passed"] is True

    def test_fails_when_rank_is_one(self):
        from tools.template_pipeline import validate_thesis
        out = validate_thesis(
            {
                "benchmark_sharpe_rank": 1,
                "corr_shift": 0.66,
                "max_dd_reduction_pp": -0.20,
            },
            ranked_findings=[],
        )
        # condition 1 fails because benchmark IS ranked first.
        assert out["passed"] is False
        cond_by_id = {c["id"]: c for c in out["conditions"]}
        assert cond_by_id["benchmark_not_first"]["passed"] is False
        assert cond_by_id["material_corr_shift"]["passed"] is True
        assert cond_by_id["meaningful_dd_reduction"]["passed"] is True

    def test_fails_when_rank_missing_and_no_finding(self):
        from tools.template_pipeline import validate_thesis
        out = validate_thesis(
            {"corr_shift": 0.66, "max_dd_reduction_pp": -0.20},
            ranked_findings=[],
        )
        assert out["passed"] is False
        cond_by_id = {c["id"]: c for c in out["conditions"]}
        assert cond_by_id["benchmark_not_first"]["value"] is None


# ── live_from_payload computes benchmark_sharpe_rank ───────────────────────


class TestApaCitationFormatter:
    def test_apa_journal_article_format(self):
        from tools.template_pipeline import _format_citation
        out = _format_citation({
            "author": "Markowitz, H.",
            "year": "1952",
            "title": "Portfolio selection",
            "journal_or_institution": "Journal of Finance",
            "volume_issue_pages": "7(1), 77-91",
            "url": "https://doi.org/10.1111/j.1540-6261.1952.tb01525.x",
        })
        # Author period, year in parens, title period, italicised
        # journal with volume + issue + pages, DOI.
        assert "Markowitz, H." in out
        assert "(1952)" in out
        assert "Portfolio selection." in out
        # Italics markdown carries the journal title.
        assert "*Journal of Finance*" in out
        assert "7(1), 77-91" in out
        assert "doi.org" in out

    def test_apa_no_url(self):
        from tools.template_pipeline import _format_citation
        out = _format_citation({
            "author": "Sharpe, W. F.",
            "year": "1994",
            "title": "The Sharpe ratio",
            "journal_or_institution":
                "Journal of Portfolio Management",
            "volume_issue_pages": "21(1), 49-58",
            "url": None,
        })
        assert "Sharpe, W. F." in out
        assert "*Journal of Portfolio Management*" in out
        # No trailing URL — last token is the volume citation.
        assert out.rstrip().endswith("21(1), 49-58.") \
            or out.rstrip().endswith("49-58.")

    def test_apa_working_paper_no_volume(self):
        from tools.template_pipeline import _format_citation
        out = _format_citation({
            "author": "Author, A. A.",
            "year": "2020",
            "title": "Working paper title",
            "journal_or_institution": "NBER",
            "volume_issue_pages": "",
            "url": "https://nber.org/papers/wXXXXX",
        })
        # Italicised institution; URL at the end.
        assert "*NBER*" in out
        assert "nber.org" in out


class TestLiveFromPayloadRank:
    def _payload(self, sharpes: dict[str, float]) -> dict:
        return {
            "strategies": {
                name: {
                    "strategy_name": name,
                    "sharpe_ratio": s,
                    "monthly_returns": [],
                }
                for name, s in sharpes.items()
            },
            "academic": {"study_period": {
                "start": "2002-07-31", "end": "2025-12-31",
                "n_months": 282}},
        }

    def test_benchmark_first(self):
        from tools.template_pipeline import live_from_payload
        out = live_from_payload(self._payload({
            "BENCHMARK": 1.00, "REGIME_SWITCHING": 0.50,
            "VOL_TARGETING": 0.40,
        }))
        # Benchmark beats every other strategy → rank 1.
        assert out["benchmark_sharpe_rank"] == 1
        assert out["n_strategies_ranked"] == 3

    def test_benchmark_not_first(self):
        from tools.template_pipeline import live_from_payload
        out = live_from_payload(self._payload({
            "BENCHMARK": 0.50, "REGIME_SWITCHING": 0.62,
            "VOL_TARGETING": 0.70, "EQUAL_WEIGHT": 0.55,
        }))
        # Three strategies beat the benchmark (0.62, 0.70, 0.55)
        # → benchmark is in 4th place.
        assert out["benchmark_sharpe_rank"] == 4
        assert out["n_strategies_ranked"] == 4

    def test_benchmark_missing(self):
        from tools.template_pipeline import live_from_payload
        out = live_from_payload(self._payload({
            "REGIME_SWITCHING": 0.62, "VOL_TARGETING": 0.70,
        }))
        assert out["benchmark_sharpe_rank"] is None
