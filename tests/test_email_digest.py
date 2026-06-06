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
    _git_authors_for,
    _hours_ago_utc,
    _section_deadlines,
    _section_implied_asset_allocation,
    _section_latest_cio_recommendation,
    _section_platform_health,
    _section_platform_usage,
    _section_rebalance_triggers,
    _section_team_activity,
    _section_warm_history,
    _truncate_to_word_cap,
    _week_start_utc,
    build_digest_email,
    send_daily_digest,
)


# ── Per-section pure assemblers (no DB / network) ─────────────────────────


@pytest.mark.asyncio
async def test_platform_health_renders_without_warm_state():
    """The section degrades cleanly when get_warm_state() has not yet
    been hit (cold process state). It must still produce non-empty
    HTML + text so a digest sent on cold deploy isn't blank.

    June 2 2026 — the section became async (invariant fallback +
    alembic_version both read from the DB), so the test awaits."""
    s = await _section_platform_health()
    assert s.title == "Platform health"
    assert "<table" in s.html
    assert "PLATFORM HEALTH" in s.text
    assert "Last warm:" in s.text
    assert "DB head:" in s.text
    # The DB-head value renders one of the three permitted states:
    # a real version_num from alembic_version, or 'unavailable' on
    # any failure path. The old 'unknown' literal is retired.
    assert "DB head:" in s.text
    # In test env without a live DB the value must be 'unavailable',
    # never 'unknown'.
    assert "unknown" not in s.text


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
    assert "Platform usage" in html
    assert "Team activity" in html
    assert "Warm history" in html
    assert "Open work" in html
    assert "Deadlines" in html
    # Plain-text fallback covers every section too.
    assert "PLATFORM HEALTH" in text
    assert "PLATFORM RELEASES" in text
    assert "ANALYTICS SNAPSHOT" in text
    assert "PLATFORM USAGE" in text
    assert "TEAM ACTIVITY" in text
    assert "WARM HISTORY" in text
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


@pytest.mark.asyncio
async def test_send_daily_digest_uses_digest_sender(
    monkeypatch, capsys,
):
    """The daily digest must send from `digest@analyticsdesk.app`,
    NOT the platform's generic RESEND_FROM_EMAIL. The dev-env path
    prints the From address; capture it to confirm the per-purpose
    override took effect even when the env var disagrees."""
    monkeypatch.setenv("DIGEST_RECIPIENTS", "michael@example.com")
    # Set RESEND_FROM_EMAIL to something OTHER than the digest mailbox
    # so the test catches the per-call override (not a coincidental
    # env-var match).
    monkeypatch.setenv("RESEND_FROM_EMAIL", "wrong@example.com")
    result = await send_daily_digest()
    assert result["sent"] is True
    out = capsys.readouterr().out
    assert "From:    digest@analyticsdesk.app" in out
    assert "wrong@example.com" not in out


# ── Time-window helpers (US/Eastern WTD) ──────────────────────────────────


def test_week_start_anchors_to_eastern_monday():
    """The WTD window must start at Mon 00:00 ET, NOT UTC. A digest
    fired Wed 14:00 UTC (= Wed 10:00 ET in summer) should report
    WTD as starting Mon 04:00 UTC (= Mon 00:00 EDT)."""
    from datetime import datetime, timezone
    fixed = datetime(2026, 6, 3, 14, 0, tzinfo=timezone.utc)
    start = _week_start_utc(fixed)
    # June 1 2026 is a Monday — EDT (UTC-4) → Mon 00:00 EDT = Mon 04:00 UTC.
    assert start.isoformat() == "2026-06-01T04:00:00+00:00"


def test_week_start_handles_monday_morning_correctly():
    """Cron fires 11:00 UTC on a Monday (= 07:00 ET). WTD must be
    today's Mon 00:00 ET, not last week's."""
    from datetime import datetime, timezone
    fixed = datetime(2026, 6, 1, 11, 0, tzinfo=timezone.utc)
    start = _week_start_utc(fixed)
    assert start.isoformat() == "2026-06-01T04:00:00+00:00"


def test_hours_ago_simple():
    from datetime import datetime, timezone
    fixed = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)
    assert _hours_ago_utc(24, fixed).isoformat() == (
        "2026-06-01T12:00:00+00:00")


def test_git_authors_includes_mapped_personal_email():
    """Michael commits as mikeruurds@gmail; the digest must count those
    commits against ruurdsm@queens.edu via GIT_AUTHOR_EMAIL_MAP."""
    authors = _git_authors_for("ruurdsm@queens.edu")
    assert "ruurdsm@queens.edu" in authors
    assert "mikeruurds@gmail.com" in authors


def test_git_authors_unmapped_returns_self_only():
    authors = _git_authors_for("thaob@queens.edu")
    assert authors == {"thaob@queens.edu"}


# ── Section 4 — Platform usage ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_platform_usage_renders_without_db():
    """No live DB in the test env — the section must fail-open with
    the table shell present and zeros in every category row, so a
    cold-deploy digest still sends with a recognisable section."""
    s = await _section_platform_usage()
    assert s.title == "Platform usage"
    assert "Council queries" in s.html
    assert "Defense Prep" in s.html
    assert "Peer Review" in s.html
    assert "Other" in s.html
    assert "Total" in s.html
    # Text fallback carries the column headers and the three primary
    # categories so a plain-text reader sees the structure.
    assert "PLATFORM USAGE" in s.text
    assert "Council queries" in s.text
    assert "24h" in s.text
    assert "WTD" in s.text


# ── Section 5 — Team activity ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_team_activity_renders_three_team_members():
    """Bob, Molly, and Michael always appear in the table — even when
    every count is zero. The digest is an operational signal that
    confirms each team member's activity (or lack thereof)."""
    s = await _section_team_activity()
    assert s.title == "Team activity"
    for name in ("Michael", "Bob", "Molly"):
        assert name in s.html, f"missing {name} in HTML"
        assert name in s.text, f"missing {name} in text"
    # Column headers — the four signals the user spec'd.
    for label in ("Commits", "Doc edits", "UAT", "Logins"):
        assert label in s.html


@pytest.mark.asyncio
async def test_team_activity_michael_appears_first():
    """Michael is sysadmin and the lead engineer — the canonical first
    row of every team-attribution table in the platform. The digest
    must follow the same convention so a reader scanning columns
    finds the same identity in the same place every day."""
    s = await _section_team_activity()
    michael_pos = s.html.find("Michael")
    bob_pos = s.html.find("Bob")
    molly_pos = s.html.find("Molly")
    assert 0 <= michael_pos < bob_pos
    assert michael_pos < molly_pos


# ── Section 6 — Warm history ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_warm_history_renders_empty_state_without_rows():
    """The section must produce a non-empty 'no warm runs' placeholder
    when the analytics_metrics_cache has no invariant_summary rows
    in the last 7 days — same shape as a fresh deploy."""
    s = await _section_warm_history()
    assert s.title == "Warm history"
    # In test env without a populated cache, the placeholder copy
    # appears in BOTH the HTML and the text fallback.
    assert "Warm history (last 7 days)" in s.html
    assert "WARM HISTORY (last 7 days)" in s.text
    assert ("No warm runs" in s.html
            or "No warm runs" in s.text)


# ── June 5 2026 — three new sections (digest #17 + #18) ──────────────────


@pytest.mark.asyncio
async def test_implied_asset_allocation_renders_placeholder_on_cold_cache():
    """In the test env strategy_results_cache + forward_projection are
    both cold. The section must still render — the title appears, the
    placeholder body explains why it's empty, and neither half of the
    DigestSection is blank."""
    s = await _section_implied_asset_allocation()
    assert s.title == "Implied asset allocation"
    assert "Implied asset allocation" in s.html
    # Placeholder copy on the cold path.
    assert "No live blend" in s.html or "No live blend" in s.text
    # The text fallback always renders — the section never returns
    # html-only.
    assert s.text.strip()


@pytest.mark.asyncio
async def test_latest_cio_recommendation_renders_placeholder_on_cold_cache():
    """Without a populated cio_recommendations table the section still
    fires — header present, placeholder explaining the empty cache,
    text fallback non-empty."""
    s = await _section_latest_cio_recommendation()
    assert s.title == "CIO recommendation"
    assert "CIO recommendation" in s.html
    assert ("No CIO recommendation cached" in s.html
            or "No CIO recommendation cached" in s.text
            or "carries no narrative text" in s.html)
    assert s.text.strip()


@pytest.mark.asyncio
async def test_rebalance_triggers_renders_placeholder_on_cold_cache():
    """No regime_signals_cache rows + no regime_blends metric → the
    section falls open to a placeholder. Header present, text
    non-empty."""
    s = await _section_rebalance_triggers()
    assert s.title == "What would trigger a rebalance"
    assert "What would trigger a rebalance" in s.html
    assert ("No regime signals or blend targets" in s.html
            or "No regime signals or blend targets" in s.text)


def test_truncate_to_word_cap_below_cap_returns_unchanged():
    text = "one two three"
    assert _truncate_to_word_cap(text, 5) == text


def test_truncate_to_word_cap_above_cap_truncates_with_ellipsis():
    text = "one two three four five six"
    out = _truncate_to_word_cap(text, 3)
    # First three words, ellipsis at the end.
    assert out.startswith("one two three")
    assert out.endswith("…")
    # Original five-word remainder is gone.
    assert "four" not in out


def test_truncate_to_word_cap_exact_cap_returns_unchanged():
    text = "one two three"
    # When count == cap, no truncation — same string back.
    assert _truncate_to_word_cap(text, 3) == text


def test_truncate_to_word_cap_empty_input():
    assert _truncate_to_word_cap("", 3) == ""


@pytest.mark.asyncio
async def test_build_digest_includes_three_new_section_titles():
    """The top-level build still works after wiring three new sections.
    All three titles appear in the assembled HTML; the morning skim
    sees them at the TOP, not buried below the ops content. A blank
    cache renders placeholders, not skipped sections.

    Ordering contract (June 6 2026 reorder per operator request):
    CIO recommendation -> Implied asset allocation -> Rebalance
    triggers all precede Platform health / Releases / Warm history /
    Open work so the morning skim sees the allocation decision first.
    """
    subject, html, text = await build_digest_email()
    assert "Implied asset allocation" in html
    assert "CIO recommendation" in html
    assert "What would trigger a rebalance" in html
    # Text fallback carries the same three.
    assert "IMPLIED ASSET ALLOCATION" in text
    assert "CIO RECOMMENDATION" in text
    assert "WHAT WOULD TRIGGER A REBALANCE" in text
    # Primary ordering contract: the three decision sections lead the
    # digest, with the analytics-triplet appearing BEFORE platform
    # health, warm history, and open work.
    html_lc = html.lower()
    cio_idx = html_lc.index("cio recommendation")
    blend_idx = html_lc.index("implied asset allocation")
    trigger_idx = html_lc.index("what would trigger a rebalance")
    health_idx = html_lc.index("platform health")
    warm_idx = html_lc.index("warm history")
    open_idx = html_lc.index("open work")
    # Decision triplet appears in the named order at the top.
    assert cio_idx < blend_idx < trigger_idx
    # Decision triplet precedes the ops sections.
    assert trigger_idx < health_idx
    assert trigger_idx < warm_idx
    assert trigger_idx < open_idx
