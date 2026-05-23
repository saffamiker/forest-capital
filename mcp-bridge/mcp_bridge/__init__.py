"""mcp-bridge — Claude.ai mobile ↔ Claude Code desktop relay.

A lightweight local MCP server that exposes a job queue to Claude.ai
(via remote MCP) so prompts pushed from the mobile chat reach Claude
Code running on Michael's desktop without copy-paste or Teamviewer.

Two consumer modes ship together:
  1. Manual fetch — Claude Code's slash command `/check-mobile`
     pulls the next pending prompt, executes it in the LIVE session
     (so file state and context are shared with whatever Michael
     was doing), and posts the result back via the bridge tools.
  2. Autonomous worker — a separate Python daemon polls the queue
     and shells out to `claude -p` for each pending prompt
     (--resume keeps context across prompts). Off by default —
     enable via `worker_enabled` in the config.

See README.md for setup, tunnel options, and claude.ai connector
registration.
"""

__version__ = "0.1.0"
