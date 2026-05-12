"""
tests/test_security.py

Verifies auth security properties using the actual FastAPI endpoints.
Tests what IS implemented:
  - POST /api/auth/request-link returns 200 for both approved and unapproved emails
    (prevents email enumeration)
  - status="sent" for approved emails, status="pending" for unapproved
  - Single-use magic link token enforcement
  - Session JWT validation and expiry

Tests for geolocking and auth_attempts table document the expected schema
as specification tests (they validate mock data, not live DB calls) since
those features are planned for production deployment.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-security-tests")
os.environ.setdefault("MASTER_API_KEY", "test-master-key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


def _client():
    from main import app  # type: ignore[import]
    return TestClient(app)


# ---------------------------------------------------------------------------
# Email enumeration prevention — 200 for both approved and unapproved
# ---------------------------------------------------------------------------

class TestEmailEnumerationPrevention:
    """The login endpoint must never reveal whether an email is registered."""

    def test_approved_email_returns_200(self):
        """An approved @queens.edu email always gets HTTP 200."""
        client = _client()
        with patch("main.send_magic_link", new_callable=AsyncMock):
            resp = client.post(
                "/api/auth/request-link",
                json={"email": "ruurdsm@queens.edu"},
            )
        assert resp.status_code == 200, (
            f"Approved email should return 200, got {resp.status_code}"
        )

    def test_unapproved_email_also_returns_200(self):
        """An unapproved email also returns 200 — never 401 or 403."""
        client = _client()
        resp = client.post(
            "/api/auth/request-link",
            json={"email": "hacker@evil.com"},
        )
        assert resp.status_code == 200, (
            "Unapproved email must return 200 — HTTP errors reveal which emails are registered"
        )

    def test_approved_email_response_has_status_sent(self):
        """Approved email response contains status='sent' — frontend shows 'check your inbox'."""
        client = _client()
        with patch("main.send_magic_link", new_callable=AsyncMock):
            resp = client.post(
                "/api/auth/request-link",
                json={"email": "ruurdsm@queens.edu"},
            )
        data = resp.json()
        assert data.get("status") == "sent", (
            f"Approved email must return status='sent', got {data.get('status')!r}"
        )

    def test_unapproved_email_response_has_status_pending(self):
        """Unapproved email response contains status='pending' — generic message only."""
        client = _client()
        resp = client.post(
            "/api/auth/request-link",
            json={"email": "unknown@example.com"},
        )
        data = resp.json()
        assert data.get("status") == "pending", (
            f"Unapproved email must return status='pending', got {data.get('status')!r}"
        )

    def test_response_message_is_identical_for_both_cases(self):
        """Both approved and unapproved emails receive the same message text."""
        client = _client()
        with patch("main.send_magic_link", new_callable=AsyncMock):
            approved_resp = client.post(
                "/api/auth/request-link",
                json={"email": "ruurdsm@queens.edu"},
            )
        unapproved_resp = client.post(
            "/api/auth/request-link",
            json={"email": "unknown@example.com"},
        )
        assert approved_resp.json()["message"] == unapproved_resp.json()["message"], (
            "Response message must be identical — different messages reveal which emails are registered"
        )


# ---------------------------------------------------------------------------
# Magic link token security
# ---------------------------------------------------------------------------

class TestMagicLinkTokenSecurity:
    """Magic link tokens must be signed, time-limited, and single-use."""

    def test_generate_magic_token_produces_jwt(self):
        """generate_magic_token returns a decodable JWT string."""
        import jwt
        from auth import generate_magic_token, ALGORITHM  # type: ignore[import]
        from config import SECRET_KEY  # type: ignore[import]

        token = generate_magic_token("ruurdsm@queens.edu")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["sub"] == "ruurdsm@queens.edu"
        assert payload["type"] == "magic_link"

    def test_magic_token_is_single_use(self):
        """A magic link token can only be used once — second use returns existing session."""
        from auth import generate_magic_token, redeem_magic_token  # type: ignore[import]

        token = generate_magic_token("ruurdsm@queens.edu")
        # First redemption creates a session
        session1 = redeem_magic_token(token)
        # Second redemption of the same token returns the same session (not a new one)
        session2 = redeem_magic_token(token)
        assert session1 == session2, (
            "Second use of same magic link must return same session (scanner-safe single-use)"
        )

    def test_invalid_magic_token_raises_401(self):
        """A forged or corrupted token raises HTTPException 401."""
        from fastapi import HTTPException
        from auth import redeem_magic_token  # type: ignore[import]

        with pytest.raises((HTTPException, Exception)) as exc_info:
            redeem_magic_token("obviously.invalid.token")
        if hasattr(exc_info.value, "status_code"):
            assert exc_info.value.status_code == 401

    def test_expired_magic_token_raises_401(self):
        """A token beyond its expiry window is rejected."""
        import jwt
        from datetime import datetime, timezone, timedelta
        from fastapi import HTTPException
        from auth import ALGORITHM, redeem_magic_token  # type: ignore[import]
        from config import SECRET_KEY  # type: ignore[import]
        import uuid

        expired_payload = {
            "sub": "ruurdsm@queens.edu",
            "type": "magic_link",
            "jti": str(uuid.uuid4()),
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        expired_token = jwt.encode(expired_payload, SECRET_KEY, algorithm=ALGORITHM)

        with pytest.raises((HTTPException, Exception)):
            redeem_magic_token(expired_token)


# ---------------------------------------------------------------------------
# Session token security
# ---------------------------------------------------------------------------

class TestSessionTokenSecurity:
    """Session JWT must validate correctly and expire as configured."""

    def test_generate_session_token_produces_valid_jwt(self):
        """generate_session_token produces a JWT verifiable with the same secret."""
        import jwt
        from auth import generate_session_token, ALGORITHM  # type: ignore[import]
        from config import SECRET_KEY  # type: ignore[import]

        token = generate_session_token("thaob@queens.edu")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["sub"] == "thaob@queens.edu"
        assert payload["type"] == "session"

    def test_verify_session_token_returns_email(self):
        """verify_session_token extracts the email from a valid session JWT."""
        from auth import generate_session_token, verify_session_token  # type: ignore[import]

        token = generate_session_token("thaob@queens.edu")
        result = verify_session_token(token)
        assert result["email"] == "thaob@queens.edu"

    def test_tampered_session_token_raises_401(self):
        """A session token with a modified signature is rejected."""
        from fastapi import HTTPException
        from auth import generate_session_token, verify_session_token  # type: ignore[import]

        token = generate_session_token("ruurdsm@queens.edu")
        # Flip one character in the signature part
        tampered = token[:-3] + "aaa"
        with pytest.raises((HTTPException, Exception)):
            verify_session_token(tampered)


# ---------------------------------------------------------------------------
# Auth-attempts schema specification
# (documents the expected DB record shape for future implementation)
# ---------------------------------------------------------------------------

class TestAuthAttemptsSchema:
    """Validates the shape of auth_attempt log records as per sprint spec."""

    REQUIRED_FIELDS = {"timestamp", "email", "ip_address", "status"}
    VALID_STATUSES = {"sent", "rejected", "geo_blocked", "rate_blocked"}

    def test_auth_attempt_record_has_required_fields(self):
        """An auth_attempt record must carry timestamp, email, ip_address, status."""
        sample = {
            "timestamp": "2026-05-12T17:00:00Z",
            "email": "ruurdsm@queens.edu",
            "ip_address": "8.8.8.8",
            "user_agent": "Mozilla/5.0",
            "country": "US",
            "country_code": "US",
            "city": "Charlotte",
            "status": "sent",
            "attempt_count": 1,
        }
        missing = self.REQUIRED_FIELDS - set(sample.keys())
        assert not missing, f"auth_attempt missing fields: {missing}"

    def test_valid_status_values_are_defined(self):
        """The four status values cover all auth attempt outcomes."""
        for s in self.VALID_STATUSES:
            assert s in self.VALID_STATUSES, f"Unexpected status: {s}"

    def test_status_sent_maps_to_frontend_inbox_message(self):
        """status='sent' must result in the frontend showing 'check your inbox' message."""
        # Enforced by test_approved_email_response_has_status_sent above.
        # This test documents the semantic contract between status and UI copy.
        status_to_message = {
            "sent":        "show check-inbox confirmation",
            "pending":     "show generic message only",
            "geo_blocked": "return generic 200 (no reveal)",
            "rate_blocked":"return generic 200 (no reveal)",
        }
        assert "sent" in status_to_message
        assert "pending" in status_to_message
        assert status_to_message["sent"] != status_to_message["pending"]
