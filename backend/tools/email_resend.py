"""tools/email_resend.py — shared Resend email helper.

The platform sends two non-auth email types from cron jobs:
  - the daily team digest (Component 1)
  - the immediate hard-failure / soft-warning alert (Component 2)

Both reuse the Resend SDK pattern auth.py established for magic
links. This module is the single seam through which non-auth email
goes out, so:
  - the test environment intercepts here (no network);
  - production failures (missing key, invalid sender, rate limit)
    surface in one structured-log location;
  - a future contributor swapping the email provider touches one
    file, not three.

send_email() returns the Resend message id on success, None on
failure, and never raises — the caller logs and moves on. Email
infrastructure failure must not abort a warm or a cron run.
"""
from __future__ import annotations

import os
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# Default sender for callers that don't pass `sender=` explicitly.
# Matches the verified Resend domain. Override via RESEND_FROM_EMAIL
# if the verified address changes.
#
# June 2 2026 — moved from noreply@ to digest@ because the daily
# digest is the primary scheduled email and a per-purpose mailbox
# makes Resend's reply / bounce routing legible (a reply to a digest
# lands in the digest inbox, an alert reply lands in the alert
# inbox). The Render RESEND_FROM_EMAIL env var is updated alongside
# this change; the code fallback exists only for dev/test where the
# env var is unset.
_DEFAULT_FROM = "digest@analyticsdesk.app"

# Per-purpose sender addresses. The daily digest and immediate
# invariant alert each send from a dedicated mailbox so a reply or
# a bounce routes to the right place. Both addresses must be added
# and verified in Resend under the analyticsdesk.app domain before
# production sending will succeed.
DIGEST_FROM = "digest@analyticsdesk.app"
ALERT_FROM = "alerts@analyticsdesk.app"


def _is_dev_or_test() -> bool:
    return (os.environ.get("ENVIRONMENT") or "").lower() in (
        "development", "test")


def send_email(
    *,
    to: list[str] | str,
    subject: str,
    html: str,
    text: str | None = None,
    tag: str = "platform-email",
    sender: str | None = None,
) -> str | None:
    """Sends an HTML email via Resend with a plain-text fallback.

    In development / test: prints a one-line summary to stdout and
    returns a fake "dev-" message id. Production: calls Resend and
    returns the real message id, or None on any failure.

    `tag` is a structured-log marker so the source of an email lands
    in a single log column — `digest`, `alert`, `test-alert`, etc.

    `sender` overrides the From address for this single call. Used by
    the digest and the invariant alert paths so each email type sends
    from a dedicated mailbox (DIGEST_FROM / ALERT_FROM). Callers that
    leave it None fall back to the RESEND_FROM_EMAIL env var, then to
    _DEFAULT_FROM — that path covers auth.py's magic-link and welcome
    emails, which share the platform's generic sender.
    """
    recipients: list[str] = [to] if isinstance(to, str) else list(to)
    if not recipients:
        log.warning("email_send_skipped_no_recipients", tag=tag)
        return None
    from_addr = (
        sender
        or os.environ.get("RESEND_FROM_EMAIL")
        or _DEFAULT_FROM)

    if _is_dev_or_test():
        # Dev path mirrors auth.send_magic_link — print a banner
        # rather than swallowing silently. The deterministic id
        # lets tests assert the call shape.
        print(f"\n{'=' * 64}")
        print(f"  [DEV] Email -> {', '.join(recipients)}")
        print(f"  From:    {from_addr}")
        print(f"  Subject: {subject}")
        print(f"  Tag:     {tag}")
        print(f"  HTML chars: {len(html)} | Text chars: {len(text or '')}")
        print(f"{'=' * 64}\n")
        log.info("email_dev_printed", tag=tag,
                 recipients=recipients, subject=subject,
                 sender=from_addr)
        return f"dev-{tag}"

    try:
        import resend
        resend.api_key = os.environ.get("RESEND_API_KEY")
        params: dict[str, Any] = {
            "from":    from_addr,
            "to":      recipients,
            "subject": subject,
            "html":    html,
        }
        if text:
            params["text"] = text
        result = resend.Emails.send(params)
        msg_id = (result or {}).get("id")
        log.info("email_sent", tag=tag, recipients=recipients,
                 subject=subject, message_id=msg_id, sender=from_addr)
        return msg_id
    except Exception as exc:  # noqa: BLE001
        # The auth.py pattern extracts the Resend error detail; reuse
        # it so a misconfigured API key / unverified sender surfaces
        # the same way magic-link failures do.
        try:
            from auth import _resend_error_detail
            detail = _resend_error_detail(exc)
        except Exception:  # noqa: BLE001
            detail = {}
        log.error("email_send_failed", tag=tag,
                  recipients=recipients, subject=subject,
                  error=str(exc), **detail)
        return None


def digest_recipients() -> list[str]:
    """Reads DIGEST_RECIPIENTS env var — comma-separated list of the
    team-digest recipients. In dev / test, falls back to a single
    placeholder so the assembler doesn't drop the send."""
    raw = os.environ.get("DIGEST_RECIPIENTS", "").strip()
    if raw:
        return [e.strip() for e in raw.split(",") if e.strip()]
    if _is_dev_or_test():
        return ["digest-dev@analyticsdesk.app"]
    return []


def alert_recipient() -> str | None:
    """Reads ALERT_RECIPIENT env var — Michael's single email for
    immediate hard-failure / soft-warning alerts. In dev / test, a
    placeholder so the assembler doesn't drop the send."""
    raw = os.environ.get("ALERT_RECIPIENT", "").strip()
    if raw:
        return raw
    if _is_dev_or_test():
        return "alert-dev@analyticsdesk.app"
    return None
