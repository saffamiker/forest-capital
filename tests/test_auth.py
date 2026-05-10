"""
Sprint 1 — Auth tests.
Covers token generation, verification, session lifecycle, and security
properties of the magic-link authentication system.
"""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-auth-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)

import pytest
import jwt
from fastapi import HTTPException

import auth
from config import SECRET_KEY, MASTER_API_KEY
from auth import ALGORITHM


# ── Magic token generation ────────────────────────────────────────────────────

def test_generate_magic_token_returns_string():
    token = auth.generate_magic_token("ruurdsm@queens.edu")
    assert isinstance(token, str)
    assert len(token) > 0

def test_magic_token_is_decodable():
    email = "ruurdsm@queens.edu"
    token = auth.generate_magic_token(email)
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["sub"] == email

def test_magic_token_type_field():
    token = auth.generate_magic_token("ruurdsm@queens.edu")
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["type"] == "magic_link"

def test_magic_token_has_jti():
    token = auth.generate_magic_token("ruurdsm@queens.edu")
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert "jti" in payload

def test_magic_tokens_are_unique():
    """Each token must have a distinct jti — prevents replay."""
    t1 = auth.generate_magic_token("ruurdsm@queens.edu")
    t2 = auth.generate_magic_token("ruurdsm@queens.edu")
    p1 = jwt.decode(t1, SECRET_KEY, algorithms=[ALGORITHM])
    p2 = jwt.decode(t2, SECRET_KEY, algorithms=[ALGORITHM])
    assert p1["jti"] != p2["jti"]

def test_magic_token_expiry_is_set():
    token = auth.generate_magic_token("ruurdsm@queens.edu")
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert "exp" in payload


# ── Magic token verification ──────────────────────────────────────────────────

def test_verify_magic_token_returns_email():
    email = "ruurdsm@queens.edu"
    token = auth.generate_magic_token(email)
    result = auth.verify_magic_token(token)
    assert result == email

def test_verify_magic_token_invalid_raises_401():
    with pytest.raises(HTTPException) as exc_info:
        auth.verify_magic_token("not.a.valid.token")
    assert exc_info.value.status_code == 401

def test_verify_magic_token_wrong_secret_raises_401():
    token = jwt.encode(
        {"sub": "ruurdsm@queens.edu", "type": "magic_link"},
        "wrong-secret",
        algorithm=ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc_info:
        auth.verify_magic_token(token)
    assert exc_info.value.status_code == 401

def test_verify_magic_token_expired_raises_401():
    from datetime import datetime, timedelta, timezone
    payload = {
        "sub": "ruurdsm@queens.edu",
        "type": "magic_link",
        "jti": "test-jti",
        "iat": datetime.now(timezone.utc) - timedelta(minutes=30),
        "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    with pytest.raises(HTTPException) as exc_info:
        auth.verify_magic_token(token)
    assert exc_info.value.status_code == 401

def test_verify_magic_token_wrong_type_raises_401():
    """A session token must not be accepted as a magic link token."""
    from datetime import datetime, timedelta, timezone
    payload = {
        "sub": "ruurdsm@queens.edu",
        "type": "session",           # wrong type
        "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    with pytest.raises(HTTPException) as exc_info:
        auth.verify_magic_token(token)
    assert exc_info.value.status_code == 401


# ── Session token generation ──────────────────────────────────────────────────

def test_generate_session_token_returns_string():
    token = auth.generate_session_token("ruurdsm@queens.edu")
    assert isinstance(token, str)

def test_session_token_type_field():
    token = auth.generate_session_token("ruurdsm@queens.edu")
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["type"] == "session"

def test_session_token_has_session_id():
    token = auth.generate_session_token("ruurdsm@queens.edu")
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert "session_id" in payload

def test_session_token_subject_is_email():
    email = "thaob@queens.edu"
    token = auth.generate_session_token(email)
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["sub"] == email


# ── Session verification ──────────────────────────────────────────────────────

def test_verify_session_token_returns_dict():
    email = "ruurdsm@queens.edu"
    token = auth.generate_session_token(email)
    result = auth.verify_session_token(token)
    assert isinstance(result, dict)
    assert result["email"] == email

def test_verify_session_token_invalid_raises_401():
    with pytest.raises(HTTPException) as exc_info:
        auth.verify_session_token("garbage.token.here")
    assert exc_info.value.status_code == 401

def test_verify_session_token_magic_type_raises_401():
    """A magic-link token must not be accepted as a session token."""
    token = auth.generate_magic_token("ruurdsm@queens.edu")
    with pytest.raises(HTTPException) as exc_info:
        auth.verify_session_token(token)
    assert exc_info.value.status_code == 401


# ── Session invalidation ──────────────────────────────────────────────────────

def test_invalidate_session_removes_session():
    email = "ruurdsm@queens.edu"
    token = auth.generate_session_token(email)
    # Session is valid before invalidation
    auth.verify_session_token(token)
    # Invalidate
    auth.invalidate_session(token)
    # Now it must fail
    with pytest.raises(HTTPException) as exc_info:
        auth.verify_session_token(token)
    assert exc_info.value.status_code == 401

def test_invalidate_session_bad_token_does_not_raise():
    """Logging out with an already-invalid token must not raise."""
    auth.invalidate_session("not.a.real.token")  # should not raise


# ── Master API key ────────────────────────────────────────────────────────────

def test_require_auth_accepts_master_key():
    import asyncio
    result = asyncio.run(auth.require_auth(x_api_key=MASTER_API_KEY))
    assert result["role"] == "developer"

def test_require_auth_no_key_raises_401():
    import asyncio
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(auth.require_auth(x_api_key=None))
    assert exc_info.value.status_code == 401

def test_require_auth_accepts_valid_session():
    import asyncio
    email = "ruurdsm@queens.edu"
    session_token = auth.generate_session_token(email)
    result = asyncio.run(auth.require_auth(x_api_key=session_token))
    assert result["email"] == email
