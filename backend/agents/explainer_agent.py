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


def _inject_academic(system_prompt: str) -> str:
    """Append uploaded academic-context documents to a Grok system prompt.
    The Haiku fallback path goes through call_claude, which injects on its
    own — so each provider path injects exactly once. Fail-open."""
    try:
        from tools.academic_context import inject_academic_context
        return inject_academic_context(system_prompt)
    except Exception as exc:  # noqa: BLE001
        log.warning("academic_context_inject_failed", error=str(exc))
        return system_prompt


# Backwards-compatible exports — older tests import these for assertion
# purposes. The runtime path now routes through agents/_xai_config so the
# Explainer transparently supports both direct xAI (`xai-...` keys against
# api.x.ai) and OpenRouter (`sk-or-...` keys against openrouter.ai).
XAI_API_URL = "https://api.x.ai/v1/chat/completions"
XAI_MODEL = "grok-4.3"
XAI_TIMEOUT_SECONDS = 30.0

from agents._xai_config import build_headers, resolve_xai_config  # noqa: E402

# Haiku fallback default when the Explainer can't reach xAI. Bumped from
# 800 → 2000 after production traces showed truncated JSON responses
# (max_tokens hit mid-string, breaking json.loads downstream). 2000 is
# safe for all five Explainer methods — the longest legitimate response
# is the 30-item QA explanation, which sits at ~1400 tokens.
HAIKU_FALLBACK_MAX_TOKENS = 2000

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
    Single-shot Grok call returning the plain-text content. Raises on any
    non-2xx response so _call_llm() can catch and fall back to Haiku.

    Provider resolution: the request routes through resolve_xai_config()
    which inspects XAI_API_KEY's prefix and picks the right base URL +
    model. `sk-or-...` keys go to OpenRouter as `x-ai/grok-4.3`;
    `xai-...` keys go to api.x.ai as `grok-4.3`. XAI_BASE_URL +
    XAI_MODEL env vars override the auto-detection. Body shape is
    identical for both providers — OpenAI chat-completions spec.

    On any 4xx the response body is logged at
    `explainer_grok_http_error.body_preview[:500]` so future provider
    drift surfaces in Render logs instead of a bare status code line.
    """
    # api_key is forwarded for backwards-compat with existing tests that
    # patch _call_grok directly; the real value comes from resolve_xai_config
    # so XAI_BASE_URL / XAI_MODEL overrides land here automatically.
    xai = resolve_xai_config()
    if xai is None:
        # Fall back to the literal api_key + canonical xAI endpoint when
        # the resolver finds nothing in env. Preserves the test contract
        # of "call _call_grok with an explicit key and it works".
        from agents._xai_config import _DIRECT_XAI_BASE_URL, _DIRECT_XAI_MODEL
        chat_url = f"{_DIRECT_XAI_BASE_URL}/chat/completions"
        model = _DIRECT_XAI_MODEL
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        provider = "direct_xai"
    else:
        chat_url = xai.chat_url
        model = xai.model
        headers = build_headers(xai.api_key, xai.provider)
        provider = xai.provider

    with httpx.Client(timeout=XAI_TIMEOUT_SECONDS) as client:
        resp = client.post(
            chat_url,
            headers=headers,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _inject_academic(system_prompt)},
                    {"role": "user", "content": user_message},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.7,
            },
        )
        # Capture the response body on any non-2xx so callers (and the
        # operator) see WHY the provider rejected the request, not just
        # the status code. The body is bounded to 500 chars to keep log
        # lines sane.
        if resp.status_code >= 400:
            log.warning(
                "explainer_grok_http_error",
                status=resp.status_code,
                body_preview=resp.text[:500],
                provider=provider,
                model=model,
            )
        resp.raise_for_status()
        data = resp.json()
    # Both providers return the OpenAI chat-completions shape:
    # choices[0].message.content
    return data["choices"][0]["message"]["content"]


def _call_llm(system_prompt: str, user_message: str, max_tokens: int = 800) -> str:
    """
    Routes every Explainer LLM call through here. Grok is tried first
    when XAI_API_KEY is set (direct xAI or OpenRouter, auto-detected);
    Haiku is the silent fallback when the key is unset or the Grok call
    fails (timeout, 5xx, malformed response). Callers see plain text
    either way and don't branch on which model produced it.

    Haiku fallback uses a higher max_tokens cap (HAIKU_FALLBACK_MAX_TOKENS,
    2000) regardless of what the Grok caller requested — production traces
    showed Haiku returning JSON truncated mid-string when the cap was 800,
    breaking every downstream json.loads. The cap is safe to raise here
    because the fallback path is rare (Grok available ~99% of the time)
    and the extra tokens only get charged on fallback.
    """
    api_key = os.getenv("XAI_API_KEY", "")
    if api_key:
        try:
            text = _call_grok(api_key, system_prompt, user_message, max_tokens)
            log.info("explainer_grok_completed", chars=len(text))
            return text
        except Exception as exc:
            # Common reasons to fall back: rate limit, brief provider
            # outage, response shape change. Logged at warning level so
            # it shows up in the AI Usage Log without flooding the error feed.
            log.warning("explainer_grok_fallback_to_haiku", error=str(exc))

    # Haiku fallback — generous token cap so JSON responses complete fully.
    fallback_tokens = max(max_tokens, HAIKU_FALLBACK_MAX_TOKENS)
    try:
        return call_claude(HAIKU_MODEL, system_prompt, user_message, fallback_tokens)
    except Exception as exc:
        # Both providers are down. Logged symmetrically with the Grok path
        # above so the failure surface is consistent; re-raised so the
        # caller's per-method try/except handles it exactly as before.
        log.error("explainer_haiku_fallback_failed", error=str(exc))
        raise


def _repair_common_json_mistakes(text: str) -> str:
    """
    Best-effort repair of the JSON malformations Grok-3-mini emits when
    routed via OpenRouter. The three failure modes we've seen in Render
    logs:

      1. Missing comma between two adjacent key/value pairs on
         consecutive lines:
             "foo": "bar"
             "next": ...
         Grok writes the second key on a new line without the trailing
         comma after "bar". Pattern: a closing quote / number / bracket
         followed by whitespace + newline + whitespace + an opening
         quote. We insert the comma.

      2. Trailing comma before } or ]:
             { "foo": "bar", }
         strict json.loads rejects this; Python json5/demjson tolerate
         it. We strip the offending comma.

      3. Single quotes around keys or string values (Python-style dict
         literal sneaking in). Replaced with double quotes for the
         narrow case of `'key':` or `: 'value'` — leaves quotes inside
         strings alone.

    Pure-stdlib (regex) implementation so we don't pull in a new dep.
    Each pattern is independent — if all three repairs leave the text
    still unparseable, the caller falls back silently.
    """
    import re as _re

    # 1. Missing comma between two key/value pairs.
    # Match: ending quote/digit/brace/bracket, optional whitespace,
    # newline, whitespace, opening double quote of next key.
    text = _re.sub(
        r'(["\d\]\}])\s*\n(\s*)(")',
        r"\1,\n\2\3",
        text,
    )

    # 2. Trailing comma before } or ] (with optional whitespace between).
    text = _re.sub(r",\s*([}\]])", r"\1", text)

    # 3. Single-quoted keys → double-quoted. Only at the start of a key
    # position (after { or , or whitespace at start). Conservative —
    # doesn't touch single quotes inside legitimately-quoted strings.
    text = _re.sub(r"([{,]\s*)'([^']+?)'(\s*:)", r'\1"\2"\3', text)

    return text


def _safe_json_parse(response: str, fallback: Any) -> Any:
    """
    Defensive JSON parser used by every explain_* method.

    Tolerates the failure modes we see in production:
      1. Truncated JSON (max_tokens hit mid-string) → JSONDecodeError
      2. Markdown code fences around the JSON → stripped before parse
      3. Leading/trailing prose around the JSON → first {...} extracted
      4. Missing commas / trailing commas / single-quoted keys (typical
         OpenRouter Grok-3-mini output) → repaired by regex before parse

    Returns `fallback` on any parse failure rather than raising — keeps
    the Explainer endpoints always returning a valid (possibly empty)
    response. We deliberately do NOT log a warning on parse failure
    because the Explainer fires on every chart hover; a model that emits
    malformed JSON would flood Render logs with one warning per request.
    The fallback dict is the operator's signal that something's off.
    """
    if not isinstance(response, str) or not response.strip():
        return fallback

    cleaned = response.strip()

    # Strip ```json or ``` fences.
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1] if "```" in cleaned[3:] else cleaned[3:]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    # Try a direct parse first (handles the happy path).
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Truncated/prose-wrapped fallback: extract the first balanced JSON
    # object from the first { to the LAST }. The "last }" catches the
    # common case where the model finished one nested struct but the
    # outer object was cut off mid-stream.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = cleaned[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        # Repair common Grok / OpenRouter malformations and retry.
        repaired = _repair_common_json_mistakes(candidate)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    return fallback


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
            parsed = _safe_json_parse(response, fallback=None)
            if parsed is None:
                # Parsing failed even after the defensive extract — fall
                # through to the curated fallback rather than return an
                # empty dict that would look like "no explanations exist".
                return self._fallback_terms(significant)
            return parsed
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

        param_fallback = {
            "parameter": parameter,
            "value": str(value),
            "hover": f"{parameter} is currently set to {value}.",
            "what": f"This parameter controls {parameter} in the analysis.",
            "why": "See CLAUDE.md config for the rationale behind this value.",
            "effect_now": "Effect cannot be computed — Explainer temporarily unavailable.",
            "what_if": "Effect cannot be computed — Explainer temporarily unavailable.",
        }
        try:
            response = _call_llm(_SYSTEM_PROMPT, user_message, max_tokens=512)
            return _safe_json_parse(response, fallback=param_fallback)
        except Exception as exc:
            log.error("explainer_parameter_error", parameter=parameter, error=str(exc))
            return param_fallback

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

        persona_fallback = {
            "plain_english": f"The {agent_name} analyses portfolio strategies from its specialist perspective.",
            "design_decisions": "See system prompt tab for the full configuration.",
            "this_session": findings.get("summary", "No summary available."),
        }
        try:
            response = _call_llm(_SYSTEM_PROMPT, user_message, max_tokens=512)
            return _safe_json_parse(response, fallback=persona_fallback)
        except Exception as exc:
            log.error("explainer_persona_error", agent=agent_name, error=str(exc))
            return persona_fallback

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
            parsed = _safe_json_parse(response, fallback=None)
            if parsed is None:
                return self._fallback_chart(chart_id, chart_type, significant)
            return parsed
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
            # QA explanations cover up to 30 checklist items at once; bump
            # the per-call max_tokens above the 800 default so the JSON
            # response doesn't get truncated mid-string. Production traces
            # showed this exact failure when the Haiku fallback fired.
            response = _call_llm(_SYSTEM_PROMPT, user_message, max_tokens=2000)
            return _safe_json_parse(response, fallback={})
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


# ── Inline metric explainer — streamed ────────────────────────────────────────

def _metric_explain_prompt(metric: str, current_value: Any) -> str:
    """
    Builds the user message for POST /api/council/explain — the InfoIcon
    (ⓘ) click path. Deliberately tightly scoped: the InfoIcon answers
    "what does this metric mean?", so the prompt is capped at 150 words
    and three parts. The deeper, contextual explanation belongs to the
    separate Data Explain feature (POST /api/council/explain-data).
    """
    value_str = "not shown" if current_value in (None, "") else str(current_value)
    return (
        f"Explain the {metric} metric to a senior investment professional.\n\n"
        f"Current value: {value_str}\n\n"
        f"Cover exactly three things, in order:\n"
        f"1. What this metric or chart measures — one short paragraph.\n"
        f"2. How to interpret the current value shown ({value_str}) — one "
        f"short paragraph: is it good, concerning, or typical?\n"
        f"3. One sentence connecting it to the project thesis (the 2022 "
        f"equity-bond correlation regime break).\n\n"
        f"Hard limits: maximum 150 words total. Plain English. No extended "
        f"academic framing. Do not discuss what the team should do next."
    )


def _data_explain_prompt(metric: str, current_value: Any, context: Any) -> str:
    """
    Builds the user message for POST /api/council/explain-data — the
    "Explain this data" (✨) click path.

    Unlike the InfoIcon, which explains what a metric *means*, this
    explains what the *specific values currently on screen* mean together.
    It is allowed the academic framing the InfoIcon prompt forbids.
    """
    value_str = "not shown" if current_value in (None, "") else str(current_value)
    context_str = "" if context in (None, "") else str(context)
    is_strategy = "strategy" in f"{metric} {context_str}".lower()

    lines = [
        f"Explain what these specific on-screen values mean — not what the "
        f"metric type means in general — for: {metric}.",
        "",
        f"Values currently shown: {value_str}",
    ]
    if context_str:
        lines.append(f"Context: {context_str}")
    lines.append("")
    if is_strategy:
        lines += [
            "Cover, grounded in the numbers above:",
            "1. What these numbers mean together as a risk-return profile.",
            "2. How this strategy compares to the cohort (relative "
            "positioning).",
            "3. What the CV score and Tier ranking imply.",
            "4. What this means for the 2022 correlation regime-break thesis.",
            "5. One sentence on whether this strategy is worth highlighting "
            "in the midpoint paper.",
        ]
    else:
        lines += [
            "Cover, grounded in the values above:",
            "1. What the viewer is looking at right now — the key statistics "
            "and what stands out.",
            "2. How to read the notable findings (date ranges, pre/post-2022 "
            "values, outliers).",
            "3. What this view implies for the 2022 correlation regime-break "
            "thesis.",
        ]
    lines += [
        "",
        "Be direct and specific, every claim tied to a number above. "
        "Maximum 250 words. Plain English.",
    ]
    return "\n".join(lines)


def _mock_metric_explanation(metric: str) -> list[str]:
    """Deterministic explanation chunks for the test environment / no API key."""
    text = (
        f"[mock explanation — the live explainer model is unavailable in this "
        f"environment] {metric} is a standard portfolio-analysis metric. "
        f"Interpret the value shown against the benchmark, and read it in the "
        f"context of the 2022 equity-bond correlation regime break — the "
        f"central question of whether diversification still holds."
    )
    words = text.split(" ")
    return [" ".join(words[i:i + 8]) + " " for i in range(0, len(words), 8)]


def _mock_data_explanation(metric: str) -> list[str]:
    """Deterministic Data Explain chunks for the test environment / no key."""
    text = (
        f"[mock data explanation — the live explainer model is unavailable "
        f"in this environment] The values shown for {metric} describe a "
        f"specific risk-return profile. Read them together against the "
        f"cohort, and against the 2022 equity-bond correlation regime break "
        f"— the central question of whether diversification still holds."
    )
    words = text.split(" ")
    return [" ".join(words[i:i + 8]) + " " for i in range(0, len(words), 8)]


async def _stream_haiku(user_message: str, mock_chunks: list[str],
                        label: str, max_tokens: int):
    """
    Shared streamer for the explainer's text endpoints. Yields the
    explanation in text chunks via Anthropic Haiku. The Anthropic
    streaming client is synchronous, so the real path runs it in a worker
    thread and pushes chunks onto an asyncio.Queue, keeping the event loop
    free. Test environments and a missing key fall back to mock_chunks.
    """
    import asyncio
    import threading

    if os.getenv("ENVIRONMENT", "development") == "test" \
            or not os.getenv("ANTHROPIC_API_KEY"):
        for chunk in mock_chunks:
            yield chunk
        return

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    sentinel = object()

    def _worker() -> None:
        try:
            from agents.base import get_anthropic_client
            client = get_anthropic_client()
            with client.messages.stream(
                model=HAIKU_MODEL,
                max_tokens=max_tokens,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            ) as stream:
                for text in stream.text_stream:
                    loop.call_soon_threadsafe(queue.put_nowait, text)
        except Exception as exc:  # noqa: BLE001
            log.error("explainer_stream_failed", label=label, error=str(exc))
            loop.call_soon_threadsafe(
                queue.put_nowait,
                "The explainer is temporarily unavailable. The hover tooltip "
                "still describes this metric.")
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, sentinel)

    threading.Thread(target=_worker, daemon=True).start()
    while True:
        item = await queue.get()
        if item is sentinel:
            break
        yield item


async def stream_metric_explanation(metric: str, current_value: Any):
    """
    Async generator yielding a plain-English explanation of one metric in
    text chunks — backs POST /api/council/explain (the InfoIcon click).
    """
    async for chunk in _stream_haiku(
        _metric_explain_prompt(metric, current_value),
        _mock_metric_explanation(metric),
        label=f"metric:{metric}",
        max_tokens=400,
    ):
        yield chunk


async def stream_data_explanation(metric: str, current_value: Any, context: Any):
    """
    Async generator yielding a contextual explanation of the specific
    values currently on screen — backs POST /api/council/explain-data
    (the "Explain this data" click). Allowed the academic framing the
    InfoIcon prompt forbids, and given a larger token budget.
    """
    async for chunk in _stream_haiku(
        _data_explain_prompt(metric, current_value, context),
        _mock_data_explanation(metric),
        label=f"data:{metric}",
        max_tokens=600,
    ):
        yield chunk
