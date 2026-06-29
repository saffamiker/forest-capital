"""scripts/heal_draft_88_manifest_hash.py -- June 29 2026.

One-time migration for draft 88: replace stale data_hash
(d0b1339e06845559) with the active freeze hash
(c421fb895347f924) in every value_manifest entry, reset
migration_run so the auto-upgrade walker re-runs, then trigger
_auto_upgrade_draft_to_token_values to re-stamp every
token_value node with the corrected hash.

Run on the Render shell:
    cd backend
    python scripts/heal_draft_88_manifest_hash.py

Idempotent. Safe to re-run; if the manifest already points
at the freeze hash, the UPDATE matches zero rows and the
upgrade hook is a no-op (migration_run stays True after the
first successful run).

Operator note: this is a one-off heal for the specific
draft-88 incident; the manifest writer fix in main.py:
_manifest_data_hash() ensures future generations write the
freeze hash directly so this script should not be needed for
subsequent drafts.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys


_DRAFT_ID = 88
_STALE_HASH = "d0b1339e06845559"
_FREEZE_HASH = "c421fb895347f924"


async def main() -> int:
    # Make the backend package importable when running this
    # script directly (Render shell cwd is backend/).
    here = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.dirname(here)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    try:
        from database import AsyncSessionLocal
        from sqlalchemy import text
    except Exception as exc:
        print(f"FATAL: could not import database layer: {exc}")
        return 2

    if AsyncSessionLocal is None:
        print("FATAL: DATABASE_URL not configured -- run on Render shell")
        return 2

    async with AsyncSessionLocal() as s:
        # 1. Read current draft state.
        row = await s.execute(text(
            "SELECT id, document_type, data_hash, value_manifest, "
            "       migration_run, content_json IS NOT NULL "
            "FROM editor_drafts "
            "WHERE id = :i AND is_deleted = false"
        ), {"i": _DRAFT_ID})
        r = row.fetchone()
        if not r:
            print(f"FATAL: draft {_DRAFT_ID} not found")
            return 1
        (
            draft_id, doc_type, draft_hash, manifest,
            migration_run, has_content,
        ) = r
        print(
            f"Draft {draft_id} ({doc_type}): "
            f"draft.data_hash={draft_hash[:16] if draft_hash else None}, "
            f"migration_run={migration_run}, "
            f"has_content_json={has_content}")
        if not manifest:
            print("INFO: no value_manifest -- nothing to update")
            return 0

        # 2. Update manifest entries in place.
        entries_updated = 0
        new_manifest = {}
        for value, entry in manifest.items():
            if (isinstance(entry, dict)
                    and entry.get("data_hash") == _STALE_HASH):
                new_entry = dict(entry)
                new_entry["data_hash"] = _FREEZE_HASH
                new_manifest[value] = new_entry
                entries_updated += 1
            else:
                new_manifest[value] = entry
        print(
            f"Manifest entries: {len(manifest)} total, "
            f"{entries_updated} updated stale->freeze hash")

        if entries_updated == 0:
            print(
                "INFO: no stale-hash entries to update -- manifest "
                "already healed")
        else:
            await s.execute(text(
                "UPDATE editor_drafts "
                "SET value_manifest = CAST(:m AS JSONB), "
                "    migration_run  = false, "
                "    updated_at     = NOW() "
                "WHERE id = :i"
            ), {
                "m": json.dumps(new_manifest),
                "i": _DRAFT_ID,
            })
            await s.commit()
            print(
                f"OK: persisted updated manifest + reset "
                f"migration_run=False for draft {_DRAFT_ID}")

    # 3. Re-trigger the auto-upgrade walker so token_value nodes
    #    pick up the corrected hashes.
    print(
        "\nTriggering _auto_upgrade_draft_to_token_values "
        "for draft 88...")
    try:
        # Import the hook from main.py. The function reads the
        # draft + manifest + content_json fresh, so the updated
        # manifest above is what it sees.
        from main import _auto_upgrade_draft_to_token_values
        await _auto_upgrade_draft_to_token_values(
            _DRAFT_ID, doc_type)
        print("OK: auto-upgrade walker completed")
    except Exception as exc:
        print(
            f"WARNING: auto-upgrade walker raised: {exc}\n"
            "Draft manifest IS updated; run admin endpoint "
            "POST /api/v1/admin/upgrade-all-drafts-to-token-"
            "values to retry the walker.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
