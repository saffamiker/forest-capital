"""
tools/audit_layer2.py — Layer 2 of the statistical audit: independent
metric recomputation.

The raw data and the formula specifications are sent to the auditor
model (claude-opus-4-7) — an entirely separate model from the
claude-sonnet-4-6 the platform computes with. The auditor recomputes
every metric from scratch and compares its value with the platform's.

Task groups run in parallel (one Opus call each, plus one per
summary-statistics entry):
  A — summary statistics (CAGR, volatility, Sharpe, max drawdown,
      skewness, excess return, information ratio), per asset
  B — Carhart factor loadings, split across two calls of five
      strategies each (one call for all ten truncates past parsing,
      same failure mode as regime split)
  C — the efficient-frontier max-Sharpe point
  D — the pre/post-2022 regime split, split across two calls of five
      strategies each (one call for all ten truncates past parsing)
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

# May 28 2026 — raised from 4000 to 8000. The factor-loadings group was
# truncating past parsing even after chunking; regime was running close
# to the cap when the auditor emitted full reasoning per check. 8000
# gives every chunked group ~4× the worst-case payload size and never
# exceeds the model's per-call output budget.
_AUDITOR_MAX_TOKENS = 8000

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
    """Pulls the audit task group's JSON response out of the model
    output. Returns None when nothing parses — the caller treats that
    as a WARNING, never a CRITICAL.

    Two-pass extraction (May 28 2026):

      1. text.find('{') to text.rfind('}') — handles the common case
         where the response is JSON wrapped in plain prose or markdown
         fences with no stray braces inside the prose.

      2. Regex retry anchored on the known JSON-shape '{"strategy"'.
         Handles the case where the auditor emits a stray '{' or '}'
         in prose before or after the actual JSON, which mis-extracts
         pass 1. The anchor on the auditor's literal opening token
         scopes the candidate slice to the real JSON object even when
         surrounding text contains braces.
    """
    if not text:
        return None
    # Pass 1 — outermost brace span.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            if isinstance(obj, dict):
                return obj
        except Exception:  # noqa: BLE001
            pass
    # Pass 2 — strategy-anchored regex retry. The auditor's response
    # always begins '{"strategy"...'; anchoring the regex on that
    # token strips any leading prose containing a stray '{'. The
    # regex is greedy backwards to the LAST '}' so a trailing prose
    # block still works when pass 1 picked up an inner '}'.
    import re

    m = re.search(r'\{\s*"strategy"[\s\S]*\}', text)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except Exception:  # noqa: BLE001
            pass
    return None


def _call_auditor(user_message: str) -> str:
    """One Opus auditor call. Synchronous — the caller fans groups out
    with asyncio.to_thread.

    trigger="statistical_audit_layer2" so the llm_call log line names
    the most expensive call in the codebase (30-50K tokens per call ×
    five task groups per audit run) for direct cost attribution.
    hash_gate=True because audit_engine.run_full_audit() runs an
    is_audit_current() check BEFORE firing the auditor — every
    auditor call has already passed the gate when it reaches here.
    """
    from agents.base import OPUS_MODEL, call_claude
    return call_claude(OPUS_MODEL, _AUDITOR_SYSTEM, user_message,
                        max_tokens=_AUDITOR_MAX_TOKENS,
                        trigger="statistical_audit_layer2",
                        hash_gate=True)


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


def _factor_prompt(
    payload: dict, subset_names: list[str] | None = None,
) -> str:
    """Builds the Carhart factor-loadings prompt for a SUBSET of
    strategies. May 28 2026 — chunking mirror of _regime_prompt. The
    previous "all ten strategies in one call" path overshot the
    auditor's output cap and the response truncated past the final
    '}' — _extract_json then reported "no JSON in response" and the
    whole group degraded to a single WARN finding. The orchestrator
    now splits this into two parallel calls of five strategies each,
    same pattern as the regime split.

    subset_names=None (the legacy signature) preserves backwards
    compatibility — every strategy in the payload is included. Tests
    rely on the legacy signature when they want to inspect the
    full-payload behaviour.
    """
    specs = payload["formula_specifications"]
    all_returns = payload["raw_data"]["strategy_returns"]
    all_loadings = payload["platform_computed"]["factor_loadings"]
    if subset_names is None:
        subset_names = list(all_returns.keys())
    subset_returns = {k: v for k, v in all_returns.items()
                      if k in subset_names}
    subset_loadings = {k: v for k, v in all_loadings.items()
                       if k in subset_names}
    return (
        "Independently verify the Carhart four-factor loadings for these "
        f"strategies: {', '.join(subset_names)}.\n\n"
        f"Strategy monthly returns: {json.dumps(subset_returns)}\n"
        f"Fama-French factors (percent): {json.dumps(payload['raw_data']['ff_factors'])}\n"
        f"Monthly risk-free series: {payload['raw_data']['asset_returns'].get('rf')}\n\n"
        f"Platform factor loadings: {json.dumps(subset_loadings)}\n\n"
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


def _regime_prompt(payload: dict, subset_names: list[str]) -> str:
    """Builds a regime-split prompt for a SUBSET of strategies. The
    regime split is audited in two calls of five strategies each — one
    call for all ten overshoots the response token cap and the JSON is
    truncated past parsing."""
    specs = payload["formula_specifications"]
    all_returns = payload["raw_data"]["strategy_returns"]
    all_regime = payload["platform_computed"]["regime_conditional"]
    subset_returns = {k: v for k, v in all_returns.items()
                      if k in subset_names}
    subset_regime = {k: v for k, v in all_regime.items()
                     if k in subset_names}
    return (
        "Independently verify the pre/post-2022 regime split for these "
        f"strategies: {', '.join(subset_names)}.\n\n"
        f"Strategy monthly returns: {json.dumps(subset_returns)}\n"
        f"Dates: {payload['raw_data']['asset_returns'].get('dates')}\n"
        f"Monthly risk-free series: {payload['raw_data']['asset_returns'].get('rf')}\n\n"
        f"Platform regime-conditional values: {json.dumps(subset_regime)}\n\n"
        f"Split rule: {specs['regime_split']}\n"
        f"Sharpe formula: {specs['sharpe']}\nCAGR formula: {specs['cagr']}\n\n"
        "Compute pre- and post-2022 Sharpe and CAGR for each strategy and "
        "compare with the platform values.\n\n"
        "Return ONLY a raw JSON object. No markdown. No code blocks. No "
        "preamble. No explanation outside the JSON structure.\n\n"
        "Be concise — one sentence of reasoning per strategy maximum.\n\n"
        "A single check object looks exactly like this:\n"
        '{\n'
        '  "metric": "REGIME_SWITCHING.post_2022_sharpe",\n'
        '  "platform_value": 0.63,\n'
        '  "auditor_value": 0.63,\n'
        '  "status": "pass",\n'
        '  "discrepancy_pct": 0.0,\n'
        '  "reasoning": "One concise sentence.",\n'
        '  "flag": ""\n'
        '}\n\n'
        "The full object to return:\n"
        '{"strategy": "regime_split", "checks": [ <one check object per '
        'strategy for post_2022_sharpe> ]}\n'
        "`status` is one of pass, warning, fail."
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
    """Runs one task group end to end — call, parse, convert. Fail-open.

    May 28 2026 — emits a DEBUG-level audit_layer2_raw_response log
    line BEFORE _extract_json runs. Production stays at INFO so the
    line is silent, but a sysadmin chasing a parse failure can set
    LOG_LEVEL=DEBUG (or grep raw bytes) to see exactly what the
    auditor returned. The line is structured (group + char count +
    raw text) for clean filtering.
    """
    try:
        raw = _call_auditor(prompt)
        log.debug("audit_layer2_raw_response",
                  group=group, response_chars=len(raw or ""), raw=raw)
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
    # Task groups B-E — chunked where the payload exceeds the cap.
    #
    # Factor loadings (May 28 2026) — was previously a single call for
    # all 10 strategies; under the auditor's output cap the response
    # truncated past parsing and the whole group degraded to a single
    # WARN finding. Split into two parallel calls of 5 strategies each
    # mirroring the regime-split chunking below — _run_group flattens
    # each job's findings, so the two calls' findings concatenate
    # transparently into one set tagged "factor loadings (A)" /
    # "factor loadings (B)".
    factor_names = list(payload["raw_data"]["strategy_returns"].keys())
    half_f = (len(factor_names) + 1) // 2
    jobs.append(("factor loadings (A)",
                 _factor_prompt(payload, factor_names[:half_f]),
                 specs["factor_regression"]))
    jobs.append(("factor loadings (B)",
                 _factor_prompt(payload, factor_names[half_f:]),
                 specs["factor_regression"]))
    jobs.append(("efficient frontier", _frontier_prompt(payload),
                 specs["efficient_frontier"]))
    # The regime split runs as two parallel calls of five strategies
    # each — one call for all ten overshoots the token cap and the JSON
    # truncates past parsing. _run_group flattens each job's findings,
    # so the two calls' findings concatenate transparently.
    regime_names = list(payload["raw_data"]["strategy_returns"].keys())
    half = (len(regime_names) + 1) // 2
    jobs.append(("regime split (A)",
                 _regime_prompt(payload, regime_names[:half]),
                 specs["regime_split"]))
    jobs.append(("regime split (B)",
                 _regime_prompt(payload, regime_names[half:]),
                 specs["regime_split"]))
    jobs.append(("rolling metrics", _rolling_prompt(payload),
                 specs["rolling_correlation"]))

    results = await asyncio.gather(*[
        asyncio.to_thread(_run_group, name, prompt, h, formula)
        for name, prompt, formula in jobs
    ])
    findings = [f for group in results for f in group]
    return {"status": layer_status(findings), "findings": findings}
