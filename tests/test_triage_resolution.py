"""
tests/test_triage_resolution.py — Triage resolution workflow (Commits 1-5).

Coverage:
  - Markdown report → triage_report_items parsing (Commit 2)
    _split_report_into_section_blocks, _parse_item_block
  - Resolved-item context block for the agent prompt (Commit 4)
    _recent_resolved_items fail-open, _format_resolved_items_block,
    _triage_user_message threading
  - resolve_triage_items helper (Commit 3, tools/triage_resolver.py)
  - GET /api/v1/testing/triage/items endpoint (Commit 2)
  - PATCH /api/v1/testing/triage/items/{id}/resolve (Commit 2)
  - PATCH /api/v1/testing/triage/items/{id}/unresolve (Commit 2)
  - retest_requested kind in get_notifications (Commit 3)

The endpoint tests rely on require_permission resolving via the
config-fallback path (no platform_users table needed); ruurdsm@ →
sysadmin, others → not. The engine's database helpers are
monkeypatched where a Postgres-backed exercise would otherwise be
required, mirroring the pattern test_triage.py established.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402
from auth import generate_session_token  # noqa: E402
from tools import triage_engine, triage_resolver, test_runner  # noqa: E402

client = TestClient(app)

SYSADMIN = {"X-API-Key": generate_session_token("ruurdsm@queens.edu")}
TEAM = {"X-API-Key": generate_session_token("thaob@queens.edu")}
VIEWER = {"X-API-Key": generate_session_token("panttserk@queens.edu")}

ITEMS_URL = "/api/v1/testing/triage/items"


# ── Report parsing ───────────────────────────────────────────────────────────

SAMPLE_REPORT = """\
## IMMEDIATE ACTIONS

- Council 502 timeout [failure #42]
  Stream the council via SSE so Render does not gateway-timeout.
- Settings gear icon does nothing [failure #43]

## QUICK WINS

- Fix the "Organisation" spelling [feedback #7]

## PATTERNS AND THEMES

- Text overflow on narrow viewports surfaced in three reports

## POST-DEADLINE BACKLOG

- Konva canvas touch UX rebuild

## SUMMARY

3 immediate, 1 quick win, 1 pattern, 1 backlog item.
"""


class TestSplitReportIntoSectionBlocks:
    def test_splits_each_section_into_its_own_bucket(self):
        blocks = triage_engine._split_report_into_section_blocks(SAMPLE_REPORT)
        assert len(blocks["immediate"]) == 2
        assert len(blocks["quick_win"]) == 1
        assert len(blocks["pattern"]) == 1
        assert len(blocks["backlog"]) == 1

    def test_summary_section_is_not_a_bucket(self):
        # The SUMMARY section carries aggregate counts, not individual
        # items — it must never appear as an item_type bucket.
        blocks = triage_engine._split_report_into_section_blocks(SAMPLE_REPORT)
        assert "summary" not in blocks

    def test_continuation_lines_attach_to_their_bullet(self):
        # The "Stream the council via SSE…" continuation is indented
        # below its bullet — must be collected into the same item.
        blocks = triage_engine._split_report_into_section_blocks(SAMPLE_REPORT)
        first_immediate = blocks["immediate"][0]
        assert "Council 502 timeout" in first_immediate
        assert "Stream the council via SSE" in first_immediate

    def test_empty_report_returns_empty_buckets(self):
        blocks = triage_engine._split_report_into_section_blocks("")
        assert blocks == {"immediate": [], "quick_win": [],
                          "pattern": [], "backlog": []}

    def test_missing_section_is_tolerated(self):
        report = "## IMMEDIATE ACTIONS\n\n- Only one item here"
        blocks = triage_engine._split_report_into_section_blocks(report)
        assert len(blocks["immediate"]) == 1
        assert blocks["quick_win"] == []
        assert blocks["pattern"] == []
        assert blocks["backlog"] == []

    def test_numbered_bullets_are_recognised(self):
        report = "## IMMEDIATE ACTIONS\n\n1. First item\n2) Second item"
        blocks = triage_engine._split_report_into_section_blocks(report)
        assert len(blocks["immediate"]) == 2


class TestParseItemBlock:
    def test_extracts_failure_source_reference(self):
        block = "Council 502 timeout [failure #42]\nStream via SSE."
        parsed = triage_engine._parse_item_block(block, {})
        assert parsed["source_item_type"] == "failure"
        assert parsed["source_item_id"] == 42
        assert parsed["item_title"] == "Council 502 timeout [failure #42]"
        assert "Stream via SSE" in (parsed["item_body"] or "")

    def test_extracts_feedback_source_reference(self):
        block = "Spelling: Organisation → Organization [feedback #7]"
        parsed = triage_engine._parse_item_block(block, {})
        assert parsed["source_item_type"] == "feedback"
        assert parsed["source_item_id"] == 7

    def test_no_source_reference_returns_nones(self):
        block = "Text overflow on narrow viewports"
        parsed = triage_engine._parse_item_block(block, {})
        assert parsed["source_item_type"] is None
        assert parsed["source_item_id"] is None
        assert parsed["item_title"] == "Text overflow on narrow viewports"

    def test_attaches_github_issue_when_source_matches(self):
        issues = {("failure", 42): {"number": 187, "url": "https://gh/i/187"}}
        block = "Council 502 timeout [failure #42]"
        parsed = triage_engine._parse_item_block(block, issues)
        assert parsed["github_issue_number"] == 187
        assert parsed["github_issue_url"] == "https://gh/i/187"

    def test_no_issue_when_source_does_not_match(self):
        issues = {("failure", 99): {"number": 999, "url": "x"}}
        block = "Council 502 timeout [failure #42]"
        parsed = triage_engine._parse_item_block(block, issues)
        assert parsed["github_issue_number"] is None
        assert parsed["github_issue_url"] is None

    def test_title_is_capped_at_500_chars(self):
        long_title = "x" * 800
        parsed = triage_engine._parse_item_block(long_title, {})
        assert len(parsed["item_title"]) == 500


# ── Resolved-item context block (Commit 4) ───────────────────────────────────

class TestRecentResolvedItems:
    def test_returns_empty_list_without_a_database(self):
        # No DB → fail-open empty list. The agent simply runs without a
        # resolved-item context block — the same behaviour as before
        # Commit 4 shipped.
        items = asyncio.run(triage_engine._recent_resolved_items())
        assert items == []


class TestFormatResolvedItemsBlock:
    def test_empty_list_returns_empty_string(self):
        # A brand-new deployment (no resolved items yet) reads cleanly:
        # the user message simply omits the section.
        assert triage_engine._format_resolved_items_block([]) == ""

    def test_renders_each_item_with_resolution_metadata(self):
        items = [{
            "id": 1, "item_type": "immediate",
            "item_title": "Council 502 timeout",
            "resolution_note": "Streaming via SSE",
            "fix_commit": "0123456789abcdef",
            "requires_retest": True,
            "retest_completed_at": None,
        }]
        block = triage_engine._format_resolved_items_block(items)
        assert "RECENTLY RESOLVED ITEMS" in block
        assert "Council 502 timeout" in block
        assert "Streaming via SSE" in block
        assert "01234567" in block  # 8-char short SHA
        assert "Retest: pending" in block

    def test_retest_complete_state_renders(self):
        items = [{
            "id": 2, "item_type": "quick_win", "item_title": "Spelling fix",
            "resolution_note": "Done", "fix_commit": "ab",
            "requires_retest": True,
            "retest_completed_at": "2026-05-20T10:00:00",
        }]
        block = triage_engine._format_resolved_items_block(items)
        assert "Retest: complete" in block

    def test_not_required_state_renders(self):
        items = [{
            "id": 3, "item_type": "pattern", "item_title": "Refactor only",
            "resolution_note": "No retest needed", "fix_commit": None,
            "requires_retest": False,
            "retest_completed_at": None,
        }]
        block = triage_engine._format_resolved_items_block(items)
        assert "Retest: not_required" in block


class TestTriageUserMessageThreadsResolvedBlock:
    def test_resolved_block_appears_in_user_message(self):
        msg = triage_engine._triage_user_message(
            failures=[], feedback=[],
            resolved_block="RECENTLY RESOLVED ITEMS:\n  - foo",
        )
        assert "RECENTLY RESOLVED ITEMS" in msg
        assert "  - foo" in msg

    def test_empty_resolved_block_is_omitted(self):
        msg = triage_engine._triage_user_message(
            failures=[], feedback=[], resolved_block="")
        assert "RECENTLY RESOLVED" not in msg


# ── triage_resolver.resolve_triage_items (Commit 3) ──────────────────────────

class TestResolveTriageItemsHelper:
    def _patch_engine(self, monkeypatch, results):
        """results: {item_id: dict|None} returned by resolve_triage_item."""
        called: list[dict] = []

        async def _fake_resolve(item_id, **kw):
            called.append({"item_id": item_id, **kw})
            return results.get(item_id)

        async def _fake_reporter(source_type, source_id):
            # Two reporters for the two source rows below.
            if source_type == "failure" and source_id == 100:
                return "thaob@queens.edu"
            if source_type == "feedback" and source_id == 200:
                return "murdockm@queens.edu"
            return None

        monkeypatch.setattr(triage_engine, "resolve_triage_item", _fake_resolve)
        monkeypatch.setattr(triage_resolver, "_reporter_for_source",
                            _fake_reporter)
        return called

    def test_resolves_every_id_and_returns_summary(self, monkeypatch):
        results = {
            42: {"id": 42, "item_title": "Council 502",
                 "source_item_type": "failure", "source_item_id": 100,
                 "requires_retest": True},
            43: {"id": 43, "item_title": "Settings gear",
                 "source_item_type": "failure", "source_item_id": 100,
                 "requires_retest": True},
        }
        self._patch_engine(monkeypatch, results)
        out = asyncio.run(triage_resolver.resolve_triage_items(
            [42, 43], resolution_note="Fixed", fix_commit="abc1234",
            requires_retest=True,
        ))
        assert out["resolved"] == [42, 43]
        assert out["failed"] == []
        # Both source rows resolve to the same reporter — deduplicated.
        assert out["notified_reporters"] == ["thaob@queens.edu"]
        assert out["item_titles"] == ["Council 502", "Settings gear"]

    def test_missing_item_lands_in_failed_list(self, monkeypatch):
        self._patch_engine(monkeypatch, {42: None})
        out = asyncio.run(triage_resolver.resolve_triage_items(
            [42], resolution_note="…", fix_commit="abc1234"))
        assert out["resolved"] == []
        assert out["failed"] == [42]

    def test_per_item_fail_open_continues_with_remaining(self, monkeypatch):
        self._patch_engine(monkeypatch, {
            42: None,  # missing → failed
            43: {"id": 43, "item_title": "OK", "source_item_type": None,
                 "source_item_id": None, "requires_retest": False},
        })
        out = asyncio.run(triage_resolver.resolve_triage_items(
            [42, 43], resolution_note="…", fix_commit="abc1234"))
        assert out["resolved"] == [43]
        assert out["failed"] == [42]

    def test_resolution_note_and_commit_propagate(self, monkeypatch):
        called = self._patch_engine(monkeypatch, {
            42: {"id": 42, "item_title": "x", "source_item_type": None,
                 "source_item_id": None, "requires_retest": False},
        })
        asyncio.run(triage_resolver.resolve_triage_items(
            [42], resolution_note="The fix", fix_commit="deadbee",
            requires_retest=False,
        ))
        assert called[0]["resolved_by"] == "claude_code"
        assert called[0]["resolution_note"] == "The fix"
        assert called[0]["fix_commit"] == "deadbee"
        assert called[0]["requires_retest"] is False

    def test_requires_retest_false_skips_reporter_lookup(self, monkeypatch):
        self._patch_engine(monkeypatch, {
            42: {"id": 42, "item_title": "x", "source_item_type": "failure",
                 "source_item_id": 100, "requires_retest": False},
        })
        out = asyncio.run(triage_resolver.resolve_triage_items(
            [42], resolution_note="…", fix_commit="abc1234",
            requires_retest=False,
        ))
        # requires_retest=False on the returned row → no notification.
        assert out["notified_reporters"] == []

    def test_reporter_for_source_returns_none_when_table_unknown(self):
        # Pattern items have no source row → returns None unconditionally.
        out = asyncio.run(triage_resolver._reporter_for_source(None, 42))
        assert out is None
        out = asyncio.run(triage_resolver._reporter_for_source("pattern", 42))
        assert out is None


# ── Endpoint gating + behaviour (Commit 2) ───────────────────────────────────

class TestTriageItemsEndpointGating:
    def test_get_items_rejects_a_viewer(self):
        assert client.get(ITEMS_URL, headers=VIEWER).status_code == 403

    def test_get_items_rejects_a_team_member(self):
        # Team membership is not sysadmin — manage_users is the gate.
        assert client.get(ITEMS_URL, headers=TEAM).status_code == 403

    def test_get_items_admits_the_sysadmin(self):
        resp = client.get(ITEMS_URL, headers=SYSADMIN)
        assert resp.status_code == 200
        assert "items" in resp.json()

    def test_get_items_unauthenticated_is_401(self):
        assert client.get(ITEMS_URL).status_code == 401

    def test_get_items_with_report_id_filter_admits_sysadmin(self):
        resp = client.get(
            f"{ITEMS_URL}?report_id=1", headers=SYSADMIN)
        assert resp.status_code == 200

    def test_resolve_endpoint_rejects_a_team_member(self):
        resp = client.patch(
            f"{ITEMS_URL}/1/resolve",
            headers=TEAM, json={"resolution_note": "…"})
        assert resp.status_code == 403

    def test_unresolve_endpoint_rejects_a_team_member(self):
        resp = client.patch(f"{ITEMS_URL}/1/unresolve", headers=TEAM)
        assert resp.status_code == 403


class TestResolveEndpointValidation:
    """Body validation behaviour of the resolve endpoint — does NOT
    require a database since the validation runs before the engine
    call (the 422 is raised on the empty resolution_note)."""

    def test_missing_resolution_note_is_422(self):
        resp = client.patch(
            f"{ITEMS_URL}/1/resolve",
            headers=SYSADMIN, json={})
        assert resp.status_code == 422
        assert "resolution_note" in resp.json()["detail"]

    def test_blank_resolution_note_is_422(self):
        resp = client.patch(
            f"{ITEMS_URL}/1/resolve",
            headers=SYSADMIN, json={"resolution_note": "   "})
        assert resp.status_code == 422


class TestResolveEndpointBehaviour:
    """End-to-end PATCH /resolve and /unresolve with the engine helpers
    monkeypatched. No database needed."""

    def test_resolve_calls_engine_with_session_email(self, monkeypatch):
        captured: dict = {}

        async def _fake(item_id, **kw):
            captured["item_id"] = item_id
            captured.update(kw)
            return {
                "id": item_id, "item_title": "x",
                "source_item_type": None, "source_item_id": None,
                "requires_retest": True,
            }

        monkeypatch.setattr(triage_engine, "resolve_triage_item", _fake)
        resp = client.patch(
            f"{ITEMS_URL}/42/resolve",
            headers=SYSADMIN,
            json={"resolution_note": "All fixed", "fix_commit": "abc1234",
                  "requires_retest": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "resolved"
        assert captured["item_id"] == 42
        assert captured["resolved_by"] == "ruurdsm@queens.edu"
        assert captured["resolution_note"] == "All fixed"
        assert captured["fix_commit"] == "abc1234"
        assert captured["requires_retest"] is True

    def test_resolve_missing_item_returns_404(self, monkeypatch):
        async def _fake(item_id, **kw):
            return None
        monkeypatch.setattr(triage_engine, "resolve_triage_item", _fake)
        resp = client.patch(
            f"{ITEMS_URL}/9999/resolve",
            headers=SYSADMIN, json={"resolution_note": "x"})
        assert resp.status_code == 404

    def test_resolve_defaults_requires_retest_to_false(self, monkeypatch):
        captured: dict = {}

        async def _fake(item_id, **kw):
            captured.update(kw)
            return {"id": item_id, "item_title": "x",
                    "source_item_type": None, "source_item_id": None,
                    "requires_retest": False}
        monkeypatch.setattr(triage_engine, "resolve_triage_item", _fake)
        client.patch(
            f"{ITEMS_URL}/1/resolve",
            headers=SYSADMIN, json={"resolution_note": "x"})
        # Default — caller did not pass requires_retest.
        assert captured["requires_retest"] is False

    def test_unresolve_calls_engine_and_returns_status(self, monkeypatch):
        async def _fake(item_id):
            return True
        monkeypatch.setattr(triage_engine, "unresolve_triage_item", _fake)
        resp = client.patch(f"{ITEMS_URL}/42/unresolve", headers=SYSADMIN)
        assert resp.status_code == 200
        assert resp.json() == {"status": "unresolved"}

    def test_unresolve_missing_item_returns_404(self, monkeypatch):
        async def _fake(item_id):
            return False
        monkeypatch.setattr(triage_engine, "unresolve_triage_item", _fake)
        resp = client.patch(f"{ITEMS_URL}/9999/unresolve", headers=SYSADMIN)
        assert resp.status_code == 404


# ── get_notifications fail-open + retest_requested shape (Commit 3) ──────────

class TestGetNotificationsFailsOpenAndCarriesRetest:
    def test_returns_all_three_kinds_keyed_safely_without_a_db(self):
        # No database → fail-open empty shape, but every key the
        # frontend reads MUST be present (resolved_failures,
        # responded_feedback, retest_requested).
        out = asyncio.run(
            test_runner.get_notifications("thaob@queens.edu"))
        assert out == {
            "resolved_failures": [],
            "responded_feedback": [],
            "retest_requested": [],
        }
