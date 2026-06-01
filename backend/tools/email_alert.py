"""tools/email_alert.py — immediate invariant-violation alert.

Component 2 of the automated email system. Fires the moment the
invariant framework records ANY violation (hard failure OR soft
warning) during a warm. Recipient: Michael only (ALERT_RECIPIENT
env var), so the team digest at 07:00 ET stays the team-facing
channel and the alert stays the operator-facing channel.

The hook lives at the end of tools/invariant_checks.run_all_
invariants — it fires a fire-and-forget background task after the
result is finalised. The send is fail-open: a Resend outage or a
missing ALERT_RECIPIENT env var logs a warning and returns; never
raises, never re-enters the invariant runner.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from tools.email_resend import alert_recipient, send_email

log = structlog.get_logger(__name__)


def _severity_label(severity: str, warm_aborted: bool) -> tuple[str, str]:
    """Returns (badge_text, badge_color)."""
    if severity == "hard":
        return ("HARD FAILURE — warm aborted, cache unchanged"
                if warm_aborted
                else "HARD FAILURE",
                "#ef4444")
    return ("WARNING — warm completed, review recommended",
            "#f59e0b")


def build_alert_email(
    invariant_result: dict[str, Any],
    *,
    warm_aborted: bool | None = None,
) -> tuple[str, str, str] | None:
    """Returns (subject, html, text), or None when there's nothing to
    alert about (the result carries no violations). `warm_aborted` is
    inferred from `invariant_result.passed`: a non-passing result
    aborts the cache write at the set_strategy_cache pre-write gate."""
    violations = invariant_result.get("violations") or []
    if not violations:
        return None

    hf = invariant_result.get("hard_failures", 0)
    sw = invariant_result.get("soft_warnings", 0)
    passed_overall = invariant_result.get("passed", True)
    if warm_aborted is None:
        warm_aborted = not passed_overall
    ran_at = invariant_result.get("ran_at")

    # Subject — leads with severity so the inbox sort puts hard
    # failures on top, soft warnings underneath. User-spec'd.
    if hf > 0:
        subject = ("[ALERT] analyticsdesk data issue detected "
                   "— action required")
    else:
        subject = ("[WARNING] analyticsdesk invariant warnings "
                   "— review recommended")

    # Sort violations: hard first, then by code so the same code
    # clusters across strategies.
    sev_order = {"hard": 0, "soft": 1}
    sorted_vios = sorted(
        violations,
        key=lambda v: (sev_order.get(v.get("severity"), 99),
                       v.get("code") or "", v.get("entity") or ""))

    rows_html: list[str] = []
    rows_text: list[str] = []
    for v in sorted_vios:
        severity = v.get("severity") or "?"
        badge_text, color = _severity_label(severity, warm_aborted)
        code = v.get("code") or "?"
        entity = v.get("entity") or "—"
        metric = v.get("metric") or "—"
        expected = v.get("expected") or ""
        actual = v.get("actual") or ""
        detail = v.get("detail") or ""

        rows_html.append(
            f"<tr style='border-top:1px solid #1e2d47'>"
            f"<td style='padding:8px;vertical-align:top;width:90px'>"
            f"<code style='color:{color};font-weight:600'>{code}</code><br/>"
            f"<span style='color:{color};font-size:10px'>{badge_text}</span>"
            f"</td>"
            f"<td style='padding:8px;vertical-align:top'>"
            f"<div style='color:#cbd5e1;font-size:12px;margin-bottom:4px'>"
            f"<b>{entity}</b> &nbsp;·&nbsp; metric "
            f"<code>{metric}</code></div>"
            f"<div style='color:#94a3b8;font-size:11px;margin-bottom:2px'>"
            f"Expected: <code>{expected}</code></div>"
            f"<div style='color:#94a3b8;font-size:11px;margin-bottom:4px'>"
            f"Actual: <code>{actual}</code></div>"
            f"<div style='color:#64748b;font-size:11px;font-style:italic'>"
            f"{detail}</div>"
            f"</td>"
            f"</tr>")
        rows_text.append(
            f"[{code}] {severity.upper()} — {entity} / {metric}\n"
            f"  Expected: {expected}\n"
            f"  Actual:   {actual}\n"
            f"  Detail:   {detail}\n")

    severity_line_html = (
        f"<div style='font-size:13px;color:#cbd5e1;margin-bottom:12px'>"
        f"<b>{hf}</b> hard failure(s) &nbsp;·&nbsp; "
        f"<b>{sw}</b> soft warning(s) "
        f"&nbsp;·&nbsp; warm "
        f"<b style='color:{'#ef4444' if warm_aborted else '#22c55e'}'>"
        f"{'ABORTED' if warm_aborted else 'COMPLETED'}</b>"
        f"</div>")
    cache_state_line = (
        "Cache state: previous row preserved (no write occurred)."
        if warm_aborted else
        "Cache state: written. Review the warnings and decide whether "
        "to roll back.")

    html = (
        f"<!doctype html><html><body style='background:#0a0e1a;"
        f"color:#cbd5e1;font-family:-apple-system,Helvetica,Arial,"
        f"sans-serif;padding:24px;margin:0'>"
        f"<div style='max-width:720px;margin:0 auto;background:#0d1424;"
        f"padding:24px;border-radius:8px;border:1px solid #1e2d47'>"
        f"<h2 style='color:#cbd5e1;margin:0 0 6px 0;font-size:16px'>"
        f"AnalyticsDesk — data integrity alert</h2>"
        f"<div style='color:#64748b;font-size:11px;margin-bottom:16px'>"
        f"Detected at <code>{ran_at or 'unknown'}</code> "
        f"&nbsp;·&nbsp; automated"
        f"</div>"
        f"{severity_line_html}"
        f"<div style='font-size:12px;color:#94a3b8;margin-bottom:16px'>"
        f"{cache_state_line}</div>"
        f"<table cellspacing='0' style='width:100%;border:1px solid "
        f"#1e2d47;border-collapse:collapse'>"
        f"{''.join(rows_html)}"
        f"</table>"
        f"<div style='color:#64748b;font-size:10px;margin-top:24px;"
        f"padding-top:12px;border-top:1px solid #1e2d47'>"
        f"This alert is sent only when a data integrity issue is "
        f"detected. To stop receiving alerts, update your notification "
        f"settings in the platform admin panel."
        f"</div>"
        f"</div></body></html>")
    text = (
        f"ANALYTICSDESK — DATA INTEGRITY ALERT\n"
        f"Detected at: {ran_at or 'unknown'}\n"
        f"Severity: {hf} hard failure(s), {sw} soft warning(s)\n"
        f"Warm: {'ABORTED — cache unchanged' if warm_aborted else 'completed'}\n"
        f"{cache_state_line}\n\n"
        + "\n".join(rows_text)
        + "\n---\nAutomated alert. To stop receiving alerts, update your "
          "notification settings in the platform admin panel.\n")
    return subject, html, text


def send_alert(
    invariant_result: dict[str, Any],
    *,
    warm_aborted: bool | None = None,
) -> dict[str, Any]:
    """Builds + sends the alert email synchronously. Returns a dict
    suitable for the admin endpoint response. Fail-open: a missing
    ALERT_RECIPIENT or a Resend outage logs + returns; never raises."""
    built = build_alert_email(
        invariant_result, warm_aborted=warm_aborted)
    if built is None:
        return {"sent": False, "reason": "no violations — nothing to alert"}
    subject, html, text = built
    to = alert_recipient()
    if not to:
        log.warning("alert_skipped_no_recipient")
        return {"sent": False, "reason": "ALERT_RECIPIENT unset"}
    msg_id = send_email(
        to=to, subject=subject, html=html, text=text,
        tag="invariant-alert")
    return {
        "sent":       msg_id is not None,
        "message_id": msg_id,
        "recipient":  to,
        "subject":    subject,
    }


def send_test_alert() -> dict[str, Any]:
    """Builds a synthetic alert payload and sends it. Backs the
    POST /api/v1/admin/test-alert endpoint."""
    synthetic = {
        "passed":        False,
        "checks_run":    22,
        "hard_failures": 1,
        "soft_warnings": 1,
        "ran_at":        datetime.now(timezone.utc).isoformat(),
        "violations": [
            {
                "code":     "1a", "severity": "hard", "category": 1,
                "entity":   "BENCHMARK/COVID_Crash_2020",
                "metric":   "cumulative_return",
                "expected": "|return| ≤ |full-period max DD| = 0.5256",
                "actual":   "|−0.7353| = 0.7353",
                "detail":   ("Synthetic alert for testing. A crisis-window "
                             "cumulative loss cannot exceed the strategy's "
                             "worst-ever loss across the full sample."),
            },
            {
                "code":     "4a", "severity": "soft", "category": 4,
                "entity":   "VOL_TARGETING/GFC_2008-2009",
                "metric":   "cumulative_return",
                "expected": "VOL_TARGETING return > benchmark −0.4566",
                "actual":   "−0.5000",
                "detail":   ("Synthetic alert for testing. A "
                             "volatility-targeting strategy lost more "
                             "than the benchmark in a crash window — "
                             "investigate the regime-detection / "
                             "scaling logic for this window."),
            },
        ],
    }
    return send_alert(synthetic, warm_aborted=True)
