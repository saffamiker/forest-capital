"""
tools/research_engine.py — orchestrator for the macro market research agent.

FEATURE 2 (May 21 2026), Commit 2/5. Mirrors triage_engine /
audit_engine conventions:
  - is_research_running() concurrency lock (no two runs at once)
  - run_research(triggered_by) end-to-end orchestration
  - trigger_research_async(reason) fire-and-forget loop-or-thread spawn
  - last_research_run_at() / get_latest_digest() read accessors
  - 24-hour cache freshness — a digest under 24h old is "current"; a
    cold-deploy or stale-cache state triggers a fresh run

The engine is the persistence layer for agents/research_agent.py. The
agent itself is pure compute (returns a digest dict); the engine
inserts the 'running' row, calls the agent, finalises with the
generated digest, fail-opens on error, and records the run for the
dashboard widget + context-injection layer (Commit 3).

FAIL-OPEN end to end. Any database error, any agent error, any spawn
failure logs and degrades. The failure mode is "no fresh digest this
hour" — the council and academic_review still run, the dashboard
shows the previous digest or an empty state. The data pipeline
itself is NEVER blocked on the research engine.

NO HASH-SKIP. Unlike the chart snapshots (data-hash gated) and the
audit engine (data-hash gated), the macro digest's freshness is
time-based: today's macro news has nothing to do with the historical
data hash. The freshness gate is `_is_current(window_hours=24)` —
"do not re-run within 24 hours of the last completed digest."
"""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# ── Concurrency lock + freshness ─────────────────────────────────────────────

# The cache freshness window. A digest under 24h old is current; the
# auto-trigger skips re-running within the window.
_FRESHNESS_WINDOW_HOURS = 24

# Stuck-run timeout. A real research run completes in 30-90s; a row
# stuck in 'running' for more than this many minutes is dead — most
# likely a Render restart or worker crash that fired the INSERT but
# never reached the UPDATE-to-complete. Reaping it un-blocks the
# concurrency lock for the next run. Mirrors audit_engine's
# _AUDIT_TIMEOUT_MINUTES pattern; the audit timeout is 15 minutes
# (audits do a lot more work). Research is faster, so 10 minutes is
# the conservative cap.
_RUN_TIMEOUT_MINUTES = 10

# Active background-task refs — same pattern audit_engine uses to keep
# fire-and-forget tasks alive on the event loop.
_research_bg_tasks: set[asyncio.Task[Any]] = set()

# Daily scheduler — the macro digest auto-refreshes once per UTC day at
# this hour. 21:00 UTC is 5pm New York, an hour after US equity market
# close — late enough for the closing print to be in the news, early
# enough that the agents reading the digest the next morning have a
# fresh read. The scheduler is a daemon coroutine that loops:
# sleep-until-next-fire → run_research_if_stale → loop. The 24h
# freshness gate (_FRESHNESS_WINDOW_HOURS) prevents duplicate runs
# when a deploy restart fires the startup trigger within an hour of
# the schedule. Set the env var DAILY_RESEARCH_HOUR_UTC to override
# the hour without a code change.
_DAILY_SCHEDULE_HOUR_UTC = 21


def _next_daily_fire(now: datetime, hour_utc: int) -> datetime:
    """Returns the next UTC datetime at HH:00:00 strictly after `now`.

    If now is BEFORE the scheduled hour today, fire today; otherwise
    fire tomorrow. Pure function so the scheduler test exercises the
    boundary without a clock.
    """
    target_today = now.replace(
        hour=hour_utc, minute=0, second=0, microsecond=0)
    if now < target_today:
        return target_today
    return target_today + timedelta(days=1)


async def daily_research_loop(hour_utc: int | None = None) -> None:
    """A daemon coroutine: sleeps until next 21:00 UTC, fires
    run_research_if_stale, then loops. The 24h freshness gate inside
    run_research_if_stale prevents duplicate runs if the startup hook
    already produced today's digest. Fail-open: a research failure or
    DB error logs and the loop continues to the next day.

    Spawn from the lifespan startup hook with asyncio.create_task; the
    hook must hold a strong reference so the task is not GC'd. Cancel
    cleanly via task.cancel() at shutdown — the CancelledError unwinds
    the sleep and exits the while loop.
    """
    import os
    h_env = os.getenv("DAILY_RESEARCH_HOUR_UTC")
    h = int(h_env) if h_env and h_env.isdigit() else (
        hour_utc if hour_utc is not None else _DAILY_SCHEDULE_HOUR_UTC)

    log.info("research_daily_scheduler_started", hour_utc=h)
    while True:
        now = datetime.now(timezone.utc)
        fire_at = _next_daily_fire(now, h)
        sleep_seconds = (fire_at - now).total_seconds()
        log.info("research_daily_scheduler_sleeping",
                 next_fire_at=fire_at.isoformat(),
                 sleep_seconds=int(sleep_seconds))
        try:
            await asyncio.sleep(sleep_seconds)
        except asyncio.CancelledError:
            log.info("research_daily_scheduler_cancelled")
            raise
        # Fire the scheduled run. run_research_if_stale's 24h gate
        # makes a duplicate fire (deploy restart firing the startup
        # trigger right before the scheduled time) a silent no-op.
        try:
            result = await run_research_if_stale()
            log.info("research_daily_scheduler_fired",
                     status=result.get("status"),
                     reason=result.get("reason"))
        except Exception as exc:  # noqa: BLE001
            log.warning("research_daily_scheduler_fire_failed",
                        error=str(exc))


def start_daily_scheduler() -> asyncio.Task[Any] | None:
    """Spawns the daily scheduler on the running event loop. Returns
    the task so the caller holds a strong reference (a GC'd task gets
    cancelled silently). Returns None when no event loop is running —
    a non-event-loop context simply skips the scheduler rather than
    crashing the boot.

    Defensive test-env guard: even though main.py's lifespan hook
    skips this in ENVIRONMENT=test, the scheduler refuses to start
    here too so a future caller (or a test that monkeypatches around
    the main.py guard) can't accidentally launch the daemon. The
    daemon's sleep-then-fire loop would otherwise pollute the test
    DB with 'running' rows between assertions.
    """
    if _is_test_env():
        log.info("research_daily_scheduler_skipped_test_env")
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.warning("research_daily_scheduler_no_loop",
                    note="not started; no running event loop")
        return None
    task = loop.create_task(daily_research_loop())
    _research_bg_tasks.add(task)
    task.add_done_callback(_research_bg_tasks.discard)
    return task


async def fail_stale_running_digests(
    *, timeout_minutes: int = _RUN_TIMEOUT_MINUTES,
) -> int:
    """
    Marks every macro_research_digests row stuck in 'running' past
    `timeout_minutes` as 'failed' so the concurrency lock the row
    holds is released. Returns the number of rows reaped.

    A research run that crashes mid-flight (Render restart, worker
    OOM, network timeout on the agent call) leaves its row in
    'running' forever. Without this reaper, every subsequent run is
    skipped with reason 'already_running' and the dashboard's Run
    Now button does nothing — the surface the user reported as the
    "Run now is not actuating" bug on May 22 2026.

    `timeout_minutes` is the minimum age (in minutes) a row must
    have to be reaped. Default _RUN_TIMEOUT_MINUTES for runtime
    safety checks (a row in 'running' under the timeout might still
    be live). The startup hook passes timeout_minutes=0 so EVERY
    'running' row is reaped — the previous process is dead by
    definition post-restart, so its rows cannot possibly still be
    executing. This catches the row-15 case: a row stuck in 'running'
    for less than the timeout when the worker crashed.

    Called from:
      - lifespan startup hook (main.py) so a Render redeploy reaps
        any zombie row from the previous boot before any user request
      - is_research_running() (so every concurrency-lock check reaps
        first; a stuck run never holds the lock more than one check
        past the timeout)

    Fail-open: a database error logs a warning and returns 0 — the
    caller proceeds as if no rows were reaped.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return 0
        async with AsyncSessionLocal() as session:
            # The CAST is required: asyncpg cannot infer the type of a
            # bind parameter inside make_interval and raises
            # IndeterminateDatatypeError without it. Mirrors the audit
            # reaper's pattern.
            res = await session.execute(text(
                "UPDATE macro_research_digests SET "
                " status = 'failed', "
                " error = :reason "
                "WHERE status = 'running' "
                "  AND generated_at < now() "
                "    - make_interval(mins => CAST(:mins AS integer)) "
                "RETURNING id, generated_at, triggered_by"
            ), {
                "reason": (
                    f"Auto-reaped: stuck in 'running' state for more "
                    f"than {timeout_minutes} minutes — either the worker "
                    f"crashed mid-run or the freshness gate skipped "
                    f"execution after row creation."
                ),
                "mins": int(timeout_minutes),
            })
            reaped = res.fetchall()
            await session.commit()
        for row in reaped:
            log.warning(
                "research_run_timed_out",
                row_id=int(row[0]),
                generated_at=row[1].isoformat() if row[1] else None,
                triggered_by=row[2],
                timeout_minutes=timeout_minutes,
            )
        return len(reaped)
    except Exception as exc:  # noqa: BLE001
        log.warning("research_fail_stale_failed", error=str(exc))
        return 0


async def is_research_running() -> bool:
    """True when a macro_research_digests row is still in 'running'.
    Reaps any stuck-past-_RUN_TIMEOUT_MINUTES rows first, so a zombie
    row never permanently blocks the lock. Fail-open: a database
    error reports False so a research run is never permanently
    blocked by a stale read."""
    await fail_stale_running_digests()
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return False
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT 1 FROM macro_research_digests "
                "WHERE status = 'running' LIMIT 1"))
            return row.fetchone() is not None
    except Exception as exc:  # noqa: BLE001
        log.warning("research_running_check_failed", error=str(exc))
        return False


async def last_research_run_at() -> datetime | None:
    """The generated_at of the most recent completed run, or None.
    Used by _is_current and surfaced on the dashboard widget so the
    user sees how fresh the current digest is."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT generated_at FROM macro_research_digests "
                "WHERE status = 'complete' "
                "ORDER BY generated_at DESC LIMIT 1"))
            found = row.fetchone()
            return found[0] if found else None
    except Exception as exc:  # noqa: BLE001
        log.warning("research_last_at_failed", error=str(exc))
        return None


async def _is_current(window_hours: int = _FRESHNESS_WINDOW_HOURS) -> bool:
    """True when the latest completed digest is within `window_hours`."""
    last = await last_research_run_at()
    if last is None:
        return False
    age = datetime.now(timezone.utc) - last
    return age < timedelta(hours=window_hours)


# ── Persistence helpers ──────────────────────────────────────────────────────

async def _create_running_row(triggered_by: str) -> int | None:
    """INSERTs the 'running' row — both the placeholder and the
    concurrency lock. Returns its id, or None on a database error."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "INSERT INTO macro_research_digests "
                "(triggered_by, status) "
                "VALUES (:tb, 'running') RETURNING id"
            ), {"tb": triggered_by})
            new_id = row.scalar()
            await session.commit()
            return int(new_id) if new_id is not None else None
    except Exception as exc:  # noqa: BLE001
        log.warning("research_create_running_failed", error=str(exc))
        return None


async def _finalise_row(
    row_id: int, *, digest: dict[str, Any], usage: dict[str, Any],
    status: str,
) -> None:
    """UPDATEs the running row with the completed digest. Fail-open."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return
        async with AsyncSessionLocal() as session:
            await session.execute(text(
                "UPDATE macro_research_digests SET "
                " status = :st, summary_text = :st_text, "
                " regime_implication = :reg, "
                " key_signals = :sigs, citation_urls = :urls, "
                " model = :model, raw_response = :raw, "
                " error = :err, metadata = :md "
                "WHERE id = :id"
            ), {
                "id": row_id, "st": status,
                "st_text": digest.get("summary_text") or "",
                "reg": digest.get("regime_implication") or "",
                "sigs": json.dumps(digest.get("key_signals") or []),
                "urls": json.dumps(digest.get("citation_urls") or []),
                "model": usage.get("model"),
                "raw": digest.get("raw_response") or "",
                "err": digest.get("error"),
                "md": json.dumps({
                    "input_tokens":  usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "n_searches":    usage.get("n_searches"),
                    "n_fetches":     usage.get("n_fetches"),
                }),
            })
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("research_finalise_failed",
                    row_id=row_id, error=str(exc))


# ── Read accessors — Commit 3 (macro_context) consumes these ─────────────────

async def get_latest_digest() -> dict[str, Any] | None:
    """Returns the most recent COMPLETED digest as a dict, or None
    when no completed digest exists yet. The frontend widget and
    macro_context.inject_macro_digest both read this. JSONB columns
    are returned as deserialised Python lists/dicts.

    Fail-open: any database error returns None — Commit 3's injector
    treats None as "no digest available, run text-only."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT id, generated_at, triggered_by, summary_text, "
                " regime_implication, key_signals, citation_urls, "
                " model, metadata "
                "FROM macro_research_digests "
                "WHERE status = 'complete' "
                "ORDER BY generated_at DESC LIMIT 1"))
            found = row.fetchone()
            if not found:
                return None
            # asyncpg returns JSONB as already-deserialised structures
            # in this codebase's setup; deserialise from a string only
            # when the column comes back as one (a defensive guard
            # against future driver swaps).
            sigs = found[5]
            urls = found[6]
            md = found[8]
            if isinstance(sigs, str):
                try:
                    sigs = json.loads(sigs)
                except json.JSONDecodeError:
                    sigs = []
            if isinstance(urls, str):
                try:
                    urls = json.loads(urls)
                except json.JSONDecodeError:
                    urls = []
            if isinstance(md, str):
                try:
                    md = json.loads(md)
                except json.JSONDecodeError:
                    md = {}
            return {
                "id":                 int(found[0]),
                "generated_at":       found[1].isoformat() if found[1] else None,
                "triggered_by":       found[2],
                "summary_text":       found[3] or "",
                "regime_implication": found[4] or "",
                "key_signals":        sigs or [],
                "citation_urls":      urls or [],
                "model":              found[7],
                "metadata":           md or {},
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("research_latest_read_failed", error=str(exc))
        return None


async def get_recent_digests(limit: int = 10) -> list[dict[str, Any]]:
    """Returns the N most recent runs (every status). Powers the
    sysadmin history view on the dashboard widget. Fail-open → []."""
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text(
                "SELECT id, generated_at, triggered_by, status, "
                " summary_text, error, model "
                "FROM macro_research_digests "
                "ORDER BY generated_at DESC LIMIT :lim"
            ), {"lim": max(1, int(limit))})
            return [{
                "id":           int(r[0]),
                "generated_at": r[1].isoformat() if r[1] else None,
                "triggered_by": r[2],
                "status":       r[3],
                "summary_text": r[4] or "",
                "error":        r[5],
                "model":        r[6],
            } for r in rows.fetchall()]
    except Exception as exc:  # noqa: BLE001
        log.warning("research_recent_read_failed", error=str(exc))
        return []


# ── Orchestrator ─────────────────────────────────────────────────────────────

def _is_test_env() -> bool:
    import os
    return os.getenv("ENVIRONMENT", "").lower() == "test"


def _mock_digest() -> tuple[dict[str, Any], dict[str, Any]]:
    """Deterministic digest used in the test env so suite runs do not
    hit Anthropic. Mirrors triage_engine._mock_triage_report pattern."""
    digest = {
        "summary_text": (
            "Test environment digest — no live web_search occurred."),
        "key_signals": [
            {"category": "rates",
             "signal":   "Stub signal — test environment.",
             "implication": "No real implication in tests.",
             "source_url": "https://example.com/stub"},
        ],
        "regime_implication": "Stub regime — test environment.",
        "citation_urls":      ["https://example.com/stub"],
        "raw_response":       "stub",
    }
    usage = {"input_tokens": 0, "output_tokens": 0,
             "model": "claude-sonnet-4-6",
             "n_searches": 0, "n_fetches": 0}
    return digest, usage


async def run_research(triggered_by: str = "manual") -> dict[str, Any]:
    """
    End-to-end run: lock → call agent → persist.

    Returns a summary dict {status, row_id, signals_count, citations_count,
    skipped_reason?} for the caller (an endpoint, the scheduler, a test).

    Skip paths (all log + return early, never raise):
      - already_running     — a 'running' row exists (concurrency lock)
      - row_create_failed   — database write failed before agent call

    Status paths:
      - complete            — agent returned a non-empty digest
      - failed              — agent returned a digest carrying `error`,
                              persisted for audit
    """
    if await is_research_running():
        log.info("research_run_skipped_already_running",
                 triggered_by=triggered_by)
        return {"status": "skipped", "reason": "already_running"}

    row_id = await _create_running_row(triggered_by)
    if row_id is None:
        log.warning("research_run_skipped_row_create_failed",
                    triggered_by=triggered_by)
        return {"status": "skipped", "reason": "row_create_failed"}

    log.info("research_run_started",
             triggered_by=triggered_by, row_id=row_id)

    # Freshness guard AFTER row creation. A non-manual trigger that
    # reaches this point with a current digest (under the freshness
    # window) skips execution rather than burning model budget — but
    # the row already exists, so we MUST finalise it as 'skipped'
    # instead of returning silently. Without this, the row sits in
    # 'running' forever and the concurrency lock blocks every
    # subsequent run (the row-15 bug, May 23 2026: a non-manual
    # trigger reached this path with a current digest and the row
    # never got finalised).
    #
    # Manual runs (triggered_by="manual") intentionally bypass the
    # freshness window — the sysadmin clicked Run Now to force a
    # fresh digest, so we do not skip.
    if triggered_by != "manual" and await _is_current():
        log.info(
            "research_run_skipped_current_post_row",
            row_id=row_id, triggered_by=triggered_by)
        await _finalise_row(
            row_id,
            digest={
                "summary_text":       "",
                "regime_implication": "",
                "key_signals":        [],
                "citation_urls":      [],
                "raw_response":       "",
                "error": (
                    "Run skipped: a completed digest under the 24h "
                    "freshness window already exists. Row finalised "
                    "as skipped so the concurrency lock is released."),
            },
            usage={
                "model": None, "input_tokens": None,
                "output_tokens": None,
                "n_searches": None, "n_fetches": None,
            },
            status="skipped",
        )
        return {
            "status": "skipped",
            "reason": "current",
            "row_id": row_id,
        }

    # CRITICAL: the row exists in 'running' state. Every exit path
    # below MUST finalise it, otherwise it sits in 'running' forever
    # and holds the concurrency lock. The reaper catches stuck rows
    # past _RUN_TIMEOUT_MINUTES, but a row stuck under the timeout
    # still blocks the dashboard's Run Now button until the timeout
    # elapses. Wrap the agent + finalise path in try/except and
    # finalise on any unhandled exception.
    try:
        # Agent generation. Synchronous (SDK is synchronous). In the
        # test env we substitute the mock digest so pytest never hits
        # Anthropic.
        if _is_test_env():
            digest, usage = _mock_digest()
        else:
            from agents.research_agent import generate_digest
            # Run the synchronous SDK call off the event loop.
            digest, usage = await asyncio.to_thread(generate_digest)

        status = "failed" if digest.get("error") else "complete"
        await _finalise_row(
            row_id, digest=digest, usage=usage, status=status)
    except Exception as exc:  # noqa: BLE001
        # Defensive: ensure the row is finalised on ANY unhandled
        # exception so it does not stay 'running' forever. Without
        # this, a generate_digest exception propagates and the row
        # holds the concurrency lock until the timeout reaper fires.
        log.warning(
            "research_run_unhandled_exception",
            row_id=row_id, triggered_by=triggered_by, error=str(exc))
        await _finalise_row(
            row_id,
            digest={
                "summary_text":       "",
                "regime_implication": "",
                "key_signals":        [],
                "citation_urls":      [],
                "raw_response":       "",
                "error": (
                    f"Unhandled exception in run_research: {exc}. "
                    f"Row finalised as failed so the concurrency lock "
                    f"is released."),
            },
            usage={
                "model": None, "input_tokens": None,
                "output_tokens": None,
                "n_searches": None, "n_fetches": None,
            },
            status="failed",
        )
        return {
            "status": "failed",
            "row_id": row_id,
            "error":  str(exc),
            "signals_count": 0,
            "citations_count": 0,
        }

    # Refresh the macro-context cache (Commit 3) so the fresh digest
    # flows into agent prompts within one tick of land. A failed run
    # ALSO refreshes — the cache returns to whatever the latest
    # successful digest is via get_latest_digest's WHERE clause. The
    # cache layer is fail-open so a refresh problem never raises into
    # this orchestrator.
    try:
        from tools.macro_context import refresh_macro_context
        await refresh_macro_context()
    except Exception as exc:  # noqa: BLE001
        log.warning("research_post_refresh_failed", error=str(exc))
    # Item 5 (May 23 2026 — analytics narrative). The fifth sentence
    # of the narrative consumes the macro digest. Refresh it after
    # the macro cache lands so the next agent prompt picks up the
    # newest framing.
    try:
        from tools.analytics_context import refresh_analytics_context
        await refresh_analytics_context()
    except Exception as exc:  # noqa: BLE001
        log.warning("research_post_analytics_refresh_failed",
                    error=str(exc))
    log.info("research_run_complete",
             row_id=row_id, status=status,
             n_signals=len(digest.get("key_signals") or []),
             n_citations=len(digest.get("citation_urls") or []))
    return {
        "status": status,
        "row_id": row_id,
        "signals_count": len(digest.get("key_signals") or []),
        "citations_count": len(digest.get("citation_urls") or []),
    }


async def run_research_if_stale() -> dict[str, Any]:
    """Idempotent variant — skips the run when a completed digest is
    less than 24 hours old. The scheduler / startup hook calls this,
    so a Render redeploy within the freshness window is a no-op.
    Manual sysadmin runs bypass this and call run_research directly."""
    if await _is_current():
        log.info("research_run_skipped_current")
        return {"status": "skipped", "reason": "current"}
    return await run_research("scheduled")


# ── Async trigger — fire-and-forget loop-or-thread spawn ─────────────────────

def trigger_research_async(reason: str = "scheduled") -> None:
    """
    Spawns run_research_if_stale() in the background. Mirrors
    audit_engine.trigger_audit_async — works on or off an event loop,
    fail-open. Fired from the lifespan startup hook (Commit 4) so a
    cold Render boot produces a digest within minutes if none exists.

    A manual sysadmin trigger calls run_research() directly via the
    endpoint, NOT this function — manual runs intentionally skip the
    24h freshness gate.

    Defensive test-env guard: refuses to spawn anything in
    ENVIRONMENT=test so a background task can't write a 'running' row
    to the test DB and pollute subsequent assertions. main.py's
    lifespan hook already skips the trigger in test env; this guard
    is the belt to that brace.
    """
    if _is_test_env():
        log.info("research_trigger_async_skipped_test_env", reason=reason)
        return
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            task = loop.create_task(_run_with_reason(reason))
            _research_bg_tasks.add(task)
            task.add_done_callback(_research_bg_tasks.discard)
        else:
            threading.Thread(
                target=lambda: asyncio.run(_run_with_reason(reason)),
                daemon=True, name="auto-research",
            ).start()
    except Exception as exc:  # noqa: BLE001
        log.warning("research_spawn_failed", reason=reason, error=str(exc))


async def _run_with_reason(reason: str) -> None:
    """Thin wrapper so the trigger can be both stale-aware AND carry a
    custom reason. 'startup' bypasses the stale gate (a fresh deploy
    that already has a < 24h digest still gets a 'startup' run logged
    if no completed digest exists; otherwise it skips)."""
    if reason == "startup":
        # Startup hook — kick off a digest only when none exists OR
        # the latest is stale; same gate as the scheduled path.
        result = await run_research_if_stale()
    else:
        result = await run_research_if_stale()
    log.info("research_auto_trigger_complete",
             reason=reason, status=result.get("status"),
             skipped_reason=result.get("reason"))
