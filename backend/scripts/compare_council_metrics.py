"""scripts/compare_council_metrics.py — A/B baseline vs typed bundle comparison.

For each of five fixed test questions, run the council TWICE:
  1. BASELINE — full live_context (recommendation_context() — the wide
     pre-classifier scope), row written with question_type='baseline_full'.
  2. TYPED    — classify_question() picks a bundle, that bundle's
     resolver runs, row written with question_type=<classified type>.

Both writes carry cio_input_tokens populated (PR #266 / migration 052).
After all 10 deliberations land, prints a single comparison table.

This is the production counterpart to baseline_council_metrics.py — the
baseline script captures the BEFORE state; this script captures
BEFORE + AFTER in one run and prints the reduction signal directly.

USAGE
  cd backend && python -m scripts.compare_council_metrics --dry-run
                  # rehearse, no writes, no LLM calls (uses mocked-out
                  # deliberation if --dry-run is set; the comparison
                  # table prints zeros for everything).

  cd backend && python -m scripts.compare_council_metrics --confirm
                  # real run, 10 council deliberations,
                  # ~$10-30 in API spend.

EXIT CODE
  0 on success.
  1 on missing --confirm or --dry-run, or any unrecoverable failure.

The script reuses the structure and patterns from
baseline_council_metrics.py — the bundle classifier dispatch and the
in-script direction/alignment scoring are the only new pieces.
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


# Same five questions as baseline_council_metrics.py — one per bundle
# type. Copied verbatim so the BASELINE rows this script writes line up
# with the rows the baseline script writes (both label them
# question_type='baseline_full' so the dashboard's AVG aggregates pool
# them correctly).
_QUESTIONS = [
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


async def _run_one(label: str, question: str) -> dict:
    """Drive ONE question through BOTH paths (baseline + typed) and
    return a row of comparison data for the final table.

    Both deliberations share the same get_full_history() and
    run_all_strategies() outputs — those are pure data preparation and
    don't depend on the bundle choice. Running them once per question
    halves the prep time compared to two independent invocations.
    """
    from agents.cio import CIO
    from agents.usage import collect_usage, start_usage_capture
    from tools.backtester import run_all_strategies
    from tools.council_direction_extractor import (
        alignment_score, extract_direction,
    )
    from tools.council_live_context import recommendation_context
    from tools.council_question_bundles import (
        QUESTION_TYPE_FULL, classify_question, resolve_bundle,
    )
    from tools.data_fetcher import get_full_history

    history = await asyncio.to_thread(get_full_history)
    strategies = await asyncio.to_thread(run_all_strategies, history)

    cio = CIO()

    # ── Run 1 — BASELINE (full live_context) ──────────────────────────
    start_usage_capture()
    baseline_ctx = await recommendation_context()
    baseline = await asyncio.to_thread(
        cio.deliberate, question, strategies, history,
        live_context=baseline_ctx)
    baseline_usage = collect_usage()
    baseline_bundle_size = (
        len(json.dumps(baseline_ctx, default=str))
        if baseline_ctx else 0)
    baseline_cio_in = _per_agent_cio_input(baseline_usage)

    # ── Run 2 — TYPED (classifier-routed bundle) ──────────────────────
    # The dispatch mirrors main.py's live endpoint exactly: classify,
    # try to resolve, fall back to the wide context labeled 'full' on
    # a None classification or an empty bundle.
    start_usage_capture()
    classified = classify_question(question)
    typed_ctx = None
    if classified is not None:
        bundle = await resolve_bundle(classified)
        if bundle:
            typed_ctx = bundle
        else:
            log.info("compare_bundle_empty_fallback",
                     label=label, classified=classified)
            classified = None
    if typed_ctx is None:
        typed_ctx = await recommendation_context()
        question_type_typed = QUESTION_TYPE_FULL
    else:
        question_type_typed = classified
    typed = await asyncio.to_thread(
        cio.deliberate, question, strategies, history,
        live_context=typed_ctx)
    typed_usage = collect_usage()
    typed_bundle_size = (
        len(json.dumps(typed_ctx, default=str)) if typed_ctx else 0)
    typed_cio_in = _per_agent_cio_input(typed_usage)

    # ── Alignment score — in-script via the direction extractor ───────
    # The typed-path synthesis is the one a live request would produce,
    # so the alignment we report is the typed-path alignment. (The
    # baseline alignment is computable too; reporting one keeps the
    # table at the spec'd 7 columns.)
    typed_text = typed.get("final_recommendation") or ""
    direction = extract_direction(typed_text)
    hmm_state, hmm_conf = _hmm_state_from_context(typed_ctx)
    alignment = alignment_score(direction, hmm_state, hmm_conf)

    return {
        "label":               label,
        "question":            question,
        "baseline_input":      baseline_usage.get("input_tokens"),
        "typed_input":         typed_usage.get("input_tokens"),
        "baseline_cio_input":  baseline_cio_in,
        "typed_cio_input":     typed_cio_in,
        "baseline_bundle":     baseline_bundle_size,
        "typed_bundle":        typed_bundle_size,
        "typed_question_type": question_type_typed,
        "direction":           direction,
        "hmm_state":           hmm_state,
        "hmm_confidence":      hmm_conf,
        "alignment":           alignment,
        "baseline_recommendation_preview":
            (baseline.get("final_recommendation") or "")[:160],
        "typed_recommendation_preview":
            typed_text[:160],
        # The full usage dicts ride along so the row writer's per-CIO
        # value comes from the same source as the column above.
        "_baseline_usage":     baseline_usage,
        "_typed_usage":        typed_usage,
        "_baseline_text":      baseline.get("final_recommendation") or "",
        "_typed_text":         typed_text,
    }


def _per_agent_cio_input(usage: dict) -> int | None:
    """Mirror the live-endpoint extraction (main.py:5387) so the
    metric the row writer stamps matches the metric the comparison
    table prints. Returns None when the 'cio' label never tagged a
    record_usage() call — e.g. an LLM outage that fell through to a
    mock response before tag_agent fired."""
    per_agent = usage.get("per_agent") or {}
    cio = per_agent.get("cio")
    if isinstance(cio, dict):
        return cio.get("input_tokens")
    return None


def _hmm_state_from_context(ctx: dict | None) -> tuple[str | None, float | None]:
    """Pull the HMM state + confidence out of a live_context dict.

    The regime bundle nests it at ctx['regime']['hmm_state'] /
    ['hmm_confidence']; the recommendation_context() bundle nests it at
    ctx['hmm']['state'] / ['confidence']. Both shapes are tried so this
    works on the typed AND baseline contexts.

    Returns (None, None) when nothing matches — alignment_score handles
    a missing state by returning the balanced * confidence path.
    """
    if not isinstance(ctx, dict):
        return None, None
    regime = ctx.get("regime") if isinstance(ctx.get("regime"), dict) else None
    if regime:
        return regime.get("hmm_state"), regime.get("hmm_confidence")
    hmm = ctx.get("hmm") if isinstance(ctx.get("hmm"), dict) else None
    if hmm:
        return hmm.get("state"), hmm.get("confidence")
    return None, None


async def _write_rows(row: dict) -> None:
    """Write BOTH the baseline_full row and the typed row for this
    question to council_query_metrics. Mirrors the live endpoint's
    _write_council_query_metric path so the columns line up exactly."""
    from main import _write_council_query_metric
    from tools.council_question_bundles import QUESTION_TYPE_BASELINE_FULL

    await _write_council_query_metric(
        question_type=QUESTION_TYPE_BASELINE_FULL,
        input_tokens=row["baseline_input"],
        output_tokens=row["_baseline_usage"].get("output_tokens"),
        cio_input_tokens=row["baseline_cio_input"],
        context_bundle_size=row["baseline_bundle"],
        synthesis_text=row["_baseline_text"],
    )
    await _write_council_query_metric(
        question_type=row["typed_question_type"],
        input_tokens=row["typed_input"],
        output_tokens=row["_typed_usage"].get("output_tokens"),
        cio_input_tokens=row["typed_cio_input"],
        context_bundle_size=row["typed_bundle"],
        synthesis_text=row["_typed_text"],
    )


# ── Output table ──────────────────────────────────────────────────────


def _fmt_int(v) -> str:
    if v is None:
        return "  --"
    try:
        return f"{int(v):>9,}"
    except (TypeError, ValueError):
        return "  --"


def _fmt_pct_reduction(baseline, typed) -> str:
    """Negative percentage = reduction (typed < baseline). Positive =
    typed grew (unexpected; surfaced for debug). '--' on missing data."""
    try:
        b, t = int(baseline), int(typed)
    except (TypeError, ValueError):
        return "   --"
    if b <= 0:
        return "   --"
    pct = (t - b) / b * 100.0
    return f"{pct:>+6.1f}%"


def _fmt_float(v) -> str:
    if v is None:
        return "  --"
    try:
        return f"{float(v):>5.2f}"
    except (TypeError, ValueError):
        return "  --"


def _print_table(rows: list[dict]) -> None:
    """Render the 8-column comparison table:
      Question | Baseline in | Typed in | CIO baseline | CIO typed |
      Bundle red. | CIO red. | Alignment

    8 columns instead of the spec'd 7 — the user accepted the extra
    CIO-reduction column in the design review."""
    header = (
        f"{'Question':<14} | "
        f"{'Baseline in':>11} | "
        f"{'Typed in':>9} | "
        f"{'CIO base':>9} | "
        f"{'CIO typed':>9} | "
        f"{'Bundle red':>10} | "
        f"{'CIO red':>8} | "
        f"{'Align':>5}"
    )
    sep = "-" * len(header)
    print()
    print(header)
    print(sep)
    for r in rows:
        line = (
            f"{r['label']:<14} | "
            f"{_fmt_int(r['baseline_input'])} | "
            f"{_fmt_int(r['typed_input'])} | "
            f"{_fmt_int(r['baseline_cio_input'])} | "
            f"{_fmt_int(r['typed_cio_input'])} | "
            f"{_fmt_pct_reduction(r['baseline_input'], r['typed_input']):>10} | "
            f"{_fmt_pct_reduction(r['baseline_cio_input'], r['typed_cio_input']):>8} | "
            f"{_fmt_float(r['alignment']):>5}"
        )
        print(line)
    print(sep)


# ── Main ──────────────────────────────────────────────────────────────


async def _main_async(dry_run: bool, confirm: bool) -> int:
    if not dry_run and not confirm:
        print(
            "[compare] Refusing to run without --confirm or --dry-run.\n"
            "  --dry-run rehearses the path without writing rows or "
            "burning API credits.\n"
            "  --confirm acknowledges that this will make 10 council "
            "deliberations (~$10-30 in API spend) and write 10 rows to "
            "council_query_metrics.",
            file=sys.stderr,
        )
        return 1

    rows: list[dict] = []
    for label, question in _QUESTIONS:
        log.info("compare_question_starting", label=label,
                 question=question, dry_run=dry_run)
        if dry_run:
            # Skip the real run path entirely — return a zero row so
            # the table printer renders without LLM cost.
            rows.append({
                "label":               label,
                "question":            question,
                "baseline_input":      None,
                "typed_input":         None,
                "baseline_cio_input":  None,
                "typed_cio_input":     None,
                "baseline_bundle":     0,
                "typed_bundle":        0,
                "typed_question_type": "(dry-run)",
                "direction":           "balanced",
                "hmm_state":           None,
                "hmm_confidence":      None,
                "alignment":           None,
                "baseline_recommendation_preview": "(dry-run)",
                "typed_recommendation_preview":    "(dry-run)",
                "_baseline_usage":     {},
                "_typed_usage":        {},
                "_baseline_text":      "",
                "_typed_text":         "",
            })
            continue

        try:
            r = await _run_one(label, question)
            log.info(
                "compare_question_complete",
                label=label,
                baseline_in=r["baseline_input"],
                typed_in=r["typed_input"],
                baseline_cio=r["baseline_cio_input"],
                typed_cio=r["typed_cio_input"],
                typed_type=r["typed_question_type"],
                alignment=r["alignment"],
            )
            await _write_rows(r)
            rows.append(r)
        except Exception as exc:  # noqa: BLE001
            log.error("compare_question_failed", label=label,
                      error=str(exc))
            print(f"\n[compare] FAILED on {label}: {exc}",
                  file=sys.stderr)
            return 1

    print("\n=== Comparison complete ===")
    _print_table(rows)
    if dry_run:
        print("\n(--dry-run: no rows written, no LLM calls made — "
              "values are placeholders.)")
    else:
        print("\nWritten 10 rows to council_query_metrics (5 "
              "'baseline_full' + 5 typed). Re-run after a deploy to "
              "extend the sample.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true",
                   help="Rehearse the five questions without writing "
                        "rows or making any LLM calls. Prints the "
                        "table with placeholder values so the "
                        "structure is verified locally.")
    p.add_argument("--confirm", action="store_true",
                   help="Acknowledges the ~$10-30 API spend for 10 "
                        "council deliberations. Required for a real "
                        "run; without it the script exits 1.")
    args = p.parse_args()
    return asyncio.run(_main_async(args.dry_run, args.confirm))


if __name__ == "__main__":
    sys.exit(main())
