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

    def test_valid_request_clears_validation(self):
        # Valid email + role → reaches create_user. Any status that is
        # not a validation rejection (422) or a duplicate (409) proves
        # validation was passed — 503 with no database, 201 with one.
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

        async def _send(email, display_name=None, notes=None):
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

        async def _send(email, display_name=None, notes=None):
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

        async def _send(email, display_name=None, notes=None):
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
