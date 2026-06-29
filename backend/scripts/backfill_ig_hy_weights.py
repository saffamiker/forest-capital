"""backend/scripts/backfill_ig_hy_weights.py -- one-shot June 2026.

Backfills avg_ig_weight / avg_hy_weight onto every existing
strategy_results_cache row. The PR that ships the new fields emits
them on FRESH cache writes (from run_all_strategies via the warm
path or /api/backtest/compare). Old rows lack the keys; this script
walks every per-strategy entry in results_json, reads the persisted
weight_schedule, and computes the per-strategy mean(ig) / mean(hy)
across the schedule's rebalance dates.

USAGE (manual, on Render shell):

    cd backend
    python -m scripts.backfill_ig_hy_weights

Idempotent: a row whose results_json already carries avg_ig_weight /
avg_hy_weight for every strategy is left untouched. Run it again at
any point -- it always converges to the same state.

CONTRACT:

  - Reads from strategy_results_cache.
  - Writes back to results_json (JSONB UPDATE).
  - Never deletes rows.
  - Never modifies non-weight fields.
  - Logs a summary at the end: rows seen / rows updated / strategies
    backfilled / strategies skipped.

The script ALSO preserves the avg_bond_weight back-compat alias --
existing readers that consume avg_bond_weight continue to work, and
the new ig + hy fields are added alongside (NOT in place of).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mean(values: list[float]) -> float:
    """Plain arithmetic mean -- no numpy dependency at script time so
    this runs in the slim shell container without an import dance."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def _compute_ig_hy_from_schedule(
    weight_schedule: list[dict[str, Any]],
) -> tuple[float, float]:
    """Walk the weight_schedule (list of {date, weights: {equity, ig,
    hy}}) and compute mean(ig), mean(hy) across rebalance dates.
    Missing ig/hy keys on any row default to 0.0 -- the upstream
    backtester always emits both keys (even for IG-only strategies
    that carry hy=0.0), so a missing key indicates a malformed row
    we round to zero rather than skipping the whole entry."""
    igs: list[float] = []
    hys: list[float] = []
    for row in weight_schedule or []:
        if not isinstance(row, dict):
            continue
        w = row.get("weights")
        if not isinstance(w, dict):
            continue
        try:
            igs.append(float(w.get("ig", 0.0) or 0.0))
            hys.append(float(w.get("hy", 0.0) or 0.0))
        except (TypeError, ValueError):
            continue
    return round(_mean(igs), 4), round(_mean(hys), 4)


def _backfill_one_entry(entry: Any) -> tuple[bool, str]:
    """Mutate ONE per-strategy entry in place. Returns (updated, reason).
    Reasons: "already_present", "no_schedule", "ok", "skip_non_dict"."""
    if not isinstance(entry, dict):
        return False, "skip_non_dict"
    if (entry.get("avg_ig_weight") is not None
            and entry.get("avg_hy_weight") is not None):
        return False, "already_present"
    schedule = entry.get("weight_schedule")
    if not isinstance(schedule, list) or not schedule:
        # No schedule means we can't derive the split. Set both to
        # 0.0 so downstream readers don't see None and crash on the
        # back-compat invariant check, but log this case so a manual
        # audit can spot the unusual rows.
        entry["avg_ig_weight"] = 0.0
        entry["avg_hy_weight"] = entry.get("avg_bond_weight", 0.0)
        return True, "no_schedule"
    ig, hy = _compute_ig_hy_from_schedule(schedule)
    entry["avg_ig_weight"] = ig
    entry["avg_hy_weight"] = hy
    return True, "ok"


async def _run() -> None:
    """The async entry point. Reads every row from
    strategy_results_cache, mutates results_json in place where
    needed, and writes back when at least one entry was updated."""
    from sqlalchemy import text
    from database import AsyncSessionLocal

    if AsyncSessionLocal is None:
        print("Database unavailable -- AsyncSessionLocal is None.")
        return

    rows_seen = 0
    rows_updated = 0
    strategies_updated = 0
    strategies_already_ok = 0
    strategies_no_schedule = 0

    async with AsyncSessionLocal() as session:
        rows = await session.execute(text(
            "SELECT id, results_json FROM strategy_results_cache "
            "ORDER BY id ASC"))
        cache_rows = list(rows.fetchall())
        for row_id, results_json in cache_rows:
            rows_seen += 1
            if not isinstance(results_json, dict):
                # JSONB always deserialises to a dict; defensive.
                continue
            row_changed = False
            for strategy_name, entry in results_json.items():
                updated, reason = _backfill_one_entry(entry)
                if updated:
                    row_changed = True
                    if reason == "ok":
                        strategies_updated += 1
                    elif reason == "no_schedule":
                        strategies_no_schedule += 1
                elif reason == "already_present":
                    strategies_already_ok += 1
            if row_changed:
                rows_updated += 1
                await session.execute(
                    text(
                        "UPDATE strategy_results_cache "
                        "SET results_json = CAST(:rj AS JSONB) "
                        "WHERE id = :id"),
                    {"rj": json.dumps(results_json), "id": row_id},
                )
        await session.commit()

    print("─" * 60)
    print(f"strategy_results_cache rows seen:    {rows_seen}")
    print(f"strategy_results_cache rows updated: {rows_updated}")
    print(f"per-strategy entries backfilled:     {strategies_updated}")
    print(
        f"per-strategy entries already current: {strategies_already_ok}")
    print(
        f"per-strategy entries lacking schedule: "
        f"{strategies_no_schedule} "
        f"(set ig=0, hy=avg_bond as fallback)")
    print("─" * 60)


if __name__ == "__main__":
    os.environ.setdefault("ENVIRONMENT", "production")
    asyncio.run(_run())
