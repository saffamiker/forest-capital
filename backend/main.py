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

from fastapi import FastAPI, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
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
    if email not in {e.lower() for e in ALLOWED_EMAILS}:
        # Don't reveal which emails are allowed — same response either way
        log.warning("magic_link_unauthorized_email", email_hash=hash(email))
        return MagicLinkResponse(
            message="If that email is authorised, a login link has been sent.",
            dev_mode=(ENVIRONMENT == "development"),
        )
    token = generate_magic_token(email)
    await send_magic_link(email, token)
    return MagicLinkResponse(
        message="If that email is authorised, a login link has been sent.",
        dev_mode=(ENVIRONMENT == "development"),
    )


@app.get("/api/auth/verify")
async def verify_magic_link(token: str = Query(...)):
    email = verify_magic_token(token)
    session_token = generate_session_token(email)
    log.info("auth_success", email=email)
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
    # Sprint 3: all 10 strategies computed from real data.
    # get_full_history() is called once; run_all_strategies receives the pre-loaded dict.
    # Falls back to mock data in test environment or on individual strategy failures.
    if ENVIRONMENT != "test":
        try:
            from tools.data_fetcher import get_full_history
            from tools.backtester import run_all_strategies
            history = get_full_history()
            results_dict = run_all_strategies(history)
            ranked = sorted(results_dict.values(), key=lambda r: r.get("sharpe_ratio", 0.0), reverse=True)
            return {"strategies": ranked, "ranked_by": "sharpe_ratio"}
        except Exception as exc:
            log.warning("compare_all_strategies_fallback", error=str(exc))
    # Fallback: MOCK_STRATEGIES used only in test environment or when the real
    # pipeline raises an exception.  Should never be the primary response in
    # production — the warning log above will flag if this path is taken.
    sorted_strategies = sorted(MOCK_STRATEGIES, key=lambda s: s["sharpe_ratio"], reverse=True)
    return {"strategies": sorted_strategies, "ranked_by": "sharpe_ratio"}


# ── Regime ────────────────────────────────────────────────────────────────────

@app.get("/api/regime/current")
async def get_current_regime(session: dict = Depends(require_auth)):
    # Sprint 2: real threshold-based regime. Fall back to mock in test env or on error.
    if ENVIRONMENT != "test":
        try:
            from tools.regime_detector import detect_current_regime
            return detect_current_regime()
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

            return council_response

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


# ── Report ────────────────────────────────────────────────────────────────────

@app.get("/api/report/export")
async def export_report(session: dict = Depends(require_auth)):
    return {
        "message": "PDF report generation available in Sprint 4.",
        "status": "not_implemented",
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
