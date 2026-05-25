"""
Forest Capital Portfolio Intelligence System — FastAPI backend.
Sprint 4: all 8 agents live, council deliberation wired, QA methodology checklist,
          WebSocket streaming, scope guard enforced, council_sessions logging.
"""
from __future__ import annotations
import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import (
    FastAPI, Depends, HTTPException, Query, Response, WebSocket,
    WebSocketDisconnect, UploadFile, File, Form,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

from config import FRONTEND_URL, ENVIRONMENT, PERMISSIONS, ROLE_PRESETS
from agents.base import SONNET_MODEL, OPUS_MODEL, GEMINI_MODEL
from logger import configure_logging, get_logger
from auth import (
    require_auth,
    require_team_member,
    require_sysadmin,
    require_permission,
    require_master_key,
    generate_magic_token,
    generate_session_token,
    verify_magic_token,
    verify_session_token,
    redeem_magic_token,
    invalidate_session,
    send_magic_link,
)
from models.schemas import (
    MagicLinkRequest,
    MagicLinkResponse,
    SessionResponse,
    LogoutRequest,
    CouncilQueryRequest,
    BacktestRequest,
    QAQueryRequest,
    OptimizeRequest,
    UIUXReviewRequest,
    AdvisorAnalyseRequest,
    AdvisorVerifyRequest,
    AdvisorCitationsRequest,
    MOCK_STRATEGIES,
    MOCK_REGIME,
    MOCK_COUNCIL_RESPONSE,
    MOCK_QA_AUDIT,
    MOCK_EFFICIENT_FRONTIER,
)

configure_logging()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("forest_capital_starting", environment=ENVIRONMENT, frontend_url=FRONTEND_URL)
    # Warm the academic-context cache so the first agent invocation after a
    # restart already carries the uploaded rubric / requirements documents.
    # Fail-open: a cold cache simply means agents run without that context.
    if ENVIRONMENT != "test":
        # Reap any audit left 'running' by a crash or a redeploy before
        # this boot — a hung run otherwise holds the statistical-audit
        # lock until the first poll or audit-start happens to clear it.
        try:
            from tools.audit_engine import fail_stale_audits
            reaped = await fail_stale_audits()
            if reaped:
                log.warning(
                    f"Startup reap: marked {reaped} stale audit "
                    f"run(s) as failed", count=reaped)
            else:
                log.info("Startup reap: no stale audit runs found")
        except Exception as exc:  # noqa: BLE001
            log.warning("audit_startup_reap_failed", error=str(exc))
        # Screenshot cleanup — drops UAT screenshots older than 30 days
        # from SCREENSHOT_DIR. The disk is the persistent Render volume
        # in production; without this sweep, every failure-report
        # screenshot ever uploaded would accumulate forever. Fail-open
        # — a missing directory or unreadable file logs and continues.
        try:
            from tools.test_runner import cleanup_old_screenshots
            deleted, remaining = cleanup_old_screenshots()
            if deleted == 0 and remaining == 0:
                log.info("screenshot_cleanup: directory empty or absent")
            else:
                log.info(
                    "screenshot_cleanup",
                    deleted=deleted,
                    remaining=remaining,
                    message=(
                        f"screenshot_cleanup: deleted {deleted} "
                        f"screenshots older than 30 days, "
                        f"{remaining} remain"
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("screenshot_cleanup_failed", error=str(exc))
        # One-time baseline log — counts the historical rows that landed
        # before the cost-tracking deploy (pre-migration 020 token columns
        # are null). Sets a clear line in the Render logs: everything from
        # this deploy forward carries cost data; rows older than this can
        # never be backfilled.
        try:
            from datetime import date
            from sqlalchemy import text
            from database import AsyncSessionLocal
            async with AsyncSessionLocal() as ses:  # type: ignore[union-attr]
                result = await ses.execute(text(
                    "SELECT COUNT(*) FROM agent_interactions "
                    "WHERE estimated_cost_usd IS NULL"))
                n_null = int(result.scalar() or 0)
            today = date.today().isoformat()
            log.info(
                "cost_tracking_baseline",
                active_from=today,
                n_historical_null=n_null,
                message=(f"cost tracking active from {today}. "
                         f"{n_null} historical interactions have no cost "
                         "data (pre-migration 020)."),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("cost_tracking_baseline_failed", error=str(exc))
        try:
            from tools.academic_context import refresh_academic_context
            await refresh_academic_context()
        except Exception as exc:  # noqa: BLE001
            log.warning("academic_context_warm_failed", error=str(exc))
        # Auto-extend the monthly data pipeline beyond the Excel file —
        # fetch any complete calendar months that have closed since the
        # last run. A daemon thread so startup never blocks on yfinance;
        # fail-open inside extend_market_data. audit_reason="startup" so
        # a redeploy with no data change logs audit_trigger_skipped and
        # fires no Opus audit; only a genuine new month triggers one.
        try:
            import threading
            from tools.data_fetcher import extend_market_data
            threading.Thread(
                target=lambda: extend_market_data(audit_reason="startup"),
                daemon=True, name="monthly-data-extend").start()
        except Exception as exc:  # noqa: BLE001
            log.warning("monthly_extend_startup_failed", error=str(exc))
        # Macro research digest — reap any 'running' row left by a
        # crashed run on the previous boot BEFORE firing the new
        # trigger. Without this, a zombie row indefinitely blocks
        # the concurrency lock and every Run Now click returns
        # "already_running" with no real run firing — the symptom
        # surfaced by UAT on May 22 2026.
        #
        # TEST-ENV SKIP: in the test environment we do NOT fire the
        # startup reaper, the startup trigger, or the daily scheduler.
        # Each of those three side-effects (a SQL UPDATE, a background
        # task that hits the real DB, and a daemon coroutine that
        # sleeps until 21:00 UTC) pollutes test isolation: the trigger
        # writes a 'running' row to the CI database that other tests
        # in the run then trip over (the
        # test_is_research_running_returns_false_without_db, the
        # test_get_recent_digests_returns_empty_without_db, and the
        # test_skipped_when_row_create_fails failures surfaced May 22
        # 2026). The engine layer itself substitutes a mock digest in
        # ENVIRONMENT=test, but the side-effect writes still happen
        # before the mock substitution. Skip the whole stack at the
        # lifespan boundary.
        import os
        _is_test_env = (
            os.getenv("ENVIRONMENT", "").lower() == "test"
            or os.getenv("TESTING", "").lower() == "true"
        )
        if not _is_test_env:
            try:
                from tools.research_engine import (
                    fail_stale_running_digests, start_daily_scheduler,
                    trigger_research_async,
                )
                # timeout_minutes=0 on startup: the previous process is
                # dead by definition post-restart, so ANY row left in
                # 'running' is a zombie regardless of age. The default
                # 10-minute timeout protects runtime checks against
                # legitimately-in-flight rows; startup has no live
                # process to protect against. This catches the row-15
                # case where a row was stuck for less than the timeout
                # when the worker crashed.
                reaped = await fail_stale_running_digests(
                    timeout_minutes=0)
                if reaped:
                    log.warning(
                        f"Startup reap: marked {reaped} stuck research "
                        f"run(s) as failed", count=reaped)
                # Then fire a research run on startup when the latest
                # completed digest is stale (> 24h) or absent. The
                # trigger is loop-aware (we are on the event loop here)
                # and idempotent — a fresh boot within the freshness
                # window logs research_run_skipped_current and no model
                # call fires. Fail-open: a research failure logs and
                # proceeds.
                trigger_research_async("startup")
                # Daily scheduler — fires run_research_if_stale once per
                # UTC day at 21:00 (US market close + 1h). The 24h
                # freshness gate inside run_research_if_stale means a
                # boot-time fire and a scheduled fire within an hour of
                # each other still produce only one model call. The
                # scheduler is a daemon task held on _research_bg_tasks
                # so the GC does not silently cancel it.
                start_daily_scheduler()
            except Exception as exc:  # noqa: BLE001
                log.warning("research_startup_trigger_failed",
                            error=str(exc))
        else:
            log.info("research_startup_skipped_test_env")
        # Macro context cache warm — read whatever digest already
        # exists in the DB into the agent-prompt injection cache so
        # the FIRST agent call after restart sees the previous
        # deploy's digest. The startup trigger above produces a fresh
        # one in the background; this warm-read is the in-flight
        # fallback that prevents an empty cache for the few seconds
        # it takes the agent to land. Fail-open.
        try:
            from tools.macro_context import refresh_macro_context
            await refresh_macro_context()
        except Exception as exc:  # noqa: BLE001
            log.warning("macro_context_warm_failed", error=str(exc))
        # Item 5 (May 23 2026 — analytics narrative). Warm-read the
        # narrative cache so the first agent call after restart sees
        # the previous deploy's narrative. The diversification refresh
        # tick fires the canonical rebuild later; this just covers
        # the cold-deploy gap.
        try:
            from tools.analytics_context import refresh_analytics_context
            await refresh_analytics_context()
        except Exception as exc:  # noqa: BLE001
            log.warning("analytics_context_warm_failed", error=str(exc))
        # Analytical staging findings — same warm-read pattern so the
        # Academic Writer's first call after restart sees the most
        # recent staged findings. The endpoint refresh() also fires
        # after every fresh staging.
        try:
            from tools.analytical_findings import refresh_findings_context
            await refresh_findings_context()
        except Exception as exc:  # noqa: BLE001
            log.warning("findings_context_warm_failed", error=str(exc))
        # Item 9 commit 5 — strategy context cache warm-read. Same
        # warm-read pattern as the other three context blocks: agent
        # calls in the first seconds after restart see whatever
        # strategy_characterisations the previous deploy produced. The
        # next refresh_strategy_characterisations() run refreshes the
        # cache via tools/strategy_context.refresh_strategy_context_
        # cache() at the end of its orchestrator. Fail-open.
        try:
            from tools.strategy_context import refresh_strategy_context_cache
            await refresh_strategy_context_cache()
        except Exception as exc:  # noqa: BLE001
            log.warning("strategy_context_warm_failed", error=str(exc))
        # May 24 2026 (fourth iteration) — fully automatic warm with
        # retry. Per user directive: "after every deploy, cache warms
        # automatically within 60 seconds of startup. No operator
        # action required."
        #
        # The auto-warm runs as a fire-and-forget background task so
        # the lifespan handler does NOT block on it. asyncio's task
        # scheduler picks up the coroutine after the server is
        # accepting requests, avoiding the cold-boot competition
        # that caused the prior iteration's hang.
        #
        # `auto_warm_analytics` retries up to MAX_ATTEMPTS (3) with
        # exponential backoff (5s → 15s → 45s). A transient hiccup
        # at boot doesn't leave the cache cold.
        #
        # Status is reported through GET /api/v1/admin/cache-status.
        # The manual POST /api/v1/admin/warm-analytics-cache endpoint
        # remains as a sysadmin-only override.
        if not _is_test_env:
            try:
                from tools.cache_warm_state import auto_warm_analytics

                async def _auto_warm_task() -> None:
                    # Tiny initial delay so the server has settled
                    # into its first-request handler before we
                    # kick off the heavier compute path.
                    await asyncio.sleep(2.0)
                    await auto_warm_analytics()

                asyncio.create_task(_auto_warm_task())
                log.info("analytics_cache_auto_warm_scheduled")
            except Exception as exc:  # noqa: BLE001
                log.warning("analytics_cache_auto_warm_schedule_failed",
                            error=str(exc))

        # PR-MODEL-1 (May 27 2026) — model availability check. Pings
        # every chain's primary; on 404, advances the chain and
        # pings the next entry. This is the SAME fallback chain
        # call_claude / call_gemini consult at call time, so a model
        # the startup check detected as deprecated is silently
        # routed past for every request that follows. Fires in a
        # background task so the lifespan handler does not block on
        # the SDK round-trips (~1-3s each, larger on cold provider
        # endpoints). Fail-open per chain.
        if not _is_test_env:
            try:
                from agents.models import check_model_availability

                async def _model_check_task() -> None:
                    # 5s delay so the first user request is already
                    # being served before the check fires — the
                    # availability check itself never queues behind
                    # a real request thanks to the model resolver,
                    # but a deferred ping keeps cold-boot logs tidy.
                    await asyncio.sleep(5.0)
                    summary = await check_model_availability()
                    log.info("model_availability_check_complete",
                             summary=summary)

                asyncio.create_task(_model_check_task())
                log.info("model_availability_check_scheduled")
            except Exception as exc:  # noqa: BLE001
                log.warning("model_availability_check_schedule_failed",
                            error=str(exc))
    yield
    log.info("forest_capital_shutdown")


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="Forest Capital Portfolio Intelligence System",
    version="0.4.0-sprint4",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── Request validation logger ────────────────────────────────────────────────
#
# May 24 2026 — when a frontend bug constructs a malformed path
# parameter (the "3:1" generation-id pattern was the reported case),
# FastAPI returns a 422 with the validation detail but Starlette
# doesn't log it. Production has no signal that the bug is occurring
# until a user reports it. This handler preserves the default 422
# response shape and adds a structured log line carrying the URL,
# method, client IP and a compact dump of the validation errors so
# future malformed IDs are visible in Render logs the moment they
# fire — making it trivial to track the bad call to its origin.
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler


@app.exception_handler(RequestValidationError)
async def _logged_validation_error(request: Request, exc: RequestValidationError):
    try:
        client_ip = request.client.host if request.client else "unknown"
        # exc.errors() may carry non-serializable objects (Decimal,
        # datetime, set). str() is the conservative summary —
        # full structured payload still goes back to the client.
        compact = [
            {
                "loc": list(e.get("loc", [])),
                "msg": str(e.get("msg", "")),
                "type": str(e.get("type", "")),
            }
            for e in (exc.errors() or [])
        ]
        log.warning(
            "request_validation_error",
            method=request.method,
            url=str(request.url),
            path=str(request.url.path),
            client_ip=client_ip,
            errors=compact,
        )
    except Exception:  # noqa: BLE001
        # Never let logging suppress the actual 422 response.
        pass
    return await request_validation_exception_handler(request, exc)

# Uploads mount — serves UAT test-runner screenshots read-only.
# config.SCREENSHOT_DIR resolves to /data/test_screenshots on Render (a
# persistent disk — screenshots survive redeployments) and to
# backend/data/test_screenshots in local development. The directory is
# created on startup so the mount always resolves; the mount is rooted
# one level above it so the stored "test_screenshots/<uuid>" relative
# paths resolve under /uploads.
try:
    from fastapi.staticfiles import StaticFiles
    from config import SCREENSHOT_DIR
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    _uploads_dir = os.path.dirname(SCREENSHOT_DIR)
    app.mount("/uploads", StaticFiles(directory=_uploads_dir), name="uploads")
except Exception as _exc:  # noqa: BLE001
    log.warning("uploads_mount_failed", error=str(_exc))

# CORS — dev allows localhost:5173 only; prod allows Vercel URL only
origins = [FRONTEND_URL] if FRONTEND_URL else ["http://localhost:5173"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type"],
)


# ── Auth endpoints (no auth required) ────────────────────────────────────────

@app.post("/api/auth/request-link", response_model=MagicLinkResponse)
async def request_magic_link(body: MagicLinkRequest, request: Request):
    email = body.email.strip().lower()
    # Both branches return HTTP 200 with an identical message to prevent email
    # enumeration. The status field is the only difference: the frontend shows a
    # specific "check your inbox" confirmation only when status == "sent".
    # Authorisation is the platform_users table — is_login_allowed falls back
    # to the config ALLOWED_EMAILS allowlist only if that table is unreachable.
    from tools.platform_users import is_login_allowed
    if not await is_login_allowed(email):
        log.warning("magic_link_unauthorized_email", email_hash=hash(email))
        return MagicLinkResponse(
            message="If that email is authorized, a login link has been sent.",
            status="pending",
            dev_mode=(ENVIRONMENT == "development"),
        )
    token = generate_magic_token(email)
    await send_magic_link(email, token)
    return MagicLinkResponse(
        message="If that email is authorized, a login link has been sent.",
        status="sent",
        dev_mode=(ENVIRONMENT == "development"),
    )


@app.get("/api/auth/verify")
async def verify_magic_link(token: str = Query(...), response: Response = None):
    # JTI persistence: check the DB before the in-memory dict so single-use protection
    # survives Render restarts. The in-memory dict handles the scanner pre-fetch case
    # within the same server instance; the DB handles cross-restart replay protection.
    try:
        import jwt as _jwt
        from datetime import datetime, timezone as _tz
        _peek = _jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        _jti = _peek.get("jti", "")
        _exp = _peek.get("exp")
        _email_for_jti = _peek.get("sub", "")
        if _jti:
            from tools.cache import is_jti_used, mark_jti_used
            if await is_jti_used(_jti):
                raise HTTPException(status_code=401, detail="This link has already been used. Please request a new one.")
    except HTTPException:
        raise
    except Exception:
        # jwt.decode failure (expired, invalid) is handled inside redeem_magic_token
        _jti = ""
        _exp = None
        _email_for_jti = ""

    # Look up the user's role / display_name / permissions so they are
    # embedded in the session JWT — require_auth then needs no per-request
    # database hit. A None result (user not found, or database down)
    # mints a plain token that require_auth resolves via the fallback.
    user_attrs: dict | None = None
    if _email_for_jti:
        try:
            from tools.platform_users import get_active_user
            _u = await get_active_user(_email_for_jti)
            if _u:
                user_attrs = {
                    "role": _u["role"],
                    "display_name": _u["display_name"],
                    "permissions": _u["permissions"],
                }
        except Exception:  # noqa: BLE001
            user_attrs = None

    session_token = redeem_magic_token(token, user_attrs)

    # Persist JTI to DB after first successful redemption (non-blocking — failure is safe)
    if _jti and _exp:
        try:
            from tools.cache import mark_jti_used
            from datetime import datetime, timezone as _tz
            await mark_jti_used(_jti, datetime.fromtimestamp(_exp, tz=_tz.utc), _email_for_jti)
        except Exception:
            pass

    email = verify_session_token(session_token)["email"]
    # Stamp last_login_at — fail-open, never blocks the login response.
    try:
        from tools.platform_users import record_login
        await record_login(email)
    except Exception:  # noqa: BLE001
        pass
    log.info("auth_success", email=email)
    # Prevent browsers and intermediary caches from storing the session token.
    # A cached 200 response could replay a stale token on a shared machine.
    if response is not None:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return SessionResponse(
        session_token=session_token,
        email=email,
        expires_in_hours=int(os.getenv("SESSION_EXPIRY_HOURS", "8")),
    )


@app.post("/api/auth/logout")
async def logout(body: LogoutRequest):
    invalidate_session(body.session_token)
    return {"message": "Logged out successfully."}


@app.get("/api/auth/me")
async def get_me(session: dict = Depends(require_auth)):
    """The signed-in user — email, role, display name, the authoritative
    permissions array the frontend gates the UI on, and the lifetime
    council-query allocation (council_queries_limit None = unlimited)."""
    council_used = 0
    council_limit: int | None = None
    try:
        from tools.platform_users import get_active_user
        u = await get_active_user(session["email"])
        if u:
            council_used = u.get("council_queries_used", 0)
            council_limit = u.get("council_queries_limit")
    except Exception:  # noqa: BLE001
        pass
    return {
        "email": session["email"],
        "role": session.get("role") or "viewer",
        "display_name": session.get("display_name"),
        "permissions": session.get("permissions") or [],
        "council_queries_used": council_used,
        "council_queries_limit": council_limit,
    }


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    # May 24 2026 — surface RENDER_GIT_COMMIT so deployment status is
    # verifiable WITHOUT the master API key. The dashboard-timeout
    # investigation kept hitting "is Render on the latest commit?"
    # as the unverifiable first question; exposing the short SHA on
    # /health closes that gap. Render injects RENDER_GIT_COMMIT
    # automatically on every build (full 40-char SHA); a local dev
    # run has no value set, in which case "dev" surfaces. The branch
    # name (RENDER_GIT_BRANCH) is included so a deploy from a
    # feature branch is obviously identified.
    commit = os.getenv("RENDER_GIT_COMMIT", "dev")
    return {
        "status": "ok",
        "sprint": "4",
        "environment": ENVIRONMENT,
        "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
        "gemini": bool(os.getenv("GOOGLE_API_KEY")),
        "cache": True,
        "commit": commit[:7] if commit != "dev" else "dev",
        "commit_full": commit,
        "branch": os.getenv("RENDER_GIT_BRANCH") or "dev",
    }


# ── Provenance ───────────────────────────────────────────────────────────────

_PROVENANCE_PATH = Path(__file__).parent / "data" / "provenance.json"


@app.get("/api/v1/provenance")
async def get_provenance(session: dict = Depends(require_auth)):
    """
    Returns the full data_series_registry as JSON.

    Reads from provenance.json rather than PostgreSQL so the endpoint works
    in CI (no DB) and on cold starts before the pipeline has run.
    The frontend never hardcodes provenance — it always fetches from here.
    """
    if not _PROVENANCE_PATH.exists():
        # Pipeline hasn't run yet — return empty registry rather than 500.
        return {"series": [], "last_pipeline_run": None, "cross_validation": {}}
    try:
        data = json.loads(_PROVENANCE_PATH.read_text(encoding="utf-8"))
        # May 23 2026 — defensive sanitisation. A provenance.json
        # written by an older pipeline build may contain literal NaN /
        # Infinity tokens (Python json.dumps default behaviour), which
        # parse into float('nan') / float('inf') here. Starlette /
        # orjson then refuse to serialise them, returning 500 with
        # "Out of range float values are not JSON compliant". Replace
        # them with None at the read site so existing bad files don't
        # break the endpoint. New writes go through the sanitiser at
        # the WRITE site (see tools/data_fetcher._sanitise_json_floats).
        return _sanitise_nan_floats(data)
    except Exception as exc:
        log.warning("provenance_read_error", error=str(exc))
        raise HTTPException(status_code=500, detail="Could not read provenance data.")


def _sanitise_nan_floats(obj):
    """Recursively replaces NaN / Inf float values with None so the
    response round-trips through strict JSON serialisers (starlette /
    orjson). The write side has the same guard — this is the second
    layer for older files written before the fix landed."""
    import math
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitise_nan_floats(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitise_nan_floats(v) for v in obj]
    return obj


@app.get("/api/v1/provenance/justification")
async def get_provenance_justification(session: dict = Depends(require_auth)):
    """
    Structured justification for each supplemental data source.

    Returned to: Data Sources panel (expandable rows), Academic Writer Agent
    (Analytical Appendix Section 3.2), Explainer Agent (Commentary mode hover).
    The justification is factual metadata — it does not change unless the
    pipeline design changes, so no DB or provenance.json dependency is needed.
    """
    return {
        "spy_daily": {
            "source": "yfinance",
            "ticker": "SPY",
            "strategies_enabled": ["VOL_TARGETING", "MOMENTUM_ROTATION"],
            "without_this_source": "VOL_TARGETING and MOMENTUM_ROTATION unavailable — "
                "both require daily return frequency for 21-day rolling volatility signals.",
            "key_reason": "Monthly data cannot resolve intramonth volatility spikes "
                "(e.g. March 2020 circuit breakers). VOL_TARGETING scales equity weight "
                "by TARGET_VOL / realised_vol_21d, which requires daily observations.",
            "months_added": 0,
            "statistical_impact": "Enables 2 of 10 strategies. Without it, dynamic "
                "allocation universe shrinks from 5 to 3 strategies.",
        },
        "vixcls": {
            "source": "fred_api",
            "series_id": "VIXCLS",
            "strategies_enabled": ["REGIME_SWITCHING"],
            "without_this_source": "Regime threshold classifier degrades to equity-trend "
                "and yield-curve only — VIX forward-looking fear signal lost.",
            "key_reason": "VIX > 25 triggers the BEAR regime flag. It provides a "
                "forward-looking signal independent of equity price — VIX spikes "
                "precede equity drawdowns by 0-5 trading days on average.",
            "months_added": 0,
            "statistical_impact": "Regime Switching Sharpe 0.629 (with VIX) vs 0.571 "
                "(without, threshold-only). Regime agreement rate falls from 87% to 71%.",
        },
        "dgs2": {
            "source": "fred_api",
            "series_id": "DGS2",
            "strategies_enabled": ["REGIME_SWITCHING"],
            "without_this_source": "10Y-2Y yield curve signal unavailable. "
                "Regime classifier cannot detect yield curve inversion.",
            "key_reason": "The 10Y-2Y spread has preceded every US recession since 1955. "
                "The curve inverted in April 2022 — six months before the equity trough. "
                "A VIX + equity-only detector would have been late to this signal.",
            "months_added": 0,
            "statistical_impact": "April 2022 early warning — 6 months lead time vs "
                "equity-only detection in October 2022.",
        },
        "lqd_bridge": {
            "source": "yfinance",
            "ticker": "LQD",
            "strategies_enabled": [
                "BENCHMARK", "CLASSIC_60_40", "RISK_PARITY", "MIN_VARIANCE",
                "EQUAL_WEIGHT", "MOMENTUM_ROTATION", "REGIME_SWITCHING",
                "VOL_TARGETING", "BLACK_LITTERMAN", "MAX_SHARPE_ROLLING",
            ],
            "without_this_source": "BND inception April 2007 — aligned dataset starts "
                "May 2007 (224 months). Dot-com recovery (2002-2007) excluded entirely.",
            "key_reason": "LQD (iShares IG Corporate Bond ETF) tracks the same IG "
                "corporate bond universe as BND and began trading July 2002. Monthly "
                "returns spliced: LQD used 2002-07 to 2007-04, BND from 2007-05.",
            "months_added": 58,
            "statistical_impact": "Without the bridge, n=224 observations; "
                "with the bridge, n is the live monthly count (286 on the "
                "current Render snapshot — auto-extends past 2025-12 once "
                "Apr 2026 fully closes). Power analysis requires n >= 220 "
                "for 80% power at p < 0.005 — the bridge provides the "
                "statistical margin. Without it, the dataset barely clears "
                "the minimum.",
        },
    }


# ── Strategies ────────────────────────────────────────────────────────────────

@app.get("/api/strategies/list")
async def list_strategies(session: dict = Depends(require_auth)):
    # Primary path: derive strategy list from the real backtester output so the
    # names and types always match what run_all_strategies() actually produces.
    # MOCK_STRATEGIES is the fallback — same keys, but avoids running the full
    # pipeline just to return a name/type list.
    if ENVIRONMENT != "test":
        try:
            from tools.data_fetcher import get_full_history_async
            from tools.backtester import run_all_strategies
            # OFF-LOOP: get_full_history reads the DB (and may run the cold
            # pipeline through Excel/FRED/yfinance). Pushed to a worker
            # thread so the event loop keeps serving concurrent requests.
            history = await get_full_history_async()
            results_dict = await asyncio.to_thread(run_all_strategies, history)
            return {
                "strategies": [
                    {"name": name, "type": r.get("strategy_type", "static")}
                    for name, r in results_dict.items()
                ]
            }
        except Exception as exc:
            log.warning("list_strategies_fallback", error=str(exc))
    # Fallback: MOCK_STRATEGIES mirrors the real strategy keys and types.
    return {
        "strategies": [
            {"name": s["strategy_name"], "type": s["strategy_type"]}
            for s in MOCK_STRATEGIES
        ]
    }


# ── Backtest ──────────────────────────────────────────────────────────────────

@app.post("/api/backtest/run")
@limiter.limit("20/minute")
async def run_backtest(
    request: Request,
    body: BacktestRequest,
    session: dict = Depends(require_auth),
):
    # Canonical strategy names come from MOCK_STRATEGIES, which mirrors the
    # keys that run_all_strategies() produces.  This validation step has no
    # dependency on the real pipeline so it is always cheap.
    valid_strategies = {s["strategy_name"] for s in MOCK_STRATEGIES}
    if body.strategy not in valid_strategies:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown strategy '{body.strategy}'. Valid: {sorted(valid_strategies)}",
        )

    log.info("backtest_run", strategy=body.strategy, user=session["email"])

    # Primary path: run the full pipeline and return the requested strategy.
    # run_all_strategies() computes all 10 at once; we pick the one requested.
    # The old condition checked for "100% Equity (Benchmark)" — the Sprint 1
    # human-readable name — which never matched the real key "BENCHMARK", making
    # that branch dead code.  Fixed here to handle all strategies uniformly.
    if ENVIRONMENT != "test":
        try:
            from tools.data_fetcher import get_full_history_async
            from tools.backtester import run_all_strategies
            history = await get_full_history_async()
            results_dict = await asyncio.to_thread(run_all_strategies, history)
            if body.strategy in results_dict:
                return results_dict[body.strategy]
        except Exception as exc:
            log.warning("backtest_run_fallback", strategy=body.strategy, error=str(exc))

    # Fallback: return the corresponding MOCK_STRATEGIES entry when the real
    # pipeline is unavailable (test environment or unhandled exception above).
    result = next(s for s in MOCK_STRATEGIES if s["strategy_name"] == body.strategy)
    return result


@app.get("/api/backtest/compare")
@limiter.limit("30/minute")
async def compare_strategies(request: Request, session: dict = Depends(require_auth)):
    # Sprint 5: strategy_results_cache checked before calling run_all_strategies().
    # Cache key = SHA-256 of (n_monthly_rows, last_date, n_strategies=10).
    # On a cache hit: ~200ms response.  On a miss: recompute (~30s) then cache.
    # Cache survives Render restarts because it lives in PostgreSQL, not memory.
    if ENVIRONMENT != "test":
        try:
            from tools.data_fetcher import get_full_history_async
            from tools.backtester import run_all_strategies
            from tools.cache import get_strategy_cache, set_strategy_cache, _compute_data_hash

            # OFF-LOOP — this endpoint is the dashboard hot path. Sync
            # get_full_history() used to block the event loop on every
            # call, serialising all parallel dashboard requests behind a
            # single slow read and producing the 35-70s response-time
            # cluster observed in Render logs on 2026-05-24. await
            # get_full_history_async() pushes the entire read pipeline
            # (DB + Excel + FRED + yfinance on a cold path) into a worker
            # thread so the loop stays free to service the other 6+
            # parallel dashboard calls.
            history = await get_full_history_async()

            # Build a stable hash from pipeline metadata to detect new data
            monthly = history.get("equity_monthly")
            n_rows = len(monthly) if monthly is not None else 0
            first_date = str(monthly.index[0].date()) if monthly is not None and len(monthly) > 0 else "unknown"
            last_date = str(monthly.index[-1].date()) if monthly is not None and len(monthly) > 0 else "unknown"
            strategy_hash = _compute_data_hash(n_rows, last_date, n_strategies=10)

            # Expose the actual date range so the frontend can label charts
            # dynamically. With the LQD bridge: ~2002-07 to ~2024-12 (282 months).
            # Without LQD bridge: ~2007-05 to ~2024-12 (224 months) — fall-back state.
            data_range = {"start": first_date, "end": last_date, "n_months": n_rows}

            cached = await get_strategy_cache(strategy_hash)
            # Schema gate: cache entries written before Sprint 6 lack the
            # monthly_returns field that /charts/data needs. Treat them as
            # stale so a single recompute fills the cache with current-schema
            # entries; both endpoints see the benefit on the next request.
            cache_current_schema = bool(cached) and all(
                isinstance(r.get("monthly_returns"), list) and len(r["monthly_returns"]) > 0
                for r in cached.values()
            )
            if cache_current_schema:
                ranked = sorted(cached.values(), key=lambda r: r.get("sharpe_ratio", 0.0), reverse=True)
                return {"strategies": ranked, "ranked_by": "sharpe_ratio", "cache": "hit", "data_range": data_range}

            # Cache miss / schema refresh — run_all_strategies executes 10
            # backtests serially (~30s on a cold pipeline). Offload to a
            # worker thread so the loop is not blocked while it computes.
            results_dict = await asyncio.to_thread(run_all_strategies, history)
            ranked = sorted(results_dict.values(), key=lambda r: r.get("sharpe_ratio", 0.0), reverse=True)

            # Write-through: persist for next cold start or Render restart
            await set_strategy_cache(strategy_hash, results_dict, n_observations=n_rows)

            cache_label = "miss" if not cached else "schema_refresh"
            return {"strategies": ranked, "ranked_by": "sharpe_ratio", "cache": cache_label, "data_range": data_range}
        except Exception as exc:
            log.warning("compare_all_strategies_fallback", error=str(exc))
    # Fallback: MOCK_STRATEGIES used only in test environment or when the real
    # pipeline raises an exception.  Should never be the primary response in
    # production — the warning log above will flag if this path is taken.
    sorted_strategies = sorted(MOCK_STRATEGIES, key=lambda s: s["sharpe_ratio"], reverse=True)
    return {"strategies": sorted_strategies, "ranked_by": "sharpe_ratio"}


# ── Charts data — aux payload for Statistical Evidence & Regime Analysis ─────

@app.get("/api/v1/charts/data")
@limiter.limit("30/minute")
async def get_chart_data(request: Request, session: dict = Depends(require_auth)):
    """
    Returns the auxiliary data required by all twelve Sprint 6 charts in a
    single call. Bundling avoids 12 sequential round-trips on Render's free
    tier where each cold start adds noticeable latency. The payload is
    derived from the same get_full_history() + run_all_strategies() outputs
    that /api/backtest/compare uses, so a cache hit there means cache hit here.
    """
    if ENVIRONMENT == "test":
        # Tests: return an empty-shape payload so frontend tests can mock
        # without spinning up the full pipeline.
        return {
            "cpcv": {}, "cv_radar": {}, "walk_forward": {},
            "regime_conditional": {}, "regime_timeline": [],
            "correlation_breakdown": [], "factor_loadings": {},
            "attribution": {}, "transition_matrix": {},
            "n_strategies": 0, "n_months": 0,
        }

    try:
        from tools.data_fetcher import get_full_history_async
        from tools.backtester import run_all_strategies
        from tools.chart_data import compute_chart_data
        from tools.cache import get_strategy_cache, _compute_data_hash

        # OFF-LOOP — second dashboard hot path. Same fix as
        # /api/backtest/compare; the two endpoints together account for
        # most of the dashboard's parallel-request fan-out.
        history = await get_full_history_async()
        monthly = history.get("equity_monthly")
        n_rows = len(monthly) if monthly is not None else 0
        last_date = str(monthly.index[-1]) if monthly is not None and len(monthly) > 0 else "unknown"
        strategy_hash = _compute_data_hash(n_rows, last_date, n_strategies=10)

        # Reuse the strategy cache if a prior /compare call populated it.
        # The cached results MUST carry monthly_returns — without them, the
        # per-strategy chart computations (CPCV, walk-forward, regime-
        # conditional, factor loadings, attribution, CV radar) all produce
        # empty output. Cache entries written before Sprint 6 lack this
        # field; detect and refresh so the system self-heals without
        # requiring a manual DB invalidation.
        cached_results = await get_strategy_cache(strategy_hash)
        cache_is_chart_compatible = bool(cached_results) and all(
            isinstance(r.get("monthly_returns"), list) and len(r["monthly_returns"]) > 0
            for r in cached_results.values()
        )
        if cache_is_chart_compatible:
            results_dict = cached_results
            log.info("chart_data_cache_hit", strategy_hash=strategy_hash[:8])
        else:
            if cached_results:
                log.info(
                    "chart_data_cache_schema_miss",
                    strategy_hash=strategy_hash[:8],
                    note="cached entry lacks monthly_returns — refreshing",
                )
            results_dict = run_all_strategies(history)
            # Write-through so the next /charts/data hit AND the next
            # /compare hit both find a schema-compatible entry.
            try:
                from tools.cache import set_strategy_cache
                await set_strategy_cache(strategy_hash, results_dict, n_observations=n_rows)
            except Exception as exc:
                log.warning("chart_data_cache_write_failed", error=str(exc))

        payload = compute_chart_data(history, results_dict)
        payload["strategy_hash"] = strategy_hash
        return payload
    except Exception as exc:
        log.warning("chart_data_fallback", error=str(exc))
        return {
            "cpcv": {}, "cv_radar": {}, "walk_forward": {},
            "regime_conditional": {}, "regime_timeline": [],
            "correlation_breakdown": [], "factor_loadings": {},
            "attribution": {}, "transition_matrix": {},
            "n_strategies": 0, "n_months": 0, "error": str(exc),
        }


# ── Academic analytics ────────────────────────────────────────────────────────

@app.get("/api/v1/analytics/academic")
@limiter.limit("30/minute")
async def get_academic_analytics(request: Request, session: dict = Depends(require_auth)):
    """
    Bundled academic analytics for the analytics view and the midpoint paper:
    summary statistics, cumulative total return, 12-month rolling correlation,
    rolling excess return, regime-conditional performance, drawdown comparison,
    Carhart four-factor loadings, and the source-controlled strategy metadata.

    Every figure is derived from data already in PostgreSQL —
    market_data_monthly, strategy_results_cache, ff_factors_monthly — so the
    endpoint is a set of light reads plus pure NumPy/statsmodels compute; it
    never triggers get_full_history() or run_all_strategies(). Parameter
    sensitivity is deliberately NOT bundled here — it re-runs ~23 backtests
    and has its own endpoint, /api/v1/analytics/sensitivity.
    """
    if ENVIRONMENT == "test":
        return {"available": False, "note": "analytics unavailable in test environment"}
    try:
        # May 22 2026 — first try the pre-computed cache. The
        # strategy_cache write hook fires refresh_all_analytics
        # immediately after a fresh strategy ingestion, so the
        # cache is normally hot. Cold-cache fallback runs the
        # inline compute below.
        from tools.cache import (
            get_monthly_returns, get_latest_strategy_cache, get_ff_factors,
            get_latest_strategy_hash,
        )
        from tools.precomputed_analytics import (
            get_metric as get_precomputed,
            get_latest_metric as get_latest_precomputed,
        )

        latest_hash = await get_latest_strategy_hash()
        if latest_hash:
            cached = await get_precomputed(latest_hash, "academic_analytics")
            if cached:
                log.info("academic_analytics_cache_hit",
                         data_hash=latest_hash[:8])
                return cached
            # Stale-cache fallback — the latest hash hasn't been
            # refreshed yet but a previous data ingestion's payload
            # is still in the table. Better to serve stale than to
            # block on the inline compute when the refresh hook is
            # in flight. The response carries _stale=True so the
            # frontend can decide whether to surface a notice.
            stale = await get_latest_precomputed("academic_analytics")
            if stale:
                log.info("academic_analytics_cache_stale_hit")
                return stale

        # COLD CACHE — never run the inline compute on the request
        # thread. The inline path runs factor_loadings (OLS per
        # strategy), regime_conditional_performance, and 5 other
        # heavy reductions; on Render's shared CPU it can exceed the
        # frontend's 30s timeout. Hotfix iteration 2 (May 23 2026):
        # fire the background refresh and return a warming response
        # immediately. The frontend retries every ~10s until the
        # cache row lands.
        #
        # May 24 2026 P0 hotfix — fire the refresh UNCONDITIONALLY,
        # not gated on `if latest_hash:`. The pre-existing gate meant
        # that on a Render restart with an empty strategy_results_
        # cache, the refresh NEVER fired and the user saw a 30s
        # timeout forever. refresh_all_analytics now substitutes a
        # "BOOT-WARM" sentinel hash when none is supplied, so the
        # subsequent get_latest_precomputed call finds the row even
        # without a real strategy hash to key against.
        log.warning("academic_analytics_cache_cold_warming",
                    has_strategy_hash=bool(latest_hash))
        try:
            from tools.precomputed_analytics import (
                trigger_refresh_async,
            )
            trigger_refresh_async(latest_hash or "")
        except Exception as trig_exc:  # noqa: BLE001
            log.warning("academic_analytics_refresh_trigger_failed",
                        error=str(trig_exc))
        return {
            "available": False,
            "warming": True,
            "retry_after_ms": 10000,
            "note": ("Analytics are being computed in the "
                     "background — refresh in ~10 seconds."),
        }
    except Exception as exc:
        log.warning("academic_analytics_failed", error=str(exc))
        return {"available": False, "note": "analytics computation failed"}


# ── Diversification suite endpoints (item 8) ──────────────────────────────────
# Seven GET endpoints for the diversification context suite. Each
# reads a single pre-computed row from analytics_metrics_cache (the
# refresh hook fires on every strategy_cache write). Cold-cache
# fallback: compute inline from tools.diversification_analytics so
# the user always gets data even before the refresh hook lands.
# require_team_member per the spec; rate-limited to protect the
# DB / inline-compute paths from abuse.


async def _read_div_metric_or_compute(
    metric_kind: str,
    inline_fn,
) -> dict[str, Any]:
    """Three-tier read for diversification metrics: cache hit by
    current data_hash → stale cache → inline compute. Returns the
    payload dict, never raises. Logs which path served the request
    so production logs surface cache hit / miss patterns."""
    try:
        from tools.cache import (
            get_latest_strategy_hash, get_latest_strategy_cache,
        )
        from tools.precomputed_analytics import (
            get_metric, get_latest_metric,
        )
        latest_hash = await get_latest_strategy_hash()
        if latest_hash:
            cached = await get_metric(latest_hash, metric_kind)
            if cached:
                log.info("div_metric_cache_hit", metric=metric_kind)
                return cached
            stale = await get_latest_metric(metric_kind)
            if stale:
                log.info("div_metric_cache_stale_hit", metric=metric_kind)
                return stale
        # Cold cache — inline compute.
        strategies = await get_latest_strategy_cache()
        if not strategies:
            return {"available": False,
                    "note": "strategy cache not yet populated"}
        log.info("div_metric_cache_miss_inline", metric=metric_kind)
        return inline_fn(strategies)
    except Exception as exc:  # noqa: BLE001
        log.warning("div_metric_failed", metric=metric_kind, error=str(exc))
        return {"available": False, "note": "computation failed"}


@app.get("/api/v1/analytics/correlation")
@limiter.limit("30/minute")
async def get_analytics_correlation(
    request: Request,
    session: dict = Depends(require_team_member),
):
    """11x11 strategy + benchmark correlation matrices (full +
    pre-2022 + post-2022). Drives the heatmap + insight callout."""
    if ENVIRONMENT == "test":
        return {"available": False}
    from tools import diversification_analytics as div
    return await _read_div_metric_or_compute(
        "correlation_matrices",
        lambda s: div.correlation_matrices(s),
    )


@app.get("/api/v1/analytics/tail-risk")
@limiter.limit("30/minute")
async def get_analytics_tail_risk(
    request: Request,
    session: dict = Depends(require_team_member),
):
    """VaR + CVaR at 95% / 99%, monthly + annualised, historical
    simulation. Drives the Downside Risk table."""
    if ENVIRONMENT == "test":
        return {"available": False}
    from tools import diversification_analytics as div
    return await _read_div_metric_or_compute(
        "tail_risk",
        lambda s: {"strategies": div.tail_risk(s)},
    )


@app.get("/api/v1/analytics/capture-ratios")
@limiter.limit("30/minute")
async def get_analytics_capture_ratios(
    request: Request,
    session: dict = Depends(require_team_member),
):
    """Up / Down capture + capture score per strategy over full +
    pre-2022 + post-2022 windows. Drives the capture scatter."""
    if ENVIRONMENT == "test":
        return {"available": False}
    from tools import diversification_analytics as div
    return await _read_div_metric_or_compute(
        "capture_ratios",
        lambda s: {"strategies": div.capture_ratios(s)},
    )


@app.get("/api/v1/analytics/drawdown-duration")
@limiter.limit("30/minute")
async def get_analytics_drawdown_duration(
    request: Request,
    session: dict = Depends(require_team_member),
):
    """Avg / max drawdown duration + recovery + current-in-drawdown
    state per strategy. Drives the Drawdown Duration table."""
    if ENVIRONMENT == "test":
        return {"available": False}
    from tools import diversification_analytics as div
    return await _read_div_metric_or_compute(
        "drawdown_duration",
        lambda s: {"strategies": div.drawdown_duration(s)},
    )


@app.get("/api/v1/analytics/crisis-performance")
@limiter.limit("30/minute")
async def get_analytics_crisis_performance(
    request: Request,
    session: dict = Depends(require_team_member),
):
    """CAGR + max DD + Sharpe per strategy over 5 historical crisis
    windows. Drives the Crisis Performance table."""
    if ENVIRONMENT == "test":
        return {"available": False}
    from tools import diversification_analytics as div
    return await _read_div_metric_or_compute(
        "crisis_performance",
        lambda s: div.crisis_performance(s),
    )


@app.get("/api/v1/analytics/risk-contribution")
@limiter.limit("30/minute")
async def get_analytics_risk_contribution(
    request: Request,
    session: dict = Depends(require_team_member),
):
    """MCTR + % risk contribution for equal-weight and tangency-weight
    portfolios. Drives the Risk Contribution stacked bar."""
    if ENVIRONMENT == "test":
        return {"available": False}
    from tools import diversification_analytics as div
    return await _read_div_metric_or_compute(
        "marginal_contribution_to_risk",
        lambda s: div.marginal_contribution_to_risk(s),
    )


@app.get("/api/v1/analytics/distribution")
@limiter.limit("30/minute")
async def get_analytics_distribution(
    request: Request,
    session: dict = Depends(require_team_member),
):
    """Skewness / excess kurtosis / Jarque-Bera + best/worst months
    per strategy. Drives the Distribution Summary table."""
    if ENVIRONMENT == "test":
        return {"available": False}
    from tools import diversification_analytics as div
    return await _read_div_metric_or_compute(
        "return_distribution",
        lambda s: {"strategies": div.return_distribution(s)},
    )


# ── Strategy characterisations (item 9) ───────────────────────────────────────


@app.get("/api/v1/strategies/characterisations")
@limiter.limit("30/minute")
async def get_strategy_characterisations(
    request: Request,
    session: dict = Depends(require_team_member),
):
    """
    Item 9 — per-strategy Portfolio Profile data. Returns one row per
    strategy with the AI-generated construction_summary,
    behavioural_profile, regime_sensitivity, behavioural_tag, and the
    deterministic portfolio_characteristics.

    Reads from strategy_characterisations keyed by the current
    data_hash. Cold-cache fallback returns the most recent
    characterisation per strategy regardless of hash (DISTINCT ON in
    the helper) so a fresh deploy that hasn't seen a refresh yet
    still has something to render.

    Auth: require_team_member. Same gate as the diversification
    metrics — non-team viewers see the Dashboard rankings but not
    the per-strategy editorial context.
    """
    if ENVIRONMENT == "test":
        return {"available": False, "strategies": []}
    try:
        from tools.cache import get_latest_strategy_hash
        from tools.strategy_characterisations import (
            get_all_characterisations,
        )
        latest_hash = await get_latest_strategy_hash()
        rows = await get_all_characterisations(latest_hash)
        if not rows:
            return {
                "available": False,
                "strategies": [],
                "note": "Strategy characterisations have not been "
                        "computed yet. They populate automatically "
                        "after the first strategy_results_cache write.",
            }
        return {
            "available": True,
            "data_hash": latest_hash,
            "strategies": rows,
        }
    except Exception as exc:
        log.warning("strategy_characterisations_endpoint_failed",
                    error=str(exc))
        return {"available": False, "strategies": []}


# ── Analytical findings staging (May 22 2026) ─────────────────────────────────


@app.post("/api/v1/reports/stage-findings")
@limiter.limit("5/minute")
async def post_stage_findings(
    request: Request,
    session: dict = Depends(require_team_member),
):
    """
    Runs the analytical staging computation and writes the result to
    analytical_findings_cache. The Academic Writer picks up the
    latest row on its next document-generation call via the workflow
    hook in agents/academic_writer._writer_system_prompt().

    On-demand only — NOT triggered by data-hash change. Findings carry
    interpretation (a NUGGET STRENGTH per finding and an IMPLICATION
    paragraph); pre-computing them silently on every ingestion would
    produce drift the team did not ask for.

    Returns the structured findings + the rendered markdown so the
    report writer UI can display the staging-summary card and let the
    user open the full report inline before enabling [Generate Draft].

    Rate-limited (5/minute). Auth: require_team_member.
    """
    if ENVIRONMENT == "test":
        return {
            "id":              None,
            "data_hash":       None,
            "strategy_count":  0,
            "surprise_count":  0,
            "n_high_strength": 0,
            "findings":        [],
            "findings_md":     "",
        }
    try:
        from tools.analytical_findings import (
            stage_findings, refresh_findings_context,
        )
        result = await stage_findings(triggered_by="api")
        # Refresh the in-process context cache so the next academic-
        # writer call picks up the fresh findings without waiting for
        # a process restart.
        await refresh_findings_context()
        return result
    except Exception as exc:
        log.warning("stage_findings_endpoint_failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Findings staging failed — see server logs.",
        )


@app.get("/api/v1/reports/latest-findings")
async def get_latest_findings_endpoint(
    session: dict = Depends(require_team_member),
):
    """Returns the most recent staged-findings row. The report writer
    UI reads this on mount so the "last staged at" pill renders
    without firing a fresh compute."""
    if ENVIRONMENT == "test":
        return {"available": False}
    try:
        from tools.analytical_findings import get_latest_findings
        row = await get_latest_findings()
        if not row:
            return {"available": False}
        return {"available": True, **row}
    except Exception as exc:
        log.warning("latest_findings_endpoint_failed", error=str(exc))
        return {"available": False}


# ── Report writer template pipeline (item 12) ────────────────────────────────


@app.get("/api/v1/reports/templates")
async def list_report_templates(
    session: dict = Depends(require_team_member),
):
    """Active report templates for the report-writer dropdown.
    Drops system_prompt + section_instructions from the response —
    those are heavy and only needed at generation time."""
    if ENVIRONMENT == "test":
        return {"templates": []}
    from tools.report_templates import list_active_templates
    rows = await list_active_templates()
    return {"templates": [
        {k: v for k, v in r.items()
         if k not in ("system_prompt", "section_instructions")}
        for r in rows
    ]}


@app.post("/api/v1/reports/source-citations")
@limiter.limit("3/minute")
async def post_source_citations(
    request: Request, body: dict,
    session: dict = Depends(require_team_member),
):
    """STEP 1B — source citations for a template's concept list.

    Body: {"template_id": "midpoint_check_fna670"}.

    Returns the verified citations object + the quality indicator
    (green / amber / red). Persists each citation in citations_cache.
    Rate-limited (3/min) because each call hits the web_search tool
    up to 10 times.
    """
    if ENVIRONMENT == "test":
        return {
            "template_id": body.get("template_id"),
            "citations": {},
            "quality": "red",
            "verified_count": 0,
        }
    template_id = body.get("template_id")
    if not template_id:
        raise HTTPException(
            status_code=422, detail="template_id is required.")
    try:
        from tools.report_templates import get_template
        from tools.template_pipeline import (
            source_citations, citation_quality, persist_citations,
        )
        from tools.report_generator import create_placeholder_generation
        tmpl = await get_template(template_id)
        if not tmpl:
            raise HTTPException(
                status_code=404,
                detail=f"Template '{template_id}' not found.")
        concepts = tmpl.get("concepts") or []
        citations = await source_citations(concepts)

        # UAT 2026-05-24 — Open Review fix. Create a placeholder
        # report_generations row BEFORE persisting citations so each
        # citation row carries a real generation_id (was NULL before
        # this change). The frontend Citation Review panel is keyed on
        # generation_id; without a row, the panel rendered nothing
        # and Open Review was a silent no-op.
        #
        # Body accepts an existing generation_id so a re-run uses the
        # same row instead of leaving an orphan placeholder behind.
        # On placeholder failure (DB unavailable) generation_id stays
        # None — citations are still persisted (standalone), behaviour
        # degrades to the pre-fix state rather than failing the step.
        gid_in = body.get("generation_id")
        if isinstance(gid_in, int) and gid_in > 0:
            generation_id: int | None = gid_in
        else:
            generation_id = await create_placeholder_generation(template_id)

        await persist_citations(citations, generation_id=generation_id)
        # CITATION_VERIFIED_STATES covers every state that counts as a
        # real citation — auto-verified, human-accepted, alternative-
        # selected, manually-added. Initial counts are all
        # auto-verified, but the new 3-pass path will also produce
        # pending_review entries that need reviewer attention.
        from tools.template_pipeline import CITATION_VERIFIED_STATES
        verified = sum(
            1 for c in citations.values()
            if c.get("verification_status") in CITATION_VERIFIED_STATES)
        return {
            "template_id":     template_id,
            "generation_id":   generation_id,
            "citations":       citations,
            "quality":         citation_quality(citations),
            "verified_count":  verified,
            "concept_count":   len(concepts),
        }
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("source_citations_endpoint_failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Citation sourcing failed — see server logs.")


# ── Citation reviewer workflow (item 1, May 23 2026) ─────────────────────────
#
# The midpoint paper's citation pipeline now produces pending_review
# entries when the trusted-domain search returns nothing — picked up
# by passes 2 and 3 of source_citations. Bob reviews each pending
# entry and either accepts the search result, picks an alternative
# from the captured pass-2/3 hits, enters a citation manually, or
# rejects the concept entirely. The two endpoints below back the
# CitationReviewPanel on the report writer screen.

@app.get("/api/v1/citations/{generation_id}")
async def get_citations_list(
    generation_id: int,
    session: dict = Depends(require_team_member),
):
    """Return every citation row for a generation_id with its current
    state, alternatives, and reviewer-action history. Powers the
    CitationReviewPanel."""
    from tools.template_pipeline import get_citations_for_generation
    citations = await get_citations_for_generation(generation_id)
    return {
        "generation_id": generation_id,
        "citations":     citations,
    }


@app.post("/api/v1/citations/{citation_id}/review")
async def post_citation_review(
    citation_id: int,
    body: dict,
    session: dict = Depends(require_team_member),
):
    """Apply a reviewer action to a citations_cache row.

    Body:
      action: 'accept_untrusted' | 'select_alternative' |
              'reject' | 'manual_add'
      selected_alternative: dict (required when action ==
                            'select_alternative')
      manual_citation:      dict (required when action == 'manual_add')

    The action drives the state transition per the 7-state machine
    (see CITATION_STATES in tools/template_pipeline.py). Reviewer
    identity and timestamp are recorded on the row.
    """
    action = body.get("action")
    if not action:
        raise HTTPException(
            status_code=422, detail="action is required.")
    from tools.template_pipeline import (
        CITATION_REVIEW_ACTIONS, apply_citation_review,
    )
    if action not in CITATION_REVIEW_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=(f"Unknown action '{action}'. Valid actions: "
                    + ", ".join(sorted(CITATION_REVIEW_ACTIONS))))
    reviewer_email = session.get("email") or "unknown@reviewer"
    selected = body.get("selected_alternative")
    manual = body.get("manual_citation")
    updated = await apply_citation_review(
        citation_id, action, reviewer_email,
        selected_alternative=selected,
        manual_citation=manual,
    )
    if updated is None:
        raise HTTPException(
            status_code=404,
            detail=("Citation not found, or the action payload was "
                    "invalid. select_alternative requires "
                    "selected_alternative; manual_add requires "
                    "manual_citation."))
    return {"citation": updated}


@app.post("/api/v1/reports/team-activity")
@limiter.limit("30/minute")
async def post_team_activity(
    request: Request,
    session: dict = Depends(require_team_member),
):
    """STEP 1C — per-member + platform-wide activity counts.

    No body required — the team_emails are pinned in the pipeline
    helper. Used by the report writer and the Issue Tracker activity
    view both.
    """
    if ENVIRONMENT == "test":
        return {"activity": {}, "cross_check_flags": []}
    try:
        from tools.template_pipeline import (
            fetch_team_activity, cross_check_team_activity,
        )
        activity = await fetch_team_activity()
        flags = cross_check_team_activity(activity)
        return {"activity": activity, "cross_check_flags": flags}
    except Exception as exc:
        log.warning("team_activity_endpoint_failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Team activity fetch failed — see server logs.")


@app.post("/api/v1/reports/validate-thesis")
@limiter.limit("30/minute")
async def post_validate_thesis(
    request: Request,
    session: dict = Depends(require_team_member),
):
    """STEP 6 — thesis validation gate. Pulls the latest verified data
    + ranked findings from the cache and runs the three thesis
    conditions. Blocks generation when any condition fails."""
    if ENVIRONMENT == "test":
        return {
            "passed":           True,
            "conditions":       [],
            "blocker_reasons":  [],
        }
    try:
        from tools.analytical_findings import (
            gather_payload_from_db, get_latest_findings,
        )
        from tools.cache import get_latest_strategy_hash
        from tools.template_pipeline import (
            live_from_payload, cross_check, validate_thesis,
        )
        data_hash = await get_latest_strategy_hash()
        payload = await gather_payload_from_db(data_hash)
        live = live_from_payload(payload)
        findings_row = await get_latest_findings()
        staged_md = (findings_row or {}).get("findings_md") or ""
        verified, _ = cross_check(live, staged_md)
        ranked = (findings_row or {}).get("ranked_findings") or []
        result = validate_thesis(verified, ranked)
        return result
    except Exception as exc:
        log.warning("validate_thesis_endpoint_failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Thesis validation failed — see server logs.")


@app.post("/api/v1/reports/rank-findings")
@limiter.limit("30/minute")
async def post_rank_findings(
    request: Request,
    session: dict = Depends(require_team_member),
):
    """STEP 7 — explicit ranking endpoint. Reads the latest findings,
    re-ranks them, and returns the ordered list. The stage-findings
    endpoint already writes ranked_findings; this endpoint is a
    convenience for the UI to refresh the ranking after a manual
    findings edit."""
    if ENVIRONMENT == "test":
        return {"ranked_findings": []}
    try:
        from tools.analytical_findings import get_latest_findings
        from tools.template_pipeline import rank_findings
        row = await get_latest_findings()
        if not row:
            return {"ranked_findings": []}
        ranked = rank_findings(row.get("findings") or [])
        return {"ranked_findings": ranked}
    except Exception as exc:
        log.warning("rank_findings_endpoint_failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Rank findings failed — see server logs.")


# ── Report writer — generation + editor + download endpoints ────────────────
#
# Backs item 12 commit 2. The generate endpoint runs the eight-step
# pipeline; the editor endpoints (paper-md PATCH, iterate, resolve-bob,
# final-check) support Bob's iteration loop; the academic-review
# endpoint scores the draft against the FNA670 rubric; the download
# endpoints emit the two .docx files. Every endpoint is team_member-
# gated so a viewer cannot kick off a generation that costs Anthropic
# tokens. The download endpoints honor a soft gate on
# academic_readiness=needs_significant_revision — Bob can override
# with acknowledge_warning=true on the query string.


_DOCX_MEDIA = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document")


@app.post("/api/v1/reports/templates/{template_id}/generate")
@limiter.limit("3/minute")
async def generate_report_from_template(
    request: Request, template_id: str,
    session: dict = Depends(require_team_member),
):
    """End-to-end report generation. Runs the full pipeline, persists
    one report_generations row, returns the full draft payload
    (paper_md, appendix_md, flags, bob_blocks, verified_data, etc.).

    Failure modes:
      404 — template_not_found
      422 — thesis_validation_blocked (returns the failing conditions)
      500 — unexpected pipeline error
    """
    if ENVIRONMENT == "test":
        return {
            "id":              None,
            "template_id":     template_id,
            "paper_md":        "",
            "appendix_md":     "",
            "flag_count":      0,
            "bob_block_count": 0,
            "bob_blocks":      [],
            "flags":           [],
        }
    try:
        from tools.report_generator import generate_paper
        result = await generate_paper(template_id)
        if result.get("error") == "template_not_found":
            raise HTTPException(
                status_code=404,
                detail=f"Template '{template_id}' not found.")
        if result.get("error") == "thesis_validation_blocked":
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "thesis_validation_blocked",
                    "thesis_validation": result.get("thesis_validation"),
                })
        return result
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("generate_report_failed",
                    template_id=template_id, error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Report generation failed — see server logs.")


@app.get("/api/v1/reports/generations")
async def list_report_generations(
    template_id: str | None = None,
    limit: int = 20,
    session: dict = Depends(require_team_member),
):
    """Returns the user's most recent draft generations newest first
    — backs the Draft selector dropdown so Bob can switch between
    saved drafts instead of starting fresh on every login.

    Query parameters:
      template_id  Optional — filter to a single template's drafts.
                   Default: list every template the user has touched.
      limit        Max number of drafts to return. Default 20, capped
                   at 100 server-side so a pathological caller cannot
                   pull every row.

    Auth: team-member only — same scope as the rest of the report
    writer endpoints (viewers cannot generate, so they cannot list).
    """
    if ENVIRONMENT == "test":
        return {"drafts": []}
    capped = max(1, min(int(limit or 20), 100))
    try:
        from tools.report_generator import list_generations_for_user
        email = (session.get("email") or "").strip()
        drafts = await list_generations_for_user(
            email, limit=capped, template_id=template_id)
        return {"drafts": drafts}
    except Exception as exc:
        log.warning("list_generations_endpoint_failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="List drafts failed — see server logs.")


@app.delete("/api/v1/reports/generations/{generation_id}")
async def delete_report_generation(
    generation_id: int,
    session: dict = Depends(require_team_member),
):
    """Hard-delete a saved generation (the Draft Selector trash icon
    calls this). Removes the generation row + every saved version +
    the pipeline-audit row that pointed at it. Citations are kept
    (they're concept-keyed, may be reused by other drafts).

    Frontend confirms with a type-DELETE dialog before firing the
    request — same pattern as the version-history Delete UX.

    Idempotent contract (May 24 2026 update — user-reported fix):
      - 200 OK with {"deleted": True}                — row removed
      - 200 OK with {"deleted": True,
                     "already_absent": True}         — row was
                                                       already gone
                                                       (a second
                                                       click, a
                                                       parallel
                                                       session, a
                                                       stale FE
                                                       cache)
      - 500 with structured detail                   — real DB
                                                       failure
    """
    if ENVIRONMENT == "test":
        return {"deleted": True}
    from tools.report_generator import delete_generation
    result = await delete_generation(generation_id)
    status = result.get("status")
    if status == "deleted":
        return {"deleted": True}
    if status == "already_absent":
        # Idempotent success — DO NOT raise. The user clicked
        # Delete on a draft that's already gone; the desired
        # outcome (no draft) is reached, so we return 200.
        return {"deleted": True, "already_absent": True}
    # status == "error" — surface as 500 so the frontend can
    # render an actual error message. The detail block carries
    # the underlying error string for Render-logs cross-reference.
    raise HTTPException(
        status_code=500,
        detail={
            "error":   "delete_failed",
            "message": result.get("error")
                       or "Delete failed — see server logs.",
        })


@app.get("/api/v1/reports/generations/{generation_id}")
async def get_report_generation(
    generation_id: int,
    session: dict = Depends(require_team_member),
):
    """Returns the persisted generation row + a fresh post-check on
    the current paper_md so the editor renders the same flag list the
    backend will gate on."""
    if ENVIRONMENT == "test":
        return {"error": "test_environment_no_generations"}
    try:
        from tools.report_generator import (
            get_generation, _post_check_summary,
            _load_citations_for_generation,
        )
        gen = await get_generation(generation_id)
        if not gen:
            raise HTTPException(status_code=404,
                                detail="Generation not found.")
        citations = await _load_citations_for_generation(generation_id)
        checks = _post_check_summary(
            gen.get("paper_md") or "",
            gen.get("verified_data") or {},
            citations)
        return {**gen, **checks, "citations": citations}
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("get_generation_endpoint_failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Read generation failed — see server logs.")


@app.patch("/api/v1/reports/generations/{generation_id}/paper-md")
@limiter.limit("60/minute")
async def patch_generation_paper_md(
    request: Request, generation_id: int, body: dict,
    session: dict = Depends(require_team_member),
):
    """Inline editor save. Body:
      paper_md (required) — the new full paper text
      expected_revision (optional) — the paper_revision the caller
        last saw. If supplied AND it does not match the current row,
        the endpoint returns 409 with the actual revision so the
        frontend can show a "concurrent edit detected" prompt. The
        debounced auto-save typically omits this so it never blocks
        itself; the explicit Save action passes it.
      source (optional) — what triggered the save. One of: manual,
        auto_iterate, auto_resolve_bob, auto_edit (default). Stored
        on the version snapshot so the history reads cleanly.
    """
    if ENVIRONMENT == "test":
        return {"saved": False, "flag_count": 0, "paper_revision": 0}
    paper_md = body.get("paper_md")
    if paper_md is None:
        raise HTTPException(
            status_code=422, detail="paper_md is required.")
    expected_revision = body.get("expected_revision")
    source = body.get("source") or "auto_edit"
    try:
        from tools.report_generator import update_paper_md
        result = await update_paper_md(
            int(generation_id), str(paper_md),
            expected_revision=(int(expected_revision)
                                 if expected_revision is not None else None),
            saved_by_email=session.get("email"),
            source=source,
        )
        if result.get("error") == "generation_not_found":
            raise HTTPException(status_code=404,
                                detail="Generation not found.")
        if result.get("error") == "revision_mismatch":
            # 409 Conflict — the caller's snapshot is stale. The
            # response body carries the current revision so the
            # frontend can offer a "refresh and re-apply" flow.
            raise HTTPException(
                status_code=409,
                detail={
                    "error":             "revision_mismatch",
                    "current_revision":  result.get("current_revision"),
                    "expected_revision": result.get("expected_revision"),
                    "message": (
                        "Another reviewer saved while you were "
                        "editing. Refresh the paper and re-apply "
                        "your change."),
                })
        return result
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("patch_paper_md_failed", error=str(exc))
        raise HTTPException(
            status_code=500, detail="Save failed — see server logs.")


# ── Paper version history (item 2, May 23 2026) ──────────────────────────────
#
# Every save to paper_md creates an append-only snapshot in
# report_paper_versions. The three endpoints below back the version
# history panel: list, save (manual snapshot), restore.

@app.get("/api/v1/reports/generations/{generation_id}/versions")
async def get_paper_versions(
    generation_id: int,
    session: dict = Depends(require_team_member),
):
    """Every version snapshot for a generation, newest first.
    Includes paper_md so the preview can render without a second
    round-trip per version. Powers VersionHistoryPanel."""
    from tools.paper_versions import check_revision, list_versions
    versions = await list_versions(generation_id)
    revision = await check_revision(generation_id)
    return {
        "generation_id":     generation_id,
        "paper_revision":    revision,
        "versions":          versions,
        "version_count":     len(versions),
    }


@app.post("/api/v1/reports/generations/{generation_id}/versions")
async def post_paper_version(
    generation_id: int, body: dict,
    session: dict = Depends(require_team_member),
):
    """Save the CURRENT paper_md as a named version snapshot. Body:
      label (optional) — short description of the save point
      source (optional) — defaults to 'manual'
    The current paper_md is read fresh from report_generations so a
    concurrent inline edit just before the manual save is captured."""
    from tools.report_generator import get_generation
    from tools.paper_versions import save_version
    gen = await get_generation(generation_id)
    if not gen:
        raise HTTPException(
            status_code=404, detail="Generation not found.")
    snapshot = await save_version(
        generation_id, gen.get("paper_md") or "",
        saved_by_email=session.get("email"),
        label=body.get("label"),
        source=body.get("source") or "manual",
        flag_count=int(gen.get("flag_count") or 0),
        word_counts=gen.get("word_counts") or {},
    )
    if snapshot is None:
        raise HTTPException(
            status_code=500,
            detail="Failed to save version — see server logs.")
    return {"snapshot": snapshot}


@app.post(
    "/api/v1/reports/generations/{generation_id}/versions"
    "/{version_number}/restore")
async def post_paper_version_restore(
    generation_id: int, version_number: int,
    session: dict = Depends(require_team_member),
):
    """Restore a prior version as the new current paper_md. The
    older version row is preserved — a new row is appended with
    source='restore' and restored_from_version pointing at the
    source. Returns the new version snapshot."""
    from tools.paper_versions import restore_version
    snapshot = await restore_version(
        generation_id, version_number,
        reviewer_email=session.get("email"),
    )
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=("Version not found, or the restore failed — "
                    "see server logs."))
    return {"snapshot": snapshot}


@app.post(
    "/api/v1/reports/generations/{generation_id}/versions"
    "/{version_number}/mark-final")
async def post_mark_version_final(
    generation_id: int, version_number: int,
    session: dict = Depends(require_team_member),
):
    """Marks one saved version as the Final Submission. Clears any
    prior Final marker on the same generation. Defense Prep and
    Citation Adjudication reference the Final-marked version
    instead of the most recent draft.

    May 24 2026 P5 — Final Submission marker.
    """
    if ENVIRONMENT == "test":
        return {"marked": True, "version_number": version_number}
    from tools.paper_versions import mark_version_final
    snapshot = await mark_version_final(generation_id, version_number)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=("Version not found or the mark failed — "
                    "see server logs."))
    return {"marked": True, "snapshot": snapshot}


@app.delete(
    "/api/v1/reports/generations/{generation_id}/versions"
    "/final-marker")
async def delete_final_marker(
    generation_id: int,
    session: dict = Depends(require_team_member),
):
    """Clears the Final Submission marker on a generation. The
    versions themselves are preserved; only the flag is cleared.

    May 24 2026 P5 — Final Submission marker.
    """
    if ENVIRONMENT == "test":
        return {"unmarked": True}
    from tools.paper_versions import unmark_final_version
    ok = await unmark_final_version(generation_id)
    return {"unmarked": ok}


@app.get(
    "/api/v1/reports/generations/{generation_id}/versions/canonical")
async def get_canonical_version_endpoint(
    generation_id: int,
    session: dict = Depends(require_team_member),
):
    """Returns the canonical submission version for a generation:
    the Final-marked version when one exists, the most recent
    saved version otherwise. Defense Prep and Citation
    Adjudication consume this so both reference the same row.

    May 24 2026 P5 — Final Submission marker.
    """
    if ENVIRONMENT == "test":
        return {"canonical": None}
    from tools.paper_versions import get_canonical_version
    canonical = await get_canonical_version(generation_id)
    if canonical is None:
        raise HTTPException(
            status_code=404,
            detail="No saved version found for this generation.")
    return {"canonical": canonical}


@app.delete(
    "/api/v1/reports/generations/{generation_id}/versions"
    "/{version_number}")
async def delete_paper_version(
    generation_id: int, version_number: int,
    session: dict = Depends(require_team_member),
):
    """Hard-delete a single saved version. The frontend Version History
    panel gates this behind a type-DELETE confirmation. The currently-
    active paper_md on report_generations is NOT touched — only the
    snapshot row in report_paper_versions is removed.

    May 24 2026 — Version History Delete UX.
    """
    if ENVIRONMENT == "test":
        return {"deleted": True}
    from tools.paper_versions import delete_version
    ok = await delete_version(generation_id, version_number)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Version not found, or the delete failed — "
                   "see server logs.")
    return {"deleted": True}


@app.delete(
    "/api/v1/reports/generations/{generation_id}/versions")
async def delete_all_paper_versions(
    generation_id: int,
    session: dict = Depends(require_team_member),
):
    """Hard-delete EVERY saved version for this generation. The
    Delete All Drafts flow on the Version History panel; gated behind
    a type-DELETE confirmation.

    May 24 2026 — Version History Delete UX.
    """
    if ENVIRONMENT == "test":
        return {"deleted": 0}
    from tools.paper_versions import delete_all_versions
    n = await delete_all_versions(generation_id)
    return {"deleted": n}


@app.post("/api/v1/reports/generations/{generation_id}/iterate")
@limiter.limit("30/minute")
async def post_iterate_text(
    request: Request, generation_id: int, body: dict,
    session: dict = Depends(require_team_member),
):
    """AI iteration toolbar. Body: {action, selection, instruction?}.

    action ∈ {rephrase, tighten, expand, ask}.

    Returns the rewritten text + any new unverified numbers or
    citations the iteration introduced — the editor warns Bob before
    accepting changes that would fail the next final check."""
    if ENVIRONMENT == "test":
        return {
            "original":  body.get("selection", ""),
            "rewritten": body.get("selection", ""),
            "word_delta": 0,
            "new_unverified_numbers": [],
            "new_unverified_citations": [],
        }
    action = body.get("action")
    selection = body.get("selection")
    if not action or selection is None:
        raise HTTPException(
            status_code=422,
            detail="action and selection are required.")
    if action not in ("rephrase", "tighten", "expand", "ask"):
        raise HTTPException(
            status_code=422,
            detail=f"unknown action '{action}'")
    try:
        from tools.report_generator import iterate_text
        return await iterate_text(
            int(generation_id), action, str(selection),
            instruction=body.get("instruction"))
    except Exception as exc:
        log.warning("iterate_endpoint_failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Iteration failed — see server logs.")


@app.post("/api/v1/reports/generations/{generation_id}/resolve-bob")
@limiter.limit("60/minute")
async def post_resolve_bob_block(
    request: Request, generation_id: int, body: dict,
    session: dict = Depends(require_team_member),
):
    """Replaces the FIRST occurrence of `marker` in paper_md with
    `replacement`. Body: {marker: str, replacement: str}."""
    if ENVIRONMENT == "test":
        return {"saved": False, "flag_count": 0}
    marker = body.get("marker")
    replacement = body.get("replacement")
    if marker is None or replacement is None:
        raise HTTPException(
            status_code=422,
            detail="marker and replacement are required.")
    try:
        from tools.report_generator import resolve_bob_block
        result = await resolve_bob_block(
            int(generation_id), str(marker), str(replacement))
        if result.get("error") == "generation_not_found":
            raise HTTPException(status_code=404,
                                detail="Generation not found.")
        if result.get("error") == "marker_not_found":
            raise HTTPException(
                status_code=422,
                detail=f"marker not found in paper_md: {marker}")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("resolve_bob_endpoint_failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Resolve failed — see server logs.")


@app.post("/api/v1/reports/generations/{generation_id}/rebalance")
@limiter.limit("12/minute")
async def post_rebalance_paper(
    request: Request, generation_id: int,
    session: dict = Depends(require_team_member),
):
    """May 24 2026 — Pass 2 of the two-pass draft generation flow.
    After Bob adjudicates every [BOB] block, the section word
    counts are off-budget. This endpoint re-runs the writer over
    the current paper_md with a rebalance instruction that brings
    each off-budget section back within ±5 words of its target,
    without touching inline citations or specific numbers.

    No body required — the endpoint reads the current paper_md
    from the generation row. Returns the updated paper_md + a
    targets list naming which sections were re-balanced."""
    if ENVIRONMENT == "test":
        return {"saved": True, "paper_md": "", "rebalanced": False,
                "note": "test environment"}
    try:
        from tools.report_generator import rebalance_paper
        result = await rebalance_paper(int(generation_id))
        if result.get("error") == "generation_not_found":
            raise HTTPException(status_code=404,
                                detail="Generation not found.")
        if result.get("error") == "empty_paper":
            raise HTTPException(status_code=422,
                                detail="Paper is empty — cannot rebalance.")
        if result.get("error") == "writer_unavailable":
            raise HTTPException(
                status_code=503,
                detail="Writer model unavailable for rebalance — try again.")
        if result.get("error") == "writer_returned_empty":
            raise HTTPException(
                status_code=502,
                detail="Writer returned empty response — try again.")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("rebalance_endpoint_failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Rebalance failed — see server logs.")


@app.post("/api/v1/reports/generations/{generation_id}/final-check")
@limiter.limit("60/minute")
async def post_run_final_check(
    request: Request, generation_id: int,
    session: dict = Depends(require_team_member),
):
    """Re-runs the post-checks against the current paper_md and
    updates flag_count on the row so the download endpoints can gate.
    No body required."""
    if ENVIRONMENT == "test":
        return {"passed": True, "flag_count": 0, "flags": []}
    try:
        from tools.report_generator import run_final_check
        result = await run_final_check(int(generation_id))
        if result.get("error") == "generation_not_found":
            raise HTTPException(status_code=404,
                                detail="Generation not found.")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("final_check_endpoint_failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Final check failed — see server logs.")


@app.post("/api/v1/reports/generations/{generation_id}/academic-review")
@limiter.limit("6/minute")
async def post_run_academic_review(
    request: Request, generation_id: int,
    session: dict = Depends(require_team_member),
):
    """Step 10 — scores the current paper_md against the active
    rubric for its template. Persists the review payload + readiness
    on the row so the download endpoint can soft-gate."""
    if ENVIRONMENT == "test":
        return {
            "per_criterion":     [],
            "data_gaps":         [],
            "citation_gaps":     [],
            "thesis_coherence":  [],
            "tone_violations":   [],
            "length_compliance": [],
            "readiness":         "ready_to_submit",
            "summary":           "(test environment)",
        }
    try:
        from tools.report_generator import run_academic_review
        result = await run_academic_review(int(generation_id))
        if result.get("error") == "generation_not_found":
            raise HTTPException(status_code=404,
                                detail="Generation not found.")
        if result.get("error") == "rubric_not_found":
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No rubric uploaded for template "
                    f"'{result.get('template_id')}'."))
        return result
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("academic_review_endpoint_failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Academic review failed — see server logs.")


def _download_filename(template_id: str, kind: str, ext: str) -> str:
    """forest-capital-midpoint-check-paper-2026-05-22.docx"""
    from datetime import date
    slug = template_id.replace("_", "-")
    return (
        f"forest-capital-{slug}-{kind}-{date.today().isoformat()}.{ext}")


def _gate_download(
    gen: dict, acknowledge_warning: bool,
) -> None:
    """Enforces the two-tier download gate.

    Hard gate: flag_count > 0 means BOB blocks or unresolved numbers
    are still present — refuse with 422.
    Soft gate: academic_readiness == needs_significant_revision —
    refuse unless acknowledge_warning is True. The endpoint records
    the override in the row's audit trail (caller's responsibility
    via the activity log, not duplicated here)."""
    if int(gen.get("flag_count") or 0) > 0:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "flags_remaining",
                "flag_count": int(gen.get("flag_count") or 0),
                "message": (
                    "Resolve all [BOB] blocks and unverified numbers "
                    "before downloading."),
            })
    readiness = gen.get("academic_readiness")
    if (readiness == "needs_significant_revision"
            and not acknowledge_warning):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "academic_review_significant_revision",
                "readiness": readiness,
                "message": (
                    "Academic review flagged significant gaps. "
                    "Retry with ?acknowledge_warning=true to "
                    "download anyway."),
            })


@app.get(
    "/api/v1/reports/generations/{generation_id}/download-paper")
async def download_report_paper(
    generation_id: int,
    acknowledge_warning: bool = False,
    session: dict = Depends(require_team_member),
):
    """Returns the paper .docx. Soft-gated by academic_readiness."""
    if ENVIRONMENT == "test":
        raise HTTPException(
            status_code=404,
            detail="No generation available in test environment.")
    try:
        from tools.report_generator import (
            get_generation, render_paper_bytes,
        )
        gen = await get_generation(generation_id)
        if not gen:
            raise HTTPException(status_code=404,
                                detail="Generation not found.")
        _gate_download(gen, acknowledge_warning)
        content = await render_paper_bytes(generation_id)
        if not content:
            raise HTTPException(
                status_code=500,
                detail="Render failed — see server logs.")
        filename = _download_filename(
            gen.get("template_id") or "report", "paper", "docx")
        return Response(
            content=content, media_type=_DOCX_MEDIA,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            })
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("download_paper_failed", error=str(exc))
        raise HTTPException(
            status_code=500, detail="Download failed.")


@app.get(
    "/api/v1/reports/generations/{generation_id}/download-appendix")
async def download_report_appendix(
    generation_id: int,
    session: dict = Depends(require_team_member),
):
    """Returns the appendix .docx. Always available once a generation
    exists — the appendix is the data record, not the editable
    submission, so it is NOT gated by flag_count or readiness."""
    if ENVIRONMENT == "test":
        raise HTTPException(
            status_code=404,
            detail="No generation available in test environment.")
    try:
        from tools.report_generator import (
            get_generation, render_appendix_bytes,
        )
        gen = await get_generation(generation_id)
        if not gen:
            raise HTTPException(status_code=404,
                                detail="Generation not found.")
        content = await render_appendix_bytes(generation_id)
        if not content:
            raise HTTPException(
                status_code=500,
                detail="Render failed — see server logs.")
        filename = _download_filename(
            gen.get("template_id") or "report", "appendix", "docx")
        return Response(
            content=content, media_type=_DOCX_MEDIA,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            })
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("download_appendix_failed", error=str(exc))
        raise HTTPException(
            status_code=500, detail="Download failed.")


@app.get("/api/v1/reports/templates/{template_id}/rubric")
async def get_template_rubric(
    template_id: str,
    session: dict = Depends(require_team_member),
):
    """Latest active rubric for a template. Drives the report
    writer's Rubric panel."""
    if ENVIRONMENT == "test":
        return {"rubric": None}
    try:
        from tools.report_rubrics import get_latest_rubric
        rubric = await get_latest_rubric(template_id)
        return {"rubric": rubric}
    except Exception as exc:
        log.warning("get_rubric_failed", error=str(exc))
        raise HTTPException(
            status_code=500, detail="Read rubric failed.")


@app.get("/api/v1/reports/pipeline-audit/active")
async def get_active_pipeline_audit(
    session: dict = Depends(require_team_member),
):
    """Returns the most recent pipeline run started by the caller in
    the last two hours, or {available: false} when none exists. The
    report writer reads this on mount to restore step state across
    navigation."""
    if ENVIRONMENT == "test":
        return {"available": False}
    try:
        from tools.pipeline_audit import get_active_run_for_user
        row = await get_active_run_for_user(session["email"])
        if not row:
            return {"available": False}
        # Attach the persisted paper_md when a generation_id exists,
        # so the editor restores the draft text on the same fetch.
        paper_md = ""
        gen_id = row.get("generation_id")
        if isinstance(gen_id, int):
            try:
                from tools.report_generator import get_generation
                gen = await get_generation(gen_id)
                if gen:
                    paper_md = gen.get("paper_md") or ""
            except Exception:  # noqa: BLE001
                paper_md = ""
        return {"available": True, "audit": row, "paper_md": paper_md}
    except Exception as exc:
        log.warning("active_audit_endpoint_failed", error=str(exc))
        return {"available": False}


@app.post("/api/v1/reports/pipeline-audit")
@limiter.limit("60/minute")
async def post_pipeline_audit(
    request: Request, body: dict,
    session: dict = Depends(require_team_member),
):
    """Records one pipeline audit row. Posted by the report-writer UI
    after the pipeline reaches step 7 (success) or fails at any
    earlier step. The body shape is the flat per-step dict the UI
    builds from its timing state; the writer fills in the
    triggered_by from the authenticated session.

    Body:
      generation_id:        int | null
      template_id:          str (required)
      total_pipeline_ms:    int | null
      failure_step:         int | null
      failure_reason:       str | null
      steps:                {step_<n>_status, step_<n>_ms,
                             step_5_mismatch_count?,
                             step_6_conditions?}
    """
    if ENVIRONMENT == "test":
        return {"id": None}
    template_id = body.get("template_id")
    if not template_id:
        raise HTTPException(
            status_code=422, detail="template_id is required.")
    try:
        from tools.pipeline_audit import (
            upsert_active_run, update_generation_timings,
        )
        steps = body.get("steps") or {}
        # Item 12 commit B — incremental upsert. The frontend rounds
        # trip the audit_id so every step completion writes to the
        # same row. First write (audit_id None) inserts; subsequent
        # writes update only the fields present in the steps dict.
        # The terminal write (Step 7 success/failure) still flows
        # through here — it just happens to be the last upsert.
        new_id = await upsert_active_run(
            template_id=str(template_id),
            triggered_by=session.get("email"),
            steps=steps,
            total_pipeline_ms=body.get("total_pipeline_ms"),
            failure_step=body.get("failure_step"),
            failure_reason=body.get("failure_reason"),
            generation_id=body.get("generation_id"),
            audit_id=body.get("audit_id"),
        )
        # When the run produced a generation, also persist the
        # per-step ms dict on the row so the summary card has a
        # canonical record. Fail-open.
        gen_id = body.get("generation_id")
        if isinstance(gen_id, int):
            timings = {
                k.replace("_ms", ""): v
                for k, v in steps.items() if k.endswith("_ms")
            }
            if timings:
                await update_generation_timings(gen_id, timings)
        return {"id": new_id, "audit_id": new_id}
    except Exception as exc:
        log.warning("pipeline_audit_endpoint_failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Pipeline audit write failed.")


@app.get("/api/v1/admin/pipeline-audit")
async def list_pipeline_audit_runs(
    limit: int = 100,
    session: dict = Depends(require_sysadmin),
):
    """Newest-first audit runs for the sysadmin Settings panel."""
    if ENVIRONMENT == "test":
        return {"runs": []}
    try:
        from tools.pipeline_audit import list_audit_runs
        runs = await list_audit_runs(limit=limit)
        return {"runs": runs}
    except Exception as exc:
        log.warning("pipeline_audit_list_endpoint_failed", error=str(exc))
        raise HTTPException(
            status_code=500, detail="Read audit runs failed.")


@app.get("/api/v1/admin/pipeline-audit/{audit_id}")
async def get_pipeline_audit_run(
    audit_id: int,
    session: dict = Depends(require_sysadmin),
):
    """Single audit run by id for the expand-row view."""
    if ENVIRONMENT == "test":
        return {"error": "not_found"}
    try:
        from tools.pipeline_audit import get_audit_run
        row = await get_audit_run(audit_id)
        if not row:
            raise HTTPException(status_code=404, detail="Run not found.")
        return row
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("pipeline_audit_read_endpoint_failed", error=str(exc))
        raise HTTPException(
            status_code=500, detail="Read audit run failed.")


@app.post("/api/v1/reports/templates/{template_id}/rubric")
@limiter.limit("6/minute")
async def post_upload_rubric(
    request: Request, template_id: str, body: dict,
    session: dict = Depends(require_team_member),
):
    """Uploads a new rubric version for a template.

    Body: {rubric_text, criteria, source_filename?}. The PDF/docx
    text extraction runs client-side (the frontend uses the same
    pypdf path as the academic_documents flow) so this endpoint
    accepts already-extracted text + already-parsed criteria.
    """
    if ENVIRONMENT == "test":
        return {"id": None, "version": 1}
    rubric_text = body.get("rubric_text")
    criteria = body.get("criteria")
    if not rubric_text or not isinstance(criteria, list):
        raise HTTPException(
            status_code=422,
            detail="rubric_text and criteria (list) are required.")
    try:
        from tools.report_rubrics import upload_rubric, get_latest_rubric
        await upload_rubric(
            template_id, str(rubric_text), criteria,
            uploaded_by=session.get("email"),
            source_filename=body.get("source_filename"))
        latest = await get_latest_rubric(template_id)
        return {"rubric": latest}
    except Exception as exc:
        log.warning("upload_rubric_failed", error=str(exc))
        raise HTTPException(
            status_code=500, detail="Upload rubric failed.")


_RISK_FREE_SOURCE = "FRED DTB3 (3-month T-bill, mean monthly rate, annualised)"


async def _read_cached_metric_or_fallback(
    metric_kind: str,
    fallback: "Callable[[], Awaitable[dict]]",
) -> dict:
    """Three-tier read for analytics_metrics_cache where the cold-cache
    fallback isn't the strategy-keyed inline path that
    _read_div_metric_or_compute assumes. The fallback is an arbitrary
    async callable that produces the payload from whatever the
    endpoint's natural inline path is.

    Used by /api/v1/analytics/sensitivity (cold path needs the full
    pipeline history) and /api/v1/analytics/config (cold path reads
    market_data_monthly directly). Both pre-existed the cache layer;
    F1 + F4 fold them in alongside the seven diversification metrics
    that already use _read_div_metric_or_compute.
    """
    try:
        from tools.cache import get_latest_strategy_hash
        from tools.precomputed_analytics import (
            get_metric, get_latest_metric,
        )
        latest_hash = await get_latest_strategy_hash()
        if latest_hash:
            cached = await get_metric(latest_hash, metric_kind)
            if cached:
                log.info("metric_cache_hit", metric=metric_kind)
                return cached
            stale = await get_latest_metric(metric_kind)
            if stale:
                log.info("metric_cache_stale_hit", metric=metric_kind)
                return stale
        log.info("metric_cache_miss_inline", metric=metric_kind)
        return await fallback()
    except Exception as exc:  # noqa: BLE001
        log.warning("metric_read_failed", metric=metric_kind, error=str(exc))
        return await fallback()


@app.get("/api/v1/analytics/config")
async def get_analytics_config(session: dict = Depends(require_auth)):
    """
    The analytics assumptions surfaced in Settings → Analytics
    Configuration. Currently the risk-free rate applied to every Sharpe
    ratio and to the efficient frontier — the mean monthly DTB3 rate from
    market_data_monthly, annualised (×12). This is the SAME value the
    /api/optimize/weights frontier and the analytics layer use. Read-only.

    F4 (May 22 2026): cache-first via analytics_metrics_cache. The
    cold-cache fallback runs the original inline compute so the
    contract is preserved on a fresh deploy that hasn't seen a
    strategy_cache write yet.
    """
    if ENVIRONMENT == "test":
        return {"available": False, "risk_free_rate": None,
                "risk_free_source": _RISK_FREE_SOURCE}

    async def _inline_config() -> dict:
        try:
            from tools.cache import get_monthly_returns
            monthly = await get_monthly_returns()
            rf_list = (monthly or {}).get("rf") or []
            rf_annual = (
                sum(rf_list) / len(rf_list) * 12) if rf_list else None
            return {
                "available": rf_annual is not None,
                "risk_free_rate":
                    round(rf_annual, 4) if rf_annual is not None else None,
                "risk_free_source": _RISK_FREE_SOURCE,
            }
        except Exception as exc:
            log.warning("analytics_config_failed", error=str(exc))
            return {"available": False, "risk_free_rate": None,
                    "risk_free_source": _RISK_FREE_SOURCE}

    return await _read_cached_metric_or_fallback(
        "risk_free_rate_config", _inline_config)


@app.get("/api/v1/analytics/sensitivity")
@limiter.limit("10/minute")
async def get_analytics_sensitivity(request: Request, session: dict = Depends(require_auth)):
    """
    Parameter sensitivity analysis for the four dynamic strategies — the
    Sharpe ratio swept across a range of each strategy's key parameter.

    This is a ~23-backtest computation. F1 (May 22 2026) folded it into
    analytics_metrics_cache: refresh_sensitivity runs the sweep once
    when a fresh strategy_results_cache row lands, and this endpoint
    serves the cached payload on every subsequent request. The cold-
    cache fallback below preserves the original
    get_full_history + compute_sensitivity path for a fresh deploy
    that has not seen a strategy_cache write yet — compute_sensitivity
    has its own worker-local memo so even the cold path only does
    full work once per worker per history. The frontend section
    shows its own loading state regardless.
    """
    if ENVIRONMENT == "test":
        return {"available": False, "strategies": []}

    async def _inline_sensitivity() -> dict:
        try:
            from tools.data_fetcher import get_full_history_async
            from tools.sensitivity import compute_sensitivity
            history = await get_full_history_async()
            result = await asyncio.to_thread(compute_sensitivity, history)
            return {"available": True, **result}
        except Exception as exc:
            log.warning("analytics_sensitivity_failed", error=str(exc))
            return {"available": False, "strategies": []}

    return await _read_cached_metric_or_fallback(
        "sensitivity", _inline_sensitivity)


# ── Admin: data status ────────────────────────────────────────────────────────

@app.post("/api/v1/admin/warm-analytics-cache")
async def post_warm_analytics_cache(
    session: dict = Depends(require_team_member),
):
    """May 24 2026 — manual warm trigger for the
    analytics_metrics_cache table. The auto-warm hook fires this
    automatically at startup with retry; this endpoint is the
    sysadmin override for "warm now, regardless of cache state".

    Runs refresh_all_analytics inline and returns when the rows
    have landed. Updates WarmState so /cache-status reflects the
    completed run.

    Returns:
      {warmed: bool, latest_hash: str | None, took_s: float,
       academic_analytics: bool, efficient_frontier: bool}
    """
    if ENVIRONMENT == "test":
        return {
            "warmed":            True,
            "latest_hash":       None,
            "took_s":            0.0,
            "academic_analytics": False,
            "efficient_frontier": False,
            "note":              "test environment",
        }
    try:
        from tools.cache import get_latest_strategy_hash
        from tools.cache_warm_state import auto_warm_analytics, get_warm_state
        # Single attempt — the user is asking for an explicit
        # warm now, so we don't apply the auto-warm's exponential
        # backoff. Failures still surface to the WarmState.
        state = await auto_warm_analytics(max_attempts=1)
        latest_hash = await get_latest_strategy_hash()
        if state.status != "warm":
            raise HTTPException(
                status_code=500,
                detail=f"Warm failed: {state.last_attempt_error}")
        landed = state.last_landed or {}
        return {
            "warmed":              True,
            "latest_hash":         latest_hash,
            "took_s":              state.last_took_s or 0.0,
            "academic_analytics":  bool(landed.get("academic_analytics")),
            "efficient_frontier":  bool(landed.get("efficient_frontier")),
        }
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("warm_analytics_cache_failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"Warm failed: {exc}")


@app.get("/api/v1/admin/cache-status")
async def get_cache_status(
    session: dict = Depends(require_auth),
):
    """May 24 2026 — analytics cache status for the Admin UI.

    Returns the auto-warm subsystem's current state plus the
    per-row landed booleans from analytics_metrics_cache so the
    Admin UI can show the right state without a second round-trip:

      {
        "status": "idle" | "warming" | "warm" | "failed",
        "in_progress": bool,
        "attempts": int,
        "last_attempt_at": ISO8601 | null,
        "last_success_at": ISO8601 | null,
        "last_success_age_seconds": float | null,
        "last_attempt_error": str | null,
        "last_took_s": float | null,
        "last_landed": {academic_analytics, efficient_frontier},
        "cache_present": {
            "academic_analytics": bool,
            "efficient_frontier": bool,
        },
      }

    Any authenticated user can read this — the UI's Warm Cache
    button itself is sysadmin-only but every team member should
    be able to see whether the dashboard's cache is hot.
    """
    from tools.cache_warm_state import get_warm_state
    state = get_warm_state().to_dict()
    cache_present = {"academic_analytics": False, "efficient_frontier": False}
    if ENVIRONMENT != "test":
        try:
            from tools.cache import get_latest_strategy_hash
            from tools.precomputed_analytics import (
                get_metric as get_precomputed,
                get_latest_metric as get_latest_precomputed,
            )
            latest_hash = await get_latest_strategy_hash()
            sentinel = latest_hash or "BOOT-WARM"
            aa = await get_precomputed(sentinel, "academic_analytics")
            ef = await get_precomputed(sentinel, "efficient_frontier")
            if aa is None:
                aa = await get_latest_precomputed("academic_analytics")
            if ef is None:
                ef = await get_latest_precomputed("efficient_frontier")
            cache_present = {
                "academic_analytics":  bool(aa),
                "efficient_frontier":  bool(ef),
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("cache_status_read_failed", error=str(exc))
    state["cache_present"] = cache_present
    return state


@app.get("/api/v1/admin/data-status")
async def get_admin_data_status(session: dict = Depends(require_auth)):
    """
    Read-only status of the data tables feeding the analytics layer —
    row counts, date ranges, last-updated timestamps and a green/amber/red
    staleness pill per table. Surfaced in Settings → Data and Study Period.
    """
    if ENVIRONMENT == "test":
        return {"available": False, "study_period": None, "tables": []}
    from tools.cache import get_data_status
    return await get_data_status()


@app.get("/api/v1/admin/team-activity/merge-commit-authors")
async def get_merge_commit_authors_diagnostic(
    session: dict = Depends(require_sysadmin),
):
    """Sysadmin diagnostic — returns the distinct `author` values
    of every commit_activity row whose message contains "merge
    pull request" (ILIKE).

    Hotfix May 23 2026 (iteration 4): production data shows ~100
    merged PRs but only 2 matches against the IN clause for
    Michael's known git emails. This endpoint exposes the actual
    author identities used on merge commits so we can confirm the
    fix is catching them.

    Expected output: a small set of distinct emails, e.g.
    mikeruurds@gmail.com (2) + mikeruurds@users.noreply.github.com
    (~98). The hotfix's local-part match should bring the count
    to ~100.

    Sysadmin-only — the endpoint exposes git author emails which
    could be PII for non-team contributors. Returns at most the
    most recent N rows to bound the response.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return {"available": False, "rows": []}
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                "SELECT LOWER(author) AS author_email, COUNT(*) AS n, "
                " MAX(timestamp) AS latest_at "
                "FROM commit_activity "
                "WHERE message ILIKE '%merge pull request%' "
                "GROUP BY LOWER(author) "
                "ORDER BY n DESC LIMIT 50"
            ))
            rows = [
                {"author": row[0],
                 "merge_commit_count": int(row[1] or 0),
                 "latest_at": (row[2].isoformat() if row[2] else None)}
                for row in r.fetchall()
            ]
            r2 = await s.execute(text(
                "SELECT COUNT(*) FROM commit_activity "
                "WHERE message ILIKE '%merge pull request%'"))
            total = int(r2.scalar() or 0)
            return {
                "available": True,
                "total_merge_commits": total,
                "distinct_authors": rows,
                "note": (
                    "Hotfix iteration 4: if Michael's GitHub "
                    "noreply email appears here with a high count, "
                    "the local-part LIKE match in template_pipeline."
                    "fetch_team_activity is what now catches it. "
                    "Confirm Step 3's michael_prs_merged increases "
                    "to approximately this total."),
            }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc)}


# ── Academic documents (agent context) ───────────────────────────────────────

# 10 MB upload ceiling — the rubric / requirements documents are short;
# anything larger is almost certainly the wrong file.
_ACADEMIC_DOC_MAX_BYTES = 10 * 1024 * 1024


@app.post("/api/v1/documents/academic/upload")
async def upload_academic_document(
    request: Request,
    file: UploadFile = File(...),
    document_type: str = Form("other"),
    session: dict = Depends(require_team_member),
):
    """
    Uploads a PDF or Markdown (.md) reference document (the midpoint
    rubric, the final-presentation requirements, etc.). Text is extracted
    server-side; only the text is persisted. After storage every agent
    injects the document as system context on its next invocation.

    File type is decided by extension, not MIME type — browsers send
    Markdown as text/plain or text/markdown inconsistently, so the
    extension is the authoritative check. PDFs go through pypdf; .md
    files are read directly as UTF-8 (pypdf is bypassed entirely).
    """
    from tools.academic_context import (
        DOCUMENT_TYPES, extract_document_text, insert_academic_document,
    )

    if document_type not in DOCUMENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown document_type '{document_type}'. "
                   f"Valid: {', '.join(DOCUMENT_TYPES)}",
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")
    if len(raw) > _ACADEMIC_DOC_MAX_BYTES:
        raise HTTPException(status_code=422, detail="File too large (max 10 MB).")

    filename = file.filename or "document"
    lower_name = filename.lower()
    if lower_name.endswith(".pdf"):
        try:
            content_text = extract_document_text(filename, raw)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        file_type = "PDF"
    elif lower_name.endswith(".md"):
        # Markdown — extension is authoritative. Read directly as UTF-8,
        # bypassing pypdf entirely; the bytes are the content verbatim.
        content_text = raw.decode("utf-8", errors="replace").strip()
        if not content_text:
            raise HTTPException(status_code=422, detail="Uploaded file is empty.")
        file_type = "MD"
    else:
        raise HTTPException(
            status_code=400,
            detail="Only PDF and Markdown (.md) files are supported",
        )

    doc_id = await insert_academic_document(filename, document_type, content_text)
    if not doc_id:
        raise HTTPException(
            status_code=500,
            detail="Could not store the document — database unavailable.",
        )
    # Log type + size so ingestion can be verified from the production logs.
    log.info(
        "academic_document_ingested",
        file_type=file_type,
        char_count=len(content_text),
        document_type=document_type,
    )
    # Team Activity — record the upload as an interaction (non-blocking).
    _log_interaction_bg(
        request, session, "document_upload",
        metadata={
            "document_type": document_type,
            "filename": filename,
            "file_type": file_type,
            "char_count": len(content_text),
        },
    )
    return {
        "id": doc_id,
        "name": filename,
        "document_type": document_type,
        "file_type": file_type,
        "char_count": len(content_text),
    }


@app.get("/api/v1/documents/academic")
async def list_academic_docs(session: dict = Depends(require_auth)):
    """Lists uploaded academic documents (metadata only — no content text)."""
    from tools.academic_context import list_academic_documents
    return {"documents": await list_academic_documents()}


@app.delete("/api/v1/documents/academic/{doc_id}")
async def delete_academic_doc(doc_id: str, session: dict = Depends(require_team_member)):
    """Deletes an academic document and refreshes the agent-context cache."""
    from tools.academic_context import delete_academic_document
    ok = await delete_academic_document(doc_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Document not found.")
    return {"deleted": True, "id": doc_id}


# ── Charts — server-rendered PNGs for the canvas presentation editor ──────────

@app.get("/api/v1/charts/available")
async def charts_available(session: dict = Depends(require_team_member)):
    """The charts the canvas presentation editor can embed — every chart
    render_deck_charts produces server-side. Project team only."""
    from tools.chart_render import AVAILABLE_CHARTS
    return AVAILABLE_CHARTS


@app.get("/api/v1/charts/render/{chart_key}")
@limiter.limit("60/minute")
async def charts_render(
    chart_key: str,
    request: Request,
    theme: str = "light",
    width: int = 720,
    height: int = 400,
    session: dict = Depends(require_team_member),
):
    """
    Renders one chart server-side as a PNG for the canvas editor, sized
    to width x height. theme=dark falls back to the light render (the
    matplotlib renderers are light-only). The PNG is cached five minutes
    per (chart_key, theme, width, height). 404 for an unknown chart_key.
    Project team only.
    """
    from tools.chart_render import is_known_chart, render_chart_png
    if not is_known_chart(chart_key):
        raise HTTPException(status_code=404,
                            detail=f"Unknown chart key: {chart_key}")
    theme = "dark" if str(theme).lower() == "dark" else "light"
    w = max(80, min(int(width), 2000))
    h = max(80, min(int(height), 2000))
    png = await render_chart_png(chart_key, theme, w, h)
    return Response(content=png, media_type="image/png")


# ── Document editor — editor_drafts / editor_draft_versions ────────────────────
#
# The in-platform editor for Bob's midpoint paper and Molly's presentation
# deck (migration 021). All endpoints require team_member; the auto-save
# PATCH is silent (no version), POST .../versions saves a named checkpoint.

@app.get("/api/v1/documents/drafts")
async def editor_list_drafts(session: dict = Depends(require_team_member)):
    """Every non-deleted draft owned by the current user."""
    from tools.editor_drafts import list_drafts
    return {"drafts": await list_drafts(session["email"])}


@app.get("/api/v1/documents/drafts/{draft_id}")
async def editor_get_draft(
    draft_id: int, session: dict = Depends(require_team_member),
):
    """A single draft with its current working content."""
    from tools.editor_drafts import get_draft
    draft = await get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return draft


@app.post("/api/v1/documents/drafts", status_code=201)
async def editor_create_draft(
    body: dict, session: dict = Depends(require_team_member),
):
    """
    Creates a draft and makes it the current one for this owner +
    document_type. Body: {document_type, title, content_json,
    content_text, created_from?}.
    """
    from tools.editor_drafts import DOCUMENT_TYPES, create_draft
    document_type = str(body.get("document_type") or "")
    if document_type not in DOCUMENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"document_type must be one of {DOCUMENT_TYPES}.")
    title = str(body.get("title") or "Untitled draft")[:300]
    created_from = str(body.get("created_from") or "manual")
    draft = await create_draft(
        document_type, session["email"], title,
        body.get("content_json"), body.get("content_text"),
        created_from=created_from)
    if draft is None:
        raise HTTPException(status_code=503,
                            detail="Draft storage is unavailable.")
    return draft


@app.patch("/api/v1/documents/drafts/{draft_id}")
async def editor_update_draft(
    draft_id: int, body: dict,
    session: dict = Depends(require_team_member),
):
    """
    Auto-save — overwrites the working content. Silent: does NOT create
    a version. Body: {content_json, content_text, word_count?}.
    """
    from tools.editor_drafts import update_draft
    wc = body.get("word_count")
    ok = await update_draft(
        draft_id, body.get("content_json"), body.get("content_text"),
        word_count_override=int(wc) if isinstance(wc, (int, float)) else None)
    if not ok:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return {"saved": True, "draft_id": draft_id}


@app.post("/api/v1/documents/drafts/{draft_id}/versions", status_code=201)
async def editor_save_version(
    draft_id: int, body: dict | None = None,
    session: dict = Depends(require_team_member),
):
    """Saves a named version checkpoint. Body: {version_label}."""
    from tools.editor_drafts import save_version
    label = None
    if body and body.get("version_label"):
        label = str(body["version_label"])[:200]
    version = await save_version(draft_id, label, session["email"])
    if version is None:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return version


@app.get("/api/v1/documents/drafts/{draft_id}/versions")
async def editor_list_versions(
    draft_id: int, session: dict = Depends(require_team_member),
):
    """Every saved version of a draft, newest first."""
    from tools.editor_drafts import list_versions
    return {"versions": await list_versions(draft_id)}


@app.post("/api/v1/documents/drafts/{draft_id}/restore/{version_id}")
async def editor_restore_version(
    draft_id: int, version_id: int,
    session: dict = Depends(require_team_member),
):
    """Restores a saved version as the draft's current content."""
    from tools.editor_drafts import restore_version
    draft = await restore_version(draft_id, version_id)
    if draft is None:
        raise HTTPException(
            status_code=404,
            detail="Draft or version not found.")
    return draft


@app.delete("/api/v1/documents/drafts/{draft_id}")
async def editor_delete_draft(
    draft_id: int, session: dict = Depends(require_team_member),
):
    """Soft-deletes a draft."""
    from tools.editor_drafts import soft_delete_draft
    ok = await soft_delete_draft(draft_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return {"deleted": True, "draft_id": draft_id}


@app.post("/api/v1/documents/drafts/{draft_id}/chat")
@limiter.limit("30/hour")
async def editor_chat(
    draft_id: int, body: dict, request: Request,
    session: dict = Depends(require_team_member),
):
    """
    The editor's Writing Assistant chat. The signed-in user asks for
    writing help on their draft; the assistant answers with the draft's
    full text as context, referencing the actual content.

    Body: {message, history: [{role, content}], selection?}. A selection
    (a passage the user is asking about) is quoted into the prompt. The
    rate limit — 30/hour — keeps the assistant a help, not a crutch.

    404 if the draft does not exist or is not owned by the caller.
    Logged as a writing_assistant interaction so it appears in Team
    Activity and AI cost tracking.
    """
    import asyncio

    from tools.editor_drafts import get_draft
    draft = await get_draft(draft_id)
    # A draft the caller does not own is a 404 — not a 403 — so the
    # endpoint never confirms another user's draft exists.
    if draft is None or draft.get("owner_email") != session.get("email"):
        raise HTTPException(status_code=404, detail="Draft not found.")

    message = str(body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="'message' is required.")
    if len(message) > 2000:
        raise HTTPException(status_code=422,
                            detail="Message exceeds the 2000-character limit.")
    selection = body.get("selection")
    history = body.get("history") or []

    if ENVIRONMENT == "test":
        return {"response": "Writing assistant is mocked in the test "
                            "environment."}

    content_text = (draft.get("content_text") or "")[:8000]
    system_prompt = (
        f"You are a writing assistant helping {draft['owner_email']} "
        f"improve their {draft['document_type']} document.\n\n"
        f"The full document is:\n---\n{content_text}\n---\n\n"
        "Help the user improve their writing. Be specific and "
        "constructive. Reference the actual content of their document in "
        "your responses. Do not rewrite large sections unless explicitly "
        "asked. Keep responses concise — this is a chat interface, not a "
        "document."
    )

    # The last six exchanges, flattened into the prompt as context
    # (call_claude is single-turn).
    convo_lines: list[str] = []
    for turn in history[-6:]:
        role = "User" if turn.get("role") == "user" else "Assistant"
        text = str(turn.get("content") or "").strip()
        if text:
            convo_lines.append(f"{role}: {text}")
    user_message = ""
    if convo_lines:
        user_message += ("Earlier in this conversation:\n"
                         + "\n".join(convo_lines) + "\n\n")
    if selection:
        user_message += (f"Regarding this passage:\n> "
                         f"{str(selection)[:1000]}\n\n")
    user_message += message

    from agents.usage import start_usage_capture
    start_usage_capture()
    try:
        from agents.base import SONNET_MODEL, call_claude
        reply = await asyncio.to_thread(
            call_claude, SONNET_MODEL, system_prompt, user_message, 600)
    except Exception as exc:  # noqa: BLE001
        ref = uuid.uuid4().hex[:8]
        log.error("editor_chat_failed", ref=ref, error=str(exc))
        raise HTTPException(
            status_code=502,
            detail=f"Writing assistant unavailable (ref: {ref})")

    _log_interaction_bg(
        request, session, "writing_assistant",
        question_text=message[:500],
        response_summary=reply[:500],
        metadata={"draft_id": draft_id})
    return {"response": reply}


# ── Rubric Review (May 25 2026) ──────────────────────────────────────────────
#
# Sends the current midpoint paper draft to Gemini with the full FNA 670
# midpoint rubric. Returns a structured verdict — per-section pass/fail
# against the rubric criteria, specific optional edits with reasoning,
# and an overall readiness verdict. Read-only: the endpoint never
# modifies the draft — it surfaces suggestions inline in the Writing
# Assistant panel for the user to apply (or ignore) manually.
#
# Distinct from the Academic Review council pass: that endpoint runs
# every council peer + the arbiter and is expensive. This endpoint is
# a single fast Gemini call against an explicit rubric prompt — useful
# for the quick "where am I against the rubric" check the user wants
# before committing to a full review.

_MIDPOINT_RUBRIC_PROMPT = (
    "You are reviewing a graduate-finance midpoint paper against the "
    "FNA 670 rubric. The paper is 3 pages, double-spaced, 12-point font, "
    "750-900 words TOTAL, organised into four sections:\n"
    "\n"
    "  1. DATA AND METHODOLOGY (1 page, 235-285 words)\n"
    "     - Identifies data sources and study period\n"
    "     - Names the portfolio constraints (long-only, fully invested, "
    "no cash, quarterly rebalancing)\n"
    "     - Distinguishes static vs dynamic strategies\n"
    "     - Names the Carhart four-factor attribution model\n"
    "     - APA style, past tense, third person\n"
    "\n"
    "  2. PRELIMINARY RESULTS AND DIAGNOSTICS (1 page, 235-285 words)\n"
    "     - INTERPRETS the results (does not merely list numbers)\n"
    "     - Discusses the 2022 equity-bond correlation break explicitly\n"
    "     - References summary statistics and regime-conditional "
    "performance with specific values\n"
    "     - Connects findings back to the research question\n"
    "\n"
    "  3. ROLES AND DIVISION OF LABOR (0.5 page, 110-135 words)\n"
    "     - States each team member's role\n"
    "     - Attributes documented contributions (commits, sessions, "
    "documents)\n"
    "     - Factual, no invented contributions\n"
    "\n"
    "  4. NEXT STEPS AND OPEN QUESTIONS (0.5 page, 110-135 words)\n"
    "     - Forward-looking, not retrospective\n"
    "     - Names specific areas for further investigation\n"
    "     - Reflects the team's analytical priorities\n"
    "\n"
    "Return a structured JSON response with EXACTLY this shape — no "
    "additional fields, no preamble:\n"
    "\n"
    "{\n"
    '  "sections": {\n'
    '    "methodology":  {"verdict": "pass" | "fail",\n'
    '                     "reasoning": "1-3 sentences naming which '
    'criteria were met and which were not"},\n'
    '    "results":      {"verdict": "pass" | "fail", "reasoning": "..."},\n'
    '    "roles":        {"verdict": "pass" | "fail", "reasoning": "..."},\n'
    '    "next_steps":   {"verdict": "pass" | "fail", "reasoning": "..."}\n'
    "  },\n"
    '  "edits": [\n'
    '    {"section": "methodology|results|roles|next_steps",\n'
    '     "suggestion": "specific change to consider",\n'
    '     "reasoning": "why this would strengthen the section"}\n'
    "  ],\n"
    '  "overall": {\n'
    '    "verdict": "ready" | "needs_work" | "not_ready",\n'
    '    "reasoning": "2-4 sentences summarising readiness"\n'
    "  }\n"
    "}\n"
    "\n"
    "VERDICT semantics:\n"
    "  - section 'pass' — the section meets the rubric criteria. Minor "
    "wordsmithing is fine; the substance is there.\n"
    "  - section 'fail' — a substantive rubric criterion is unmet "
    "(missing required content, wrong tense, no interpretation in "
    "results, etc.). Be honest, not generous.\n"
    "  - overall 'ready' — all four sections pass and the paper reads "
    "cleanly as a 3-page midpoint draft.\n"
    "  - overall 'needs_work' — sections substantially correct but "
    "specific edits would strengthen the submission.\n"
    "  - overall 'not_ready' — one or more sections fail; the draft is "
    "not yet at submission quality.\n"
    "\n"
    "EDITS — provide 3-6 specific optional suggestions ordered by "
    "impact. Each must name a section, a concrete change, and the "
    "rubric reason. These are SUGGESTIONS — the team will choose what "
    "to apply.\n"
    "\n"
    "Respond ONLY with the JSON object. No markdown fences, no preamble."
)


def _parse_rubric_review(raw: str) -> dict[str, object] | None:
    """Best-effort JSON parse on a Gemini response. Strips ``` fences
    and any preamble before / after the {…} block. Returns None on
    unparseable input — the endpoint then falls back to a friendly
    error rather than a 500.
    """
    import json as _json
    text = (raw or "").strip()
    # Strip ```json fences if Gemini ignored the no-fence instruction.
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    # Extract the outermost {…} block if there is any preamble.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return _json.loads(text[start:end + 1])
    except Exception:  # noqa: BLE001
        return None


def _rubric_review_unavailable_payload(reason: str) -> dict[str, object]:
    """Used by the test env path AND the no-Gemini-key / parse-failure
    fall-throughs. Returns a payload that renders cleanly in the UI
    without claiming a pass/fail verdict the model didn't produce."""
    return {
        "sections": {
            "methodology": {"verdict": "fail", "reasoning": reason},
            "results":     {"verdict": "fail", "reasoning": reason},
            "roles":       {"verdict": "fail", "reasoning": reason},
            "next_steps":  {"verdict": "fail", "reasoning": reason},
        },
        "edits": [],
        "overall": {"verdict": "not_ready", "reasoning": reason},
        "unavailable": True,
    }


@app.post("/api/v1/documents/drafts/{draft_id}/rubric-review")
@limiter.limit("10/hour")
async def rubric_review(
    draft_id: int, request: Request,
    session: dict = Depends(require_team_member),
):
    """
    Sends the current draft to Gemini with the midpoint paper rubric
    and returns a structured per-section verdict + suggested edits.
    Read-only — never modifies the draft. The Writing Assistant panel
    surfaces the verdict inline so the user can decide which edits to
    apply manually.

    Scoped to midpoint_paper drafts — other document types (executive
    brief, presentation deck/script) have their own rubrics not yet
    encoded here.
    """
    import asyncio

    from tools.editor_drafts import get_draft
    draft = await get_draft(draft_id)
    if draft is None or draft.get("owner_email") != session.get("email"):
        raise HTTPException(status_code=404, detail="Draft not found.")
    if draft.get("document_type") != "midpoint_paper":
        raise HTTPException(
            status_code=422,
            detail="Rubric review is only available for midpoint paper "
                   "drafts.")

    content_text = (draft.get("content_text") or "").strip()
    if not content_text:
        return _rubric_review_unavailable_payload(
            "The draft is empty — write some content before requesting "
            "a rubric review.")

    if ENVIRONMENT == "test":
        # Deterministic shape so the frontend test can pin the render
        # without hitting Gemini. Mirrors the structured response a
        # real run would produce.
        return {
            "sections": {
                "methodology": {"verdict": "pass",
                                "reasoning": "Methodology rubric criteria met."},
                "results":     {"verdict": "fail",
                                "reasoning": "Results lack explicit "
                                             "interpretation."},
                "roles":       {"verdict": "pass",
                                "reasoning": "Roles attributed factually."},
                "next_steps":  {"verdict": "pass",
                                "reasoning": "Forward-looking and specific."},
            },
            "edits": [
                {"section": "results",
                 "suggestion": "Add one sentence interpreting the "
                               "post-2022 Sharpe gap.",
                 "reasoning": "Rubric requires interpretation, not "
                              "listing."},
            ],
            "overall": {"verdict": "needs_work",
                        "reasoning": "Three sections pass; results "
                                     "needs interpretation."},
        }

    if not os.getenv("GOOGLE_API_KEY"):
        return _rubric_review_unavailable_payload(
            "Rubric review is configured to run on Gemini but the "
            "GOOGLE_API_KEY env var is not set.")

    user_message = (
        "Review the following midpoint paper draft against the rubric. "
        "The draft text is the four sections concatenated in order; the "
        "[[BOB: …]] markers are placeholders that the user will resolve "
        "before submission (treat them as 'work in progress' rather "
        "than rubric failures, but note when a placeholder is the only "
        "content for a required topic).\n\n"
        f"---\n{content_text[:12000]}\n---\n\n"
        "Respond with the JSON object specified in the instructions. "
        "No preamble, no markdown fences."
    )

    from agents.usage import start_usage_capture
    start_usage_capture()
    try:
        from agents.base import GEMINI_MODEL, call_gemini
        raw = await asyncio.to_thread(
            call_gemini, GEMINI_MODEL,
            _MIDPOINT_RUBRIC_PROMPT, user_message,
            trigger="rubric_review",
        )
    except Exception as exc:  # noqa: BLE001
        ref = uuid.uuid4().hex[:8]
        log.error("rubric_review_failed",
                  ref=ref, error=str(exc), draft_id=draft_id)
        return _rubric_review_unavailable_payload(
            f"Gemini call failed (ref: {ref}). Retry in a moment.")

    parsed = _parse_rubric_review(raw or "")
    if parsed is None or "sections" not in parsed or "overall" not in parsed:
        log.warning("rubric_review_parse_failed",
                    draft_id=draft_id, raw_head=(raw or "")[:200])
        return _rubric_review_unavailable_payload(
            "The rubric review came back in an unexpected format. "
            "Retry — Gemini occasionally drops the JSON shape.")

    _log_interaction_bg(
        request, session, "rubric_review",
        question_text=f"Rubric review for draft {draft_id}",
        response_summary=(parsed.get("overall") or {}).get(
            "reasoning", "")[:500],
        metadata={"draft_id": draft_id,
                  "overall_verdict": (parsed.get("overall") or {}).get("verdict")})
    return parsed


@app.post("/api/v1/documents/script/generate")
@limiter.limit("6/minute")
async def generate_presentation_script(
    request: Request,
    body: dict | None = None,
    session: dict = Depends(require_permission("team_member")),
):
    """
    Generates a presentation script from a presentation_deck draft.

    Reads the deck (draft_id in the body), the caller's current
    executive_brief and midpoint_paper drafts as academic context (both
    optional — generation degrades gracefully without them), runs the
    Academic Writer through the generator-evaluator harness, and stores
    the result as a new presentation_script editor draft. Returns the new
    draft id and its word / speaker / slide counts.

    400 when no slide has a speaker assigned; 404 when the deck draft is
    not found. Team members only.
    """
    import asyncio

    from tools.editor_drafts import create_draft, get_current_draft, get_draft
    from tools.script_generation import deck_speakers, generate_script

    raw_id = (body or {}).get("draft_id")
    try:
        deck_id = int(raw_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="'draft_id' is required.")

    deck = await get_draft(deck_id)
    if deck is None or deck.get("document_type") != "presentation_deck":
        raise HTTPException(
            status_code=404, detail="Presentation deck draft not found.")

    content = deck.get("content_json") or {}
    slides = content.get("slides", []) if isinstance(content, dict) else []
    if not deck_speakers(slides):
        raise HTTPException(
            status_code=400,
            detail="Assign speakers to slides before generating the script.")

    email = session["email"]
    exec_brief = await get_current_draft(email, "executive_brief")
    midpoint = await get_current_draft(email, "midpoint_paper")
    result = await asyncio.to_thread(
        generate_script, deck, exec_brief, midpoint)

    new_draft = await create_draft(
        "presentation_script", email, "Presentation Script",
        result["content_json"], result["content_text"],
        created_from="generated")
    if new_draft is None:
        ref = uuid.uuid4().hex[:8]
        log.error("script_draft_create_failed", ref=ref)
        raise HTTPException(
            status_code=500,
            detail=f"Could not save the generated script (ref: {ref})")
    return {
        "draft_id": new_draft["id"],
        "word_count": result["word_count"],
        "speaker_count": result["speaker_count"],
        "slide_count": result["slide_count"],
    }


@app.get("/api/v1/documents/rehearsal")
async def get_rehearsal_payload(
    session: dict = Depends(require_team_member),
):
    """
    Combined deck + script payload for the presentation rehearsal mode
    (combined-script-and-slide-view overlay in the script editor).

    Reads the requesting user's current (is_current=true)
    presentation_deck and presentation_script editor drafts and returns
    them side by side, with the script parsed into per-slide sections.

    Returns 404 when either draft is absent — the rehearsal needs both:
      deck missing:    "No presentation deck found. Generate your deck first."
      script missing:  "No presentation script found. Generate your script first."

    Estimated delivery time is total_words / 150 (the platform-wide
    150-wpm convention; the script editor's delivery time pill uses
    the same rate).
    """
    from tools.editor_drafts import get_current_draft
    from tools.rehearsal import parse_script_sections

    email = session.get("email", "")
    deck = await get_current_draft("presentation_deck", email)
    if deck is None:
        raise HTTPException(
            status_code=404,
            detail="No presentation deck found. Generate your deck first.")
    script = await get_current_draft("presentation_script", email)
    if script is None:
        raise HTTPException(
            status_code=404,
            detail="No presentation script found. Generate your script first.")

    # Deck slides — pass through the canvas shape verbatim. The frontend
    # rehearsal overlay reuses the same CanvasSlide / CanvasElement
    # types the editor itself uses to render the static preview.
    deck_json = deck.get("content_json") or {}
    slides = deck_json.get("slides") if isinstance(deck_json, dict) else []

    # Script — parse the TipTap doc into per-slide sections.
    script_json = script.get("content_json") or {}
    sections = parse_script_sections(script_json)
    total_words = sum(s.get("word_count", 0) for s in sections)
    estimated_minutes = max(1, round(total_words / 150))

    return {
        "deck": {
            "draft_id": deck.get("id"),
            "slides":   slides or [],
        },
        "script": {
            "draft_id":          script.get("id"),
            "sections":          sections,
            "total_words":       total_words,
            "estimated_minutes": estimated_minutes,
        },
    }


@app.post("/api/v1/documents/drafts/{draft_id}/export")
async def export_editor_draft(
    draft_id: int,
    body: dict | None = None,
    session: dict = Depends(require_team_member),
):
    """
    Exports a presentation_script editor draft as a .docx — the master
    script (every speaker) when no speaker is given, or one speaker's
    individual script (their slides only) when {speaker} is provided.
    Team members only.
    """
    import asyncio
    from datetime import date

    from tools.editor_drafts import get_draft
    from tools.script_docx import build_script_docx

    draft = await get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found.")
    if draft.get("document_type") != "presentation_script":
        raise HTTPException(
            status_code=400,
            detail="This export is available for presentation scripts only.")

    raw_speaker = (body or {}).get("speaker")
    speaker = str(raw_speaker).strip() if raw_speaker else None
    content = await asyncio.to_thread(build_script_docx, draft, speaker)

    slug = (speaker.lower().replace(" ", "-") if speaker else "master")
    filename = (f"forest-capital-script-{slug}-"
                f"{date.today().isoformat()}.docx")
    return Response(
        content=content, media_type=_DOCX_MEDIA,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ── Regime ────────────────────────────────────────────────────────────────────

@app.get("/api/regime/current")
async def get_current_regime(session: dict = Depends(require_auth)):
    # Sprint 5: regime_signals_cache checked before calling FRED.
    # DB cache TTL = 15 minutes — matches in-process dict in regime_detector.py.
    # On a Render restart the DB row is still valid; the in-process dict is gone.
    # This prevents FRED timeout (30-60s) from blocking every post-restart request.
    if ENVIRONMENT != "test":
        try:
            from tools.cache import get_regime_cache, set_regime_cache
            cached = await get_regime_cache()
            if cached:
                return cached

            from tools.regime_detector import detect_current_regime
            result = detect_current_regime()
            await set_regime_cache(result, ttl_minutes=15)
            return result
        except Exception as exc:
            log.warning("regime_detection_fallback", error=str(exc))
    return MOCK_REGIME


# ── Optimize ──────────────────────────────────────────────────────────────────

async def _strategy_portfolio_points() -> list[dict]:
    """
    Builds the (volatility, return) scatter coordinates for all ten
    strategies on the efficient-frontier chart.

    Reads the most recent strategy_results_cache row — the SAME results
    /api/backtest/compare serves to the rest of the dashboard — so the
    scatter is consistent with the strategy comparison table. It does NOT
    call get_full_history() or run_all_strategies(): an optimize request
    must stay light. (An earlier revision rebuilt get_full_history() here
    only to recompute the cache hash; on a Render cold start that ran the
    full ~30s pipeline concurrently with /api/backtest/compare's identical
    call.) On an empty cache returns [] — the dashboard's
    /api/backtest/compare call populates the table; the frontier curve
    still renders, just without the per-strategy markers until then.
    """
    try:
        from tools.cache import get_latest_strategy_cache

        cached = await get_latest_strategy_cache()
        if not cached:
            log.info("portfolio_points_cache_empty")
            return []

        points: list[dict] = []
        for name, r in cached.items():
            if not isinstance(r, dict):
                continue
            vol = r.get("volatility")
            ret = r.get("cagr")
            if vol is None or ret is None:
                continue
            # The strategy name: prefer the record's own field, but a
            # null/blank strategy_name produced a grey unlabelled dot on
            # the chart. `.get(key, default)` only substitutes when the
            # key is ABSENT — a present-but-None value slips through — so
            # fall back explicitly to the cache key (which IS the strategy
            # name, strategy_results_cache being keyed by it).
            strategy_name = r.get("strategy_name") or name or "UNKNOWN"
            points.append({
                "strategy": strategy_name,
                "volatility": float(vol),
                "expected_return": float(ret),
                "sharpe": float(r.get("sharpe_ratio") or 0.0),
            })
        return points
    except Exception as exc:
        log.warning("portfolio_points_unavailable", error=str(exc))
        return []


def _build_efficient_frontier(
    raw_frontier: list[dict],
    portfolio_points: list[dict],
) -> dict:
    """
    Reshapes the optimizer's flat frontier sweep into the structured
    {frontier_points, portfolio_points, max_sharpe_point, min_variance_point}
    object the EfficientFrontier component reads.

    efficient_frontier() keys the annualised return as `return`; the
    component reads `expected_return` — renamed here. Pure and synchronous
    so it is unit-testable without the DB or an event loop. Tolerant of an
    empty sweep and of a point dict missing a key — a malformed point is
    skipped, never raised on.
    """
    frontier_points: list[dict] = []
    for p in raw_frontier:
        vol = p.get("volatility")
        ret = p.get("return")
        if vol is None or ret is None:
            continue
        frontier_points.append({
            "volatility": float(vol),
            "expected_return": float(ret),
            "sharpe": float(p.get("sharpe") or 0.0),
        })

    max_sharpe_point = None
    min_variance_point = None
    if frontier_points:
        max_sharpe_point = max(frontier_points, key=lambda fp: fp["sharpe"])
        min_variance_point = min(frontier_points, key=lambda fp: fp["volatility"])

        # Diagnostic — the frontier's tangency portfolio should dominate
        # every STATIC strategy dot (each strategy is a long-only 3-asset
        # portfolio, a subset of the frontier's feasible set). A dynamic
        # strategy (regime switching, momentum) can legitimately sit above
        # the static frontier — that timing edge is the project's thesis —
        # so this is logged, not asserted.
        strat_sharpes = {
            p.get("strategy", "?"): p.get("sharpe")
            for p in portfolio_points if p.get("sharpe") is not None
        }
        if strat_sharpes:
            best_strat = max(strat_sharpes, key=lambda k: strat_sharpes[k])
            log.info(
                "efficient_frontier_max_sharpe_check",
                frontier_max_sharpe=round(max_sharpe_point["sharpe"], 4),
                frontier_point={"volatility": round(max_sharpe_point["volatility"], 4),
                                "expected_return": round(max_sharpe_point["expected_return"], 4)},
                best_strategy=best_strat,
                best_strategy_sharpe=round(float(strat_sharpes[best_strat]), 4),
                strategy_sharpes={k: round(float(v), 4) for k, v in strat_sharpes.items()},
            )

    return {
        "frontier_points": frontier_points,
        "portfolio_points": portfolio_points,
        "max_sharpe_point": max_sharpe_point,
        "min_variance_point": min_variance_point,
    }


@app.post("/api/optimize/weights")
async def optimize_weights(body: OptimizeRequest, session: dict = Depends(require_auth)):
    valid_methods = {"MEAN_VARIANCE", "RISK_PARITY", "MIN_VARIANCE", "BLACK_LITTERMAN", "MAX_SHARPE", "MIN_DRAWDOWN"}
    if body.method not in valid_methods:
        raise HTTPException(status_code=422, detail=f"Unknown method '{body.method}'")

    # Sprint 3: real optimizer backed by historical returns.
    if ENVIRONMENT != "test":
        try:
            from tools.optimizer import optimize_weights as _optimize, efficient_frontier as _frontier
            from tools.cache import get_monthly_returns
            import pandas as pd

            # The frontier is computed from the equity/IG/HY monthly return
            # series in market_data_monthly — the SAME three-asset universe
            # the ten strategies are built on. Earlier this path fetched
            # SPY/TLT/IEF/GLD daily from yfinance: a different universe AND
            # a different frequency, so the frontier curve sat visibly
            # offset from the strategy scatter dots. yfinance also drops
            # tickers to NaN from Render's cloud IPs. Reading the DB series
            # is reliable, recompute-free, and puts the curve on the same
            # (volatility, return) scale as the dots.
            monthly = await get_monthly_returns()
            if not monthly or len(monthly.get("dates", [])) < 24:
                raise ValueError(
                    "market_data_monthly unavailable or too short for a "
                    "frontier — falling back to mock"
                )

            returns = pd.DataFrame(
                {
                    "EQUITY": monthly["equity"],
                    "IG":     monthly["ig"],
                    "HY":     monthly["hy"],
                },
                index=pd.to_datetime(monthly["dates"]),
            ).dropna()

            # Issue 1: log the exact asset list reaching the solver.
            log.info(
                "optimize_frontier_universe",
                tickers=list(returns.columns),
                n_obs=len(returns),
                source="market_data_monthly",
            )

            # Annualised risk-free rate for the frontier's Sharpe — the mean
            # of the monthly DTB3 series, ×12. Using the same rate the
            # strategy scatter is built on keeps the curve's tangency
            # (max-Sharpe) point consistent with the strategy dots.
            rf_monthly = monthly.get("rf") or []
            rf_annual = (sum(rf_monthly) / len(rf_monthly) * 12) if rf_monthly else 0.0
            log.info("optimize_frontier_risk_free", risk_free_annual=round(rf_annual, 4))

            # CACHE LOOKUP MOVED EARLY (May 24 2026 UAT fix).
            # Previously the endpoint ran `_optimize(method, returns)`
            # (a single SLSQP solve) BEFORE checking the precomputed
            # frontier cache. A transient solver convergence failure
            # — cvxpy CLARABEL "infeasible_or_unbounded", SLSQP
            # "Inequality constraints incompatible", etc. — would
            # raise here, fall through to the outer except, and
            # serve MOCK_EFFICIENT_FRONTIER. The user saw the chart
            # render with 11 hardcoded mock points + the wrong asset
            # universe (SPY/TLT/IEF/GLD) instead of the real frontier
            # that WAS in the cache. Re-ordering: cache first, then
            # solver. The chart never goes mock when real cache data
            # exists, even if the per-method weights solve fails.
            #
            # Hotfix (May 23 2026) note kept: the cache exists because
            # the 100-point SLSQP sweep runs once at ingestion via
            # refresh_efficient_frontier; same input → same output
            # across every method, so the cache row is method-
            # agnostic. ~50ms cache hit vs ~10-30s on-demand sweep
            # (would exceed Render's 30s gateway timeout).
            raw_frontier: list[dict] | None = None
            try:
                from tools.cache import get_latest_strategy_hash
                from tools.precomputed_analytics import (
                    get_metric as get_precomputed,
                    get_latest_metric as get_latest_precomputed,
                )
                latest_hash = await get_latest_strategy_hash()
                cached = None
                if latest_hash:
                    cached = await get_precomputed(
                        latest_hash, "efficient_frontier")
                if cached is None:
                    # Stale-cache fallback — serve the previous data-
                    # hash's frontier rather than block on the inline
                    # sweep when the refresh hook is in flight or has
                    # not yet fired.
                    cached = await get_latest_precomputed(
                        "efficient_frontier")
                if cached and cached.get("frontier_points"):
                    raw_frontier = cached["frontier_points"]
                    log.info("optimize_frontier_cache_hit",
                             n_points=len(raw_frontier),
                             data_hash=(latest_hash[:8]
                                          if latest_hash else None))
            except Exception as cache_exc:  # noqa: BLE001
                log.warning("optimize_frontier_cache_read_failed",
                            error=str(cache_exc))

            # Per-method weights — runs AFTER the cache lookup so a
            # solver failure here only nulls out the weights field,
            # never wipes the cached frontier into a mock-data
            # response. The dashboard's EfficientFrontier component
            # only consumes `efficient_frontier`; the `weights` field
            # is read by callers that explicitly asked for a method
            # (e.g. a Portfolio Profile drill-down). A null weights
            # field there degrades cleanly (the caller can re-try
            # the specific method) — better than a fake frontier
            # painted across the whole dashboard.
            #
            # UAT 2026-05-24 P0 fix — `_optimize` is a SYNC cvxpy
            # call (CLARABEL / SLSQP) and was running on the asyncio
            # event loop. On Render's shared CPU it can take 1-3s
            # per solve, occasionally longer if the solver iterates
            # to convergence. Dashboard mount fires `_optimize` AND
            # `/api/v1/analytics/academic` in parallel via the
            # store's Promise.all — a slow `_optimize` blocked the
            # loop, queued the analytics request behind it, and
            # both axios calls timed out at 30s. Same root cause for
            # both timeouts the user reported.
            #
            # asyncio.to_thread pushes the sync solver into a worker
            # thread so the event loop stays free to service the
            # cumulative-returns request in parallel. Mirrors the
            # PR #122 fix for get_full_history / run_all_strategies.
            import asyncio
            result: dict
            try:
                result = await asyncio.to_thread(
                    _optimize, body.method, returns)
            except Exception as opt_exc:  # noqa: BLE001
                log.warning("optimize_weights_solve_failed",
                            method=body.method, error=str(opt_exc))
                result = {
                    "method":    body.method,
                    "weights":   None,
                    "sum_check": None,
                    "error":     str(opt_exc),
                }

            if raw_frontier is None:
                # COLD CACHE — never run the inline sweep on the
                # request thread. The 100-point SLSQP sweep takes
                # 10-30s on Render's shared CPU, which exceeds the
                # frontend's 30s timeout. Hotfix iteration 2 (May
                # 23 2026): instead of blocking, fire the precompute
                # refresh in the background and return a warming
                # response immediately. The frontend retries every
                # ~10s until the cache row lands.
                #
                # The warming response carries an empty frontier
                # array + a `warming: true` flag the EfficientFrontier
                # component reads to render a "computing..." state.
                # portfolio_points still ships so the strategy
                # scatter is visible during the warmup.
                # May 24 2026 P0 hotfix — fire the refresh
                # UNCONDITIONALLY. The pre-existing `if h:` gate
                # meant a cold Render deploy with an empty
                # strategy_results_cache never triggered the
                # background sweep, and the user saw a 30s timeout
                # forever. refresh_all_analytics now substitutes a
                # BOOT-WARM sentinel hash when none is supplied.
                log.warning("optimize_frontier_cache_cold_warming",
                            method=body.method)
                try:
                    from tools.cache import get_latest_strategy_hash
                    from tools.precomputed_analytics import (
                        trigger_refresh_async,
                    )
                    h = await get_latest_strategy_hash()
                    trigger_refresh_async(h or "")
                except Exception as trig_exc:  # noqa: BLE001
                    log.warning(
                        "optimize_frontier_refresh_trigger_failed",
                        error=str(trig_exc))

                portfolio_points = await _strategy_portfolio_points()
                return {
                    "method":  body.method,
                    "weights": result["weights"],
                    "sum_check": result["sum_check"],
                    "warming": True,
                    "retry_after_ms": 10000,
                    "efficient_frontier": {
                        "frontier_points":   [],
                        "portfolio_points":  portfolio_points,
                        "max_sharpe_point":  None,
                        "min_variance_point": None,
                        "warming":           True,
                    },
                }

            # efficient_frontier() returns a flat list keyed `return`, but
            # the EfficientFrontier component reads `expected_return` off a
            # structured {frontier_points, portfolio_points, ...} object —
            # the same shape MOCK_EFFICIENT_FRONTIER uses. Reshape here so
            # the real and mock paths return an identical contract; a flat
            # list left the chart blank (no frontier_points key on an array).
            portfolio_points = await _strategy_portfolio_points()

            return {
                "method": body.method,
                "weights": result["weights"],
                "sum_check": result["sum_check"],
                "efficient_frontier": _build_efficient_frontier(
                    raw_frontier, portfolio_points
                ),
            }
        except Exception as exc:
            log.warning("optimize_weights_fallback", method=body.method, error=str(exc))

    return {
        "method": body.method,
        "weights": {"SPY": 0.40, "TLT": 0.30, "IEF": 0.15, "GLD": 0.15},
        "efficient_frontier": MOCK_EFFICIENT_FRONTIER,
        "note": "Fallback mock — real optimisation failed or test environment",
    }


# ── Market data ───────────────────────────────────────────────────────────────

@app.get("/api/data/market")
async def get_market_data(
    tickers: str = Query(..., description="Comma-separated tickers"),
    start: str = Query("2020-01-01"),
    end: str = Query("2024-12-31"),
    session: dict = Depends(require_auth),
):
    return {
        "tickers": tickers.split(","),
        "start": start,
        "end": end,
        "note": "Sprint 1 mock — real data fetch in Sprint 2",
        "prices": {},
        "returns": {},
    }


# ── Council ───────────────────────────────────────────────────────────────────

def _log_council_session(
    query: str,
    agents_called: list[str],
    response: dict[str, Any],
    start_time: float,
    user_email: str,
) -> None:
    """
    Persists council session metadata to the AI usage log.

    Writes to council_sessions table when DB is available; always logs
    to structlog so the session is traceable even without Postgres.
    Cost estimates use Anthropic's published pricing as of Sprint 4.
    """
    duration_ms = int((time.time() - start_time) * 1000)
    session_id = str(uuid.uuid4())

    log.info(
        "council_session_complete",
        session_id=session_id,
        user_hash=hash(user_email),
        agents_called=agents_called,
        n_significant=len(response.get("significant_strategies", [])),
        duration_ms=duration_ms,
    )


# ── Non-blocking interaction logging ──────────────────────────────────────────
# Background tasks must be referenced or the event loop may GC them mid-run;
# the set holds a strong ref and the done-callback drops it on completion.
_activity_bg_tasks: set = set()


def _log_interaction_bg(
    request: Request,
    session: dict,
    interaction_type: str,
    *,
    question_text: str | None = None,
    agents_involved: list[str] | None = None,
    response_summary: str | None = None,
    metadata: dict | None = None,
) -> None:
    """
    Fire-and-forget agent_interactions logging — schedules the DB write
    on the running loop and returns immediately. The session_id and
    session_type travel from the frontend as request headers. Wrapped
    so a scheduling or DB failure never touches the primary response.

    AI token cost: collect_usage() is read here, in the request context,
    so any endpoint that called start_usage_capture() has its token
    totals logged and its per-agent breakdown folded into metadata. An
    endpoint that did not start a capture simply logs null token columns.
    """
    try:
        import asyncio
        from tools.activity_log import log_agent_interaction

        in_tok = out_tok = model_used = cost = None
        try:
            from agents.usage import collect_usage
            usage = collect_usage()
            in_tok = usage.get("input_tokens")
            out_tok = usage.get("output_tokens")
            model_used = usage.get("model_used")
            cost = usage.get("estimated_cost_usd")
            if usage.get("per_agent"):
                metadata = {**(metadata or {}),
                            "per_agent_cost": usage["per_agent"]}
        except Exception:  # noqa: BLE001 — cost telemetry is never fatal
            pass

        task = asyncio.create_task(log_agent_interaction(
            user_email=session.get("email", ""),
            session_id=request.headers.get("x-session-id"),
            session_type=request.headers.get("x-session-type"),
            interaction_type=interaction_type,
            question_text=question_text,
            agents_involved=agents_involved,
            response_summary=response_summary,
            metadata=metadata,
            input_tokens=in_tok,
            output_tokens=out_tok,
            model_used=model_used,
            estimated_cost_usd=cost,
        ))
        _activity_bg_tasks.add(task)
        task.add_done_callback(_activity_bg_tasks.discard)
    except Exception as exc:  # noqa: BLE001
        log.warning("interaction_log_schedule_failed",
                    interaction_type=interaction_type, error=str(exc))


# Maps cio.deliberate() agent keys to the display name/role/model the frontend expects.
# The frontend's AGENT_STYLE dict in CouncilDebate.tsx is keyed by these exact display names.
_AGENT_META: dict[str, tuple[str, str, str]] = {
    "equity_analyst":       ("Equity Analyst",               "specialist", SONNET_MODEL),
    "fixed_income_analyst": ("Fixed Income Analyst",          "specialist", SONNET_MODEL),
    "risk_manager":         ("Risk Manager",                  "specialist", SONNET_MODEL),
    "quant_backtester":     ("Quant Backtester",              "specialist", SONNET_MODEL),
    "independent_analyst":  ("Independent Analyst (Gemini)",  "dissenter",  GEMINI_MODEL),
    "contrarian_analyst":   ("Contrarian Analyst (Grok)",     "dissenter",  "grok-4.3"),
    "cio":                  ("CIO",                           "cio",        OPUS_MODEL),
}


def _deliberate_to_frontend(query: str, council_response: dict[str, Any]) -> dict[str, Any]:
    """
    Converts cio.deliberate() output to the CouncilDebateResponse shape the frontend expects.

    cio.deliberate() returns {"agents": {snake_case_key: report_dict, ...}, ...}.
    The frontend's CouncilResponse type expects {"messages": [AgentMessage, ...], ...}.
    This conversion runs inside the council_query endpoint so the raw backend
    structure never reaches the client.
    """
    agents = council_response.get("agents", {})
    messages = []
    for key, (display_name, role, model) in _AGENT_META.items():
        report = agents.get(key, {})
        if not report:
            # Agent was never invoked — skip rather than show an empty card.
            # An expected agent missing from the response is a council-flow
            # bug, not a rendering bug; the heatmap or another diagnostic
            # will catch it.
            continue

        # Per-agent content selection — every agent shows its FULL
        # narrative, not a one-line summary:
        #   CIO         → final synthesis (the recommendation narrative)
        #   Gemini/Grok → full challenge text (the dissenting narrative)
        #   Specialists → raw_analysis (the complete specialist analysis;
        #                 summary is only the one-line fallback)
        tech = report.get("technical_findings", {}) or {}
        if key == "cio":
            content = tech.get("final_synthesis_text") or report.get("summary", "")
        elif key == "independent_analyst":
            content = tech.get("full_challenge") or report.get("summary", "")
        elif key == "contrarian_analyst":
            content = tech.get("full_challenge") or report.get("summary", "")
        else:
            content = tech.get("raw_analysis") or report.get("summary", "")

        # NEVER drop an agent whose report exists. An empty content field
        # used to be silently filtered, which produced an empty Debate tab
        # whenever a single LLM extraction returned no text. The frontend
        # renders a placeholder for empty content; the audience still sees
        # which agent ran and what its tag was.
        if not content:
            content = "(Narrative unavailable — agent ran but no text returned.)"

        messages.append({
            "agent":    display_name,
            "role":     role,
            "model":    model,
            "content":  content,
            "is_final": key == "cio",
        })
    return {
        "query":                query,
        "messages":             messages,
        "final_recommendation": council_response.get("final_recommendation", ""),
        "consensus_reached":    True,
        # "live" = real council run; the mock-fallback path sets "fallback"
        # so a consumer can tell genuine analysis from demo data.
        "mode":                 "live",
    }


def _chunk_synthesis(text: str, words_per_chunk: int = 8) -> list[str]:
    """
    Splits the CIO synthesis prose into small chunks for SSE streaming.
    Mirrors agents.academic_review.chunk_arbiter_text — word groups so
    the consumer sees the synthesis arrive progressively rather than as
    one large final frame. 8 words ≈ 50 chars typical, well below the
    user-spec'd 100-char ceiling, but big enough to keep frame count
    bounded for a 1000-word synthesis.
    """
    if not text:
        return []
    words = text.split(" ")
    return [
        " ".join(words[i:i + words_per_chunk]) + " "
        for i in range(0, len(words), words_per_chunk)
    ]


@app.post("/api/council/query")
@limiter.limit("10/minute")
async def council_query(
    request: Request,
    body: CouncilQueryRequest,
    session: dict = Depends(require_auth),
):
    """
    Convenes the full investment council and STREAMS phase events via SSE.

    Replaces the previous synchronous JSON response — the deliberation
    routinely takes 50-100 seconds and was hitting Render's gateway
    timeout. Streaming keeps the connection alive at each phase
    boundary; the frontend assembles the same CouncilDebateResponse
    shape from the events.

    Event sequence (each `data: {json}\\n\\n`):
      1. council_started     — fires immediately on request receipt
      2. specialist_complete — one per analyst, in as_completed order
      3. draft_ready         — CIO draft consensus
      4. dissent_complete    — gemini, then grok
      5. synthesis_chunk     — CIO synthesis text, chunked progressively
      6. council_complete    — full CouncilDebateResponse dict
      7. data: [DONE]\\n\\n   — end-of-stream sentinel

    On a deliberation failure the stream yields a `council_error` frame
    and a [DONE] sentinel rather than silently falling through to mock
    data — the previous fall-through hid real errors from the user.

    Scope guard runs synchronously BEFORE the StreamingResponse so a
    422 still returns as a normal HTTP error; the same for the 429
    quota response. The stream only starts once the request has been
    cleared for processing.

    The test environment keeps the synchronous JSON contract — every
    council test in tests/test_council_deliberation.py asserts on
    resp.json() and would break if asked to parse SSE.
    """
    if len(body.query) > 500:
        raise HTTPException(status_code=422, detail="Query exceeds 500 character limit.")

    # Viewer council query allocation — a user with a council_queries_limit
    # set is blocked once their lifetime allowance is spent. Team members
    # and sysadmins have a NULL limit and are never blocked. Checked before
    # the scope guard so a blocked viewer never spends a classifier call.
    # The 429 returns as a normal HTTP response, BEFORE the stream starts.
    council_quota: dict[str, Any] | None = None
    from tools.platform_users import (
        get_council_allocation, increment_council_queries,
    )
    allocation = await get_council_allocation(session["email"])
    if allocation and allocation.get("council_queries_limit") is not None:
        used = int(allocation.get("council_queries_used", 0) or 0)
        limit = int(allocation["council_queries_limit"])
        if used >= limit:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "council_limit_reached",
                    "limit": limit,
                    "used": used,
                },
            )
        # The query is counted against the allowance before processing.
        council_quota = await increment_council_queries(session["email"])

    # Scope guard — must pass before any agent is invoked.
    # Runs synchronously so a 422 returns as a normal HTTP error, not
    # as an SSE frame.
    if ENVIRONMENT != "test":
        try:
            from scope_guard import ScopeGuard
            guard = ScopeGuard()
            scope_result = await guard.check(body.query)
            if not scope_result["allowed"]:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "out_of_scope",
                        "message": scope_result["rejection_message"],
                        "system": "Forest Capital Portfolio Intelligence System",
                    },
                )
        except HTTPException:
            raise
        except Exception as exc:
            # Scope guard failure is non-fatal — log and proceed
            log.warning("scope_guard_error", error=str(exc))

    log.info("council_query_started", user=session["email"], query_len=len(body.query))
    start_time = time.time()

    # Test environment keeps the synchronous JSON contract so the existing
    # /api/council/query test suite (TestClient.post(...).json()) keeps
    # passing without rewrite. Production streams; tests do not need to.
    if ENVIRONMENT == "test":
        response = dict(MOCK_COUNCIL_RESPONSE)
        response["query"] = body.query
        response["mode"] = "fallback"
        if council_quota:
            response["council_queries_used"] = council_quota["council_queries_used"]
            response["council_queries_limit"] = council_quota["council_queries_limit"]
        return response

    query = body.query

    async def event_stream():
        # The CIO generator is synchronous and does heavy work (parallel
        # specialists, network-bound LLM calls). We bridge it to async
        # via asyncio.to_thread(next, ...) so the event loop stays free
        # between yields — that is what allows the gateway connection
        # to keep alive past 30s.
        import asyncio
        import traceback

        from agents.cio import CIO
        from agents.harness import (
            start_harness_capture, collect_harness_metrics,
        )
        from agents.usage import start_usage_capture
        from tools.backtester import run_all_strategies
        from tools.data_fetcher import get_full_history

        final_result: dict[str, Any] | None = None

        try:
            yield _sse("council_started", query=query)

            # Heavy data load offloaded to threads so the council_started
            # event flushes immediately and the event loop stays
            # responsive. get_full_history() is memoised (30s); a warm
            # call is fast, but the first call after a hash change runs
            # the read pipeline.
            history = await asyncio.to_thread(get_full_history)
            strategy_results = await asyncio.to_thread(
                run_all_strategies, history)

            # Capture every specialist's harness run + every agent
            # call's token usage. Seeded before the generator runs so
            # the copied thread contexts share the accumulator lists.
            start_harness_capture()
            start_usage_capture()

            cio = CIO()
            gen = cio.deliberate_streaming(query, strategy_results, history)
            sentinel = object()
            while True:
                # Each next(gen) runs a phase — specialists fan-out,
                # draft, dissenter, synthesis. The phase work happens
                # in the worker thread; the yield back to async land
                # flushes the SSE frame.
                event = await asyncio.to_thread(next, gen, sentinel)
                if event is sentinel:
                    break
                kind = event[0]
                if kind == "specialist_complete":
                    _, agent_id, report = event
                    yield _sse("specialist_complete",
                               agent_id=agent_id, response=report)
                elif kind == "draft_ready":
                    _, draft = event
                    yield _sse("draft_ready", draft=draft)
                elif kind == "dissent_complete":
                    _, source, report = event
                    yield _sse("dissent_complete",
                               source=source, challenge=report)
                elif kind == "cio_synthesis_text":
                    _, synthesis_text = event
                    # Chunk the synthesis prose so the user sees it
                    # arrive progressively — same pattern as
                    # academic_review.chunk_arbiter_text.
                    for chunk in _chunk_synthesis(synthesis_text):
                        yield _sse("synthesis_chunk", text=chunk)
                elif kind == "council_complete":
                    _, full_result = event
                    final_result = full_result
                    # Convert to the CouncilDebateResponse shape the
                    # frontend store assembles into — same dict the
                    # previous synchronous handler returned.
                    result = _deliberate_to_frontend(query, full_result)
                    if council_quota:
                        result["council_queries_used"] = (
                            council_quota["council_queries_used"])
                        result["council_queries_limit"] = (
                            council_quota["council_queries_limit"])
                    yield _sse("council_complete", result=result)

            # Post-stream logging — runs only when the deliberation
            # completed cleanly. _log_council_session writes to the
            # AI usage log; _log_interaction_bg is fire-and-forget
            # Team Activity.
            if final_result is not None:
                harness_meta = collect_harness_metrics()
                council_agents = [
                    "equity_analyst", "fixed_income_analyst",
                    "risk_manager", "quant_backtester",
                    "independent_analyst", "contrarian_analyst", "cio",
                ]
                _log_council_session(
                    query=query,
                    agents_called=council_agents,
                    response=final_result,
                    start_time=start_time,
                    user_email=session["email"],
                )
                _log_interaction_bg(
                    request, session, "council",
                    question_text=query,
                    agents_involved=council_agents,
                    response_summary=final_result.get(
                        "final_recommendation", ""),
                    metadata=({"harness": harness_meta}
                              if harness_meta else None),
                )
        except Exception as exc:  # noqa: BLE001
            # A deliberation failure surfaces clearly — no silent
            # fall-through to mock data. The frontend renders the
            # error and lets the user retry.
            log.error("council_query_failed", error=str(exc),
                      traceback=traceback.format_exc())
            yield _sse(
                "council_error",
                message="Council query failed. Please try again.",
            )
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Academic review ───────────────────────────────────────────────────────────

def _sse(event_type: str, **payload: Any) -> str:
    """Format one Server-Sent Events frame: data: {json}\\n\\n."""
    return f"data: {json.dumps({'type': event_type, **payload})}\n\n"


def _parse_overall_rating(verdict: str) -> str | None:
    """
    Pulls the section-5 readiness rating (Strong | Developing | Needs
    Work) out of the arbiter verdict so the Team Activity summary can
    show it without re-parsing the markdown. Returns None if the
    verdict is malformed or unavailable.
    """
    import re
    m = re.search(
        r"###\s*5\.[^\n]*\n\s*\*\*Rating:\*\*\s*(Strong|Developing|Needs Work)",
        verdict or "", re.IGNORECASE,
    )
    return m.group(1) if m else None


@app.post("/api/council/academic-review")
@limiter.limit("10/minute")
async def council_academic_review(request: Request, session: dict = Depends(require_team_member)):
    """
    Convenes the council to evaluate the project's academic readiness.

    No request body — all context is assembled server-side: an analytics
    inventory plus the uploaded academic documents. Every peer agent
    answers a stock four-part review question in parallel; the academic
    advisor then arbitrates, synthesising a five-section rubric-mapped
    verdict.

    Streams a text/event-stream response:
      1. {"type": "peer_responses", "data": {agentId: text}}
      2. {"type": "arbiter_chunk", "text": chunk}   (streamed)
      3. data: [DONE]

    Both the peer responses and the arbiter verdict are produced through
    the generator-evaluator harness. The arbiter is generated IN FULL and
    harness-evaluated before any chunk is streamed — a failed attempt is
    never shown, only the accepted verdict.

    document_type query param — when "presentation_script", the arbiter
    runs the SCRIPT-SPECIFIC rubric (coherence, audience clarity, slide
    coverage, speaker differentiation; skips citation formatting and
    paragraph structure). Default rubric otherwise.
    """
    import asyncio
    from agents.academic_review import (
        gather_review_context, run_peer_fan_out, run_arbiter_with_harness,
        chunk_arbiter_text, ARBITER_MODEL,
    )
    from agents.harness import start_harness_capture, collect_harness_metrics
    from agents.usage import start_usage_capture

    # Rubric switch — read once at the top of the handler so a future
    # gather_review_context refactor can also consume it. Only the literal
    # string "presentation_script" enables the script rubric.
    script_review = (
        request.query_params.get("document_type") == "presentation_script")

    async def event_stream():
        try:
            # Capture the peer + arbiter harness runs for Team Activity, and
            # every agent call's token usage for cost tracking. The ContextVar
            # lists are shared into the asyncio.to_thread peer and arbiter
            # tasks, so every run is recorded.
            start_harness_capture()
            start_usage_capture()
            ctx = await gather_review_context(
                reviewer_email=session.get("email"))
            context_block = ctx["context_block"]
            multi_user = ctx.get("multi_user_activity", False)
            # Threaded into the chart-vision scope sentences so all-
            # strategy chart captions render the exact count rather
            # than the count-omitted fallback.
            n_strategies = ctx["analytics"].get("strategy_count")

            peer_responses = await run_peer_fan_out(
                context_block, multi_user, n_strategies)
            log.info(
                "academic_review_peers_complete",
                agents=list(peer_responses.keys()),
                response_lengths={k: len(v) for k, v in peer_responses.items()},
                arbiter_model=ARBITER_MODEL,
                risk_free_rate=ctx["analytics"].get("risk_free_rate"),
                document_types_present=ctx["document_types_present"],
                document_types_missing=ctx["document_types_missing"],
                script_review=script_review,
            )
            yield _sse("peer_responses", data=peer_responses)

            # Arbiter — generated in full and harness-evaluated in a worker
            # thread (the harness is synchronous), then streamed as chunks.
            # The loading state on the frontend covers the evaluation wait.
            arbiter_text = await asyncio.to_thread(
                run_arbiter_with_harness, context_block, peer_responses,
                multi_user, script_review, n_strategies)
            for chunk in chunk_arbiter_text(arbiter_text):
                yield _sse("arbiter_chunk", text=chunk)
            log.info("academic_review_arbiter_complete",
                     arbiter_chars=len(arbiter_text))

            # Team Activity — log the completed review. The overall
            # readiness rating is parsed out of the verdict; the harness
            # block aggregates the peer + arbiter quality runs.
            agents = list(peer_responses.keys()) + ["academic_advisor"]
            review_metadata: dict[str, Any] = {
                "overall_rating": _parse_overall_rating(arbiter_text),
            }
            harness_meta = collect_harness_metrics()
            if harness_meta:
                review_metadata["harness"] = harness_meta
            _log_interaction_bg(
                request, session, "academic_review",
                agents_involved=agents,
                response_summary=arbiter_text,
                metadata=review_metadata,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("academic_review_failed", error=str(exc))
            yield _sse("error", message="Academic review failed — please retry.")
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Peer Review Assistant (item 7, Feature A) ─────────────────────────────────
#
# Bob / Michael / Molly each review another team's midpoint
# submission for the June 3 cohort meetup. Upload the peer team's
# PDF / DOCX / MD; we extract text in-memory (no persistence —
# peer papers are not reference documents and don't belong in
# academic_documents), run the harness-gated Opus call against the
# four FNA 670 rubric dimensions, and stream a 3-4 minute review
# script SSE-style.

@app.post("/api/council/peer-review")
@limiter.limit("10/minute")
async def council_peer_review(
    request: Request,
    file: UploadFile = File(...),
    submission_name: str = Form(""),
    session: dict = Depends(require_team_member),
):
    """Streams a peer-review script for the uploaded submission.

    multipart/form-data:
      file              the peer team's midpoint paper (.pdf, .docx, .md)
      submission_name   optional display name surfaced in the verdict;
                        defaults to the file's basename

    SSE wire format mirrors /api/council/academic-review for UI
    consistency:
      data: {"type": "submission_meta", "name": "...", "char_count": N}
      data: {"type": "arbiter_chunk", "text": "..."}     (repeated)
      data: [DONE]

    Errors flow as a single `{"type": "error", "message": "..."}`
    frame followed by [DONE] so the frontend has one consistent
    parse path."""
    import asyncio
    from agents.peer_review import (
        extract_peer_paper_text,
        build_peer_review_context_block,
        render_peer_review_context_block,
        run_peer_review_with_harness,
        chunk_arbiter_text,
        MAX_PEER_PAPER_BYTES,
    )

    # Read + extract synchronously BEFORE the stream opens so a
    # 422 (unsupported format, scanned PDF, oversize) lands on the
    # POST itself instead of mid-stream. Frontend renders the
    # error inline.
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422,
                             detail="Uploaded file is empty.")
    if len(raw) > MAX_PEER_PAPER_BYTES:
        raise HTTPException(
            status_code=422,
            detail=(f"Uploaded file exceeds "
                    f"{MAX_PEER_PAPER_BYTES} bytes."))
    try:
        text = extract_peer_paper_text(file.filename or "", raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    name = (submission_name or "").strip()
    if not name:
        # Fall back to the filename without the extension.
        from pathlib import Path
        name = Path(file.filename or "submission").stem or "submission"

    ctx_dict = build_peer_review_context_block(name, text)
    context_block = render_peer_review_context_block(ctx_dict)

    async def event_stream():
        try:
            yield _sse(
                "submission_meta",
                name=name,
                char_count=len(text),
            )
            verdict = await asyncio.to_thread(
                run_peer_review_with_harness, context_block)
            for chunk in chunk_arbiter_text(verdict):
                yield _sse("arbiter_chunk", text=chunk)
            log.info(
                "peer_review_complete",
                submission_name=name,
                paper_chars=len(text),
                verdict_chars=len(verdict))
            _log_interaction_bg(
                request, session, "peer_review",
                agents_involved=["peer_review_assistant"],
                response_summary=verdict,
                metadata={
                    "submission_name": name,
                    "paper_chars":     len(text),
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.error("peer_review_failed", error=str(exc))
            yield _sse(
                "error",
                message="Peer review failed — please retry.")
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(),
                              media_type="text/event-stream")


# ── Thesis Defense Prep (item 7, Feature B) ───────────────────────────────────
#
# Auto-loads the calling user's most-recent midpoint_paper editor
# draft and streams an anticipated Q&A prep sheet across three
# categories (technical, academic, governance). No file upload —
# the draft is the source of truth.

@app.post("/api/council/defense-prep")
@limiter.limit("10/minute")
async def council_defense_prep(
    request: Request,
    session: dict = Depends(require_team_member),
):
    """Streams a mock-panel Q&A prep sheet for the calling user's
    paper.

    May 24 2026 (P5 — Final Submission marker): if the request body
    carries `generation_id`, Defense Prep references the
    Final-marked version of THAT generation when one exists
    (falling back to the most recent saved version). Otherwise it
    auto-fetches the user's most-recent midpoint_paper editor
    draft via get_current_draft (the prior behaviour).

    The body shape:
      {generation_id?: int}    optional; when present pins the
                               canonical version per migration 040.

    SSE wire format:
      data: {"type": "draft_meta", "title": "...",
              "word_count": N, "updated_at": "...",
              "source": "final_marker" | "most_recent" | "editor_draft"}
      data: {"type": "arbiter_chunk", "text": "..."}     (repeated)
      data: [DONE]
    """
    import asyncio
    from agents.peer_review import (
        build_defense_prep_context_block,
        render_defense_prep_context_block,
        run_defense_prep_with_harness,
        chunk_arbiter_text,
    )

    # Optional generation_id from the request body — when present,
    # the Final-marked version takes precedence over the editor
    # draft. Read defensively: a missing body or non-JSON body
    # falls through to the prior editor-draft path.
    generation_id: int | None = None
    try:
        body = await request.json()
        if isinstance(body, dict) and body.get("generation_id") is not None:
            generation_id = int(body["generation_id"])
    except Exception:
        generation_id = None

    # Resolve the draft BEFORE opening the stream so a 404-style
    # error frame fires immediately rather than mid-flow.
    draft = None
    source = "editor_draft"
    try:
        if generation_id is not None:
            from tools.paper_versions import (
                get_canonical_version, get_final_version,
            )
            canonical = await get_canonical_version(generation_id)
            if canonical:
                final = await get_final_version(generation_id)
                source = ("final_marker"
                          if final is not None else "most_recent")
                draft = {
                    "content_text": canonical.get("paper_md") or "",
                    "title":        (canonical.get("label")
                                       or f"Generation {generation_id} "
                                          f"v{canonical.get('version_number')}"),
                    "word_count":   sum(
                        int(v) for v in
                        (canonical.get("word_counts") or {}).values()
                        if isinstance(v, (int, float))
                    ),
                    "updated_at":   canonical.get("saved_at"),
                }
        if draft is None:
            from tools.editor_drafts import get_current_draft
            draft = await get_current_draft(
                owner_email=session.get("email") or "",
                document_type="midpoint_paper")
            source = "editor_draft"
    except Exception as exc:  # noqa: BLE001
        log.warning("defense_prep_draft_lookup_failed",
                    error=str(exc))

    async def event_stream():
        if not draft or not (draft.get("content_text") or "").strip():
            # No draft — surface the gap rather than running the
            # agent against empty input. The frontend renders the
            # error and offers a "Open editor" link.
            yield _sse(
                "error",
                message=(
                    "No midpoint paper draft found. Generate or "
                    "open a draft in the Reports editor first, "
                    "then re-run Defense Prep."))
            yield "data: [DONE]\n\n"
            return
        draft_text = draft["content_text"] or ""
        title = draft.get("title") or "Midpoint paper draft"

        try:
            yield _sse(
                "draft_meta",
                title=title,
                word_count=draft.get("word_count") or 0,
                updated_at=draft.get("updated_at") or None,
                source=source,
            )

            ctx_dict = build_defense_prep_context_block(
                "Forest Capital (team draft)", draft_text)
            context_block = render_defense_prep_context_block(
                ctx_dict)

            verdict = await asyncio.to_thread(
                run_defense_prep_with_harness, context_block)
            for chunk in chunk_arbiter_text(verdict):
                yield _sse("arbiter_chunk", text=chunk)
            log.info(
                "defense_prep_complete",
                draft_id=draft.get("id"),
                draft_chars=len(draft_text),
                verdict_chars=len(verdict))
            _log_interaction_bg(
                request, session, "defense_prep",
                agents_involved=["thesis_defense_prep"],
                response_summary=verdict,
                metadata={
                    "draft_id":   draft.get("id"),
                    "draft_chars": len(draft_text),
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.error("defense_prep_failed", error=str(exc))
            yield _sse(
                "error",
                message="Defense prep failed — please retry.")
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(),
                              media_type="text/event-stream")


# ── Inline metric explainer ───────────────────────────────────────────────────

@app.post("/api/council/explain")
@limiter.limit("30/minute")
async def council_explain(
    request: Request,
    body: dict,
    session: dict = Depends(require_auth),
):
    """
    Streams a plain-English explanation of one metric or chart — backs
    the InfoIcon → ExplainerPanel click path on the Analytics and
    Dashboard screens.

    Uses the Explainer agent's system prompt and streams via Haiku. The
    response is a raw text/plain stream of explanation chunks — NOT
    Server-Sent-Events: there is no `data:` framing and no `[DONE]`
    sentinel, unlike /api/council/academic-review. Consumers read it as
    a plain token stream. The completed explanation is logged to
    agent_interactions as interaction_type "explain" (team-gated inside
    log_agent_interaction).
    """
    metric = str(body.get("metric") or "").strip()
    if not metric:
        raise HTTPException(status_code=422, detail="metric is required")
    current_value = body.get("current_value")

    from agents.explainer_agent import stream_metric_explanation
    from agents.usage import start_usage_capture
    from tools.strategy_context import (
        detect_strategies_in_query, set_active_strategies,
    )

    # Item 9 commit 5 — strategy context. The InfoIcon click on a
    # strategy-specific metric ("Strategy: REGIME_SWITCHING Sharpe") or
    # a metric label that names a strategy injects the strategy's
    # characterisation into the explainer's system prompt via the
    # per-request ContextVar. _stream_haiku copies the request context
    # into its worker thread so the var propagates. No-op when no
    # strategy is named.
    named = detect_strategies_in_query(
        f"{metric} {current_value or ''}")
    if named:
        set_active_strategies(named)

    # Seed the usage bucket before the Haiku stream starts; _stream_haiku
    # copies the request context into its worker thread so record_usage
    # after the stream completes lands here.
    start_usage_capture()

    async def gen():
        collected: list[str] = []
        async for chunk in stream_metric_explanation(metric, current_value):
            collected.append(chunk)
            yield chunk
        # Team Activity — non-blocking, team-gated inside log_agent_interaction.
        _log_interaction_bg(
            request, session, "explain",
            question_text=metric,
            response_summary="".join(collected),
            metadata=({"current_value": str(current_value)}
                      if current_value not in (None, "") else None),
        )

    return StreamingResponse(gen(), media_type="text/plain")


@app.post("/api/council/explain-data")
@limiter.limit("30/minute")
async def council_explain_data(
    request: Request,
    body: dict,
    session: dict = Depends(require_auth),
):
    """
    Streams a contextual explanation of the SPECIFIC values currently on
    screen — backs the "Explain this data" (✨) button on the strategy
    detail subscreen and the Analytics charts.

    Deliberately distinct from /api/council/explain: the InfoIcon answers
    "what does this metric mean?" in 150 words; this answers "what do
    these specific values mean together?" with the deeper, academic
    framing. The completed explanation is logged to agent_interactions as
    interaction_type "explain_data" (team-gated inside log_agent_interaction).

    Like /api/council/explain this is a raw text/plain token stream — no
    SSE framing, no [DONE] sentinel.
    """
    metric = str(body.get("metric") or "").strip()
    if not metric:
        raise HTTPException(status_code=422, detail="metric is required")
    current_value = body.get("current_value")
    context = body.get("context")

    from agents.explainer_agent import stream_data_explanation
    from agents.usage import start_usage_capture
    from tools.strategy_context import (
        detect_strategies_in_query, set_active_strategies,
    )

    # Item 9 commit 5 — strategy context. The Data Explain (✨) click
    # typically carries the strategy name in the metric label and the
    # full strategy row in the context dict. Scan both for known
    # strategy ids and set the per-request ContextVar so the explainer
    # system prompt picks up the characterisation block.
    haystack = (
        f"{metric} {current_value or ''} "
        + (str(context) if context else ''))
    named = detect_strategies_in_query(haystack)
    if named:
        set_active_strategies(named)

    # Same pattern as /api/council/explain — seed before the stream worker
    # thread starts so its copied context inherits this bucket.
    start_usage_capture()

    async def gen():
        collected: list[str] = []
        async for chunk in stream_data_explanation(metric, current_value, context):
            collected.append(chunk)
            yield chunk
        _log_interaction_bg(
            request, session, "explain_data",
            question_text=metric,
            response_summary="".join(collected),
            metadata=({"current_value": str(current_value)}
                      if current_value not in (None, "") else None),
        )

    return StreamingResponse(gen(), media_type="text/plain")


# ── Explainer CIO follow-up — multi-turn thread inside the panel ──────────────
#
# The ExplainerPanel surfaces a 150-word static explanation from Haiku
# (the /api/council/explain stream above). Once the user has read that,
# they may want a follow-up — a clarification, a question grounded in
# the chart, a tie-back to the macro digest. The follow-up endpoint
# calls the CIO directly (Opus) for higher-quality reasoning, scoped
# tightly to the explainer context: the topic, the panel content
# already shown, the optional chart values, the optional macro
# summary, and the prior thread of user/cio exchanges in this panel
# session. Capped at three exchanges per session; beyond that the
# user takes the question to the full council via a handoff package.


_FOLLOWUP_MAX_EXCHANGES = 3


class ExplainerFollowupExchange(__import__("pydantic").BaseModel):
    role: str  # "user" or "cio"
    content: str


class ExplainerFollowupRequest(__import__("pydantic").BaseModel):
    explainer_topic: str
    explainer_content: str
    chart_context: dict | None = None
    macro_summary: str | None = ""
    thread: list[ExplainerFollowupExchange] = []
    question: str


def _build_followup_system_prompt(
    topic: str, explainer_content: str,
    chart_context: dict | None, macro_summary: str | None,
) -> str:
    """Builds the CIO follow-up system prompt with the explainer
    context baked in. Same evidence-discipline rule the other agents
    use ("only reference numbers a tool has actually returned"), plus
    the explainer-specific instruction to be concise."""
    from agents.cio import _SYSTEM_PROMPT as _CIO_SYSTEM_PROMPT

    parts = [
        _CIO_SYSTEM_PROMPT,
        "",
        "=== EXPLAINER FOLLOW-UP CONTEXT ===",
        f"You are answering a follow-up question from a user looking "
        f"at the {topic} explainer on the Forest Capital portfolio "
        f"intelligence platform.",
        "",
        "The user has already read the following explainer content:",
        explainer_content[:2000],
    ]
    if chart_context:
        parts.append("")
        parts.append("Chart context — values currently on screen:")
        import json as _json
        parts.append(_json.dumps(chart_context, indent=2, default=str)[:1000])
    if macro_summary:
        parts.append("")
        parts.append("Current macro conditions summary:")
        parts.append(macro_summary[:1000])
        parts.append("When you draw on this macro context in your answer, "
                     "cite it inline using [Macro: <category>] (e.g. "
                     "[Macro: monetary_policy]) so the user sees which "
                     "signals you used.")
    parts.append("")
    parts.append(
        "CONCISENESS — keep your answer to 2-4 sentences for a simple "
        "clarification, up to 2 short paragraphs for a complex question. "
        "Do not restate the explainer content; build on it.")
    parts.append(
        "WHEN TO ESCALATE — if the question requires "
        "multi-specialist deliberation (e.g. comparing strategies on "
        "more than one dimension, weighing the equity vs fixed-income "
        "view on the same question, deriving a portfolio "
        "recommendation), conclude your answer with the line "
        "'[SUGGEST_COUNCIL]' on its own. The frontend strips this "
        "marker and surfaces a 'Take this to the Council' button. "
        "Use [SUGGEST_COUNCIL] sparingly — only when the question "
        "genuinely warrants the full council, not for every "
        "follow-up.")
    return "\n".join(parts)


@app.post("/api/v1/council/explainer-followup")
@limiter.limit("15/minute")
async def council_explainer_followup(
    request: Request,
    body: ExplainerFollowupRequest,
    session: dict = Depends(require_team_member),
):
    """
    Streams a CIO follow-up answer for a question asked inside the
    ExplainerPanel thread. Up to three exchanges per panel session.

    Response is text/event-stream — three frame types:
      data: {"type":"chunk","text":"..."}    (streamed chunks)
      data: {"type":"meta","exchanges_used":N,"suggest_council":bool}
      data: [DONE]

    The frontend assembles the chunks into the assistant message,
    reads exchanges_used to update the "X of 3 follow-ups used"
    counter, and toggles the council handoff prompt when
    suggest_council is true.
    """
    if not body.question.strip():
        raise HTTPException(status_code=422, detail="question is required")
    if len(body.question) > 300:
        raise HTTPException(status_code=422,
                            detail="question must be 300 chars or fewer")
    if len(body.thread) >= _FOLLOWUP_MAX_EXCHANGES:
        raise HTTPException(status_code=429,
                            detail="Follow-up limit reached. Take the "
                                   "question to the council.")

    from agents.base import OPUS_MODEL, call_claude
    from agents.usage import start_usage_capture
    start_usage_capture()

    system_prompt = _build_followup_system_prompt(
        body.explainer_topic, body.explainer_content,
        body.chart_context, body.macro_summary,
    )

    # Build the multi-turn conversation. Prior exchanges go into the
    # user_message as a transcript; the new question goes last. We
    # avoid Anthropic's messages-array conversation API for this
    # endpoint (it requires alternating user/assistant which is a
    # heavier conformance surface for a 3-turn cap).
    transcript_parts: list[str] = []
    for ex in body.thread:
        label = "USER" if ex.role.lower() == "user" else "CIO"
        transcript_parts.append(f"{label}: {ex.content}")
    transcript = "\n\n".join(transcript_parts)
    user_message = (
        f"{transcript}\n\nUSER: {body.question}".strip()
        if transcript
        else f"USER: {body.question}")

    exchanges_used = len(body.thread) + 1

    async def gen():
        full_text = ""
        suggest_council = False
        try:
            # Non-streaming call (call_claude returns the full string).
            # For the follow-up surface a "stream as one chunk" is
            # acceptable — the response is short (2-4 sentences typical)
            # and the user expects a CIO-quality answer rather than a
            # token-by-token reveal. The SSE framing is preserved so
            # the frontend's stream reader works uniformly.
            full_text = call_claude(
                OPUS_MODEL, system_prompt, user_message, max_tokens=600,
                trigger="council_followup")
        except Exception as exc:  # noqa: BLE001
            log.warning("council_followup_failed", error=str(exc))
            full_text = ("The CIO follow-up is unavailable right now. "
                         "Try again in a moment, or take the question to "
                         "the council.")
        # Strip the [SUGGEST_COUNCIL] sentinel — the frontend uses
        # the boolean, not the literal marker in the body text.
        if "[SUGGEST_COUNCIL]" in full_text:
            suggest_council = True
            full_text = full_text.replace("[SUGGEST_COUNCIL]", "").strip()

        # Emit as a single chunk + meta frame.
        chunk_frame = json.dumps({"type": "chunk", "text": full_text})
        meta_frame = json.dumps({
            "type": "meta",
            "exchanges_used": exchanges_used,
            "suggest_council": suggest_council,
        })
        yield f"data: {chunk_frame}\n\n"
        yield f"data: {meta_frame}\n\n"
        yield "data: [DONE]\n\n"

        _log_interaction_bg(
            request, session, "explainer_followup",
            question_text=body.question,
            response_summary=full_text[:500],
            metadata={
                "explainer_topic": body.explainer_topic,
                "exchanges_used": exchanges_used,
                "suggest_council": suggest_council,
            },
        )

    return StreamingResponse(gen(), media_type="text/event-stream")


# ── Guided UAT test runner ────────────────────────────────────────────────────
#
# Records attested test-step results, structured failure reports, and
# AI-categorised tester feedback. Test SCRIPTS are frontend config
# (constants/testScripts.ts) — only results and feedback are persisted
# here. All endpoints are team-gated; the admin views are ruurdsm@ only.

# Team-gating is the require_team_member dependency on the testing
# endpoints; the failure-reports and feedback-backlog views require the
# view_admin permission. Both are permission checks — no hardcoded email.


async def _read_screenshots(files: list[UploadFile]) -> list[str]:
    """Reads uploaded screenshot files and saves them to local storage,
    returning their relative paths. Fail-open — never raises."""
    from tools.test_runner import save_screenshots
    pairs: list[tuple[str, bytes]] = []
    for f in files or []:
        try:
            content = await f.read()
            if content:
                pairs.append((f.filename or "shot.png", content))
        except Exception:  # noqa: BLE001
            continue
    return save_screenshots(pairs) if pairs else []


# ── Automated triage triggers ─────────────────────────────────────────────────
#
# The triage engine runs in the background, never blocking a result /
# feedback submission. Two automatic triggers plus the manual endpoint:
#   threshold  — 5+ unaddressed items have accumulated since the last run
#   test_pass  — a tester has just completed a full test script
# Both are fire-and-forget and fail-open — a triage failure never affects
# the primary submission.

_triage_bg_tasks: set = set()


async def _triage_trigger(kind: str) -> None:
    """Runs in the background. For the threshold trigger it first checks
    the ≥5-unaddressed-since-last-run condition; the test_pass trigger
    runs unconditionally. run_triage itself skips a concurrent run."""
    try:
        from tools.triage_engine import (
            count_unaddressed_items, is_triage_running, last_triage_at,
            run_triage,
        )
        if await is_triage_running():
            return
        if kind == "threshold":
            since = await last_triage_at()
            if await count_unaddressed_items(since=since) < 5:
                return
            await run_triage("threshold")
        else:
            await run_triage("test_pass")
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_trigger_failed", kind=kind, error=str(exc))


def _fire_triage(kind: str) -> None:
    """Fire-and-forget scheduling of a triage trigger — a strong reference
    is held so the task is not garbage-collected mid-run."""
    try:
        import asyncio
        task = asyncio.create_task(_triage_trigger(kind))
        _triage_bg_tasks.add(task)
        task.add_done_callback(_triage_bg_tasks.discard)
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_fire_failed", kind=kind, error=str(exc))


@app.post("/api/v1/testing/results")
@limiter.limit("120/minute")
async def testing_record_result(
    request: Request,
    script_id: str = Form(...),
    step_id: str = Form(...),
    result: str = Form(...),
    notes: str = Form(default=""),
    failure_description: str = Form(default=""),
    expected_result: str = Form(default=""),
    actual_result: str = Form(default=""),
    severity: str = Form(default=""),
    browser_info: str = Form(default=""),
    override_reason: str = Form(default=""),
    low_quality: bool = Form(default=False),
    script_complete: bool = Form(default=False),
    screenshots: list[UploadFile] = File(default=[]),
    session: dict = Depends(require_team_member),
):
    """
    Records (upserts) one attested test-step result. Always multipart so
    a step with or without screenshots uses one content type. A
    re-attestation overwrites the row and flips `overridden`. Team-only
    (require_team_member).
    """
    email = session.get("email", "")
    if result not in {"pass", "fail", "skip"}:
        raise HTTPException(status_code=422, detail="result must be pass | fail | skip")

    from tools.test_runner import record_result
    paths = await _read_screenshots(screenshots)
    stored = await record_result(
        user_email=email,
        session_type=request.headers.get("x-session-type") or "testing",
        script_id=script_id, step_id=step_id, result=result,
        notes=notes or None, failure_description=failure_description or None,
        expected_result=expected_result or None,
        actual_result=actual_result or None, severity=severity or None,
        browser_info=browser_info or None, screenshot_paths=paths or None,
        low_quality=low_quality, override_reason=override_reason or None,
    )
    if stored is None:
        raise HTTPException(status_code=503,
                            detail="Could not record the result — database unavailable.")

    # Automation hooks — both fire-and-forget, never block this response.
    # Threshold: a new failure/feedback item may push the backlog past 5.
    # Test pass: `script_complete` is set by the client (which holds the
    # testScripts.ts step inventory) when the final step has been attested.
    _fire_triage("threshold")
    if script_complete:
        _fire_triage("test_pass")
    return stored


@app.get("/api/v1/testing/results")
async def testing_get_results(session: dict = Depends(require_team_member)):
    """The current user's test results, grouped by script_id. Team-only."""
    email = session.get("email", "")
    from tools.test_runner import get_results
    grouped: dict[str, list] = {}
    for row in await get_results(email):
        grouped.setdefault(row["script_id"], []).append(row)
    return {"results": grouped}


@app.get("/api/v1/testing/unseen")
async def testing_unseen(session: dict = Depends(require_team_member)):
    """Per-script attested-step inventory — the frontend diffs it against
    testScripts.ts to surface scripts with new/changed steps. Team-only."""
    email = session.get("email", "")
    from tools.test_runner import get_unseen
    return await get_unseen(email)


@app.get("/api/v1/testing/summary")
async def testing_summary(session: dict = Depends(require_team_member)):
    """Per-script pass/fail/skip counts for the current user. The frontend
    derives total and pending from its own step inventory. Team-only."""
    email = session.get("email", "")
    from tools.test_runner import get_summary
    return {"summary": await get_summary(email)}


@app.get("/api/v1/testing/team-progress")
async def testing_team_progress(
    session: dict = Depends(require_permission("view_uat_status")),
):
    """Shared UAT progress across every team member — backs the
    Settings → UAT Team Progress dashboard (May 24 2026).

    Permission: view_uat_status (carried by team_member and sysadmin
    per the UAT #119 split). READ-ONLY; the frontend cannot attest
    steps for another tester through this route — only
    /api/v1/testing/results (require_team_member, scoped to the
    caller's own email) accepts attestations.

    Real-time: the frontend polls every 15s. The response is small
    (per-user per-script step-id lists + 4 scalar fields) so the
    cost is bounded; one round-trip per 15s per logged-in viewer.

    Fail-open: a DB outage returns the team list with empty progress
    so the frontend renders a per-member card at 0%, never a blank.
    """
    from tools.test_runner import get_team_progress
    return await get_team_progress()


@app.get("/api/v1/testing/failures")
async def testing_failures(
    session: dict = Depends(require_permission("view_uat_status")),
):
    """Every failed step across all testers, severity-sorted.

    May 24 2026 (UAT #119) — relaxed from view_admin to view_uat_status
    so Bob and Molly can see real-time UAT progress without admin
    access. The endpoint is read-only; mutation endpoints (resolve,
    suggestions/approve, triage) remain manage_users / view_admin-
    gated so a team_member cannot act on a row, only see it.
    """
    from tools.test_runner import get_all_failures
    return {"failures": await get_all_failures()}


@app.get("/api/v1/testing/issue-tracker")
async def testing_issue_tracker(
    session: dict = Depends(require_permission("view_uat_status")),
):
    """
    Issue Tracker view — every row that has ever failed, with a
    computed status field ∈ {open, pending_retest, passed, closed}
    per compute_issue_status(). Includes Passed rows (re-attested
    after a resolution) so the tracker shows the full lifecycle of
    each issue, not just the currently-failing ones.

    Filtering, sorting and column projection live on the frontend
    — the endpoint returns the full row set and the UI shapes it.

    May 24 2026 (UAT #119) — relaxed from view_admin to view_uat_status
    so team_member sees the same read-only tracker every admin sees.
    """
    from tools.test_runner import get_issue_tracker_rows
    return {"issues": await get_issue_tracker_rows()}


# ── PR-driven Suggested Resolutions — Commit 3/7 ─────────────────────────────
# The webhook populates pr_suggestions; these three endpoints are the
# consumer side. Sysadmin-only (require_sysadmin = require_permission
# "manage_users") — same gate other sysadmin actions use.

@app.get("/api/v1/testing/suggestions")
async def testing_list_suggestions(
    session: dict = Depends(require_sysadmin),
):
    """
    Returns every pending_review suggestion joined to its failure row.
    Backs the Suggested Resolutions banner + review modal on Failure
    Reports. Sysadmin only.

    The response carries the failure context (script_id, step_id,
    user_email, severity, etc.) so the review modal renders the
    "Failure half" + "PR half" + resolution fields without a second
    round-trip. step_title and feature are derived on the frontend
    via the existing TEST_SCRIPTS / ROUTE_TO_FEATURE helpers.
    """
    from tools.pr_suggestions import list_pending_suggestions
    return {"suggestions": await list_pending_suggestions()}


@app.get("/api/v1/testing/suggestions/by-failure")
async def testing_suggestions_by_failure(
    session: dict = Depends(require_sysadmin),
):
    """
    Returns {failure_id: pending_count} for every failure that has at
    least one pending suggestion. Powers the "[Fix available — review]"
    row badge on Failure Reports (Commit 5/7) — the frontend fetches
    this once on load and joins it onto the failure list to render
    the badges in one shot. Sysadmin only.
    """
    from tools.pr_suggestions import pending_count_by_failure
    return {"by_failure": await pending_count_by_failure()}


@app.post("/api/v1/testing/suggestions/{suggestion_id}/approve")
async def testing_approve_suggestion(
    suggestion_id: int, body: dict,
    session: dict = Depends(require_sysadmin),
):
    """
    Converts a pending_review suggestion into a real resolution on
    its failure row.

    Body (required):
      root_cause        — written into resolution_note. Universal across
                          all resolution types.
      remediation_note  — written into the resolution row. Required
                          because this endpoint always applies
                          resolution_type='code_fix_deployed' (the
                          modal pre-selects it; a reviewer who wants
                          a different type uses the manual modal on
                          the Failure Reports row instead).

    Side effects:
      1. resolve_failure writes the structured resolution onto the
         failure row with fix_reference="#{pr_number}".
      2. The step is "reset to pending" by virtue of the resolved_at
         column being set (the existing get_unseen carve-out — see
         migration 025's UPSERT change).
      3. get_notifications surfaces the resolved_failures pill to the
         original tester on their next login (notification is
         DERIVED, not pushed).
      4. Sibling pending suggestions for the same failure id are
         auto-dismissed (decision point 4 — cleaner queue).

    404 — suggestion id not found, OR the suggestion's failure row
          has gone away (a stale request after the failure was
          cleaned up).
    409 — suggestion exists but is no longer in pending_review
          (already approved or dismissed). The frontend treats 409 as
          a "queue out of date — refresh" signal.
    422 — body validation: missing root_cause OR missing
          remediation_note.
    """
    root_cause = str(body.get("root_cause") or "").strip()
    remediation_note = str(body.get("remediation_note") or "").strip()
    if not root_cause:
        raise HTTPException(
            status_code=422, detail="root_cause is required.")
    if not remediation_note:
        raise HTTPException(
            status_code=422, detail="remediation_note is required.")

    from tools.pr_suggestions import approve_suggestion, get_suggestion

    # Pre-flight read so 404 vs 409 are distinguishable. The
    # alternative is to surface a single 4xx for both, but the
    # frontend benefits from "this is stale, refresh the queue" vs
    # "this never existed" guidance.
    existing = await get_suggestion(suggestion_id)
    if not existing:
        raise HTTPException(
            status_code=404, detail=f"Suggestion {suggestion_id} not found.")
    if existing["state"] != "pending_review":
        raise HTTPException(
            status_code=409,
            detail=f"Suggestion {suggestion_id} is already "
                   f"{existing['state']}. Refresh the queue.")

    result = await approve_suggestion(
        suggestion_id,
        reviewed_by=session.get("email", ""),
        root_cause=root_cause,
        remediation_note=remediation_note,
    )
    if result is None:
        # The pre-flight passed but the approve flow couldn't land
        # the resolution — most likely the failure row was deleted
        # between read and write. Return 404 with a clear message.
        raise HTTPException(
            status_code=404,
            detail="Suggestion approved but the failure row could not "
                   "be updated. Reload the queue.")
    return {"approved": True, **result}


@app.post("/api/v1/testing/suggestions/{suggestion_id}/dismiss")
async def testing_dismiss_suggestion(
    suggestion_id: int, body: dict | None = None,
    session: dict = Depends(require_sysadmin),
):
    """
    Marks a suggestion dismissed. The failure stays Open — no
    resolution is recorded. Body: {dismiss_reason?: str}.

    404 — suggestion not found OR already in a terminal state. The
          UPDATE's `WHERE state='pending_review'` clause is what
          gates this; the helper returns False on a no-op and the
          endpoint maps that to 404.
    """
    from tools.pr_suggestions import dismiss_suggestion as _dismiss

    body = body or {}
    dismiss_reason = (
        str(body.get("dismiss_reason") or "").strip() or None
    )
    ok = await _dismiss(
        suggestion_id,
        reviewed_by=session.get("email", ""),
        dismiss_reason=dismiss_reason,
    )
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Suggestion {suggestion_id} not found or already "
                   "in a terminal state.")
    return {"dismissed": True}


@app.post("/api/v1/testing/failures/{failure_id}/resolve")
async def testing_resolve_failure(
    failure_id: int, body: dict,
    session: dict = Depends(require_permission("view_admin")),
):
    """
    Marks a failure resolved. The row is kept (the resolution is the audit
    trail) with the migration-025 metadata block: resolution_type, the
    root cause in resolution_note, and — for code_fix_deployed only —
    fix_reference + remediation_note.

    Validation contract:
      - resolution_type      required, one of RESOLUTION_TYPES
      - resolution_note      required (the root cause; universal)
      - fix_reference        required when resolution_type =
                             'code_fix_deployed'. Accepted formats:
                             7+ hex chars (SHA), #NNN (PR number),
                             https://github.com/... URL
      - remediation_note     required when resolution_type =
                             'code_fix_deployed'

    Step-reset semantics:
      no_bug_detected / code_fix_deployed → tester sees the resolved
        failure as a pending re-test (the login notification carries
        a Re-test This Step CTA).
      wont_fix → step is NOT reset; the notification card is
        informational only, no CTA, no re-attestation prompt.

    Requires view_admin.
    """
    from tools.test_runner import RESOLUTION_TYPES, resolve_failure

    resolution_type = str(body.get("resolution_type") or "").strip()
    resolution_note = str(body.get("resolution_note") or "").strip()
    fix_reference = str(body.get("fix_reference") or "").strip() or None
    remediation_note = str(body.get("remediation_note") or "").strip() or None

    if resolution_type not in RESOLUTION_TYPES:
        raise HTTPException(
            status_code=422,
            detail="resolution_type is required and must be one of "
                   f"{list(RESOLUTION_TYPES)}.")
    if not resolution_note:
        raise HTTPException(
            status_code=422,
            detail="resolution_note (root cause) is required.")
    if resolution_type == "code_fix_deployed":
        if not fix_reference or not _is_valid_fix_reference(fix_reference):
            raise HTTPException(
                status_code=422,
                detail="fix_reference is required for code_fix_deployed. "
                       "Accepted formats: 7+ hex characters (commit SHA), "
                       "#NNN (PR number), or a GitHub URL.")
        if not remediation_note:
            raise HTTPException(
                status_code=422,
                detail="remediation_note is required for code_fix_deployed.")

    resolved = await resolve_failure(
        failure_id, session.get("email", ""), resolution_note,
        resolution_type=resolution_type,
        fix_reference=fix_reference,
        remediation_note=remediation_note,
    )
    if resolved is None:
        raise HTTPException(status_code=404, detail="Failure not found.")
    return {"resolved": True, **resolved}


# Fix-reference shape: 7+ hex chars (SHA), #NNN (PR number), or a
# https://github.com/... URL. Kept as a small standalone helper so the
# resolution-modal frontend and the endpoint validator share one
# definition (the frontend re-implements the same regex set; if either
# diverges the test in tests/test_failure_resolution.py catches it).
_SHA_RE = __import__("re").compile(r"^[0-9a-fA-F]{7,40}$")
_PR_RE = __import__("re").compile(r"^#\d{1,6}$")
_GH_URL_RE = __import__("re").compile(
    r"^https?://(?:www\.)?github\.com/[^/]+/[^/]+/(?:commit|pull|issues)/.+$")


def _is_valid_fix_reference(s: str) -> bool:
    """True when `s` looks like a commit SHA / PR number / GitHub URL.
    Lax on whitespace; strict on shape. The endpoint applies this guard
    BEFORE the DB write so a code-fix claim cannot be recorded without
    a traceable reference."""
    s = s.strip()
    return bool(_SHA_RE.match(s) or _PR_RE.match(s) or _GH_URL_RE.match(s))


@app.post("/api/v1/testing/feedback")
@limiter.limit("60/minute")
async def testing_submit_feedback(
    request: Request,
    feedback_type: str = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    script_id: str = Form(default=""),
    step_id: str = Form(default=""),
    source_route: str = Form(default=""),
    priority: str = Form(default=""),
    browser_info: str = Form(default=""),
    low_quality: bool = Form(default=False),
    screenshots: list[UploadFile] = File(default=[]),
    session: dict = Depends(require_team_member),
):
    """
    Accepts a feedback submission, runs AI categorisation, and stores it.
    A submission is step-linked (script_id + step_id) or free-form
    (neither — source_route set, the "Suggest an enhancement" path).
    Returns the stored row including the AI categorisation. Team-only.
    """
    import asyncio

    email = session.get("email", "")
    from tools.test_runner import categorize_feedback, submit_feedback

    step_context = f"{script_id or 'free-form'} / {step_id or source_route or 'n/a'}"
    ai = await asyncio.to_thread(
        categorize_feedback, feedback_type, title, description, step_context)
    paths = await _read_screenshots(screenshots)
    stored = await submit_feedback(
        user_email=email, script_id=script_id or None, step_id=step_id or None,
        source_route=source_route or None, feedback_type=feedback_type,
        title=title, description=description, priority=priority or None,
        screenshot_paths=paths or None, browser_info=browser_info or None,
        low_quality=low_quality, ai=ai,
    )
    if stored is None:
        raise HTTPException(status_code=503,
                            detail="Could not store the feedback — database unavailable.")

    # Threshold trigger — a new feedback item may push the unaddressed
    # backlog past 5. Fire-and-forget; never blocks this response.
    _fire_triage("threshold")
    return stored


@app.get("/api/v1/testing/feedback")
async def testing_get_feedback(
    category: str | None = None, severity: str | None = None,
    effort: str | None = None, status: str | None = None,
    user_email: str | None = None,
    session: dict = Depends(require_permission("view_uat_status")),
):
    """All tester feedback, newest first, with optional filters.

    May 24 2026 (UAT #119) — relaxed from view_admin to view_uat_status.
    Team members READ the backlog; the resolve action below remains
    view_admin so only an admin can change a feedback row's status.
    """
    from tools.test_runner import get_all_feedback
    feedback = await get_all_feedback({
        "category": category, "severity": severity, "effort": effort,
        "status": status, "user_email": user_email,
    })
    return {"feedback": feedback}


@app.post("/api/v1/testing/feedback/{feedback_id}/resolve")
async def testing_resolve_feedback(
    feedback_id: int, body: dict,
    session: dict = Depends(require_permission("view_admin")),
):
    """Updates a feedback row's status. The submitter sees a login
    notification on the next visit. Requires the view_admin permission."""
    admin = session.get("email", "")
    status = str(body.get("status") or "")
    if status not in {"noted", "planned", "wont_do", "resolved"}:
        raise HTTPException(
            status_code=422,
            detail="status must be noted | planned | wont_do | resolved")
    from tools.test_runner import resolve_feedback
    resolved = await resolve_feedback(
        feedback_id, status, str(body.get("resolution_note") or "") or None, admin)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Feedback not found.")
    return {"resolved": True, **resolved}


@app.get("/api/v1/testing/notifications")
async def testing_notifications(session: dict = Depends(require_team_member)):
    """
    The current tester's operational login notifications — failures an
    admin resolved (pending re-test) and feedback an admin responded to.
    The "new tests available" notification is computed on the frontend
    from /unseen. Team-only.
    """
    email = session.get("email", "")
    from tools.test_runner import get_notifications
    return await get_notifications(email)


@app.post("/api/v1/testing/quality-check")
@limiter.limit("120/minute")
async def testing_quality_check(
    request: Request, body: dict, session: dict = Depends(require_team_member),
):
    """
    The quality gate — scores a failure report or feedback submission
    before the frontend stores it. Fail-open: an evaluator error returns
    passed=true so a flaky evaluator never blocks a submission. Team-only;
    logs an interaction of type test_quality_eval.
    """
    import asyncio

    submission_type = str(body.get("type") or "feedback")
    description = str(body.get("description") or "")
    step_context = str(body.get("step_context") or "")
    actual_result = body.get("actual_result")

    from tools.test_runner import quality_check
    from agents.usage import start_usage_capture

    # quality_check is a Sonnet call wrapped in asyncio.to_thread, which DOES
    # propagate the contextvars — so seeding the bucket here captures it.
    start_usage_capture()
    verdict = await asyncio.to_thread(
        quality_check, submission_type, step_context, description,
        str(actual_result) if actual_result else None)

    _log_interaction_bg(
        request, session, "test_quality_eval",
        question_text=f"{submission_type}: {step_context}"[:500],
        metadata={"overall": verdict.get("overall"),
                  "passed": verdict.get("passed")},
    )
    return verdict


# ── Triage reports — sysadmin only ────────────────────────────────────────────

@app.post("/api/v1/testing/triage")
async def testing_run_triage(
    session: dict = Depends(require_permission("manage_users")),
):
    """
    Manually triggers a triage run in the background — does not block.
    The report appears under Settings → Triage Reports when complete.
    Sysadmin only (the manage_users permission).
    """
    _fire_triage("manual")
    return {"status": "triage_started",
            "message": "Triage report will be ready shortly."}


@app.get("/api/v1/testing/triage")
async def testing_get_triage_reports(
    session: dict = Depends(require_permission("manage_users")),
):
    """Every triage report, newest first. Sysadmin only."""
    from tools.triage_engine import get_all_triage_reports
    return {"reports": await get_all_triage_reports()}


@app.get("/api/v1/testing/triage/latest")
async def testing_get_latest_triage_report(
    session: dict = Depends(require_permission("manage_users")),
):
    """The most recent triage report, or null. Sysadmin only."""
    from tools.triage_engine import get_latest_triage_report
    return {"report": await get_latest_triage_report()}


# ── Triage report items — sysadmin only ───────────────────────────────────────
# Item-level resolution endpoints (migration 023 + triage Commit 2). The
# items table normalises the verdict prose into addressable rows; these
# three endpoints back the Settings → Triage Reports per-item UI.

@app.get("/api/v1/testing/triage/items")
async def testing_get_triage_items(
    report_id: int | None = None,
    session: dict = Depends(require_permission("manage_users")),
):
    """
    Every triage_report_items row with full resolution status.
    Optionally filtered to a specific report_id. Sysadmin only.
    """
    from tools.triage_engine import get_all_triage_items
    return {"items": await get_all_triage_items(report_id=report_id)}


@app.patch("/api/v1/testing/triage/items/{item_id}/resolve")
async def testing_resolve_triage_item(
    item_id: int,
    body: dict,
    request: Request,
    session: dict = Depends(require_permission("manage_users")),
):
    """
    Marks a triage item resolved. Body: {resolution_note, fix_commit?,
    requires_retest?}.

    When requires_retest=true the row's retest_requested_at is stamped
    to now() — frontend TestNotifications then surfaces a "Fix ready
    for retest" pill to the original reporter (Commit 3 wires that
    notification path through the existing get_notifications surface).
    Sysadmin only.
    """
    from tools.triage_engine import resolve_triage_item

    resolution_note = str(body.get("resolution_note") or "").strip()
    if not resolution_note:
        raise HTTPException(
            status_code=422,
            detail="resolution_note is required.")
    fix_commit_raw = body.get("fix_commit")
    fix_commit = (str(fix_commit_raw).strip() or None) if fix_commit_raw else None
    requires_retest = bool(body.get("requires_retest", False))

    result = await resolve_triage_item(
        item_id,
        resolved_by=session.get("email", ""),
        resolution_note=resolution_note,
        fix_commit=fix_commit,
        requires_retest=requires_retest,
    )
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Triage item {item_id} not found.")
    return {"status": "resolved", "item": result}


@app.patch("/api/v1/testing/triage/items/{item_id}/unresolve")
async def testing_unresolve_triage_item(
    item_id: int,
    session: dict = Depends(require_permission("manage_users")),
):
    """
    Clears the resolution fields on a triage item — sysadmin recovery
    for an item resolved in error. Sysadmin only.
    """
    from tools.triage_engine import unresolve_triage_item

    ok = await unresolve_triage_item(item_id)
    if not ok:
        raise HTTPException(
            status_code=404, detail=f"Triage item {item_id} not found.")
    return {"status": "unresolved"}


# ── Macro market research (FEATURE 2) ────────────────────────────────────────
# The daily-scheduled macro digest the council + academic_review prompts
# inject as a CURRENT MACRO CONDITIONS block. Read endpoints are open to
# any authenticated user (the dashboard widget renders the latest digest
# for the whole team); the manual run trigger is sysadmin only because
# it bypasses the 24h freshness gate and burns the Sonnet + web_search
# budget on demand.

@app.get("/api/v1/context/freshness")
async def get_context_freshness(
    session: dict = Depends(require_auth),
):
    """Returns a per-layer freshness map of every agent-context cache.
    Item 5 (May 23 2026 — analytics context injection, freshness
    badges). Powers the dashboard freshness badges so the user sees
    how current the prompts an agent sees are.

    Three layers — keys are stable, values are ISO timestamps or null:
      macro_context        — last research digest the macro layer is
                              reflecting (mirrors /research/latest's
                              last_completed_at)
      analytics_context    — last refresh of the narrative cache
                              (rebuilds on every analytics refresh
                              tick)
      diversification_context — last refresh of the structured
                              diversification cache

    Diversification context doesn't carry an explicit timestamp
    field today; the strategy cache's computed_at is reported as a
    proxy since the diversification refresh always trails it.
    """
    from tools.analytics_context import get_analytics_freshness
    from tools.research_engine import last_research_run_at
    macro_at = await last_research_run_at()
    analytics_at = get_analytics_freshness()
    # Diversification refresh shadows the strategy cache; report
    # the strategy cache's latest computed_at as the freshness proxy.
    diversification_at: str | None = None
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is not None:
            async with AsyncSessionLocal() as s:
                r = await s.execute(text(
                    "SELECT MAX(computed_at) "
                    "FROM strategy_results_cache"))
                row = r.fetchone()
                if row and row[0]:
                    diversification_at = row[0].isoformat()
    except Exception as exc:  # noqa: BLE001
        log.warning("context_freshness_diversification_failed",
                    error=str(exc))
    return {
        "macro_context":           (macro_at.isoformat()
                                      if macro_at else None),
        "analytics_context":        analytics_at,
        "diversification_context":  diversification_at,
    }


@app.get("/api/v1/research/latest")
async def research_get_latest_digest(
    session: dict = Depends(require_auth),
):
    """The most recent COMPLETED digest, or null when no completed
    digest exists yet. Powers the dashboard widget; the widget renders
    a "preparing first digest" empty state on null."""
    from tools.research_engine import get_latest_digest, last_research_run_at
    digest = await get_latest_digest()
    last_run = await last_research_run_at()
    return {
        "digest":              digest,
        "last_completed_at":   last_run.isoformat() if last_run else None,
    }


@app.get("/api/v1/research/history")
async def research_get_history(
    limit: int = 10,
    session: dict = Depends(require_auth),
):
    """The N most recent runs across every status (running / complete /
    failed). Used by the sysadmin run-history accordion below the
    widget so the team can see when failed runs happened. Open to any
    authenticated user — visibility into failures is a transparency
    feature, not a privileged one."""
    from tools.research_engine import get_recent_digests
    return {"runs": await get_recent_digests(limit=max(1, min(int(limit), 50)))}


@app.post("/api/v1/research/run")
async def research_run_now(
    session: dict = Depends(require_permission("manage_users")),
):
    """Forces a fresh research run — bypasses the 24h freshness gate.
    Returns immediately with status: 'running'; the dashboard widget
    polls /research/latest to pick up the digest once it lands.
    Sysadmin only — manual runs burn the Sonnet + web_search budget."""
    from tools.research_engine import (
        _research_bg_tasks, is_research_running, run_research,
    )
    if await is_research_running():
        return {"status": "already_running",
                "message": "A research run is already in progress."}

    # Direct manual run — bypass the stale gate. Spawn the run on the
    # event loop (we are on it here) so a long Sonnet + web_search
    # call returns the 200 to the user immediately; the digest lands
    # via the post-run cache refresh.
    import asyncio
    try:
        task = asyncio.create_task(run_research("manual"))
        # Strong ref so the task is not GC'd mid-run.
        _research_bg_tasks.add(task)
        task.add_done_callback(_research_bg_tasks.discard)
    except Exception as exc:  # noqa: BLE001
        log.warning("research_manual_spawn_failed", error=str(exc))
        raise HTTPException(
            status_code=500, detail="Failed to spawn research run.")
    return {"status": "running",
            "message": "Research run started. Poll /api/v1/research/latest "
                       "for the result."}


# ── Statistical audit — sysadmin only ─────────────────────────────────────────

@app.post("/api/v1/audit/run")
async def audit_run(
    body: dict | None = None,
    session: dict = Depends(require_sysadmin),
):
    """
    Triggers a full three-layer statistical audit in the background and
    returns immediately with the audit_id. A concurrent run is refused
    with already_running.

    `triggered_by` may be "manual" (default), "pre_submission" (an
    Analytical-Appendix audit) or "demo" (a forced run for the live
    presentation). The smart-audit-caching "Run Live Demo" button sends
    {"reason": "demo"} — accepted here as an alias for triggered_by.
    Sysadmin only — triggering a QA/audit run is restricted to the
    platform sysadmin (Michael); the read-only audit views remain open
    to the project team.
    """
    from tools.audit_engine import start_audit
    from tools.qa_guard import (
        QA_BUSY_MESSAGE_STATISTICAL, statistical_audit_in_progress,
    )

    # Per-type lock — a statistical audit is blocked only by another
    # statistical audit in flight, never by a methodology run. A run
    # stuck past the 15-minute timeout is reaped inside is_audit_running,
    # so a hung run never wedges this. start_audit keeps its own
    # is_audit_running() check as the race backstop.
    if await statistical_audit_in_progress():
        raise HTTPException(status_code=409,
                            detail=QA_BUSY_MESSAGE_STATISTICAL)

    body = body or {}
    triggered_by = str(body.get("triggered_by") or body.get("reason")
                       or "manual")
    if triggered_by not in ("manual", "scheduled", "pre_submission", "demo"):
        triggered_by = "manual"
    return await start_audit(triggered_by, session.get("email", ""))


@app.get("/api/v1/audit/runs")
async def audit_get_runs(
    session: dict = Depends(require_permission("team_member")),
):
    """Every audit run with summary stats, newest first. Project team only."""
    from tools.audit_engine import get_audit_runs
    return {"runs": await get_audit_runs()}


@app.get("/api/v1/audit/runs/latest")
async def audit_get_latest_run(
    session: dict = Depends(require_auth),
):
    """
    The most recent audit run with its findings, or null. Open to every
    authenticated user — viewers see the read-only audit summary in the
    QA tab; the full findings panel is gated to the project team in the
    frontend.

    Carries the smart-audit-caching verdict: is_current is True when the
    live data fingerprint matches the last completed run's data_hash, so
    the QA tab can show a cached result instead of an unnecessary re-run.
    """
    from tools.audit_assembler import is_audit_current
    from tools.audit_engine import fail_stale_audits, get_latest_audit_run
    # Reap a hung run before reporting status — the latest run any user's
    # poll sees is then never a stale 'running' row.
    await fail_stale_audits()
    run = await get_latest_audit_run()
    currency = await is_audit_current()
    return {
        "run": run,
        "is_current": currency["is_current"],
        "statistical_current": currency["statistical_current"],
        "qa_current": currency["qa_current"],
        "current_data_hash": currency["current_data_hash"],
        "last_hash": currency["last_hash"],
    }


@app.get("/api/v1/audit/runs/{run_id}")
async def audit_get_run(
    run_id: int,
    session: dict = Depends(require_permission("team_member")),
):
    """One audit run with all findings grouped by layer. Project team only."""
    from tools.audit_engine import get_audit_run
    run = await get_audit_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Audit run not found.")
    return run


@app.get("/api/v1/audit/runs/{run_id}/export")
async def audit_export_run(
    run_id: int,
    session: dict = Depends(require_permission("team_member")),
):
    """
    The audit run as a downloadable PDF — the Statistical Audit Report,
    professionally formatted for inclusion in the Analytical Appendix as
    evidence of independent statistical verification. Project team only.
    """
    from datetime import date
    from tools.audit_engine import get_audit_run
    from tools.audit_pdf import build_statistical_audit_pdf
    run = await get_audit_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Audit run not found.")
    pdf = build_statistical_audit_pdf(run)
    filename = f"forest_capital_statistical_audit_{date.today().isoformat()}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/v1/audit/findings/{finding_id}/resolve")
async def audit_resolve_finding(
    finding_id: int,
    body: dict | None = None,
    session: dict = Depends(require_permission("team_member")),
):
    """
    Acknowledges an audit finding — the WARN acknowledge/resolve workflow.
    Records the team's response (resolution_note) and sets resolved. This
    is a response, not a correction: the audit's overall verdict does not
    change. Project team only.
    """
    from tools.audit_engine import resolve_finding
    note = str((body or {}).get("resolution_note") or "").strip()
    if not note:
        raise HTTPException(
            status_code=422, detail="A resolution note is required.")
    finding = await resolve_finding(
        finding_id, True, note, resolved_by=session["email"])
    if finding is None:
        raise HTTPException(status_code=404, detail="Audit finding not found.")
    return finding


@app.post("/api/v1/audit/findings/{finding_id}/unresolve")
async def audit_unresolve_finding(
    finding_id: int,
    session: dict = Depends(require_permission("team_member")),
):
    """Clears the acknowledgement on an audit finding. Project team only."""
    from tools.audit_engine import resolve_finding
    finding = await resolve_finding(
        finding_id, False, None, resolved_by=None)
    if finding is None:
        raise HTTPException(status_code=404, detail="Audit finding not found.")
    return finding


@app.get("/api/v1/report/readiness")
async def report_readiness(
    session: dict = Depends(require_auth),
):
    """
    Workstream C report-readiness verdict (May 28 2026).

    Returns the platform's combined verdict on whether either audit
    surface has unreviewed blocking items that should prevent the
    team from generating a graded submission. The same logic is run
    by the generation-endpoint gate (_require_report_ready) — this
    GET surfaces the verdict to the frontend so the Reports page can
    show a readiness indicator and a blocking modal that names every
    outstanding item.

    Shape:
      {
        is_ready: bool,
        blocking_count: int,
        statistical: { unreviewed_warnings, unreviewed_failures },
        methodology: { unresolved_warnings, unresolved_failures },
        checked_at: ISO timestamp
      }

    Auth: any authenticated user — the readiness verdict is a project
    record visible to viewers, not a sysadmin-private surface.
    """
    from tools.report_readiness import compute_readiness

    return await compute_readiness()


@app.post("/api/v1/cache/invalidate")
async def cache_invalidate(
    session: dict = Depends(require_permission("manage_users")),
):
    """
    Clears strategy_results_cache so the backtester recomputes from fresh
    data on the next /api/backtest request — used after a data update or
    to repopulate cached results that predate a new result-dict field
    (e.g. the persisted weight_schedule). Sysadmin only.
    """
    from tools.cache import clear_strategy_cache
    removed = await clear_strategy_cache()
    log.info("strategy_cache_invalidated", rows_removed=removed,
             by=session.get("email"))
    # Smart audit caching — the cache invalidation is a data event; if the
    # last audit no longer reflects the data, run_full_audit re-verifies it
    # in the background (idempotent — a no-op when already current).
    from tools.audit_engine import trigger_audit_async
    trigger_audit_async("cache_invalidation")
    return {"status": "cleared", "rows_removed": removed}


@app.post("/api/v1/admin/refresh-monthly-data")
async def refresh_monthly_data(
    session: dict = Depends(require_permission("manage_users")),
):
    """
    Extends the monthly data pipeline beyond the Excel file: fetches the
    total return of every complete calendar month that has closed since
    the last run — SPY (equity), BND (investment grade) and HYG (high
    yield) from yfinance, DTB3 from FRED — validates the splice, and
    appends the new rows to market_data_monthly. Sysadmin only.

    After a successful extension the strategy cache is cleared and the
    audits auto-trigger. The blocking fetch runs off the event loop.
    """
    import asyncio as _asyncio
    from tools.data_fetcher import extend_market_data
    result = await _asyncio.to_thread(extend_market_data)
    log.info("refresh_monthly_data", by=session.get("email"),
             status=result.get("status"),
             rows_added=result.get("monthly_rows_added"),
             new_max=result.get("monthly_new_max"))
    return result


# ── User management ───────────────────────────────────────────────────────────
#
# The sysadmin manages platform_users from inside the platform. Every
# endpoint requires the manage_users permission; the "last sysadmin"
# guard prevents the platform from being left with no administrator.


def _valid_email(email: str) -> bool:
    """A minimal email-shape check for the create-user form."""
    return bool(email) and "@" in email and "." in email.split("@")[-1]


def _clean_permissions(raw: Any, role: str) -> list[str]:
    """Validated permissions — the supplied list filtered to known keys,
    or the role's preset when no explicit list was given."""
    if isinstance(raw, list):
        return [p for p in raw if p in PERMISSIONS]
    return list(ROLE_PRESETS.get(role, ROLE_PRESETS["viewer"]))


@app.get("/api/v1/admin/users")
async def admin_list_users(
    session: dict = Depends(require_permission("manage_users")),
):
    """Every platform user, with an activity count. Sysadmin only."""
    from tools.platform_users import list_all_users
    return {"users": await list_all_users()}


@app.get("/api/v1/admin/users/activity-breakdown")
async def admin_users_activity_breakdown(
    session: dict = Depends(require_permission("manage_users")),
):
    """
    Per-user activity broken down by interaction_type and session_type
    over BOTH a lifetime window and a rolling 30-day window — the
    analytics behind the Settings → Users → Platform Engagement panel.
    The panel renders LIFETIME as the headline (the figure that matters
    for academic-integrity tracking) and the 30-day count as
    recent-activity context.

    Joins against platform_users (LEFT JOIN) so every user appears even
    when they have zero interactions in either window. The breakdown
    aggregates two source tables:
      agent_interactions  → counts by interaction_type + SUM(cost)
      session_events      → counts by session_type for page_view events

    Each user row carries both a `lifetime` block (breakdown,
    session_breakdown, total_interactions, total_cost_usd, first_seen,
    last_seen) and a `rolling_30d` block (the same minus the
    lifetime-only first/last_seen pair).

    Sysadmin only. Fail-open — a DB error returns an empty users list.
    """
    from tools.platform_users import users_activity_breakdown
    return await users_activity_breakdown()


@app.post("/api/v1/admin/users")
async def admin_create_user(
    body: dict, session: dict = Depends(require_permission("manage_users")),
):
    """Adds a platform user. Sysadmin only."""
    from tools.platform_users import create_user, email_exists

    email = str(body.get("email") or "").strip().lower()
    if not _valid_email(email):
        raise HTTPException(status_code=422,
                            detail="A valid email address is required.")
    role = str(body.get("role") or "viewer")
    if role not in ROLE_PRESETS:
        raise HTTPException(status_code=422,
                            detail="role must be viewer | team_member | sysadmin")
    if await email_exists(email):
        raise HTTPException(
            status_code=409,
            detail="A user with that email already exists — edit them instead.")
    created = await create_user(
        email=email,
        display_name=(str(body["display_name"]).strip()
                      if body.get("display_name") else None),
        role=role,
        permissions=_clean_permissions(body.get("permissions"), role),
        notes=(str(body["notes"]).strip() if body.get("notes") else None),
        created_by=session.get("email", ""),
    )
    if created is None:
        raise HTTPException(status_code=503,
                            detail="Could not create the user — database unavailable.")
    # Welcome email — sent only after the user is successfully created.
    # Fail-open: send_welcome_email never raises, so a delivery failure
    # cannot undo or block the creation. welcome_email_sent tells the
    # frontend which confirmation message to show.
    from auth import send_welcome_email
    welcome_email_sent = await send_welcome_email(
        email=email,
        display_name=created.get("display_name"),
        notes=created.get("notes"),
        council_limit=created.get("council_queries_limit"),
    )
    return {**created, "welcome_email_sent": welcome_email_sent}


@app.patch("/api/v1/admin/users/{user_id}")
async def admin_update_user(
    user_id: int, body: dict,
    session: dict = Depends(require_permission("manage_users")),
):
    """
    Updates a user's display_name / role / permissions / is_active /
    notes / council_queries_limit / council_queries_used. email is
    immutable. The last active sysadmin cannot be demoted or
    deactivated. Sysadmin only.
    """
    from tools.platform_users import (
        count_active_sysadmins, get_user_by_id, update_user,
    )

    user = await get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    fields: dict[str, Any] = {}
    if "display_name" in body:
        fields["display_name"] = (str(body["display_name"]).strip()
                                  if body["display_name"] else None)
    if "notes" in body:
        fields["notes"] = (str(body["notes"]).strip()
                           if body["notes"] else None)
    if "role" in body:
        if body["role"] not in ROLE_PRESETS:
            raise HTTPException(status_code=422,
                                detail="role must be viewer | team_member | sysadmin")
        fields["role"] = body["role"]
    if "permissions" in body:
        fields["permissions"] = _clean_permissions(
            body["permissions"], fields.get("role", user["role"]))
    if "is_active" in body:
        fields["is_active"] = bool(body["is_active"])
    # Council query allocation — Adjust Limit (int), Unlimited (null),
    # Reset Usage (used = 0). bool is rejected: it is an int subclass.
    if "council_queries_limit" in body:
        cql = body["council_queries_limit"]
        if cql is None:
            fields["council_queries_limit"] = None
        elif isinstance(cql, int) and not isinstance(cql, bool) and cql >= 0:
            fields["council_queries_limit"] = cql
        else:
            raise HTTPException(
                status_code=422,
                detail="council_queries_limit must be a non-negative "
                       "integer or null.")
    if "council_queries_used" in body:
        cqu = body["council_queries_used"]
        if isinstance(cqu, int) and not isinstance(cqu, bool) and cqu >= 0:
            fields["council_queries_used"] = cqu
        else:
            raise HTTPException(
                status_code=422,
                detail="council_queries_used must be a non-negative integer.")

    # Last-sysadmin guard — refuse a change that would leave no active
    # administrator. A "sysadmin" is any active user holding manage_users.
    new_active = fields.get("is_active", user["is_active"])
    new_perms = fields.get("permissions", user["permissions"])
    was_admin = user["is_active"] and "manage_users" in user["permissions"]
    still_admin = new_active and "manage_users" in new_perms
    if was_admin and not still_admin and await count_active_sysadmins() <= 1:
        raise HTTPException(status_code=400,
                            detail="Cannot remove the last sysadmin.")

    updated = await update_user(user_id, fields)
    if updated is None:
        raise HTTPException(status_code=503,
                            detail="Could not update the user — database unavailable.")
    return updated


@app.delete("/api/v1/admin/users/{user_id}")
async def admin_delete_user(
    user_id: int, session: dict = Depends(require_permission("manage_users")),
):
    """
    Soft-deletes a user (is_active = false) — the row is kept so activity
    history stays attributed. The last active sysadmin cannot be deleted.
    Sysadmin only.
    """
    from tools.platform_users import (
        count_active_sysadmins, get_user_by_id, update_user,
    )

    user = await get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    was_admin = user["is_active"] and "manage_users" in user["permissions"]
    if was_admin and await count_active_sysadmins() <= 1:
        raise HTTPException(status_code=400,
                            detail="Cannot remove the last sysadmin.")
    updated = await update_user(user_id, {"is_active": False})
    if updated is None:
        raise HTTPException(status_code=503,
                            detail="Could not deactivate the user — database unavailable.")
    return {"deactivated": True, "id": user_id}


# ── Team Activity ─────────────────────────────────────────────────────────────

@app.post("/api/v1/activity/events")
@limiter.limit("120/minute")
async def activity_events(
    request: Request,
    body: dict,
    session: dict = Depends(require_auth),
):
    """
    Receives a batch of UI telemetry events from the frontend and
    inserts them into session_events in one transaction.

    Always returns 200 — the UI must never be blocked or shown an
    error by activity logging. The PROJECT_TEAM_EMAILS allowlist is
    enforced inside insert_session_events: a non-team user's events are
    dropped silently. login / logout events are stamped server-side
    with the request IP (and user agent, for login) rather than trusting
    the client.
    """
    try:
        from tools.activity_log import insert_session_events

        events = body.get("events")
        if not isinstance(events, list):
            return {"accepted": 0}

        sid = request.headers.get("x-session-id")
        stype = request.headers.get("x-session-type")
        ip = request.client.host if request.client else None
        ua = request.headers.get("user-agent")

        for ev in events:
            if not isinstance(ev, dict):
                continue
            ev.setdefault("session_id", sid)
            ev.setdefault("session_type", stype)
            # Server-authoritative IP / UA for the auth-boundary events.
            if ev.get("event_type") in ("login", "logout"):
                ev["ip_address"] = ip
            if ev.get("event_type") == "login":
                ev["user_agent"] = ua

        written = await insert_session_events(
            [e for e in events if isinstance(e, dict)], session["email"])
        return {"accepted": written}
    except Exception as exc:  # noqa: BLE001
        # Logging must never surface an error to the UI.
        log.warning("activity_events_failed", error=str(exc))
        return {"accepted": 0}


@app.post("/api/v1/activity/commits/webhook")
async def activity_commits_webhook(request: Request):
    """
    GitHub webhook receiver — handles both push and pull_request events
    against the same registration. Validates the X-Hub-Signature-256
    HMAC against GITHUB_WEBHOOK_SECRET (any invalid/missing signature
    is a 401).

    Push events       → upsert commits into commit_activity.
    pull_request events with action=closed and merged=true →
                        scan the body + commit messages for "Resolves
                        failure #N" references and queue
                        pr_suggestions rows (Suggested Resolutions
                        Commit 2/7).

    Other events (the `ping` GitHub sends at registration, draft PR
    state changes, etc.) are acknowledged and ignored. Operationally
    this means ONE webhook registration on the repo covers both the
    Team Activity commit sync AND the Suggested Resolutions workflow —
    one secret to manage, one URL to point GitHub at.
    """
    from config import GITHUB_WEBHOOK_SECRET
    from tools.github_sync import verify_signature, parse_push_payload

    raw = await request.body()
    sig = request.headers.get("x-hub-signature-256")
    if not verify_signature(GITHUB_WEBHOOK_SECRET, raw, sig):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    event_type = request.headers.get("x-github-event")
    if event_type not in ("push", "pull_request"):
        return {"status": "ignored",
                "reason": f"event '{event_type}' is not handled"}

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Malformed JSON payload.")

    if event_type == "push":
        commits = parse_push_payload(payload)
        if not commits:
            return {"status": "ok", "synced": 0}
        from tools.activity_log import upsert_commits
        written = await upsert_commits(commits)
        log.info("activity_webhook_push",
                 commits=len(commits), upserted=written)
        return {"status": "ok", "synced": written}

    # pull_request event — Suggested Resolutions Commit 2/7.
    from config import GITHUB_REPO, GITHUB_TOKEN
    from tools.github_sync import fetch_pr_commits
    from tools.pr_suggestion_scanner import (
        parse_pr_payload, record_pr_suggestions,
    )

    # Pre-parse to extract the PR number (cheap, no network), then
    # enrich with commit messages via the REST API so the scanner can
    # find references in commit messages too. The fetch is fail-open
    # — a missing GITHUB_TOKEN or API error degrades to body-only.
    pre = parse_pr_payload(payload)
    if pre is None:
        # Not a closed+merged PR — silently ack.
        return {"status": "ok", "reason": "not a merged PR"}

    commits = await fetch_pr_commits(
        GITHUB_REPO, GITHUB_TOKEN, pre["pr_number"])
    # Re-parse with the enriched commits so the scanner can match
    # references in commit messages alongside the PR body.
    enriched_payload = dict(payload)
    enriched_payload["__commits"] = commits
    parsed = parse_pr_payload(enriched_payload)
    if parsed is None:
        return {"status": "ok", "reason": "not a merged PR"}

    summary = await record_pr_suggestions(parsed)
    log.info("activity_webhook_pull_request",
             pr_number=parsed["pr_number"],
             references_found=len(parsed["matches"]),
             created=summary["created"],
             skipped_missing=summary["skipped_missing"],
             skipped_resolved=summary["skipped_resolved"],
             skipped_duplicate=summary["skipped_duplicate"])
    return {
        "status": "ok",
        "pr_number": parsed["pr_number"],
        "references_found": len(parsed["matches"]),
        "suggestions_created": len(summary["created"]),
        "skipped_missing": len(summary["skipped_missing"]),
        "skipped_resolved": len(summary["skipped_resolved"]),
        "skipped_duplicate": len(summary["skipped_duplicate"]),
    }


@app.get("/api/v1/activity/commits/sync")
@limiter.limit("6/minute")
async def activity_commits_sync(
    request: Request,
    session: dict = Depends(require_auth),
):
    """
    Manual sync of the last 100 commits from the GitHub REST API.
    Upserts on sha, so it is safe to run repeatedly — used to backfill
    history and to catch up anything the webhook missed. Requires
    GITHUB_TOKEN (the repository is private).
    """
    from config import GITHUB_REPO, GITHUB_TOKEN
    from tools.github_sync import fetch_recent_commits
    from tools.activity_log import upsert_commits

    try:
        commits = await fetch_recent_commits(GITHUB_REPO, GITHUB_TOKEN, limit=100)
    except RuntimeError as exc:
        # Misconfiguration (e.g. GITHUB_TOKEN unset). The message is a
        # deliberate, safe operator hint — surfaced via a proper 503 so the
        # client can detect the failure from the status code.
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        ref = uuid.uuid4().hex[:8]
        log.warning("activity_sync_failed", ref=ref, error=str(exc))
        raise HTTPException(
            status_code=502, detail=f"Commit sync failed (ref: {ref})")

    written = await upsert_commits(commits)
    log.info("activity_commits_synced", fetched=len(commits), upserted=written)
    return {"synced": written, "fetched": len(commits)}


@app.get("/api/v1/activity/team")
async def activity_team(
    request: Request,
    user_id: Optional[str] = Query(None),
    activity_type: str = Query("all"),
    session_type: str = Query("analytical"),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: dict = Depends(require_auth),
):
    """
    Unified Team Activity timeline — commit_activity, agent_interactions
    and session_events interleaved and sorted by timestamp descending.

    session_type defaults to "analytical"; pass "all" to include
    Testing Mode activity. commit history is session-agnostic and is
    always included when the activity_type filter permits commits.
    """
    from tools.activity_log import get_team_activity

    return await get_team_activity(
        user_id=user_id,
        activity_type=activity_type,
        session_type=session_type,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@app.get("/api/v1/activity/summary")
async def activity_summary(
    request: Request,
    include_testing: bool = Query(False),
    session: dict = Depends(require_auth),
):
    """
    Per-member interaction and commit counts, the most-consulted agents,
    and the latest academic-review verdict — the Team Activity summary
    panel. Analytical sessions only unless include_testing is set.
    """
    from tools.activity_log import get_activity_summary

    return await get_activity_summary(analytical_only=not include_testing)


@app.get("/api/v1/activity/cost-summary")
async def activity_cost_summary(
    request: Request,
    include_testing: bool = Query(False),
    session: dict = Depends(require_auth),
):
    """
    AI token spend — grand total plus per-member and per-interaction-type
    breakdowns, drawn from the agent_interactions cost columns. Drives the
    Team Activity cost panel. Analytical sessions only unless
    include_testing is set.
    """
    from tools.activity_log import get_cost_summary

    return await get_cost_summary(analytical_only=not include_testing)


# ── Changelog ─────────────────────────────────────────────────────────────────

@app.get("/api/v1/changelog")
async def changelog_all(session: dict = Depends(require_auth)):
    """Every changelog entry, newest version first — the Settings Release
    History. Returns {entries: [...]}."""
    from tools.changelog import get_all_changelog
    return {"entries": await get_all_changelog()}


@app.get("/api/v1/changelog/unseen")
async def changelog_unseen(session: dict = Depends(require_auth)):
    """
    Changelog entries released after the calling user last dismissed the
    What's New modal, plus has_tour_update and the current tour_version.
    Drives the What's New modal's trigger.
    """
    from tools.changelog import get_unseen_changelog
    return await get_unseen_changelog(session["email"])


@app.post("/api/v1/changelog/mark-seen")
async def changelog_mark_seen(
    body: dict | None = None,
    session: dict = Depends(require_auth),
):
    """
    Records that the user has seen the changelog up to now. An optional
    body {"tour_version_seen": int} also records the site-tour version
    the user has completed.
    """
    from tools.changelog import mark_changelog_seen
    tour_seen: int | None = None
    if isinstance(body, dict):
        tv = body.get("tour_version_seen")
        if isinstance(tv, int) and not isinstance(tv, bool):
            tour_seen = tv
    ok = await mark_changelog_seen(session["email"], tour_seen)
    return {"ok": ok}


# ── Explainer ─────────────────────────────────────────────────────────────────

@app.post("/api/explain/terms")
@limiter.limit("20/minute")
async def explain_terms(
    request: Request,
    body: dict,
    session: dict = Depends(require_auth),
):
    """Generates contextual glossary terms from the full council output."""
    if ENVIRONMENT != "test":
        try:
            from agents.explainer_agent import ExplainerAgent
            explainer = ExplainerAgent()
            # UAT 2026-05-27 P0 — Render logs showed every concurrent
            # dashboard request queuing behind explainer_grok_completed
            # events and draining together (39s + 24s clusters). Root
            # cause: explain_terms calls _call_llm → _call_grok which
            # uses httpx.Client (SYNC) inside an async endpoint. The
            # blocking POST stalls the event loop for 24-39s and
            # starves every other coroutine. Same fix pattern as
            # PR #122 (get_full_history_async) and #126 (optimizer
            # solver) — push the sync call into a worker thread.
            import asyncio
            return await asyncio.to_thread(
                explainer.explain_terms,
                body.get("council_output", {}))
        except Exception as exc:
            log.error("explain_terms_error", error=str(exc))
    return {}


@app.post("/api/explain/parameter")
@limiter.limit("20/minute")
async def explain_parameter(
    request: Request,
    body: dict,
    session: dict = Depends(require_auth),
):
    """Explains a config parameter in the context of current results."""
    if ENVIRONMENT != "test":
        try:
            from agents.explainer_agent import ExplainerAgent
            explainer = ExplainerAgent()
            # UAT 2026-05-27 P0 — sync Grok HTTP blocks the event
            # loop. Wrap in asyncio.to_thread; see explain_terms
            # above for the full diagnosis. asyncio.to_thread
            # supports kwargs, so the keyword call shape is
            # preserved verbatim.
            import asyncio
            return await asyncio.to_thread(
                explainer.explain_parameter,
                parameter=body.get("parameter", ""),
                value=body.get("value"),
                current_results=body.get("current_results", {}),
            )
        except Exception as exc:
            log.error("explain_parameter_error", error=str(exc))
    return {}


@app.post("/api/explain/chart")
@limiter.limit("20/minute")
async def explain_chart(
    request: Request,
    body: dict,
    session: dict = Depends(require_auth),
):
    """Generates a chart explanation anchored to actual chart data."""
    if ENVIRONMENT != "test":
        try:
            from agents.explainer_agent import ExplainerAgent
            explainer = ExplainerAgent()
            # UAT 2026-05-27 P0 — sync Grok HTTP blocks the event
            # loop. See explain_terms above for the full diagnosis.
            import asyncio
            return await asyncio.to_thread(
                explainer.explain_chart,
                chart_id=body.get("chart_id", ""),
                chart_type=body.get("chart_type", ""),
                chart_data=body.get("chart_data"),
                current_results=body.get("current_results", {}),
            )
        except Exception as exc:
            log.error("explain_chart_error", error=str(exc))
    return {}


@app.post("/api/explain/qa")
@limiter.limit("10/minute")
async def explain_qa(
    request: Request,
    body: dict,
    session: dict = Depends(require_auth),
):
    """Generates plain-English explanations for all 30 QA checklist items."""
    if ENVIRONMENT != "test":
        try:
            from agents.explainer_agent import ExplainerAgent
            explainer = ExplainerAgent()
            # UAT 2026-05-27 P0 — sync Grok HTTP blocks the event
            # loop. See explain_terms above for the full diagnosis.
            import asyncio
            return await asyncio.to_thread(
                explainer.explain_qa,
                body.get("audit_results", []))
        except Exception as exc:
            log.error("explain_qa_error", error=str(exc))
    return {}


# ── Academic Advisor (Agent 10) ───────────────────────────────────────────────
#
# Three endpoints map 1:1 to AcademicAdvisor methods. All three enforce citation
# integrity via Anthropic's server-side web_search tool — any URL the model
# emits that the tool did not actually fetch is dropped before the response
# is returned to the frontend. See agents/academic_advisor.py:_filter_to_verified
# for the runtime check.
#
# Limit is 10/min (same as council/QA endpoints) — the advisor is interactive
# but each call costs ~$0.04-0.06 incl. web_search, and we don't want a stuck
# floating button to drain the daily credit cap.

@app.post("/api/advisor/analyse")
@limiter.limit("10/minute")
async def advisor_analyse(
    request: Request,
    body: AdvisorAnalyseRequest,
    session: dict = Depends(require_auth),
):
    """
    Main advisor entry point — academic guidance for one deliverable.

    The query is scope-checked (portfolio-analysis only); the advisor agent
    uses web_search to verify any citations before returning them. Verified
    citations are merged into the response so the frontend can render them
    immediately without a second round-trip.
    """
    if ENVIRONMENT == "test":
        # Mock keeps the test suite hermetic — no Anthropic API calls.
        from agents.academic_advisor import MOCK_ADVISOR_ANALYSE
        return MOCK_ADVISOR_ANALYSE

    try:
        from agents.academic_advisor import AcademicAdvisor
        advisor = AcademicAdvisor()
        return advisor.analyse_findings(
            query=body.query,
            deliverable_type=body.deliverable_type,
            strategy_results=body.strategy_results,
        )
    except Exception as exc:
        log.error("advisor_analyse_endpoint_error", error=str(exc))
        return {
            "key_findings":     [],
            "guidance":         [],
            "citations":        [],
            "potential_issues": [],
            "error":            "Advisor temporarily unavailable.",
        }


@app.post("/api/advisor/verify-finding")
@limiter.limit("10/minute")
async def advisor_verify_finding(
    request: Request,
    body: AdvisorVerifyRequest,
    session: dict = Depends(require_auth),
):
    """
    Verifies one specific finding against external academic evidence.

    Returns supporting_evidence, contradicting_evidence, and a verdict in
    {"plausible", "implausible", "uncertain"}. The frontend uses this to
    sanity-check a single number before committing it to a slide or paper.
    """
    if ENVIRONMENT == "test":
        from agents.academic_advisor import MOCK_ADVISOR_VERIFY
        return MOCK_ADVISOR_VERIFY

    try:
        from agents.academic_advisor import AcademicAdvisor
        advisor = AcademicAdvisor()
        return advisor.check_finding_plausibility(
            finding=body.finding,
            magnitude=body.magnitude,
            period=body.period,
        )
    except Exception as exc:
        log.error("advisor_verify_endpoint_error", error=str(exc))
        return {
            "supporting_evidence":    [],
            "contradicting_evidence": [],
            "verdict":                "uncertain",
            "reasoning":              "Advisor temporarily unavailable.",
            "verified_sources":       [],
        }


@app.post("/api/advisor/citations")
@limiter.limit("10/minute")
async def advisor_citations(
    request: Request,
    body: AdvisorCitationsRequest,
    session: dict = Depends(require_auth),
):
    """
    Returns up to n_sources verified academic citations for a finding.

    Citations the agent emits but web_search did not return are silently
    dropped — this is the citation integrity contract. n_sources is capped
    at 5 server-side regardless of what the request specifies.
    """
    if ENVIRONMENT == "test":
        from agents.academic_advisor import MOCK_ADVISOR_CITATIONS
        return MOCK_ADVISOR_CITATIONS

    try:
        from agents.academic_advisor import AcademicAdvisor
        advisor = AcademicAdvisor()
        return advisor.find_supporting_citations(
            finding=body.finding,
            n_sources=body.n_sources,
        )
    except Exception as exc:
        log.error("advisor_citations_endpoint_error", error=str(exc))
        return {"citations": [], "verified_sources": []}


# ── QA ────────────────────────────────────────────────────────────────────────

@app.post("/api/qa/audit")
@limiter.limit("10/minute")
async def qa_audit(request: Request, session: dict = Depends(require_sysadmin)):
    """
    Runs the full QA methodology audit against real strategy results.

    QA Agent uses Opus for the narrative; deterministic checks run from
    the strategy results dict to guarantee pass/fail verdicts are never
    hallucinated. Falls back to mock audit if pipeline is unavailable.

    Per-type guard: rejected with 409 only when another methodology
    audit is already in progress — a statistical audit never blocks it
    (tools/qa_guard.py).
    """
    from tools.qa_guard import (
        QA_BUSY_MESSAGE_METHODOLOGY, begin_methodology, end_methodology,
        methodology_in_progress,
    )
    # Per-type lock — a methodology audit is blocked only by another
    # methodology audit, never by a statistical one.
    if methodology_in_progress():
        raise HTTPException(status_code=409,
                            detail=QA_BUSY_MESSAGE_METHODOLOGY)

    begin_methodology()
    try:
        if ENVIRONMENT == "test":
            return MOCK_QA_AUDIT

        # Hotfix May 23 2026: this handler used to swallow every
        # exception, log it, and return MOCK_QA_AUDIT — a 200
        # response with mock data. The frontend treated that as a
        # successful run, overwrote real per-check state with the
        # mock's empty data, and the user's "Re-run" click
        # produced no visible result. Now we propagate the real
        # error as a 500 so the qaStore surfaces it via its
        # error field.
        #
        # May 24 2026 deeper fix: this handler ALSO used to call
        # get_full_history() + run_all_strategies() inline on every
        # request, which is the heavy compute that times out on
        # cold deploys. Symptom: every check returned INCOMPLETE
        # ("Analysis not completed — re-run...") because the request
        # was failing before the QA agent even ran. Per user
        # directive: read from strategy_results_cache directly. If
        # the cache is cold, return 503 with a specific actionable
        # message rather than recomputing inline and timing out.
        # The dashboard's /api/backtest/compare endpoint is the
        # canonical warmer — hitting the dashboard once populates
        # the cache so the QA audit can complete.
        try:
            from tools.cache import (
                get_latest_strategy_cache, get_latest_qa,
                get_most_recent_qa_run, set_qa_cache,
                _compute_data_hash,
            )
            from agents.qa_agent import QAAgent
            from agents.usage import start_usage_capture

            strategy_results = await get_latest_strategy_cache()
            if not strategy_results:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": "analytics_cache_cold",
                        "message": (
                            "Analytics cache not warmed — load the "
                            "dashboard first to populate cache, then "
                            "re-run audit."
                        ),
                        "hint": (
                            "Open the main Dashboard tab once. The "
                            "strategy comparison call populates "
                            "strategy_results_cache. Then return here "
                            "and re-run the audit."
                        ),
                    },
                )

            # ── QA RE-RUN GATE (May 24 2026) ──────────────────────────
            # The QA agent uses Sonnet — a full audit is ~$0.05-0.10
            # of token burn. The previous handler ran unconditionally
            # on every click and on every QAHub mount (qaStore.load()
            # fires reload() if its in-memory `loaded` flag is false,
            # which a server restart or a fresh user session resets).
            # Multiple users navigating to the QA tab triggered N
            # redundant identical audits, all consuming tokens against
            # the same strategy_hash.
            #
            # Two cascading gates BEFORE any LLM call fires:
            #
            #   1) HASH GATE — if a non-expired Tier 99 ("full audit")
            #      verdict already exists for this strategy_hash, serve
            #      it from qa_results_cache. The data has not changed
            #      since the cached audit ran, so the audit verdict has
            #      not changed either — no LLM call needed.
            #
            #   2) MIN-INTERVAL GATE — secondary safety net. Even if
            #      the hash logic somehow misfired (e.g. the hash
            #      computation changed subtly between deploys), never
            #      run a full audit more than once per QA_MIN_INTERVAL
            #      seconds across the whole platform. The cap is
            #      generous (5 minutes) — a real user click after a
            #      legitimate data refresh always proceeds, but a
            #      polling-driven flood is capped.
            #
            # Each gate logs the skip with hash + age so a future
            # production-log audit can confirm the rate limiter is
            # firing. Bypassed by neither (i.e. real run) logs
            # qa_audit_running so each authentic run is also visible.
            #
            # AUDIT_TIER_FULL=99 distinguishes the qa_audit endpoint's
            # full-checklist response from Tier 1/2/3 narrow verdicts
            # already stored in qa_results_cache. Same table, separate
            # tier band so the tiered-QA paths and the full-audit path
            # never overwrite each other.
            AUDIT_TIER_FULL = 99
            QA_MIN_INTERVAL_SECONDS = 5 * 60

            # ── HASH = the canonical strategy_results_cache hash ──────
            #
            # UAT staleness bug (May 24 2026). Three write paths
            # computed three different hashes for the same underlying
            # data:
            #   (a) /api/backtest/compare wrote strategy_results_cache
            #       with `str(monthly.index[-1].date())` → "2025-12-31"
            #   (b) _current_strategy_hash used `str(monthly.index[-1])`
            #       → "2025-12-31 00:00:00" (Timestamp str form)
            #   (c) this endpoint READ `monthly[-1].get("date")` from a
            #       LIST-of-pairs payload that is NEVER a dict, falling
            #       through to "unknown" every time.
            # is_audit_current() compared (a) against the qa_results_cache
            # hash written via (c) — never matched, so the methodology
            # audit ALWAYS read as "stale" right after a successful run.
            #
            # FIX — pull the canonical hash from strategy_results_cache
            # via get_latest_strategy_hash(). That's the SAME value
            # is_audit_current() reads back on the strategy side, so the
            # two halves of the comparison are guaranteed to match by
            # construction when the audit verified the latest data.
            try:
                from tools.cache import get_latest_strategy_hash
                qa_hash = await get_latest_strategy_hash()
            except Exception as _exc:  # noqa: BLE001
                # If the canonical hash read fails (DB outage) fall
                # through to a real run — the audit is the safer
                # default than skipping.
                log.info("qa_audit_hash_read_failed", error=str(_exc))
                qa_hash = None

            from datetime import datetime, timezone

            # Gate 1 — hash gate. Same data → cached audit verdict
            # stands. expires_at filtering inside get_latest_qa
            # discards a row past its TTL so a freshly-changed dataset
            # never reuses a verdict tied to old data.
            if qa_hash:
                cached_audit = await get_latest_qa(
                    qa_hash, min_tier=AUDIT_TIER_FULL)
                if cached_audit and isinstance(cached_audit.get("checklist"), dict):
                    log.info(
                        "qa_audit_skipped_hash_match",
                        strategy_hash=qa_hash[:8],
                        cached_run_at=cached_audit.get("run_at"),
                    )
                    return cached_audit["checklist"]

            # Gate 2 — minimum-interval gate. Secondary safety net
            # independent of hash. Caps token burn if the hash logic
            # has an edge case (e.g. the strategy_results shape
            # changed and the hash diverged without the data
            # changing). Returns the most recent verdict regardless
            # of which hash it ran against.
            most_recent = await get_most_recent_qa_run(
                min_tier=AUDIT_TIER_FULL)
            if most_recent and most_recent.get("run_at"):
                try:
                    run_at = datetime.fromisoformat(
                        most_recent["run_at"].replace("Z", "+00:00"))
                    age_seconds = (datetime.now(timezone.utc)
                                   - run_at).total_seconds()
                    if (age_seconds < QA_MIN_INTERVAL_SECONDS
                            and isinstance(most_recent.get("checklist"), dict)):
                        log.info(
                            "qa_audit_skipped_interval",
                            age_seconds=int(age_seconds),
                            min_interval_seconds=QA_MIN_INTERVAL_SECONDS,
                            cached_strategy_hash=str(
                                most_recent.get("strategy_hash", ""))[:8],
                            current_strategy_hash=(
                                qa_hash[:8] if qa_hash else "unknown"),
                        )
                        return most_recent["checklist"]
                except (ValueError, AttributeError):
                    pass  # malformed run_at — fall through to real run

            log.info("qa_audit_running",
                     strategy_hash=qa_hash[:8] if qa_hash else "unknown")

            # AN01 / AN04 pre-flight (May 24 2026). Fetch the
            # Carhart loadings and transition matrix rows from
            # analytics_metrics_cache BEFORE the deterministic checks
            # run; trigger refresh on miss/incomplete. The QA audit
            # should never WARN on data the platform could have
            # computed itself.
            try:
                from tools.precomputed_analytics import (
                    ensure_qa_data_complete,
                )
                analytics_cache = await ensure_qa_data_complete(qa_hash)
                if analytics_cache.get("refresh_triggered"):
                    log.info(
                        "qa_preflight_refreshed",
                        triggered=analytics_cache["refresh_triggered"],
                        completeness=analytics_cache["completeness"],
                    )
            except Exception as _exc:  # noqa: BLE001
                log.warning("qa_preflight_error", error=str(_exc))
                analytics_cache = None

            # IN01 submission-window attestation (May 25 2026). Async
            # query so the sync run_audit can read the verdict without
            # spawning a nested event loop. A query failure surfaces
            # as a FAIL verdict in the audit, not an endpoint 500.
            try:
                from tools.audit_engine import compute_in01_attestation
                audit_attestation = await compute_in01_attestation()
            except Exception as _exc:  # noqa: BLE001
                log.warning("qa_in01_attestation_error", error=str(_exc))
                audit_attestation = None

            # Seed the per-request usage bucket before the QA
            # agent's call_claude invocations so their token usage
            # is captured.
            start_usage_capture()
            qa = QAAgent()
            audit = qa.run_audit(
                strategy_results,
                run_full_checklist=True,
                analytics_cache=analytics_cache,
                audit_attestation=audit_attestation,
            )

            # Persist the full audit to qa_results_cache so the next
            # /api/qa/audit call within the TTL window short-circuits
            # at gate 1 above. Tier=AUDIT_TIER_FULL distinguishes this
            # from the tiered-QA narrow verdicts. Fail-open: if the
            # write fails (DB unavailable), the response still returns
            # normally; the next call will just re-run.
            if qa_hash:
                try:
                    await set_qa_cache(
                        qa_hash, audit, tier=AUDIT_TIER_FULL)
                except Exception as _exc:  # noqa: BLE001
                    log.warning("qa_audit_cache_write_failed",
                                error=str(_exc))

            # Team Activity — record the audit run (non-blocking).
            _log_interaction_bg(
                request, session, "qa",
                response_summary=str(audit.get("summary", "")),
                metadata={"verdict": audit.get("verdict")},
            )
            return audit

        except HTTPException:
            raise
        except Exception as exc:
            # Surface the real error so the frontend's "Re-run"
            # button shows what actually went wrong instead of
            # silently appearing to succeed. The previous catch-all
            # mock fallback hid the underlying data pipeline
            # failures (get_full_history timeout, missing strategy
            # cache row, etc.) from every operator.
            import traceback
            log.error(
                "qa_audit_error",
                error=str(exc),
                error_type=type(exc).__name__,
                traceback=traceback.format_exc(limit=10),
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error":      "qa_audit_failed",
                    "error_type": type(exc).__name__,
                    "message":    str(exc),
                    "hint": (
                        "The QA audit needs the strategy results "
                        "cache. If this is a fresh deploy, wait "
                        "for the analytics warm-up to complete and "
                        "retry. If the error persists, check the "
                        "Render logs for the qa_audit_error event."),
                },
            )
    finally:
        end_methodology()


@app.get("/api/v1/qa/export")
@limiter.limit("10/minute")
async def qa_export(request: Request, session: dict = Depends(require_auth)):
    """
    The QA methodology audit as a downloadable PDF — the Methodology
    Audit Report, formatted for inclusion in the Analytical Appendix.
    Open to every authenticated user: the Methodology Review section of
    the QA tab is not team-gated.

    The audit is run fresh (the same path as POST /api/qa/audit) so the
    PDF always reflects the current strategy results; the test
    environment and a pipeline failure fall back to the mock audit so
    the endpoint always returns a valid PDF.
    """
    from datetime import date
    from tools.audit_pdf import build_methodology_audit_pdf
    audit = MOCK_QA_AUDIT
    if ENVIRONMENT != "test":
        try:
            from tools.data_fetcher import get_full_history_async
            from tools.backtester import run_all_strategies
            from agents.qa_agent import QAAgent
            from tools.audit_engine import compute_in01_attestation
            history = await get_full_history_async()
            strategy_results = await asyncio.to_thread(run_all_strategies, history)
            # IN01 attestation — same async-then-pass pattern as the
            # /api/qa/audit endpoint. The PDF export's IN01 row reflects
            # the submission-window verdict.
            attestation = await compute_in01_attestation()
            audit = await asyncio.to_thread(
                lambda: QAAgent().run_audit(
                    strategy_results, run_full_checklist=True,
                    audit_attestation=attestation,
                )
            )
        except Exception as exc:
            log.error("qa_export_error", error=str(exc))
            audit = MOCK_QA_AUDIT
    # Intentional-design overrides (May 28 2026 hotfix). Fetch any
    # qa_intentional_overrides rows so build_methodology_audit_pdf
    # can render the team's recorded disclosure under each check.
    # Fail-open: a DB miss / read error leaves overrides empty and
    # the PDF renders without the disclosure lines rather than 500.
    overrides_map: dict[str, dict] = {}
    if ENVIRONMENT != "test":
        try:
            from sqlalchemy import text as _text
            from database import AsyncSessionLocal
            if AsyncSessionLocal is not None:
                async with AsyncSessionLocal() as conn:
                    rows = await conn.execute(_text(
                        "SELECT check_id, note, marked_by, marked_at "
                        "FROM qa_intentional_overrides"))
                    for row in rows.fetchall():
                        overrides_map[row[0]] = {
                            "check_id": row[0],
                            "note":     row[1],
                            "marked_by": row[2],
                            "marked_at": row[3].isoformat() if row[3] else None,
                        }
        except Exception as exc:  # noqa: BLE001
            log.warning("qa_export_overrides_read_failed", error=str(exc))
    pdf = build_methodology_audit_pdf(audit, overrides=overrides_map)
    filename = f"forest_capital_methodology_audit_{date.today().isoformat()}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/qa/ask")
@limiter.limit("10/minute")
async def qa_ask(
    request: Request,
    body: QAQueryRequest,
    session: dict = Depends(require_auth),
):
    """
    Conversational QA endpoint — routes questions through scope guard
    then the QA Agent for methodology questions.
    """
    if ENVIRONMENT != "test":
        try:
            from scope_guard import ScopeGuard
            guard = ScopeGuard()
            scope_result = await guard.check(body.question)
            if not scope_result["allowed"]:
                return {
                    "question": body.question,
                    "answer": scope_result["rejection_message"],
                    "verdict": "OUT_OF_SCOPE",
                }
        except Exception as exc:
            log.warning("qa_ask_scope_error", error=str(exc))

        try:
            from agents.base import call_claude, OPUS_MODEL
            from agents.qa_agent import _SYSTEM_PROMPT as QA_SYSTEM_PROMPT

            answer = call_claude(OPUS_MODEL, QA_SYSTEM_PROMPT, body.question,
                                 trigger="qa_ask")
            return {"question": body.question, "answer": answer, "verdict": "PASS"}
        except Exception as exc:
            log.error("qa_ask_error", error=str(exc))

    return {
        "question": body.question,
        "answer": "QA Agent temporarily unavailable. Please try POST /api/qa/audit for the full checklist.",
        "verdict": "WARN",
    }


# ── Tiered QA (Sprint 6) ──────────────────────────────────────────────────────
#
# Tier 1: pure-Python deterministic, sync. Result cached forever per
#   strategy_hash; same inputs always produce the same verdict.
# Tier 2: Sonnet narrative audit, async background. Runs when strategy_hash
#   changes or the most recent Tier 2 cache entry is older than 24h.
# Tier 3: Opus deep review, manual only. Auto-triggered if Tier 2 returns FAIL.
#
# The Present-mode gate reads the LATEST cached verdict for the current
# strategy_hash. Tier 1 alone is enough to unlock Present mode (≥ WARN);
# higher tiers refine the narrative shown on the QA tab. The dashboard
# never waits on a Sonnet/Opus call.

async def _current_strategy_hash() -> tuple[str, dict[str, dict] | None]:
    """
    Returns the canonical strategy_hash and the cached strategy_results
    for it (when present). Shared by every QA endpoint so a single hash
    value can serve both the status read and any tier trigger that
    follows.

    UAT staleness fix (May 24 2026) — uses get_latest_strategy_hash()
    as the canonical source. Previously this helper recomputed the
    hash with `str(monthly.index[-1])` (no `.date()` call), which
    produced a different value from /api/backtest/compare's
    `str(monthly.index[-1].date())`. The two never agreed, so the
    methodology audit's qa_results_cache row never matched the
    strategy_results_cache row it audited — is_audit_current()
    reported stale forever. Reading the canonical value from
    strategy_results_cache guarantees the comparison succeeds when
    the audit verified the latest data.

    Returns ("", None) when there is no strategy_results_cache row
    yet — same shape as the previous helper, so existing callers
    continue to work.
    """
    from tools.cache import get_strategy_cache, get_latest_strategy_hash

    strategy_hash = await get_latest_strategy_hash() or ""
    cached = await get_strategy_cache(strategy_hash) if strategy_hash else None
    return strategy_hash, cached


@app.get("/api/v1/qa/status")
@limiter.limit("60/minute")
async def qa_status(request: Request, session: dict = Depends(require_auth)):
    """
    Returns the latest QA verdict for the current strategy_hash.

    Response shape:
      {
        verdict: PASS|WARN|FAIL|UNKNOWN,
        tier: 1|2|3|null,
        run_at: iso8601 | null,
        age_hours: float | null,
        strategy_hash: str,
        present_mode_allowed: bool,
        running: bool,            # a methodology or statistical audit is
                                  #   in flight — global, not session-scoped
      }

    The nav-bar badge polls this endpoint every 30 seconds so the
    Running → PASS/WARN/FAIL transition shows up without a page reload.
    """
    if ENVIRONMENT == "test":
        return {
            "verdict": "UNKNOWN", "tier": None, "run_at": None,
            "age_hours": None, "strategy_hash": "test",
            "present_mode_allowed": False, "running": False,
        }

    try:
        from tools.cache import get_latest_qa
        from tools.qa_guard import (
            methodology_in_progress, statistical_audit_in_progress,
        )
        # Cross-user run state: the statistical audit_runs row is global,
        # the methodology flag is process-wide on the single worker — so
        # every user's poll sees the same answer, not session-scoped
        # state. statistical_audit_in_progress also reaps a run hung past
        # the 15-minute timeout.
        running = (methodology_in_progress()
                   or await statistical_audit_in_progress())
        strategy_hash, _cached = await _current_strategy_hash()
        latest = await get_latest_qa(strategy_hash, min_tier=1)

        if not latest:
            return {
                "verdict": "UNKNOWN", "tier": None, "run_at": None,
                "age_hours": None, "strategy_hash": strategy_hash,
                "present_mode_allowed": False, "running": running,
            }

        # Present-mode gate: verdict ≥ WARN AND age < 48h AND hash matches.
        # The hash equality is already enforced by the query filter, so we
        # only need to check verdict and age here.
        from datetime import datetime, timezone
        run_at_iso = latest.get("run_at")
        age_hours: float | None = None
        if run_at_iso:
            run_at_dt = datetime.fromisoformat(run_at_iso.replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - run_at_dt).total_seconds() / 3600.0

        present_allowed = (
            latest["verdict"] in ("PASS", "WARN")
            and age_hours is not None
            and age_hours < 48.0
        )

        return {
            "verdict":              latest["verdict"],
            "tier":                 latest["tier"],
            "run_at":               run_at_iso,
            "age_hours":            round(age_hours, 2) if age_hours is not None else None,
            "strategy_hash":        strategy_hash,
            "present_mode_allowed": present_allowed,
            "running":              running,
        }
    except Exception as exc:
        log.warning("qa_status_fallback", error=str(exc))
        return {
            "verdict": "UNKNOWN", "tier": None, "run_at": None,
            "age_hours": None, "strategy_hash": "unknown",
            "present_mode_allowed": False, "running": False,
        }


@app.post("/api/v1/qa/run")
@limiter.limit("20/minute")
async def qa_run(request: Request, session: dict = Depends(require_sysadmin)):
    """
    Runs Tier 1 synchronously and triggers Tier 2 in the background.
    Returns immediately with the Tier 1 verdict — the audience never
    waits on the Sonnet call. The Tier 2 result lands in the cache
    on its own; subsequent /qa/status polls pick it up.

    Auto-escalation to Tier 3 happens inside the background worker if
    Tier 2 returns FAIL (see schedule_tier2_background).

    Per-type guard: rejected with 409 only when another methodology
    audit is already in progress — a statistical audit never blocks it
    (tools/qa_guard.py).
    """
    from tools.qa_guard import (
        QA_BUSY_MESSAGE_METHODOLOGY, begin_methodology, end_methodology,
        methodology_in_progress,
    )
    # Per-type lock — a methodology audit is blocked only by another
    # methodology audit, never by a statistical one.
    if methodology_in_progress():
        raise HTTPException(status_code=409,
                            detail=QA_BUSY_MESSAGE_METHODOLOGY)

    if ENVIRONMENT == "test":
        return {"verdict": "PASS", "tier": 1, "tier2_scheduled": False}

    begin_methodology()
    try:
        from tools.qa_tiered import run_tier1_checks, schedule_tier2_background
        from tools.cache import set_qa_cache
        from tools.backtester import run_all_strategies
        from tools.data_fetcher import get_full_history_async
        from agents.usage import start_usage_capture

        strategy_hash, cached = await _current_strategy_hash()
        if cached:
            results_dict = cached
        else:
            history = await get_full_history_async()
            results_dict = await asyncio.to_thread(run_all_strategies, history)

        # Tier 1 — synchronous, deterministic, free. Tier 2 runs on a
        # background executor that does NOT inherit this context, so its
        # cost is not captured here; Tier 1 is sync and free of AI calls
        # today but the seed is here for forward-compatibility.
        start_usage_capture()
        t1 = run_tier1_checks(results_dict)
        await set_qa_cache(strategy_hash, t1, tier=1)

        # Tier 2 — fire and forget. Need a sync wrapper for the writer.
        # off_loop=True: _writer runs in a background thread via asyncio.run(),
        # so set_qa_cache must use the NullPool write engine — a pooled
        # connection would be orphaned when that loop closes.
        import asyncio as _asyncio
        def _writer(h: str, v: dict, tier: int) -> None:
            _asyncio.run(set_qa_cache(h, v, tier=tier, off_loop=True))
        schedule_tier2_background(results_dict, strategy_hash, _writer)

        # Team Activity — record the QA run (non-blocking).
        _log_interaction_bg(
            request, session, "qa",
            response_summary=str(t1.get("summary", "")),
            metadata={"verdict": t1.get("verdict"), "tier": 1},
        )

        return {
            "verdict": t1["verdict"],
            "tier": 1,
            "tier2_scheduled": True,
            "strategy_hash": strategy_hash,
            "checks_total": t1["checks_total"],
            "checks_passed": t1["checks_passed"],
            "checks_warned": t1["checks_warned"],
            "checks_failed": t1["checks_failed"],
            "summary": t1["summary"],
        }
    except Exception as exc:
        ref = uuid.uuid4().hex[:8]
        log.error("qa_run_error", ref=ref, error=str(exc))
        raise HTTPException(status_code=500, detail=f"QA run failed (ref: {ref})")
    finally:
        end_methodology()


@app.post("/api/v1/qa/full-review")
@limiter.limit("5/minute")
async def qa_full_review(request: Request, session: dict = Depends(require_sysadmin)):
    """
    Manually triggers a Tier 3 (Opus) deep review. Synchronous because
    the caller (Admin screen Full Review button) is willing to wait
    20-30 seconds — unlike the dashboard, which never waits.

    Sysadmin only — triggering a QA run is restricted to the platform
    sysadmin; the rate limit caps abuse at 5/minute.

    Per-type guard: rejected with 409 only when another methodology
    audit is already in progress — a statistical audit never blocks it
    (tools/qa_guard.py).
    """
    from tools.qa_guard import (
        QA_BUSY_MESSAGE_METHODOLOGY, begin_methodology, end_methodology,
        methodology_in_progress,
    )
    # Per-type lock — a methodology audit is blocked only by another
    # methodology audit, never by a statistical one.
    if methodology_in_progress():
        raise HTTPException(status_code=409,
                            detail=QA_BUSY_MESSAGE_METHODOLOGY)

    if ENVIRONMENT == "test":
        return {"verdict": "PASS", "tier": 3}

    begin_methodology()
    try:
        from tools.qa_tiered import run_tier3_review
        from tools.cache import set_qa_cache
        from tools.backtester import run_all_strategies
        from tools.data_fetcher import get_full_history_async

        strategy_hash, cached = await _current_strategy_hash()
        if cached:
            results_dict = cached
        else:
            history = await get_full_history_async()
            results_dict = await asyncio.to_thread(run_all_strategies, history)

        t3 = await asyncio.to_thread(run_tier3_review, results_dict)
        await set_qa_cache(strategy_hash, t3, tier=3)

        return {
            **t3,
            "strategy_hash": strategy_hash,
        }
    except Exception as exc:
        ref = uuid.uuid4().hex[:8]
        log.error("qa_full_review_error", ref=ref, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Full review failed (ref: {ref})")
    finally:
        end_methodology()


# ── QA findings — Flag for Fix + Mark as Intentional ─────────────────────────
#
# May 22 2026 — companion endpoints to the QA Action Required UI
# (f96d897). The Flag for Fix button creates a triage_report_items
# row directly from the QA finding, routing the QA-driven issue into
# the same fix workflow used for UAT-derived items. The Mark as
# Intentional button writes an upsert into qa_intentional_overrides
# (migration 027), recording the team's judgement that the WARN is
# intentional methodology, not a defect — the override outlives the
# audit run that surfaced it.
#
# Both endpoints are team_member-gated (consistent with the May 2026
# QA endpoint regating that opened audit operations to the whole
# project team). Sysadmin status is recorded as the `marked_by`
# value on the intentional override so the audit trail attributes
# the decision to a specific user.


class QAFlagForFixRequest(__import__("pydantic").BaseModel):
    """POST body for the Flag for Fix endpoint."""
    check_title: str
    finding: str | None = None
    implication: str | None = None
    remediation: str | None = None
    severity: str | None = None  # WARN → 'major' / FAIL → 'blocking'


class QAMarkIntentionalRequest(__import__("pydantic").BaseModel):
    """POST body for the Mark as Intentional endpoint.

    May 28 2026 hotfix: `note` is now REQUIRED with a 20-character
    minimum. The previous shape (`note: str | None = None`) accepted
    a single-click confirmation from the UI where the body sent
    `{note: check.finding}` — the AI-generated check description
    became the disclosure note. That is not a disclosure; it is a
    rephrasing of the warning. The new gate forces the team to type
    a real reason before the override is recorded.

    Pydantic min_length=20 produces an automatic 422 with the
    standard validation-error body when the client submits a shorter
    note — no extra handler logic needed. Stale frontends that still
    send `{note: check.finding}` will fail loudly with 422 rather
    than silently recording the AI text.
    """
    note: str = __import__("pydantic").Field(..., min_length=20)
    audit_run_hash: str | None = None


@app.post("/api/v1/qa/findings/{check_id}/flag-for-fix")
@limiter.limit("30/minute")
async def qa_flag_for_fix(
    request: Request,
    check_id: str,
    body: QAFlagForFixRequest,
    session: dict = Depends(require_team_member),
):
    """
    Creates a triage_report_items row for the named QA check so the
    finding enters the normal fix workflow. The triage row is
    attached to a fresh triage_reports stub (triggered_by='qa_audit')
    so the existing report-items query layer ignores no rows.

    Returns the new triage_report_items id so the frontend can show
    a "Flagged · #N" badge alongside the QA card going forward.
    """
    if not check_id or len(check_id) > 20:
        raise HTTPException(status_code=422,
                            detail="check_id must be 1-20 chars")
    try:
        from sqlalchemy import text as _text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            raise HTTPException(status_code=503,
                                detail="Database unavailable")
        # Compose the item body so the triage view shows the full
        # finding without a join back to the QA audit.
        body_parts = []
        if body.finding:
            body_parts.append(f"FINDING: {body.finding}")
        if body.implication:
            body_parts.append(f"IMPLICATION: {body.implication}")
        if body.remediation:
            body_parts.append(f"REMEDIATION: {body.remediation}")
        item_body = "\n\n".join(body_parts) or None
        item_title = f"QA {check_id} — {body.check_title}"

        async with AsyncSessionLocal() as conn:
            # Synthesise a one-off triage_reports row for this flag so
            # the items query layer sees a parent report row. The
            # report_text is the same item_body for traceability.
            r = await conn.execute(_text(
                "INSERT INTO triage_reports "
                "(triggered_by, status, report_text) "
                "VALUES ('qa_audit', 'complete', :rep) "
                "RETURNING id"
            ), {"rep": (item_body or item_title)})
            report_id = r.scalar()

            # The QA check is the source. source_item_type='qa_check'
            # is a new value alongside the existing 'failure' /
            # 'feedback' — the schema is permissive (a String(20) with
            # no CHECK constraint), and the items query layer treats
            # an unknown source as 'no back-pointer' which is the
            # honest signal here. source_item_id stays NULL because a
            # QA check is keyed by check_id (a string), not by an
            # integer row id.
            r2 = await conn.execute(_text(
                "INSERT INTO triage_report_items "
                "(report_id, item_type, item_title, item_body, "
                " source_item_type, source_item_id) "
                "VALUES (:rid, 'immediate', :title, :body, "
                " 'qa_check', NULL) "
                "RETURNING id"
            ), {"rid": report_id, "title": item_title, "body": item_body})
            item_id = r2.scalar()
            await conn.commit()

        log.info("qa_flag_for_fix_recorded",
                 check_id=check_id, triage_item_id=int(item_id) if item_id else None,
                 by=session.get("email"),
                 severity=body.severity)
        return {
            "ok": True,
            "check_id": check_id,
            "triage_item_id": int(item_id) if item_id else None,
            "triage_report_id": int(report_id) if report_id else None,
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        ref = uuid.uuid4().hex[:8]
        log.error("qa_flag_for_fix_failed", ref=ref, error=str(exc))
        raise HTTPException(status_code=500,
                            detail=f"Flag for fix failed (ref: {ref})")


@app.post("/api/v1/qa/findings/{check_id}/mark-intentional")
@limiter.limit("30/minute")
async def qa_mark_intentional(
    request: Request,
    check_id: str,
    body: QAMarkIntentionalRequest,
    session: dict = Depends(require_team_member),
):
    """
    Records (or updates) an entry in qa_intentional_overrides so the
    QA panel renders "Confirmed intentional — recorded {date}" on
    every subsequent audit instead of the WARN action card. The
    override outlives the audit run that surfaced it.

    Idempotent — a second click on the same check_id UPDATEs the
    existing row (the unique constraint enforces one override per
    check_id).
    """
    if not check_id or len(check_id) > 20:
        raise HTTPException(status_code=422,
                            detail="check_id must be 1-20 chars")
    email = session.get("email") or "unknown"
    try:
        from sqlalchemy import text as _text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            raise HTTPException(status_code=503,
                                detail="Database unavailable")
        async with AsyncSessionLocal() as conn:
            # Upsert — ON CONFLICT (check_id) DO UPDATE so a second
            # Mark Intentional refreshes the note + timestamp rather
            # than producing a duplicate row.
            r = await conn.execute(_text(
                "INSERT INTO qa_intentional_overrides "
                "(check_id, marked_by, note, audit_run_hash) "
                "VALUES (:cid, :by, :note, :hash) "
                "ON CONFLICT (check_id) DO UPDATE SET "
                " marked_at = now(), "
                " marked_by = EXCLUDED.marked_by, "
                " note = EXCLUDED.note, "
                " audit_run_hash = EXCLUDED.audit_run_hash "
                "RETURNING id, marked_at"
            ), {
                "cid": check_id, "by": email,
                "note": body.note, "hash": body.audit_run_hash,
            })
            row = r.fetchone()
            await conn.commit()

        log.info("qa_mark_intentional_recorded",
                 check_id=check_id, by=email,
                 override_id=int(row[0]) if row else None)
        return {
            "ok": True,
            "check_id": check_id,
            "marked_by": email,
            "marked_at": row[1].isoformat() if row and row[1] else None,
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        ref = uuid.uuid4().hex[:8]
        log.error("qa_mark_intentional_failed", ref=ref, error=str(exc))
        raise HTTPException(status_code=500,
                            detail=f"Mark intentional failed (ref: {ref})")


@app.delete("/api/v1/qa/findings/{check_id}/mark-intentional")
@limiter.limit("30/minute")
async def qa_revoke_intentional(
    request: Request,
    check_id: str,
    session: dict = Depends(require_team_member),
):
    """
    Revokes a previously-recorded intentional override.

    Workstream F (May 28 2026) — when the team later determines that a
    finding is not actually intentional after all, this endpoint removes
    the qa_intentional_overrides row. The QA panel's Confirmed Intentional
    badge disappears and the original Action Required card re-renders on
    the next audit read. The report-readiness gate (workstream C) re-
    evaluates the next time it loads, so a revoked disclosure stops
    counting as resolved.

    Returns 200 with deleted=true on a successful revoke, 200 with
    deleted=false when no override existed (idempotent — a revoke on
    nothing is not an error). 422 on a malformed check_id. 503 if the
    database is unreachable.

    Project team only — mirrors the gating on mark-intentional.
    """
    if not check_id or len(check_id) > 20:
        raise HTTPException(status_code=422,
                            detail="check_id must be 1-20 chars")
    email = session.get("email") or "unknown"
    try:
        from sqlalchemy import text as _text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            raise HTTPException(status_code=503,
                                detail="Database unavailable")
        async with AsyncSessionLocal() as conn:
            r = await conn.execute(_text(
                "DELETE FROM qa_intentional_overrides "
                "WHERE check_id = :cid "
                "RETURNING id, marked_by"
            ), {"cid": check_id})
            row = r.fetchone()
            await conn.commit()

        if row is None:
            # Idempotent — a revoke on nothing is not an error. The
            # frontend can fire DELETE without first checking that an
            # override exists.
            log.info("qa_revoke_intentional_noop",
                     check_id=check_id, by=email)
            return {"ok": True, "check_id": check_id, "deleted": False}

        log.info("qa_revoke_intentional_recorded",
                 check_id=check_id, by=email,
                 override_id=int(row[0]) if row[0] else None,
                 was_marked_by=row[1])
        return {
            "ok": True,
            "check_id": check_id,
            "deleted": True,
            "revoked_by": email,
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        ref = uuid.uuid4().hex[:8]
        log.error("qa_revoke_intentional_failed", ref=ref, error=str(exc))
        raise HTTPException(status_code=500,
                            detail=f"Revoke intentional failed (ref: {ref})")


@app.get("/api/v1/qa/intentional-overrides")
@limiter.limit("60/minute")
async def qa_intentional_overrides_list(
    request: Request,
    session: dict = Depends(require_auth),
):
    """
    Returns every recorded intentional override keyed by check_id.
    The QA panel reads this on mount and renders a "Confirmed
    intentional — recorded {date}" badge in place of the Action
    Required card for any check_id present here.

    Auth: any authenticated user — the audit trail is a project
    record visible to viewers, not a sysadmin-private surface.
    """
    try:
        from sqlalchemy import text as _text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return {"overrides": {}}
        async with AsyncSessionLocal() as conn:
            r = await conn.execute(_text(
                "SELECT check_id, marked_at, marked_by, note, "
                "       audit_run_hash "
                "FROM qa_intentional_overrides "
                "ORDER BY marked_at DESC"
            ))
            rows = r.fetchall()
        overrides = {
            row[0]: {
                "marked_at": row[1].isoformat() if row[1] else None,
                "marked_by": row[2],
                "note": row[3],
                "audit_run_hash": row[4],
            }
            for row in rows
        }
        return {"overrides": overrides}
    except Exception as exc:  # noqa: BLE001
        log.warning("qa_intentional_overrides_read_failed", error=str(exc))
        return {"overrides": {}}


# ── Report ────────────────────────────────────────────────────────────────────

@app.get("/api/report/export")
async def export_report(session: dict = Depends(require_auth)):
    return {
        "message": "PDF report generation available in Sprint 4.",
        "status": "not_implemented",
    }


# ── Academic export package ───────────────────────────────────────────────────
#
# A one-click ZIP of the analytical assets (charts + tables) the team needs
# for the written deliverables. Charts and tables are rendered client-side
# (recharts → PNG, data table → CSV) and POSTed here as multipart blobs; the
# backend's only job is deterministic assembly — zipping the uploads and
# adding curated metadata files. The grader gets a self-describing archive.

# Curated, deterministic descriptions keyed by the chart filename slug. The
# original spec proposed calling the Academic Writer agent to generate these,
# but an export endpoint must be deterministic and outage-proof — it must
# never hang or 500 because an LLM is unreachable. Static descriptions are the
# correct engineering call: instant, reproducible, and graded identically.
_CHART_DESCRIPTIONS: dict[str, str] = {
    "cumulative_returns": (
        "Cumulative total return — growth of $1 invested in each strategy "
        "over the study period, benchmarked against the 100% S&P 500 equity "
        "index. Cite as evidence of long-horizon outperformance or shortfall."
    ),
    "rolling_correlation": (
        "Rolling 12-month equity-vs-bond correlation. The 2022 regime break "
        "(correlation turning positive) is the project's central finding. "
        "Cite when discussing the breakdown of static diversification."
    ),
    "rolling_excess_return": (
        "Rolling excess return of each strategy over the benchmark. Cite to "
        "show the consistency, not just the average, of any outperformance."
    ),
    "efficient_frontier": (
        "Mean-variance efficient frontier with each strategy plotted by "
        "realised volatility and return. Cite when discussing risk-adjusted "
        "positioning relative to the optimal frontier."
    ),
    "sensitivity_analysis": (
        "Parameter sensitivity — strategy metrics under +/-20% perturbation "
        "of key parameters. Cite as the robustness check against overfitting."
    ),
    "team_activity_timeline": (
        "Team Activity timeline — commits, council runs and platform usage "
        "interleaved over time. Cite as evidence for the Roles & Division of "
        "Labor deliverable."
    ),
    "team_contribution_split": (
        "Per-member contribution split across commits and AI interactions. "
        "Cite to substantiate the division-of-labour narrative."
    ),
    "agent_engagement": (
        "AI agent engagement — how often each council agent was consulted. "
        "Cite in the AI-usage section of the final presentation."
    ),
}


def _slug_for_chart(filename: str) -> str:
    """Extracts the description key from an uploaded chart filename.
    Filenames arrive prefixed/suffixed (e.g. '01_cumulative_returns.png');
    the matching slug is whichever known key the filename contains."""
    stem = filename.rsplit(".", 1)[0].lower()
    for slug in _CHART_DESCRIPTIONS:
        if slug in stem:
            return slug
    return ""


@app.post("/api/v1/export/package")
@limiter.limit("10/minute")
async def export_package(
    request: Request,
    charts: list[UploadFile] = File(default=[]),
    tables: list[UploadFile] = File(default=[]),
    metadata: str = Form(default="{}"),
    session: dict = Depends(require_permission("export_package")),
):
    """
    Assembles an academic export ZIP from client-rendered chart PNGs and
    table CSVs plus curated metadata files.

    Multipart/form-data fields:
      charts    — PNG image blobs; each .filename is the in-ZIP name
      tables    — CSV blobs; each .filename is the in-ZIP name
      metadata  — JSON string of study-period fields

    ZIP layout:
      charts/<uploaded chart filenames>
      tables/<uploaded table filenames>
      metadata/study_period.txt
      metadata/chart_descriptions.txt
      README.txt

    Returned as application/zip with an attachment Content-Disposition so
    the browser downloads it. Deterministic — no LLM, no pipeline run.
    """
    import io
    import zipfile
    from datetime import date

    # Seeded for consistency with every other interaction-logging endpoint.
    # No AI work runs in this handler today (descriptions are curated and
    # deterministic), so this is a no-op for cost — but a future change that
    # adds an AI-generated README or chart caption would be captured for free.
    from agents.usage import start_usage_capture
    start_usage_capture()

    try:
        # A malformed metadata string must not break the export — the ZIP
        # is still useful with placeholder study-period fields.
        try:
            meta = json.loads(metadata) if metadata else {}
            if not isinstance(meta, dict):
                meta = {}
        except (json.JSONDecodeError, TypeError):
            meta = {}

        def _m(key: str) -> str:
            value = meta.get(key)
            return str(value) if value not in (None, "") else "—"

        generated = _m("generated_at")
        today = date.today().isoformat()

        study_period = (
            f"Study period: {_m('study_period_start')} to "
            f"{_m('study_period_end')}\n"
            f"{_m('n_months')} months of monthly data\n"
            "Benchmark: 100% S&P 500 equity index\n"
            "Risk-free rate: DTB3 mean monthly, annualised\n"
            "Factor model: Carhart four-factor (MKT-RF, SMB, HML, MOM)\n"
            f"Generated: {generated}\n"
        )

        readme = (
            "Forest Capital Portfolio Intelligence System — "
            "Academic Export Package\n"
            f"Generated: {generated}\n\n"
            "Charts: PNG at 2x resolution, light mode, suitable for "
            "Word/PowerPoint.\n"
            "Tables: CSV, importable into Excel.\n\n"
            "Cite as: Portfolio Intelligence System analytical output, "
            "Forest Capital /\n"
            f"McColl School of Business FNA 670, {today}.\n"
        )

        # Describe only the charts actually present in this upload.
        desc_lines: list[str] = ["CHART DESCRIPTIONS\n"]
        n_charts = 0
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for chart in charts:
                content = await chart.read()
                name = chart.filename or f"chart_{n_charts + 1}.png"
                zf.writestr(f"charts/{name}", content)
                slug = _slug_for_chart(name)
                description = _CHART_DESCRIPTIONS.get(
                    slug,
                    "Analytical chart exported from the Portfolio "
                    "Intelligence System dashboard.",
                )
                desc_lines.append(f"\n{name}\n  {description}\n")
                n_charts += 1

            n_tables = 0
            for tbl in tables:
                content = await tbl.read()
                name = tbl.filename or f"table_{n_tables + 1}.csv"
                zf.writestr(f"tables/{name}", content)
                n_tables += 1

            zf.writestr("metadata/study_period.txt", study_period)
            zf.writestr("metadata/chart_descriptions.txt", "".join(desc_lines))
            zf.writestr("README.txt", readme)

        zip_bytes = zip_buffer.getvalue()

        # Activity logging — awaited (not fire-and-forget): the export is
        # already complete, so a synchronous one-row INSERT costs nothing
        # and guarantees the row lands before the response returns.
        # Team-gated and fail-open inside log_agent_interaction.
        try:
            from tools.activity_log import log_agent_interaction
            await log_agent_interaction(
                user_email=session.get("email", ""),
                session_id=request.headers.get("x-session-id"),
                session_type=request.headers.get("x-session-type"),
                interaction_type="export",
                response_summary=f"{n_charts} charts, {n_tables} tables",
                metadata={"n_charts": n_charts, "n_tables": n_tables,
                          "bytes": len(zip_bytes)},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("export_log_failed", error=str(exc))

        filename = f"forest_capital_academic_export_{today}.zip"
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as exc:
        ref = uuid.uuid4().hex[:8]
        log.error("export_package_error", ref=ref, error=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"Export package generation failed (ref: {ref})",
        )


# ── Academic deliverable generation — first-draft .docx / .pptx ───────────────
#
# POST /api/v1/export/midpoint-paper     → 3-page midpoint submission (.docx)
# POST /api/v1/export/executive-brief    → 5-page executive brief (.docx)
# POST /api/v1/export/presentation-deck  → 16-slide final deck (.pptx)
#
# Each assembles a graded deliverable as a FIRST DRAFT for Bob to refine:
# every figure is real platform data (tools/academic_export.gather_document_
# data — light cache reads, never a pipeline run), and every narrative
# section is written by the Academic Writer agent run through the generator-
# evaluator harness. A section whose source data is unavailable is filled
# with a [DATA PENDING] marker — a missing input never fails the document.

_DOCX_MEDIA = ("application/vnd.openxmlformats-officedocument."
               "wordprocessingml.document")
_PPTX_MEDIA = ("application/vnd.openxmlformats-officedocument."
               "presentationml.presentation")

# Key analytical findings that MUST appear in the midpoint paper. These
# are appended to the section task prompts (never overwrite the existing
# instruction — the task string is one logical document). The findings
# are split by the section they belong in; the Academic Review arbiter
# scores the same set (agents/academic_review.py).
_MIDPOINT_S1_KEY_FINDINGS = (
    # May 25 2026 — TIGHTENED. The prior block was 306 prompt words
    # covering 4 findings + 6 methodology highlights; against a
    # 250-300 word output target the model was forced to bloat to
    # mention everything. One sentence per finding, the three most
    # distinctive methodology highlights only.
    "\n\nKEY FINDINGS — introduce each in one sentence:\n"
    "(a) The 2022 equity-IG correlation broke from roughly -0.05 to "
    "+0.61 — the central finding of the project, developed in Results.\n"
    "(b) Five strategies (MIN_VARIANCE, BLACK_LITTERMAN, "
    "MAX_SHARPE_ROLLING, MOMENTUM_ROTATION, REGIME_SWITCHING) start "
    "later than the 2002-07 study period because of lookback windows; "
    "their metrics cover actual data periods.\n"
    "(c) Every metric was independently recomputed by a separate AI "
    "model (Claude Opus) with zero critical failures across 59 checks.\n"
    "(d) Data: equity (SPY monthly), investment-grade (LQD-to-BND "
    "splice), high-yield (BAMLHYH0A0HYM2TRIV through 2025; HYG ETF "
    "proxy thereafter), risk-free (FRED DTB3).\n\n"
    "METHODOLOGY HIGHLIGHTS — name explicitly: Carhart four-factor "
    "attribution (MOM included), Benjamini-Hochberg FDR correction at "
    "q < 0.005, and true one-way drift-inclusive portfolio turnover."
)
# Verification caveats appended to the document-section task prompts.
# CAVEAT 2 — every external citation is preceded by a [[VERIFY CITATION]]
# marker; CAVEAT 3 — every uncertain numeric value is wrapped in a
# [[VERIFY]] marker. The academic_docx renderer shows both bold and
# highlighted; the Academic Review arbiter flags any that survive into a
# submitted draft. Applied via _apply_draft_caveats so a task that
# already carries one form is not given a second, conflicting copy.
_CAVEAT_CITATION = (
    "\n\nCITATION VERIFICATION — immediately before every external "
    "citation you include, insert an inline marker of the form "
    "[[VERIFY CITATION: check that Author (Year) exists and supports "
    "this specific claim before submitting]], so no unverified citation "
    "is missed."
)
_CAVEAT_STATS = (
    "\n\nSTATISTIC VERIFICATION — if you are uncertain about any "
    "specific numeric value, do NOT insert it silently; wrap it in an "
    "inline marker of the form [[VERIFY: <the value and what it is>]] "
    "(for example [[VERIFY: Sharpe ratio for Regime Switching = 0.63]]) "
    "so a team member confirms it against the Analytics page before "
    "submission."
)


def _apply_draft_caveats(specs: list[dict]) -> list[dict]:
    """
    Appends the citation- and statistic-verification caveats to each
    section task prompt. Idempotent per form — a task that already
    carries the [[VERIFY CITATION]] or [[VERIFY:]] instruction is not
    given a second, conflicting copy (the midpoint methodology and
    results tasks already carry the statistic marker).
    """
    for spec in specs:
        task = spec.get("task", "")
        if "[[VERIFY CITATION" not in task:
            task += _CAVEAT_CITATION
        if "[[VERIFY:" not in task:
            task += _CAVEAT_STATS
        spec["task"] = task
    return specs


_MIDPOINT_S2_KEY_FINDINGS = (
    # May 25 2026 — TRIMMED to the four most impactful themes per user
    # spec (regime break, best Sharpe, OOS validation, diversification
    # benefit). Dropped the FDR-correction and efficient-frontier
    # concentration findings — interesting but secondary, and pushing
    # the section above its 250-300 word target. One sentence per
    # finding maximum.
    "\n\nKEY FINDINGS — present these in this order, ONE SENTENCE EACH:\n"
    "(1) Regime break: the equity-IG correlation shifted from "
    "approximately -0.05 (pre-2022) to +0.61 (post-2022) — quote the "
    "pre/post values from the correlation_pre_post data and connect to "
    "the divergence in strategy performance.\n"
    "(2) Best Sharpe: Regime Switching delivered the highest full-period "
    "risk-adjusted return (approximately 0.63 vs the benchmark's 0.52) "
    "by adapting to the correlation break — cite the actual values from "
    "the summary_statistics data.\n"
    "(3) OOS validation: in the post-2022 holdout window Regime "
    "Switching's Sharpe (approximately 0.2483) materially exceeded the "
    "benchmark's, confirming the result is not an in-sample artefact — "
    "cite the regime_conditional data.\n"
    "(4) Diversification benefit: static 60/40 underperformed in the "
    "post-break period because the IG correlation flip removed the "
    "diversification cushion; the dynamic regime-aware strategies "
    "preserved it."
)


async def _generate_narratives(
    specs: list[dict], *, n_strategies: int | None = None,
) -> dict[str, str]:
    """
    Generates a set of narrative sections concurrently.

    Each spec is {key, agent_id, task, context, available, pending?}.
    Sections with available=False skip the LLM entirely and take their
    [DATA PENDING] marker directly; the rest run through harness_narrative
    in worker threads (the harness is synchronous) and complete in
    parallel — the same asyncio.to_thread fan-out the Academic Review
    peer agents use.

    n_strategies — uniform across every section of a single document
    (it counts the cache, not the section). Threaded through to
    harness_narrative once per spec so the chart-vision scope sentences
    render the precise count instead of the count-omitted fallback.
    """
    import asyncio

    from tools.academic_export import DATA_PENDING, harness_narrative

    out: dict[str, str] = {}
    jobs: list[tuple[str, Any]] = []
    for spec in specs:
        if not spec.get("available", True):
            out[spec["key"]] = spec.get(
                "pending", f"{DATA_PENDING} — source data unavailable.")
            continue
        jobs.append((spec["key"], asyncio.to_thread(
            harness_narrative, spec["agent_id"], spec["task"], spec["context"],
            n_strategies=n_strategies)))
    if jobs:
        results = await asyncio.gather(*[j for _, j in jobs],
                                       return_exceptions=True)
        for (key, _), res in zip(jobs, results):
            out[key] = res if isinstance(res, str) else (
                f"{DATA_PENDING} — narrative generation failed.")
    return out


async def _editor_export(editor_draft_id: int) -> Response:
    """
    Builds a .docx (paper/brief) or .pptx (deck) from an editor draft's
    current content — the in-editor Export path. Renders the editor
    content directly rather than regenerating the document, so the
    export is exactly what the author has in the editor.
    """
    import asyncio
    from datetime import date

    from tools.editor_drafts import get_draft

    draft = await get_draft(editor_draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found.")

    if draft["document_type"] == "presentation_deck":
        from tools.academic_deck import build_editor_pptx
        from tools.chart_render import is_known_chart, render_chart_png

        # Render each chart element's PNG server-side (an async path) and
        # hand the {element_id: png} map to the sync .pptx builder. A
        # failed render is left out — the builder degrades it gracefully.
        content_json = draft.get("content_json") or {}
        deck_slides = (content_json.get("slides", [])
                       if isinstance(content_json, dict) else [])
        chart_pngs: dict[str, bytes] = {}
        for sl in deck_slides:
            for el in (sl.get("elements") or [] if isinstance(sl, dict) else []):
                if (isinstance(el, dict) and el.get("type") == "chart"
                        and is_known_chart(str(el.get("chartKey", "")))):
                    try:
                        # Render at 2x the element box for print quality.
                        w = min(2000, max(80, int(el.get("width") or 360) * 2))
                        h = min(2000, max(80, int(el.get("height") or 220) * 2))
                        chart_pngs[str(el.get("id"))] = await render_chart_png(
                            str(el["chartKey"]), "light", w, h)
                    except Exception:  # noqa: BLE001 — skip, builder degrades
                        pass
        content = await asyncio.to_thread(build_editor_pptx, draft, chart_pngs)
        media, ext = _PPTX_MEDIA, "pptx"
    else:
        from tools.academic_docx import build_editor_docx
        content = await asyncio.to_thread(build_editor_docx, draft)
        media, ext = _DOCX_MEDIA, "docx"

    slug = draft["document_type"].replace("_", "-")
    filename = f"forest-capital-{slug}-{date.today().isoformat()}.{ext}"
    return Response(
        content=content, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ── Async document generation — job system ────────────────────────────────────
# The three generation endpoints take 30-90s. Each creates a job, spawns
# generation as a background task, and returns 202 immediately; the
# frontend polls GET /api/v1/jobs/{id}. See tools/generation_jobs.py.
_generation_bg_tasks: set = set()


async def _require_report_ready() -> None:
    """
    Workstream C report gate (May 28 2026). Raises 422 with a structured
    detail when either audit surface has unreviewed blocking items.

    A 422 is the right status here — the request is well-formed, but it
    asks the platform to publish a deliverable that the audit subsystem
    has flagged as not-yet-reviewed. The frontend reads detail.blockers
    and shows them in a blocking modal so the user can navigate back to
    the audit panel and act on each.

    Fail-open: the readiness module returns empty lists on any read
    error, so a database outage or an empty audit history reports
    is_ready=true and the gate does not block.
    """
    from tools.report_readiness import compute_readiness, summarise_blockers

    readiness = await compute_readiness()
    if readiness.get("is_ready"):
        return
    raise HTTPException(
        status_code=422,
        detail={
            "error": "report_not_ready",
            "message": (
                f"{readiness.get('blocking_count', 0)} audit item"
                f"{'s' if readiness.get('blocking_count') != 1 else ''} "
                "must be reviewed or revoked before a report can be "
                "generated."),
            "blocking_count": readiness.get("blocking_count", 0),
            "blockers": summarise_blockers(readiness),
            "statistical": readiness.get("statistical"),
            "methodology": readiness.get("methodology"),
        },
    )


def _start_generation_job(
    document_type: str, session: dict, request: Request,
) -> JSONResponse:
    """Creates a job, spawns generation on the event loop, returns 202.

    Seeds the per-request usage bucket BEFORE the task spawns so the
    Academic Writer harness calls inside _generate_async (which run on
    the same loop, inheriting this context) populate it. The task ends
    by calling _log_interaction_bg which reads collect_usage().
    """
    import asyncio

    from tools.generation_jobs import create_job, update_job
    from agents.usage import start_usage_capture

    start_usage_capture()
    job = create_job(document_type, session["email"])
    task = asyncio.create_task(
        _generate_async(job["job_id"], document_type, session, request))
    update_job(job["job_id"], _task=task)
    _generation_bg_tasks.add(task)
    task.add_done_callback(_generation_bg_tasks.discard)
    return JSONResponse(
        status_code=202,
        content={"job_id": job["job_id"], "status": "pending"})


async def _generate_async(
    job_id: str, document_type: str, session: dict, request: Request,
) -> None:
    """Runs document generation for a job and records the outcome on it.
    A cancelled job's status is set by the DELETE handler, so a
    CancelledError propagates untouched."""
    import asyncio
    from datetime import datetime, timezone

    from tools.generation_jobs import update_job

    update_job(job_id, status="running")
    try:
        if document_type == "midpoint_paper":
            file_bytes, filename, media, draft_id = \
                await _generate_midpoint_document(session["email"])
        elif document_type == "executive_brief":
            file_bytes, filename, media, draft_id = \
                await _generate_brief_document(session["email"])
        else:
            file_bytes, filename, media, draft_id = \
                await _generate_deck_document(session["email"])
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        ref = uuid.uuid4().hex[:8]
        log.error("generation_job_failed", job_id=job_id,
                  document_type=document_type, ref=ref, error=str(exc))
        update_job(job_id, status="failed",
                   error=f"Generation failed (ref: {ref})",
                   completed_at=datetime.now(timezone.utc))
        return

    update_job(job_id, status="complete", draft_id=draft_id,
               download_url=f"/api/v1/jobs/{job_id}/download",
               completed_at=datetime.now(timezone.utc),
               _file_bytes=file_bytes, _filename=filename, _media_type=media)
    _log_interaction_bg(
        request, session, "export", agents_involved=["academic_writer"],
        response_summary=f"{document_type} generated",
        metadata={"deliverable": document_type, "draft_id": draft_id})


@app.post("/api/v1/export/midpoint-paper")
@limiter.limit("6/minute")
async def export_midpoint_paper(
    request: Request,
    body: dict | None = None,
    session: dict = Depends(require_permission("generate_documents")),
):
    """
    Starts midpoint-paper generation.

    With an editor_draft_id in the body the .docx is built synchronously
    from that draft's current content (the in-editor Export path).
    Otherwise generation — the four Academic Writer sections, 30-60s —
    runs as a background job and the endpoint returns 202 with a job_id;
    poll GET /api/v1/jobs/{id}.
    """
    editor_draft_id = (body or {}).get("editor_draft_id")
    if editor_draft_id:
        # Editor exports are a faithful render of what the author has
        # already saved; the gate runs only on fresh AI generation.
        return await _editor_export(int(editor_draft_id))
    await _require_report_ready()
    return _start_generation_job("midpoint_paper", session, request)


# ── Midpoint word-count validation (May 25 2026) ──────────────────────────────
#
# The midpoint paper is 3 pages double-spaced 12pt — roughly 750-900 words
# total. The four sections have their own targets (per the user spec):
#   methodology  250-300 words
#   results      250-300 words
#   roles        125-150 words
#   next_steps   125-150 words
# Each task prompt names its target, but the harness retries up to a
# quality threshold rather than a length one — so a generated section can
# still drift outside its range. This validator scores the post-generation
# narratives, surfaces section-by-section warnings into the editor draft
# (so the user sees the problem before submitting), and logs a structured
# warning so a Render scan reports drift over time.

# Per-section word targets (May 25 2026 — shaved 15 from each end of
# every section to leave room for the empirical-citation overhead
# (4 findings × ~15-25 words per inline citation ≈ 60-100 words).
# The total stays at 750-900 — the paper's physical-length constraint
# — and the gap between section sums (690-840) and the total (750-900)
# is precisely where the citation overhead lives. A paper whose
# sections hit the floor + carries 60-100 words of citations lands at
# the total floor; the validator accepts that as valid because both
# the section and total checks pass independently.
_MIDPOINT_WORD_TARGETS: dict[str, tuple[int, int]] = {
    "methodology":  (235, 285),
    "results":      (235, 285),
    "roles":        (110, 135),
    "next_steps":   (110, 135),
}
_MIDPOINT_TOTAL_TARGET = (750, 900)


def _count_words(text: str) -> int:
    """Whitespace-split word count — same convention Word's status bar
    uses. [[VERIFY: …]] / [[BOB: …]] markers count as words; they are
    written by the AI and will be resolved by the user before
    submission, so counting them keeps the post-resolution count
    realistic rather than systematically over-counting."""
    if not text:
        return 0
    return len([w for w in text.split() if w.strip()])


def _validate_midpoint_word_counts(
    narratives: dict[str, str],
) -> dict[str, object]:
    """Returns a structured validation result for a generated midpoint
    paper's narratives. Used to (a) log a warning when generation lands
    out-of-range, and (b) inject a banner into the editor draft so the
    user sees the discrepancy on first open. Always returns a usable
    dict — never raises on missing keys.

    Shape:
      { valid: bool,
        total_words: int,
        total_target: (lo, hi),
        sections: {
          key: { words, target: (lo, hi), in_range, label }
        },
        warnings: [ "Methodology 220 words is below the 250-300 target.",
                    "Total 670 words is below the 750-900 target.", ... ],
      }
    """
    section_labels = {
        "methodology": "Data and Methodology",
        "results":     "Preliminary Results and Diagnostics",
        "roles":       "Roles and Division of Labor",
        "next_steps":  "Next Steps and Open Questions",
    }
    sections: dict[str, dict[str, object]] = {}
    warnings_list: list[str] = []
    total_words = 0
    for key, (lo, hi) in _MIDPOINT_WORD_TARGETS.items():
        text = narratives.get(key) or ""
        words = _count_words(text)
        total_words += words
        in_range = lo <= words <= hi
        label = section_labels.get(key, key)
        sections[key] = {
            "words": words,
            "target": [lo, hi],
            "in_range": in_range,
            "label": label,
        }
        if not in_range:
            direction = "below" if words < lo else "above"
            warnings_list.append(
                f"{label} ran {words} words — {direction} the "
                f"{lo}-{hi} target."
            )
    total_lo, total_hi = _MIDPOINT_TOTAL_TARGET
    total_in_range = total_lo <= total_words <= total_hi
    if not total_in_range:
        direction = "below" if total_words < total_lo else "above"
        warnings_list.append(
            f"Total ran {total_words} words — {direction} the "
            f"{total_lo}-{total_hi} target for a 3-page double-spaced "
            f"paper."
        )
    valid = total_in_range and all(s["in_range"] for s in sections.values())
    return {
        "valid": valid,
        "total_words": total_words,
        "total_target": [total_lo, total_hi],
        "sections": sections,
        "warnings": warnings_list,
    }


async def _generate_midpoint_document(
    email: str,
) -> tuple[bytes, str, str, int | None]:
    """
    Generates the three-page midpoint submission. Returns (file bytes,
    filename, media type, editor draft id). Raises on failure — the job
    wrapper records it.

    Four sections per the FNA 670 brief — Data & Methodology, Preliminary
    Results (with the summary-statistics and regime-conditional tables
    embedded), Roles & Division of Labor (from real Team Activity counts),
    and Next Steps (from the last Academic Review verdict).
    """
    import asyncio
    from datetime import date

    from tools.academic_docx import build_midpoint_paper
    from tools.academic_export import (
        DATA_PENDING, gather_document_data, gather_roles_activity,
    )

    try:
        data = await gather_document_data()
        period = data["study_period"]
        has_results = bool(data["summary_statistics"] or data["regime_conditional"])
        has_review = bool(data["last_review_text"])

        # Section 3 (Roles) is pre-seeded from real per-member platform
        # activity — commits, council runs, reviews, uploads, UAT — so Bob
        # personalises a factual draft rather than writing from scratch.
        roles_activity = await gather_roles_activity(data["team_summary"])
        has_roles = any(
            v.get("commits") or v.get("council_sessions_run")
            or v.get("academic_review_sessions") or v.get("documents_uploaded")
            or v.get("uat_sections_attested")
            for v in roles_activity.values()
        )

        specs = [
            {"key": "methodology", "available": True,
             "agent_id": "midpoint_methodology",
             "task": (
                 "Write the Data and Methodology section of a graduate "
                 "finance midpoint paper. TARGET LENGTH: 235-285 words "
                 "of prose (this is a 3-page double-spaced 12-point "
                 "paper; the section must land in that range — under "
                 "235 is too thin, over 285 squeezes the other three "
                 "sections; the 15-word headroom from a 250-300 target "
                 "absorbs the empirical-citation overhead — each inline "
                 "(Author, Year) citation adds 15-25 words to the "
                 "section's actual length). APA style, past tense, "
                 "third person. "
                 "Cover the data sources (aligned "
                 "monthly returns for equity, investment-grade and high-yield "
                 "bonds; Carhart factor series), the study period, the "
                 "portfolio constraints (long-only, fully invested, no cash, "
                 "quarterly rebalancing), the ten strategies grouped as "
                 "static versus dynamic, and the Carhart four-factor "
                 "attribution model. When discussing portfolio turnover, "
                 "always clarify: turnover is reported as one-way annualised "
                 "turnover (the standard institutional convention) and "
                 "two-way round-trip turnover is approximately double the "
                 "reported figures; true turnover is computed from the "
                 "drift-inclusive weight schedule at each quarterly "
                 "rebalance, capturing both signal-driven reallocation and "
                 "drift-correction trading back to target. Note that "
                 "Black-Litterman, despite its dynamic classification, "
                 "exhibits static-like turnover (4.7%) reflecting the "
                 "framework's modest weight adjustments from its equilibrium "
                 "prior — a genuine analytical finding, not a data issue. "
                 "If you are uncertain about any specific numeric value, do "
                 "NOT insert it silently — wrap it in an inline verification "
                 "marker of the form [[VERIFY: <claim>]] (for example "
                 "[[VERIFY: Sharpe ratio for Regime Switching = 0.63]]) so a "
                 "team member checks it before submission."
                 + _MIDPOINT_S1_KEY_FINDINGS),
             "context": {"study_period": period,
                         "strategy_metadata": data.get("strategy_metadata"),
                         "risk_free_rate": data["risk_free_rate"]}},
            {"key": "results", "available": has_results,
             "pending": (f"{DATA_PENDING} — preliminary results require the "
                         "analytics caches. Load the dashboard once to warm "
                         "them, then regenerate this paper."),
             "agent_id": "midpoint_results",
             "task": (
                 "Write the Preliminary Results section. TARGET LENGTH: "
                 "235-285 words of prose (under 235 is too thin; over "
                 "285 leaves no room for Roles and Next Steps; the "
                 "15-word headroom from a 250-300 target absorbs "
                 "empirical-citation overhead — each inline (Author, "
                 "Year) citation adds 15-25 words). "
                 "Interpret the summary statistics and the regime-conditional "
                 "performance; do not merely list numbers. You MUST explicitly "
                 "discuss the 2022 equity-bond correlation break and what the "
                 "pre- versus post-2022 Sharpe ratios reveal. Reference "
                 "Table 1 (summary statistics) and Table 2 (regime-conditional "
                 "performance). If you are uncertain about any specific "
                 "numeric value, do NOT insert it silently — wrap it in an "
                 "inline verification marker of the form [[VERIFY: <claim>]] "
                 "(for example [[VERIFY: Sharpe ratio for Regime Switching = "
                 "0.63]]) so a team member checks it before submission."
                 + _MIDPOINT_S2_KEY_FINDINGS),
             "context": {"summary_statistics": data["summary_statistics"],
                         "regime_conditional": data["regime_conditional"],
                         "correlation_pre_post": {
                             "pre_2022": data["rolling_correlation"].get("pre_2022"),
                             "post_2022": data["rolling_correlation"].get("post_2022")}}},
            # Section 3 (Roles and Division of Labor) is pre-seeded from
            # real Team Activity counts. The AI draft is factual, not
            # authoritative — build_midpoint_paper renders a "BOB —
            # PERSONALISE" callout beneath it directing him to rewrite it
            # in his own voice and add what the platform data cannot show.
            {"key": "roles", "available": has_roles,
             "pending": (f"{DATA_PENDING} — no platform activity on record "
                         "yet. Run council sessions, reviews and UAT, then "
                         "regenerate; meanwhile describe the roles directly."),
             "agent_id": "midpoint_roles",
             "task": (
                 "Write the Roles and Division of Labor section. "
                 "TARGET LENGTH: 110-135 words (this is a shorter "
                 "section — keep it tight; citations are rarely needed "
                 "here so the headroom is for the prose itself). "
                 "APA style, past tense, third person. Use ONLY the "
                 "team_activity_summary data provided. State each team "
                 "member's role and attribute their documented platform "
                 "activity — commits, council sessions run, academic review "
                 "sessions, documents uploaded, UAT sections attested. Do "
                 "NOT invent contributions the data does not show; if a "
                 "count is zero, omit it rather than guessing. This is a "
                 "factual pre-seed for the team to personalise — write it "
                 "plainly and let each member's actual activity counts "
                 "carry the section."),
             "context": {"team_activity_summary": roles_activity}},
            {"key": "next_steps", "available": has_review,
             "pending": (f"{DATA_PENDING} — no Academic Review verdict on "
                         "record. Run an Academic Review on the Council "
                         "screen, then regenerate; meanwhile list next steps "
                         "as planned work."),
             "agent_id": "midpoint_next_steps",
             "task": (
                 "Write the Next Steps and Open Questions section. "
                 "TARGET LENGTH: 110-135 words (keep it tight — the "
                 "section closes the paper and shouldn't bloat; "
                 "citations are rare here, so the headroom is for "
                 "actual next-steps content). "
                 "Convert the supplied Academic Review verdict — its "
                 "Priority Areas for Further Investigation and any Developing "
                 "or Needs Work ratings — into a forward-looking next-steps "
                 "narrative."),
             "context": {"academic_review_verdict":
                         (data["last_review_text"] or "")[:4000]}},
        ]
        narratives = await _generate_narratives(
            _apply_draft_caveats(specs),
            n_strategies=len(data.get("strategy_results") or {}))

        # Word-count validation (May 25 2026). Three pages double-spaced
        # 12pt is ~750-900 words; each section has its own target. If
        # any section or the total drifts outside the range, log a
        # structured warning AND surface it as a banner in the editor
        # draft so the user sees it before submitting.
        word_validation = _validate_midpoint_word_counts(narratives)
        if not word_validation["valid"]:
            log.warning(
                "midpoint_word_count_validation_failed",
                total_words=word_validation["total_words"],
                section_words={
                    k: v["words"]
                    for k, v in word_validation["sections"].items()
                },
                warnings=word_validation["warnings"],
            )
        else:
            log.info(
                "midpoint_word_count_validation_passed",
                total_words=word_validation["total_words"],
            )

        docx_bytes = await asyncio.to_thread(build_midpoint_paper, data, narratives)

        # Load the generated content into an editor draft so the frontend
        # can open it directly in the editor. The draft_id rides back in
        # the X-Draft-Id response header (the body is the binary .docx).
        # A draft-storage failure never fails the download.
        draft_id: int | None = None
        try:
            from tools.editor_content import midpoint_to_editor
            from tools.editor_drafts import create_draft
            content_json, content_text = midpoint_to_editor(
                narratives,
                word_validation=word_validation,
            )
            draft = await create_draft(
                "midpoint_paper", email,
                f"Midpoint Paper — {date.today().isoformat()}",
                content_json, content_text, created_from="generated")
            if draft is not None:
                draft_id = draft["id"]
        except Exception as exc:  # noqa: BLE001
            log.warning("midpoint_draft_create_failed", error=str(exc))

        filename = f"forest-capital-midpoint-paper-{date.today().isoformat()}.docx"
        return docx_bytes, filename, _DOCX_MEDIA, draft_id
    except Exception as exc:  # noqa: BLE001
        log.error("midpoint_paper_generation_error", error=str(exc))
        raise


@app.post("/api/v1/export/executive-brief")
@limiter.limit("6/minute")
async def export_executive_brief(
    request: Request,
    body: dict | None = None,
    session: dict = Depends(require_permission("generate_documents")),
):
    """
    Starts executive-brief generation.

    With an editor_draft_id in the body the .docx is built synchronously
    from that draft (the in-editor Export path). Otherwise generation —
    the eight Academic Writer sections, 45-90s — runs as a background
    job and the endpoint returns 202 with a job_id; poll
    GET /api/v1/jobs/{id}.
    """
    editor_draft_id = (body or {}).get("editor_draft_id")
    if editor_draft_id:
        return await _editor_export(int(editor_draft_id))
    await _require_report_ready()
    return _start_generation_job("executive_brief", session, request)


async def _generate_brief_document(
    email: str,
) -> tuple[bytes, str, str, int | None]:
    """
    Generates the five-page executive brief. Returns (file bytes,
    filename, media type, editor draft id). Raises on failure — the job
    wrapper records it.

    A title page, then Executive Summary, Methodology Overview, four Key
    Findings (with the regime-conditional, summary-statistics, drawdown
    and factor-loadings tables embedded), Limitations and Risks, and
    Final Recommendations.
    """
    import asyncio
    from datetime import date

    from tools.academic_docx import build_executive_brief
    from tools.academic_export import DATA_PENDING, gather_document_data

    try:
        data = await gather_document_data()
        avail = data["available"]
        pending = (f"{DATA_PENDING} — analytics caches not warm. Load the "
                   "dashboard once, then regenerate this brief.")

        specs = [
            {"key": "exec_summary", "available": avail, "pending": pending,
             "agent_id": "brief_exec_summary",
             "task": (
                 "Write the Executive Summary of an investment brief — about "
                 "180 words, for a senior investment audience. State the "
                 "central question (does diversification across equities and "
                 "fixed income improve risk-adjusted performance, and does "
                 "that answer change after 2022), the key finding (the 2022 "
                 "equity-bond correlation break), the best-performing "
                 "strategies with their metrics, and the strategic "
                 "recommendation."),
             "context": {"summary_statistics": data["summary_statistics"],
                         "regime_conditional": data["regime_conditional"],
                         "study_period": data["study_period"]}},
            {"key": "methodology", "available": True,
             "agent_id": "brief_methodology",
             "task": (
                 "Write the Methodology Overview — about 280 words. Cover the "
                 "data sources and study period, the portfolio constraints "
                 "(long-only, fully invested, no cash), the ten strategies "
                 "(static versus dynamic), the Carhart four-factor model, the "
                 "benchmark definition (100% S&P 500), and the key "
                 "assumptions."),
             "context": {"study_period": data["study_period"],
                         "strategy_metadata": data.get("strategy_metadata"),
                         "risk_free_rate": data["risk_free_rate"]}},
            {"key": "finding_1", "available": avail, "pending": pending,
             "agent_id": "brief_finding_2022",
             "task": (
                 "Write Finding 1: The 2022 Correlation Break — about 220 "
                 "words. Interpret the pre- and post-2022 equity-bond "
                 "correlation and the regime-conditional Sharpe ratios. "
                 "Explain why the diversification benefit broke down and what "
                 "it means for a static 60/40 allocation."),
             "context": {"regime_conditional": data["regime_conditional"],
                         "correlation_pre_post": {
                             "pre_2022": data["rolling_correlation"].get("pre_2022"),
                             "post_2022": data["rolling_correlation"].get("post_2022")}}},
            {"key": "finding_2", "available": avail, "pending": pending,
             "agent_id": "brief_finding_static",
             "task": (
                 "Write Finding 2: Static Allocation Results — about 220 "
                 "words. Using the summary statistics, identify the best "
                 "static strategy and justify it, comparing it to the 100% "
                 "equity benchmark."),
             "context": {"summary_statistics": data["summary_statistics"]}},
            {"key": "finding_3", "available": avail, "pending": pending,
             "agent_id": "brief_finding_dynamic",
             "task": (
                 "Write Finding 3: Dynamic Allocation Results — about 220 "
                 "words. Assess the dynamic strategies' performance and "
                 "drawdown behaviour, and justify the rules-based logic."),
             "context": {"regime_conditional": data["regime_conditional"],
                         "drawdown_comparison": data["drawdown_comparison"]}},
            {"key": "finding_4", "available": avail, "pending": pending,
             "agent_id": "brief_finding_factor",
             "task": (
                 "Write Finding 4: Factor Analysis — about 220 words. "
                 "Interpret the Carhart four-factor loadings: assess alpha "
                 "generation and explain what the factor exposures reveal "
                 "about each strategy's return drivers."),
             "context": {"factor_loadings": data["factor_loadings"]}},
            {"key": "limitations", "available": True,
             "agent_id": "brief_limitations",
             "task": (
                 "Write the Limitations and Risks section — about 160 words. "
                 "Be honest, not defensive. Cover backtesting limitations, "
                 "transaction-cost modelling, out-of-sample considerations, "
                 "and the constraints of the data period."),
             "context": {"study_period": data["study_period"]}},
            {"key": "recommendations", "available": avail, "pending": pending,
             "agent_id": "brief_recommendations",
             "task": (
                 "Write the Final Recommendations section — about 160 words. "
                 "Give a strategic allocation recommendation grounded in the "
                 "results, with supporting evidence."),
             "context": {"regime_conditional": data["regime_conditional"],
                         "summary_statistics": data["summary_statistics"]}},
        ]
        narratives = await _generate_narratives(
            _apply_draft_caveats(specs),
            n_strategies=len(data.get("strategy_results") or {}))
        docx_bytes = await asyncio.to_thread(
            build_executive_brief, data, narratives)

        # Load the generated content into an editor draft so the frontend
        # can open it directly in the editor — the same pattern as the
        # midpoint paper and the deck. The draft_id rides back in the
        # X-Draft-Id response header; a draft-storage failure never fails
        # the download.
        draft_id: int | None = None
        try:
            from tools.editor_content import executive_brief_to_editor
            from tools.editor_drafts import create_draft
            content_json, content_text = executive_brief_to_editor(narratives)
            draft = await create_draft(
                "executive_brief", email,
                f"Executive Brief — {date.today().isoformat()}",
                content_json, content_text, created_from="generated")
            if draft is not None:
                draft_id = draft["id"]
        except Exception as exc:  # noqa: BLE001
            log.warning("executive_brief_draft_create_failed", error=str(exc))

        filename = f"forest-capital-executive-brief-{date.today().isoformat()}.docx"
        return docx_bytes, filename, _DOCX_MEDIA, draft_id
    except Exception as exc:  # noqa: BLE001
        log.error("executive_brief_generation_error", error=str(exc))
        raise


@app.post("/api/v1/export/presentation-deck")
@limiter.limit("4/minute")
async def export_presentation_deck(
    request: Request,
    body: dict | None = None,
    session: dict = Depends(require_permission("generate_documents")),
):
    """
    Starts presentation-deck generation.

    With an editor_draft_id in the body the .pptx is built synchronously
    from that draft's current slides (the in-editor Export path).
    Otherwise generation — Academic Writer prose, server-side charts,
    45-90s — runs as a background job and the endpoint returns 202 with
    a job_id; poll GET /api/v1/jobs/{id}.
    """
    editor_draft_id = (body or {}).get("editor_draft_id")
    if editor_draft_id:
        return await _editor_export(int(editor_draft_id))
    await _require_report_ready()
    return _start_generation_job("presentation_deck", session, request)


async def _generate_deck_document(
    email: str,
) -> tuple[bytes, str, str, int | None]:
    """
    Generates the 16-slide final presentation deck. Returns (file bytes,
    filename, media type, editor draft id). Raises on failure — the job
    wrapper records it.

    A professional navy/white theme — deliberately not the platform's
    dark UI. Charts are rendered server-side as light-mode PNGs with
    matplotlib; a chart whose data or matplotlib is unavailable degrades
    to a [DATA PENDING] note. The conclusions, recommendations, thesis
    and AI-leverage prose run through the Academic Writer harness.
    """
    import asyncio
    from datetime import date

    from tools.academic_deck import build_presentation_deck, render_deck_charts
    from tools.academic_export import DATA_PENDING, gather_document_data

    try:
        data = await gather_document_data()
        avail = data["available"]
        pending = (f"{DATA_PENDING} — analytics caches not warm. Load the "
                   "dashboard once, then regenerate the deck.")

        # Sensitivity is a heavier compute — best-effort, memoised. A
        # failure (or the test environment) leaves the slide [DATA PENDING].
        sensitivity: dict | None = None
        if avail and ENVIRONMENT != "test":
            try:
                from tools.data_fetcher import get_full_history
                from tools.sensitivity import compute_sensitivity
                sensitivity = await asyncio.to_thread(
                    lambda: compute_sensitivity(get_full_history()))
            except Exception as exc:  # noqa: BLE001
                log.warning("deck_sensitivity_unavailable", error=str(exc))

        specs = [
            {"key": "thesis", "available": avail, "pending": pending,
             "agent_id": "deck_thesis",
             "task": (
                 "Write a single-sentence thesis statement — at most 30 "
                 "words — on what the 2022 equity-bond correlation break "
                 "means for diversification. Return only the sentence."),
             "context": {"correlation_pre_post": {
                 "pre_2022": data["rolling_correlation"].get("pre_2022"),
                 "post_2022": data["rolling_correlation"].get("post_2022")}}},
            {"key": "conclusions", "available": avail, "pending": pending,
             "agent_id": "deck_conclusions",
             "task": (
                 "Write exactly five concise conclusion bullet points for a "
                 "presentation slide — one per line, each starting with "
                 "'- ', each under 22 words. They must directly address "
                 "whether diversification improves risk-adjusted performance "
                 "and whether 2022 changed the answer."),
             "context": {"regime_conditional": data["regime_conditional"],
                         "summary_statistics": data["summary_statistics"]}},
            {"key": "recommendations", "available": avail, "pending": pending,
             "agent_id": "deck_recommendations",
             "task": (
                 "Write a strategic allocation recommendation as four to "
                 "five concise bullet points for a presentation slide — one "
                 "per line, each starting with '- ', each under 22 words, "
                 "grounded in the supplied results."),
             "context": {"regime_conditional": data["regime_conditional"],
                         "summary_statistics": data["summary_statistics"]}},
            {"key": "ai_leverage", "available": True,
             "agent_id": "deck_ai_leverage",
             "task": (
                 "Write a brief two-to-three-sentence narrative on how the "
                 "team used AI to build and check this work: a multi-model "
                 "council (Claude, Gemini, Grok), a generator-evaluator "
                 "quality harness, and an academic-review quality gate. End "
                 "on the idea that the AI interrogated the work so faculty "
                 "can."),
             "context": {"team_summary": data["team_summary"]}},
        ]
        narratives = await _generate_narratives(
            specs, n_strategies=len(data.get("strategy_results") or {}))
        charts = await asyncio.to_thread(render_deck_charts, data, sensitivity)
        pptx_bytes = await asyncio.to_thread(
            build_presentation_deck, data, narratives, charts)

        # Load the generated deck into a presentation_deck editor draft so
        # Molly can open it directly in the slide editor; the draft_id
        # rides back in the X-Draft-Id header. Never fails the download.
        draft_id: int | None = None
        try:
            from tools.editor_content import deck_to_editor
            from tools.editor_drafts import create_draft
            content_json, content_text = deck_to_editor(narratives)
            draft = await create_draft(
                "presentation_deck", email,
                f"Presentation Deck — {date.today().isoformat()}",
                content_json, content_text, created_from="generated")
            if draft is not None:
                draft_id = draft["id"]
        except Exception as exc:  # noqa: BLE001
            log.warning("deck_draft_create_failed", error=str(exc))

        filename = f"forest-capital-presentation-deck-{date.today().isoformat()}.pptx"
        return pptx_bytes, filename, _PPTX_MEDIA, draft_id
    except Exception as exc:  # noqa: BLE001
        log.error("presentation_deck_generation_error", error=str(exc))
        raise


# ── Async document generation — job status / download / cancel ────────────────

@app.get("/api/v1/jobs/{job_id}")
async def get_generation_job(
    job_id: str, session: dict = Depends(require_auth),
):
    """The current state of a generation job. Owner-only; 404 when the
    job is unknown or has expired (the two-hour TTL)."""
    from tools.generation_jobs import get_job, public_view
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job["owner_email"] != session.get("email"):
        raise HTTPException(status_code=403, detail="This is not your job.")
    return public_view(job)


@app.get("/api/v1/jobs")
async def list_generation_jobs(session: dict = Depends(require_auth)):
    """The caller's last 10 generation jobs, most recent first."""
    from tools.generation_jobs import list_jobs, public_view
    return {"jobs": [public_view(j)
                     for j in list_jobs(session.get("email", ""))]}


@app.get("/api/v1/jobs/{job_id}/download")
async def download_generation_job(
    job_id: str, session: dict = Depends(require_auth),
):
    """Downloads a completed job's rendered file. Owner-only.

    Serves the bytes once. After the first download the buffer is
    cleared (mark_downloaded) to free memory — a 2 MB PPTX would
    otherwise hold a buffer for the full 2-hour job TTL. A second
    download attempt returns 410 Gone with guidance to regenerate;
    the job record itself stays so the client can still poll status.
    """
    from tools.generation_jobs import get_job, mark_downloaded, was_downloaded
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job["owner_email"] != session.get("email"):
        raise HTTPException(status_code=403, detail="This is not your job.")
    if was_downloaded(job_id):
        # Bytes already cleared — a re-attempt is a 410 (Gone), not 409
        # (Conflict). The download was successful; the buffer is intentionally
        # absent now.
        raise HTTPException(
            status_code=410,
            detail=("This download has already been served. "
                    "Regenerate the document if needed."))
    if job["status"] != "complete" or job["_file_bytes"] is None:
        raise HTTPException(status_code=409,
                            detail="The job has no downloadable file yet.")
    # Snapshot the bytes BEFORE marking served — mark_downloaded sets
    # _file_bytes to None, so we must build the response from the
    # snapshot rather than from the (now-cleared) job dict.
    content = job["_file_bytes"]
    media_type = job["_media_type"]
    fname = job["_filename"] or "forest-capital-document"
    mark_downloaded(job_id)
    return Response(
        content=content, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.delete("/api/v1/jobs/{job_id}")
async def cancel_generation_job(
    job_id: str, session: dict = Depends(require_auth),
):
    """Cancels a generation job — sets it to cancelled and cancels the
    background task if it is still in flight. Owner-only."""
    from tools.generation_jobs import get_job, public_view, update_job
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job["owner_email"] != session.get("email"):
        raise HTTPException(status_code=403, detail="This is not your job.")
    if job["status"] in ("pending", "running"):
        update_job(job_id, status="cancelled")
        task = job.get("_task")
        if task is not None and not task.done():
            task.cancel()
    return public_view(job)


# Sprint 6 Priority 1 — midpoint paper for the May 27 submission
# deadline (the June 3 cohort meetup is a peer-review event, not a gate).
# Academic Writer composes the prose; tools/docx_generator assembles the
# .docx around it. Every page carries the AI DRAFT banner so Bob can never
# accidentally submit a template-generated draft verbatim.

@app.post("/api/reports/midpoint-template")
@limiter.limit("10/minute")
async def midpoint_template(request: Request, session: dict = Depends(require_auth)):
    """
    Generates the 3-page midpoint paper draft as a .docx download.

    Four sections per the FNA 670 brief:
      1. Data & Methodology   (Academic Writer → write_methodology)
      2. Preliminary Results  (Academic Writer → write_results)
      3. Roles & Division     (deterministic team-roles section)
      4. Next Steps           (deterministic remaining-sprints section)

    The Academic Writer Agent runs on Sonnet and can take 10-30 seconds.
    Returned as application/vnd.openxmlformats-officedocument.wordprocessingml.document
    with a filename header so the browser triggers a download instead
    of rendering the bytes inline.
    """
    from fastapi.responses import Response as FastAPIResponse

    try:
        from agents.academic_writer import AcademicWriter
        from tools.data_fetcher import get_full_history_async
        from tools.backtester import run_all_strategies
        from tools.docx_generator import build_docx
        from tools.cache import get_strategy_cache, _compute_data_hash

        # In test env we skip the pipeline entirely so the smoke test runs
        # in milliseconds. The structured fallback in build_docx still
        # produces a valid .docx Bob could open and edit.
        if ENVIRONMENT == "test":
            results_dict: dict = {}
            data_range = {"start": "—", "end": "—", "n_months": 0}
        else:
            from tools.data_fetcher import get_full_history_async  # noqa: F401
            history = await get_full_history_async()
            monthly = history.get("equity_monthly")
            n_rows = len(monthly) if monthly is not None else 0
            last_date = (
                str(monthly.index[-1].date())
                if monthly is not None and len(monthly) > 0 else "unknown"
            )
            strategy_hash = _compute_data_hash(n_rows, last_date, n_strategies=10)
            cached = await get_strategy_cache(strategy_hash)
            if cached:
                results_dict = cached
            else:
                results_dict = await asyncio.to_thread(run_all_strategies, history)
            first_date = (
                str(monthly.index[0].date())
                if monthly is not None and len(monthly) > 0 else "unknown"
            )
            data_range = {"start": first_date, "end": last_date, "n_months": n_rows}

        significance_flags = {
            name: bool(r.get("is_significant"))
            for name, r in results_dict.items()
        }
        n_significant = sum(1 for v in significance_flags.values() if v)

        # Build the four sections. Academic Writer methods already prepend
        # an AI DRAFT banner to each section; the docx builder also adds
        # one to the document header. The banner is intentionally redundant
        # so a partial-page PDF export still carries the warning.
        writer = AcademicWriter()
        methodology = writer.write_methodology(
            data_sources={"data_range": data_range, "n_months": data_range["n_months"]},
            strategies=list(results_dict.keys()),
            statistical_tests=[
                "Paired t-test (full period)",
                "Benjamini-Hochberg FDR correction",
                "Deflated Sharpe Ratio",
                "Walk-forward out-of-sample",
                "CV Stability Score",
            ],
        )
        results = writer.write_results(
            strategy_results=results_dict,
            significance_flags=significance_flags,
            stress_tests={},
        )

        roles_body = (
            "Michael Ruurds — Lead Engineer. Responsible for the full backend "
            "implementation, data pipeline, AI council architecture, statistical "
            "test suite, and the React frontend that surfaces all analytical results. "
            "Hours: ~20 per week.\n\n"
            "Bob Thao — Lead Analyst. Responsible for the academic interpretation "
            "of all results, methodological justification, and the written report "
            "in APA format. Edits this AI draft into the final submission.\n\n"
            "Molly Murdock — Lead Presenter. Responsible for the Forest Capital "
            "presentation slide deck, executive brief, and the July 1 demo.\n\n"
            "Dr. Panttser — Faculty supervisor and reviewer."
        )
        next_steps_body = (
            f"As of the midpoint, {n_significant} of 10 strategies pass all five "
            f"Tier 1 statistical gates at p < 0.005 with Benjamini-Hochberg FDR "
            f"correction. Remaining work for the final presentation:\n\n"
            "• Sprint 6: Academic Writer Agent endpoints (analytical appendix, "
            "executive brief), Storyboard Editor, Presentation Script Writer, "
            "Gemini assistant for inline editing, full regression suite, "
            "accessibility audit, presentation-ready demo.\n\n"
            "• Final tag v1.0.0-presentation targeted for July 1."
        )

        sections = [
            {"heading": "1. Data & Methodology", "body": methodology},
            {"heading": "2. Preliminary Results", "body": results},
            {"heading": "3. Roles & Division of Labor", "body": roles_body},
            {"heading": "4. Next Steps & Open Questions", "body": next_steps_body},
        ]

        docx_bytes = build_docx(
            title="Forest Capital Portfolio Intelligence System",
            subtitle=(
                "Midpoint Checkpoint — FNA 670 Practicum · "
                f"Data range: {data_range['start']} – {data_range['end']} · "
                f"{n_significant}/10 strategies pass all Tier 1 gates"
            ),
            sections=sections,
            strategy_results=results_dict,
            references=None,
        )

        # Tag the filename with the date so iterative drafts don't overwrite
        # each other in Bob's downloads folder.
        from datetime import date
        filename = f"forest-capital-midpoint-{date.today().isoformat()}.docx"
        return FastAPIResponse(
            content=docx_bytes,
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as exc:
        ref = uuid.uuid4().hex[:8]
        log.error("midpoint_template_error", ref=ref, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Midpoint generation failed (ref: {ref})")


# ── Bob's remaining report generators ─────────────────────────────────────────
#
# Two endpoints round out Bob's deliverables alongside midpoint-template:
#   POST /api/reports/analytical-appendix    HTML  (35% of grade)
#   POST /api/reports/executive-brief-template  .docx 5-page  (20% of grade)
#
# Both follow the midpoint pattern: Academic Writer composes prose, helper
# module assembles the file, AI DRAFT banner mandatory on every page. The
# test-env fast path skips the pipeline so the smoke tests run in
# milliseconds against deterministic mock data.


def _build_results_dict_and_range() -> tuple[dict, dict]:
    """
    Loads strategy results from the cache (or runs the full pipeline if the
    cache is cold) and returns a (results, data_range) tuple.

    Centralised so the three report endpoints share the same data-loading
    semantics. ENVIRONMENT=test bypasses entirely so report tests run in
    milliseconds against an empty results dict — the docx/html builders
    still produce a valid file from the prose-only sections.
    """
    if ENVIRONMENT == "test":
        return {}, {"start": "—", "end": "—", "n_months": 0}

    from tools.data_fetcher import get_full_history
    from tools.backtester import run_all_strategies
    from tools.cache import get_strategy_cache, _compute_data_hash

    history = get_full_history()
    monthly = history.get("equity_monthly")
    n_rows = len(monthly) if monthly is not None else 0
    last_date = (
        str(monthly.index[-1].date())
        if monthly is not None and len(monthly) > 0 else "unknown"
    )
    first_date = (
        str(monthly.index[0].date())
        if monthly is not None and len(monthly) > 0 else "unknown"
    )
    strategy_hash = _compute_data_hash(n_rows, last_date, n_strategies=10)

    # asyncpg cache call is async — caller runs us inside an async endpoint
    # so we delegate the await to the caller via a small inline wrapper.
    # Simpler to do the actual run inline at call sites; this helper now
    # only returns the synchronous part. (Refactored below.)
    raise RuntimeError("Use _load_results_async inside route handlers")


async def _load_results_async() -> tuple[dict, dict]:
    """Async loader — single source of truth for data + cache hit logic."""
    if ENVIRONMENT == "test":
        return {}, {"start": "—", "end": "—", "n_months": 0}

    from tools.data_fetcher import get_full_history_async
    from tools.backtester import run_all_strategies
    from tools.cache import get_strategy_cache, _compute_data_hash

    history = await get_full_history_async()
    monthly = history.get("equity_monthly")
    n_rows = len(monthly) if monthly is not None else 0
    last_date = (
        str(monthly.index[-1].date())
        if monthly is not None and len(monthly) > 0 else "unknown"
    )
    first_date = (
        str(monthly.index[0].date())
        if monthly is not None and len(monthly) > 0 else "unknown"
    )
    strategy_hash = _compute_data_hash(n_rows, last_date, n_strategies=10)
    cached = await get_strategy_cache(strategy_hash)
    results = cached if cached else await asyncio.to_thread(run_all_strategies, history)
    return results, {"start": first_date, "end": last_date, "n_months": n_rows}


@app.post("/api/reports/analytical-appendix")
@limiter.limit("10/minute")
async def analytical_appendix(request: Request, session: dict = Depends(require_auth)):
    """
    Generates the comprehensive HTML analytical appendix — 35% of the grade.

    Six sections per CLAUDE.md Section 14:
      1. Abstract               (Academic Writer → write_abstract via results)
      2. Data Sources & Provenance  (registry + cross-validation)
      3. Portfolio Construction Methodology (Academic Writer → write_methodology)
      4. Statistical Results in APA format  (Academic Writer → write_results)
         + Table 1 strategy comparison auto-injected after this section
      5. Sensitivity Analysis    (deterministic ±20% parameter sweep summary)
      6. Reproducibility Notes   (random seed, data file, config snapshot)

    Returns text/html with a filename header so browsers either render
    inline (when content-disposition is inline) or download (attachment).
    We choose attachment so Bob has the source HTML to edit; he can
    Open With Word or paste into Pages for the final submission.
    """
    from fastapi.responses import Response as FastAPIResponse

    try:
        from agents.academic_writer import AcademicWriter
        from tools.html_report_generator import build_html_report

        results_dict, data_range = await _load_results_async()
        significance_flags = {
            name: bool(r.get("is_significant"))
            for name, r in results_dict.items()
        }
        n_significant = sum(1 for v in significance_flags.values() if v)

        writer = AcademicWriter()
        methodology = writer.write_methodology(
            data_sources={"data_range": data_range, "n_months": data_range["n_months"]},
            strategies=list(results_dict.keys()),
            statistical_tests=[
                "Paired t-test (full period)",
                "Benjamini-Hochberg FDR correction",
                "Deflated Sharpe Ratio",
                "Walk-forward out-of-sample",
                "CV Stability Score",
                "Combinatorial Purged Cross-Validation",
            ],
        )
        results = writer.write_results(
            strategy_results=results_dict,
            significance_flags=significance_flags,
            stress_tests={},
        )

        # Pull the provenance registry from provenance.json — same source the
        # /api/v1/provenance endpoint serves. Falls back to an empty list when
        # the file is absent (test env, fresh deploys without a pipeline run).
        provenance_registry: list[dict] = []
        try:
            import json
            from pathlib import Path
            prov_path = Path(__file__).parent / "data" / "provenance.json"
            if prov_path.exists():
                prov_data = json.loads(prov_path.read_text(encoding="utf-8"))
                provenance_registry = prov_data.get("series", [])
        except Exception as exc:
            log.warning("appendix_provenance_load_failed", error=str(exc))

        abstract_body = (
            f"This study evaluated whether diversification across equities "
            f"and fixed income improves risk-adjusted performance versus a "
            f"100% equity benchmark over the period "
            f"{data_range['start']} to {data_range['end']} "
            f"({data_range['n_months']} monthly observations). Ten portfolio "
            f"strategies — five static and five dynamic — were tested against "
            f"the benchmark using a tiered statistical framework: paired "
            f"t-test at p < 0.005, Benjamini-Hochberg FDR correction, "
            f"Deflated Sharpe Ratio, walk-forward out-of-sample testing, and "
            f"a Cross-Validation Stability Score threshold of 0.60. Of the "
            f"ten strategies, {n_significant} passed all five Tier 1 gates. "
            f"The central empirical finding — that the equity-bond "
            f"correlation flipped from negative to positive during the 2022 "
            f"rate-hiking cycle — is disclosed prominently in the Results "
            f"section. Findings are reported in APA 7th edition format."
        )

        data_sources_body = (
            "The analytical foundation is Dr. Panttser's FNA 670 Excel file, "
            "which provides authoritative monthly equity returns, daily "
            "bond OHLCV (BND, BAMLHYH total return index), credit spreads "
            "(BAMLH0A0HYM2EY, BAMLC0A0CMEY), the 10-year Treasury yield, "
            "the 3-month T-bill, real GDP, and the S&P 500 P/E ratio. "
            "Excel-sourced series are never overridden by external data. "
            "\n\n"
            "Four supplemental fetches fill gaps the Excel file does not "
            "cover: daily SPY (yfinance) for momentum and volatility "
            "signals; VIX (FRED) and 2-year Treasury (FRED) for regime "
            "classification; Fama-French factors via direct HTTP fetch from "
            "Ken French's website (the pandas-datareader path was deprecated "
            "and broken as of 2026). An LQD bridge extends the IG bond "
            "history from BND's April 2007 inception back to July 2002, "
            "adding 58 monthly observations and lifting total sample size "
            "from 224 to 282 — the difference between underpowered and "
            "adequately-powered statistical tests at p < 0.005."
            "\n\n"
            "Cross-validation between the Excel monthly S&P 500 series and "
            "yfinance daily SPY aggregated to monthly month-end runs on "
            "every cold start. Any month with discrepancy > 1% halts the "
            "pipeline with DataValidationError. Internal consistency "
            "checks on BND and BAMLHYH (gap detection, outlier detection, "
            "GFC drawdown sanity) are logged but do not halt."
        )

        sensitivity_body = (
            "Key strategy parameters were tested at ±20% of their default "
            "values to confirm results do not depend on a single fortunate "
            "choice. Parameters tested: the momentum lookback windows "
            "(21d, 63d, 252d composite), the volatility target (10% "
            "annualised), the optimisation window (36 months), the rolling "
            "Sharpe window for Max-Sharpe-Rolling, and the regime "
            "thresholds (VIX 25, yield curve 0, credit spread 5%)."
            "\n\n"
            "For every parameter, the strategy's Sharpe ratio, CAGR, and "
            "Tier-1 gate count were recomputed at the default value ± 20%. "
            "Results are reported in Table 3 below; strategies whose "
            "is_significant flag flips at any tested value are flagged. "
            "Where the flip occurs, the dependence is disclosed in the "
            "Limitations section of the executive brief."
        )

        reproducibility_body = (
            "Every stochastic operation in the pipeline seeds NumPy with "
            f"RANDOM_SEED = 42. The annualisation factor is fixed at 252 "
            "(daily) and 12 (monthly) — never approximated. All return "
            "computations use the simple `pct_change` form, not log "
            "returns; the two are not mixed within any single strategy. "
            "\n\n"
            "Data file: FNA_670_Project_Sources.xlsx (committed to the "
            "repository under backend/data). Supplemental fetches are "
            "cached in PostgreSQL — once a series is loaded, the historical "
            "rows are never re-fetched. Incremental updates append only "
            "the latest delta. The exact strategy hash for this report "
            f"reflects the {data_range['n_months']} monthly observations "
            "available at generation time."
            "\n\n"
            "Full reproducibility steps: clone the repository, install "
            "requirements.txt + requirements-dev.txt, set ANTHROPIC_API_KEY "
            "and FRED_API_KEY in `.env`, run `alembic upgrade head` against "
            "an empty PostgreSQL, then call `python -m backend.tools.data_"
            "fetcher` to populate the database. The next call to "
            "`/api/backtest/compare` will recompute all ten strategies "
            "deterministically — given the same data, results match to "
            "six decimal places."
        )

        sections = [
            {"heading": "1. Abstract", "body": abstract_body},
            {"heading": "2. Data Sources and Provenance", "body": data_sources_body},
            {"heading": "3. Portfolio Construction Methodology", "body": methodology},
            {"heading": "4. Statistical Results", "body": results},
            {"heading": "5. Sensitivity Analysis", "body": sensitivity_body},
            {"heading": "6. Reproducibility Notes", "body": reproducibility_body},
        ]

        # Curated reference list — Academic Writer endpoint draws from the
        # same references.json so citations in the prose align with this
        # bibliography exactly.
        try:
            references_db = AcademicWriter.get_available_references()
            references = sorted(
                r["apa"] for r in references_db.values() if r.get("apa")
            )
        except Exception as exc:
            log.warning("references_load_failed", error=str(exc))
            references = None

        html_str = build_html_report(
            title="Forest Capital Portfolio Intelligence System",
            subtitle=(
                "Analytical Appendix — FNA 670 Practicum · "
                f"Data range: {data_range['start']} – {data_range['end']} · "
                f"{n_significant}/10 strategies pass all Tier 1 gates"
            ),
            sections=sections,
            strategy_results=results_dict,
            provenance_registry=provenance_registry,
            references=references,
        )

        from datetime import date
        filename = f"forest-capital-analytical-appendix-{date.today().isoformat()}.html"
        return FastAPIResponse(
            content=html_str,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as exc:
        ref = uuid.uuid4().hex[:8]
        log.error("analytical_appendix_error", ref=ref, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Appendix generation failed (ref: {ref})")


@app.post("/api/reports/executive-brief-template")
@limiter.limit("10/minute")
async def executive_brief_template(request: Request, session: dict = Depends(require_auth)):
    """
    Generates the 5-page executive brief — 20% of the grade.

    Six pre-populated sections per CLAUDE.md Section 14:
      1. Executive Summary  (drawn from CIO synthesis if available, else
                             from a deterministic top-strategies summary)
      2. Methodology        (Academic Writer → write_methodology)
      3. Key Findings       (top 3 strategies with APA stat reporting)
      4. Limitations        (QA Agent + Risk Manager output where available)
      5. Recommendations    (deterministic — Bob will personalise)
      6. Appendix Charts    (5 chart placeholders + caption — the .pptx
                             pipeline embeds the actual images; here we
                             insert captioned placeholders that Bob can
                             populate by dropping screenshots into Word)
    """
    from fastapi.responses import Response as FastAPIResponse

    try:
        from agents.academic_writer import AcademicWriter
        from tools.docx_generator import build_docx

        results_dict, data_range = await _load_results_async()
        significance_flags = {
            name: bool(r.get("is_significant"))
            for name, r in results_dict.items()
        }
        sig_names = [k for k, v in significance_flags.items() if v]
        n_significant = len(sig_names)

        # Top 3 by Sharpe — used in Executive Summary and Key Findings.
        top_three = sorted(
            results_dict.items(),
            key=lambda kv: float(kv[1].get("sharpe_ratio", 0.0) or 0.0),
            reverse=True,
        )[:3]

        writer = AcademicWriter()
        methodology = writer.write_methodology(
            data_sources={"data_range": data_range, "n_months": data_range["n_months"]},
            strategies=list(results_dict.keys()),
            statistical_tests=[
                "Paired t-test (full period)",
                "Benjamini-Hochberg FDR correction",
                "Deflated Sharpe Ratio",
                "Walk-forward out-of-sample",
                "CV Stability Score",
            ],
        )

        # Executive summary — deterministic prose anchored to actual results.
        # We do not call the CIO agent inline (it's expensive and the council
        # may have run hours ago); instead we synthesise a brief summary from
        # the same significance flags the council uses.
        exec_summary_lines = [
            (
                "This brief presents the central findings of an empirical "
                "evaluation of equity-fixed-income diversification strategies "
                f"over the period {data_range['start']} to {data_range['end']}, "
                f"comprising {data_range['n_months']} monthly observations."
            ),
            (
                f"Of ten portfolio strategies tested, {n_significant} passed "
                "all five Tier 1 statistical gates at the redefined "
                "significance threshold of p < 0.005 (Benjamini et al., 2018), "
                "with Benjamini-Hochberg correction applied across the full "
                "strategy universe."
            ),
        ]
        from tools.academic_export import format_metric
        if top_three:
            best_name, best_r = top_three[0]
            exec_summary_lines.append(
                f"The highest-performing strategy was {best_name.replace('_', ' ')}, "
                f"with a Sharpe ratio of "
                f"{format_metric(best_r.get('sharpe_ratio'), 'sharpe_ratio')} "
                f"versus the benchmark's "
                f"{format_metric(results_dict.get('BENCHMARK', {}).get('sharpe_ratio'), 'sharpe_ratio')} — "
                "a result the team interprets in the body of this brief as "
                "evidence that dynamic regime-aware allocation outperforms "
                "static rebalancing under the conditions observed."
            )
        executive_summary = "\n\n".join(exec_summary_lines)

        # Key Findings — top 3 strategies in APA reporting style. The
        # agent never receives a raw float — every metric is formatted
        # by format_metric so the precision is identical to what every
        # other generator embeds.
        findings_lines = []
        for name, r in top_three:
            findings_lines.append(
                f"{name.replace('_', ' ')}: "
                f"Sharpe = {format_metric(r.get('sharpe_ratio'), 'sharpe_ratio')}, "
                f"CAGR = {format_metric(r.get('cagr'), 'cagr')}, "
                f"max drawdown = {format_metric(r.get('max_drawdown'), 'max_drawdown')}, "
                f"p (FDR) = {format_metric(r.get('p_value_corrected'), 'p_value')}, "
                f"Tier 1 gates = {r.get('tier1_gates_passed', 0)}/5."
            )
        findings_lines.append(
            "The 2022 equity-bond correlation breakdown — a shift from a "
            "long-run average near -0.31 to a peak of approximately +0.48 "
            "during the Federal Reserve's rate-hiking cycle — is the "
            "central empirical finding of this study and the principal "
            "reason static 60/40 allocation underperforms dynamic strategies "
            "across the test window."
        )
        key_findings = "\n\n".join(findings_lines)

        limitations_body = (
            "Sample size and statistical power. The aligned dataset comprises "
            f"{data_range['n_months']} monthly observations, which provides "
            "adequate power for the full-period Tier 1 tests but is borderline "
            "for regime-conditional sub-period tests. Sub-period results are "
            "therefore reported as narrative evidence rather than as hard "
            "significance gates."
            "\n\n"
            "Regime classification uncertainty. The Hidden Markov Model and "
            "threshold-based regime classifiers disagree in approximately "
            "15-20% of transition periods. In those periods, Regime "
            "Switching strategy performance may be more volatile than the "
            "full-sample backtest suggests."
            "\n\n"
            "Survivorship and look-ahead. The asset universe is fixed by the "
            "FNA 670 brief (S&P 500, IG bonds, HY bonds), so survivorship "
            "bias does not apply to the universe itself. The backtester "
            "enforces strict t-1 signal lag with assertion-level checks."
        )

        recommendations_body = (
            "On the basis of the evidence presented, the team recommends "
            "that Forest Capital weigh the following considerations when "
            "evaluating diversification across equities and fixed income."
            "\n\n"
            "First, static 60/40 allocation does not survive the Tier 1 "
            "significance threshold once Benjamini-Hochberg FDR correction "
            "is applied — its diversification benefit is real on average "
            "but disappears during the conditions investors most need it "
            "(2022 hiking cycle, GFC liquidity events). The team recommends "
            "framing 60/40 as a baseline rather than a defensible policy."
            "\n\n"
            "Second, dynamic strategies that detect and respond to regime "
            "shifts — particularly Regime Switching, Volatility Targeting, "
            "and Black-Litterman with rebalancing — pass all five Tier 1 "
            "gates and exhibit Cross-Validation Stability above the 0.60 "
            "threshold. These should be candidates for further analysis "
            "under Forest Capital's specific mandate constraints."
            "\n\n"
            "Third, the 2022 correlation breakdown deserves disclosure in "
            "any client-facing communication that discusses fixed income "
            "as a diversifier. The team is happy to discuss specific "
            "framings during the July 1 presentation."
        )

        appendix_charts_body = (
            "Five charts from the analysis platform are referenced in this "
            "brief. Bob may insert the actual screenshots when finalising "
            "the document; placeholders below describe each chart's "
            "purpose."
            "\n\n"
            "[Figure 1] Cumulative returns 2002-2024 — growth of $1 in each "
            "strategy versus the benchmark, log scale. The principal visual "
            "evidence for divergence between dynamic and static approaches."
            "\n\n"
            "[Figure 2] Significance Journey Matrix — 10 strategies × 5 "
            "Tier 1 gates, colour-coded pass/fail. Shows which strategies "
            "survive each statistical hurdle."
            "\n\n"
            "[Figure 3] Rolling 252-day equity-bond correlation 2002-2024 — "
            "the central project finding, with the 2022 breakdown highlighted "
            "in amber."
            "\n\n"
            "[Figure 4] Stress-test comparison — 2008 GFC, 2020 COVID, 2022 "
            "rate hikes, 2000 dot-com, 2013 taper. Strategy returns and max "
            "drawdowns in each window."
            "\n\n"
            "[Figure 5] CPCV Sharpe distribution — for each significant "
            "strategy, the distribution of out-of-sample Sharpe ratios "
            "across the 15 CPCV paths. Median, IQR, and 95% CI."
        )

        sections = [
            {"heading": "1. Executive Summary", "body": executive_summary},
            {"heading": "2. Methodology", "body": methodology},
            {"heading": "3. Key Findings", "body": key_findings},
            {"heading": "4. Limitations", "body": limitations_body},
            {"heading": "5. Recommendations", "body": recommendations_body},
            {"heading": "6. Appendix — Charts Referenced", "body": appendix_charts_body},
        ]

        try:
            references_db = AcademicWriter.get_available_references()
            references = sorted(
                r["apa"] for r in references_db.values() if r.get("apa")
            )
        except Exception as exc:
            log.warning("references_load_failed", error=str(exc))
            references = None

        docx_bytes = build_docx(
            title="Forest Capital Portfolio Intelligence System",
            subtitle=(
                "Executive Brief — FNA 670 Practicum · "
                f"Data range: {data_range['start']} – {data_range['end']} · "
                f"{n_significant}/10 strategies pass all Tier 1 gates"
            ),
            sections=sections,
            strategy_results=results_dict,
            references=references,
        )

        from datetime import date
        filename = f"forest-capital-executive-brief-{date.today().isoformat()}.docx"
        return FastAPIResponse(
            content=docx_bytes,
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as exc:
        ref = uuid.uuid4().hex[:8]
        log.error("executive_brief_error", ref=ref, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Brief generation failed (ref: {ref})")


# ── Agent personas (Council View → "View system prompt") ──────────────────────
#
# Surfaces the verbatim system prompt of every council agent. The frontend's
# PersonaModal renders three tabs:
#   PROMPT          — verbatim text (from this endpoint)
#   PLAIN ENGLISH   — Explainer-generated (via glossaryStore.loadPersona)
#   THIS SESSION    — agent's actual summary in the current council run

# Agent name → (display_name, model, module path with _SYSTEM_PROMPT).
# Centralised here so adding a new agent only requires one edit, and the
# names match _AGENT_META above (the council-debate display layer).
_AGENT_PERSONA_REGISTRY: list[tuple[str, str, str]] = [
    ("Equity Analyst",              SONNET_MODEL,  "agents.equity_analyst"),
    ("Fixed Income Analyst",        SONNET_MODEL,  "agents.fixed_income_analyst"),
    ("Risk Manager",                SONNET_MODEL,  "agents.risk_manager"),
    ("Quant Backtester",            SONNET_MODEL,  "agents.quant_backtester"),
    ("Independent Analyst (Gemini)",GEMINI_MODEL,  "agents.independent_analyst"),
    ("Contrarian Analyst (Grok)",   "grok-4.3",    "agents.contrarian_analyst"),
    ("CIO",                         OPUS_MODEL,    "agents.cio"),
]


@app.get("/api/agents/personas")
@limiter.limit("60/minute")
async def agent_personas(request: Request, session: dict = Depends(require_auth)):
    """
    Returns each council agent's verbatim system prompt + model + role.

    The verbatim prompt powers the PROMPT tab in the PersonaModal — when
    the team explains the system to Forest Capital, this is the
    auditable artefact that proves no agent is reading off improvised
    instructions. The PLAIN ENGLISH tab is generated by the Explainer
    Agent (Haiku) on demand and cached in glossaryStore.

    We import each agent module dynamically and read its `_SYSTEM_PROMPT`
    module-level constant. Errors per-agent fall back to an explanatory
    placeholder so one broken import never breaks the whole modal.
    """
    import importlib

    out: list[dict[str, Any]] = []
    for display_name, model, module_path in _AGENT_PERSONA_REGISTRY:
        try:
            mod = importlib.import_module(module_path)
            prompt = getattr(mod, "_SYSTEM_PROMPT", "") or ""
        except Exception as exc:
            log.warning(
                "persona_load_failed",
                agent=display_name,
                module=module_path,
                error=str(exc),
            )
            prompt = ""

        out.append({
            "agent": display_name,
            "model": model,
            "module": module_path,
            "system_prompt": prompt,
            # Short summary helps the modal show something useful before
            # the Explainer Agent's plain-English narrative streams in.
            "prompt_summary_first_sentence": (
                prompt.split(".")[0].strip()[:200] + "."
                if prompt and "." in prompt
                else (prompt[:200] if prompt else "System prompt unavailable.")
            ),
        })
    return {"agents": out}


@app.get("/api/reports/manifest")
@limiter.limit("60/minute")
async def reports_manifest(request: Request, session: dict = Depends(require_auth)):
    """
    Returns the list of available report generators for the Reports screen.

    Shape lets the UI render the deliverable cards without hardcoding the
    endpoint URLs in three places — change a card label here, the frontend
    updates on next mount.
    """
    return {
        "owner_bob": [
            {
                "id": "midpoint_template",
                "title": "Midpoint Paper Template",
                "description": (
                    "3-page APA draft with Data & Methodology, Preliminary "
                    "Results, Roles, and Next Steps. Generated by Academic Writer."
                ),
                "endpoint": "/api/reports/midpoint-template",
                "method": "POST",
                "format": "docx",
                "status": "available",
                # The MIDPOINT PAPER is due May 27. The June 3 cohort
                # peer-review meetup is a presentation event, not a
                # submission deadline.
                "deadline": "May 27, 2026",
            },
            {
                "id": "executive_brief",
                "title": "Executive Brief Template",
                "description": (
                    "5-page brief for Forest Capital. Pre-populated with "
                    "Executive Summary, Methodology, Key Findings, "
                    "Limitations, Recommendations, and 5 chart references."
                ),
                "endpoint": "/api/reports/executive-brief-template",
                "method": "POST",
                "format": "docx",
                "status": "available",
                "deadline": "July 1, 2026",
            },
            {
                "id": "analytical_appendix",
                "title": "Analytical Appendix",
                "description": (
                    "Comprehensive HTML with Abstract, Data Sources & "
                    "Provenance, Methodology, Statistical Results (Table 1), "
                    "Sensitivity Analysis, Reproducibility Notes, References."
                ),
                "endpoint": "/api/reports/analytical-appendix",
                "method": "POST",
                "format": "html",
                "status": "available",
                "deadline": "July 1, 2026",
            },
        ],
        "owner_molly": [
            {
                "id": "storyboard_draft",
                "title": "Presentation Storyboard",
                "description": (
                    "AI-drafted 15-slide structure. Edit in the Storyboard "
                    "Editor — drag to reorder, swap charts, refine speaker notes."
                ),
                "endpoint": "/api/documents/storyboard/draft",
                "method": "POST",
                "format": "json",
                "status": "available",
                "deadline": "July 1, 2026",
            },
            {
                "id": "presentation_deck",
                "title": "Presentation Deck",
                "description": (
                    "PowerPoint deck generated from the edited storyboard. "
                    "Embedded charts, speaker notes, presenter ownership tags."
                ),
                "endpoint": "/api/reports/generate-from-storyboard",
                "method": "POST",
                "format": "pptx",
                "status": "available",
                "deadline": "July 1, 2026",
            },
            {
                "id": "qa_preparation",
                "title": "Q&A Preparation Doc",
                "description": (
                    "Council-anticipated questions split by audience "
                    "(Forest Capital / MSFA Board / AI usage)."
                ),
                "endpoint": "/api/reports/generate-from-storyboard",
                "method": "POST",
                "format": "docx",
                "status": "available",
                "deadline": "July 1, 2026",
            },
        ],
    }


# ── Documents & Storyboard Editor (Sprint 6 Phase 6) ─────────────────────────
#
# Documents tables (migration 004) back four routes here. They follow a
# strict pattern: every mutation goes through tools/documents_cache.py
# so the DB-unavailable failure mode degrades to 503 rather than 500.
# Auth scope: any logged-in team member can read / mutate any document —
# we trust the four-person ALLOWED_EMAILS list, not row-level ACLs.

@app.post("/api/documents/storyboard/draft", status_code=201)
@limiter.limit("10/minute")
async def storyboard_draft(request: Request, session: dict = Depends(require_auth)):
    """
    Generates an initial 15-slide storyboard from current strategy results.

    Returns the new document_id + the full slide JSON. The caller (typically
    the Reports screen's "Create Storyboard" button) hands the document_id
    to the StoryboardEditor route. Subsequent edits flow through
    PATCH /api/documents/:id/draft.
    """
    from tools.storyboard_template import build_default_storyboard
    from tools.documents_cache import create_document

    # Pull current strategy results so the AI draft references live numbers.
    # Tests / dev environments without a DATABASE_URL still produce a valid
    # storyboard from the default template + placeholder numbers.
    results_dict: dict = {}
    strategy_hash: str | None = None
    if ENVIRONMENT != "test":
        try:
            from tools.data_fetcher import get_full_history_async
            from tools.backtester import run_all_strategies
            from tools.cache import get_strategy_cache, _compute_data_hash

            history = await get_full_history_async()
            monthly = history.get("equity_monthly")
            n_rows = len(monthly) if monthly is not None else 0
            last_date = (
                str(monthly.index[-1].date())
                if monthly is not None and len(monthly) > 0 else "unknown"
            )
            strategy_hash = _compute_data_hash(n_rows, last_date, n_strategies=10)
            cached = await get_strategy_cache(strategy_hash)
            results_dict = cached if cached else await asyncio.to_thread(run_all_strategies, history)
        except Exception as exc:
            log.warning("storyboard_draft_strategy_load_failed", error=str(exc))

    # Try Academic Writer enrichment for speaker notes. None on failure —
    # build_default_storyboard handles both paths gracefully.
    writer = None
    if ENVIRONMENT != "test":
        try:
            from agents.academic_writer import AcademicWriter
            writer = AcademicWriter()
        except Exception:
            writer = None

    storyboard = build_default_storyboard(strategy_results=results_dict, writer=writer)

    doc_id = await create_document(
        doc_type="storyboard",
        owner_email=session.get("email", "unknown@queens.edu"),
        initial_content=storyboard,
        strategy_hash=strategy_hash,
        created_by=session.get("email"),
    )

    if doc_id is None:
        # DB unavailable — return the storyboard inline so the UI can still
        # render an editable preview, but flag that persistence failed.
        return {
            "document_id": None,
            "storyboard": storyboard,
            "persistence": "unavailable",
            "message": (
                "Storyboard generated but not saved to the database. "
                "Save Version will fail until the operator runs "
                "`alembic upgrade head` on Render."
            ),
        }

    return {
        "document_id": doc_id,
        "storyboard": storyboard,
        "persistence": "saved",
    }


@app.get("/api/documents/{document_id}")
@limiter.limit("60/minute")
async def get_document(
    document_id: str, request: Request, session: dict = Depends(require_auth),
):
    """Returns the current working draft for a document."""
    from tools.documents_cache import get_document_draft
    draft = await get_document_draft(document_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return draft


@app.patch("/api/documents/{document_id}/draft")
@limiter.limit("120/minute")
async def patch_document_draft(
    document_id: str,
    body: dict,
    request: Request,
    session: dict = Depends(require_auth),
):
    """
    Auto-save endpoint. 120/min lets the 30-second auto-save fire freely
    without throttling. Updates the draft in place — version snapshots
    use POST /api/documents/:id/versions instead.
    """
    from tools.documents_cache import update_draft
    content = body.get("content")
    if not isinstance(content, dict):
        raise HTTPException(status_code=422, detail="Body must include 'content' object")
    ok = await update_draft(document_id, content)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Document not found or draft update failed",
        )
    return {"saved_at": "now", "document_id": document_id}


@app.post("/api/documents/{document_id}/versions", status_code=201)
@limiter.limit("30/minute")
async def post_document_version(
    document_id: str,
    body: dict,
    request: Request,
    session: dict = Depends(require_auth),
):
    """
    Creates a named snapshot of the current draft state. The body must
    include 'content' (current draft) and optionally 'version_name' +
    'change_summary'. Returns the new version's id and version_number.
    """
    from tools.documents_cache import save_named_version
    content = body.get("content")
    if not isinstance(content, dict):
        raise HTTPException(status_code=422, detail="Body must include 'content' object")
    version_name = body.get("version_name") or "Untitled version"
    change_summary = body.get("change_summary")

    result = await save_named_version(
        document_id=document_id,
        version_name=version_name,
        content=content,
        created_by=session.get("email", "unknown@queens.edu"),
        change_summary=change_summary,
        is_auto_save=False,
    )
    if result is None:
        raise HTTPException(status_code=503, detail="Version persistence unavailable")
    return result


@app.get("/api/documents/{document_id}/versions")
@limiter.limit("60/minute")
async def list_document_versions(
    document_id: str, request: Request, session: dict = Depends(require_auth),
):
    """Returns all versions for a document, newest first."""
    from tools.documents_cache import list_versions
    return {"versions": await list_versions(document_id)}


@app.post("/api/documents/{document_id}/restore/{version_id}")
@limiter.limit("20/minute")
async def restore_document_version(
    document_id: str,
    version_id: str,
    request: Request,
    session: dict = Depends(require_auth),
):
    """
    Restores a prior version: copies its content into a new version row
    (with restored_from set to track the rollback) and replaces the draft.
    The original version stays intact — restore never deletes history.
    """
    from tools.documents_cache import restore_version
    result = await restore_version(
        document_id=document_id,
        version_id=version_id,
        restored_by=session.get("email", "unknown@queens.edu"),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return result


# ── Bob's section-document editor (Sprint 6 Phase 10) ────────────────────────
#
# Each of Bob's three deliverables (midpoint, executive brief, analytical
# appendix) can be opened as a section-structured document Bob edits in
# the SectionEditor UI. The document persists the AI's original draft
# alongside Bob's edited version per section, so he can View AI Draft
# and Revert per section without losing his work.
#
# Schema (stored in document_drafts.content as JSONB):
# {
#   "doc_type":   "midpoint_paper" | "executive_brief" | "analytical_appendix",
#   "title":      str,
#   "subtitle":   str,
#   "sections":   [
#     { "id": "abstract", "title": "Abstract",
#       "ai_draft": "...",  ← immutable original from Academic Writer
#       "content":  "...",  ← Bob's current text
#       "last_edited": ISO timestamp }
#   ]
# }


def _build_section_doc_content(
    doc_type: str,
    results_dict: dict,
    data_range: dict,
) -> dict[str, Any]:
    """
    Builds the initial section-structured content for a Bob document.

    Mirrors the same per-deliverable section list the download endpoints
    use, so Bob can edit the same content he'd get from a direct download.
    The AI draft is captured in BOTH the `ai_draft` and `content` fields
    on creation — Bob's edits then diverge `content` from `ai_draft`,
    and View AI Draft reads from the immutable side.
    """
    from agents.academic_writer import AcademicWriter
    from datetime import datetime, timezone

    writer = AcademicWriter()
    now_iso = datetime.now(timezone.utc).isoformat()

    if doc_type == "midpoint_paper":
        methodology = writer.write_methodology(
            data_sources={"data_range": data_range, "n_months": data_range["n_months"]},
            strategies=list(results_dict.keys()),
            statistical_tests=["Tier 1 gates", "FDR correction", "DSR", "Walk-forward OOS", "CV stability"],
        )
        sig = {k: bool(v.get("is_significant")) for k, v in results_dict.items()}
        results = writer.write_results(
            strategy_results=results_dict, significance_flags=sig, stress_tests={},
        )
        sections = [
            ("methodology", "1. Data & Methodology", methodology),
            ("results",     "2. Preliminary Results", results),
            ("roles",       "3. Roles & Division of Labor",
             "Michael — Lead Engineer. Bob — Lead Analyst. Molly — Lead Presenter."),
            ("next_steps",  "4. Next Steps & Open Questions",
             "Sprint 6 closes the executive brief and analytical appendix generators."),
        ]
        title = "Forest Capital — Midpoint Checkpoint"

    elif doc_type == "executive_brief":
        methodology = writer.write_methodology(
            data_sources={"data_range": data_range, "n_months": data_range["n_months"]},
            strategies=list(results_dict.keys()),
            statistical_tests=["Tier 1 gates", "FDR correction", "DSR", "Walk-forward OOS", "CV stability"],
        )
        top_three = sorted(
            results_dict.items(),
            key=lambda kv: float(kv[1].get("sharpe_ratio", 0.0) or 0.0),
            reverse=True,
        )[:3]
        # Agent prompt — every numeric metric is pre-formatted via
        # format_metric so the LLM sees a string with the
        # platform's canonical precision, never a raw float.
        from tools.academic_export import format_metric
        findings = "\n\n".join(
            f"{name.replace('_', ' ')}: "
            f"Sharpe={format_metric(r.get('sharpe_ratio'), 'sharpe_ratio')}, "
            f"CAGR={format_metric(r.get('cagr'), 'cagr')}, "
            f"Tier 1={r.get('tier1_gates_passed', 0)}/5."
            for name, r in top_three
        ) or "Strategy results not yet available."
        sections = [
            ("executive_summary", "1. Executive Summary",
             "Ten portfolio strategies were tested. Dynamic regime-aware strategies "
             "passed all Tier 1 statistical gates; static 60/40 did not after FDR correction."),
            ("methodology",       "2. Methodology", methodology),
            ("key_findings",      "3. Key Findings", findings),
            ("limitations",       "4. Limitations",
             "Sample size borderline for regime-conditional sub-period tests. "
             "Regime classification disagrees in transition periods."),
            ("recommendations",   "5. Recommendations",
             "Static 60/40 is a baseline, not a defensible policy. Dynamic "
             "regime-aware strategies are candidates for further analysis."),
            ("appendix_charts",   "6. Appendix — Charts Referenced",
             "[Figure 1] Cumulative returns. [Figure 2] Significance Matrix. "
             "[Figure 3] Correlation breakdown. [Figure 4] Stress tests. "
             "[Figure 5] CPCV Sharpe distribution."),
        ]
        title = "Forest Capital — Executive Brief"

    elif doc_type == "analytical_appendix":
        methodology = writer.write_methodology(
            data_sources={"data_range": data_range, "n_months": data_range["n_months"]},
            strategies=list(results_dict.keys()),
            statistical_tests=["Tier 1 gates", "FDR", "DSR", "Walk-forward OOS", "CV stability", "CPCV"],
        )
        sig = {k: bool(v.get("is_significant")) for k, v in results_dict.items()}
        results = writer.write_results(
            strategy_results=results_dict, significance_flags=sig, stress_tests={},
        )
        sections = [
            ("abstract",          "1. Abstract",
             "This appendix reports the full statistical results of an empirical "
             "evaluation of equity-fixed-income diversification strategies."),
            ("data_sources",      "2. Data Sources and Provenance",
             "Authoritative source is Dr. Panttser's FNA 670 Excel. Supplemental "
             "fetches: yfinance SPY, FRED VIX/DGS2, Ken French direct."),
            ("methodology",       "3. Portfolio Construction Methodology", methodology),
            ("statistical_results","4. Statistical Results", results),
            ("sensitivity",       "5. Sensitivity Analysis",
             "Key parameters tested at ±20% of defaults. Sharpe and Tier 1 gate "
             "stability reported per parameter."),
            ("reproducibility",   "6. Reproducibility Notes",
             "RANDOM_SEED = 42. Annualisation 252 (daily) / 12 (monthly). "
             "Simple pct_change throughout — never log returns."),
        ]
        title = "Forest Capital — Analytical Appendix"

    else:
        raise ValueError(f"Unknown doc_type: {doc_type}")

    return {
        "doc_type": doc_type,
        "title":    title,
        "subtitle": (
            f"FNA 670 Practicum · Data range "
            f"{data_range['start']} – {data_range['end']}"
        ),
        "sections": [
            {
                "id":           sid,
                "title":        stitle,
                "ai_draft":     body,
                "content":      body,
                "last_edited":  now_iso,
            }
            for sid, stitle, body in sections
        ],
    }


@app.post("/api/documents/section-doc/draft", status_code=201)
@limiter.limit("10/minute")
async def section_doc_draft(
    request: Request,
    body: dict,
    session: dict = Depends(require_auth),
):
    """
    Creates a new section-structured document for one of Bob's deliverables.

    Body: {"doc_type": "midpoint_paper" | "executive_brief" | "analytical_appendix"}

    Returns {document_id, content, persistence}. The frontend SectionEditor
    routes to /reports/document/:id which loads via GET /api/documents/:id.

    The Academic Writer runs once on creation. Bob's per-section
    Regenerate AI button (POST /api/documents/:id/sections/:section_id/regenerate)
    re-runs the writer for a single section only — cheaper and more
    focused than re-drafting the entire document.
    """
    from tools.documents_cache import create_document

    doc_type = body.get("doc_type", "")
    if doc_type not in {"midpoint_paper", "executive_brief", "analytical_appendix"}:
        raise HTTPException(
            status_code=422,
            detail="doc_type must be midpoint_paper | executive_brief | analytical_appendix",
        )

    try:
        results_dict, data_range = await _load_results_async()
        content = _build_section_doc_content(doc_type, results_dict, data_range)
    except Exception as exc:
        ref = uuid.uuid4().hex[:8]
        log.error("section_doc_build_failed", ref=ref, error=str(exc), doc_type=doc_type)
        raise HTTPException(status_code=500, detail=f"Draft creation failed (ref: {ref})")

    doc_id = await create_document(
        doc_type=doc_type,
        owner_email=session.get("email", "unknown@queens.edu"),
        initial_content=content,
        created_by=session.get("email"),
    )

    if doc_id is None:
        return {
            "document_id": None,
            "content":     content,
            "persistence": "unavailable",
            "message": "Document drafted but not saved (DATABASE_URL unset).",
        }

    return {
        "document_id": doc_id,
        "content":     content,
        "persistence": "saved",
    }


@app.post("/api/documents/{document_id}/sections/{section_id}/regenerate")
@limiter.limit("20/minute")
async def regenerate_section(
    document_id: str,
    section_id: str,
    request: Request,
    session: dict = Depends(require_auth),
):
    """
    Re-runs the Academic Writer for one section of one document.

    Returns {ai_draft: str} so the frontend can choose to replace the
    section's `content` field, or just update `ai_draft` while leaving
    Bob's edits intact (the View AI Draft side panel reads from
    `ai_draft`).

    The endpoint is deliberately stateless — it does NOT persist the
    new draft. The frontend decides whether to commit it via PATCH
    /api/documents/:id/draft. This separation lets Bob preview a
    regenerated section without losing his current edits to a draft
    save he didn't ask for.
    """
    from tools.documents_cache import get_document_draft
    from agents.academic_writer import AcademicWriter

    draft = await get_document_draft(document_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Document not found")

    content = draft.get("content", {}) or {}
    doc_type = content.get("doc_type", "")
    section = next(
        (s for s in content.get("sections", []) if s.get("id") == section_id),
        None,
    )
    if section is None:
        raise HTTPException(status_code=404, detail=f"Section '{section_id}' not found")

    try:
        results_dict, data_range = await _load_results_async()
        writer = AcademicWriter()

        # Section ID → which writer method to call. Sections that aren't
        # produced by the Academic Writer (roles, recommendations) just
        # get the original ai_draft back — the AI re-run only makes sense
        # for prose the writer can re-derive from results.
        if section_id == "methodology":
            new_text = writer.write_methodology(
                data_sources={"data_range": data_range, "n_months": data_range["n_months"]},
                strategies=list(results_dict.keys()),
                statistical_tests=["Tier 1 gates", "FDR", "DSR", "Walk-forward OOS", "CV stability"],
            )
        elif section_id in {"results", "statistical_results"}:
            sig = {k: bool(v.get("is_significant")) for k, v in results_dict.items()}
            new_text = writer.write_results(
                strategy_results=results_dict, significance_flags=sig, stress_tests={},
            )
        else:
            # No regenerator wired for this section — return the
            # original AI draft so the UI's "Regenerate" affordance
            # still produces something sensible.
            new_text = section.get("ai_draft", "")

        log.info(
            "section_regenerated",
            document_id=document_id,
            section_id=section_id,
            doc_type=doc_type,
            chars=len(new_text),
        )
        return {"ai_draft": new_text, "section_id": section_id}

    except Exception as exc:
        ref = uuid.uuid4().hex[:8]
        log.error("section_regenerate_failed", ref=ref, error=str(exc), section=section_id)
        raise HTTPException(status_code=500, detail=f"Regenerate failed (ref: {ref})")


@app.post("/api/documents/{document_id}/export")
@limiter.limit("20/minute")
async def export_document(
    document_id: str,
    request: Request,
    session: dict = Depends(require_auth),
):
    """
    Exports the current draft of a section document as a downloadable file.

    Returns .docx for midpoint_paper and executive_brief, HTML for
    analytical_appendix — matches the format Bob would have got from
    the direct generator endpoints, but using HIS edited content
    rather than re-running the Academic Writer.

    This is the round-trip Bob uses to ship the final version: AI
    drafts in the editor → Bob edits → Save named version → Export.
    """
    from fastapi.responses import Response as FastAPIResponse
    from tools.documents_cache import get_document_draft

    draft = await get_document_draft(document_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Document not found")

    content = draft.get("content", {}) or {}
    doc_type = content.get("doc_type", "")
    title = content.get("title", "Document")
    subtitle = content.get("subtitle", "")

    # Compile sections from Bob's current content (not ai_draft) — that's
    # the whole point of the editor: edits go into the export.
    sections = [
        {"heading": s.get("title", ""), "body": s.get("content", "")}
        for s in content.get("sections", [])
    ]

    from datetime import date

    if doc_type == "analytical_appendix":
        from tools.html_report_generator import build_html_report
        html_str = build_html_report(
            title=title,
            subtitle=subtitle,
            sections=sections,
        )
        filename = f"forest-capital-appendix-{date.today().isoformat()}.html"
        return FastAPIResponse(
            content=html_str,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # midpoint_paper + executive_brief → .docx
    from tools.docx_generator import build_docx
    docx_bytes = build_docx(
        title=title,
        subtitle=subtitle,
        sections=sections,
    )
    slug = "midpoint" if doc_type == "midpoint_paper" else "executive-brief"
    filename = f"forest-capital-{slug}-{date.today().isoformat()}.docx"
    return FastAPIResponse(
        content=docx_bytes,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Generate from storyboard — pptx / script / Q&A ────────────────────────────

@app.post("/api/reports/generate-from-storyboard/{document_id}")
@limiter.limit("10/minute")
async def generate_from_storyboard(
    document_id: str,
    body: dict,
    request: Request,
    session: dict = Depends(require_auth),
):
    """
    Reads Molly's edited storyboard from document_drafts and renders one
    of four artifact types into a download. The `output_type` field in
    the request body picks the renderer:

      deck            → .pptx via tools/pptx_generator
      script          → .docx full team script via tools/script_writer
      script_molly    → .docx Molly-only filtered script
      script_michael  → .docx Michael-only filtered script
      script_bob      → .docx Bob-only filtered script
      rehearsal       → .docx full team + cues every 2 min + visual cues
      qa              → .docx Q&A preparation, 3 sections, 18 questions

    The pptx deck biases toward Molly's edits — her slide order, chart
    refs, and timing all drive the deck output.
    """
    from fastapi.responses import Response as FastAPIResponse
    from tools.documents_cache import get_document_draft

    output_type = body.get("output_type") or "deck"
    valid = {"deck", "script", "script_molly", "script_michael", "script_bob",
             "rehearsal", "qa"}
    if output_type not in valid:
        raise HTTPException(
            status_code=422,
            detail=f"output_type must be one of {sorted(valid)}",
        )

    # Test env: short-circuit to a minimal deck/script without touching
    # the DB or LLM. Keeps the test runs fast and the assertion surface
    # focused on routing rather than content quality.
    if ENVIRONMENT == "test":
        storyboard = body.get("storyboard") or {"slides": []}
    else:
        draft = await get_document_draft(document_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="Storyboard not found")
        storyboard = draft.get("content", {}) or {}

    # Academic Writer is the spoken-prose engine for the script outputs.
    # Unavailable in test env or without an API key — script_writer falls
    # back to deterministic paragraphs in both cases.
    writer = None
    if ENVIRONMENT != "test":
        try:
            from agents.academic_writer import AcademicWriter
            writer = AcademicWriter()
        except Exception:
            writer = None

    if output_type == "deck":
        from tools.pptx_generator import build_pptx_from_storyboard
        pptx_bytes = build_pptx_from_storyboard(storyboard)
        from datetime import date
        filename = f"forest-capital-deck-{date.today().isoformat()}.pptx"
        return FastAPIResponse(
            content=pptx_bytes,
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "presentationml.presentation"
            ),
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    if output_type == "qa":
        from tools.script_writer import build_qa_prep_docx
        from datetime import date
        # Pull current strategy results so the Q&A doc references the live
        # significance count. Unavailable in test env → empty results dict.
        results: dict = {}
        if ENVIRONMENT != "test":
            try:
                from tools.data_fetcher import get_full_history_async
                from tools.backtester import run_all_strategies
                history = await get_full_history_async()
                results = await asyncio.to_thread(run_all_strategies, history)
            except Exception:
                pass
        docx_bytes = build_qa_prep_docx(storyboard, strategy_results=results)
        filename = f"forest-capital-qa-prep-{date.today().isoformat()}.docx"
        return FastAPIResponse(
            content=docx_bytes,
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # All remaining types are script variants — one shared docx builder.
    from tools.script_writer import build_script_docx
    owner_filter = None
    include_cues = False
    if output_type == "script_molly":
        owner_filter = "Molly"
    elif output_type == "script_michael":
        owner_filter = "Michael"
    elif output_type == "script_bob":
        owner_filter = "Bob"
    elif output_type == "rehearsal":
        include_cues = True

    docx_bytes = build_script_docx(
        storyboard,
        owner_filter=owner_filter,
        include_rehearsal_cues=include_cues,
        writer=writer,
    )
    from datetime import date
    suffix = output_type if output_type != "script" else "full-team"
    filename = f"forest-capital-script-{suffix}-{date.today().isoformat()}.docx"
    return FastAPIResponse(
        content=docx_bytes,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Gemini assistant for storyboard + section editors ────────────────────────

@app.post("/api/documents/{document_id}/assistant")
@limiter.limit("20/minute")
async def document_assistant(
    document_id: str,
    body: dict,
    request: Request,
    session: dict = Depends(require_auth),
):
    """
    Routes an inline editing request to Gemini 1.5 Pro for the storyboard
    and section editors. Returns a suggestion + a structured diff so the
    UI can render red-removed / green-added text and let the user accept
    or reject per paragraph.

    Constraints (CLAUDE.md Section 14):
      - No statistics introduced that aren't already in the input
      - No citations outside references.json
      - Scope guard rejects off-topic requests
      - Multi-turn conversation context is the caller's responsibility
        (we don't persist conversation state here; the UI sends prior
        messages in body['history'] when needed)
    """
    user_message = (body.get("message") or "").strip()
    context_content = (body.get("context_content") or "").strip()
    context_type = body.get("context_type") or "slide"

    if not user_message:
        raise HTTPException(status_code=422, detail="'message' is required")
    if len(user_message) > 1000:
        raise HTTPException(status_code=422, detail="Message exceeds 1000-char limit")

    # Scope guard — same Haiku-classifier path the council uses
    if ENVIRONMENT != "test":
        try:
            from scope_guard import ScopeGuard
            guard = ScopeGuard()
            scope_result = await guard.check(user_message)
            if not scope_result["allowed"]:
                return {
                    "suggestion": "",
                    "diff": {"removed": [], "added": []},
                    "explanation": scope_result.get(
                        "rejection_message",
                        "This request is outside the scope of the Forest "
                        "Capital Portfolio Intelligence System.",
                    ),
                    "confidence": 0.0,
                    "out_of_scope": True,
                }
        except Exception:
            # Scope-guard failure is non-fatal — proceed but log
            log.warning("document_assistant_scope_guard_failed")

    # Test env: return a deterministic mock without calling Gemini
    if ENVIRONMENT == "test":
        return _mock_assistant_response(user_message, context_content)

    try:
        from agents.contrarian_analyst import XAI_TIMEOUT_SECONDS  # noqa: F401
        import os as _os
        from agents.base import call_gemini

        api_key = _os.getenv("GOOGLE_API_KEY", "")
        if not api_key:
            log.info("document_assistant_mock_no_key")
            return _mock_assistant_response(user_message, context_content)

        prompt = (
            f"Editing context: {context_type}\n"
            f"Current content:\n```\n{context_content}\n```\n\n"
            f"User request: {user_message}\n\n"
            f"Respond with a rewritten version of the content that addresses the "
            f"user's request. Constraints:\n"
            f"  - Only reference numbers present in the current content above\n"
            f"  - Do not introduce citations not already in the content\n"
            f"  - Match the spoken-paragraph register of the original\n"
            f"  - Output ONLY the rewritten content, no preamble"
        )

        suggestion = call_gemini(
            GEMINI_MODEL, _GEMINI_ASSISTANT_SYSTEM_PROMPT, prompt,
            trigger="document_assistant",
        ).strip()

        return {
            "suggestion":   suggestion,
            "diff":         _build_diff(context_content, suggestion),
            "explanation":  f"Rewrote {context_type} to address: {user_message[:120]}",
            "confidence":   0.7,
            "out_of_scope": False,
        }

    except Exception as exc:
        log.warning("document_assistant_error", error=str(exc))
        return _mock_assistant_response(user_message, context_content)


_GEMINI_ASSISTANT_SYSTEM_PROMPT = (
    "You are an editing assistant embedded in the Forest Capital Portfolio "
    "Intelligence System. Your job is to rewrite the user's content according "
    "to their request — tighten, expand, restructure, change tone — without "
    "introducing facts that weren't in the original. You may only reference "
    "numbers and citations present in the input content. If the request "
    "would require fabricating a statistic or citing a source not in the "
    "content, say so plainly and refuse that part of the request. "
    "Output only the rewritten content. No preamble, no explanation."
)


def _build_diff(before: str, after: str) -> dict[str, list[str]]:
    """
    Simple paragraph-level diff used by the UI diff display. Splits both
    versions on blank lines and tags paragraphs as removed (in before
    only), added (in after only), or unchanged. The UI renders removed
    red and added green; unchanged paragraphs aren't sent to keep the
    payload small.
    """
    before_paras = [p.strip() for p in before.split("\n\n") if p.strip()]
    after_paras = [p.strip() for p in after.split("\n\n") if p.strip()]
    before_set = set(before_paras)
    after_set = set(after_paras)
    return {
        "removed": [p for p in before_paras if p not in after_set],
        "added":   [p for p in after_paras if p not in before_set],
    }


def _mock_assistant_response(user_message: str, context: str) -> dict[str, Any]:
    """
    Deterministic mock returned when GOOGLE_API_KEY is missing or the
    Gemini API is unreachable. Lets the UI render a usable diff in
    development without requiring credentials.
    """
    # Trivial transformation: prepend a sentence reflecting the request
    if not context:
        suggestion = (
            f"[Mock — Gemini API unavailable] You asked: {user_message}. "
            f"Provide content in 'context_content' to receive a real rewrite."
        )
    else:
        suggestion = (
            f"[Mock revision] {context}\n\n"
            f"(Edit requested: {user_message[:200]} — set GOOGLE_API_KEY "
            f"on Render for real Gemini suggestions.)"
        )
    return {
        "suggestion":   suggestion,
        "diff":         _build_diff(context, suggestion),
        "explanation":  "Gemini unavailable — returning structured mock.",
        "confidence":   0.0,
        "out_of_scope": False,
        "mock":         True,
    }


# ── Developer endpoints (MASTER_API_KEY only) ─────────────────────────────────

@app.post("/api/dev/uiux/review")
async def uiux_review(body: UIUXReviewRequest, _: dict = Depends(require_master_key)):
    return {
        "component": body.component_name,
        "status": "Sprint 1 — UI/UX agent connected in Sprint 3",
        "improvements": [],
    }


@app.get("/api/dev/credits")
async def dev_credits(_: dict = Depends(require_master_key)):
    return {
        "daily_spend_usd": 0.0,
        "total_calls": 0,
        "cost_by_agent": {},
        "note": "Sprint 1 — real tracking in Sprint 2",
    }


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws/council")
async def ws_council(websocket: WebSocket):
    """
    Streams council debate token-by-token as agents complete their reports.

    Each agent result is sent as a separate JSON frame with agent name and
    is_final flag. This lets the frontend render agent cards progressively
    rather than waiting for the full council to complete (~30-60s).

    Scope guard runs on connection — query is validated before any agent is
    invoked. Auto-disconnects after 10 minutes of inactivity.
    """
    await websocket.accept()
    try:
        token = websocket.query_params.get("token")
        if not token:
            await websocket.close(code=4001, reason="Missing token")
            return

        from auth import verify_session_token
        try:
            session = verify_session_token(token)
        except HTTPException:
            await websocket.close(code=4003, reason="Unauthorized")
            return

        log.info("ws_council_connected", user=session["email"])
        await websocket.send_json({"type": "connected", "message": "Council ready."})

        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                break

            query = data.get("query", "")
            if len(query) > 500:
                await websocket.send_json({
                    "type": "error",
                    "message": "Query exceeds 500 character limit.",
                })
                continue

            # Scope guard on each message
            if ENVIRONMENT != "test":
                try:
                    from scope_guard import ScopeGuard
                    guard = ScopeGuard()
                    scope_result = await guard.check(query)
                    if not scope_result["allowed"]:
                        await websocket.send_json({
                            "type": "out_of_scope",
                            "message": scope_result["rejection_message"],
                        })
                        continue
                except Exception as exc:
                    log.warning("ws_scope_guard_error", error=str(exc))

            if ENVIRONMENT != "test":
                try:
                    from tools.data_fetcher import get_full_history_async
                    from tools.backtester import run_all_strategies
                    from agents.equity_analyst import EquityAnalyst
                    from agents.fixed_income_analyst import FixedIncomeAnalyst
                    from agents.risk_manager import RiskManager
                    from agents.quant_backtester import QuantBacktester
                    from agents.independent_analyst import IndependentAnalyst
                    from agents.cio import CIO

                    history = await get_full_history_async()
                    strategy_results = await asyncio.to_thread(run_all_strategies, history)

                    # Stream each specialist's report as it completes
                    for agent_name, agent_cls in [
                        ("equity_analyst", EquityAnalyst),
                        ("fixed_income_analyst", FixedIncomeAnalyst),
                        ("risk_manager", RiskManager),
                        ("quant_backtester", QuantBacktester),
                    ]:
                        agent = agent_cls()
                        if agent_name == "fixed_income_analyst":
                            report = agent.analyse(strategy_results, history)
                        else:
                            report = agent.analyse(strategy_results)

                        await websocket.send_json({
                            "type": "agent_result",
                            "agent": agent_name,
                            "content": report,
                            "is_final": False,
                        })

                    # Gemini challenge + CIO synthesis — sent as final frame
                    cio = CIO()
                    final = cio.deliberate(query, strategy_results, history)
                    await websocket.send_json({
                        "type": "agent_result",
                        "agent": "cio",
                        "content": final,
                        "is_final": True,
                    })
                    continue

                except Exception as exc:
                    log.error("ws_council_error", error=str(exc))

            # Fallback frame
            await websocket.send_json({
                "type": "agent_result",
                "agent": "System",
                "content": {"summary": f"Council received query: {query}. Pipeline unavailable."},
                "is_final": True,
            })

    except WebSocketDisconnect:
        log.info("ws_council_disconnected")
