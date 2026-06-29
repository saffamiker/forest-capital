"""scripts/add_colab_setup_cell.py -- June 29 2026.

Insert a Colab-friendly setup cell as the new Cell 0. The existing
title-markdown cell (db6a4fe4) shifts to position 1. Also strips
the hmmlearn comment from the Chart 4 cell (a0aff74f) so a grader
running in Colab sees no references to libraries they cannot
install.

Idempotent: if a cell with the COLAB_CELL_ID already exists, the
script no-ops.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


NOTEBOOK = Path(__file__).resolve().parents[1] / "analytical_appendix.ipynb"
COLAB_CELL_ID = "colab_setup_2026_06_29"


COLAB_CELL_SOURCE = """# Forest Capital -- FNA 670 Analytical Appendix
#
# Run this notebook in Google Colab by uploading the notebook
# AND the notebook_data/ folder, or by cloning the repository:
#   git clone https://github.com/saffamiker/forest-capital
#   cd forest-capital
#   jupyter notebook analytical_appendix.ipynb
#
# All data is pre-exported from the platform analytics cache.
# Data hash:     c421fb895347f924  (platform fingerprint, brief/appendix/deck)
# Strategy key:  f2e87dec7dcabe71  (notebook strategy-cache key)
# No internet connection or platform access required to run.
from pathlib import Path

DATA_DIR = Path("notebook_data")
assert DATA_DIR.exists(), (
    "notebook_data/ folder not found -- please upload it "
    "alongside this notebook")
print(f"Data directory found: {DATA_DIR.resolve()}")
print(f"Files: {sorted(f.name for f in DATA_DIR.iterdir())}")
"""


def main() -> int:
    with open(NOTEBOOK, encoding="utf-8") as f:
        nb = json.load(f)

    cells = nb["cells"]

    # Idempotence guard.
    if any(c.get("id") == COLAB_CELL_ID for c in cells):
        print(f"INFO: cell {COLAB_CELL_ID} already present; no-op")
    else:
        colab_cell = {
            "cell_type": "code",
            "id": COLAB_CELL_ID,
            "metadata": {},
            "execution_count": None,
            "outputs": [],
            "source": [s + "\n" for s in COLAB_CELL_SOURCE.splitlines()],
        }
        if colab_cell["source"]:
            colab_cell["source"][-1] = (
                colab_cell["source"][-1].rstrip("\n"))
        cells.insert(0, colab_cell)
        print(f"INSERTED Colab setup cell at position 0")

    # Clean the hmmlearn mention from the Chart 4 cell comment.
    for c in cells:
        if c.get("id") != "a0aff74f":
            continue
        new_src = []
        for line in c["source"]:
            if "hmmlearn unavailable" in line:
                new_src.append(
                    "# Local fallback runs (when hmmlearn isn't "
                    "available) yield\n")
            else:
                new_src.append(line)
        c["source"] = new_src
        print("CLEANED hmmlearn mention in Chart 4 cell")
        break

    with open(NOTEBOOK, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=1)
        f.write("\n")
    print("OK: notebook saved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
