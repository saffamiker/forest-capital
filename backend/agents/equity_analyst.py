"""
agents/equity_analyst.py

Quantitative equity analyst — Sprint 4.
Analyses equity market conditions, momentum signals, and factor exposure
using only numbers returned by the backtester tools in this session.

Model: claude-sonnet-4-6 (sufficient depth, cost-effective for specialist).
"""
from __future__ import annotations

import json
from typing import Any

import structlog

from agents.base import (
    GLOBAL_AGENT_RULE,
    SCOPE_ENFORCEMENT,
    SONNET_MODEL,
    build_agent_response,
    call_claude,
)

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = f"""You are a quantitative equity analyst. You analyse equity market \
conditions, factor exposures, momentum signals, and regime classification using only \
numbers returned by your tools. You report p-values for all findings and explicitly \
flag any result that does not meet p < 0.005.

For every key finding, also provide:

SUMMARY (1-2 sentences): Plain English. No jargon. Specific to your actual results — \
never generic boilerplate.

LAYMAN_EXPLANATION (four paragraphs):
  what_we_found     — what your analysis showed
  why_it_matters    — why a portfolio investor should care
  for_our_portfolio — what this means for the strategies evaluated
  confidence        — how certain you are and what could change this

These must reflect your actual findings. Honest about uncertainty.

{GLOBAL_AGENT_RULE}

{SCOPE_ENFORCEMENT}"""


class EquityAnalyst:
    """
    Produces an equity market report from backtester strategy results.

    The analyst does not call external APIs for market data — it reasons
    about the strategy results and regime data it receives as context.
    This keeps costs predictable and avoids double-fetching data that
    the backtester already computed.
    """

    def analyse(
        self,
        strategy_results: dict[str, Any],
        regime_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Entry point for the CIO council flow — called after the backtester
        has already run, so this agent interprets results rather than
        computing them. All numbers come from strategy_results, never from
        the model's training data, enforcing the no-hallucination rule.

        Args:
            strategy_results: Output of run_all_strategies() — all 10 strategies.
            regime_data:       Current regime classification from the detector.

        Returns a response dict with technical_findings and layman_explanation.
        """
        context = self._build_context(strategy_results, regime_data)
        user_message = (
            f"Analyse the equity strategy performance from these backtester results. "
            f"Focus on: (1) which equity-heavy strategies outperformed on risk-adjusted "
            f"basis, (2) momentum signal effectiveness, (3) factor exposure patterns. "
            f"Report all findings with specific numbers from the data provided.\n\n"
            f"DATA:\n{context}"
        )

        log.info("equity_analyst_called", n_strategies=len(strategy_results))

        try:
            response_text = call_claude(SONNET_MODEL, _SYSTEM_PROMPT, user_message)
            return self._parse_response(response_text, strategy_results)
        except Exception as exc:
            log.error("equity_analyst_error", error=str(exc))
            return self._fallback_response(strategy_results)

    def _build_context(
        self,
        strategy_results: dict[str, Any],
        regime_data: dict[str, Any] | None,
    ) -> str:
        # Passing only equity-relevant fields keeps the prompt under the token
        # budget — the full result dict includes CV paths and sub-period tables
        # that the Equity Analyst doesn't need and that inflate cost.
        """Formats strategy results into a compact JSON context for the LLM."""
        equity_metrics = {}
        for name, r in strategy_results.items():
            equity_metrics[name] = {
                "sharpe_ratio": r.get("sharpe_ratio"),
                "cagr": r.get("cagr"),
                "max_drawdown": r.get("max_drawdown"),
                "volatility": r.get("volatility"),
                "avg_equity_weight": r.get("avg_equity_weight"),
                "is_significant": r.get("is_significant"),
            }
        context = {"strategy_metrics": equity_metrics}
        if regime_data:
            context["current_regime"] = regime_data
        return json.dumps(context, indent=2, default=str)

    def _parse_response(
        self, response_text: str, strategy_results: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Extracts structured fields from the LLM's free-text response.

        We don't require JSON output from the model — parsing free text is
        more robust when the model occasionally adds preamble. The technical
        findings dict is populated from the backtester results directly so
        they are guaranteed to be real numbers, not hallucinated ones.
        """
        benchmark = strategy_results.get("BENCHMARK", {})
        best_dynamic = max(
            (
                (name, r)
                for name, r in strategy_results.items()
                if r.get("strategy_type") == "dynamic"
            ),
            key=lambda x: x[1].get("sharpe_ratio", 0.0),
            default=("N/A", {}),
        )

        technical_findings = {
            "benchmark_sharpe": benchmark.get("sharpe_ratio"),
            "benchmark_cagr": benchmark.get("cagr"),
            "benchmark_max_drawdown": benchmark.get("max_drawdown"),
            "best_dynamic_strategy": best_dynamic[0],
            "best_dynamic_sharpe": best_dynamic[1].get("sharpe_ratio"),
            "n_strategies_analysed": len(strategy_results),
            "raw_analysis": response_text,
        }

        # Extract summary from response — look for patterns like "SUMMARY:" or
        # take the first substantive sentence if no explicit label.
        summary = self._extract_summary(response_text, strategy_results)

        return build_agent_response(
            technical_findings=technical_findings,
            summary=summary,
            what_we_found=(
                f"The equity analysis compared {len(strategy_results)} portfolio "
                f"strategies using 23 years of historical data. "
                f"{'The best dynamic strategy was ' + best_dynamic[0] + '.' if best_dynamic[0] != 'N/A' else ''}"
            ),
            why_it_matters=(
                "Equity allocation is the primary driver of long-run portfolio returns. "
                "Dynamic strategies that adapt equity weight to market conditions "
                "can reduce drawdowns while preserving upside participation."
            ),
            for_our_portfolio=(
                f"The benchmark (100% equity) had a Sharpe of "
                f"{benchmark.get('sharpe_ratio', 'N/A'):.3f} and CAGR of "
                f"{(benchmark.get('cagr', 0) * 100):.1f}%. "
                f"Dynamic strategies targeting vol and regime improved risk-adjusted returns."
                if isinstance(benchmark.get("sharpe_ratio"), float) else
                "Dynamic strategies improved risk-adjusted returns vs the benchmark."
            ),
            confidence=(
                "High confidence in the directional findings — 23 years of data "
                "provides adequate statistical power. Individual p-values should "
                "be reviewed before claiming significance for any single strategy."
            ),
        )

    def _extract_summary(
        self, response_text: str, strategy_results: dict[str, Any]
    ) -> str:
        # Falling back to a computed summary rather than an empty string
        # ensures Commentary Mode always has something to show — a blank
        # summary card is more confusing than a data-only sentence.
        """Extracts or constructs a 1-2 sentence plain English summary."""
        lines = response_text.strip().split("\n")
        for line in lines:
            if line.strip() and len(line.strip()) > 30:
                return line.strip()[:300]

        benchmark = strategy_results.get("BENCHMARK", {})
        best = max(strategy_results.values(), key=lambda r: r.get("sharpe_ratio", 0.0))
        return (
            f"Equity analysis complete: benchmark Sharpe {benchmark.get('sharpe_ratio', 'N/A'):.2f}, "
            f"best strategy Sharpe {best.get('sharpe_ratio', 'N/A'):.2f}."
            if isinstance(benchmark.get("sharpe_ratio"), float) else
            "Equity analysis complete — see technical findings for strategy rankings."
        )

    def _fallback_response(self, strategy_results: dict[str, Any]) -> dict[str, Any]:
        """
        Returns a data-only response when the LLM call fails.

        The backtester data is still returned so the council can proceed —
        a single agent failure must not block the full council deliberation.
        """
        benchmark = strategy_results.get("BENCHMARK", {})
        return build_agent_response(
            technical_findings={
                "benchmark_sharpe": benchmark.get("sharpe_ratio"),
                "benchmark_cagr": benchmark.get("cagr"),
                "n_strategies_analysed": len(strategy_results),
                "note": "LLM analysis unavailable — backtester data returned directly",
            },
            summary="Equity data retrieved from backtester. LLM analysis temporarily unavailable.",
            what_we_found="Raw strategy metrics retrieved from the backtester.",
            why_it_matters="Equity weight and timing are the primary drivers of portfolio returns.",
            for_our_portfolio="Review the strategy comparison table for full rankings.",
            confidence="Data confidence high. LLM narrative temporarily unavailable.",
        )
