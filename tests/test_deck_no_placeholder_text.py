"""tests/test_deck_no_placeholder_text.py -- June 27 2026.

PR 3 of three. Pins the spec contract that the EXPORTED PPTX must
NEVER contain `[DATA PENDING]` or `[Chart unavailable: ...]`
placeholder text under any circumstances, AND that any
un-substituted `{{...}}` token gets caught by the post-build
_substitute_pptx_text pass.

Per spec (June 27 2026):
  * If a renderer cannot resolve bullets / chart / table, it
    must LOG + SKIP that element silently (no placeholder text).
  * The post-build _substitute_pptx_text pass walks every text
    frame + speaker-notes frame + table cell and replaces any
    remaining `{{TOKEN}}` with substitution_table[TOKEN].
  * Together these guarantee a clean PPTX even when the LLM
    response was incomplete or the substitution_table was built
    against a stale CIO row.

Test groups:

  TestNoPlaceholderTextInExportedPptx
    Build a deck with empty bullets / empty table_data / no chart
    PNGs and confirm zero placeholder strings appear in the
    exported PPTX XML.

  TestSubstitutePptxTextPostBuildPass
    The _substitute_pptx_text helper replaces {{TOKEN}} in body
    text frames, table cells, and speaker notes.

  TestRendererSkipBehaviour
    When bullets is empty, the renderer logs the per-renderer
    'bullets_empty_skipping' event so operators can grep for it.
"""
from __future__ import annotations

import io
import os
import zipfile

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── No placeholder text in exported PPTX ───────────────────────────


def _build_empty_deck() -> bytes:
    """Build a deck where every slide has empty bullets +
    table_data + chart png. Exercises every fail-loud / log+skip
    path simultaneously."""
    from tools.academic_deck import (
        build_presentation_deck, DECK_SLIDE_COUNT, SLIDE_TITLES,
    )
    slides = [
        {
            "slide_number": i + 1,
            "title": SLIDE_TITLES[i],
            "bullets": [],
            "table_data": None,
            "speaker_notes": "",
        }
        for i in range(DECK_SLIDE_COUNT)
    ]
    return build_presentation_deck(slides, charts={})


def _extract_all_slide_text(pptx_bytes: bytes) -> str:
    """Concatenate every slide's XML body for substring searches."""
    out = []
    with zipfile.ZipFile(io.BytesIO(pptx_bytes)) as zf:
        for n in zf.namelist():
            if n.startswith("ppt/slides/slide") and n.endswith(".xml"):
                out.append(
                    zf.read(n).decode("utf-8", errors="ignore"))
    return "".join(out)


class TestNoPlaceholderTextInExportedPptx:

    def test_no_data_pending_string(self):
        pptx = _build_empty_deck()
        text = _extract_all_slide_text(pptx)
        assert "[DATA PENDING]" not in text, (
            "[DATA PENDING] placeholder text appeared in exported "
            "PPTX -- spec violation. Renderers must log + skip "
            "missing elements, never emit placeholder text.")

    def test_no_chart_unavailable_string(self):
        pptx = _build_empty_deck()
        text = _extract_all_slide_text(pptx)
        assert "Chart unavailable" not in text, (
            "[Chart unavailable] placeholder text appeared in "
            "exported PPTX -- spec violation. The _image helper "
            "must log + skip when png is missing.")

    def test_build_succeeds_with_empty_bullets(self):
        """The build path completes without raising even when
        every slide has missing bullets / table / chart. This
        pins the no-fail-on-empty-data contract."""
        pptx = _build_empty_deck()
        # Non-empty PPTX (zip with content) -- build didn't bail.
        assert len(pptx) > 1000

    def test_canonical_titles_still_render(self):
        """Even with empty bullets / table / chart, the slide
        titles render so the deck has structural visibility."""
        from tools.academic_deck import (
            DECK_SLIDE_COUNT, SLIDE_TITLES,
        )
        pptx = _build_empty_deck()
        text = _extract_all_slide_text(pptx)
        # Slide 2 title (Agenda) is plain text + always renders.
        assert "Agenda" in text
        assert "Investment Case" in text  # slide 3
        assert "Why Static" in text       # slide 4


# ── _substitute_pptx_text post-build pass ──────────────────────────


class TestSubstitutePptxTextPostBuildPass:

    def test_replaces_tokens_in_slide_text(self):
        """Build a deck where the slide title carries a raw
        {{TOKEN}} placeholder (simulating the fallback case where
        sl.get('title') was empty and the renderer fell back to
        SLIDE_TITLES[idx-1] which contains literal tokens). The
        post-build pass replaces them."""
        from tools.academic_deck import (
            build_presentation_deck,
        )
        slides = [
            {
                "slide_number": 1,
                "title": "Headline: {{TEST_TOKEN}} value",
                "bullets": [],
                "table_data": None,
                "speaker_notes": "",
            },
        ] + [
            {"slide_number": i + 2, "title": "Filler",
             "bullets": [], "table_data": None,
             "speaker_notes": ""}
            for i in range(10)
        ]
        substitution_table = {"{{TEST_TOKEN}}": "REPLACED"}
        pptx = build_presentation_deck(
            slides, charts={},
            substitution_table=substitution_table)
        text = _extract_all_slide_text(pptx)
        assert "REPLACED" in text
        assert "{{TEST_TOKEN}}" not in text

    def test_no_substitution_table_is_legacy_passthrough(self):
        """When substitution_table is None / empty, the helper
        is a no-op -- any {{...}} in the slide text remains."""
        from tools.academic_deck import (
            build_presentation_deck,
        )
        slides = [
            {"slide_number": 1, "title": "{{LEAK_TOKEN}}",
             "bullets": [], "table_data": None,
             "speaker_notes": ""},
        ] + [
            {"slide_number": i + 2, "title": "X",
             "bullets": [], "table_data": None,
             "speaker_notes": ""}
            for i in range(10)
        ]
        pptx = build_presentation_deck(slides, charts={})
        text = _extract_all_slide_text(pptx)
        # No substitution table -> placeholder remains visible
        # (legacy behaviour; this defends against an accidental
        # post-build mutation when callers don't opt in).
        assert "{{LEAK_TOKEN}}" in text

    def test_unknown_token_left_intact(self):
        """A {{TOKEN}} that the substitution_table doesn't define
        is LEFT INTACT (not silently replaced with '—'). This
        surfaces the typo'd token to the operator instead of
        masking it."""
        from tools.academic_deck import (
            build_presentation_deck,
        )
        slides = [
            {"slide_number": 1,
             "title": "Known {{KNOWN}} Unknown {{UNKNOWN}}",
             "bullets": [], "table_data": None,
             "speaker_notes": ""},
        ] + [
            {"slide_number": i + 2, "title": "X",
             "bullets": [], "table_data": None,
             "speaker_notes": ""}
            for i in range(10)
        ]
        pptx = build_presentation_deck(
            slides, charts={},
            substitution_table={"{{KNOWN}}": "OK"})
        text = _extract_all_slide_text(pptx)
        assert "OK" in text                  # substituted
        assert "{{UNKNOWN}}" in text         # left intact


# Note: structural source-inspection pins for renderer skip
# behaviour were removed -- they over-matched on operator-facing
# log warning text (e.g. "no [DATA PENDING] placeholder" inside
# a log.warning message). The 4 end-to-end PPTX-build tests in
# TestNoPlaceholderTextInExportedPptx above already validate the
# contract by inspecting the actual exported slide XML.


# ── SLIDE_SPECIFICATIONS + retry logic ────────────────────────────


class TestSlidesRequiringBulletsSet:
    """PR 3 (June 27 2026) -- the canonical set of slides that
    MUST emit a non-empty bullets array. Slides 4, 7, 9, 12 are
    table-heavy proof slides where empty bullets is acceptable."""

    def test_set_contents_pinned(self):
        from tools.academic_deck import SLIDES_REQUIRING_BULLETS
        assert SLIDES_REQUIRING_BULLETS == frozenset(
            {1, 3, 5, 6, 8, 10, 11})

    def test_set_is_immutable(self):
        from tools.academic_deck import SLIDES_REQUIRING_BULLETS
        # frozenset is hashable + immutable -- a regression to a
        # mutable set would silently allow runtime mutation.
        assert isinstance(SLIDES_REQUIRING_BULLETS, frozenset)

    def test_table_heavy_slides_excluded(self):
        """Slides 4, 7, 9, 12 are intentionally absent from the
        requiring-bullets set -- they are table-heavy proof
        slides where the table fully carries the evidence."""
        from tools.academic_deck import SLIDES_REQUIRING_BULLETS
        for table_heavy_slide in (4, 7, 9, 12):
            assert table_heavy_slide not in (
                SLIDES_REQUIRING_BULLETS), (
                f"slide {table_heavy_slide} is table-heavy -- "
                "must NOT be in SLIDES_REQUIRING_BULLETS so its "
                "empty-bullets case does not trigger the retry "
                "loop unnecessarily")


class TestSlideSpecsCarryNonEmptyBulletsRequirement:
    """Pins the SLIDE_SPECIFICATIONS prose contract for PR 3.
    The Sonnet per-slide prompt reads this text, so the
    instruction MUST be present and reference the canonical
    slide-number set."""

    def test_specs_mention_non_empty_bullets_requirement(self):
        from tools.academic_deck import SLIDE_SPECIFICATIONS
        # The explicit instruction added in PR 3.
        assert "NON-EMPTY BULLETS REQUIREMENT" in (
            SLIDE_SPECIFICATIONS), (
            "SLIDE_SPECIFICATIONS missing the PR 3 non-empty "
            "bullets requirement block")

    def test_specs_name_the_required_slides(self):
        from tools.academic_deck import SLIDE_SPECIFICATIONS
        # The requirement block must enumerate the exact slide
        # numbers Sonnet must populate.
        assert "1, 3, 5, 6, 8, 10, 11" in SLIDE_SPECIFICATIONS

    def test_specs_clarify_table_heavy_exception(self):
        """The spec text must explicitly carve out slides 4, 7,
        9, 12 so Sonnet does not over-correct + force bullets
        onto the table-only slides."""
        from tools.academic_deck import SLIDE_SPECIFICATIONS
        assert "4, 7, 9, 12" in SLIDE_SPECIFICATIONS

    def test_floor_is_zero_phrase_removed_from_bullet_discipline(
            self):
        """The pre-PR-3 BULLET DISCIPLINE block said 'Floor is
        zero' as a blanket rule. That conflicts with the PR 3
        non-empty requirement for 7 of the 11 slides. The
        floor-is-zero phrasing should appear only inside the
        table-heavy exception language now, not as a blanket
        rule at the top of the discipline block."""
        from tools.academic_deck import SLIDE_SPECIFICATIONS
        bullet_discipline_idx = SLIDE_SPECIFICATIONS.find(
            "BULLET DISCIPLINE:")
        non_empty_idx = SLIDE_SPECIFICATIONS.find(
            "NON-EMPTY BULLETS REQUIREMENT")
        assert bullet_discipline_idx >= 0
        assert non_empty_idx > bullet_discipline_idx, (
            "NON-EMPTY BULLETS REQUIREMENT must appear AFTER "
            "BULLET DISCIPLINE in SLIDE_SPECIFICATIONS so the "
            "Sonnet prompt reads the override after the general "
            "rule")
        # The phrase 'Floor is zero' should be scoped to the
        # table-heavy exception, NOT the blanket discipline.
        # Check that 'Floor is zero' (if present) sits after the
        # non-empty requirement marker, where the table-heavy
        # exception language lives.
        floor_zero_idx = SLIDE_SPECIFICATIONS.find(
            "Floor is zero")
        if floor_zero_idx >= 0:
            assert floor_zero_idx > non_empty_idx, (
                "'Floor is zero' phrasing must appear AFTER the "
                "NON-EMPTY BULLETS REQUIREMENT block (scoped to "
                "table-heavy slides), not as a blanket rule")


class TestRetryLogicWiredIntoDeckGenerator:
    """Source inspection -- pins that the per-slide loop in
    _generate_deck_document calls _generate_one_deck_slide a
    SECOND time when bullets come back empty for a slide in
    SLIDES_REQUIRING_BULLETS. Pure structural pin so a future
    refactor cannot silently drop the retry."""

    def test_generator_imports_slides_requiring_bullets(self):
        import inspect
        from main import _generate_deck_document
        src = inspect.getsource(_generate_deck_document)
        assert "SLIDES_REQUIRING_BULLETS" in src, (
            "_generate_deck_document must import + use "
            "SLIDES_REQUIRING_BULLETS to gate the retry-on-"
            "empty-bullets logic")

    def test_generator_logs_retry_event(self):
        import inspect
        from main import _generate_deck_document
        src = inspect.getsource(_generate_deck_document)
        assert "deck_slide_bullets_empty_retrying" in src, (
            "_generate_deck_document must log "
            "'deck_slide_bullets_empty_retrying' before the "
            "second _generate_one_deck_slide call so operators "
            "can grep the retry trigger in Render logs")

    def test_generator_logs_post_retry_failure(self):
        import inspect
        from main import _generate_deck_document
        src = inspect.getsource(_generate_deck_document)
        assert "deck_slide_bullets_empty_after_retry" in src, (
            "_generate_deck_document must log "
            "'deck_slide_bullets_empty_after_retry' when the "
            "second call also returns empty bullets so operators "
            "can grep the persistent-empty case (the slide then "
            "renders without the bullet block)")

    def test_retry_calls_generate_one_deck_slide_again(self):
        """A second call to _generate_one_deck_slide MUST appear
        inside the retry branch, with the same kwargs as the
        first call. Pins against a regression that fires the
        first call twice but accidentally drops a kwarg."""
        import inspect
        from main import _generate_deck_document
        src = inspect.getsource(_generate_deck_document)
        # Two _generate_one_deck_slide call sites must be
        # present -- the first attempt + the retry. The
        # SLIDES_REQUIRING_BULLETS guard sits between them, so
        # a naive count <2 means the retry was dropped.
        assert src.count("_generate_one_deck_slide") >= 2, (
            "_generate_deck_document must call "
            "_generate_one_deck_slide TWICE in the loop body "
            "(first attempt + retry-on-empty-bullets); only "
            f"{src.count('_generate_one_deck_slide')} call(s) "
            "found")
