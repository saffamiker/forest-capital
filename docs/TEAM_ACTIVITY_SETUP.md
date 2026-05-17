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
