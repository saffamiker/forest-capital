"""tools/commit_summaries.py — plain-English commit summaries for Team Activity.

Translates a developer commit/PR message into a one-sentence, non-technical
description a faculty reviewer understands, cached by SHA (migration 049) so
each message is summarised once. The batch summariser sends every uncached
SHA in ONE Anthropic Haiku call, parses the JSON map, and stores each row.

Fail-open: in the test environment, with no API key, or on any error, the
affected SHAs simply get no summary and the Team Activity report falls back
to the technical commit message.
"""
from __future__ import annotations

import json

import structlog

from config import ENVIRONMENT

log = structlog.get_logger(__name__)

_DB_AVAILABLE = False
try:  # pragma: no cover - environment dependent
    from database import AsyncSessionLocal
    _DB_AVAILABLE = AsyncSessionLocal is not None
except Exception:  # noqa: BLE001
    AsyncSessionLocal = None  # type: ignore[assignment]

_SYSTEM = (
    "You translate software commit messages into plain English for a "
    "non-technical academic reader. For each message write ONE sentence "
    "(under 25 words) describing what changed in human terms — what it "
    "means for the platform or the analysis, not how it was coded. Avoid "
    "ALL developer jargon: no 'commit', 'API', 'asyncpg', 'migration', "
    "'refactor', 'endpoint', function names or file names. Respond ONLY "
    "with a JSON object mapping each id to its plain-English sentence — no "
    "preamble, no markdown fences.\n\n"
    "Examples:\n"
    "  'fix: asyncpg bind type for event_date' -> 'Fixed a data-storage "
    "error that was preventing historical event analysis from saving "
    "correctly.'\n"
    "  'feat: 10-slide deck rebuild' -> 'Built an automated presentation "
    "generator that produces structured academic slides from live data.'"
)


async def get_cached_summaries(shas: list[str]) -> dict[str, str]:
    """The stored plain-English summary for each known SHA, or {}."""
    if not _DB_AVAILABLE or not shas:
        return {}
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            rows = await session.execute(
                text("SELECT sha, plain_summary FROM commit_summaries "
                     "WHERE sha = ANY(:shas)"), {"shas": list(shas)})
            return {r[0]: r[1] for r in rows.fetchall()}
    except Exception as exc:  # noqa: BLE001
        log.warning("commit_summaries_read_error", error=str(exc))
        return {}


async def _store_summaries(items: list[tuple[str, str, str]]) -> None:
    if not _DB_AVAILABLE or not items:
        return
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            for sha, summary, model in items:
                await session.execute(text(
                    "INSERT INTO commit_summaries (sha, plain_summary, model) "
                    "VALUES (:s, :p, :m) ON CONFLICT (sha) DO NOTHING"),
                    {"s": sha, "p": summary, "m": model})
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("commit_summaries_write_error", error=str(exc))


def _summarize_batch(commits: list[dict]) -> dict[str, str]:
    """ONE Haiku call mapping {short_id: message} -> {sha: plain sentence}.
    Synchronous (call_claude is sync); the async caller runs it in a thread.
    Fail-open to {}."""
    try:
        from agents.base import HAIKU_MODEL, call_claude
    except Exception:  # noqa: BLE001
        return {}
    by_short = {c["sha"][:8]: c["sha"] for c in commits}
    payload = {c["sha"][:8]: (c.get("message") or "").split("\n")[0][:200]
               for c in commits}
    user = ("Translate these commit messages. The JSON keys are ids, the "
            "values are the messages:\n" + json.dumps(payload))
    try:
        raw = call_claude(HAIKU_MODEL, _SYSTEM, user,
                          max_tokens=1500, trigger="commit_summary")
        out_text = (raw or "").strip()
        if out_text.startswith("```"):
            out_text = out_text.strip("`")
            if out_text[:4].lower() == "json":
                out_text = out_text[4:]
        start, end = out_text.find("{"), out_text.rfind("}")
        if start == -1 or end == -1 or end < start:
            return {}
        parsed = json.loads(out_text[start:end + 1])
        result: dict[str, str] = {}
        for short, summary in parsed.items():
            sha = by_short.get(short)
            if sha and isinstance(summary, str) and summary.strip():
                result[sha] = summary.strip()
        return result
    except Exception as exc:  # noqa: BLE001
        log.warning("commit_summary_generate_failed", error=str(exc))
        return {}


async def summarize_commits(commits: list[dict]) -> dict[str, str]:
    """{sha: plain-English summary} for every commit, generating and caching
    the uncached ones in a single Haiku call. `commits` is [{sha, message}].

    Fail-open: returns whatever is available (possibly empty / partial) so
    the Team Activity report can fall back to the technical message. Skips
    generation entirely in the test environment."""
    shas = [c["sha"] for c in commits if c.get("sha")]
    if not shas:
        return {}
    cached = await get_cached_summaries(shas)
    uncached = [c for c in commits
                if c.get("sha") and c["sha"] not in cached]
    if uncached and ENVIRONMENT != "test":
        import asyncio

        from agents.base import HAIKU_MODEL
        fresh = await asyncio.to_thread(_summarize_batch, uncached)
        if fresh:
            await _store_summaries(
                [(sha, s, HAIKU_MODEL) for sha, s in fresh.items()])
            cached.update(fresh)
    return cached
