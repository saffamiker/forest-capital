"""scripts/replace_cell0_final_spec.py -- June 29 2026.

Final Cell 0 per the operator's exact spec:
  * `from pathlib import Path` first
  * `urllib.request` + `os` imports up top (not lazy)
  * `print(f"  ✓ {f}")` per file with unicode check
  * raises FileNotFoundError on a missing file
  * prints DATA_DIR.resolve() + "Files verified: N/N" + "Ready to run."

Also strips the redundant `DATA_DIR = Path('notebook_data')` from
Cell 3 (the manifest) so the Cell 0 declaration carries through
all subsequent cells unaltered.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


NOTEBOOK = Path(__file__).resolve().parents[1] / "analytical_appendix.ipynb"
CELL0_ID = "colab_setup_2026_06_29"
MANIFEST_ID = "801d9305"


CELL0_SOURCE = '''# ============================================================
# Forest Capital -- FNA 670 Analytical Appendix
# Regime-Conditional Asset Allocation | McColl School of Business
# ============================================================
#
# HOW TO RUN:
#   Google Colab: Open this notebook and click Runtime > Run all.
#   Data files download automatically from GitHub.
#
#   Local: Clone the repo and run jupyter notebook.
#   notebook_data/ is already present.
#
# Data hash: c421fb895347f924 (strategy cache freeze)
# Live hash:  d0b1339e06845559 (market data fingerprint)
# ============================================================

from pathlib import Path
import urllib.request
import os

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

# Set DATA_DIR -- used by ALL subsequent cells
DATA_DIR = Path("notebook_data")

if DATA_DIR.exists() and all((DATA_DIR / f).exists() for f in DATA_FILES):
    print(f"notebook_data/ found locally -- {DATA_DIR.resolve()}")
else:
    print("Downloading data files from GitHub...")
    DATA_DIR.mkdir(exist_ok=True)
    for f in DATA_FILES:
        url = f"{GITHUB_BASE}/{f}"
        dest = DATA_DIR / f
        urllib.request.urlretrieve(url, dest)
        print(f"  ✓ {f}")
    print("Download complete.")

# Verify all files present
missing = [f for f in DATA_FILES if not (DATA_DIR / f).exists()]
if missing:
    raise FileNotFoundError(f"Missing files: {missing}")

print(f"\\nDATA_DIR = {DATA_DIR.resolve()}")
print(f"Files verified: {len(DATA_FILES)}/{len(DATA_FILES)}")
print("Ready to run.")'''


def main() -> int:
    with open(NOTEBOOK, encoding="utf-8") as f:
        nb = json.load(f)

    found_cell0 = False
    found_manifest = False
    for c in nb["cells"]:
        if c.get("id") == CELL0_ID:
            c["source"] = [
                s + "\n" for s in CELL0_SOURCE.splitlines()]
            if c["source"]:
                c["source"][-1] = c["source"][-1].rstrip("\n")
            c["outputs"] = []
            c["execution_count"] = None
            found_cell0 = True
            print(f"REPLACED Cell 0 (id={CELL0_ID}) with operator's final spec")
        elif c.get("id") == MANIFEST_ID:
            # Strip the redundant DATA_DIR re-declaration + its
            # assertion. Cell 0 now owns DATA_DIR; the manifest
            # cell just uses it.
            new_src: list[str] = []
            skip_next = False
            for line in c["source"]:
                if skip_next:
                    skip_next = False
                    continue
                # Match the standalone `DATA_DIR = Path('notebook_data')`
                # line and the two-line `assert DATA_DIR.is_dir(),` block
                # that follows.
                if line.strip() == "DATA_DIR = Path('notebook_data')":
                    print(
                        f"STRIPPED Cell 3 redundant DATA_DIR declaration")
                    continue
                if line.strip().startswith(
                        "assert DATA_DIR.is_dir()"):
                    # The assertion spans 3 lines; skip until the
                    # closing paren line.
                    print(
                        f"STRIPPED Cell 3 redundant DATA_DIR assertion")
                    # Drop this line + look ahead for closing line.
                    # Mark a flag so we keep stripping until we see
                    # the closing ")".
                    new_src_pending_close = True
                    continue
                new_src.append(line)
            # Second pass to strip multiline assertion remnants:
            # if a "'notebook must be run from..." line is still
            # there, drop it.
            cleaned: list[str] = []
            drop = False
            for line in new_src:
                if "'notebook must be run from the" in line:
                    drop = True
                    continue
                if drop and line.strip().endswith("')"):
                    drop = False
                    continue
                if drop:
                    continue
                cleaned.append(line)
            c["source"] = cleaned
            found_manifest = True

    if not found_cell0:
        print(f"FATAL: Cell 0 (id={CELL0_ID}) not found")
        return 1
    if not found_manifest:
        print(f"FATAL: manifest cell (id={MANIFEST_ID}) not found")
        return 1

    with open(NOTEBOOK, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=1)
        f.write("\n")
    print("OK: notebook saved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
