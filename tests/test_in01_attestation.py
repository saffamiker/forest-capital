"""
tests/test_in01_attestation.py — May 25 2026.

Coverage for the IN01 repurpose: from a redundant "audit_integration"
tautology to a submission-window attestation.

The rule (per user spec):
  PASS  — a project team member triggered a manual / pre_submission
          full QA audit on or after 2026-05-25.
  FAIL  — no qualifying run (no manual runs / before-window / wrong
          email).

Two layers tested:
  1. tools.audit_engine.compute_in01_attestation — the async DB
     query that decides the verdict. Mocked at the AsyncSessionLocal
     boundary so no live DB is required.
  2. agents.qa_agent._run_deterministic_checks — the sync caller
     that reads the attestation off its new argument and surfaces it
     under check_id IN01 / key 'audit_integration'.
"""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")


def _make_mock_session(returns: list[tuple] | None) -> MagicMock:
    """Build an async-context-manager mock session whose execute()
    returns ONE row per .fetchone() call. Pass a list of rows in the
    order the SUT will fetch them; pass None to return no row.
    """
    rows = list(returns or [])

    class _Result:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    async def _execute(*args, **kwargs):
        return _Result(rows.pop(0) if rows else None)

    session = MagicMock()
    session.execute = _execute
    session.commit = MagicMock(return_value=None)

    @asynccontextmanager
    async def _ctx():
        yield session

    factory = MagicMock()
    factory.return_value = _ctx()
    return factory


# ── compute_in01_attestation ─────────────────────────────────────────────────

class TestComputeIN01Attestation:

    def test_team_member_manual_run_after_window_returns_pass(self, monkeypatch):
        """The happy path: ruurdsm@queens.edu fired a manual audit
        on 2026-05-25. The attestation reads PASS and includes the
        timestamp + email in the evidence so the report carries an
        auditable claim, not just a status badge."""
        from tools import audit_engine as ae

        triggered_at = datetime(2026, 5, 26, 14, 30, tzinfo=timezone.utc)
        factory = _make_mock_session([
            (1, "manual", triggered_at, "ruurdsm@queens.edu"),
        ])
        monkeypatch.setattr(
            "database.AsyncSessionLocal", factory, raising=False)
        result = asyncio.run(ae.compute_in01_attestation())
        assert result["status"] == "PASS"
        assert "ruurdsm@queens.edu" in result["evidence"]
        assert "2026-05-26" in result["evidence"]
        assert result["triggered_by_email"] == "ruurdsm@queens.edu"
        assert result["triggered_by"] == "manual"

    def test_pre_submission_run_by_team_member_counts(self, monkeypatch):
        """pre_submission is the dedicated 'I'm about to submit' button.
        A run from that trigger must satisfy IN01 just as 'manual'
        does."""
        from tools import audit_engine as ae

        triggered_at = datetime(2026, 6, 30, 18, 0, tzinfo=timezone.utc)
        factory = _make_mock_session([
            (2, "pre_submission", triggered_at, "thaob@queens.edu"),
        ])
        monkeypatch.setattr(
            "database.AsyncSessionLocal", factory, raising=False)
        result = asyncio.run(ae.compute_in01_attestation())
        assert result["status"] == "PASS"
        assert result["triggered_by"] == "pre_submission"

    def test_run_before_window_returns_fail_with_specific_reason(self,
                                                                  monkeypatch):
        """A team-member manual run from BEFORE 2026-05-25 fails the
        attestation. The evidence names the user and the date so a
        log reader sees WHY without re-querying."""
        from tools import audit_engine as ae

        # No qualifying row, but a recent manual run exists from
        # earlier in May.
        triggered_at = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
        factory = _make_mock_session([
            None,  # qualifying query returns nothing
            (3, "manual", triggered_at, "ruurdsm@queens.edu"),  # fallback
        ])
        monkeypatch.setattr(
            "database.AsyncSessionLocal", factory, raising=False)
        result = asyncio.run(ae.compute_in01_attestation())
        assert result["status"] == "FAIL"
        assert "before the submission window" in result["evidence"]
        assert "ruurdsm@queens.edu" in result["evidence"]
        assert result["last_manual_run_by"] == "ruurdsm@queens.edu"

    def test_run_by_non_team_email_returns_fail(self, monkeypatch):
        """A developer-account manual run from after the window does
        NOT pass — the attestation requires a project team member."""
        from tools import audit_engine as ae

        triggered_at = datetime(2026, 5, 27, 9, 0, tzinfo=timezone.utc)
        factory = _make_mock_session([
            None,  # qualifying query (filtered on team emails) misses
            (4, "manual", triggered_at, "developer@external.test"),
        ])
        monkeypatch.setattr(
            "database.AsyncSessionLocal", factory, raising=False)
        result = asyncio.run(ae.compute_in01_attestation())
        assert result["status"] == "FAIL"
        assert "not triggered by a project team member" in result["evidence"]
        assert result["last_manual_run_by"] == "developer@external.test"

    def test_no_manual_runs_at_all_returns_fail(self, monkeypatch):
        """A clean install with no manual runs at all reads FAIL with
        a specific 'click Run Full QA' nudge — the team needs to know
        what to do, not just that the gate failed."""
        from tools import audit_engine as ae

        factory = _make_mock_session([None, None])
        monkeypatch.setattr(
            "database.AsyncSessionLocal", factory, raising=False)
        result = asyncio.run(ae.compute_in01_attestation())
        assert result["status"] == "FAIL"
        assert "No manual QA audit" in result["evidence"]
        assert "Run Full QA" in result["evidence"]

    def test_database_unavailable_returns_fail(self, monkeypatch):
        """A DB outage fails the gate (not silently passes) — better
        to over-block than to certify an audit we cannot verify ran."""
        from tools import audit_engine as ae

        monkeypatch.setattr(
            "database.AsyncSessionLocal", None, raising=False)
        result = asyncio.run(ae.compute_in01_attestation())
        assert result["status"] == "FAIL"
        assert "Database unavailable" in result["evidence"]


# ── QAAgent.run_audit wiring ─────────────────────────────────────────────────

class TestQAAgentIN01Wiring:
    """The agent's deterministic checks read audit_attestation as a
    pure arg — no DB, no async. Each test passes a fabricated payload
    and asserts the IN01 row reflects it verbatim."""

    def _strategy_results(self) -> dict:
        # Minimum strategy_results shape that satisfies upstream checks
        # without affecting IN01.
        return {
            "BENCHMARK": {
                "strategy_name": "BENCHMARK",
                "monthly_returns": [["2024-01-31", 0.01]],
                "sharpe_ratio": 0.5, "cagr": 0.08,
                "is_significant": False,
            },
        }

    def test_attestation_pass_surfaces_as_in01_pass(self):
        from agents.qa_agent import QAAgent
        attestation = {
            "status": "PASS",
            "evidence": ("Manual audit by ruurdsm@queens.edu on "
                         "2026-05-26T14:30:00Z — within window."),
        }
        results = QAAgent()._run_deterministic_checks(
            self._strategy_results(), audit_attestation=attestation,
        )
        assert results["audit_integration"]["status"] == "PASS"
        assert "ruurdsm@queens.edu" in results["audit_integration"]["evidence"]

    def test_attestation_fail_surfaces_as_in01_fail(self):
        from agents.qa_agent import QAAgent
        attestation = {
            "status": "FAIL",
            "evidence": "No manual QA audit has been triggered.",
        }
        results = QAAgent()._run_deterministic_checks(
            self._strategy_results(), audit_attestation=attestation,
        )
        assert results["audit_integration"]["status"] == "FAIL"
        assert "No manual QA audit" in results["audit_integration"]["evidence"]

    def test_no_attestation_falls_through_to_fail_with_no_attestation_note(self):
        """An agent invoked without an attestation payload must NOT
        silently pass — the gap is itself a failure mode that the
        report has to surface."""
        from agents.qa_agent import QAAgent
        results = QAAgent()._run_deterministic_checks(
            self._strategy_results(), audit_attestation=None,
        )
        assert results["audit_integration"]["status"] == "FAIL"
        # The evidence names the missing-attestation cause so a log
        # reader can tell this apart from a real attestation-FAIL.
        assert "No IN01 submission-window attestation" \
            in results["audit_integration"]["evidence"]


# ── IN01 description in _CHECKLIST_ITEMS ─────────────────────────────────────

class TestIN01ChecklistMetadata:

    def test_in01_description_reflects_the_new_rule(self):
        """The check description shows in the audit report and the PDF.
        It must name the rule the deterministic check actually enforces,
        not the prior tautology."""
        from agents.qa_agent import _CHECKLIST_ITEMS
        in01 = next(c for c in _CHECKLIST_ITEMS if c["check_id"] == "IN01")
        assert "submission window" in in01["description"].lower() \
            or "submission-window" in in01["description"].lower() \
            or "2026-05-25" in in01["description"]
        assert "team member" in in01["description"].lower()
        # The key stays 'audit_integration' so downstream lookups are
        # unchanged.
        assert in01["key"] == "audit_integration"
