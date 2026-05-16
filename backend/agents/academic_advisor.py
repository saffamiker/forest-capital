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

# web_fetch lets the model retrieve the actual page text behind a URL it
# discovered via web_search. The excerpt requirement (CLAUDE.md addendum)
# is enforced here: every citation must be backed by a successful fetch,
# and the 2-3 sentence excerpt the model writes must come from the fetched
# page. We cap at 5 fetches per call — enough to back the typical 3-5
# citations the advisor returns, without inviting runaway tool use.
_WEB_FETCH_TOOL = {
    "type": "web_fetch_20250910",
    "name": "web_fetch",
    "max_uses": 5,
}

# Sentinel shown on the frontend when a citation has no excerpt — either
# because the fetch failed (paywall, 404, robots.txt) or because the
# model emitted a citation it didn't actually fetch. Either case fails
# the integrity gate; the user is directed to verify directly.
EXCERPT_UNAVAILABLE = None

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
  1. ALWAYS use web_search to verify a source exists before citing it.
  2. THEN use web_fetch on the URL of every source you intend to cite.
     The fetch retrieves the actual page text — you must read it before
     including the source in your citations list.
  3. From the fetched page, write a 2-3 sentence excerpt that
     specifically corroborates the finding you are citing it for.
     The excerpt must be drawn from the fetched page text — never
     from your training memory. Quote or paraphrase tightly enough
     that a reader can locate the supporting passage in the source.
  4. If web_fetch fails (paywall, 404, robots.txt block, timeout),
     OMIT the citation entirely. Do not write an excerpt from memory.
     It is better to return three verified citations than five with
     fabricated excerpts.
  5. NEVER cite a paper you cannot verify via web_search AND web_fetch.
  6. Reputable sources only: Fed, IMF, BIS, AQR, NBER, peer-reviewed
     journals, major central banks. No blogs, LinkedIn, unverified
     preprints.

GRADE AWARENESS:
  Prioritise feedback by grade weight. For the midpoint, focus on framing
  and preliminary findings. For the final, focus on completeness and
  statistical rigour.

OUTPUT FORMAT:
  Respond in JSON with four top-level keys:
    key_findings     (list[str])
    guidance         (list[str])
    citations        (list of objects with keys: title, url, relevance, excerpt, verified=true)
    potential_issues (list[str])
  The excerpt field is REQUIRED for every citation and must be a direct
  2-3 sentence passage from the page returned by web_fetch. If you did
  not fetch the page, do not include the citation.

{GLOBAL_AGENT_RULE}

{SCOPE_ENFORCEMENT}"""


def _extract_text_sources_and_fetches(
    response: anthropic.types.Message,
) -> tuple[str, list[dict[str, Any]], set[str]]:
    """
    Pulls the final-turn text, URLs surfaced by web_search, and the set of
    URLs successfully retrieved by web_fetch.

    Why we walk the content list ourselves rather than using message.content[0]:
    when web_search + web_fetch run, the SDK returns a mixed list —
    server_tool_use blocks, web_search_tool_result blocks,
    web_fetch_tool_result blocks, and text blocks all interleaved. The
    final answer is the LAST text block.

    fetched_urls is the integrity gate for excerpts: a citation gets a
    rendered excerpt only when its URL is in this set. URLs whose fetch
    errored (paywall, 404, timeout) never enter the set — the
    web_fetch_tool_result_error path is detected and skipped. The model
    can still emit an excerpt in its JSON, but _filter_to_verified
    strips it whenever the URL was not actually fetched.
    """
    final_text = ""
    sources: list[dict[str, Any]] = []
    fetched_urls: set[str] = set()

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

        elif block_type == "web_fetch_tool_result":
            # A successful fetch carries a web_fetch_result inner block
            # with the fetched URL and a document with the page text.
            # A failed fetch carries a web_fetch_tool_result_error block
            # — we deliberately skip those so the URL never enters
            # fetched_urls, which the excerpt gate depends on.
            content = getattr(block, "content", None)
            if content is None:
                continue
            inner_type = getattr(content, "type", None)
            if inner_type == "web_fetch_result":
                url = getattr(content, "url", "")
                if url:
                    fetched_urls.add(_normalise_url(url))
            # web_fetch_tool_result_error path: intentionally skipped.
            # The error code is in content.error_code if needed for
            # logging, but the integrity contract is binary: in or out.

    return final_text, sources, fetched_urls


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


def _call_advisor_with_web_tools(
    user_message: str,
    max_tokens: int = MAX_OUTPUT_TOKENS,
    enable_fetch: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]], set[str], dict[str, Any]]:
    """
    Single entry point for all advisor calls.

    Returns (parsed_json, verified_sources, fetched_urls, usage_metadata).

    verified_sources reflects URLs returned by the web_search tool.
    fetched_urls is the set of URLs the model successfully retrieved via
    web_fetch — used by _filter_to_verified to gate excerpt rendering.

    enable_fetch defaults to True for analyse/citations, where the
    excerpt contract applies. The verify-finding endpoint passes False:
    that endpoint returns supporting/contradicting evidence with one-
    line summaries, not full per-citation excerpts.
    """
    client = get_anthropic_client()
    tools: list[dict[str, Any]] = [_WEB_SEARCH_TOOL]
    if enable_fetch:
        tools.append(_WEB_FETCH_TOOL)

    # Inject any uploaded academic-context documents (midpoint rubric,
    # final-presentation requirements) so the advisor's feedback is anchored
    # to the actual evaluation criteria. This agent builds its own
    # web-search-tool call rather than going through call_claude, so it
    # injects here. Fail-open.
    system_prompt = _SYSTEM_PROMPT
    try:
        from tools.academic_context import inject_academic_context
        system_prompt = inject_academic_context(_SYSTEM_PROMPT)
    except Exception:  # noqa: BLE001
        pass

    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        tools=tools,
        messages=[{"role": "user", "content": user_message}],
    )

    text, sources, fetched_urls = _extract_text_sources_and_fetches(response)
    parsed = _parse_json_response(text)

    usage = {
        "input_tokens":  getattr(response.usage, "input_tokens", 0),
        "output_tokens": getattr(response.usage, "output_tokens", 0),
        "model":         SONNET_MODEL,
        "n_searches":    len(sources),
        "n_fetches":     len(fetched_urls),
    }
    return parsed, sources, fetched_urls, usage


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
            parsed, sources, fetched_urls, usage = _call_advisor_with_web_tools(user_message)
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
            "citations":         _filter_to_verified(
                parsed.get("citations", []),
                sources,
                fetched_urls,
            ),
            "potential_issues":  parsed.get("potential_issues", []),
            "verified_sources":  sources,
            "deliverable_type":  deliverable_type,
        }

        _log_advisor_call(
            method="analyse",
            deliverable=deliverable_type,
            usage=usage,
            n_citations_verified=len(result["citations"]),
            n_excerpts=sum(1 for c in result["citations"] if c.get("excerpt")),
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
            # verify-finding doesn't need excerpts — its evidence carries
            # short summary text already. Keeping web_fetch off here saves
            # tokens and keeps the call snappier for a sanity-check loop.
            parsed, sources, fetched_urls, usage = _call_advisor_with_web_tools(
                user_message, enable_fetch=False,
            )
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
            "supporting_evidence":    _filter_to_verified(
                parsed.get("supporting_evidence", []), sources, fetched_urls,
            ),
            "contradicting_evidence": _filter_to_verified(
                parsed.get("contradicting_evidence", []), sources, fetched_urls,
            ),
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
            parsed, sources, fetched_urls, usage = _call_advisor_with_web_tools(user_message)
        except Exception as exc:
            log.error("advisor_citations_error", error=str(exc), finding=finding[:80])
            return {"citations": [], "verified_sources": []}

        verified_citations = _filter_to_verified(
            parsed.get("citations", []), sources, fetched_urls,
        )
        _log_advisor_call(
            method="citations",
            usage=usage,
            n_citations_verified=len(verified_citations),
            n_excerpts=sum(1 for c in verified_citations if c.get("excerpt")),
            n_citations_requested=capped,
        )
        return {
            "citations":        verified_citations[:capped],
            "verified_sources": sources,
        }


def _filter_to_verified(
    citations: list[Any],
    verified_sources: list[dict[str, Any]],
    fetched_urls: set[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Drops any citation whose URL did not appear in the web_search results.
    Gates the `excerpt` field on a successful web_fetch of the same URL.

    The two-gate integrity contract:

      Gate 1 — web_search verification (URL existence):
        Even if the model invents a plausible-looking URL, it cannot
        survive this filter unless web_search actually returned it.
        URLs are compared case-insensitively with trailing slashes
        stripped — a common source of spurious mismatches.

      Gate 2 — web_fetch verification (excerpt provenance):
        The model emits an `excerpt` field per citation when the system
        prompt asks for one. We only keep the excerpt if the URL is in
        fetched_urls — meaning Anthropic's web_fetch tool retrieved the
        actual page. If not, excerpt is forced to None so the frontend
        shows "Excerpt unavailable — click to verify directly". The
        model cannot bypass this by writing an excerpt from memory: no
        fetch, no excerpt.

    fetched_urls=None disables Gate 2 entirely (used by verify-finding,
    which carries one-line summaries rather than passage excerpts).
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
        if url not in verified_urls:
            continue

        # Gate 2 — excerpt provenance. When fetched_urls is provided,
        # we enforce: excerpt is present only if the URL was fetched.
        # Otherwise the frontend falls back to "Excerpt unavailable".
        emitted_excerpt = c.get("excerpt")
        if fetched_urls is None:
            # Caller opted out of the excerpt gate (verify-finding).
            excerpt_value: str | None = (
                emitted_excerpt if isinstance(emitted_excerpt, str) and emitted_excerpt.strip()
                else None
            )
        elif url in fetched_urls and isinstance(emitted_excerpt, str) and emitted_excerpt.strip():
            # Both gates passed: keep the model's excerpt (it generated
            # it from the fetched page).
            excerpt_value = emitted_excerpt.strip()
        else:
            # Either the fetch never landed (paywall/error/timeout) or
            # the model didn't emit an excerpt despite being asked.
            excerpt_value = None

        # Force verified=true even if the model emitted otherwise, and
        # always emit the excerpt field so the frontend can rely on its
        # presence (None signals fallback text).
        out.append({**c, "verified": True, "excerpt": excerpt_value})

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
            # Excerpt mirrors the contract: short, drawn from the fetched
            # page, corroborates the specific finding. In production this
            # comes from web_fetch; in tests it's a fixture.
            "excerpt": "The previously negative stock-bond correlation flipped positive in 2022 as both asset classes repriced lower in response to coordinated central bank tightening. The traditional 60/40 portfolio offered little diversification benefit during the worst drawdown.",
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
            "excerpt": "Investment policy — the long-term mix of asset classes — explains over 90 percent of the variance of returns for a typical pension fund. Market timing and security selection contribute far less to total performance variation than the strategic asset allocation decision.",
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
