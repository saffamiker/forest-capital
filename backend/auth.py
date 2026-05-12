"""
Magic-link authentication with JWT session tokens.

Dev mode  → magic link URL printed to terminal (no email sent).
Prod mode → magic link sent via SendGrid.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Header, status

from config import (
    SECRET_KEY,
    MASTER_API_KEY,
    MAGIC_LINK_EXPIRY_MINUTES,
    SESSION_EXPIRY_HOURS,
    ENVIRONMENT,
    FRONTEND_URL,
    ALLOWED_EMAILS,
)
from logger import get_logger

log = get_logger(__name__)

# In-memory session store — replace with Redis for production
_sessions: dict[str, dict] = {}

# Tracks redeemed magic-link JTIs so that a second presentation of the same token
# (e.g. an email security scanner pre-fetching the link) returns the existing active
# session rather than creating a new one and invalidating the user's real session.
# Maps jti → session_token that was issued on first redemption.
_used_magic_jtis: dict[str, str] = {}

ALGORITHM = "HS256"


# ── Token generation ──────────────────────────────────────────────────────────

def generate_magic_token(email: str) -> str:
    payload = {
        "sub": email,
        "type": "magic_link",
        "jti": str(uuid.uuid4()),
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=MAGIC_LINK_EXPIRY_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def generate_session_token(email: str) -> str:
    session_id = str(uuid.uuid4())
    exp = datetime.now(timezone.utc) + timedelta(hours=SESSION_EXPIRY_HOURS)
    payload = {
        "sub": email,
        "type": "session",
        "session_id": session_id,
        "iat": datetime.now(timezone.utc),
        "exp": exp,
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    _sessions[session_id] = {
        "email": email,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": exp.isoformat(),
    }
    log.info("session_created", email=email, session_id=session_id)
    return token


# ── Token verification ────────────────────────────────────────────────────────

def verify_magic_token(token: str) -> str:
    """Return email if token is valid, else raise HTTPException."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Magic link has expired. Please request a new one.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid magic link token.")

    if payload.get("type") != "magic_link":
        raise HTTPException(status_code=401, detail="Invalid token type.")
    return payload["sub"]


def redeem_magic_token(token: str) -> str:
    """
    Single-use magic-link redemption.

    On first call: validates the token, creates a session, records the jti.
    On repeat call with the same token (email security scanner pre-fetch):
      returns the existing session token if still active, so the user's real
      click is not invalidated. Raises 401 only if the existing session has
      already expired or been invalidated.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Magic link has expired. Please request a new one.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid magic link token.")

    if payload.get("type") != "magic_link":
        raise HTTPException(status_code=401, detail="Invalid token type.")

    email: str = payload["sub"]
    jti: str = payload.get("jti", "")

    if jti and jti in _used_magic_jtis:
        existing_token = _used_magic_jtis[jti]
        try:
            existing_payload = jwt.decode(existing_token, SECRET_KEY, algorithms=[ALGORITHM])
            existing_session_id = existing_payload.get("session_id", "")
            if existing_session_id in _sessions:
                log.info("magic_link_reuse_returning_existing_session",
                         email=email, jti=jti[:8],
                         note="Token already redeemed — returning existing active session")
                return existing_token
        except Exception:
            pass
        raise HTTPException(
            status_code=401,
            detail="This link has already been used. Please request a new one.",
        )

    # First redemption — issue a session and record the jti to detect future reuse
    session_token = generate_session_token(email)
    if jti:
        _used_magic_jtis[jti] = session_token
    return session_token


def verify_session_token(token: str) -> dict:
    """Return session dict if token is valid, else raise HTTPException."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid session token.")

    if payload.get("type") != "session":
        raise HTTPException(status_code=401, detail="Invalid token type.")

    session_id = payload.get("session_id", "")
    if session_id not in _sessions:
        raise HTTPException(status_code=401, detail="Session not found or already invalidated.")

    return {"email": payload["sub"], "session_id": session_id}


def invalidate_session(token: str) -> None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        session_id = payload.get("session_id", "")
        _sessions.pop(session_id, None)
    except Exception:
        pass


# ── Magic link delivery ───────────────────────────────────────────────────────

async def send_magic_link(email: str, token: str) -> None:
    magic_url = f"{FRONTEND_URL}/auth/verify?token={token}"

    if ENVIRONMENT in ("development", "test"):
        border = "=" * 64
        print(f"\n{border}")
        print(f"  [{ENVIRONMENT.upper()}] Magic link for {email}")
        print(f"  URL: {magic_url}")
        print(f"  Expires in {MAGIC_LINK_EXPIRY_MINUTES} minutes")
        print(f"{border}\n")
        log.info("magic_link_dev_printed", email=email, environment=ENVIRONMENT)
        return

    # Production: send via SendGrid
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail
        import os

        html = f"""
        <div style="font-family:sans-serif;max-width:500px;margin:0 auto">
          <h2 style="color:#0a0e1a">Forest Capital Portfolio Intelligence System</h2>
          <p>Click below to log in. This link expires in {MAGIC_LINK_EXPIRY_MINUTES} minutes.</p>
          <p>
            <a href="{magic_url}"
               style="background:#3b82f6;color:#fff;padding:12px 28px;
                      text-decoration:none;border-radius:6px;display:inline-block">
              Log In
            </a>
          </p>
          <p style="color:#666;font-size:12px">
            If you did not request this link, you can safely ignore this email.
          </p>
        </div>
        """
        sg = sendgrid.SendGridAPIClient(api_key=os.getenv("SENDGRID_API_KEY"))
        message = Mail(
            from_email=os.getenv("SENDGRID_FROM_EMAIL", "noreply@queens.edu"),
            to_emails=email,
            subject="Forest Capital — Your Login Link",
            html_content=html,
        )
        resp = sg.send(message)
        log.info("magic_link_sent", email=email, status=resp.status_code)
    except Exception as exc:
        log.error("magic_link_send_failed", email=email, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to send magic link email.")


# ── FastAPI dependencies ──────────────────────────────────────────────────────

async def require_auth(x_api_key: Optional[str] = Header(None)) -> dict:
    """Validate session token OR master API key. Apply to all protected endpoints."""
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    if x_api_key == MASTER_API_KEY:
        return {"email": "developer@forest-capital", "role": "developer"}
    return verify_session_token(x_api_key)


async def require_master_key(x_api_key: Optional[str] = Header(None)) -> dict:
    """Master API key only — developer endpoints."""
    if x_api_key != MASTER_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Developer access only.",
        )
    return {"role": "developer"}
