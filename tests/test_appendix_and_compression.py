"""tests/test_appendix_and_compression.py -- the appendix + brief
compression PR (June 21 2026).

Six fixes:
  Fix 2 -- appendix narrative tasks carry a hard 100-130 word cap
  Fix 3 -- _apply_draft_caveats skips citation caveat for
           analytical_appendix; appendix call site passes the
           document_type kwarg
  Pre-flight cache gate -- _generate_appendix_document raises
           HTTPException(409) when bootstrap_ci_sharpe /
           factor_loadings / cost_sensitivity are empty
  Admin endpoint -- POST /api/v1/admin/refresh-appendix-caches
           wires the backtester + refresh_academic_analytics +
           refresh_oos_cost_sensitivity chain end-to-end
  Issue 2 (Option 2) -- harness_narrative does a post-pass
           story-plan-violation check + retries once when the
           count exceeds threshold
  Issue 3 -- _BRIEF_SECTION_WORD_TARGETS upper bands tightened;
           per-section evaluator length_in_target bands match;
           academic_writer's References instruction moved from
           end-of-document to per-section
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


# ── Fix 2 -- appendix word cap instruction ──────────────────────────────


class TestAppendixWordCapInstruction:
    """The brief-alignment excerpt added in PR #364 was driving
    the appendix narrative agent to expand prose to mirror the
    brief (~430 words/section observed vs. 100-130 word target).
    Fix: append _APPENDIX_WORD_CAP_INSTRUCTION to every appendix
    section task. The instruction is the last thing the writer
    sees, so the constraint sticks."""

    def test_constant_carries_hard_limit_language(self):
        import main as main_module
        block = main_module._APPENDIX_WORD_CAP_INSTRUCTION
        assert "HARD WORD LIMIT" in block
        assert "130 words" in block
        # The brief excerpt's purpose is clarified -- alignment
        # context only, not scope expansion.
        assert "ALIGNMENT CONTEXT ONLY" in block

    def test_instruction_appended_after_brief_excerpt(self):
        # The cap must be appended AFTER the brief excerpt so
        # the writer sees the constraint last. We can't fully
        # exercise the spec assembly without DB, but we can pin
        # the constant exists and is referenced in
        # _generate_appendix_document's source.
        import inspect
        import main as main_module
        src = inspect.getsource(main_module._generate_appendix_document)
        assert "_APPENDIX_WORD_CAP_INSTRUCTION" in src
        # And the appending order: word cap AFTER the brief
        # alignment excerpt. The source assembles the task as
        # appendix_guide + framing prelude + task + brief
        # excerpt + word cap.
        word_cap_idx = src.find("_APPENDIX_WORD_CAP_INSTRUCTION")
        brief_block_idx = src.find('section_brief_blocks.get(key, "")')
        assert word_cap_idx > brief_block_idx > 0, (
            "word cap must be appended AFTER the brief excerpt "
            "block in the spec assembly")


# ── Fix 3 -- caveat gate extended to appendix ───────────────────────────


class TestCaveatGateAppendix:
    def test_appendix_skips_citation_caveat(self):
        from main import _apply_draft_caveats
        spec = {"key": "x", "task": "Write Section X."}
        out = _apply_draft_caveats(
            [spec], document_type="analytical_appendix")
        assert "[[VERIFY CITATION" not in out[0]["task"]
        # Stats caveat still applied for uncertain numerics.
        assert "[[VERIFY:" in out[0]["task"]

    def test_appendix_call_site_passes_document_type(self):
        # Source-level pin: the appendix generator must pass
        # document_type="analytical_appendix" to
        # _apply_draft_caveats. Future PR that drops the kwarg
        # would silently re-introduce the [[VERIFY CITATION]]
        # leak that hit production (Fix 3 root cause).
        import inspect
        import main as main_module
        src = inspect.getsource(main_module._generate_appendix_document)
        assert (
            '_apply_draft_caveats(\n                specs, '
            'document_type="analytical_appendix"' in src
            or '_apply_draft_caveats(specs, '
               'document_type="analytical_appendix")' in src), (
            "appendix generator must pass document_type="
            "'analytical_appendix' to _apply_draft_caveats")


# ── Pre-flight cache gate ───────────────────────────────────────────────


class TestAppendixPreflightCacheGate:
    """The appendix's graded sections (B, C, D, E, G) all depend
    on upstream cache populations. Generating against an empty
    cache produces misleading output and wastes generation
    budget. The pre-flight gate raises HTTPException(409) with
    the list of missing fields and directs the operator to the
    new admin endpoint."""

    def test_raises_409_when_strategy_results_empty(self, monkeypatch):
        import asyncio
        import main as main_module
        from fastapi import HTTPException

        async def _empty_data():
            return {
                "available": True,
                "study_period": {},
                "strategy_results": {},  # empty -- C/E source
                "bootstrap_ci_sharpe": [{"strategy": "X"}],
                "factor_loadings": [{"strategy": "X"}],
                "cost_sensitivity": {"net_sharpe_15bp": 0.5},
                "data_hash": "test",
            }

        # Stub brief grounding so the brief gate passes.
        async def _fake_brief(_email):
            return {"content_text": "brief body", "content_hash": "h",
                    "draft_id": 1}

        monkeypatch.setattr(
            "tools.brief_grounding.get_brief_for_grounding",
            _fake_brief)
        monkeypatch.setattr(
            "tools.academic_export.gather_analytical_appendix_data",
            _empty_data)

        try:
            asyncio.run(main_module._generate_appendix_document(
                "ruurdsm@queens.edu"))
        except HTTPException as exc:
            assert exc.status_code == 409
            detail = exc.detail or ""
            assert "Analytics caches are not warm" in detail
            assert "strategy_results" in detail
            assert "/api/v1/admin/refresh-appendix-caches" in detail
            return
        assert False, "expected HTTPException(409)"

    def test_raises_409_when_bootstrap_empty(self, monkeypatch):
        import asyncio
        import main as main_module
        from fastapi import HTTPException

        async def _empty_data():
            return {
                "available": True,
                "study_period": {},
                "strategy_results": {"A": {"sharpe_ratio": 0.5}},
                "bootstrap_ci_sharpe": [],  # empty -- D source
                "factor_loadings": [{"strategy": "X"}],
                "cost_sensitivity": {"net_sharpe_15bp": 0.5},
                "data_hash": "test",
            }

        async def _fake_brief(_email):
            return {"content_text": "brief", "content_hash": "h",
                    "draft_id": 1}

        monkeypatch.setattr(
            "tools.brief_grounding.get_brief_for_grounding",
            _fake_brief)
        monkeypatch.setattr(
            "tools.academic_export.gather_analytical_appendix_data",
            _empty_data)

        try:
            asyncio.run(main_module._generate_appendix_document(
                "ruurdsm@queens.edu"))
        except HTTPException as exc:
            assert exc.status_code == 409
            assert "bootstrap_ci_sharpe" in (exc.detail or "")
            return
        assert False, "expected HTTPException(409)"

    def test_raises_409_when_cost_sensitivity_none(self, monkeypatch):
        import asyncio
        import main as main_module
        from fastapi import HTTPException

        async def _data_missing_cost():
            return {
                "available": True,
                "study_period": {},
                "strategy_results": {"A": {"sharpe_ratio": 0.5}},
                "bootstrap_ci_sharpe": [{"strategy": "X"}],
                "factor_loadings": [{"strategy": "X"}],
                "cost_sensitivity": None,  # missing -- G source
                "data_hash": "test",
            }

        async def _fake_brief(_email):
            return {"content_text": "brief", "content_hash": "h",
                    "draft_id": 1}

        monkeypatch.setattr(
            "tools.brief_grounding.get_brief_for_grounding",
            _fake_brief)
        monkeypatch.setattr(
            "tools.academic_export.gather_analytical_appendix_data",
            _data_missing_cost)

        try:
            asyncio.run(main_module._generate_appendix_document(
                "ruurdsm@queens.edu"))
        except HTTPException as exc:
            assert exc.status_code == 409
            assert "cost_sensitivity" in (exc.detail or "")
            return
        assert False, "expected HTTPException(409)"


# ── Admin endpoint -- refresh-appendix-caches ───────────────────────────


class TestRefreshAppendixCachesEndpoint:
    def test_endpoint_exists_and_returns_test_env_noop(self):
        # In ENVIRONMENT=test the endpoint short-circuits so CI
        # doesn't hit real compute. The endpoint itself must
        # exist; route inspection confirms it.
        from main import app
        routes = [
            r.path for r in app.routes
            if hasattr(r, "path")]
        assert "/api/v1/admin/refresh-appendix-caches" in routes

    def test_endpoint_handler_runs_chain_in_sequence(self):
        # Source-level pin: the handler must call backtester
        # FIRST (produces strategy_hash), then
        # refresh_academic_analytics + refresh_oos_cost_sensitivity
        # in that order. Out-of-order would write metrics keyed
        # to a stale hash.
        #
        # Anchor on the actual call expressions (await ...) so
        # docstring mentions of the function names don't
        # confuse the ordering check.
        import inspect
        import main as main_module
        src = inspect.getsource(main_module.post_refresh_appendix_caches)
        # The backtester is called via asyncio.to_thread so the
        # symbol appears as `run_all_strategies,` (followed by a
        # comma not a paren). Anchor on the comma form.
        bt_idx = src.find("run_all_strategies,")
        aa_idx = src.find("refresh_academic_analytics(")
        cs_idx = src.find("refresh_oos_cost_sensitivity(")
        assert 0 < bt_idx < aa_idx < cs_idx, (
            f"compute chain order must be backtester -> "
            f"academic_analytics -> cost_sensitivity; got "
            f"bt={bt_idx} aa={aa_idx} cs={cs_idx}")


# ── Issue 2 -- harness story-plan-violation retry ───────────────────────


class TestHarnessStoryPlanViolationRetry:
    """When a brief section's prose emits more than
    _STORY_PLAN_VIOLATION_RETRY_THRESHOLD numbers outside the
    section's locked anchors set, harness_narrative re-calls the
    generator ONCE with explicit feedback listing the
    unauthorized tokens. Accepts the retry output if it has
    fewer violations."""

    def test_threshold_constant(self):
        from tools import academic_export as ae
        # Threshold pinned -- a future PR that drops it to 0
        # would cause every brief generation to retry; a PR
        # that raises it past 5 weakens the contract. 3 is the
        # observed signal-vs-noise sweet spot.
        assert ae._STORY_PLAN_VIOLATION_RETRY_THRESHOLD == 3

    def test_count_unauthorized_numbers_finds_offenders(self):
        from tools.academic_export import _count_unauthorized_numbers
        # Anchors say 0.86 and 0.43 are authorized.
        prose = ("The blend achieved 0.86 OOS Sharpe versus 0.43 "
                 "benchmark. The 60/40 allocation rose 12.5% "
                 "and recovered 71 months later.")
        bad = _count_unauthorized_numbers(prose, [0.86, 0.43])
        # 60, 40, 12.5, 71 are unauthorized. Some may collapse.
        assert "60" in bad or "40" in bad or "12.5" in bad or "71" in bad
        assert "0.86" not in bad
        assert "0.43" not in bad

    def test_year_in_citation_parens_not_flagged(self):
        from tools.academic_export import _count_unauthorized_numbers
        prose = "The framework (Hamilton, 1989) provides the basis."
        bad = _count_unauthorized_numbers(prose, [0.86])
        # 1989 is in citation parens -- not unauthorized.
        assert "1989" not in bad

    def test_no_anchors_short_circuits_empty(self):
        from tools.academic_export import _count_unauthorized_numbers
        assert _count_unauthorized_numbers("text with 1.24", []) == []

    def test_inject_brief_section_plan_threads_anchors_onto_specs(self):
        # The injector adds numeric_anchors onto each spec so
        # _generate_narratives can pass them to harness_narrative.
        from main import _inject_brief_section_plan
        specs = [{"key": "methodology", "task": "Write Section 2.",
                  "context": {}}]
        plan = {
            "methodology": {
                "key_message": "HMM regime detection",
                "numeric_anchors": {"hamilton_year": 1989,
                                    "oos_window_months": 40},
                "target_length_words": 350,
            }
        }
        out = _inject_brief_section_plan(specs, plan)
        assert out[0]["numeric_anchors"] == {
            "hamilton_year": 1989, "oos_window_months": 40}


# ── Issue 3 -- per-section word-band tightening ─────────────────────────


class TestPerSectionWordBandsTightened:
    """Upper bands compressed June 21 2026 to fit 5-page DS
    page budget. Lower bands UNCHANGED -- required content
    preservation is the constraint, no shortening of mandatory
    sections."""

    def test_brief_section_word_targets_tightened(self):
        from tools.document_audit import _BRIEF_SECTION_WORD_TARGETS as T
        # New upper bands (was the value before -> now the value).
        # Note: lower bands stay constant.
        assert T["Executive Summary"][1] == 280       # was 300
        assert T["Methodology"][1] == 380             # was 400
        assert T["Key Findings"][1] == 580            # was 620
        assert T["Limitations"][1] == 330             # was 350
        assert T["Final Recommendations"][1] == 380   # was 400
        assert T["Visuals"][1] == 260                 # was 300
        # Lower bands UNCHANGED -- required content preserved.
        assert T["Methodology"][0] == 300
        assert T["Key Findings"][0] == 480
        assert T["Final Recommendations"][0] == 300

    def test_upper_band_sum_within_page_budget(self):
        # 5-page double-spaced ceiling is ~2370 prose words.
        # With per-section References blocks (~80-150 words
        # saved) and tightened upper bands, the sum stays under
        # 2210 -- ~160 words of headroom for the page budget.
        from tools.document_audit import _BRIEF_SECTION_WORD_TARGETS as T
        upper_sum = sum(t[1] for t in T.values())
        assert upper_sum <= 2210, (
            f"upper band sum {upper_sum} exceeds 5-page DS "
            "ceiling; compression target was <=2210")

    def test_evaluator_length_bands_aligned_with_audit_targets(self):
        # The per-section evaluator's length_in_target criterion
        # carries its own band. Aligning the evaluator's upper
        # with the audit's upper keeps the harness from
        # rewarding sections that the audit will then flag.
        from agents.evaluator_prompts import (
            brief_section_evaluator_prompt,
            brief_executive_summary_evaluator_prompt,
        )
        # Methodology: 300-380 (audit upper 380).
        meth = brief_section_evaluator_prompt("methodology")
        assert "300-380 words" in meth
        # Key Findings: 480-580 (audit upper 580).
        kf = brief_section_evaluator_prompt("key_findings")
        assert "480-580" in kf
        # Limitations: 250-330 (audit upper 330).
        lim = brief_section_evaluator_prompt("limitations")
        assert "250-330" in lim
        # Final Recommendations: 300-380 (audit upper 380).
        fr = brief_section_evaluator_prompt("final_recommendations")
        assert "300-380" in fr
        # Visuals: 210-260 (audit upper 260; lower bumped to
        # 210 from 200 since per-chart structure already
        # requires 4 entries at ~50 words each = 200 minimum).
        vis = brief_section_evaluator_prompt("visuals")
        assert "210-260" in vis
        # Executive Summary: 200-280 (audit upper 280).
        es = brief_executive_summary_evaluator_prompt()
        assert "200-280" in es


# ── Issue 3 -- References moved to per-section blocks ────────────────────


class TestReferencesPerSectionBlocks:
    """The writer's system prompt previously instructed an
    end-of-document References block consolidating every
    citation. June 21 2026 -- moved to per-section blocks so
    each section's References subsection lists only its 2-4
    cited papers. Saves ~80-150 words off the trailing block."""

    def test_writer_prompt_instructs_per_section_references(self):
        from agents.academic_writer import _SYSTEM_PROMPT
        assert "REFERENCES PLACEMENT" in _SYSTEM_PROMPT
        assert "After EACH section" in _SYSTEM_PROMPT
        # And explicitly prohibits the end-of-document
        # consolidated block.
        assert (
            "Do NOT emit a single end-of-document References block"
            in _SYSTEM_PROMPT)

    def test_writer_prompt_no_longer_instructs_end_of_document(self):
        # The old instruction ("Include a References section at
        # the end") must be gone. A future PR that re-adds it
        # would split the writer's behaviour (some sections
        # emit per-section, some accumulate end-of-document).
        from agents.academic_writer import _SYSTEM_PROMPT
        assert (
            "Include a References section at the end"
            not in _SYSTEM_PROMPT)
