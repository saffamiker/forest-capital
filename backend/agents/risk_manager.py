"""
agents/risk_manager.py

Risk manager and statistical guardian — Sprint 4.
Enforces FDR correction, runs stress test comparisons, and flags any
strategy that fails on any single statistical dimension.
The council's last line of defence against overfitting and false positives.

Model: claude-sonnet-4-6.
"""
from __future__ import annotations

import json
from typing import Any

import structlog

from agents.base import (
    CITATION_INSTRUCTION,
    GLOBAL_AGENT_RULE,
    SCOPE_ENFORCEMENT,
    SONNET_MODEL,
    STRUCTURE_INSTRUCTION,
    VISUAL_REASONING_RULES,
    WEB_SEARCH_TOOL,
    build_agent_response,
    call_claude,
)
from agents.harness import GeneratorEvaluatorHarness
from agents.evaluator_prompts import council_evaluator_prompt
from tools.chart_vision import (
    COUNCIL_CHARTS, get_charts_for_context, snapshots_dir_exists,
)
from config import P_THRESHOLD_PRIMARY, FDR_Q_VALUE, STRESS_SCENARIOS

# The analyst's task, phrased as a question — the harness evaluator scores
# the response's relevance against it.
_EVALUATOR_QUESTION = (
    "What are the tail risks, drawdowns, and stress-test outcomes across "
    "the strategies, and are the statistical results sound?"
)

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = f"""You are the portfolio risk manager and statistical guardian. \
You enforce FDR correction on all p-values, run Hansen's SPA test on all strategy \
comparisons, and flag any strategy that fails on any single dimension. You are the \
council's last line of defence against overfitting.

For every key finding, also provide:

SUMMARY (1-2 sentences): Plain English. No jargon. Specific to your actual results.

LAYMAN_EXPLANATION (four paragraphs):
  what_we_found     — what your analysis showed
  why_it_matters    — why a portfolio investor should care
  for_our_portfolio — what this means for the strategies evaluated
  confidence        — how certain you are and what could change this

DEPTH REQUIREMENT — produce a detailed, thorough analysis of 300-500 words. Do not \
summarise and do not defer with "see above" or "see the CIO for synthesis": the CIO \
synthesises the council — your job is to provide complete domain expertise, not a \
brief verdict. Your analysis must contain domain-specific analysis of the question \
asked, quantitative references to the actual strategy metrics in the data provided, \
and a clear position with its supporting reasoning.

VISUAL CONTEXT — you may receive chart snapshots alongside the prompt: \
rolling_correlation, cumulative_returns, regime_signals, \
regime_conditional_returns, factor_loadings, rolling_excess_return. \
cumulative_returns shows the depth and persistence of drawdowns visually — \
use it to substantiate which strategies were on the wrong side of the \
2008 / 2020 / 2022 sell-offs. regime_signals indicates which regime each \
period was classified into; cross-check tail outcomes against the regime \
strip when discussing stress-test results. factor_loadings provides visual \
evidence for factor exposures that may flag overfitting (e.g. a strategy \
loading heavily on a single factor inflates idiosyncratic risk).

{VISUAL_REASONING_RULES}

{STRUCTURE_INSTRUCTION}

{GLOBAL_AGENT_RULE}

{SCOPE_ENFORCEMENT}

{CITATION_INSTRUCTION}"""


class RiskManager:
    """
    Produces a risk assessment from strategy results and statistical test outputs.

    Computes stress test comparisons directly from the results dict rather
    than re-running backtests — the backtester already has the stress period
    data and this avoids duplicate computation.
    """

    def analyse(
        self,
        strategy_results: dict[str, Any],
        statistical_results: dict[str, Any] | None = None,
        query: str = "",
    ) -> dict[str, Any]:
        """
        Risk arithmetic (drawdown comparisons, significance tallies, FDR
        pass/fail counts) runs before the LLM to prevent hallucination of
        p-values or drawdown figures. The model then interprets pre-computed
        findings — it cannot invent numbers the backtester didn't produce.

        Args:
            strategy_results:   All 10 strategy results from the backtester.
            statistical_results: Optional extended stat test outputs (DSR, PSR, SPA).
            query:              The user's question. See equity_analyst.analyse
                                for the rationale.
        """
        risk_summary = self._compute_risk_summary(strategy_results)
        context = self._build_context(strategy_results, risk_summary, statistical_results)

        query_line = (
            f"USER QUESTION: {query.strip()}\n\n"
            "Frame your risk and statistical-integrity review around the "
            "user question above. Connect the risk and significance data "
            "to what they asked. If the question is meta or methodology-"
            "oriented (peer-reviewer questions, presentation framing, "
            "written-report scope), answer from your risk-manager "
            "perspective: which risk findings would the question target, "
            "which statistical numbers ground a good answer, and what "
            "tail-risk or significance caveats apply.\n\n"
        ) if query and query.strip() else ""
        user_message = (
            f"{query_line}"
            "Perform a risk assessment and statistical integrity review of these "
            "strategy results. Required: (1) Identify strategies that fail any "
            f"Tier 1 gate (p < {P_THRESHOLD_PRIMARY}). "
            "(2) Flag any implausibly high Sharpe or suspiciously low drawdown. "
            "(3) Assess tail risk from the max drawdown data. "
            "(4) Confirm whether multiple comparison correction (FDR) was applied. "
            "Use only the numbers provided.\n\n"
            f"DATA:\n{context}"
        )

        log.info("risk_manager_called", n_strategies=len(strategy_results))

        # COUNCIL_CHARTS snapshots — built once, captured in the generator
        # closure. Evaluators MUST NOT see them (harness._evaluate omits
        # the kwarg).
        visual_context = self._build_visual_context(len(strategy_results))

        try:
            # Routed through the generator-evaluator harness — see
            # equity_analyst for the rationale.
            harness = GeneratorEvaluatorHarness()
            result = harness.run(
                generator_fn=lambda p: call_claude(
                    SONNET_MODEL, _SYSTEM_PROMPT, p, max_tokens=1500,
                    tools=[WEB_SEARCH_TOOL],
                    visual_context=visual_context),
                evaluator_prompt=council_evaluator_prompt(_EVALUATOR_QUESTION),
                generator_prompt=user_message,
                context=str(context)[:4000],
                agent_id="risk_manager",
            )
            return self._parse_response(result.response, strategy_results,
                                        risk_summary)
        except Exception as exc:
            log.error("risk_manager_error", error=str(exc))
            return self._fallback_response(strategy_results, risk_summary)

    def _build_visual_context(
        self, n_strategies: int | None = None,
    ) -> list[dict] | None:
        """COUNCIL_CHARTS snapshots as content blocks, or None when no
        snapshots are on disk (cold deploy, first run). See EquityAnalyst._build_visual_context."""
        if not snapshots_dir_exists():
            log.info("risk_manager_no_snapshots_dir",
                     note="proceeding without visual context")
            return None
        blocks = get_charts_for_context(COUNCIL_CHARTS, n_strategies=n_strategies)
        if not blocks:
            log.info("risk_manager_no_snapshots_available",
                     note="proceeding without visual context")
            return None
        return blocks

    def _compute_risk_summary(self, strategy_results: dict[str, Any]) -> dict[str, Any]:
        """
        Computes aggregate risk metrics from the strategy results dict.

        Doing the arithmetic here rather than in the LLM guarantees the
        numbers are correct — max drawdown comparisons, pass/fail counts,
        and significance tallies are deterministic from the backtester output.
        """
        significant = [
            name for name, r in strategy_results.items()
            if r.get("is_significant", False)
        ]
        worst_drawdown = min(
            strategy_results.values(),
            key=lambda r: r.get("max_drawdown", 0.0),
        )
        best_sharpe = max(
            strategy_results.values(),
            key=lambda r: r.get("sharpe_ratio", 0.0),
        )

        benchmark = strategy_results.get("BENCHMARK", {})
        strategies_beating_benchmark = [
            name for name, r in strategy_results.items()
            if name != "BENCHMARK"
            and r.get("sharpe_ratio", 0.0) > benchmark.get("sharpe_ratio", 0.0)
        ]

        stress_2022 = {
            name: r.get("stress_results", {}).get("RATE_HIKE_2022", {})
            for name, r in strategy_results.items()
            if r.get("stress_results")
        }

        return {
            "n_significant": len(significant),
            "significant_strategies": significant,
            "worst_drawdown_strategy": min(
                strategy_results.keys(),
                key=lambda k: strategy_results[k].get("max_drawdown", 0.0),
            ),
            "worst_drawdown_value": min(
                r.get("max_drawdown", 0.0) for r in strategy_results.values()
            ),
            "best_sharpe_strategy": max(
                strategy_results.keys(),
                key=lambda k: strategy_results[k].get("sharpe_ratio", 0.0),
            ),
            "best_sharpe_value": max(
                r.get("sharpe_ratio", 0.0) for r in strategy_results.values()
            ),
            "n_beating_benchmark": len(strategies_beating_benchmark),
            "strategies_beating_benchmark": strategies_beating_benchmark,
            "stress_2022_available": bool(stress_2022),
        }

    def _build_context(
        self,
        strategy_results: dict[str, Any],
        risk_summary: dict[str, Any],
        statistical_results: dict[str, Any] | None,
    ) -> str:
        # risk_summary is embedded directly — the LLM sees arithmetic results
        # (n_significant, worst_drawdown, etc.) as ground truth, not as values
        # it should recompute or remember from training.
        """Builds a compact JSON context for the risk analysis prompt."""
        metrics = {}
        for name, r in strategy_results.items():
            metrics[name] = {
                "sharpe_ratio": r.get("sharpe_ratio"),
                "max_drawdown": r.get("max_drawdown"),
                "volatility": r.get("volatility"),
                "cagr": r.get("cagr"),
                "is_significant": r.get("is_significant"),
                "p_value_ttest": r.get("p_value_ttest"),
                "p_value_corrected": r.get("p_value_corrected"),
                "dsr_p_value": r.get("dsr_p_value"),
                "oos_p_value": r.get("oos_p_value"),
                "cv_stability_score": r.get("cross_validation", {}).get("cv_stability_score"),
            }

        context = {
            "strategy_metrics": metrics,
            "risk_summary": risk_summary,
            "tier1_threshold": P_THRESHOLD_PRIMARY,
            "fdr_q_value": FDR_Q_VALUE,
            "stress_scenarios": list(STRESS_SCENARIOS.keys()),
        }
        if statistical_results:
            context["extended_stats"] = statistical_results

        return json.dumps(context, indent=2, default=str)

    def _parse_response(
        self,
        response_text: str,
        strategy_results: dict[str, Any],
        risk_summary: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Builds the structured response with real risk metrics.

        The is_significant flags come from the backtester, not the LLM —
        this ensures the risk verdict reflects actual statistical test results.
        """
        n_sig = risk_summary["n_significant"]
        sig_names = risk_summary["significant_strategies"]

        summary = (
            f"{n_sig} of {len(strategy_results)} strategies pass all Tier 1 gates "
            f"(p < {P_THRESHOLD_PRIMARY} across full-period, FDR, DSR, OOS, CV). "
            f"{'Recommended: ' + ', '.join(sig_names[:3]) + '.' if sig_names else 'No strategies pass all gates.'}"
        )

        technical_findings = {
            "n_strategies_significant": n_sig,
            "significant_strategies": sig_names,
            "n_strategies_beating_benchmark": risk_summary["n_beating_benchmark"],
            "worst_drawdown": {
                "strategy": risk_summary["worst_drawdown_strategy"],
                "value": risk_summary["worst_drawdown_value"],
            },
            "best_sharpe": {
                "strategy": risk_summary["best_sharpe_strategy"],
                "value": risk_summary["best_sharpe_value"],
            },
            "fdr_threshold_applied": FDR_Q_VALUE,
            "tier1_threshold": P_THRESHOLD_PRIMARY,
            "raw_analysis": response_text,
        }

        return build_agent_response(
            technical_findings=technical_findings,
            summary=summary,
            what_we_found=(
                f"Of 10 strategies tested, {n_sig} passed all five Tier 1 statistical gates. "
                f"The Benjamin et al. (2018) p < 0.005 threshold was applied with "
                f"Benjamini-Hochberg FDR correction across all strategies."
            ),
            why_it_matters=(
                "Multiple comparison correction is not a technicality — it prevents "
                "false positives that would lead Forest Capital to invest in a strategy "
                "that only looked good by chance. With 10 strategies tested, the expected "
                "number of false positives at p < 0.05 is 0.5; at p < 0.005 it is 0.05."
            ),
            for_our_portfolio=(
                f"Only strategies with p < {P_THRESHOLD_PRIMARY} after FDR correction are "
                f"recommended. The {n_sig} significant strategies are: "
                f"{', '.join(sig_names) if sig_names else 'none'}."
            ),
            confidence=(
                "High confidence in pass/fail verdicts — they are computed directly "
                "from the statistical test outputs. The exact p-values are in "
                "technical_findings. Sub-period results are Tier 2 (p < 0.05) and "
                "inform narrative only, not the significance verdict."
            ),
        )

    def _fallback_response(
        self,
        strategy_results: dict[str, Any],
        risk_summary: dict[str, Any],
    ) -> dict[str, Any]:
        # Build technical_findings that mirrors _parse_response's schema.
        # risk_summary uses "n_significant" internally; tests and the council
        # expect "n_strategies_significant" — map it explicitly here so the
        # fallback path is indistinguishable from the live path schema-wise.
        """Returns data-only response when LLM call fails."""
        technical_findings = {
            "n_strategies_significant": risk_summary["n_significant"],
            "significant_strategies": risk_summary["significant_strategies"],
            "n_strategies_beating_benchmark": risk_summary["n_beating_benchmark"],
            "worst_drawdown": {
                "strategy": risk_summary["worst_drawdown_strategy"],
                "value": risk_summary["worst_drawdown_value"],
            },
            "best_sharpe": {
                "strategy": risk_summary["best_sharpe_strategy"],
                "value": risk_summary["best_sharpe_value"],
            },
        }
        return build_agent_response(
            technical_findings=technical_findings,
            summary=(
                f"{risk_summary['n_significant']} strategies pass all Tier 1 gates. "
                "LLM analysis temporarily unavailable."
            ),
            what_we_found="Risk metrics retrieved from backtester output.",
            why_it_matters="Statistical integrity is required before any strategy recommendation.",
            for_our_portfolio="Review significant_strategies in technical findings.",
            confidence="Data confidence high. LLM narrative temporarily unavailable.",
        )
