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
    PROJECT_TEAM_EMAILS,
    PERMISSIONS,
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


def generate_session_token(
    email: str,
    *,
    role: str | None = None,
    display_name: str | None = None,
    permissions: list[str] | None = None,
) -> str:
    """
    Mints a session JWT. When role / display_name / permissions are
    supplied (the magic-link login path, which looks them up from
    platform_users) they are embedded in the token, so require_auth
    needs no per-request database hit. A token minted without them
    (an old token, or a test fixture) is resolved by require_auth at
    verify time instead.
    """
    session_id = str(uuid.uuid4())
    exp = datetime.now(timezone.utc) + timedelta(hours=SESSION_EXPIRY_HOURS)
    payload = {
        "sub": email,
        "type": "session",
        "session_id": session_id,
        "iat": datetime.now(timezone.utc),
        "exp": exp,
    }
    if role is not None:
        payload["role"] = role
    if display_name is not None:
        payload["display_name"] = display_name
    if permissions is not None:
        payload["permissions"] = permissions
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


def redeem_magic_token(token: str, user_attrs: dict | None = None) -> str:
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

    # First redemption — issue a session and record the jti to detect future
    # reuse. user_attrs (role / display_name / permissions, looked up from
    # platform_users by the caller) is embedded in the session JWT.
    session_token = generate_session_token(email, **(user_attrs or {}))
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

    # role / display_name / permissions are present on tokens minted by the
    # post-migration login path; absent on older or test-minted tokens, in
    # which case require_auth resolves them.
    return {
        "email": payload["sub"],
        "session_id": session_id,
        "role": payload.get("role"),
        "display_name": payload.get("display_name"),
        "permissions": payload.get("permissions"),
    }


def invalidate_session(token: str) -> None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        session_id = payload.get("session_id", "")
        _sessions.pop(session_id, None)
    except Exception as exc:
        # A malformed/expired token on logout is harmless (nothing to
        # invalidate) — but log it so a real decode regression is visible.
        log.debug("invalidate_session_decode_failed", error=str(exc))


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
    """
    Validate a session token OR the master API key. Apply to all
    protected endpoints. Returns a session dict that always carries
    `permissions` — the authoritative capability list.

    Permission resolution is three-tier:
      1. The JWT — tokens minted by the post-migration login path embed
         role / display_name / permissions; no database hit.
      2. platform_users — for a token that did not embed them (an older
         or test-minted token), require_auth looks the user up.
      3. The config allowlists — if platform_users is unreachable, the
         lookup falls back to PROJECT_TEAM_EMAILS / ALLOWED_EMAILS, so a
         database outage never locks the team out.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    # The master API key — the developer/CLI credential — holds every
    # permission and bypasses the database entirely.
    if x_api_key == MASTER_API_KEY:
        return {
            "email": "developer@forest-capital",
            "role": "developer",
            "display_name": "Developer",
            "permissions": list(PERMISSIONS.keys()),
        }
    session = verify_session_token(x_api_key)
    if session.get("permissions") is None:
        # Tier 2/3 — the JWT did not carry permissions; resolve them now.
        from tools.platform_users import resolve_user
        resolved = await resolve_user(session["email"])
        session["role"] = resolved["role"]
        session["display_name"] = resolved["display_name"]
        session["permissions"] = resolved["permissions"]
    return session


async def require_team_member(session: dict = Depends(require_auth)) -> dict:
    """
    Extends require_auth with the project-team check — the platform's
    second access tier. Any authenticated user may explore the analytics
    and ask the council; the action features (document upload, the
    export endpoints, Academic Review, the test runner) are restricted
    to PROJECT_TEAM_EMAILS.

    The master API key (role "developer") is Michael's CLI key and
    bypasses the check — it is the most privileged credential. A non-team
    authenticated user gets 403.
    """
    if session.get("role") == "developer":
        return session
    email = (session.get("email") or "").strip().lower()
    if email not in {e.lower() for e in PROJECT_TEAM_EMAILS}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action is restricted to the project team.",
        )
    return session


async def require_master_key(x_api_key: Optional[str] = Header(None)) -> dict:
    """Master API key only — developer endpoints."""
    if x_api_key != MASTER_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Developer access only.",
        )
    return {"role": "developer"}
