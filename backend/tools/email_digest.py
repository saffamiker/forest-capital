"""tools/email_digest.py — daily team digest assembler.

Component 1 of the automated email system. Fires at 07:00 ET via the
Render cron `forest-capital-digest` and lands in the inboxes of every
address in DIGEST_RECIPIENTS (Michael, Bob, Molly). Mirrors the
content the user spec'd on May 31 2026:

  1. Platform health summary
  2. Recent platform releases (last 24h)
  3. Key analytics snapshot
  4. Platform usage (last 24h / WTD)            ← added June 2 2026
  5. Team activity (last 24h / WTD)             ← added June 2 2026
  6. Warm history (last 7 days)                 ← added June 2 2026
  7. Open work
  8. Deadline tracker

Every section is fail-open per data source — a missing cache row or
a transient GitHub API outage degrades that section to a placeholder
line rather than aborting the whole send. The team needs the digest
even when one upstream is flaky.

All assembly is synchronous and pure-ish — the async I/O lives at
the edges (DB reads, GitHub fetch) and is awaited at the top-level
build_digest_email() coroutine. The assembler does NOT call Resend
directly; the caller (send_daily_digest in this module) does, so
unit tests can exercise the assembler without monkey-patching the
network layer.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import httpx
import structlog

try:
    # Python 3.9+ stdlib timezone DB. The digest runs in the Render
    # container's UTC clock; we need US/Eastern for "this week" so the
    # WTD window matches the team's actual workweek, not UTC Monday.
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover — Python < 3.9
    ZoneInfo = None  # type: ignore[assignment]

from tools.email_resend import DIGEST_FROM, digest_recipients, send_email

log = structlog.get_logger(__name__)


# ── Deliverable deadlines (the user's project calendar) ───────────────────
# Hardcoded — the calendar is fixed for the practicum.
_DEADLINES: list[tuple[str, date]] = [
    ("Cohort peer review",                       date(2026, 6, 3)),
    ("Executive brief + final presentation",     date(2026, 7, 1)),
]


# ── Section assemblers — each returns (html_block, text_block) ────────────


@dataclass
class DigestSection:
    title: str
    html: str
    text: str


# ── Section 1 — Platform health summary ───────────────────────────────────


async def _section_platform_health() -> DigestSection:
    """Cache warm states (6 of them) + last warm timestamp +
    invariant verdict + alembic head.

    All three reads are fail-open and now async-capable so the
    invariant read can fall back to the persisted summary in
    analytics_metrics_cache when the in-memory cache is empty (a
    Render redeploy between warm cron and digest cron resets the
    module-level cache; the persisted row survives). June 2 2026
    digest fix."""
    try:
        from tools.cache_warm_state import get_warm_state
        warm = get_warm_state().to_dict()
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_warm_state_failed", error=str(exc))
        warm = {"status": "unknown", "last_success_at": None,
                "last_landed": {}, "last_attempt_error": str(exc)}

    # Invariant verdict — prefer the in-memory cache, fall back to
    # the persisted summary (analytics_metrics_cache row written by
    # set_strategy_cache after each warm). Either path produces the
    # same {checks_run, hard_failures, soft_warnings, ran_at} shape.
    inv: dict | None = None
    try:
        from tools.invariant_checks import get_latest_result
        inv = get_latest_result()
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_invariant_read_failed", error=str(exc))
    if inv is None:
        try:
            from tools.precomputed_analytics import get_latest_metric
            inv = await get_latest_metric("invariant_summary")
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "digest_invariant_persisted_read_failed",
                error=str(exc))

    # DB head — query alembic_version directly. The source of truth
    # lives in the DB, not the filesystem. June 2 2026 digest fix:
    # the old parents[1] migrations-folder parse was unreliable on
    # the Render filesystem layout.
    head = await _alembic_head_from_db()

    # Build a status table for the 6 named caches the warm path lands.
    landed = warm.get("last_landed") or {}
    cache_names = list(landed.keys()) if landed else [
        "strategy_results", "academic_analytics", "diversification",
        "efficient_frontier", "sensitivity", "risk_free_rate_config",
    ]
    rows_html: list[str] = []
    rows_text: list[str] = []
    for name in cache_names:
        ok = bool(landed.get(name)) if landed else False
        badge = "✅" if ok else "⚠️"
        rows_html.append(
            f"<tr><td style='padding:4px 8px'>{name}</td>"
            f"<td style='padding:4px 8px;text-align:right'>{badge}</td></tr>")
        rows_text.append(f"  {badge} {name}")

    last_success = warm.get("last_success_at") or "never"
    inv_line_html: str
    inv_line_text: str
    if inv is None:
        inv_line_html = "<em>Invariant framework has not run yet.</em>"
        inv_line_text = "Invariant framework has not run yet."
    else:
        hf = inv.get("hard_failures", 0)
        sw = inv.get("soft_warnings", 0)
        passed = inv.get("checks_run", 0) - hf - sw
        inv_line_html = (
            f"<b>Invariants</b> &nbsp; "
            f"{passed} passed &nbsp;·&nbsp; "
            f"{sw} warnings &nbsp;·&nbsp; "
            f"{hf} failures")
        inv_line_text = (
            f"Invariants: {passed} passed | "
            f"{sw} warnings | {hf} failures")

    html = (
        f"<h3 style='color:#cbd5e1;margin:0 0 8px 0;font-size:14px'>"
        f"Platform health</h3>"
        f"<table cellspacing='0' style='font-family:monospace;"
        f"font-size:12px;color:#94a3b8;margin-bottom:8px'>"
        f"{''.join(rows_html)}"
        f"</table>"
        f"<div style='font-size:12px;color:#94a3b8;margin-bottom:4px'>"
        f"Last warm: <span style='color:#cbd5e1'>{last_success}</span>"
        f"</div>"
        f"<div style='font-size:12px;color:#94a3b8;margin-bottom:4px'>"
        f"{inv_line_html}</div>"
        f"<div style='font-size:12px;color:#94a3b8'>"
        f"DB head: <span style='color:#cbd5e1'>{head}</span></div>")
    text = (
        f"PLATFORM HEALTH\n"
        + "\n".join(rows_text) + "\n"
        + f"  Last warm: {last_success}\n"
        + f"  {inv_line_text}\n"
        + f"  DB head:   {head}\n")
    return DigestSection(title="Platform health", html=html, text=text)


# ── Section 2 — Recent platform releases (last 24h) ───────────────────────


async def _section_releases() -> DigestSection:
    """Reads commit_activity for the last 24h and joins commit_summaries
    for the plain-English summary. The user spec: 'Platform Release',
    not 'PR'."""
    rows: list[tuple[str, str, str]] = []  # (sha8, message_head, plain)
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            res = await session.execute(text(
                "SELECT c.sha, c.message, c.timestamp, c.github_url, "
                "       s.plain_summary "
                "FROM commit_activity c "
                "LEFT JOIN commit_summaries s ON s.sha = c.sha "
                "WHERE c.timestamp > now() - interval '24 hours' "
                "ORDER BY c.timestamp DESC "
                "LIMIT 20"))
            for row in res.fetchall():
                sha, msg, _ts, _url, plain = row[:5]
                head = (msg or "").splitlines()[0] if msg else ""
                rows.append((
                    (sha or "")[:8],
                    head,
                    plain or "",
                ))
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_releases_read_failed", error=str(exc))
        rows = []

    if not rows:
        html = (
            f"<h3 style='color:#cbd5e1;margin:0 0 8px 0;font-size:14px'>"
            f"Platform releases (last 24h)</h3>"
            f"<div style='font-size:12px;color:#64748b'>"
            f"No releases in the last 24 hours.</div>")
        text = "PLATFORM RELEASES (last 24h)\n  None.\n"
        return DigestSection(title="Platform releases",
                             html=html, text=text)

    html_rows: list[str] = []
    text_rows: list[str] = []
    for sha, head, plain in rows:
        body = plain if plain else head
        html_rows.append(
            f"<li style='margin-bottom:6px'>"
            f"<code style='color:#94a3b8'>{sha}</code> "
            f"<span style='color:#cbd5e1'>{body}</span></li>")
        text_rows.append(f"  {sha}  {body}")

    html = (
        f"<h3 style='color:#cbd5e1;margin:0 0 8px 0;font-size:14px'>"
        f"Platform releases (last 24h)</h3>"
        f"<ul style='font-size:12px;color:#cbd5e1;"
        f"padding-left:18px;margin:0'>"
        f"{''.join(html_rows)}"
        f"</ul>")
    text = "PLATFORM RELEASES (last 24h)\n" + "\n".join(text_rows) + "\n"
    return DigestSection(title="Platform releases", html=html, text=text)


# ── Section 3 — Key analytics snapshot ────────────────────────────────────


async def _section_analytics_snapshot() -> DigestSection:
    """Current regime + confidence, live blend weights, locked
    academic OOS Sharpe figure. The locked figure is the academic
    submission's number (PR #248) — the live OOS extends it but is
    never used in submissions."""
    regime_label = "unknown"
    regime_conf: float | None = None
    blend_weights: dict[str, float] = {}

    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            r = await session.execute(text(
                "SELECT hmm_regime, hmm_probabilities "
                "FROM regime_signals_cache "
                "ORDER BY fetched_at DESC LIMIT 1"))
            row = r.fetchone()
            if row:
                regime_label = row[0] or "unknown"
                probs = row[1]
                if isinstance(probs, str):
                    try:
                        probs = json.loads(probs)
                    except Exception:  # noqa: BLE001
                        probs = None
                if probs and regime_label in probs:
                    regime_conf = float(probs[regime_label])
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_regime_read_failed", error=str(exc))

    # Blend weights — read from the forward_projection cached metric,
    # the SAME source the CIO tile and the deck-slide regime context
    # use (main.py:10362, council_live_context._build_live_context).
    # The earlier read from cio_recommendations.weights was wrong:
    # that table is the SHAPER for the human-readable card, not the
    # canonical source of the live blend. (June 2 2026 digest fix.)
    try:
        from tools.regime_meta_forward import (
            get_cached_forward_projection,
        )
        proj = await get_cached_forward_projection()
        if proj and proj.get("blend_weights"):
            bw = proj["blend_weights"]
            if isinstance(bw, str):
                try:
                    bw = json.loads(bw)
                except Exception:  # noqa: BLE001
                    bw = {}
            blend_weights = bw or {}
            # The forward_projection metric also carries the live
            # regime — prefer it over the regime_signals_cache read
            # above when both are present, so the digest's regime
            # label always matches the CIO tile's blend.
            if proj.get("regime"):
                regime_label = proj["regime"]
            rp = proj.get("regime_probability")
            if rp is not None:
                regime_conf = float(rp)
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_blend_weights_read_failed", error=str(exc))

    # Locked academic OOS Sharpe — Dec 2025 data lock.
    try:
        from tools.academic_deck import (
            OOS_SHARPE_REGIME_CONDITIONAL,
            OOS_SHARPE_BENCHMARK,
        )
        oos_blend = OOS_SHARPE_REGIME_CONDITIONAL
        oos_bench = OOS_SHARPE_BENCHMARK
    except Exception:  # noqa: BLE001
        oos_blend = 0.86
        oos_bench = 0.43

    conf_str = (f" ({regime_conf:.0%})" if regime_conf is not None else "")
    weights_lines_html: list[str] = []
    weights_lines_text: list[str] = []
    if blend_weights:
        # Sort by weight descending so the dominant strategies head
        # the list — same convention as the dashboard's strategy
        # table and the bootstrap-CI table.
        items = sorted(
            blend_weights.items(), key=lambda kv: -float(kv[1] or 0))
        for name, w in items[:8]:
            pct = float(w) * 100
            weights_lines_html.append(
                f"<li style='font-size:12px;color:#cbd5e1'>"
                f"<b>{name}</b> &nbsp; {pct:.1f}%</li>")
            weights_lines_text.append(f"    {name:<22} {pct:>5.1f}%")
    else:
        weights_lines_html.append(
            "<li style='font-size:12px;color:#64748b'>"
            "Blend weights not available.</li>")
        weights_lines_text.append("    (blend weights not available)")

    html = (
        f"<h3 style='color:#cbd5e1;margin:0 0 8px 0;font-size:14px'>"
        f"Analytics snapshot</h3>"
        f"<div style='font-size:12px;color:#94a3b8;margin-bottom:4px'>"
        f"Current regime: <b style='color:#cbd5e1'>{regime_label}</b>"
        f"{conf_str}</div>"
        f"<div style='font-size:12px;color:#94a3b8;margin-bottom:4px'>"
        f"Live blend weights:</div>"
        f"<ul style='padding-left:18px;margin:0 0 8px 0'>"
        f"{''.join(weights_lines_html)}"
        f"</ul>"
        f"<div style='font-size:12px;color:#94a3b8'>"
        f"OOS Sharpe (Dec 2025 lock — academic submission): "
        f"<b style='color:#cbd5e1'>blend {oos_blend:.2f}</b> "
        f"&nbsp;·&nbsp; benchmark {oos_bench:.2f}</div>")
    text = (
        f"ANALYTICS SNAPSHOT\n"
        f"  Current regime: {regime_label}{conf_str}\n"
        f"  Live blend weights:\n"
        + "\n".join(weights_lines_text) + "\n"
        + f"  OOS Sharpe (Dec 2025 lock):  blend {oos_blend:.2f}  "
          f"benchmark {oos_bench:.2f}\n")
    return DigestSection(title="Analytics snapshot",
                         html=html, text=text)


# ── Time-window helpers (US/Eastern WTD) ──────────────────────────────────


def _now_utc() -> datetime:
    """Wrapper so tests can monkeypatch the digest's notion of 'now'."""
    return datetime.now(timezone.utc)


def _week_start_utc(now: datetime | None = None) -> datetime:
    """Returns Monday 00:00 US/Eastern, expressed as a UTC-aware
    datetime suitable for an SQL bound parameter against a
    TIMESTAMPTZ column.

    Why ET, not UTC: the team works on the US East Coast and the
    digest cron fires 07:00 ET. Anchoring WTD to UTC Monday would
    mis-report Sunday-night work as the next week's effort. ET
    matches the rest of the platform's calendar conventions
    (deadlines, cron labels, presentation times)."""
    now = now or _now_utc()
    if ZoneInfo is None:
        # Pre-3.9 fallback: anchor to UTC Monday. Tests will not hit
        # this path; production runs on Render's Python 3.12.
        et_now = now
    else:
        et_now = now.astimezone(ZoneInfo("America/New_York"))
    # Monday is weekday() == 0.
    monday_local = et_now - timedelta(days=et_now.weekday())
    monday_local = datetime.combine(
        monday_local.date(), time(0, 0, 0),
        tzinfo=et_now.tzinfo)
    return monday_local.astimezone(timezone.utc)


def _hours_ago_utc(hours: float, now: datetime | None = None) -> datetime:
    return (now or _now_utc()) - timedelta(hours=hours)


# ── Section 4 — Platform usage (last 24h / WTD) ───────────────────────────
# Reads agent_interactions to surface how the team actually drove the
# AI council and the support assistants. Three primary categories the
# user spec'd — council, defense_prep, peer_review — plus an "Other"
# rollup for the utility interactions (qa, document_upload, explain,
# explain_data, export, writing_assistant, test_quality_eval, etc.)
# so the table sums to the true total without flooding it.


_PRIMARY_USAGE_TYPES = ("council", "defense_prep", "peer_review")


async def _section_platform_usage() -> DigestSection:
    rows: dict[str, dict[str, int]] = {}
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            raise RuntimeError("AsyncSessionLocal unavailable")
        now = _now_utc()
        h24 = _hours_ago_utc(24, now)
        wk0 = _week_start_utc(now)
        async with AsyncSessionLocal() as session:
            # One query per window so the bound timestamps are explicit
            # and stable across SQL engines. Counts are tiny.
            for window_key, since in (("h24", h24), ("wtd", wk0)):
                r = await session.execute(text(
                    "SELECT interaction_type, COUNT(*) "
                    "FROM agent_interactions "
                    "WHERE timestamp >= :since "
                    "GROUP BY interaction_type"), {"since": since})
                for itype, count in r.fetchall():
                    rows.setdefault(itype or "?", {})[window_key] = int(count)
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_platform_usage_read_failed", error=str(exc))

    def _total(key: str) -> int:
        return sum(v.get(key, 0) for v in rows.values())

    def _bucket(key: str) -> int:
        return rows.get(key, {}).get("h24", 0)

    def _bucket_wtd(key: str) -> int:
        return rows.get(key, {}).get("wtd", 0)

    primary_counts = [
        ("Council queries",    "council"),
        ("Defense Prep",       "defense_prep"),
        ("Peer Review",        "peer_review"),
    ]
    other_h24 = _total("h24") - sum(
        _bucket(k) for _, k in primary_counts)
    other_wtd = _total("wtd") - sum(
        _bucket_wtd(k) for _, k in primary_counts)

    rows_html: list[str] = []
    rows_text: list[str] = []
    for label, key in primary_counts:
        h24 = _bucket(key)
        wtd = _bucket_wtd(key)
        rows_html.append(
            f"<tr>"
            f"<td style='padding:4px 8px;color:#cbd5e1'>{label}</td>"
            f"<td style='padding:4px 8px;color:#cbd5e1;text-align:right'>"
            f"{h24}</td>"
            f"<td style='padding:4px 8px;color:#94a3b8;text-align:right'>"
            f"{wtd}</td>"
            f"</tr>")
        rows_text.append(f"  {label:<20} {h24:>4}  {wtd:>4}")
    rows_html.append(
        f"<tr>"
        f"<td style='padding:4px 8px;color:#64748b'>Other</td>"
        f"<td style='padding:4px 8px;color:#64748b;text-align:right'>"
        f"{max(other_h24, 0)}</td>"
        f"<td style='padding:4px 8px;color:#64748b;text-align:right'>"
        f"{max(other_wtd, 0)}</td>"
        f"</tr>"
        f"<tr style='border-top:1px solid #1e2d47'>"
        f"<td style='padding:4px 8px;color:#cbd5e1'><b>Total</b></td>"
        f"<td style='padding:4px 8px;color:#cbd5e1;text-align:right'>"
        f"<b>{_total('h24')}</b></td>"
        f"<td style='padding:4px 8px;color:#cbd5e1;text-align:right'>"
        f"<b>{_total('wtd')}</b></td>"
        f"</tr>")
    rows_text.append(f"  {'Other':<20} {max(other_h24,0):>4}  "
                     f"{max(other_wtd,0):>4}")
    rows_text.append(f"  {'-' * 22}{'----':>4}  {'----':>4}")
    rows_text.append(f"  {'Total':<20} {_total('h24'):>4}  "
                     f"{_total('wtd'):>4}")

    html = (
        f"<h3 style='color:#cbd5e1;margin:0 0 8px 0;font-size:14px'>"
        f"Platform usage</h3>"
        f"<table cellspacing='0' style='font-family:monospace;"
        f"font-size:12px;border-collapse:collapse'>"
        f"<thead><tr>"
        f"<th style='padding:4px 8px;text-align:left;color:#64748b'>"
        f"Category</th>"
        f"<th style='padding:4px 8px;text-align:right;color:#64748b'>"
        f"24h</th>"
        f"<th style='padding:4px 8px;text-align:right;color:#64748b'>"
        f"WTD</th>"
        f"</tr></thead><tbody>"
        f"{''.join(rows_html)}"
        f"</tbody></table>")
    text = (
        f"PLATFORM USAGE\n"
        f"  {'Category':<20} {'24h':>4}  {'WTD':>4}\n"
        + "\n".join(rows_text) + "\n")
    return DigestSection(title="Platform usage", html=html, text=text)


# ── Section 5 — Team activity (last 24h / WTD) ────────────────────────────
# Per-person counts across four signals: commits (via the GIT_AUTHOR_
# EMAIL_MAP reverse), doc edits (editor_drafts.updated_at), UAT
# attempts (test_results.attested_at), and logins (session_events
# WHERE event_type = 'login'). The team is fixed: Michael, Bob, Molly.
# Order is sysadmin-first (Michael) then alphabetical — same order
# the Team Activity dashboard uses.


_TEAM_DISPLAY_ORDER: list[tuple[str, str]] = [
    ("ruurdsm@queens.edu",  "Michael"),
    ("thaob@queens.edu",    "Bob"),
    ("murdockm@queens.edu", "Molly"),
]


def _git_authors_for(email: str) -> set[str]:
    """Reverse of config.GIT_AUTHOR_EMAIL_MAP — every git author email
    that maps to this platform email, plus the platform email itself
    (commits authored directly from the platform domain are
    unmapped). Fail-open to {email} on import failure."""
    try:
        from config import GIT_AUTHOR_EMAIL_MAP
    except Exception:  # noqa: BLE001
        return {email}
    authors = {email}
    for git_email, platform_email in GIT_AUTHOR_EMAIL_MAP.items():
        if platform_email == email:
            authors.add(git_email)
    return authors


async def _section_team_activity() -> DigestSection:
    per_person: dict[str, dict[str, dict[str, int]]] = {
        email: {"commits": {}, "drafts": {}, "uat": {}, "logins": {}}
        for email, _ in _TEAM_DISPLAY_ORDER
    }
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            raise RuntimeError("AsyncSessionLocal unavailable")
        now = _now_utc()
        h24 = _hours_ago_utc(24, now)
        wk0 = _week_start_utc(now)
        async with AsyncSessionLocal() as session:
            for email, _ in _TEAM_DISPLAY_ORDER:
                authors = list(_git_authors_for(email))
                for window_key, since in (("h24", h24), ("wtd", wk0)):
                    # Commits — by git author through the reverse map.
                    r = await session.execute(text(
                        "SELECT COUNT(*) FROM commit_activity "
                        "WHERE author = ANY(:authors) "
                        "AND timestamp >= :since"),
                        {"authors": authors, "since": since})
                    per_person[email]["commits"][window_key] = int(
                        (r.scalar() or 0))
                    # Doc edits — distinct editor_drafts touched.
                    r = await session.execute(text(
                        "SELECT COUNT(DISTINCT id) FROM editor_drafts "
                        "WHERE owner_email = :e "
                        "AND updated_at >= :since "
                        "AND is_deleted = false"),
                        {"e": email, "since": since})
                    per_person[email]["drafts"][window_key] = int(
                        (r.scalar() or 0))
                    # UAT — count of test_results attestations.
                    r = await session.execute(text(
                        "SELECT COUNT(*) FROM test_results "
                        "WHERE user_email = :e "
                        "AND attested_at >= :since"),
                        {"e": email, "since": since})
                    per_person[email]["uat"][window_key] = int(
                        (r.scalar() or 0))
                    # Logins — session_events login events.
                    r = await session.execute(text(
                        "SELECT COUNT(*) FROM session_events "
                        "WHERE user_email = :e "
                        "AND event_type = 'login' "
                        "AND timestamp >= :since"),
                        {"e": email, "since": since})
                    per_person[email]["logins"][window_key] = int(
                        (r.scalar() or 0))
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_team_activity_read_failed", error=str(exc))

    cols = [
        ("Commits",  "commits"),
        ("Doc edits", "drafts"),
        ("UAT",      "uat"),
        ("Logins",   "logins"),
    ]
    header_html = (
        "<tr><th style='padding:4px 8px;text-align:left;color:#64748b'>"
        "Member</th>"
        + "".join(
            f"<th style='padding:4px 8px;text-align:right;"
            f"color:#64748b'>{label}<br/>"
            f"<span style='font-size:10px;color:#475569'>24h / WTD"
            f"</span></th>"
            for label, _ in cols)
        + "</tr>")

    body_rows_html: list[str] = []
    body_rows_text: list[str] = []
    for email, name in _TEAM_DISPLAY_ORDER:
        cells_html: list[str] = []
        cells_text: list[str] = []
        for _, key in cols:
            stats = per_person[email][key]
            h = stats.get("h24", 0)
            w = stats.get("wtd", 0)
            cells_html.append(
                f"<td style='padding:4px 8px;text-align:right;"
                f"color:#cbd5e1;font-family:monospace'>"
                f"{h} / {w}</td>")
            cells_text.append(f"{h:>3}/{w:<3}")
        body_rows_html.append(
            f"<tr><td style='padding:4px 8px;color:#cbd5e1'>{name}</td>"
            + "".join(cells_html) + "</tr>")
        body_rows_text.append(
            f"  {name:<8} " + "  ".join(cells_text))

    html = (
        f"<h3 style='color:#cbd5e1;margin:0 0 8px 0;font-size:14px'>"
        f"Team activity</h3>"
        f"<table cellspacing='0' style='font-size:12px;"
        f"border-collapse:collapse'>"
        f"<thead>{header_html}</thead>"
        f"<tbody>{''.join(body_rows_html)}</tbody></table>")
    header_text = (
        f"  {'Member':<8} " +
        "  ".join(f"{label:<7}" for label, _ in cols))
    text = (
        f"TEAM ACTIVITY (24h / WTD)\n"
        f"{header_text}\n"
        + "\n".join(body_rows_text) + "\n")
    return DigestSection(title="Team activity", html=html, text=text)


# ── Section 6 — Warm history (last 7 days) ────────────────────────────────
# Reads analytics_metrics_cache where metric_kind='invariant_summary' —
# the rows PR #252 (set_strategy_cache_invariant_persist) starts
# writing on every warm. One row per distinct data_hash, upserted with
# fresh computed_at on each warm against the same hash. For the digest
# we show the most recent 10 rows in the last 7 days, oldest at the
# bottom so a reader scans top-to-bottom newest-first.


async def _section_warm_history() -> DigestSection:
    rows: list[dict[str, Any]] = []
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            raise RuntimeError("AsyncSessionLocal unavailable")
        since = _hours_ago_utc(24 * 7)
        async with AsyncSessionLocal() as session:
            r = await session.execute(text(
                "SELECT data_hash, payload, computed_at "
                "FROM analytics_metrics_cache "
                "WHERE metric_kind = 'invariant_summary' "
                "AND computed_at >= :since "
                "ORDER BY computed_at DESC "
                "LIMIT 10"), {"since": since})
            for data_hash, payload, computed_at in r.fetchall():
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:  # noqa: BLE001
                        payload = {}
                rows.append({
                    "data_hash":   (data_hash or "")[:8],
                    "passed":      bool((payload or {}).get("passed", True)),
                    "hard":        int((payload or {}).get(
                        "hard_failures", 0)),
                    "soft":        int((payload or {}).get(
                        "soft_warnings", 0)),
                    "violations":  (payload or {}).get("violations") or [],
                    "computed_at": computed_at,
                })
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_warm_history_read_failed", error=str(exc))

    if not rows:
        html = (
            f"<h3 style='color:#cbd5e1;margin:0 0 8px 0;font-size:14px'>"
            f"Warm history (last 7 days)</h3>"
            f"<div style='font-size:12px;color:#64748b'>"
            f"No warm runs recorded in the last 7 days.</div>")
        text = (
            "WARM HISTORY (last 7 days)\n"
            "  No warm runs recorded.\n")
        return DigestSection(title="Warm history", html=html, text=text)

    html_rows: list[str] = []
    text_rows: list[str] = []
    for row in rows:
        ts = row["computed_at"]
        ts_str = (ts.strftime("%Y-%m-%d %H:%M UTC")
                  if hasattr(ts, "strftime") else str(ts))
        if row["passed"]:
            badge_html = (
                "<span style='color:#22c55e'>✅ passed</span>")
            badge_text = "✅ passed"
        elif row["hard"] > 0:
            badge_html = (
                f"<span style='color:#ef4444'>❌ "
                f"{row['hard']} HARD FAILURE"
                f"{'S' if row['hard'] != 1 else ''}</span>")
            badge_text = f"❌ {row['hard']} HARD FAILURE(S)"
        else:
            badge_html = (
                f"<span style='color:#f59e0b'>⚠️ "
                f"{row['soft']} warning"
                f"{'s' if row['soft'] != 1 else ''}</span>")
            badge_text = f"⚠️ {row['soft']} warning(s)"

        # First hard failure — the most actionable detail. Soft-only
        # rows surface the first soft warning's detail so the row
        # isn't cryptic.
        detail = ""
        for v in row["violations"]:
            if v.get("severity") == "hard":
                detail = (
                    f"[{v.get('code')}] {v.get('entity','')} — "
                    f"{v.get('detail','')}")
                break
        if not detail:
            for v in row["violations"]:
                if v.get("severity") == "soft":
                    detail = (
                        f"[{v.get('code')}] {v.get('entity','')} — "
                        f"{v.get('detail','')}")
                    break

        html_rows.append(
            f"<li style='margin-bottom:6px;font-size:12px;"
            f"color:#cbd5e1'>"
            f"<code style='color:#64748b'>{ts_str}</code> &nbsp; "
            f"{badge_html} &nbsp; "
            f"<span style='color:#64748b'>hash "
            f"<code>{row['data_hash']}</code></span>"
            + (f"<br/><span style='color:#94a3b8;font-size:11px;"
               f"margin-left:18px'>{detail}</span>" if detail else "")
            + "</li>")
        text_rows.append(
            f"  {ts_str}  {badge_text}  (hash {row['data_hash']})"
            + (f"\n    {detail}" if detail else ""))

    html = (
        f"<h3 style='color:#cbd5e1;margin:0 0 8px 0;font-size:14px'>"
        f"Warm history (last 7 days)</h3>"
        f"<ul style='padding-left:18px;margin:0'>"
        f"{''.join(html_rows)}"
        f"</ul>")
    text = "WARM HISTORY (last 7 days)\n" + "\n".join(text_rows) + "\n"
    return DigestSection(title="Warm history", html=html, text=text)


# ── Section 6b — Implied asset allocation (June 5 2026) ──────────────────
#
# Per-strategy weights live in strategy_results_cache.results_json[s]:
#   avg_equity_weight   float in [0, 1]
#   avg_bond_weight     float in [0, 1]   ← COMBINED (IG + HY), no split today
#
# Per CLAUDE.md the bond figure is one number; the per-strategy IG/HY tilt
# isn't persisted to the cache. The digest spec asks for three asset columns
# (Equity / IG / HY) but rather than guess a tilt at the digest layer we
# show Equity / Bonds with a footnote so the reader knows the bond split
# is downstream of strategy choice, not a separate axis. A follow-up can
# add per-strategy IG/HY decomposition if the backtester starts emitting it.
#
# Portfolio total row is the weighted average of each asset share across
# the strategy weights, summing to 100% with a small rounding tolerance.


async def _section_implied_asset_allocation() -> DigestSection:
    """Reads the live blend (per-strategy weights from analytics_metrics_cache
    `forward_projection`, fail-open to strategy_results_cache.avg_*_weight
    directly when forward_projection is cold) and the per-strategy asset
    weights from strategy_results_cache. Renders a table: strategy + weight
    + equity% + bonds% per non-zero strategy + a portfolio total row.

    The implied portfolio-level shares answer the question 'after the live
    blend, what fraction of capital sits in equity vs bonds?' — not what
    each strategy's average exposure was in isolation. Both reads are
    fail-open: a cold cache renders a 'no live blend' placeholder rather
    than skipping the section, so the reader knows the cron fired."""
    try:
        from tools.cache import get_latest_strategy_cache
        from tools.precomputed_analytics import get_latest_metric
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_asset_allocation_imports_failed", error=str(exc))
        return _empty_section("Implied asset allocation",
                              "  Section unavailable.")

    try:
        # Live blend weights: prefer the cached forward_projection
        # (the probability-weighted blend the platform serves) over
        # raw strategy_results_cache, which carries only average per-
        # strategy exposures and doesn't reflect the live regime read.
        forward = await get_latest_metric("forward_projection") or {}
        blend_weights = forward.get("blend_weights") or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_asset_allocation_forward_read_failed",
                    error=str(exc))
        blend_weights = {}

    try:
        strategies = await get_latest_strategy_cache() or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_asset_allocation_strategies_read_failed",
                    error=str(exc))
        strategies = {}

    if not blend_weights or not strategies:
        return _empty_section(
            "Implied asset allocation",
            "  No live blend available — the forward_projection cache "
            "is cold or the strategy cache is empty.")

    # Filter to non-zero strategies, sort by weight descending.
    rows: list[tuple[str, float, float, float]] = []
    for strategy, weight in sorted(
            blend_weights.items(), key=lambda kv: -float(kv[1] or 0)):
        w = float(weight or 0)
        if w <= 0:
            continue
        s = strategies.get(strategy) or {}
        eq = float(s.get("avg_equity_weight") or 0)
        bd = float(s.get("avg_bond_weight") or 0)
        rows.append((strategy, w, eq, bd))

    if not rows:
        return _empty_section(
            "Implied asset allocation",
            "  All strategy weights are zero — nothing to allocate.")

    # Portfolio total — weighted sum of each asset share by strategy
    # weight. With a fully-invested blend (sum w_i ≈ 1) and fully-
    # invested strategies (eq_i + bd_i ≈ 1) the totals also sum to ~1.
    total_eq = sum(w * eq for _, w, eq, _ in rows)
    total_bd = sum(w * bd for _, w, _, bd in rows)

    # HTML table — same inline style register as the other digest sections.
    html_rows = "".join(
        f"<tr><td style='padding:2px 6px;color:#cbd5e1'>{name}</td>"
        f"<td style='padding:2px 6px;text-align:right;color:#cbd5e1'>"
        f"{w * 100:.1f}%</td>"
        f"<td style='padding:2px 6px;text-align:right;color:#94a3b8'>"
        f"{eq * 100:.1f}%</td>"
        f"<td style='padding:2px 6px;text-align:right;color:#94a3b8'>"
        f"{bd * 100:.1f}%</td></tr>"
        for name, w, eq, bd in rows)
    html = (
        f"<h3 style='color:#cbd5e1;margin:0 0 8px 0;font-size:14px'>"
        f"Implied asset allocation</h3>"
        f"<table style='border-collapse:collapse;font-size:12px;"
        f"margin-bottom:6px'>"
        f"<thead><tr>"
        f"<th style='padding:2px 6px;text-align:left;color:#94a3b8'>Strategy</th>"
        f"<th style='padding:2px 6px;text-align:right;color:#94a3b8'>Weight</th>"
        f"<th style='padding:2px 6px;text-align:right;color:#94a3b8'>Equity</th>"
        f"<th style='padding:2px 6px;text-align:right;color:#94a3b8'>Bonds</th>"
        f"</tr></thead>"
        f"<tbody>{html_rows}"
        f"<tr style='border-top:1px solid #334155'>"
        f"<td style='padding:4px 6px;color:#fbbf24'><b>Portfolio total</b></td>"
        f"<td style='padding:4px 6px;text-align:right;color:#fbbf24'>"
        f"<b>100.0%</b></td>"
        f"<td style='padding:4px 6px;text-align:right;color:#fbbf24'>"
        f"<b>{total_eq * 100:.1f}%</b></td>"
        f"<td style='padding:4px 6px;text-align:right;color:#fbbf24'>"
        f"<b>{total_bd * 100:.1f}%</b></td></tr>"
        f"</tbody></table>"
        f"<div style='font-size:10px;color:#64748b'>"
        f"Bonds column is combined investment-grade + high-yield; the "
        f"per-strategy IG/HY tilt is not persisted to the strategy cache."
        f"</div>")

    text_lines = [
        "IMPLIED ASSET ALLOCATION",
        "  Strategy            Weight    Equity     Bonds",
    ]
    for name, w, eq, bd in rows:
        text_lines.append(
            f"  {name:<18}  {w * 100:>5.1f}%   {eq * 100:>5.1f}%   "
            f"{bd * 100:>5.1f}%")
    text_lines.append(
        f"  {'PORTFOLIO TOTAL':<18}  100.0%   {total_eq * 100:>5.1f}%   "
        f"{total_bd * 100:>5.1f}%")
    text_lines.append(
        "  (Bonds = IG + HY combined; per-strategy split not persisted.)")
    text = "\n".join(text_lines) + "\n"

    return DigestSection(
        title="Implied asset allocation",
        html=html, text=text)


# ── Section 6c — Latest CIO recommendation (June 5 2026) ─────────────────


async def _section_latest_cio_recommendation() -> DigestSection:
    """Pulls the most recent row from cio_recommendations and surfaces
    the `recommendation` field (capped at 300 words). When PR #273's
    A/B/C transparency structure is present in the cached prose the
    digest shows it verbatim (the cap leaves room for all three
    sections in typical cases). When the cache is cold or the table
    is empty, a placeholder section renders rather than the digest
    skipping it."""
    try:
        from tools.cio_recommendation import get_latest_recommendation
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_cio_recommendation_imports_failed",
                    error=str(exc))
        return _empty_section("CIO recommendation",
                              "  Section unavailable.")

    try:
        rec = await get_latest_recommendation()
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_cio_recommendation_read_failed", error=str(exc))
        rec = None

    if not rec:
        return _empty_section(
            "CIO recommendation",
            "  No CIO recommendation cached — the cio_recommendations "
            "table is empty.")

    # Prefer the recommendation field; fall back to signal for the
    # case where only the deterministic-fallback path has written.
    body = (rec.get("recommendation") or rec.get("signal") or "").strip()
    if not body:
        return _empty_section(
            "CIO recommendation",
            "  CIO recommendation row exists but carries no narrative "
            "text — likely an older schema. Re-run the recommendation "
            "pipeline.")

    truncated = _truncate_to_word_cap(body, 300)
    regime = rec.get("regime") or (rec.get("confidence") or {}).get("regime")
    computed_at = rec.get("computed_at") or ""

    meta_line = ""
    if regime:
        meta_line += f"Current regime: {regime}. "
    if computed_at:
        meta_line += f"Computed at: {computed_at}."

    # HTML — render the truncated body as a paragraph block (the prose
    # may contain markdown ### headings from PR #273; we surface them
    # verbatim, the email client will render the # characters as text).
    html = (
        f"<h3 style='color:#cbd5e1;margin:0 0 8px 0;font-size:14px'>"
        f"CIO recommendation</h3>"
        f"<div style='font-size:11px;color:#94a3b8;margin-bottom:6px'>"
        f"{meta_line}</div>"
        f"<pre style='white-space:pre-wrap;font-family:inherit;"
        f"font-size:12px;color:#cbd5e1;margin:0;padding:6px 8px;"
        f"background:#0f172a;border-left:2px solid #334155'>"
        f"{truncated}</pre>"
        + (
            "<div style='font-size:10px;color:#64748b;margin-top:4px'>"
            "Truncated to 300 words — full recommendation on the "
            "platform.</div>"
            if len(body.split()) > 300 else ""))
    text = "CIO RECOMMENDATION\n"
    if meta_line:
        text += f"  {meta_line}\n"
    text += "\n" + "\n".join(f"  {line}" for line in truncated.splitlines())
    if len(body.split()) > 300:
        text += (
            "\n  (Truncated to 300 words — full recommendation on the "
            "platform.)")
    text += "\n"

    return DigestSection(
        title="CIO recommendation", html=html, text=text)


# ── Section 6d — What would trigger a rebalance (June 5 2026) ────────────


async def _section_rebalance_triggers() -> DigestSection:
    """Two halves:

      1. Current regime signal values + the project's threshold
         constants — names the watch points and what direction they
         would have to move to flip the regime.
      2. The per-regime target blends — what the live blend would
         shift to on a BULL / BEAR / TRANSITION flip.

    Source 1 reads regime_signals_cache (15-min TTL, refreshed on every
    /api/regime/current call). Source 2 reads analytics_metrics_cache
    metric_kind='regime_blends' (refreshed by refresh_regime_blends in
    refresh_all_analytics, June 5 2026). Both fail-open."""
    try:
        from config import (
            BEAR_MARKET_THRESHOLD, CREDIT_SPREAD_WIDE,
            VIX_HIGH_THRESHOLD, YIELD_CURVE_INVERSION,
        )
        from tools.precomputed_analytics import get_latest_metric
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_rebalance_triggers_imports_failed",
                    error=str(exc))
        return _empty_section(
            "What would trigger a rebalance",
            "  Section unavailable.")

    # Read the latest regime_signals_cache row directly — ignoring the
    # 15-minute TTL that get_regime_cache() enforces. The digest runs
    # once per day; the cache is almost always stale by then. For the
    # watchpoints we want the LAST KNOWN signal values, not a refused
    # read on expiry. The values are informational and labelled "current
    # cached signal" so the reader isn't misled.
    signals = await _read_latest_regime_signals_for_digest()

    # Overlay live daily-vs-monthly HMM divergence. The cached row only
    # carries the daily HMM label; the monthly HMM label (which drives
    # the blend weights) lives only in the live detect_current_regime()
    # return. Pulling it here lets the digest surface the divergence
    # disclosure when the two models disagree. Fail-open: if the live
    # read errors, the digest reverts to silent behaviour.
    monthly_hmm_regime: str | None = None
    hmm_models_agree: bool = True
    try:
        from tools.regime_detector import detect_current_regime
        live = await asyncio.to_thread(detect_current_regime)
        monthly_hmm_regime = (live or {}).get("monthly_hmm_regime")
        hmm_models_agree = (live or {}).get("hmm_models_agree", True)
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_live_regime_unavailable", error=str(exc))

    try:
        regime_blends = await get_latest_metric("regime_blends") or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_rebalance_blends_read_failed", error=str(exc))
        regime_blends = {}

    # Trigger lines — each is (label, current value, comparator,
    # threshold, sentence template). Only render rows where the signal
    # is actually in the cache; a missing field is dropped silently
    # rather than rendered as "None".
    trigger_lines: list[tuple[str, str]] = []  # (html_li, text_line)
    vix = signals.get("vix_level")
    if vix is not None:
        trigger_lines.append((
            f"<li><b>VIX {float(vix):.2f}</b> — sustained rise above "
            f"<b>{VIX_HIGH_THRESHOLD}</b> would signal BEAR.</li>",
            f"    VIX {float(vix):.2f} — rise above {VIX_HIGH_THRESHOLD} "
            f"signals BEAR."))
    cs = signals.get("credit_spread")
    if cs is not None:
        trigger_lines.append((
            f"<li><b>Credit spread {float(cs):.2f}</b> — widening "
            f"above <b>{CREDIT_SPREAD_WIDE}</b> would signal stress.</li>",
            f"    Credit spread {float(cs):.2f} — widening above "
            f"{CREDIT_SPREAD_WIDE} signals stress."))
    yc = signals.get("yield_curve_slope")
    if yc is not None:
        trigger_lines.append((
            f"<li><b>Yield curve {float(yc):.2f}</b> — inversion "
            f"(below <b>{YIELD_CURVE_INVERSION}</b>) would signal BEAR.</li>",
            f"    Yield curve {float(yc):.2f} — inversion below "
            f"{YIELD_CURVE_INVERSION} signals BEAR."))
    eq_trend = signals.get("equity_trend")
    if eq_trend is not None:
        trigger_lines.append((
            f"<li><b>Equity trend {float(eq_trend):.2%}</b> — trailing "
            f"return below <b>{BEAR_MARKET_THRESHOLD:.0%}</b> would "
            f"signal BEAR.</li>",
            f"    Equity trend {float(eq_trend):.2%} — below "
            f"{BEAR_MARKET_THRESHOLD:.0%} signals BEAR."))
    hmm = signals.get("hmm_regime")
    if hmm is not None:
        trigger_lines.append((
            f"<li><b>HMM regime: {hmm}</b> — a shift to BULL or BEAR "
            f"triggers a rebalance.</li>",
            f"    HMM regime: {hmm} — shift to BULL or BEAR triggers "
            f"a rebalance."))

    # Daily-vs-monthly HMM divergence — surfaced ONLY when the two
    # models actually disagree. The live label (daily HMM) and the
    # blend regime (monthly HMM) can drift apart on different windows;
    # the digest must show that drift rather than letting the reader
    # infer agreement.
    divergence_lines: list[tuple[str, str]] = []
    if not hmm_models_agree and hmm and monthly_hmm_regime:
        divergence_lines.append((
            f"<li><b>Divergence:</b> live regime signal ({hmm}) diverges "
            f"from the blend regime ({monthly_hmm_regime}). Blend weights "
            f"reflect the monthly model; the live label reflects the "
            f"daily model.</li>",
            f"    Divergence: live regime signal ({hmm}) diverges from "
            f"the blend regime ({monthly_hmm_regime}). Blend weights "
            f"reflect the monthly model; the live label reflects the "
            f"daily model."))

    # Per-regime blend targets. The payload's `blends` dict is keyed
    # by regime name with strategy → weight values.
    blends = regime_blends.get("blends") or {}
    # Bridge (June 8 2026) -- compute the per-regime implied
    # equity/bond split + delta from the current portfolio so each
    # blend row carries the asset-class translation alongside the
    # strategy weights. compute_regime_blends_implied lives in
    # tools.cio_recommendation and reuses the same per-strategy
    # avg_equity_weight / avg_bond_weight the live implied-allocation
    # row uses. Fail-open: if either the regime_implied or the
    # live current_implied is unavailable, the digest renders the
    # strategy-weights row alone (pre-bridge behaviour).
    current_implied: dict[str, float] | None = None
    regime_implied: dict[str, dict[str, Any]] | None = None
    try:
        from tools.cio_recommendation import (
            compute_implied_asset_allocation,
            compute_regime_blends_implied,
        )
        # The live blend is implicitly the blend FOR the current regime;
        # the digest's signals dict carries hmm_regime / threshold_regime
        # but the actual blend weights live in regime_blends keyed by
        # the live regime label. detect_current_regime() is already in
        # the digest's context via signals; fall through to None on any
        # missing key.
        live_regime = signals.get("hmm_regime")
        live_blend = blends.get(live_regime) if live_regime else None
        if live_blend:
            current_implied = await compute_implied_asset_allocation(
                live_blend)
        regime_implied = await compute_regime_blends_implied(
            blends, current_implied)
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_blend_implied_failed", error=str(exc))

    def _fmt_pp(value: float) -> str:
        # Explicit-sign percentage-points formatter -- "+35.6pp" /
        # "-35.6pp" so the reader sees direction at a glance.
        return f"{'+' if value >= 0 else ''}{value:.1f}pp"

    blend_lines: list[tuple[str, str]] = []  # (html_li, text_line)
    for regime in ("BULL", "BEAR", "TRANSITION"):
        weights = blends.get(regime) or {}
        if not weights:
            continue
        # Format as "STRAT_1 35%, STRAT_2 25%, ..." — top 3 only to
        # keep the email readable.
        top = sorted(weights.items(), key=lambda kv: -float(kv[1] or 0))[:3]
        weight_str = ", ".join(
            f"{name} {float(w) * 100:.0f}%" for name, w in top
            if float(w or 0) > 0)
        if not weight_str:
            continue

        # Sub-lines: implied equity/bonds and delta vs current.
        sub_html: list[str] = []
        sub_text: list[str] = []
        entry = (regime_implied or {}).get(regime) or {}
        eq_pct = entry.get("equity_pct")
        bd_pct = entry.get("bond_pct")
        if isinstance(eq_pct, (int, float)) and isinstance(bd_pct, (int, float)):
            implied_str = (
                f"Equity {float(eq_pct) * 100:.1f}% | "
                f"Bonds {float(bd_pct) * 100:.1f}%")
            sub_html.append(
                f"<div style='margin-left:14px;color:#cbd5e1;"
                f"font-size:11px'>{implied_str}</div>")
            sub_text.append(f"      {implied_str}")
        dq = entry.get("equity_delta_pp")
        db = entry.get("bond_delta_pp")
        if isinstance(dq, (int, float)) and isinstance(db, (int, float)):
            delta_str = (
                f"vs today: Equity {_fmt_pp(float(dq))} | "
                f"Bonds {_fmt_pp(float(db))}")
            sub_html.append(
                f"<div style='margin-left:14px;color:#94a3b8;"
                f"font-size:11px'>{delta_str}</div>")
            sub_text.append(f"      {delta_str}")

        blend_lines.append((
            f"<li><b>{regime}:</b> {weight_str}"
            + "".join(sub_html)
            + "</li>",
            f"    {regime}: {weight_str}"
            + ("\n" + "\n".join(sub_text) if sub_text else "")))

    if not trigger_lines and not blend_lines and not divergence_lines:
        return _empty_section(
            "What would trigger a rebalance",
            "  No regime signals or blend targets cached. Run the "
            "warm-cache refresh.")

    html_parts: list[str] = []
    text_parts: list[str] = []

    if trigger_lines:
        html_parts.append(
            f"<div style='font-size:12px;color:#cbd5e1;margin-bottom:4px'>"
            f"<b>Watch points</b></div>"
            f"<ul style='padding-left:18px;margin:0 0 8px 0;"
            f"font-size:12px;color:#94a3b8'>"
            + "".join(html for html, _ in trigger_lines)
            + "</ul>")
        text_parts.append("  Watch points:")
        text_parts.extend(text for _, text in trigger_lines)
    if divergence_lines:
        html_parts.append(
            f"<div style='font-size:12px;color:#fca5a5;margin-bottom:4px'>"
            f"<b>Model divergence</b></div>"
            f"<ul style='padding-left:18px;margin:0 0 8px 0;"
            f"font-size:12px;color:#fca5a5'>"
            + "".join(html for html, _ in divergence_lines)
            + "</ul>")
        text_parts.append("  Model divergence:")
        text_parts.extend(text for _, text in divergence_lines)
    if blend_lines:
        html_parts.append(
            f"<div style='font-size:12px;color:#cbd5e1;margin-bottom:4px'>"
            f"<b>Blend shift on regime flip</b></div>"
            f"<ul style='padding-left:18px;margin:0;"
            f"font-size:12px;color:#94a3b8'>"
            + "".join(html for html, _ in blend_lines)
            + "</ul>")
        text_parts.append("  Blend shift on regime flip:")
        text_parts.extend(text for _, text in blend_lines)

    html = (
        f"<h3 style='color:#cbd5e1;margin:0 0 8px 0;font-size:14px'>"
        f"What would trigger a rebalance</h3>"
        + "".join(html_parts))
    text = ("WHAT WOULD TRIGGER A REBALANCE\n"
            + "\n".join(text_parts) + "\n")
    return DigestSection(
        title="What would trigger a rebalance",
        html=html, text=text)


# ── Shared helpers for the new sections (June 5 2026) ────────────────────


def _empty_section(title: str, body_text: str) -> DigestSection:
    """A uniform placeholder so each new section renders SOMETHING even
    on a cold cache. The reader sees the section header (so they know
    the cron fired and didn't skip it) plus a one-line reason."""
    html = (
        f"<h3 style='color:#cbd5e1;margin:0 0 8px 0;font-size:14px'>"
        f"{title}</h3>"
        f"<div style='font-size:12px;color:#64748b'>"
        f"{body_text.strip()}</div>")
    text = f"{title.upper()}\n{body_text}\n"
    return DigestSection(title=title, html=html, text=text)


async def _read_latest_regime_signals_for_digest() -> dict:
    """Returns the latest regime_signals_cache row WITHOUT the 15-minute
    TTL check that tools.cache.get_regime_cache enforces. The digest
    runs once per day, so the cache is almost always stale by then —
    refusing on expiry would render the rebalance-triggers section
    blank every morning. Stale values are fine for an informational
    watch list. Fail-open to an empty dict on any DB error / missing
    table."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal  # type: ignore
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "digest_regime_signals_imports_failed", error=str(exc))
        return {}
    if AsyncSessionLocal is None:  # type: ignore[comparison-overlap]
        return {}
    try:
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            row = await session.execute(text(
                "SELECT vix_level, yield_curve_slope, credit_spread, "
                "       equity_trend, hmm_regime, fetched_at "
                "FROM regime_signals_cache "
                "ORDER BY fetched_at DESC LIMIT 1"))
            r = row.fetchone()
            if not r:
                return {}
            return {
                "vix_level":         r[0],
                "yield_curve_slope": r[1],
                "credit_spread":     r[2],
                "equity_trend":      r[3],
                "hmm_regime":        r[4],
                "fetched_at":        str(r[5]) if r[5] else None,
            }
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "digest_regime_signals_read_failed", error=str(exc))
        return {}


def _truncate_to_word_cap(text: str, cap: int) -> str:
    """Word-bound truncation with an ellipsis when shortened. Preserves
    internal whitespace by splitting on whitespace and rejoining with
    single spaces — the recommendation prose has markdown ### headings
    which read fine with normalised whitespace inside an email."""
    words = text.split()
    if len(words) <= cap:
        return text
    return " ".join(words[:cap]) + "…"


# ── Section 7 — Open work ─────────────────────────────────────────────────


async def _section_open_work() -> DigestSection:
    """Invariant SOFT WARNINGS (hard failures go to Component 2,
    not here) + failing CI on open branches via the GitHub API."""
    soft_warnings: list[dict] = []
    try:
        from tools.invariant_checks import get_latest_result
        inv = get_latest_result() or {}
        soft_warnings = [
            v for v in (inv.get("violations") or [])
            if v.get("severity") == "soft"
        ]
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_invariant_soft_read_failed", error=str(exc))

    failing_prs: list[tuple[int, str]] = await _fetch_failing_open_prs()

    html_parts: list[str] = []
    text_parts: list[str] = []

    if soft_warnings:
        html_parts.append(
            f"<div style='font-size:12px;color:#cbd5e1;margin-bottom:4px'>"
            f"<b>Invariant soft warnings</b></div>"
            f"<ul style='padding-left:18px;margin:0 0 8px 0;"
            f"font-size:12px;color:#94a3b8'>"
            + "".join(
                f"<li><code style='color:#fbbf24'>{v.get('code')}</code> "
                f"<span style='color:#cbd5e1'>{v.get('entity')}</span> &mdash; "
                f"{v.get('detail')}</li>"
                for v in soft_warnings[:10])
            + "</ul>")
        text_parts.append("  Invariant soft warnings:")
        for v in soft_warnings[:10]:
            text_parts.append(
                f"    [{v.get('code')}] {v.get('entity')}: "
                f"{v.get('detail')}")
    if failing_prs:
        html_parts.append(
            f"<div style='font-size:12px;color:#cbd5e1;margin-bottom:4px'>"
            f"<b>Open PRs with failing CI</b></div>"
            f"<ul style='padding-left:18px;margin:0;"
            f"font-size:12px;color:#94a3b8'>"
            + "".join(
                f"<li>#{num} &mdash; {title}</li>"
                for num, title in failing_prs[:10])
            + "</ul>")
        text_parts.append("  Open PRs with failing CI:")
        for num, title in failing_prs[:10]:
            text_parts.append(f"    #{num} — {title}")

    if not html_parts:
        html_parts.append(
            "<div style='font-size:12px;color:#64748b'>"
            "No invariant warnings; all open PRs passing.</div>")
        text_parts.append(
            "  No invariant warnings; all open PRs passing.")

    html = (
        f"<h3 style='color:#cbd5e1;margin:0 0 8px 0;font-size:14px'>"
        f"Open work</h3>"
        + "".join(html_parts))
    text = "OPEN WORK\n" + "\n".join(text_parts) + "\n"
    return DigestSection(title="Open work", html=html, text=text)


async def _fetch_failing_open_prs() -> list[tuple[int, str]]:
    """Calls the GitHub API to enumerate open PRs and check their
    check-run conclusion. Skipped silently when GITHUB_TOKEN is
    absent. Bounded to 20 PRs to keep the API call cheap."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get(
        "GH_TOKEN")
    if not token:
        return []
    repo = os.environ.get("GITHUB_REPO") or "saffamiker/forest-capital"
    out: list[tuple[int, str]] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"https://api.github.com/repos/{repo}/pulls",
                params={"state": "open", "per_page": 20},
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json"})
            if r.status_code != 200:
                log.warning("digest_github_prs_failed",
                            status=r.status_code)
                return []
            prs = r.json() or []
            for pr in prs:
                num = pr.get("number")
                sha = (pr.get("head") or {}).get("sha")
                title = pr.get("title") or ""
                if not (num and sha):
                    continue
                # Cheaper check: combined status for the PR head sha.
                cr = await client.get(
                    f"https://api.github.com/repos/{repo}/commits/"
                    f"{sha}/check-runs",
                    headers={"Authorization": f"Bearer {token}",
                             "Accept": "application/vnd.github+json"})
                if cr.status_code != 200:
                    continue
                runs = (cr.json() or {}).get("check_runs") or []
                if any(run.get("conclusion") == "failure"
                       for run in runs):
                    out.append((num, title))
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_github_check_fetch_failed", error=str(exc))
    return out


# ── Section 8 — Deadline tracker ──────────────────────────────────────────


def _section_deadlines(today: date | None = None) -> DigestSection:
    today = today or date.today()
    rows_html: list[str] = []
    rows_text: list[str] = []
    for label, when in _DEADLINES:
        days = (when - today).days
        if days < 0:
            badge = "past"
            color = "#64748b"
        elif days <= 7:
            badge = f"in {days} day{'s' if days != 1 else ''}"
            color = "#ef4444"
        elif days <= 30:
            badge = f"in {days} days"
            color = "#f59e0b"
        else:
            badge = f"in {days} days"
            color = "#22c55e"
        rows_html.append(
            f"<tr>"
            f"<td style='padding:4px 8px;color:#cbd5e1'>{label}</td>"
            f"<td style='padding:4px 8px;color:#94a3b8'>"
            f"{when.isoformat()}</td>"
            f"<td style='padding:4px 8px;color:{color};text-align:right'>"
            f"{badge}</td>"
            f"</tr>")
        rows_text.append(f"  {label:<40}  {when.isoformat()}  ({badge})")

    html = (
        f"<h3 style='color:#cbd5e1;margin:0 0 8px 0;font-size:14px'>"
        f"Deadlines</h3>"
        f"<table cellspacing='0' style='font-size:12px;"
        f"font-family:monospace'>{''.join(rows_html)}</table>")
    text = "DEADLINES\n" + "\n".join(rows_text) + "\n"
    return DigestSection(title="Deadlines", html=html, text=text)


# ── Top-level assembler ───────────────────────────────────────────────────


async def _alembic_head_from_db() -> str:
    """Reads the current migration head from the alembic_version
    table — the SOURCE OF TRUTH for what migrations have actually
    landed, not what migration files happen to exist on disk.

    Returns the version_num string on success, "unavailable" when
    the query fails for any reason (DB unreachable, table missing,
    permission denied). The previous filesystem-parse implementation
    was unreliable on Render's container layout — switched to the
    direct DB query per the June 2 2026 user directive."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return "unavailable"
        async with AsyncSessionLocal() as session:
            row = await session.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1"))
            r = row.fetchone()
            if r and r[0]:
                return str(r[0])
        return "unavailable"
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_alembic_head_db_query_failed",
                    error=str(exc))
        return "unavailable"


async def build_digest_email() -> tuple[str, str, str]:
    """Returns (subject, html, text). All sections fail-open so a
    single missing source does not abort the digest. The HTML uses
    a dark-navy-on-white theme matching the platform; the text
    fallback is plain ASCII."""
    today = date.today()
    sections: list[DigestSection] = []
    # June 7 2026 (bridge #84) — analytics-decision content leads the
    # digest. The reader's first scroll is what to do (CIO
    # recommendation), how to express it (implied asset allocation),
    # when to revisit (rebalance watch points), and what the live
    # state is (analytics snapshot — current regime, live blend
    # weights, OOS Sharpe). Platform health and ops content sit
    # below so the morning skim surfaces the allocation call first;
    # the operator goes deeper for health / activity only when
    # something there matters. Release history / commit summaries
    # sit at the very bottom — they are useful as a "what shipped
    # since I last looked" footer, not as front-page material.
    sections.append(await _section_latest_cio_recommendation())
    sections.append(await _section_implied_asset_allocation())
    sections.append(await _section_rebalance_triggers())
    sections.append(await _section_analytics_snapshot())
    sections.append(await _section_platform_health())
    sections.append(await _section_platform_usage())
    sections.append(await _section_team_activity())
    sections.append(await _section_warm_history())
    sections.append(await _section_open_work())
    sections.append(_section_deadlines(today))
    sections.append(await _section_releases())

    subject = (
        f"AnalyticsDesk daily digest — {today.isoformat()}")

    body_html = "".join(
        f"<section style='margin:0 0 20px 0'>{s.html}</section>"
        for s in sections)
    html = (
        f"<!doctype html><html><body style='"
        f"background:#0a0e1a;color:#cbd5e1;font-family:"
        f"-apple-system,Helvetica,Arial,sans-serif;"
        f"padding:24px;margin:0'>"
        f"<div style='max-width:640px;margin:0 auto;background:#0d1424;"
        f"padding:24px;border-radius:8px;border:1px solid #1e2d47'>"
        f"<h2 style='color:#cbd5e1;margin:0 0 16px 0;font-size:16px'>"
        f"AnalyticsDesk — daily digest</h2>"
        f"<div style='color:#64748b;font-size:11px;margin-bottom:16px'>"
        f"{today.strftime('%A, %B %d %Y')} &nbsp;·&nbsp; "
        f"automated; reply to this address is not monitored"
        f"</div>"
        f"{body_html}"
        f"<div style='color:#64748b;font-size:10px;margin-top:24px;"
        f"padding-top:12px;border-top:1px solid #1e2d47'>"
        f"This digest is sent daily at 07:00 ET via Render cron. "
        f"To stop receiving it, update DIGEST_RECIPIENTS on Render "
        f"or unsubscribe via the platform admin panel."
        f"</div>"
        f"</div></body></html>")
    text = "\n".join(s.text for s in sections) + (
        "\n---\nAutomated daily digest. To stop receiving it, update "
        "DIGEST_RECIPIENTS on Render or unsubscribe via the platform "
        "admin panel.\n")
    return subject, html, text


async def send_daily_digest() -> dict[str, Any]:
    """Builds the digest and sends it to every address in
    DIGEST_RECIPIENTS. Returns a dict suitable for the admin endpoint
    response: {sent: bool, message_id, recipients, error?}.

    Fail-open: any exception in assembly falls back to a minimal
    "digest failed to assemble" email so Michael is still notified
    that the cron fired but produced nothing useful."""
    try:
        subject, html, text = await build_digest_email()
    except Exception as exc:  # noqa: BLE001
        log.error("digest_assembly_failed", error=str(exc))
        subject = "AnalyticsDesk daily digest — ASSEMBLY FAILED"
        html = (
            f"<html><body><p>Digest assembly failed with: "
            f"<code>{exc}</code></p>"
            f"<p>Check the Render log for stack trace.</p></body></html>")
        text = f"Digest assembly failed: {exc}\nSee Render log.\n"
    recipients = digest_recipients()
    if not recipients:
        log.warning("digest_skipped_no_recipients")
        return {"sent": False, "recipients": [],
                "error": "no recipients (DIGEST_RECIPIENTS unset)"}
    msg_id = send_email(
        to=recipients, subject=subject, html=html, text=text,
        tag="daily-digest", sender=DIGEST_FROM)
    return {
        "sent":        msg_id is not None,
        "message_id":  msg_id,
        "recipients":  recipients,
        "subject":     subject,
    }
