# Team Activity — Deployment Setup

The Team Activity feature has two operator steps that cannot be done
from a local dev machine: registering the GitHub push webhook and
backfilling commit history. Both are one-time, post-deploy actions.

The backend code (the webhook receiver, the sync endpoint, the
activity tables) ships in commits 1–8b and needs no further action —
only the steps below to start the commit feed.

---

## 1. Environment variables on Render

Set these three on the backend service (Render → service → Environment):

| Variable | Value | Purpose |
|----------|-------|---------|
| `GITHUB_REPO` | `saffamiker/forest-capital` | Repo the sync + webhook target (already the default). |
| `GITHUB_TOKEN` | a GitHub personal access token with `repo` scope | The repository is **private**, so `GET /api/v1/activity/commits/sync` needs a token to read its commits. |
| `GITHUB_WEBHOOK_SECRET` | a 64-char hex string (see below) | Validates the `X-Hub-Signature-256` on every incoming webhook. **The webhook endpoint rejects every event until this is set.** |

Generate the webhook secret:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Use the **same value** in the Render env var and in the `gh` command in
step 2. Treat it like a password — it is never committed to the repo
(`.env.example` carries only a placeholder).

---

## 2. Register the GitHub push webhook

After `GITHUB_WEBHOOK_SECRET` is set on Render, register the webhook on
the repository. This requires `gh` authenticated with the `admin:repo_hook`
scope (`gh auth login` / `gh auth refresh -s admin:repo_hook`).

```bash
gh api repos/saffamiker/forest-capital/hooks \
  --method POST \
  --field "config[url]=https://forest-capital.onrender.com/api/v1/activity/commits/webhook" \
  --field "config[content_type]=json" \
  --field "config[secret]=<GITHUB_WEBHOOK_SECRET>" \
  --field "events[]=push" \
  --field "active=true"
```

Replace `<GITHUB_WEBHOOK_SECRET>` with the value from step 1.

GitHub sends a `ping` event immediately on registration — the endpoint
acknowledges and ignores it (`{"status":"ignored"}`). The next real
push delivers commits into `commit_activity`.

**Verify:** push a commit, then check the Render logs for
`activity_webhook_push` — or open Team Activity in the Reports view and
confirm the commit appears in the timeline.

---

## 3. Backfill historical commits

The webhook only delivers commits made *after* it is registered. To
populate `commit_activity` with the project's full history, call the
manual sync endpoint once after deploy. It pulls the last 100 commits
from the GitHub REST API and **upserts on SHA**, so it is safe to run
repeatedly — re-running it never duplicates a row and also catches up
anything the webhook missed.

Authenticated as any signed-in team member:

```bash
curl -H "X-API-Key: <your-session-token>" \
  https://forest-capital.onrender.com/api/v1/activity/commits/sync
```

A successful run returns `{"synced": <n>, "fetched": <n>}`. If
`GITHUB_TOKEN` is not set the response is
`{"synced": 0, "error": "GITHUB_TOKEN is not set …"}` — set the token
(step 1) and retry.

The first sync fetches each commit's detail for the
additions/deletions/files-changed stats, so it takes ~15–40 seconds
for 100 commits; later syncs are faster as unchanged rows simply
re-upsert.

**Verify:** open Team Activity in the Reports view — the timeline and
the "Activity over time" chart should show the historical commits,
attributed to Michael Ruurds (his git author email resolves through
`GIT_AUTHOR_EMAIL_MAP` to his platform identity).

