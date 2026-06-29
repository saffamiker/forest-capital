"""Remove the residual `hmmlearn` mention from the Chart 4 cell
comment. The cell already doesn't import or call hmmlearn (it
only reads regime_classification.csv); the comment was a leftover
explanatory note. Reword to focus on the count tolerance.
"""
from __future__ import annotations

import json
from pathlib import Path


NOTEBOOK = Path(__file__).resolve().parents[1] / "analytical_appendix.ipynb"


def main() -> int:
    with open(NOTEBOOK, encoding="utf-8") as f:
        nb = json.load(f)

    for c in nb["cells"]:
        if c.get("id") != "a0aff74f":
            continue
        new_src = []
        for line in c["source"]:
            if "hmmlearn" in line:
                new_src.append(
                    "# Local fallback re-runs yield ~55 / ~196 "
                    "/ ~36 -- within tolerance;\n")
                continue
            new_src.append(line)
        c["source"] = new_src
        print("CLEANED")
        break

    with open(NOTEBOOK, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=1)
        f.write("\n")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
