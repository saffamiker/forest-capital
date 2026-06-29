"""OAuth 2.1 shim tests — the claude.ai connector handshake.

Covers the full authorization-code + PKCE flow end to end plus every
failure path:
  - discovery metadata (AS + protected-resource) shape
  - 401 on /mcp carries the WWW-Authenticate resource_metadata pointer
  - /authorize validates client_id, redirect_uri, response_type, PKCE
  - /authorize auto-approves and redirects with code + state
  - /token exchanges the code for the bridge's auth_token
  - PKCE S256 verification (correct verifier passes, wrong fails)
  - client-secret validation (post + basic), code replay, expiry
  - the issued token actually authenticates a subsequent /mcp call
"""
from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient


def _client(cfg):
    from mcp_bridge.server import create_app
    # follow_redirects=False so we can assert on the 302 from /authorize.
    return TestClient(create_app(cfg), follow_redirects=False)


def _pkce_pair() -> tuple[str, str]:
    """Return (verifier, S256 challenge)."""
    verifier = "test-verifier-0123456789-abcdefghij-KLMNOPQRST"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


REDIRECT = "https://claude.ai/api/mcp/callback"


# ── Discovery ───────────────────────────────────────────────────────────────


def test_authorization_server_metadata(cfg):
    r = _client(cfg).get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    m = r.json()
    assert m["issuer"]
    assert m["authorization_endpoint"].endswith("/authorize")
    assert m["token_endpoint"].endswith("/token")
    assert m["response_types_supported"] == ["code"]
    assert "authorization_code" in m["grant_types_supported"]
    assert "S256" in m["code_challenge_methods_supported"]


def test_protected_resource_metadata(cfg):
    r = _client(cfg).get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200
    m = r.json()
    assert m["resource"]
    assert isinstance(m["authorization_servers"], list)
    assert m["authorization_servers"]
    assert m["bearer_methods_supported"] == ["header"]


def test_discovery_open_without_auth(cfg):
    # Discovery must work before the client has any token.
    c = _client(cfg)
    assert c.get("/.well-known/oauth-authorization-server"
                 ).status_code == 200
    assert c.get("/.well-known/oauth-protected-resource"
                 ).status_code == 200


def test_401_carries_resource_metadata_pointer(cfg):
    r = _client(cfg).post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "status", "params": {}})
    assert r.status_code == 401
    www = r.headers.get("www-authenticate", "")
    assert "resource_metadata=" in www
    assert "/.well-known/oauth-protected-resource" in www


# ── /authorize ──────────────────────────────────────────────────────────────


def _authorize(client, cfg, **overrides):
    _, challenge = _pkce_pair()
    params = {
        "response_type": "code",
        "client_id": cfg.oauth_client_id,
        "redirect_uri": REDIRECT,
        "state": "xyz-state",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    params.update(overrides)
    return client.get("/authorize", params=params)


def test_authorize_redirects_with_code_and_state(cfg):
    r = _authorize(_client(cfg), cfg)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith(REDIRECT)
    q = parse_qs(urlparse(loc).query)
    assert q["state"] == ["xyz-state"]
    assert q["code"] and len(q["code"][0]) > 20


def test_authorize_rejects_unknown_client(cfg):
    r = _authorize(_client(cfg), cfg, client_id="not-the-client")
    assert r.status_code == 400
    assert r.json()["error"] == "unauthorized_client"


def test_authorize_requires_redirect_uri(cfg):
    r = _authorize(_client(cfg), cfg, redirect_uri="")
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"


def test_authorize_rejects_non_code_response_type(cfg):
    # After client validation, errors redirect BACK to the client.
    r = _authorize(_client(cfg), cfg, response_type="token")
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["error"] == ["unsupported_response_type"]


def test_authorize_503_when_oauth_unconfigured(cfg):
    cfg.oauth_client_id = ""
    r = _authorize(_client(cfg), cfg, client_id="anything")
    assert r.status_code == 503


# ── /token ──────────────────────────────────────────────────────────────────


def _get_code(client, cfg, challenge):
    r = _authorize(client, cfg, code_challenge=challenge)
    return parse_qs(urlparse(r.headers["location"]).query)["code"][0]


def test_token_exchange_returns_fresh_unique_access_token(cfg):
    """The /token endpoint mints a fresh random access token on every
    successful exchange — NOT the static cfg.auth_token. Each issued
    bearer is persisted to the tokens table so it survives a bridge
    restart (the whole point of TokenStore)."""
    client = _client(cfg)
    verifier, challenge = _pkce_pair()
    code = _get_code(client, cfg, challenge)
    r = client.post("/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "client_id": cfg.oauth_client_id,
        "client_secret": cfg.oauth_client_secret,
        "code_verifier": verifier,
    })
    assert r.status_code == 200
    body = r.json()
    # The access token is freshly minted — distinct from the static
    # admin/CLI cfg.auth_token, never empty, and long enough to be a
    # real secret (secrets.token_urlsafe(32) → ~43 chars).
    assert body["access_token"]
    assert body["access_token"] != cfg.auth_token
    assert len(body["access_token"]) > 20
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] > 0


def test_issued_token_authenticates_mcp(cfg):
    # End-to-end: run the flow, then use the token on /mcp.
    client = _client(cfg)
    verifier, challenge = _pkce_pair()
    code = _get_code(client, cfg, challenge)
    tok = client.post("/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": REDIRECT, "client_id": cfg.oauth_client_id,
        "client_secret": cfg.oauth_client_secret,
        "code_verifier": verifier,
    }).json()["access_token"]
    r = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "status",
              "params": {}},
        headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["result"]["alive"] is True


def test_token_rejects_bad_client_secret(cfg):
    client = _client(cfg)
    verifier, challenge = _pkce_pair()
    code = _get_code(client, cfg, challenge)
    r = client.post("/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": REDIRECT, "client_id": cfg.oauth_client_id,
        "client_secret": "wrong-secret", "code_verifier": verifier,
    })
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_client"


def test_token_rejects_wrong_pkce_verifier(cfg):
    client = _client(cfg)
    _, challenge = _pkce_pair()
    code = _get_code(client, cfg, challenge)
    r = client.post("/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": REDIRECT, "client_id": cfg.oauth_client_id,
        "client_secret": cfg.oauth_client_secret,
        "code_verifier": "this-is-not-the-right-verifier-at-all",
    })
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_token_client_secret_basic_auth(cfg):
    client = _client(cfg)
    verifier, challenge = _pkce_pair()
    code = _get_code(client, cfg, challenge)
    basic = base64.b64encode(
        f"{cfg.oauth_client_id}:{cfg.oauth_client_secret}".encode()
    ).decode()
    r = client.post(
        "/token",
        data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": REDIRECT, "code_verifier": verifier,
        },
        headers={"Authorization": f"Basic {basic}"})
    assert r.status_code == 200
    # client_secret_basic accepts the same TokenStore-minted access
    # token as client_secret_post — the access token is never the
    # static cfg.auth_token regardless of how the client authenticated.
    body = r.json()
    assert body["access_token"]
    assert body["access_token"] != cfg.auth_token


def test_token_code_is_single_use(cfg):
    client = _client(cfg)
    verifier, challenge = _pkce_pair()
    code = _get_code(client, cfg, challenge)
    payload = {
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": REDIRECT, "client_id": cfg.oauth_client_id,
        "client_secret": cfg.oauth_client_secret,
        "code_verifier": verifier,
    }
    first = client.post("/token", data=payload)
    assert first.status_code == 200
    # Replay the SAME code — must fail.
    second = client.post("/token", data=payload)
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"


def test_token_rejects_redirect_uri_mismatch(cfg):
    client = _client(cfg)
    verifier, challenge = _pkce_pair()
    code = _get_code(client, cfg, challenge)
    r = client.post("/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "https://evil.example/callback",
        "client_id": cfg.oauth_client_id,
        "client_secret": cfg.oauth_client_secret,
        "code_verifier": verifier,
    })
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_token_rejects_unsupported_grant_type(cfg):
    r = _client(cfg).post("/token", data={
        "grant_type": "client_credentials",
        "client_id": cfg.oauth_client_id,
        "client_secret": cfg.oauth_client_secret,
    })
    assert r.status_code == 400
    assert r.json()["error"] == "unsupported_grant_type"


# ── TokenStore — persistent issued-access-token table ───────────────────────
#
# These tests cover the requirement the user spec'd in the June 2 2026
# task: bearer tokens issued from /token must outlive a bridge restart
# so claude.ai's stored bearer keeps working without re-authenticating
# on every desktop reboot.


def _exchange(client, cfg, verifier, challenge):
    """Run the full authorize → token exchange and return the issued
    access token. Helper around _get_code → POST /token so the
    TokenStore tests stay focused on the persistence behaviour."""
    code = _get_code(client, cfg, challenge)
    r = client.post("/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": REDIRECT,
        "client_id": cfg.oauth_client_id,
        "client_secret": cfg.oauth_client_secret,
        "code_verifier": verifier,
    })
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_two_token_exchanges_produce_two_distinct_tokens(cfg):
    """Each /token exchange mints a fresh token — two separate
    connect flows produce two distinct bearers (both valid)."""
    client = _client(cfg)
    verifier, challenge = _pkce_pair()
    t1 = _exchange(client, cfg, verifier, challenge)
    t2 = _exchange(client, cfg, verifier, challenge)
    assert t1 != t2
    # Both authenticate /mcp independently.
    for tok in (t1, t2):
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "status",
                  "params": {}},
            headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200


def test_issued_token_survives_a_bridge_restart(cfg):
    """The persistence contract: a token minted by app instance A and
    stored in queue.db must still authenticate /mcp on a freshly
    constructed app instance B that points at the same db_path. This
    is the symptom the user reported — without the SQLite-backed
    TokenStore, restarting the bridge would force claude.ai through
    the connect flow again."""
    a = _client(cfg)            # follow_redirects=False — _get_code needs the 302
    verifier, challenge = _pkce_pair()
    tok = _exchange(a, cfg, verifier, challenge)
    # Simulated restart — same cfg, same db_path, brand new app.
    b = _client(cfg)
    r = b.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "status",
              "params": {}},
        headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["result"]["alive"] is True


def test_static_auth_token_still_authenticates_mcp(cfg):
    """The static cfg.auth_token remains a valid bearer — the CLI
    (`bridge push`, `bridge status`) and the worker authenticate with
    the config-file token, never the OAuth flow. The TokenStore is
    additive; it does NOT replace the static-token path."""
    client = _client(cfg)
    r = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "status",
              "params": {}},
        headers={"Authorization": f"Bearer {cfg.auth_token}"})
    assert r.status_code == 200


def test_unknown_bearer_is_rejected(cfg):
    """Negative case: a bearer that is neither in the TokenStore nor
    equal to cfg.auth_token must produce a 401 with the resource-
    metadata WWW-Authenticate header."""
    client = _client(cfg)
    r = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "status",
              "params": {}},
        headers={"Authorization": "Bearer not-a-valid-token"})
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers.get("www-authenticate", "")


def test_expired_token_is_rejected_and_deleted(cfg, tmp_queue_path):
    """A token whose expires_at is in the past must NOT authenticate.
    validate() also DELETEs the stale row on detection so the table
    stays small without a separate sweeper thread."""
    from mcp_bridge.oauth import TokenStore
    store = TokenStore(tmp_queue_path)
    # Issue a token with a negative TTL — immediately expired.
    tok, _ = store.issue(client_id="test-client-id", ttl_seconds=-1)
    assert store.validate(tok) is False
    # validate() removed the row on the failed check.
    assert store.validate(tok) is False  # still false, idempotent
    import sqlite3
    with sqlite3.connect(tmp_queue_path) as c:
        rows = c.execute(
            "SELECT COUNT(*) FROM tokens WHERE access_token = ?",
            (tok,)).fetchone()
        assert rows[0] == 0


def test_token_store_sweep_expired_at_startup(cfg, tmp_queue_path):
    """The TokenStore constructor sweeps expired rows so they do not
    accumulate across long-running deployments. This is the
    'load existing valid tokens' step from the user spec — validate()
    reads SQLite on every request, so the only thing startup needs to
    do is drop the dead rows."""
    from mcp_bridge.oauth import TokenStore
    a = TokenStore(tmp_queue_path)
    fresh, _ = a.issue(client_id="cid", ttl_seconds=3600)
    stale, _ = a.issue(client_id="cid", ttl_seconds=-1)
    # New store on the same DB sweeps stale rows.
    b = TokenStore(tmp_queue_path)
    assert b.validate(fresh) is True
    assert b.validate(stale) is False
    import sqlite3
    with sqlite3.connect(tmp_queue_path) as c:
        n = c.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
    assert n == 1  # only the fresh row remains


def test_token_store_table_columns_match_spec(tmp_queue_path):
    """Schema check — the table has the four columns the user spec'd:
    access_token (PK), client_id, issued_at, expires_at."""
    from mcp_bridge.oauth import TokenStore
    TokenStore(tmp_queue_path)
    import sqlite3
    with sqlite3.connect(tmp_queue_path) as c:
        info = c.execute("PRAGMA table_info(tokens)").fetchall()
    cols = {row[1]: row[2] for row in info}
    assert "access_token" in cols
    assert "client_id" in cols
    assert "issued_at" in cols
    assert "expires_at" in cols
    # access_token is the primary key.
    pk_cols = [row[1] for row in info if row[5]]
    assert pk_cols == ["access_token"]
