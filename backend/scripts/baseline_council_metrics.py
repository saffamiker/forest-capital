"""scripts/baseline_council_metrics.py — capture pre-classifier baseline.

Runs the existing FULL-CONTEXT council path against five fixed test
questions (one per bundle type) and writes the resulting metric row
for each as `question_type='baseline_full'`. These rows are the
before-state for the per-bundle cost-reduction comparison the
/admin/council-metrics endpoint surfaces.

Run after migration 050 lands, BEFORE the classifier-driven path
goes to production-load. One run is enough — the rows are append-
only and the aggregate uses AVG(), so a second run just adds more
samples to the baseline mean.

USAGE
  Render shell:    python -m scripts.baseline_council_metrics
  Local CLI:       cd backend && python -m scripts.baseline_council_metrics

  --dry-run        Run the questions through the SCORE/EXTRACT path
                   but don't write to council_query_metrics. Useful
                   for sanity-checking the question set on a local
                   DB without polluting it.

EXIT CODE
  0 on success (all five questions ran and wrote a row, or
    --dry-run completed cleanly)
  1 on any unrecoverable failure (no DB / no model / one question
    raised — message in stderr names the failing step)

The script is intentionally STANDALONE — no FastAPI imports, no
asyncio fan-out, no streaming. It re-runs the same path
/api/council/query runs (CIO.deliberate → full live_context →
synthesis), captures the same metric fields, and writes the row
directly with the FULL-CONTEXT label. The classifier is bypassed:
this is the BASELINE so it must use the pre-classifier path.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

# Make the backend package importable when run as a script.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import structlog

log = structlog.get_logger(__name__)


# The five baseline questions — one per bundle type. The wording is
# deliberately blunt so the classifier WOULD recognise each one
# unambiguously (REGIME hits "regime", RECOMMENDATION hits
# "recommend", etc.). The baseline capture forces FULL context
# anyway; the classifier-friendly wording exists so that comparing
# baseline rows to live rows is apples-to-apples on the same
# question intent.
_BASELINE_QUESTIONS = [
    ("REGIME",
     "What is the current market regime and how confident are you?"),
    ("RECOMMENDATION",
     "What allocation does the council recommend given current conditions?"),
    ("RISK",
     "What is the downside risk profile of the current portfolio?"),
    ("STATISTICAL",
     "Is the portfolio's outperformance statistically significant?"),
    ("FORWARD",
     "What is the 6-month forward outlook for the blend?"),
]


async def _run_one(question: str, *, dry_run: bool) -> dict:
    """Drive one baseline question end-to-end. Returns a dict
    describing what landed."""
    # Force the same path the production endpoint uses, but with
    # the classifier disabled — feed page-style full context, label
    # the metric row "baseline_full".
    from agents.cio import CIO
    from agents.usage import collect_usage, start_usage_capture
    from tools.backtester import run_all_strategies
    from tools.council_live_context import recommendation_context
    from tools.data_fetcher import get_full_history

    start_usage_capture()
    history = await asyncio.to_thread(get_full_history)
    strategies = await asyncio.to_thread(run_all_strategies, history)

    # Baseline uses the FULL recommendation-context bundle (the
    # widest of the three PR #229 scopes) — that's the pre-classifier
    # context.
    live_context = await recommendation_context()

    cio = CIO()
    final = await asyncio.to_thread(
        cio.deliberate, question, strategies, history,
        live_context=live_context)
    usage = collect_usage()
    bundle_size = (
        len(json.dumps(live_context, default=str))
        if live_context else 0)

    if dry_run:
        return {
            "question": question,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "context_bundle_size": bundle_size,
            "dry_run": True,
            "recommendation_preview":
                (final.get("final_recommendation") or "")[:200],
        }

    # Write the council_query_metrics row directly — bypass the API
    # endpoint so the script stays standalone. June 3 2026: mirror the
    # live event-stream extraction so baseline rows carry the per-CIO
    # input-token total (migration 052). Without this thread-through
    # cio_input_tokens defaults to None in the writer and every baseline
    # row writes NULL, leaving the like-for-like comparison aggregate
    # unable to compute a baseline mean.
    per_agent = usage.get("per_agent") or {}
    cio_input = (
        per_agent.get("cio", {}).get("input_tokens")
        if isinstance(per_agent.get("cio"), dict)
        else None)
    from main import _write_council_query_metric
    from tools.council_question_bundles import QUESTION_TYPE_BASELINE_FULL
    await _write_council_query_metric(
        question_type=QUESTION_TYPE_BASELINE_FULL,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        cio_input_tokens=cio_input,
        context_bundle_size=bundle_size,
        synthesis_text=final.get("final_recommendation", "") or "",
    )
    return {
        "question": question,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cio_input_tokens": cio_input,
        "context_bundle_size": bundle_size,
        "recommendation_preview":
            (final.get("final_recommendation") or "")[:200],
    }


async def _main_async(dry_run: bool) -> int:
    results: list[dict] = []
    for label, question in _BASELINE_QUESTIONS:
        log.info("baseline_question_starting", label=label,
                 question=question)
        try:
            r = await _run_one(question, dry_run=dry_run)
            log.info("baseline_question_complete", label=label,
                     **{k: v for k, v in r.items() if k != "question"})
            results.append({"label": label, **r})
        except Exception as exc:  # noqa: BLE001
            log.error("baseline_question_failed", label=label,
                      error=str(exc))
            print(f"\n[baseline] FAILED on {label}: {exc}",
                  file=sys.stderr)
            return 1
    print("\n=== Baseline capture complete ===")
    for r in results:
        print(f"  {r['label']:<14} "
              f"input={r.get('input_tokens')}  "
              f"output={r.get('output_tokens')}  "
              f"bundle={r.get('context_bundle_size')} chars")
    if dry_run:
        print("\n(--dry-run: no rows written to council_query_metrics)")
    else:
        print("\nWritten to council_query_metrics with question_type="
              "'baseline_full'.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true",
                   help="Run the five questions but don't write rows.")
    args = p.parse_args()
    return asyncio.run(_main_async(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
