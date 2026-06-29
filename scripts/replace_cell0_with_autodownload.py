"""scripts/replace_cell0_with_autodownload.py -- June 29 2026.

Replace the Colab setup cell (id=colab_setup_2026_06_29) with the
operator's richer auto-download variant. The new cell tries the
local notebook_data/ folder first, then falls back to downloading
each file directly from GitHub raw (the notebook-data-export
branch) -- so a Colab user just opens the notebook + clicks
Runtime > Run All, no manual upload step.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


NOTEBOOK = Path(__file__).resolve().parents[1] / "analytical_appendix.ipynb"
CELL_ID = "colab_setup_2026_06_29"


CELL_SOURCE = '''# ============================================================
# Forest Capital -- FNA 670 Analytical Appendix
# Regime-Conditional Asset Allocation | McColl School of Business
# ============================================================
#
# HOW TO RUN:
#   Google Colab: Open this notebook via the shared link and
#   click Runtime > Run All. Data files download automatically.
#
#   Local: Clone the repo and run jupyter notebook.
#   notebook_data/ is already present in the repo.
#
# Data hash: c421fb895347f924 (strategy cache freeze)
# Live hash:  d0b1339e06845559 (market data fingerprint)
# See README.md for dual-hash architecture explanation.
# ============================================================
import os
from pathlib import Path

GITHUB_BASE = (
    "https://raw.githubusercontent.com/saffamiker/forest-capital"
    "/notebook-data-export/notebook_data"
)
DATA_FILES = [
    "monthly_returns.csv",
    "ff_factors.csv",
    "rebalance_events.csv",
    "strategy_results.json",
    "blend_oos_monthly_returns.csv",
    "regime_classification.csv",
    "oos_summary.json",
    "README.md",
]

DATA_DIR = Path("notebook_data")
if DATA_DIR.exists() and all(
        (DATA_DIR / f).exists() for f in DATA_FILES):
    print(
        f"notebook_data/ found locally -- loading from "
        f"{DATA_DIR.resolve()}")
else:
    print("notebook_data/ not found -- downloading from GitHub...")
    import urllib.request
    DATA_DIR.mkdir(exist_ok=True)
    for f in DATA_FILES:
        url = f"{GITHUB_BASE}/{f}"
        dest = DATA_DIR / f
        urllib.request.urlretrieve(url, dest)
        print(f"  [OK] {f}")
    print("All files ready.")

# Verify
present = [f for f in DATA_FILES if (DATA_DIR / f).exists()]
missing = [f for f in DATA_FILES if not (DATA_DIR / f).exists()]
print(f"\\nFiles present: {len(present)}/{len(DATA_FILES)}")
if missing:
    print(f"WARNING -- missing: {missing}")
else:
    print("All data files verified. Ready to run.")'''


def main() -> int:
    with open(NOTEBOOK, encoding="utf-8") as f:
        nb = json.load(f)

    for c in nb["cells"]:
        if c.get("id") != CELL_ID:
            continue
        c["source"] = [s + "\n" for s in CELL_SOURCE.splitlines()]
        if c["source"]:
            c["source"][-1] = c["source"][-1].rstrip("\n")
        c["outputs"] = []
        c["execution_count"] = None
        print(f"REPLACED Cell 0 (id={CELL_ID}) with auto-download variant")
        break
    else:
        print(f"FATAL: cell {CELL_ID} not found")
        return 1

    with open(NOTEBOOK, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=1)
        f.write("\n")
    print("OK: notebook saved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
