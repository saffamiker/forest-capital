"""tests/test_council_focus_brief.py -- June 27 2026.

Pins the optional pre-review focus-brief plumbing on the
/api/council/academic-review endpoint + the shared context builder.
The brief is the user-facing answer to "council missed it on the
first pass" -- it directs attention without blinding the council
to other issues.

Test groups:

  TestFocusBriefMax
    The FOCUS_BRIEF_MAX_CHARS constant is exactly 1000 (frontend
    textarea maxLength keys off this; an off-by-one drift would
    corrupt the contract).

  TestBuildContextBlockInjection
    build_review_context_block emits the brief section above the
    documents block with the spec's verbatim 'Prioritize / Do not
    limit' instruction. None / empty / whitespace omits the
    section entirely (legacy byte-for-byte behaviour).

  TestBuildContextBlockTruncation
    A brief longer than FOCUS_BRIEF_MAX_CHARS is truncated to
    exactly the limit + ellipsis, even though the endpoint usually
    truncates upstream (defence in depth -- a direct caller can't
    blow the prompt budget).

  TestBuildContextBlockPosition
    The brief section must appear ABOVE the documents block so
    agents read the directive before scanning the document content.

  TestGatherReviewContextThreading
    The kwarg threads from gather_review_context through to
    build_review_context_block. (Smoke-test only: no DB; covers
    that the parameter was added in both signatures.)
"""
from __future__ import annotations

import inspect
import os

import pytest


os.environ.setdefault("ENVIRONMENT", "test")


# ── Constant ──────────────────────────────────────────────────────────


class TestFocusBriefMax:

    def test_max_chars_is_1000(self):
        from agents.academic_review import FOCUS_BRIEF_MAX_CHARS
        assert FOCUS_BRIEF_MAX_CHARS == 1000


# ── Helper fixtures ──────────────────────────────────────────────────


def _minimal_analytics() -> dict:
    return {
        "strategy_count": 3,
        "performance_range": None,
        "risk_free_rate": 0.04,
        "analytics_components": [],
    }


def _minimal_docs() -> dict[str, list[dict]]:
    return {
        "brief_review": [],
        "deck_review": [],
        "appendix_review": [],
        "presentation_script": [],
    }


# ── Context-block injection ──────────────────────────────────────────


class TestBuildContextBlockInjection:

    def test_brief_present_emits_section(self):
        from agents.academic_review import (
            build_review_context_block,
        )
        out = build_review_context_block(
            _minimal_analytics(), _minimal_docs(),
            focus_brief="Check Section B Table B.1 column.")
        assert "REVIEWER FOCUS BRIEF (from document author):" in out
        assert "Check Section B Table B.1 column." in out

    def test_brief_carries_do_not_limit_instruction(self):
        """The 'Do not limit your review to only these areas'
        instruction is verbatim from the user spec and load-bearing
        -- without it agents would treat the brief as scope
        reduction. Pin the exact phrasing."""
        from agents.academic_review import (
            build_review_context_block,
        )
        out = build_review_context_block(
            _minimal_analytics(), _minimal_docs(),
            focus_brief="Whatever")
        assert (
            "Prioritize these areas in your review. "
            "Do not limit your review to only these areas -- "
            "surface all issues found."
        ) in out

    def test_none_omits_section(self):
        from agents.academic_review import (
            build_review_context_block,
        )
        out = build_review_context_block(
            _minimal_analytics(), _minimal_docs(),
            focus_brief=None)
        assert "REVIEWER FOCUS BRIEF" not in out

    def test_empty_string_omits_section(self):
        from agents.academic_review import (
            build_review_context_block,
        )
        out = build_review_context_block(
            _minimal_analytics(), _minimal_docs(),
            focus_brief="")
        assert "REVIEWER FOCUS BRIEF" not in out

    def test_whitespace_only_omits_section(self):
        from agents.academic_review import (
            build_review_context_block,
        )
        out = build_review_context_block(
            _minimal_analytics(), _minimal_docs(),
            focus_brief="   \n\t  ")
        assert "REVIEWER FOCUS BRIEF" not in out

    def test_omitted_kwarg_matches_legacy_output(self):
        """A run that doesn't pass focus_brief at all must produce
        byte-for-byte the same context block as the pre-feature
        baseline. Pin by comparing against a None-passed call --
        both paths should be identical."""
        from agents.academic_review import (
            build_review_context_block,
        )
        no_arg = build_review_context_block(
            _minimal_analytics(), _minimal_docs())
        none_arg = build_review_context_block(
            _minimal_analytics(), _minimal_docs(),
            focus_brief=None)
        assert no_arg == none_arg


# ── Truncation guard ─────────────────────────────────────────────────


class TestBuildContextBlockTruncation:

    def test_oversized_brief_truncated_to_max_chars(self):
        from agents.academic_review import (
            FOCUS_BRIEF_MAX_CHARS, build_review_context_block,
        )
        import re
        out = build_review_context_block(
            _minimal_analytics(), _minimal_docs(),
            focus_brief="X" * (FOCUS_BRIEF_MAX_CHARS + 500))
        m = re.search(
            r"REVIEWER FOCUS BRIEF .*?\n(X+)",
            out, re.DOTALL)
        assert m is not None
        x_run = len(m.group(1))
        assert x_run == FOCUS_BRIEF_MAX_CHARS, (
            f"expected {FOCUS_BRIEF_MAX_CHARS} X chars, "
            f"got {x_run}")
        # Ellipsis appended after truncation.
        assert chr(0x2026) in out

    def test_exactly_max_chars_not_truncated(self):
        from agents.academic_review import (
            FOCUS_BRIEF_MAX_CHARS, build_review_context_block,
        )
        brief = "Y" * FOCUS_BRIEF_MAX_CHARS
        out = build_review_context_block(
            _minimal_analytics(), _minimal_docs(),
            focus_brief=brief)
        # The full brief is present and no ellipsis appended.
        assert brief in out
        assert chr(0x2026) not in out


# ── Positioning ──────────────────────────────────────────────────────


class TestBuildContextBlockPosition:

    def test_brief_appears_above_documents(self):
        from agents.academic_review import (
            build_review_context_block,
        )
        out = build_review_context_block(
            _minimal_analytics(),
            {"brief_review": [{
                "document_type": "brief_review",
                "name": "Executive Brief",
                "content_text": "body",
            }]},
            focus_brief="X")
        brief_idx = out.find("REVIEWER FOCUS BRIEF")
        docs_idx = out.find("PROJECT DOCUMENTS")
        assert brief_idx >= 0
        assert docs_idx >= 0
        assert brief_idx < docs_idx, (
            "brief must appear ABOVE documents so agents read the "
            "directive before scanning the content")

    def test_brief_appears_above_primary_document_for_per_doc_review(self):
        from agents.academic_review import (
            build_review_context_block,
        )
        out = build_review_context_block(
            _minimal_analytics(),
            {"brief_review": [{
                "document_type": "brief_review",
                "name": "Executive Brief",
                "content_text": "body",
            }]},
            target_review_type="brief_review",
            focus_brief="X")
        brief_idx = out.find("REVIEWER FOCUS BRIEF")
        target_idx = out.find("PRIMARY DOCUMENT FOR REVIEW")
        assert brief_idx >= 0
        assert target_idx >= 0
        assert brief_idx < target_idx


# ── Signature threading ─────────────────────────────────────────────


class TestGatherReviewContextThreading:
    """Defensive: confirm the focus_brief kwarg threads from the
    endpoint -> gather_review_context -> build_review_context_block.
    Signature inspection only -- no DB / agent calls."""

    def test_gather_review_context_accepts_focus_brief(self):
        from agents.academic_review import gather_review_context
        sig = inspect.signature(gather_review_context)
        assert "focus_brief" in sig.parameters
        # Optional with None default.
        param = sig.parameters["focus_brief"]
        assert param.default is None

    def test_build_review_context_block_accepts_focus_brief(self):
        from agents.academic_review import (
            build_review_context_block,
        )
        sig = inspect.signature(build_review_context_block)
        assert "focus_brief" in sig.parameters
        param = sig.parameters["focus_brief"]
        assert param.default is None

    def test_council_endpoint_source_parses_focus_brief(self):
        """Structural -- the endpoint body parses request.json() and
        forwards focus_brief to gather_review_context. A regression
        that drops one of those calls would silently disable the
        feature; pin against grep of the source."""
        import inspect
        from main import council_academic_review
        src = inspect.getsource(council_academic_review)
        assert "focus_brief" in src
        assert "body.get(\"focus_brief\")" in src
        assert "academic_review_focus_brief" in src   # log key
