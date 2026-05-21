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


def get_charts_for_context(chart_keys: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
    """
    Builds the Anthropic content-block list for a set of chart keys.

    For every key whose snapshot exists, the returned list contains
    TWO blocks in order:
      1. An image block carrying the PNG as base64.
      2. A text block captioning the image with the chart key and the
         description from AVAILABLE_CHARTS so the model can name the
         chart back when reasoning about it.

    Missing snapshots are skipped silently — the result simply omits
    them. When NONE of the requested keys have a snapshot the returned
    list is empty; the caller's prompt then proceeds text-only (the
    pre-vision behaviour, identical to before this feature shipped).

    Spread the result into a content array before the text prompt:

      messages = [{
          "role": "user",
          "content": [
              *get_charts_for_context(COUNCIL_CHARTS),
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
        caption = (f"Chart: {key} — {desc}"
                   if desc else f"Chart: {key}")
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
