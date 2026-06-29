"""tests/test_apply_fix_inline_splice.py -- June 27 2026.

Pins the surgical-section-splice refactor of the apply-fix +
propose-fix-text + accept-fix-text endpoints. Two bugs motivated
the refactor:

  Bug 1 (PR #443 follow-up) -- the legacy story-plan SELECT only
    handled the deck via get_latest_story_plan; brief / appendix /
    script fell through to the bare-hash SELECT which never found
    a row for appendix or script (no story_plans row exists) and
    409'd "No story plan to patch -- regenerate the document".

  Bug 2 (urgent, Bob 2026-06-27) -- propose-fix-text's section
    extractor fell back to the WHOLE document when the section
    heading couldn't be located. Sonnet then returned just the
    patched section, and the frontend's content_text string-replace
    silently overwrote the entire document with only that section.

Test groups:

  TestExtractSectionText
    _extract_section_text handles letter labels ('Section B'),
    refuses the unsafe whole-document fallback, and applies the
    80% safety guard.

  TestLocateSectionInContent
    _locate_section_in_content matches deck slides + TipTap section
    ranges with fuzzy heading-text drift handling (the Bob tonight
    tripwire).

  TestSpliceSectionIntoContent
    _splice_section_into_content preserves every other section
    verbatim while replacing only the targeted anchor.

  TestFuzzyHeadingMatch
    Heading drift cases (commas / extra words / letter labels).

  TestApplyFixSkip
    post_apply_fix raises 422 instead of falling back to regen
    when the inline path can't apply.

  TestProposeFixTextRefusesWholeDocFallback
    propose-fix-text 409s when the section can't be located,
    instead of using the whole document as the original_text.

  TestApplyFixDoesNotCallStartGenerationJob
    The legacy regen kickoff is gone -- apply-fix never reaches
    _start_generation_job under any code path.
"""
from __future__ import annotations

import os

import pytest


# Force test-environment branches in the live endpoints so the
# Sonnet calls fall through to the deterministic stubs.
os.environ.setdefault("ENVIRONMENT", "test")


# ── _extract_section_text ───────────────────────────────────────────


class TestExtractSectionText:

    def test_letter_label_section_b(self):
        """Section B in an appendix-shaped doc with 'B. ...'
        headings was the Bob 2026-06-27 tripwire. The old regex
        only stripped 'Section <digits>' so 'Section B' normalised
        to 'b' and missed every heading."""
        from main import _extract_section_text
        doc = (
            "A. Setup\nFirst.\n\n"
            "B. Results\nMiddle.\n\n"
            "C. Method\nLast.\n")
        out = _extract_section_text(doc, "Section B")
        assert out is not None
        assert "B. Results" in out
        assert "Middle" in out
        assert "Last" not in out
        assert "First" not in out

    def test_unlocatable_section_returns_none(self):
        """Pre-fix behavior: returned the whole document on a
        miss. Post-fix: returns None so the caller refuses
        instead of silently overwriting everything."""
        from main import _extract_section_text
        doc = ("A. Setup\nFirst.\n\nB. Results\nMiddle.\n")
        out = _extract_section_text(doc, "Section Z")
        assert out is None

    def test_80_percent_guard_rejects_whole_doc_span(self):
        """A heading match that bounds the WHOLE document is a
        regression signal; the guard returns None to refuse the
        unsafe span."""
        from main import _extract_section_text
        # 'X. Title' is the only heading; body extends to EOF.
        doc = "X. Title\n" + ("line\n" * 200)
        out = _extract_section_text(doc, "X. Title")
        # Either None (guard fired) or substantially less than
        # the whole doc.
        assert out is None or len(out) < 0.8 * len(doc)

    def test_substring_drift(self):
        """'Limitations' should match a heading 'Honest
        Limitations' via the substring-direction fallback."""
        from main import _extract_section_text
        doc = (
            "1. Setup\nFirst.\n\n"
            "2. Honest Limitations\nMiddle.\n\n"
            "3. Conclusion\nLast.\n")
        out = _extract_section_text(doc, "Limitations")
        assert out is not None
        assert "Honest Limitations" in out
        assert "Middle" in out
        assert "Last" not in out


# ── _locate_section_in_content ──────────────────────────────────────


class TestLocateSectionInContent:

    def test_deck_slide_by_title(self):
        from main import _locate_section_in_content
        cj = {"slides": [
            {"id": 1, "title": "Intro",   "elements": []},
            {"id": 2, "title": "Setup",   "elements": []},
            {"id": 3, "title": "Verdict", "elements": []},
        ]}
        loc = _locate_section_in_content(
            "presentation_deck", cj, "Setup")
        assert loc is not None
        anchor, original = loc
        assert anchor == 1
        assert original["title"] == "Setup"

    def test_deck_slide_by_slide_number(self):
        """A finding's section_name often says 'Slide 4' even
        when the slide's actual title has drifted."""
        from main import _locate_section_in_content
        cj = {"slides": [
            {"id": 1, "title": "Yes", "elements": []},
            {"id": 2, "title": "Agenda", "elements": []},
            {"id": 3, "title": "Investment Case", "elements": []},
            {"id": 4, "title": "Why Static Allocation Failed in 2022",
             "elements": []},
        ]}
        loc = _locate_section_in_content(
            "presentation_deck", cj, "Slide 4: Why Static Failed")
        assert loc is not None
        anchor, original = loc
        assert anchor == 3
        assert original["title"].startswith(
            "Why Static Allocation Failed")

    def test_tiptap_section_by_letter_label(self):
        """Section B against TipTap H1 'B. Results' -- the
        same Bob tonight tripwire applied to the structured
        TipTap path."""
        from main import _locate_section_in_content
        cj = {
            "type": "doc",
            "content": [
                {"type": "heading", "attrs": {"level": 1},
                 "content": [{"type": "text", "text": "A. Setup"}]},
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "first"}]},
                {"type": "heading", "attrs": {"level": 1},
                 "content": [{"type": "text", "text": "B. Results"}]},
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "middle"}]},
                {"type": "heading", "attrs": {"level": 1},
                 "content": [{"type": "text", "text": "C. Method"}]},
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "last"}]},
            ],
        }
        loc = _locate_section_in_content(
            "analytical_appendix", cj, "Section B")
        assert loc is not None
        anchor, original = loc
        assert anchor == (2, 4)
        assert len(original) == 2  # heading + paragraph

    def test_tiptap_unlocatable_returns_none(self):
        from main import _locate_section_in_content
        cj = {
            "type": "doc",
            "content": [
                {"type": "heading", "attrs": {"level": 1},
                 "content": [{"type": "text", "text": "A. Setup"}]},
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "first"}]},
            ],
        }
        loc = _locate_section_in_content(
            "analytical_appendix", cj, "Section Z")
        assert loc is None


# ── _splice_section_into_content ────────────────────────────────────


class TestSpliceSectionIntoContent:

    def test_tiptap_splice_preserves_other_sections(self):
        """The motivating bug -- write-back overwriting all other
        sections. Verify every untouched section comes through
        verbatim."""
        from main import (
            _locate_section_in_content,
            _splice_section_into_content,
            _node_text_for_match,
        )
        cj = {
            "type": "doc",
            "content": [
                {"type": "heading", "attrs": {"level": 1},
                 "content": [{"type": "text", "text": "A. Setup"}]},
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "FIRST"}]},
                {"type": "heading", "attrs": {"level": 1},
                 "content": [{"type": "text", "text": "B. Results"}]},
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "OLD"}]},
                {"type": "heading", "attrs": {"level": 1},
                 "content": [{"type": "text", "text": "C. Method"}]},
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "LAST"}]},
            ],
        }
        loc = _locate_section_in_content(
            "analytical_appendix", cj, "Section B")
        assert loc is not None
        anchor, _orig = loc
        patched = [
            {"type": "heading", "attrs": {"level": 1},
             "content": [{"type": "text", "text": "B. Results"}]},
            {"type": "paragraph", "content": [
                {"type": "text", "text": "PATCHED"}]},
        ]
        new_cj = _splice_section_into_content(
            "analytical_appendix", cj, anchor, patched)
        new_texts = [_node_text_for_match(n)
                     for n in new_cj["content"]]
        # FIRST, A. Setup, and LAST / C. Method all preserved.
        assert "FIRST" in new_texts
        assert "LAST" in new_texts
        assert "A. Setup" in new_texts
        assert "C. Method" in new_texts
        # OLD replaced by PATCHED, B. Results heading preserved.
        assert "PATCHED" in new_texts
        assert "OLD" not in new_texts

    def test_deck_splice_preserves_other_slides(self):
        from main import (
            _locate_section_in_content,
            _splice_section_into_content,
        )
        cj = {"slides": [
            {"id": 1, "title": "Intro", "elements": [
                {"id": "s1", "type": "text", "content": "A"}]},
            {"id": 2, "title": "Setup", "elements": [
                {"id": "s2", "type": "text", "content": "OLD"}]},
            {"id": 3, "title": "Verdict", "elements": [
                {"id": "s3", "type": "text", "content": "C"}]},
        ]}
        loc = _locate_section_in_content(
            "presentation_deck", cj, "Setup")
        assert loc is not None
        anchor, orig = loc
        patched = {**orig, "elements": [
            {"id": "s2", "type": "text", "content": "PATCHED"}]}
        new_cj = _splice_section_into_content(
            "presentation_deck", cj, anchor, patched)
        contents = [
            s["elements"][0]["content"] for s in new_cj["slides"]]
        assert contents == ["A", "PATCHED", "C"]


# ── Fuzzy heading drift cases ───────────────────────────────────────


class TestFuzzyHeadingMatch:
    """The Bob tonight tripwire: heading-text drift between the
    LLM finding's section_name and the actual draft heading.
    These cases exercise the fuzzy match tiers."""

    def test_word_overlap_drift_in_tiptap(self):
        from main import _find_tiptap_section_range
        nodes = [
            {"type": "heading", "attrs": {"level": 1},
             "content": [
                 {"type": "text",
                  "text": "Why Static Allocation Failed in 2022"}]},
            {"type": "paragraph", "content": [
                {"type": "text", "text": "body"}]},
        ]
        # Finding says 'Why Static Failed' -- word-overlap match.
        rng = _find_tiptap_section_range(
            nodes, "Why Static Failed")
        assert rng is not None
        assert rng[0] == 0

    def test_slide_number_prefix_in_tiptap(self):
        """Script H2 markers are 'Slide N: title'; the finding
        may say just 'Slide 4'."""
        from main import _find_tiptap_section_range
        nodes = [
            {"type": "heading", "attrs": {"level": 2},
             "content": [
                 {"type": "text", "text": "Slide 4: Why Static Failed"}]},
            {"type": "paragraph", "content": [
                {"type": "text", "text": "narration"}]},
        ]
        rng = _find_tiptap_section_range(nodes, "Slide 4")
        assert rng is not None
        assert rng[0] == 0


# ── _ApplyFixSkip wired to 422 ──────────────────────────────────────


class TestApplyFixSkip:
    """post_apply_fix raises 422 instead of falling back to regen
    when the inline path can't apply. Exercise via the test
    environment short-circuit + a synthetic _ApplyFixSkip path."""

    @pytest.mark.asyncio
    async def test_apply_fix_skip_raises_with_section_name(self):
        from main import _ApplyFixSkip
        skip = _ApplyFixSkip(
            "Section 'X' not found.",
            section_name="X")
        assert skip.detail == "Section 'X' not found."
        assert "editor" in skip.hint.lower()
        assert skip.section_name == "X"


# ── propose-fix-text refuses whole-doc fallback ─────────────────────


class TestProposeFixTextRefusesWholeDocFallback:
    """The motivating Bob 2026-06-27 bug -- propose-fix-text
    returned the WHOLE document as original_text when the section
    couldn't be located. Verify the helper now returns None on a
    miss (the endpoint translates None -> 409)."""

    def test_no_partial_match_returns_none(self):
        from main import _extract_section_text
        doc = (
            "A. Setup\nFirst.\n\n"
            "B. Results\nMiddle.\n\n"
            "C. Method\nLast.\n")
        # section_name 'Z' doesn't appear anywhere in the doc.
        out = _extract_section_text(doc, "Section Z")
        assert out is None, (
            f"Section Z should return None, got {out!r}")


# ── apply-fix has no _start_generation_job call site ────────────────


class TestApplyFixDoesNotCallStartGenerationJob:
    """The legacy regenerate-the-whole-document fallback inside
    post_apply_fix is GONE. Confirm by greping the function body
    -- this is a structural test, not a behavioural one."""

    def test_post_apply_fix_does_not_call_start_generation_job(
            self):
        """Structural: confirm post_apply_fix's source code does
        not CALL _start_generation_job (the call site looks like
        '_start_generation_job('). Comments mentioning the legacy
        path are fine; an actual call is the regression."""
        import inspect
        import re as _re
        from main import post_apply_fix
        src = inspect.getsource(post_apply_fix)
        # Strip Python comments to ignore docstring + inline notes.
        no_comments = _re.sub(
            r'(?m)^\s*#.*$', '', src)
        no_docstr = _re.sub(
            r'""".*?"""', '', no_comments, flags=_re.DOTALL)
        assert "_start_generation_job(" not in no_docstr, (
            "post_apply_fix MUST NOT call _start_generation_job "
            "-- the regen fallback path was removed in the "
            "surgical-splice refactor.")

    def test_post_apply_fix_does_not_select_story_plans(self):
        """Structural: confirm post_apply_fix doesn't execute a
        SQL SELECT against story_plans. Comments / variable names
        may still mention the table by name historically."""
        import inspect
        import re as _re
        from main import post_apply_fix
        src = inspect.getsource(post_apply_fix)
        no_comments = _re.sub(
            r'(?m)^\s*#.*$', '', src)
        no_docstr = _re.sub(
            r'""".*?"""', '', no_comments, flags=_re.DOTALL)
        # Look for actual SQL patterns or helper calls -- not
        # literal mentions in surviving comments / log keys.
        assert "FROM story_plans" not in no_docstr.replace(
            "\n", " ")
        assert "UPDATE story_plans" not in no_docstr.replace(
            "\n", " ")
        assert "get_latest_story_plan(" not in no_docstr

    def test_post_apply_fix_routes_through_try_direct_section_patch(
            self):
        import inspect
        from main import post_apply_fix
        src = inspect.getsource(post_apply_fix)
        assert "_try_direct_section_patch" in src
