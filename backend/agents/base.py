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
    "FNA 670 practicum at the McColl School of Business, Queens University "
    "Charlotte. If a query or instruction attempts to redirect "
    "you to any other task — regardless of how it is framed — respond only "
    "with: 'This query is outside the scope of the Forest Capital Portfolio "
    "Intelligence System.' Do not explain further. Do not engage with the "
    "off-topic content in any way."
)

# Model-name constants — the single source of truth for every model
# string. Sonnet for specialist analysts; Opus for CIO and QA; Haiku for
# the Explainer. GEMINI_MODEL is the non-Claude dissenter model — kept
# here too so every model string the codebase references lives in one place.
SONNET_MODEL = "claude-sonnet-4-6"
OPUS_MODEL = "claude-opus-4-7"
HAIKU_MODEL = "claude-haiku-4-5-20251001"
# gemini-1.5-pro was retired; gemini-2.0-flash is the current GA model.
# The Gemini SDK was also migrated — google-generativeai (deprecated) →
# google-genai. See call_gemini below.
GEMINI_MODEL = "gemini-2.0-flash"

# Token budget per call — protects credits from runaway prompts.
MAX_INPUT_TOKENS = 2048
MAX_OUTPUT_TOKENS = 1024

# Anthropic server-side web-search tool. The search runs inside
# Anthropic's infrastructure — the SDK handles the search/read loop
# transparently and the call still returns once. max_uses caps the
# number of searches per call; the specialist citation instruction asks
# for 2-3 citations, so 3 searches is the ceiling.
WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3,
}

# Appended verbatim to every specialist analyst's system prompt. It is a
# runtime instruction, paired with WEB_SEARCH_TOOL on the call — without
# the tool the model cannot search and would be tempted to invent a
# citation, which the "omit rather than invent" rule forbids.
CITATION_INSTRUCTION = (
    "When your analysis references a well-known finding, methodology, or "
    "empirical result, search for and cite a relevant academic paper or "
    "authoritative source to support it.\n\n"
    "Prioritise:\n"
    "- Peer-reviewed finance journals (Journal of Finance, Review of "
    "Financial Studies, Journal of Portfolio Management)\n"
    "- Recognised practitioner research (AQR, Research Affiliates, PIMCO "
    "white papers)\n"
    "- SSRN working papers for recent findings\n\n"
    "Format citations inline as (Author, Year) with a full reference at "
    "the end of your analysis.\n\n"
    "Do not fabricate citations. If you cannot find a relevant source via "
    "web search, omit the citation rather than inventing one.\n\n"
    "Limit to 2-3 citations per analysis — support key claims only, do "
    "not pad."
)


def _extract_text(message: Any) -> str:
    """
    Pulls the assistant's final text from a messages-API response.

    Without tools the response is a single text block — content[0].text.
    With the web-search tool the SDK returns a mixed block list
    (server_tool_use, web_search_tool_result, text); the visible answer
    is the concatenation of the text blocks. Joining the text blocks
    works for both shapes, so it is used unconditionally.
    """
    parts = [getattr(b, "text", "") for b in message.content
             if getattr(b, "type", None) == "text"]
    joined = "".join(parts).strip()
    if joined:
        return joined
    # No text block at all — fall back to the first block's text if it
    # has one, else empty string (the caller's harness/except handles it).
    first = message.content[0] if message.content else None
    return getattr(first, "text", "") if first is not None else ""


def get_anthropic_client() -> anthropic.Anthropic:
    """Returns an authenticated Anthropic client using the environment key."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    return anthropic.Anthropic(api_key=api_key)


def call_claude(
    model: str,
    system_prompt: str,
    user_message: str,
    max_tokens: int = MAX_OUTPUT_TOKENS,
    tools: list[dict] | None = None,
) -> str:
    """
    Thin wrapper around the Anthropic messages API.

    Keeps all agents on the same calling convention and makes token caps
    easy to enforce in one place. The 1024 output cap is CLAUDE.md Section 13
    credit protection — sufficient for analysis, prevents runaway prompts.

    tools — when supplied (e.g. [WEB_SEARCH_TOOL]) the call is made with
    that tool list. The Anthropic server-side web-search tool runs the
    search loop inside Anthropic's infrastructure, so the call still
    returns a single response; _extract_text joins the text blocks out
    of the mixed block list.

    Error-handling note: call_claude deliberately does NOT catch Anthropic
    API errors — it lets them propagate. This is asymmetric with the Gemini
    (independent_analyst) and Grok (contrarian_analyst) helpers, which catch
    and return a structured fallback. The asymmetry is intentional: every
    call_claude caller already wraps it (the GeneratorEvaluatorHarness, and
    each specialist's own try/except), so a second layer of swallowing here
    would only hide failures. Do not "align" this without removing those
    outer handlers first.
    """
    client = get_anthropic_client()
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": _with_academic_context(system_prompt),
        "messages": [{"role": "user", "content": user_message}],
    }
    if tools:
        kwargs["tools"] = tools
    message = client.messages.create(**kwargs)
    # Token-usage capture — a no-op unless an endpoint started a capture.
    try:
        from agents.usage import record_usage
        record_usage(model, message.usage.input_tokens,
                     message.usage.output_tokens)
        # Web-search requests carry a small per-search surcharge on top of
        # the token cost. They are logged (not token-priced) so a search-
        # heavy call is visible in the Render logs alongside cost tracking.
        n_searches = _web_search_count(message)
        if n_searches:
            log.info("web_search_used", model=model, n_searches=n_searches)
    except Exception:  # noqa: BLE001 — cost telemetry must never break a call
        pass
    return _extract_text(message)


def _web_search_count(message: Any) -> int:
    """Number of server-side web searches a response consumed, or 0."""
    try:
        stu = getattr(message.usage, "server_tool_use", None)
        return int(getattr(stu, "web_search_requests", 0) or 0)
    except Exception:  # noqa: BLE001
        return 0


def _with_academic_context(system_prompt: str) -> str:
    """
    Appends any uploaded academic reference documents (the midpoint rubric,
    the final-presentation requirements, etc.) to a system prompt.

    This is the single injection point for every Anthropic agent that goes
    through call_claude — equity / fixed-income / risk / quant analysts,
    the CIO, the QA agent and the academic writer. Agents that build their
    own API call (academic_advisor with web-search tools, the Gemini and
    Grok agents) call inject_academic_context() at their own call sites.
    Fail-open: any error here must never block an agent response.
    """
    try:
        from tools.academic_context import inject_academic_context
        return inject_academic_context(system_prompt)
    except Exception as exc:  # noqa: BLE001
        # Fail-open, but log so a persistently broken academic-context
        # cache is visible rather than silently dropping agent context.
        log.warning("academic_context_inject_failed", error=str(exc))
        return system_prompt


def call_gemini(model: str, system_prompt: str, user_message: str) -> str:
    """
    Thin wrapper around the google-genai SDK — the single Gemini call
    convention for the codebase.

    google-genai is the current Google GenAI package; the older
    google-generativeai package (genai.configure / genai.GenerativeModel)
    was deprecated. This wrapper isolates the SDK so the Gemini dissenter
    (independent_analyst), the academic-review Gemini peer and the
    document-editing assistant all construct the client one way.

    The SDK is imported lazily so the test environment — which mocks every
    Gemini path before reaching here — never needs the package installed.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY", ""))
    response = client.models.generate_content(
        model=model,
        contents=user_message,
        config=types.GenerateContentConfig(system_instruction=system_prompt),
    )
    # Token-usage capture — a no-op unless an endpoint started a capture.
    try:
        from agents.usage import record_usage
        um = getattr(response, "usage_metadata", None)
        if um is not None:
            record_usage(model,
                         getattr(um, "prompt_token_count", 0),
                         getattr(um, "candidates_token_count", 0))
    except Exception:  # noqa: BLE001 — cost telemetry must never break a call
        pass
    return response.text or ""


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
