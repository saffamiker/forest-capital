"""
tests/test_platform_users.py

Tests for the database-managed access-control layer:
  - the manage_users permission gate on /api/v1/admin/users
  - create-user input validation
  - the config fallback that mirrors the migration-015 seed
  - the platform_users data layer's fail-open reads
  - /api/auth/me returning the authoritative permissions array

These run with no database (ENVIRONMENT=test): every platform_users
read swallows the connection error and returns a safe default, so the
gate, the validation and the fallback are all exercised without
Postgres. A token minted by generate_session_token(email) carries no
embedded permissions, so require_auth resolves the user through the
config fallback — which mirrors the seed (SYSADMIN_EMAILS → sysadmin,
PROJECT_TEAM_EMAILS → team_member, anything else → viewer).
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)

from fastapi.testclient import TestClient  # noqa: E402

from main import app, _valid_email, _clean_permissions  # noqa: E402
from auth import generate_session_token  # noqa: E402

client = TestClient(app)

# ruurdsm@ is in SYSADMIN_EMAILS — config_fallback resolves him to the
# sysadmin role, whose preset includes manage_users.
SYSADMIN = "ruurdsm@queens.edu"
# thaob@ is a project team member but not a sysadmin — has team_member
# but NOT manage_users.
TEAM = "thaob@queens.edu"
# panttserk@ is an authorised login but not on the team — a viewer.
VIEWER = "panttserk@queens.edu"

SYSADMIN_HEADERS = {"X-API-Key": generate_session_token(SYSADMIN)}
TEAM_HEADERS = {"X-API-Key": generate_session_token(TEAM)}
VIEWER_HEADERS = {"X-API-Key": generate_session_token(VIEWER)}
MASTER_HEADERS = {"X-API-Key": "test_master_key"}

USERS = "/api/v1/admin/users"


class TestManageUsersGate:
    """The /api/v1/admin/users endpoints require the manage_users
    permission — held only by the sysadmin and the master key."""

    def test_list_users_rejects_a_viewer(self):
        resp = client.get(USERS, headers=VIEWER_HEADERS)
        assert resp.status_code == 403

    def test_list_users_rejects_a_team_member(self):
        # A team member has team_member but not manage_users.
        resp = client.get(USERS, headers=TEAM_HEADERS)
        assert resp.status_code == 403

    def test_list_users_admits_the_sysadmin(self):
        # config_fallback resolves ruurdsm@ to sysadmin → manage_users.
        # The users list is [] with no database and the migration-015
        # seed when a database is present — assert the contract (200 + a
        # `users` list), not a count that is environment-dependent.
        resp = client.get(USERS, headers=SYSADMIN_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert "users" in body and isinstance(body["users"], list)

    def test_list_users_admits_the_master_key(self):
        resp = client.get(USERS, headers=MASTER_HEADERS)
        assert resp.status_code == 200

    def test_create_user_rejects_a_team_member(self):
        resp = client.post(USERS, json={"email": "x@queens.edu"},
                            headers=TEAM_HEADERS)
        assert resp.status_code == 403

    def test_patch_user_rejects_a_team_member(self):
        resp = client.patch(f"{USERS}/1", json={"notes": "x"},
                            headers=TEAM_HEADERS)
        assert resp.status_code == 403

    def test_delete_user_rejects_a_team_member(self):
        resp = client.delete(f"{USERS}/1", headers=TEAM_HEADERS)
        assert resp.status_code == 403

    def test_unauthenticated_request_is_401_not_403(self):
        resp = client.get(USERS)
        assert resp.status_code == 401


class TestActivityBreakdownEndpoint:
    """The /api/v1/admin/users/activity-breakdown endpoint — per-user
    interaction-type and session-type counts over a 30-day rolling
    window. manage_users-gated like every other /admin/users route."""

    ENDPOINT = "/api/v1/admin/users/activity-breakdown"

    def test_requires_auth(self):
        # Unauthenticated request — 401, not the 403 we use for a
        # logged-in user missing the permission.
        assert client.get(self.ENDPOINT).status_code == 401

    def test_rejects_a_team_member(self):
        # team_member alone does not include manage_users.
        resp = client.get(self.ENDPOINT, headers=TEAM_HEADERS)
        assert resp.status_code == 403

    def test_rejects_a_viewer(self):
        resp = client.get(self.ENDPOINT, headers=VIEWER_HEADERS)
        assert resp.status_code == 403

    def test_admits_the_sysadmin_and_returns_the_shape(self):
        resp = client.get(self.ENDPOINT, headers=SYSADMIN_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        # Top-level shape — three keys.
        assert set(body) == {"users", "period_days", "generated_at"}
        assert body["period_days"] == 30
        assert isinstance(body["users"], list)
        assert isinstance(body["generated_at"], str)

    def test_admits_the_master_key(self):
        resp = client.get(self.ENDPOINT, headers=MASTER_HEADERS)
        assert resp.status_code == 200

    def test_user_row_carries_the_full_breakdown_shape(self, monkeypatch):
        # Patch the data layer so we exercise the JSON-shaping in the
        # endpoint without a database. Verifies every documented field
        # appears on each user row and that the merge logic interleaves
        # the per-email breakdown / session_counts / user rows correctly.
        from tools import platform_users as pu

        async def _stub_users():
            return [
                {"id": 1, "email": "a@x.com", "display_name": "A User",
                 "role": "team_member", "permissions": ["team_member"],
                 "is_active": True, "created_at": None, "created_by": None,
                 "last_login_at": None, "notes": None,
                 "council_queries_used": 0, "council_queries_limit": None},
                {"id": 2, "email": "b@x.com", "display_name": "B User",
                 "role": "viewer", "permissions": [],
                 "is_active": True, "created_at": None, "created_by": None,
                 "last_login_at": None, "notes": None,
                 "council_queries_used": 0, "council_queries_limit": None},
            ]

        async def _stub_interactions():
            return {
                "a@x.com": {
                    "by_type": {"council": 12, "academic_review": 4,
                                "explain": 8, "qa": 2},
                    "total_interactions": 26,
                    "total_cost_usd": 0.6886,
                    "first_seen": "2026-05-01T10:00:00+00:00",
                    "last_seen":  "2026-05-19T17:00:00+00:00",
                },
                # b@x.com is intentionally absent — verifies the LEFT-JOIN
                # contract: every platform_users row appears even with
                # zero interactions.
            }

        async def _stub_sessions():
            return {
                "a@x.com": {"analytical": 280, "testing": 45},
            }

        monkeypatch.setattr(pu, "_fetch_platform_users", _stub_users)
        monkeypatch.setattr(pu, "_fetch_interaction_breakdowns",
                            _stub_interactions)
        monkeypatch.setattr(pu, "_fetch_session_breakdowns", _stub_sessions)

        resp = client.get(self.ENDPOINT, headers=SYSADMIN_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        users = body["users"]
        assert len(users) == 2

        # User A — fully populated breakdown.
        a = next(u for u in users if u["email"] == "a@x.com")
        for key in ("email", "display_name", "role", "breakdown",
                    "session_breakdown", "total_interactions",
                    "total_cost_usd", "first_seen", "last_seen"):
            assert key in a, f"a@x.com row missing {key}"
        assert a["breakdown"] == {"council": 12, "academic_review": 4,
                                  "explain": 8, "qa": 2}
        assert a["session_breakdown"] == {"analytical": 280, "testing": 45}
        assert a["total_interactions"] == 26
        assert a["total_cost_usd"] == pytest.approx(0.6886, rel=1e-6)
        assert a["display_name"] == "A User"
        assert a["role"] == "team_member"

        # User B — zero-activity row still appears (LEFT-JOIN contract).
        b = next(u for u in users if u["email"] == "b@x.com")
        assert b["breakdown"] == {}
        assert b["session_breakdown"] == {}
        assert b["total_interactions"] == 0
        assert b["total_cost_usd"] == 0.0
        assert b["first_seen"] is None
        assert b["last_seen"] is None

    def test_empty_result_when_no_users(self, monkeypatch):
        from tools import platform_users as pu

        async def _no_users():
            return []

        async def _no_data():
            return {}

        monkeypatch.setattr(pu, "_fetch_platform_users", _no_users)
        monkeypatch.setattr(pu, "_fetch_interaction_breakdowns", _no_data)
        monkeypatch.setattr(pu, "_fetch_session_breakdowns", _no_data)

        resp = client.get(self.ENDPOINT, headers=SYSADMIN_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["users"] == []
        assert body["period_days"] == 30


class TestCreateUserValidation:
    """The create endpoint validates email and role before touching the
    database. A request that clears validation reaches create_user —
    503 with no database, 201 against a database (the migration-015
    seed applied). Either way it is NOT a validation rejection."""

    def test_invalid_email_is_422(self):
        resp = client.post(USERS, json={"email": "not-an-email"},
                            headers=SYSADMIN_HEADERS)
        assert resp.status_code == 422

    def test_missing_email_is_422(self):
        resp = client.post(USERS, json={"display_name": "No Email"},
                            headers=SYSADMIN_HEADERS)
        assert resp.status_code == 422

    def test_invalid_role_is_422(self):
        resp = client.post(USERS,
                            json={"email": "new@queens.edu", "role": "wizard"},
                            headers=SYSADMIN_HEADERS)
        assert resp.status_code == 422

    def test_valid_request_clears_validation(self, clean_platform_users):
        # Valid email + role → reaches create_user. Any status that is
        # not a validation rejection (422) or a duplicate (409) proves
        # validation was passed — 503 with no database, 201 with one.
        # The clean_platform_users fixture (conftest) removes the row
        # this test inserts when a database is present, so a re-run
        # never collides on the duplicate email.
        resp = client.post(USERS,
                            json={"email": "new@queens.edu", "role": "viewer"},
                            headers=SYSADMIN_HEADERS)
        assert resp.status_code not in (422, 409)


class TestPatchDeleteNotFound:
    """With no database get_user_by_id returns None — the endpoints
    404 rather than leaking a database error."""

    def test_patch_unknown_user_is_404(self):
        resp = client.patch(f"{USERS}/999", json={"notes": "x"},
                            headers=SYSADMIN_HEADERS)
        assert resp.status_code == 404

    def test_delete_unknown_user_is_404(self):
        resp = client.delete(f"{USERS}/999", headers=SYSADMIN_HEADERS)
        assert resp.status_code == 404


class TestAuthMe:
    """/api/auth/me returns the authoritative permissions array the
    frontend gates its UI on."""

    def test_sysadmin_me_carries_manage_users(self):
        resp = client.get("/api/auth/me", headers=SYSADMIN_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "sysadmin"
        assert "manage_users" in body["permissions"]

    def test_viewer_me_lacks_manage_users(self):
        resp = client.get("/api/auth/me", headers=VIEWER_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "viewer"
        assert "manage_users" not in body["permissions"]


class TestConfigFallback:
    """config_fallback mirrors the migration-015 seed exactly, so a
    database outage degrades gracefully — Michael keeps administration,
    the team keep their access."""

    def test_sysadmin_email_resolves_to_sysadmin(self):
        from tools.platform_users import config_fallback
        r = config_fallback(SYSADMIN)
        assert r["role"] == "sysadmin"
        assert "manage_users" in r["permissions"]

    def test_team_email_resolves_to_team_member(self):
        from tools.platform_users import config_fallback
        r = config_fallback("murdockm@queens.edu")
        assert r["role"] == "team_member"
        assert "manage_users" not in r["permissions"]

    def test_other_authorised_email_resolves_to_viewer(self):
        from tools.platform_users import config_fallback
        r = config_fallback(VIEWER)
        assert r["role"] == "viewer"

    def test_unknown_email_resolves_to_viewer(self):
        from tools.platform_users import config_fallback
        r = config_fallback("stranger@example.com")
        assert r["role"] == "viewer"

    def test_email_match_is_case_insensitive(self):
        from tools.platform_users import config_fallback
        assert config_fallback("RuurdsM@Queens.Edu")["role"] == "sysadmin"


class TestFailOpenReads:
    """Every platform_users read swallows a database error and returns a
    safe value rather than raising — a database problem must never lock
    the team out. These assertions verify that safe-value contract, and
    hold whether or not a database is present: with no database the
    reads fail open to the empty default, and with a database (the
    migration-015 seed applied) they return real rows. They never raise
    and never return an unsafe type either way."""

    def test_get_active_user_returns_safe_value(self):
        from tools.platform_users import get_active_user
        result = asyncio.run(get_active_user(SYSADMIN))
        assert result is None or isinstance(result, dict)

    def test_list_all_users_returns_a_list(self):
        from tools.platform_users import list_all_users
        assert isinstance(asyncio.run(list_all_users()), list)


class TestListAllUsersSubQueryIsolation:
    """
    list_all_users is split into three sub-queries — _fetch_platform_users,
    _fetch_activity_counts, _fetch_ai_costs — each in its own session
    with its own try/except. A failure in one MUST NOT poison the others;
    in particular, a missing/failing ai_cost query cannot wipe the whole
    user list (which was the pre-fix behaviour when the monolithic
    try/except wrapped all three together).
    """

    def _stub_user(self, email: str) -> dict:
        return {
            "id": 1, "email": email, "display_name": email,
            "role": "team_member", "permissions": ["team_member"],
            "is_active": True, "created_at": None, "created_by": "x",
            "last_login_at": None, "notes": None,
            "council_queries_used": 0, "council_queries_limit": None,
        }

    def test_ai_costs_failure_does_not_drop_the_user_list(self, monkeypatch):
        # The original bug — when the cost query failed (e.g. the
        # estimated_cost_usd column did not exist), the monolithic
        # try/except swallowed everything and the page rendered empty.
        from tools import platform_users as pu

        users = [self._stub_user("a@x.com"), self._stub_user("b@x.com")]

        async def _ok_users():
            return list(users)

        async def _ok_counts():
            return {"a@x.com": 5, "b@x.com": 3}

        async def _fail_costs():
            raise RuntimeError("estimated_cost_usd does not exist")

        monkeypatch.setattr(pu, "_fetch_platform_users", _ok_users)
        monkeypatch.setattr(pu, "_fetch_activity_counts", _ok_counts)
        # Wrap so the inner raise is swallowed by _fetch_ai_costs' own
        # try/except, mirroring how the real helper handles a bad DB call.
        async def _wrapped_costs():
            try:
                return await _fail_costs()
            except Exception:
                return {}
        monkeypatch.setattr(pu, "_fetch_ai_costs", _wrapped_costs)

        result = asyncio.run(pu.list_all_users())
        # The user list survives. Activity counts present, cost defaulted.
        assert [u["email"] for u in result] == ["a@x.com", "b@x.com"]
        assert result[0]["activity_count"] == 5
        assert result[1]["activity_count"] == 3
        assert result[0]["ai_cost_usd"] == 0.0
        assert result[1]["ai_cost_usd"] == 0.0

    def test_activity_counts_failure_does_not_drop_the_user_list(
        self, monkeypatch,
    ):
        from tools import platform_users as pu

        async def _ok_users():
            return [self._stub_user("a@x.com")]

        async def _empty_counts():
            return {}

        async def _ok_costs():
            return {"a@x.com": 0.123456}

        monkeypatch.setattr(pu, "_fetch_platform_users", _ok_users)
        monkeypatch.setattr(pu, "_fetch_activity_counts", _empty_counts)
        monkeypatch.setattr(pu, "_fetch_ai_costs", _ok_costs)

        result = asyncio.run(pu.list_all_users())
        assert len(result) == 1
        assert result[0]["activity_count"] == 0  # defaulted
        assert result[0]["ai_cost_usd"] == 0.123456  # preserved

    def test_base_user_query_failure_returns_empty_list(self, monkeypatch):
        # If platform_users itself is unreachable, there are genuinely no
        # users to display — this is the ONLY failure mode that returns [].
        from tools import platform_users as pu

        async def _no_users():
            return []

        async def _should_not_be_called():
            raise AssertionError("must not be called when users is empty")

        monkeypatch.setattr(pu, "_fetch_platform_users", _no_users)
        monkeypatch.setattr(pu, "_fetch_activity_counts", _should_not_be_called)
        monkeypatch.setattr(pu, "_fetch_ai_costs", _should_not_be_called)

        assert asyncio.run(pu.list_all_users()) == []

    def test_all_three_succeed_returns_full_enriched_users(self, monkeypatch):
        from tools import platform_users as pu

        async def _ok_users():
            return [self._stub_user("a@x.com"), self._stub_user("b@x.com")]

        async def _ok_counts():
            return {"a@x.com": 42}

        async def _ok_costs():
            return {"b@x.com": 1.5}

        monkeypatch.setattr(pu, "_fetch_platform_users", _ok_users)
        monkeypatch.setattr(pu, "_fetch_activity_counts", _ok_counts)
        monkeypatch.setattr(pu, "_fetch_ai_costs", _ok_costs)

        result = asyncio.run(pu.list_all_users())
        # Each user gets both fields; emails missing from a sub-query map
        # default to 0 — no KeyError, no missing key on the response.
        assert result[0]["activity_count"] == 42
        assert result[0]["ai_cost_usd"] == 0.0
        assert result[1]["activity_count"] == 0
        assert result[1]["ai_cost_usd"] == 1.5

    def test_count_active_sysadmins_returns_a_count(self):
        from tools.platform_users import count_active_sysadmins
        count = asyncio.run(count_active_sysadmins())
        assert isinstance(count, int) and count >= 0

    def test_email_exists_false_for_a_nonexistent_email(self):
        # A genuinely non-existent address — False both with no database
        # (fail-open) and with a database (not in the migration-015 seed).
        from tools.platform_users import email_exists
        assert asyncio.run(email_exists("notauser@example.com")) is False

    def test_resolve_user_falls_back_to_config(self):
        from tools.platform_users import resolve_user
        r = asyncio.run(resolve_user(SYSADMIN))
        assert r["role"] == "sysadmin"

    def test_is_login_allowed_falls_back_to_allowlist(self):
        # With no database, is_login_allowed falls back to ALLOWED_EMAILS:
        # an authorised email passes, a stranger is refused.
        from tools.platform_users import is_login_allowed
        assert asyncio.run(is_login_allowed(SYSADMIN)) is True
        assert asyncio.run(is_login_allowed("stranger@example.com")) is False


class TestMagicLinkRequest:
    """The magic-link request endpoint never enumerates users — both an
    authorised and an unauthorised email return an identical 200."""

    def test_authorised_email_returns_200(self):
        resp = client.post("/api/auth/request-link", json={"email": SYSADMIN})
        assert resp.status_code == 200

    def test_unauthorised_email_returns_identical_200(self):
        resp = client.post("/api/auth/request-link",
                            json={"email": "stranger@example.com"})
        assert resp.status_code == 200


class TestPureHelpers:
    """_valid_email and _clean_permissions — the create/update helpers."""

    def test_valid_email_accepts_a_normal_address(self):
        assert _valid_email("person@queens.edu") is True

    def test_valid_email_rejects_no_at_sign(self):
        assert _valid_email("personqueens.edu") is False

    def test_valid_email_rejects_no_tld(self):
        assert _valid_email("person@queens") is False

    def test_valid_email_rejects_empty(self):
        assert _valid_email("") is False

    def test_clean_permissions_filters_unknown_keys(self):
        cleaned = _clean_permissions(
            ["view_analytics", "fly_to_the_moon"], "viewer")
        assert "view_analytics" in cleaned
        assert "fly_to_the_moon" not in cleaned

    def test_clean_permissions_falls_back_to_role_preset(self):
        # A non-list input → the role's preset.
        from config import ROLE_PRESETS
        assert _clean_permissions(None, "team_member") == list(
            ROLE_PRESETS["team_member"])


class TestWelcomeEmail:
    """A welcome email is sent — fail-open — after a user is created via
    POST /api/v1/admin/users."""

    @staticmethod
    def _patch_creation(monkeypatch, *, created):
        """Stubs create_user / email_exists so the endpoint reaches the
        welcome-email step without a database."""
        import tools.platform_users as pu

        async def _exists(_email: str) -> bool:
            return False

        async def _create(**_kwargs):
            return created
        monkeypatch.setattr(pu, "email_exists", _exists)
        monkeypatch.setattr(pu, "create_user", _create)

    def test_welcome_email_sent_on_successful_creation(self, monkeypatch):
        self._patch_creation(monkeypatch, created={
            "id": 9, "email": "guest@queens.edu", "display_name": "Guest",
            "role": "viewer", "notes": None, "permissions": [],
            "is_active": True,
        })
        import auth
        sent_to: list[str] = []

        async def _send(email, display_name=None, notes=None,
                        council_limit=None):
            sent_to.append(email)
            return True
        monkeypatch.setattr(auth, "send_welcome_email", _send)
        resp = client.post(USERS,
                            json={"email": "guest@queens.edu", "role": "viewer"},
                            headers=SYSADMIN_HEADERS)
        assert resp.status_code == 200
        assert sent_to == ["guest@queens.edu"]
        assert resp.json()["welcome_email_sent"] is True

    def test_welcome_email_not_sent_when_creation_fails(self, monkeypatch):
        # create_user returns None (no database) → 503, and the email
        # step is never reached.
        self._patch_creation(monkeypatch, created=None)
        import auth
        sent: list[str] = []

        async def _send(email, display_name=None, notes=None,
                        council_limit=None):
            sent.append(email)
            return True
        monkeypatch.setattr(auth, "send_welcome_email", _send)
        resp = client.post(USERS,
                            json={"email": "guest@queens.edu", "role": "viewer"},
                            headers=SYSADMIN_HEADERS)
        assert resp.status_code == 503
        assert sent == []

    def test_email_failure_does_not_block_creation(self, monkeypatch):
        self._patch_creation(monkeypatch, created={
            "id": 9, "email": "guest@queens.edu", "display_name": None,
            "role": "viewer", "notes": None, "permissions": [],
            "is_active": True,
        })
        import auth

        async def _send(email, display_name=None, notes=None,
                        council_limit=None):
            return False   # delivery failed — fail-open
        monkeypatch.setattr(auth, "send_welcome_email", _send)
        resp = client.post(USERS,
                            json={"email": "guest@queens.edu", "role": "viewer"},
                            headers=SYSADMIN_HEADERS)
        assert resp.status_code == 200   # the user is still created
        assert resp.json()["welcome_email_sent"] is False

    def test_email_body_contains_registered_email(self):
        from auth import build_welcome_email
        _subject, body = build_welcome_email("guest@queens.edu", "Guest", None)
        assert "guest@queens.edu" in body

    def test_email_body_contains_platform_url(self):
        from auth import build_welcome_email
        from config import PLATFORM_URL
        _subject, body = build_welcome_email("guest@queens.edu", None, None)
        assert PLATFORM_URL in body

    def test_email_body_includes_notes_when_given(self):
        from auth import build_welcome_email
        _subject, body = build_welcome_email(
            "guest@queens.edu", "Guest", "FNA 670 guest reviewer")
        assert "You have been added as: FNA 670 guest reviewer" in body


class TestCouncilAllocation:
    """Viewer council query allocation — the welcome-email line, the
    council endpoint gate, and the Settings override controls."""

    # ── Welcome email (CHANGE 4) ──────────────────────────────────────────────

    def test_welcome_email_states_the_council_allocation(self):
        from auth import build_welcome_email
        _subject, body = build_welcome_email(
            "guest@queens.edu", "Guest", None, council_limit=5)
        assert "provisioned with 5 council queries" in body

    def test_welcome_email_omits_allocation_for_unlimited(self):
        from auth import build_welcome_email
        _subject, body = build_welcome_email(
            "tm@queens.edu", "TM", None, council_limit=None)
        assert "provisioned with" not in body

    # ── increment_council_queries — fail-open ────────────────────────────────

    def test_increment_council_queries_no_db_returns_none(self, monkeypatch):
        import asyncio
        import database
        import tools.platform_users as pu
        monkeypatch.setattr(database, "AsyncSessionLocal", None, raising=False)
        assert asyncio.run(pu.increment_council_queries("x@y.edu")) is None

    # ── Council endpoint gate ─────────────────────────────────────────────────

    @staticmethod
    def _patch_user(monkeypatch, *, used, limit):
        """Stub the council endpoint's allocation lookup. get_council_
        allocation is monkeypatched (not get_active_user) so auth
        resolution is untouched."""
        import tools.platform_users as pu

        async def _get(_email):
            return {"council_queries_used": used,
                    "council_queries_limit": limit}

        async def _inc(_email):
            return {"council_queries_used": used + 1,
                    "council_queries_limit": limit}
        monkeypatch.setattr(pu, "get_council_allocation", _get)
        monkeypatch.setattr(pu, "increment_council_queries", _inc)

    def test_viewer_blocked_when_at_limit(self, monkeypatch):
        self._patch_user(monkeypatch, used=5, limit=5)
        resp = client.post("/api/council/query", json={"query": "test query"},
                            headers=SYSADMIN_HEADERS)
        assert resp.status_code == 429
        assert resp.json()["detail"]["error"] == "council_limit_reached"

    def test_viewer_allowed_when_under_limit(self, monkeypatch):
        # Query 5 of 5 (used=4) is still allowed.
        self._patch_user(monkeypatch, used=4, limit=5)
        resp = client.post("/api/council/query", json={"query": "test query"},
                            headers=SYSADMIN_HEADERS)
        assert resp.status_code == 200

    def test_unlimited_user_is_never_blocked(self, monkeypatch):
        # A NULL limit is unlimited even with a high used count.
        self._patch_user(monkeypatch, used=999, limit=None)
        resp = client.post("/api/council/query", json={"query": "test query"},
                            headers=SYSADMIN_HEADERS)
        assert resp.status_code == 200

    def test_increment_called_once_on_an_allowed_query(self, monkeypatch):
        import tools.platform_users as pu

        async def _get(_email):
            return {"council_queries_used": 1, "council_queries_limit": 5}
        calls: list[str] = []

        async def _inc(email):
            calls.append(email)
            return {"council_queries_used": 2, "council_queries_limit": 5}
        monkeypatch.setattr(pu, "get_council_allocation", _get)
        monkeypatch.setattr(pu, "increment_council_queries", _inc)
        resp = client.post("/api/council/query", json={"query": "test query"},
                            headers=SYSADMIN_HEADERS)
        assert resp.status_code == 200
        assert len(calls) == 1   # counted exactly once

    # ── Settings override — PATCH council fields ──────────────────────────────

    @staticmethod
    def _patch_update(monkeypatch):
        """Stub get_user_by_id / count_active_sysadmins / update_user so the
        PATCH endpoint reaches the council-field handling. Returns the list
        the update_user fields dict is recorded into."""
        import tools.platform_users as pu
        recorded: list[dict] = []

        async def _get_by_id(_uid):
            return {"id": 1, "email": "v@queens.edu", "role": "viewer",
                    "permissions": ["view_analytics", "ask_council"],
                    "is_active": True}

        async def _count():
            return 5

        async def _update(uid, fields):
            recorded.append(fields)
            return {"id": uid, **fields}
        monkeypatch.setattr(pu, "get_user_by_id", _get_by_id)
        monkeypatch.setattr(pu, "count_active_sysadmins", _count)
        monkeypatch.setattr(pu, "update_user", _update)
        return recorded

    def test_reset_usage_patches_used_to_zero(self, monkeypatch):
        recorded = self._patch_update(monkeypatch)
        resp = client.patch("/api/v1/admin/users/1",
                             json={"council_queries_used": 0},
                             headers=SYSADMIN_HEADERS)
        assert resp.status_code == 200
        assert recorded and recorded[0].get("council_queries_used") == 0

    def test_adjust_limit_patches_the_new_value(self, monkeypatch):
        recorded = self._patch_update(monkeypatch)
        resp = client.patch("/api/v1/admin/users/1",
                             json={"council_queries_limit": 12},
                             headers=SYSADMIN_HEADERS)
        assert resp.status_code == 200
        assert recorded and recorded[0].get("council_queries_limit") == 12

    def test_unlimited_patches_limit_to_null(self, monkeypatch):
        recorded = self._patch_update(monkeypatch)
        resp = client.patch("/api/v1/admin/users/1",
                             json={"council_queries_limit": None},
                             headers=SYSADMIN_HEADERS)
        assert resp.status_code == 200
        assert recorded and "council_queries_limit" in recorded[0]
        assert recorded[0]["council_queries_limit"] is None

    def test_negative_limit_is_rejected(self, monkeypatch):
        self._patch_update(monkeypatch)
        resp = client.patch("/api/v1/admin/users/1",
                             json={"council_queries_limit": -3},
                             headers=SYSADMIN_HEADERS)
        assert resp.status_code == 422
