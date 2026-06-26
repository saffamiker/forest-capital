"""
scripts/backfill_chart_config.py

Backfill the chart_config block on every type='chart' element of
the current presentation_deck draft. Companion to the
feat(generation) commit that prepopulates chart_config at
deck-generation time -- this script gives EXISTING drafts the
same configs without requiring a full regeneration.

USAGE (Render shell, post-merge):

    python scripts/backfill_chart_config.py           # apply
    python scripts/backfill_chart_config.py --dry-run # inspect

Reads DATABASE_URL from the environment. Targets the current
presentation_deck draft via:

    document_type = 'presentation_deck'
    AND is_current = TRUE
    AND is_deleted = FALSE

No draft IDs hardcoded; idempotent.

WHAT GETS BACKFILLED

  For every type='chart' element on the draft:
    - chart_config: built via tools.chart_config_defaults
      .build_chart_config_for_key(chartKey, strategy_names) --
      same logic that prepopulates new decks at generation time.
      Series list pulled from the live strategy_results cache
      (or [] when the cache is cold).
    - If chart_config is ALREADY present on the element, it is
      LEFT ALONE. The script only fills in absent configs --
      it does NOT overwrite operator-edited values.

  Tables: the legacy markdown-pipe-in-body shape stays in place
  on existing drafts. Promoting an existing body-text element to
  a first-class type='table' element would require parsing the
  markdown back out + would risk losing the operator's prose
  context. New table elements only appear on freshly-generated
  decks; existing drafts keep their body markdown until the next
  regen.

IDEMPOTENCY

  Re-runs are no-ops: every chart element ends up with a
  chart_config; the second run sees them all and skips.

TRANSACTION SAFETY

  Single asyncpg transaction. --dry-run raises a sentinel
  AFTER the per-element transforms log so the transaction rolls
  back rather than commits.

EXIT CODES
  0  every element updated (or already-backfilled)
  1  unexpected error -- nothing committed
  2  the target draft was not found
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

# Allow `python scripts/backfill_chart_config.py` from the repo
# root (the Render shell working dir). We need backend/ on the
# path so tools.chart_config_defaults imports.
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

import asyncpg  # noqa: E402

from tools.chart_config_defaults import (  # noqa: E402
    build_chart_config_for_key,
    default_strategy_names_from_cache,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("backfill_chart_config")


def _normalise_dsn(raw: str) -> str:
    """asyncpg uses 'postgresql://' (no '+asyncpg' driver suffix)."""
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", raw)


async def _fetch_current_deck(
    conn: asyncpg.Connection,
) -> dict | None:
    row = await conn.fetchrow(
        "SELECT id, content_json, version "
        "FROM editor_drafts "
        "WHERE document_type = 'presentation_deck' "
        "AND is_current = TRUE AND is_deleted = FALSE "
        "LIMIT 1")
    if row is None:
        return None
    cj = row["content_json"]
    if isinstance(cj, str):
        cj = json.loads(cj)
    return {
        "id": row["id"], "content_json": cj,
        "version": row["version"],
    }


async def _fetch_strategy_names(
    conn: asyncpg.Connection,
) -> list[str]:
    """Pulls strategy ids from the live strategy_results cache.
    Returns [] when the cache is empty or unreachable -- the
    chart_config still gets prepopulated (just without a series
    list); the renderer's fallback handles the absent-series case."""
    try:
        rows = await conn.fetch(
            "SELECT strategy FROM strategy_results_cache "
            "ORDER BY strategy")
        return [str(r["strategy"]) for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "strategy_results_cache fetch failed: %s; "
            "proceeding with no series list.", exc)
        return []


async def _commit_draft(
    conn: asyncpg.Connection, draft_id: int,
    content_json: dict,
) -> int:
    new_version = await conn.fetchval(
        "UPDATE editor_drafts SET "
        "content_json = $2::jsonb, "
        "version = version + 1, updated_at = NOW() "
        "WHERE id = $1 RETURNING version",
        draft_id, json.dumps(content_json))
    return new_version


def backfill_slides(
    slides: list[dict[str, Any]],
    strategy_names: list[str],
) -> tuple[list[dict[str, Any]], int, int]:
    """For each type='chart' element across every slide, add
    chart_config if absent. Returns (mutated_slides, added,
    skipped) -- skipped counts elements that already carried a
    chart_config."""
    added = 0
    skipped = 0
    for slide in slides:
        for el in slide.get("elements", []):
            if not isinstance(el, dict):
                continue
            if el.get("type") != "chart":
                continue
            if el.get("chart_config"):
                skipped += 1
                continue
            chart_key = str(el.get("chartKey", ""))
            if not chart_key:
                continue
            el["chart_config"] = build_chart_config_for_key(
                chart_key, strategy_names)
            added += 1
    return slides, added, skipped


async def main(dry_run: bool) -> int:
    dsn_raw = os.environ.get("DATABASE_URL")
    if not dsn_raw:
        log.error(
            "DATABASE_URL is not set. Run on the Render shell "
            "(which exports it) or set it locally first.")
        return 1
    dsn = _normalise_dsn(dsn_raw)

    rc = 0
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            deck = await _fetch_current_deck(conn)
            if deck is None:
                log.error(
                    "No current presentation_deck draft found.")
                return 2
            log.info(
                "Deck draft id=%s version=%s",
                deck["id"], deck["version"])

            strategy_names = await _fetch_strategy_names(conn)
            log.info(
                "strategy_results_cache: %d strategies "
                "(%s)",
                len(strategy_names),
                ", ".join(strategy_names) if strategy_names
                else "empty")

            content = deepcopy(deck["content_json"]) or {}
            if not isinstance(content, dict):
                log.error("content_json is not a dict; aborting.")
                return 1
            slides = (
                content.get("slides", [])
                if isinstance(content, dict) else [])
            if not isinstance(slides, list):
                log.error("content_json.slides is not a list.")
                return 1

            slides, added, skipped = backfill_slides(
                slides, strategy_names)
            content["slides"] = slides
            log.info(
                "Backfill summary: %d chart elements gained a "
                "chart_config; %d already had one (skipped).",
                added, skipped)

            new_version = deck["version"] + 1
            if added == 0:
                log.info(
                    "Nothing to do -- every chart element "
                    "already has a chart_config. Exiting "
                    "without a version bump.")
            elif not dry_run:
                new_version = await _commit_draft(
                    conn, deck["id"], content)
                log.info(
                    "Committed: draft id=%s version %s -> %s",
                    deck["id"], deck["version"], new_version)

            if dry_run:
                raise _DryRunAbort
    except _DryRunAbort:
        log.info("Dry-run -- transaction rolled back.")
    finally:
        await conn.close()

    print("\n" + "=" * 60)
    print("BACKFILL SUMMARY")
    print("=" * 60)
    print(f"  Strategies in cache : {len(strategy_names)}")
    print(f"  Elements backfilled : {added}")
    print(f"  Elements skipped    : {skipped}")
    if dry_run:
        print("  Mode                : DRY-RUN (no DB writes)")
    elif added > 0:
        print(f"  New version         : {new_version}")
    else:
        print("  Mode                : applied (no-op)")
    print("=" * 60)
    return rc


class _DryRunAbort(Exception):
    """Sentinel to bail out of the transaction cleanly."""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Backfill chart_config on the current "
            "presentation_deck draft's chart elements."))
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Log every transform but roll back at the end. "
             "Useful for inspecting output before a real run.")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.dry_run)))
