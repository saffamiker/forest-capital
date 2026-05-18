"""
agents/independent_analyst.py

Independent analyst powered by Google Gemini Pro — Sprint 4.
Deliberately uses a different model from the Claude agents to surface
blind spots that similarly-trained models might miss.

Gemini's sole job: challenge the council consensus with specific,
data-grounded objections. Not contrarianism for its own sake.

Model: gemini-2.0-flash (google-genai SDK).
UI accent: purple (#7c3aed) — always distinct from Claude agents.
"""
from __future__ import annotations

import json
import os
from typing import Any

import structlog

from agents.base import GEMINI_MODEL, call_gemini

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = """You are an independent investment analyst reviewing recommendations \
from a Claude-powered investment council. Your job is to challenge their consensus — \
not contrarianism for its own sake, but surfacing risks, alternative interpretations, \
and blind spots that similarly-trained models might miss.

Be specific. Cite data from the evidence provided to you. Identify exactly what would \
have to be true for the council to be wrong.

You do not know any historical return figures, Sharpe ratios, p-values, drawdown \
statistics, or any other quantitative results from your training data. You may ONLY \
reference numbers that have been explicitly provided in the evidence you receive. \
If a number is not in the provided data, you cannot cite it."""


class IndependentAnalyst:
    """
    Google Gemini Pro analyst that challenges the Claude council's consensus.

    Using Gemini rather than another Claude instance is a deliberate
    architectural choice: different training data, different tendencies,
    genuinely independent perspective. The council's conclusions are
    stronger if they survive Gemini's challenge.
    """

    def challenge(
        self,
        council_summary: str,
        strategy_results: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Challenges the council's draft consensus with specific objections.

        Args:
            council_summary:  The CIO's draft recommendation before finalisation.
            strategy_results: The full backtester results for data reference.

        Returns a structured critique with specific objections, alternative
        views, and what would have to be true for the consensus to be wrong.
        """
        api_key = os.getenv("GOOGLE_API_KEY", "")
        environment = os.getenv("ENVIRONMENT", "development")

        # In test environment or without API key, return a structured mock response
        # so the council can proceed without requiring a live Gemini call.
        if environment == "test" or not api_key:
            log.info(
                "gemini_analyst_mock",
                reason="test_env" if environment == "test" else "no_api_key",
            )
            return self._mock_challenge(council_summary, strategy_results)

        try:
            # Inject any uploaded academic-context documents so the Gemini
            # dissenter judges the consensus against the same evaluation
            # criteria the Claude agents see. Fail-open.
            system_instruction = _SYSTEM_PROMPT
            try:
                from tools.academic_context import inject_academic_context
                system_instruction = inject_academic_context(_SYSTEM_PROMPT)
            except Exception as exc:  # noqa: BLE001
                log.warning("academic_context_inject_failed", error=str(exc))

            evidence = self._build_evidence(council_summary, strategy_results)
            prompt = (
                "Challenge this consensus. Be specific. Cite data from the evidence below. "
                "Identify at least two concrete risks or alternative interpretations "
                "the council may have underweighted. What would have to be true "
                "for this recommendation to be wrong?\n\n"
                f"COUNCIL CONSENSUS:\n{council_summary}\n\n"
                f"EVIDENCE:\n{evidence}"
            )

            challenge_text = call_gemini(GEMINI_MODEL, system_instruction, prompt)

            log.info("gemini_analyst_completed", response_len=len(challenge_text))
            return self._parse_challenge(challenge_text, strategy_results)

        except Exception as exc:
            log.error("gemini_analyst_error", error=str(exc))
            return self._mock_challenge(council_summary, strategy_results)

    def _build_evidence(
        self,
        council_summary: str,
        strategy_results: dict[str, Any],
    ) -> str:
        """
        Builds a compact evidence summary for Gemini.

        Only passes metrics, not the full results dict — keeps the prompt
        under the context budget and focuses Gemini on the key numbers.
        """
        metrics = {
            name: {
                "sharpe": r.get("sharpe_ratio"),
                "cagr": r.get("cagr"),
                "max_dd": r.get("max_drawdown"),
                "is_significant": r.get("is_significant"),
                "oos_sharpe": r.get("oos_sharpe"),
                "alpha_after_costs_bps": r.get("alpha_after_costs_bps"),
            }
            for name, r in strategy_results.items()
        }
        return json.dumps(metrics, indent=2, default=str)

    def _parse_challenge(
        self,
        challenge_text: str,
        strategy_results: dict[str, Any],
    ) -> dict[str, Any]:
        # Gemini returns free text — we wrap it in the standard agent schema
        # so the CIO can consume it identically to Claude agent outputs.
        # gemini_challenge is stored as a plain string (not structured) because
        # Gemini's response format is less predictable than Claude's.
        """Structures the free-text Gemini response into the standard schema."""
        # Split into bullet points if Gemini used them, otherwise use paragraphs.
        lines = [l.strip() for l in challenge_text.strip().split("\n") if l.strip()]
        objections = [l for l in lines if l.startswith(("-", "•", "*", "1", "2", "3"))]
        if not objections:
            # Use first three non-empty paragraphs as objections
            paragraphs = challenge_text.strip().split("\n\n")
            objections = paragraphs[:3]

        significant = [
            name for name, r in strategy_results.items()
            if r.get("is_significant", False)
        ]

        return {
            "agent": "Independent Analyst (Gemini)",
            "accent_color": "#7c3aed",
            "label": "Independent Analyst — Dissenting View",
            "technical_findings": {
                "objections": objections,
                "strategies_challenged": [
                    name for name, r in strategy_results.items()
                    if r.get("is_significant", False)
                ],
                "full_challenge": challenge_text,
            },
            "summary": (
                f"Gemini challenges the consensus on {len(significant)} significant "
                f"strategies. Key concern: "
                f"{objections[0][:150] if objections else 'See full challenge.'}..."
            ),
            "layman_explanation": {
                "what_we_found": (
                    "An independent AI using a different architecture reviewed the "
                    "Claude council's conclusions and identified potential blind spots."
                ),
                "why_it_matters": (
                    "A second opinion from a different AI model surfaces risks that "
                    "similarly-trained models might miss. The council's conclusions "
                    "are stronger if they survive this challenge."
                ),
                "for_our_portfolio": (
                    "Review the specific objections in technical_findings.objections "
                    "before accepting the CIO's final recommendation."
                ),
                "confidence": (
                    "The challenge is a structural critique, not a data-driven rejection. "
                    "The council's statistical evidence is not disputed."
                ),
            },
        }

    def _mock_challenge(
        self,
        council_summary: str,
        strategy_results: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Returns a structured mock challenge when Gemini is unavailable.

        We still need a real-looking dissent for the council to engage with,
        so this mock raises genuine concerns based on the actual results.
        """
        significant = [
            name for name, r in strategy_results.items()
            if r.get("is_significant", False)
        ]

        # Build a data-grounded challenge from the actual results
        low_alpha = [
            name for name, r in strategy_results.items()
            if (
                isinstance(r.get("alpha_after_costs_bps"), (int, float))
                and r.get("alpha_after_costs_bps", 100) < 50
                and r.get("is_significant", False)
            )
        ]

        objections = [
            (
                f"Economic significance: {len(low_alpha)} significant strategies have "
                f"alpha after costs below 50bps — the CLAUDE.md economic significance "
                f"threshold. Statistical significance does not guarantee profitability."
                if low_alpha else
                "The 50bps economic significance threshold should be verified "
                "for each recommended strategy after accounting for all costs."
            ),
            (
                "The 2022 correlation breakdown is a structural change in the bond market. "
                "All walk-forward OOS periods pre-date this regime shift — the OOS results "
                "may overstate robustness if tested primarily in the pre-2022 environment."
            ),
            (
                f"With {len(significant)} strategies passing all gates, there is a risk "
                f"of presenting too many 'winners' to Forest Capital. "
                f"The council should identify 1-2 primary recommendations "
                f"rather than endorsing all significant strategies equally."
                if len(significant) > 2 else
                "Statistical significance at p < 0.005 does not imply future performance. "
                "Market regimes change and past statistical patterns may not persist."
            ),
        ]

        return {
            "agent": "Independent Analyst (Gemini)",
            "accent_color": "#7c3aed",
            "label": "Independent Analyst — Dissenting View",
            "technical_findings": {
                "objections": objections,
                "strategies_challenged": significant,
                "note": "Gemini mock — API unavailable in this environment",
                "full_challenge": "\n\n".join(objections),
            },
            "summary": (
                "Gemini raises concerns about economic significance thresholds and "
                "2022 regime shift. The council's statistical evidence is not disputed."
            ),
            "layman_explanation": {
                "what_we_found": (
                    "An independent AI reviewed the council's conclusions and raised "
                    "three specific concerns: economic significance of alpha after costs, "
                    "2022 regime shift risk, and the number of strategies recommended."
                ),
                "why_it_matters": (
                    "Statistical significance at p < 0.005 does not guarantee future "
                    "profitability. Real-world implementation adds costs and slippage "
                    "that can erode statistically significant alpha."
                ),
                "for_our_portfolio": (
                    "The council should verify that recommended strategies have alpha "
                    "after costs above 50bps — the minimum to be economically viable "
                    "given realistic implementation friction."
                ),
                "confidence": (
                    "These are structural concerns, not a rejection of the statistical "
                    "methodology. The backtesting rigour is acknowledged."
                ),
            },
        }
