"""
tools/academic_export.py

Shared data-gathering and narrative layer behind the three generated
academic deliverables — the midpoint paper, the executive brief, and the
final presentation deck (the .docx/.pptx builders live in
tools/academic_docx.py and tools/academic_deck.py).

Two responsibilities:

  gather_document_data()  — pulls every figure the documents cite from
    data already in PostgreSQL (market_data_monthly,
    strategy_results_cache, ff_factors_monthly), the Team Activity tables,
    and the academic_documents table. Light reads only — never
    get_full_history() or run_all_strategies(). On a cold cache or in the
    test environment it returns available=False and the builders fall
    back to [DATA PENDING] markers rather than failing the document.

  harness_narrative()  — runs one Academic Writer generation through the
    generator-evaluator harness with the academic_review peer-evaluator
    criteria (the spec mandates the harness for every academic_writer
    call). Fail-open: any error — including the test environment, where
    no API key is configured — returns a [DATA PENDING] marker, so one
    failed section never sinks the whole document.

Every generated document is a FIRST DRAFT for Bob to refine. The
[DATA PENDING] marker and the AI DRAFT banner make that explicit.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import structlog

from config import ENVIRONMENT

log = structlog.get_logger(__name__)

# Inserted wherever a section's source data could not be loaded. A grep
# for this string across a generated document tells Bob exactly what he
# still has to supply by hand.
DATA_PENDING = "[DATA PENDING]"


async def gather_document_data() -> dict[str, Any]:
    """
    Assembles the full data bundle the document builders consume.

    Never raises — every failure mode degrades to available=False with
    empty collections, so a caller can build a structurally complete
    document carrying [DATA PENDING] markers in place of live figures.
    """
    bundle: dict[str, Any] = {
        "available": False,
        "study_period": {"start": "—", "end": "—", "n_months": 0},
        "summary_statistics": [],
        "regime_conditional": [],
        "drawdown_comparison": [],
        "factor_loadings": [],
        "cumulative_returns": {"strategies": [], "points": []},
        "rolling_correlation": {},
        "strategy_results": {},
        "strategy_metadata": {},
        "risk_free_rate": None,
        "team_summary": {},
        "last_review_text": None,
        "academic_docs": [],
    }

    # The test environment has no warmed caches and no API key — return
    # the empty bundle so the builders exercise their [DATA PENDING] path.
    if ENVIRONMENT == "test":
        return bundle

    # ── Analytics bundle — the same light reads /api/v1/analytics/academic
    #    uses; no get_full_history(), no run_all_strategies(). ──────────────
    try:
        import pandas as pd

        from tools.cache import (
            get_ff_factors, get_latest_strategy_cache, get_monthly_returns,
        )
        from tools import analytics as an

        monthly = await get_monthly_returns()
        strategies = await get_latest_strategy_cache()
        ff = await get_ff_factors()

        if monthly and strategies:
            idx = pd.to_datetime(monthly["dates"])
            equity = pd.Series(monthly["equity"], index=idx)
            ig = pd.Series(monthly["ig"], index=idx)
            hy = pd.Series(monthly["hy"], index=idx)
            rf = pd.Series(monthly["rf"], index=idx)

            benchmark = strategies.get("BENCHMARK", {})
            bench_series = an._pairs_to_series(benchmark.get("monthly_returns") or [])
            asset_series: dict[str, Any] = {"EQUITY": equity, "IG": ig, "HY": hy}
            if not bench_series.empty:
                asset_series["BENCHMARK"] = bench_series

            try:
                from strategy_metadata import STRATEGY_METADATA
            except Exception:  # noqa: BLE001
                STRATEGY_METADATA = {}

            rf_list = monthly.get("rf") or []
            bundle.update({
                "available": True,
                "study_period": {
                    "start": str(idx[0].date()),
                    "end": str(idx[-1].date()),
                    "n_months": len(idx),
                },
                "summary_statistics": an.summary_statistics(asset_series, rf),
                "regime_conditional": an.regime_conditional_performance(strategies, rf),
                "drawdown_comparison": an.drawdown_comparison(strategies),
                "factor_loadings": an.factor_loadings(strategies, ff or []),
                "cumulative_returns": an.cumulative_returns(strategies),
                "rolling_correlation": an.rolling_correlation(equity, ig, hy, window=12),
                "strategy_results": strategies,
                "strategy_metadata": STRATEGY_METADATA,
                "risk_free_rate": (
                    round(sum(rf_list) / len(rf_list) * 12, 4) if rf_list else None
                ),
            })
    except Exception as exc:  # noqa: BLE001
        log.warning("academic_export_analytics_failed", error=str(exc))

    # ── Team Activity — per-member counts behind the Roles section ─────────
    try:
        from tools.activity_log import get_activity_summary
        bundle["team_summary"] = await get_activity_summary(analytical_only=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("academic_export_team_summary_failed", error=str(exc))

    # ── Last Academic Review verdict — seeds the Next Steps section ────────
    try:
        bundle["last_review_text"] = await _last_academic_review_verdict()
    except Exception as exc:  # noqa: BLE001
        log.warning("academic_export_review_read_failed", error=str(exc))

    # ── Uploaded requirements / rubric documents ───────────────────────────
    try:
        from tools.academic_context import _read_all_with_content
        bundle["academic_docs"] = await _read_all_with_content()
    except Exception as exc:  # noqa: BLE001
        log.warning("academic_export_docs_read_failed", error=str(exc))

    return bundle


async def _last_academic_review_verdict() -> str | None:
    """
    The full text of the most recent Academic Review arbiter verdict, or
    None when no review has been run. Stored in agent_interactions by the
    /api/council/academic-review endpoint as response_summary.
    """
    try:
        from sqlalchemy import text

        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT response_summary FROM agent_interactions "
                "WHERE interaction_type = 'academic_review' "
                "ORDER BY timestamp DESC LIMIT 1"
            ))
            found = row.fetchone()
            return found[0] if found and found[0] else None
    except Exception as exc:  # noqa: BLE001
        log.warning("last_academic_review_query_failed", error=str(exc))
        return None


def academic_doc_present(academic_docs: list[dict], document_type: str) -> bool:
    """True when a document of the given type has been uploaded in Settings."""
    return any(d.get("document_type") == document_type for d in academic_docs)


def harness_narrative(
    agent_id: str,
    task: str,
    context: Any,
    *,
    max_tokens: int = 900,
) -> str:
    """
    Generates one section of academic prose through the Academic Writer
    agent, wrapped in the generator-evaluator harness.

    The harness scores the draft against the academic_review peer-evaluator
    criteria and retries below threshold — the spec requires every
    academic_writer call to run through it. Synchronous (the harness and
    call_claude are both synchronous); callers run it in asyncio.to_thread.

    Fail-open: in the test environment, or on any generation error, a
    [DATA PENDING] marker is returned so the surrounding document still
    assembles.
    """
    if ENVIRONMENT == "test":
        return (
            f"{DATA_PENDING} — section narrative is generated at runtime "
            "and is skipped in the test environment."
        )

    ctx_str = context if isinstance(context, str) else json.dumps(
        context, indent=2, default=str)
    user_message = f"{task}\n\nDATA (cite only these figures):\n{ctx_str}"

    try:
        from agents.academic_writer import _SYSTEM_PROMPT
        from agents.base import SONNET_MODEL, call_claude
        from agents.evaluator_prompts import academic_review_peer_evaluator_prompt
        from agents.harness import GeneratorEvaluatorHarness

        harness = GeneratorEvaluatorHarness()
        result = harness.run(
            generator_fn=lambda prompt: call_claude(
                SONNET_MODEL, _SYSTEM_PROMPT, prompt, max_tokens=max_tokens),
            evaluator_prompt=academic_review_peer_evaluator_prompt("academic writer"),
            generator_prompt=user_message,
            context=ctx_str,
            agent_id=agent_id,
        )
        return _strip_banner(result.response) or (
            f"{DATA_PENDING} — narrative generation returned no content."
        )
    except Exception as exc:  # noqa: BLE001
        ref = uuid.uuid4().hex[:8]
        log.warning("academic_narrative_failed",
                    agent_id=agent_id, ref=ref, error=str(exc))
        return f"{DATA_PENDING} — narrative generation unavailable (ref: {ref})."


def _strip_banner(text: str) -> str:
    """
    Drops a leading 'AI DRAFT — REQUIRES HUMAN REVIEW' line if the model
    emitted one. The .docx/.pptx builders add the banner themselves (on
    every page / slide), so an inline copy would only be a duplicate.
    """
    out = (text or "").strip()
    lines = out.split("\n")
    while lines and ("AI DRAFT" in lines[0].upper() or not lines[0].strip()):
        lines.pop(0)
    return "\n".join(lines).strip()


# ── Table adapters ────────────────────────────────────────────────────────────
#
# Convert the analytics-layer dicts into a (headers, rows-of-strings) pair.
# Both the .docx builders and the .pptx deck embed the same four tables, so
# the formatting lives here once. Every cell is a display string — the
# builders only lay them out.


def _pct(v: Any) -> str:
    """Decimal fraction → percentage string, or an em dash when absent."""
    return f"{v * 100:.2f}%" if isinstance(v, (int, float)) else "—"


def _num(v: Any, places: int = 3) -> str:
    """Number → fixed-decimal string, or an em dash when absent."""
    return f"{v:.{places}f}" if isinstance(v, (int, float)) else "—"


def table_summary_statistics(stats: list[dict]) -> tuple[list[str], list[list[str]]]:
    """Asset-level summary statistics — the headline figures table."""
    headers = ["Asset", "CAGR", "Volatility", "Sharpe", "Max DD", "Skew"]
    rows = [
        [
            str(r.get("asset", "—")),
            _pct(r.get("cagr")),
            _pct(r.get("ann_volatility")),
            _num(r.get("sharpe_ratio")),
            _pct(r.get("max_drawdown")),
            _num(r.get("skewness"), 2),
        ]
        for r in stats
    ]
    return headers, rows


def table_regime_conditional(rows_in: list[dict]) -> tuple[list[str], list[list[str]]]:
    """Per-strategy Sharpe and CAGR split at the 2022 regime break."""
    headers = ["Strategy", "Pre-2022 Sharpe", "Post-2022 Sharpe",
               "Pre-2022 CAGR", "Post-2022 CAGR"]
    rows = [
        [
            str(r.get("strategy", "—")),
            _num(r.get("pre_2022_sharpe")),
            _num(r.get("post_2022_sharpe")),
            _pct(r.get("pre_2022_cagr")),
            _pct(r.get("post_2022_cagr")),
        ]
        for r in rows_in
    ]
    return headers, rows


def table_factor_loadings(rows_in: list[dict]) -> tuple[list[str], list[list[str]]]:
    """Carhart four-factor betas, annualised alpha and R² per strategy."""
    headers = ["Strategy", "Alpha (ann.)", "MKT-RF", "SMB", "HML", "MOM", "R²"]
    rows = []
    for r in rows_in:
        # A trailing '*' marks a coefficient significant at p < 0.05.
        def _star(value: Any, sig_key: str) -> str:
            s = _num(value)
            return s + ("*" if r.get(sig_key) else "") if s != "—" else "—"
        rows.append([
            str(r.get("strategy", "—")),
            _star(r.get("alpha_annualized"), "alpha_significant"),
            _star(r.get("mkt_rf"), "mkt_rf_significant"),
            _star(r.get("smb"), "smb_significant"),
            _star(r.get("hml"), "hml_significant"),
            _star(r.get("mom"), "mom_significant"),
            _num(r.get("r_squared")),
        ])
    return headers, rows


def table_drawdown(rows_in: list[dict]) -> tuple[list[str], list[list[str]]]:
    """Max drawdown and recovery period per strategy, deepest loss first."""
    headers = ["Strategy", "Max Drawdown", "Recovery (months)"]
    rows = [
        [
            str(r.get("strategy", "—")),
            _pct(r.get("max_drawdown")),
            (str(r["recovery_months"]) if r.get("recovery_months") is not None
             else "not recovered"),
        ]
        for r in rows_in
    ]
    return headers, rows
