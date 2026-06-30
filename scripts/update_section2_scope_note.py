"""scripts/update_section2_scope_note.py -- June 29 2026.

Stale scope decision note at the bottom of Section 2 (Cell 5).
After PR #513 wired runtime MOM download + Carhart 4-factor with
3-factor fallback in Cell 10, the original "3-factor, not Carhart"
note is incorrect. Replace with the operator-spec'd note that
reflects the Carhart-first / 3-factor-fallback behaviour.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


NOTEBOOK = Path(__file__).resolve().parents[1] / "analytical_appendix.ipynb"
CELL_ID = "cc801457"


OLD_BLOCK = """print('=' * 72)
print('Scope decision: 3-factor regression, not Carhart 4-factor.')
print('=' * 72)
print('The data freeze does not include the Kenneth French')
print('momentum (UMD/MOM) factor. Cell 8 runs the Fama-')
print('French three-factor model. The Executive Brief')
print('does not require Carhart specifically; the three-')
print('factor alpha and beta are the headline regression')
print('outputs.')"""

NEW_BLOCK = """print('=' * 72)
print('Scope decision: Carhart (1997) four-factor regression.')
print('=' * 72)
print('Regression model: Carhart (1997) four-factor (MKT-')
print('RF, SMB, HML, MOM). The MOM factor is downloaded at')
print('runtime from Kenneth French\\'s data library. If the')
print('download fails (no internet), Cell 10 falls back')
print('gracefully to the Fama-French three-factor model and')
print('notes the limitation. The Executive Brief and')
print('Analytical Appendix Table E1 both reference Carhart')
print('(1997).')"""


def main() -> int:
    with open(NOTEBOOK, encoding="utf-8") as f:
        nb = json.load(f)
    for c in nb["cells"]:
        if c.get("id") != CELL_ID:
            continue
        src = "".join(c["source"])
        if OLD_BLOCK not in src:
            print("FATAL: old scope-decision block not found")
            return 1
        src = src.replace(OLD_BLOCK, NEW_BLOCK)
        c["source"] = [s + "\n" for s in src.splitlines()]
        if c["source"]:
            c["source"][-1] = c["source"][-1].rstrip("\n")
        c["outputs"] = []
        c["execution_count"] = None
        print(f"PATCHED Section 2 scope-decision note (id={CELL_ID})")
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
