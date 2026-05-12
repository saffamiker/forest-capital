"""
agents/cio.py

Chief Investment Officer — Claude Opus (claude-opus-4-20250514).

Orchestrates the full council deliberation:
  1. Briefs four Claude Sonnet specialists in parallel
  2. Compiles a draft consensus
  3. Sends the draft to Gemini for challenge
  4. Synthesises the final recommendation

Opus is used here because the CIO must hold all specialist reports in
context simultaneously and reason across conflicting views. The
incremental cost over Sonnet is justified by the complexity of the
synthesis task — this is the one call where model quality is paramount.
"""
from __future__ import annotations

import json
from typing import Any

import structlog

from agents.base import (
    GLOBAL_AGENT_RULE,
    OPUS_MODEL,
    SCOPE_ENFORCEMENT,
    build_agent_response,
    call_claude,
)
from agents.equity_analyst import EquityAnalyst
from agents.fixed_income_analyst import FixedIncomeAnalyst
from agents.independent_analyst import IndependentAnalyst
from agents.quant_backtester import QuantBacktester
from agents.risk_manager import RiskManager

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = f"""You are the Chief Investment Officer of a quantitative investment council \
advising Forest Capital. You manage a team of specialist analysts and an independent \
dissenting analyst (Gemini). Your role is to synthesise their findings and make final \
portfolio allocation decisions with full reasoning.

You only recommend strategies that pass ALL five Tier 1 primary gates:
  (1) p < 0.005 full-period test vs benchmark (power confirmed)
  (2) q < 0.005 after Benjamini-Hochberg FDR correction
  (3) p < 0.005 Deflated Sharpe Ratio
  (4) p < 0.005 out-of-sample walk-forward
  (5) CV Stability Score >= 0.60
Sub-period and regime results (Tier 2, p < 0.05) inform your narrative \
but are not hard gates. Always disclose which threshold tier applies when \
citing a p-value.

You are rigorous, decisive, and intellectually honest about uncertainty. \
You always explain reasoning in terms a sophisticated investor can follow. \
When Gemini challenges the consensus, you engage seriously before confirming \
or revising. You never recommend a strategy based on in-sample results alone.

{GLOBAL_AGENT_RULE}

{SCOPE_ENFORCEMENT}"""


class CIO:
    """
    Council orchestrator — briefs specialists, engages Gemini, synthesises.

    The deliberation is intentionally sequential (not fully parallel) because
    each step informs the next: specialist reports feed the draft consensus
    which feeds Gemini's challenge which feeds the final synthesis. Running
    all agents at once would prevent Gemini from seeing the council's position.
    """

    def __init__(self) -> None:
        self._equity = EquityAnalyst()
        self._fi = FixedIncomeAnalyst()
        self._risk = RiskManager()
        self._quant = QuantBacktester()
        self._gemini = IndependentAnalyst()

    def deliberate(
        self,
        query: str,
        strategy_results: dict[str, Any],
        history: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Runs the full council deliberation and returns a CouncilDebateResponse.

        Steps follow CLAUDE.md Section 5 council flow exactly:
          1-5. Brief four specialists
          6.   Compile draft consensus
          7-8. Challenge via Gemini
          9.   Synthesise final recommendation
          10.  Return structured response

        Args:
            query:            The user's portfolio analysis question.
            strategy_results: All 10 strategy results from run_all_strategies().
            history:          Full history dict — enables FI correlation analysis.
        """
        log.info("council_deliberation_started", query_len=len(query))

        # Step 1-5: Brief specialists
        equity_report = self._equity.analyse(strategy_results)
        fi_report = self._fi.analyse(strategy_results, history)
        risk_report = self._risk.analyse(strategy_results)
        quant_report = self._quant.analyse(strategy_results)

        log.info(
            "specialist_reports_collected",
            equity_ok=bool(equity_report),
            fi_ok=bool(fi_report),
            risk_ok=bool(risk_report),
            quant_ok=bool(quant_report),
        )

        # Step 6: Compile draft consensus — CIO summarises specialist views
        draft_consensus = self._compile_draft_consensus(
            query, equity_report, fi_report, risk_report, quant_report, strategy_results
        )

        # Step 7-8: Gemini challenge
        gemini_report = self._gemini.challenge(draft_consensus, strategy_results)
        log.info("gemini_challenge_received")

        # Step 9: Synthesise final recommendation — CIO engages with Gemini's dissent
        cio_synthesis = self._synthesise(
            query,
            draft_consensus,
            gemini_report,
            equity_report,
            fi_report,
            risk_report,
            quant_report,
            strategy_results,
        )

        log.info("council_deliberation_complete")

        return {
            "query": query,
            "agents": {
                "equity_analyst": equity_report,
                "fixed_income_analyst": fi_report,
                "risk_manager": risk_report,
                "quant_backtester": quant_report,
                "independent_analyst": gemini_report,
                "cio": cio_synthesis,
            },
            "draft_consensus": draft_consensus,
            "final_recommendation": cio_synthesis.get("recommendation", ""),
            "significant_strategies": self._get_significant(strategy_results),
        }

    def _compile_draft_consensus(
        self,
        query: str,
        equity_report: dict[str, Any],
        fi_report: dict[str, Any],
        risk_report: dict[str, Any],
        quant_report: dict[str, Any],
        strategy_results: dict[str, Any],
    ) -> str:
        """
        Asks Opus to synthesise the four specialist reports into a draft.

        This intermediate step gives Gemini a well-structured target to
        challenge — rather than Gemini reacting to four raw reports, it
        reacts to the CIO's synthesis, which mirrors the real deliberation
        flow where the chair summarises before seeking dissent.
        """
        significant = self._get_significant(strategy_results)
        n_sig = len(significant)

        context = json.dumps(
            {
                "query": query,
                "significant_strategies": significant,
                "equity_summary": equity_report.get("summary", ""),
                "fi_summary": fi_report.get("summary", ""),
                "risk_summary": risk_report.get("summary", ""),
                "quant_summary": quant_report.get("summary", ""),
                "risk_technical": {
                    k: v
                    for k, v in risk_report.get("technical_findings", {}).items()
                    if k in ("n_strategies_significant", "significant_strategies",
                             "worst_drawdown", "best_sharpe")
                },
                "fi_correlation": fi_report.get("technical_findings", {}).get(
                    "breakdown_detected"
                ),
            },
            indent=2,
            default=str,
        )

        user_message = (
            f"Based on these specialist reports, compile a draft consensus "
            f"recommendation. {n_sig} strategies passed all Tier 1 gates. "
            f"The draft will be sent to an independent Gemini analyst for challenge.\n\n"
            f"DATA:\n{context}"
        )

        try:
            return call_claude(OPUS_MODEL, _SYSTEM_PROMPT, user_message, max_tokens=800)
        except Exception as exc:
            log.error("cio_draft_error", error=str(exc))
            # Fallback draft from specialist summaries
            return (
                f"DRAFT CONSENSUS: {n_sig} strategies pass all Tier 1 gates. "
                f"Equity view: {equity_report.get('summary', 'N/A')} "
                f"Fixed income: {fi_report.get('summary', 'N/A')} "
                f"Risk: {risk_report.get('summary', 'N/A')} "
                f"Quant OOS: {quant_report.get('summary', 'N/A')}"
            )

    def _synthesise(
        self,
        query: str,
        draft_consensus: str,
        gemini_report: dict[str, Any],
        equity_report: dict[str, Any],
        fi_report: dict[str, Any],
        risk_report: dict[str, Any],
        quant_report: dict[str, Any],
        strategy_results: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Final CIO synthesis — engages with Gemini's challenge before recommending.

        Opus sees the draft, Gemini's specific objections, and all specialist
        data simultaneously. The synthesis must either rebut Gemini's concerns
        with evidence or revise the recommendation to address them.
        """
        significant = self._get_significant(strategy_results)
        gemini_objections = gemini_report.get("technical_findings", {}).get(
            "objections", []
        )

        context = json.dumps(
            {
                "query": query,
                "draft_consensus": draft_consensus,
                "gemini_objections": gemini_objections,
                "significant_strategies": significant,
                "specialist_summaries": {
                    "equity": equity_report.get("summary", ""),
                    "fixed_income": fi_report.get("summary", ""),
                    "risk": risk_report.get("summary", ""),
                    "quant": quant_report.get("summary", ""),
                },
                "quant_technical": {
                    k: v
                    for k, v in quant_report.get("technical_findings", {}).items()
                    if k in ("most_stable_strategy", "flagged_for_overfitting",
                             "transaction_cost_bps_applied")
                },
            },
            indent=2,
            default=str,
        )

        user_message = (
            "You have reviewed four specialist reports and received Gemini's challenge. "
            "Now produce the FINAL RECOMMENDATION. Required:\n"
            "1. Engage with each of Gemini's objections — rebut or acknowledge.\n"
            "2. State which strategies you recommend and why (Tier 1 gates required).\n"
            "3. State which strategies you do NOT recommend and why.\n"
            "4. Give one primary recommendation with highest conviction.\n"
            "5. State the key risk that could invalidate this recommendation.\n"
            "Use only the numbers in the data provided.\n\n"
            f"DATA:\n{context}"
        )

        try:
            synthesis_text = call_claude(
                OPUS_MODEL, _SYSTEM_PROMPT, user_message, max_tokens=1024
            )
        except Exception as exc:
            log.error("cio_synthesis_error", error=str(exc))
            synthesis_text = (
                f"FINAL RECOMMENDATION: The council recommends "
                f"{', '.join(significant[:2]) if significant else 'no strategies'} "
                f"based on Tier 1 statistical gates. Gemini's concerns are noted. "
                f"LLM narrative temporarily unavailable."
            )

        # Identify primary recommendation (first significant strategy)
        primary = significant[0] if significant else "None — no strategies pass all gates"

        return build_agent_response(
            technical_findings={
                "recommended_strategies": significant,
                "primary_recommendation": primary,
                "gemini_objections_addressed": len(gemini_objections),
                "draft_consensus": draft_consensus,
                "final_synthesis_text": synthesis_text,
            },
            summary=(
                f"Council recommends {len(significant)} strategies (Tier 1). "
                f"Primary recommendation: {primary}. "
                f"Gemini's {len(gemini_objections)} objection(s) engaged."
            ),
            what_we_found=(
                "The council reviewed 10 strategies across equity, fixed income, "
                "risk, and quantitative dimensions. An independent Gemini analyst "
                "challenged the consensus before the final recommendation was made."
            ),
            why_it_matters=(
                "A multi-agent council is more robust than a single model — "
                "each specialist focuses on their domain and Gemini introduces "
                "a genuinely different perspective. The final recommendation "
                "has survived both internal and external challenge."
            ),
            for_our_portfolio=(
                f"{len(significant)} strategies pass all five Tier 1 statistical "
                f"gates (p < 0.005 full-period, FDR, DSR, OOS, CV ≥ 0.60). "
                f"{'Recommended: ' + ', '.join(significant[:3]) if significant else 'No strategies pass all gates.'}"
            ),
            confidence=(
                "High confidence in the significance verdict — it is determined "
                "by statistical tests, not the LLM. The strategic narrative "
                "(which strategies to prioritise) is the CIO's judgement and "
                "should be read as an informed opinion, not a guarantee."
            ),
        )

    @staticmethod
    def _get_significant(strategy_results: dict[str, Any]) -> list[str]:
        """Returns strategy names that pass all Tier 1 gates."""
        return [
            name
            for name, r in strategy_results.items()
            if r.get("is_significant", False)
        ]
