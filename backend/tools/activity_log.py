"""
tools/activity_log.py

The data layer for the Team Activity feature — every read and write
against session_events, agent_interactions and commit_activity
(migration 010).

Design choices that the rest of the feature depends on:

  - A user is identified by EMAIL. There is no users table; every
    per-user table in the project keys on the email string.

  - The PROJECT_TEAM_EMAILS allowlist is enforced HERE, before any
    session_events / agent_interactions insert — not at the query
    layer. A non-team user (e.g. Dr. Panttser) generates no rows at
    all, so the Team Activity view is naturally team-only with no
    extra filtering. The allowlist deliberately does NOT gate
    commit_activity, nor login_failed events (kept for security
    visibility regardless of who triggered them).

  - Git commit authors are resolved through GIT_AUTHOR_EMAIL_MAP so a
    team member who commits under a personal git identity shows as one
    merged identity in the timeline and summary.

  - Every function is fail-open: a DB error is logged and swallowed,
    never raised. Activity logging must never break a primary request.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import structlog
    log = structlog.get_logger(__name__)
except ImportError:  # pragma: no cover
    log = logging.getLogger(__name__)  # type: ignore[assignment]

from config import (
    PROJECT_TEAM_EMAILS,
    GIT_AUTHOR_EMAIL_MAP,
    TEAM_MEMBER_NAMES,
)

_DB_AVAILABLE = False
try:
    from database import AsyncSessionLocal
    _DB_AVAILABLE = AsyncSessionLocal is not None
except Exception:  # pragma: no cover
    pass

# Valid enum values — anything else is coerced/dropped so a malformed
# frontend payload can never widen the schema's effective domain.
_SESSION_EVENT_TYPES = {
    "login", "logout", "page_view", "feature_click", "export", "login_failed",
}
_INTERACTION_TYPES = {"council", "academic_review", "qa", "document_upload",
                      "explain", "explain_data", "export", "test_quality_eval"}
_SESSION_TYPES = {"analytical", "testing"}


# ── Identity helpers ──────────────────────────────────────────────────────────

def is_team_member(email: str | None) -> bool:
    """True when this email is on the project-team allowlist."""
    return bool(email) and email in PROJECT_TEAM_EMAILS


def resolve_git_author(git_email: str | None) -> str:
    """
    Maps a git commit author email to its platform identity. A team
    member who commits under a personal git account (Michael →
    mikeruurds@gmail.com) is merged onto his platform email; an
    unmapped author is returned unchanged so it still displays.
    """
    if not git_email:
        return "unknown"
    key = git_email.strip().lower()
    return GIT_AUTHOR_EMAIL_MAP.get(key, git_email.strip())


def display_name(identity: str | None) -> str:
    """Human-readable name for a platform email; falls back to the
    identity string itself (a git email, or an unknown address)."""
    if not identity:
        return "unknown"
    return TEAM_MEMBER_NAMES.get(identity, identity)


def _norm_session_type(value: Any) -> str:
    """Coerce an inbound session_type to a known value, defaulting to
    analytical — Testing Mode is opt-in and never the silent default."""
    return value if value in _SESSION_TYPES else "analytical"


# ── Writes — session events ───────────────────────────────────────────────────

async def insert_session_events(events: list[dict], user_email: str) -> int:
    """
    Batch-inserts UI telemetry into session_events in a single
    transaction. Returns the number of rows written.

    The PROJECT_TEAM_EMAILS allowlist is applied per event: a non-team
    user's events are dropped, EXCEPT login_failed events, which are
    always kept for security visibility. Fail-open — a DB error logs
    and returns 0; the caller (the /activity/events endpoint) still
    responds 200 so the UI is never blocked.
    """
    if not _DB_AVAILABLE or not events:
        return 0

    team = is_team_member(user_email)
    rows: list[dict] = []
    for ev in events:
        etype = str(ev.get("event_type", "")).strip()
        if etype not in _SESSION_EVENT_TYPES:
            continue
        # Allowlist gate — login_failed bypasses it; everything else
        # from a non-team user is silently dropped.
        if not team and etype != "login_failed":
            continue
        dur = ev.get("duration_seconds")
        rows.append({
            "user_email": user_email,
            "session_id": str(ev.get("session_id") or "")[:36],
            "session_type": _norm_session_type(ev.get("session_type")),
            "event_type": etype,
            "page": (str(ev["page"])[:255] if ev.get("page") else None),
            "feature": (str(ev["feature"])[:120] if ev.get("feature") else None),
            "duration_seconds": int(dur) if isinstance(dur, (int, float)) else None,
            "ip_address": (str(ev["ip_address"])[:64] if ev.get("ip_address") else None),
            "user_agent": (str(ev["user_agent"]) if ev.get("user_agent") else None),
            "metadata": _json_or_none(ev.get("metadata")),
        })
    if not rows:
        return 0

    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            await session.execute(
                text(
                    "INSERT INTO session_events "
                    "(user_email, session_id, session_type, event_type, page, "
                    " feature, duration_seconds, ip_address, user_agent, metadata) "
                    "VALUES (:user_email, :session_id, :session_type, :event_type, "
                    " :page, :feature, :duration_seconds, :ip_address, :user_agent, "
                    " CAST(:metadata AS JSONB))"
                ),
                rows,
            )
            await session.commit()
        return len(rows)
    except Exception as exc:  # noqa: BLE001
        log.warning("session_events_insert_failed", error=str(exc))
        return 0


# ── Writes — agent interactions ───────────────────────────────────────────────

async def log_agent_interaction(
    user_email: str,
    session_id: str | None,
    session_type: str | None,
    interaction_type: str,
    question_text: str | None = None,
    agents_involved: list[str] | None = None,
    response_summary: str | None = None,
    metadata: dict | None = None,
) -> bool:
    """
    Records one substantive AI interaction (a council run, an academic
    review, a document upload, a QA audit) into agent_interactions.

    Allowlist-gated: a non-team user produces no row. Fail-open — wrap
    the call in asyncio.create_task() at the call site so it never
    blocks or breaks the primary response. Returns True on a write.
    """
    if not _DB_AVAILABLE:
        return False
    if not is_team_member(user_email):
        return False
    if interaction_type not in _INTERACTION_TYPES:
        log.warning("agent_interaction_unknown_type", interaction_type=interaction_type)
        return False

    summary = response_summary[:500] if response_summary else None
    try:
        import json
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            await session.execute(
                text(
                    "INSERT INTO agent_interactions "
                    "(user_email, session_id, session_type, interaction_type, "
                    " question_text, agents_involved, response_summary, metadata) "
                    "VALUES (:user_email, :session_id, :session_type, "
                    " :interaction_type, :question_text, "
                    " CAST(:agents_involved AS JSONB), :response_summary, "
                    " CAST(:metadata AS JSONB))"
                ),
                {
                    "user_email": user_email,
                    "session_id": str(session_id or "")[:36],
                    "session_type": _norm_session_type(session_type),
                    "interaction_type": interaction_type,
                    "question_text": question_text,
                    "agents_involved": json.dumps(agents_involved)
                    if agents_involved is not None else None,
                    "response_summary": summary,
                    "metadata": _json_or_none(metadata),
                },
            )
            await session.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("agent_interaction_insert_failed", error=str(exc))
        return False


# ── Writes — commits ──────────────────────────────────────────────────────────

async def upsert_commits(commits: list[dict]) -> int:
    """
    Upserts commit rows on the unique sha. Safe to run repeatedly — a
    commit already stored is updated in place, never duplicated. No
    team-email filter applies: every commit on the branch is logged,
    attributed by its git author.

    Each commit dict: sha, author (git email), message, timestamp
    (datetime or ISO string), files_changed, insertions, deletions,
    github_url, branch. Returns the number of rows written.
    """
    if not _DB_AVAILABLE or not commits:
        return 0
    rows: list[dict] = []
    for c in commits:
        sha = str(c.get("sha", "")).strip()
        if not sha:
            continue
        # commit_activity.timestamp is NOT NULL — GitHub always supplies
        # an ISO string; a commit we cannot date is malformed, so skip it.
        ts = _to_datetime(c.get("timestamp"))
        if ts is None:
            log.warning("commit_upsert_bad_timestamp", sha=sha[:7])
            continue
        rows.append({
            "sha": sha[:40],
            "author": str(c.get("author") or "unknown")[:255],
            "message": str(c.get("message") or ""),
            "timestamp": ts,
            "files_changed": _int_or_none(c.get("files_changed")),
            "insertions": _int_or_none(c.get("insertions")),
            "deletions": _int_or_none(c.get("deletions")),
            "github_url": (str(c["github_url"]) if c.get("github_url") else None),
            "branch": str(c.get("branch") or "main")[:120],
        })
    if not rows:
        return 0
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            await session.execute(
                text(
                    "INSERT INTO commit_activity "
                    "(sha, author, message, timestamp, files_changed, insertions, "
                    " deletions, github_url, branch) "
                    "VALUES (:sha, :author, :message, :timestamp, :files_changed, "
                    " :insertions, :deletions, :github_url, :branch) "
                    "ON CONFLICT (sha) DO UPDATE SET "
                    " author = EXCLUDED.author, message = EXCLUDED.message, "
                    " timestamp = EXCLUDED.timestamp, "
                    " files_changed = EXCLUDED.files_changed, "
                    " insertions = EXCLUDED.insertions, "
                    " deletions = EXCLUDED.deletions, "
                    " github_url = EXCLUDED.github_url, branch = EXCLUDED.branch, "
                    " synced_at = now()"
                ),
                rows,
            )
            await session.commit()
        return len(rows)
    except Exception as exc:  # noqa: BLE001
        log.warning("commit_upsert_failed", error=str(exc))
        return 0


# ── Reads — unified timeline ──────────────────────────────────────────────────

# activity_type filter → which sources to include.
_TYPE_SOURCES: dict[str, set[str]] = {
    "all":             {"commits", "council", "academic_review", "qa",
                        "uploads", "page_views"},
    "council":         {"council"},
    "academic_review": {"academic_review"},
    "commits":         {"commits"},
    "page_views":      {"page_views"},
    "uploads":         {"uploads"},
}


async def get_team_activity(
    user_id: str | None = None,
    activity_type: str = "all",
    session_type: str = "analytical",
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """
    Returns one interleaved timeline across commit_activity,
    agent_interactions and session_events, sorted by timestamp
    descending.

    session_type filters the two session-scoped tables: "analytical"
    (default) or "testing" select that band; "all" drops the filter.
    commit_activity has no session_type and is always included when the
    activity_type filter permits commits — git history is not
    session-scoped.
    """
    empty = {"events": [], "total_returned": 0, "limit": limit, "offset": offset}
    if not _DB_AVAILABLE:
        return empty

    sources = _TYPE_SOURCES.get(activity_type, _TYPE_SOURCES["all"])
    # Over-fetch (offset + limit) from every source, merge, then slice —
    # correct cross-source pagination at these volumes (limit defaults 100).
    fetch_n = offset + limit
    merged: list[dict] = []
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            if "commits" in sources:
                merged += await _read_commits(session, text, date_from, date_to,
                                              user_id, fetch_n)
            interaction_types = {
                "council": "council", "academic_review": "academic_review",
                "qa": "qa", "uploads": "document_upload",
            }
            wanted = [interaction_types[s] for s in sources if s in interaction_types]
            if wanted:
                merged += await _read_interactions(
                    session, text, wanted, session_type, date_from, date_to,
                    user_id, fetch_n)
            if "page_views" in sources:
                merged += await _read_page_views(
                    session, text, session_type, date_from, date_to,
                    user_id, fetch_n)
    except Exception as exc:  # noqa: BLE001
        log.warning("team_activity_query_failed", error=str(exc))
        return empty

    merged.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
    page = merged[offset:offset + limit]
    return {"events": page, "total_returned": len(page),
            "limit": limit, "offset": offset}


def _ts(value: Any) -> str | None:
    """Render a DB timestamp as an ISO-8601 string for the JSON payload."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def _read_commits(session, text, date_from, date_to, user_id, fetch_n):
    clauses, params = [], {"n": fetch_n}
    if date_from:
        clauses.append("timestamp >= :df"); params["df"] = date_from
    if date_to:
        clauses.append("timestamp <= :dt"); params["dt"] = date_to
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = await session.execute(
        text(
            "SELECT sha, author, message, timestamp, files_changed, insertions, "
            " deletions, github_url, branch FROM commit_activity"
            + where + " ORDER BY timestamp DESC LIMIT :n"
        ),
        params,
    )
    out = []
    for r in rows.fetchall():
        identity = resolve_git_author(r[1])
        # user_id filter resolves through the git→platform map so filtering
        # by ruurdsm@queens.edu also catches Michael's personal-git commits.
        if user_id and identity != user_id:
            continue
        out.append({
            "kind": "commit",
            "timestamp": _ts(r[3]),
            "user": identity,
            "user_name": display_name(identity),
            "session_type": None,
            "sha": r[0],
            "message": r[2],
            "files_changed": r[4],
            "insertions": r[5],
            "deletions": r[6],
            "github_url": r[7],
            "branch": r[8],
        })
    return out


async def _read_interactions(session, text, wanted, session_type,
                             date_from, date_to, user_id, fetch_n):
    clauses = ["interaction_type = ANY(:types)"]
    params: dict[str, Any] = {"types": wanted, "n": fetch_n}
    if session_type in _SESSION_TYPES:
        clauses.append("session_type = :st"); params["st"] = session_type
    if user_id:
        clauses.append("user_email = :uid"); params["uid"] = user_id
    if date_from:
        clauses.append("timestamp >= :df"); params["df"] = date_from
    if date_to:
        clauses.append("timestamp <= :dt"); params["dt"] = date_to
    rows = await session.execute(
        text(
            "SELECT user_email, session_type, interaction_type, timestamp, "
            " question_text, agents_involved, response_summary, metadata "
            "FROM agent_interactions WHERE " + " AND ".join(clauses)
            + " ORDER BY timestamp DESC LIMIT :n"
        ),
        params,
    )
    out = []
    for r in rows.fetchall():
        out.append({
            "kind": r[2],   # council | academic_review | qa | document_upload
            "timestamp": _ts(r[3]),
            "user": r[0],
            "user_name": display_name(r[0]),
            "session_type": r[1],
            "question_text": r[4],
            "agents_involved": r[5],
            "response_summary": r[6],
            "metadata": r[7],
        })
    return out


async def _read_page_views(session, text, session_type, date_from, date_to,
                           user_id, fetch_n):
    clauses = ["event_type = 'page_view'"]
    params: dict[str, Any] = {"n": fetch_n}
    if session_type in _SESSION_TYPES:
        clauses.append("session_type = :st"); params["st"] = session_type
    if user_id:
        clauses.append("user_email = :uid"); params["uid"] = user_id
    if date_from:
        clauses.append("timestamp >= :df"); params["df"] = date_from
    if date_to:
        clauses.append("timestamp <= :dt"); params["dt"] = date_to
    rows = await session.execute(
        text(
            "SELECT user_email, session_type, timestamp, page, duration_seconds "
            "FROM session_events WHERE " + " AND ".join(clauses)
            + " ORDER BY timestamp DESC LIMIT :n"
        ),
        params,
    )
    out = []
    for r in rows.fetchall():
        out.append({
            "kind": "page_view",
            "timestamp": _ts(r[2]),
            "user": r[0],
            "user_name": display_name(r[0]),
            "session_type": r[1],
            "page": r[3],
            "duration_seconds": r[4],
        })
    return out


# ── Reads — summary ───────────────────────────────────────────────────────────

async def get_activity_summary(analytical_only: bool = True) -> dict[str, Any]:
    """
    Per-member interaction and commit counts, the most-consulted agents,
    and the latest academic-review verdict. Drives the Team Activity
    summary panel and — with analytical_only=True — the team-activity
    block injected into agent context (testing sessions are never shown
    to agents).
    """
    empty = {
        "per_member": [], "commits": {"total": 0, "this_week": 0, "by_author": {}},
        "most_active_agents": [], "last_academic_review": None,
        "total_interactions": 0, "analytical_sessions_only": analytical_only,
    }
    if not _DB_AVAILABLE:
        return empty
    try:
        from sqlalchemy import text
        st_clause = " AND session_type = 'analytical'" if analytical_only else ""
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            # Per-member interaction counts by type.
            inter = await session.execute(text(
                "SELECT user_email, interaction_type, COUNT(*), MAX(timestamp) "
                "FROM agent_interactions WHERE 1=1" + st_clause
                + " GROUP BY user_email, interaction_type"
            ))
            # Per-member page-view counts + last activity.
            pv = await session.execute(text(
                "SELECT user_email, COUNT(*), MAX(timestamp) FROM session_events "
                "WHERE event_type = 'page_view'" + st_clause
                + " GROUP BY user_email"
            ))
            # Per-member most-used features.
            feats = await session.execute(text(
                "SELECT user_email, feature, COUNT(*) AS c FROM session_events "
                "WHERE event_type = 'feature_click' AND feature IS NOT NULL"
                + st_clause + " GROUP BY user_email, feature ORDER BY c DESC"
            ))
            commits = await session.execute(text(
                "SELECT author, timestamp FROM commit_activity"
            ))
            agents = await session.execute(text(
                "SELECT agents_involved FROM agent_interactions "
                "WHERE agents_involved IS NOT NULL" + st_clause
            ))
            last_ar = await session.execute(text(
                "SELECT user_email, timestamp, metadata FROM agent_interactions "
                "WHERE interaction_type = 'academic_review'" + st_clause
                + " ORDER BY timestamp DESC LIMIT 1"
            ))

            members: dict[str, dict] = {}

            def _member(email: str) -> dict:
                return members.setdefault(email, {
                    "user": email, "user_name": display_name(email),
                    "council_interactions": 0, "academic_review_sessions": 0,
                    "document_uploads": 0, "qa_audits": 0, "page_views": 0,
                    "last_active": None, "most_used_features": [],
                })

            total_interactions = 0
            for email, itype, count, last in inter.fetchall():
                m = _member(email)
                count = int(count)
                total_interactions += count
                if itype == "council":
                    m["council_interactions"] = count
                elif itype == "academic_review":
                    m["academic_review_sessions"] = count
                elif itype == "document_upload":
                    m["document_uploads"] = count
                elif itype == "qa":
                    m["qa_audits"] = count
                m["last_active"] = _max_iso(m["last_active"], _ts(last))

            for email, count, last in pv.fetchall():
                m = _member(email)
                m["page_views"] = int(count)
                m["last_active"] = _max_iso(m["last_active"], _ts(last))

            feat_seen: dict[str, set[str]] = {}
            for email, feature, _c in feats.fetchall():
                bucket = feat_seen.setdefault(email, set())
                if len(bucket) >= 3 or feature in bucket:
                    continue
                bucket.add(feature)
                _member(email)["most_used_features"].append(feature)

            # Commits — total, last 7 days, by resolved author.
            week_ago = datetime.now(timezone.utc) - timedelta(days=7)
            by_author: dict[str, int] = {}
            total_commits = this_week = 0
            for author, ts in commits.fetchall():
                total_commits += 1
                identity = resolve_git_author(author)
                by_author[identity] = by_author.get(identity, 0) + 1
                if isinstance(ts, datetime):
                    cmp_ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
                    if cmp_ts >= week_ago:
                        this_week += 1

            # Most-consulted agents across every interaction's involved list.
            agent_counts: dict[str, int] = {}
            for (involved,) in agents.fetchall():
                for a in (involved or []):
                    agent_counts[a] = agent_counts.get(a, 0) + 1
            most_active = sorted(
                ({"agent": a, "count": c} for a, c in agent_counts.items()),
                key=lambda x: x["count"], reverse=True,
            )[:3]

            last_review = None
            ar_row = last_ar.fetchone()
            if ar_row:
                meta = ar_row[2] or {}
                last_review = {
                    "user": ar_row[0],
                    "user_name": display_name(ar_row[0]),
                    "timestamp": _ts(ar_row[1]),
                    "overall_rating": meta.get("overall_rating"),
                }

        return {
            "per_member": sorted(members.values(), key=lambda m: m["user"]),
            "commits": {"total": total_commits, "this_week": this_week,
                        "by_author": by_author},
            "most_active_agents": most_active,
            "last_academic_review": last_review,
            "total_interactions": total_interactions,
            "analytical_sessions_only": analytical_only,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("activity_summary_failed", error=str(exc))
        return empty


# ── small helpers ─────────────────────────────────────────────────────────────

def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    try:
        import json
        return json.dumps(value)
    except Exception:  # noqa: BLE001
        return None


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _to_datetime(value: Any) -> datetime | None:
    """
    Coerces a commit timestamp to a datetime — asyncpg binds the
    TIMESTAMP column from a datetime, not an ISO string. Accepts an
    existing datetime or an ISO-8601 string (GitHub's trailing 'Z' is
    normalised to +00:00). Returns None when the value cannot be parsed.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _max_iso(a: str | None, b: str | None) -> str | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if a >= b else b
