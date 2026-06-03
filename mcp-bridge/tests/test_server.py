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


# ── /admin/purge-queue (June 3 2026) ───────────────────────────────────────


def test_purge_queue_requires_token(cfg):
    """Same auth gate as every other operator endpoint — without the
    bearer token the route 401s. The purge changes queue state so it
    cannot be public even on localhost."""
    client = _client(cfg)
    r = client.post("/admin/purge-queue")
    assert r.status_code == 401


def test_purge_queue_with_wrong_token_returns_401(cfg):
    client = _client(cfg)
    r = client.post(
        "/admin/purge-queue",
        headers={"Authorization": "Bearer not-the-right-token"})
    assert r.status_code == 401


def test_purge_queue_cancels_pending_rows_and_returns_count(cfg):
    """The endpoint runs the queue purge and returns the count of
    rows cancelled plus a fresh status snapshot — one round-trip
    confirms the action landed."""
    client = _client(cfg)
    # Seed two pending prompts.
    for _ in range(2):
        r = client.post(
            "/push", json={"prompt": "x"}, headers=_auth(cfg))
        assert r.status_code == 200
    # Purge.
    r = client.post("/admin/purge-queue", headers=_auth(cfg))
    assert r.status_code == 200
    body = r.json()
    assert body["cancelled"] == 2
    # The snapshot in the response shows the post-purge state — no
    # second round-trip needed.
    assert body["snapshot"]["counts"].get("cancelled") == 2
    assert body["snapshot"]["counts"].get("pending", 0) == 0


def test_purge_queue_idempotent_on_empty_queue(cfg):
    """A purge against an empty queue is a clean no-op — returns 0,
    never raises. Lets operator scripts retry safely."""
    client = _client(cfg)
    r = client.post("/admin/purge-queue", headers=_auth(cfg))
    assert r.status_code == 200
    assert r.json()["cancelled"] == 0


def test_purge_queue_preserves_terminal_rows(cfg):
    """Completed rows are part of the audit trail — the endpoint
    must NOT touch them. Only in-flight (pending/running) rows
    flip to cancelled."""
    client = _client(cfg)
    # Push + complete a row through the queue API directly.
    push = client.post(
        "/push", json={"prompt": "done"}, headers=_auth(cfg))
    pid = push.json()["prompt_id"]
    # Drive it to complete via the queue (no public REST drain step,
    # so reach in via the app's queue handle the same way the worker
    # does).
    from mcp_bridge.server import create_app
    app = create_app(cfg)
    app.state.queue.claim_next("worker")
    app.state.queue.post_result(pid, result="ok")
    # Push one more — that becomes the pending row.
    client_two = _client(cfg)
    client_two.post(
        "/push", json={"prompt": "pending"}, headers=_auth(cfg))
    r = client_two.post(
        "/admin/purge-queue", headers=_auth(cfg))
    body = r.json()
    # The completed row from BEFORE the second client + the new
    # pending row from the second client share the same on-disk
    # SQLite db (cfg.db_path is shared), so we expect cancelled=1
    # — the new pending row — and complete >= 1 untouched.
    assert body["cancelled"] == 1
    counts = body["snapshot"]["counts"]
    assert counts.get("complete", 0) >= 1


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


# ── MCP lifecycle handshake ─────────────────────────────────────────────────
#
# Claude.ai's "Add Custom Connector" conversation toggle (Anthropic/
# Toolbox 1.0.0) runs three JSON-RPC calls in order:
#   1. initialize                     — server returns handshake info
#   2. notifications/initialized      — client ACK, server replies 202
#   3. tools/list                     — server returns the tool catalog
# All three must succeed for the toggle to flip from "couldn't
# connect" to ON. These tests pin the contract.


def test_initialize_returns_protocol_version_capabilities_and_serverinfo(cfg):
    """The `initialize` response is the spec'd shape: protocolVersion
    echoed back, tools capability advertised, serverInfo carrying the
    bridge's name + version."""
    client = _client(cfg)
    r = client.post(
        "/mcp", headers={**_auth(cfg),
                         "User-Agent": "Anthropic/Toolbox 1.0.0"},
        json=_rpc("initialize", {
            "protocolVersion": "2025-11-25",
            "capabilities":    {},
            "clientInfo":      {"name": "Anthropic/Toolbox"},
        }))
    assert r.status_code == 200
    body = r.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    result = body["result"]
    assert result["protocolVersion"] == "2025-11-25"
    assert result["capabilities"] == {"tools": {}}
    assert result["serverInfo"]["name"] == "mcp-bridge"
    # The bridge ships its version in mcp_bridge/__init__.py; whatever
    # that string is, it must surface here as a non-empty value so the
    # client knows what server it's talking to.
    assert isinstance(result["serverInfo"]["version"], str)
    assert result["serverInfo"]["version"]


def test_notifications_initialized_returns_empty_202(cfg):
    """`notifications/initialized` is a JSON-RPC NOTIFICATION (no id).
    The server MUST NOT respond with a JSON-RPC envelope. We return
    HTTP 202 with a zero-byte body — a strict client would reject a
    `null`-bodied response as a malformed envelope."""
    client = _client(cfg)
    r = client.post(
        "/mcp", headers=_auth(cfg),
        json={
            "jsonrpc": "2.0",
            "method":  "notifications/initialized",
            "params":  {},
        })
    assert r.status_code == 202
    # Zero-byte body — not the literal "null".
    assert r.content == b""


def test_unknown_notification_is_silently_accepted(cfg):
    """JSON-RPC 2.0: a server SHOULD silently ignore unknown
    notifications. The bridge returns 202 with an empty body and
    leaves a diagnostic log line behind (covered by the diagnostic
    logging contract, not this test)."""
    client = _client(cfg)
    r = client.post(
        "/mcp", headers=_auth(cfg),
        json={
            "jsonrpc": "2.0",
            "method":  "notifications/some_future_method",
            "params":  {},
        })
    assert r.status_code == 202
    assert r.content == b""


def test_tools_list_surfaces_three_tools_with_input_schemas(cfg):
    """`tools/list` returns the three bridge tools in MCP shape:
      push_prompt — prompt:str (required), session_id:str (optional)
      get_result  — prompt_id:str (required)
      status      — no params
    The names, descriptions, and required-field sets are pinned here
    because Claude.ai's UI surfaces them verbatim and a silent rename
    would break the user's saved tool toggles."""
    client = _client(cfg)
    r = client.post("/mcp", json=_rpc("tools/list", {}, req_id=2),
                     headers=_auth(cfg))
    assert r.status_code == 200
    body = r.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 2
    tools = body["result"]["tools"]
    assert isinstance(tools, list)
    assert len(tools) == 3

    by_name = {t["name"]: t for t in tools}
    assert set(by_name) == {"push_prompt", "get_result", "status"}

    push = by_name["push_prompt"]
    assert "Push a prompt" in push["description"]
    assert push["inputSchema"]["type"] == "object"
    assert push["inputSchema"]["properties"]["prompt"]["type"] == "string"
    assert (push["inputSchema"]["properties"]["session_id"]["type"]
            == "string")
    assert push["inputSchema"]["required"] == ["prompt"]

    get_r = by_name["get_result"]
    assert "Get the result" in get_r["description"]
    assert (get_r["inputSchema"]["properties"]["prompt_id"]["type"]
            == "string")
    assert get_r["inputSchema"]["required"] == ["prompt_id"]

    st = by_name["status"]
    assert "queue status" in st["description"]
    assert st["inputSchema"] == {"type": "object", "properties": {}}


# ── tools/call — the MCP invocation path ────────────────────────────────────


import json as _json  # local alias — keeps the existing _rpc helper isolated


def _tools_call(client, cfg, name, arguments=None, rid=10):
    """Helper — sends a tools/call request and returns the parsed body."""
    return client.post(
        "/mcp", headers=_auth(cfg),
        json=_rpc("tools/call",
                   {"name": name, "arguments": arguments or {}},
                   req_id=rid)).json()


def test_tools_call_push_prompt_returns_mcp_content_envelope(cfg):
    """tools/call wraps the underlying handler's dict in the MCP
    content envelope: {content: [{type: 'text', text: <JSON string>}]}.
    Claude.ai's LLM parses the text JSON to extract structured fields."""
    client = _client(cfg)
    body = _tools_call(client, cfg, "push_prompt",
                       {"prompt": "hello from claude.ai"}, rid=20)
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 20
    content = body["result"]["content"]
    assert isinstance(content, list)
    assert len(content) == 1
    assert content[0]["type"] == "text"
    parsed = _json.loads(content[0]["text"])
    assert parsed["status"] == "pending"
    assert isinstance(parsed["prompt_id"], int)


def test_tools_call_get_result_coerces_string_prompt_id(cfg):
    """tools/list advertises get_result.prompt_id as `string` per the
    user spec, but the underlying _h_get_result expects int. The
    tools/call dispatcher coerces digit-string → int so the schema
    Claude.ai sees and the handler the CLI uses stay reconciled."""
    client = _client(cfg)
    # Push first so we have a real id to fetch.
    push = _tools_call(client, cfg, "push_prompt",
                       {"prompt": "for coercion"}, rid=21)
    pid = _json.loads(push["result"]["content"][0]["text"])["prompt_id"]

    body = _tools_call(client, cfg, "get_result",
                       {"prompt_id": str(pid)}, rid=22)
    parsed = _json.loads(body["result"]["content"][0]["text"])
    assert parsed["id"] == pid
    assert parsed["prompt"] == "for coercion"
    assert parsed["status"] == "pending"


def test_tools_call_get_result_also_accepts_int_prompt_id(cfg):
    """The coercion is one-way (string → int). An int passed
    directly must still work — the CLI / mobile path has always
    called with int and that contract is unchanged."""
    client = _client(cfg)
    push = _tools_call(client, cfg, "push_prompt",
                       {"prompt": "native int path"}, rid=23)
    pid = _json.loads(push["result"]["content"][0]["text"])["prompt_id"]

    body = _tools_call(client, cfg, "get_result",
                       {"prompt_id": pid}, rid=24)
    parsed = _json.loads(body["result"]["content"][0]["text"])
    assert parsed["id"] == pid


def test_tools_call_status_returns_queue_snapshot(cfg):
    """No-argument tool. The status snapshot lands JSON-stringified
    in the content envelope."""
    client = _client(cfg)
    body = _tools_call(client, cfg, "status", {}, rid=25)
    parsed = _json.loads(body["result"]["content"][0]["text"])
    assert parsed["alive"] is True
    assert "counts" in parsed
    assert parsed["worker_enabled"] is False


def test_tools_call_unknown_tool_returns_invalid_params(cfg):
    """An unknown tool name surfaces as a JSON-RPC invalid_params
    error (-32602). Claude.ai handles that as a tool-call failure
    rather than a transport-level error."""
    client = _client(cfg)
    body = _tools_call(client, cfg, "frobnicate", {}, rid=26)
    assert "error" in body
    assert body["error"]["code"] == -32602
    # The error message names the tool that was attempted AND lists
    # what is known — useful in the bridge log + in Claude.ai's
    # surfaced error pane.
    assert "frobnicate" in body["error"]["message"]
    assert "push_prompt" in body["error"]["message"]


def test_tools_call_missing_name_returns_error(cfg):
    client = _client(cfg)
    body = client.post("/mcp", headers=_auth(cfg),
                        json=_rpc("tools/call",
                                  {"arguments": {}}, req_id=27)).json()
    assert "error" in body
    assert body["error"]["code"] == -32602


def test_tools_call_missing_required_argument_surfaces_handler_error(cfg):
    """The underlying handler's validation (push_prompt requires a
    non-empty `prompt`) propagates through tools/call as a JSON-RPC
    error — the dispatcher's `except ValueError` arm catches it."""
    client = _client(cfg)
    body = _tools_call(client, cfg, "push_prompt", {}, rid=28)
    assert "error" in body
    assert body["error"]["code"] == -32602
    assert "prompt" in body["error"]["message"].lower()


def test_tools_list_then_tools_call_round_trip(cfg):
    """End-to-end: list the tools, then invoke each one. This is the
    flow Claude.ai runs after a successful initialize handshake."""
    client = _client(cfg)
    auth = _auth(cfg)
    listed = client.post(
        "/mcp", headers=auth,
        json=_rpc("tools/list", {}, req_id=30)).json()
    names = [t["name"] for t in listed["result"]["tools"]]
    assert set(names) == {"push_prompt", "get_result", "status"}

    # Every advertised tool answers a tools/call invocation without
    # a JSON-RPC error.
    for i, name in enumerate(names, start=31):
        # Minimal viable arguments per tool.
        if name == "push_prompt":
            args = {"prompt": "roundtrip"}
        elif name == "get_result":
            # Need a real id — push first.
            push = _tools_call(client, cfg, "push_prompt",
                               {"prompt": "roundtrip-target"}, rid=100)
            pid = _json.loads(
                push["result"]["content"][0]["text"])["prompt_id"]
            args = {"prompt_id": str(pid)}
        else:
            args = {}
        body = _tools_call(client, cfg, name, args, rid=i)
        assert "error" not in body, f"tools/call {name} failed: {body}"
        assert body["result"]["content"][0]["type"] == "text"


def test_full_handshake_then_existing_tool_still_works(cfg):
    """End-to-end: run the three lifecycle calls in order, then prove
    the existing direct-method dispatch is untouched. This is the
    contract the user spec'd — the handshake adds new behaviour
    'alongside the existing push_prompt / get_result / status tool
    handlers' — those keep working unchanged."""
    client = _client(cfg)
    auth = _auth(cfg)

    init = client.post("/mcp", headers=auth, json=_rpc(
        "initialize", {"protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "Anthropic/Toolbox"}}))
    assert init.status_code == 200
    assert init.json()["result"]["protocolVersion"] == "2025-11-25"

    ack = client.post(
        "/mcp", headers=auth,
        json={"jsonrpc": "2.0",
              "method": "notifications/initialized", "params": {}})
    assert ack.status_code == 202
    assert ack.content == b""

    listed = client.post("/mcp", headers=auth,
                         json=_rpc("tools/list", {}, req_id=2))
    assert listed.status_code == 200
    assert len(listed.json()["result"]["tools"]) == 3

    # The direct-method path the local CLI and the mobile relay
    # already use is unchanged.
    pushed = client.post("/mcp", headers=auth, json=_rpc(
        "push_prompt", {"prompt": "post-handshake test"}))
    assert pushed.status_code == 200
    assert "result" in pushed.json()
    assert pushed.json()["result"]["status"] == "pending"
