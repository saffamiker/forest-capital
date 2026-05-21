"""
tests/test_test_runner.py

Tests for the guided UAT test runner — the /api/v1/testing/* endpoints,
the quality gate, and the persistence layer in tools/test_runner.py.

Two tiers, the same pattern as test_activity.py / test_export_package.py:
  - Endpoint-contract tests (auth, team/admin gating, the fail-open
    quality gate, screenshot path storage) run everywhere including CI.
  - DB round-trip tests exercise the test_results / test_feedback
    persistence directly; they skip cleanly when no live PostgreSQL with
    the migration-014 tables is reachable.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)

from main import app  # noqa: E402
from auth import generate_session_token  # noqa: E402

client = TestClient(app)
ADMIN = "ruurdsm@queens.edu"          # the test-runner administrator
TEAM = "thaob@queens.edu"             # a team member, not admin
NON_TEAM = "panttserk@queens.edu"     # authenticated, not on the team
ADMIN_HEADERS = {"X-API-Key": generate_session_token(ADMIN)}
TEAM_HEADERS = {"X-API-Key": generate_session_token(TEAM)}
NON_TEAM_HEADERS = {"X-API-Key": generate_session_token(NON_TEAM)}


def _run(coro):
    return asyncio.run(coro)


# ── Endpoint contract — runs in CI ────────────────────────────────────────────

class TestTestRunnerContract:
    def test_quality_check_fails_open_in_test_env(self):
        # No API key in the test env → the evaluator cannot run, so the
        # gate must pass the submission rather than block it.
        resp = client.post("/api/v1/testing/quality-check",
                            json={"type": "feedback", "description": "x",
                                  "step_context": "y"},
                            headers=TEAM_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["passed"] is True

    def test_quality_check_requires_auth(self):
        assert client.post("/api/v1/testing/quality-check",
                            json={"type": "feedback"}).status_code == 401

    def test_failures_view_is_admin_only(self):
        # The admin reaches it; a non-admin team member is forbidden.
        assert client.get("/api/v1/testing/failures",
                          headers=ADMIN_HEADERS).status_code == 200
        assert client.get("/api/v1/testing/failures",
                          headers=TEAM_HEADERS).status_code == 403

    def test_feedback_view_is_admin_only(self):
        assert client.get("/api/v1/testing/feedback",
                          headers=ADMIN_HEADERS).status_code == 200
        assert client.get("/api/v1/testing/feedback",
                          headers=TEAM_HEADERS).status_code == 403

    def test_results_endpoint_rejects_non_team(self):
        # An authenticated non-team user (Dr. Panttser) is gated out.
        resp = client.post("/api/v1/testing/results",
                            data={"script_id": "all_testers_v1",
                                  "step_id": "x", "result": "pass"},
                            headers=NON_TEAM_HEADERS)
        assert resp.status_code == 403

    def test_results_endpoint_requires_auth(self):
        resp = client.post("/api/v1/testing/results",
                            data={"script_id": "s", "step_id": "x",
                                  "result": "pass"})
        assert resp.status_code == 401

    def test_unseen_and_summary_require_auth(self):
        assert client.get("/api/v1/testing/unseen").status_code == 401
        assert client.get("/api/v1/testing/summary").status_code == 401

    def test_result_value_is_validated(self):
        resp = client.post("/api/v1/testing/results",
                            data={"script_id": "s", "step_id": "x",
                                  "result": "bogus"},
                            headers=TEAM_HEADERS)
        assert resp.status_code == 422


class TestScreenshotStorage:
    def test_screenshots_are_stored_as_paths_not_blobs(self):
        from tools.test_runner import save_screenshots
        paths = save_screenshots([("shot.png", b"\x89PNG_fake_image_bytes")])
        assert len(paths) == 1
        # The stored value is a relative path string — never the bytes.
        assert isinstance(paths[0], str)
        assert paths[0].startswith("test_screenshots/")
        assert paths[0].endswith(".png")

    def test_invalid_screenshots_degrade_gracefully(self):
        from tools.test_runner import save_screenshots
        # A disallowed extension / empty content yields no paths and no error.
        assert save_screenshots([("notes.txt", b"data")]) == []
        assert save_screenshots([("empty.png", b"")]) == []
        assert save_screenshots([]) == []


class TestScreenshotCleanup:
    """The startup lifespan calls cleanup_old_screenshots() to drop files
    older than 30 days; delete_screenshots() is the per-row helper for
    a future explicit-delete endpoint. Both are fail-open."""

    def _make_screenshot(self, dir_path, age_days: float) -> str:
        """Writes one fake PNG into dir_path and stamps its mtime so
        age_days have notionally passed since it was created."""
        import os
        import time
        import uuid as _uuid
        name = f"{_uuid.uuid4().hex}.png"
        target = dir_path / name
        target.write_bytes(b"\x89PNG_fake")
        past = time.time() - age_days * 86400
        os.utime(target, (past, past))
        return name

    def test_cleanup_deletes_screenshots_older_than_30_days(
        self, tmp_path, monkeypatch,
    ):
        from tools import test_runner
        monkeypatch.setattr(test_runner, "SCREENSHOT_DIR", str(tmp_path))
        # Two stale, one fresh.
        old_a = self._make_screenshot(tmp_path, age_days=45)
        old_b = self._make_screenshot(tmp_path, age_days=31)
        fresh = self._make_screenshot(tmp_path, age_days=2)
        deleted, remaining = test_runner.cleanup_old_screenshots()
        assert deleted == 2 and remaining == 1
        # The 45-day and 31-day files are gone; the 2-day file survives.
        names = {p.name for p in tmp_path.iterdir() if p.is_file()}
        assert old_a not in names and old_b not in names
        assert fresh in names

    def test_cleanup_leaves_recent_screenshots_intact(
        self, tmp_path, monkeypatch,
    ):
        from tools import test_runner
        monkeypatch.setattr(test_runner, "SCREENSHOT_DIR", str(tmp_path))
        # Three fresh files — all under 30 days — survive the sweep.
        for age in (0.5, 5, 29):
            self._make_screenshot(tmp_path, age_days=age)
        deleted, remaining = test_runner.cleanup_old_screenshots()
        assert deleted == 0 and remaining == 3

    def test_cleanup_on_missing_directory_returns_zero(
        self, tmp_path, monkeypatch,
    ):
        from tools import test_runner
        # A path that doesn't exist — the sweep must not raise.
        monkeypatch.setattr(
            test_runner, "SCREENSHOT_DIR", str(tmp_path / "does-not-exist"))
        assert test_runner.cleanup_old_screenshots() == (0, 0)

    def test_delete_screenshots_removes_matching_files(
        self, tmp_path, monkeypatch,
    ):
        # Match the production layout exactly: SCREENSHOT_DIR is the
        # test_screenshots subdirectory; the DB stores a path like
        # "test_screenshots/<uuid>.png" relative to SCREENSHOT_DIR's
        # parent (so the /uploads StaticFiles mount resolves it).
        from tools import test_runner
        shot_dir = tmp_path / "test_screenshots"
        shot_dir.mkdir()
        monkeypatch.setattr(test_runner, "SCREENSHOT_DIR", str(shot_dir))
        a = self._make_screenshot(shot_dir, age_days=1)
        b = self._make_screenshot(shot_dir, age_days=1)
        c = self._make_screenshot(shot_dir, age_days=1)
        removed = test_runner.delete_screenshots([
            f"test_screenshots/{a}",
            f"test_screenshots/{b}",
            "test_screenshots/does-not-exist.png",  # absent → silent skip
        ])
        assert removed == 2
        names = {p.name for p in shot_dir.iterdir() if p.is_file()}
        assert a not in names and b not in names
        assert c in names  # not in the delete list — preserved

    def test_delete_screenshots_handles_empty_input(self):
        from tools.test_runner import delete_screenshots
        assert delete_screenshots(None) == 0
        assert delete_screenshots([]) == 0

    def test_delete_screenshots_refuses_path_escape(
        self, tmp_path, monkeypatch,
    ):
        from tools import test_runner
        shot_dir = tmp_path / "test_screenshots"
        shot_dir.mkdir()
        # A sibling file outside SCREENSHOT_DIR — must not be reachable.
        sibling = tmp_path / "outside_file.png"
        sibling.write_bytes(b"\x89PNG_fake")
        monkeypatch.setattr(test_runner, "SCREENSHOT_DIR", str(shot_dir))
        removed = test_runner.delete_screenshots(["../outside_file.png"])
        assert removed == 0
        assert sibling.exists()  # untouched


# ── DB round-trip — skips without a live database ─────────────────────────────

_db_ready_cache: bool | None = None


async def _fresh_session():
    from database import engine, AsyncSessionLocal
    if engine is not None:
        await engine.dispose()
    return AsyncSessionLocal()  # type: ignore[union-attr]


def _db_ready() -> bool:
    """True when a live PostgreSQL with the migration-014 tables exists."""
    global _db_ready_cache
    if _db_ready_cache is not None:
        return _db_ready_cache
    try:
        from tools.cache import _DB_AVAILABLE
        if not _DB_AVAILABLE:
            _db_ready_cache = False
            return False
        from sqlalchemy import text

        async def _probe() -> bool:
            async with await _fresh_session() as s:
                await s.execute(text("SELECT 1 FROM test_results LIMIT 1"))
            return True

        _db_ready_cache = _run(_probe())
    except Exception:
        _db_ready_cache = False
    return _db_ready_cache


async def _cleanup(script_id: str):
    from sqlalchemy import text
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as s:  # type: ignore[union-attr]
        await s.execute(text("DELETE FROM test_results WHERE script_id = :s"),
                        {"s": script_id})
        await s.execute(text("DELETE FROM test_feedback WHERE script_id = :s"),
                        {"s": script_id})
        await s.commit()


class TestTestRunnerPersistence:
    def test_record_result_inserts_then_upserts_overridden(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.test_runner import record_result, get_results

        sid = f"sc_{uuid.uuid4().hex[:8]}"

        async def scenario():
            from database import engine
            await engine.dispose()
            try:
                first = await record_result(
                    user_email=TEAM, session_type="testing", script_id=sid,
                    step_id="step1", result="pass")
                assert first is not None
                # First insert — not yet overridden.
                assert first["overridden"] is False
                # Re-attestation — same (user, script, step) → upsert,
                # overridden flips true.
                second = await record_result(
                    user_email=TEAM, session_type="testing", script_id=sid,
                    step_id="step1", result="fail",
                    failure_description="broke on click", severity="major")
                assert second is not None
                assert second["overridden"] is True
                assert second["result"] == "fail"
                rows = await get_results(TEAM)
                step1 = [r for r in rows if r["script_id"] == sid
                         and r["step_id"] == "step1"]
                assert len(step1) == 1   # upsert — one row, not two
            finally:
                await _cleanup(sid)

        _run(scenario())

    def test_get_results_returns_only_current_user(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.test_runner import record_result, get_results

        sid = f"sc_{uuid.uuid4().hex[:8]}"

        async def scenario():
            from database import engine
            await engine.dispose()
            try:
                await record_result(user_email=TEAM, session_type="testing",
                                     script_id=sid, step_id="a", result="pass")
                await record_result(user_email=ADMIN, session_type="testing",
                                    script_id=sid, step_id="a", result="skip")
                team_rows = await get_results(TEAM)
                assert all(r["script_id"] != sid or True for r in team_rows)
                team_sid = [r for r in team_rows if r["script_id"] == sid]
                assert len(team_sid) == 1
                assert team_sid[0]["result"] == "pass"
            finally:
                await _cleanup(sid)

        _run(scenario())

    def test_summary_counts_pass_fail_skip(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.test_runner import record_result, get_summary

        sid = f"sc_{uuid.uuid4().hex[:8]}"

        async def scenario():
            from database import engine
            await engine.dispose()
            try:
                await record_result(user_email=TEAM, session_type="testing",
                                     script_id=sid, step_id="a", result="pass")
                await record_result(user_email=TEAM, session_type="testing",
                                     script_id=sid, step_id="b", result="skip")
                summary = await get_summary(TEAM)
                assert summary.get(sid, {}).get("pass") == 1
                assert summary.get(sid, {}).get("skip") == 1
                assert summary.get(sid, {}).get("fail") == 0
            finally:
                await _cleanup(sid)

        _run(scenario())

    def test_resolve_failure_marks_row_pending_for_retest(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.test_runner import (
            record_result, get_all_failures, resolve_failure, get_unseen)

        sid = f"sc_{uuid.uuid4().hex[:8]}"

        async def scenario():
            from database import engine
            await engine.dispose()
            try:
                await record_result(
                    user_email=TEAM, session_type="testing", script_id=sid,
                    step_id="x", result="fail",
                    failure_description="it crashed")
                # The failure shows in the admin view.
                failures = await get_all_failures()
                mine = [f for f in failures if f["script_id"] == sid]
                assert len(mine) == 1
                # Before resolving, the step counts as attested.
                before = await get_unseen(TEAM)
                assert "x" in before["scripts"].get(sid, {}).get(
                    "attested_step_ids", [])
                # Resolve it. Migration 025 — resolution_type is required
                # and code_fix_deployed additionally requires fix_reference
                # + remediation_note. The endpoint validates these too;
                # this test exercises the direct DB write.
                resolved = await resolve_failure(
                    mine[0]["id"], ADMIN, "Root cause: race condition.",
                    resolution_type="code_fix_deployed",
                    fix_reference="abc1234",
                    remediation_note="Added a lock around the contended path.",
                )
                assert resolved is not None
                assert resolved["user_email"] == TEAM
                assert resolved["resolution_type"] == "code_fix_deployed"
                # After resolving, the step is pending re-test — no longer
                # counted as attested. (wont_fix would keep it attested;
                # see TestResolutionStepReset below.)
                after = await get_unseen(TEAM)
                assert "x" not in after["scripts"].get(sid, {}).get(
                    "attested_step_ids", [])
            finally:
                await _cleanup(sid)

        _run(scenario())

    def test_wont_fix_resolution_does_not_reset_the_step(self):
        """Migration 025 — Part 4 of the resolution-gate spec:
        wont_fix closes the item but the step stays at its current
        attested state (it does NOT appear as pending re-test in
        get_unseen). no_bug_detected and code_fix_deployed DO reset
        the step; only wont_fix is special."""
        if not _db_ready():
            pytest.skip("no live database")
        from tools.test_runner import (
            record_result, get_all_failures, resolve_failure, get_unseen)

        sid = f"sc_{uuid.uuid4().hex[:8]}"

        async def scenario():
            from database import engine
            await engine.dispose()
            try:
                await record_result(
                    user_email=TEAM, session_type="testing", script_id=sid,
                    step_id="y", result="fail",
                    failure_description="acceptable as designed")
                failures = await get_all_failures()
                mine = [f for f in failures if f["script_id"] == sid]
                assert len(mine) == 1
                resolved = await resolve_failure(
                    mine[0]["id"], ADMIN,
                    "By design — sysadmin-only endpoint.",
                    resolution_type="wont_fix",
                )
                assert resolved is not None
                assert resolved["resolution_type"] == "wont_fix"
                # The step STAYS attested — wont_fix does not reset.
                after = await get_unseen(TEAM)
                assert "y" in after["scripts"].get(sid, {}).get(
                    "attested_step_ids", [])
                # The row is in the resolved set on the admin view —
                # it's just that the tester doesn't see it as
                # pending re-test in their queue.
                after_failures = await get_all_failures()
                row = next(f for f in after_failures
                           if f["script_id"] == sid)
                assert row["resolved_at"] is not None
                assert row["resolution_type"] == "wont_fix"
            finally:
                await _cleanup(sid)

        _run(scenario())

    def test_submit_feedback_stores_ai_categorisation_fields(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.test_runner import submit_feedback, get_all_feedback

        sid = f"sc_{uuid.uuid4().hex[:8]}"

        async def scenario():
            from database import engine
            await engine.dispose()
            try:
                stored = await submit_feedback(
                    user_email=TEAM, script_id=sid, step_id="a",
                    source_route=None, feedback_type="observation",
                    title="A note", description="Something to consider.",
                    priority=None, screenshot_paths=None, browser_info=None,
                    low_quality=False,
                    ai={"category": "Enhancement", "severity": "Minor",
                        "effort_estimate": "Small", "tags": ["analytics"],
                        "summary": "An enhancement.", "ai_confidence": 0.8})
                assert stored is not None
                assert stored["ai_category"] == "Enhancement"
                assert stored["ai_effort_estimate"] == "Small"
                # The categorisation persisted and reads back.
                all_fb = await get_all_feedback({})
                mine = [f for f in all_fb if f["script_id"] == sid]
                assert len(mine) == 1
                assert mine[0]["ai_category"] == "Enhancement"
                assert mine[0]["ai_tags"] == ["analytics"]
            finally:
                await _cleanup(sid)

        _run(scenario())

    def test_free_form_feedback_has_no_script_or_step(self):
        if not _db_ready():
            pytest.skip("no live database")
        from tools.test_runner import submit_feedback, get_all_feedback

        route = f"/route_{uuid.uuid4().hex[:8]}"

        async def scenario():
            from database import engine
            from sqlalchemy import text
            from database import AsyncSessionLocal
            await engine.dispose()
            try:
                stored = await submit_feedback(
                    user_email=TEAM, script_id=None, step_id=None,
                    source_route=route, feedback_type="feature_request",
                    title="Free-form idea", description="A standalone idea.",
                    priority="should_have", screenshot_paths=None,
                    browser_info=None, low_quality=False, ai={})
                assert stored is not None
                all_fb = await get_all_feedback({})
                mine = [f for f in all_fb if f["source_route"] == route]
                assert len(mine) == 1
                assert mine[0]["script_id"] is None
                assert mine[0]["step_id"] is None
            finally:
                async with AsyncSessionLocal() as s:  # type: ignore[union-attr]
                    await s.execute(
                        text("DELETE FROM test_feedback WHERE source_route = :r"),
                        {"r": route})
                    await s.commit()

        _run(scenario())
