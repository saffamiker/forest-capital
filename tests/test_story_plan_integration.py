"""tests/test_story_plan_integration.py -- PR #333 integration tests.

The story plan foundation shipped in PR #330 (the 4-pass generator +
cache + deterministic fallback). This PR wires it into the deck and
brief generation pipelines.

Tests pin:
  * Per-slide injection: the locked story plan entry's headline,
    numeric_anchors, and bullets are prepended to the slide prompt so
    the per-slide Sonnet call writes prose around the lock.
  * Speaker notes override: the parsed slide dict's speaker_notes is
    OVERWRITTEN with the plan's speaker_notes after parse -- the
    plan is the source of truth, the LLM never gets to "improve" it.
  * Fall-open: a None plan entry leaves _generate_one_deck_slide
    behaving exactly as before (the contract that makes this PR
    safe to ship -- a missing plan never blocks slide generation).
  * Brief section injection: the same pattern threads section_plan
    entries through the spec list.
  * Audit story_plan_violation: a slide value not in numeric_anchors
    and not in the cache flags; values in the anchors do not flag;
    no-plan skips the check silently.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")


# ── Per-slide injection ─────────────────────────────────────────────────


class TestSlidePromptInjection:
    """The slide_plan_entry kwarg is the integration point. When
    supplied, the user_message handed to call_claude must carry the
    LOCKED CONTRACT block; without it, the prompt is unchanged from
    the pre-PR-333 shape."""

    def test_plan_entry_appears_in_user_message(self, monkeypatch):
        from main import _generate_one_deck_slide

        captured: dict = {}

        def _fake_call_claude(model, system, user, *, max_tokens,
                              trigger):
            captured["user"] = user
            # Return a minimal parseable slide so the call returns
            # successfully -- the test only inspects the prompt that
            # was built, not what the slide ends up looking like.
            return ('{"slide_number": 1, "title": "T", '
                    '"bullets": ["b1"], "speaker_notes": "ai notes"}')

        monkeypatch.setattr(
            "agents.base.call_claude", _fake_call_claude)

        plan_entry = {
            "slide_number": 1,
            "headline": "OOS Sharpe 1.24 vs 0.73",
            "key_visual": "cumulative_return_post_2022",
            "numeric_anchors": {
                "oos_sharpe_blend": 1.24,
                "oos_sharpe_benchmark": 0.73,
            },
            "slide_bullets": ["Bullet anchor 1", "Bullet anchor 2"],
            "speaker_notes": "Locked speaker notes.",
            "transition_to_next": "Next slide.",
        }
        out = _generate_one_deck_slide(
            1, {"summary": "ctx"}, 10, slide_plan_entry=plan_entry)
        assert out is not None
        # The LOCKED CONTRACT block is present.
        msg = captured["user"]
        assert "STORY PLAN FOR THIS SLIDE (do not deviate)" in msg
        assert "OOS Sharpe 1.24 vs 0.73" in msg
        assert "cumulative_return_post_2022" in msg
        assert "oos_sharpe_blend" in msg
        assert "1.24" in msg
        assert "Bullet anchor 1" in msg
        # The "layout and prose only" hard guard is also present.
        assert "Your job is layout and prose formatting only" in msg

    def test_no_plan_entry_leaves_prompt_unchanged(self, monkeypatch):
        """The fail-open contract: when slide_plan_entry is None the
        user_message must NOT carry the LOCKED CONTRACT block (so the
        deck behaves exactly as it did pre-PR-333)."""
        from main import _generate_one_deck_slide

        captured: dict = {}

        def _fake_call_claude(model, system, user, *, max_tokens,
                              trigger):
            captured["user"] = user
            return ('{"slide_number": 1, "title": "T", '
                    '"bullets": ["b1"], "speaker_notes": "ai notes"}')

        monkeypatch.setattr(
            "agents.base.call_claude", _fake_call_claude)

        _generate_one_deck_slide(1, {"summary": "ctx"}, 10)
        msg = captured["user"]
        assert "STORY PLAN FOR THIS SLIDE" not in msg

    def test_speaker_notes_overridden_from_plan(self, monkeypatch):
        """After parsing the slide JSON, the speaker_notes from the
        story plan must REPLACE whatever the LLM wrote -- the plan is
        the source of truth and the per-slide Sonnet pass never gets
        to override it. This pins the contract that PPTX build picks
        up the Opus-arbited notes, not the per-slide retread."""
        from main import _generate_one_deck_slide

        def _fake_call_claude(model, system, user, *, max_tokens,
                              trigger):
            return ('{"slide_number": 1, "title": "T", '
                    '"bullets": ["b"], '
                    '"speaker_notes": "LLM rewrote the notes badly."}')

        monkeypatch.setattr(
            "agents.base.call_claude", _fake_call_claude)

        plan_entry = {
            "slide_number": 1,
            "headline": "h", "key_visual": "kv",
            "numeric_anchors": {},
            "slide_bullets": [],
            "speaker_notes": "Locked notes from the Opus arbiter.",
        }
        out = _generate_one_deck_slide(
            1, {}, 10, slide_plan_entry=plan_entry)
        assert out is not None
        # The plan's notes won; the LLM's variant was discarded.
        assert out["speaker_notes"] == (
            "Locked notes from the Opus arbiter.")

    def test_speaker_notes_unchanged_when_plan_lacks_them(
            self, monkeypatch):
        """If the plan entry has no speaker_notes (or empty), the
        LLM's notes pass through. Defensive against partial plans."""
        from main import _generate_one_deck_slide

        def _fake_call_claude(model, system, user, *, max_tokens,
                              trigger):
            return ('{"slide_number": 1, "title": "T", '
                    '"bullets": ["b"], '
                    '"speaker_notes": "LLM notes."}')

        monkeypatch.setattr(
            "agents.base.call_claude", _fake_call_claude)

        plan_entry = {
            "slide_number": 1, "headline": "h", "key_visual": "kv",
            "numeric_anchors": {}, "slide_bullets": [],
            "speaker_notes": "",
        }
        out = _generate_one_deck_slide(
            1, {}, 10, slide_plan_entry=plan_entry)
        assert out is not None
        assert out["speaker_notes"] == "LLM notes."


# ── Brief section injection ─────────────────────────────────────────────


class TestBriefSectionInjection:

    def test_section_plan_prepended_to_each_spec_task(self):
        from main import _inject_brief_section_plan

        specs = [
            {"key": "executive_summary", "task": "ORIGINAL TASK A"},
            {"key": "methodology",       "task": "ORIGINAL TASK B"},
        ]
        section_plan = {
            "executive_summary": {
                "key_message": "Lead with the verdict.",
                "numeric_anchors": {"oos_sharpe_blend": 1.24},
                "target_length_words": 250,
            },
            "methodology": {
                "key_message": "HMM + OOS window + Carhart.",
                "numeric_anchors": {},
                "target_length_words": 300,
            },
        }
        out = _inject_brief_section_plan(specs, section_plan)
        assert len(out) == 2
        # Each spec's task gets the NUMERIC_PLACEHOLDER_GUIDE
        # prepended (June 21 2026, Layer-1 substitution PR) followed
        # by the SECTION PLAN block + the EXECUTIVE_VOICE_REQUIREMENT
        # block. Pin the head + check both load-bearing headers
        # survive so a future regression can't quietly drop either.
        for spec in out:
            assert spec["task"].startswith(
                "DETERMINISTIC FIGURES REQUIREMENT:")
            assert "SECTION PLAN (do not deviate):" in spec["task"]
            assert "VOICE AND AUDIENCE REQUIREMENT" in spec["task"]
            assert "senior investment professional addressing a CIO" \
                in spec["task"]
            # The token contract is the load-bearing instruction the
            # substitution architecture relies on -- pin the verbatim
            # placeholder name list head.
            assert "{{OOS_SHARPE_BLEND}}" in spec["task"]
        # The original task content survives below the block.
        assert "ORIGINAL TASK A" in out[0]["task"]
        assert "ORIGINAL TASK B" in out[1]["task"]
        # The numeric anchors land verbatim.
        assert "1.24" in out[0]["task"]
        assert "Lead with the verdict." in out[0]["task"]
        # The 'investment conclusions not next steps' guard is in the
        # block so the section 5 prompt cannot drift back to a
        # 'next steps' framing.
        assert "investment conclusions" in out[0]["task"]

    def test_section_with_no_plan_entry_passes_through_unchanged(self):
        """A spec whose key is absent from the section_plan dict must
        be returned unchanged -- defensive against a partial plan
        (e.g. Opus only emitted 4 of 6 sections)."""
        from main import _inject_brief_section_plan
        specs = [
            {"key": "executive_summary", "task": "ORIGINAL TASK"},
            {"key": "novel_section",     "task": "UNTOUCHED"},
        ]
        section_plan = {
            "executive_summary": {
                "key_message": "x", "numeric_anchors": {},
                "target_length_words": 200},
        }
        out = _inject_brief_section_plan(specs, section_plan)
        assert out[1] == {"key": "novel_section", "task": "UNTOUCHED"}

    def test_empty_section_plan_returns_specs_verbatim(self):
        from main import _inject_brief_section_plan
        specs = [{"key": "executive_summary", "task": "ORIGINAL"}]
        # An empty dict means no plan was resolved; specs unchanged.
        assert _inject_brief_section_plan(specs, {}) == specs


# ── Audit: story plan violation check ───────────────────────────────────


class TestStoryPlanViolationCheck:

    plan_slides = [
        {
            "slide_number": 1,
            "headline": "OOS Sharpe 1.24",
            "key_visual": "kv",
            "numeric_anchors": {
                "oos_sharpe_blend": 1.24,
                "oos_sharpe_benchmark": 0.73,
            },
            "slide_bullets": [],
            "speaker_notes": "",
        },
        {
            "slide_number": 2,
            "headline": "Drawdown",
            "key_visual": "kv",
            "numeric_anchors": {"max_drawdown_blend": 0.253},
            "slide_bullets": [],
            "speaker_notes": "",
        },
    ]

    def test_value_not_in_anchors_or_cache_flags(self):
        from tools.document_audit import check_story_plan_violations
        slides = [
            {
                "slide_number": 1,
                "title": "T",
                # 0.86 is NOT in anchors (which have 1.24 and 0.73)
                # and NOT in the cache (empty). Must flag.
                "bullets": ["The OOS Sharpe is 0.86 over the period."],
                "speaker_notes": "",
            },
        ]
        flags = check_story_plan_violations(
            slides, self.plan_slides, strategy_cache={})
        assert len(flags) >= 1
        f = flags[0]
        assert f["type"] == "story_plan_violation"
        assert f["slide"] == 1
        assert f["value"] == 0.86

    def test_value_matching_anchor_does_not_flag(self):
        from tools.document_audit import check_story_plan_violations
        slides = [
            {
                "slide_number": 1,
                "title": "T",
                "bullets": ["The OOS Sharpe is 1.24 over the period."],
                "speaker_notes": "",
            },
        ]
        flags = check_story_plan_violations(
            slides, self.plan_slides, strategy_cache={})
        # 1.24 is in the slide-1 anchors -- no flag.
        assert flags == []

    def test_value_within_tolerance_of_anchor_does_not_flag(self):
        """1.241 vs anchor 1.24 -- diff 0.001 < 0.01 tolerance, no flag."""
        from tools.document_audit import check_story_plan_violations
        slides = [
            {"slide_number": 1, "title": "T",
             "bullets": ["Sharpe 1.241 stands out."], "speaker_notes": ""},
        ]
        flags = check_story_plan_violations(
            slides, self.plan_slides, strategy_cache={})
        assert flags == []

    def test_value_in_cache_does_not_flag(self):
        from tools.document_audit import check_story_plan_violations
        # 0.86 is in the cache (under sharpe_ratio for Regime Switching)
        # so the audit should NOT flag it even though it's missing from
        # the plan anchors. The plan is one source of truth; the cache
        # is the other.
        cache = {
            "Regime Switching": {
                "sharpe_ratio": 0.86,
            },
        }
        slides = [
            {"slide_number": 1, "title": "T",
             "bullets": ["Sharpe 0.86."], "speaker_notes": ""},
        ]
        flags = check_story_plan_violations(
            slides, self.plan_slides, strategy_cache=cache)
        assert flags == []

    def test_no_plan_skips_silently(self):
        from tools.document_audit import check_story_plan_violations
        slides = [{"slide_number": 1, "bullets": ["random 999.99 num"],
                   "speaker_notes": ""}]
        # No plan slides supplied -> check has no opinion, returns [].
        assert check_story_plan_violations(slides, None) == []
        assert check_story_plan_violations(slides, []) == []

    def test_no_slides_returns_empty(self):
        from tools.document_audit import check_story_plan_violations
        assert check_story_plan_violations(
            [], self.plan_slides) == []
        assert check_story_plan_violations(
            None, self.plan_slides) == []  # type: ignore[arg-type]


class TestAuditDispatcherWiresStoryPlanCheck:
    """The dispatcher accepts slides + story_plan_slides kwargs and
    surfaces story_plan flags in flag_counts."""

    def test_dispatcher_runs_check_when_both_supplied(self):
        from tools.document_audit import audit_document
        plan_slides = [
            {"slide_number": 1, "numeric_anchors": {"sharpe": 1.24}},
        ]
        slides = [
            {"slide_number": 1, "bullets": ["Sharpe 0.86."]},
        ]
        result = audit_document(
            text="", document_type="presentation_deck",
            strategy_cache={},
            slides=slides, story_plan_slides=plan_slides)
        # Story plan violation flagged.
        assert result.flag_counts["story_plan"] >= 1
        # The skipped dict does NOT carry the story_plan key when the
        # check actually ran.
        assert "story_plan" not in result.skipped

    def test_dispatcher_skips_when_no_plan(self):
        from tools.document_audit import audit_document
        result = audit_document(
            text="", document_type="presentation_deck",
            strategy_cache={})
        # Skip reason is surfaced so the frontend never confuses
        # "no violations" with "the check did not run".
        assert result.skipped.get("story_plan") == (
            "no_plan_or_no_slides")
        assert result.flag_counts["story_plan"] == 0
