"""tests/test_rationalization.py — word-count rationalization pass
(follow-up to commit 70a9290, May 26 2026).

The writer prompt now places interpretation inline (no [BOB] block
merge step), but Sonnet does not perfectly hit per-section word
budgets. A single rationalization pass between the writer call and
the final post-check compresses over-budget sections in place.

Covers:
  - _split_heading_and_body: heading extraction
  - _build_rationalizer_system_prompt: contract pins (preserve numbers
    + citations, never cut thesis, output is prose not bullets)
  - _SECTION_PURPOSE: every budgeted section has a purpose mapping
  - _rationalize_over_budget_sections: no-op when sections fit,
    rebuild semantics, fail-open on rationalizer error, no_heading
    safety, status accounting (rationalized / still_over / failed /
    skipped / no_heading), word-count check runs AFTER the pass
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault(
    "SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,"
    "murdockm@queens.edu,panttserk@queens.edu",
)


from tools import report_generator as rg  # noqa: E402


# ── _split_heading_and_body ─────────────────────────────────────────────────


class TestSplitHeadingAndBody:
    """Splitting a section produced by template_pipeline.split_by_section
    into (heading_line, body) is the precondition for replacing the
    body without touching the heading. The split must work for the
    writer's actual layout AND degrade gracefully on edge inputs."""

    def test_standard_markdown_heading(self):
        # The writer emits a markdown H2 followed by a blank line and
        # the body — that's the canonical shape produced by the
        # template's section format.
        section = (
            "## 2. Preliminary Results and Diagnostics\n"
            "\n"
            "The Sharpe ratio of the regime-switching strategy was "
            "0.83 versus 0.61 for the equity benchmark.\n"
            "Bonds rallied during the 2022 regime shift.\n"
        )
        heading, body = rg._split_heading_and_body(section)
        assert heading == "## 2. Preliminary Results and Diagnostics"
        assert "Sharpe ratio" in body
        assert "0.83" in body
        # Heading line not duplicated in the body.
        assert "## 2." not in body

    def test_section_with_leading_blank_lines(self):
        section = "\n\n## 3. Roles\n\nMichael led the data pipeline."
        heading, body = rg._split_heading_and_body(section)
        assert heading == "## 3. Roles"
        assert body == "Michael led the data pipeline."

    def test_empty_section_returns_empty_pair(self):
        heading, body = rg._split_heading_and_body("")
        assert heading == ""
        assert body == ""

    def test_whitespace_only_section_returns_empty_heading(self):
        heading, body = rg._split_heading_and_body("\n\n   \n\n")
        assert heading == ""
        # The body fallback returns the original whitespace; the
        # caller treats empty-heading as "leave untouched".
        assert body.strip() == ""

    def test_single_line_section_uses_it_as_heading(self):
        # A section with only a heading line and no body — the
        # caller should treat this as no_heading because the body
        # is empty.
        heading, body = rg._split_heading_and_body("## 4. Next Steps")
        assert heading == "## 4. Next Steps"
        assert body == ""


# ── _SECTION_PURPOSE coverage ───────────────────────────────────────────────


class TestSectionPurposeCoverage:
    """Every budgeted section in _SECTION_BUDGETS must have a matching
    purpose entry — without one the rationalizer would skip the
    section and the over-budget flag would survive into the warning
    block. Pinning this prevents a future budget addition from
    silently degrading the rationalization pass."""

    def test_every_budget_has_a_purpose(self):
        from tools.template_pipeline import _SECTION_BUDGETS
        missing = [
            n for n in _SECTION_BUDGETS
            if n not in rg._SECTION_PURPOSE
        ]
        assert not missing, (
            f"Sections without purpose mapping: {missing}")

    def test_purposes_are_non_empty_strings(self):
        for sec_num, purpose in rg._SECTION_PURPOSE.items():
            assert isinstance(purpose, str), sec_num
            assert purpose.strip(), sec_num


# ── _build_rationalizer_system_prompt ───────────────────────────────────────


class TestRationalizerSystemPrompt:
    """The compression prompt carries the contract: preserve numbers
    and citations, never cut the thesis or the open question,
    output coherent prose. A regression that loosened any of these
    rules would let the rationalizer strip data from the paper."""

    def test_prompt_names_section_and_budget(self):
        out = rg._build_rationalizer_system_prompt(
            section_num=2, budget=300, purpose="Preliminary Results")
        assert "Section 2" in out
        assert "300" in out

    def test_prompt_pins_preserve_numbers_rule(self):
        out = rg._build_rationalizer_system_prompt(
            section_num=1, budget=250, purpose="Data")
        assert "NEVER remove a number" in out
        # Citation reference shape is named verbatim so the model
        # cannot mistake it for incidental prose.
        assert "(Author, Year)" in out

    def test_prompt_protects_thesis_and_open_question(self):
        out = rg._build_rationalizer_system_prompt(
            section_num=2, budget=300, purpose="Preliminary Results")
        assert "NEVER cut the central thesis" in out
        assert "NEVER cut the open question" in out

    def test_prompt_requires_prose_not_bullets(self):
        out = rg._build_rationalizer_system_prompt(
            section_num=2, budget=300, purpose="Preliminary Results")
        assert "Coherent academic prose" in out
        assert "NOT a bulleted list" in out

    def test_prompt_requires_body_only_no_heading(self):
        # The rationalizer must NOT emit a heading because the
        # caller already owns the heading line and re-inserts it.
        # A duplicate heading would survive the merge.
        out = rg._build_rationalizer_system_prompt(
            section_num=3, budget=150, purpose="Roles")
        assert "Return ONLY the rewritten section body" in out
        assert "No heading" in out


# ── _rationalize_over_budget_sections ───────────────────────────────────────


def _patch_rationalizer(monkeypatch, replacement_factory):
    """Helper — stubs _call_rationalizer_sync to return a deterministic
    rewrite per section. replacement_factory(system, user) -> str."""
    def _fake(system, user, max_tokens=1200):
        return replacement_factory(system, user)
    monkeypatch.setattr(rg, "_call_rationalizer_sync", _fake)


_FAKE_PAPER_WITHIN_BUDGET = (
    "## 1. Data and Methodology\n\n"
    "Three monthly return series spanning 2002 to 2024.\n\n"
    "## 2. Preliminary Results and Diagnostics\n\n"
    "Sharpe 0.83 versus benchmark 0.61.\n\n"
    "## 3. Roles and Division of Labor\n\n"
    "Michael led the data pipeline. Bob led the methodology.\n\n"
    "## 4. Next Steps and Open Questions\n\n"
    "Open question: does the regime hold post-2024?\n"
)


def _make_over_budget_paper() -> str:
    """A paper where Section 2 is clearly over its 300-word budget.
    Padded with 'lorem' tokens so the deterministic word counter
    flags it red without depending on any LLM call."""
    s2_body = " ".join(["lorem"] * 400)
    return (
        "## 1. Data and Methodology\n\n"
        "Three monthly return series.\n\n"
        f"## 2. Preliminary Results and Diagnostics\n\n{s2_body}\n\n"
        "## 3. Roles\n\nMichael led the pipeline.\n\n"
        "## 4. Next Steps\n\nOpen question.\n"
    )


class TestRationalizeOverBudgetSections:

    def test_no_op_when_every_section_fits(self, monkeypatch):
        # No section is over budget — the rationalizer must not run.
        called: list[bool] = []
        def _fake(system, user, max_tokens=1200):
            called.append(True)
            return "REWRITE"
        monkeypatch.setattr(
            rg, "_call_rationalizer_sync", _fake)

        rewritten, details = asyncio.run(
            rg._rationalize_over_budget_sections(
                _FAKE_PAPER_WITHIN_BUDGET))
        assert rewritten == _FAKE_PAPER_WITHIN_BUDGET
        assert details == []
        assert called == []

    def test_empty_input_returns_empty_pair(self, monkeypatch):
        rewritten, details = asyncio.run(
            rg._rationalize_over_budget_sections(""))
        assert rewritten == ""
        assert details == []

    def test_over_budget_section_is_compressed(self, monkeypatch):
        # The rationalizer returns a short body; the function must
        # rebuild the section as heading + new body.
        def _fake(system, user, max_tokens=1200):
            return "Compressed body for section 2."
        monkeypatch.setattr(
            rg, "_call_rationalizer_sync", _fake)

        paper = _make_over_budget_paper()
        rewritten, details = asyncio.run(
            rg._rationalize_over_budget_sections(paper))

        # The section heading survives.
        assert "## 2. Preliminary Results and Diagnostics" in rewritten
        # The new body landed in place of the lorem padding.
        assert "Compressed body for section 2." in rewritten
        assert "lorem lorem lorem lorem" not in rewritten
        # Other sections were not touched.
        assert "Michael led the pipeline." in rewritten
        assert "Open question." in rewritten

        # Details record the section that was rationalized.
        assert len(details) == 1
        d = details[0]
        assert d["section"] == 2
        assert d["target"] == 300
        assert d["status"] == "rationalized"
        assert d["before"] > 300
        assert d["after"] <= 300 * 1.10

    def test_failed_call_keeps_original_section(self, monkeypatch):
        # Rationalizer returns '' — caller treats as "keep original".
        def _fake(system, user, max_tokens=1200):
            return ""
        monkeypatch.setattr(
            rg, "_call_rationalizer_sync", _fake)

        paper = _make_over_budget_paper()
        rewritten, details = asyncio.run(
            rg._rationalize_over_budget_sections(paper))

        # Original lorem padding is intact — content not wiped.
        assert "lorem lorem" in rewritten
        # Detail records the failure.
        assert len(details) == 1
        assert details[0]["section"] == 2
        assert details[0]["status"] == "failed"

    def test_still_over_status_when_rewrite_exceeds_budget(
            self, monkeypatch):
        # The rationalizer attempted but did not compress enough.
        # The function records the attempt with status='still_over'
        # rather than silently passing.
        def _fake(system, user, max_tokens=1200):
            return " ".join(["lorem"] * 400)  # Still 400 words
        monkeypatch.setattr(
            rg, "_call_rationalizer_sync", _fake)

        paper = _make_over_budget_paper()
        _, details = asyncio.run(
            rg._rationalize_over_budget_sections(paper))

        assert len(details) == 1
        assert details[0]["status"] == "still_over"
        assert details[0]["after"] > 300 * 1.10

    def test_malformed_paper_does_not_raise(self, monkeypatch):
        # A paper missing section headers entirely — split_by_section
        # returns the whole text under key 0, no numbered sections
        # are identified, no rationalization runs. The contract is
        # "fail-open, never raise".
        def _fake(system, user, max_tokens=1200):
            return "should not be called"
        monkeypatch.setattr(
            rg, "_call_rationalizer_sync", _fake)

        garbage = " ".join(["lorem"] * 500)
        rewritten, details = asyncio.run(
            rg._rationalize_over_budget_sections(garbage))
        # No numbered section was found so no rationalization fired —
        # the garbage input passes through unchanged.
        assert rewritten == garbage
        # Either no details (no over-budget numbered section detected)
        # or every detail is a non-failure status.
        for d in details:
            assert d["status"] in (
                "skipped", "no_heading", "rationalized")

    def test_word_count_check_runs_after_pass(self, monkeypatch):
        # The contract: word_count_report is run on the FINAL
        # rewritten paper, not the pre-pass writer output. Verify
        # by running word_count_report on the returned paper and
        # confirming section 2 is no longer 'red'.
        def _fake(system, user, max_tokens=1200):
            return "Compressed body for section 2."
        monkeypatch.setattr(
            rg, "_call_rationalizer_sync", _fake)

        paper = _make_over_budget_paper()
        rewritten, _ = asyncio.run(
            rg._rationalize_over_budget_sections(paper))

        from tools.template_pipeline import word_count_report
        counts = word_count_report(rewritten)
        s2 = counts["per_section"].get(2) or {}
        assert s2.get("status") == "green"


# ── Multi-pass rationalization (May 26 2026) ────────────────────────────────


class TestMultiPassRationalization:
    """Each over-budget section gets up to _MAX_RATIONALIZATION_PASSES
    rationalizer calls. The loop bails as soon as the section lands
    within tolerance OR the cap is reached — never silently fails."""

    def test_max_passes_constant_is_three(self):
        # Pinned by name so a change to the constant requires updating
        # this test in lockstep.
        assert rg._MAX_RATIONALIZATION_PASSES == 3

    def test_bails_early_when_first_pass_lands_in_tolerance(
            self, monkeypatch):
        # The first pass already lands the section inside ±10% — no
        # second call should fire.
        call_count: list[int] = [0]

        def _fake(system, user, max_tokens=1200):
            call_count[0] += 1
            # Return a 50-word body well under the 300-word budget.
            return " ".join(["short"] * 50)

        monkeypatch.setattr(rg, "_call_rationalizer_sync", _fake)
        paper = _make_over_budget_paper()
        _, details = asyncio.run(
            rg._rationalize_over_budget_sections(paper))

        assert call_count[0] == 1, (
            "expected a single call when first pass lands in tolerance")
        assert len(details) == 1
        assert details[0]["status"] == "rationalized"
        assert details[0]["passes"] == 1

    def test_loops_up_to_three_passes_when_each_call_overshoots(
            self, monkeypatch):
        # Every call returns 400 words (still over budget). The loop
        # must run the cap and then return still_over — not failed.
        call_count: list[int] = [0]

        def _fake(system, user, max_tokens=1200):
            call_count[0] += 1
            return " ".join(["lorem"] * 400)

        monkeypatch.setattr(rg, "_call_rationalizer_sync", _fake)
        paper = _make_over_budget_paper()
        _, details = asyncio.run(
            rg._rationalize_over_budget_sections(paper))

        assert call_count[0] == 3, (
            "expected three calls when every pass overshoots")
        assert len(details) == 1
        d = details[0]
        assert d["status"] == "still_over"
        assert d["passes"] == 3
        # The reviewer downstream sees the warn-only badge; the run
        # still proceeds. word_count_over_budget is in
        # _WARN_ONLY_FLAG_KINDS, so this status does NOT hard-gate.

    def test_each_pass_compresses_against_latest_rewrite(
            self, monkeypatch):
        # First pass returns ~400 words, second returns ~250 (now in
        # tolerance against the 300-word budget). The loop must bail
        # after the second pass, not the third.
        responses = iter([
            " ".join(["lorem"] * 400),  # pass 1 — still over
            " ".join(["lorem"] * 250),  # pass 2 — in tolerance
        ])
        call_count: list[int] = [0]

        def _fake(system, user, max_tokens=1200):
            call_count[0] += 1
            try:
                return next(responses)
            except StopIteration:
                raise AssertionError(
                    "rationalizer called more than twice")

        monkeypatch.setattr(rg, "_call_rationalizer_sync", _fake)
        paper = _make_over_budget_paper()
        _, details = asyncio.run(
            rg._rationalize_over_budget_sections(paper))

        assert call_count[0] == 2
        assert details[0]["status"] == "rationalized"
        assert details[0]["passes"] == 2

    def test_failed_first_pass_records_failed_not_still_over(
            self, monkeypatch):
        # Every attempted call fails. The detail records 'failed' and
        # the original section text survives unchanged.
        def _fake(system, user, max_tokens=1200):
            return ""

        monkeypatch.setattr(rg, "_call_rationalizer_sync", _fake)
        paper = _make_over_budget_paper()
        rewritten, details = asyncio.run(
            rg._rationalize_over_budget_sections(paper))

        # Original lorem padding survives the failed compression.
        assert "lorem lorem" in rewritten
        assert len(details) == 1
        d = details[0]
        assert d["status"] == "failed"
        # The "passes" key is present and records how many attempts
        # were made before the bailout (one — the first one failed).
        assert d.get("passes") == 1

    def test_late_failure_preserves_earlier_rewrite(self, monkeypatch):
        # First pass succeeds but is still over budget, second pass
        # fails. The earlier rewrite must be preserved (not reverted
        # to the original), with note='final_pass_failed' on the
        # detail.
        responses = iter([
            " ".join(["lorem"] * 400),  # pass 1 — succeeds but over
            "",                         # pass 2 — fails
        ])

        def _fake(system, user, max_tokens=1200):
            return next(responses)

        monkeypatch.setattr(rg, "_call_rationalizer_sync", _fake)
        paper = _make_over_budget_paper()
        _, details = asyncio.run(
            rg._rationalize_over_budget_sections(paper))

        d = details[0]
        assert d["status"] == "still_over"
        assert d.get("note") == "final_pass_failed"
        # The status is still_over (warn-only downstream), not 'failed'
        # — a partial success is honoured.


# ── Warn-only contract for still_over (option b) ────────────────────────────


class TestStillOverIsWarnOnly:
    """The user-explicit contract: a section that lands still_over
    after the rationalization cap is a WARNING, not a download
    blocker. _WARN_ONLY_FLAG_KINDS holds word_count_over_budget;
    _HARD_GATE_FLAG_KINDS does not. _gate_download (main.py) checks
    flag_count which counts only hard-gate kinds."""

    def test_word_count_over_budget_is_in_warn_only_set(self):
        assert "word_count_over_budget" in rg._WARN_ONLY_FLAG_KINDS

    def test_word_count_over_budget_is_NOT_in_hard_gate_set(self):
        assert "word_count_over_budget" not in rg._HARD_GATE_FLAG_KINDS

    def test_warn_and_hard_gate_sets_are_disjoint(self):
        # No flag kind can be both warn-only AND hard-gate.
        overlap = rg._WARN_ONLY_FLAG_KINDS & rg._HARD_GATE_FLAG_KINDS
        assert overlap == frozenset()


# ── _call_rationalizer_sync — fail-open contract ────────────────────────────


class TestRationalizerCallFailOpen:
    """The sync wrapper must never raise — a flaky LLM cannot wipe
    Bob's content. Failure paths return ''; the orchestrator treats
    that as 'keep original'."""

    def test_import_failure_returns_empty(self, monkeypatch):
        # Patch agents.base to raise on import via sys.modules.
        import sys as _sys
        original = _sys.modules.pop("agents.base", None)
        _sys.modules["agents.base"] = None  # type: ignore[assignment]
        try:
            out = rg._call_rationalizer_sync("sys", "user", 100)
            assert out == ""
        finally:
            if original is not None:
                _sys.modules["agents.base"] = original
            else:
                _sys.modules.pop("agents.base", None)

    def test_call_claude_exception_returns_empty(self, monkeypatch):
        # call_claude raises — wrapper swallows and returns ''.
        from agents import base
        def _boom(**kwargs):
            raise RuntimeError("simulated")
        monkeypatch.setattr(base, "call_claude", _boom)
        out = rg._call_rationalizer_sync("sys", "user", 100)
        assert out == ""

    def test_empty_response_returns_empty(self, monkeypatch):
        from agents import base
        monkeypatch.setattr(
            base, "call_claude",
            lambda **kwargs: "   \n\n  ")
        out = rg._call_rationalizer_sync("sys", "user", 100)
        assert out == ""
