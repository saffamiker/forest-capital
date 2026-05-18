"""
tools/audit_layer2.py — Layer 2 of the statistical audit: independent
metric recomputation.

The raw data and the formula specifications are sent to the auditor
model (claude-opus-4-7) — an entirely separate model from the
claude-sonnet-4-6 the platform computes with. The auditor recomputes
every metric from scratch and compares its value with the platform's.

Five task groups, run in parallel (one Opus call each, plus one per
summary-statistics entry):
  A — summary statistics (CAGR, volatility, Sharpe, max drawdown,
      skewness, excess return, information ratio), per asset
  B — Carhart factor loadings, all strategies in one call
  C — the efficient-frontier max-Sharpe point
  D — the pre/post-2022 regime split
  E — the rolling pre/post-2022 correlation averages

FAIL-OPEN: a task group whose response will not parse is recorded as a
WARNING, never a CRITICAL — a flaky auditor never manufactures a false
critical failure. In the test environment, or with no API key, Layer 2
skips cleanly.
"""
from __future__ import annotations

import json
import os
from typing import Any

import structlog

from tools.audit_common import layer_status, make_finding

log = structlog.get_logger(__name__)

_AUDITOR_MAX_TOKENS = 4000

_AUDITOR_SYSTEM = (
    "You are an independent quantitative auditor verifying portfolio "
    "analytics calculations. You recompute every metric from the raw "
    "data provided, using ONLY the formula specifications given — you "
    "never trust or reuse the platform's intermediate calculations. "
    "Show your working, then compare your value with the platform's. "
    "Flag a metric PASS when your value is within 0.01% of the "
    "platform's, WARNING for a 0.01%-0.1% discrepancy, and FAIL for a "
    "discrepancy above 0.1% or a directionally wrong result. "
    "Return ONLY a valid JSON object — no markdown, no prose outside it."
)


def _is_test_env() -> bool:
    return os.getenv("ENVIRONMENT", "").lower() == "test"


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pulls the first balanced JSON object out of a model response.
    Returns None when nothing parses — the caller treats that as a
    WARNING, never a CRITICAL."""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _call_auditor(user_message: str) -> str:
    """One Opus auditor call. Synchronous — the caller fans groups out
    with asyncio.to_thread."""
    from agents.base import OPUS_MODEL, call_claude
    return call_claude(OPUS_MODEL, _AUDITOR_SYSTEM, user_message,
                        max_tokens=_AUDITOR_MAX_TOKENS)


def _checks_to_findings(
    group: str, parsed: dict[str, Any], hash_: str | None,
    formula: str,
) -> list[dict[str, Any]]:
    """Converts an auditor JSON response's `checks` array into findings."""
    findings: list[dict[str, Any]] = []
    strategy = parsed.get("strategy") or parsed.get("entity")
    for chk in parsed.get("checks", []) or []:
        if not isinstance(chk, dict):
            continue
        raw_status = str(chk.get("status", "warning")).lower()
        status = raw_status if raw_status in ("pass", "fail", "warning") \
            else "warning"
        severity = ("critical" if status == "fail"
                    else "warning" if status == "warning" else "info")
        findings.append(make_finding(
            2, f"Layer 2 — {group}", str(chk.get("metric", group)),
            status, severity,
            strategy=str(strategy) if strategy else None,
            platform_value=chk.get("platform_value"),
            auditor_value=chk.get("auditor_value"),
            discrepancy=(str(chk.get("flag")) if chk.get("flag")
                         else (f"{chk.get('discrepancy_pct')}%"
                               if chk.get("discrepancy_pct") is not None
                               else None)),
            formula_used=formula,
            raw_inputs_hash=hash_,
            auditor_reasoning=str(chk.get("reasoning", "")),
        ))
    return findings


def _parse_failed_finding(
    group: str, hash_: str | None, detail: str,
) -> dict[str, Any]:
    """The fail-open finding for a task group that errored or would not
    parse — a WARNING, never a CRITICAL."""
    return make_finding(
        2, f"Layer 2 — {group}", group, "warning", "warning",
        raw_inputs_hash=hash_,
        auditor_reasoning=f"The auditor response for this task group could "
                          f"not be processed ({detail}). Recorded as a "
                          "warning — a parse failure is never treated as a "
                          "critical discrepancy.")


# ── Task-group prompt builders ────────────────────────────────────────────────

def _summary_prompt(asset: str, payload: dict, platform: dict) -> str:
    raw = payload["raw_data"]["asset_returns"]
    specs = payload["formula_specifications"]
    rf_annual = payload["metadata"]["risk_free_rate"]["value"]
    return (
        f"Independently verify the summary statistics for {asset}.\n\n"
        f"Raw monthly returns: {raw.get('equity') if asset == 'EQUITY' else '(see series below)'}\n"
        f"Series to audit ({asset}): {payload['raw_data']['asset_returns'].get(asset.lower(), raw.get('equity'))}\n"
        f"Dates: {raw.get('dates')}\n"
        f"Monthly risk-free series: {raw.get('rf')}\n"
        f"Annualised risk-free rate: {rf_annual}\n"
        f"Benchmark monthly returns (equity): {raw.get('equity')}\n\n"
        f"Platform-computed values for {asset}: {json.dumps(platform)}\n\n"
        "Formula specifications:\n"
        f"- cagr: {specs['cagr']}\n"
        f"- volatility: {specs['volatility']}\n"
        f"- sharpe: {specs['sharpe']}\n"
        f"- max_drawdown: {specs['max_drawdown']}\n"
        f"- skewness: {specs['skewness']}\n"
        f"- excess_return: {specs['excess_return']}\n"
        f"- information_ratio: {specs['information_ratio']}\n\n"
        "Recompute each metric, then return ONLY this JSON:\n"
        '{"strategy": "' + asset + '", "checks": [{"metric": "cagr", '
        '"platform_value": <num>, "auditor_value": <num>, '
        '"status": "pass|warning|fail", "discrepancy_pct": <num>, '
        '"reasoning": "<step by step>", "flag": "<description if not pass>"}]}'
    )


def _factor_prompt(payload: dict) -> str:
    specs = payload["formula_specifications"]
    return (
        "Independently verify the Carhart four-factor loadings for every "
        "strategy.\n\n"
        f"Strategy monthly returns: {json.dumps(payload['raw_data']['strategy_returns'])}\n"
        f"Fama-French factors (percent): {json.dumps(payload['raw_data']['ff_factors'])}\n"
        f"Monthly risk-free series: {payload['raw_data']['asset_returns'].get('rf')}\n\n"
        f"Platform factor loadings: {json.dumps(payload['platform_computed']['factor_loadings'])}\n\n"
        f"Formula: {specs['factor_regression']}\n\n"
        "Run the OLS regression for each strategy; compare betas, alpha, "
        "R-squared and the significance flags. Return ONLY JSON: "
        '{"strategy": "factor_loadings", "checks": [{"metric": '
        '"<strategy>.<coef>", "platform_value": <num>, "auditor_value": '
        '<num>, "status": "pass|warning|fail", "discrepancy_pct": <num>, '
        '"reasoning": "...", "flag": "..."}]}'
    )


def _frontier_prompt(payload: dict) -> str:
    specs = payload["formula_specifications"]
    raw = payload["raw_data"]["asset_returns"]
    return (
        "Independently verify the efficient-frontier max-Sharpe point.\n\n"
        f"Equity monthly returns: {raw.get('equity')}\n"
        f"IG monthly returns: {raw.get('ig')}\n"
        f"HY monthly returns: {raw.get('hy')}\n"
        f"Annualised risk-free rate: {payload['metadata']['risk_free_rate']['value']}\n\n"
        f"Platform max-Sharpe point: {json.dumps(payload['platform_computed']['efficient_frontier'])}\n\n"
        f"Formula: {specs['efficient_frontier']}\n\n"
        "Find the tangency (max-Sharpe) portfolio and compare sigma, mu "
        "and the weights. Return ONLY JSON: "
        '{"strategy": "efficient_frontier", "checks": [{"metric": '
        '"max_sharpe.sigma", "platform_value": <num>, "auditor_value": '
        '<num>, "status": "pass|warning|fail", "discrepancy_pct": <num>, '
        '"reasoning": "...", "flag": "..."}]}'
    )


def _regime_prompt(payload: dict) -> str:
    specs = payload["formula_specifications"]
    return (
        "Independently verify the pre/post-2022 regime split.\n\n"
        f"Strategy monthly returns: {json.dumps(payload['raw_data']['strategy_returns'])}\n"
        f"Dates: {payload['raw_data']['asset_returns'].get('dates')}\n"
        f"Monthly risk-free series: {payload['raw_data']['asset_returns'].get('rf')}\n\n"
        f"Platform regime-conditional values: {json.dumps(payload['platform_computed']['regime_conditional'])}\n\n"
        f"Split rule: {specs['regime_split']}\n"
        f"Sharpe formula: {specs['sharpe']}\nCAGR formula: {specs['cagr']}\n\n"
        "Compute pre- and post-2022 Sharpe and CAGR for each strategy and "
        "compare. Return ONLY JSON: "
        '{"strategy": "regime_split", "checks": [{"metric": '
        '"<strategy>.post_2022_sharpe", "platform_value": <num>, '
        '"auditor_value": <num>, "status": "pass|warning|fail", '
        '"discrepancy_pct": <num>, "reasoning": "...", "flag": "..."}]}'
    )


def _rolling_prompt(payload: dict) -> str:
    specs = payload["formula_specifications"]
    raw = payload["raw_data"]["asset_returns"]
    return (
        "Independently verify the rolling pre/post-2022 correlation "
        "averages.\n\n"
        f"Equity monthly returns: {raw.get('equity')}\n"
        f"IG monthly returns: {raw.get('ig')}\n"
        f"HY monthly returns: {raw.get('hy')}\n"
        f"Dates: {raw.get('dates')}\n\n"
        f"Platform rolling correlation: {json.dumps(payload['platform_computed']['rolling_correlation'])}\n\n"
        f"Formula: {specs['rolling_correlation']}\n\n"
        "Compute the 12-month rolling correlation of equity-vs-IG and "
        "equity-vs-HY, then the pre- and post-2022 averages, and compare. "
        "Return ONLY JSON: "
        '{"strategy": "rolling_metrics", "checks": [{"metric": '
        '"equity_ig.post_2022", "platform_value": <num>, "auditor_value": '
        '<num>, "status": "pass|warning|fail", "discrepancy_pct": <num>, '
        '"reasoning": "...", "flag": "..."}]}'
    )


# ── Orchestration ─────────────────────────────────────────────────────────────

def _run_group(group: str, prompt: str, hash_: str | None,
               formula: str) -> list[dict[str, Any]]:
    """Runs one task group end to end — call, parse, convert. Fail-open."""
    try:
        raw = _call_auditor(prompt)
        parsed = _extract_json(raw)
        if parsed is None:
            return [_parse_failed_finding(group, hash_, "no JSON in response")]
        return _checks_to_findings(group, parsed, hash_, formula)
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_layer2_group_failed", group=group, error=str(exc))
        return [_parse_failed_finding(group, hash_, str(exc))]


async def layer_2_metric_audit(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Runs the five task groups against the auditor model, in parallel.
    Returns {"status": pass|fail|skip, "findings": [...]}. The test
    environment / no API key skips the layer cleanly.
    """
    if not payload.get("available"):
        return {"status": "skip", "findings": []}
    if _is_test_env() or not os.getenv("ANTHROPIC_API_KEY"):
        return {"status": "skip", "findings": [make_finding(
            2, "Layer 2 — recomputation", "layer_2", "warning", "info",
            auditor_reasoning="Independent recomputation skipped — the "
                              "auditor model is not available in this "
                              "environment.")]}

    import asyncio

    h = payload.get("raw_inputs_hash")
    specs = payload["formula_specifications"]
    summary = payload["platform_computed"]["summary_statistics"]

    # Task group A — one call per summary-statistics entry.
    jobs: list[tuple[str, str, str]] = [
        (f"summary statistics ({asset})",
         _summary_prompt(asset, payload, vals),
         specs["sharpe"])
        for asset, vals in summary.items()
    ]
    # Task groups B-E — one call each.
    jobs.append(("factor loadings", _factor_prompt(payload),
                 specs["factor_regression"]))
    jobs.append(("efficient frontier", _frontier_prompt(payload),
                 specs["efficient_frontier"]))
    jobs.append(("regime split", _regime_prompt(payload),
                 specs["regime_split"]))
    jobs.append(("rolling metrics", _rolling_prompt(payload),
                 specs["rolling_correlation"]))

    results = await asyncio.gather(*[
        asyncio.to_thread(_run_group, name, prompt, h, formula)
        for name, prompt, formula in jobs
    ])
    findings = [f for group in results for f in group]
    return {"status": layer_status(findings), "findings": findings}
