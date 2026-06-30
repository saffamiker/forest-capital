"""Fix the syntax error at the tail of the bootstrap cell.
The original NEW_CELL11 string in apply_notebook_review_fixes.py
ended with `print('...different bootstrap').` (stray dot after
the closing paren). Patch the tail to a clean line."""
from __future__ import annotations

import json
import sys
from pathlib import Path


NOTEBOOK = Path(__file__).resolve().parents[1] / "analytical_appendix.ipynb"
CELL_ID = "93c216b7"

OLD_TAIL = (
    "print('choice (the platform uses a different bootstrap')."
)
NEW_TAIL = (
    "print('choice (the platform uses a different bootstrap '\n"
    "      'seed + a longer block).')"
)


def main() -> int:
    with open(NOTEBOOK, encoding="utf-8") as f:
        nb = json.load(f)
    for c in nb["cells"]:
        if c.get("id") != CELL_ID:
            continue
        src = "".join(c["source"])
        if OLD_TAIL not in src:
            print("FATAL: tail fragment not found")
            return 1
        src = src.replace(OLD_TAIL, NEW_TAIL)
        c["source"] = [s + "\n" for s in src.splitlines()]
        if c["source"]:
            c["source"][-1] = c["source"][-1].rstrip("\n")
        print("PATCHED bootstrap cell tail")
        break
    with open(NOTEBOOK, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=1)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
