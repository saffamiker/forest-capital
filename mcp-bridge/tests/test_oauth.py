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


def test_token_exchange_returns_auth_token(cfg):
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
    # The access token IS the bridge's bearer token.
    assert body["access_token"] == cfg.auth_token
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
    assert r.json()["access_token"] == cfg.auth_token


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
