"""tests/test_brief_grounding.py -- the brief-as-anchor PR.

Pins the contract that the presentation deck and analytical
appendix are grounded in the finalized executive brief. The
brief is the anchor; the deck visualizes what it argues; the
appendix supports its claims. Numbers were already guaranteed
consistent via the shared substitution table; this PR closes the
narrative-framing gap.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


# ── brief_grounding module ──────────────────────────────────────────────


class TestBriefContentHash:
    def test_consistent_hash_for_same_content(self):
        from tools.brief_grounding import brief_content_hash
        h1 = brief_content_hash("body of the brief")
        h2 = brief_content_hash("body of the brief")
        assert h1 == h2
        assert len(h1) == 16  # SHA256 prefix

    def test_different_content_different_hash(self):
        from tools.brief_grounding import brief_content_hash
        h1 = brief_content_hash("first version")
        h2 = brief_content_hash("second version")
        assert h1 != h2

    def test_empty_returns_empty_string(self):
        from tools.brief_grounding import brief_content_hash
        assert brief_content_hash("") == ""
        assert brief_content_hash(None) == ""


class TestCacheKeyWithBrief:
    def test_extended_key_includes_both_hashes(self):
        from tools.brief_grounding import cache_key_with_brief
        key = cache_key_with_brief("c421fb89", "abc123")
        assert key == "c421fb89|abc123"

    def test_empty_brief_returns_data_hash_alone(self):
        # Preserves the legacy cache-hit path for any caller that
        # hasn't wired brief grounding yet.
        from tools.brief_grounding import cache_key_with_brief
        assert cache_key_with_brief("c421fb89", "") == "c421fb89"
        assert cache_key_with_brief("c421fb89", None) == "c421fb89"


class TestSlideExclusionIsExplicit:
    """The user explicitly required slide 9 (live demo) and slide
    10 (AI methodology) be EXCLUDED from brief excerpt threading.
    The exclusion is a named frozenset constant + a dispatch
    helper that consults it FIRST. This test pins both layers so
    a future PR can't accidentally re-include the slides by
    editing the slide-to-section map directly."""

    def test_excluded_slides_constant_is_grep_able_frozenset(self):
        from tools.brief_grounding import SLIDES_EXCLUDED_FROM_BRIEF_GROUNDING
        # Pin the type so a future edit can't quietly swap to a
        # mutable container.
        assert isinstance(SLIDES_EXCLUDED_FROM_BRIEF_GROUNDING, frozenset)
        # Pin the membership so the two excluded slides are
        # locked. A future PR that removes one breaks this test.
        assert SLIDES_EXCLUDED_FROM_BRIEF_GROUNDING == frozenset({9, 10})

    def test_excluded_slides_not_in_section_map(self):
        # The slide -> brief section map intentionally OMITS
        # slides 9 and 10. Belt-and-suspenders: the dispatcher
        # ALSO checks the exclusion set first, but the map omits
        # the keys entirely so even a naive direct lookup of
        # SLIDE_TO_BRIEF_SECTION[9] would KeyError rather than
        # silently return a section name.
        from tools.brief_grounding import SLIDE_TO_BRIEF_SECTION
        assert 9 not in SLIDE_TO_BRIEF_SECTION
        assert 10 not in SLIDE_TO_BRIEF_SECTION

    def test_dispatcher_returns_none_for_excluded_slides(self):
        from tools.brief_grounding import brief_section_for_slide
        # The single dispatch point that per-slide writers MUST
        # call. Returns None for excluded slides; the per-slide
        # writer then receives an empty brief_excerpt and
        # brief_section_block returns "" so the prompt is
        # unchanged from pre-grounding behaviour.
        assert brief_section_for_slide(9) is None
        assert brief_section_for_slide(10) is None

    def test_dispatcher_returns_section_for_included_slides(self):
        from tools.brief_grounding import brief_section_for_slide
        # Spot-check a few mapped slides to confirm the dispatcher
        # actually returns the section for the included slides.
        assert brief_section_for_slide(1) == "Executive Summary"
        assert brief_section_for_slide(4) == "Key Findings and Insights"
        assert brief_section_for_slide(11) == "Final Recommendations"


class TestBriefSectionExcerpt:
    _BRIEF_TEXT = """\
## Executive Summary

The blend outperforms benchmark on OOS Sharpe.

## Methodology Overview

We use HMM regime detection citing Hamilton (1989).

## Key Findings and Insights

Drawdown reduction of 50% versus benchmark.

## Limitations and Risks

Sample size 40 months.

## Final Recommendations

We recommend the regime-conditional blend.

## Visuals

Four charts demonstrate the findings.
"""

    def test_extracts_named_section_body(self):
        from tools.brief_grounding import brief_section_excerpt
        out = brief_section_excerpt(self._BRIEF_TEXT, "Executive Summary")
        assert "The blend outperforms benchmark" in out
        # Body of OTHER sections must NOT leak in.
        assert "Hamilton" not in out
        assert "Drawdown reduction" not in out

    def test_empty_brief_returns_empty(self):
        from tools.brief_grounding import brief_section_excerpt
        assert brief_section_excerpt("", "Executive Summary") == ""
        assert brief_section_excerpt(None, "Executive Summary") == ""

    def test_none_section_returns_empty(self):
        # Slides 9 + 10 pass section_name=None; the excerpt fn
        # must short-circuit so the per-slide writer receives
        # an empty string.
        from tools.brief_grounding import brief_section_excerpt
        assert brief_section_excerpt(self._BRIEF_TEXT, None) == ""

    def test_missing_section_returns_empty(self):
        # A brief that doesn't carry the requested heading
        # returns "" rather than crashing.
        from tools.brief_grounding import brief_section_excerpt
        text = "## Some Other Heading\n\nBody."
        assert brief_section_excerpt(text, "Executive Summary") == ""


class TestBriefGroundingBlock:
    def test_full_block_contains_brief_text(self):
        from tools.brief_grounding import brief_grounding_block
        block = brief_grounding_block(
            "The blend outperforms benchmark.")
        # The user-locked phrasing for the NARRATIVE ANCHOR
        # block (2026-06-21). Pinning the exact framing keeps a
        # future PR from quietly softening the language.
        assert "BRIEF GROUNDING CONTEXT" in block
        assert "NARRATIVE ANCHOR" in block
        assert "The blend outperforms benchmark." in block
        # The block must instruct the model to visualize what
        # the brief argues, not re-derive its own conclusions.
        assert "visualizes and presents" in block
        assert "re-derive" in block

    def test_empty_brief_returns_empty_block(self):
        # The composition pattern `prompt + block` is a no-op
        # when brief_text is empty -- pre-grounding behaviour
        # preserved for callers that don't pass brief_text.
        from tools.brief_grounding import brief_grounding_block
        assert brief_grounding_block("") == ""
        assert brief_grounding_block(None) == ""


class TestBriefSectionBlock:
    def test_block_carries_excerpt_and_section_name(self):
        from tools.brief_grounding import brief_section_block
        block = brief_section_block(
            "The blend outperforms benchmark.",
            "Executive Summary")
        assert "BRIEF ALIGNMENT EXCERPT" in block
        assert "Executive Summary" in block
        assert "The blend outperforms benchmark." in block

    def test_empty_excerpt_returns_empty_block(self):
        # When brief_section_excerpt returned "" (slide 9 / 10
        # excluded, or missing section), the block must compose
        # to nothing so the writer's prompt is unchanged.
        from tools.brief_grounding import brief_section_block
        assert brief_section_block("", "Executive Summary") == ""

    def test_none_section_returns_empty_block(self):
        # The dispatcher returned None (slide 9 / 10 etc.); the
        # block must short-circuit to "".
        from tools.brief_grounding import brief_section_block
        assert brief_section_block("body", None) == ""


# ── Slide-to-brief + appendix-to-brief maps ──────────────────────────────


class TestAppendixToBriefSectionMap:
    def test_data_and_methodology_align_with_brief_methodology(self):
        from tools.brief_grounding import APPENDIX_TO_BRIEF_SECTION
        assert (APPENDIX_TO_BRIEF_SECTION["appendix_data_sources"]
                == "Methodology Overview")
        assert (APPENDIX_TO_BRIEF_SECTION["appendix_methodology"]
                == "Methodology Overview")

    def test_performance_aligns_with_brief_key_findings(self):
        from tools.brief_grounding import APPENDIX_TO_BRIEF_SECTION
        assert (APPENDIX_TO_BRIEF_SECTION["appendix_performance"]
                == "Key Findings and Insights")

    def test_sensitivity_aligns_with_brief_limitations(self):
        from tools.brief_grounding import APPENDIX_TO_BRIEF_SECTION
        assert (APPENDIX_TO_BRIEF_SECTION["appendix_sensitivity"]
                == "Limitations and Risks")

    def test_appendix_specific_sections_have_no_brief_counterpart(self):
        # Portfolio construction + calculations are appendix-
        # specific (full 10-strategy detail; the brief uses the
        # three-strategy lens). Mapping to None means the
        # per-section writer receives no excerpt.
        from tools.brief_grounding import APPENDIX_TO_BRIEF_SECTION
        assert APPENDIX_TO_BRIEF_SECTION["appendix_portfolio_construction"] is None
        assert APPENDIX_TO_BRIEF_SECTION["appendix_calculations"] is None


# ── Story plan cache key extension ───────────────────────────────────────


class TestRefreshStoryPlanCacheKeyExtension:
    """refresh_story_plan now extends the cache key with
    brief_hash for non-brief document types. This is the
    mechanism that auto-invalidates the cached deck plan when
    Bob regenerates the brief."""

    def test_deck_storage_key_includes_brief_hash(self, monkeypatch):
        # Monkeypatch get_cached_story_plan to capture the
        # storage_hash argument the function uses.
        import asyncio
        from tools import story_plan as sp
        captured: dict = {}

        async def _fake_cached(storage_hash, doc_type):
            captured["storage_hash"] = storage_hash
            captured["doc_type"] = doc_type
            # Return a fake cached plan to short-circuit the
            # generator branches we don't care about.
            return {"_model": "claude-opus-4-7",
                    "slide_plan": [{"slide_number": 1}]}

        monkeypatch.setattr(
            sp, "get_cached_story_plan", _fake_cached)
        result = asyncio.run(sp.refresh_story_plan(
            "c421fb89", "deck",
            deck_context={}, slide_titles=[],
            brief_text="brief body",
            brief_hash="abc123"))
        # The deck plan must be looked up under the EXTENDED key.
        assert captured["storage_hash"] == "c421fb89|abc123"
        assert captured["doc_type"] == "deck"
        # Cache hit path returns the stub directly.
        assert result["cache"] == "hit"

    def test_brief_storage_key_is_data_hash_alone(self, monkeypatch):
        # Brief plan does NOT extend its cache key -- the brief
        # IS the anchor, can't be grounded in itself.
        import asyncio
        from tools import story_plan as sp
        captured: dict = {}

        async def _fake_cached(storage_hash, doc_type):
            captured["storage_hash"] = storage_hash
            return {"_model": "claude-opus-4-7",
                    "section_plan": {}}

        monkeypatch.setattr(
            sp, "get_cached_story_plan", _fake_cached)
        asyncio.run(sp.refresh_story_plan(
            "c421fb89", "brief",
            brief_context={}, rubric_sections=[],
            brief_text="should be ignored for brief itself",
            brief_hash="abc123"))
        # Brief lookup uses the bare data_hash; no pipe.
        assert captured["storage_hash"] == "c421fb89"

    def test_no_brief_hash_preserves_legacy_cache_key(
        self, monkeypatch,
    ):
        # Caller that hasn't been wired through (legacy path)
        # passes no brief_hash. Storage key must equal data_hash
        # alone so any legacy-cached row remains accessible.
        import asyncio
        from tools import story_plan as sp
        captured: dict = {}

        async def _fake_cached(storage_hash, doc_type):
            captured["storage_hash"] = storage_hash
            return {"_model": "claude-opus-4-7",
                    "slide_plan": []}

        monkeypatch.setattr(
            sp, "get_cached_story_plan", _fake_cached)
        asyncio.run(sp.refresh_story_plan(
            "c421fb89", "deck",
            deck_context={}, slide_titles=[]))
        assert captured["storage_hash"] == "c421fb89"


# ── 409 gate on deck + appendix exports ──────────────────────────────────


class TestDeckAppendixGateOnMissingBrief:
    """Both deck and appendix generators raise HTTPException(409)
    when the user has no executive_brief editor draft. The 409
    detail string surfaces in the frontend
    DocumentGenerationPanel error slot (see frontend audit in PR
    body). The error message names the constraint clearly so
    Bob / Molly know to generate the brief first."""

    def test_deck_generator_raises_409_when_no_brief(self, monkeypatch):
        import asyncio
        import main as main_module
        from fastapi import HTTPException

        async def _fake_no_brief(_email):
            return None

        monkeypatch.setattr(
            "tools.brief_grounding.get_brief_for_grounding",
            _fake_no_brief)

        try:
            asyncio.run(main_module._generate_deck_document(
                "ruurdsm@queens.edu"))
        except HTTPException as exc:
            assert exc.status_code == 409
            assert "executive brief" in (exc.detail or "").lower()
            assert "deck" in (exc.detail or "").lower()
            return
        assert False, "expected HTTPException(409)"

    def test_appendix_generator_raises_409_when_no_brief(self, monkeypatch):
        import asyncio
        import main as main_module
        from fastapi import HTTPException

        async def _fake_no_brief(_email):
            return None

        monkeypatch.setattr(
            "tools.brief_grounding.get_brief_for_grounding",
            _fake_no_brief)

        try:
            asyncio.run(main_module._generate_appendix_document(
                "ruurdsm@queens.edu"))
        except HTTPException as exc:
            assert exc.status_code == 409
            assert "executive brief" in (exc.detail or "").lower()
            assert "appendix" in (exc.detail or "").lower()
            return
        assert False, "expected HTTPException(409)"


# ── Brief text flows through to deck Pass-1 system prompt ────────────────


class TestDeckStoryPlanReceivesBriefText:
    """generate_deck_story_plan(brief_text=..., appendix_text=...)
    composes BOTH grounding blocks onto the Pass-1 Opus system
    prompt + the GENERATION RULES closer. The arbiter sees the
    FULL brief + FULL appendix once per plan generation; the
    per-slide writers see per-section excerpts only (cheaper)."""

    def test_brief_text_landed_in_system_prompt(self, monkeypatch):
        # Capture the system_prompt passed into the harness call
        # so we can inspect the assembled prompt.
        from tools import story_plan as sp
        captured: dict = {}

        def _capture(**kwargs):
            captured["system_prompt"] = kwargs.get("system_prompt", "")
            raise RuntimeError("captured")

        monkeypatch.setattr(sp, "_run_pass1_with_harness", _capture)
        sp.generate_deck_story_plan(
            deck_context={"validated_constants": {}},
            slide_titles=["A", "B"],
            brief_text="UNIQUE_MARKER_brief_body_for_test")
        assert "UNIQUE_MARKER_brief_body_for_test" in captured["system_prompt"]
        assert "BRIEF GROUNDING CONTEXT" in captured["system_prompt"]
        assert "NARRATIVE ANCHOR" in captured["system_prompt"]

    def test_appendix_text_landed_in_system_prompt(self, monkeypatch):
        # Confirm the appendix grounding block composes onto the
        # Pass-1 prompt when appendix_text is supplied.
        from tools import story_plan as sp
        captured: dict = {}

        def _capture(**kwargs):
            captured["system_prompt"] = kwargs.get("system_prompt", "")
            raise RuntimeError("captured")

        monkeypatch.setattr(sp, "_run_pass1_with_harness", _capture)
        sp.generate_deck_story_plan(
            deck_context={"validated_constants": {}},
            slide_titles=["A", "B"],
            brief_text="brief body",
            appendix_text="UNIQUE_MARKER_appendix_body_for_test")
        assert "UNIQUE_MARKER_appendix_body_for_test" in captured["system_prompt"]
        assert "APPENDIX GROUNDING CONTEXT" in captured["system_prompt"]
        assert "EVIDENTIARY BACKING" in captured["system_prompt"]

    def test_generation_rules_block_present_with_grounding(
        self, monkeypatch,
    ):
        # When EITHER brief_text or appendix_text is supplied,
        # the GENERATION RULES block closes the grounding
        # section so the arbiter sees the constraints framing
        # what to do with the upstream documents.
        from tools import story_plan as sp
        captured: dict = {}

        def _capture(**kwargs):
            captured["system_prompt"] = kwargs.get("system_prompt", "")
            raise RuntimeError("captured")

        monkeypatch.setattr(sp, "_run_pass1_with_harness", _capture)
        sp.generate_deck_story_plan(
            deck_context={"validated_constants": {}},
            slide_titles=["A", "B"],
            brief_text="brief body",
            appendix_text="appendix body")
        prompt = captured["system_prompt"]
        assert "GENERATION RULES" in prompt
        # Pin the slide 9/10 exclusion clause appears in the
        # rules so the arbiter sees the carve-out alongside the
        # grounding constraint.
        assert "Slides 9 and 10 are excluded" in prompt
        assert "LIVE_DEMO_SEQUENCE" in prompt

    def test_no_brief_text_preserves_legacy_prompt(self, monkeypatch):
        from tools import story_plan as sp
        captured: dict = {}

        def _capture(**kwargs):
            captured["system_prompt"] = kwargs.get("system_prompt", "")
            raise RuntimeError("captured")

        monkeypatch.setattr(sp, "_run_pass1_with_harness", _capture)
        sp.generate_deck_story_plan(
            deck_context={"validated_constants": {}},
            slide_titles=["A", "B"])
        # No grounding blocks AND no GENERATION RULES when
        # neither upstream document is supplied (legacy path).
        prompt = captured["system_prompt"]
        assert "BRIEF GROUNDING CONTEXT" not in prompt
        assert "APPENDIX GROUNDING CONTEXT" not in prompt
        assert "GENERATION RULES" not in prompt


# ── Appendix gate for deck generation (the second upstream doc) ──────────


class TestDeckRequiresAppendixGate:
    """The deck is the THIRD document in the generation order
    (brief -> appendix -> deck). The brief gate alone is not
    enough -- the deck Pass-1 Opus arbiter needs the appendix
    grounding to confirm that technical claims it puts on slides
    are traceable to the appendix's per-strategy detail.

    Two 409 gates run in sequence. The brief gate fires first
    (clearer 'start at the beginning' message); the appendix
    gate fires only when the brief exists but the appendix
    doesn't."""

    def test_deck_raises_409_when_brief_exists_but_appendix_missing(
        self, monkeypatch,
    ):
        import asyncio
        import main as main_module
        from fastapi import HTTPException

        async def _fake_brief(_email):
            return {
                "content_text": "brief body",
                "content_hash": "abc123",
                "draft_id": 1,
            }

        async def _fake_no_appendix(_email):
            return None

        monkeypatch.setattr(
            "tools.brief_grounding.get_brief_for_grounding",
            _fake_brief)
        monkeypatch.setattr(
            "tools.brief_grounding.get_appendix_for_grounding",
            _fake_no_appendix)

        try:
            asyncio.run(main_module._generate_deck_document(
                "ruurdsm@queens.edu"))
        except HTTPException as exc:
            assert exc.status_code == 409
            # Detail must name the appendix explicitly so Bob /
            # Molly know which document is missing.
            assert "analytical appendix" in (exc.detail or "").lower()
            # And reference the canonical generation order so
            # the user understands the constraint.
            assert "brief" in (exc.detail or "").lower()
            assert "appendix" in (exc.detail or "").lower()
            assert "deck" in (exc.detail or "").lower()
            return
        assert False, "expected HTTPException(409)"

    def test_get_appendix_for_grounding_returns_none_when_no_draft(
        self, monkeypatch,
    ):
        # The grounding helper returns None when the user has no
        # appendix draft. Same shape as get_brief_for_grounding.
        import asyncio
        from tools import brief_grounding as bg

        async def _no_draft(_email, _doc_type):
            return None

        monkeypatch.setattr(
            "tools.editor_drafts.get_current_draft", _no_draft)
        out = asyncio.run(bg.get_appendix_for_grounding(
            "ruurdsm@queens.edu"))
        assert out is None

    def test_get_appendix_for_grounding_returns_payload_when_draft_exists(
        self, monkeypatch,
    ):
        import asyncio
        from tools import brief_grounding as bg

        async def _draft(_email, doc_type):
            assert doc_type == "analytical_appendix"
            return {
                "id": 42,
                "content_text": "appendix body content",
            }

        monkeypatch.setattr(
            "tools.editor_drafts.get_current_draft", _draft)
        out = asyncio.run(bg.get_appendix_for_grounding(
            "ruurdsm@queens.edu"))
        assert out is not None
        assert out["content_text"] == "appendix body content"
        assert out["draft_id"] == 42
        # Hash is non-empty SHA256 prefix.
        assert len(out["content_hash"]) == 16


# ── 3-document cache key ─────────────────────────────────────────────────


class TestCacheKeyWithBriefAndAppendix:
    """The deck's cache key extends from
    (data_hash, 'deck') to
    (data_hash | brief_hash | appendix_hash, 'deck') so a regen
    of EITHER upstream document auto-invalidates the cached
    deck plan."""

    def test_full_three_doc_key(self):
        from tools.brief_grounding import (
            cache_key_with_brief_and_appendix,
        )
        key = cache_key_with_brief_and_appendix(
            "c421fb89", "brief_abc", "appendix_xyz")
        assert key == "c421fb89|brief_abc|appendix_xyz"

    def test_missing_appendix_falls_back_to_brief_only(self):
        # A future opt-out of appendix grounding (or a transient
        # appendix-read failure that returns "") should preserve
        # the brief-grounded cache shape.
        from tools.brief_grounding import (
            cache_key_with_brief_and_appendix,
        )
        key = cache_key_with_brief_and_appendix(
            "c421fb89", "brief_abc", "")
        assert key == "c421fb89|brief_abc"

    def test_missing_both_falls_back_to_data_hash_alone(self):
        from tools.brief_grounding import (
            cache_key_with_brief_and_appendix,
        )
        key = cache_key_with_brief_and_appendix(
            "c421fb89", "", "")
        assert key == "c421fb89"

    def test_refresh_story_plan_uses_three_doc_key_for_deck(
        self, monkeypatch,
    ):
        # The deck branch of refresh_story_plan uses
        # cache_key_with_brief_and_appendix; the stored hash
        # must include both upstream hashes.
        import asyncio
        from tools import story_plan as sp
        captured: dict = {}

        async def _fake_cached(storage_hash, doc_type):
            captured["storage_hash"] = storage_hash
            return {"_model": "claude-opus-4-7",
                    "slide_plan": [{"slide_number": 1}]}

        monkeypatch.setattr(
            sp, "get_cached_story_plan", _fake_cached)
        asyncio.run(sp.refresh_story_plan(
            "c421fb89", "deck",
            deck_context={}, slide_titles=[],
            brief_text="brief body",
            brief_hash="brief_abc",
            appendix_text="appendix body",
            appendix_hash="appendix_xyz"))
        assert captured["storage_hash"] == (
            "c421fb89|brief_abc|appendix_xyz")
