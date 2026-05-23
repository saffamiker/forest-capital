"""HTTP-layer tests for the bridge server.

Covers:
  - bearer-token auth on every protected route
  - the JSON-RPC envelope shape for every tool
  - validation errors come back as structured RPC errors (not 500)
  - the REST shim is consistent with the MCP tools
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def _client(cfg):
    from mcp_bridge.server import create_app
    return TestClient(create_app(cfg))


def _auth(cfg):
    return {"Authorization": f"Bearer {cfg.auth_token}"}


def _rpc(method, params=None, req_id=1):
    return {
        "jsonrpc": "2.0",
        "id":      req_id,
        "method":  method,
        "params":  params or {},
    }


# ── Health is always open ──────────────────────────────────────────────────


def test_health_no_auth_required(cfg):
    client = _client(cfg)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["service"] == "mcp-bridge"


# ── Auth gate on /mcp ───────────────────────────────────────────────────────


def test_mcp_without_token_returns_401(cfg):
    client = _client(cfg)
    r = client.post("/mcp", json=_rpc("status"))
    assert r.status_code == 401


def test_mcp_with_wrong_token_returns_401(cfg):
    client = _client(cfg)
    r = client.post("/mcp", json=_rpc("status"),
                     headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_mcp_with_correct_token_returns_200(cfg):
    client = _client(cfg)
    r = client.post("/mcp", json=_rpc("status"), headers=_auth(cfg))
    assert r.status_code == 200
    body = r.json()
    assert "result" in body
    assert body["result"]["alive"] is True


def test_empty_token_disables_auth(cfg):
    # Bridge mode: localhost-only with auth_token=''. Every request
    # should pass through without a header. This is the doc'd dev
    # shortcut — also the only way to run the bridge with zero
    # config.
    cfg.auth_token = ""
    client = _client(cfg)
    r = client.post("/mcp", json=_rpc("status"))
    assert r.status_code == 200


# ── REST shim mirror ───────────────────────────────────────────────────────


def test_rest_push_requires_token(cfg):
    client = _client(cfg)
    r = client.post("/push", json={"prompt": "hi"})
    assert r.status_code == 401


def test_rest_push_and_get_result(cfg):
    client = _client(cfg)
    r = client.post("/push",
                     json={"prompt": "hi from mobile"},
                     headers=_auth(cfg))
    assert r.status_code == 200
    pid = r.json()["prompt_id"]
    assert isinstance(pid, int)
    # Pending row exists.
    r = client.get(f"/result/{pid}", headers=_auth(cfg))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert body["prompt"] == "hi from mobile"


def test_rest_result_404_for_unknown(cfg):
    client = _client(cfg)
    r = client.get("/result/9999", headers=_auth(cfg))
    assert r.status_code == 404


def test_rest_status_includes_worker_flag(cfg):
    client = _client(cfg)
    r = client.get("/status", headers=_auth(cfg))
    assert r.status_code == 200
    body = r.json()
    assert body["alive"] is True
    assert "worker_enabled" in body
    assert body["worker_enabled"] is False  # default off


# ── MCP tools: each method end to end ──────────────────────────────────────


def test_push_prompt_via_mcp(cfg):
    client = _client(cfg)
    r = client.post("/mcp",
                     json=_rpc("push_prompt",
                                {"prompt": "what's the weather"}),
                     headers=_auth(cfg))
    body = r.json()
    assert "error" not in body
    assert body["result"]["status"] == "pending"
    assert isinstance(body["result"]["prompt_id"], int)


def test_push_prompt_rejects_empty(cfg):
    client = _client(cfg)
    r = client.post("/mcp",
                     json=_rpc("push_prompt", {"prompt": ""}),
                     headers=_auth(cfg))
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == -32602  # INVALID_PARAMS


def test_push_prompt_enforces_max_bytes(cfg):
    cfg.max_prompt_bytes = 16
    client = _client(cfg)
    r = client.post("/mcp",
                     json=_rpc("push_prompt",
                                {"prompt": "x" * 100}),
                     headers=_auth(cfg))
    body = r.json()
    assert "error" in body
    assert "exceeds" in body["error"]["message"]


def test_full_round_trip_via_mcp(cfg):
    client = _client(cfg)
    # 1. Mobile pushes.
    r = client.post("/mcp",
                     json=_rpc("push_prompt",
                                {"prompt": "round-trip"}),
                     headers=_auth(cfg))
    pid = r.json()["result"]["prompt_id"]

    # 2. Consumer lists pending and sees the row.
    r = client.post("/mcp", json=_rpc("list_pending"),
                     headers=_auth(cfg))
    pending = r.json()["result"]["pending"]
    assert any(row["id"] == pid for row in pending)

    # 3. Consumer claims.
    r = client.post("/mcp",
                     json=_rpc("claim_next",
                                {"claimed_by": "test-worker"}),
                     headers=_auth(cfg))
    claimed = r.json()["result"]["prompt"]
    assert claimed is not None
    assert claimed["id"] == pid
    assert claimed["status"] == "running"

    # 4. Consumer posts the result.
    r = client.post("/mcp",
                     json=_rpc("post_result",
                                {"prompt_id": pid,
                                 "result": "the answer is 42"}),
                     headers=_auth(cfg))
    assert r.json()["result"]["ok"] is True

    # 5. Mobile polls get_result.
    r = client.post("/mcp",
                     json=_rpc("get_result", {"prompt_id": pid}),
                     headers=_auth(cfg))
    final = r.json()["result"]
    assert final["status"] == "complete"
    assert final["result"] == "the answer is 42"


def test_claim_next_returns_null_when_empty(cfg):
    client = _client(cfg)
    r = client.post("/mcp",
                     json=_rpc("claim_next",
                                {"claimed_by": "w"}),
                     headers=_auth(cfg))
    assert r.json()["result"]["prompt"] is None


def test_get_result_unknown_id_returns_error_envelope(cfg):
    client = _client(cfg)
    r = client.post("/mcp",
                     json=_rpc("get_result", {"prompt_id": 9999}),
                     headers=_auth(cfg))
    body = r.json()
    # The unknown-id path comes back as a structured RPC error
    # rather than HTTP 404 — clients have one parse path.
    assert "error" in body
    assert "not found" in body["error"]["message"].lower()


def test_post_result_requires_exactly_one_of_result_or_error(cfg):
    client = _client(cfg)
    r = client.post("/mcp",
                     json=_rpc("push_prompt", {"prompt": "x"}),
                     headers=_auth(cfg))
    pid = r.json()["result"]["prompt_id"]
    # Neither field — invalid.
    r = client.post("/mcp",
                     json=_rpc("post_result",
                                {"prompt_id": pid}),
                     headers=_auth(cfg))
    assert "error" in r.json()
    # Both fields — also invalid.
    r = client.post("/mcp",
                     json=_rpc("post_result",
                                {"prompt_id": pid,
                                 "result": "r",
                                 "error":  "e"}),
                     headers=_auth(cfg))
    assert "error" in r.json()


def test_unknown_method_returns_method_not_found(cfg):
    client = _client(cfg)
    r = client.post("/mcp",
                     json=_rpc("does_not_exist"),
                     headers=_auth(cfg))
    body = r.json()
    assert body["error"]["code"] == -32601


def test_malformed_json_returns_parse_error(cfg):
    client = _client(cfg)
    r = client.post("/mcp", content=b"not json at all",
                     headers={**_auth(cfg),
                              "Content-Type": "application/json"})
    body = r.json()
    assert body["error"]["code"] == -32700


def test_status_method_reports_queue_counts(cfg):
    client = _client(cfg)
    # Push a couple of prompts so the counts aren't all zero.
    for _ in range(3):
        client.post("/mcp",
                    json=_rpc("push_prompt", {"prompt": "p"}),
                    headers=_auth(cfg))
    r = client.post("/mcp", json=_rpc("status"),
                     headers=_auth(cfg))
    counts = r.json()["result"]["counts"]
    assert counts["pending"] == 3
    assert counts["running"] == 0
    assert counts["complete"] == 0
