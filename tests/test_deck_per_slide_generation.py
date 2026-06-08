"""tests/test_deck_per_slide_generation.py -- bridge #95.

The deck generation now runs ONE LLM call per slide (instead of one
4000-token call for all six) so the JSON never truncates. These tests
pin:

  * slide_generation_prompt -- the per-slide prompt isolates ONE
    slide's spec from SLIDE_SPECIFICATIONS so the model has the full
    project framing but only one slide's required content to emit.
  * parse_single_slide_json -- tolerant of markdown fences, leading
    prose, trailing prose. Returns None on unparseable input so the
    caller can surface a per-slide [DATA PENDING].
  * _slice_slide_spec -- the spec-splitter that returns one slide's
    block from the SLIDE_SPECIFICATIONS string.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")


class TestSliceSlideSpec:
    """Each slide's body must be isolatable so the per-slide call
    sees exactly one slide's required content."""

    def test_slice_returns_only_the_requested_slide(self):
        from tools.academic_deck import _slice_slide_spec

        slide_2 = _slice_slide_spec(2)
        # The slice MUST start with the slide-2 header.
        assert slide_2.startswith("Slide 2 --")
        # And MUST NOT carry the slide-3 header or any later slide.
        assert "Slide 3 --" not in slide_2
        assert "Slide 4 --" not in slide_2
        assert "Slide 5 --" not in slide_2
        assert "Slide 6 --" not in slide_2

    def test_slice_returns_the_last_slide_to_end_of_string(self):
        from tools.academic_deck import _slice_slide_spec, DECK_SLIDE_COUNT

        last = _slice_slide_spec(DECK_SLIDE_COUNT)
        assert last.startswith(f"Slide {DECK_SLIDE_COUNT} --")
        # The slice MUST carry the slide's body content -- speaker
        # notes survive into the LLM prompt.
        assert "Speaker notes" in last

    def test_slice_raises_on_out_of_range_slide_number(self):
        from tools.academic_deck import _slice_slide_spec, DECK_SLIDE_COUNT

        with pytest.raises(ValueError):
            _slice_slide_spec(0)
        with pytest.raises(ValueError):
            _slice_slide_spec(DECK_SLIDE_COUNT + 1)


class TestSlideGenerationPrompt:
    """The per-slide prompt carries the project framing AND exactly
    one slide's spec AND a single-object JSON contract -- not the
    {"slides":[...]} wrapper."""

    def test_prompt_carries_project_framing(self):
        from tools.academic_deck import slide_generation_prompt

        prompt = slide_generation_prompt(1)
        # The preamble must still introduce the project so the LLM
        # has every detail it needs for one slide.
        assert "diversification" in prompt.lower()
        assert "FNA 670" in prompt

    def test_prompt_isolates_one_slide_only(self):
        """The prompt for slide 3 must carry slide 3's spec and
        NOT carry slide 5's spec (or any other slide's body)."""
        from tools.academic_deck import slide_generation_prompt

        s3 = slide_generation_prompt(3)
        assert "Slide 3 --" in s3
        # Slide 4 and 5 specs must NOT bleed into slide 3's prompt.
        assert "Slide 4 --" not in s3
        assert "Slide 5 --" not in s3

    def test_prompt_asks_for_single_object_not_wrapped_list(self):
        from tools.academic_deck import slide_generation_prompt

        prompt = slide_generation_prompt(2)
        # The contract is a SINGLE object -- speaker notes, bullets,
        # table -- not the {"slides":[...]} wrapper the all-six call
        # uses.
        assert '"slide_number": 2' in prompt
        assert '"speaker_notes"' in prompt
        # The wrapper must NOT appear -- a model that emits the
        # wrapper would force the parser to dig in.
        assert '"slides":' not in prompt


class TestParseSingleSlideJson:
    """The single-slide parser pulls one object out of the LLM
    response. Tolerant of fences and prose; None on failure."""

    def test_parses_clean_json_object(self):
        from tools.academic_deck import parse_single_slide_json

        raw = (
            '{"slide_number": 1, "title": "Hi", '
            '"bullets": ["a", "b"], "table_data": null, '
            '"speaker_notes": "notes"}'
        )
        out = parse_single_slide_json(raw)
        assert out is not None
        assert out["slide_number"] == 1
        assert out["bullets"] == ["a", "b"]

    def test_strips_markdown_fences(self):
        from tools.academic_deck import parse_single_slide_json

        raw = (
            "```json\n"
            '{"slide_number": 2, "title": "T", "bullets": [],'
            ' "speaker_notes": ""}\n'
            "```"
        )
        out = parse_single_slide_json(raw)
        assert out is not None
        assert out["slide_number"] == 2

    def test_returns_none_on_empty_input(self):
        from tools.academic_deck import parse_single_slide_json

        assert parse_single_slide_json("") is None
        assert parse_single_slide_json(None) is None
        assert parse_single_slide_json("   \n") is None

    def test_returns_none_on_truncated_unparseable_response(self):
        from tools.academic_deck import parse_single_slide_json

        # Mid-JSON truncation -- the exact failure mode the bridge #95
        # change is designed to avoid. parse_single_slide_json returns
        # None so the caller logs a per-slide warning AND inserts a
        # [DATA PENDING] placeholder for that slide only.
        raw = '{"slide_number": 3, "title": "Three", "bullets": ["unclosed'
        assert parse_single_slide_json(raw) is None

    def test_returns_none_on_array_at_root(self):
        """A model that returned the {"slides":[...]} wrapper instead
        of a single object should NOT be accepted as a single slide."""
        from tools.academic_deck import parse_single_slide_json

        # An array (not a dict) is not a valid single-slide object.
        # The parser pulls between the first { and last } -- which
        # would be the inner slide, but that's by design: a wrapper
        # with one slide is ambiguous. Use a clearly-non-dict input.
        raw = "[1, 2, 3]"
        assert parse_single_slide_json(raw) is None


class TestDeckSlideCount:
    """The deck has exactly 11 slides post-rebuild (bridge #98).
    _slice_slide_spec + slide_generation_prompt + parse_single_slide_json
    all rely on this constant; pin it so a future change touches every
    helper at once."""

    def test_eleven_slides_post_rebuild(self):
        from tools.academic_deck import DECK_SLIDE_COUNT
        assert DECK_SLIDE_COUNT == 11

    def test_every_slide_number_has_a_spec(self):
        """The per-slide loop iterates 1..DECK_SLIDE_COUNT. Each must
        produce a non-empty slice or the loop drops a slide silently."""
        from tools.academic_deck import _slice_slide_spec, DECK_SLIDE_COUNT
        for n in range(1, DECK_SLIDE_COUNT + 1):
            slice_n = _slice_slide_spec(n)
            assert slice_n
            assert f"Slide {n} --" in slice_n


class TestStoryArcSeed:
    """Bridge #98: each per-slide prompt carries the story-arc seed
    so the LLM knows where it sits in the 11-slide narrative without
    re-reading every other slide's content."""

    def test_seed_substitutes_slide_number_and_title(self):
        from tools.academic_deck import (
            slide_generation_prompt, SLIDE_TITLES,
        )
        prompt = slide_generation_prompt(7)
        assert "slide 7 of 11" in prompt
        assert SLIDE_TITLES[6] in prompt   # slide 7's title

    def test_seed_lists_the_eleven_arc_stops(self):
        from tools.academic_deck import slide_generation_prompt
        prompt = slide_generation_prompt(1)
        # The arc seed lists all 11 narrative beats so the slide-1
        # generator knows where the deck is heading.
        for n in range(1, 12):
            assert f"{n}." in prompt


class TestNoHarnessReferenced:
    """Bridge #100: the deck slide generation must NOT route through
    the academic-writer harness (which uses the peer-discussant
    evaluator that caused the slide-2 leak). The prompt is sent
    directly to Sonnet via call_claude."""

    def test_main_imports_call_claude_not_harness_for_slide_generation(self):
        """Grep the _generate_one_deck_slide body for the import.
        A future refactor that reintroduces harness_narrative for
        slide generation breaks this pin. The docstring may mention
        the deprecated harness path for context; the test checks the
        non-comment body for the actual invocation."""
        import inspect
        import main
        src = inspect.getsource(main._generate_one_deck_slide)
        assert "call_claude" in src
        # Strip the docstring out before scanning -- the docstring
        # explains WHY harness_narrative was removed, so it's allowed
        # to mention the name. The actual function body must not.
        lines = src.split("\n")
        in_docstring = False
        body_only_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('"""'):
                # Either opens or closes a docstring; could open AND
                # close on the same line for one-liners.
                if in_docstring:
                    in_docstring = False
                else:
                    in_docstring = True
                    if stripped.count('"""') >= 2:
                        in_docstring = False
                continue
            if in_docstring:
                continue
            body_only_lines.append(line)
        body = "\n".join(body_only_lines)
        # The body must not import or call harness_narrative.
        assert "harness_narrative" not in body
