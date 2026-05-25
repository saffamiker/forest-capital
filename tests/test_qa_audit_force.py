"""
tests/test_qa_audit_force.py — May 25 2026.

POST /api/qa/audit accepts {"force": true} to bypass both the hash gate
(qa_audit_skipped_hash_match) and the min-interval gate
(qa_audit_skipped_interval) so a manual re-run after an Academic Review
re-evaluates IN02 even when strategy_hash is unchanged.

Two surfaces tested:
  1. The endpoint's force parameter contract — the source carries the
     bypass logic on both gates, and the test-env response is tagged
     so a TestClient round-trip can verify the parameter reached the
     handler.
  2. The diagnostic logger — _log_an01_an04_raw_state emits three
     structured log lines naming the cached AN01 / AN04 field values,
     so a forced re-run that still WARNs leaves the upstream data
     visible in the Render log without hand-inspecting JSONB.
"""
from __future__ import annotations

import inspect
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


def _auth_headers() -> dict:
    from config import MASTER_API_KEY  # type: ignore[import]
    return {"X-API-Key": MASTER_API_KEY}


@pytest.fixture
def client() -> TestClient:
    from main import app  # noqa: WPS433
    return TestClient(app)


# ── Endpoint behaviour ───────────────────────────────────────────────────────


class TestForceParameter:
    """Round-trip the parameter through the test-env path: with force
    the response carries forced=True; without it, the response is the
    bare mock. This confirms the body parameter reaches the handler
    (the hash-gate bypass logic itself is source-checked below)."""

    def test_force_true_round_trips_to_response(self, client: TestClient):
        r = client.post("/api/qa/audit",
                        headers=_auth_headers(),
                        json={"force": True})
        assert r.status_code == 200
        assert r.json().get("forced") is True

    def test_force_false_returns_bare_mock(self, client: TestClient):
        r = client.post("/api/qa/audit",
                        headers=_auth_headers(),
                        json={"force": False})
        assert r.status_code == 200
        assert "forced" not in r.json()

    def test_missing_body_treated_as_force_false(
        self, client: TestClient,
    ):
        # A POST with no body must continue working — the existing
        # load() path on the qaStore sends no body and expects to
        # serve the cached audit when available.
        r = client.post("/api/qa/audit", headers=_auth_headers())
        assert r.status_code == 200
        assert "forced" not in r.json()


# ── Source-level contract for the gate bypass ────────────────────────────────


class TestGateBypassSource:
    """The two gates are exercised only against a live DB with a
    cached audit row — heavy to set up. Instead, pin the bypass at
    the source level so a regression is caught without DB setup.
    The contract is: both gate conditions read `not force` so a
    forced call falls through."""

    def test_hash_gate_reads_force_flag(self):
        from main import qa_audit
        src = inspect.getsource(qa_audit)
        # The hash gate's outer condition guards `get_latest_qa`
        # with `not force` — without that guard, the bypass is
        # broken even when the body field arrives.
        assert "if qa_hash and not force:" in src

    def test_interval_gate_reads_force_flag(self):
        from main import qa_audit
        src = inspect.getsource(qa_audit)
        # The min-interval gate's outer condition guards
        # `get_most_recent_qa_run` with `not force`.
        assert "if not force:" in src

    def test_force_value_extracted_from_body(self):
        from main import qa_audit
        src = inspect.getsource(qa_audit)
        # The body parser uses .get("force") and coerces to bool —
        # a missing body / missing key reads as False.
        assert 'force = bool((body or {}).get("force"))' in src

    def test_qa_audit_signature_accepts_body(self):
        from main import qa_audit
        sig = inspect.signature(qa_audit)
        # The body parameter must be optional and default to None so
        # the existing no-body call sites continue to work.
        assert "body" in sig.parameters
        assert sig.parameters["body"].default is None


# ── Diagnostic logger ────────────────────────────────────────────────────────


class TestAN01AN04Diagnostics:
    """The diagnostic logger surfaces the cached AN01 / AN04 field
    values to the Render log so a forced re-run that still WARNs
    leaves the upstream data visible without inspecting the JSONB
    payload by hand. structlog's capture_logs() intercepts the three
    log lines so the field contract is testable."""

    def test_emits_three_structured_log_lines(self):
        import structlog

        # Use a deterministic processor chain so capture_logs() lands.
        structlog.reset_defaults()
        from main import _log_an01_an04_raw_state

        analytics_cache = {
            "academic_analytics": {
                "factor_loadings": [
                    {"strategy": "BENCHMARK", "alpha_annualized": 0.0,
                     "alpha_significant": False, "mkt_rf": 0.95,
                     "mkt_rf_significant": True, "smb_significant": False,
                     "hml_significant": False, "mom": 0.05,
                     "mom_significant": False, "r_squared": 0.97},
                ],
                "regime_conditional": [
                    {"strategy": "REGIME_SWITCHING",
                     "pre_2022_sharpe": 0.71,  "pre_2022_months": 240,
                     "post_2022_sharpe": 0.24, "post_2022_months": 41},
                ],
            },
            "transition_matrix": {
                "BULL":       {"BULL": 0.82, "BEAR": 0.05, "TRANSITION": 0.13},
                "BEAR":       {"BULL": 0.10, "BEAR": 0.70, "TRANSITION": 0.20},
                "TRANSITION": {"BULL": 0.30, "BEAR": 0.20, "TRANSITION": 0.50},
            },
            "refresh_triggered": ["academic_analytics"],
            "completeness": {
                "factor_loadings": True,
                "regime_conditional": True,
                "transition_matrix": True,
            },
        }

        with structlog.testing.capture_logs() as captured:
            _log_an01_an04_raw_state(
                analytics_cache, "abc12345f00d", forced=True)

        events = [c.get("event") for c in captured]
        assert "qa_audit_an01_state" in events
        assert "qa_audit_an04_regime_state" in events
        assert "qa_audit_an04_transition_state" in events

        an01 = next(c for c in captured if c["event"] == "qa_audit_an01_state")
        assert an01["forced"] is True
        assert an01["factor_loadings_complete"] is True
        assert an01["factor_loadings_row_count"] == 1
        assert an01["factor_loadings_refreshed"] is True
        sample = an01["factor_loadings_sample"]
        assert isinstance(sample, list) and len(sample) == 1
        # Every field the AN01 deterministic check inspects must appear.
        for key in ("strategy", "alpha_annualized", "alpha_significant",
                    "mkt_rf", "mkt_rf_significant", "smb_significant",
                    "hml_significant", "mom", "mom_significant",
                    "r_squared"):
            assert key in sample[0]

        an04_regime = next(
            c for c in captured if c["event"] == "qa_audit_an04_regime_state")
        assert an04_regime["regime_conditional_complete"] is True
        assert an04_regime["regime_conditional_row_count"] == 1
        rc_sample = an04_regime["regime_conditional_sample"]
        assert rc_sample[0]["strategy"] == "REGIME_SWITCHING"
        assert rc_sample[0]["post_2022_sharpe"] == 0.24
        assert rc_sample[0]["pre_2022_months"] == 240

        an04_tm = next(
            c for c in captured
            if c["event"] == "qa_audit_an04_transition_state")
        assert an04_tm["transition_matrix_complete"] is True
        assert an04_tm["transition_matrix_refreshed"] is False
        # Row sums round to 1.0 within each non-empty regime row.
        for regime in ("BULL", "BEAR", "TRANSITION"):
            assert regime in an04_tm["transition_matrix_row_sums"]
            assert abs(an04_tm["transition_matrix_row_sums"][regime]
                       - 1.0) < 1e-6
        assert an04_tm["transition_matrix_keys"] == [
            "BEAR", "BULL", "TRANSITION"]

    def test_fail_open_on_empty_cache(self):
        """A missing / partial analytics_cache must not raise — the
        diagnostic is best-effort and never breaks the audit path."""
        import structlog
        structlog.reset_defaults()
        from main import _log_an01_an04_raw_state

        with structlog.testing.capture_logs() as captured:
            _log_an01_an04_raw_state(None, None, forced=True)

        events = [c.get("event") for c in captured]
        # All three lines emit even when there's no cache to read —
        # the operator still sees that the pre-flight returned empty.
        assert "qa_audit_an01_state" in events
        assert "qa_audit_an04_regime_state" in events
        assert "qa_audit_an04_transition_state" in events
        an01 = next(c for c in captured if c["event"] == "qa_audit_an01_state")
        assert an01["factor_loadings_row_count"] == 0
        assert an01["factor_loadings_sample"] == []

    def test_caps_at_three_sample_rows(self):
        """The sample-row arrays are capped at three so the log line
        fits Render's single-line ingest comfortably regardless of
        how many strategies the analytics layer produced."""
        import structlog
        structlog.reset_defaults()
        from main import _log_an01_an04_raw_state

        fl_rows = [
            {"strategy": f"S{i}", "alpha_annualized": 0.01 * i,
             "alpha_significant": False, "mkt_rf": 0.9, "mkt_rf_significant": True,
             "smb_significant": False, "hml_significant": False,
             "mom": 0.0, "mom_significant": False, "r_squared": 0.95}
            for i in range(7)
        ]
        with structlog.testing.capture_logs() as captured:
            _log_an01_an04_raw_state(
                {"academic_analytics": {"factor_loadings": fl_rows,
                                        "regime_conditional": []},
                 "transition_matrix": {},
                 "refresh_triggered": [],
                 "completeness": {}},
                "x", forced=False)
        an01 = next(c for c in captured if c["event"] == "qa_audit_an01_state")
        # Row count is full; the sample array is capped.
        assert an01["factor_loadings_row_count"] == 7
        assert len(an01["factor_loadings_sample"]) == 3
