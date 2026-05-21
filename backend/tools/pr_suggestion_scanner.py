"""
tools/pr_suggestion_scanner.py — Suggested Resolutions Commit 2/7.

When a pull_request webhook event arrives with action=closed and
merged=true, this module:

  1. Scans the PR body AND every commit message for failure references
     in five formats (case-insensitive):
       Resolves failure #N
       Fixes failure #N
       Addresses failure #N
       Closes failure #N
       failure #N        (the bare form — last to match so the prefixed
                          forms get their canonical "matched_on" text)
  2. For each unique failure ID matched, verifies a test_results row
     with that id exists AND is still unresolved (resolved_at IS NULL).
  3. Inserts a pr_suggestions row with state pending_review. Webhook
     idempotency is handled by the UNIQUE (failure_report_id, pr_number)
     constraint from migration 026 + ON CONFLICT DO NOTHING here.

A non-existent or already-resolved failure id is skipped silently
with a log warning — the webhook doesn't fail on a bad reference.

The scanner is import-light and synchronous (the regex parse happens
on the request hot-path); the DB INSERT is awaited. parse_pr_payload
is exported so the webhook handler in main.py can call it and the
test suite can exercise the parse without spinning up Postgres.
"""
from __future__ import annotations

import re
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# Five reference formats. Case-insensitive. The capture group always
# pulls the failure-id integer. Bare `failure #N` ALSO matches inside
# the prefixed forms, so we apply the prefixed regex FIRST and only
# fall through to the bare form for any IDs the prefixed scan missed.
_PREFIXED_REF_RE = re.compile(
    r"\b(?:resolves|fixes|addresses|closes)\s+failure\s+#(\d+)\b",
    re.IGNORECASE,
)
_BARE_REF_RE = re.compile(
    r"\bfailure\s+#(\d+)\b",
    re.IGNORECASE,
)


def parse_pr_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """
    Extracts PR metadata + scans for failure references. Returns the
    structured shape the webhook handler stores on pr_suggestions, or
    None when the payload does NOT represent a closed+merged PR (a
    `closed` event for a non-merged PR is silently ignored; the
    handler returns 200 either way).

    Returned shape on a merged PR:
      {
        "pr_number":   int,
        "pr_title":    str,
        "pr_url":      str,
        "pr_merged_at": str (ISO),
        "pr_author":   str | None,
        "commit_shas": list[str],
        "matches": [
            {"failure_id": int, "matched_on": str},
            ...
        ],
      }

    Note `matches` is a list of {failure_id, matched_on} — one entry
    per UNIQUE failure id found across body + commit messages. The
    matched_on text is the exact reference line that surfaced that
    failure id, used by the review modal to render the citation.
    """
    if payload.get("action") != "closed":
        return None
    pr = payload.get("pull_request") or {}
    if not pr.get("merged"):
        return None

    pr_number = int(pr.get("number") or 0)
    if pr_number <= 0:
        log.warning("pr_suggestion_scanner_missing_pr_number")
        return None

    pr_title = str(pr.get("title") or "")[:500]
    pr_url = str(pr.get("html_url") or pr.get("url") or "")[:500]
    pr_merged_at = str(pr.get("merged_at") or "")
    user = pr.get("user") or {}
    pr_author = str(user.get("login") or "")[:100] or None
    body = str(pr.get("body") or "")

    # GitHub doesn't include commit messages in the pull_request payload
    # by default; the `commits_url` field points at a separate API call.
    # The webhook handler resolves that on demand via tools.github_sync;
    # the scanner accepts the resolved list here (passed in `commits`).
    # If absent, we still scan the body.
    commits_list = payload.get("__commits") or []
    commit_shas: list[str] = [
        str(c.get("sha")) for c in commits_list if c.get("sha")
    ]

    # Build the search corpus: PR body lines + every commit message
    # line. Keep per-line so matched_on cites the EXACT line, not a
    # giant blob.
    lines: list[str] = []
    if body:
        lines.extend(body.splitlines())
    for c in commits_list:
        message = c.get("commit_message") or c.get("message") or ""
        if message:
            lines.extend(str(message).splitlines())

    matches: dict[int, str] = {}  # failure_id → matched_on (first seen wins)

    for line in lines:
        # Prefixed format first — produces the canonical citation.
        for m in _PREFIXED_REF_RE.finditer(line):
            fid = int(m.group(1))
            matches.setdefault(fid, line.strip()[:500])
    # Second pass — bare `failure #N` references for IDs the prefixed
    # scan didn't find. Skipping IDs already matched avoids
    # overwriting a "Resolves failure #3" citation with a later "see
    # failure #3" prose mention.
    for line in lines:
        for m in _BARE_REF_RE.finditer(line):
            fid = int(m.group(1))
            if fid not in matches:
                matches.setdefault(fid, line.strip()[:500])

    return {
        "pr_number":    pr_number,
        "pr_title":     pr_title,
        "pr_url":       pr_url,
        "pr_merged_at": pr_merged_at,
        "pr_author":    pr_author,
        "commit_shas":  commit_shas,
        "matches": [
            {"failure_id": fid, "matched_on": matched_on}
            for fid, matched_on in matches.items()
        ],
    }


async def record_pr_suggestions(parsed: dict[str, Any]) -> dict[str, Any]:
    """
    Inserts a pr_suggestions row per matched failure id. Verifies each
    failure exists AND is unresolved before inserting; non-existent or
    already-resolved IDs are skipped with a warning log.

    Returns a summary dict:
      {created: list[int (failure_ids)],
       skipped_missing: list[int],
       skipped_resolved: list[int],
       skipped_duplicate: list[int]}

    Fail-open at the table level — a database error logs and continues
    so a transient DB hiccup never poisons the webhook response.
    """
    out: dict[str, list[int]] = {
        "created": [],
        "skipped_missing": [],
        "skipped_resolved": [],
        "skipped_duplicate": [],
    }
    if not parsed.get("matches"):
        return out

    try:
        import json
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            log.warning("pr_suggestion_scanner_no_db")
            return out
        async with AsyncSessionLocal() as session:
            for match in parsed["matches"]:
                fid = int(match["failure_id"])
                # Lookup — the failure must exist AND be unresolved.
                # A row that's already been resolved (by an earlier PR
                # or a manual modal resolution) needs no suggestion.
                row = await session.execute(text("""
                    SELECT resolved_at FROM test_results WHERE id = :id
                """), {"id": fid})
                found = row.fetchone()
                if not found:
                    log.warning("pr_suggestion_skipped_missing_failure",
                                failure_id=fid,
                                pr_number=parsed["pr_number"])
                    out["skipped_missing"].append(fid)
                    continue
                if found[0] is not None:
                    log.warning("pr_suggestion_skipped_already_resolved",
                                failure_id=fid,
                                pr_number=parsed["pr_number"])
                    out["skipped_resolved"].append(fid)
                    continue

                # INSERT with ON CONFLICT DO NOTHING — the UNIQUE
                # constraint from migration 026 makes a redelivered
                # webhook a silent no-op.
                result = await session.execute(text("""
                    INSERT INTO pr_suggestions
                        (failure_report_id, pr_number, pr_title, pr_url,
                         pr_merged_at, pr_author, matched_commit_shas,
                         matched_on, suggestion_state)
                    VALUES
                        (:fid, :pr_number, :pr_title, :pr_url,
                         :pr_merged_at, :pr_author, :shas,
                         :matched_on, 'pending_review')
                    ON CONFLICT (failure_report_id, pr_number)
                    DO NOTHING
                    RETURNING id
                """), {
                    "fid": fid,
                    "pr_number": parsed["pr_number"],
                    "pr_title": parsed["pr_title"],
                    "pr_url": parsed["pr_url"],
                    "pr_merged_at": parsed["pr_merged_at"],
                    "pr_author": parsed["pr_author"],
                    "shas": json.dumps(parsed["commit_shas"]),
                    "matched_on": match["matched_on"],
                })
                inserted_id = result.scalar()
                if inserted_id is None:
                    # ON CONFLICT path — a suggestion for this
                    # (failure, PR) pair already exists. Treat as
                    # idempotent no-op, not a failure.
                    out["skipped_duplicate"].append(fid)
                else:
                    out["created"].append(fid)
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("pr_suggestion_record_failed",
                    pr_number=parsed.get("pr_number"),
                    error=str(exc))
    return out
