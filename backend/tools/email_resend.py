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


# Default sender — matches the verified Resend domain. Override via
# RESEND_FROM_EMAIL if the verified address changes.
_DEFAULT_FROM = "noreply@analyticsdesk.app"


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
) -> str | None:
    """Sends an HTML email via Resend with a plain-text fallback.

    In development / test: prints a one-line summary to stdout and
    returns a fake "dev-" message id. Production: calls Resend and
    returns the real message id, or None on any failure.

    `tag` is a structured-log marker so the source of an email lands
    in a single log column — `digest`, `alert`, `test-alert`, etc.
    """
    recipients: list[str] = [to] if isinstance(to, str) else list(to)
    if not recipients:
        log.warning("email_send_skipped_no_recipients", tag=tag)
        return None

    if _is_dev_or_test():
        # Dev path mirrors auth.send_magic_link — print a banner
        # rather than swallowing silently. The deterministic id
        # lets tests assert the call shape.
        print(f"\n{'=' * 64}")
        print(f"  [DEV] Email -> {', '.join(recipients)}")
        print(f"  Subject: {subject}")
        print(f"  Tag:     {tag}")
        print(f"  HTML chars: {len(html)} | Text chars: {len(text or '')}")
        print(f"{'=' * 64}\n")
        log.info("email_dev_printed", tag=tag,
                 recipients=recipients, subject=subject)
        return f"dev-{tag}"

    try:
        import resend
        resend.api_key = os.environ.get("RESEND_API_KEY")
        sender = os.environ.get("RESEND_FROM_EMAIL", _DEFAULT_FROM)
        params: dict[str, Any] = {
            "from":    sender,
            "to":      recipients,
            "subject": subject,
            "html":    html,
        }
        if text:
            params["text"] = text
        result = resend.Emails.send(params)
        msg_id = (result or {}).get("id")
        log.info("email_sent", tag=tag, recipients=recipients,
                 subject=subject, message_id=msg_id)
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
