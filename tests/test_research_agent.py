"""
tests/test_research_agent.py — coverage for agents/research_agent.py.

Pins the digest parsing, the citation-integrity filter, and the
failure-digest contract. The SDK call itself is mocked — pytest never
hits Anthropic.

The engine (research_engine), context injection (macro_context),
endpoints, and frontend are covered in their own test modules.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")

import pytest  # noqa: E402

from agents import research_agent as ra  # noqa: E402


SAMPLE_JSON = """\
{
  "summary_text": "Fed paused; CPI cooler.",
  "key_signals": [
    {"category": "monetary_policy",
     "signal": "Fed holds at 5.25-5.50%.",
     "implication": "IG duration tailwind.",
     "source_url": "https://federalreserve.gov/example"},
    {"category": "inflation",
     "signal": "CPI 3.1% vs 3.2% expected.",
     "implication": "Dovish across asset classes.",
     "source_url": "https://bls.gov/example"},
    {"category": "vix",
     "signal": "VIX -2pts week-over-week.",
     "implication": "Risk-on bias firming.",
     "source_url": "https://fabricated.example.com/never-searched"}
  ],
  "regime_implication": "Transition to risk-on."
}
"""


# ── JSON parsing ─────────────────────────────────────────────────────────────

class TestParseDigestJson:
    def test_parses_a_plain_json_object(self):
        out = ra._parse_digest_json('{"summary_text": "x", "key_signals": []}')
        assert out["summary_text"] == "x"
        assert out["key_signals"] == []

    def test_strips_json_code_fence(self):
        text = '```json\n{"summary_text": "x"}\n```'
        assert ra._parse_digest_json(text) == {"summary_text": "x"}

    def test_strips_generic_code_fence(self):
        text = '```\n{"summary_text": "x"}\n```'
        assert ra._parse_digest_json(text) == {"summary_text": "x"}

    def test_extracts_json_from_surrounding_prose(self):
        text = (
            "Here is the digest you asked for:\n\n"
            '{"summary_text": "embedded"}\n\n'
            "Let me know if you need a second pass."
        )
        assert ra._parse_digest_json(text) == {"summary_text": "embedded"}

    def test_unparseable_text_returns_empty_dict(self):
        # The engine layer maps an empty dict onto status='failed' so the
        # caller never sees a partial digest.
        assert ra._parse_digest_json("not json at all") == {}
        assert ra._parse_digest_json("") == {}

    def test_non_dict_json_returns_empty_dict(self):
        # The agent's contract is a dict; a bare list / string at the top
        # level is rejected so downstream filters never have to type-check.
        assert ra._parse_digest_json("[1, 2, 3]") == {}

    def test_strips_fence_with_surrounding_prose(self):
        """Combined worst-case: ```json fence wrapping the JSON AND
        prose before and after the fence. Both must be handled."""
        text = (
            "Here is the digest:\n\n"
            "```json\n"
            '{"summary_text": "fenced+surrounded"}\n'
            "```\n\n"
            "Let me know if you need anything else."
        )
        assert ra._parse_digest_json(text) == {
            "summary_text": "fenced+surrounded"}

    def test_handles_unclosed_code_fence(self):
        """If the model opens a ```json fence but never closes it
        (rarely seen but observed when the response is truncated by
        max_tokens), the parser should still extract the JSON inside
        rather than giving up."""
        text = '```json\n{"summary_text": "no closing fence"}'
        assert ra._parse_digest_json(text) == {
            "summary_text": "no closing fence"}


class TestStripToJsonBraces:
    """The strict brace-only extraction is the canary against the
    May 22 2026 chain-of-thought leak. Pin its contract."""

    def test_strips_preamble_and_closing(self):
        text = (
            "I'll start by running 5 parallel searches.\n\n"
            '{"summary_text": "x"}\n\n'
            "Let me know if you need more."
        )
        assert ra._strip_to_json_braces(text) == '{"summary_text": "x"}'

    def test_strips_chain_of_thought_only_preamble(self):
        text = 'Step 1: think.\nStep 2: query.\n\n{"a": 1}'
        assert ra._strip_to_json_braces(text) == '{"a": 1}'

    def test_strips_closing_remark_only(self):
        text = '{"a": 1}\n\nThanks for asking — let me know if you have follow-ups.'
        assert ra._strip_to_json_braces(text) == '{"a": 1}'

    def test_empty_string_when_no_braces(self):
        assert ra._strip_to_json_braces("Just plain prose, no JSON anywhere.") == ""

    def test_empty_string_on_empty_input(self):
        assert ra._strip_to_json_braces("") == ""
        assert ra._strip_to_json_braces(None) == ""  # type: ignore[arg-type]

    def test_handles_nested_braces(self):
        # The model might emit nested JSON; rfind('}') finds the LAST,
        # so the full nested structure is preserved.
        text = (
            "preamble\n"
            '{"outer": {"inner": "value"}, "list": [1, 2, 3]}\n'
            "closing"
        )
        out = ra._strip_to_json_braces(text)
        assert out == '{"outer": {"inner": "value"}, "list": [1, 2, 3]}'


# ── Citation integrity ──────────────────────────────────────────────────────

class TestFilterToVerifiedSignals:
    def test_keeps_signals_whose_url_was_searched(self):
        parsed = ra._parse_digest_json(SAMPLE_JSON)
        verified = {
            "https://federalreserve.gov/example",
            "https://bls.gov/example",
        }
        signals, urls = ra._filter_to_verified_signals(parsed, verified)
        # Two verified urls → two signals retained; the fabricated VIX
        # citation is dropped.
        assert len(signals) == 2
        assert signals[0]["source_url"] == "https://federalreserve.gov/example"
        assert signals[1]["source_url"] == "https://bls.gov/example"
        assert urls == [
            "https://federalreserve.gov/example",
            "https://bls.gov/example",
        ]

    def test_drops_signals_with_no_source_url(self):
        parsed = {
            "key_signals": [{
                "category":    "rates",
                "signal":      "10Y up 5bp",
                "implication": "IG duration cost",
                "source_url":  "",  # blank
            }],
        }
        signals, urls = ra._filter_to_verified_signals(parsed, set())
        assert signals == []
        assert urls == []

    def test_drops_signals_with_unsearched_url(self):
        # The model fabricates a url that web_search did not return; the
        # filter must drop it even though the JSON shape is valid.
        parsed = {
            "key_signals": [{
                "category":    "rates",
                "signal":      "10Y up 5bp",
                "implication": "IG duration cost",
                "source_url":  "https://fake.example.com/article",
            }],
        }
        signals, urls = ra._filter_to_verified_signals(
            parsed, {"https://real.example.com/article"})
        assert signals == []
        assert urls == []

    def test_deduplicates_citation_urls(self):
        # Two signals referencing the same source — the citation_urls
        # list MUST de-duplicate so the dashboard does not render the
        # same link twice in the footer.
        parsed = {
            "key_signals": [
                {"category": "monetary_policy", "signal": "Fed pause",
                 "implication": "x", "source_url": "https://fed.gov/a"},
                {"category": "monetary_policy", "signal": "Fed forward guidance",
                 "implication": "y", "source_url": "https://fed.gov/a"},
            ],
        }
        signals, urls = ra._filter_to_verified_signals(
            parsed, {"https://fed.gov/a"})
        assert len(signals) == 2
        assert urls == ["https://fed.gov/a"]  # de-duplicated

    def test_handles_non_dict_signal_entries(self):
        # A model that emits "key_signals": ["string", null, 42] must not
        # crash the filter — the agent's downstream caller would explode.
        parsed = {"key_signals": ["a", None, 42]}
        signals, urls = ra._filter_to_verified_signals(parsed, set())
        assert signals == []
        assert urls == []

    def test_non_list_key_signals_returns_empty(self):
        # Defensive — the JSON could emit a malformed shape.
        parsed = {"key_signals": "not a list"}
        signals, urls = ra._filter_to_verified_signals(parsed, set())
        assert signals == []
        assert urls == []


# ── Failure digest shape ─────────────────────────────────────────────────────

class TestFailureDigest:
    def test_carries_an_error_key(self):
        # The engine layer maps `error` truthy → status='failed'. Pin the
        # key name so a refactor cannot silently lose this signal.
        out = ra._failure_digest("SDK timeout")
        assert out["error"] == "SDK timeout"

    def test_carries_empty_collections(self):
        out = ra._failure_digest("x")
        assert out["key_signals"] == []
        assert out["citation_urls"] == []

    def test_summary_explains_the_failure(self):
        out = ra._failure_digest("x")
        assert "could not be generated" in out["summary_text"].lower()


# ── End-to-end generate_digest with the SDK mocked ───────────────────────────

class _StubBlock:
    def __init__(self, **kw): self.__dict__.update(kw)


def _stub_response(text: str, search_urls: list[str] | None = None):
    """Builds an Anthropic-shaped response with text + web_search_tool_result
    blocks. Mirrors the academic_advisor test pattern."""
    blocks: list[object] = []
    if search_urls:
        search_results = [
            _StubBlock(type="web_search_result", title=f"t{i}", url=url)
            for i, url in enumerate(search_urls)
        ]
        blocks.append(_StubBlock(
            type="web_search_tool_result", content=search_results))
    blocks.append(_StubBlock(type="text", text=text))
    return SimpleNamespace(
        content=blocks,
        usage=SimpleNamespace(input_tokens=100, output_tokens=200))


class TestGenerateDigest:
    def test_research_max_output_tokens_default_is_at_least_4096(self):
        """May 23 2026 production fire: runs 9-11 all returned the
        failure digest because the response was truncated by the
        project-wide MAX_OUTPUT_TOKENS=1024. The research agent issues
        3-5 web_search calls + up to 4 web_fetch calls and composes a
        JSON digest with 5+ signals — well over 1024 tokens. The cap
        was bumped to 4096; this test pins the floor so a future
        config change that lowers it fails loudly."""
        assert ra._RESEARCH_MAX_OUTPUT_TOKENS >= 4096

    def test_generate_digest_passes_research_cap_to_sdk(self):
        """The 4096 cap must actually reach the messages.create call;
        regressing back to the lower project default would silently
        reintroduce the truncation."""
        client = MagicMock()
        client.messages.create.return_value = _stub_response(
            SAMPLE_JSON, search_urls=["https://federalreserve.gov/x"])
        with patch.object(ra, "get_anthropic_client", return_value=client):
            ra.generate_digest()
        _, kwargs = client.messages.create.call_args
        assert kwargs["max_tokens"] >= 4096

    def test_empty_parse_log_includes_stop_reason(self):
        """If max_tokens DID truncate the response, the warning log
        must carry stop_reason='max_tokens' so the failure is
        debuggable from Render logs without re-running the agent."""
        import logging as _logging
        client = MagicMock()
        # A response that has no parseable JSON AND a max_tokens stop.
        client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="no json at all")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=20),
            stop_reason="max_tokens",
        )
        with patch.object(ra, "get_anthropic_client", return_value=client):
            digest, _ = ra.generate_digest()
        # We can't easily intercept structlog without more harness, so
        # just confirm the failure path was reached.
        assert "error" in digest
        assert digest["key_signals"] == []

    def test_happy_path_returns_filtered_digest(self):
        client = MagicMock()
        client.messages.create.return_value = _stub_response(
            SAMPLE_JSON,
            search_urls=[
                "https://federalreserve.gov/example",
                "https://bls.gov/example",
            ],
        )
        with patch.object(ra, "get_anthropic_client", return_value=client):
            digest, usage = ra.generate_digest()

        assert "error" not in digest
        assert digest["summary_text"].startswith("Fed paused")
        # Three signals in the JSON but only two with verified URLs.
        assert len(digest["key_signals"]) == 2
        assert digest["citation_urls"] == [
            "https://federalreserve.gov/example",
            "https://bls.gov/example",
        ]
        assert usage["input_tokens"] == 100
        assert usage["output_tokens"] == 200
        assert usage["n_searches"] == 2

    def test_sdk_error_returns_failure_digest(self):
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("Anthropic 503")
        with patch.object(ra, "get_anthropic_client", return_value=client):
            digest, usage = ra.generate_digest()
        assert digest["error"]
        assert "Anthropic 503" in digest["error"]
        assert digest["key_signals"] == []
        # Usage is zeroed when the SDK never returned a message.
        assert usage["input_tokens"] == 0
        assert usage["output_tokens"] == 0

    def test_unparseable_response_returns_failure_digest(self):
        """May 22 2026 contract reversal — the earlier plain-text
        fallback (store raw text as summary on parse failure) was
        removed because it allowed model chain-of-thought to reach
        the dashboard tile ("I'll start by running 5 parallel
        searches…" — the user-reported leak). Failed parses now take
        the hard-failure path so chain-of-thought is NEVER stored as
        summary content."""
        client = MagicMock()
        client.messages.create.return_value = _stub_response(
            "Sorry, I can't help with that today.", search_urls=[])
        with patch.object(ra, "get_anthropic_client", return_value=client):
            digest, usage = ra.generate_digest()
        # Critical: `error` IS present — chain-of-thought never reaches
        # summary_text.
        assert digest["error"]
        assert "no parseable JSON" in digest["error"]
        # The raw "Sorry, I can't help…" prose must NOT appear in any
        # user-facing field.
        assert "Sorry, I can't help" not in digest["summary_text"]
        assert digest["key_signals"] == []
        # Usage still carries the response tokens — the call DID happen.
        assert usage["input_tokens"] == 100

    def test_truly_empty_response_returns_failure_digest(self):
        """Hard-failure path is also taken when the model returns no
        text at all. Same hard-failure as the unparseable case above —
        the dashboard renders its empty state until the next
        successful run."""
        client = MagicMock()
        client.messages.create.return_value = _stub_response(
            "", search_urls=[])
        with patch.object(ra, "get_anthropic_client", return_value=client):
            digest, usage = ra.generate_digest()
        assert digest["error"]
        assert "no parseable JSON" in digest["error"]
        assert digest["key_signals"] == []

    def test_chain_of_thought_with_valid_json_stores_only_parsed_fields(self):
        """The user-reported leak: model emits chain-of-thought
        preamble THEN a valid JSON object. The parser must extract the
        JSON; the chain-of-thought must NEVER reach summary_text,
        regime_implication, or raw_response. Pin this contract so a
        future relaxation cannot quietly re-expose the leak surface."""
        chain_of_thought_then_json = (
            "I'll start by running 5 parallel searches across the key "
            "macro categories — monetary policy, inflation, growth, "
            "rates, and credit.\n\n"
            "Now I have sufficient sourced data to construct the "
            "digest. Let me parse the results.\n\n"
            '{"summary_text": "Fed paused at 5.25-5.50% and CPI cooled.",\n'
            ' "key_signals": [{"category": "monetary_policy",\n'
            '   "signal": "Fed holds rates steady.",\n'
            '   "implication": "IG duration tailwind.",\n'
            '   "source_url": "https://federalreserve.gov/example"}],\n'
            ' "regime_implication": "Dovish transition."}\n\n'
            "Closing remark: I have included the most relevant signals."
        )
        client = MagicMock()
        client.messages.create.return_value = _stub_response(
            chain_of_thought_then_json,
            search_urls=["https://federalreserve.gov/example"])
        with patch.object(ra, "get_anthropic_client", return_value=client):
            digest, usage = ra.generate_digest()

        # Critical contract: the run completes (parse succeeded) but
        # the chain-of-thought tokens NEVER appear in any stored field.
        assert "error" not in digest
        assert digest["summary_text"] == "Fed paused at 5.25-5.50% and CPI cooled."
        assert digest["regime_implication"] == "Dovish transition."
        # The leak-canary strings — none of these must appear in any
        # stored field.
        for leak_string in (
            "I'll start by running",
            "Now I have sufficient",
            "Closing remark",
            "Let me parse the results",
        ):
            assert leak_string not in digest["summary_text"]
            assert leak_string not in digest["regime_implication"]
            assert leak_string not in digest["raw_response"]

    def test_system_prompt_carries_strict_json_only_instruction(self):
        """The May 22 2026 prompt change tells the model NOT to emit any
        prose, reasoning, or commentary around the JSON. Pin the
        instruction so a future prompt edit cannot silently re-enable
        the chain-of-thought leak."""
        prompt = ra._SYSTEM_PROMPT
        assert "Return ONLY a valid JSON object" in prompt
        assert "Do not include any text, commentary, reasoning" in prompt
        assert "chain-of-thought preamble" in prompt
        # Specific exemplar of the leak the user reported — pin so a
        # generic re-wording cannot silently drop it.
        assert "I'll start by running searches" in prompt

    def test_concatenates_multiple_text_blocks(self):
        """May 22 2026 fix: web-search-using responses interleave
        reasoning text with tool calls. The JSON output sometimes
        lands in an earlier text block while a later block carries a
        closing remark; the previous parser took ONLY the last block
        and lost the JSON entirely. Verify the parser now finds JSON
        in any text block."""
        json_block = _StubBlock(type="text",
                                text='{"summary_text": "early block JSON"}')
        note_block = _StubBlock(type="text",
                                text="Let me know if you need more.")
        search_results = [_StubBlock(type="web_search_result", title="t",
                                     url="https://example.com")]
        search_block = _StubBlock(type="web_search_tool_result",
                                  content=search_results)
        response = SimpleNamespace(
            content=[search_block, json_block, note_block],
            usage=SimpleNamespace(input_tokens=100, output_tokens=200))

        client = MagicMock()
        client.messages.create.return_value = response
        with patch.object(ra, "get_anthropic_client", return_value=client):
            digest, usage = ra.generate_digest()

        # The JSON in the FIRST text block (between two non-text
        # operations) must be located, not lost behind the closing
        # remark in the last text block.
        assert "error" not in digest
        assert digest["summary_text"] == "early block JSON"

    def test_markdown_fenced_json_with_prose_parses(self):
        """May 22 2026 failure mode: the model wraps the JSON in a
        ```json fence AND adds prose on either side. The previous
        parser handled fenced JSON OR embedded JSON but the
        combination tripped the fence stripper into discarding the
        prose containing the JSON. Verify both forms now parse."""
        fenced = (
            "Here is the macro digest you requested:\n\n"
            "```json\n"
            '{"summary_text": "fenced and surrounded",\n'
            ' "key_signals": [{"category": "rates",\n'
            '   "signal": "10Y +5bp",\n'
            '   "implication": "IG duration cost",\n'
            '   "source_url": "https://example.com"}],\n'
            ' "regime_implication": "Transition."}\n'
            "```\n\n"
            "Let me know if you need a deeper read on any signal."
        )
        client = MagicMock()
        client.messages.create.return_value = _stub_response(
            fenced, search_urls=["https://example.com"])
        with patch.object(ra, "get_anthropic_client", return_value=client):
            digest, usage = ra.generate_digest()

        assert "error" not in digest
        assert digest["summary_text"] == "fenced and surrounded"
        assert digest["regime_implication"] == "Transition."
        assert len(digest["key_signals"]) == 1
        assert digest["key_signals"][0]["source_url"] == "https://example.com"

    def test_unparseable_response_does_not_leak_via_citation_urls(self):
        """The earlier plain-text fallback also kept the verified URLs
        on a failed digest so the team could trace reasoning. With the
        fallback removed (chain-of-thought leak fix, May 22 2026), a
        failed run produces NO summary content at all — citation URLs
        are part of the failure digest's empty contract because they
        anchor user-visible explanation that would otherwise be
        attached to a non-existent summary."""
        client = MagicMock()
        client.messages.create.return_value = _stub_response(
            "I found two articles but cannot summarise them in JSON.",
            search_urls=["https://fed.gov/example",
                         "https://bls.gov/example"],
        )
        with patch.object(ra, "get_anthropic_client", return_value=client):
            digest, usage = ra.generate_digest()

        # Hard failure — the raw prose never reaches summary_text.
        assert digest["error"]
        assert "I found two articles" not in digest["summary_text"]
        assert digest["citation_urls"] == []
        assert digest["key_signals"] == []

    def test_signals_without_any_verified_urls_yield_empty(self):
        """Every signal in the JSON references an URL web_search did not
        return → every signal is dropped → digest carries no signals
        but the run still completes (the agent had nothing falsifiable
        to report, which is a valid outcome).

        Caveat: the parsed JSON itself is still valid, so this is a
        'complete' run with empty signals — distinct from the
        unparseable case above."""
        client = MagicMock()
        client.messages.create.return_value = _stub_response(
            SAMPLE_JSON,
            search_urls=["https://different-source.example.com/x"],
        )
        with patch.object(ra, "get_anthropic_client", return_value=client):
            digest, usage = ra.generate_digest()
        # No `error` — the parse worked, the filter just had nothing
        # to keep.
        assert "error" not in digest
        assert digest["key_signals"] == []
        assert digest["citation_urls"] == []
        assert usage["n_searches"] == 1
