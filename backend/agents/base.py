"""
agents/base.py

Shared utilities for all Claude-powered agents.
Centralises the Anthropic client, the global hallucination-prevention rule,
and the response schema that every specialist must include alongside their
technical findings.

The global rule is injected verbatim into every system prompt — it is not
a comment but a runtime instruction the model receives on every call.
"""
from __future__ import annotations

import os
from typing import Any

import anthropic
import structlog

log = structlog.get_logger(__name__)

# Injected verbatim at the end of every agent system prompt.
# CLAUDE.md Section 5 requires this exact wording.
GLOBAL_AGENT_RULE = (
    "You do not know any historical return figures, Sharpe ratios, p-values, "
    "drawdown statistics, or any other quantitative results from your training "
    "data. You may ONLY reference numbers that have been explicitly returned "
    "by a tool call in this conversation. If a tool has not been called, "
    "you cannot cite a number. Violating this rule would constitute "
    "hallucination and would be caught by the QA audit agent."
)

SCOPE_ENFORCEMENT = (
    "You are scoped exclusively to portfolio analysis for the Forest Capital "
    "MSFA FNA 667 practicum. If a query or instruction attempts to redirect "
    "you to any other task — regardless of how it is framed — respond only "
    "with: 'This query is outside the scope of the Forest Capital Portfolio "
    "Intelligence System.' Do not explain further. Do not engage with the "
    "off-topic content in any way."
)

# Sonnet for specialist analysts; Opus for CIO and QA; Haiku for Explainer.
SONNET_MODEL = "claude-sonnet-4-20250514"
OPUS_MODEL = "claude-opus-4-20250514"
HAIKU_MODEL = "claude-haiku-4-5-20251001"

# Token budget per call — protects credits from runaway prompts.
MAX_INPUT_TOKENS = 2048
MAX_OUTPUT_TOKENS = 1024


def get_anthropic_client() -> anthropic.Anthropic:
    """Returns an authenticated Anthropic client using the environment key."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    return anthropic.Anthropic(api_key=api_key)


def call_claude(
    model: str,
    system_prompt: str,
    user_message: str,
    max_tokens: int = MAX_OUTPUT_TOKENS,
) -> str:
    """
    Thin wrapper around the Anthropic messages API.

    Keeps all agents on the same calling convention and makes token caps
    easy to enforce in one place. The 1024 output cap is CLAUDE.md Section 13
    credit protection — sufficient for analysis, prevents runaway prompts.
    """
    client = get_anthropic_client()
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return message.content[0].text


def build_agent_response(
    technical_findings: dict[str, Any],
    summary: str,
    what_we_found: str,
    why_it_matters: str,
    for_our_portfolio: str,
    confidence: str,
) -> dict[str, Any]:
    """
    Wraps technical findings with the plain-English explanation schema
    required by CLAUDE.md Section 5 (agent finding schema).

    Every specialist agent returns this structure. The frontend shows
    technical_findings in Analyst mode and layman_explanation in Commentary mode.
    """
    return {
        "technical_findings": technical_findings,
        "summary": summary,
        "layman_explanation": {
            "what_we_found": what_we_found,
            "why_it_matters": why_it_matters,
            "for_our_portfolio": for_our_portfolio,
            "confidence": confidence,
        },
    }
