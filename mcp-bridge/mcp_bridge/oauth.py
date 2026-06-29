"""mcp_bridge.oauth — minimal OAuth 2.1 shim for claude.ai connector.

claude.ai's "Add Custom Connector" UI registers a remote MCP server
via OAuth 2.1, not a static bearer header. It wants:

  1. Discovery — GET /.well-known/oauth-authorization-server (RFC 8414)
     and GET /.well-known/oauth-protected-resource (RFC 9728).
  2. Authorization — GET /authorize (the user's browser is redirected
     here to approve; we auto-approve since this is a single-user
     desktop bridge).
  3. Token — POST /token (exchanges the authorization code for an
     access token).

The access token /token issues IS the bridge's existing
cfg.auth_token. So once claude.ai completes the flow it sends
`Authorization: Bearer <auth_token>` on every /mcp call, which the
existing server.require_token validates UNCHANGED. The OAuth layer
only has to mint a code, then hand back the token in exchange — it
is a thin front door over the auth that already works.

DESIGN NOTES — single-user pragmatics:

  - NO dynamic client registration. The claude.ai UI has explicit
    Client ID / Client Secret fields, so the operator pastes the
    credentials `bridge init` generated. /token validates them
    against cfg.oauth_client_id / cfg.oauth_client_secret.

  - /authorize AUTO-APPROVES. A single-user personal bridge has no
    second user to consent on behalf of; the security boundary is
    the client secret, the issued bearer token, and the tunnel. A
    consent screen would add friction with no security gain here.

  - PKCE (S256) is supported and ENFORCED when the client sends a
    code_challenge (OAuth 2.1 requires PKCE; claude.ai sends it).
    /token verifies BASE64URL(SHA256(verifier)) == challenge.

  - Authorization codes are single-use, 10-minute-TTL, in-memory.
    The bridge is one process per desktop; an in-memory dict is the
    right store (a restart invalidates pending codes, which is
    correct — the user just re-runs the connect flow).

  - Issuer / endpoint URLs in the metadata are derived from the
    REQUEST (Host + X-Forwarded-Proto/Host), so the same code serves
    correct absolute URLs behind ngrok / cloudflared without a
    hardcoded public_url.

If cfg.oauth_client_id is empty the OAuth routes still mount but
/authorize and /token return errors directing the operator to run
`bridge init` — the discovery docs are harmless to expose.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .config import BridgeConfig


# Authorization codes live this long before they are rejected.
_CODE_TTL_SECONDS = 600
# The access token the bridge issues is long-lived (it IS the static
# auth_token, which does not rotate). Advertise a year so claude.ai
# does not churn the connect flow.
_TOKEN_TTL_SECONDS = 365 * 24 * 3600
_SCOPE = "mcp"


@dataclass
class _AuthCode:
    """One pending authorization code. Bound to the client + redirect
    + PKCE challenge it was issued against; all three are re-checked at
    /token time so a code cannot be replayed against a different
    client or redirect."""
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    created_at: float
    used: bool = False


class _AuthCodeStore:
    """In-memory single-use authorization-code store. One process per
    desktop, so a plain dict + manual TTL sweep is sufficient — no
    external store, no locking (FastAPI's default sync routes run in a
    threadpool, but each code is touched exactly twice: issue, then
    redeem, and redeem flips `used` under the GIL-atomic dict op)."""

    def __init__(self) -> None:
        self._codes: dict[str, _AuthCode] = {}

    def issue(
        self, *, client_id: str, redirect_uri: str,
        code_challenge: str, code_challenge_method: str,
    ) -> str:
        self._sweep()
        code = secrets.token_urlsafe(32)
        self._codes[code] = _AuthCode(
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            created_at=time.time(),
        )
        return code

    def redeem(
        self, code: str, *, client_id: str, redirect_uri: str,
        code_verifier: str | None,
    ) -> tuple[bool, str]:
        """Validate + consume a code. Returns (ok, error_message).
        On success the code is marked used so a replay fails."""
        entry = self._codes.get(code)
        if entry is None:
            return False, "invalid_grant: unknown or expired code"
        if entry.used:
            return False, "invalid_grant: code already redeemed"
        if (time.time() - entry.created_at) > _CODE_TTL_SECONDS:
            return False, "invalid_grant: code expired"
        if entry.client_id != client_id:
            return False, "invalid_grant: client_id mismatch"
        if entry.redirect_uri != redirect_uri:
            return False, "invalid_grant: redirect_uri mismatch"
        # PKCE — enforced whenever a challenge was recorded at
        # /authorize time. OAuth 2.1 + claude.ai always send one.
        if entry.code_challenge:
            if not code_verifier:
                return False, "invalid_grant: code_verifier required"
            if not _verify_pkce(
                    code_verifier, entry.code_challenge,
                    entry.code_challenge_method):
                return False, "invalid_grant: PKCE verification failed"
        entry.used = True
        return True, ""

    def _sweep(self) -> None:
        """Drop expired / used codes so the dict cannot grow unbounded
        on a long-lived server."""
        now = time.time()
        stale = [
            c for c, e in self._codes.items()
            if e.used or (now - e.created_at) > _CODE_TTL_SECONDS
        ]
        for c in stale:
            self._codes.pop(c, None)


_TOKENS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    access_token TEXT PRIMARY KEY,
    client_id    TEXT,
    issued_at    INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tokens_expires_at
    ON tokens(expires_at);
"""


class TokenStore:
    """SQLite-backed issued-access-token store.

    Why persistent: claude.ai stores the bearer token it received from
    /token and re-uses it on every /mcp call. If the bridge keeps the
    issued tokens in-memory only, a restart invalidates every prior
    token and claude.ai is forced through the full authorization-code
    flow again — visible to the user as a "please re-connect" prompt
    on every desktop reboot. Persisting the tokens to the existing
    queue.db SQLite file makes the bearer survive a restart, which is
    the whole point of an access token in OAuth.

    Schema (one row per issued token):
      access_token TEXT PRIMARY KEY  the bearer claude.ai sends
      client_id    TEXT              the OAuth client that earned it
      issued_at    INTEGER           epoch seconds (debug + audit)
      expires_at   INTEGER           epoch seconds (the validity cap)

    Concurrency: shares queue.db with the prompt queue, which already
    runs WAL + synchronous=NORMAL. The Queue constructor sets those
    pragmas on first connect, so by the time the TokenStore creates
    its table the pragmas are already in effect — there is nothing
    extra to configure here.

    Cleanup: validate() opportunistically deletes the row when it
    detects an expired token. There is no separate sweeper thread —
    the queue is small (one row per connect flow) and rows that are
    never re-validated stay until the next process restart triggers
    a sweep_expired() at startup. That is the load-existing-tokens
    step from the spec: it is implemented as a sweep + nothing else
    because validation reads SQLite on every request, so a separate
    in-memory cache is not needed.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        # Create the parent directory if it doesn't exist (mirrors the
        # Queue class — keeps the store usable without external setup).
        p = Path(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as c:
            c.executescript(_TOKENS_SCHEMA)
        # Drop any tokens that expired before this process started.
        self.sweep_expired()

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None — same convention as the Queue. Each
        # statement is its own transaction unless we BEGIN explicitly.
        c = sqlite3.connect(self.db_path, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    def issue(
        self, *, client_id: str, ttl_seconds: int,
    ) -> tuple[str, int]:
        """Mint a fresh random access token and persist it.

        Returns (access_token, expires_in_seconds). The token is 32
        bytes of secrets.token_urlsafe, which is the same entropy class
        as authorization codes — collisions are not a concern at the
        single-user scale this bridge runs at.
        """
        now = int(time.time())
        expires_at = now + int(ttl_seconds)
        token = secrets.token_urlsafe(32)
        with self._connect() as c:
            c.execute(
                "INSERT INTO tokens "
                "(access_token, client_id, issued_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (token, client_id, now, expires_at))
        return token, ttl_seconds

    def validate(self, token: str) -> bool:
        """True iff `token` is in the table and has not expired.

        An expired row is deleted on detection so the table stays
        small without a separate sweeper. A non-existent row is a
        plain False — no exception, no logging (validation is a hot
        path and the unauthenticated 401 carries no token detail to
        Claude.ai anyway)."""
        if not token:
            return False
        now = int(time.time())
        with self._connect() as c:
            row = c.execute(
                "SELECT expires_at FROM tokens "
                "WHERE access_token = ?",
                (token,)).fetchone()
            if row is None:
                return False
            if int(row["expires_at"]) <= now:
                # Stale — clean up and reject.
                c.execute(
                    "DELETE FROM tokens WHERE access_token = ?",
                    (token,))
                return False
        return True

    def sweep_expired(self) -> int:
        """DELETE every row whose expires_at is in the past. Returns
        the number of rows removed. Called from __init__ as the
        load-existing-tokens step from the spec: there's nothing to
        load into memory (validate() hits SQLite directly), but a
        startup sweep keeps the table bounded across long-running
        restarts. Idempotent."""
        now = int(time.time())
        with self._connect() as c:
            cur = c.execute(
                "DELETE FROM tokens WHERE expires_at <= ?", (now,))
            return int(cur.rowcount or 0)


def _verify_pkce(
    verifier: str, challenge: str, method: str,
) -> bool:
    """OAuth 2.1 PKCE check. S256 is the only method claude.ai uses
    (and the only one OAuth 2.1 permits for public clients); 'plain'
    is accepted defensively for completeness."""
    if method == "plain":
        return secrets.compare_digest(verifier, challenge)
    # S256: challenge == BASE64URL-NOPAD( SHA256( verifier ) )
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(computed, challenge)


def _base_url(request: Request) -> str:
    """Absolute base URL for THIS deployment, honouring a tunnel's
    forwarded headers. ngrok / cloudflared set X-Forwarded-Proto and
    X-Forwarded-Host; without them we fall back to the request URL.
    Trailing slash stripped so callers can append '/authorize' etc."""
    proto = request.headers.get("x-forwarded-proto")
    host = request.headers.get("x-forwarded-host")
    if proto and host:
        return f"{proto}://{host}".rstrip("/")
    # request.base_url already accounts for the mounted root path.
    return str(request.base_url).rstrip("/")


def register_oauth_routes(app: FastAPI, cfg: BridgeConfig) -> None:
    """Mounts the OAuth 2.1 endpoints on `app`. Idempotent per app —
    called once from create_app. The discovery routes are always
    safe to expose; /authorize and /token error cleanly when no
    client credentials are configured.

    Two stores: the authorization-CODE store is in-memory (codes are
    short-lived single-use intermediates the user re-issues if
    they're lost), and the TOKEN store is SQLite-backed (issued
    bearers must outlive a bridge restart or Claude.ai's connector
    re-authenticates every time the bridge bounces)."""
    store = _AuthCodeStore()
    app.state.oauth_code_store = store
    token_store = TokenStore(cfg.db_path)
    app.state.oauth_token_store = token_store

    # ── Discovery ───────────────────────────────────────────────────────

    @app.get("/.well-known/oauth-authorization-server")
    def as_metadata(request: Request) -> JSONResponse:
        """RFC 8414 Authorization Server Metadata. claude.ai reads
        this to find the authorize + token endpoints and the
        supported PKCE methods."""
        base = _base_url(request)
        return JSONResponse({
            "issuer": base,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": [
                "client_secret_post", "client_secret_basic",
            ],
            "scopes_supported": [_SCOPE],
        })

    @app.get("/.well-known/oauth-protected-resource")
    def pr_metadata(request: Request) -> JSONResponse:
        """RFC 9728 Protected Resource Metadata. Points claude.ai at
        the authorization server that guards this MCP resource."""
        base = _base_url(request)
        return JSONResponse({
            "resource": base,
            "authorization_servers": [base],
            "scopes_supported": [_SCOPE],
            "bearer_methods_supported": ["header"],
        })

    # ── Authorization endpoint ──────────────────────────────────────────

    @app.get("/authorize", response_model=None)
    def authorize(request: Request) -> RedirectResponse | JSONResponse:
        """OAuth 2.1 authorization endpoint. claude.ai redirects the
        operator's browser here. Single-user bridge: AUTO-APPROVE —
        validate the request, mint a code bound to the client +
        redirect + PKCE challenge, and 302 back to redirect_uri with
        code + state. No consent screen (see module docstring)."""
        q = request.query_params
        client_id = q.get("client_id", "")
        redirect_uri = q.get("redirect_uri", "")
        response_type = q.get("response_type", "")
        state = q.get("state", "")
        code_challenge = q.get("code_challenge", "")
        code_challenge_method = q.get("code_challenge_method", "S256")

        if not cfg.oauth_client_id:
            return JSONResponse(
                status_code=503,
                content={
                    "error": "temporarily_unavailable",
                    "error_description": (
                        "OAuth is not configured. Run `bridge init` "
                        "to generate client credentials."),
                })
        # Validate before redirecting anywhere. A bad client_id or a
        # missing redirect_uri must NOT cause an open redirect, so we
        # return a JSON error rather than bouncing to an attacker
        # redirect.
        if not redirect_uri:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_request",
                         "error_description": "redirect_uri required"})
        if client_id != cfg.oauth_client_id:
            return JSONResponse(
                status_code=400,
                content={"error": "unauthorized_client",
                         "error_description": "unknown client_id"})
        if response_type != "code":
            # Per spec, errors AFTER client validation redirect back.
            return _redirect_error(
                redirect_uri, state, "unsupported_response_type",
                "only response_type=code is supported")
        if code_challenge_method not in ("S256", "plain"):
            return _redirect_error(
                redirect_uri, state, "invalid_request",
                "code_challenge_method must be S256")

        code = store.issue(
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
        )
        params = {"code": code}
        if state:
            params["state"] = state
        sep = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(
            url=f"{redirect_uri}{sep}{urlencode(params)}",
            status_code=302)

    # ── Token endpoint ──────────────────────────────────────────────────

    @app.post("/token")
    async def token(request: Request) -> JSONResponse:
        """OAuth 2.1 token endpoint. Exchanges an authorization code
        for the access token (which IS cfg.auth_token). Validates the
        client credentials (POST body or HTTP Basic), the code, the
        redirect_uri, and the PKCE verifier."""
        if not cfg.oauth_client_id or not cfg.auth_token:
            return _token_error(
                "invalid_client",
                "OAuth is not configured. Run `bridge init`.", 503)

        form = await request.form()
        grant_type = str(form.get("grant_type", ""))
        code = str(form.get("code", ""))
        redirect_uri = str(form.get("redirect_uri", ""))
        code_verifier = form.get("code_verifier")
        code_verifier = str(code_verifier) if code_verifier else None

        # Client auth — accept client_secret_post (form fields) or
        # client_secret_basic (Authorization: Basic). Either is fine
        # per the advertised token_endpoint_auth_methods_supported.
        client_id, client_secret = _client_credentials(request, form)

        if grant_type != "authorization_code":
            return _token_error(
                "unsupported_grant_type",
                "only authorization_code is supported")
        if client_id != cfg.oauth_client_id or \
                not secrets.compare_digest(
                    client_secret or "", cfg.oauth_client_secret):
            return _token_error(
                "invalid_client", "client authentication failed", 401)

        ok, err = store.redeem(
            code, client_id=client_id, redirect_uri=redirect_uri,
            code_verifier=code_verifier)
        if not ok:
            return _token_error("invalid_grant", err)

        # Mint a fresh random access token and persist it. Each
        # successful authorization-code exchange gets its own token —
        # Claude.ai stores what we return here and replays it on every
        # /mcp call. The TokenStore persists to queue.db so the token
        # survives a bridge restart (the whole point of this layer).
        access_token, expires_in = token_store.issue(
            client_id=client_id, ttl_seconds=_TOKEN_TTL_SECONDS)
        return JSONResponse({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "scope": _SCOPE,
        })


def _client_credentials(
    request: Request, form,
) -> tuple[str, str]:
    """Extract (client_id, client_secret) from either HTTP Basic
    (client_secret_basic) or the form body (client_secret_post).
    Basic takes precedence when present."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        try:
            raw = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
            cid, _, secret = raw.partition(":")
            return cid, secret
        except Exception:  # noqa: BLE001
            return "", ""
    return str(form.get("client_id", "")), str(form.get("client_secret", ""))


def _redirect_error(
    redirect_uri: str, state: str, error: str, desc: str,
) -> RedirectResponse:
    """OAuth error returned by redirecting back to the client (used
    only AFTER the client + redirect_uri are validated)."""
    params = {"error": error, "error_description": desc}
    if state:
        params["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        url=f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)


def _token_error(
    error: str, desc: str, status: int = 400,
) -> JSONResponse:
    """RFC 6749 §5.2 token error response."""
    return JSONResponse(
        status_code=status,
        content={"error": error, "error_description": desc})
