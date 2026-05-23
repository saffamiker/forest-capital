"""tests/test_qa_audit_error_surface.py — /api/qa/audit error
surface contract.

May 23 2026 bug report: the user clicked "Re-run" on an INCOMPLETE
QA check; the button showed a running state, completed with no
visible result, and reset. Root cause: the endpoint was catching
every exception, logging it, and returning MOCK_QA_AUDIT — a 200
response with mock data. The frontend treated that as a successful
run and overwrote real per-check state with the mock's empty data.

Fix: errors are now propagated as HTTP 500 with a structured detail
object {error, error_type, message, hint}. The frontend's
_formatAuditError helper renders these to a readable error string
in the qaStore.error field.

These tests pin the contract.
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")


class TestErrorSurfaceContract:
    """Source-level checks — the handler must:
      1. Raise HTTPException 500 on failure (not silently return mock)
      2. Carry a structured detail object so the frontend can render it
      3. Re-raise HTTPException (don't catch + repackage 4xx as 500)
    """

    def test_qa_audit_raises_http_exception_on_failure(self):
        # Find the qa_audit function in main and inspect its source.
        from main import qa_audit
        src = inspect.getsource(qa_audit)
        # The handler must raise HTTPException on failure, NOT return
        # MOCK_QA_AUDIT in the production path.
        assert "raise HTTPException" in src
        # MOCK_QA_AUDIT may still appear (for the test-env branch);
        # but it must NOT appear AFTER an except Exception line as
        # the fallback for live production errors.
        # Source-level proxy: check that the except block carries
        # the new structured detail + raise — not a return of
        # MOCK_QA_AUDIT.
        assert "status_code=500" in src
        assert '"error":      "qa_audit_failed"' in src \
            or '"error": "qa_audit_failed"' in src
        assert "error_type" in src
        assert "hint" in src

    def test_qa_audit_test_env_still_returns_mock(self):
        # The test-env path is BEFORE the try block — pytest sees the
        # mock audit so tests don't depend on a live data pipeline.
        from main import qa_audit
        src = inspect.getsource(qa_audit)
        # The test-env early-return must precede the try block. We
        # check for the early-return statement, not its exact form.
        assert "ENVIRONMENT == \"test\"" in src
        assert "return MOCK_QA_AUDIT" in src

    def test_http_exception_is_reraised_not_repackaged(self):
        from main import qa_audit
        src = inspect.getsource(qa_audit)
        # If a 4xx HTTPException somehow bubbles up from a
        # dependency, we want to re-raise it as-is so the original
        # status code and detail land. The except chain must catch
        # HTTPException FIRST.
        assert "except HTTPException:" in src
        # And the raise on that branch is the original exception,
        # not a wrapped 500.
        assert "except HTTPException:\n            raise" in src

    def test_traceback_logged_for_debugging(self):
        from main import qa_audit
        src = inspect.getsource(qa_audit)
        # The logger must include enough context for production
        # debugging — error type + a truncated traceback.
        assert "traceback" in src
        assert "error_type" in src
