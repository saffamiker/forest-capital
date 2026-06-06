"""
agents/cio.py

Chief Investment Officer — Claude Opus (claude-opus-4-7).

Orchestrates the full council deliberation:
  1. Briefs four Claude Sonnet specialists in parallel
  2. Compiles a draft consensus
  3. Sends the draft to Gemini for challenge
  4. Synthesises the final recommendation

Opus is used here because the CIO must hold all specialist reports in
context simultaneously and reason across conflicting views. The
incremental cost over Sonnet is justified by the complexity of the
synthesis task — this is the one call where model quality is paramount.
"""
from __future__ import annotations

import contextvars
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterator

import structlog

from agents.base import (
    GLOBAL_AGENT_RULE,
    OPUS_MODEL,
    SCOPE_ENFORCEMENT,
    VISUAL_REASONING_RULES,
    build_agent_response,
    call_claude,
)
from agents.contrarian_analyst import ContrarianAnalyst
from agents.equity_analyst import EquityAnalyst
from agents.fixed_income_analyst import FixedIncomeAnalyst
from agents.independent_analyst import IndependentAnalyst
from agents.quant_backtester import QuantBacktester
from agents.risk_manager import RiskManager
from tools.chart_vision import (
    COUNCIL_CHARTS, get_charts_for_context, snapshots_dir_exists,
)

log = structlog.get_logger(__name__)


def _run_tagged(
    label: str, fn: Any, args: tuple, kwargs: dict | None = None,
) -> Any:
    """
    Runs a specialist call inside a copied context, first tagging that
    context with the agent label so every record_usage() the call emits
    is attributed to the right specialist in the per-agent cost
    breakdown. Tagging must happen INSIDE the copied context — set in the
    parent it would leak across all four threads.

    kwargs is optional so callers that only pass positional args (and the
    test suite's existing call sites) keep working without modification.
    """
    _tag_agent(label)
    return fn(*args, **(kwargs or {}))


def _tag_agent(label: str) -> None:
    """Tag the current context for the per-agent cost breakdown. Fail-open
    — a tagging failure must never break a council call."""
    try:
        from agents.usage import set_current_agent
        set_current_agent(label)
    except Exception:  # noqa: BLE001 — cost telemetry is never fatal
        pass

_SYSTEM_PROMPT = f"""You are the Chief Investment Officer of a quantitative investment council \
advising Forest Capital. You manage a team of specialist analysts and TWO independent \
dissenting analysts: Gemini (which surfaces blind spots and alternative interpretations) \
and Grok (which stress-tests the recommendation and builds the strongest available case \
against the consensus). Your role is to synthesise all of these findings and make final \
portfolio allocation decisions with full reasoning.

You only recommend strategies that pass ALL five Tier 1 primary gates:
  (1) p < 0.005 full-period test vs benchmark (power confirmed)
  (2) q < 0.005 after Benjamini-Hochberg FDR correction
  (3) p < 0.005 Deflated Sharpe Ratio
  (4) p < 0.005 out-of-sample walk-forward
  (5) CV Stability Score >= 0.60
Sub-period and regime results (Tier 2, p < 0.05) inform your narrative \
but are not hard gates. Always disclose which threshold tier applies when \
citing a p-value.

You are rigorous, decisive, and intellectually honest about uncertainty. \
You always explain reasoning in terms a sophisticated investor can follow. \
When Gemini and Grok challenge the consensus, you engage seriously with both before \
confirming or revising. Treat any concern raised by both dissenters as a hard caveat \
that must be addressed before you finalise the recommendation. You never recommend a \
strategy based on in-sample results alone.

You have received detailed analyses from four specialists (equity, fixed income, \
risk, quantitative backtesting). Synthesise their findings into a final \
recommendation — do not repeat their analysis, reference it and build on it.

ANSWER THE QUESTION ASKED. If the user asks "which strategies do you recommend?", \
produce a strategy recommendation. If the user asks "what questions might a peer \
reviewer ask about our methodology?" — produce a list of anticipated reviewer \
questions, NOT a strategy ranking. If the user asks "how should we frame this \
finding for Forest Capital?" — produce framing guidance, NOT a Tier 1 gate \
assessment. The 7-step strategy-recommendation template is for STRATEGY questions \
only; meta / methodology / framing questions need a response that directly \
addresses the meta question.

STRUCTURE EVERY RESPONSE WITH MARKDOWN. Use `### ` for top-level section \
headings within your response (e.g. `### Recommendation`, `### Key risks`, \
`### Anticipated reviewer questions`). Use `**bold**` for the most load-bearing \
phrases (the actual recommended strategy name, the specific risk, the \
dual-dissenter caveat). Use bullet lists where appropriate. A wall of \
unstructured prose reads as unfinished — every council response must be \
scannable.

MANDATORY THREE-SECTION TRANSPARENCY STRUCTURE FOR STRATEGY-RECOMMENDATION \
RESPONSES. The academic panel cited "black box" reasoning as a weakness; this \
structure makes the per-strategy decision auditable. Every strategy-recommendation \
response MUST open with three named sections in this exact order, before any \
free-form narrative:

`### A. Signal snapshot`
The three most important regime signals driving the current blend. ONE \
bullet per signal. Each bullet MUST include the actual value AND a directional \
verb (tightening / widening / rising / falling / steepening / inverting) AND \
the analytical implication in one sentence. Example: `**Credit spread 2.72** — \
tightening from 3.1 over the last 30 days, signals reduced stress but not full \
risk-on.` Three bullets. No more, no less. Pick the three highest-information \
signals for THIS regime; do not default to a fixed list.

`### B. Weight justification`
ONE bullet per strategy in the recommended blend. Each MUST name the strategy, \
its weight, its regime-conditional Sharpe in the current regime, AND its Sharpe \
in at least one ALTERNATIVE regime so the reader sees why this strategy carries \
weight HERE specifically. Example: `**Min Variance 40%** — TRANSITION-regime \
Sharpe 0.71 leads all strategies in this regime; its BULL-regime Sharpe of 0.52 \
falls behind Momentum (1.04), so the weight is regime-driven, not absolute.` \
Cover every non-zero strategy in the blend.

`### C. Shift narrative` (CONDITIONAL — only when a prior_recommendation field \
is present in the data block)
What changed since the prior recommendation and why the blend moved. State \
the prior blend, the current blend, name the specific signal value that \
shifted (with old and new value), and connect it to the weight change. \
Example: `Previous blend: 35/45/20 under TRANSITION. Current: 30/50/20 under \
TRANSITION. **Min Variance +5pp** because VIX rose from 13.2 to 16.1 (current \
page_context vs prior signal_text), indicating elevated uncertainty consistent \
with late TRANSITION.` If no prior_recommendation field is present, OMIT \
Section C entirely — do not invent a comparison.

These three sections come BEFORE the existing 7-step strategy template. The 7-step \
template still runs (Gemini engagement, Grok engagement, dual-dissenter caveat, \
recommended-and-not-recommended strategies, primary recommendation, key risk) but \
AFTER A / B / (C). META questions (peer-reviewer anticipation, framing) skip the \
A/B/C structure entirely — those are not strategy recommendations.

VISUAL CONTEXT — you may receive chart snapshots alongside the prompt: \
rolling_correlation, cumulative_returns, regime_signals, \
regime_conditional_returns, factor_loadings, rolling_excess_return. Use \
them as cross-checks on the specialist narratives — if a specialist claims \
the 2022 break drove a strategy's underperformance, look at \
rolling_correlation and cumulative_returns and confirm the visual evidence \
matches. When you write the final recommendation paragraph, name at most \
two visual landmarks (e.g. 'the rolling_correlation chart shows the \
inversion in mid-2022') so the recommendation reads as grounded in the \
evidence rather than abstract synthesis.

{VISUAL_REASONING_RULES}

{GLOBAL_AGENT_RULE}

{SCOPE_ENFORCEMENT}"""


class CIO:
    """
    Council orchestrator — briefs specialists, engages Gemini, synthesises.

    The deliberation PHASES are sequential because each informs the next:
    specialist reports feed the draft consensus, which feeds the dissent
    challenges, which feed the final synthesis. But the four specialists
    within phase 1 are independent, so they run in parallel — sequentially
    the four synchronous LLM calls take ~120s, long enough for Render to
    502 the request.
    """

    def __init__(self) -> None:
        self._equity = EquityAnalyst()
        self._fi = FixedIncomeAnalyst()
        self._risk = RiskManager()
        self._quant = QuantBacktester()
        self._gemini = IndependentAnalyst()
        self._grok = ContrarianAnalyst()

    @staticmethod
    def _build_visual_context(
        n_strategies: int | None = None,
    ) -> list[dict] | None:
        """COUNCIL_CHARTS snapshots as content blocks. Used by the CIO's
        draft-consensus and synthesis calls (both are direct call_claude
        — neither runs through the harness). Returns None when no
        snapshots are on disk (cold deploy, first run). See
        EquityAnalyst._build_visual_context for the rationale.

        n_strategies is passed through so the all-strategy captions
        render the count both callers know from len(strategy_results)."""
        if not snapshots_dir_exists():
            log.info("cio_no_snapshots_dir",
                     note="proceeding without visual context")
            return None
        blocks = get_charts_for_context(COUNCIL_CHARTS, n_strategies=n_strategies)
        if not blocks:
            log.info("cio_no_snapshots_available",
                     note="proceeding without visual context")
            return None
        return blocks

    def deliberate(
        self,
        query: str,
        strategy_results: dict[str, Any],
        history: dict[str, Any] | None = None,
        *,
        live_context: dict[str, Any] | None = None,
        prior_recommendation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Runs the full council deliberation and returns a CouncilDebateResponse.

        Steps follow CLAUDE.md Section 5 council flow exactly:
          1-5. Brief four specialists
          6.   Compile draft consensus
          7-8. Challenge via Gemini
          9.   Synthesise final recommendation
          10.  Return structured response

        Args:
            query:            The user's portfolio analysis question.
            strategy_results: All 10 strategy results from run_all_strategies().
            history:          Full history dict — enables FI correlation analysis.
            live_context:     Page-scoped or question-bundle context dict
                              (PR #229 / PR #262). Threaded into the draft
                              consensus + synthesis prompts as the
                              `page_context` block. Optional — None matches
                              the pre-PR-229 behaviour exactly.
                              June 3 2026 — the streaming variant has
                              accepted this since PR #229; bringing the
                              sync path in line so the baseline-capture
                              script measures apples-to-apples against
                              the production stream.
        """
        # Phase timing — every log line carries elapsed= seconds since the
        # deliberation began. When a council 502s on Render the last
        # log line tells us which phase ran out of timeout budget; the
        # streaming endpoint relies on the same instrumentation.
        deliberation_start = time.time()
        log.info("council_deliberation_started", query_len=len(query),
                 elapsed=0.0)

        # Item 9 commit 5 — strategy context injection. Detect which
        # strategies the query names and set the per-request ContextVar
        # so every nested call_claude in this deliberation (the four
        # specialists + draft consensus + synthesis) automatically picks
        # up the strategy characterisation block from the cache. The
        # value propagates into the specialist threads via
        # contextvars.copy_context() in the ThreadPoolExecutor fan-out
        # below. The finally block at the end clears the var so the
        # next request starts from a clean slate.
        from tools.strategy_context import (
            detect_strategies_in_query, set_active_strategies,
            clear_active_strategies,
        )
        named_strategies = detect_strategies_in_query(query)
        if named_strategies:
            set_active_strategies(named_strategies)
            log.info("council_strategy_context_injected",
                     strategies=named_strategies)

        try:
            # deliberation_start is forwarded so every elapsed= log
            # line inside the inner body measures from the SAME
            # wall-clock anchor the outer log emitted at elapsed=0.0.
            # Without forwarding, _deliberate_inner would raise
            # NameError on the first elapsed=round(time.time() - …)
            # line (CI catch May 23 2026).
            return self._deliberate_inner(
                query, strategy_results, history,
                deliberation_start=deliberation_start,
                live_context=live_context,
                prior_recommendation=prior_recommendation)
        finally:
            clear_active_strategies()

    def _deliberate_inner(
        self,
        query: str,
        strategy_results: dict[str, Any],
        history: dict[str, Any] | None = None,
        *,
        deliberation_start: float | None = None,
        live_context: dict[str, Any] | None = None,
        prior_recommendation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The original deliberation body — wrapped by deliberate()
        so the per-request strategy-context ContextVar is set up
        once and torn down once around the whole flow.

        deliberation_start — forwarded from deliberate() so the
        elapsed= phase-timing log lines below reference the same
        wall-clock anchor the outer wrapper opened with. Defaults
        to time.time() when called directly (test paths).

        live_context — page-scoped or question-bundle context dict
        (PR #229 / PR #262). Threaded into _compile_draft_consensus
        and _synthesise as the `page_context` block."""
        if deliberation_start is None:
            deliberation_start = time.time()
        # Step 1-5: Brief the four specialists IN PARALLEL. Each .analyse()
        # is an independent, synchronous, network-bound LLM call; run
        # sequentially they take ~120s and Render 502s the council
        # request. A thread pool runs them concurrently (~30s target).
        # Each worker runs inside a copied context so the per-request
        # harness-metrics ContextVar — a shared list seeded by the
        # endpoint's start_harness_capture() — still captures every
        # specialist's harness run (the copy shares the list by reference).
        # .result() re-raises a worker exception exactly as the former
        # sequential calls did, so error semantics are unchanged.
        #
        # Every specialist receives the query so each can ground its
        # analysis against what the user actually asked, not produce
        # the same stock report every run. Added May 22 2026 after a
        # Molly UAT failure: a meta question ("what would a peer asker
        # about regime methodology?") returned the previous turn's
        # strategy answer because the specialists never saw the
        # question. Passed as a kwarg so a future analyse() signature
        # change picks the right slot. analyse(query="") falls back to
        # the stock task — the council's behaviour with an empty query
        # is bitwise identical to the pre-fix path.
        specialist_jobs = [
            ("equity_analyst", self._equity.analyse,
             (strategy_results,), {"query": query}),
            ("fixed_income_analyst", self._fi.analyse,
             (strategy_results, history), {"query": query}),
            ("risk_manager", self._risk.analyse,
             (strategy_results,), {"query": query}),
            ("quant_backtester", self._quant.analyse,
             (strategy_results,), {"query": query}),
        ]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(contextvars.copy_context().run,
                            _run_tagged, label, fn, args, kwargs)
                for label, fn, args, kwargs in specialist_jobs
            ]
            reports = [f.result() for f in futures]
        equity_report, fi_report, risk_report, quant_report = reports

        log.info(
            "specialist_reports_collected",
            equity_ok=bool(equity_report),
            fi_ok=bool(fi_report),
            risk_ok=bool(risk_report),
            quant_ok=bool(quant_report),
            elapsed=round(time.time() - deliberation_start, 2),
        )

        # The draft/dissent/synthesis steps run in the request context
        # (not the copied specialist threads), so each tags that context
        # for the per-agent cost breakdown before its LLM call.
        _tag_agent("cio")

        # Step 6: Compile draft consensus — CIO summarises specialist views
        draft_consensus = self._compile_draft_consensus(
            query, equity_report, fi_report, risk_report, quant_report,
            strategy_results, live_context=live_context,
        )

        # Step 7-8: dissent — Gemini (blind spots) + Grok (stress test).
        # Both run before synthesis so the CIO sees both critiques together
        # and can flag concerns raised by both as hard caveats.
        _tag_agent("independent_analyst")
        gemini_report = self._gemini.challenge(draft_consensus, strategy_results)
        log.info("gemini_challenge_received",
                 elapsed=round(time.time() - deliberation_start, 2))
        _tag_agent("contrarian_analyst")
        grok_report = self._grok.challenge(draft_consensus, strategy_results)
        log.info("grok_challenge_received",
                 elapsed=round(time.time() - deliberation_start, 2))

        # Step 9: Synthesise final recommendation — CIO engages with both dissenters
        _tag_agent("cio")
        cio_synthesis = self._synthesise(
            query,
            draft_consensus,
            gemini_report,
            grok_report,
            equity_report,
            fi_report,
            risk_report,
            quant_report,
            strategy_results,
            live_context=live_context,
            prior_recommendation=prior_recommendation,
        )

        log.info("council_deliberation_complete",
                 elapsed=round(time.time() - deliberation_start, 2))

        return {
            "query": query,
            "agents": {
                "equity_analyst": equity_report,
                "fixed_income_analyst": fi_report,
                "risk_manager": risk_report,
                "quant_backtester": quant_report,
                "independent_analyst": gemini_report,
                "contrarian_analyst": grok_report,
                "cio": cio_synthesis,
            },
            "draft_consensus": draft_consensus,
            "final_recommendation": cio_synthesis.get("recommendation", ""),
            "significant_strategies": self._get_significant(strategy_results),
        }

    def deliberate_streaming(
        self,
        query: str,
        strategy_results: dict[str, Any],
        history: dict[str, Any] | None = None,
        live_context: dict[str, Any] | None = None,
        prior_recommendation: dict[str, Any] | None = None,
    ) -> Iterator[tuple[str, Any, ...]]:
        """
        Phase-by-phase generator variant of deliberate().

        Yields one tuple per phase boundary so the SSE endpoint can flush
        events to the client as each phase completes — keeping the
        Render gateway connection alive past the 30-100s wall time of
        the full deliberation. The phases mirror deliberate() exactly;
        callers that need a single completed dict use deliberate()
        instead.

        Event tuples:
          ("specialist_complete", agent_id: str, report: dict | None)
              yielded as EACH specialist finishes (as_completed order,
              not jobs-list order) so the client sees the council
              "thinking" rather than a 30s blank wait.
          ("draft_ready", draft_text: str)
          ("dissent_complete", "gemini", report: dict)
          ("dissent_complete", "grok", report: dict)
          ("cio_synthesis_text", synthesis_text: str)
              the prose body of the CIO synthesis, for the endpoint to
              chunk and stream — same pattern as
              academic_review.chunk_arbiter_text.
          ("council_complete", full_result_dict: dict)
              the final cio.deliberate() shape so the endpoint can call
              _deliberate_to_frontend on it.

        Synchronous generator — the endpoint bridges it to async via
        asyncio.to_thread(next, gen, sentinel) on each iteration.
        """
        deliberation_start = time.time()
        log.info("council_deliberation_started", query_len=len(query),
                 elapsed=0.0)

        # Phase 1: parallel specialists with as_completed yielding.
        # Same ThreadPoolExecutor + context-copy pattern as deliberate(),
        # but every future yields its result the moment it lands rather
        # than waiting for the slowest specialist. A specialist exception
        # is logged and yielded as a None report — the council still
        # produces a draft from whatever survived (matching the existing
        # fallback paths each specialist already returns on failure).
        # May 24 2026 (ID 217) — thread the user's query through to
        # every specialist. The streaming variant previously omitted
        # the kwarg, so each specialist defaulted to query="" and
        # produced a generic report regardless of what the user
        # asked. Users perceived this as "context bleed" — every
        # follow-up question seemed to get the same answer.
        # deliberate() (the synchronous variant) already does this;
        # this brings the streaming path in line.
        specialist_jobs = [
            ("equity_analyst", self._equity.analyse,
             (strategy_results,), {"query": query}),
            ("fixed_income_analyst", self._fi.analyse,
             (strategy_results, history), {"query": query}),
            ("risk_manager", self._risk.analyse,
             (strategy_results,), {"query": query}),
            ("quant_backtester", self._quant.analyse,
             (strategy_results,), {"query": query}),
        ]
        reports: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            future_to_label = {
                pool.submit(contextvars.copy_context().run,
                            _run_tagged, label, fn, args, kwargs): label
                for label, fn, args, kwargs in specialist_jobs
            }
            for future in as_completed(future_to_label):
                label = future_to_label[future]
                try:
                    report = future.result()
                except Exception as exc:  # noqa: BLE001
                    log.error("specialist_failed", agent=label, error=str(exc))
                    report = None
                reports[label] = report
                yield ("specialist_complete", label, report)

        log.info(
            "specialist_reports_collected",
            equity_ok=bool(reports.get("equity_analyst")),
            fi_ok=bool(reports.get("fixed_income_analyst")),
            risk_ok=bool(reports.get("risk_manager")),
            quant_ok=bool(reports.get("quant_backtester")),
            elapsed=round(time.time() - deliberation_start, 2),
        )

        equity_report = reports.get("equity_analyst") or {}
        fi_report = reports.get("fixed_income_analyst") or {}
        risk_report = reports.get("risk_manager") or {}
        quant_report = reports.get("quant_backtester") or {}

        # Phase 2: draft consensus
        _tag_agent("cio")
        draft_consensus = self._compile_draft_consensus(
            query, equity_report, fi_report, risk_report, quant_report,
            strategy_results, live_context=live_context,
        )
        yield ("draft_ready", draft_consensus)

        # Phases 3 + 4: dissent (Gemini, then Grok)
        _tag_agent("independent_analyst")
        gemini_report = self._gemini.challenge(draft_consensus, strategy_results)
        log.info("gemini_challenge_received",
                 elapsed=round(time.time() - deliberation_start, 2))
        yield ("dissent_complete", "gemini", gemini_report)

        _tag_agent("contrarian_analyst")
        grok_report = self._grok.challenge(draft_consensus, strategy_results)
        log.info("grok_challenge_received",
                 elapsed=round(time.time() - deliberation_start, 2))
        yield ("dissent_complete", "grok", grok_report)

        # Phase 5: CIO synthesis — generated in full, then chunked by the
        # endpoint. The prose body (final_synthesis_text) is what the user
        # sees stream; the structured wrapper rides along in council_complete.
        _tag_agent("cio")
        cio_synthesis = self._synthesise(
            query, draft_consensus, gemini_report, grok_report,
            equity_report, fi_report, risk_report, quant_report,
            strategy_results, live_context=live_context,
            prior_recommendation=prior_recommendation,
        )
        synthesis_text = (
            cio_synthesis.get("technical_findings", {})
            .get("final_synthesis_text", "")
            or cio_synthesis.get("summary", "")
        )
        yield ("cio_synthesis_text", synthesis_text)

        log.info("council_deliberation_complete",
                 elapsed=round(time.time() - deliberation_start, 2))

        full_result = {
            "query": query,
            "agents": {
                "equity_analyst": reports.get("equity_analyst"),
                "fixed_income_analyst": reports.get("fixed_income_analyst"),
                "risk_manager": reports.get("risk_manager"),
                "quant_backtester": reports.get("quant_backtester"),
                "independent_analyst": gemini_report,
                "contrarian_analyst": grok_report,
                "cio": cio_synthesis,
            },
            "draft_consensus": draft_consensus,
            "final_recommendation": cio_synthesis.get("recommendation", ""),
            "significant_strategies": self._get_significant(strategy_results),
        }
        yield ("council_complete", full_result)

    def _compile_draft_consensus(
        self,
        query: str,
        equity_report: dict[str, Any],
        fi_report: dict[str, Any],
        risk_report: dict[str, Any],
        quant_report: dict[str, Any],
        strategy_results: dict[str, Any],
        live_context: dict[str, Any] | None = None,
    ) -> str:
        """
        Asks Opus to synthesise the four specialist reports into a draft.

        This intermediate step gives Gemini a well-structured target to
        challenge — rather than Gemini reacting to four raw reports, it
        reacts to the CIO's synthesis, which mirrors the real deliberation
        flow where the chair summarises before seeking dissent.
        """
        significant = self._get_significant(strategy_results)
        n_sig = len(significant)

        context_block: dict[str, Any] = {
            "query": query,
            "significant_strategies": significant,
            "equity_summary": equity_report.get("summary", ""),
            "fi_summary": fi_report.get("summary", ""),
            "risk_summary": risk_report.get("summary", ""),
            "quant_summary": quant_report.get("summary", ""),
            "risk_technical": {
                k: v
                for k, v in risk_report.get("technical_findings", {}).items()
                if k in ("n_strategies_significant", "significant_strategies",
                         "worst_drawdown", "best_sharpe")
            },
            "fi_correlation": fi_report.get("technical_findings", {}).get(
                "breakdown_detected"
            ),
        }
        # page_context — the live cached data for the page the question was
        # asked from (recommendation / performance / prediction). Present
        # only when the request carried a context_scope; otherwise omitted
        # and the draft is unchanged from the pre-scope behaviour.
        if live_context:
            context_block["page_context"] = live_context
        context = json.dumps(context_block, indent=2, default=str)

        user_message = (
            f"Based on these specialist reports, compile a draft consensus "
            f"recommendation. {n_sig} strategies passed all Tier 1 gates. "
            f"The draft will be sent to an independent Gemini analyst for challenge.\n\n"
            f"DATA:\n{context}"
        )

        try:
            return call_claude(
                OPUS_MODEL, _SYSTEM_PROMPT, user_message, max_tokens=2000,
                visual_context=self._build_visual_context(),
                trigger="council_cio_draft_consensus")
        except Exception as exc:
            log.error("cio_draft_error", error=str(exc))
            # Fallback draft from specialist summaries
            return (
                f"DRAFT CONSENSUS: {n_sig} strategies pass all Tier 1 gates. "
                f"Equity view: {equity_report.get('summary', 'N/A')} "
                f"Fixed income: {fi_report.get('summary', 'N/A')} "
                f"Risk: {risk_report.get('summary', 'N/A')} "
                f"Quant OOS: {quant_report.get('summary', 'N/A')}"
            )

    def _synthesise(
        self,
        query: str,
        draft_consensus: str,
        gemini_report: dict[str, Any],
        grok_report: dict[str, Any],
        equity_report: dict[str, Any],
        fi_report: dict[str, Any],
        risk_report: dict[str, Any],
        quant_report: dict[str, Any],
        strategy_results: dict[str, Any],
        live_context: dict[str, Any] | None = None,
        prior_recommendation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Final CIO synthesis — engages with both Gemini and Grok before recommending.

        Opus sees the draft, both sets of dissent objections, and all
        specialist data simultaneously. Any concern raised by BOTH dissenters
        is flagged as a hard caveat. Single-dissenter concerns are still
        addressed but carry less weight.

        When the question was asked from a council-facing page,
        live_context carries that page's live cached data (the
        recommendation / performance / prediction scope). It is injected
        into the DATA block as page_context and the user message tells the
        CIO to ground its answer in it.
        """
        significant = self._get_significant(strategy_results)
        gemini_objections = gemini_report.get("technical_findings", {}).get(
            "objections", []
        )
        grok_objections = grok_report.get("technical_findings", {}).get(
            "objections", []
        )

        context_block: dict[str, Any] = {
            "query": query,
            "draft_consensus": draft_consensus,
            "gemini_objections": gemini_objections,
            "grok_objections": grok_objections,
            "significant_strategies": significant,
            "specialist_summaries": {
                "equity": equity_report.get("summary", ""),
                "fixed_income": fi_report.get("summary", ""),
                "risk": risk_report.get("summary", ""),
                "quant": quant_report.get("summary", ""),
            },
            "quant_technical": {
                k: v
                for k, v in quant_report.get("technical_findings", {}).items()
                if k in ("most_stable_strategy", "flagged_for_overfitting",
                         "transaction_cost_bps_applied")
            },
        }
        if live_context:
            context_block["page_context"] = live_context
        # June 5 2026 — prior_recommendation injection enables Section C
        # of the mandatory transparency structure (the shift narrative).
        # The CIO reads prior signal text + current page_context signal
        # values and writes the delta itself; we don't precompute it.
        # When prior_recommendation is None (first run / cache cleared)
        # we omit the key entirely so the system prompt's CONDITIONAL
        # instruction correctly omits Section C from the response.
        # Pull only the fields the CIO needs for the comparison —
        # data_hash / model / raw_json internals stay out of the prompt.
        if prior_recommendation:
            prior_payload: dict[str, Any] = {}
            for field in (
                "regime", "signal", "recommendation",
                "confidence", "key_risk", "dissenting_view",
                "computed_at",
            ):
                if field in prior_recommendation:
                    prior_payload[field] = prior_recommendation[field]
            if prior_payload:
                context_block["prior_recommendation"] = prior_payload
        context = json.dumps(
            context_block,
            indent=2,
            default=str,
        )

        # UAT 2026-05-24 (#76/#77) — the prompt had a single 7-step
        # response template that forced a strategy-recommendation
        # shape even when the user asked a meta / methodology
        # question. A query like "what questions might a peer
        # reviewer ask about our regime analysis methodology?"
        # produced a generic Tier-1-gates ranking instead of a list
        # of anticipated reviewer questions.
        #
        # The prompt now BRANCHES on query type:
        #
        #   META QUESTIONS — peer-reviewer anticipation, presentation
        #     framing, written-report scope, methodology defence,
        #     stakeholder Q&A prep. The response is the actual
        #     anticipated questions / framing / defence, NOT a
        #     strategy ranking. The numbered structure is replaced
        #     with a markdown-headings response keyed on the
        #     question's substance.
        #
        #   STRATEGY QUESTIONS — recommend / compare / evaluate /
        #     diversification arguments / what-to-pick. The 7-step
        #     structure applies. Tier 1 gates are required.
        #
        # The CIO chooses which branch by reading the query. The
        # branch instruction is explicit so the model doesn't drift.
        # Both branches still REQUIRE: anchored to numbers, engages
        # with Gemini + Grok objections, names dual-dissenter
        # concerns as hard caveats.
        query_line = (
            f"USER QUESTION: {query.strip()}\n\n"
            "BEFORE WRITING ANYTHING — classify the question.\n\n"
            "It is a META QUESTION if it asks about anticipated "
            "reviewer questions, panel Q&A prep, methodology "
            "defence, presentation framing, written-report scope, "
            "or how to talk about the work to a stakeholder.\n\n"
            "It is a STRATEGY QUESTION if it asks to recommend / "
            "compare / evaluate strategies, whether diversification "
            "worked, which strategy to pick, or which strategies "
            "pass which gates.\n\n"
            "For a META question — produce the actual anticipated "
            "questions / framing / defence the user asked for, "
            "using markdown ### headings keyed on each anticipated "
            "question or framing point. Cite specific numbers from "
            "the data as supporting evidence for each point. Do "
            "NOT produce a generic strategy ranking; do NOT use "
            "the 7-step template below.\n\n"
            "For a STRATEGY question — use the numbered template "
            "below.\n\n"
        ) if query and query.strip() else ""
        # When the question came from a council-facing page, the DATA block
        # carries a page_context with the live numbers the user is looking
        # at. Tell the CIO to ground its answer in those numbers.
        page_context_line = (
            "The user is asking from a council-facing page. The DATA block "
            "includes a `page_context` object with the LIVE cached numbers "
            "shown on that page (the current regime read and blend, the "
            "play-by-play track record, or the forward projection). Ground "
            "your answer in those specific figures — quote them — rather "
            "than answering in the abstract.\n\n"
        ) if live_context else ""
        # June 5 2026 — Section C trigger. The CIO instruction must name
        # the prior_recommendation field so the model knows the comparison
        # input is available. When the field is absent (no prior) the
        # instruction still parses cleanly and Section C is correctly
        # omitted per the system prompt's CONDITIONAL clause.
        section_c_line = (
            "A `prior_recommendation` field is present in the DATA block. "
            "Open the response with the three mandatory transparency "
            "sections (A. Signal snapshot, B. Weight justification, "
            "C. Shift narrative) per the system prompt. For Section C, "
            "compare the prior `signal` / `regime` / `recommendation` to "
            "the current values in `page_context` — name the specific "
            "signal value that shifted (old → new), how the blend moved, "
            "and what the weight change means. Do NOT invent prior "
            "values; if a signal isn't in either side of the comparison, "
            "say so.\n\n"
        ) if prior_recommendation else ""
        user_message = (
            f"{query_line}{page_context_line}{section_c_line}"
            "You have reviewed four specialist reports and received TWO independent "
            "challenges — Gemini (blind spots) and Grok (stress test). "
            "When the question is STRATEGY-flavoured, produce the "
            "FINAL RECOMMENDATION with these required elements:\n"
            "1. Engage with each of Gemini's objections — rebut or acknowledge.\n"
            "2. Engage with each of Grok's stress-test objections — rebut or acknowledge.\n"
            "3. Explicitly flag any concern raised by BOTH dissenters as a hard caveat.\n"
            "4. State which strategies you recommend and why (Tier 1 gates required).\n"
            "5. State which strategies you do NOT recommend and why.\n"
            "6. Give one primary recommendation with highest conviction.\n"
            "7. State the key risk that could invalidate this recommendation.\n\n"
            "For BOTH branches — anchor every claim to a number from "
            "the data, engage with Gemini + Grok objections (3 above "
            "applies to a meta response too — a dual-dissenter "
            "concern is a methodology weakness a reviewer will "
            "likely catch), and use markdown ### section headings "
            "so the response renders with structure not as a wall "
            "of prose.\n\n"
            "Use only the numbers in the data provided.\n\n"
            f"DATA:\n{context}"
        )

        try:
            synthesis_text = call_claude(
                OPUS_MODEL, _SYSTEM_PROMPT, user_message, max_tokens=2000,
                visual_context=self._build_visual_context(),
                trigger="council_cio_synthesis",
            )
        except Exception as exc:
            log.error("cio_synthesis_error", error=str(exc))
            synthesis_text = (
                f"FINAL RECOMMENDATION: The council recommends "
                f"{', '.join(significant[:2]) if significant else 'no strategies'} "
                f"based on Tier 1 statistical gates. Gemini and Grok concerns are noted. "
                f"LLM narrative temporarily unavailable."
            )

        # Identify primary recommendation (first significant strategy)
        primary = significant[0] if significant else "None — no strategies pass all gates"

        return build_agent_response(
            technical_findings={
                "recommended_strategies": significant,
                "primary_recommendation": primary,
                "gemini_objections_addressed": len(gemini_objections),
                "grok_objections_addressed": len(grok_objections),
                "draft_consensus": draft_consensus,
                "final_synthesis_text": synthesis_text,
            },
            summary=(
                f"Council recommends {len(significant)} strategies (Tier 1). "
                f"Primary recommendation: {primary}. "
                f"Engaged with {len(gemini_objections)} Gemini and "
                f"{len(grok_objections)} Grok objection(s)."
            ),
            what_we_found=(
                "The council reviewed 10 strategies across equity, fixed income, "
                "risk, and quantitative dimensions. Two independent dissenters — "
                "Gemini and Grok — challenged the consensus before the final "
                "recommendation was made."
            ),
            why_it_matters=(
                "A multi-agent council is more robust than a single model — "
                "each specialist focuses on their domain and two non-Claude "
                "dissenters introduce genuinely different perspectives. The "
                "final recommendation has survived both internal review and "
                "external challenge from two different model providers."
            ),
            for_our_portfolio=(
                f"{len(significant)} strategies pass all five Tier 1 statistical "
                f"gates (p < 0.005 full-period, FDR, DSR, OOS, CV ≥ 0.60). "
                f"{'Recommended: ' + ', '.join(significant[:3]) if significant else 'No strategies pass all gates.'}"
            ),
            confidence=(
                "High confidence in the significance verdict — it is determined "
                "by statistical tests, not the LLM. The strategic narrative "
                "(which strategies to prioritise) is the CIO's judgement and "
                "should be read as an informed opinion, not a guarantee."
            ),
        )

    @staticmethod
    def _get_significant(strategy_results: dict[str, Any]) -> list[str]:
        """Returns strategy names that pass all Tier 1 gates."""
        return [
            name
            for name, r in strategy_results.items()
            if r.get("is_significant", False)
        ]
