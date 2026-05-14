"""
agents/explainer_agent.py

Explainer Agent — Grok-3-mini via xAI (primary), Claude Haiku (fallback).

Generates all plain-English content dynamically after council completes.
Never blocks the council deliberation — runs in background after results arrive.

Four trigger types (CLAUDE.md Section 15 Dynamic Explanation Architecture):
  1. Terms glossary    — after full council session
  2. Parameter click   — on demand from dashboard
  3. Persona prompt    — on "View system prompt" click
  4. Chart explanation — on chart hover/click

Why Grok-3-mini over Haiku:
  Cost per session is similar at these prompt sizes (< $0.05 either way),
  but routing the high-frequency Explainer load through Grok keeps the
  Anthropic credit budget concentrated on the council deliberations
  (Sonnet specialists + Opus CIO + Opus QA) where quality matters most.
  Grok-3-mini's output quality at the constrained prompts here — every
  call cites only numbers already in the input — is indistinguishable
  from Haiku for the user-visible content.

Falls back to Haiku when XAI_API_KEY is unset or the xAI endpoint
returns an error. The fallback is silent from the caller's perspective:
both code paths return plain text via the same _call_llm() wrapper.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
import structlog

from agents.base import HAIKU_MODEL, SCOPE_ENFORCEMENT, call_claude


# xAI / Grok configuration — mirrors the pattern in agents/contrarian_analyst.py
# so the cost-routing decisions stay consistent across the agent fleet.
# Tighter timeout than the contrarian (20s vs 30s): the Explainer fires
# on user interactions (chart hover, term click) and a slow response
# degrades the UX more than missing a single contrarian opinion would.
XAI_API_URL = "https://api.x.ai/v1/chat/completions"
XAI_MODEL = "grok-3-mini"
XAI_TIMEOUT_SECONDS = 20.0

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = f"""You are a financial educator embedded in a portfolio analysis system. \
Explain technical finance and statistics concepts in plain English, always anchored to the \
specific numbers and results provided to you. Never use generic textbook definitions. \
Write for a smart reader with no finance background — but write at the register of a \
Goldman Sachs research note, not a textbook. When results are uncertain, say so honestly.

FORBIDDEN PHRASES (rewrite if generated):
  'simply put' / 'in simple terms' / 'basically' / 'in other words'
  'as you can see' / 'don't worry' / 'this is just' / 'easy to understand'
  'for those unfamiliar' / 'you might be wondering' / 'let me explain'

KEY CALLOUTS must each contain:
  — a specific number from the data
  — an implication, not just an observation

NARRATIVE (what_to_tell_the_audience):
  Sentence 1: the finding, with a specific number
  Sentence 2: the mechanism — why does this happen?
  Sentence 3: the implication — what should an investor do with this?
  Total: 60-80 words.

{SCOPE_ENFORCEMENT}"""


def _call_grok(
    api_key: str, system_prompt: str, user_message: str, max_tokens: int,
) -> str:
    """
    Single-shot xAI call returning the plain-text content. Raises on any
    non-2xx response so _call_llm() can catch and fall back to Haiku.
    """
    with httpx.Client(timeout=XAI_TIMEOUT_SECONDS) as client:
        resp = client.post(
            XAI_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
            },
            json={
                "model":       XAI_MODEL,
                "messages":    [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_message},
                ],
                "max_tokens":  max_tokens,
                "temperature": 0.7,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    # xAI returns OpenAI-compatible shape — same as agents/contrarian_analyst
    return data["choices"][0]["message"]["content"]


def _call_llm(system_prompt: str, user_message: str, max_tokens: int = 800) -> str:
    """
    Routes every Explainer LLM call through here. Grok-3-mini is tried
    first when XAI_API_KEY is set; Haiku is the silent fallback when
    the key is unset or the xAI call fails (timeout, 5xx, malformed
    response). Callers see plain text either way and don't branch on
    which model produced it.
    """
    api_key = os.getenv("XAI_API_KEY", "")
    if api_key:
        try:
            text = _call_grok(api_key, system_prompt, user_message, max_tokens)
            log.info("explainer_grok_completed", chars=len(text))
            return text
        except Exception as exc:
            # Common reasons to fall back: rate limit, xAI brief outage,
            # response shape change. Logged at warning level so it shows
            # up in the AI Usage Log without flooding the error feed.
            log.warning("explainer_grok_fallback_to_haiku", error=str(exc))

    # Haiku fallback — also the no-XAI-key path. Same prompt, same shape.
    return call_claude(HAIKU_MODEL, system_prompt, user_message, max_tokens)


class ExplainerAgent:
    """
    Generates contextual plain-English explanations anchored to real session data.

    All output is driven by actual numbers from the current session —
    never generic definitions. This prevents the anti-patronising principle
    from being violated: every explanation references what THIS analysis found.
    """

    def explain_terms(self, council_output: dict[str, Any]) -> dict[str, Any]:
        """
        Generates a dynamic glossary from the full council output.

        Called automatically after every council session completes.
        The glossary is specific to what the council actually found —
        not textbook definitions of Sharpe ratio in the abstract.
        """
        # Extract the key terms that appear in this session's output
        significant = council_output.get("significant_strategies", [])
        n_sig = len(significant)

        context = json.dumps(
            {
                "significant_strategies": significant,
                "n_strategies_tested": 10,
                "primary_recommendation": (
                    significant[0] if significant else "None"
                ),
                "agent_summaries": {
                    agent: data.get("summary", "")
                    for agent, data in council_output.get("agents", {}).items()
                    if isinstance(data, dict)
                },
            },
            indent=2,
            default=str,
        )

        user_message = (
            f"Generate contextual explanations for these key terms from this session's "
            f"findings. {n_sig} strategies passed all Tier 1 gates. "
            f"Reference specific numbers from the data in every explanation.\n\n"
            f"Generate plain-English explanations for:\n"
            f"1. p < 0.005 (using actual results)\n"
            f"2. Sharpe ratio (referencing the actual best/worst values found)\n"
            f"3. Walk-forward OOS (referencing actual OOS degradation found)\n"
            f"4. FDR correction (referencing how many strategies passed/failed)\n"
            f"5. CV Stability Score (referencing the actual stability scores)\n\n"
            f"DATA:\n{context}\n\n"
            f"Return JSON: {{term: {{hover: str, what: str, why: str, in_context: str}}}}"
        )

        try:
            response = _call_llm(_SYSTEM_PROMPT, user_message, max_tokens=800)
            # Strip markdown fences if present
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            return json.loads(clean.strip())
        except Exception as exc:
            log.error("explainer_terms_error", error=str(exc))
            return self._fallback_terms(significant)

    def explain_parameter(
        self,
        parameter: str,
        value: Any,
        current_results: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Generates a contextual explanation for a single config parameter.

        Called when the user clicks a parameter on the dashboard.
        The explanation anchors to what effect the current value had
        on this session's actual results — not a generic definition.
        """
        context = json.dumps(
            {
                "parameter": parameter,
                "current_value": value,
                "significant_strategies": [
                    name for name, r in current_results.items()
                    if r.get("is_significant", False)
                ],
                "sample_sharpe": {
                    name: r.get("sharpe_ratio")
                    for name, r in list(current_results.items())[:3]
                },
            },
            indent=2,
            default=str,
        )

        user_message = (
            f"Explain the config parameter '{parameter}' (current value: {value}) "
            f"in the context of this session's results. What effect is this value "
            f"having right now? What would change if we increased or decreased it?\n\n"
            f"DATA:\n{context}\n\n"
            f"Return JSON: {{parameter: str, value: str, hover: str, what: str, "
            f"why: str, effect_now: str, what_if: str}}"
        )

        try:
            response = _call_llm(_SYSTEM_PROMPT, user_message, max_tokens=512)
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            return json.loads(clean.strip())
        except Exception as exc:
            log.error("explainer_parameter_error", parameter=parameter, error=str(exc))
            return {
                "parameter": parameter,
                "value": str(value),
                "hover": f"{parameter} is currently set to {value}.",
                "what": f"This parameter controls {parameter} in the analysis.",
                "why": "See CLAUDE.md config for the rationale behind this value.",
                "effect_now": "Effect cannot be computed — Explainer temporarily unavailable.",
                "what_if": "Effect cannot be computed — Explainer temporarily unavailable.",
            }

    def explain_persona(
        self,
        agent_name: str,
        system_prompt: str,
        findings: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Explains what an agent's system prompt instructed and how it shaped findings.

        Triggered when user clicks "View system prompt" on an agent card.
        The explanation is specific to this session — not a generic description
        of what the agent type does in theory.
        """
        context = json.dumps(
            {
                "agent_name": agent_name,
                "system_prompt_excerpt": system_prompt[:400],  # cap for token budget
                "agent_summary": findings.get("summary", ""),
            },
            indent=2,
            default=str,
        )

        user_message = (
            f"Explain what the {agent_name}'s system prompt instructed it to do, "
            f"in plain English. Then explain how those instructions shaped the "
            f"actual findings in this session. Avoid repeating the prompt verbatim — "
            f"explain the intent and design decisions.\n\n"
            f"DATA:\n{context}\n\n"
            f"Return JSON: {{plain_english: str, design_decisions: str, this_session: str}}"
        )

        try:
            response = _call_llm(_SYSTEM_PROMPT, user_message, max_tokens=512)
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            return json.loads(clean.strip())
        except Exception as exc:
            log.error("explainer_persona_error", agent=agent_name, error=str(exc))
            return {
                "plain_english": f"The {agent_name} analyses portfolio strategies from its specialist perspective.",
                "design_decisions": "See system prompt tab for the full configuration.",
                "this_session": findings.get("summary", "No summary available."),
            }

    def explain_chart(
        self,
        chart_id: str,
        chart_type: str,
        chart_data: Any,
        current_results: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Generates a chart-specific explanation anchored to what the chart shows.

        Called on chart hover/click. The key_callouts must reference
        actual values in chart_data — never generic observations that
        could apply to any chart of this type.
        """
        significant = [
            name for name, r in current_results.items()
            if r.get("is_significant", False)
        ]

        # Build a compact summary of chart data for the prompt
        chart_summary: dict[str, Any] = {"chart_id": chart_id, "chart_type": chart_type}
        if isinstance(chart_data, dict):
            # Include a sample of the actual data for grounding
            chart_summary["data_keys"] = list(chart_data.keys())[:10]
        elif isinstance(chart_data, list) and chart_data:
            chart_summary["n_data_points"] = len(chart_data)
            chart_summary["sample"] = chart_data[:3]

        context = json.dumps(
            {
                "chart": chart_summary,
                "significant_strategies": significant,
                "top_strategy_sharpe": {
                    name: current_results[name].get("sharpe_ratio")
                    for name in significant[:3]
                },
                "benchmark_sharpe": current_results.get("BENCHMARK", {}).get("sharpe_ratio"),
            },
            indent=2,
            default=str,
        )

        user_message = (
            f"Generate a chart explanation for '{chart_id}' ({chart_type}). "
            f"Write key_callouts that reference actual values from the data — "
            f"not generic observations. The what_to_tell_the_audience must be "
            f"60-80 words: finding (with number), mechanism, implication.\n\n"
            f"DATA:\n{context}\n\n"
            f"Return JSON: {{"
            f"chart_id: str, hover_summary: str, purpose: str, how_to_read: str, "
            f"key_callouts: list[str], narrative: str, what_to_watch: str"
            f"}}"
        )

        try:
            response = _call_llm(_SYSTEM_PROMPT, user_message, max_tokens=600)
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            return json.loads(clean.strip())
        except Exception as exc:
            log.error("explainer_chart_error", chart_id=chart_id, error=str(exc))
            return self._fallback_chart(chart_id, chart_type, significant)

    def explain_qa(
        self, audit_results: list[dict[str, Any]]
    ) -> dict[str, dict[str, str]]:
        """
        Generates plain-English explanations for all 30 QA checklist items.

        Called after every audit run. Explanations are specific to the
        actual pass/fail results — not generic descriptions of the checks.
        Streams into glossaryStore.qa namespace on the frontend.
        """
        # Build compact audit summary for prompt
        failed = [r for r in audit_results if r.get("status") == "FAIL"]
        warned = [r for r in audit_results if r.get("status") == "WARN"]
        passed = [r for r in audit_results if r.get("status") == "PASS"]

        context = json.dumps(
            {
                "n_failed": len(failed),
                "n_warned": len(warned),
                "n_passed": len(passed),
                "failed_items": [r.get("check_id") for r in failed],
                "warned_items": [r.get("check_id") for r in warned],
                "sample_items": audit_results[:5],
            },
            indent=2,
            default=str,
        )

        user_message = (
            f"Generate plain-English explanations for each QA audit result. "
            f"{len(failed)} items failed, {len(warned)} warned, {len(passed)} passed. "
            f"For each item, explain: what it tests, why it matters, what failure "
            f"would mean, and how it was tested in this session.\n\n"
            f"DATA:\n{context}\n\n"
            f"Return JSON: {{check_id: {{what: str, why: str, failure_meaning: str, how_tested: str}}}}"
        )

        try:
            response = _call_llm(_SYSTEM_PROMPT, user_message, max_tokens=800)
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            return json.loads(clean.strip())
        except Exception as exc:
            log.error("explainer_qa_error", error=str(exc))
            return {}

    # ── Fallbacks ────────────────────────────────────────────────────────────

    def _fallback_terms(self, significant: list[str]) -> dict[str, Any]:
        """Returns minimal glossary when Haiku is unavailable."""
        n = len(significant)
        return {
            "p < 0.005": {
                "hover": "Less than 0.5% chance the result is due to luck.",
                "what": "The probability threshold for calling a result genuine.",
                "why": "10 strategies tested means some will look good by chance. p < 0.005 guards against this.",
                "in_context": f"{n} strategies cleared this threshold across all five Tier 1 gates.",
            },
            "Sharpe ratio": {
                "hover": "Return earned per unit of risk — higher is better.",
                "what": "Divides excess return by volatility.",
                "why": "Lets you compare strategies with different risk levels on equal terms.",
                "in_context": f"{n} strategies beat the benchmark Sharpe after all tests.",
            },
        }

    def _fallback_chart(
        self, chart_id: str, chart_type: str, significant: list[str]
    ) -> dict[str, Any]:
        """Returns minimal chart explanation when Haiku is unavailable."""
        return {
            "chart_id": chart_id,
            "hover_summary": f"This chart shows {chart_id.replace('_', ' ')} data.",
            "purpose": "Provides visual evidence for the portfolio analysis findings.",
            "how_to_read": "Compare strategy lines against the benchmark.",
            "key_callouts": [f"{len(significant)} strategies passed all Tier 1 gates."],
            "narrative": "See the technical findings panel for detailed statistics.",
            "what_to_watch": "Focus on strategy lines vs the benchmark (red).",
        }
