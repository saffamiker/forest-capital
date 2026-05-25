"""
Magic-link authentication with JWT session tokens.

Dev mode  → magic link URL printed to terminal (no email sent).
Prod mode → magic link sent via SendGrid.
"""
from __future__ import annotations
import os
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
    PLATFORM_URL,
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

# PLATFORM_URL (imported from config) is the single source of truth for
# the logo asset URLs the email embeds. May 27 2026 rebrand: the
# platform now lives at analyticsdesk.app (Vercel-hosted), with
# frontend/public/assets/logos/{mccoll.jpeg,queens.png} served at
# /assets/logos/. Override via the PLATFORM_URL env var if a staging
# deploy needs different logo URLs in its outbound emails.

MAGIC_LINK_SUBJECT = "Your Analytics Desk login link"


def build_magic_link_email(
    magic_url: str,
    expiry_minutes: int = MAGIC_LINK_EXPIRY_MINUTES,
    *,
    platform_url: str = PLATFORM_URL,
) -> tuple[str, str]:
    """
    Builds the (subject, html_body) of the magic-link email. Pure — no
    I/O — so it is unit-testable. Mirrors build_welcome_email's shape.

    EMAIL-SAFE HTML conventions enforced here:
      - Inline styles only. <style> blocks are stripped by Gmail and
        the Outlook desktop clients.
      - Logos are embedded via hosted https:// URLs (mccoll.jpeg,
        queens.png on the Vercel public-assets path), NOT CID
        attachments. The user spec was explicit: CID renders
        inconsistently across clients (Outlook in particular often
        shows a broken-image icon for CID attachments).
      - Logos carry explicit width / height attributes — several
        clients ignore CSS dimensions on <img> and would otherwise
        render at full intrinsic size.
      - The button is a coloured <a> with padding (Outlook strips
        many <button> styles; a styled anchor is the standard
        email-client-safe approach).
    """
    logo_base = f"{platform_url}/assets/logos"
    html = (
        f'<div style="font-family:-apple-system,BlinkMacSystemFont,'
        f"'Segoe UI',Roboto,sans-serif;max-width:560px;margin:0 auto;"
        f'padding:32px 24px;color:#0a0e1a;background:#ffffff">'
        # ── Header: two institutional logos + headline ────────────
        f'<div style="text-align:center;margin-bottom:24px">'
        f'<div style="margin-bottom:16px">'
        f'<img src="{logo_base}/mccoll.jpeg" alt="McColl School of Business"'
        f' width="120" height="auto" style="height:auto;max-height:56px;'
        f'vertical-align:middle;margin:0 12px" />'
        f'<img src="{logo_base}/queens.png" alt="Queens University of Charlotte"'
        f' width="120" height="auto" style="height:auto;max-height:56px;'
        f'vertical-align:middle;margin:0 12px" />'
        f'</div>'
        f'<h1 style="margin:8px 0 4px 0;font-size:20px;font-weight:600;'
        f'color:#0a0e1a">Portfolio Intelligence System</h1>'
        f'<p style="margin:0;font-size:13px;color:#64748b">'
        f'McColl School of Business · Queens University of Charlotte</p>'
        f'</div>'
        # ── Body copy + call-to-action button ─────────────────────
        f'<p style="font-size:15px;line-height:1.5;margin:24px 0 16px 0">'
        f'Click below to log in. This link expires in {expiry_minutes} minutes.'
        f'</p>'
        f'<p style="text-align:center;margin:24px 0">'
        f'<a href="{magic_url}" '
        f'style="background:#3b82f6;color:#ffffff;padding:14px 32px;'
        f'text-decoration:none;border-radius:6px;display:inline-block;'
        f'font-weight:500;font-size:15px">Log In</a>'
        f'</p>'
        f'<p style="color:#64748b;font-size:12px;line-height:1.5;'
        f'margin:24px 0 0 0">'
        f'If you did not request this link, you can safely ignore this email.'
        f'</p>'
        # ── Footer ────────────────────────────────────────────────
        f'<hr style="border:none;border-top:1px solid #e5e7eb;'
        f'margin:32px 0 16px 0" />'
        f'<p style="text-align:center;color:#94a3b8;font-size:11px;'
        f'margin:0">MSFA FNA 670 · '
        f'<a href="{platform_url}" style="color:#94a3b8;'
        f'text-decoration:none">analyticsdesk.app</a></p>'
        f'</div>'
    )
    return MAGIC_LINK_SUBJECT, html


def _sendgrid_error_detail(exc: BaseException) -> dict[str, object]:
    """Extracts the full SendGrid error detail from an exception so the
    caller can spread it into a structlog event. SendGrid raises
    python_http_client.exceptions.HTTPError (and subclasses like
    UnauthorizedError, ForbiddenError) — each carries:

      .status_code  — HTTP status integer
      .body         — the raw response body bytes; SendGrid's API
                      returns JSON with an `errors` array describing
                      WHY the call failed (e.g. invalid API key,
                      unverified sender, suspended account). The body
                      is the single most useful field for triage and
                      was previously lost — str(exc) renders only
                      'HTTP Error 401: Unauthorized' or similar.
      .headers      — response headers (dict-like); usually safe to
                      log but capped at a small size to prevent floods.

    Defensive on every field: an exception that is NOT an HTTPError
    (e.g. a connection error, a TypeError raised before the HTTP
    round-trip) returns only error_type + error_message — never raises.

    Body and headers are truncated to a sensible cap (2000 chars body,
    50 header entries) so a misbehaving SendGrid response cannot flood
    the log stream.
    """
    detail: dict[str, object] = {
        "error_type": type(exc).__name__,
        "error_message": str(exc),
    }

    def _safe_attr(name):
        """getattr through a try/except — a malformed exception with
        a @property that raises on access (or any other descriptor
        side-effect) must NOT poison the log helper."""
        try:
            return getattr(exc, name, None)
        except Exception:  # noqa: BLE001
            return None

    # SendGrid's HTTPError carries .status_code / .body / .headers.
    status = _safe_attr("status_code")
    if status is not None:
        detail["sendgrid_status"] = status
    raw_body = _safe_attr("body")
    if raw_body is not None:
        # body may be bytes or str depending on SDK version. Decode
        # bytes leniently so we never raise on an undecodable byte.
        if isinstance(raw_body, bytes):
            body_text = raw_body.decode("utf-8", errors="replace")
        else:
            body_text = str(raw_body)
        if len(body_text) > 2000:
            body_text = body_text[:2000] + "…(truncated)"
        detail["sendgrid_body"] = body_text
        # Best-effort parse — SendGrid returns JSON with an `errors`
        # array. Surface the structured field separately so a log
        # filter can pivot on it without parsing the body string.
        try:
            import json as _json
            parsed = _json.loads(body_text)
            if isinstance(parsed, dict) and "errors" in parsed:
                detail["sendgrid_errors"] = parsed["errors"]
        except Exception:  # noqa: BLE001
            pass  # body wasn't JSON or wasn't the expected shape
    raw_headers = _safe_attr("headers")
    if raw_headers is not None:
        try:
            # Cap at 50 entries — the canonical SendGrid response
            # carries ~10-15 headers, so 50 is generous but bounded.
            items = list(raw_headers.items())[:50]
            detail["sendgrid_headers"] = {str(k): str(v) for k, v in items}
        except Exception:  # noqa: BLE001
            pass
    return detail


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

        subject, html = build_magic_link_email(magic_url)
        sg = sendgrid.SendGridAPIClient(api_key=os.getenv("SENDGRID_API_KEY"))
        message = Mail(
            from_email=os.getenv("SENDGRID_FROM_EMAIL", "noreply@queens.edu"),
            to_emails=email,
            subject=subject,
            html_content=html,
        )
        resp = sg.send(message)
        log.info("magic_link_sent", email=email, status=resp.status_code)
    except Exception as exc:
        # Surface the full SendGrid response — body / status / errors
        # array — so a 401 / 403 from SendGrid can be triaged from the
        # Render log alone (invalid API key, unverified sender,
        # suspended account each return a different errors[] body).
        log.error("magic_link_send_failed",
                  email=email,
                  error=str(exc),
                  **_sendgrid_error_detail(exc))
        raise HTTPException(status_code=500, detail="Failed to send magic link email.")


# ── Welcome email (sent on user creation) ─────────────────────────────────────

WELCOME_EMAIL_SUBJECT = (
    "You've been granted access to the Forest Capital "
    "Portfolio Intelligence System"
)


def build_welcome_email(
    email: str,
    display_name: str | None = None,
    notes: str | None = None,
    council_limit: int | None = None,
) -> tuple[str, str]:
    """
    Builds the (subject, plain-text body) of the welcome email sent to a
    newly-created platform user. Pure — no I/O — so it is unit-testable.
    The greeting falls back to the email when no display name was given;
    a notes line is added only when Michael recorded notes on the user.
    council_limit, when set (a viewer's finite allocation), adds a council
    query-allowance line; None (an unlimited user) adds nothing.
    """
    greeting = display_name or email
    notes_line = (f"\nYou have been added as: {notes}\n" if notes else "")
    # The raw email address stays plain text — mailto: links do not work
    # in a plain-text email; the platform UI applies the anchor instead.
    council_alloc = ""
    if council_limit is not None:
        council_alloc = (
            f"\n  You have been provisioned with {council_limit} council "
            f"queries. If you would like\n  additional access, please "
            f"contact Michael Ruurds at ruurdsm@queens.edu."
        )
    rule = "-" * 60
    body = f"""Dear {greeting},

Michael Ruurds has granted you access to the Forest Capital Portfolio
Intelligence System, a platform developed by Group 1 for the FNA 670
Industry Practicum at the McColl School of Business, Queens University
Charlotte.

Group 1: Michael Ruurds, Bob Thao, and Molly Murdock.
{notes_line}
{rule}

ACCESSING THE PLATFORM

Platform URL: {PLATFORM_URL}

This platform uses magic link authentication — there is no password to
remember.

To log in:
  1. Visit {PLATFORM_URL}
  2. Enter your registered email address: {email}
  3. Check your inbox for a magic link email
  4. Click the link to be logged in instantly

Important: you must use {email} to log in. Magic links expire after
{MAGIC_LINK_EXPIRY_MINUTES} minutes, so use them promptly.

{rule}

WHAT YOU CAN EXPLORE

Dashboard
  Ten asset allocation strategies evaluated across a 23-year dataset
  (2002-2026), with Sharpe ratios, CAGR, drawdown, and tier rankings.

Analytics
  Rolling correlations, regime-conditional performance, Carhart
  four-factor loadings, sensitivity analysis, and the efficient frontier.

AI Investment Council
  A council of seven AI agents deliberates on portfolio questions in
  real time. Ask any question about the strategies, data, or methodology.{council_alloc}

Statistical Evidence
  Walk-forward validation, combinatorial purged cross-validation (CPCV),
  probabilistic Sharpe ratios, and significance testing.

Regime Analysis
  Analysis of the 2022 equity-bond correlation regime break and its
  implications for portfolio construction.

Quality Assurance
  A 39-point methodology audit and a three-layer independent statistical
  audit — every metric independently verified.

{rule}

If you have any questions or encounter any issues, please contact
Michael Ruurds at ruurdsm@queens.edu.

We hope you find the platform useful and welcome your feedback.

Michael Ruurds
Group 1, FNA 670
McColl School of Business
Queens University Charlotte
"""
    return WELCOME_EMAIL_SUBJECT, body


async def send_welcome_email(
    email: str,
    display_name: str | None = None,
    notes: str | None = None,
    council_limit: int | None = None,
) -> bool:
    """
    Sends the welcome email to a newly-created platform user. Fail-open:
    returns True when the email was sent (or printed, in dev/test), False
    on any failure — never raises, so a delivery problem cannot block the
    user-creation response.
    """
    subject, body = build_welcome_email(
        email, display_name, notes, council_limit)

    if ENVIRONMENT in ("development", "test"):
        border = "=" * 64
        print(f"\n{border}")
        print(f"  [{ENVIRONMENT.upper()}] Welcome email for {email}")
        print(f"  Subject: {subject}")
        print(f"{border}\n")
        log.info("welcome_email_dev_printed", email=email,
                 environment=ENVIRONMENT)
        return True

    try:
        import os

        import sendgrid
        from sendgrid.helpers.mail import Mail

        sg = sendgrid.SendGridAPIClient(api_key=os.getenv("SENDGRID_API_KEY"))
        message = Mail(
            from_email=os.getenv("SENDGRID_FROM_EMAIL", "noreply@queens.edu"),
            to_emails=email,
            subject=subject,
            plain_text_content=body,
        )
        resp = sg.send(message)
        log.info("welcome_email_sent", email=email, status=resp.status_code)
        return True
    except Exception as exc:  # noqa: BLE001
        # Fail-open — the user is already created; a delivery failure is
        # logged for the operator but never raised into the response.
        # Same SendGrid-detail enrichment as the magic-link send path
        # so a 401 / 403 here is debuggable from the Render log alone.
        log.error("welcome_email_send_failed",
                  email=email,
                  error=str(exc),
                  **_sendgrid_error_detail(exc))
        return False


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


def require_permission(permission: str):
    """
    A dependency factory — returns a FastAPI dependency that admits only
    a user whose authoritative `permissions` array contains `permission`,
    and 403s everyone else. Permissions come from the session resolved by
    require_auth (the JWT, or the platform_users / config fallback). The
    master API key holds every permission.

    Endpoint → permission map: document upload/delete, Academic Review
    and the testing endpoints require "team_member"; the three
    document-generation endpoints require "generate_documents"; the
    academic-package export requires "export_package"; the failure
    reports / feedback backlog require "view_admin"; user management
    requires "manage_users".
    """
    async def _dependency(session: dict = Depends(require_auth)) -> dict:
        if permission not in (session.get("permissions") or []):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This action is restricted to the project team.",
            )
        return session
    return _dependency


# require_team_member is the "team_member" permission check — kept as a
# named dependency so existing imports and call sites need no change.
require_team_member = require_permission("team_member")

# require_sysadmin gates the QA-run and statistical-audit triggers.
# "sysadmin" is a role, not a permission — "manage_users" is the
# permission only the sysadmin role's preset carries, so it is the
# effective sysadmin-only gate (the same one user management uses).
require_sysadmin = require_permission("manage_users")


async def require_master_key(x_api_key: Optional[str] = Header(None)) -> dict:
    """Master API key only — developer endpoints."""
    if x_api_key != MASTER_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Developer access only.",
        )
    return {"role": "developer"}
