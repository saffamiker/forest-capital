"""
tools/github_sync.py

GitHub commit ingestion for the Team Activity feature — two paths into
commit_activity:

  1. The push webhook (real-time). GitHub POSTs every push; we validate
     the HMAC-SHA256 signature, then parse the commits out of the
     payload. Webhook payloads carry per-commit file lists but no line
     counts, so insertions/deletions arrive null and are filled later.

  2. The manual sync (backfill / catch-up). Pulls the recent commit
     list from the GitHub REST API, then fetches each commit's detail
     for the additions/deletions stats. Because commit_activity upserts
     on sha, a sync after a webhook simply enriches the same rows.

The repository is private, so the REST calls require a token
(GITHUB_TOKEN). The webhook needs no token — it is authenticated by the
shared-secret signature instead.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

try:
    import structlog
    log = structlog.get_logger(__name__)
except ImportError:  # pragma: no cover
    log = logging.getLogger(__name__)  # type: ignore[assignment]

_GITHUB_API = "https://api.github.com"


# ── Webhook signature ─────────────────────────────────────────────────────────

def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """
    Validates a GitHub webhook's X-Hub-Signature-256 header — an
    HMAC-SHA256 of the raw request body keyed by the shared secret.

    Returns False (never raises) on a missing secret, a missing or
    malformed header, or a digest mismatch. Constant-time comparison
    guards against timing attacks.
    """
    if not secret or not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ── Webhook payload parsing ───────────────────────────────────────────────────

def parse_push_payload(payload: dict) -> list[dict]:
    """
    Extracts commit rows from a GitHub push-event payload.

    A push payload's `commits` array gives sha, message, author, url
    and the added/removed/modified file lists — enough for
    files_changed, but not line counts (insertions/deletions stay null
    until a sync enriches the row). Non-push payloads yield [].
    """
    commits_in = payload.get("commits")
    if not isinstance(commits_in, list):
        return []
    ref = str(payload.get("ref", ""))
    branch = ref.rsplit("/", 1)[-1] if ref else "main"

    out: list[dict] = []
    for c in commits_in:
        sha = c.get("id") or c.get("sha")
        if not sha:
            continue
        author = (c.get("author") or {})
        n_files = (
            len(c.get("added") or [])
            + len(c.get("removed") or [])
            + len(c.get("modified") or [])
        )
        out.append({
            "sha": sha,
            "author": author.get("email") or author.get("name") or "unknown",
            "message": c.get("message", ""),
            "timestamp": c.get("timestamp"),
            "files_changed": n_files or None,
            "insertions": None,   # not in the webhook payload
            "deletions": None,
            "github_url": c.get("url"),
            "branch": branch,
        })
    return out


# ── REST API sync ─────────────────────────────────────────────────────────────

async def fetch_recent_commits(
    repo: str, token: str, limit: int = 100,
) -> list[dict]:
    """
    Fetches the most recent `limit` commits from the GitHub REST API,
    each enriched with its additions/deletions/files-changed stats.

    The list endpoint carries no stats, so each commit's detail is
    fetched concurrently (bounded) for the numbers the timeline shows.
    Raises RuntimeError with a clear message when the token is missing
    or the API rejects the request — the caller surfaces it as JSON.
    """
    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN is not set — the repository is private, so the "
            "commit sync needs a personal access token to read it."
        )
    import asyncio
    import httpx

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        resp = await client.get(
            f"{_GITHUB_API}/repos/{repo}/commits",
            params={"per_page": min(limit, 100)},
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"GitHub API returned {resp.status_code} for {repo}/commits "
                f"— check GITHUB_TOKEN scope and repository access."
            )
        listing = resp.json()

        sem = asyncio.Semaphore(8)

        async def _detail(item: dict) -> dict:
            sha = item.get("sha", "")
            commit = item.get("commit", {}) or {}
            author = commit.get("author", {}) or {}
            row = {
                "sha": sha,
                "author": author.get("email") or author.get("name") or "unknown",
                "message": commit.get("message", ""),
                "timestamp": author.get("date"),
                "files_changed": None,
                "insertions": None,
                "deletions": None,
                "github_url": item.get("html_url"),
                "branch": "main",
            }
            async with sem:
                try:
                    d = await client.get(f"{_GITHUB_API}/repos/{repo}/commits/{sha}")
                    if d.status_code == 200:
                        detail = d.json()
                        stats = detail.get("stats", {}) or {}
                        row["insertions"] = stats.get("additions")
                        row["deletions"] = stats.get("deletions")
                        files = detail.get("files")
                        if isinstance(files, list):
                            row["files_changed"] = len(files)
                except Exception as exc:  # noqa: BLE001
                    # A single detail failure must not abort the whole sync —
                    # the row still upserts with null stats.
                    log.warning("github_commit_detail_failed", sha=sha[:7],
                                error=str(exc))
            return row

        rows = await asyncio.gather(*[_detail(it) for it in listing])
    return list(rows)


async def fetch_pr_commits(
    repo: str, token: str, pr_number: int,
) -> list[dict]:
    """
    Fetches the list of commits on one PR for the Suggested Resolutions
    scanner (PR-driven workflow, Commit 2/7). Returns a list of
    {sha, commit_message} dicts — the scanner only needs SHAs + messages.

    Fail-open: any HTTP / auth / parse error returns []. The body-only
    scan still surfaces references, so a fetch failure degrades
    gracefully — a PR that follows the CLAUDE.md "Resolves failure #N"
    convention in its body works whether or not commit-message coverage
    is available.

    Missing-token returns [] silently rather than raising — the webhook
    handler should never 500 because the operator hasn't configured
    GITHUB_TOKEN for the optional commit-scan layer.
    """
    if not token:
        log.info("pr_commits_no_token",
                 note="commit-message scan skipped; body-only scan proceeds")
        return []
    import httpx

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            resp = await client.get(
                f"{_GITHUB_API}/repos/{repo}/pulls/{int(pr_number)}/commits",
                params={"per_page": 100},
            )
            if resp.status_code != 200:
                log.warning("pr_commits_fetch_failed",
                            status=resp.status_code, pr_number=pr_number)
                return []
            listing = resp.json() or []
            return [{
                "sha": item.get("sha"),
                "commit_message":
                    (item.get("commit") or {}).get("message", ""),
            } for item in listing]
    except Exception as exc:  # noqa: BLE001
        log.warning("pr_commits_fetch_exception",
                    pr_number=pr_number, error=str(exc))
        return []
