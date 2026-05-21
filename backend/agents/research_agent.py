"""
agents/research_agent.py

Macro Market Research Agent — Claude Sonnet (claude-sonnet-4-6) plus the
Anthropic server-side web_search + web_fetch tools.

FEATURE 2 (May 21 2026). The historical backtest covers 2002-2025; the
council's analytical agents reason against that history. Without a
research layer they cannot account for what happened *yesterday* — a
recent Fed pivot, an unexpected CPI print, a yield curve inversion. This
agent bridges the gap. It runs on a daily cadence, identifies recent
macro events affecting equity / IG / HY markets, and produces a
structured digest the council and academic_review prompts inject as a
CURRENT MACRO CONDITIONS block.

  Daily scheduler / persistence / context injection live in Commits 2-3:
    tools/research_engine.py   — orchestrator + concurrency lock
    tools/macro_context.py     — get_latest_macro_digest + inject helper
  Frontend visibility in Commit 4. Tests + docs in Commit 5.

This module is generate-only. It returns the parsed digest, the
verified-source URL list (mirroring the academic_advisor citation
integrity pattern), and the usage metadata. Persistence is the engine
layer's responsibility — the agent itself is pure compute, mocked
deterministically under pytest.

DIGEST SHAPE — every successful generation returns:
  {
    "summary_text":       str    # 2-3 sentence overview of the week
    "key_signals":        list[dict]
        Each dict: {category, signal, implication, source_url}
        category ∈ {monetary_policy, inflation, growth, rates,
                    credit, volatility, geopolitical, other}
    "regime_implication": str    # which regime (bull/bear/transition)
                                 # the signals collectively suggest, with
                                 # the mechanism
    "citation_urls":      list[str]  # every source_url from key_signals,
                                     # de-duplicated and verified
  }

CITATION INTEGRITY. Every URL in citation_urls must be a URL the
web_search tool actually returned during the call. The agent prompt
forbids fabricated citations; the verified_sources list returned from
the Anthropic SDK is the integrity gate. A URL the model wrote in its
JSON output that web_search did not surface is dropped before the
digest is persisted.

FAIL-OPEN. A web_search failure / SDK error / JSON parse failure returns
a defaulted shape ("Unable to generate digest" summary, empty signals,
empty citations). The engine layer (Commit 2) persists the failed run
with status='failed' so the dashboard never goes blank.
"""
from __future__ import annotations

import json
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


# Anthropic server-side web_search tool. Same shape as the academic
# advisor uses; max_uses kept low (5) because the agent should issue a
# handful of focused queries — Fed news, CPI, yield curve, VIX, credit
# spreads — not browse aimlessly.
_WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 5,
}

# Server-side web_fetch lets the model retrieve the actual page text
# behind a URL it discovered. Capped at 4 fetches — enough for the
# agent to back its top 3-4 key_signals with grounded reads of the
# source articles.
_WEB_FETCH_TOOL = {
    "type": "web_fetch_20250910",
    "name": "web_fetch",
    "max_uses": 4,
}


_SYSTEM_PROMPT = f"""You are a macro market research analyst for a Forest \
Capital portfolio intelligence platform built by graduate students at the \
McColl School of Business, Queens University Charlotte. The platform \
analyses equity / investment-grade-bond / high-yield-bond strategies \
over the period 2002-2025 and recommends asset allocations.

YOUR ONE JOB:
Produce a daily macro digest the council's analytical agents read \
BEFORE reasoning. The digest tells them what has happened in the LAST \
SEVEN DAYS in the macroeconomic environment surrounding equities, \
investment-grade bonds, and high-yield bonds — so their recommendations \
reflect current conditions, not just historical patterns.

WHAT TO COVER (each category should produce 0-2 key signals, depending \
on what actually happened — never invent a signal to fill a slot):
  monetary_policy — Fed announcements, FOMC minutes, regional Fed \
    speeches, balance-sheet decisions.
  inflation        — CPI / PCE prints, Fed inflation projections, \
    inflation-expectation surveys.
  growth           — GDP nowcasts, PMI prints, jobs reports, \
    consumption data.
  rates            — Treasury yield moves, 10Y-2Y curve changes, \
    issuance auctions.
  credit           — IG/HY spread moves, default rate reports, \
    significant rating actions.
  volatility       — VIX moves > 3pts, MOVE index spikes, equity \
    drawdowns > 3%.
  geopolitical     — events with documented market impact (avoid \
    speculation; require visible price reaction).
  other            — anything materially affecting equity/IG/HY \
    pricing that does not fit above.

CITATION INTEGRITY — NON-NEGOTIABLE:
ALWAYS use the web_search tool to find sources. NEVER cite a URL the \
tool did not return to you. For every signal you report, the \
source_url MUST be a URL web_search surfaced during this call. \
Fabricating a citation is a hallucination this platform's QA layer \
detects and fails on. Prefer reputable sources only: Federal Reserve, \
BLS, BEA, IMF, BIS, AQR, NBER, major central banks, Reuters, \
Bloomberg, FT, WSJ. No blogs, no anonymous Substacks, no LinkedIn \
posts. If you cannot verify a story, omit it.

WHAT NOT TO DO:
  - Do not predict. Report what HAPPENED and what it IMPLIES; never \
    forecast tomorrow's prices.
  - Do not invent signals. Three high-quality signals beat ten \
    speculative ones.
  - Do not give portfolio advice. The council does that. You supply \
    facts and short interpretive notes.
  - Do not cite anything older than 7 days unless it is the original \
    source of a still-developing story.

RESPONSE FORMAT — return ONLY valid JSON, no preamble:
{{
  "summary_text": "2-3 sentences naming the dominant theme of the \
week and what it means for diversified portfolios.",
  "key_signals": [
    {{
      "category": "monetary_policy",
      "signal":   "Concrete sentence with the specific number/event.",
      "implication": "One sentence on what it implies for equity / IG / \
HY exposure.",
      "source_url": "https://..."
    }}
  ],
  "regime_implication": "Single paragraph naming which regime — bull, \
bear, or transition — the signals collectively support, with the \
mechanism."
}}

{GLOBAL_AGENT_RULE}

{SCOPE_ENFORCEMENT}"""


def _extract_text_sources_and_fetches(
    response: anthropic.types.Message,
) -> tuple[str, list[dict[str, Any]], set[str]]:
    """
    Pulls the final-turn text, the URLs surfaced by web_search, and the
    set of URLs successfully fetched by web_fetch.

    Mirrors academic_advisor._extract_text_sources_and_fetches — kept as
    a private copy here rather than imported so a future refactor of
    the advisor's extraction logic does not silently change the research
    agent's URL-integrity gate.
    """
    final_text = ""
    sources: list[dict[str, Any]] = []
    fetched_urls: set[str] = set()

    for block in response.content:
        block_type = getattr(block, "type", None)

        if block_type == "text":
            # Last text block wins — earlier text blocks are reasoning
            # interleaved with tool calls.
            final_text = block.text

        elif block_type == "web_search_tool_result":
            content = getattr(block, "content", None)
            if isinstance(content, list):
                for item in content:
                    if getattr(item, "type", None) == "web_search_result":
                        url = getattr(item, "url", "")
                        if url:
                            sources.append({
                                "title": getattr(item, "title", ""),
                                "url":   url,
                            })

        elif block_type == "web_fetch_tool_result":
            content = getattr(block, "content", None)
            if content is None:
                continue
            inner_type = getattr(content, "type", None)
            if inner_type == "web_fetch_result":
                url = getattr(content, "url", "")
                if url:
                    fetched_urls.add(url)

    return final_text, sources, fetched_urls


def _parse_digest_json(text: str) -> dict[str, Any]:
    """
    Extracts the digest JSON from the model's final text block. Sonnet
    occasionally wraps the JSON in a ```json code fence or surrounding
    prose; we strip both. On any parse failure we return a defaulted
    empty digest so the engine layer's `status='failed'` path takes
    over rather than the call raising.
    """
    cleaned = (text or "").strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        parsed = json.loads(cleaned[start: end + 1])
    except json.JSONDecodeError as exc:
        log.warning("research_agent_json_parse_failed",
                    error=str(exc), text_head=cleaned[:200])
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _filter_to_verified_signals(
    parsed: dict[str, Any], verified_urls: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Strips every key_signal whose source_url is NOT in the
    verified_urls set (the URLs web_search actually returned during
    this call). Returns (verified_signals, deduped_citation_urls).

    Citation integrity gate — the model can write any URL it likes in
    its JSON; only URLs that came from web_search survive. A signal
    without a verified URL is dropped entirely (a research signal
    without a source is unfalsifiable).
    """
    signals_raw = parsed.get("key_signals") or []
    if not isinstance(signals_raw, list):
        return [], []

    verified_signals: list[dict[str, Any]] = []
    seen_urls: list[str] = []
    for sig in signals_raw:
        if not isinstance(sig, dict):
            continue
        url = str(sig.get("source_url") or "").strip()
        if not url or url not in verified_urls:
            log.info("research_signal_dropped_unverified_url",
                     url=url[:80], category=sig.get("category"))
            continue
        verified_signals.append({
            "category":    str(sig.get("category") or "other"),
            "signal":      str(sig.get("signal") or "").strip(),
            "implication": str(sig.get("implication") or "").strip(),
            "source_url":  url,
        })
        if url not in seen_urls:
            seen_urls.append(url)

    return verified_signals, seen_urls


def generate_digest(
    *, max_tokens: int = MAX_OUTPUT_TOKENS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Runs the macro research agent once and returns (digest, usage).

    digest shape on success:
      {summary_text, key_signals, regime_implication, citation_urls}
    digest shape on failure (web_search outage, SDK error, parse
    failure):
      {summary_text: "...could not generate...", key_signals: [],
       regime_implication: "", citation_urls: [], error: str}
    The engine layer (Commit 2) maps the error key onto
    macro_research_digests.status='failed'.

    Synchronous (the Anthropic SDK call is synchronous); callers run it
    in asyncio.to_thread when on the event loop.
    """
    client = get_anthropic_client()
    user_message = (
        "Produce today's macro digest covering the last seven days. "
        "Issue 3-5 web_search queries across the categories above, "
        "back the top signals with web_fetch, then return the digest "
        "as JSON exactly matching the response format."
    )

    try:
        response = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            tools=[_WEB_SEARCH_TOOL, _WEB_FETCH_TOOL],
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("research_agent_sdk_call_failed", error=str(exc))
        return (
            _failure_digest(f"SDK error: {exc}"),
            {"input_tokens": 0, "output_tokens": 0, "model": SONNET_MODEL,
             "n_searches": 0, "n_fetches": 0},
        )

    text, sources, fetched_urls = _extract_text_sources_and_fetches(response)
    verified_urls = {s["url"] for s in sources if s.get("url")}
    parsed = _parse_digest_json(text)
    if not parsed:
        log.warning("research_agent_empty_parse",
                    n_searches=len(sources), n_fetches=len(fetched_urls))
        return (
            _failure_digest("model returned no parseable JSON"),
            _usage_meta(response, sources, fetched_urls),
        )

    signals, citation_urls = _filter_to_verified_signals(parsed, verified_urls)
    digest = {
        "summary_text":       str(parsed.get("summary_text") or "").strip(),
        "key_signals":        signals,
        "regime_implication": str(parsed.get("regime_implication") or "").strip(),
        "citation_urls":      citation_urls,
        "raw_response":       text,
    }
    log.info("research_digest_generated",
             n_signals=len(signals),
             n_citations=len(citation_urls),
             n_searches=len(sources),
             n_fetches=len(fetched_urls))
    return digest, _usage_meta(response, sources, fetched_urls)


def _failure_digest(error: str) -> dict[str, Any]:
    """The defaulted shape returned when the agent cannot produce a real
    digest. The engine layer treats `error` truthy as a signal to
    record status='failed' on the persisted row."""
    return {
        "summary_text": (
            "A current macro digest could not be generated. Agents will "
            "reason from historical context only this run."
        ),
        "key_signals":        [],
        "regime_implication": "",
        "citation_urls":      [],
        "raw_response":       "",
        "error":              error,
    }


def _usage_meta(
    response: anthropic.types.Message,
    sources: list[dict[str, Any]],
    fetched_urls: set[str],
) -> dict[str, Any]:
    """Token usage + tool-use counts for the engine's cost-tracking
    write. Format mirrors what record_usage stores so a future
    Cost-by-Agent breakdown sees research_agent like every other
    Sonnet caller."""
    return {
        "input_tokens":  getattr(response.usage, "input_tokens", 0),
        "output_tokens": getattr(response.usage, "output_tokens", 0),
        "model":         SONNET_MODEL,
        "n_searches":    len(sources),
        "n_fetches":     len(fetched_urls),
    }
