"""
tests/test_visual_reasoning_prompts.py

Pins the prompt-contract for FEATURE 1 Commit 4 — every agent that
receives chart snapshots must carry VISUAL_REASONING_RULES (the
shared no-hallucination / fail-open guidance) in its system prompt,
and must name its specific chart set so a future edit can't silently
drop the chart-naming guidance.

The wiring contract (that visual_context is passed at the call site
and that the harness evaluator never receives it) lives in
test_chart_vision_wiring.py. This module covers only the prompt text.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")


# ── Shared rules block ────────────────────────────────────────────────────────


class TestSharedRulesBlock:
    """VISUAL_REASONING_RULES is the cross-cutting rule block. It must
    name the fail-open rule (no charts → don't cite charts), the
    don't-invent rule, and the chart-key naming convention so every
    agent that embeds it gets the same guarantees."""

    def test_module_exports_rules(self):
        from agents.base import VISUAL_REASONING_RULES
        assert isinstance(VISUAL_REASONING_RULES, str)
        assert len(VISUAL_REASONING_RULES) > 200

    def test_rules_cover_fail_open(self):
        from agents.base import VISUAL_REASONING_RULES
        # The fail-open scenario must be named explicitly — citing a
        # chart that was not attached is the hallucination this rule
        # exists to prevent.
        assert "cold-deploy" in VISUAL_REASONING_RULES.lower() or \
               "no charts" in VISUAL_REASONING_RULES.lower()
        assert "hallucination" in VISUAL_REASONING_RULES.lower()

    def test_rules_cover_invention(self):
        from agents.base import VISUAL_REASONING_RULES
        assert "never invent" in VISUAL_REASONING_RULES.lower()

    def test_rules_name_caption_convention(self):
        from agents.base import VISUAL_REASONING_RULES
        # Charts are captioned "Chart: <key> — …"; agents must refer
        # to them by key so a reader knows which image is meant.
        assert "key" in VISUAL_REASONING_RULES.lower()


# ── Council specialists ──────────────────────────────────────────────────────


class TestCouncilSpecialistPrompts:
    """Each of the four council specialists must (a) embed the shared
    rules and (b) name the COUNCIL_CHARTS set so the prompt explains
    which charts to expect."""

    def test_equity_analyst_embeds_rules(self):
        from agents.equity_analyst import _SYSTEM_PROMPT
        from agents.base import VISUAL_REASONING_RULES
        assert VISUAL_REASONING_RULES in _SYSTEM_PROMPT
        # Names the chart set's central member — the agent should know
        # what's attached.
        assert "rolling_correlation" in _SYSTEM_PROMPT
        assert "cumulative_returns" in _SYSTEM_PROMPT

    def test_fixed_income_analyst_embeds_rules(self):
        from agents.fixed_income_analyst import _SYSTEM_PROMPT
        from agents.base import VISUAL_REASONING_RULES
        assert VISUAL_REASONING_RULES in _SYSTEM_PROMPT
        # The FI prompt MUST single out rolling_correlation by name —
        # it is the direct visual evidence of the 2022 break, which is
        # the FI analyst's core responsibility.
        assert "rolling_correlation" in _SYSTEM_PROMPT
        assert "2022" in _SYSTEM_PROMPT

    def test_risk_manager_embeds_rules(self):
        from agents.risk_manager import _SYSTEM_PROMPT
        from agents.base import VISUAL_REASONING_RULES
        assert VISUAL_REASONING_RULES in _SYSTEM_PROMPT
        assert "cumulative_returns" in _SYSTEM_PROMPT

    def test_quant_backtester_embeds_rules(self):
        from agents.quant_backtester import _SYSTEM_PROMPT
        from agents.base import VISUAL_REASONING_RULES
        assert VISUAL_REASONING_RULES in _SYSTEM_PROMPT
        # The quant prompt names rolling_excess_return — the visual
        # signature of overfitting at the OOS boundary.
        assert "rolling_excess_return" in _SYSTEM_PROMPT


# ── CIO ──────────────────────────────────────────────────────────────────────


class TestCIOPrompt:
    def test_cio_embeds_rules(self):
        from agents.cio import _SYSTEM_PROMPT
        from agents.base import VISUAL_REASONING_RULES
        assert VISUAL_REASONING_RULES in _SYSTEM_PROMPT
        # The CIO's synthesis paragraph should reference at most two
        # visual landmarks; the prompt must guide that explicitly.
        assert "visual" in _SYSTEM_PROMPT.lower()


# ── Academic Review ──────────────────────────────────────────────────────────


class TestAcademicReviewPrompts:
    """Peer prompt and arbiter instructions both embed visual guidance.
    The peer prompt is built per-agent (different lens / name); the
    arbiter's _ARBITER_INSTRUCTIONS is a module-level constant."""

    def test_peer_prompt_embeds_rules(self):
        from agents.academic_review import _PEER_AGENTS, _peer_system_prompt
        from agents.base import VISUAL_REASONING_RULES
        prompt = _peer_system_prompt(_PEER_AGENTS["equity_analyst"])
        assert VISUAL_REASONING_RULES in prompt
        # ACADEMIC_REVIEW_CHARTS-specific keys named.
        assert "drawdown_periods" in prompt
        assert "significance_journey" in prompt
        assert "oos_performance" in prompt

    def test_arbiter_instructions_mention_visual_evidence(self):
        from agents.academic_review import _ARBITER_INSTRUCTIONS
        assert "VISUAL EVIDENCE" in _ARBITER_INSTRUCTIONS
        assert "rolling_correlation" in _ARBITER_INSTRUCTIONS
        # The arbiter must address the fail-open path explicitly so a
        # cold-deploy verdict doesn't reference non-existent figures.
        assert "cold deploy" in _ARBITER_INSTRUCTIONS.lower() or \
               "no charts" in _ARBITER_INSTRUCTIONS.lower()


# ── Academic Writer ──────────────────────────────────────────────────────────


class TestAcademicWriterPrompt:
    def test_academic_writer_embeds_rules(self):
        from agents.academic_writer import _SYSTEM_PROMPT
        from agents.base import VISUAL_REASONING_RULES
        assert VISUAL_REASONING_RULES in _SYSTEM_PROMPT
        # DOCUMENT_GENERATION_CHARTS-specific keys named (writer reasons
        # about regime/factor/drawdown when drafting).
        assert "rolling_sharpe" in _SYSTEM_PROMPT
        assert "drawdown_periods" in _SYSTEM_PROMPT
        # The writer must connect visual features to academic prose
        # explicitly — that is the value-add of vision over text-only.
        assert "academic prose" in _SYSTEM_PROMPT.lower() or \
               "visible evidence" in _SYSTEM_PROMPT.lower()
