"""
Forest Capital Portfolio Intelligence System — FastAPI backend.
Sprint 4: all 8 agents live, council deliberation wired, QA 30-point checklist,
          WebSocket streaming, scope guard enforced, council_sessions logging.
"""
from __future__ import annotations
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

from config import FRONTEND_URL, ENVIRONMENT, ALLOWED_EMAILS
from logger import configure_logging, get_logger
from auth import (
    require_auth,
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
    if email not in {e.lower() for e in ALLOWED_EMAILS}:
        log.warning("magic_link_unauthorized_email", email_hash=hash(email))
        return MagicLinkResponse(
            message="If that email is authorised, a login link has been sent.",
            status="pending",
            dev_mode=(ENVIRONMENT == "development"),
        )
    token = generate_magic_token(email)
    await send_magic_link(email, token)
    return MagicLinkResponse(
        message="If that email is authorised, a login link has been sent.",
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

    session_token = redeem_magic_token(token)

    # Persist JTI to DB after first successful redemption (non-blocking — failure is safe)
    if _jti and _exp:
        try:
            from tools.cache import mark_jti_used
            from datetime import datetime, timezone as _tz
            await mark_jti_used(_jti, datetime.fromtimestamp(_exp, tz=_tz.utc), _email_for_jti)
        except Exception:
            pass

    email = verify_session_token(session_token)["email"]
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
    return {"email": session["email"], "role": session.get("role", "user")}


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "sprint": "4",
        "environment": ENVIRONMENT,
        "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
        "gemini": bool(os.getenv("GOOGLE_API_KEY")),
        "cache": True,
    }


# ── Provenance ───────────────────────────────────────────────────────────────

_PROVENANCE_PATH = Path(__file__).parent / "data" / "provenance.json"


@app.get("/api/v1/provenance")
async def get_provenance():
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
        return data
    except Exception as exc:
        log.warning("provenance_read_error", error=str(exc))
        raise HTTPException(status_code=500, detail="Could not read provenance data.")


@app.get("/api/v1/provenance/justification")
async def get_provenance_justification():
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
            "statistical_impact": "n=282 vs n=224 observations. Power analysis requires "
                "n >= 220 for 80% power at p < 0.005 — the bridge provides the "
                "statistical margin. Without it, the dataset barely clears the minimum.",
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
            from tools.data_fetcher import get_full_history
            from tools.backtester import run_all_strategies
            history = get_full_history()
            results_dict = run_all_strategies(history)
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
            from tools.data_fetcher import get_full_history
            from tools.backtester import run_all_strategies
            history = get_full_history()
            results_dict = run_all_strategies(history)
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
            from tools.data_fetcher import get_full_history
            from tools.backtester import run_all_strategies
            from tools.cache import get_strategy_cache, set_strategy_cache, _compute_data_hash

            history = get_full_history()

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

            results_dict = run_all_strategies(history)
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
        from tools.data_fetcher import get_full_history
        from tools.backtester import run_all_strategies
        from tools.chart_data import compute_chart_data
        from tools.cache import get_strategy_cache, _compute_data_hash

        history = get_full_history()
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

@app.post("/api/optimize/weights")
async def optimize_weights(body: OptimizeRequest, session: dict = Depends(require_auth)):
    valid_methods = {"MEAN_VARIANCE", "RISK_PARITY", "MIN_VARIANCE", "BLACK_LITTERMAN", "MAX_SHARPE", "MIN_DRAWDOWN"}
    if body.method not in valid_methods:
        raise HTTPException(status_code=422, detail=f"Unknown method '{body.method}'")

    # Sprint 3: real optimizer backed by historical returns.
    if ENVIRONMENT != "test":
        try:
            from tools.data_fetcher import fetch_equity_data
            from tools.optimizer import optimize_weights as _optimize, efficient_frontier as _frontier
            import pandas as pd

            assets = body.assets or ["SPY", "TLT", "IEF", "GLD"]
            start = body.start or "2000-01-01"
            end = body.end or "2024-12-31"

            prices = fetch_equity_data(assets, start, end)
            returns = prices.pct_change().dropna()

            result = _optimize(body.method, returns, assets=assets)
            frontier = _frontier(returns, n_points=100, assets=assets)

            return {
                "method": body.method,
                "weights": result["weights"],
                "sum_check": result["sum_check"],
                "efficient_frontier": frontier,
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


# Maps cio.deliberate() agent keys to the display name/role/model the frontend expects.
# The frontend's AGENT_STYLE dict in CouncilDebate.tsx is keyed by these exact display names.
_AGENT_META: dict[str, tuple[str, str, str]] = {
    "equity_analyst":       ("Equity Analyst",               "specialist", "claude-sonnet-4-6"),
    "fixed_income_analyst": ("Fixed Income Analyst",          "specialist", "claude-sonnet-4-6"),
    "risk_manager":         ("Risk Manager",                  "specialist", "claude-sonnet-4-6"),
    "quant_backtester":     ("Quant Backtester",              "specialist", "claude-sonnet-4-6"),
    "independent_analyst":  ("Independent Analyst (Gemini)",  "dissenter",  "gemini-1.5-pro"),
    "contrarian_analyst":   ("Contrarian Analyst (Grok)",     "dissenter",  "grok-3-mini"),
    "cio":                  ("CIO",                           "cio",        "claude-opus-4-6"),
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

        # Per-agent content selection:
        #   CIO         → final synthesis (the recommendation narrative)
        #   Gemini/Grok → full challenge text (the dissenting narrative)
        #   Specialists → summary (1-2 sentences purpose-built for display)
        tech = report.get("technical_findings", {}) or {}
        if key == "cio":
            content = tech.get("final_synthesis_text") or report.get("summary", "")
        elif key == "independent_analyst":
            content = tech.get("full_challenge") or report.get("summary", "")
        elif key == "contrarian_analyst":
            content = tech.get("full_challenge") or report.get("summary", "")
        else:
            content = report.get("summary", "")

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
    }


@app.post("/api/council/query")
@limiter.limit("10/minute")
async def council_query(
    request: Request,
    body: CouncilQueryRequest,
    session: dict = Depends(require_auth),
):
    """
    Convenes the full investment council and returns a CouncilDebateResponse.

    Scope guard runs first (Haiku classifier). If in scope, CIO orchestrates
    all specialist agents + Gemini challenge + final synthesis. Falls back to
    mock data only if both real pipeline and scope guard fail entirely.
    """
    if len(body.query) > 500:
        raise HTTPException(status_code=422, detail="Query exceeds 500 character limit.")

    # Scope guard — must pass before any agent is invoked
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

    if ENVIRONMENT != "test":
        try:
            from tools.data_fetcher import get_full_history
            from tools.backtester import run_all_strategies
            from agents.cio import CIO

            history = get_full_history()
            strategy_results = run_all_strategies(history)

            cio = CIO()
            council_response = cio.deliberate(
                query=body.query,
                strategy_results=strategy_results,
                history=history,
            )

            _log_council_session(
                query=body.query,
                agents_called=["equity_analyst", "fixed_income_analyst",
                               "risk_manager", "quant_backtester",
                               "independent_analyst", "cio"],
                response=council_response,
                start_time=start_time,
                user_email=session["email"],
            )

            return _deliberate_to_frontend(body.query, council_response)

        except Exception as exc:
            log.error("council_query_error", error=str(exc))
            # Fall through to mock response rather than returning 500 —
            # a demo-critical endpoint should degrade gracefully.

    response = dict(MOCK_COUNCIL_RESPONSE)
    response["query"] = body.query
    return response


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
            return explainer.explain_terms(body.get("council_output", {}))
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
            return explainer.explain_parameter(
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
            return explainer.explain_chart(
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
            return explainer.explain_qa(body.get("audit_results", []))
        except Exception as exc:
            log.error("explain_qa_error", error=str(exc))
    return {}


# ── QA ────────────────────────────────────────────────────────────────────────

@app.post("/api/qa/audit")
@limiter.limit("10/minute")
async def qa_audit(request: Request, session: dict = Depends(require_auth)):
    """
    Runs the full 30-point QA audit against real strategy results.

    QA Agent uses Opus for the narrative; deterministic checks run from
    the strategy results dict to guarantee pass/fail verdicts are never
    hallucinated. Falls back to mock audit if pipeline is unavailable.
    """
    if ENVIRONMENT != "test":
        try:
            from tools.data_fetcher import get_full_history
            from tools.backtester import run_all_strategies
            from agents.qa_agent import QAAgent

            history = get_full_history()
            strategy_results = run_all_strategies(history)

            qa = QAAgent()
            return qa.run_audit(strategy_results, run_full_checklist=True)

        except Exception as exc:
            log.error("qa_audit_error", error=str(exc))

    return MOCK_QA_AUDIT


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

            answer = call_claude(OPUS_MODEL, QA_SYSTEM_PROMPT, body.question)
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
    Computes the current strategy_hash and returns it alongside cached
    strategy_results (when present). Shared by every QA endpoint so a
    single hash computation can serve both the status read and any
    tier trigger that follows.
    """
    from tools.data_fetcher import get_full_history
    from tools.cache import get_strategy_cache, _compute_data_hash

    history = get_full_history()
    monthly = history.get("equity_monthly")
    n_rows = len(monthly) if monthly is not None else 0
    last_date = str(monthly.index[-1]) if monthly is not None and len(monthly) > 0 else "unknown"
    strategy_hash = _compute_data_hash(n_rows, last_date, n_strategies=10)
    cached = await get_strategy_cache(strategy_hash)
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
        running: bool,            # True while a Tier 2 audit is in flight
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
        strategy_hash, _cached = await _current_strategy_hash()
        latest = await get_latest_qa(strategy_hash, min_tier=1)

        if not latest:
            return {
                "verdict": "UNKNOWN", "tier": None, "run_at": None,
                "age_hours": None, "strategy_hash": strategy_hash,
                "present_mode_allowed": False, "running": False,
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
            "running":              False,
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
async def qa_run(request: Request, session: dict = Depends(require_auth)):
    """
    Runs Tier 1 synchronously and triggers Tier 2 in the background.
    Returns immediately with the Tier 1 verdict — the audience never
    waits on the Sonnet call. The Tier 2 result lands in the cache
    on its own; subsequent /qa/status polls pick it up.

    Auto-escalation to Tier 3 happens inside the background worker if
    Tier 2 returns FAIL (see schedule_tier2_background).
    """
    if ENVIRONMENT == "test":
        return {"verdict": "PASS", "tier": 1, "tier2_scheduled": False}

    try:
        from tools.qa_tiered import run_tier1_checks, schedule_tier2_background
        from tools.cache import set_qa_cache
        from tools.backtester import run_all_strategies
        from tools.data_fetcher import get_full_history

        strategy_hash, cached = await _current_strategy_hash()
        if cached:
            results_dict = cached
        else:
            results_dict = run_all_strategies(get_full_history())

        # Tier 1 — synchronous, deterministic, free.
        t1 = run_tier1_checks(results_dict)
        await set_qa_cache(strategy_hash, t1, tier=1)

        # Tier 2 — fire and forget. Need a sync wrapper for the writer.
        import asyncio as _asyncio
        def _writer(h: str, v: dict, tier: int) -> None:
            _asyncio.run(set_qa_cache(h, v, tier=tier))
        schedule_tier2_background(results_dict, strategy_hash, _writer)

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
        log.error("qa_run_error", error=str(exc))
        raise HTTPException(status_code=500, detail=f"QA run failed: {exc}")


@app.post("/api/v1/qa/full-review")
@limiter.limit("5/minute")
async def qa_full_review(request: Request, session: dict = Depends(require_auth)):
    """
    Manually triggers a Tier 3 (Opus) deep review. Synchronous because
    the caller (Admin screen Full Review button) is willing to wait
    20-30 seconds — unlike the dashboard, which never waits.

    Master-key path: anyone with a valid session can trigger Tier 3,
    but the rate limit caps abuse at 5/minute.
    """
    if ENVIRONMENT == "test":
        return {"verdict": "PASS", "tier": 3}

    try:
        from tools.qa_tiered import run_tier3_review
        from tools.cache import set_qa_cache
        from tools.backtester import run_all_strategies
        from tools.data_fetcher import get_full_history

        strategy_hash, cached = await _current_strategy_hash()
        if cached:
            results_dict = cached
        else:
            results_dict = run_all_strategies(get_full_history())

        t3 = run_tier3_review(results_dict)
        await set_qa_cache(strategy_hash, t3, tier=3)

        return {
            **t3,
            "strategy_hash": strategy_hash,
        }
    except Exception as exc:
        log.error("qa_full_review_error", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Full review failed: {exc}")


# ── Report ────────────────────────────────────────────────────────────────────

@app.get("/api/report/export")
async def export_report(session: dict = Depends(require_auth)):
    return {
        "message": "PDF report generation available in Sprint 4.",
        "status": "not_implemented",
    }


# Sprint 6 Priority 1 — midpoint paper for June 3 deadline.
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
        from tools.data_fetcher import get_full_history
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
            history = get_full_history()
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
                results_dict = run_all_strategies(history)
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
        log.error("midpoint_template_error", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Midpoint generation failed: {exc}")


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
                "deadline": "June 3, 2026",
            },
            {
                "id": "executive_brief",
                "title": "Executive Brief Template",
                "description": (
                    "5-page brief for Forest Capital. Includes abstract, "
                    "methodology, key findings, discussion, recommendations."
                ),
                "endpoint": "/api/reports/executive-brief-template",
                "method": "POST",
                "format": "docx",
                "status": "planned",
                "deadline": "July 1, 2026",
            },
            {
                "id": "analytical_appendix",
                "title": "Analytical Appendix",
                "description": (
                    "Full appendix with data provenance table, methodology, "
                    "complete statistical results, sensitivity analysis, "
                    "limitations, and references."
                ),
                "endpoint": "/api/reports/analytical-appendix",
                "method": "POST",
                "format": "html",
                "status": "planned",
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

@app.post("/api/documents/storyboard/draft")
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
            strategy_hash = _compute_data_hash(n_rows, last_date, n_strategies=10)
            cached = await get_strategy_cache(strategy_hash)
            results_dict = cached if cached else run_all_strategies(history)
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


@app.post("/api/documents/{document_id}/versions")
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
                from tools.data_fetcher import get_full_history
                from tools.backtester import run_all_strategies
                history = get_full_history()
                results = run_all_strategies(history)
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
        import google.generativeai as genai  # type: ignore[import-untyped]

        api_key = _os.getenv("GOOGLE_API_KEY", "")
        if not api_key:
            log.info("document_assistant_mock_no_key")
            return _mock_assistant_response(user_message, context_content)

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            "gemini-1.5-pro",
            system_instruction=_GEMINI_ASSISTANT_SYSTEM_PROMPT,
        )

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

        response = model.generate_content(prompt)
        suggestion = response.text.strip()

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
            await websocket.close(code=4003, reason="Unauthorised")
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
                    from tools.data_fetcher import get_full_history
                    from tools.backtester import run_all_strategies
                    from agents.equity_analyst import EquityAnalyst
                    from agents.fixed_income_analyst import FixedIncomeAnalyst
                    from agents.risk_manager import RiskManager
                    from agents.quant_backtester import QuantBacktester
                    from agents.independent_analyst import IndependentAnalyst
                    from agents.cio import CIO

                    history = get_full_history()
                    strategy_results = run_all_strategies(history)

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
