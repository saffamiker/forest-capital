"""
tools/chart_vision.py — read-side companion to tools/chart_snapshots.

chart_snapshots renders every AVAILABLE_CHARTS key to disk on every
data-hash change. THIS module reads those PNGs back, base64-encodes
them, and returns Anthropic multimodal content blocks an agent can
reason about visually.

Three predefined chart sets scope which charts each agent surface
receives — see COUNCIL_CHARTS, ACADEMIC_REVIEW_CHARTS,
DOCUMENT_GENERATION_CHARTS below. Each set is deliberately small so
the per-call token budget stays predictable and the agent reads each
chart at full attention rather than skimming a wall of images.

FAIL-OPEN BY DESIGN:
  get_chart_image() returns None when the snapshot is missing — the
  caller skips that chart rather than failing.
  get_charts_for_context() returns an empty list when none of the
  requested keys have a snapshot — the caller (the agent's
  call_claude wrapper) then proceeds with text-only content, which
  is identical to the pre-vision code path. The first run after a
  cold deploy (before any snapshot has been rendered) hits this path.

EVALUATOR GUARD:
  This module's only callers are the GENERATOR call_claude paths in
  cio.py / equity_analyst.py / fixed_income_analyst.py /
  risk_manager.py / quant_backtester.py / academic_review.py /
  academic_writer.py / tools/academic_export.harness_narrative.
  The harness's _evaluate() must NOT pass visual_context to
  call_claude — the evaluator scores TEXT QUALITY and adding the
  charts as input would muddle that signal. The signature change in
  call_claude (FEATURE 1 Commit 3) defaults visual_context to None
  and evaluators never opt in.
"""
from __future__ import annotations

import base64
import os
from typing import Any

import structlog

from config import CHART_SNAPSHOT_DIR

log = structlog.get_logger(__name__)


# ── Predefined chart sets per agent surface ──────────────────────────────────
# Each tuple is deliberately small. The chart picker UI uses a categorised
# layout (regime / factors / performance / risk / significance / activity);
# these sets pick one or two charts per relevant category so the agent gets
# coverage without the token bloat of all 17.

# Council specialists — the four analysts + the CIO synthesis run on the
# performance/regime/factor narrative. Drawdown and significance can be
# inferred from context; the council does not need them visually.
COUNCIL_CHARTS: tuple[str, ...] = (
    "rolling_correlation",
    "cumulative_returns",
    "regime_signals",
    "regime_conditional_returns",
    "factor_loadings",
    "rolling_excess_return",
)

# Academic Review — the peer agents + the arbiter need to verify the
# document's claims against the visual evidence. Adds drawdown_periods
# and the significance journey so claims about strategy robustness can
# be cross-checked.
ACADEMIC_REVIEW_CHARTS: tuple[str, ...] = (
    "rolling_correlation",
    "cumulative_returns",
    "regime_signals",
    "factor_loadings",
    "drawdown_periods",
    "significance_journey",
    "oos_performance",
)

# ── Scope categories — drive the per-chart caption sentence ──────────────────
# Every renderer in tools/chart_renderers.py and tools/academic_deck.py picks
# a hardcoded data subset (a single strategy, the BENCHMARK only, every
# strategy in the cache, an asset-class view). The caption tells the agent
# which subset it is looking at so it does not have to infer the scope from
# the image alone. Three explicit buckets; charts not in any bucket fall
# back to the description-only caption.

# Default single strategy + BENCHMARK overlay — REGIME_SWITCHING (the
# project's highest-Sharpe non-benchmark strategy) with a fallback to the
# first non-BENCHMARK strategy in the cache. Matches the
# tools/chart_renderers._DEFAULT_STRATEGY constant.
_SINGLE_STRATEGY_CHARTS: frozenset[str] = frozenset({
    "drawdown_periods",
    "monthly_returns_heatmap",
    "rolling_sharpe",
    "return_distribution",
    "oos_performance",
})

# BENCHMARK series only — fall back to the first row when BENCHMARK is
# absent from the cache. Factor loadings render with the same default.
_FACTOR_CHARTS: frozenset[str] = frozenset({
    "factor_loadings",
    "factor_returns_attribution",
})

# Every strategy in strategy_results_cache, with no user-controllable
# subsetting — the legend-toggle UI on the Dashboard is frontend-only and
# has no server-side equivalent.
_ALL_STRATEGY_CHARTS: frozenset[str] = frozenset({
    "cumulative_returns",
    "rolling_excess_return",
    "risk_return",
    "significance_journey",
    "p_value_distribution",
})


# Document generation — the academic writer drafts midpoint paper,
# executive brief, and deck narrative sections. Same regime + factor
# core as the council; adds rolling_sharpe and drawdown_periods so the
# writer can reference the risk-adjusted-performance and tail-risk
# arcs explicitly when explaining strategy behaviour.
DOCUMENT_GENERATION_CHARTS: tuple[str, ...] = (
    "rolling_correlation",
    "cumulative_returns",
    "regime_signals",
    "regime_conditional_returns",
    "factor_loadings",
    "rolling_sharpe",
    "drawdown_periods",
)


def _snapshot_path(chart_key: str) -> str:
    """Stable on-disk path for a chart's PNG snapshot."""
    return os.path.join(CHART_SNAPSHOT_DIR, f"{chart_key}.png")


def get_chart_image(chart_key: str) -> str | None:
    """
    Reads /data/chart_snapshots/{chart_key}.png and returns its base64-
    encoded PNG bytes as a string. Returns None when the file is
    missing (no snapshot rendered yet) or unreadable — the caller
    then skips this chart.

    The bytes are read and encoded synchronously. PNG sizes at the
    snapshot resolution (800x500) are small (~30-60 KB raw, ~40-80 KB
    base64) so the read is fast and there is no benefit to async I/O.
    """
    path = _snapshot_path(chart_key)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
        return base64.b64encode(raw).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        log.warning("chart_vision_read_failed",
                    chart_key=chart_key, error=str(exc))
        return None


def _chart_descriptions() -> dict[str, str]:
    """
    Maps chart_key → human-readable description from AVAILABLE_CHARTS.
    Loaded lazily and cached after the first call so the registry lookup
    is O(1) for every subsequent caption.
    """
    global _DESCRIPTIONS_CACHE  # noqa: PLW0603
    if _DESCRIPTIONS_CACHE is not None:
        return _DESCRIPTIONS_CACHE
    try:
        from tools.chart_render import AVAILABLE_CHARTS
        _DESCRIPTIONS_CACHE = {
            c["key"]: c.get("description", "") for c in AVAILABLE_CHARTS
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("chart_vision_descriptions_failed", error=str(exc))
        _DESCRIPTIONS_CACHE = {}
    return _DESCRIPTIONS_CACHE


_DESCRIPTIONS_CACHE: dict[str, str] | None = None


def _scope_sentence(key: str, n_strategies: int | None) -> str:
    """
    Returns the per-chart scope sentence appended to the caption.

    The renderers in tools/chart_renderers.py and tools/academic_deck.py
    each pick a hardcoded data subset; the scope sentence names that
    subset so the agent knows exactly what is in the image without
    having to infer it from the picture. Three explicit buckets:

      single-strategy → "Showing REGIME_SWITCHING strategy vs BENCHMARK.
                         Full study period."
      factor          → "Showing market factor exposures. BENCHMARK
                         series only."
      all-strategy    → "Showing all {n} strategies. Full study period,
                         linear scale."

    Charts not in any bucket (rolling_correlation, regime_signals,
    regime_conditional_returns, team_activity) return an empty string —
    the description from AVAILABLE_CHARTS already names what they show.

    n_strategies is rendered into the all-strategy sentence when
    supplied. When None, the count is omitted and the sentence reads
    "Showing all strategies. …" — accurate but less precise.
    """
    if key in _SINGLE_STRATEGY_CHARTS:
        return ("Showing REGIME_SWITCHING strategy vs BENCHMARK. "
                "Full study period.")
    if key in _FACTOR_CHARTS:
        return "Showing market factor exposures. BENCHMARK series only."
    if key in _ALL_STRATEGY_CHARTS:
        count = f"all {n_strategies}" if n_strategies else "all"
        return f"Showing {count} strategies. Full study period, linear scale."
    return ""


def get_charts_for_context(
    chart_keys: list[str] | tuple[str, ...],
    n_strategies: int | None = None,
) -> list[dict[str, Any]]:
    """
    Builds the Anthropic content-block list for a set of chart keys.

    For every key whose snapshot exists, the returned list contains
    TWO blocks in order:
      1. An image block carrying the PNG as base64.
      2. A text block captioning the image with the chart key, the
         description from AVAILABLE_CHARTS, and a SCOPE SENTENCE
         naming the exact data subset the renderer chose (single
         strategy vs BENCHMARK, BENCHMARK only, all N strategies).

    The scope sentence is the value-add over a simple description —
    the agent knows which strategies and which date range are in the
    image rather than inferring from the visual. See _scope_sentence
    for the per-chart-category rules.

    n_strategies — caller-supplied count to render into the
    all-strategy scope sentence (e.g. "Showing all 10 strategies").
    Optional; when None the sentence omits the count. The four
    council specialists, the CIO, and the academic-export
    harness_narrative all have strategy_results in scope and pass
    len(strategy_results); academic_review threads it through from
    the analytics snapshot.

    Missing snapshots are skipped silently — the result simply omits
    them. When NONE of the requested keys have a snapshot the returned
    list is empty; the caller's prompt then proceeds text-only (the
    pre-vision behaviour, identical to before this feature shipped).

    Spread the result into a content array before the text prompt:

      messages = [{
          "role": "user",
          "content": [
              *get_charts_for_context(COUNCIL_CHARTS, n_strategies=10),
              {"type": "text", "text": existing_prompt},
          ],
      }]

    The caller is responsible for skipping the spread entirely when
    visual context is not appropriate for the call (every evaluator
    call, the explainer agent, the document-assistant chat, etc.).
    """
    if not chart_keys:
        return []

    descriptions = _chart_descriptions()
    blocks: list[dict[str, Any]] = []
    missing: list[str] = []

    for key in chart_keys:
        b64 = get_chart_image(key)
        if b64 is None:
            missing.append(key)
            continue
        blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": b64,
            },
        })
        desc = descriptions.get(key, "").strip()
        scope = _scope_sentence(key, n_strategies)
        # Caption shape: header "Chart: {key} — {desc}" (description
        # falls back to just "Chart: {key}" when absent), then the
        # scope sentence appended on the same line so the model reads
        # both together. A space separator keeps the two parts
        # visually connected; the scope sentence already ends with a
        # period.
        header = f"Chart: {key} — {desc}" if desc else f"Chart: {key}"
        caption = f"{header} {scope}".rstrip() if scope else header
        blocks.append({"type": "text", "text": caption})

    if not blocks:
        log.warning(
            "chart_vision_no_snapshots_available",
            requested=list(chart_keys),
            note="proceeding without visual context",
        )
    elif missing:
        log.info(
            "chart_vision_partial",
            rendered=len(blocks) // 2, missing=missing,
        )

    return blocks


def snapshots_dir_exists() -> bool:
    """True when CHART_SNAPSHOT_DIR is present on disk. Probe used by
    tests + the cold-deploy log line in the call_claude wrapper."""
    return os.path.isdir(CHART_SNAPSHOT_DIR)
