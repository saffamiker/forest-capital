"""Email digest (Component 1) — section assemblers + send wrapper.

Each section assembler must:
  - return a DigestSection with non-empty html + text
  - fail-open when its upstream source is missing
  - render the user-spec'd content (warm states, releases, regime,
    blend weights, deadlines)

The top-level build_digest_email + send_daily_digest are tested
end-to-end with the dev-env Resend short-circuit (no real network).
"""
from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")

from tools.email_digest import (  # noqa: E402
    _section_platform_health,
    _section_deadlines,
    build_digest_email,
    send_daily_digest,
)


# ── Per-section pure assemblers (no DB / network) ─────────────────────────


def test_platform_health_renders_without_warm_state():
    """The section degrades cleanly when get_warm_state() has not yet
    been hit (cold process state). It must still produce non-empty
    HTML + text so a digest sent on cold deploy isn't blank."""
    s = _section_platform_health()
    assert s.title == "Platform health"
    assert "<table" in s.html
    assert "PLATFORM HEALTH" in s.text
    assert "Last warm:" in s.text
    assert "DB head:" in s.text


def test_deadlines_renders_known_project_dates():
    """The two practicum deadlines are hardcoded — June 3 cohort
    review and July 1 final submission. The section must surface both
    with their ISO dates and a days-remaining badge."""
    today = date(2026, 6, 1)
    s = _section_deadlines(today=today)
    assert "Cohort peer review" in s.html
    assert "Executive brief" in s.html
    # Days-remaining badges from the fixed today.
    assert "2026-06-03" in s.html
    assert "2026-07-01" in s.html
    # Color cue: 2 days out is red (≤7-day band).
    assert "#ef4444" in s.html or "in 2 days" in s.html
    assert "in 30 days" in s.html


def test_deadlines_past_dates_show_past_badge():
    """A date already in the rear-view mirror should show 'past'
    rather than a misleading days-remaining count."""
    today = date(2026, 8, 1)
    s = _section_deadlines(today=today)
    assert "past" in s.html
    assert "past" in s.text


# ── Top-level build (async, exercises every section) ──────────────────────


@pytest.mark.asyncio
async def test_build_digest_email_returns_subject_html_text():
    subject, html, text = await build_digest_email()
    assert subject.startswith("AnalyticsDesk daily digest — ")
    # HTML has the expected wrapping + each section.
    assert "<html>" in html
    assert "Platform health" in html
    assert "Platform releases" in html
    assert "Analytics snapshot" in html
    assert "Open work" in html
    assert "Deadlines" in html
    # Plain-text fallback covers every section too.
    assert "PLATFORM HEALTH" in text
    assert "PLATFORM RELEASES" in text
    assert "ANALYTICS SNAPSHOT" in text
    assert "OPEN WORK" in text
    assert "DEADLINES" in text
    # Unsubscribe / opt-out copy per the spec.
    assert "DIGEST_RECIPIENTS" in text or "unsubscribe" in text.lower()


# ── send_daily_digest — dev-env short-circuit ─────────────────────────────


@pytest.mark.asyncio
async def test_send_daily_digest_returns_dev_message_id(monkeypatch):
    """In test env send_email() returns a deterministic dev-<tag>
    message id; the wire path is fully exercised without Resend."""
    monkeypatch.setenv(
        "DIGEST_RECIPIENTS",
        "michael@example.com,bob@example.com,molly@example.com")
    result = await send_daily_digest()
    assert result["sent"] is True
    assert result["message_id"].startswith("dev-")
    assert len(result["recipients"]) == 3
    assert result["subject"].startswith("AnalyticsDesk daily digest")


@pytest.mark.asyncio
async def test_send_daily_digest_skips_when_no_recipients(monkeypatch):
    """In production with DIGEST_RECIPIENTS unset, the helper must
    log + return sent=False rather than firing a no-recipient send.
    In test env, a placeholder kicks in so the send still lands —
    this exercises the placeholder path."""
    monkeypatch.setenv("DIGEST_RECIPIENTS", "")
    result = await send_daily_digest()
    # In test env, the placeholder digest-dev@analyticsdesk.app fills
    # in, so the send still goes through — confirms fail-open contract.
    assert result["sent"] is True
    assert result["recipients"] == ["digest-dev@analyticsdesk.app"]
