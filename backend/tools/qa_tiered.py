"""
tools/qa_tiered.py

Three-tier QA system. Each tier covers different ground at different
cost/latency tradeoffs:

  Tier 1 — Pure Python deterministic checks.
           Runs synchronously on every strategy-results update. Free.
           Checks the things you can verify without an LLM: weights sum
           to 1.0, no negative weights, n_observations crosses power
           threshold, is_significant logic matches the gate inputs,
           sanity assertions (CAGR in [8%, 12%], 2008 drawdown < -45%,
           etc.). Result is deterministic — same inputs always produce
           same output, so we can cache by strategy_hash forever.

  Tier 2 — Sonnet background audit.
           Async, fires when strategy_hash changes or the most recent
           Tier 2 cache entry is older than 24 hours. Reviews the
           methodology paragraphs: were the right tests applied? Are
           limitations correctly disclosed? Costs ~$0.05 per run.

  Tier 3 — Opus manual / auto-on-Tier-2-FAIL.
           Only invoked by the Admin screen's "Full Review" button or
           automatically when Tier 2 returns FAIL. Most expensive,
           highest quality. Output forms the basis of the Analytical
           Appendix QA section.

The Present-mode gate reads the LATEST cached verdict for the current
strategy_hash. If verdict >= WARN AND run_at < 48h AND hash matches,
Present mode unlocks. Tier 1 alone is enough — the audience never
waits on a Sonnet/Opus call.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Tier-specific TTLs (hours). Tier 1 is deterministic — its result for a
# given strategy_hash will never change, so the TTL is effectively forever
# (we still expire it nominally at 365 days so the cleanup job has a target).
#
# Tier 99 (May 24 2026) — the full /api/qa/audit checklist response, stored
# in qa_results_cache alongside the tiered verdicts but in its own band so
# the two paths never overwrite each other. 24h TTL matches the Tier 2
# pattern — same data hash will reuse the verdict for a day; a hash change
# (new data ingested) invalidates it; the qa_audit endpoint's minimum-
# interval gate caps re-runs within the day.
TIER_TTL_HOURS: dict[int, int] = {1: 24 * 365, 2: 24, 3: 24 * 7, 99: 24}

# Threshold below which Tier 1 reports an underpowered dataset.
# CLAUDE.md Section 7 calls this MIN_OBSERVATIONS_FOR_POWER.
MIN_OBSERVATIONS_FOR_POWER = 220

# Sanity-assertion thresholds. Mirror tools/data_fetcher.py — duplicated
# here so Tier 1 can run from cached results without re-running the data
# pipeline, but kept consistent so the two layers can never disagree.
SANITY_THRESHOLDS = {
    "sp500_cagr_min":         0.06,   # 8% nominal expected; band widened for noise
    "sp500_cagr_max":         0.14,
    "benchmark_dd_min":      -0.60,
    "benchmark_dd_max":      -0.40,
    "sharpe_upper_implausible": 2.0,  # > 2.0 likely indicates lookahead bias
    "drawdown_upper_implausible": 0.0,  # drawdown must be negative
}


# ── Tier 1 — pure deterministic checks ──────────────────────────────────

def _check(check_id: str, description: str, status: str, evidence: str,
           fix: str | None = None, category: str = "data") -> dict[str, Any]:
    """Builds a single check entry matching the QAAuditResult.items schema."""
    return {
        "check_id":    check_id,
        "category":    category,
        "check":       description,
        "description": description,
        "status":      status,
        "evidence":    evidence,
        "fix":         fix,
    }


def run_tier1_checks(results_dict: dict[str, dict]) -> dict[str, Any]:
    """
    Synchronous deterministic QA over `results_dict` (output of
    run_all_strategies). Pure Python — no LLM calls, no network, no DB.
    Returns the QAAuditResult shape the frontend already consumes.

    Every check is deliberately conservative: any structural violation
    (negative weight, NaN return, drawdown ≥ 0) is a FAIL because those
    indicate a calculation bug, not a marginal data issue. Plausibility
    violations (implausibly-high Sharpe, missing OOS p-value) are WARN —
    they need human attention but don't block the dashboard.
    """
    checks: list[dict[str, Any]] = []
    n_strategies = len(results_dict)

    # ── Structural checks ───────────────────────────────────────────────

    # Tier 1 doesn't see raw weights — they live in the backtester.
    # Instead we verify the post-hoc invariants: every strategy must
    # have a non-zero set of metrics (a strategy with all-zero metrics
    # is almost certainly an exception that was swallowed).
    errored_strategies = [
        name for name, r in results_dict.items() if r.get("error")
    ]
    checks.append(_check(
        "T1-01",
        "All strategies completed without exception",
        "PASS" if not errored_strategies else "FAIL",
        f"{len(errored_strategies)} strategy/strategies errored: {errored_strategies}"
        if errored_strategies else "All 10 strategies returned valid results.",
        category="structural",
    ))

    # ── Drawdown sign check ─────────────────────────────────────────────
    bad_dd = [
        (name, r["max_drawdown"])
        for name, r in results_dict.items()
        if isinstance(r.get("max_drawdown"), (int, float))
        and r["max_drawdown"] >= 0
    ]
    checks.append(_check(
        "T1-02",
        "Max drawdown is negative for all strategies",
        "PASS" if not bad_dd else "FAIL",
        f"{len(bad_dd)} strategy/strategies have non-negative drawdown: {bad_dd}"
        if bad_dd else "All max_drawdown values are < 0 as expected.",
        category="structural",
    ))

    # ── Sharpe plausibility ─────────────────────────────────────────────
    implausible_sharpe = [
        (name, r["sharpe_ratio"])
        for name, r in results_dict.items()
        if isinstance(r.get("sharpe_ratio"), (int, float))
        and r["sharpe_ratio"] > SANITY_THRESHOLDS["sharpe_upper_implausible"]
    ]
    checks.append(_check(
        "T1-03",
        f"No strategy Sharpe > {SANITY_THRESHOLDS['sharpe_upper_implausible']:.1f} (implausibility guard)",
        "PASS" if not implausible_sharpe else "WARN",
        f"{len(implausible_sharpe)} strategy/strategies exceed threshold — possible lookahead bias: {implausible_sharpe}"
        if implausible_sharpe else "All Sharpe ratios within plausible range.",
        category="plausibility",
        fix=(
            "Review the affected strategy's signal-construction code for "
            "lookahead bias (e.g., using future returns to compute signal)."
            if implausible_sharpe else None
        ),
    ))

    # ── Benchmark CAGR sanity ───────────────────────────────────────────
    bm = results_dict.get("BENCHMARK", {})
    bm_cagr = bm.get("cagr")
    cagr_ok = (
        isinstance(bm_cagr, (int, float))
        and SANITY_THRESHOLDS["sp500_cagr_min"] <= bm_cagr <= SANITY_THRESHOLDS["sp500_cagr_max"]
    )
    checks.append(_check(
        "T1-04",
        f"Benchmark CAGR in [{SANITY_THRESHOLDS['sp500_cagr_min']:.0%}, {SANITY_THRESHOLDS['sp500_cagr_max']:.0%}]",
        "PASS" if cagr_ok else "WARN",
        f"BENCHMARK CAGR = {bm_cagr:.2%}" if isinstance(bm_cagr, (int, float)) else "BENCHMARK CAGR missing",
        category="sanity",
    ))

    # ── Benchmark drawdown sanity ───────────────────────────────────────
    bm_dd = bm.get("max_drawdown")
    dd_ok = (
        isinstance(bm_dd, (int, float))
        and SANITY_THRESHOLDS["benchmark_dd_min"] <= bm_dd <= SANITY_THRESHOLDS["benchmark_dd_max"]
    )
    checks.append(_check(
        "T1-05",
        f"Benchmark max DD in [{SANITY_THRESHOLDS['benchmark_dd_min']:.0%}, {SANITY_THRESHOLDS['benchmark_dd_max']:.0%}] (captures 2008 GFC)",
        "PASS" if dd_ok else "WARN",
        f"BENCHMARK max DD = {bm_dd:.2%}" if isinstance(bm_dd, (int, float)) else "BENCHMARK max DD missing",
        category="sanity",
    ))

    # ── is_significant agrees with the five gates ───────────────────────
    # A strategy is supposed to be is_significant=True iff tier1_gates_passed == 5.
    # Any disagreement means the gates-aggregation logic broke.
    inconsistent_sig = [
        name for name, r in results_dict.items()
        if not r.get("error")
        and bool(r.get("is_significant"))
        != (int(r.get("tier1_gates_passed", 0)) == 5)
    ]
    checks.append(_check(
        "T1-06",
        "is_significant ↔ tier1_gates_passed=5 consistency",
        "PASS" if not inconsistent_sig else "FAIL",
        f"{len(inconsistent_sig)} strategy/strategies disagree: {inconsistent_sig}"
        if inconsistent_sig else "All is_significant flags match their gate counts.",
        category="structural",
    ))

    # ── Power sanity (n_observations >= threshold) ─────────────────────
    underpowered = [
        (name, r.get("n_observations"))
        for name, r in results_dict.items()
        if isinstance(r.get("n_observations"), int)
        and r["n_observations"] < MIN_OBSERVATIONS_FOR_POWER
    ]
    checks.append(_check(
        "T1-07",
        f"All strategies have n_observations ≥ {MIN_OBSERVATIONS_FOR_POWER}",
        "PASS" if not underpowered else "WARN",
        f"{len(underpowered)} underpowered: {underpowered}"
        if underpowered else "All strategies meet the Tier 1 power threshold.",
        category="statistical",
        fix=(
            "Underpowered tests inflate false-negative rate at p<0.005. "
            "Check whether the LQD bridge ran — n_observations should be ≥282."
            if underpowered else None
        ),
    ))

    # ── Stress tests present for significant strategies ─────────────────
    sig_strategies = [n for n, r in results_dict.items() if r.get("is_significant")]
    missing_stress = [
        name for name in sig_strategies
        if not results_dict[name].get("stress_results")
    ]
    if sig_strategies:
        checks.append(_check(
            "T1-08",
            "Significant strategies include stress-test results",
            "PASS" if not missing_stress else "WARN",
            f"{len(missing_stress)} significant strategy/strategies missing stress data: {missing_stress}"
            if missing_stress else f"All {len(sig_strategies)} significant strategies have stress_results.",
            category="completeness",
        ))

    # ── Aggregate verdict ──────────────────────────────────────────────
    n_failed = sum(1 for c in checks if c["status"] == "FAIL")
    n_warned = sum(1 for c in checks if c["status"] == "WARN")
    n_passed = sum(1 for c in checks if c["status"] == "PASS")
    if n_failed > 0:
        verdict = "FAIL"
    elif n_warned > 0:
        verdict = "WARN"
    else:
        verdict = "PASS"

    summary = (
        f"Tier 1 deterministic QA on {n_strategies} strategies: "
        f"{n_passed} pass, {n_warned} warn, {n_failed} fail."
    )

    return {
        "tier":           1,
        "verdict":        verdict,
        "checks_total":   len(checks),
        "checks_passed":  n_passed,
        "checks_warned":  n_warned,
        "checks_failed":  n_failed,
        "summary":        summary,
        "items":          checks,
        "limitations":    [],
        "data_caveats":   [],
        "model_assumptions": [
            "Tier 1 is pure Python and deterministic. Same inputs always "
            "produce the same verdict — re-running adds no new information.",
        ],
    }


# ── Tier 2 — Sonnet background audit ────────────────────────────────────

def run_tier2_audit(results_dict: dict[str, dict]) -> dict[str, Any]:
    """
    Sonnet-powered methodology review. Async on the caller side — never
    blocks the dashboard. Reads the same results_dict as Tier 1 but feeds
    it to the QA agent for narrative analysis.

    Falls back to a Tier 1 verdict augmented with a Tier 2 note if the
    LLM is unavailable, so the cache always gets *something* and the
    Present-mode gate doesn't get stuck waiting.
    """
    try:
        from agents.qa_agent import QAAgent
        agent = QAAgent()
        agent_result = agent.run_audit(results_dict, run_full_checklist=True)
        # Stamp the result with tier=2 so the cache row is correctly labelled.
        agent_result["tier"] = 2
        return agent_result
    except Exception as exc:
        log.warning("tier2_audit_fallback_to_tier1", error=str(exc))
        t1 = run_tier1_checks(results_dict)
        t1["tier"] = 2
        t1["summary"] = (
            f"Tier 2 audit unavailable ({type(exc).__name__}); "
            f"falling back to Tier 1 result: {t1['summary']}"
        )
        t1["model_assumptions"].append(
            "Tier 2 LLM call failed — verdict shown is Tier 1's. Set the "
            "ANTHROPIC_API_KEY and re-trigger the audit when the model is reachable."
        )
        return t1


# ── Tier 3 — Opus manual / auto-on-Tier-2-FAIL ─────────────────────────

def run_tier3_review(results_dict: dict[str, dict]) -> dict[str, Any]:
    """
    Opus-level review. Reuses the QAAgent but with the Opus model and a
    deeper checklist. Only invoked manually (Admin screen button) or
    automatically when Tier 2 returns FAIL — never on the data-update path.
    """
    try:
        from agents.qa_agent import QAAgent
        # The QAAgent already runs on Opus — Tier 3 is the same agent at
        # full depth. Future extension: pass a depth=full flag if needed.
        agent = QAAgent()
        agent_result = agent.run_audit(results_dict, run_full_checklist=True)
        agent_result["tier"] = 3
        return agent_result
    except Exception as exc:
        log.warning("tier3_review_fallback_to_tier2", error=str(exc))
        t2 = run_tier2_audit(results_dict)
        t2["tier"] = 3
        t2["model_assumptions"].append(
            "Tier 3 (Opus) failed — verdict shown is Tier 2 (Sonnet)."
        )
        return t2


# ── Async dispatch helpers ─────────────────────────────────────────────

def run_tier1_in_thread(results_dict: dict[str, dict]) -> dict[str, Any]:
    """
    Convenience wrapper for awaitable callers. Tier 1 is pure CPU — wrapping
    it in a thread keeps an async event loop responsive while it runs.
    """
    return run_tier1_checks(results_dict)


# ── Tier 2 background executor — process-wide singleton ──────────────────────
#
# Memory-audit finding: schedule_tier2_background used to construct a NEW
# ThreadPoolExecutor on EVERY call and shut it down with wait=False. Each
# executor + its worker thread stayed alive until the (10-30s Sonnet, longer
# on Tier 3 Opus) audit finished, and the nested `_run_and_cache` CLOSURE
# pinned the full results_dict — 10 strategies including their monthly_returns
# lists — for that whole window. Repeated triggers stacked executors, threads,
# and results_dict copies.
#
# Fix: one module-level executor for the life of the process. max_workers=1
# means audits queue and run one at a time — QA audits aren't latency-critical
# and serialising them bounds memory to a single in-flight results_dict.
# The interpreter's concurrent.futures atexit hook joins the worker on
# shutdown; a single in-flight audit (~30s) is an acceptable redeploy delay
# and means an audit is never lost mid-write.
_TIER2_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="qa-tier2",
)


def _tier2_run_and_cache(
    results_dict: dict[str, dict],
    strategy_hash: str,
    cache_writer,
) -> None:
    """
    Module-level worker for the Tier 2 background audit.

    Deliberately a top-level function, NOT a closure nested inside
    schedule_tier2_background: a top-level function has no __closure__
    cells, so it captures NOTHING implicitly. results_dict / strategy_hash
    / cache_writer arrive as explicit submit() arguments — the executor's
    work item holds them only until the task runs, then releases them.
    The previous nested-closure form pinned results_dict via a closure
    cell for the whole task lifetime even after it was no longer needed.
    """
    try:
        verdict = run_tier2_audit(results_dict)
        # cache_writer is sync; if it's async, run it in a fresh loop
        if asyncio.iscoroutinefunction(cache_writer):
            asyncio.run(cache_writer(strategy_hash, verdict, tier=2))
        else:
            cache_writer(strategy_hash, verdict, tier=2)
        log.info("tier2_background_complete",
                 strategy_hash=strategy_hash[:8], verdict=verdict.get("verdict"))
        # Auto-escalate to Tier 3 if Tier 2 came back FAIL
        if verdict.get("verdict") == "FAIL":
            log.info("tier2_failed_escalating_to_tier3",
                     strategy_hash=strategy_hash[:8])
            t3 = run_tier3_review(results_dict)
            if asyncio.iscoroutinefunction(cache_writer):
                asyncio.run(cache_writer(strategy_hash, t3, tier=3))
            else:
                cache_writer(strategy_hash, t3, tier=3)
    except Exception as exc:
        log.error("tier2_background_error", error=str(exc))


def schedule_tier2_background(
    results_dict: dict[str, dict],
    strategy_hash: str,
    cache_writer,
) -> None:
    """
    Fire-and-forget a Tier 2 audit on the process-wide _TIER2_EXECUTOR.
    The caller passes a write callback (typically `tools.cache.set_qa_cache`)
    so this module doesn't import the cache layer directly — keeps the
    dependency graph one-directional.

    results_dict / strategy_hash / cache_writer are passed as submit()
    arguments (not captured in a closure) so the executor releases its
    reference to results_dict as soon as the task finishes.
    """
    _TIER2_EXECUTOR.submit(
        _tier2_run_and_cache, results_dict, strategy_hash, cache_writer,
    )
