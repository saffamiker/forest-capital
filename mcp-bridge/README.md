# mcp-bridge — Claude.ai mobile ↔ Claude Code desktop relay

A lightweight local MCP server that lets you push prompts from
**Claude.ai on your phone** into **Claude Code on your desktop**
without copy-paste or TeamViewer. The desktop executes the prompt;
the result returns to the mobile chat.

```
Mobile claude.ai
     │  push_prompt("…")             via remote MCP (token-gated)
     ▼
Local mcp-bridge (FastAPI + SQLite queue) — runs on your desktop
     │  list_pending / claim_next / post_result
     ▼
Live Claude Code session (default)     OR   Autonomous worker daemon
  /check-mobile slash command                claude -p "…" --resume <session>
  executes in the live session with          runs each queued prompt
  full file state and tool access            hands-off
```

Two consumer modes ship in the same binary:

| Mode | Default | How prompts get executed |
| --- | --- | --- |
| **Manual fetch** | ON | The live Claude Code session calls `/check-mobile`. Execution happens in that session — full file state, full context, all tools. Michael triggers it when ready. |
| **Autonomous worker** | OFF | A separate Python daemon polls the SQLite queue and shells out to `claude -p "<prompt>" --resume <session>` for every pending prompt. Enable explicitly via `worker_enabled: true`. |

Switching modes is a single config-file edit — no code change. Both
modes coexist; flipping `worker_enabled` on doesn't break manual
fetch (the live session can still claim prompts the daemon hasn't
got to yet).

---

## Quick start (manual mode, 5 minutes)

```bash
cd mcp-bridge
pip install -r requirements.txt

# 1. Generate config + auth token
python -m mcp_bridge init
#   → prints the bearer token. Save it.

# 2. Start the server (binds to 127.0.0.1:8765 by default)
python -m mcp_bridge serve

# 3. From another terminal — push a test prompt
python -m mcp_bridge push "what's 2+2"
#   → {"prompt_id": 1, "status": "pending"}

python -m mcp_bridge status
#   → {... "counts": {"pending": 1, ...}}
```

The bridge is live and the queue is working. To wire it up to
Claude Code as a consumer, see [Registering with Claude Code](#registering-with-claude-code).
To reach the bridge from mobile claude.ai, see
[Exposing via a tunnel](#exposing-via-a-tunnel).

---

## Registering with Claude Code

Claude Code consumes MCP servers via `.mcp.json` in the project
root or via `claude --mcp-config`. Add the bridge as an entry:

```json
{
  "mcpServers": {
    "bridge": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp",
      "headers": {
        "Authorization": "Bearer <paste-the-token-from-init>"
      }
    }
  }
}
```

Then drop the slash command file into your project's `.claude/commands/`
directory:

```bash
cp mcp-bridge/commands/check-mobile.md ~/.claude/commands/check-mobile.md
# or per-project:
cp mcp-bridge/commands/check-mobile.md .claude/commands/check-mobile.md
```

Inside any Claude Code session, run `/check-mobile`. Claude Code
will use the bridge's MCP tools to pull the next pending prompt,
execute it in the live session, and post the result back. The
mobile chat picks up the result on its next poll.

---

## Exposing via a tunnel

Mobile claude.ai cannot reach `localhost:8765` on your desktop
directly. Two operator-side tunnel options work; pick one.

### ngrok (one command, ephemeral URL)

```bash
ngrok http 8765
#   → forwards https://<random>.ngrok-free.app → http://localhost:8765
```

Paste the ngrok HTTPS URL into the Claude.ai connector
configuration. The URL changes every restart on the free tier —
fine for ad-hoc use, painful for daily.

### cloudflared (stable URL, no inbound firewall hole)

```bash
cloudflared tunnel --url http://localhost:8765
#   → forwards a stable subdomain
```

A paid Cloudflare Tunnel gives you a custom subdomain that
survives restarts. Recommended for routine use.

**Security note**: the moment the bridge is reachable from the
public internet, the bearer token is the only thing standing
between an attacker and prompt execution on your desktop. The CLI
`serve` command refuses to bind to anything other than `127.0.0.1`
when `auth_token` is empty — but if you set a token AND bind to
`0.0.0.0`, you ARE exposed; the only guard is the token. Use a
long random token (the `init` command generates 32 bytes
URL-safe), rotate it after every demo, and never commit it to a
repo. If you suspect compromise: `python -m mcp_bridge init` to
generate a fresh one.

---

## Registering with Claude.ai (mobile)

Claude.ai accepts remote MCP servers under
**Settings → Connectors → Add custom MCP server**. The current UI
registers the server via **OAuth 2.1** — it has Client ID and Client
Secret fields, not an Authorization-header field. The bridge ships a
minimal OAuth 2.1 shim (added May 2026) so the connector flow
completes.

Configure:

| Field | Value |
| --- | --- |
| Name | `mcp-bridge` |
| URL / Server URL | `https://<tunnel-url>/mcp` |
| Client ID | the `oauth_client_id` printed by `bridge init` |
| Client Secret | the `oauth_client_secret` printed by `bridge init` |

claude.ai discovers the `/authorize` and `/token` endpoints
automatically from the bridge's discovery metadata
(`/.well-known/oauth-authorization-server` and
`/.well-known/oauth-protected-resource`). You do NOT paste a bearer
token into the connector — the OAuth flow issues it. Under the hood
the token claude.ai receives from `/token` IS the bridge's
`auth_token`, so every `/mcp` call it makes is validated by the same
bearer check the CLI uses.

> **How the OAuth shim works.** `/authorize` auto-approves (a
> single-user desktop bridge has no second user to consent for) and
> mints a short-lived, single-use, PKCE-bound authorization code.
> `/token` validates the Client ID + Secret and the PKCE verifier,
> then returns the `auth_token` as the access token. The security
> boundary is the Client Secret, the issued bearer token, and the
> tunnel — exactly the same surface as the bearer-only mode, with an
> OAuth front door bolted on so claude.ai's UI accepts it.
>
> **Bearer-header mode still works** for the desktop Claude Code
> side (`.mcp.json`) and the CLI: set
> `Authorization: Bearer <auth_token>` directly. The OAuth shim is
> only needed for claude.ai's connector UI. Leaving
> `oauth_client_id` empty disables the OAuth endpoints (bearer-only,
> the pre-OAuth behaviour).

Once connected, the mobile chat can call the bridge's tools:

- `push_prompt(prompt, session_id?)`
- `get_result(prompt_id)`
- `status()`

Tell mobile Claude to push your prompt:

> *"Use the mcp-bridge tool to push this prompt to my desktop:
> 'Run the full QA audit and tell me which checks failed.' Then
> poll for the result."*

The mobile session pushes via `push_prompt`, polls `get_result`
every few seconds, and renders the result string when the row
flips to `complete`. The desktop side (manual or worker) executes
the prompt in between.

---

## Enabling the autonomous worker

When you want hands-off execution (stepping away from the desktop,
overnight runs, demo recordings), enable the worker:

```bash
# Edit ~/.config/mcp-bridge/config.json
{
  "auth_token": "…",
  "worker_enabled": true,
  "worker_session_id": "<optional persistent session id>"
}

# Then start the daemon in a separate terminal
python -m mcp_bridge worker
```

The daemon polls the queue every `worker_poll_interval_s` seconds
(default 2), claims the next pending prompt, shells out to
`claude -p "<prompt>" --output-format json` (plus
`--resume <session_id>` when configured), and posts the result
back. Errors (timeout, missing binary, non-zero exit) round-trip
to the mobile chat as the row's `error` field — nothing hangs
silently.

`worker_enabled: false` (the default) makes the daemon refuse to
start with exit code 2 and a clear message. The opt-in is
explicit so accidental execution can't happen.

---

## Config reference

`~/.config/mcp-bridge/config.json` accepts the following keys
(every key has a sensible default — the file can carry only the
ones you want to override):

| Key | Default | What it does |
| --- | --- | --- |
| `host` | `127.0.0.1` | Server bind address. |
| `port` | `8765` | Server bind port. |
| `auth_token` | `""` | Bearer token. Empty disables auth (localhost only). |
| `oauth_client_id` | `""` | OAuth client id for the claude.ai connector. Generated by `init`. Empty disables the OAuth shim. |
| `oauth_client_secret` | `""` | OAuth client secret for the claude.ai connector. Generated by `init`. |
| `db_path` | `~/.local/share/mcp-bridge/queue.db` | SQLite file. |
| `worker_enabled` | `false` | Whether the daemon runs. |
| `worker_poll_interval_s` | `2.0` | Daemon poll period. |
| `worker_prompt_timeout_s` | `600` | Per-prompt subprocess timeout. |
| `worker_session_id` | `""` | `--resume` value for context continuity. |
| `claude_binary` | `claude` | Path to the claude CLI. |
| `claude_extra_args` | `[]` | Extra args appended to every invocation. |
| `max_prompt_bytes` | `65536` | Reject mobile pushes above this. |

Every key has a matching env var (`MCP_BRIDGE_<UPPER>`). Env
beats file beats defaults.

---

## CLI reference

```bash
python -m mcp_bridge init                  # generate token + config
python -m mcp_bridge serve [--host H] [--port P]   # run the server
python -m mcp_bridge worker                # run the daemon (opt-in)
python -m mcp_bridge push "<prompt>" \      # enqueue locally
  [--session-id <id>]
python -m mcp_bridge result <id>           # look up a result
python -m mcp_bridge status                # print queue counts
```

`init` and `push` / `result` / `status` are one-shot. `serve` and
`worker` are long-running; run them in separate terminals or a
process manager (systemd, supervisord, screen, tmux).

---

## Tests

```bash
cd mcp-bridge
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

69 tests cover the queue state machine, config precedence, server
auth, full MCP round-trip, every worker failure path (subprocess
mocked — no claude binary required for CI), and the OAuth 2.1
handshake (discovery metadata, the authorize/token flow, PKCE S256
verification, client-secret validation, code replay + expiry).

---

## How a prompt flows end-to-end

A complete trace, manual mode:

1. Michael on mobile: *"Push this to my desktop: rewrite section 2 of the midpoint paper."*
2. Mobile claude.ai calls the bridge's `push_prompt` tool via the registered MCP connector.
3. Bridge writes a `pending` row to `queue.db`, returns `prompt_id: 42`.
4. Mobile claude.ai begins polling `get_result(42)` every 3 seconds.
5. Michael (or a periodic prompt) runs `/check-mobile` in his desktop Claude Code session.
6. The slash command calls `claim_next` → bridge atomically flips row 42 to `running` and returns the prompt.
7. Claude Code executes the prompt in the live session (reads files, edits them, runs tools as needed).
8. When the work is done, Claude Code calls `post_result(42, result="…")`.
9. Bridge flips row 42 to `complete`.
10. Mobile's next `get_result` poll returns the result; chat renders it.

Autonomous mode: skip step 5 — the worker daemon polls the queue,
claims row 42, runs `claude -p "<prompt>"`, posts the result. No
desktop interaction required.

---

## What this is not

- **Not a session-injection tool.** The autonomous worker runs a
  FRESH `claude -p` invocation per prompt. Session continuity is
  best-effort via `--resume <session_id>`. If you need the prompt
  to land in your CURRENTLY OPEN live session, use manual mode
  with `/check-mobile`.
- **Not a multi-user service.** One bridge per desktop. No
  multi-tenant accounting. Sharing a tunnel URL means sharing the
  desktop's full Claude Code authority.
- **Not encrypted in transit.** TLS terminates at the tunnel
  (ngrok / cloudflared). The bridge itself is plain HTTP — fine
  because it only ever binds to localhost or the tunnel's loopback.

---

## License

Part of the forest-capital project (MIT). See the root LICENSE
file.
