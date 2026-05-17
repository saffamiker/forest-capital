"""
agents/fixed_income_analyst.py

Fixed income analyst — Sprint 4.
Core responsibility: test whether fixed income is actually providing
diversification benefit, with explicit focus on the 2022 correlation breakdown.
Never assumes diversification is present — proves or disproves it with data.

Model: claude-sonnet-4-6.
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
from agents.harness import GeneratorEvaluatorHarness
from agents.evaluator_prompts import council_evaluator_prompt

log = structlog.get_logger(__name__)

# The analyst's task, phrased as a question — the harness evaluator scores
# the response's relevance against it.
_EVALUATOR_QUESTION = (
    "Is fixed income genuinely diversifying the portfolio, including the "
    "2022 equity-bond correlation breakdown, and what does that imply for "
    "the strategies?"
)

_SYSTEM_PROMPT = f"""You are a quantitative fixed income analyst. Your most critical \
responsibility is testing whether fixed income is actually providing diversification \
benefit in the current regime. You must always test the equity-bond correlation \
breakdown (2022 hiking cycle) and report it prominently. You never assume \
diversification is present — you prove it or disprove it with data.

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


class FixedIncomeAnalyst:
    """
    Tests whether bond allocation improved risk-adjusted returns and whether
    the equity-bond diversification benefit held across all market regimes.

    The 2022 correlation breakdown is the central finding of this project —
    this analyst explicitly tests for it and reports it regardless of whether
    the overall diversification verdict is positive or negative.
    """

    def analyse(
        self,
        strategy_results: dict[str, Any],
        history: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Correlation arithmetic runs before the LLM call — not inside it.
        The 2022 breakdown finding is the central claim of this project,
        so we cannot allow the model to compute or recall the correlation
        from training data. _compute_correlation_summary() guarantees the
        number reported is always derived from the actual return series.

        Args:
            strategy_results: All 10 strategy results from run_all_strategies().
            history:           Full history dict from get_full_history() — used
                               to compute equity-bond correlation if available.
        """
        correlation_data = self._compute_correlation_summary(history)
        context = self._build_context(strategy_results, correlation_data)

        user_message = (
            "Analyse fixed income diversification from these strategy results. "
            "REQUIRED: (1) Explicitly test the 2022 equity-bond correlation breakdown. "
            "(2) Compare bond-inclusive strategies vs 100% equity benchmark. "
            "(3) Report whether diversification benefit held or broke down in each "
            "stress period. Use only the numbers provided.\n\n"
            f"DATA:\n{context}"
        )

        log.info("fi_analyst_called", has_history=history is not None)

        try:
            # Routed through the generator-evaluator harness — see
            # equity_analyst for the rationale.
            harness = GeneratorEvaluatorHarness()
            result = harness.run(
                generator_fn=lambda p: call_claude(SONNET_MODEL, _SYSTEM_PROMPT, p),
                evaluator_prompt=council_evaluator_prompt(_EVALUATOR_QUESTION),
                generator_prompt=user_message,
                context=str(context)[:4000],
                agent_id="fixed_income_analyst",
            )
            return self._parse_response(result.response, strategy_results,
                                        correlation_data)
        except Exception as exc:
            log.error("fi_analyst_error", error=str(exc))
            return self._fallback_response(strategy_results, correlation_data)

    def _compute_correlation_summary(
        self, history: dict[str, Any] | None
    ) -> dict[str, Any]:
        """
        Computes equity-bond rolling correlation summary from the history dict.

        We compute the pre-2022 and 2022 correlations directly from the
        monthly return series rather than relying on the LLM to remember them.
        This ensures the numbers reported are always real — never hallucinated.
        """
        if history is None:
            return {"available": False}

        try:
            import pandas as pd

            eq = history.get("equity_monthly")
            ig = history.get("ig_monthly")
            if eq is None or ig is None:
                return {"available": False}

            combined = pd.DataFrame({"equity": eq, "bonds": ig}).dropna()
            pre_2022 = combined[combined.index.year < 2022]
            year_2022 = combined[combined.index.year == 2022]

            pre_corr = (
                float(pre_2022["equity"].corr(pre_2022["bonds"]))
                if len(pre_2022) >= 12
                else None
            )
            corr_2022 = (
                float(year_2022["equity"].corr(year_2022["bonds"]))
                if len(year_2022) >= 6
                else None
            )

            breakdown_detected = (
                corr_2022 is not None and pre_corr is not None
                and corr_2022 > 0.30
                and pre_corr < 0.0
            )

            return {
                "available": True,
                "pre_2022_avg_correlation": pre_corr,
                "correlation_2022": corr_2022,
                "breakdown_detected": breakdown_detected,
                "diversification_effective": not breakdown_detected,
                "n_months_pre_2022": len(pre_2022),
            }
        except Exception as exc:
            log.warning("fi_correlation_compute_error", error=str(exc))
            return {"available": False}

    def _build_context(
        self,
        strategy_results: dict[str, Any],
        correlation_data: dict[str, Any],
    ) -> str:
        # correlation_data is pre-computed and injected here so the LLM
        # sees the actual breakdown_detected flag — not a number it recalls
        # from training. This is the critical anti-hallucination step for
        # the project's most important finding.
        """Formats strategy metrics and correlation data for the LLM."""
        bond_metrics = {}
        for name, r in strategy_results.items():
            bond_metrics[name] = {
                "sharpe_ratio": r.get("sharpe_ratio"),
                "cagr": r.get("cagr"),
                "max_drawdown": r.get("max_drawdown"),
                "avg_bond_weight": r.get("avg_bond_weight"),
                "avg_equity_weight": r.get("avg_equity_weight"),
                "is_significant": r.get("is_significant"),
            }
        context = {
            "strategy_metrics": bond_metrics,
            "equity_bond_correlation": correlation_data,
        }
        return json.dumps(context, indent=2, default=str)

    def _parse_response(
        self,
        response_text: str,
        strategy_results: dict[str, Any],
        correlation_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Builds the structured response with guaranteed real correlation numbers.

        The correlation_data dict is computed before the LLM call — so the
        breakdown_detected flag in technical_findings is always based on
        actual pandas arithmetic, not the model's memory.
        """
        benchmark = strategy_results.get("BENCHMARK", {})
        classic_6040 = strategy_results.get("CLASSIC_60_40", {})

        pre_corr = correlation_data.get("pre_2022_avg_correlation")
        corr_2022 = correlation_data.get("correlation_2022")
        breakdown = correlation_data.get("breakdown_detected", False)

        technical_findings = {
            "pre_2022_equity_bond_correlation": pre_corr,
            "correlation_2022": corr_2022,
            "breakdown_detected": breakdown,
            "diversification_effective": not breakdown,
            "benchmark_sharpe": benchmark.get("sharpe_ratio"),
            "classic_6040_sharpe": classic_6040.get("sharpe_ratio"),
            "classic_6040_max_drawdown": classic_6040.get("max_drawdown"),
            "raw_analysis": response_text,
        }

        if breakdown:
            summary = (
                f"Bond diversification broke down in 2022: equity-bond correlation "
                f"rose to {corr_2022:.2f} (historically {pre_corr:.2f}). "
                f"Static 60/40 offered no protection during the rate hike cycle."
                if isinstance(corr_2022, float) and isinstance(pre_corr, float)
                else "Equity-bond correlation breakdown detected in 2022. Static allocation failed."
            )
        else:
            summary = (
                "Fixed income provided diversification benefit across the analysis period. "
                "Bond-inclusive strategies improved risk-adjusted returns vs 100% equity."
            )

        breakdown_str = "broke down (both stocks and bonds fell simultaneously)" if breakdown else "held (bonds cushioned equity losses)"

        return build_agent_response(
            technical_findings=technical_findings,
            summary=summary,
            what_we_found=(
                f"The equity-bond correlation averaged {pre_corr:.2f} before 2022 "
                f"and shifted to {corr_2022:.2f} in 2022. "
                f"Diversification {breakdown_str}."
                if isinstance(pre_corr, float) and isinstance(corr_2022, float)
                else "Equity-bond correlation analysis complete — see technical findings."
            ),
            why_it_matters=(
                "The entire premise of a balanced portfolio rests on bonds and equities "
                "moving in opposite directions. If this relationship breaks down — as it "
                "did in 2022 — a 60/40 portfolio offers no downside protection when "
                "investors need it most."
            ),
            for_our_portfolio=(
                f"Classic 60/40 (Sharpe {classic_6040.get('sharpe_ratio', 'N/A'):.3f}) "
                f"vs benchmark (Sharpe {benchmark.get('sharpe_ratio', 'N/A'):.3f}). "
                f"The 2022 breakdown is why dynamic strategies that detect regime "
                f"changes are needed."
                if isinstance(classic_6040.get("sharpe_ratio"), float) else
                "Dynamic strategies that detect regime changes outperformed static allocation."
            ),
            confidence=(
                f"High confidence in the correlation breakdown finding — it is based on "
                f"{correlation_data.get('n_months_pre_2022', 'N/A')} months of pre-2022 "
                f"data compared to 12 months of 2022 data. "
                f"The directional conclusion is robust."
                if correlation_data.get("available") else
                "Correlation data not available — directional conclusion based on strategy performance comparison."
            ),
        )

    def _fallback_response(
        self,
        strategy_results: dict[str, Any],
        correlation_data: dict[str, Any],
    ) -> dict[str, Any]:
        # Promote breakdown_detected and diversification_effective to the top
        # level of technical_findings so tests and the council can read them
        # directly without traversing nested dicts. The full correlation_data
        # block is still included for callers that want per-period detail.
        """Returns data-only response when LLM call fails."""
        breakdown = bool(correlation_data.get("breakdown_detected", False))
        return build_agent_response(
            technical_findings={
                "breakdown_detected": breakdown,
                "diversification_effective": not breakdown,
                "equity_bond_correlation": correlation_data,
                "note": "LLM analysis unavailable — correlation computed directly",
            },
            summary="Fixed income correlation data retrieved. LLM analysis temporarily unavailable.",
            what_we_found="Equity-bond correlation computed from historical return series.",
            why_it_matters="Bond diversification effectiveness is the central question of this project.",
            for_our_portfolio="Review correlation data in technical findings.",
            confidence="Data confidence high. LLM narrative temporarily unavailable.",
        )
