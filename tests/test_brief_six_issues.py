"""tests/test_brief_six_issues.py -- the seven-fix PR (June 21 2026).

Pins each of the seven fixes from the brief-regeneration audit:

  Issue 1 -- brief_key_findings task uses {{TOKEN}} placeholders
             instead of raw academic figures.
  Issue 2 -- brief_methodology spec has max_tokens=2500 override.
  Issue 3 -- per-section evaluator dispatch covers all five non-
             executive-summary brief sections.
  Issue 4 -- the six previously-missing academic citations
             (hamilton_1989, carhart_1997, ang_bekaert_2002,
             harvey_liu_zhu_2016, bailey_lopez_de_prado_2014) plus
             the existing ones are all in data/references.json, AND
             the writer's CITATIONS block enumerates EVERY registry
             key (no gaps, no extras).
  Issue 5 -- _apply_draft_caveats skips the [[VERIFY CITATION]]
             caveat when document_type == "executive_brief".
  Issue 6 -- update_value_manifest rolls the session back between
             its data_hash attempt and the legacy retry.
  Issue 7 -- _schedule_auto_academic_review is a no-op for every
             current document type; the function survives for
             future opt-in.
"""
from __future__ import annotations

import json
import os
import re
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


# ── Issue 1 -- key_findings task uses tokens ─────────────────────────────


class TestIssue1KeyFindingsTokensInTask:
    """The key_findings section task used to instruct the writer to
    cite RAW figures ('Drawdown -52.6% (benchmark) vs -25.3%
    (blend)'). The writer echoed them as raw numbers and the
    substitution table never matched -- production logs showed
    tokens_replaced=[] count=0 for brief_key_findings while every
    other section resolved tokens correctly. Fix: rewrite the
    'Key figures to cite' block to use {{TOKEN}} placeholders that
    the substitution table can match."""

    def _find_key_findings_task(self) -> str:
        # The brief specs are constructed inside _generate_brief_
        # document, which is hard to inspect without running. Read
        # main.py text directly and locate the key_findings spec
        # task block. Robust enough for a pinning test.
        path = (Path(__file__).resolve().parents[1]
                / "backend" / "main.py")
        src = path.read_text(encoding="utf-8")
        # Locate the spec block; the agent_id pins the surface.
        m = re.search(
            r'"agent_id":\s*"brief_key_findings".*?"context":',
            src, re.DOTALL)
        assert m, "brief_key_findings spec not found in main.py"
        return m.group(0)

    def test_task_carries_token_placeholders(self):
        task = self._find_key_findings_task()
        # The four substitution tokens the key_findings prose now
        # asks for. Each must appear LITERALLY in the task body.
        for token in (
            "{{BENCHMARK_MAX_DD}}",
            "{{REGIME_SWITCHING_MAX_DD}}",
            "{{OOS_SHARPE_BLEND}}",
            "{{OOS_SHARPE_BENCHMARK}}",
            "{{OOS_WINDOW_MONTHS}}",
        ):
            assert token in task, (
                f"key_findings task missing required token {token}")

    def test_task_no_longer_dictates_raw_values(self):
        # The old raw-value bullets that the substitution table
        # couldn't intercept.
        task = self._find_key_findings_task()
        # "-52.6% (benchmark) vs -25.3%" was the literal bullet --
        # the writer must NOT see it any more.
        assert "-52.6% (benchmark)" not in task
        assert "0.86 (blend) vs 0.43 (benchmark)" not in task
        assert "40-month post-2022" not in task


# ── Issue 2 -- methodology max_tokens=2500 ───────────────────────────────


class TestIssue2MethodologyMaxTokens:
    """The methodology spec used to default to max_tokens=1500;
    production runs were truncating at the references block
    because methodology cites four foundational papers. Bumped to
    2500 in line with the key_findings + visuals overrides from
    PR #361."""

    def test_methodology_spec_carries_max_tokens_override(self):
        path = (Path(__file__).resolve().parents[1]
                / "backend" / "main.py")
        src = path.read_text(encoding="utf-8")
        # Find the methodology spec block. Pin: the spec literal
        # carries '"max_tokens": 2500' before the task field.
        m = re.search(
            r'"agent_id":\s*"brief_methodology".*?"task":',
            src, re.DOTALL)
        assert m, "brief_methodology spec not found in main.py"
        spec_block = m.group(0)
        assert '"max_tokens": 2500' in spec_block, (
            "brief_methodology spec missing max_tokens=2500 override")


# ── Issue 3 -- per-section evaluators ────────────────────────────────────


class TestIssue3PerSectionEvaluators:
    """The five non-executive-summary brief sections each need
    a section-specific evaluator. Before this PR, methodology /
    key_findings / limitations / final_recommendations / visuals
    all scored against academic_review_peer_evaluator_prompt
    ('academic writer'), whose criteria penalise correct brief
    sections by design (the highest-weighted criterion is
    'actionable_next_steps' which Final Recommendations
    explicitly is NOT meant to be)."""

    def test_brief_section_evaluator_prompt_handles_all_five(self):
        from agents.evaluator_prompts import brief_section_evaluator_prompt
        for key in (
            "methodology", "key_findings", "limitations",
            "final_recommendations", "visuals",
        ):
            prompt = brief_section_evaluator_prompt(key)
            assert isinstance(prompt, str) and len(prompt) > 500
            # The closing JSON contract must be present so the
            # harness can parse the evaluator response.
            assert '"scores"' in prompt
            assert '"overall"' in prompt

    def test_methodology_evaluator_scores_citations(self):
        from agents.evaluator_prompts import brief_section_evaluator_prompt
        prompt = brief_section_evaluator_prompt("methodology")
        # The methodology rubric weights core_citations_present
        # highest (0.30) -- the four foundational citations
        # (Hamilton 1989, Carhart 1997, Ang and Bekaert 2002,
        # Markowitz 1952) are named in the rubric so the
        # evaluator scores presence directly.
        assert "core_citations_present" in prompt
        assert "Hamilton (1989)" in prompt
        assert "Carhart (1997)" in prompt
        assert "Ang and Bekaert (2002)" in prompt
        assert "Markowitz (1952)" in prompt

    def test_final_recommendations_evaluator_rewards_not_penalises_conclusions(self):
        from agents.evaluator_prompts import brief_section_evaluator_prompt
        prompt = brief_section_evaluator_prompt("final_recommendations")
        # The rubric's highest-weighted criterion is
        # "investment_conclusions_not_next_steps" -- the OPPOSITE
        # of the peer-review evaluator's "actionable_next_steps"
        # which was structurally penalising this section.
        assert "investment_conclusions_not_next_steps" in prompt
        assert "0.30" in prompt
        # And the rubric must explicitly warn against the failure
        # mode the previous evaluator created.
        assert (
            "INVESTMENT CONCLUSIONS, not next steps" in prompt
            or "CIO memo, not an academic open-questions" in prompt)

    def test_unknown_section_key_falls_back_with_warning(self):
        from agents.evaluator_prompts import (
            brief_executive_summary_evaluator_prompt,
            brief_section_evaluator_prompt,
        )
        # An unknown key falls back to the executive_summary
        # evaluator (the most generic of the section rubrics).
        out = brief_section_evaluator_prompt("not_a_section")
        # Fallback returns the same prompt -- pinning via equality
        # rather than substring so a future change to either side
        # is caught.
        assert out == brief_executive_summary_evaluator_prompt()

    def test_harness_dispatches_each_brief_agent_id_correctly(self):
        # The agent_id -> section_key mapping in harness_narrative
        # routes each brief_* agent_id to the matching section
        # evaluator. Inspect the source so we can verify the
        # dispatch table without running the harness.
        import inspect
        from tools import academic_export
        src = inspect.getsource(academic_export.harness_narrative)
        for agent_id in (
            "brief_executive_summary", "brief_methodology",
            "brief_key_findings", "brief_limitations",
            "brief_final_recommendations", "brief_visuals",
        ):
            assert f'"{agent_id}"' in src, (
                f"harness_narrative dispatch table missing {agent_id}")
        # The function imports brief_section_evaluator_prompt now.
        assert "brief_section_evaluator_prompt" in src


# ── Issue 4 -- registry completeness + CITATIONS block sync ──────────────


class TestIssue4RegistryAndCitationsBlock:
    """data/references.json was missing six citations the brief's
    section tasks reference (Hamilton 1989, Carhart 1997, Ang and
    Bekaert 2002, Harvey + Liu + Zhu 2016, Bailey + López de
    Prado 2014, plus López de Prado 2018 was already present).
    With PR #362's web search disabled + registry-only contract,
    every cited (Author, Year) MUST have a matching registry
    key. The CITATIONS block in the writer system prompt must
    enumerate EVERY registry key (no gaps, no extras) so the
    writer never invents a citation the registry can't render."""

    @staticmethod
    def _registry() -> dict:
        path = (Path(__file__).resolve().parents[1]
                / "backend" / "data" / "references.json")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _writer_citations_block() -> str:
        from agents import academic_writer as aw
        prompt = aw._SYSTEM_PROMPT
        # Pull the Available citation keys block.
        m = re.search(
            r"Available citation keys.*?The registry is the canonical",
            prompt, re.DOTALL)
        assert m, "CITATIONS block not found in writer system prompt"
        return m.group(0)

    def test_six_previously_missing_citations_now_in_registry(self):
        reg = self._registry()
        for key in (
            "hamilton_1989", "carhart_1997", "ang_bekaert_2002",
            "harvey_liu_zhu_2016", "bailey_lopez_de_prado_2014",
        ):
            assert key in reg, f"missing registry key: {key}"

    def test_every_added_entry_has_required_fields(self):
        reg = self._registry()
        for key in (
            "hamilton_1989", "carhart_1997", "ang_bekaert_2002",
            "harvey_liu_zhu_2016", "bailey_lopez_de_prado_2014",
        ):
            entry = reg[key]
            assert isinstance(entry.get("author"), str)
            assert isinstance(entry.get("year"), int)
            assert isinstance(entry.get("apa"), str)
            # The APA string is what the document assembler
            # renders -- must be non-empty.
            assert len(entry["apa"]) > 30

    def test_writer_citations_block_has_every_registry_key(self):
        """The contract the user named explicitly: EVERY registry
        key must appear in the writer's CITATIONS block, by its
        canonical (Author, Year) form. No gaps."""
        reg = self._registry()
        block = self._writer_citations_block()
        for key, entry in sorted(reg.items()):
            first_surname = entry["author"].split(",", 1)[0].strip()
            year_marker = f"({entry['year']})"
            # The bullet line for this key must contain both the
            # first surname AND its year marker.
            found = False
            for line in block.splitlines():
                if (first_surname in line and year_marker in line
                        and line.strip().startswith("-")):
                    found = True
                    break
            assert found, (
                f"writer CITATIONS block missing entry for {key} "
                f"(expected line containing '{first_surname}' + "
                f"'{year_marker}')")

    def test_writer_citations_block_has_no_extra_keys(self):
        """The reverse contract: no bullet in the CITATIONS block
        names a citation that isn't in the registry. Production
        runs were citing Hamilton (1989) etc. with no matching
        registry entry, then the audit flagged the citation as
        'missing from References'. With this test, a future
        regression that adds a bullet without a registry entry is
        caught at PR time."""
        reg = self._registry()
        block = self._writer_citations_block()
        # Pull every "(YYYY)" year-marker from the block.
        bullet_lines = [
            line.strip() for line in block.splitlines()
            if line.strip().startswith("-")
        ]
        for line in bullet_lines:
            year_m = re.search(r"\((\d{4})\)", line)
            if not year_m:
                continue
            year = int(year_m.group(1))
            # Find the (Surname ... (Year)) prefix to look up.
            prefix_m = re.match(
                r"-\s+([^\(]+)\s+\(", line)
            assert prefix_m, f"bullet shape unexpected: {line!r}"
            # Extract just the first author surname. Handles:
            #   "Hamilton (1989)"                  -> "Hamilton"
            #   "Ang and Bekaert (2002)"           -> "Ang"
            #   "Harvey, Liu, and Zhu (2016)"      -> "Harvey"
            #   "Benjamin et al. (2018)"           -> "Benjamin"
            #   "Brinson, Hood, and Beebower ..."  -> "Brinson"
            raw = prefix_m.group(1).strip()
            # Strip trailing "et al." first.
            raw = re.sub(
                r"\s+et\s+al\.?$", "", raw, flags=re.IGNORECASE)
            # Split on the FIRST separator (comma, ampersand, " and ").
            parts = re.split(r"\s*[,&]\s*|\s+and\s+", raw, maxsplit=1)
            first_surname = parts[0].strip()
            # The registry must contain a key whose author starts
            # with this surname AND year matches.
            ok = False
            for k, entry in reg.items():
                reg_surname = entry["author"].split(",", 1)[0].strip()
                if reg_surname == first_surname and entry["year"] == year:
                    ok = True
                    break
            assert ok, (
                f"bullet {line!r} has no matching registry entry "
                f"(surname={first_surname!r}, year={year})")


# ── Issue 5 -- _apply_draft_caveats gates citation caveat by document_type


class TestIssue5CaveatsGatedByDocumentType:
    """The [[VERIFY CITATION]] caveat was being appended to every
    section task unconditionally. With web search disabled in
    PR #362 and citations locked to the pre-verified registry,
    every citation IS verified by construction -- the caveat
    still drove the writer to mark every citation with
    [[VERIFY CITATION:]] blocks that surfaced as submission
    blockers in the audit. Gate the citation caveat by
    document_type=='executive_brief'."""

    def test_brief_specs_do_not_get_citation_caveat(self):
        from main import _apply_draft_caveats
        spec = {
            "key": "methodology", "task": "Write Section 2."}
        out = _apply_draft_caveats(
            [spec], document_type="executive_brief")
        task = out[0]["task"]
        # The citation caveat is gone.
        assert "[[VERIFY CITATION" not in task
        # The statistics caveat is still appended (numerics can
        # still drift; the marker flow catches those).
        assert "[[VERIFY:" in task

    def test_non_brief_specs_still_get_citation_caveat(self):
        # midpoint_paper / deck / appendix all still get the
        # citation caveat -- the brief is the only surface
        # that's been locked to the registry.
        from main import _apply_draft_caveats
        for doc_type in (None, "midpoint_paper",
                         "presentation_deck", "analytical_appendix"):
            spec = {
                "key": "section_x", "task": "Write Section X."}
            out = _apply_draft_caveats(
                [spec], document_type=doc_type)
            assert "[[VERIFY CITATION" in out[0]["task"], (
                f"citation caveat must still appear for "
                f"document_type={doc_type!r}")

    def test_idempotent_when_task_already_has_caveat(self):
        # Same as the legacy behaviour -- a task that ALREADY
        # carries the marker is not given a second copy.
        from main import _apply_draft_caveats
        spec = {
            "key": "x",
            "task": "Write [[VERIFY CITATION: existing]] section."}
        out = _apply_draft_caveats(
            [spec], document_type="midpoint_paper")
        # Exactly one [[VERIFY CITATION marker in the result.
        assert out[0]["task"].count("[[VERIFY CITATION") == 1


# ── Issue 6 -- update_value_manifest rollback ────────────────────────────


class TestIssue6ManifestTxRollback:
    """The update_value_manifest helper had a try/except retry for
    pre-migration-057 schemas (no data_hash column), but the
    retry used the SAME session that the failed UPDATE left in
    an aborted-transaction state. Production log:
    InFailedSQLTransactionError. Fix: rollback the session
    between attempts. Same fix shape as PR #360."""

    def test_function_source_contains_rollback_between_attempts(self):
        # Source-level check: we can't easily mock asyncpg's
        # aborted-transaction state in a unit test, but we can
        # pin the function source to ensure the rollback is
        # present between the two SELECT attempts. The same
        # check pattern was used in PR #360's test sweep.
        import inspect
        from tools import editor_drafts
        src = inspect.getsource(editor_drafts.update_value_manifest)
        # The legacy retry must call await s.rollback() before
        # re-executing.
        rollback_idx = src.find("await s.rollback()")
        first_update = src.find(
            "UPDATE editor_drafts ")
        second_update = src.find(
            "UPDATE editor_drafts ", first_update + 1)
        assert rollback_idx > -1, "update_value_manifest missing rollback"
        assert first_update > -1 and second_update > -1
        assert first_update < rollback_idx < second_update, (
            "rollback must sit between the two UPDATE attempts; "
            f"first_update={first_update}, "
            f"rollback={rollback_idx}, "
            f"second_update={second_update}")


# ── Issue 7 -- auto-fire academic review disabled ────────────────────────


class TestIssue7AutoFireDisabled:
    """The auto-fire Academic Review on every brief generation
    was firing ~8-10 LLM calls per generation purely to populate
    the editor pill + the IN02 attestation row. Both surfaces
    are still reachable via the manual SSE endpoint at
    POST /api/council/academic-review. Disabling the auto-fire
    saves the LLM cost; the manual click satisfies IN02 for the
    14-day lookback window."""

    def test_schedule_returns_no_op_for_executive_brief(self, monkeypatch):
        # The function used to schedule a coroutine task for
        # executive_brief; now it returns without scheduling and
        # logs auto_academic_review_skipped_by_design.
        import main as main_module
        scheduled: list = []

        def _fake_create_task(coro):
            scheduled.append(coro)
            # Close the coroutine so we don't leak a warning.
            try:
                coro.close()
            except Exception:  # noqa: BLE001
                pass
            return None

        monkeypatch.setattr(os, "getenv", lambda k, *a: (
            "production" if k == "ENVIRONMENT" else os.environ.get(k, *a)))
        import asyncio
        monkeypatch.setattr(
            asyncio, "create_task", _fake_create_task)
        main_module._schedule_auto_academic_review(
            draft_id=1, document_type="executive_brief",
            owner_email="ruurdsm@queens.edu")
        assert scheduled == [], (
            "_schedule_auto_academic_review must NOT schedule for "
            "executive_brief; auto-fire is disabled by design")

    def test_schedule_returns_no_op_for_deck_and_appendix(self, monkeypatch):
        import main as main_module
        scheduled: list = []

        def _fake_create_task(coro):
            scheduled.append(coro)
            try:
                coro.close()
            except Exception:  # noqa: BLE001
                pass
            return None

        monkeypatch.setattr(os, "getenv", lambda k, *a: (
            "production" if k == "ENVIRONMENT" else os.environ.get(k, *a)))
        import asyncio
        monkeypatch.setattr(
            asyncio, "create_task", _fake_create_task)
        for doc_type in (
            "presentation_deck", "analytical_appendix",
            "midpoint_paper",
        ):
            main_module._schedule_auto_academic_review(
                draft_id=1, document_type=doc_type,
                owner_email="ruurdsm@queens.edu")
        assert scheduled == [], (
            "_schedule_auto_academic_review must NOT schedule for "
            "any document type; auto-fire is disabled across the "
            "board")

    def test_function_survives_for_future_opt_in(self):
        # The function should not have been deleted -- it stays
        # in place so a future opt-in (single-line edit to the
        # allow-list) restores the auto-fire without re-plumbing.
        import main as main_module
        assert callable(main_module._schedule_auto_academic_review)
        assert callable(main_module._run_auto_academic_review)
