"""
tools/chart_snapshots.py — server-side chart snapshots rendered on
every data-hash change and consumed by agents that reason visually.

Agents (council specialists, the academic writer, the academic-review
arbiter) read the snapshots as base64-encoded image blocks through
tools/chart_vision.py. By rendering on hash change and overwriting the
previous PNGs, the snapshots are always current — never stale, never
ahead of the data.

THIS MODULE OWNS:
  render_all_chart_snapshots() — render every key in AVAILABLE_CHARTS
    to CHART_SNAPSHOT_DIR as a PNG, plus a manifest.json that names
    the rendered files + the data hash they reflect.
  trigger_chart_snapshot_async() — fire the render in the background
    from the data pipeline's hash-change hooks.

WHEN IT RUNS:
  Same three hooks that fire trigger_audit_async("data_ingestion"):
    - full pipeline DB persist (_persist_to_db)
    - incremental daily update (check_and_run_incremental_update)
    - monthly auto-extension (extend_market_data)
  All three are no-op when the data hasn't changed (the underlying
  render_chart_png cache + the audit's idempotency check together
  prevent wasted work).

FAIL-OPEN BY DESIGN:
  - A single failing renderer is logged with its chart_key and the
    others continue; the run never aborts on one chart.
  - A directory-create / disk-write failure is logged and the
    pipeline keeps moving — agents that try to read missing snapshots
    fall back to a no-charts code path in chart_vision.
  - The trigger spawn is wrapped so a thread/loop failure never
    raises into the calling data pipeline.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import structlog

from config import CHART_SNAPSHOT_DIR

log = structlog.get_logger(__name__)

# Snapshot resolution — generous enough to stay sharp when an agent
# reasons about visual features (the 2022 break, drawdown depth), small
# enough that 17 charts plus base64 padding remain well under typical
# multimodal-input ceilings. The chart_render cache keys on these
# dimensions; matching them here means the second call is a cache hit.
SNAPSHOT_WIDTH = 800
SNAPSHOT_HEIGHT = 500

# Background-task registry — strong references so the loop's GC does
# not collect an in-flight render task before it completes. Mirrors the
# _audit_bg_tasks pattern in audit_engine.
_snapshot_bg_tasks: set[asyncio.Task] = set()


def _ensure_snapshot_dir() -> bool:
    """Best-effort mkdir for CHART_SNAPSHOT_DIR. Returns False on
    failure so callers can short-circuit rather than crash."""
    try:
        os.makedirs(CHART_SNAPSHOT_DIR, exist_ok=True)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("chart_snapshot_mkdir_failed",
                    path=CHART_SNAPSHOT_DIR, error=str(exc))
        return False


def _path_for(chart_key: str) -> str:
    """Stable on-disk path for a chart's PNG snapshot."""
    return os.path.join(CHART_SNAPSHOT_DIR, f"{chart_key}.png")


def _manifest_path() -> str:
    """Stable on-disk path for the snapshot manifest."""
    return os.path.join(CHART_SNAPSHOT_DIR, "manifest.json")


async def render_all_chart_snapshots() -> dict[str, Any]:
    """
    Render every key in AVAILABLE_CHARTS to CHART_SNAPSHOT_DIR and
    write the manifest. Returns a small summary dict (n_rendered,
    n_failed, hash_prefix) primarily for the log line and the tests.

    Each chart is rendered through the same render_chart_png() path
    the canvas editor uses, so the PNG bytes here are exactly what
    that endpoint serves — never a separate / drifted render path.
    """
    # Lazy imports — chart_render pulls in matplotlib via the deck +
    # extended renderers, which is heavy. Deferring the imports keeps
    # the module light at startup.
    from tools.chart_render import AVAILABLE_CHARTS, render_chart_png

    if not _ensure_snapshot_dir():
        return {"n_rendered": 0, "n_failed": 0, "hash_prefix": None,
                "rendered": []}

    # Best-effort hash for the manifest + log line. Falls back to an
    # empty string when the audit assembler is unavailable — the
    # snapshots still render either way.
    hash_value = ""
    try:
        from tools.audit_assembler import current_data_hash
        hash_value = await current_data_hash() or ""
    except Exception as exc:  # noqa: BLE001
        log.warning("chart_snapshot_hash_unavailable", error=str(exc))
    hash_prefix = (hash_value[:8] or "unknown") if hash_value else "unknown"

    rendered: list[dict[str, Any]] = []
    n_failed = 0
    started = time.time()

    for chart in AVAILABLE_CHARTS:
        key = chart["key"]
        try:
            png = await render_chart_png(
                key, "light", SNAPSHOT_WIDTH, SNAPSHOT_HEIGHT)
            path = _path_for(key)
            # atomic-ish write: write to .tmp then rename, so a partial
            # write cannot leave a half-PNG that the agent reader picks up.
            tmp = f"{path}.tmp"
            with open(tmp, "wb") as f:
                f.write(png)
            os.replace(tmp, path)
            size_kb = len(png) // 1024
            log.info("chart_snapshot_rendered",
                     chart_key=key, size_kb=size_kb)
            rendered.append({
                "key": key,
                "path": path,
                "size_kb": size_kb,
                "category": chart.get("category", "uncategorised"),
            })
        except Exception as exc:  # noqa: BLE001
            n_failed += 1
            log.warning("chart_snapshot_render_failed",
                        chart_key=key, error=str(exc))

    # Manifest — describes the snapshot directory's current contents.
    # Agents read this to confirm a snapshot exists before consuming.
    manifest = {
        "hash": hash_value,
        "rendered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "charts": rendered,
    }
    try:
        manifest_tmp = _manifest_path() + ".tmp"
        with open(manifest_tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, sort_keys=True)
        os.replace(manifest_tmp, _manifest_path())
    except Exception as exc:  # noqa: BLE001
        log.warning("chart_snapshot_manifest_write_failed", error=str(exc))

    elapsed = round(time.time() - started, 2)
    log.info("chart_snapshot_complete",
             n_rendered=len(rendered), n_failed=n_failed,
             hash_prefix=hash_prefix, elapsed_seconds=elapsed)

    return {
        "n_rendered": len(rendered),
        "n_failed": n_failed,
        "hash_prefix": hash_prefix,
        "rendered": rendered,
    }


def trigger_chart_snapshot_async() -> None:
    """
    Fire render_all_chart_snapshots() in the background — the
    snapshot-on-hash-change hook. Works whether or not the caller is
    on an event loop: on a loop (an async endpoint), it schedules a
    task; off a loop (the sync data pipeline), it runs in a daemon
    thread with its own loop. Mirrors trigger_audit_async() in
    audit_engine — same fail-open contract, never raises into the
    primary data pipeline.
    """
    import threading

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            task = loop.create_task(render_all_chart_snapshots())
            _snapshot_bg_tasks.add(task)
            task.add_done_callback(_snapshot_bg_tasks.discard)
        else:
            threading.Thread(
                target=lambda: asyncio.run(render_all_chart_snapshots()),
                daemon=True, name="chart-snapshot",
            ).start()
    except Exception as exc:  # noqa: BLE001
        log.warning("chart_snapshot_spawn_failed", error=str(exc))
