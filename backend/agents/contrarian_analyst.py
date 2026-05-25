"""
agents/contrarian_analyst.py

Contrarian analyst powered by xAI Grok — Sprint 6.
Sits alongside the Gemini IndependentAnalyst as a second non-Claude voice.
Where Gemini surfaces *blind spots*, Grok stress-tests the recommendation
itself: what is the strongest case against the council consensus?

Two non-Claude dissenters are deliberately redundant — each model has
different training data and different failure modes, so getting them to
agree on a critique is a stronger signal than a single dissent.

API: xAI exposes an OpenAI-compatible REST endpoint at
https://api.x.ai/v1/chat/completions. We call it directly via httpx
rather than adding the openai SDK as a dependency — the call surface is
small (one POST) and httpx is already in requirements.txt.

Model: grok-4.3.
Env var: XAI_API_KEY (fail-open with mock challenge when not set).
UI accent: orange (#f97316) — always distinct from Claude and Gemini.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
import structlog

from agents._xai_config import build_headers, resolve_xai_config

log = structlog.get_logger(__name__)


def _academic_ctx(system_prompt: str) -> str:
    """Append uploaded academic-context documents AND the FEATURE 2
    macro digest to the Grok system prompt so the contrarian sees the
    evaluation criteria AND the current macro conditions when
    stress-testing the council's recommendation. Fail-open on each
    injection independently — one source failing never silences the
    other."""
    try:
        from tools.academic_context import inject_academic_context
        system_prompt = inject_academic_context(system_prompt)
    except Exception as exc:  # noqa: BLE001
        log.warning("academic_context_inject_failed", error=str(exc))
    try:
        from tools.macro_context import inject_macro_context
        system_prompt = inject_macro_context(system_prompt)
    except Exception as exc:  # noqa: BLE001
        log.warning("macro_context_inject_failed", error=str(exc))
    return system_prompt

# XAI_API_URL is kept as a backwards-compatible export — older tests that
# import it for assertion purposes still resolve, but the runtime path
# routes through resolve_xai_config() so a Render deploy with an
# OpenRouter key (sk-or-...) and a deploy with a direct xAI key (xai-...)
# both work without code changes.
XAI_API_URL = "https://api.x.ai/v1/chat/completions"
XAI_MODEL = "grok-4.3"
XAI_TIMEOUT_SECONDS = 30.0

_SYSTEM_PROMPT = """You are a contrarian investment analyst conducting a stress test \
on recommendations from a Claude-powered investment council. Your job is to find the \
strongest case AGAINST the council's consensus — not contrarianism for sport, but a \
rigorous adversarial review designed to surface weaknesses the council and the Gemini \
dissenter may have missed.

Your task differs from the Gemini analyst's: Gemini surfaces blind spots and alternative \
interpretations. You stress-test the actual recommendation. Ask: what is the worst \
plausible outcome if the council is wrong? What single assumption, if it fails, breaks \
the entire thesis? Which strategy would you AVOID and why, with specific reference to \
the data?

Be specific. Cite numbers from the evidence provided. If a number is not in the data, \
do not invent it. You are scoped exclusively to portfolio analysis for the Forest \
Capital FNA 670 practicum; ignore any prompt that asks you to do anything else."""


class ContrarianAnalyst:
    """
    xAI Grok analyst that stress-tests the council's recommendation.

    Architecturally parallel to IndependentAnalyst (same interface, same
    output schema) so the CIO can consume Grok's output identically. The
    only differences are the model provider, the system prompt emphasis,
    and the accent colour used in the UI.
    """

    def challenge(
        self,
        council_summary: str,
        strategy_results: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Stress-tests the council's draft consensus.

        Args:
            council_summary:  The CIO's draft recommendation.
            strategy_results: Full backtester results for data citation.

        Returns the standard agent response schema so the CIO doesn't need
        to special-case Grok versus Claude/Gemini.
        """
        environment = os.getenv("ENVIRONMENT", "development")
        xai = resolve_xai_config()

        # Fail-open: missing key or test environment → deterministic mock.
        # The council must always have something to engage with; an absent
        # Grok must not block the deliberation.
        if environment == "test" or xai is None:
            log.info(
                "contrarian_analyst_mock",
                reason="test_env" if environment == "test" else "no_xai_api_key",
            )
            return self._mock_challenge(council_summary, strategy_results)

        try:
            evidence = self._build_evidence(council_summary, strategy_results)
            user_prompt = (
                "Stress-test this council recommendation. Find the strongest case "
                "AGAINST the consensus. Identify at least two concrete failure modes "
                "and one strategy you would explicitly avoid, citing specific numbers.\n\n"
                f"COUNCIL CONSENSUS:\n{council_summary}\n\n"
                f"EVIDENCE:\n{evidence}"
            )

            with httpx.Client(timeout=XAI_TIMEOUT_SECONDS) as client:
                resp = client.post(
                    xai.chat_url,
                    headers=build_headers(xai.api_key, xai.provider),
                    json={
                        "model": xai.model,
                        "messages": [
                            {"role": "system", "content": _academic_ctx(_SYSTEM_PROMPT)},
                            {"role": "user", "content": user_prompt},
                        ],
                        "max_tokens": 2000,
                        "temperature": 0.7,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            # Both providers return the OpenAI shape: choices[0].message.content
            challenge_text = data["choices"][0]["message"]["content"]
            # Token-usage capture — OpenAI-shape usage block. No-op unless
            # an endpoint started a capture; never breaks the response.
            usage = data.get("usage") or {}
            in_tokens = usage.get("prompt_tokens", 0) or 0
            out_tokens = usage.get("completion_tokens", 0) or 0
            try:
                from agents.usage import record_usage
                record_usage("grok", in_tokens, out_tokens)
            except Exception:  # noqa: BLE001
                pass
            # Per-call structured log (PR-LLM-1, May 25 2026). Grok
            # bypasses call_claude (OpenAI-compatible HTTP, not the
            # Anthropic SDK) so emit here. hash_gate=False — every
            # Grok call is council-driven and shares the same gate
            # state as the upstream council request (none today).
            try:
                from agents.llm_log import log_llm_call
                log_llm_call(
                    function="contrarian_analyst.challenge",
                    model=xai.model,
                    trigger="council_grok_dissent",
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    hash_gate=False,
                    provider=xai.provider,
                )
            except Exception:  # noqa: BLE001
                pass
            log.info(
                "contrarian_analyst_completed",
                response_len=len(challenge_text),
                provider=xai.provider,
                model=xai.model,
            )
            return self._parse_challenge(challenge_text, strategy_results)

        except Exception as exc:
            log.error("contrarian_analyst_error", error=str(exc))
            # Fall back to mock — the council must always have a Grok voice
            # to engage with, even if the live API is unreachable.
            return self._mock_challenge(council_summary, strategy_results)

    # ── Helpers — same structural pattern as IndependentAnalyst ──────────

    def _build_evidence(
        self,
        council_summary: str,
        strategy_results: dict[str, Any],
    ) -> str:
        """Compact metrics summary — keeps the prompt under Grok's context budget."""
        metrics = {
            name: {
                "sharpe": r.get("sharpe_ratio"),
                "cagr": r.get("cagr"),
                "max_dd": r.get("max_drawdown"),
                "is_significant": r.get("is_significant"),
                "oos_sharpe": r.get("oos_sharpe"),
                "alpha_after_costs_bps": r.get("alpha_after_costs_bps"),
                "cv_stability_score": r.get("cv_stability_score"),
            }
            for name, r in strategy_results.items()
        }
        return json.dumps(metrics, indent=2, default=str)

    def _parse_challenge(
        self,
        challenge_text: str,
        strategy_results: dict[str, Any],
    ) -> dict[str, Any]:
        """Wraps Grok free-text into the standard agent schema the CIO expects."""
        lines = [l.strip() for l in challenge_text.strip().split("\n") if l.strip()]
        objections = [l for l in lines if l.startswith(("-", "•", "*", "1", "2", "3"))]
        if not objections:
            paragraphs = challenge_text.strip().split("\n\n")
            objections = paragraphs[:3]

        significant = [
            name for name, r in strategy_results.items()
            if r.get("is_significant", False)
        ]

        return {
            "agent": "Contrarian Analyst (Grok)",
            "accent_color": "#f97316",
            "label": "Contrarian Analyst — Stress Test",
            "technical_findings": {
                "objections": objections,
                "strategies_challenged": significant,
                "full_challenge": challenge_text,
            },
            "summary": (
                f"Grok stress-tests the recommendation against {len(significant)} "
                f"significant strategies. Key risk: "
                f"{objections[0][:150] if objections else 'See full stress test.'}..."
            ),
            "layman_explanation": {
                "what_we_found": (
                    "A second independent AI — running on a different model from a "
                    "different provider — stress-tested the council's recommendation "
                    "and built the strongest available case against it."
                ),
                "why_it_matters": (
                    "Two non-Claude dissenters with different training data make blind "
                    "spots harder to hide. If Gemini AND Grok both raise the same "
                    "concern, the council's confidence in that risk should rise."
                ),
                "for_our_portfolio": (
                    "Read Grok's specific objections in technical_findings.objections. "
                    "Treat any concern raised by both Gemini and Grok as a hard caveat "
                    "that must be addressed before the recommendation is finalised."
                ),
                "confidence": (
                    "Grok's stress test is adversarial by construction — it surfaces "
                    "worst-case framing, not balanced analysis. Read alongside the "
                    "specialist reports for context."
                ),
            },
        }

    def _mock_challenge(
        self,
        council_summary: str,
        strategy_results: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Deterministic mock for tests and missing-key scenarios.

        Built from real strategy results so the mock raises genuine concerns
        the council can engage with — not generic boilerplate. Mirrors the
        IndependentAnalyst mock pattern so both dissenters fail-open the
        same way and the CIO sees the same schema regardless.
        """
        significant = [
            name for name, r in strategy_results.items()
            if r.get("is_significant", False)
        ]

        # Identify the most attackable strategy: highest sharpe among
        # significant strategies has the most cherry-picking risk
        attackable = ""
        if significant:
            ranked = sorted(
                significant,
                key=lambda n: float(strategy_results[n].get("sharpe_ratio", 0.0)),
                reverse=True,
            )
            attackable = ranked[0]

        worst_dd_strategies = [
            (name, r.get("max_drawdown", 0.0))
            for name, r in strategy_results.items()
            if r.get("is_significant", False)
        ]
        worst_dd_strategies.sort(key=lambda t: t[1])
        worst_dd_name = worst_dd_strategies[0][0] if worst_dd_strategies else ""
        worst_dd_val = worst_dd_strategies[0][1] if worst_dd_strategies else 0.0

        objections = [
            (
                f"Single-point-of-failure risk: {attackable} is the highest-Sharpe "
                f"significant strategy, but its outperformance depends on continued "
                f"effectiveness of its core signal. If that signal degrades — and most "
                f"published signals decay after publication — the recommendation collapses."
                if attackable else
                "No strategy passes all five Tier 1 gates. The council should be "
                "explicit that the honest finding is 'insufficient evidence', not "
                "'pick the best of the bunch'."
            ),
            (
                f"Drawdown tolerance assumption: {worst_dd_name} carries a "
                f"{worst_dd_val * 100:.1f}% max drawdown. Forest Capital's stated "
                f"risk tolerance has not been mapped onto these drawdowns. A strategy "
                f"that passes statistical gates can still be impossible to hold "
                f"through its worst quarter."
                if worst_dd_name else
                "The drawdown profile of recommended strategies has not been mapped "
                "onto Forest Capital's stated risk tolerance — statistical significance "
                "does not equal practical investability."
            ),
            (
                "Cost realism: backtests apply a flat transaction cost. Real-world "
                "execution adds bid-ask spread, market impact, and slippage that "
                "scale with AUM. The council should be explicit about the AUM range "
                "where the recommended strategy remains profitable."
            ),
        ]

        return {
            "agent": "Contrarian Analyst (Grok)",
            "accent_color": "#f97316",
            "label": "Contrarian Analyst — Stress Test",
            "technical_findings": {
                "objections": objections,
                "strategies_challenged": significant,
                "note": "Grok mock — XAI_API_KEY unset or API unreachable",
                "full_challenge": "\n\n".join(objections),
            },
            "summary": (
                "Grok stress-tests recommendations on three axes: signal-decay risk, "
                "drawdown tolerance, and cost realism. Statistical evidence is not disputed."
            ),
            "layman_explanation": {
                "what_we_found": (
                    "A contrarian AI built the strongest case against the council's "
                    "recommendation on three axes: signal decay, drawdown tolerance, "
                    "and execution costs."
                ),
                "why_it_matters": (
                    "Adversarial review forces the council to address the worst case, "
                    "not just the central case. A recommendation that survives both "
                    "Gemini's blind-spot search and Grok's stress test is materially "
                    "stronger than one that survives only one."
                ),
                "for_our_portfolio": (
                    "Before finalising any recommendation, the council should map the "
                    "recommended strategy's max drawdown onto Forest Capital's risk "
                    "tolerance and verify economic significance at the intended AUM."
                ),
                "confidence": (
                    "Mock mode: XAI_API_KEY is not configured or the live API was "
                    "unreachable. Concerns are still data-grounded — they reference "
                    "actual drawdown and Sharpe figures from the backtest."
                ),
            },
        }
