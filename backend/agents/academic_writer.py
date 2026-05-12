"""
agents/academic_writer.py

Academic Writer Agent — Claude Sonnet (claude-sonnet-4-20250514).
Sprint 4: scaffold only. Report generation endpoints deferred to Sprint 6.

Generates APA 7th edition academic drafts for all three written deliverables.
All output is labeled "AI DRAFT — REQUIRES HUMAN REVIEW" and designed
to be edited by Bob, not submitted verbatim.

CRITICAL CONSTRAINT: Every number cited in output MUST be passed explicitly
as input. The agent never fabricates statistics, p-values, or citations.
All citations drawn ONLY from references.json — hallucinated references
would directly undermine the Analytical Appendix grade (35% of mark).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from agents.base import (
    GLOBAL_AGENT_RULE,
    SCOPE_ENFORCEMENT,
    SONNET_MODEL,
    build_agent_response,
    call_claude,
)

log = structlog.get_logger(__name__)

# Load the curated citation database once at import time.
# The agent is prohibited from citing any source not in this file.
_REFERENCES_PATH = Path(__file__).parent.parent / "data" / "references.json"
_REFERENCES: dict[str, Any] = {}


def _load_references() -> dict[str, Any]:
    """Loads references.json — the only permitted citation source."""
    try:
        return json.loads(_REFERENCES_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("references_load_failed", path=str(_REFERENCES_PATH), error=str(exc))
        return {}


_REFERENCES = _load_references()

_SYSTEM_PROMPT = f"""You are an academic writer specialising in quantitative finance research \
for graduate-level coursework. You write in APA 7th edition format.

STYLE REQUIREMENTS:
- Past tense throughout: 'The analysis examined...' not 'The analysis examines...'
- Third person: 'The study' or 'the research team' not 'we' or 'I'
- Hedged language: 'results suggest' not 'results prove', 'appeared to' not 'did'
- Precise statistical reporting: t(282) = 2.14, p = .003, d = 0.43
- Every claim supported by a specific number from the input data
- No unsupported generalisations

APA FORMATTING:
- In-text citations: (Author, Year) or (Author, Year, p. X) for quotes
- Reference list: hanging indent, alphabetical by author surname
- Tables: APA Table format with number, title, and notes
- Statistics: italicise t, F, p, r, M, SD (written in prose, not code)

ABSOLUTE PROHIBITIONS:
- Never cite a source not in the provided references list
- Never report a statistic not in the provided input data
- Never claim statistical significance beyond what is_significant shows
- Never use first person
- Never omit the 'AI DRAFT — REQUIRES HUMAN REVIEW' label

{GLOBAL_AGENT_RULE}

{SCOPE_ENFORCEMENT}"""

_AI_DRAFT_BANNER = "AI DRAFT — REQUIRES HUMAN REVIEW\n\n"


class AcademicWriter:
    """
    Generates APA-formatted academic prose for the three written deliverables.

    Sprint 4: agent exists for council integration.
    Sprint 6: report generation endpoints wired to these methods.

    The citation restriction (references.json only) is enforced by passing
    the full reference list as part of every prompt — the model sees exactly
    what citations it is allowed to use.
    """

    def write_methodology(
        self,
        data_sources: dict[str, Any],
        strategies: list[str],
        statistical_tests: list[str],
    ) -> str:
        """
        Generates the Data & Methodology section (~1 page, APA format).

        Describes data hierarchy, provenance, statistical framework,
        tiered p-value thresholds, CPCV, block bootstrap, and DSR.
        References strategies and test names passed as input — never invents them.
        """
        context = json.dumps(
            {
                "data_sources": data_sources,
                "strategies": strategies,
                "statistical_tests": statistical_tests,
                "references_available": list(_REFERENCES.keys()),
                "p_threshold_primary": 0.005,
                "p_threshold_subperiod": 0.05,
                "n_strategies": len(strategies),
                "walk_forward_train": 36,
                "walk_forward_test": 12,
            },
            indent=2,
            default=str,
        )

        user_message = (
            "Write the Data & Methodology section in APA 7th edition format. "
            "Cover: data sources and hierarchy, portfolio construction, "
            "statistical framework (p-thresholds, FDR, DSR, CPCV, block bootstrap). "
            "Cite only from the references_available list. ~400 words.\n\n"
            f"DATA:\n{context}"
        )

        try:
            text = call_claude(SONNET_MODEL, _SYSTEM_PROMPT, user_message, max_tokens=800)
            return _AI_DRAFT_BANNER + text
        except Exception as exc:
            log.error("academic_writer_methodology_error", error=str(exc))
            return _AI_DRAFT_BANNER + "Methodology section temporarily unavailable."

    def write_results(
        self,
        strategy_results: dict[str, Any],
        significance_flags: dict[str, bool],
        stress_tests: dict[str, Any] | None = None,
    ) -> str:
        """
        Generates the Results and Analysis section (~1.5 pages, APA format).

        Formats all metrics as APA statistical reporting.
        References only metrics passed in strategy_results — never invents numbers.
        """
        # Compact the results for token budget
        compact = {
            name: {
                "sharpe": r.get("sharpe_ratio"),
                "cagr": r.get("cagr"),
                "max_dd": r.get("max_drawdown"),
                "is_significant": r.get("is_significant"),
                "p_value_ttest": r.get("p_value_ttest"),
                "oos_sharpe": r.get("oos_sharpe"),
            }
            for name, r in strategy_results.items()
        }

        context = json.dumps(
            {
                "strategy_results": compact,
                "significant_strategies": [k for k, v in significance_flags.items() if v],
                "references_available": list(_REFERENCES.keys()),
                "stress_tests": stress_tests or {},
            },
            indent=2,
            default=str,
        )

        user_message = (
            "Write the Results and Analysis section in APA 7th edition format. "
            "Use APA Table format for strategy comparison. Report all statistics "
            "as: t(282) = x.xx, p = .xxx format. "
            "Cite only from references_available. ~600 words.\n\n"
            f"DATA:\n{context}"
        )

        try:
            text = call_claude(SONNET_MODEL, _SYSTEM_PROMPT, user_message, max_tokens=1024)
            return _AI_DRAFT_BANNER + text
        except Exception as exc:
            log.error("academic_writer_results_error", error=str(exc))
            return _AI_DRAFT_BANNER + "Results section temporarily unavailable."

    def write_discussion(
        self,
        limitations: list[str],
        regime_analysis: dict[str, Any],
        recommendations: str,
    ) -> str:
        """
        Generates the Discussion and Limitations section (~0.5 pages).

        Frames limitations honestly — no minimising. Draws from QA Agent
        limitations[] and Risk Manager regime_caveats[].
        """
        context = json.dumps(
            {
                "limitations": limitations,
                "regime_analysis": regime_analysis,
                "recommendations": recommendations,
                "references_available": list(_REFERENCES.keys()),
            },
            indent=2,
            default=str,
        )

        user_message = (
            "Write the Discussion and Limitations section in APA 7th edition format. "
            "Be honest about limitations — do not minimise them. "
            "Connect methodology to findings. ~200 words.\n\n"
            f"DATA:\n{context}"
        )

        try:
            text = call_claude(SONNET_MODEL, _SYSTEM_PROMPT, user_message, max_tokens=512)
            return _AI_DRAFT_BANNER + text
        except Exception as exc:
            log.error("academic_writer_discussion_error", error=str(exc))
            return _AI_DRAFT_BANNER + "Discussion section temporarily unavailable."

    def write_abstract(self, all_sections: str) -> str:
        """
        Generates a 150-word abstract after all sections are complete.

        Structured: purpose, method, findings, implications.
        Never exceeds 150 words — APA abstract constraint.
        """
        user_message = (
            "Write a 150-word APA abstract with four elements: "
            "purpose, method, findings (with specific numbers), implications. "
            "Strictly 150 words — no more.\n\n"
            f"SECTIONS TO SUMMARISE:\n{all_sections[:1000]}"
        )

        try:
            text = call_claude(SONNET_MODEL, _SYSTEM_PROMPT, user_message, max_tokens=300)
            return _AI_DRAFT_BANNER + text
        except Exception as exc:
            log.error("academic_writer_abstract_error", error=str(exc))
            return _AI_DRAFT_BANNER + "Abstract temporarily unavailable."

    def write_references(self, citations_used: list[str]) -> str:
        """
        Generates an APA reference list from references.json.

        Only includes works actually cited — prevents reference list inflation.
        Every key in citations_used must exist in _REFERENCES or is skipped.
        """
        apa_entries = []
        for key in citations_used:
            ref = _REFERENCES.get(key)
            if ref and ref.get("apa"):
                apa_entries.append(ref["apa"])

        if not apa_entries:
            return _AI_DRAFT_BANNER + "References\n\nNo verified citations provided."

        # Sort alphabetically by first author surname (APA requirement)
        apa_entries.sort()
        reference_list = "\n\n".join(apa_entries)
        return _AI_DRAFT_BANNER + f"References\n\n{reference_list}"

    def format_apa_table(
        self,
        data: dict[str, Any],
        caption: str,
        notes: str,
    ) -> str:
        """
        Formats a data dict as an APA-compliant table in markdown.

        APA Table format: number, title above, notes below in italics.
        Used for strategy comparison tables in the Analytical Appendix.
        """
        if not data:
            return f"*Table: {caption}*\n\nNo data available."

        # Build simple markdown table from dict
        headers = list(next(iter(data.values())).keys()) if data else []
        rows = [
            "| " + " | ".join([str(name)] + [str(v) for v in vals.values()]) + " |"
            for name, vals in data.items()
        ]
        header_row = "| Strategy | " + " | ".join(headers) + " |"
        separator = "|" + "|".join(["---"] * (len(headers) + 1)) + "|"

        return (
            f"*Table N*\n\n*{caption}*\n\n"
            + "\n".join([header_row, separator] + rows)
            + f"\n\n*Note.* {notes}"
        )

    @staticmethod
    def get_available_references() -> dict[str, Any]:
        """Returns the full reference database for validation."""
        return _REFERENCES
