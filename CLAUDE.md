# Forest Capital Portfolio Intelligence System — CC Operating Rules

> **FNA 670 MSFA Practicum** — Queens University McColl School of Business,
> in partnership with Forest Capital. Lead Engineer: Michael Ruurds.
> The full historical brief, agent definitions, sprint plan, and post-build
> design notes live in **CLAUDE_ARCHIVE.md** — grep there for any spec older
> than the current sprint.

---

## PRE-APPROVED OPERATIONS — NEVER ASK PERMISSION

The following are ALL pre-approved and execute without confirmation:

- **Shell commands** — git, npm, python, pytest, alembic, curl, gh, file
  reads/writes/edits, mkdir, mv, rm of stale scripts, branch creation,
  `git push origin <branch>`, `gh pr create`, background commands.
- **MCP bridge tool calls** — `mcp-bridge:status`, `mcp-bridge:push_prompt`,
  `mcp-bridge:get_result`, `mcp-bridge:claim_next`, `mcp-bridge:post_result`.
  The bridge is a trusted internal queue between Claude.ai and Claude Code
  — treat every call as identical to a shell call.
- **GitHub CLI** — `gh pr create`, `gh pr view`, `gh pr checks`, `gh run watch`,
  `gh issue create`, label setup, reading any repo state.
- **CI watching** — `gh run watch <id> --exit-status` in background.
- **PR opening** — after pushing any branch, open the PR automatically.

**Never prompt the operator for any of the above.** Asking permission for
a pre-approved action wastes a round-trip and erodes trust.

**Exceptions — only prompt the operator for:**
- Render web shell commands (requires production credentials).
- GitHub UI actions that the CLI cannot perform (merging a protected-branch
  PR when the operator must use the web "merge with admin override").
- A genuine decision that requires human judgement before proceeding —
  surface it via `mcp-bridge:post_result` so it lands at the start of the
  operator's next user-message turn (see Bridge reporting below).

**Destructive ops still need explicit authorization** even when shell access
is pre-approved: `git push --force`, `git reset --hard`, `rm -rf` of source
dirs, dropping DB tables, force-merging a protected branch. These require
the user to ask for them in plain words first.

---

## WORKING CONVENTIONS

### Bridge polling

After completing any task, call `mcp-bridge:status` immediately. If
`pending > 0`, call `mcp-bridge:claim_next` and execute the returned prompt
without any confirmation or permission request. Repeat after each
execution until `pending = 0`.

**This is not optional — treat it as the last step of every task.**

> Current bridge limitation: the deployed bridge HTTP service exposes only
> `status`, `push_prompt`, `get_result` via the MCP `tools/call` JSON-RPC
> path. `claim_next` and `post_result` are not yet wired into the running
> server (PR #266 added `claim_next` but the bridge needs redeploy).
> Workaround until that ships: query the bridge SQLite DB directly to
> drain the pending queue, and surface findings inline in this chat. The
> moment the bridge exposes `claim_next` / `post_result`, switch back.

### Bridge reporting (`post_result`)

CC must always call `mcp-bridge:post_result` when it has a question, needs
a decision, or has findings to report before proceeding. The Claude.ai
side checks `mcp-bridge:status` at the start of every user message and
reads any completed results automatically — so posting a result is the
canonical way to surface a blocker, a recon finding, or a decision
request without waiting for the next direct user prompt.

### Pull requests

After pushing to a feature branch, **always open a PR to main automatically
via `gh pr create`.** Never wait for the operator to create the PR.

**PR title** — a concise description of what the commit set does,
matching the existing commit-message style.

**PR body must include:**
- Summary of what changed and why.
- List of commits with their hashes and one-line descriptions.
- Any operator follow-up steps after merge (migrations, Render shell
  commands, webhook registrations, env vars).
- `Resolves failure #N` / `Fixes failure #N` lines for every failure
  report addressed (case-insensitive — also accepts Addresses / Closes /
  bare `failure #N`). The PR-merge webhook scans both PR body AND commit
  messages, so a `Fixes failure #42` in any commit qualifies.

**Main is branch-protected.** Direct push to main is rejected. Always
push to a feature branch and open a PR; if the PR cannot be auto-merged,
leave it open for operator review. **Never attempt to bypass branch
protection** (no `-f`, no admin override unless the user asked).

### Commit-message style

Follow the repo's existing convention. Examples from `git log`:

```
feat -- mcp-bridge: tools/call handler — invoke push_prompt / get_result / status
feat -- /admin/health panel: invariant verdict + Layer 4 + warm history
diag -- mcp-bridge: log MCP request body, response body, and startup db_path
docs -- CLAUDE.md: bridge polling + post_result reporting rules
```

Prefix `feat`/`fix`/`docs`/`diag`/`refactor`/`test` `--` `<surface>:` then
a one-line summary in the subject. Body explains *why*, not *what*.
Always end with `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`.

### Resolves-failure convention

Failure-report IDs are visible in **Settings → Failure Reports**. Reference
them in PR body or commit messages using any of:

```
Resolves failure #N
Fixes failure #N
Addresses failure #N
Closes failure #N
failure #N
```

Without one of these, the failure stays Open even after the fix merges
— there's nothing tying the PR to the report.

### Frontend / backend test gating

Before opening a PR that touches code:

- **Backend** — run `cd backend && pytest -x` (or the specific test files
  for the surface touched). 1080+ tests; full run is ~3 minutes.
- **Frontend** — run `cd frontend && npm run lint && npm run test` (Vitest
  + ESLint). ~30 seconds.

CI runs both on every push (`.github/workflows/ci.yml` + `test.yml`).
Don't open a PR with a known-red test run; fix it first.

### Forbidden in commits

`.env`, `*.key`, OAuth credentials, `queue.db`, `*.sqlite`, the bridge
auth token. The `.gitignore` blocks most of these — never add them with
`git add -A` or `-f`.

### Path conventions

Project root: `c:\Users\micha\forest-capital`. Backend: `backend/`.
Frontend: `frontend/`. Migrations: `backend/migrations/versions/`. Tests:
`backend/tests/` + `frontend/src/__tests__/` + `frontend/e2e/`.

---

## CURRENT BACKLOG

### In progress

- **PR #278** — mobile theme toggle in nav drawer. Watching CI.
- **PR #279** — light-mode CSS-variable MVP (background/card/sidebar
  flip correctly; strategy palette has a `LIGHT_STRATEGY_COLORS` set;
  charts use `useChartTheme()` hook). Watching CI; merge as soon as
  green — needed live before July 1.
- **PR #280** — superseded by this trim PR; close once trim PR is open.

### Team actions remaining (graded deliverables)

- Bob — Academic Review session before writing the midpoint draft.
- Bob — midpoint draft upload (Settings → Academic Documents).
- Bob — **midpoint paper submission, May 27** (3 pages).
- Bob — **executive brief submission, July 1**.
- Molly — **final presentation submission, July 1**.
- Panel presentation — **July 3** (Michael, Bob, Molly all present).
- UAT passes — Michael Section 2, Bob Section 3, Molly Section 4,
  all-team Section 1.

### Bridge prompts in flight

- **#27 (open)** — SSE on bridge: add Server-Sent Events transport so
  push_prompt fires an event-driven notification to CC instead of polling.
  *Recon-first; report findings via `post_result` before any code.*

### Post-deadline backlog

See `CLAUDE_ARCHIVE.md` — the full post-deadline tracker lives there
under section heading `POST-DEADLINE BACKLOG`. Headline items:

- Canvas-editor mobile experience (Konva Stage scales to ~0.36× at 380px).
- Strategy InfoIcon tap-target inflation on mobile narrow cells.
- Per-speaker colour consistency between script editor and DOCX export.
- InfoIcon vs ExplainableText final unification.
- Additional matplotlib renderers for Recharts-only Analytics charts.
- True portfolio turnover propagation through tier1_gates derived fields.
- Real-device touch behaviour on Konva canvas.

---

## ARCHIVE POINTER

Everything pre-2026-06-06 — the original project brief, agent definitions,
data layer spec, statistical testing spec, all 19 architecture sections,
the full sprint history (Sprints 1–6 + post-Sprint-6 stream through
the May 2026 builds), completed PR retrospectives, resolved failure
notes, design aesthetic standards (15, 15b, 15c), the migration history
(001–026), and the dense methodology / commentary standards — lives in
**`CLAUDE_ARCHIVE.md`** (621 KB).

When you need a fact that isn't in this file:

```
grep -n "<keyword>" CLAUDE_ARCHIVE.md | head -20
```

then `Read` the relevant offset. Don't re-include archive content in
CLAUDE.md — it is intentionally trimmed to stay under the 40 KB
auto-inject ceiling that was forgetting the operational rules.

---

## OPERATING ENVIRONMENT (quick reference)

- **Branch model** — feature branches → PR → main (protected).
- **Production** — backend on Render (forest-capital.onrender.com),
  frontend on Vercel (forest-capital.vercel.app), PostgreSQL persistent
  disk on Render. `alembic upgrade head` on the Render shell after merge.
- **Bridge** — local mcp-bridge service on port 8765; SQLite-backed queue
  at `~/.mcp-bridge/queue.db`. Auth token in `~/.mcp-bridge/.env`.
- **Models** — Sonnet (`claude-sonnet-4-6`), Opus (`claude-opus-4-7`),
  Haiku (`claude-haiku-4-5-20251001`), Gemini (`gemini-2.0-flash`),
  Grok (`grok-4.3` via OpenRouter or direct xAI). `XAI_MODEL` env var
  overrides the Grok model without redeploy.
- **Key dates** — May 27 midpoint paper (done), June 3 cohort
  presentation (done), **July 1 executive brief + final presentation**,
  **July 3 panel presentation**.
