"""
tools/github_labels.py — the triage GitHub label set.

The triage engine tags every issue it opens with a severity label and a
category label. ensure_triage_labels() makes sure those labels exist on
the repository with sensible colours before the first issue is opened.

Fail-open throughout: a missing token, an API error or a network
failure is logged and swallowed. GitHub auto-creates an unknown label
(with a random colour) when an issue references it, so a failure here
only costs the labels their intended colours — never an issue.
"""
from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

# name → 6-hex colour (no leading #). Severity, category and the two
# triage-specific tags the engine and the agent report use.
_TRIAGE_LABELS: dict[str, str] = {
    # category
    "bug":            "d73a4a",
    "enhancement":    "1d76db",
    "ux-issue":       "8b5cf6",
    "question":       "fbca04",
    # severity
    "blocking":       "b60205",
    "major":          "d93f0b",
    "minor":          "0e8a16",
    "trivial":        "ededed",
    # triage tags
    "quick-win":      "22d3ee",
    "post-deadline":  "6a737d",
}

_GITHUB_API = "https://api.github.com"


async def ensure_triage_labels() -> int:
    """
    Creates any of the triage labels missing from the repository.

    Returns the number of labels created (0 when all already exist or on
    any failure). Never raises — every failure mode is logged and
    swallowed so it can never abort a triage run.
    """
    from config import GITHUB_REPO, GITHUB_TOKEN
    if not GITHUB_TOKEN:
        log.info("triage_labels_skipped", reason="GITHUB_TOKEN not set")
        return 0
    try:
        import httpx

        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            resp = await client.get(
                f"{_GITHUB_API}/repos/{GITHUB_REPO}/labels",
                params={"per_page": 100})
            if resp.status_code != 200:
                log.warning("triage_labels_list_failed",
                            status=resp.status_code)
                return 0
            existing = {
                str(lbl.get("name", "")).lower()
                for lbl in resp.json()
            }
            created = 0
            for name, color in _TRIAGE_LABELS.items():
                if name.lower() in existing:
                    continue
                made = await client.post(
                    f"{_GITHUB_API}/repos/{GITHUB_REPO}/labels",
                    json={"name": name, "color": color})
                if made.status_code in (200, 201):
                    created += 1
                else:
                    # A 422 means it already exists (a race) — not an error.
                    log.info("triage_label_create_skipped",
                             label=name, status=made.status_code)
            if created:
                log.info("triage_labels_created", count=created)
            return created
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_labels_failed", error=str(exc))
        return 0
