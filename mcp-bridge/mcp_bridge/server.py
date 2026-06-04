"""mcp_bridge.server — FastAPI MCP server with Streamable HTTP transport.

The MCP wire protocol is JSON-RPC 2.0 over POST. Implementing it
directly here (instead of pulling in the full mcp SDK) keeps the
dependency footprint small — FastAPI + sqlite3 are the only
non-stdlib needs — and gives full control over auth and error
shape.

Exposed JSON-RPC methods (the "tools" the client can call):

  push_prompt(prompt: str, session_id: str | None)
    → {prompt_id: int, status: str}
    Mobile → bridge. Puts the prompt on the queue.

  get_result(prompt_id: int)
    → {status: str, result: str | null, error: str | null, ...}
    Mobile → bridge. Poll for the result.

  list_pending(limit: int = 50)
    → {pending: [...]}
    Consumer (slash command / worker) → bridge. See what's queued.

  claim_next(claimed_by: str = "live")
    → {prompt: {...}} | {prompt: null}
    Consumer → bridge. Atomically claim the oldest pending row.

  post_result(prompt_id: int, result: str | null,
              error: str | null)
    → {ok: true}
    Consumer → bridge. Mark the row done.

  status()
    → {alive: true, counts: {...}, last_completed_at, version}
    Health check. Mobile calls it to confirm the desktop bridge
    is live before pushing.

Plus a small REST shim (/health, /push, /result/<id>, /status)
for the local CLI's testing convenience. Both surfaces share the
same Queue instance so a CLI push shows up immediately in a /status
read.

Auth: every request to /mcp and to the REST shim (except /health
which is operational) must carry an `Authorization: Bearer <token>`
header. The token lives in the bridge config. An empty config token
disables auth — fine for localhost-only deploys, never expose the
bridge via a tunnel with no token.
"""
from __future__ import annotations

import json
import os
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from . import __version__
from .config import BridgeConfig, load_config
from .queue import Queue

try:
    import structlog
    _slog: Any = structlog.get_logger(__name__)
except Exception:  # noqa: BLE001 — structlog optional for minimal installs
    _slog = None


# ── Diagnostic logging (June 2 2026) ─────────────────────────────────────
# Claude.ai's "Add Custom Connector" handshake at the SETTINGS level
# only exercises auth + an OPTIONS / discovery probe. The actual MCP
# capability negotiation runs when the user toggles the connector ON
# inside a conversation — at that point Claude.ai issues the JSON-RPC
# lifecycle calls (`initialize`, `tools/list`, `notifications/...`)
# that the connector must answer for the toggle to succeed. The user
# is seeing "couldn't connect" specifically at the conversation toggle,
# so we need to SEE the request bodies Claude.ai sends in order to
# know whether the bridge's _METHOD_HANDLERS dispatch table is
# missing the expected methods.
#
# These helpers log every MCP request body, every response envelope,
# and an explicit method-not-found line. Output is bounded (each
# field truncated to keep large tool catalogs from flooding the log)
# and routes through structlog when available, falling back to a
# print line so the diagnostic survives a minimal install. The same
# pattern as mcp_bridge/worker.py._log.

_MAX_LOG_FIELD_CHARS = 4000


def _log(event: str, **kw: Any) -> None:
    """structlog when present, print otherwise. Used by every
    diagnostic site in this module so a single switch (the structlog
    optional import above) decides the format."""
    if _slog is not None:
        try:
            _slog.info(event, **kw)
            return
        except Exception:  # noqa: BLE001
            pass
    kvs = " ".join(f"{k}={v!r}" for k, v in kw.items())
    print(f"[mcp-bridge.server] {event} {kvs}", flush=True)


def _truncate_for_log(value: Any) -> str:
    """JSON-serialise (preserves the wire shape) and cap at
    _MAX_LOG_FIELD_CHARS so a fat tool catalog doesn't push the
    operator's terminal to its scrollback ceiling."""
    try:
        s = json.dumps(value, default=str, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        s = repr(value)
    if len(s) > _MAX_LOG_FIELD_CHARS:
        return s[:_MAX_LOG_FIELD_CHARS] + f"…(+{len(s) - _MAX_LOG_FIELD_CHARS} chars)"
    return s


# Sentinel error codes — JSON-RPC convention is negative integers,
# < -32000 are application-defined. The MCP spec leaves room for
# custom codes; we keep ours in a contiguous block so a client can
# branch by range.
_RPC_PARSE_ERROR = -32700
_RPC_INVALID_REQUEST = -32600
_RPC_METHOD_NOT_FOUND = -32601
_RPC_INVALID_PARAMS = -32602
_RPC_INTERNAL_ERROR = -32603
_RPC_TOOL_ERROR = -32000  # base for tool-specific errors


def create_app(cfg: BridgeConfig | None = None) -> FastAPI:
    """App factory — the worker and tests construct their own app
    with a test config + in-memory or temp-file Queue. The CLI's
    `bridge serve` uses load_config() implicitly."""
    cfg = cfg or load_config()
    # Log the RESOLVED ABSOLUTE db_path on startup so the operator
    # can confirm queue.db is being found consistently across
    # restarts. A relative path resolved against the wrong cwd is
    # the classic cause of "every restart loses state" — this log
    # line makes the path discoverable without inspecting the
    # config loader's output. June 2 2026 diagnostic.
    try:
        _abs_db = os.path.abspath(cfg.db_path)
        _db_exists = os.path.exists(_abs_db)
        _log("bridge_startup",
             db_path=cfg.db_path,
             db_path_absolute=_abs_db,
             db_file_exists=_db_exists,
             cwd=os.getcwd(),
             version=__version__,
             oauth_configured=bool(cfg.oauth_client_id),
             worker_enabled=cfg.worker_enabled)
    except Exception as exc:  # noqa: BLE001 — never abort startup on a log line
        _log("bridge_startup_log_failed", error=str(exc))
    queue = Queue(cfg.db_path)
    app = FastAPI(
        title="mcp-bridge",
        version=__version__,
        description=(
            "Local MCP server bridging Claude.ai mobile and Claude "
            "Code desktop. See README.md for setup."),
    )

    # Stash config + queue on the app so the auth dep + handlers can
    # reach them without globals. Tests build their own app with a
    # different cfg / queue.
    app.state.cfg = cfg
    app.state.queue = queue

    # ── Auth dependency ─────────────────────────────────────────────────

    def require_token(request: Request) -> None:
        """Bearer-token gate on every protected route. Skipped only
        when cfg.auth_token is empty (localhost dev mode). The 401
        body is intentionally generic — no enumeration of which
        token would have worked.

        Two valid sources, checked in order:

          1. The persistent OAuth TOKEN STORE — bearers minted by
             /token and saved to queue.db so they survive a bridge
             restart. Every claude.ai /mcp call lands here.
          2. The static cfg.auth_token from the config file — used
             by the local CLI (`bridge push`, `bridge status`) and
             the worker, which never run the OAuth flow. Acts as the
             admin / operator token.

        Either path admits the request; only a bearer that fails BOTH
        produces a 401.

        On a 401 the response carries a WWW-Authenticate header
        pointing at the protected-resource metadata (RFC 9728). That
        is how an OAuth client (claude.ai) DISCOVERS the authorization
        server when it hits /mcp without a token: it reads the
        resource_metadata URL, fetches the AS metadata, and runs the
        authorize/token flow. Without the header the OAuth connector
        cannot bootstrap from a bare /mcp call."""
        static_token = app.state.cfg.auth_token
        if not static_token:
            return
        base = str(request.base_url).rstrip("/")
        proto = request.headers.get("x-forwarded-proto")
        fwd_host = request.headers.get("x-forwarded-host")
        if proto and fwd_host:
            base = f"{proto}://{fwd_host}".rstrip("/")
        challenge = (
            'Bearer resource_metadata='
            f'"{base}/.well-known/oauth-protected-resource"')
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise HTTPException(
                status_code=401, detail="Missing bearer token.",
                headers={"WWW-Authenticate": challenge})
        provided = header.split(" ", 1)[1].strip()
        # OAuth-issued tokens first (this is the common path —
        # claude.ai is the dominant client). The fail-open getattr
        # keeps tests / old apps that never registered an OAuth store
        # working — they fall through to the static-token branch.
        token_store = getattr(app.state, "oauth_token_store", None)
        if token_store is not None and token_store.validate(provided):
            return
        # Static config token — used by the CLI and the worker.
        if provided == static_token:
            return
        raise HTTPException(
            status_code=401, detail="Invalid bearer token.",
            headers={"WWW-Authenticate": challenge})

    # ── Public operational endpoint (no auth) ──────────────────────────

    @app.get("/health")
    def health() -> dict[str, Any]:
        """No-auth health check. Mobile can probe this before
        attempting the authenticated MCP handshake. Returns 200
        + a tiny payload as long as the process is up."""
        return {
            "ok":      True,
            "service": "mcp-bridge",
            "version": __version__,
        }

    # ── REST shim — convenience for the local CLI ──────────────────────

    @app.post("/push")
    def rest_push(body: dict[str, Any],
                   _: None = Depends(require_token)) -> dict[str, Any]:
        prompt = (body or {}).get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise HTTPException(
                status_code=422, detail="prompt is required.")
        if len(prompt.encode("utf-8")) > app.state.cfg.max_prompt_bytes:
            raise HTTPException(
                status_code=422,
                detail=(f"prompt exceeds "
                        f"{app.state.cfg.max_prompt_bytes} bytes."))
        session_id = (body or {}).get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            raise HTTPException(
                status_code=422,
                detail="session_id must be a string.")
        prompt_id = app.state.queue.enqueue(prompt, session_id)
        return {"prompt_id": prompt_id, "status": "pending"}

    @app.get("/result/{prompt_id}")
    def rest_result(prompt_id: int,
                     _: None = Depends(require_token)) -> dict[str, Any]:
        row = app.state.queue.get(int(prompt_id))
        if row is None:
            raise HTTPException(
                status_code=404, detail="Prompt id not found.")
        return row

    @app.get("/status")
    def rest_status(_: None = Depends(require_token)
                     ) -> dict[str, Any]:
        snap = app.state.queue.status_snapshot()
        return {
            "alive":            True,
            "version":          __version__,
            "worker_enabled":   app.state.cfg.worker_enabled,
            **snap,
        }

    @app.post("/admin/purge-queue")
    def rest_purge_queue(
        _: None = Depends(require_token),
    ) -> dict[str, Any]:
        """Cancel every pending or running prompt. Operator-facing
        queue reset — use when the queue gets jammed (worker died
        mid-prompt, prompts piled up behind a long-running one, etc.)
        without needing shell access to the SQLite db. Completed and
        failed rows are preserved; status is logged so an audit
        trail exists.

        Returns the count of rows cancelled and the post-purge
        status snapshot so the caller can confirm in one round-trip.

        Auth: same bearer token every other operator endpoint
        requires. Same trust model — the token holder can read the
        full queue via /status, so purging is no greater authority.
        June 3 2026."""
        cancelled = app.state.queue.purge_pending_and_running()
        snapshot = app.state.queue.status_snapshot()
        _log(
            "purge_queue",
            cancelled=cancelled,
            counts=snapshot.get("counts"),
        )
        return {
            "cancelled":         cancelled,
            "snapshot":          snapshot,
        }

    # ── MCP — JSON-RPC over HTTP POST /mcp ─────────────────────────────

    @app.post("/mcp")
    async def mcp_endpoint(request: Request,
                            _: None = Depends(require_token)
                            ) -> JSONResponse:
        """Streamable-HTTP MCP entry point. The body is a JSON-RPC
        2.0 envelope; the response is the JSON-RPC reply envelope.
        Single requests only — no batching in this minimal cut.

        Every request and every response is logged via _log() so the
        operator can see EXACTLY what Claude.ai sends at the
        conversation-toggle handshake and what the bridge sends back.
        See the "Diagnostic logging" block at the top of this module
        for the rationale and the truncation policy."""
        # User-Agent + Accept are useful when the client is unclear
        # (claude.ai vs claude.ai/mobile vs claude-code) — both are
        # safe to log in full.
        ua = request.headers.get("user-agent", "")
        accept = request.headers.get("accept", "")
        try:
            raw = await request.body()
            # Log the inbound BODY before any branching so a parse
            # failure or a missing method is still visible.
            _log("mcp_request_received",
                 user_agent=ua, accept=accept,
                 body=_truncate_for_log(
                     raw.decode("utf-8", errors="replace") if raw else ""))
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                resp = _rpc_error(None, _RPC_PARSE_ERROR, "Parse error.")
                _log("mcp_response_sent", req_id=None,
                     reason="parse_error", body=_truncate_for_log(
                         {"jsonrpc": "2.0", "id": None,
                          "error": {"code": _RPC_PARSE_ERROR,
                                    "message": "Parse error."}}))
                return resp
            if not isinstance(payload, dict):
                _log("mcp_response_sent", req_id=None,
                     reason="envelope_not_object")
                return _rpc_error(None, _RPC_INVALID_REQUEST,
                                   "JSON-RPC envelope must be an "
                                   "object.")
            req_id = payload.get("id")
            method = payload.get("method")
            params = payload.get("params") or {}
            _log("mcp_request_parsed",
                 req_id=req_id, method=method,
                 params=_truncate_for_log(params))
            if not isinstance(method, str):
                _log("mcp_response_sent", req_id=req_id,
                     method=method, reason="method_not_string")
                return _rpc_error(req_id, _RPC_INVALID_REQUEST,
                                   "method must be a string.")
            if not isinstance(params, dict):
                _log("mcp_response_sent", req_id=req_id,
                     method=method, reason="params_not_object")
                return _rpc_error(req_id, _RPC_INVALID_PARAMS,
                                   "params must be an object.")

            # ── Notification path ──────────────────────────────────
            # JSON-RPC 2.0: a Request without an id is a Notification,
            # and the server MUST NOT respond. By MCP convention every
            # `notifications/*` method is a notification (the missing
            # id is the wire-level signal). We run the handler for
            # any side effects, then return HTTP 202 with an empty
            # body so the client knows the message was accepted
            # without a JSON-RPC envelope coming back.
            if method.startswith("notifications/"):
                notif_handler = _NOTIFICATION_HANDLERS.get(method)
                if notif_handler is not None:
                    try:
                        notif_handler(app, params)
                    except Exception as exc:  # noqa: BLE001
                        # A notification handler failing is logged but
                        # never produces a response — the protocol
                        # forbids it.
                        _log("mcp_notification_handler_failed",
                             method=method, error=str(exc))
                    _log("mcp_response_sent", req_id=req_id,
                         method=method, reason="notification_accepted")
                else:
                    # Unknown notification — silently accept per the
                    # spec (servers ignore unknown notifications) but
                    # log it so a missing handler is visible in the
                    # diagnostic stream.
                    _log("mcp_unknown_notification", method=method)
                    _log("mcp_response_sent", req_id=req_id,
                         method=method,
                         reason="unknown_notification_ignored")
                # Truly empty body — not `null`. JSONResponse(None)
                # serialises to the JSON literal "null" which a strict
                # client might parse as a JSON-RPC response and reject;
                # bare Response with no content gives the
                # zero-byte body MCP's notification path expects.
                return Response(status_code=202)

            handler = _METHOD_HANDLERS.get(method)
            if handler is None:
                # Explicit log line for the case the user is
                # investigating — Claude.ai sends MCP lifecycle
                # methods (initialize, tools/list, …) that the
                # current dispatch table does not implement. Listing
                # the known methods inline makes the gap obvious in
                # the log scan.
                _log("mcp_method_not_found",
                     req_id=req_id, method=method,
                     known_methods=sorted(_METHOD_HANDLERS.keys()))
                _log("mcp_response_sent", req_id=req_id,
                     method=method, reason="method_not_found")
                return _rpc_error(req_id, _RPC_METHOD_NOT_FOUND,
                                   f"Unknown method '{method}'.")
            try:
                result = handler(app, params)
            except HTTPException as exc:
                # Re-shape FastAPI auth/validation errors into
                # JSON-RPC errors so the client gets a consistent
                # envelope regardless of failure site.
                _log("mcp_response_sent", req_id=req_id,
                     method=method, reason="http_exception",
                     detail=str(exc.detail))
                return _rpc_error(req_id, _RPC_TOOL_ERROR,
                                   str(exc.detail))
            except ValueError as exc:
                _log("mcp_response_sent", req_id=req_id,
                     method=method, reason="invalid_params",
                     detail=str(exc))
                return _rpc_error(req_id, _RPC_INVALID_PARAMS,
                                   str(exc))
            response_body = {
                "jsonrpc": "2.0",
                "id":      req_id,
                "result":  result,
            }
            _log("mcp_response_sent", req_id=req_id, method=method,
                 reason="success",
                 body=_truncate_for_log(response_body))
            return JSONResponse(response_body)
        except Exception as exc:  # noqa: BLE001
            # Last-resort guard — every uncaught exception comes
            # back as a structured RPC error rather than a 500
            # HTML page. The bridge stays usable from mobile.
            _log("mcp_endpoint_unhandled",
                 user_agent=ua, error=str(exc))
            return _rpc_error(None, _RPC_INTERNAL_ERROR, str(exc))

    # ── OAuth 2.1 shim — claude.ai connector registration ──────────────
    # Discovery (/.well-known/*), /authorize, /token. The token /token
    # issues IS cfg.auth_token, so require_token above validates it
    # unchanged. These routes are deliberately UNauthenticated (they
    # ARE the auth handshake). See oauth.py.
    from .oauth import register_oauth_routes
    register_oauth_routes(app, cfg)

    return app


def _rpc_error(req_id: Any, code: int, message: str) -> JSONResponse:
    """Builds a JSON-RPC error envelope. HTTP status is always 200 —
    the protocol carries the error in-band, not via HTTP status, so
    clients have one consistent parsing path."""
    return JSONResponse({
        "jsonrpc": "2.0",
        "id":      req_id,
        "error":   {"code": code, "message": message},
    })


# ── MCP method handlers ────────────────────────────────────────────────


def _h_push_prompt(app: FastAPI, params: dict[str, Any]) -> dict[str, Any]:
    prompt = params.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("'prompt' is required and must be a non-empty string.")
    if len(prompt.encode("utf-8")) > app.state.cfg.max_prompt_bytes:
        raise ValueError(
            f"'prompt' exceeds {app.state.cfg.max_prompt_bytes} bytes.")
    session_id = params.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        raise ValueError("'session_id' must be a string when provided.")
    prompt_id = app.state.queue.enqueue(prompt, session_id)
    return {"prompt_id": prompt_id, "status": "pending"}


def _h_get_result(app: FastAPI, params: dict[str, Any]) -> dict[str, Any]:
    prompt_id = params.get("prompt_id")
    if not isinstance(prompt_id, int):
        raise ValueError("'prompt_id' must be an integer.")
    row = app.state.queue.get(prompt_id)
    if row is None:
        raise ValueError(f"prompt_id {prompt_id} not found.")
    return row


def _h_list_pending(app: FastAPI, params: dict[str, Any]) -> dict[str, Any]:
    limit = params.get("limit", 50)
    if not isinstance(limit, int) or limit <= 0:
        raise ValueError("'limit' must be a positive integer.")
    return {"pending": app.state.queue.list_pending(limit)}


def _h_claim_next(app: FastAPI, params: dict[str, Any]) -> dict[str, Any]:
    claimed_by = params.get("claimed_by", "live")
    if not isinstance(claimed_by, str) or not claimed_by:
        raise ValueError("'claimed_by' must be a non-empty string.")
    row = app.state.queue.claim_next(claimed_by)
    return {"prompt": row}


def _h_post_result(app: FastAPI, params: dict[str, Any]) -> dict[str, Any]:
    prompt_id = params.get("prompt_id")
    if not isinstance(prompt_id, int):
        raise ValueError("'prompt_id' must be an integer.")
    result = params.get("result")
    error = params.get("error")
    if (result is None) == (error is None):
        raise ValueError(
            "post_result requires exactly one of 'result' or 'error'.")
    ok = app.state.queue.post_result(prompt_id, result=result, error=error)
    if not ok:
        raise ValueError(
            f"prompt_id {prompt_id} was not in pending/running state.")
    return {"ok": True}


def _h_status(app: FastAPI, _params: dict[str, Any]) -> dict[str, Any]:
    snap = app.state.queue.status_snapshot()
    return {
        "alive":           True,
        "version":         __version__,
        "worker_enabled":  app.state.cfg.worker_enabled,
        **snap,
    }


# ── MCP lifecycle handlers (June 2 2026) ──────────────────────────────────
# Claude.ai's conversation-toggle handshake (Anthropic/Toolbox client)
# calls three lifecycle methods after auth. Without these the toggle
# fails with "couldn't connect" because the bridge returns
# method_not_found on the very first call — see the PR #258 diagnostic
# log capture.
#
# The handlers below answer the protocol calls; the BRIDGE TOOLS
# themselves (push_prompt / get_result / status) keep their existing
# direct method dispatch so the local CLI and the mobile relay path
# stay unchanged. Claude.ai will discover the tools through tools/list
# and invoke them through tools/call — tools/call is a follow-up; the
# scope of this commit is the three handshake methods the user spec'd.

# Protocol version Claude.ai sends in its initialize call (Anthropic/
# Toolbox 1.0.0 → 2025-11-25). We echo it back unchanged; any newer
# version the client supports will negotiate on its side.
_MCP_PROTOCOL_VERSION = "2025-11-25"

# Tools exposed to Claude.ai via tools/list. Each entry is the
# MCP-spec shape: {name, description, inputSchema}. inputSchema is a
# JSON Schema describing the tool's arguments. The descriptions and
# schemas match the user's spec verbatim — get_result.prompt_id is
# typed string here (per the spec) even though the existing
# _h_get_result handler expects an integer; the existing push_prompt /
# get_result / status methods remain available via direct method
# dispatch, and tools/call (which would coerce arguments through
# these schemas before invoking the handler) is a follow-up.
_MCP_TOOLS_CATALOG: list[dict[str, Any]] = [
    {
        "name": "push_prompt",
        "description": "Push a prompt to Claude Code's queue for execution",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt":     {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "get_result",
        "description": "Get the result of a previously pushed prompt",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt_id": {"type": "string"},
            },
            "required": ["prompt_id"],
        },
    },
    {
        "name": "status",
        "description": "Get queue status including pending prompt count",
        "inputSchema": {
            "type":       "object",
            "properties": {},
        },
    },
    {
        # June 4 2026 — expose claim_next so Claude Code can actively
        # pull pending prompts when worker dispatch is unreliable. The
        # handler exists already (_h_claim_next, line 515) for the
        # CLI / direct-method path; this entry surfaces it to Claude.ai
        # via tools/list. Args left empty in the schema so the LLM
        # never has to specify claimed_by — the handler defaults to
        # "live" which is exactly what a CC-pull-from-queue should
        # record. The full row (id + prompt + metadata) returns under
        # the `prompt` key, so the caller reads prompt_id from
        # result.prompt.id and content from result.prompt.prompt.
        "name": "claim_next",
        "description": (
            "Claim the next pending prompt from the queue for "
            "execution. Returns the prompt content and prompt_id "
            "wrapped under the `prompt` key, or {prompt: null} when "
            "the queue is empty. Use this to actively pull pending "
            "prompts rather than waiting for worker dispatch."),
        "inputSchema": {
            "type":       "object",
            "properties": {},
        },
    },
]


def _h_initialize(_app: FastAPI, _params: dict[str, Any]) -> dict[str, Any]:
    """MCP `initialize` — the handshake response. Echoes the spec'd
    protocol version, advertises the tools capability, and identifies
    the server. The client (Claude.ai / Anthropic Toolbox) follows
    this with `notifications/initialized` and then `tools/list`."""
    return {
        "protocolVersion": _MCP_PROTOCOL_VERSION,
        "capabilities":    {"tools": {}},
        "serverInfo": {
            "name":    "mcp-bridge",
            "version": __version__,
        },
    }


def _h_tools_list(_app: FastAPI, _params: dict[str, Any]) -> dict[str, Any]:
    """MCP `tools/list` — surfaces the three tools the bridge exposes
    to Claude.ai. The tool catalog is a module-level constant so the
    same shape is returned on every call regardless of state."""
    return {"tools": _MCP_TOOLS_CATALOG}


def _h_notifications_initialized(
    _app: FastAPI, _params: dict[str, Any],
) -> None:
    """MCP `notifications/initialized` — the client's acknowledgement
    after a successful handshake. JSON-RPC notifications carry no id
    and the server MUST NOT respond, so the dispatcher routes this
    through the notification path (HTTP 202, empty body). The handler
    itself is a side-effect-free no-op; its presence in
    _NOTIFICATION_HANDLERS is what lets the dispatcher recognise it
    as a known notification rather than silently dropping it."""
    return None


# Maps a tool name (as advertised in tools/list) to the underlying
# direct-method handler. tools/call routes through this dispatch
# table so the same code path that serves the CLI's direct method
# calls also serves Claude.ai's tools/call invocations — single
# source of truth, no duplicated validation.
_MCP_TOOL_HANDLERS = {
    "push_prompt": _h_push_prompt,
    "get_result":  _h_get_result,
    "status":      _h_status,
    # June 4 2026 — tools/call also routes through here; pairs with
    # the catalog entry above so an LLM that discovers claim_next via
    # tools/list can invoke it via tools/call without a second
    # dispatch path.
    "claim_next":  _h_claim_next,
}


def _h_tools_call(app: FastAPI, params: dict[str, Any]) -> dict[str, Any]:
    """MCP `tools/call` — the invocation path Claude.ai uses inside a
    conversation after discovering the bridge's tools via tools/list.

    Wraps the underlying handler's return value in the MCP content
    envelope:

        {"content": [{"type": "text", "text": <JSON-stringified>}]}

    The text payload is the JSON serialisation of the handler's
    return dict, so Claude.ai's LLM sees a structured response and
    can extract fields by name. Errors raised by the underlying
    handler propagate as ValueError → invalid_params on the JSON-RPC
    envelope (same as direct method dispatch); see the dispatcher's
    `except ValueError` arm.

    SCHEMA-vs-HANDLER COERCION
      tools/list advertises get_result.prompt_id as `string` (per the
      user's June 2 2026 spec), but the underlying _h_get_result
      expects an `int` — the CLI / mobile path has always called it
      that way. When the LLM serialises prompt_id as a string (the
      schema's declared type), coerce digit-strings to int here so
      the underlying handler still accepts the call. The reverse
      (int passed where the schema says string) is rare and the
      underlying handler tolerates int natively, so no coercion
      needed in that direction. This shim is the entire reconciliation
      between the MCP-advertised schema and the legacy direct-method
      contract.
    """
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(name, str) or not name:
        raise ValueError(
            "'name' is required and must be a non-empty string.")
    if not isinstance(arguments, dict):
        raise ValueError("'arguments' must be an object.")
    handler = _MCP_TOOL_HANDLERS.get(name)
    if handler is None:
        raise ValueError(
            f"Unknown tool: '{name}'. Known tools: "
            f"{sorted(_MCP_TOOL_HANDLERS.keys())}")
    # Schema/handler reconciliation for get_result.prompt_id.
    if name == "get_result":
        pid = arguments.get("prompt_id")
        if isinstance(pid, str) and pid.lstrip("-").isdigit():
            arguments = {**arguments, "prompt_id": int(pid)}
    result = handler(app, arguments)
    # The handler's return value (a plain dict) → JSON text content.
    # `default=str` is the same fallback the diagnostic log uses for
    # any non-JSON-native types in the result.
    return {
        "content": [
            {"type": "text", "text": json.dumps(result, default=str)},
        ],
    }


_METHOD_HANDLERS = {
    "push_prompt":  _h_push_prompt,
    "get_result":   _h_get_result,
    "list_pending": _h_list_pending,
    "claim_next":   _h_claim_next,
    "post_result":  _h_post_result,
    "status":       _h_status,
    # MCP lifecycle — see "MCP lifecycle handlers" block above.
    "initialize":   _h_initialize,
    "tools/list":   _h_tools_list,
    "tools/call":   _h_tools_call,
}


# Methods that are NOTIFICATIONS — the client expects no response, the
# server MUST NOT send one. The /mcp dispatcher returns HTTP 202 with
# an empty body when the inbound method lands here. Membership in this
# set is independent of _METHOD_HANDLERS: a method that's recognised
# as a notification has its handler invoked for the side effect (e.g.
# logging) but no JSON-RPC envelope is built.
_NOTIFICATION_HANDLERS = {
    "notifications/initialized": _h_notifications_initialized,
}
