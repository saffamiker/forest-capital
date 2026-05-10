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

    if ENVIRONMENT == "development":
        border = "=" * 64
        print(f"\n{border}")
        print(f"  [DEV] Magic link for {email}")
        print(f"  URL: {magic_url}")
        print(f"  Expires in {MAGIC_LINK_EXPIRY_MINUTES} minutes")
        print(f"{border}\n")
        log.info("magic_link_dev_printed", email=email)
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
