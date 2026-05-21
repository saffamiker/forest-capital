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
        client = MagicMock()
        client.messages.create.return_value = _stub_response(
            "Sorry, I can't help with that today.", search_urls=[])
        with patch.object(ra, "get_anthropic_client", return_value=client):
            digest, usage = ra.generate_digest()
        assert digest["error"]
        assert digest["key_signals"] == []
        # Usage still carries the response tokens — the call DID happen,
        # it just returned no parseable JSON.
        assert usage["input_tokens"] == 100

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
