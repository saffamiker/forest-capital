"""tools/email_digest.py — daily team digest assembler.

Component 1 of the automated email system. Fires at 07:00 ET via the
Render cron `forest-capital-digest` and lands in the inboxes of every
address in DIGEST_RECIPIENTS (Michael, Bob, Molly). Mirrors the
content the user spec'd on May 31 2026:

  1. Platform health summary
  2. Recent platform releases (last 24h)
  3. Key analytics snapshot
  4. Open work
  5. Deadline tracker

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

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import httpx
import structlog

from tools.email_resend import digest_recipients, send_email

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


def _section_platform_health() -> DigestSection:
    """Cache warm states (6 of them) + last warm timestamp +
    invariant verdict + alembic head."""
    try:
        from tools.cache_warm_state import get_warm_state
        warm = get_warm_state().to_dict()
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_warm_state_failed", error=str(exc))
        warm = {"status": "unknown", "last_success_at": None,
                "last_landed": {}, "last_attempt_error": str(exc)}

    try:
        from tools.invariant_checks import get_latest_result
        inv = get_latest_result()
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_invariant_read_failed", error=str(exc))
        inv = None

    try:
        head = _alembic_head()
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_alembic_head_failed", error=str(exc))
        head = "unknown"

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

    # Blend weights — cio_recommendations cache (cio_recommendation
    # module). Falls back to None silently.
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            r = await session.execute(text(
                "SELECT weights FROM cio_recommendations "
                "ORDER BY computed_at DESC LIMIT 1"))
            row = r.fetchone()
            if row and row[0]:
                weights = row[0]
                if isinstance(weights, str):
                    try:
                        weights = json.loads(weights)
                    except Exception:  # noqa: BLE001
                        weights = {}
                blend_weights = weights or {}
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


# ── Section 4 — Open work ─────────────────────────────────────────────────


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


# ── Section 5 — Deadline tracker ──────────────────────────────────────────


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


def _alembic_head() -> str:
    """Reads the alembic head SHA without invoking the CLI — parses
    backend/migrations/versions/*.py for the highest revision id.
    Conservative: any failure returns 'unknown'."""
    try:
        from pathlib import Path
        d = Path(__file__).resolve().parents[1] / "migrations" / "versions"
        revs: list[str] = []
        for p in d.glob("*.py"):
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("revision ="):
                    rid = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if rid:
                        revs.append(rid)
                    break
        if not revs:
            return "unknown"
        # Migration ids are zero-padded 3-digit strings; lexical sort works.
        return max(revs)
    except Exception:  # noqa: BLE001
        return "unknown"


async def build_digest_email() -> tuple[str, str, str]:
    """Returns (subject, html, text). All sections fail-open so a
    single missing source does not abort the digest. The HTML uses
    a dark-navy-on-white theme matching the platform; the text
    fallback is plain ASCII."""
    today = date.today()
    sections: list[DigestSection] = []
    sections.append(_section_platform_health())
    sections.append(await _section_releases())
    sections.append(await _section_analytics_snapshot())
    sections.append(await _section_open_work())
    sections.append(_section_deadlines(today))

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
        tag="daily-digest")
    return {
        "sent":        msg_id is not None,
        "message_id":  msg_id,
        "recipients":  recipients,
        "subject":     subject,
    }
