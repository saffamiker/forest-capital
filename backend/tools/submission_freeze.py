"""tools/submission_freeze.py -- Layer 4 of the deterministic
substitution architecture.

Locks document generators to a frozen `data_hash` for the FNA 670
final submission. The live platform (CIO card, regime detector,
daily digest, Investment Outlook) continues reading the live hash;
only the three document generators -- brief, deck, appendix --
route through `get_effective_data_hash`.

# SUBMISSION FREEZE -- OPERATIONAL SEQUENCE
#
# JUNE 30, 2026 (submission day):
#   1. Generate final brief, deck, appendix
#   2. Run verify-all check:
#        curl -s -X POST -H "X-API-Key: $MASTER_API_KEY" \
#          https://forest-capital.onrender.com/api/v1/export/verify-all
#   3. Confirm all three pass verification
#   4. Activate freeze:
#        curl -s -X POST -H "X-API-Key: $MASTER_API_KEY" \
#          -H "Content-Type: application/json" \
#          -d '{"active": true, "freeze_hash": "c421fb895347f924"}' \
#          https://forest-capital.onrender.com/api/v1/admin/submission-freeze
#   5. Export final DOCX/PPTX files
#   6. Submit to Dr. Panttser
#
# JULY 1, 2026 (presentation day):
#   Platform is frozen. Live demo shows same figures as submitted
#   documents (the freeze is one day old; data_hash is stable).
#   No operational steps needed on July 1.
#
# To check status:
#   curl -s -H "X-API-Key: $MASTER_API_KEY" \
#     https://forest-capital.onrender.com/api/v1/admin/submission-status
#
# To deactivate after presentations:
#   curl -s -X POST -H "X-API-Key: $MASTER_API_KEY" \
#     -H "Content-Type: application/json" \
#     -d '{"active": false}' \
#     https://forest-capital.onrender.com/api/v1/admin/submission-freeze

# TODO(Layer 3b): when the verification receipt page ships (a
# follow-up PR on substitution-layer3b-charts-receipt-button-badges),
# wire the freeze status into the receipt header so a downloaded
# document's verification banner reads:
#   "Submission freeze: Active (June 30, 2026) / Not active"
# and when active:
#   "This document was generated under submission freeze <hash>,
#    activated <date> for the FNA 670 final submission deadline."
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog

log = structlog.get_logger(__name__)

FREEZE_CONFIG_KEY = "submission_freeze"

_DEFAULT_FREEZE_CONFIG: dict[str, Any] = {
    "active": False,
    "freeze_hash": None,
    "freeze_date": None,
    "activated_by": None,
    "activated_at": None,
}

_DB_AVAILABLE = False
try:  # pragma: no cover - environment dependent
    from database import AsyncSessionLocal
    _DB_AVAILABLE = AsyncSessionLocal is not None
except Exception:  # noqa: BLE001
    AsyncSessionLocal = None  # type: ignore[assignment]


def _default_config() -> dict[str, Any]:
    """A fresh copy of the OFF-state freeze config -- never share the
    module-level dict, callers mutate their result."""
    return dict(_DEFAULT_FREEZE_CONFIG)


async def get_freeze_config() -> dict[str, Any]:
    """Reads the current freeze config from platform_config.

    Returns the OFF-state default ({"active": False,
    "freeze_hash": None, ...}) when:
      - The DB is unreachable (test env, cold deploy)
      - The platform_config table does not exist yet
      - The submission_freeze row was never seeded
      - Any read error fires
    Fail-open is the conservative choice: an unreadable freeze flag
    is treated as OFF, so document generation falls through to the
    live hash rather than silently locking on a stale value.
    """
    if not _DB_AVAILABLE:
        return _default_config()
    try:
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            row = await session.execute(
                text("SELECT value FROM platform_config WHERE key = :k"),
                {"k": FREEZE_CONFIG_KEY},
            )
            r = row.fetchone()
            if not r or r[0] is None:
                return _default_config()
            value = r[0]
            # Postgres JSONB comes back as a parsed dict via SQLAlchemy.
            # Defensive: if a driver returns a JSON string, parse it.
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except Exception:  # noqa: BLE001
                    return _default_config()
            if not isinstance(value, dict):
                return _default_config()
            # Merge over defaults so missing keys never trip a caller
            # that did `config["freeze_hash"]` without a .get().
            merged = _default_config()
            merged.update(value)
            return merged
    except Exception as exc:  # noqa: BLE001
        log.warning("submission_freeze_read_failed", error=str(exc))
        return _default_config()


async def set_freeze_config(
    *,
    active: bool,
    freeze_hash: str | None = None,
    activated_by: str | None = None,
) -> dict[str, Any]:
    """UPSERT the platform_config row.

    When active=True: records freeze_hash, freeze_date (today, UTC),
    activated_by, activated_at. freeze_hash is required when
    activating (the caller -- the admin endpoint -- validates it
    exists in strategy_results_cache before calling us).

    When active=False: clears freeze_hash, freeze_date, activated_by,
    activated_at -- a deactivated freeze is fully clean so the next
    activation starts from a known state.

    Returns the new config dict (the same shape get_freeze_config
    returns). Raises RuntimeError if the database is unreachable --
    set is the one path that must NOT fail-open: an admin click that
    silently no-ops would be worse than a 500.
    """
    if not _DB_AVAILABLE:
        raise RuntimeError(
            "submission_freeze unavailable: database not configured")

    now = datetime.now(timezone.utc)
    if active:
        if not freeze_hash:
            raise ValueError(
                "freeze_hash is required when activating the freeze")
        payload: dict[str, Any] = {
            "active": True,
            "freeze_hash": freeze_hash,
            "freeze_date": now.date().isoformat(),
            "activated_by": activated_by,
            "activated_at": now.isoformat(),
        }
    else:
        payload = {
            "active": False,
            "freeze_hash": None,
            "freeze_date": None,
            "activated_by": None,
            "activated_at": None,
        }

    try:
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            await session.execute(
                text(
                    "INSERT INTO platform_config (key, value, updated_at) "
                    "VALUES (:k, CAST(:v AS JSONB), :ts) "
                    "ON CONFLICT (key) DO UPDATE SET "
                    " value = EXCLUDED.value, "
                    " updated_at = EXCLUDED.updated_at"
                ),
                {"k": FREEZE_CONFIG_KEY,
                 "v": json.dumps(payload),
                 "ts": now},
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.error("submission_freeze_write_failed", error=str(exc))
        raise

    log.info(
        "submission_freeze_updated",
        active=active,
        freeze_hash=(freeze_hash or "")[:8] if freeze_hash else None,
        activated_by=activated_by,
    )

    # June 27 2026 -- on freeze activation, snapshot the current
    # regime_signals_cache row into the hash-keyed snapshot table so
    # document generators under freeze can pull the SAME live
    # signals that were current at activation time. Pass the
    # current signals explicitly (read once, snapshot once) rather
    # than letting the snapshot helper re-read inside its own
    # transaction. Without the snapshot, deck/brief/appendix exports
    # under freeze fall back to live_signals=None (watchpoint tokens
    # render em-dash). Best-effort -- a snapshot write failure logs
    # but does not roll back the freeze activation. The snapshot
    # write is idempotent (ON CONFLICT DO NOTHING) so a re-activate
    # of the same hash preserves the original capture.
    if active and freeze_hash:
        try:
            from tools.cache import (
                snapshot_regime_signals_for_hash, get_regime_cache,
            )
            current_signals = await get_regime_cache()
            await snapshot_regime_signals_for_hash(
                freeze_hash, current_signals)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "submission_freeze_regime_snapshot_failed",
                freeze_hash=freeze_hash[:8],
                error=str(exc))

    return payload


async def get_effective_data_hash(live_hash: str) -> str:
    """The single call site that enforces the freeze.

    Returns `freeze_hash` when the freeze is active, else `live_hash`.
    Called ONLY by the three document generators (brief, deck,
    appendix) in main.py -- the live platform reads use
    `current_data_hash()` directly.

    Fail-open semantics: any read error returns `live_hash`, so a
    transient DB blip during generation does not lock the document
    against the wrong hash. The submission day flow runs a
    `submission-status` GET right before submission, which surfaces
    any hash drift so the operator catches a mis-fired freeze before
    it matters.
    """
    try:
        config = await get_freeze_config()
        if config.get("active") and config.get("freeze_hash"):
            frozen = str(config["freeze_hash"])
            log.info(
                "submission_freeze_applied",
                live_hash=(live_hash or "")[:8],
                freeze_hash=frozen[:8],
                drift=frozen != live_hash,
            )
            return frozen
        return live_hash
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "submission_freeze_resolution_failed",
            error=str(exc),
            falling_back_to="live_hash",
        )
        return live_hash
