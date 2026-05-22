"""tools/diversification_context.py — agent prompt injection block.

Item 8 commit 6 (agent context injection). When council / academic
review / explainer agents reason about portfolios, they should see
the same diversification context the analytics page surfaces — so
their narratives reference real numbers (lowest correlation pair,
highest tail risk, capture leader, 2022 crisis performance) rather
than memorised generalities.

PATTERN — mirror of tools/macro_context.py:
  get_diversification_context()      sync accessor returning the
                                     formatted block (or empty when
                                     cache is cold)
  inject_diversification_context(p)  appends to a system prompt; no-op
                                     when empty so every agent calls
                                     it unconditionally
  refresh_diversification_context()  async; reads the latest
                                     diversification metric rows and
                                     rebuilds the block

CACHE — one module-level dict, mirroring macro_context._CACHE. The
strategy_cache write hook (the same hook that fires the metric
refreshes) also triggers a refresh of this block so the agents see
fresh numbers within one tick of a fresh ingestion.

FAIL-OPEN END TO END. A read miss or a malformed row leaves the
previous block in place; an empty block is a no-op injection that
doesn't break any agent prompt.
"""
from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger(__name__)


_CACHE: dict[str, str] = {"text": ""}


def _fmt_pct(x: float | None, decimals: int = 1) -> str:
    if x is None:
        return "N/A"
    return f"{x:.{decimals}f}%"


def _build_block(
    correlation: dict[str, Any] | None,
    tail_risk: dict[str, Any] | None,
    capture: dict[str, Any] | None,
    crisis: dict[str, Any] | None,
    risk_contribution: dict[str, Any] | None,
) -> str:
    """Renders the five context lines from the cached metric payloads.
    Returns empty string when no metric data is available."""
    parts: list[str] = []

    # 1. Correlation context — lowest pair (full period), highest
    # pair, and the pre/post-2022 average shift.
    if correlation:
        labels = correlation.get("labels") or []
        full = correlation.get("full") or []
        pre = correlation.get("pre_2022") or []
        post = correlation.get("post_2022") or []
        if labels and full and len(full) == len(labels):
            # Find lowest non-self correlation and highest non-self
            # correlation in the full matrix.
            lowest = (None, None, 999.0)
            highest = (None, None, -999.0)
            for i, a in enumerate(labels):
                for j, b in enumerate(labels):
                    if j <= i:
                        continue
                    r = full[i][j]
                    if r is None:
                        continue
                    if r < lowest[2]:
                        lowest = (a, b, r)
                    if r > highest[2]:
                        highest = (a, b, r)
            line = ("CORRELATION CONTEXT: ")
            if lowest[0]:
                line += (
                    f"Lowest correlation pair (full period): "
                    f"{lowest[0]} / {lowest[1]} at r={lowest[2]:.2f}. ")
            if highest[0]:
                line += (
                    f"Highest correlation pair: "
                    f"{highest[0]} / {highest[1]} at r={highest[2]:.2f}.")
            # Pre/post-2022 average pairwise correlation shift.
            if pre and post and len(pre) == len(labels) and len(post) == len(labels):
                def _avg(m: list[list[Any]]) -> float | None:
                    vals: list[float] = []
                    for i in range(len(labels)):
                        for j in range(i + 1, len(labels)):
                            if m[i][j] is not None:
                                vals.append(float(m[i][j]))
                    return sum(vals) / len(vals) if vals else None
                pre_avg = _avg(pre)
                post_avg = _avg(post)
                if pre_avg is not None and post_avg is not None:
                    line += (
                        f" Post-2022 correlation shift: average "
                        f"pairwise correlation moved from "
                        f"{pre_avg:.2f} to {post_avg:.2f}.")
            parts.append(line)

    # 2. Tail risk context — highest + lowest CVaR 99% strategy.
    if tail_risk:
        rows = tail_risk.get("strategies") or []
        if rows:
            # Sorted by cvar_99_annual ascending → rows[0] is the
            # most-negative = highest tail risk.
            worst = rows[0]
            best = rows[-1]
            parts.append(
                f"TAIL RISK CONTEXT: Highest tail risk strategy: "
                f"{worst['strategy']} CVaR 99% = "
                f"{_fmt_pct(worst.get('cvar_99_annual', 0) * 100, 1)} "
                f"annual. Lowest tail risk: {best['strategy']} CVaR 99% "
                f"= {_fmt_pct(best.get('cvar_99_annual', 0) * 100, 1)} "
                f"annual.")

    # 3. Capture ratio context — best score + strategies with low
    # downside capture.
    if capture:
        rows = capture.get("strategies") or []
        if rows:
            # Already sorted by full capture_score desc.
            top = rows[0]
            top_score = top.get("full", {}).get("capture_score")
            line = ("CAPTURE RATIO CONTEXT: ")
            if top_score is not None:
                line += (
                    f"Best capture ratio score (up/down): "
                    f"{top['strategy']} at {top_score:.2f}. ")
            defensive = [
                r["strategy"] for r in rows
                if (r.get("full", {}).get("down_capture") or 100) < 80
            ]
            if defensive:
                line += (
                    f"Strategies with down capture below 80%: "
                    f"{', '.join(defensive[:5])}.")
            parts.append(line)

    # 4. Crisis context — best + worst performer in 2022 rate shock.
    if crisis:
        rows = crisis.get("rows") or {}
        rs = "Rate_Shock_2022"
        with_2022 = [
            (name, data.get(rs, {}))
            for name, data in rows.items()
            if data.get(rs, {}).get("cagr") is not None
        ]
        if with_2022:
            with_2022.sort(key=lambda kv: kv[1]["cagr"], reverse=True)
            best_name, best_data = with_2022[0]
            worst_name, worst_data = with_2022[-1]
            parts.append(
                f"CRISIS CONTEXT: Best performer in 2022 rate shock: "
                f"{best_name} at {_fmt_pct(best_data['cagr'] * 100, 1)} "
                f"return. Worst: {worst_name} at "
                f"{_fmt_pct(worst_data['cagr'] * 100, 1)}.")

    # 5. Risk contribution context — highest contributor in equal-weight.
    if risk_contribution:
        labels = risk_contribution.get("labels") or []
        pcts = risk_contribution.get("pct_risk_contribution_equal") or []
        if labels and pcts and len(labels) == len(pcts):
            paired = sorted(
                zip(labels, pcts, strict=True),
                key=lambda kv: kv[1] or 0,
                reverse=True,
            )
            top_name, top_pct = paired[0]
            parts.append(
                f"RISK CONTRIBUTION CONTEXT: In equal-weight portfolio, "
                f"highest risk contributor: {top_name} at "
                f"{top_pct:.1f}% of total portfolio risk.")

    if not parts:
        return ""

    return "\n\n=== DIVERSIFICATION CONTEXT (pre-computed from current data) ===\n" \
        + "\n".join(parts) \
        + "\n\nReason from these specific figures when relevant. Do NOT " \
        + "invent diversification numbers absent from this block — fall " \
        + "back to historical reasoning when a specific figure is not " \
        + "captured here."


def get_diversification_context() -> str:
    return _CACHE["text"]


def inject_diversification_context(system_prompt: str) -> str:
    """Appends the diversification context to a system prompt. No-op
    when cache is empty (cold deploy, or no completed strategy
    cache yet)."""
    ctx = get_diversification_context()
    return system_prompt + ctx if ctx else system_prompt


async def refresh_diversification_context() -> None:
    """Reads the five latest metric rows from analytics_metrics_cache
    and rebuilds the cached block. Called from
    precomputed_analytics.refresh_diversification_metrics after the
    metric writes complete, so the agent context lags the metric
    refresh by one tick — which is fine, the metrics are written
    before the context refresh fires.

    Fail-open: any database error leaves the previous block in
    place, never raising into an agent call site."""
    try:
        from tools.precomputed_analytics import get_latest_metric
        corr = await get_latest_metric("correlation_matrices")
        tail = await get_latest_metric("tail_risk")
        cap = await get_latest_metric("capture_ratios")
        crisis = await get_latest_metric("crisis_performance")
        rc = await get_latest_metric("marginal_contribution_to_risk")
        text = _build_block(corr, tail, cap, crisis, rc)
        if text:
            _CACHE["text"] = text
            log.info("diversification_context_refreshed", length=len(text))
    except Exception as exc:  # noqa: BLE001
        log.warning("diversification_context_refresh_failed",
                    error=str(exc))
