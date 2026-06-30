"""scripts/apply_header_edits.py -- June 29 2026.

Header cell (id=db6a4fe4) -- two text edits:
  1. "December 2025 freeze" -> "June 2026 freeze"
  2. Replace the dual-hash sentence with the operator's
     sharper version that calls out the independent
     integrity checks.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


NOTEBOOK = Path(__file__).resolve().parents[1] / "analytical_appendix.ipynb"
CELL_ID = "db6a4fe4"


OLD_DEC25 = "(December 2025 freeze;"
NEW_JUN26 = "(June 2026 freeze;"

OLD_DUAL_HASH = (
    "Both hashes identify the same dataset under two different "
    "schemes -- the cache key is `sha256(rows:last_date:n_strategies)`, "
    "the platform fingerprint hashes the source tables' row counts + "
    "max dates + last_updated timestamps."
)
NEW_DUAL_HASH = (
    "These hashes cover different inputs by design. The cache key "
    "`f2e87dec7dcabe71` is `sha256(n_rows:last_date:n_strategies)` "
    "from the strategy backtest run. The platform fingerprint "
    "`c421fb895347f924` hashes the source market data tables' row "
    "counts, max dates, and last_updated timestamps. They are "
    "independent integrity checks -- the cache key locks the "
    "backtester output; the platform fingerprint locks the upstream "
    "market data. See README.md for the dual-hash architecture."
)


def main() -> int:
    with open(NOTEBOOK, encoding="utf-8") as f:
        nb = json.load(f)

    for c in nb["cells"]:
        if c.get("id") != CELL_ID:
            continue
        src = "".join(c["source"])
        if OLD_DEC25 not in src:
            print("FATAL: 'December 2025' fragment not found")
            return 1
        if OLD_DUAL_HASH not in src:
            print("FATAL: dual-hash sentence not found")
            return 1
        src = src.replace(OLD_DEC25, NEW_JUN26)
        src = src.replace(OLD_DUAL_HASH, NEW_DUAL_HASH)
        c["source"] = [s + "\n" for s in src.splitlines()]
        if c["source"]:
            c["source"][-1] = c["source"][-1].rstrip("\n")
        print(f"PATCHED header markdown (id={CELL_ID})")
        break
    else:
        print(f"FATAL: cell {CELL_ID} not found")
        return 1

    with open(NOTEBOOK, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=1)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
