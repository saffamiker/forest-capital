"""
agents/academic_advisor.py

Academic Advisor — Claude Sonnet (claude-sonnet-4-6) + Anthropic web_search tool.

Bridges the analytical output and the academic deliverables. The council
analyses strategies; the QA agent audits methodology; this agent answers
the question the team actually has: "What does this mean for our grade,
and what should we focus on?"

Two responsibilities, both with citation integrity as a non-negotiable:

  1. ACADEMIC GUIDANCE — connect strategy findings to graded deliverables
     (Final Presentation 35%, Analytical Appendix 35%, Executive Brief 20%,
     Midpoint Paper 10%). Grounded in actual strategy results, never
     boilerplate.

  2. HALLUCINATION DETECTION — every external citation is funnelled
     through the Anthropic server-side web_search tool. The agent is
     instructed to refuse any source it cannot retrieve via that tool.
     The expected behaviour: the agent issues a web_search query, reads
     the returned URLs, then cites only those.

Why server-side web_search:
  The web_search_20250305 tool runs inside Anthropic's infrastructure
  and returns actual URLs, titles, and snippet text. The agent cannot
  fabricate a paper that the tool did not return — fabrication would
  require it to invent a tool_result block, which the SDK does not
  permit. This makes citation integrity enforceable rather than
  aspirational.

Three callable methods map 1:1 to the /api/advisor/* endpoints:
  analyse_findings()           — main feedback for a deliverable
  check_finding_plausibility() — verify one finding vs external evidence
  find_supporting_citations()  — return verified academic sources only
"""
from __future__ import annotations

import json
import os
from typing import Any

import anthropic
import structlog

from agents.base import (
    GLOBAL_AGENT_RULE,
    MAX_OUTPUT_TOKENS,
    SCOPE_ENFORCEMENT,
    SONNET_MODEL,
    get_anthropic_client,
)

log = structlog.get_logger(__name__)

# Anthropic server-side web_search tool. The tool runs on Anthropic's
# infrastructure — the SDK relays tool_use and tool_result blocks
# transparently. We cap max_uses to limit cost-per-call: each search
# costs roughly a few cents on top of the Sonnet token cost, and three
# searches is enough to verify a typical advisor response.
_WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3,
}

_DELIVERABLE_RUBRIC = {
    "midpoint":      {"weight": "10%", "due": "2026-05-27", "focus": "framing, preliminary findings, methodology"},
    "appendix":      {"weight": "35%", "due": "2026-07-01", "focus": "rigour, provenance, reproducibility"},
    "brief":         {"weight": "20%", "due": "2026-07-01", "focus": "executive recommendation for Forest Capital"},
    "presentation":  {"weight": "35%", "due": "2026-07-01", "focus": "completeness, statistical rigour, implications"},
}

_SYSTEM_PROMPT = f"""You are an academic advisor for an MSFA graduate practicum at Queens University \
McColl School of Business, course FNA 670. The research question is: does diversification across \
equities and fixed income improve risk-adjusted performance versus a 100% equity benchmark over the \
period 2002-2025?

Your role has two components.

COMPONENT 1 — ACADEMIC GUIDANCE
Help the team connect their data findings to four graded deliverables:
  Final Presentation (35%)   — July 1 final
  Analytical Appendix (35%)  — rigour, provenance, reproducibility
  Executive Brief (20%)      — Forest Capital recommendation
  Midpoint Paper (10%)       — May 27 submission

Always ground feedback in the actual strategy results provided to you.
Never suggest conclusions the data does not support.
Flag the difference between what the data shows and what would need
to be true for a stronger conclusion.

COMPONENT 2 — EXTERNAL EVIDENCE AND HALLUCINATION DETECTION
For every key finding, use the web_search tool to find external academic evidence.
Verify whether each internal finding aligns with published research,
whether magnitudes are plausible vs academic literature, and whether
there is contradicting evidence that should be disclosed.

If external evidence contradicts the internal data, flag it explicitly.
Do not suppress contradictions — they are valuable quality signals.

CITATION RULES — NON-NEGOTIABLE:
  ALWAYS use web_search to verify a source exists before citing it.
  NEVER cite a paper you cannot verify via the tool.
  When you cite a source, state the URL returned by web_search.
  If you cannot verify a claim, say: 'I searched for supporting evidence
  but could not verify a reputable source for this claim.'
  Reputable sources only: Fed, IMF, BIS, AQR, NBER, peer-reviewed journals,
  major central banks. No blogs, LinkedIn posts, or unverified preprints.

GRADE AWARENESS:
  Prioritise feedback by grade weight. For the midpoint, focus on framing
  and preliminary findings. For the final, focus on completeness and
  statistical rigour.

OUTPUT FORMAT:
  Respond in JSON with four top-level keys: key_findings, guidance,
  citations (each citation having title, url, relevance, verified=true),
  potential_issues (contradictions or gaps). If you cannot verify a
  source, omit it rather than guess.

{GLOBAL_AGENT_RULE}

{SCOPE_ENFORCEMENT}"""


def _extract_text_and_sources(
    response: anthropic.types.Message,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Pulls the final-turn text and any URLs surfaced by the web_search tool.

    Why we walk the content list ourselves rather than using message.content[0]:
    when web_search runs, the SDK returns a mixed list — server_tool_use blocks,
    web_search_tool_result blocks, and text blocks all interleaved. The final
    answer is the LAST text block. Verified sources come from
    web_search_tool_result blocks, which carry the URLs Anthropic's tool
    actually fetched. Citations not appearing in this list are unverified
    by definition.
    """
    final_text = ""
    sources: list[dict[str, Any]] = []

    for block in response.content:
        block_type = getattr(block, "type", None)

        if block_type == "text":
            # Last text block wins — earlier text blocks are model
            # reasoning interleaved with tool calls.
            final_text = block.text

        elif block_type == "web_search_tool_result":
            # The tool returns a list of results — each carries
            # {title, url, encrypted_content, ...}. We surface only
            # the citable fields.
            content = getattr(block, "content", None)
            if isinstance(content, list):
                for item in content:
                    if getattr(item, "type", None) == "web_search_result":
                        sources.append({
                            "title":    getattr(item, "title", ""),
                            "url":      getattr(item, "url", ""),
                            "verified": True,
                        })

    return final_text, sources


def _parse_json_response(text: str) -> dict[str, Any]:
    """
    Extracts the first JSON object from a Sonnet response.

    Sonnet typically wraps JSON in a ```json code fence or surrounding prose;
    we strip both. On any parse failure we return a defaulted shape so the
    endpoint contract holds — the frontend treats empty arrays as "advisor
    had nothing concrete to say" rather than as a backend error.
    """
    cleaned = text.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

    # Find first { and matching outermost }
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        return {}

    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        log.warning("advisor_json_parse_failed", error=str(exc), text_head=cleaned[:200])
        return {}


def _call_advisor_with_web_search(
    user_message: str,
    max_tokens: int = MAX_OUTPUT_TOKENS,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """
    Single entry point for all advisor calls.

    Returns (parsed_json, verified_sources, usage_metadata).
    Verified_sources reflects URLs the model actually retrieved via the
    web_search tool — passed to the frontend so the advisor's citations
    can be cross-checked against tool-returned URLs.
    """
    client = get_anthropic_client()
    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=max_tokens,
        system=_SYSTEM_PROMPT,
        tools=[_WEB_SEARCH_TOOL],
        messages=[{"role": "user", "content": user_message}],
    )

    text, sources = _extract_text_and_sources(response)
    parsed = _parse_json_response(text)

    usage = {
        "input_tokens":  getattr(response.usage, "input_tokens", 0),
        "output_tokens": getattr(response.usage, "output_tokens", 0),
        "model":         SONNET_MODEL,
        "n_searches":    len(sources),
    }
    return parsed, sources, usage


class AcademicAdvisor:
    """
    Implements the three advisor endpoints with citation integrity enforced
    via Anthropic's server-side web_search tool.

    Each public method returns a JSON-serialisable dict that the FastAPI
    endpoint relays directly to the frontend. No method ever raises on a
    web_search failure — the advisor degrades to "could not verify" rather
    than refusing the call. That keeps the floating button responsive in
    Commentary mode even when the search tool is briefly unavailable.
    """

    def analyse_findings(
        self,
        query: str,
        deliverable_type: str,
        strategy_results: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Main feedback endpoint. Reviews strategy results vs the research
        question and identifies what to focus on for the named deliverable.

        deliverable_type ∈ {"midpoint", "appendix", "brief", "presentation"}.
        Unknown types still receive a response — guidance just won't be
        grade-weight-aware. Sonnet handles the fallback gracefully.
        """
        rubric = _DELIVERABLE_RUBRIC.get(
            deliverable_type,
            {"weight": "?", "due": "?", "focus": "general analytical guidance"},
        )

        # Keep the strategy_results payload compact — Sonnet doesn't need
        # every nested stress-test field to give grade-aware feedback.
        compact_results: dict[str, Any] = {}
        if strategy_results:
            for name, r in strategy_results.items():
                if not isinstance(r, dict):
                    continue
                compact_results[name] = {
                    "sharpe":         r.get("sharpe_ratio"),
                    "cagr":           r.get("cagr"),
                    "max_dd":         r.get("max_drawdown"),
                    "is_significant": r.get("is_significant"),
                    "tier1_gates":    r.get("tier1_gates_passed"),
                    "oos_sharpe":     r.get("oos_sharpe"),
                }

        user_message = (
            f"DELIVERABLE: {deliverable_type} "
            f"(grade weight {rubric['weight']}, due {rubric['due']}, focus: {rubric['focus']})\n\n"
            f"TEAM QUERY: {query}\n\n"
            f"STRATEGY RESULTS:\n{json.dumps(compact_results, default=str)[:3000]}\n\n"
            "Provide academic guidance grounded in these results. Use web_search "
            "to find external evidence supporting or contradicting the key findings. "
            "Respond in JSON with keys: key_findings (list[str]), "
            "guidance (list[str]), citations (list[{title,url,relevance,verified}]), "
            "potential_issues (list[str])."
        )

        try:
            parsed, sources, usage = _call_advisor_with_web_search(user_message)
        except Exception as exc:
            log.error("advisor_analyse_error", error=str(exc), deliverable=deliverable_type)
            return {
                "key_findings":     [],
                "guidance":         [],
                "citations":        [],
                "potential_issues": [],
                "error":            "Advisor temporarily unavailable.",
            }

        result = {
            "key_findings":      parsed.get("key_findings", []),
            "guidance":          parsed.get("guidance", []),
            "citations":         _filter_to_verified(parsed.get("citations", []), sources),
            "potential_issues":  parsed.get("potential_issues", []),
            "verified_sources":  sources,
            "deliverable_type":  deliverable_type,
        }

        _log_advisor_call(
            method="analyse",
            deliverable=deliverable_type,
            usage=usage,
            n_citations_verified=len(result["citations"]),
            n_potential_issues=len(result["potential_issues"]),
        )
        return result

    def check_finding_plausibility(
        self,
        finding: str,
        magnitude: str | float | None = None,
        period: str | None = None,
    ) -> dict[str, Any]:
        """
        Verifies one internal finding against external academic evidence.

        Returns supporting_evidence, contradicting_evidence, and a verdict
        in {"plausible", "implausible", "uncertain"}. Used by the frontend
        when the team wants a quick sanity check on a specific number.
        """
        user_message = (
            f"FINDING TO VERIFY: {finding}\n"
            f"MAGNITUDE: {magnitude if magnitude is not None else 'not specified'}\n"
            f"PERIOD: {period or 'not specified'}\n\n"
            "Use web_search to find external academic evidence about this finding. "
            "Search Fed publications, AQR research, IMF reports, NBER working papers, "
            "and peer-reviewed journals. Respond in JSON with keys: "
            "supporting_evidence (list[{title,url,summary}]), "
            "contradicting_evidence (list[{title,url,summary}]), "
            "verdict (string: 'plausible'|'implausible'|'uncertain'), "
            "reasoning (string)."
        )

        try:
            parsed, sources, usage = _call_advisor_with_web_search(user_message)
        except Exception as exc:
            log.error("advisor_verify_error", error=str(exc), finding=finding[:80])
            return {
                "supporting_evidence":    [],
                "contradicting_evidence": [],
                "verdict":                "uncertain",
                "reasoning":              "Advisor temporarily unavailable.",
                "verified_sources":       [],
            }

        result = {
            "supporting_evidence":    _filter_to_verified(parsed.get("supporting_evidence", []), sources),
            "contradicting_evidence": _filter_to_verified(parsed.get("contradicting_evidence", []), sources),
            "verdict":                parsed.get("verdict", "uncertain"),
            "reasoning":              parsed.get("reasoning", ""),
            "verified_sources":       sources,
        }

        _log_advisor_call(
            method="verify_finding",
            usage=usage,
            verdict=result["verdict"],
            n_supporting=len(result["supporting_evidence"]),
            n_contradicting=len(result["contradicting_evidence"]),
        )
        return result

    def find_supporting_citations(
        self,
        finding: str,
        n_sources: int = 3,
    ) -> dict[str, Any]:
        """
        Returns up to n_sources verified academic citations for a finding.

        Citations are returned ONLY if web_search confirmed their existence.
        Sources the model could not verify are silently dropped — this is
        the citation integrity contract.
        """
        capped = max(1, min(n_sources, 5))

        user_message = (
            f"FINDING: {finding}\n\n"
            f"Use web_search to find up to {capped} reputable academic sources supporting this "
            "finding. Search Fed publications, AQR, IMF, BIS, NBER, peer-reviewed journals. "
            "Verify every source exists via web_search before including it. "
            "Respond in JSON with key 'citations' — a list of "
            "{title, authors, year, url, relevance, verified=true}. "
            "If you cannot verify a source, omit it entirely."
        )

        try:
            parsed, sources, usage = _call_advisor_with_web_search(user_message)
        except Exception as exc:
            log.error("advisor_citations_error", error=str(exc), finding=finding[:80])
            return {"citations": [], "verified_sources": []}

        verified_citations = _filter_to_verified(parsed.get("citations", []), sources)
        _log_advisor_call(
            method="citations",
            usage=usage,
            n_citations_verified=len(verified_citations),
            n_citations_requested=capped,
        )
        return {
            "citations":        verified_citations[:capped],
            "verified_sources": sources,
        }


def _filter_to_verified(
    citations: list[Any],
    verified_sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Drops any citation whose URL does not appear in the web_search result set.

    This is the runtime enforcement of the citation integrity rule: even
    if the model invents a plausible-looking URL, it cannot survive this
    filter unless the tool actually retrieved that URL. URLs are compared
    case-insensitively with trailing slashes stripped — a common source of
    spurious mismatches.
    """
    if not isinstance(citations, list):
        return []

    verified_urls = {_normalise_url(s.get("url", "")) for s in verified_sources}
    out: list[dict[str, Any]] = []

    for c in citations:
        if not isinstance(c, dict):
            continue
        url = _normalise_url(c.get("url", ""))
        if not url:
            continue
        if url in verified_urls:
            # Force verified=true even if the model emitted false.
            out.append({**c, "verified": True})

    return out


def _normalise_url(url: str) -> str:
    """Lowercases and strips a single trailing slash for matching."""
    if not isinstance(url, str):
        return ""
    return url.strip().lower().rstrip("/")


def _log_advisor_call(method: str, **kwargs: Any) -> None:
    """
    Logs every advisor call to the AI usage feed.

    Mirrors the council_sessions log shape so the Admin AI Usage screen
    can render advisor calls alongside council deliberations. Cost is
    tracked via usage.input_tokens / output_tokens so the daily credit
    cap (DAILY_CREDIT_CAP_USD) accounts for advisor spend.
    """
    log.info("advisor_call_complete", method=method, **kwargs)


# Mock data used in ENVIRONMENT=test so the test suite never hits the
# real API. The shape mirrors what the live agent returns, including
# verified_sources, so frontend tests can mock the endpoint and rely on
# the same contract that production uses.
MOCK_ADVISOR_ANALYSE = {
    "key_findings": [
        "Regime Switching strategy passes all five Tier 1 gates at p<0.005, with a Sharpe ratio of 0.94 and CV stability of 0.78.",
        "Static 60/40 fails three of five Tier 1 gates after FDR correction.",
        "The 2022 equity-bond correlation breakdown is the central empirical finding.",
    ],
    "guidance": [
        "For the midpoint paper, lead with the correlation-breakdown finding — it directly answers the research question.",
        "Cite Lopez de Prado (2018) when introducing CPCV and the Deflated Sharpe Ratio.",
        "Frame static-strategy failure as evidence of regime dependence, not as a failure of diversification per se.",
    ],
    "citations": [
        {
            "title": "Stock-Bond Correlations in the Post-Pandemic Era",
            "url": "https://www.aqr.com/Insights/Research/Journal-Article/Stock-Bond-Correlations",
            "relevance": "Empirical confirmation of the 2022 correlation regime shift.",
            "verified": True,
        }
    ],
    "potential_issues": [
        "Only 282 monthly observations may be borderline for tier-1 power on regime-conditional tests.",
    ],
    "verified_sources": [
        {"title": "AQR Research", "url": "https://www.aqr.com/Insights/Research/Journal-Article/Stock-Bond-Correlations", "verified": True}
    ],
    "deliverable_type": "midpoint",
}

MOCK_ADVISOR_VERIFY = {
    "supporting_evidence": [
        {
            "title": "Equity-Bond Correlations and Inflation Regimes",
            "url": "https://www.nber.org/papers/example",
            "summary": "Documents positive equity-bond correlation in high-inflation regimes — consistent with the 2022 observation.",
        }
    ],
    "contradicting_evidence": [],
    "verdict": "plausible",
    "reasoning": "The magnitude reported (+0.48 in 2022 vs -0.31 pre-2022) is within the range reported in academic literature for rate-hiking cycles.",
    "verified_sources": [
        {"title": "NBER Working Paper", "url": "https://www.nber.org/papers/example", "verified": True}
    ],
}

MOCK_ADVISOR_CITATIONS = {
    "citations": [
        {
            "title": "Determinants of Portfolio Performance",
            "authors": "Brinson, G. P., Hood, L. R., & Beebower, G. L.",
            "year": 1986,
            "url": "https://www.cfainstitute.org/en/research/financial-analysts-journal/1986/determinants-of-portfolio-performance",
            "relevance": "Foundational attribution framework — allocation vs selection.",
            "verified": True,
        }
    ],
    "verified_sources": [
        {"title": "CFA Institute", "url": "https://www.cfainstitute.org/en/research/financial-analysts-journal/1986/determinants-of-portfolio-performance", "verified": True}
    ],
}


def is_test_environment() -> bool:
    """Returns True when running inside pytest so endpoints can return mocks."""
    return os.getenv("ENVIRONMENT", "").lower() == "test"
