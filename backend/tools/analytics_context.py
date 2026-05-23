"""tools/analytics_context.py — narrative analytics context block.

Item 5 (May 23 2026 — analytics context injection, narrative layer).

The existing diversification_context + strategy_context layers
inject STRUCTURED facts into agent prompts: pre/post-2022 correlation
shift, tail-risk leaders, capture leader, top-3 strategies by Sharpe.

This module is the NARRATIVE complement — a single paragraph that
synthesises the project's headline analytical findings into the
voice an agent should adopt when answering questions about them.
Where the structured layer answers "what are the numbers?", the
narrative layer answers "what is the story behind the numbers?".

Both layers ship as agent prompt context; both are tagged with a
generated_at timestamp the frontend surfaces as a freshness badge.

USAGE PATTERN — mirror of tools/macro_context.py and tools/
diversification_context.py:

  get_analytics_context()        sync accessor returning the
                                  cached narrative block
  get_analytics_freshness()      sync accessor returning the
                                  generated_at ISO timestamp the
                                  cache reflects (None when cold)
  inject_analytics_context(p)    appends to a system prompt; no-op
                                  on a cold cache so every agent
                                  calls unconditionally
  refresh_analytics_context()    async; rebuilds the narrative
                                  from the latest strategy_results_
                                  cache + macro digest. Called by
                                  the same hooks that fire the
                                  diversification refresh, so the
                                  three context layers stay in step.

THE NARRATIVE — five sentences, in order:

  1. The headline finding: the 2022 equity-bond correlation regime
     shift (computed pre / post averages with the actual flip
     direction and magnitude).
  2. The strategic implication: dynamic vs static allocation now
     matters where it didn't pre-2022.
  3. The leader: the top-Sharpe strategy and the size of its edge
     over the 100%-equity benchmark.
  4. The honest caveat: the 0 / 10 strict-significance result and
     why that is methodological honesty rather than failure.
  5. The current macro frame: a one-line read of where the latest
     macro digest puts the regime (only if a current macro context
     is available; otherwise omitted).

The fifth sentence is the only one that consumes the macro digest
— the rest reason against the analytics cache alone. So the
narrative is useful even when the macro layer is cold.

FAIL-OPEN — a missing strategy_results_cache, a missing macro
digest, even a database outage, all degrade silently to an
empty cache and a no-op injection. The agent runs text-only,
identical to the pre-Item-5 path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog


log = structlog.get_logger(__name__)


# Module-level cache, same shape pattern as the two sibling
# context modules. `generated_at` is None until the first
# successful refresh.
_CACHE: dict[str, Any] = {
    "text":         "",
    "generated_at": None,
}


# ── Builders ─────────────────────────────────────────────────────────────────


def _fmt_pct(x: float | None, decimals: int = 1) -> str:
    if x is None:
        return "N/A"
    return f"{x:.{decimals}f}%"


def _fmt_corr(x: float | None) -> str:
    if x is None:
        return "N/A"
    return f"{x:+.2f}"


def _build_narrative(
    strategies: list[dict[str, Any]] | None,
    correlation_metric: dict[str, Any] | None,
    macro_in_use: bool,
) -> str:
    """Renders the narrative paragraph from the cached payloads.
    Returns empty string when neither strategies nor correlation
    data is available (the narrative would be hollow)."""
    if not strategies and not correlation_metric:
        return ""

    sentences: list[str] = []

    # 1. Headline — the 2022 correlation regime shift.
    if correlation_metric:
        pre = correlation_metric.get("pre_2022_avg") \
            or correlation_metric.get("pre_2022_average")
        post = correlation_metric.get("post_2022_avg") \
            or correlation_metric.get("post_2022_average")
        if pre is not None and post is not None:
            flip = "inverted" if (pre < 0 and post > 0) else "shifted"
            sentences.append(
                f"The project's headline finding is the 2022 "
                f"equity-IG correlation regime shift: pre-2022 the "
                f"pair averaged {_fmt_corr(pre)}, post-2022 it "
                f"{flip} to {_fmt_corr(post)}.")
            # 2. Strategic implication.
            sentences.append(
                "That shift breaks the foundational assumption "
                "behind static 60/40 — bonds are no longer the "
                "reliable counter-weight to equity. Dynamic, "
                "regime-aware allocation now matters where it "
                "barely did before 2022.")

    # 3. Leader (top-Sharpe non-benchmark strategy).
    if strategies:
        non_bm = [s for s in strategies if (s.get("strategy_name") or "")
                   .upper() != "BENCHMARK"]
        non_bm_sorted = sorted(
            non_bm,
            key=lambda s: float(s.get("sharpe_ratio") or 0),
            reverse=True)
        bm = next((s for s in strategies if (s.get("strategy_name") or "")
                    .upper() == "BENCHMARK"), None)
        if non_bm_sorted and bm:
            top = non_bm_sorted[0]
            top_sharpe = float(top.get("sharpe_ratio") or 0)
            bm_sharpe = float(bm.get("sharpe_ratio") or 0)
            edge_bps = (top_sharpe - bm_sharpe) * 100  # decimals -> bps proxy
            sentences.append(
                f"The strongest performer is "
                f"{top.get('strategy_name')} (Sharpe "
                f"{top_sharpe:.2f}) vs the 100%-equity benchmark "
                f"(Sharpe {bm_sharpe:.2f}) — an edge of about "
                f"{edge_bps:+.0f} bps in risk-adjusted terms.")

    # 4. Honest caveat — the 0/10 result.
    sentences.append(
        "No strategy clears the strict Benjamin-et-al (2018) bar "
        "(p < 0.005 after FDR correction across ten strategies). "
        "That is methodological honesty — three strategies show "
        "economically meaningful outperformance, and the recommendation "
        "rests on economic significance, not formal statistical "
        "significance.")

    # 5. Macro frame — only if a macro digest is actually present.
    if macro_in_use:
        sentences.append(
            "The current macro regime read in the prior block frames "
            "the next-quarter outlook for the regime-aware strategies.")

    return " ".join(sentences)


def _wrap_in_block(narrative: str, generated_at: str) -> str:
    """Renders the narrative paragraph as a labelled context block
    that agents can spot in their prompts."""
    if not narrative:
        return ""
    lines: list[str] = [
        "",
        "",
        f"=== ANALYTICAL NARRATIVE (as of {generated_at}) ===",
        narrative,
        "",
        "Reason from this narrative when an answer benefits from "
        "the project's analytical story arc. Cite a specific "
        "number from the structured blocks above when you do — "
        "this narrative is the framing, not the source of truth.",
    ]
    return "\n".join(lines)


# ── Accessors ────────────────────────────────────────────────────────────────


def get_analytics_context() -> str:
    """Sync accessor — returns the cached narrative block. Empty
    string until refresh_analytics_context has populated the cache
    so inject_analytics_context becomes a no-op on a cold deploy."""
    return _CACHE["text"]


def get_analytics_freshness() -> str | None:
    """Sync accessor — returns the ISO timestamp the cached
    narrative was generated at, or None on a cold cache. The
    /api/v1/context/freshness endpoint reads this; the frontend
    surfaces it as a freshness badge so the user knows how current
    the agent prompts are."""
    return _CACHE["generated_at"]


def inject_analytics_context(system_prompt: str) -> str:
    """Append the analytics narrative to a system prompt. A no-op
    on a cold cache so every agent calls unconditionally."""
    ctx = get_analytics_context()
    return system_prompt + ctx if ctx else system_prompt


async def refresh_analytics_context() -> None:
    """Re-reads the latest strategy_results_cache + correlation
    metric + macro digest, then rebuilds the narrative cache. Called
    from:
      - precomputed_analytics.refresh_diversification_metrics tail
        (so the three context layers refresh in step)
      - research_engine.run_research tail (so a fresh macro digest
        re-paints sentence 5 within one tick)
      - the lifespan startup hook (cold-deploy warm read)

    Fail-open: any database / read error leaves the previous cache
    in place. A persistently broken refresh is visible in the logs."""
    try:
        # 1. Latest strategy_results_cache for the leader sentence.
        #    get_latest_strategy_cache() returns the raw run_all_
        #    strategies() output — a dict[str, dict] keyed by strategy
        #    name (each value carries sharpe_ratio, cagr, etc.). Flatten
        #    it to a list of dicts with strategy_name injected so the
        #    builder can sort by Sharpe and pick the leader.
        strategies: list[dict[str, Any]] = []
        try:
            from tools.cache import get_latest_strategy_cache
            cache_row = await get_latest_strategy_cache()
            if cache_row and isinstance(cache_row, dict):
                # Support both flat dict[str, dict] and an explicit
                # {"strategies": [...]} wrapper just in case the cache
                # format evolves under us.
                if "strategies" in cache_row \
                        and isinstance(cache_row["strategies"], list):
                    strategies = cache_row["strategies"]
                else:
                    for name, metrics in cache_row.items():
                        if isinstance(metrics, dict):
                            strategies.append(
                                {**metrics, "strategy_name": name})
        except Exception as exc:  # noqa: BLE001
            log.warning("analytics_context_strategy_read_failed",
                        error=str(exc))

        # 2. Latest correlation metric for the headline sentence.
        correlation_metric: dict[str, Any] | None = None
        try:
            from tools.precomputed_analytics import get_latest_metric
            correlation_metric = await get_latest_metric(
                "rolling_correlation")
        except Exception as exc:  # noqa: BLE001
            log.warning("analytics_context_corr_read_failed",
                        error=str(exc))

        # 3. Is the macro layer currently injecting anything? If yes
        # we include sentence 5; otherwise we omit it.
        macro_in_use = False
        try:
            from tools.macro_context import get_macro_context
            macro_in_use = bool(get_macro_context())
        except Exception:  # noqa: BLE001
            macro_in_use = False

        narrative = _build_narrative(
            strategies, correlation_metric, macro_in_use)
        generated_at = (datetime.now(timezone.utc)
                          .isoformat(timespec="seconds"))
        block = _wrap_in_block(narrative, generated_at)
        if block:
            _CACHE["text"] = block
            _CACHE["generated_at"] = generated_at
            log.info("analytics_context_refreshed",
                     length=len(block),
                     n_strategies=len(strategies),
                     macro_in_use=macro_in_use)
        else:
            # An empty narrative (no strategies AND no correlation
            # data) — leave the previous cache in place rather than
            # blanking out a previously-good narrative.
            log.info("analytics_context_refresh_empty",
                     n_strategies=len(strategies),
                     macro_in_use=macro_in_use)
    except Exception as exc:  # noqa: BLE001
        log.warning("analytics_context_refresh_failed", error=str(exc))


def _set_cache_for_test(text: str, generated_at: str | None = None) -> None:
    """Testing hook — write a known narrative + freshness pair into
    the cache without monkeypatching every reader."""
    _CACHE["text"] = text
    _CACHE["generated_at"] = generated_at
