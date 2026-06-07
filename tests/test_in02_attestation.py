"""tests/test_in02_attestation.py -- Bridge #82.

The IN02 attestation distinguishes three states:

  PASS -- row exists in the lookback window AND >= 5 rated sections.
  WARN -- row exists but the verdict has fewer than 5 sections. The
          finding text branches on whether parse_error is set:
            - parse_error=True  -> "arbiter response could not be
                                    parsed -- zero of five sections
                                    recognised" (refused / drift /
                                    error payload).
            - parse_error=False -> "verdict parsed only N of 5 ... may
                                    have truncated" (partial result).
  FAIL -- no row in the lookback window.

These tests pin the new parse_error branch added in bridge #82 by
patching the audit_engine's DB read with a fake fetchone() row and
asserting the message wording. Pure-function patching keeps the
test offline and fast.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")


def _fake_session(row):
    """Returns an async context-manager whose execute(...) yields the
    given row from fetchone()."""

    class _Result:
        def fetchone(self):
            return row

    class _Session:
        async def __aenter__(self_):
            return self_

        async def __aexit__(self_, *a):
            return None

        async def execute(self_, *a, **kw):
            return _Result()

    def _factory():
        return _Session()

    return _factory


class TestParseErrorBranch:
    """Bridge #82: when the arbiter returned non-empty text that
    yields zero parseable sections (refused / drift), the IN02
    finding must call it a parse error, not a partial truncation."""

    def test_parse_error_message_when_response_unparseable(
        self, monkeypatch,
    ):
        from tools import audit_engine
        import database

        ts = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        # Non-empty arbiter response with NO recognisable rubric
        # headings. The compute_review_score fallback in the helper
        # will set parse_error=True.
        row = (
            1, "reviewer@queens.edu", ts,
            "I cannot fulfill this review request right now.",
            None,  # metadata absent -> falls into the re-parse branch.
        )
        monkeypatch.setattr(
            database, "AsyncSessionLocal", _fake_session(row))

        verdict = asyncio.run(audit_engine.compute_in02_attestation())

        assert verdict["status"] == "WARN"
        assert verdict["n_sections"] == 0
        assert verdict["parse_error"] is True
        evidence = verdict["evidence"]
        assert "could not be parsed" in evidence
        # The old "may have truncated" wording must NOT appear when
        # the actual cause is an unparseable response.
        assert "may have truncated" not in evidence

    def test_truncation_message_when_partial_response(self, monkeypatch):
        """A partial response (1-4 sections parsed) keeps the old
        "may have truncated" wording -- that branch is genuinely a
        truncated response, not a parse failure."""
        from tools import audit_engine
        import database

        ts = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        # Three sections parse cleanly; the response was cut short
        # before sections 4 and 5 -- canonical truncation symptom.
        partial = (
            "### 1. Data Sufficiency\n\n**Rating:** Strong\n\n"
            "### 2. Requirements\n\n**Rating:** Developing\n\n"
            "### 3. Deliverable Quality\n\n**Rating:** Developing\n"
        )
        row = (
            2, "reviewer@queens.edu", ts, partial, None,
        )
        monkeypatch.setattr(
            database, "AsyncSessionLocal", _fake_session(row))

        verdict = asyncio.run(audit_engine.compute_in02_attestation())

        assert verdict["status"] == "WARN"
        assert verdict["n_sections"] == 3
        assert verdict["parse_error"] is False
        evidence = verdict["evidence"]
        assert "parsed only 3 of 5" in evidence
        assert "may have truncated" in evidence
        # The new "could not be parsed" wording is reserved for the
        # parse-error branch; a partial response is not a parse error.
        assert "could not be parsed" not in evidence

    def test_pass_when_all_five_sections_present(self, monkeypatch):
        from tools import audit_engine
        import database

        ts = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        full = "\n".join(
            f"### {i}. Heading {i}\n\n**Rating:** Strong\n"
            for i in range(1, 6)
        )
        row = (3, "reviewer@queens.edu", ts, full, None)
        monkeypatch.setattr(
            database, "AsyncSessionLocal", _fake_session(row))

        verdict = asyncio.run(audit_engine.compute_in02_attestation())

        assert verdict["status"] == "PASS"
        assert verdict["n_sections"] == 5

    def test_metadata_parse_error_trusted_over_recompute(
        self, monkeypatch,
    ):
        """When the auto-review path wrote parse_error to metadata,
        the audit_engine trusts that value -- the canonical scorer
        already ran at review time. Patch the row so metadata has
        parse_error=True even though response_summary is empty; the
        finding still surfaces parse_error=True from metadata."""
        from tools import audit_engine
        import database

        ts = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        row = (
            4, "reviewer@queens.edu", ts,
            "(arbiter raised; see harness)",
            {"sections_rated": 0, "parse_error": True,
             "overall_rating": None},
        )
        monkeypatch.setattr(
            database, "AsyncSessionLocal", _fake_session(row))

        verdict = asyncio.run(audit_engine.compute_in02_attestation())

        assert verdict["status"] == "WARN"
        assert verdict["n_sections"] == 0
        assert verdict["parse_error"] is True
        assert "could not be parsed" in verdict["evidence"]
