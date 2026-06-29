"""Layer 3b -- readiness endpoint extension + deck/appendix manifest persistence.

Pins:
  * GET /api/v1/report/readiness now includes an export_verification
    map (executive_brief / presentation_deck / analytical_appendix ->
    one of 'verified' | 'warned' | 'failed' | 'not_exported').
  * _export_verification_status (the helper that backs the map)
    classifies drafts correctly given the export_verification JSONB
    column shape.
  * editor_drafts.get_current_draft_with_layer3 falls back gracefully
    on a missing _session().
  * _finalize_deck accepts the new substitution_table kwarg without
    breaking existing call sites.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault(
    "SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "test-master-key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,"
    "murdockm@queens.edu,panttserk@queens.edu")


def _auth_headers() -> dict:
    from config import MASTER_API_KEY  # type: ignore[import]
    return {"X-API-Key": MASTER_API_KEY}


@pytest.fixture
def client() -> TestClient:
    from main import app  # noqa: WPS433
    return TestClient(app)


# ── Readiness endpoint shape: includes export_verification ──────────────


class TestReadinessExportVerification:

    def test_readiness_payload_includes_export_verification_map(
        self, client: TestClient,
    ):
        r = client.get("/api/v1/report/readiness",
                       headers=_auth_headers())
        assert r.status_code == 200
        body = r.json()
        assert "export_verification" in body, (
            "Layer 3b -- /report/readiness must surface "
            "export_verification for the Reports-page badges")
        ev = body["export_verification"]
        for k in ("executive_brief", "presentation_deck",
                  "analytical_appendix"):
            assert k in ev, f"export_verification missing key {k}"
            assert ev[k] in (
                "verified", "warned", "failed", "not_exported"), (
                f"{k} status {ev[k]} not in allowed verdict set")

    def test_readiness_export_verification_defaults_to_not_exported(
        self, client: TestClient,
    ):
        """In the test environment there is no live DB, so every
        document degrades to the neutral 'not_exported' state."""
        r = client.get("/api/v1/report/readiness",
                       headers=_auth_headers())
        body = r.json()
        ev = body["export_verification"]
        assert ev["executive_brief"] == "not_exported"
        assert ev["presentation_deck"] == "not_exported"
        assert ev["analytical_appendix"] == "not_exported"


# ── _export_verification_status helper classifies correctly ─────────────


class TestExportVerificationStatusHelper:
    """Pins the classification table directly so any future tweak to
    the verify_export_against_cache return shape that breaks the
    pill mapping is caught."""

    def _run(self, monkeypatch, drafts_by_type: dict):
        """Patches get_current_draft_with_layer3 to return the
        per-type draft shapes the test wants, then calls the helper."""
        from tools import editor_drafts

        async def _stub(email, doc_type):
            return drafts_by_type.get(doc_type)

        monkeypatch.setattr(
            editor_drafts, "get_current_draft_with_layer3", _stub)

        from main import _export_verification_status
        return asyncio.run(_export_verification_status("bob@x"))

    def test_verified_when_no_errors_no_warnings(self, monkeypatch):
        verified_draft = {
            "content_text": "irrelevant",
            "export_verification": {
                "passed": True, "errors": [], "warnings": [],
            },
        }
        out = self._run(monkeypatch, {
            "executive_brief": verified_draft,
            "presentation_deck": verified_draft,
            "analytical_appendix": verified_draft,
        })
        assert out["executive_brief"] == "verified"
        assert out["presentation_deck"] == "verified"
        assert out["analytical_appendix"] == "verified"

    def test_warned_when_warnings_only(self, monkeypatch):
        warned_draft = {
            "content_text": "irrelevant",
            "export_verification": {
                "passed": True, "errors": [],
                "warnings": [{"type": "stale_data_hash"}],
            },
        }
        out = self._run(monkeypatch, {
            "executive_brief": warned_draft,
        })
        assert out["executive_brief"] == "warned"

    def test_failed_when_errors_present(self, monkeypatch):
        failed_draft = {
            "content_text": "irrelevant",
            "export_verification": {
                "passed": False,
                "errors": [{"type": "value_missing_from_export"}],
                "warnings": [],
            },
        }
        out = self._run(monkeypatch, {
            "executive_brief": failed_draft,
        })
        assert out["executive_brief"] == "failed"

    def test_not_exported_when_no_draft(self, monkeypatch):
        out = self._run(monkeypatch, {})
        assert out["executive_brief"] == "not_exported"
        assert out["presentation_deck"] == "not_exported"
        assert out["analytical_appendix"] == "not_exported"

    def test_not_exported_when_export_verification_is_null(
        self, monkeypatch,
    ):
        out = self._run(monkeypatch, {
            "executive_brief": {
                "content_text": "x", "export_verification": None},
        })
        assert out["executive_brief"] == "not_exported"

    def test_skipped_treated_as_warned(self, monkeypatch):
        """A 'skipped' verification (no value_manifest yet, e.g. a
        pre-Layer-3 draft) should surface as 'warned' so the user is
        nudged to regenerate."""
        out = self._run(monkeypatch, {
            "executive_brief": {
                "content_text": "x",
                "export_verification": {
                    "passed": True, "errors": [], "warnings": [],
                    "skipped": "no_value_manifest",
                },
            },
        })
        assert out["executive_brief"] == "warned"

    def test_empty_email_returns_all_not_exported(self):
        from main import _export_verification_status
        out = asyncio.run(_export_verification_status(""))
        assert out["executive_brief"] == "not_exported"


# ── editor_drafts.get_current_draft_with_layer3 helper ──────────────────


class TestGetCurrentDraftWithLayer3:

    def test_returns_none_when_session_unavailable(self, monkeypatch):
        """No live DB -> _session() returns None -> helper returns
        None without raising."""
        from tools import editor_drafts
        monkeypatch.setattr(editor_drafts, "_session", lambda: None)
        out = asyncio.run(
            editor_drafts.get_current_draft_with_layer3(
                "bob@x", "executive_brief"))
        assert out is None


# ── _finalize_deck signature accepts substitution_table ─────────────────


class TestFinalizeDeckSignature:

    def test_finalize_deck_accepts_substitution_table_kwarg(self):
        """Layer 3b -- _finalize_deck must accept the new optional
        substitution_table kwarg. A signature-shape test ensures the
        old call (without the kwarg) and the new call (with the
        kwarg) both type-check without requiring a live LLM run."""
        import inspect
        from main import _finalize_deck
        sig = inspect.signature(_finalize_deck)
        assert "substitution_table" in sig.parameters, (
            "_finalize_deck must accept substitution_table for "
            "Layer 3b deck manifest persistence")
        # The new param defaults to None so existing callers still work.
        param = sig.parameters["substitution_table"]
        assert param.default is None
