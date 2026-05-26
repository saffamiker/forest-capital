"""tests/test_citation_findings.py — Citation Review redesign
(migration 045, May 26 2026).

Covers tools/citation_findings.py — the Level-1 findings seeder + the
two match helpers — plus the three new endpoints in main.py.

The DB-touching paths are exercised against the fail-open contract
(AsyncSessionLocal=None → empty/falsy result, never raise). The
actual SQL is exercised by the integration suite that runs against a
live Postgres in CI. Endpoint gating tests use the TestClient.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

os.environ.setdefault("ENVIRONMENT", "test")


from tools import citation_findings as cf  # noqa: E402


# ── _rank_audit_finding ─────────────────────────────────────────────────────


class TestRankAuditFinding:
    """An audit finding becomes a Level-1 wrapper only if it carries
    enough analytical weight: a failure OR a critical-severity issue
    is HIGH; a warning/warning pair is MEDIUM; everything else is
    dropped. The mapping is the inverse of the QA-badge's "is this
    something we'd want a citation for?" question."""

    def test_fail_status_is_high(self):
        assert cf._rank_audit_finding("fail", "warning") == "high"

    def test_critical_severity_is_high_regardless_of_status(self):
        # A 'warning' status with 'critical' severity is still high —
        # the severity wins because the analytical impact is what
        # matters, not the surface verdict.
        assert cf._rank_audit_finding("warning", "critical") == "high"

    def test_warning_status_warning_severity_is_medium(self):
        assert cf._rank_audit_finding("warning", "warning") == "medium"

    def test_pass_is_dropped(self):
        assert cf._rank_audit_finding("pass", "info") is None

    def test_info_severity_is_dropped(self):
        assert cf._rank_audit_finding("warning", "info") is None

    def test_unknown_status_is_dropped(self):
        assert cf._rank_audit_finding("", "") is None

    def test_case_insensitive(self):
        assert cf._rank_audit_finding("FAIL", "warning") == "high"
        assert cf._rank_audit_finding("Warning", "Warning") == "medium"


# ── _rank_qa_check ──────────────────────────────────────────────────────────


class TestRankQACheck:
    """QA-cache checks map to findings differently: FAIL or
    code_fix actions are HIGH (immediate action needed); WARN with
    methodology_decision is MEDIUM (a defensible choice the team
    has owned). INCOMPLETE is deliberately NOT a finding source —
    it's "we couldn't evaluate", not "we found a problem"."""

    def test_fail_is_high(self):
        assert cf._rank_qa_check("FAIL", "methodology_decision") == "high"

    def test_code_fix_action_is_high_even_on_warn(self):
        assert cf._rank_qa_check("WARN", "code_fix") == "high"

    def test_warn_methodology_decision_is_medium(self):
        assert cf._rank_qa_check("WARN", "methodology_decision") == "medium"

    def test_pass_is_dropped(self):
        # A passing check with no special action_type is never a
        # citation-worthy finding. (Note: code_fix as an action_type
        # ranks HIGH on its own per the function's documented
        # contract, but a PASS check would not realistically carry a
        # code_fix action — code_fix is for FAIL/WARN.)
        assert cf._rank_qa_check("PASS", "") is None
        assert cf._rank_qa_check("PASS", "disclosure_required") is None

    def test_incomplete_is_dropped(self):
        assert cf._rank_qa_check("INCOMPLETE", "planned_extension") is None

    def test_warn_disclosure_required_is_dropped(self):
        # Disclosure-required is its own resolution path — not a
        # citation-worthy finding.
        assert cf._rank_qa_check("WARN", "disclosure_required") is None

    def test_unknown_inputs_are_dropped(self):
        assert cf._rank_qa_check("", "") is None

    def test_case_insensitive(self):
        assert cf._rank_qa_check("fail", "code_fix") == "high"
        assert cf._rank_qa_check("Warn", "Methodology_Decision") == "medium"


# ── _rank_analytical_finding ────────────────────────────────────────────────


class TestRankAnalyticalFinding:
    """Analytical findings come from analytical_findings_cache, written
    by Step 1 (Stage Findings) in the Report Writer pipeline. The rank
    is derived from the nugget_strength field. This is the PRIMARY
    citation target — Sharpe / regime / factor claims that need
    supporting references. The rank rule is simpler than audit / QA
    because the finding pipeline already does the analytical work of
    classifying strength."""

    def test_high_strength_is_high(self):
        assert cf._rank_analytical_finding("HIGH") == "high"

    def test_medium_strength_is_medium(self):
        assert cf._rank_analytical_finding("MEDIUM") == "medium"

    def test_low_strength_is_dropped(self):
        # Low-strength findings aren't citation-worthy. Same drop
        # policy as the audit + QA gatherers; keeps the Level-1
        # surface noise-free.
        assert cf._rank_analytical_finding("LOW") is None

    def test_empty_or_unknown_is_dropped(self):
        assert cf._rank_analytical_finding("") is None
        assert cf._rank_analytical_finding("UNKNOWN") is None

    def test_case_insensitive(self):
        assert cf._rank_analytical_finding("high") == "high"
        assert cf._rank_analytical_finding("Medium") == "medium"


# ── IN02 exclusion ──────────────────────────────────────────────────────────


class TestIN02Exclusion:
    """IN02 is the Academic Review attestation. Its WARN state is by
    design (a manual reviewer action is required before a paper can
    ship). It must never surface as a Level-1 finding — the QA badge
    excludes it and Citation Review must mirror the rule so the
    panel doesn't badger the team for citations on an attestation."""

    def test_in02_is_in_excluded_set(self):
        assert "IN02" in cf._QA_EXCLUDED_CHECK_IDS

    def test_excluded_set_is_frozen(self):
        # Mutating a runtime constant would silently change panel
        # behaviour — pin the type.
        assert isinstance(cf._QA_EXCLUDED_CHECK_IDS, frozenset)


# ── Fail-open contract — AsyncSessionLocal None ─────────────────────────────


class TestFailOpenContract:
    """The four async helpers all read AsyncSessionLocal lazily. When
    it's None (the database module is unconfigured or the test env
    hasn't booted SQLAlchemy yet), every helper must degrade
    gracefully — empty list / empty dict / ok=False — and NEVER
    raise. A panel that hits a DB-unavailable state is rare but the
    contract is what keeps the report-writer surface from crashing
    when it happens."""

    def test_gather_audit_findings_returns_empty_when_no_db(self, monkeypatch):
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        assert asyncio.run(cf._gather_audit_findings()) == []

    def test_gather_qa_findings_returns_empty_when_no_db(self, monkeypatch):
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        assert asyncio.run(cf._gather_qa_findings()) == []

    def test_gather_analytical_findings_returns_empty_when_no_db(
            self, monkeypatch):
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        assert asyncio.run(cf._gather_analytical_findings()) == []

    def test_seed_returns_empty_when_no_db(self, monkeypatch):
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        assert asyncio.run(cf.seed_findings_for_generation(7)) == []

    def test_matches_read_returns_empty_when_no_db(self, monkeypatch):
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        assert asyncio.run(
            cf.get_matched_finding_ids_by_citation(7)) == {}

    def test_record_match_returns_not_ok_when_no_db(self, monkeypatch):
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        result = asyncio.run(cf.record_match(1, 2, "bob@queens.edu"))
        assert result.get("ok") is False
        assert result.get("error") == "database_unavailable"

    def test_remove_match_returns_not_ok_when_no_db(self, monkeypatch):
        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        result = asyncio.run(cf.remove_match(1, 2))
        assert result.get("ok") is False
        assert result.get("error") == "database_unavailable"


# ── Endpoint gating ─────────────────────────────────────────────────────────


class TestCitationFindingsEndpoints:
    """All three new endpoints require team_member. Unauthenticated
    callers get 401; viewer-tier sessions get 403. Body validation
    on the two match endpoints runs BEFORE any DB hit so the 422
    branch is reachable without a database."""

    def _client(self):
        from fastapi.testclient import TestClient
        from main import app
        return TestClient(app)

    def test_get_findings_unauthenticated_is_401(self):
        c = self._client()
        r = c.get("/api/v1/citations/findings/42")
        assert r.status_code == 401

    def test_post_match_unauthenticated_is_401(self):
        c = self._client()
        r = c.post("/api/v1/citations/match",
                   json={"citation_id": 1, "finding_id": 2})
        assert r.status_code == 401

    def test_delete_match_unauthenticated_is_401(self):
        c = self._client()
        r = c.request(
            "DELETE", "/api/v1/citations/match",
            json={"citation_id": 1, "finding_id": 2})
        assert r.status_code == 401

    def test_post_match_missing_ids_is_422(self):
        from auth import require_team_member
        from main import app
        async def _fake_team(): return {"email": "bob@queens.edu",
                                         "permissions": ["team_member"]}
        app.dependency_overrides[require_team_member] = _fake_team
        try:
            c = self._client()
            r = c.post("/api/v1/citations/match", json={})
            assert r.status_code == 422
            assert "required" in r.json()["detail"]
        finally:
            app.dependency_overrides.pop(require_team_member, None)

    def test_post_match_zero_id_is_422(self):
        from auth import require_team_member
        from main import app
        async def _fake_team(): return {"email": "bob@queens.edu",
                                         "permissions": ["team_member"]}
        app.dependency_overrides[require_team_member] = _fake_team
        try:
            c = self._client()
            r = c.post("/api/v1/citations/match",
                       json={"citation_id": 0, "finding_id": 0})
            assert r.status_code == 422
        finally:
            app.dependency_overrides.pop(require_team_member, None)

    def test_delete_match_missing_ids_is_422(self):
        from auth import require_team_member
        from main import app
        async def _fake_team(): return {"email": "bob@queens.edu",
                                         "permissions": ["team_member"]}
        app.dependency_overrides[require_team_member] = _fake_team
        try:
            c = self._client()
            r = c.request("DELETE", "/api/v1/citations/match", json={})
            assert r.status_code == 422
        finally:
            app.dependency_overrides.pop(require_team_member, None)

    def test_get_findings_team_member_reaches_handler(self, monkeypatch):
        # With AsyncSessionLocal=None the seeder returns []; verify
        # the endpoint passes auth, calls the seeder + citation
        # reader, and returns the empty-state payload (not a 500).
        from auth import require_team_member
        from main import app
        async def _fake_team(): return {"email": "bob@queens.edu",
                                         "permissions": ["team_member"]}
        app.dependency_overrides[require_team_member] = _fake_team

        import database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)

        # The citation reader also reads the DB — patch it to []
        # so the test doesn't depend on its fail-open behaviour.
        from tools import template_pipeline as tp
        async def _fake_cits(_gid): return []
        monkeypatch.setattr(
            tp, "get_citations_for_generation", _fake_cits)

        try:
            c = self._client()
            r = c.get("/api/v1/citations/findings/42")
            assert r.status_code == 200
            body = r.json()
            assert body["generation_id"] == 42
            assert body["findings"] == []
            assert body["citations"] == []
        finally:
            app.dependency_overrides.pop(require_team_member, None)
