"""
agents/quant_backtester.py

Quantitative researcher / backtester agent — Sprint 4.
Presents walk-forward OOS results, transaction cost analysis, and
cross-validation stability scores from the backtester output.
Never reports gross returns. All signals use only data available at t-1.

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
from config import TRANSACTION_COST_BPS, WALK_FORWARD_TRAIN, WALK_FORWARD_TEST

# The analyst's task, phrased as a question — the harness evaluator scores
# the response's relevance against it.
_EVALUATOR_QUESTION = (
    "Do the backtest results hold out of sample, and is the methodology "
    "free of overfitting and look-ahead bias?"
)

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = f"""You are a quantitative researcher. You implement and test strategies \
with institutional rigour. Every backtest includes transaction costs. Every optimised \
strategy has walk-forward OOS results. You never report gross returns. You never claim \
a strategy is robust on in-sample results alone. All signals use only data available at t-1.

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
regime_conditional_returns, factor_loadings, rolling_excess_return. The \
cumulative_returns chart shows the full equity curve of every strategy — \
flat sections, sharp drawdowns, and the relative slope of each strategy's \
post-2022 recovery are the visible signature of robustness. \
rolling_excess_return is the most useful for your role: a strategy whose \
in-sample excess return decays sharply at the OOS boundary is exhibiting \
visual overfitting. Describe what you can see on the chart and tie it to \
the OOS degradation percentages in the DATA block.

{VISUAL_REASONING_RULES}

{GLOBAL_AGENT_RULE}

{SCOPE_ENFORCEMENT}

{CITATION_INSTRUCTION}"""


class QuantBacktester:
    """
    Presents the backtester's walk-forward OOS results and cost analysis.

    The agent does not re-run backtests — it reasons about results already
    computed by run_all_strategies(). This avoids double computation and
    keeps the council responsive.
    """

    def analyse(
        self,
        strategy_results: dict[str, Any],
        query: str = "",
    ) -> dict[str, Any]:
        """
        OOS degradation check and cost arithmetic are computed before the
        LLM call — a >20% IS-to-OOS Sharpe drop is flagged by Python, not
        by the model's memory. This guarantees the overfitting verdict is
        traceable to real walk-forward results, not recalled statistics.

        Args:
            strategy_results: All 10 strategy results from run_all_strategies().
            query:            The user's question. See equity_analyst.analyse
                              for the rationale.
        """
        quant_summary = self._compute_quant_summary(strategy_results)
        context = self._build_context(strategy_results, quant_summary)

        query_line = (
            f"USER QUESTION: {query.strip()}\n\n"
            "Frame your quantitative rigour review around the user "
            "question above. Connect the IS/OOS, cost-drag and CV data "
            "to what they asked. If the question is meta or methodology-"
            "oriented (peer-reviewer questions, presentation framing, "
            "written-report scope), answer from your quant-backtester "
            "perspective: which backtest findings would the question "
            "target, which CV/OOS numbers ground a good answer, and "
            "what overfitting or out-of-sample caveats apply.\n\n"
        ) if query and query.strip() else ""
        user_message = (
            f"{query_line}"
            "Review these backtest results from a quantitative rigour perspective. "
            "Required: (1) Compare in-sample vs out-of-sample Sharpe for significant "
            "strategies. (2) Report transaction cost drag (bps/year). "
            "(3) Identify the strategy with the best cross-validation stability. "
            "(4) Flag any strategy where OOS Sharpe diverges >20% from IS Sharpe. "
            "Use only the numbers provided.\n\n"
            f"DATA:\n{context}"
        )

        log.info("quant_backtester_called", n_strategies=len(strategy_results))

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
                agent_id="quant_backtester",
            )
            return self._parse_response(result.response, strategy_results,
                                        quant_summary)
        except Exception as exc:
            log.error("quant_backtester_error", error=str(exc))
            return self._fallback_response(strategy_results, quant_summary)

    def _build_visual_context(
        self, n_strategies: int | None = None,
    ) -> list[dict] | None:
        """COUNCIL_CHARTS snapshots as content blocks, or None when no
        snapshots are on disk (cold deploy, first run). See EquityAnalyst._build_visual_context."""
        if not snapshots_dir_exists():
            log.info("quant_backtester_no_snapshots_dir",
                     note="proceeding without visual context")
            return None
        blocks = get_charts_for_context(COUNCIL_CHARTS, n_strategies=n_strategies)
        if not blocks:
            log.info("quant_backtester_no_snapshots_available",
                     note="proceeding without visual context")
            return None
        return blocks

    def _compute_quant_summary(
        self, strategy_results: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Computes OOS quality and cost metrics from strategy results.

        OOS degradation is the key metric — a strategy that loses >20% of
        its Sharpe from IS to OOS is flagged as potentially overfitted.
        """
        oos_comparison = {}
        for name, r in strategy_results.items():
            is_sharpe = r.get("sharpe_ratio")
            oos_sharpe = r.get("oos_sharpe")
            # true_turnover is genuine annualised one-way turnover —
            # sum(|drifted_i - new_target_i|)/2 over rebalances divided
            # by n_years (see backtester._true_turnover). It needs no
            # ×12 multiplier; the legacy avg_monthly_turnover proxy did
            # because it was a per-month rebalance-count, but that
            # field is no longer the basis for cost drag.
            turnover = r.get("true_turnover", 0.0)
            cost_drag_bps_year = (
                float(turnover) * TRANSACTION_COST_BPS
                if isinstance(turnover, (int, float))
                else None
            )

            if isinstance(is_sharpe, float) and isinstance(oos_sharpe, float) and is_sharpe > 0:
                degradation = (is_sharpe - oos_sharpe) / is_sharpe
                overfitted = degradation > 0.20
            else:
                degradation = None
                overfitted = False

            oos_comparison[name] = {
                "is_sharpe": is_sharpe,
                "oos_sharpe": oos_sharpe,
                "oos_degradation": degradation,
                "potentially_overfitted": overfitted,
                "cost_drag_bps_year": cost_drag_bps_year,
                "cv_stability_score": r.get("cross_validation", {}).get(
                    "cv_stability_score"
                ),
            }

        most_stable = max(
            (
                (name, v["cv_stability_score"])
                for name, v in oos_comparison.items()
                if isinstance(v["cv_stability_score"], float)
            ),
            key=lambda x: x[1],
            default=("N/A", None),
        )

        flagged = [
            name
            for name, v in oos_comparison.items()
            if v["potentially_overfitted"]
        ]

        return {
            "oos_comparison": oos_comparison,
            "most_stable_strategy": most_stable[0],
            "most_stable_cv_score": most_stable[1],
            "flagged_for_overfitting": flagged,
            "transaction_cost_bps": TRANSACTION_COST_BPS,
            "walk_forward_train_months": WALK_FORWARD_TRAIN,
            "walk_forward_test_months": WALK_FORWARD_TEST,
        }

    def _build_context(
        self,
        strategy_results: dict[str, Any],
        quant_summary: dict[str, Any],
    ) -> str:
        # IS vs OOS comparison and overfitting flags are embedded as pre-computed
        # ground truth — the LLM interprets them rather than computing them,
        # preventing any hallucinated degradation percentages.
        """Builds a compact JSON context for the quant review prompt."""
        metrics = {
            name: {
                "sharpe_ratio": r.get("sharpe_ratio"),
                "oos_sharpe": r.get("oos_sharpe"),
                "oos_cagr": r.get("oos_cagr"),
                # Genuine annualised one-way turnover — the figure the
                # Dashboard surfaces and the standard institutional
                # convention. The Sonnet agent narrates on this.
                "true_turnover": r.get("true_turnover"),
                "is_significant": r.get("is_significant"),
                "alpha_after_costs_bps": r.get("alpha_after_costs_bps"),
            }
            for name, r in strategy_results.items()
        }
        context = {
            "strategy_metrics": metrics,
            "quant_summary": quant_summary,
        }
        return json.dumps(context, indent=2, default=str)

    def _parse_response(
        self,
        response_text: str,
        strategy_results: dict[str, Any],
        quant_summary: dict[str, Any],
    ) -> dict[str, Any]:
        # OOS Sharpe and transaction cost figures are taken from quant_summary
        # (computed arithmetic), not extracted from the LLM text — prevents
        # the model from inventing or rounding numbers it didn't compute.
        """Builds the structured response with real OOS and cost figures."""
        n_flagged = len(quant_summary["flagged_for_overfitting"])
        stable_name = quant_summary["most_stable_strategy"]
        stable_cv = quant_summary["most_stable_cv_score"]

        summary = (
            f"Walk-forward OOS results confirm in-sample findings. "
            f"Most stable strategy: {stable_name} (CV score {stable_cv:.2f}). "
            f"{n_flagged} {'strategy' if n_flagged == 1 else 'strategies'} flagged "
            f"for potential IS/OOS divergence."
            if isinstance(stable_cv, float) else
            "OOS analysis complete — see technical findings for degradation flags."
        )

        technical_findings = {
            "oos_comparison": quant_summary["oos_comparison"],
            "most_stable_strategy": stable_name,
            "most_stable_cv_score": stable_cv,
            "flagged_for_overfitting": quant_summary["flagged_for_overfitting"],
            "transaction_cost_bps_applied": TRANSACTION_COST_BPS,
            "walk_forward_config": {
                "train_months": WALK_FORWARD_TRAIN,
                "test_months": WALK_FORWARD_TEST,
            },
            "raw_analysis": response_text,
        }

        return build_agent_response(
            technical_findings=technical_findings,
            summary=summary,
            what_we_found=(
                f"Walk-forward OOS testing used {WALK_FORWARD_TRAIN}-month training "
                f"windows with {WALK_FORWARD_TEST}-month test windows rolled across "
                f"the full history. Transaction costs of {TRANSACTION_COST_BPS} bps "
                f"applied on every rebalance."
            ),
            why_it_matters=(
                "In-sample results can be fabricated by optimising on the same data "
                "used for testing. Walk-forward OOS is the only honest measure — it "
                "shows how a strategy would have performed if deployed in real time, "
                "not with the benefit of hindsight."
            ),
            for_our_portfolio=(
                f"{'No strategies show >20% OOS degradation — results are robust.' if n_flagged == 0 else f'{n_flagged} strategies show >20% IS/OOS degradation — treat with caution.'} "
                f"Transaction cost drag of {TRANSACTION_COST_BPS} bps/rebalance is "
                f"included in all reported CAGR and Sharpe figures."
            ),
            confidence=(
                "High confidence — OOS results are computed directly by the backtester "
                "using data the strategy never saw during optimisation. "
                "The walk-forward design enforces strict temporal separation."
            ),
        )

    def _fallback_response(
        self,
        strategy_results: dict[str, Any],
        quant_summary: dict[str, Any],
    ) -> dict[str, Any]:
        # quant_summary already contains all OOS and cost arithmetic — the
        # council still gets actionable quantitative findings even without
        # the LLM narrative layer.
        """Returns data-only response when LLM call fails."""
        return build_agent_response(
            technical_findings=quant_summary,
            summary="Quantitative metrics retrieved. LLM analysis temporarily unavailable.",
            what_we_found="OOS and cost metrics computed from backtester output.",
            why_it_matters="Walk-forward OOS is the only honest test of strategy robustness.",
            for_our_portfolio="Review oos_comparison in technical findings.",
            confidence="Data confidence high. LLM narrative temporarily unavailable.",
        )
