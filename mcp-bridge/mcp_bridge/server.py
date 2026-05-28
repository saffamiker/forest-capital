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
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import __version__
from .config import BridgeConfig, load_config
from .queue import Queue


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

        On a 401 the response carries a WWW-Authenticate header
        pointing at the protected-resource metadata (RFC 9728). That
        is how an OAuth client (claude.ai) DISCOVERS the authorization
        server when it hits /mcp without a token: it reads the
        resource_metadata URL, fetches the AS metadata, and runs the
        authorize/token flow. Without the header the OAuth connector
        cannot bootstrap from a bare /mcp call."""
        token = app.state.cfg.auth_token
        if not token:
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
        if provided != token:
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

    # ── MCP — JSON-RPC over HTTP POST /mcp ─────────────────────────────

    @app.post("/mcp")
    async def mcp_endpoint(request: Request,
                            _: None = Depends(require_token)
                            ) -> JSONResponse:
        """Streamable-HTTP MCP entry point. The body is a JSON-RPC
        2.0 envelope; the response is the JSON-RPC reply envelope.
        Single requests only — no batching in this minimal cut."""
        try:
            raw = await request.body()
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return _rpc_error(None, _RPC_PARSE_ERROR,
                                   "Parse error.")
            if not isinstance(payload, dict):
                return _rpc_error(None, _RPC_INVALID_REQUEST,
                                   "JSON-RPC envelope must be an "
                                   "object.")
            req_id = payload.get("id")
            method = payload.get("method")
            params = payload.get("params") or {}
            if not isinstance(method, str):
                return _rpc_error(req_id, _RPC_INVALID_REQUEST,
                                   "method must be a string.")
            if not isinstance(params, dict):
                return _rpc_error(req_id, _RPC_INVALID_PARAMS,
                                   "params must be an object.")

            handler = _METHOD_HANDLERS.get(method)
            if handler is None:
                return _rpc_error(req_id, _RPC_METHOD_NOT_FOUND,
                                   f"Unknown method '{method}'.")
            try:
                result = handler(app, params)
            except HTTPException as exc:
                # Re-shape FastAPI auth/validation errors into
                # JSON-RPC errors so the client gets a consistent
                # envelope regardless of failure site.
                return _rpc_error(req_id, _RPC_TOOL_ERROR,
                                   str(exc.detail))
            except ValueError as exc:
                return _rpc_error(req_id, _RPC_INVALID_PARAMS,
                                   str(exc))
            return JSONResponse({
                "jsonrpc": "2.0",
                "id":      req_id,
                "result":  result,
            })
        except Exception as exc:  # noqa: BLE001
            # Last-resort guard — every uncaught exception comes
            # back as a structured RPC error rather than a 500
            # HTML page. The bridge stays usable from mobile.
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


_METHOD_HANDLERS = {
    "push_prompt":  _h_push_prompt,
    "get_result":   _h_get_result,
    "list_pending": _h_list_pending,
    "claim_next":   _h_claim_next,
    "post_result":  _h_post_result,
    "status":       _h_status,
}
