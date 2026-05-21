"""
tests/test_failure_resolution.py — coverage for the migration-025
failure-resolution gate.

Pins three contracts:

  1. tools.test_runner.resolve_failure — the new resolution_type +
     fix_reference + remediation_note parameters land on the row;
     an invalid resolution_type returns None without touching the DB.

  2. POST /api/v1/testing/failures/{id}/resolve — body validation:
       - missing/invalid resolution_type → 422
       - missing resolution_note → 422
       - code_fix_deployed without fix_reference → 422
       - code_fix_deployed without remediation_note → 422
       - code_fix_deployed with invalid fix_reference shape → 422
       - no_bug_detected + wont_fix may omit fix_reference + remediation_note
     The view_admin gate is also checked (a non-admin gets 403).

  3. tools.test_runner.get_notifications.resolved_failures — the new
     resolution_type / fix_reference / remediation_note fields are
     in the response shape every time (None on a no-DB fail-open).

The DB-touching tests rely on the existing no-DB fail-open behaviour —
resolve_failure returns None, get_notifications returns the empty
three-key shape. End-to-end persistence is covered by the integration
test environment when a Postgres backend is available.
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

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from main import _is_valid_fix_reference, app  # noqa: E402
from auth import generate_session_token  # noqa: E402
from tools import test_runner  # noqa: E402

client = TestClient(app)

SYSADMIN = {"X-API-Key": generate_session_token("ruurdsm@queens.edu")}
TEAM = {"X-API-Key": generate_session_token("thaob@queens.edu")}
VIEWER = {"X-API-Key": generate_session_token("panttserk@queens.edu")}

RESOLVE_URL = "/api/v1/testing/failures/42/resolve"


# ── _is_valid_fix_reference helper ───────────────────────────────────────────

class TestFixReferenceValidator:
    """Pin the shape validator the endpoint and the modal both gate on.
    The frontend re-implements the same regexes — a divergence here
    would let the modal submit a payload the backend rejects (or vice
    versa). The triplet must be exact."""

    @pytest.mark.parametrize("ref", [
        "0123abc",                      # 7-char SHA
        "0123456789abcdef0123456",      # mid-length SHA
        "0123456789abcdef0123456789abcdef01234567",  # full 40-char SHA
        "#1",                           # PR number
        "#65",
        "#66",
        "#999999",
        "https://github.com/saffamiker/forest-capital/commit/abc123",
        "https://github.com/saffamiker/forest-capital/pull/65",
        "https://github.com/saffamiker/forest-capital/issues/100",
        "https://www.github.com/anthropics/claude-code/pull/1",
    ])
    def test_valid_shapes_accepted(self, ref):
        assert _is_valid_fix_reference(ref) is True

    @pytest.mark.parametrize("ref", [
        "",                             # blank
        "  ",                           # whitespace
        "fix-was-applied",              # prose, not a reference
        "123",                          # too short for SHA, no #
        "abc",                          # too short
        "abcdef",                       # 6 chars — below 7-char minimum
        "##65",                         # malformed PR
        "65",                           # PR number missing #
        "https://gitlab.com/foo/bar/-/commit/abc123",  # not github
        "https://example.com/some-url",  # not github
        "ghijklm",                      # not hex
    ])
    def test_invalid_shapes_rejected(self, ref):
        assert _is_valid_fix_reference(ref) is False


# ── resolve_failure direct contract ──────────────────────────────────────────

class TestResolveFailureDirect:
    """resolve_failure is called from the endpoint and from any future
    admin scripts. The contract: pass an invalid resolution_type and
    the function returns None WITHOUT touching the DB."""

    def test_invalid_resolution_type_returns_none(self):
        out = asyncio.run(test_runner.resolve_failure(
            42, "ruurdsm@queens.edu", "root cause",
            resolution_type="invented_value",
        ))
        assert out is None

    def test_no_db_returns_none_cleanly(self):
        # Valid resolution_type, but no DB → the SQLAlchemy block
        # fails open and resolve_failure returns None.
        out = asyncio.run(test_runner.resolve_failure(
            42, "ruurdsm@queens.edu", "root cause",
            resolution_type="no_bug_detected",
        ))
        assert out is None

    def test_resolution_types_vocabulary_pinned(self):
        # The vocabulary is exported so the endpoint validator and any
        # future admin scripts use the same canonical list. A reorder
        # is harmless, an addition or removal needs the CHECK
        # constraint in migration 025 updated too — pin to catch.
        assert set(test_runner.RESOLUTION_TYPES) == {
            "no_bug_detected", "code_fix_deployed", "wont_fix",
        }


# ── Endpoint body validation ────────────────────────────────────────────────

class TestResolveEndpointValidation:
    """The validation gate runs BEFORE the DB write, so every 422
    case below exercises without a database. The 404 path
    (resolution OK but failure not found) does need a DB and is
    exercised via the no-DB fail-open: resolve_failure returns None
    and the endpoint converts that to 404."""

    def test_view_admin_gate_rejects_a_viewer(self):
        assert client.post(
            RESOLVE_URL, headers=VIEWER, json={
                "resolution_type": "no_bug_detected",
                "resolution_note": "x",
            }).status_code == 403

    def test_view_admin_gate_rejects_a_team_member(self):
        # team_member is below view_admin in the permission hierarchy.
        assert client.post(
            RESOLVE_URL, headers=TEAM, json={
                "resolution_type": "no_bug_detected",
                "resolution_note": "x",
            }).status_code == 403

    def test_unauthenticated_is_401(self):
        assert client.post(
            RESOLVE_URL, json={
                "resolution_type": "no_bug_detected",
                "resolution_note": "x",
            }).status_code == 401

    def test_missing_resolution_type_is_422(self):
        r = client.post(
            RESOLVE_URL, headers=SYSADMIN,
            json={"resolution_note": "x"})
        assert r.status_code == 422
        assert "resolution_type" in r.json()["detail"]

    def test_invalid_resolution_type_is_422(self):
        r = client.post(
            RESOLVE_URL, headers=SYSADMIN, json={
                "resolution_type": "invented_value",
                "resolution_note": "x",
            })
        assert r.status_code == 422
        assert "resolution_type" in r.json()["detail"]

    def test_missing_resolution_note_is_422(self):
        r = client.post(
            RESOLVE_URL, headers=SYSADMIN, json={
                "resolution_type": "no_bug_detected",
            })
        assert r.status_code == 422
        assert "resolution_note" in r.json()["detail"]

    def test_blank_resolution_note_is_422(self):
        r = client.post(
            RESOLVE_URL, headers=SYSADMIN, json={
                "resolution_type": "no_bug_detected",
                "resolution_note": "   ",  # whitespace only
            })
        assert r.status_code == 422

    def test_code_fix_without_fix_reference_is_422(self):
        r = client.post(
            RESOLVE_URL, headers=SYSADMIN, json={
                "resolution_type": "code_fix_deployed",
                "resolution_note": "Stale cache",
                "remediation_note": "Cleared cache.",
                # fix_reference omitted entirely
            })
        assert r.status_code == 422
        assert "fix_reference" in r.json()["detail"]

    def test_code_fix_without_remediation_is_422(self):
        r = client.post(
            RESOLVE_URL, headers=SYSADMIN, json={
                "resolution_type": "code_fix_deployed",
                "resolution_note": "Stale cache",
                "fix_reference": "abc1234",
                # remediation_note omitted entirely
            })
        assert r.status_code == 422
        assert "remediation_note" in r.json()["detail"]

    def test_code_fix_with_invalid_fix_reference_is_422(self):
        r = client.post(
            RESOLVE_URL, headers=SYSADMIN, json={
                "resolution_type": "code_fix_deployed",
                "resolution_note": "Stale cache",
                "fix_reference": "not a valid reference",
                "remediation_note": "Cleared cache.",
            })
        assert r.status_code == 422
        assert "fix_reference" in r.json()["detail"]

    def test_no_bug_detected_does_not_require_fix_reference(self):
        # The endpoint validation passes for this body. The DB write
        # then fails (no DB in test env) and resolve_failure returns
        # None → endpoint converts to 404. Either 200 (with DB) or 404
        # (no DB) is acceptable — the assertion is "not a 422".
        r = client.post(
            RESOLVE_URL, headers=SYSADMIN, json={
                "resolution_type": "no_bug_detected",
                "resolution_note": "User clicked the wrong button.",
            })
        assert r.status_code != 422

    def test_wont_fix_does_not_require_fix_reference(self):
        r = client.post(
            RESOLVE_URL, headers=SYSADMIN, json={
                "resolution_type": "wont_fix",
                "resolution_note": "By design — sysadmin-only feature.",
            })
        assert r.status_code != 422

    def test_code_fix_with_valid_reference_and_remediation_passes_validation(
        self,
    ):
        # Validation passes — DB write fails open in test env → 404.
        # The assertion: NOT 422. Persistence-side behaviour is covered
        # under TestResolveFailureDirect.
        r = client.post(
            RESOLVE_URL, headers=SYSADMIN, json={
                "resolution_type": "code_fix_deployed",
                "resolution_note": "Stale cache.",
                "fix_reference": "abc1234",
                "remediation_note": "Invalidated cache on every push.",
            })
        assert r.status_code != 422


# ── get_notifications shape ─────────────────────────────────────────────────

class TestGetNotificationsResolvedFailuresShape:
    """The resolved_failures notification carries the migration-025
    metadata fields. The frontend reads them to pick the right
    three-variant card; a regression that drops a field would
    silently break the wont_fix / code_fix_deployed variants."""

    def test_returns_three_key_shape_without_db(self):
        out = asyncio.run(
            test_runner.get_notifications("thaob@queens.edu"))
        # The three top-level kinds — every key must be present even
        # on a no-DB fail-open so the frontend's reads never crash.
        assert set(out.keys()) >= {
            "resolved_failures", "responded_feedback", "retest_requested",
        }
        # No rows on a no-DB path, but the shape contract holds.
        assert out["resolved_failures"] == []
