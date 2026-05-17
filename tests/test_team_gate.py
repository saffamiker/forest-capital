"""
tests/test_team_gate.py

Tests for the two-tier access model — require_team_member.

Any authenticated user may explore the analytics and ask the council;
the action endpoints (document upload, the export endpoints, Academic
Review, the test runner) are restricted to PROJECT_TEAM_EMAILS. These
contract tests run everywhere — no database needed (the team check 403s
before any handler body runs).
"""
from __future__ import annotations

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

client = TestClient(app)

# panttserk@ (Dr. Panttser) is an authorised login but NOT a project
# team member — the canonical non-team authenticated user.
TEAM = "thaob@queens.edu"
NON_TEAM = "panttserk@queens.edu"
TEAM_HEADERS = {"X-API-Key": generate_session_token(TEAM)}
NON_TEAM_HEADERS = {"X-API-Key": generate_session_token(NON_TEAM)}

# A representative team-gated endpoint — the team check 403s before the
# multipart body is even parsed, so an empty POST is enough.
GATED = "/api/v1/export/package"


class TestRequireTeamMember:
    def test_returns_403_for_a_non_team_authenticated_user(self):
        resp = client.post(GATED, headers=NON_TEAM_HEADERS)
        assert resp.status_code == 403
        assert resp.json()["detail"] == (
            "This action is restricted to the project team.")

    def test_allows_a_team_member(self):
        # A team member clears the gate — whatever the handler then does,
        # it is never the 403 from require_team_member.
        resp = client.post(GATED, headers=TEAM_HEADERS)
        assert resp.status_code != 403

    def test_unauthenticated_request_is_401_not_403(self):
        # require_auth runs first — no credentials is 401, not 403.
        resp = client.post(GATED)
        assert resp.status_code == 401


class TestOpenTier:
    def test_council_explain_is_open_to_non_team(self):
        # The council/explain endpoint is auth-only — a non-team user
        # reaches it (the test env streams a deterministic mock).
        resp = client.post("/api/council/explain",
                            json={"metric": "Sharpe Ratio"},
                            headers=NON_TEAM_HEADERS)
        assert resp.status_code == 200

    def test_council_query_is_not_team_gated(self):
        # The council query endpoint accepts any authenticated user —
        # whatever the scope guard / council then do, it is never the
        # require_team_member 403.
        resp = client.post("/api/council/query",
                            json={"query": "Compare the portfolio strategies."},
                            headers=NON_TEAM_HEADERS)
        assert resp.status_code != 403


class TestGatedTier:
    def test_academic_review_rejects_a_non_team_user(self):
        resp = client.post("/api/council/academic-review",
                            headers=NON_TEAM_HEADERS)
        assert resp.status_code == 403

    def test_academic_review_admits_a_team_user(self):
        # A team member is not 403'd (the SSE stream then begins).
        resp = client.post("/api/council/academic-review",
                            headers=TEAM_HEADERS)
        assert resp.status_code != 403
