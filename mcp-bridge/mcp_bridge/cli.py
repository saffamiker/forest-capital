"""mcp_bridge.cli — operator commands for the bridge.

Six subcommands cover the full operator surface:

  bridge init         Generates a fresh auth token + config file at
                      ~/.config/mcp-bridge/config.json. Print the
                      token so Michael can paste it into the
                      claude.ai connector configuration.
  bridge serve        Runs the FastAPI MCP server.
  bridge worker       Runs the autonomous worker daemon. Refuses
                      to start when worker_enabled is False — the
                      operator must set it explicitly so the
                      daemon is genuinely opt-in.
  bridge push "..."   Pushes a prompt to the local queue and
                      prints the new prompt_id. Useful for
                      verifying the bridge is reachable without
                      the mobile claude.ai trip.
  bridge result <id>  Prints the row for a given prompt_id —
                      status / result / error.
  bridge status       Prints the bridge status snapshot (alive
                      flag, queue counts, last completion).

`bridge serve` is the only long-running subcommand. The others
return immediately so they slot cleanly into shell scripts.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from typing import Any

from .config import config_path, load_config, write_default_config


def _cmd_init(_args: argparse.Namespace) -> int:
    """Generates a fresh URL-safe token, writes the config file,
    prints both so the operator can paste the token into the
    claude.ai connector configuration. Existing config IS
    overwritten — operator can keep the old token by editing the
    file directly instead of running init twice."""
    token = secrets.token_urlsafe(32)
    p = write_default_config(token=token)
    print(f"Wrote {p}")
    print()
    print("Bearer token (paste into the claude.ai MCP connector "
          "Authorization header):")
    print()
    print(f"  {token}")
    print()
    print("Worker daemon is DISABLED by default. To enable hands-off "
          "execution edit the config file:")
    print()
    print('  { "auth_token": "...", "worker_enabled": true }')
    print()
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    """Imports uvicorn lazily so the other subcommands don't pay
    the import cost. Binds to the configured host/port."""
    import uvicorn
    from .server import create_app

    cfg = load_config()
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    if not cfg.auth_token and cfg.host != "127.0.0.1":
        # The one safety the bridge enforces unconditionally:
        # never expose an unauthenticated server beyond localhost.
        print(
            "[mcp-bridge] REFUSING to start — auth_token is empty "
            f"and host is {cfg.host}. Run `bridge init` to "
            f"generate a token, or bind to 127.0.0.1 for a "
            f"localhost-only deploy.",
            file=sys.stderr)
        return 2
    app = create_app(cfg)
    print(f"[mcp-bridge] serving on http://{cfg.host}:{cfg.port}"
          f" (worker_enabled={cfg.worker_enabled})")
    uvicorn.run(app, host=cfg.host, port=cfg.port,
                log_level="info")
    return 0


def _cmd_worker(_args: argparse.Namespace) -> int:
    """Starts the autonomous worker. Refuses when worker_enabled
    is False — the operator must opt in explicitly."""
    from .worker import Worker
    return Worker().run()


def _cmd_push(args: argparse.Namespace) -> int:
    """Local enqueue — pushes via the Queue directly rather than
    HTTP, so this works even when the server isn't running. The
    server will pick the row up on its next read."""
    from .queue import Queue
    cfg = load_config()
    q = Queue(cfg.db_path)
    pid = q.enqueue(args.prompt, session_id=args.session_id)
    print(json.dumps({"prompt_id": pid, "status": "pending"}))
    return 0


def _cmd_result(args: argparse.Namespace) -> int:
    """Local lookup — prints the row JSON or exits non-zero
    when the id is unknown."""
    from .queue import Queue
    cfg = load_config()
    q = Queue(cfg.db_path)
    row = q.get(args.prompt_id)
    if row is None:
        print(f"prompt_id {args.prompt_id} not found.",
              file=sys.stderr)
        return 1
    print(json.dumps(row, indent=2))
    return 0


def _cmd_status(_args: argparse.Namespace) -> int:
    from .queue import Queue
    cfg = load_config()
    q = Queue(cfg.db_path)
    snap: dict[str, Any] = {
        "alive":           True,
        "config_path":     str(config_path()),
        "db_path":         cfg.db_path,
        "worker_enabled":  cfg.worker_enabled,
        **q.status_snapshot(),
    }
    print(json.dumps(snap, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bridge",
        description=(
            "mcp-bridge — Claude.ai mobile to Claude Code desktop "
            "relay. See README.md for setup."))
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init",
                   help="Generate a token + write config file.")

    p_serve = sub.add_parser(
        "serve", help="Run the FastAPI MCP server.")
    p_serve.add_argument(
        "--host", default=None,
        help="Override the bind host. Default 127.0.0.1.")
    p_serve.add_argument(
        "--port", type=int, default=None,
        help="Override the bind port. Default 8765.")

    sub.add_parser(
        "worker",
        help="Run the autonomous worker daemon. "
             "Requires worker_enabled in config.")

    p_push = sub.add_parser(
        "push", help="Enqueue a prompt locally.")
    p_push.add_argument(
        "prompt", help="The prompt text to enqueue.")
    p_push.add_argument(
        "--session-id", default=None,
        help="Optional session id stored with the prompt.")

    p_result = sub.add_parser(
        "result", help="Look up a prompt result by id.")
    p_result.add_argument("prompt_id", type=int)

    sub.add_parser(
        "status",
        help="Print bridge status (queue counts, worker mode).")

    return parser


_HANDLERS = {
    "init":   _cmd_init,
    "serve":  _cmd_serve,
    "worker": _cmd_worker,
    "push":   _cmd_push,
    "result": _cmd_result,
    "status": _cmd_status,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return _HANDLERS[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
