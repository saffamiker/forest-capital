---
description: Pull the next pending mobile prompt from mcp-bridge and execute it in this session
---

You are the live Claude Code session acting as the consumer for the
local mcp-bridge service. The mobile claude.ai client has pushed
prompts onto a queue; your job is to claim the next pending prompt,
execute it here (with full file-state and tool access), and post
the result back so the mobile chat receives the answer.

Workflow:

1. **Claim the next prompt.** Make an MCP `claim_next` call against
   the local bridge with `claimed_by: "live"`. The response carries
   `prompt.prompt` (the text the mobile sent) and `prompt.id` (the
   queue row id). When `prompt` is null, the queue is empty — say
   "No pending prompts." and stop.

2. **Execute the prompt** in this session. Treat the prompt text
   as if Michael had typed it directly. Use any tools available
   (Read, Edit, Bash, etc.) as needed.

3. **Post the result back.** When the work is complete, call MCP
   `post_result` with the prompt id and a `result` field containing
   the answer the mobile should see. Keep the result string focused
   — the mobile chat will display it verbatim.

4. **Stop after one prompt.** Even if more prompts are queued, do
   not loop without explicit instruction. Michael invokes this
   command again when he wants the next one.

If any step fails, call `post_result` with the `error` field set to
the failure description so the mobile sees a usable error instead
of a hang. Examples of errors that should round-trip rather than
silently fail: tool permission denied, file not found, ambiguous
prompt that needs clarification.

Setup prerequisite: this command requires the `mcp-bridge` MCP
server to be registered with Claude Code via `claude --mcp-config`
or `.mcp.json`. See `mcp-bridge/README.md` for the registration
snippet.
